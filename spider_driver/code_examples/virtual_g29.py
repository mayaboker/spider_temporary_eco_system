#!/usr/bin/env python3
"""Virtual G29 -- drive the home dashboard without the physical wheel.

Writes Linux joystick events into a FIFO that `G29Input` reads as if it were
`/dev/input/js0`. Nothing in the home process changes; it is pointed at the
FIFO through a generated wheel config:

    ./code_examples/virtual_g29.py                     # terminal 1
    python3 main.py home --field-host 127.0.0.1 \
        --wheel-config /tmp/virtual_g29.json           # terminal 2

Axis numbers, pedal endpoints, and the button list are read from the real
g29_config.json, so this stays in sync with whatever the wheel is mapped to.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import struct
import sys
import time

JS_EVENT = struct.Struct("IhBB")
JS_EVENT_BUTTON = 0x01
JS_EVENT_AXIS = 0x02
AXIS_MAX = 32767

# G29Input treats a read of 0 bytes as a disconnect, and a FIFO reports EOF
# whenever it has no writer. Holding our end O_RDWR keeps a writer attached for
# the whole session, so the dashboard sees a stable "connected" wheel.
FIFO_FLAGS = os.O_RDWR | os.O_NONBLOCK


class JoystickFifo:
    """The write end of the fake /dev/input/js* device."""

    def __init__(self, path):
        self.path = Path(path)
        if not self.path.exists():
            os.mkfifo(self.path, 0o600)
        self.fd = os.open(self.path, FIFO_FLAGS)

    def emit(self, kind, number, value):
        event = JS_EVENT.pack(int(time.monotonic() * 1000) & 0xFFFFFFFF,
                              value, kind, number)
        try:
            os.write(self.fd, event)
        except BlockingIOError:
            # Nobody is draining the pipe yet (home not started). Dropping the
            # event is correct: state is resent on the periodic refresh.
            pass

    def axis(self, number, value):
        self.emit(JS_EVENT_AXIS, number, max(-AXIS_MAX, min(AXIS_MAX, int(round(value)))))

    def button(self, number, pressed):
        self.emit(JS_EVENT_BUTTON, number, 1 if pressed else 0)

    def close(self):
        os.close(self.fd)


def pedal_axis_value(cfg, fraction):
    """Invert G29Input._pedal: fraction 0..1 -> raw axis value."""
    raw = cfg["released"] + fraction * (cfg["pressed"] - cfg["released"])
    return raw * AXIS_MAX


def steering_axis_value(cfg, normalized):
    """Invert G29Input.steering, minus the deadzone rescale it applies."""
    if cfg.get("invert", False):
        normalized = -normalized
    low, high = cfg["minimum"], cfg["maximum"]
    raw = low + (normalized + 1.0) * (high - low) / 2.0
    return raw * AXIS_MAX


def write_wheel_config(source, device, destination):
    config = json.loads(Path(source).read_text(encoding="utf-8"))
    config["device"] = str(device)
    Path(destination).write_text(json.dumps(config, indent=2), encoding="utf-8")
    return config


def build_app(config, fifo, config_path):
    try:
        from PySide6.QtCore import Qt, QTimer
        from PySide6.QtWidgets import (
            QApplication, QCheckBox, QGridLayout, QGroupBox, QHBoxLayout,
            QLabel, QLineEdit, QPushButton, QSlider, QVBoxLayout, QWidget,
        )
    except ImportError:
        # Same Qt5 fallback as the dashboard -- see home_process.run_home.
        from PySide2.QtCore import Qt, QTimer
        from PySide2.QtWidgets import (
            QApplication, QCheckBox, QGridLayout, QGroupBox, QHBoxLayout,
            QLabel, QLineEdit, QPushButton, QSlider, QVBoxLayout, QWidget,
        )

    STYLE = """
    QWidget { background: #0b1020; color: #e8edf7; font-family: Inter, Ubuntu, sans-serif; }
    QGroupBox { border: 1px solid #243553; border-radius: 12px; margin-top: 12px; padding: 12px; font-weight: 600; }
    QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 6px; color: #9cbcff; }
    QPushButton { background: #23314b; border: none; border-radius: 8px; padding: 9px 12px; font-weight: 600; }
    QPushButton:hover { background: #2e3f5f; }
    QPushButton:checked { background: #39d98a; color: #07130d; }
    QLineEdit { background: #080e1b; border: 1px solid #243553; border-radius: 8px; padding: 6px; font-family: monospace; color: #b9c8df; }
    QLabel#hint { color: #93a4c2; }
    """

    class VirtualWheel(QWidget):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Virtual G29")
            self.setStyleSheet(STYLE)
            self.resize(560, 620)
            self.sliders = {}
            self.buttons = {}
            self._build()
            # A real joystick only reports changes, but the dashboard may start
            # after us and miss them, so the full state is repeated at 2 Hz.
            self.refresh = QTimer(self)
            self.refresh.timeout.connect(self.send_all)
            self.refresh.start(500)
            self.send_all()

        def _build(self):
            layout = QVBoxLayout(self)

            axes = QGroupBox("Axes")
            axes_layout = QVBoxLayout(axes)
            self._add_slider(axes_layout, "steering", "Steering", -100, 100, 0)
            self._add_slider(axes_layout, "accelerator", "Accelerator", 0, 100, 0)
            self._add_slider(axes_layout, "brake", "Brake", 0, 100, 0)
            self._add_slider(axes_layout, "clutch", "Clutch", 0, 100, 0)
            hint = QLabel("Clutch above 50% enables the flippers.", objectName="hint")
            axes_layout.addWidget(hint)
            layout.addWidget(axes)

            group = QGroupBox("Buttons  (click to latch, click again to release)")
            grid = QGridLayout(group)
            for position, button in enumerate(config.get("buttons", ())):
                index = button["index"]
                label = f"{button['label']}  ({index})"
                if button.get("action"):
                    label += f"\n{button['action']}"
                widget = QPushButton(label)
                widget.setCheckable(True)
                widget.setMinimumHeight(46)
                widget.toggled.connect(
                    lambda pressed, number=index: fifo.button(number, pressed)
                )
                self.buttons[index] = widget
                grid.addWidget(widget, position // 3, position % 3)
            layout.addWidget(group)

            controls = QHBoxLayout()
            release = QPushButton("Release everything")
            release.clicked.connect(self.release_all)
            controls.addWidget(release)
            self.disconnected = QCheckBox("Simulate wheel unplugged")
            self.disconnected.toggled.connect(self._toggle_connection)
            controls.addWidget(self.disconnected)
            layout.addLayout(controls)

            layout.addWidget(QLabel("Point the dashboard at this config:", objectName="hint"))
            command = QLineEdit(
                f"python3 main.py home --field-host 127.0.0.1 --wheel-config {config_path}"
            )
            command.setReadOnly(True)
            layout.addWidget(command)
            layout.addStretch()

        def _add_slider(self, layout, name, title, low, high, start):
            row = QHBoxLayout()
            caption = QLabel(title)
            caption.setMinimumWidth(96)
            value_label = QLabel()
            value_label.setMinimumWidth(52)
            value_label.setAlignment(Qt.AlignmentFlag.AlignRight)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(low, high)
            slider.setValue(start)
            slider.valueChanged.connect(
                lambda value, key=name, target=value_label: self._axis_changed(key, value, target)
            )
            row.addWidget(caption)
            row.addWidget(slider, 1)
            row.addWidget(value_label)
            layout.addLayout(row)
            self.sliders[name] = slider
            self._axis_changed(name, start, value_label)

        def _axis_changed(self, name, value, label):
            label.setText(f"{value}%")
            self.send_axis(name)

        def send_axis(self, name):
            if self.is_unplugged():
                return
            cfg = config[name]
            value = self.sliders[name].value()
            if name == "steering":
                raw = steering_axis_value(cfg, value / 100.0)
            else:
                raw = pedal_axis_value(cfg, value / 100.0)
            fifo.axis(cfg["axis"], raw)

        def send_all(self):
            if self.is_unplugged():
                return
            for name in self.sliders:
                self.send_axis(name)
            for index, widget in self.buttons.items():
                fifo.button(index, widget.isChecked())

        def is_unplugged(self):
            return getattr(self, "disconnected", None) is not None and self.disconnected.isChecked()

        def _toggle_connection(self, unplugged):
            if not unplugged:
                self.send_all()

        def release_all(self):
            for slider in self.sliders.values():
                slider.setValue(0)
            for widget in self.buttons.values():
                widget.setChecked(False)

        def closeEvent(self, event):
            self.release_all()
            fifo.close()
            event.accept()

    app = QApplication([])
    window = VirtualWheel()
    window.show()
    return app.exec() if hasattr(app, "exec") else app.exec_()


def main(argv=None):
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Virtual G29 wheel for the home dashboard")
    parser.add_argument("--device", default="/tmp/virtual_g29_js",
                        help="FIFO to write joystick events into")
    parser.add_argument("--wheel-config", default=str(root / "g29_config.json"),
                        help="real wheel config to copy axis and button mappings from")
    parser.add_argument("--config-out", default="/tmp/virtual_g29.json",
                        help="generated config for `main.py home --wheel-config`")
    args = parser.parse_args(argv)

    config = write_wheel_config(args.wheel_config, args.device, args.config_out)
    fifo = JoystickFifo(args.device)
    print(f"[virtual-g29] device {args.device}")
    print(f"[virtual-g29] config {args.config_out}")
    print(f"[virtual-g29] run: python3 main.py home --field-host 127.0.0.1 "
          f"--wheel-config {args.config_out}")
    try:
        return build_app(config, fifo, args.config_out)
    except ImportError as exc:
        raise SystemExit("PySide6 is required: python3 -m pip install -r requirements.txt") from exc


if __name__ == "__main__":
    sys.exit(main())
