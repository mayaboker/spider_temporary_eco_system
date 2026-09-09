#!/usr/bin/env python3
"""Mock Spider robot -- the server side of the rtcom protocol.

Usage:
    python3 spider_mock_server.py                    # port 8888, SPIDER_30
    python3 spider_mock_server.py --port 5000 --platform SPIDER_10
    python3 spider_mock_server.py --quiet-telemetry  # log commands only
"""
from __future__ import annotations

import argparse
import math
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

# --------------------------------------------------------------------------
# Wire constants -- must match src/lib/rtcom/core.py
# --------------------------------------------------------------------------
CONNECTION, RAW, TYPED = 0x01, 0x02, 0x03
SYN, SYN_ACK, ACK, PING, PONG, TERMINATE = 0x01, 0x02, 0x03, 0x10, 0x11, 0xFF

PLATFORMS = {"NONE": 0, "SPIDER_10": 1, "SPIDER_20": 2, "SPIDER_30": 3}

MOTOR_COUNT = 8
VIS_NIR_NUM = 2
CLIENT_MAX_INACTIVITY = 3.0


# --------------------------------------------------------------------------
# Command table -- everything the client can send us (0x4x)
# --------------------------------------------------------------------------
@dataclass
class Command:
    name: str
    fields: tuple[str, ...] = ()
    fmt: str | Callable[[str], str] | None = None

    def format_for(self, platform: str) -> str | None:
        return self.fmt(platform) if callable(self.fmt) else self.fmt


def _control_consts_fmt(platform: str) -> str | None:
    if platform in ("SPIDER_10", "SPIDER_20"):
        return "<2f"
    if platform == "SPIDER_30":
        return "<6f"
    return None


COMMANDS: dict[int, Command] = {
    0x40: Command("twist", ("linear", "angular"), "<2f"),
    0x41: Command("flipper_position", ("fl", "fr", "rr", "rl"), "<4f"),
    0x42: Command("flipper_velocity", ("fl", "fr", "rr", "rl"), "<4f"),
    0x44: Command(
        "control_consts",
        ("position_kp", "position_ki", "velocity_kp",
         "velocity_ki", "torque_kp", "torque_ki"),
        _control_consts_fmt,
    ),
    0x45: Command("lights", (), None),
    0x46: Command("calibrate_encoders", ("flag",), "<B"),
}


# --------------------------------------------------------------------------
# Toy physics -- just enough that telemetry responds to commands
# --------------------------------------------------------------------------
@dataclass
class RobotState:
    linear: float = 0.0
    angular: float = 0.0
    flipper_target: list[float] = field(default_factory=lambda: [0.0] * 4)
    flipper_actual: list[float] = field(default_factory=lambda: [0.0] * 4)
    flipper_rate: list[float] = field(default_factory=lambda: [0.0] * 4)

    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0

    voltage: float = 40.0
    lights: bytes = b""

    def step(self, dt: float) -> None:
        # Heading integrates commanded rotation; roll/pitch just wobble a
        # little so the attitude indicator visibly moves.
        self.yaw = (self.yaw + self.angular * dt) % (2 * math.pi)
        self.roll = 0.05 * math.sin(self.yaw * 3)
        self.pitch = 0.08 * math.sin(self.yaw * 2)

        # Flippers ease toward their target, or free-run under velocity command.
        for i in range(4):
            if self.flipper_rate[i]:
                self.flipper_actual[i] += self.flipper_rate[i] * dt
            else:
                error = self.flipper_target[i] - self.flipper_actual[i]
                self.flipper_actual[i] += error * min(1.0, 4.0 * dt)

        # Battery sags under load and slowly drains.
        load = abs(self.linear) + abs(self.angular)
        self.voltage = max(29.5, self.voltage - dt * (0.002 + 0.02 * load))

    def motor_velocity(self, index: int) -> float:
        """Motors 0-1 are the tracks, 2-5 the flippers, 6-7 spare."""
        if index < 2:
            side = -1.0 if index == 0 else 1.0
            return self.linear + side * self.angular
        if index < 6:
            return self.flipper_rate[index - 2]
        return 0.0

    def motor_position(self, index: int) -> float:
        if 2 <= index < 6:
            return self.flipper_actual[index - 2]
        return 0.0


