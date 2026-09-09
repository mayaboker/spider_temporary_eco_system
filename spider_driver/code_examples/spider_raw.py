#!/usr/bin/env python3
"""Minimal raw client for the Spider robot. No project code used.

Usage:  python3 spider_raw.py <ip> <port>
"""
import socket
import struct
import sys
import threading
import time

CONNECTION, RAW, TYPED = 0x01, 0x02, 0x03
SYN, SYN_ACK, ACK, PING, PONG, TERMINATE = 0x01, 0x02, 0x03, 0x10, 0x11, 0xFF

TWIST, FLIP_POS, FLIP_VEL, PID, LIGHTS, CALIBRATE = 0x40, 0x41, 0x42, 0x44, 0x45, 0x46


class Spider:
    def __init__(self, ip, port):
        self.addr = (ip, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(0.05)
        self.connected = False
        self._stop = threading.Event()

    # --- wire ---------------------------------------------------------
    def _send(self, packet_type, payload):
        self.sock.sendto(bytes([packet_type]) + payload, self.addr)

    def send_command(self, msg_type, payload=b""):
        self._send(TYPED, bytes([msg_type]) + payload)

    # --- session ------------------------------------------------------
    def connect(self, timeout=10.0):
        """Send SYN until SYN_ACK arrives, then ACK. Starts the RX thread."""
        threading.Thread(target=self._rx_loop, daemon=True).start()
        deadline = time.time() + timeout
        while not self.connected and time.time() < deadline:
            self._send(CONNECTION, bytes([SYN]))
            time.sleep(1.0)
        if not self.connected:
            raise TimeoutError(f"no SYN_ACK from {self.addr}")

    def close(self):
        self._stop.set()
        if self.connected:
            self._send(CONNECTION, bytes([TERMINATE]))
        self.sock.close()

    def _rx_loop(self):
        """Answers PINGs. Without this the robot drops the session."""
        while not self._stop.is_set():
            try:
                data, src = self.sock.recvfrom(4096)
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                return
            if src != self.addr or not data:
                continue

            if data[0] == CONNECTION and len(data) > 1:
                if data[1] == SYN_ACK:
                    self._send(CONNECTION, bytes([ACK]))
                    self.connected = True
                    print("[session] connected")
                elif data[1] == PING and len(data) > 2:
                    self._send(CONNECTION, bytes([PONG, data[2]]))
                elif data[1] == TERMINATE:
                    self.connected = False
                    print("[session] robot terminated")

            elif data[0] == TYPED and len(data) > 1:
                print(f"[telemetry] type=0x{data[1]:02x} len={len(data) - 2}")

    # --- commands -----------------------------------------------------
    def drive(self, linear, angular, duration, rate_hz=20):
        """Hold a twist for `duration` seconds, then stop.

        The command MUST be repeated -- the robot's watchdog zeroes motion
        as soon as packets stop arriving.
        """
        period = 1.0 / rate_hz
        deadline = time.time() + duration
        while time.time() < deadline:
            self.send_command(TWIST, struct.pack("<2f", linear, angular))
            time.sleep(period)
        for _ in range(5):                      # explicit stop, repeated
            self.send_command(TWIST, struct.pack("<2f", 0.0, 0.0))
            time.sleep(period)

    def flippers_position(self, a, b, c, d, duration, rate_hz=20):
        period = 1.0 / rate_hz
        deadline = time.time() + duration
        while time.time() < deadline:
            self.send_command(FLIP_POS, struct.pack("<4f", a, b, c, d))
            time.sleep(period)

    def calibrate_encoders(self):
        self.send_command(CALIBRATE, bytes([0x01]))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(f"usage: {sys.argv[0]} <ip> <port>")

    robot = Spider(sys.argv[1], int(sys.argv[2]))
    robot.connect()
    try:
        print("driving forward 2 s")
        robot.drive(linear=0.2, angular=0.0, duration=2.0)
    finally:
        robot.close()
