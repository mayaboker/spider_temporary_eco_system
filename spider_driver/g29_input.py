"""Non-blocking Linux joystick reader and configurable G29 normalization."""

import json
import os
import struct


JS_EVENT = struct.Struct("IhBB")
JS_EVENT_BUTTON = 0x01
JS_EVENT_AXIS = 0x02
JS_EVENT_INIT = 0x80
AXIS_MAX = 32767.0


class G29Input:
    def __init__(self, config_path):
        with open(config_path, encoding="utf-8") as stream:
            self.config = json.load(stream)
        self.device = self.config.get("device", "/dev/input/js0")
        self.axes = {}
        self.buttons = {}
        self.fd = None
        self.error = None
        self.open()

    @property
    def connected(self):
        return self.fd is not None

    def open(self):
        if self.fd is not None:
            return
        try:
            self.fd = os.open(self.device, os.O_RDONLY | os.O_NONBLOCK)
            self.error = None
        except OSError as exc:
            self.error = str(exc)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def poll(self):
        if self.fd is None:
            self.open()
            return
        while True:
            try:
                data = os.read(self.fd, JS_EVENT.size)
            except BlockingIOError:
                return
            except OSError as exc:
                self.error = str(exc)
                self.close()
                return
            if len(data) != JS_EVENT.size:
                self.error = "joystick disconnected"
                self.close()
                return
            _, value, kind, number = JS_EVENT.unpack(data)
            kind &= ~JS_EVENT_INIT
            if kind == JS_EVENT_AXIS:
                self.axes[number] = max(-1.0, min(1.0, value / AXIS_MAX))
            elif kind == JS_EVENT_BUTTON:
                self.buttons[number] = bool(value)

    def _pedal(self, name):
        cfg = self.config[name]
        raw = self.axes.get(cfg["axis"], cfg["released"])
        span = cfg["pressed"] - cfg["released"]
        if span == 0:
            return 0.0
        return max(0.0, min(1.0, (raw - cfg["released"]) / span))

    @property
    def accelerator(self):
        return self._pedal("accelerator")

    @property
    def brake(self):
        return self._pedal("brake")

    @property
    def clutch(self):
        return self._pedal("clutch")

    @property
    def steering(self):
        cfg = self.config["steering"]
        raw = self.axes.get(cfg["axis"], 0.0)
        low, high = cfg["minimum"], cfg["maximum"]
        value = 2.0 * (raw - low) / (high - low) - 1.0 if high != low else 0.0
        if cfg.get("invert", False):
            value = -value
        deadzone = cfg.get("deadzone", 0.05)
        if abs(value) <= deadzone:
            return 0.0
        scaled = (abs(value) - deadzone) / (1.0 - deadzone)
        return max(-1.0, min(1.0, scaled if value > 0 else -scaled))