# --------------------------------------------------------------------------
# Server
# --------------------------------------------------------------------------
class MockSpider:
    def __init__(self, port: int, platform: str, log_telemetry: bool) -> None:
        self.platform = platform
        self.log_telemetry = log_telemetry

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", port))
        self.sock.settimeout(0.05)

        self.client_address: tuple[str, int] | None = None
        self.connected = False
        self.state = RobotState()
        self.stop = threading.Event()

        self.ping_seq = 0
        self.last_pong = 0.0
        self.last_command = 0.0
        self.unknown_types: set[int] = set()

    # -- logging ----------------------------------------------------------
    def log(self, tag: str, message: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] {tag:<10} {message}", flush=True)

    # -- sending ----------------------------------------------------------
    def send(self, packet_type: int, payload: bytes) -> None:
        if self.client_address is None:
            self.log("Cancel", "client is not exist, sending message is canceled.")
            return
        self.sock.sendto(bytes([packet_type]) + payload, self.client_address)

    def send_telemetry(self, msg_type: int, payload: bytes) -> None:
        self.send(TYPED, bytes([msg_type]) + payload)

    # -- session ----------------------------------------------------------
    def handle_connection(self, payload: bytes, src) -> None:
        if not payload:
            self.log("Cancel", "Payload is empty, handling message is canceled.")
            return
        kind = payload[0]

        if kind == SYN:
            self.client_address = src
            self.send(CONNECTION, bytes([SYN_ACK]))
            self.log("session", f"SYN from {src[0]}:{src[1]} -> SYN_ACK")

        elif kind == ACK:
            self.connected = True
            self.last_pong = time.time()
            self.log("session", "ACK received -- connected")

        elif kind == PONG and len(payload) > 1:
            self.last_pong = time.time()

        elif kind == TERMINATE:
            self.log("session", "TERMINATE from client")
            self.disconnect()

    def disconnect(self) -> None:
        if self.connected:
            self.connected = False
            self.state = RobotState()          # a real robot would e-stop
            self.log("session", "disconnected -- state reset")
        self.client_address = None

    # -- command decoding -------------------------------------------------
    def handle_typed(self, payload: bytes) -> None:
        if not payload:
            return
        msg_type, data = payload[0], payload[1:]
        self.last_command = time.time()

        command = COMMANDS.get(msg_type)
        if command is None:
            if msg_type not in self.unknown_types:
                self.unknown_types.add(msg_type)
                self.log("UNKNOWN", f"type=0x{msg_type:02x} "
                                    f"len={len(data)} raw={data.hex(' ')}")
            return

        fmt = command.format_for(self.platform)
        if fmt is None:
            self.log("command", f"{command.name} len={len(data)} "
                                f"raw={data.hex(' ')}")
            self.apply_variable(msg_type, data)
            return

        expected = struct.calcsize(fmt)
        if len(data) != expected:
            self.log("SIZE ERR", f"{command.name}: expected {expected} B, "
                                 f"got {len(data)} -- ignored")
            return

        values = struct.unpack(fmt, data)
        pairs = ", ".join(f"{n}={v:.3f}" if isinstance(v, float) else f"{n}={v}"
                          for n, v in zip(command.fields, values))
        if self.log_telemetry:
            self.log("command", f"{command.name}: {pairs}")
        self.apply(msg_type, values)

    def apply(self, msg_type: int, values) -> None:
        if msg_type == 0x40:
            self.state.linear, self.state.angular = values
        elif msg_type == 0x41:
            self.state.flipper_target = list(values)
        elif msg_type == 0x42:
            self.state.flipper_rate = list(values)
        elif msg_type == 0x46:
            self.log("action", "encoder calibration requested")
            self.state.flipper_actual = [0.0] * 4

    def apply_variable(self, msg_type: int, data: bytes) -> None:
        if msg_type == 0x45:
            self.state.lights = data
            if len(data) != 2 * VIS_NIR_NUM:
                self.log("SIZE ERR", f"lights: expected {2 * VIS_NIR_NUM} B, "
                                     f"got {len(data)}")

    # -- telemetry encoding ----------------------------------------------
    def motors_data(self) -> bytes:
        chunks = []
        for i in range(MOTOR_COUNT):
            current = 0.5 + 0.1 * i
            velocity = self.state.motor_velocity(i)
            position = self.state.motor_position(i)
            if self.platform == "SPIDER_30":
                chunks.append(struct.pack("=BfhdB", i, current,
                                          int(velocity * 100), position, 0))
            else:
                chunks.append(struct.pack("=B3fB", i, current,
                                          velocity, position, 0))
        return b"".join(chunks)

    def motors_peripherals(self) -> bytes:
        chunks = []
        for i in range(MOTOR_COUNT):
            voltage = self.state.voltage + 0.05 * i
            if self.platform == "SPIDER_30":
                chunks.append(struct.pack("=Bb6Bf", i, 35 + i,
                                          *[10] * 6, voltage))
            else:
                chunks.append(struct.pack("=B2f6ff", i, 35.0 + i, 40.0 + i,
                                          *[1.0] * 6, voltage))
        return b"".join(chunks)

    def emit_all_telemetry(self) -> None:
        s = self.state
        self.send_telemetry(0x10, struct.pack("=b", PLATFORMS[self.platform]))
        self.send_telemetry(0x11, self.motors_data())
        self.send_telemetry(0x12, struct.pack("=4f", s.linear, s.angular,
                                              s.linear, s.angular))
        self.send_telemetry(0x13, struct.pack("=4f", *s.flipper_actual))
        self.send_telemetry(0x14, self.motors_peripherals())
        self.send_telemetry(0x15, struct.pack("=3f", s.roll, s.pitch, s.yaw))

    # -- main loop --------------------------------------------------------
    def run(self) -> None:
        self.log("start", f"listening on {self.sock.getsockname()[1]} "
                          f"as {self.platform}")
        threading.Thread(target=self.telemetry_loop, daemon=True).start()

        while not self.stop.is_set():
            try:
                data, src = self.sock.recvfrom(4096)
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                break

            if not data:
                continue
            if self.connected and src != self.client_address:
                continue

            packet_type, payload = data[0], data[1:]
            if packet_type == CONNECTION:
                self.handle_connection(payload, src)
            elif packet_type == TYPED:
                self.handle_typed(payload)
            elif packet_type == RAW:
                self.log("raw", f"len={len(payload)} {payload.hex(' ')}")
            else:
                self.log("UNKNOWN", f"packet type 0x{packet_type:02x} "
                                    f"raw={data.hex(' ')}")

    def telemetry_loop(self) -> None:
        tick = 0.05
        last_ping = 0.0

        while not self.stop.is_set():
            time.sleep(tick)
            if not self.connected:
                continue

            self.state.step(tick)
            self.emit_all_telemetry()

            now = time.time()
            if now - last_ping >= 1.0:
                last_ping = now
                self.ping_seq = (self.ping_seq + 1) % 256
                self.send(CONNECTION, bytes([PING, self.ping_seq]))

                if now - self.last_pong > CLIENT_MAX_INACTIVITY:
                    self.log("session", "no PONG for 3 s -- dropping client")
                    self.disconnect()

    def close(self) -> None:
        self.stop.set()
        if self.connected:
            self.send(CONNECTION, bytes([TERMINATE]))
        self.sock.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Mock Spider robot")
    parser.add_argument("--port", type=int, default=8888)
    parser.add_argument("--platform", choices=list(PLATFORMS),
                        default="SPIDER_30")
    parser.add_argument("--quiet-telemetry", action="store_true",
                        help="only log errors and unknown message types")
    args = parser.parse_args()

    robot = MockSpider(args.port, args.platform,
                       log_telemetry=not args.quiet_telemetry)
    try:
        robot.run()
    except KeyboardInterrupt:
        print(f"[{time.strftime('%H:%M:%S')}] Close: Keyboard interruption accepted, closing server...", flush=True)
    finally:
        robot.close()


if __name__ == "__main__":
    main()
