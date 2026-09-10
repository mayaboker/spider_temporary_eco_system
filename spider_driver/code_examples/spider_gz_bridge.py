#!/usr/bin/env python3
"""Teensy protocol <-> Gazebo bridge.

Stands in for the robot, exactly like spider_mock_server.py, but drives a real
Gazebo model through ros2_control instead of toy physics. Nothing upstream
changes: home and field keep speaking the same Teensy datagrams.

    G29 -> home -> :9999 -> field -> :8888 -> THIS -> ROS topics -> Gazebo
                                                 ^
                            telemetry <- /joint_states

The matching spider_simulation launch file uses Gazebo's core DiffDrive and
JointPositionController systems (not ros2_control):

  * TWIST      -> /cmd_vel
  * FLIP_VEL   -> integrated into four /flipper/* position setpoints
  * FLIP_POS   -> same setpoint, set directly
  * PID        -> authority gate; zero gains freeze the flipper setpoint

Run it through the launcher, which sources ROS and picks the right interpreter:

    ./run_gz_bridge.sh

It must not run inside the project venv -- see the note at the import below.
"""
import argparse
import math
import socket
import struct
import sys
import time

try:
    import rclpy
    from rclpy.node import Node
    from geometry_msgs.msg import Twist
    from nav_msgs.msg import Odometry
    from sensor_msgs.msg import JointState
    from std_msgs.msg import Float64
except ModuleNotFoundError as exc:
    # rclpy itself arrives via PYTHONPATH once ROS is sourced, but it imports
    # yaml, numpy and lark from system site-packages -- which a venv created
    # without --system-site-packages hides, so the failure surfaces as a
    # missing dependency of rclpy rather than a missing rclpy.
    hint = "Run ./run_gz_bridge.sh, or 'deactivate' the venv first."
    if sys.prefix != sys.base_prefix:
        hint = (f"You are inside the venv at {sys.prefix}, which hides the "
                f"system packages rclpy needs.\n{hint}")
    raise SystemExit(f"{exc}\n\n{hint}") from exc

# Wire constants -- kept standalone so this file matches spider_mock_server.py
# and needs nothing from the project package on sys.path.
CONNECTION, RAW, TYPED = 0x01, 0x02, 0x03
SYN, SYN_ACK, ACK, PING, PONG, TERMINATE = 0x01, 0x02, 0x03, 0x10, 0x11, 0xFF
PLATFORMS = {"NONE": 0, "SPIDER_10": 1, "SPIDER_20": 2, "SPIDER_30": 3}
CLIENT_MAX_INACTIVITY = 3.0
MOTOR_COUNT = 8

WHEEL_RADIUS = 0.1

# Inner track wheel of each flipper, the one on the arm pivot axis. Each
# flipper also carries an outer "_track_wheel_2_" wheel, which spins
# identically, so these four are enough for motor and track telemetry.
WHEEL_JOINTS = ("fl_track_wheel_joint", "fr_track_wheel_joint", "rl_track_wheel_joint", "rr_track_wheel_joint")
FLIPPER_JOINTS = ("fl_flipper_joint", "fr_flipper_joint",
                  "rl_flipper_joint", "rr_flipper_joint")

# The Teensy sends flippers CLOCKWISE -- FL, FR, RR, RL -- while the controller
# expects fl, fr, rl, rr. Getting this wrong silently swaps the rear pair.
TEENSY_TO_CONTROLLER = (0, 1, 3, 2)

# The flippers are mirrored about the centre of the vehicle: the rear arms
# extend along -x, so an identical joint angle swings them the opposite way in
# space. The Teensy convention is that a positive command raises every arm, so
# the front pair reaches "up" through a negative joint angle and has to be
# negated here. Signs are in Teensy order, FL, FR, RR, RL.
FLIPPER_SIGN = (-1.0, -1.0, 1.0, 1.0)

# The spider arm joints travel +/-1.46 rad; clamping just inside that keeps
# the integrated setpoint from fighting the joint limit.
FLIPPER_LIMIT_DEG = 83.6


def reorder_flippers(teensy_values):
    """(FL, FR, RR, RL) -> (fl, fr, rl, rr)."""
    return [teensy_values[index] for index in TEENSY_TO_CONTROLLER]


def restore_flipper_order(controller_values):
    """(fl, fr, rl, rr) -> (FL, FR, RR, RL)."""
    restored = [0.0] * 4
    for teensy_index, controller_index in enumerate(TEENSY_TO_CONTROLLER):
        restored[teensy_index] = controller_values[controller_index]
    return restored


def apply_flipper_sign(teensy_values):
    """Teensy "positive is up" <-> mirrored joint angles.

    Self-inverse, since every sign is +/-1, so the same call converts a command
    on the way in and a measurement on the way back out.
    """
    return [value * sign for value, sign in zip(teensy_values, FLIPPER_SIGN)]


