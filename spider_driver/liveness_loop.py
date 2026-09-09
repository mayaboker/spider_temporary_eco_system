import socket
import struct
from threading import Thread

from definitions import Communication, Header, PlatformType, Telemetry


class LivenessLoop(Thread):
    def __init__(self, socket_to_server, server_address,
                 stop_activity_flag, connected_to_server, on_typed_packet=None):
        super().__init__(daemon=True)
        self.socket_to_server = socket_to_server
        self.server_address = server_address
        self.stop_activity_flag = stop_activity_flag
        self.connected_to_server = connected_to_server
        self.on_typed_packet = on_typed_packet
        # last platform type reported by the robot (telemetry 0x10)
        self.platform_type = PlatformType.NONE

    def run(self):
        while not self.stop_activity_flag.is_set():
            try:
                data, source = self.socket_to_server.recvfrom(4096)
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                return
            if source != self.server_address or not data:
                continue
            self._handle_sync(data)

    def send(self, packet_type, payload):
        self.socket_to_server.sendto(bytes([packet_type]) + payload, self.server_address)

    def _handle_sync(self, data):
        if len(data) < 2:
            return
        header = data[0]
        if header == Header.CONNECTION:
            self._handle_connection(data)
        elif header == Header.RAW:
            self._handle_raw(data)
        elif header == Header.TYPED:
            if self.on_typed_packet is not None:
                self.on_typed_packet(data)
            self._handle_typed(data)

    def _handle_raw(self, data):
        print(f"[raw] len={len(data) - 1}, data = {data[1:]}")

    def _handle_typed(self, data):
        # print(f"[telemetry] type=0x{data[1]:02x}, len={len(data) - 2}, data = {data[2:]}")
        if data[1] == Telemetry.PLATFORM:
            self._handle_platform_type(data[2:])

    def _handle_platform_type(self, payload):
        if len(payload) < 1:
            return
        value = struct.unpack("=b", payload[:1])[0]
        try:
            platform_type = PlatformType(value)
        except ValueError:
            print(f"[telemetry] unknown platform type: {value}")
            return
        if platform_type is not self.platform_type:
            self.platform_type = platform_type
            print(f"[telemetry] platform: {platform_type.name}")

    def _handle_connection(self, data):
        message = data[1]
        if message == Communication.SYN_ACK:
            self.send(Header.CONNECTION, bytes([Communication.ACK]))
            self.connected_to_server.set()
            print("[session] connected")
        elif message == Communication.PING and len(data) > 2:
            self.send(Header.CONNECTION, bytes([Communication.PONG, data[2]]))
        elif message == Communication.TERMINATE:
            self.connected_to_server.clear()
            print("[session] robot terminated")
