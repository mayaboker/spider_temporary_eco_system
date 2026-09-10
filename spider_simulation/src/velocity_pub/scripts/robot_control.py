#!/usr/bin/python3
"""Joystick teleop for the Spider model, straight into the simulation topics.

This bypasses the Teensy chain entirely. Use it to drive the sim from a
gamepad without running home, field and the bridge:

    ros2 launch velocity_pub four_ws_control.launch.py

For the real control path -- G29 -> home -> field -> Teensy UDP -> Gazebo --
use spider_driver/code_examples/spider_gz_bridge.py instead.

Two things about this node are worth knowing, because both changed with the
current Spider model:

Spider has no steering joints. This file used to compute four-wheel-steering
angles (opposite phase, in-phase, pivot turn) for a car-like base, and publish
them to /forward_position_controller/commands. On this robot that controller is
bound to the four flipper joints, so those steering angles would have been
swinging the track arms. The modes are gone; skid steer is the only geometry
Spider has.

Nothing publishes to ros2_control here. spider_sim.launch.py drives the model
through core gz-sim systems, and bridges /cmd_vel and /flipper/* for exactly
that reason (see the note in spider_robot.gazebo.xacro). So this node speaks
those topics, and DiffDrive does the track mixing -- which is why no wheel
radius or separation appears below. Keeping a third copy of that geometry in
sync with the URDF and the bridge was never going to work.
"""
import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from sensor_msgs.msg import Joy
from std_msgs.msg import Float64

FLIPPERS = ("fl", "fr", "rl", "rr")

# The flippers are mirrored about the centre of the vehicle, so an identical
# joint angle raises the rear arms and lowers the front ones. A positive
# arm_angle below means "up" on every corner, which the front pair reaches
# through a negative joint angle. Matches FLIPPER_SIGN in the Teensy bridge.
ARM_JOINT_SIGN = {"fl": -1.0, "fr": -1.0, "rl": 1.0, "rr": 1.0}

MAX_LINEAR = 1.0      # m/s at full stick
MAX_ANGULAR = 1.5     # rad/s at full stick
ARM_LIMIT = 1.46      # rad; matches the revolute limit in suspension.urdf.xacro
ARM_RATE = 1.0        # rad/s while a bumper is held
PERIOD = 0.02         # 50 Hz, same rate the Teensy bridge publishes at

# Deadman. The field relay stops the robot after 500 ms without a command; a
# teleop that kept driving on a dead joystick would be the one unsafe path in
# the stack, so it uses the same timeout.
JOY_TIMEOUT = 0.5

# Xbox 360 pad layout, as the previous version assumed.
AXIS_LINEAR = 1       # left stick, vertical
AXIS_ANGULAR = 3      # right stick, horizontal
BUTTON_ARMS_FLAT = 0  # A
BUTTON_ARMS_DOWN = 4  # LB
BUTTON_ARMS_UP = 5    # RB


class Commander(Node):
    def __init__(self):
        super().__init__("commander")
        self.linear = 0.0
        self.angular = 0.0
        self.arm_rate = 0.0
        self.arm_angle = 0.0
        self.last_joy = None

        self.pub_twist = self.create_publisher(Twist, "/cmd_vel", 10)
        self.pub_flippers = {
            name: self.create_publisher(Float64, f"/flipper/{name}", 10)
            for name in FLIPPERS
        }
        self.create_subscription(Joy, "joy", self.on_joy, 10)
        self.create_timer(PERIOD, self.publish_once)

    def on_joy(self, message):
        self.last_joy = self.get_clock().now()

        def axis(index):
            return message.axes[index] if index < len(message.axes) else 0.0

        def button(index):
            return index < len(message.buttons) and message.buttons[index] == 1

        self.linear = axis(AXIS_LINEAR) * MAX_LINEAR
        self.angular = axis(AXIS_ANGULAR) * MAX_ANGULAR
        # All four arms move together, matching the operator dashboard's
        # buttons 19 and 20: positive is up on every corner.
        self.arm_rate = ARM_RATE * (float(button(BUTTON_ARMS_UP))
                                    - float(button(BUTTON_ARMS_DOWN)))
        if button(BUTTON_ARMS_FLAT):
            self.arm_angle = 0.0
            self.arm_rate = 0.0

    def joy_is_live(self):
        if self.last_joy is None:
            return False
        elapsed = (self.get_clock().now() - self.last_joy).nanoseconds / 1e9
        return elapsed < JOY_TIMEOUT

    def publish_once(self):
        live = self.joy_is_live()

        twist = Twist()
        if live:
            twist.linear.x = self.linear
            twist.angular.z = self.angular
        self.pub_twist.publish(twist)

        # A stale joystick stops the tracks but holds the arms where they are;
        # dropping them would be a surprise, and gravity is not an input.
        if live:
            self.arm_angle = max(-ARM_LIMIT,
                                 min(ARM_LIMIT,
                                     self.arm_angle + self.arm_rate * PERIOD))
        for name, publisher in self.pub_flippers.items():
            publisher.publish(Float64(data=self.arm_angle * ARM_JOINT_SIGN[name]))


def main(args=None):
    rclpy.init(args=args)
    node = Commander()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
