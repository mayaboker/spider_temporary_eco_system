"""Encoding and validation for the Teensy command/telemetry wire API."""

import struct
from typing import Optional, Tuple

from definitions import Commands, Header, PlatformType, Telemetry


COMMAND_PAYLOAD_SIZES = {
    Commands.TWIST: {struct.calcsize("<2f")},
    Commands.FLIP_POS: {struct.calcsize("<4f")},
    Commands.FLIP_VEL: {struct.calcsize("<4f")},
    Commands.PID: {struct.calcsize("<2f"), struct.calcsize("<6f")},
    Commands.LIGHTS: {4},
    Commands.CALIBRATE: {1},
}


def command_packet(command: Commands, payload: bytes = b"") -> bytes:
    packet = bytes((Header.TYPED, command)) + payload
    validate_command_packet(packet)
    return packet


def validate_command_packet(packet: bytes) -> Commands:
    if len(packet) < 2 or packet[0] != Header.TYPED:
        raise ValueError("expected a Teensy TYPED command datagram")
    try:
        command = Commands(packet[1])
    except ValueError as exc:
        raise ValueError(f"unsupported Teensy command 0x{packet[1]:02x}") from exc
    payload_size = len(packet) - 2
    if payload_size not in COMMAND_PAYLOAD_SIZES[command]:
        expected = sorted(COMMAND_PAYLOAD_SIZES[command])
        raise ValueError(f"invalid {command.name} payload size {payload_size}; expected {expected}")
    if command is Commands.CALIBRATE and packet[2] != 0x01:
        raise ValueError("CALIBRATE payload must be 0x01")
    return command


def is_telemetry_packet(packet: bytes) -> bool:
    return len(packet) >= 2 and packet[0] == Header.TYPED and 0x10 <= packet[1] <= 0x1F


def twist_packet(linear: float, angular: float) -> bytes:
    return command_packet(Commands.TWIST, struct.pack("<2f", linear, angular))


def flipper_position_packet(values) -> bytes:
    return command_packet(Commands.FLIP_POS, struct.pack("<4f", *values))


def flipper_velocity_packet(values) -> bytes:
    return command_packet(Commands.FLIP_VEL, struct.pack("<4f", *values))


def pid_packet(allowed: bool, platform: PlatformType) -> bytes:
    if platform is PlatformType.NONE:
        raise ValueError("PID command requires PLATFORM telemetry")
    if platform is PlatformType.SPIDER_30:
        enabled = (20.0, 20.0, 20.0, 20.0, 25.0, 50.0)
        values = enabled if allowed else (0.0,) * 6
        payload = struct.pack("<6f", *values)
    else:
        values = (15.0, 4.0) if allowed else (0.0, 0.0)
        payload = struct.pack("<2f", *values)
    return command_packet(Commands.PID, payload)


def lights_packet(vis, nir) -> bytes:
    if len(vis) != 2 or len(nir) != 2:
        raise ValueError("LIGHTS requires two VIS and two NIR channels")
    return command_packet(Commands.LIGHTS, struct.pack("<4B", *vis, *nir))


def calibrate_packet() -> bytes:
    return command_packet(Commands.CALIBRATE, b"\x01")


def platform_from_telemetry(packet: bytes) -> Optional[PlatformType]:
    if len(packet) < 3 or packet[:2] != bytes((Header.TYPED, Telemetry.PLATFORM)):
        return None
    try:
        return PlatformType(struct.unpack("=b", packet[2:3])[0])
    except ValueError:
        return None


def telemetry_summary(packet: bytes, platform: PlatformType) -> Tuple[int, str]:
    """Decode known Teensy telemetry for display, preserving unknown data as hex."""
    if not is_telemetry_packet(packet):
        raise ValueError("expected a Teensy TYPED telemetry datagram")
    msg_type, payload = packet[1], packet[2:]
    try:
        if msg_type == Telemetry.PLATFORM:
            reported = platform_from_telemetry(packet)
            return msg_type, f"Platform: {reported.name if reported is not None else 'UNKNOWN'}"
        if msg_type == 0x12 and len(payload) == struct.calcsize("=4f"):
            values = struct.unpack("=4f", payload)
            return msg_type, "Tracks: " + _named_values(("left velocity", "right velocity", "left position", "right position"), values)
        if msg_type == 0x13 and len(payload) == struct.calcsize("=4f"):
            values = struct.unpack("=4f", payload)
            return msg_type, "Flippers: " + _named_values(("FL", "FR", "RR", "RL"), values)
        if msg_type == 0x15 and len(payload) == struct.calcsize("=3f"):
            values = struct.unpack("=3f", payload)
            return msg_type, "Attitude: " + _named_values(("roll", "pitch", "yaw"), values)
        if msg_type == 0x11:
            fmt = "=BfhdB" if platform is PlatformType.SPIDER_30 else "=B3fB"
            rows = _unpack_rows(fmt, payload)
            if rows is not None:
                return msg_type, "Motor data:\n" + "\n".join(_motor_data_row(row, platform) for row in rows)
        if msg_type == 0x14:
            fmt = "=Bb6Bf" if platform is PlatformType.SPIDER_30 else "=B2f6ff"
            rows = _unpack_rows(fmt, payload)
            if rows is not None:
                return msg_type, "Motor peripherals:\n" + "\n".join(_peripheral_row(row) for row in rows)
    except struct.error:
        pass
    payload_hex = " ".join(f"{value:02x}" for value in payload)
    return msg_type, f"Telemetry 0x{msg_type:02x}: {payload_hex}"


def _named_values(names, values):
    return ", ".join(f"{name}={value:.3f}" for name, value in zip(names, values))


def _unpack_rows(fmt, payload):
    size = struct.calcsize(fmt)
    if not payload or len(payload) % size:
        return None
    return [struct.unpack(fmt, payload[offset:offset + size]) for offset in range(0, len(payload), size)]


def _motor_data_row(row, platform):
    motor_id = row[0]
    if platform is PlatformType.SPIDER_30:
        _, current, velocity_raw, position, status = row
        velocity = velocity_raw / 100.0
    else:
        _, current, velocity, position, status = row
    return f"  {motor_id}: current={current:.3f}, velocity={velocity:.3f}, position={position:.3f}, status={status}"


def _peripheral_row(row):
    values = ", ".join(f"{value:.3f}" if isinstance(value, float) else str(value) for value in row[1:])
    return f"  {row[0]}: {values}"
