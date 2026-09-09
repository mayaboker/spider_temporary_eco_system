"""PySide6 home-side G29 dashboard and Teensy command sender."""

import socket
import time

from definitions import PlatformType
from g29_input import G29Input
from teensy_protocol import (
    flipper_velocity_packet,
    is_telemetry_packet,
    pid_packet,
    platform_from_telemetry,
    telemetry_summary,
    twist_packet,
)


STYLE = """
QWidget { background: #0b1020; color: #e8edf7; font-family: Inter, Ubuntu, sans-serif; }
QMainWindow { background: #080c18; }
QFrame#header { background: #111a2e; border: 1px solid #263653; border-radius: 16px; }
QLabel#title { font-size: 24px; font-weight: 700; color: #f5f8ff; }
QLabel#subtitle { color: #93a4c2; }
QLabel#chip { background: #18243b; border: 1px solid #2b4064; border-radius: 9px; padding: 6px 10px; }
QGroupBox { border: 1px solid #243553; border-radius: 12px; margin-top: 12px; padding: 12px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 6px; color: #9cbcff; }
QTabWidget::pane { border: 1px solid #243553; border-radius: 12px; background: #0d1425; }
QTabBar::tab { background: #121c30; color: #91a2bf; padding: 10px 18px; margin-right: 3px; border-radius: 8px; }
QTabBar::tab:selected { background: #2357d9; color: white; }
QPushButton { background: #2357d9; border: none; border-radius: 8px; padding: 8px 12px; font-weight: 600; }
QPushButton:hover { background: #3470fa; }
QPushButton#danger { background: #a62f45; }
QPushButton#secondary { background: #23314b; }
QSpinBox, QDoubleSpinBox { background: #111a2e; border: 1px solid #304565; border-radius: 6px; padding: 5px; }
QProgressBar { border: none; background: #172238; border-radius: 6px; text-align: center; min-height: 14px; }
QProgressBar::chunk { background: #3478f6; border-radius: 6px; }
QTextEdit { background: #080e1b; border: 1px solid #243553; border-radius: 10px; color: #b9c8df; padding: 8px; font-family: monospace; }
"""


def drive_values(wheel, direction):
    if not wheel.connected:
        return 0.0, 0.0
    throttle = wheel.accelerator * (1.0 - wheel.brake)
    return direction * throttle, wheel.steering


def next_direction(direction, pressed, was_pressed):
    return -direction if pressed and not was_pressed else direction


def requested_flipper_velocity(wheel, enabled):
    up = enabled and wheel.buttons.get(19, False)
    down = enabled and wheel.buttons.get(20, False)
    direction = float(up) - float(down)
    if direction == 0.0:
        return None
    speed = float(wheel.config.get("flipper_velocity", 20.0))
    return (direction * speed,) * 4


def direction_visual(direction):
    return ("Forward", "#2ecc71") if direction > 0 else ("Reverse", "#ff7043")


