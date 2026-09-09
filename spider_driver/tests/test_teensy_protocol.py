import struct
import unittest

from definitions import Commands, Header, PlatformType
from teensy_protocol import (
    calibrate_packet,
    flipper_position_packet,
    flipper_velocity_packet,
    lights_packet,
    pid_packet,
    platform_from_telemetry,
    telemetry_summary,
    twist_packet,
    validate_command_packet,
)


class TeensyProtocolTests(unittest.TestCase):
    def test_all_supported_packets_validate(self):
        packets = (
            twist_packet(0.5, -0.25),
            flipper_position_packet((1, 2, 3, 4)),
            flipper_velocity_packet((1, 2, 3, 4)),
            pid_packet(True, PlatformType.SPIDER_10),
            pid_packet(False, PlatformType.SPIDER_30),
            lights_packet((1, 2), (3, 4)),
            calibrate_packet(),
        )
        self.assertEqual(
            [validate_command_packet(packet) for packet in packets],
            [Commands.TWIST, Commands.FLIP_POS, Commands.FLIP_VEL,
             Commands.PID, Commands.PID, Commands.LIGHTS, Commands.CALIBRATE],
        )

    def test_twist_is_exact_teensy_wire_packet(self):
        expected = bytes((Header.TYPED, Commands.TWIST)) + struct.pack("<2f", 0.5, -0.25)
        self.assertEqual(twist_packet(0.5, -0.25), expected)

    def test_rejects_session_and_malformed_packets(self):
        for packet in (b"", b"\x01\x01", b"\x03\x40", b"\x03\x46\x00"):
            with self.subTest(packet=packet), self.assertRaises(ValueError):
                validate_command_packet(packet)

    def test_platform_telemetry(self):
        packet = bytes((Header.TYPED, 0x10, PlatformType.SPIDER_30))
        self.assertIs(platform_from_telemetry(packet), PlatformType.SPIDER_30)

    def test_common_telemetry_summaries(self):
        tracks = bytes((Header.TYPED, 0x12)) + struct.pack("=4f", 1, 2, 3, 4)
        attitude = bytes((Header.TYPED, 0x15)) + struct.pack("=3f", 0.1, 0.2, 0.3)
        self.assertIn("left velocity=1.000", telemetry_summary(tracks, PlatformType.SPIDER_30)[1])
        self.assertIn("yaw=0.300", telemetry_summary(attitude, PlatformType.SPIDER_30)[1])


if __name__ == "__main__":
    unittest.main()
