import socket
import struct
import threading
import time

from definitions import Commands, Header, Communication, PlatformType
from liveness_loop import LivenessLoop


class Spider:
    # PID constants per platform, as (allowed, denied). The tuple length has to
    # match the pack format chosen in send_control_consts, so both live here.
    SPIDER_10_20_CONTROL = ((15.0, 4.0), (0.0, 0.0))
    SPIDER_30_CONTROL = ((20.0, 20.0, 20.0, 20.0, 25.0, 50.0), (0.0,) * 6)

    def __init__(self, ip, port, on_typed_packet=None):
        self.server_address = (ip, port)
        self.socket_to_server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket_to_server.settimeout(0.05)
        self.connected_to_server = threading.Event()
        self.stop_activity_flag = threading.Event()
        self.liveness_loop = LivenessLoop(
            self.socket_to_server,
            self.server_address,
            self.stop_activity_flag,
            self.connected_to_server,
            on_typed_packet,
        )

    @property
    def get_platform_type(self):
        return self.liveness_loop.platform_type

    def send(self, packet_type, payload):
        self.socket_to_server.sendto(bytes([packet_type]) + payload, self.server_address)

    def send_command(self, msg_type, payload=b""):
        self.send(Header.TYPED, bytes([msg_type]) + payload)

    def send_command_packet(self, packet):
        """Send an already validated full TYPED command datagram unchanged."""
        from teensy_protocol import validate_command_packet

        validate_command_packet(packet)
        self.socket_to_server.sendto(packet, self.server_address)

    def connect(self, timeout=10.0):
        self.liveness_loop.start()
        try:
            deadline = time.time() + timeout
            while time.time() < deadline:
                self.send(Header.CONNECTION, bytes([Communication.SYN]))
                if self.connected_to_server.wait(1.0):   # set by the liveness loop
                    return
            raise TimeoutError(f"no SYN_ACK from {self.server_address}")
        except BaseException as e:
            print(f"[spider_logic] {e}")
            self.close()
            raise

    def close(self):
        if self.stop_activity_flag.is_set():
            return
        self.stop_activity_flag.set()
        if self.connected_to_server.is_set():
            self.send(Header.CONNECTION, bytes([Communication.TERMINATE]))
        if self.liveness_loop.is_alive():
            self.liveness_loop.join(timeout=1.0)
        self.socket_to_server.close()

    # --- commands -----------------------------------------------------
    def send_twist(self, linear, angular_rads):
        self.send_command(Commands.TWIST, struct.pack("<2f", linear, angular_rads))

    def send_flippers_position(self, fl, fr, rr, rl):
        self.send_command(Commands.FLIP_POS, struct.pack("<4f", fl, fr, rr, rl))

    def send_flippers_velocity(self, fl, fr, rr, rl):
        self.send_command(Commands.FLIP_VEL, struct.pack("<4f", fl, fr, rr, rl))

    def send_control_consts(self, allowed):
        platform = self.get_platform_type
        if platform is PlatformType.NONE:
            return
        if platform is PlatformType.SPIDER_30:
            consts = self.SPIDER_30_CONTROL[0 if allowed else 1]
            payload = struct.pack("<6f", *consts)
        else:
            consts = self.SPIDER_10_20_CONTROL[0 if allowed else 1]
            payload = struct.pack("<2f", *consts)
        self.send_command(Commands.PID, payload)

    # This command is not used and not tested for now.
    def lights(self, vis, nir, master=True, enable_vis=True, enable_nir=True):
        channels = len(vis)
        if len(nir) != channels:
            raise ValueError("vis and nir must have the same number of channels")
        if not master:
            payload = bytes(2 * channels)
        else:
            vis = vis if enable_vis else [0] * channels
            nir = nir if enable_nir else [0] * channels
            payload = struct.pack(f"<{2 * channels}B", *vis, *nir)
        self.send_command(Commands.LIGHTS, payload)

    # This command is not used and not tested for now.
    def calibrate_encoders(self):
        self.send_command(Commands.CALIBRATE, bytes([0x01]))
