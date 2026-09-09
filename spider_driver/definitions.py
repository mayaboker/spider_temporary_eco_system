from enum import IntEnum


class Header(IntEnum):
    CONNECTION = 0x01
    RAW = 0x02
    TYPED = 0x03


class Communication(IntEnum):
    SYN = 0x01
    SYN_ACK = 0x02
    ACK = 0x03
    PING = 0x10
    PONG = 0x11
    TERMINATE = 0xFF


class Telemetry(IntEnum):
    PLATFORM = 0x10


class PlatformType(IntEnum):
    NONE = 0
    SPIDER_10 = 1
    SPIDER_20 = 2
    SPIDER_30 = 3


class Commands(IntEnum):
    TWIST = 0x40
    FLIP_POS = 0x41
    FLIP_VEL = 0x42
    PID = 0x44
    LIGHTS = 0x45
    CALIBRATE = 0x46

SERVER_IP = "10.42.17.3"
SERVER_PORT = 8888

FRAME_RATE_HZ = 20
CONTROL_RATE_HZ = 4

FLIPPER_PRESETS = {
    "flat": (0.0, 0.0, 0.0, 0.0),
    "stand": (-70.0, -70.0, -70.0, -70.0),
    "fold": (70.0, 70.0, 70.0, 70.0),
    "climb": (25.0, 25.0, -25.0, -25.0),
}