class SpiderGzBridge(Node):
    def __init__(self, port, platform, flipper_scale, verbose):
        super().__init__("spider_gz_bridge")
        self.platform = platform
        # Degrees on the Teensy side are almost certainly what the real robot
        # uses (see FLIPPER_PRESETS), but that is unconfirmed -- this scale
        # exists so the mapping can be corrected in one place.
        self.flipper_scale = flipper_scale
        self.verbose = verbose

        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("0.0.0.0", port))
        self.socket.setblocking(False)

        self.client_address = None
        self.connected = False
        self.ping_seq = 0
        self.last_pong = 0.0
        self.unknown_types = set()

        self.linear = 0.0
        self.angular = 0.0
        self.yaw = 0.0
        # Flipper setpoints in Teensy units (degrees), Teensy order.
        self.flipper_target = [0.0] * 4
        self.flipper_rate = [0.0] * 4
        self.flippers_allowed = False
        self.joint_state = {}

        self.pub_twist = self.create_publisher(Twist, "/cmd_vel", 10)
        self.pub_flippers = [
            self.create_publisher(Float64, f"/flipper/{name}", 10)
            for name in ("fl", "fr", "rl", "rr")
        ]
        self.create_subscription(JointState, "/joint_states", self._on_joint_state, 10)
        self.create_subscription(Odometry, "/odom", self._on_odometry, 10)

        self.create_timer(0.005, self._pump_udp)       # 200 Hz socket drain
        self.create_timer(0.02, self._publish_control)  # 50 Hz to ros2_control
        self.create_timer(0.05, self._publish_telemetry)  # 20 Hz, matches the mock
        self.create_timer(1.0, self._session_tick)

        self.get_logger().info(
            f"listening on UDP {port} as {platform}; "
            f"point field at it with --robot-host <this host> --robot-port {port}")

    # -- ROS side ---------------------------------------------------------
    def _on_joint_state(self, message):
        for index, name in enumerate(message.name):
            position = message.position[index] if index < len(message.position) else 0.0
            velocity = message.velocity[index] if index < len(message.velocity) else 0.0
            effort = message.effort[index] if index < len(message.effort) else 0.0
            self.joint_state[name] = (position, velocity, effort)

    def _on_odometry(self, message):
        orientation = message.pose.pose.orientation
        # Quaternion -> yaw. Gazebo odometry is authoritative; integrating the
        # requested angular velocity drifts whenever the robot slips or stalls.
        self.yaw = math.atan2(
            2.0 * (orientation.w * orientation.z + orientation.x * orientation.y),
            1.0 - 2.0 * (orientation.y ** 2 + orientation.z ** 2),
        )

    def _publish_control(self):
        twist = Twist()
        twist.linear.x = self.linear
        twist.angular.z = self.angular
        self.pub_twist.publish(twist)

        # FLIP_VEL is integrated rather than passed through: under a velocity
        # controller the flippers would sag under gravity whenever the command
        # is zero, where the real Teensy holds position with a PID loop.
        if self.flippers_allowed:
            for index in range(4):
                self.flipper_target[index] = max(
                    -FLIPPER_LIMIT_DEG,
                    min(FLIPPER_LIMIT_DEG,
                        self.flipper_target[index] + self.flipper_rate[index] * 0.02))

        positions = [math.radians(value) * self.flipper_scale
                     for value in reorder_flippers(
                         apply_flipper_sign(self.flipper_target))]
        for publisher, position in zip(self.pub_flippers, positions):
            command = Float64()
            command.data = position
            publisher.publish(command)

    def _joint(self, name):
        return self.joint_state.get(name, (0.0, 0.0, 0.0))

    # -- UDP session ------------------------------------------------------
    def _pump_udp(self):
        while True:
            try:
                data, source = self.socket.recvfrom(4096)
            except BlockingIOError:
                return
            except OSError:
                return
            if not data:
                continue
            if self.connected and source != self.client_address:
                continue
            header, payload = data[0], data[1:]
            if header == CONNECTION:
                self._handle_connection(payload, source)
            elif header == TYPED:
                self._handle_typed(payload)

    def send(self, header, payload):
        if self.client_address is None:
            return
        try:
            self.socket.sendto(bytes([header]) + payload, self.client_address)
        except OSError:
            pass

    def send_telemetry(self, msg_type, payload):
        self.send(TYPED, bytes([msg_type]) + payload)

    def _handle_connection(self, payload, source):
        if not payload:
            return
        kind = payload[0]
        if kind == SYN:
            self.client_address = source
            self.send(CONNECTION, bytes([SYN_ACK]))
            self.get_logger().info(f"SYN from {source[0]}:{source[1]} -> SYN_ACK")
        elif kind == ACK:
            self.connected = True
            self.last_pong = time.time()
            self.get_logger().info("ACK received -- connected")
        elif kind == PONG and len(payload) > 1:
            self.last_pong = time.time()
        elif kind == TERMINATE:
            self.get_logger().info("TERMINATE from client")
            self._disconnect()

    def _disconnect(self):
        if self.connected:
            self.connected = False
            self.get_logger().info("disconnected -- commanding stop")
        self.client_address = None
        self.linear = self.angular = 0.0
        self.flipper_rate = [0.0] * 4
        self.flippers_allowed = False

    def _session_tick(self):
        if not self.connected:
            return
        self.ping_seq = (self.ping_seq + 1) % 256
        self.send(CONNECTION, bytes([PING, self.ping_seq]))
        if time.time() - self.last_pong > CLIENT_MAX_INACTIVITY:
            self.get_logger().warning("no PONG for 3 s -- dropping client")
            self._disconnect()

    # -- commands ---------------------------------------------------------
    def _handle_typed(self, payload):
        if not payload:
            return
        msg_type, data = payload[0], payload[1:]
        if msg_type == 0x40 and len(data) == 8:
            self.linear, self.angular = struct.unpack("<2f", data)
        elif msg_type == 0x41 and len(data) == 16:
            self.flipper_target = list(struct.unpack("<4f", data))
            self.flipper_rate = [0.0] * 4
        elif msg_type == 0x42 and len(data) == 16:
            self.flipper_rate = list(struct.unpack("<4f", data))
        elif msg_type == 0x44:
            # Real gains mean the flipper loop has authority; all zeros is the
            # deny state the clutch sends on release.
            fmt = "<6f" if self.platform == "SPIDER_30" else "<2f"
            if len(data) == struct.calcsize(fmt):
                gains = struct.unpack(fmt, data)
                allowed = any(abs(value) > 1e-9 for value in gains)
                if allowed != self.flippers_allowed:
                    self.get_logger().info(
                        f"flipper authority {'GRANTED' if allowed else 'DENIED'}")
                self.flippers_allowed = allowed
        elif msg_type == 0x46:
            self.flipper_target = [0.0] * 4
            self.get_logger().info("encoder calibration -- flippers zeroed")
        elif msg_type not in (0x45,) and msg_type not in self.unknown_types:
            self.unknown_types.add(msg_type)
            self.get_logger().warning(f"unhandled command 0x{msg_type:02x}")
        if self.verbose and msg_type != 0x40:
            self.get_logger().info(f"command 0x{msg_type:02x} len={len(data)}")

    # -- telemetry --------------------------------------------------------
    def _publish_telemetry(self):
        if not self.connected:
            return
        self.send_telemetry(0x10, struct.pack("=b", PLATFORMS[self.platform]))
        self.send_telemetry(0x11, self._motor_data())
        self.send_telemetry(0x12, self._track_data())
        self.send_telemetry(0x13, struct.pack("=4f", *self._flipper_positions()))
        self.send_telemetry(0x14, self._peripheral_data())
        self.send_telemetry(0x15, struct.pack("=3f", 0.0, 0.0, self.yaw))

    def _flipper_positions(self):
        measured = [math.degrees(self._joint(name)[0]) / (self.flipper_scale or 1.0)
                    for name in FLIPPER_JOINTS]
        return apply_flipper_sign(restore_flipper_order(measured))

    def _track_data(self):
        left = self._joint("fl_track_wheel_joint")
        right = self._joint("fr_track_wheel_joint")
        return struct.pack("=4f", left[1] * WHEEL_RADIUS, right[1] * WHEEL_RADIUS,
                           left[0] * WHEEL_RADIUS, right[0] * WHEEL_RADIUS)

    def _motor_source(self, index):
        """Motors 0-1 are the tracks, 2-5 the flippers, 6-7 spare."""
        if index < 2:
            return self._joint(WHEEL_JOINTS[index])
        if index < 6:
            return self._joint(FLIPPER_JOINTS[index - 2])
        return (0.0, 0.0, 0.0)

    def _motor_data(self):
        chunks = []
        for index in range(MOTOR_COUNT):
            position, velocity, effort = self._motor_source(index)
            if self.platform == "SPIDER_30":
                # Spider 30 packs velocity as an int16 scaled by 100.
                chunks.append(struct.pack("=BfhdB", index, effort,
                                          int(velocity * 100), position, 0))
            else:
                chunks.append(struct.pack("=B3fB", index, effort,
                                          velocity, position, 0))
        return b"".join(chunks)

    def _peripheral_data(self):
        # No simulator source for temperatures or bus voltage; synthesized so
        # the dashboard's peripheral pane has something well-formed to decode.
        chunks = []
        for index in range(MOTOR_COUNT):
            if self.platform == "SPIDER_30":
                chunks.append(struct.pack("=Bb6Bf", index, 35 + index, *[10] * 6, 40.0))
            else:
                chunks.append(struct.pack("=B2f6ff", index, 35.0 + index,
                                          40.0 + index, *[1.0] * 6, 40.0))
        return b"".join(chunks)

    def destroy_node(self):
        if self.connected:
            self.send(CONNECTION, bytes([TERMINATE]))
        self.socket.close()
        super().destroy_node()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Teensy <-> Gazebo bridge")
    parser.add_argument("--port", type=int, default=8888)
    parser.add_argument("--platform", choices=list(PLATFORMS), default="SPIDER_30")
    parser.add_argument("--flipper-scale", type=float, default=1.0,
                        help="extra gain on flipper angles, for exaggerating motion")
    parser.add_argument("--verbose", action="store_true")
    args, ros_args = parser.parse_known_args(argv)

    rclpy.init(args=ros_args)
    node = SpiderGzBridge(args.port, args.platform, args.flipper_scale, args.verbose)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