def run_home(field_host, field_port, config_path):
    try:
        from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
        from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen, QPolygonF
        from PySide6.QtWidgets import (
            QApplication, QFrame, QGridLayout, QGroupBox,
            QHBoxLayout, QLabel, QMainWindow,
            QScrollArea, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
        )
    except ImportError as exc:
        raise SystemExit("PySide6 is required: python3 -m pip install -r requirements.txt") from exc

    class WheelView(QWidget):
        """Scalable clean wheel artwork with separate live control overlays."""

        def __init__(self, wheel, functional_only=False):
            super().__init__()
            self.wheel = wheel
            self.functional_only = functional_only
            self.direction = 1.0
            self.setMinimumSize(520, 430)

        def paintEvent(self, _event):
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            width, height = self.width(), self.height()
            center = QPointF(width * 0.5, height * 0.47)
            radius = min(width, height) * 0.36

            glow = QLinearGradient(0, 0, width, height)
            glow.setColorAt(0, QColor("#101a31"))
            glow.setColorAt(1, QColor("#090e1b"))
            painter.fillRect(self.rect(), glow)

            # The wheel artwork follows the G29's full 900-degree range. Live
            # button overlays and pedals are painted after restore(), so they
            # remain fixed on screen as requested.
            painter.save()
            painter.translate(center)
            painter.rotate(self.wheel.steering * 450.0)
            painter.translate(-center)
            painter.setPen(QPen(QColor("#05070c"), radius * 0.19, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(center, radius, radius)
            painter.setPen(QPen(QColor("#39445a"), max(2, radius * 0.018)))
            painter.drawEllipse(center, radius * 0.94, radius * 0.94)

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#242e40"))
            hub = radius * 0.33
            left = QPointF(center.x() - radius * 0.73, center.y() - radius * 0.12)
            right = QPointF(center.x() + radius * 0.73, center.y() - radius * 0.12)
            bottom = QPointF(center.x(), center.y() + radius * 0.72)
            painter.drawPolygon(QPolygonF((left, QPointF(center.x() - hub, center.y()), QPointF(center.x() - hub * .45, center.y() + hub * .3), QPointF(center.x() - radius * .35, center.y() + radius * .35))))
            painter.drawPolygon(QPolygonF((right, QPointF(center.x() + hub, center.y()), QPointF(center.x() + hub * .45, center.y() + hub * .3), QPointF(center.x() + radius * .35, center.y() + radius * .35))))
            painter.drawPolygon(QPolygonF((bottom, QPointF(center.x() - hub * .22, center.y() + hub * .25), QPointF(center.x() + hub * .22, center.y() + hub * .25))))
            painter.setBrush(QColor("#101725"))
            painter.setPen(QPen(QColor("#60708b"), 2))
            painter.drawEllipse(center, hub, hub)
            self._draw_meerkat(painter, center, radius)

            painter.setPen(QPen(QColor("#2f7cff"), radius * 0.045, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(QPointF(center.x(), center.y() - radius * 1.07), QPointF(center.x(), center.y() - radius * .91))
            painter.restore()

            self._draw_pedal(painter, width * .34, height * .88, "CLUTCH", self.wheel.clutch)
            self._draw_pedal(painter, width * .50, height * .88, "BRAKE", self.wheel.brake)
            self._draw_pedal(painter, width * .66, height * .88, "GAS", self.wheel.accelerator)
            for button in self.wheel.config.get("buttons", ()):
                if not self.functional_only or button.get("action"):
                    self._draw_button(painter, button, width, height)

        @staticmethod
        def _draw_meerkat(painter, center, radius):
            """Draw an original meerkat emblem directly into the wheel hub."""
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#b87d43"))
            body = QRectF(center.x() - radius * .10, center.y() + radius * .02,
                          radius * .20, radius * .24)
            painter.drawRoundedRect(body, radius * .08, radius * .08)
            painter.setBrush(QColor("#d6a363"))
            head = QRectF(center.x() - radius * .13, center.y() - radius * .16,
                          radius * .26, radius * .24)
            painter.drawEllipse(head)
            ear = radius * .075
            painter.drawEllipse(QPointF(center.x() - radius * .13, center.y() - radius * .08), ear, ear)
            painter.drawEllipse(QPointF(center.x() + radius * .13, center.y() - radius * .08), ear, ear)
            painter.setBrush(QColor("#3a291f"))
            painter.drawEllipse(QPointF(center.x() - radius * .055, center.y() - radius * .075), radius * .034, radius * .025)
            painter.drawEllipse(QPointF(center.x() + radius * .055, center.y() - radius * .075), radius * .034, radius * .025)
            painter.setBrush(QColor("#f3dfbb"))
            painter.drawEllipse(QPointF(center.x() - radius * .055, center.y() - radius * .078), radius * .011, radius * .011)
            painter.drawEllipse(QPointF(center.x() + radius * .055, center.y() - radius * .078), radius * .011, radius * .011)
            painter.setBrush(QColor("#7a4d2b"))
            painter.drawEllipse(QPointF(center.x(), center.y() - radius * .005), radius * .028, radius * .02)

        def _draw_button(self, painter, button, width, height):
            pressed = self.wheel.buttons.get(button["index"], False)
            point = QPointF(width * button["x"], height * button["y"])
            label = button["label"]
            state_color = None
            if button.get("action") == "toggle_direction":
                label, state_color = direction_visual(self.direction)
            size = 19 if len(label) <= 2 else (34 if len(label) > 5 else 26)
            fill_color = state_color or ("#39d98a" if pressed else "#17233a")
            edge_color = "#ffffff" if pressed else (state_color or "#4a5d7c")
            painter.setBrush(QColor(fill_color))
            painter.setPen(QPen(QColor(edge_color), 3 if pressed else 2))
            painter.drawEllipse(point, size, size)
            painter.setPen(QColor("#07130d") if state_color or pressed else QColor("#c8d4e8"))
            painter.setFont(QFont("Ubuntu", 8, QFont.Weight.Bold))
            painter.drawText(QRectF(point.x() - size, point.y() - size, size * 2, size * 2), Qt.AlignmentFlag.AlignCenter, label)

        @staticmethod
        def _draw_pedal(painter, x, y, label, value):
            rect = QRectF(x - 34, y - 10, 68, 20)
            painter.setBrush(QColor("#1a263e"))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect, 7, 7)
            fill = QRectF(rect.x(), rect.y(), rect.width() * value, rect.height())
            painter.setBrush(QColor("#3478f6"))
            painter.drawRoundedRect(fill, 7, 7)
            painter.setPen(QColor("#dce7fa"))
            painter.setFont(QFont("Ubuntu", 7, QFont.Weight.Bold))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)

    class Dashboard(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Spider Drive Console")
            self.resize(1280, 850)
            self.setStyleSheet(STYLE)
            self.wheel = G29Input(config_path)
            self.field_address = (socket.gethostbyname(field_host), field_port)
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setblocking(False)
            self.platform = PlatformType.NONE
            self.last_telemetry = 0.0
            self.telemetry_values = {}
            self.flipper_stop_ticks = 0
            self.flippers_enabled = False
            self.flippers_were_moving = False
            self.direction = 1.0
            self.direction_button_was_pressed = False
            self.control_allowed_target = None
            self.control_platform = PlatformType.NONE
            self.control_sends_left = 0
            self.control_next_send = 0.0
            self.axis_labels = {}
            self.raw_button_labels = {}
            self._build_ui()
            self.timer = QTimer(self)
            self.timer.timeout.connect(self._tick)
            self.timer.start(50)

        def _build_ui(self):
            root = QWidget()
            root_layout = QVBoxLayout(root)
            root_layout.setContentsMargins(20, 20, 20, 20)
            root_layout.setSpacing(14)

            header = QFrame(objectName="header")
            header_layout = QHBoxLayout(header)
            titles = QVBoxLayout()
            title = QLabel("SPIDER DRIVE CONSOLE", objectName="title")
            subtitle = QLabel("G29 home station  •  native Teensy UDP")
            subtitle.setObjectName("subtitle")
            titles.addWidget(title)
            titles.addWidget(subtitle)
            header_layout.addLayout(titles)
            header_layout.addStretch()
            self.wheel_chip = QLabel(objectName="chip")
            self.robot_chip = QLabel(objectName="chip")
            self.flippers_chip = QLabel(objectName="chip")
            self.direction_chip = QLabel(objectName="chip")
            self.platform_chip = QLabel(objectName="chip")
            for chip in (self.wheel_chip, self.robot_chip, self.flippers_chip,
                         self.direction_chip, self.platform_chip):
                header_layout.addWidget(chip)
            root_layout.addWidget(header)

            body = QHBoxLayout()
            self.tabs = QTabWidget()
            self.tabs.addTab(self._all_inputs_tab(), "All wheel buttons")
            self.tabs.addTab(self._functional_tab(), "Functional controls")
            body.addWidget(self.tabs, 3)

            self.telemetry_group = QGroupBox("Live Teensy telemetry")
            telemetry_layout = QVBoxLayout(self.telemetry_group)
            self.telemetry_text = QTextEdit()
            self.telemetry_text.setReadOnly(True)
            telemetry_layout.addWidget(self.telemetry_text)
            body.addWidget(self.telemetry_group, 2)
            root_layout.addLayout(body, 1)
            self.setCentralWidget(root)
            self.tabs.currentChanged.connect(self._tab_changed)
            self.tabs.setCurrentIndex(1)
            self._tab_changed(1)

        def _tab_changed(self, index):
            self.telemetry_group.setVisible(index == 0)

        def _all_inputs_tab(self):
            page = QWidget()
            layout = QVBoxLayout(page)
            self.all_wheel = WheelView(self.wheel)
            layout.addWidget(self.all_wheel, 1)
            self.raw_grid = QGridLayout()
            raw = QWidget()
            raw.setLayout(self.raw_grid)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setMaximumHeight(135)
            scroll.setWidget(raw)
            layout.addWidget(scroll)
            return page

        def _functional_tab(self):
            page = QWidget()
            layout = QVBoxLayout(page)
            self.functional_wheel = WheelView(self.wheel, functional_only=True)
            layout.addWidget(self.functional_wheel, 1)
            help_text = QLabel(
                "STEERING + ACCELERATOR: always active   •   BRAKE: reduces speed   •   "
                "BUTTON 23: forward/reverse\nCLUTCH: enables flippers   •   + (19): all up   •   - (20): all down"
            )
            help_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
            help_text.setObjectName("subtitle")
            layout.addWidget(help_text)
            return page

        def _send(self, packet):
            self.sock.sendto(packet, self.field_address)

        def _tick(self):
            self.wheel.poll()
            self._update_direction()
            self._update_flippers()
            linear, angular = drive_values(self.wheel, self.direction)
            self._send(twist_packet(linear, angular))
            self._receive_telemetry()
            self._sync_control_allowed()
            self._update_ui()

        def _update_direction(self):
            pressed = self.wheel.connected and self.wheel.buttons.get(23, False)
            self.direction = next_direction(
                self.direction, pressed, self.direction_button_was_pressed
            )
            self.direction_button_was_pressed = pressed

        def _update_flippers(self):
            enabled = self.wheel.connected and self.wheel.clutch > 0.5
            if self.flippers_enabled and not enabled:
                self.flipper_stop_ticks = 5
            self.flippers_enabled = enabled
            values = requested_flipper_velocity(self.wheel, enabled)
            moving = values is not None
            if moving:
                self._send(flipper_velocity_packet(values))
                self.flipper_stop_ticks = 5
            elif self.flippers_were_moving:
                self.flipper_stop_ticks = 5
            if not moving and self.flipper_stop_ticks:
                self._send(flipper_velocity_packet((0.0, 0.0, 0.0, 0.0)))
                self.flipper_stop_ticks -= 1
            self.flippers_were_moving = moving

        def _sync_control_allowed(self):
            if self.platform is PlatformType.NONE:
                return
            if (self.control_platform is not self.platform
                    or self.control_allowed_target is not self.flippers_enabled):
                self.control_platform = self.platform
                self.control_allowed_target = self.flippers_enabled
                self.control_sends_left = 5
                self.control_next_send = 0.0
            now = time.monotonic()
            if self.control_sends_left and now >= self.control_next_send:
                self._send(pid_packet(self.control_allowed_target, self.platform))
                self.control_sends_left -= 1
                self.control_next_send = now + 0.25

        def _receive_telemetry(self):
            while True:
                try:
                    packet, source = self.sock.recvfrom(65535)
                except BlockingIOError:
                    return
                if source != self.field_address or not is_telemetry_packet(packet):
                    continue
                self.last_telemetry = time.monotonic()
                platform = platform_from_telemetry(packet)
                if platform is not None:
                    self.platform = platform
                msg_type, summary = telemetry_summary(packet, self.platform)
                self.telemetry_values[msg_type] = summary

        def _update_ui(self):
            online = time.monotonic() - self.last_telemetry < 1.0
            self.wheel_chip.setText(self._online_chip("WHEEL", self.wheel.connected))
            self.robot_chip.setText(self._online_chip("ROBOT", online))
            self.flippers_chip.setText("FLIPPERS  " + ("ENABLED" if self.flippers_enabled else "DISABLED"))
            self.direction_chip.setText("DRIVE  " + ("FORWARD" if self.direction > 0 else "REVERSE"))
            self.platform_chip.setText(self.platform.name)
            self.telemetry_text.setPlainText(
                "\n\n".join(self.telemetry_values[key] for key in sorted(self.telemetry_values))
                or "Waiting for telemetry…\n\nThe dashboard remains connected when the G29 is unavailable."
            )
            self.all_wheel.direction = self.direction
            self.functional_wheel.direction = self.direction
            self.all_wheel.update()
            self.functional_wheel.update()
            self._update_raw_controls()

        @staticmethod
        def _online_chip(name, online):
            color = "#39d98a" if online else "#ff5263"
            state = "ONLINE" if online else "OFFLINE"
            return f"<span style='color:{color}; font-size:16px'>●</span> {name}&nbsp;&nbsp;{state}"

        def _update_raw_controls(self):
            items = [(f"Axis {number}", f"{value:+.3f}", False)
                     for number, value in sorted(self.wheel.axes.items())]
            items += [(f"Button {number}", "PRESSED" if pressed else "released", pressed)
                      for number, pressed in sorted(self.wheel.buttons.items())]
            for index, (name, value, active) in enumerate(items):
                key = (name, index)
                if key not in self.raw_button_labels:
                    label = QLabel()
                    label.setObjectName("chip")
                    self.raw_button_labels[key] = label
                    self.raw_grid.addWidget(label, index // 6, index % 6)
                label = self.raw_button_labels[key]
                label.setText(f"{name}  {value}")
                label.setStyleSheet("background:#176b4a;border-radius:7px;padding:5px" if active else "")

        def closeEvent(self, event):
            for _ in range(5):
                self._send(twist_packet(0.0, 0.0))
                self._send(flipper_velocity_packet((0.0, 0.0, 0.0, 0.0)))
                if self.platform is not PlatformType.NONE:
                    self._send(pid_packet(False, self.platform))
            self.wheel.close()
            self.sock.close()
            event.accept()

    app = QApplication([])
    window = Dashboard()
    window.show()
    return app.exec()
