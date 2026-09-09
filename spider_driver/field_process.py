"""Field-side relay: home Teensy datagrams <-> robot or mock robot."""

from collections import deque
import socket
import threading
import time

from spider_logic import Spider
from teensy_protocol import flipper_velocity_packet, twist_packet, validate_command_packet


class RobotLink:
    def __init__(self, robot_host, robot_port, telemetry_callback):
        self.robot_host = robot_host
        self.robot_port = robot_port
        self.telemetry_callback = telemetry_callback
        self._pending = deque(maxlen=256)
        self._condition = threading.Condition()
        self._stop = False
        self.connected = threading.Event()

    def put(self, packet):
        with self._condition:
            self._pending.append((time.monotonic(), packet))
            self._condition.notify()

    def stop(self):
        with self._condition:
            self._stop = True
            self._condition.notify_all()

    def run(self):
        while not self._stop:
            spider = Spider(self.robot_host, self.robot_port, self.telemetry_callback)
            try:
                spider.connect(timeout=2.0)
                self.connected.set()
                self._forward_loop(spider)
            except (OSError, TimeoutError) as exc:
                print(f"[field] robot unavailable: {exc}; retrying")
            finally:
                self.connected.clear()
                spider.close()
            with self._condition:
                self._condition.wait(timeout=1.0)

    def _forward_loop(self, spider):
        while not self._stop and spider.connected_to_server.is_set():
            item = None
            with self._condition:
                if not self._pending:
                    self._condition.wait(timeout=0.1)
                if self._pending:
                    item = self._pending.popleft()
            if item is None:
                continue
            created, packet = item
            if time.monotonic() - created <= 0.5:
                spider.send_command_packet(packet)


class FieldRelay:
    STOP_BURST = 5
    STOP_PERIOD = 0.05

    def __init__(self, listen_host, listen_port, robot_host, robot_port,
                 safety_timeout=0.5, allowed_home_ip=None):
        self.listen_address = (listen_host, listen_port)
        self.allowed_home_ip = allowed_home_ip
        self.safety_timeout = safety_timeout
        self.home_address = None
        self.last_command = None
        self.safe = True
        self._next_stop = 0.0
        self._stop_sends_left = 0
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(self.listen_address)
        self.socket.settimeout(0.05)
        self.robot = RobotLink(robot_host, robot_port, self._relay_telemetry)
        self.robot_thread = threading.Thread(target=self.robot.run, daemon=True)

    def run(self):
        self.robot_thread.start()
        print(f"[field] listening on {self.socket.getsockname()[0]}:{self.socket.getsockname()[1]}")
        try:
            while True:
                self._receive_once()
                self._check_safety()
        except KeyboardInterrupt:
            print("\n[field] stopping")
        finally:
            self._arm_stop()
            for _ in range(self.STOP_BURST):
                self._send_stop()
                time.sleep(self.STOP_PERIOD)
            self.robot.stop()
            self.robot_thread.join(timeout=3.0)
            self.socket.close()

    def _receive_once(self):
        try:
            packet, source = self.socket.recvfrom(4096)
        except socket.timeout:
            return
        if self.allowed_home_ip and source[0] != self.allowed_home_ip:
            return
        try:
            validate_command_packet(packet)
        except ValueError as exc:
            print(f"[field] rejected packet from {source[0]}:{source[1]}: {exc}")
            return
        self.home_address = source
        self.last_command = time.monotonic()
        if self.safe:
            print(f"[field] command source {source[0]}:{source[1]} active")
        self.safe = False
        self._stop_sends_left = 0
        self.robot.put(packet)

    def _check_safety(self):
        now = time.monotonic()
        if not self.safe and self.last_command is not None and now - self.last_command >= self.safety_timeout:
            print("[field] home command timeout; sending safe stop")
            self._arm_stop()
        if self._stop_sends_left and now >= self._next_stop:
            self._send_stop()
            self._next_stop = now + self.STOP_PERIOD

    def _arm_stop(self):
        self.safe = True
        self._stop_sends_left = self.STOP_BURST
        self._next_stop = 0.0

    def _send_stop(self):
        self.robot.put(twist_packet(0.0, 0.0))
        self.robot.put(flipper_velocity_packet((0.0, 0.0, 0.0, 0.0)))
        self._stop_sends_left = max(0, self._stop_sends_left - 1)

    def _relay_telemetry(self, packet):
        if self.home_address is None:
            return
        try:
            self.socket.sendto(packet, self.home_address)
        except OSError:
            pass
