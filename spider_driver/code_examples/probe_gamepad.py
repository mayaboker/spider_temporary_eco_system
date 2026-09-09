"""Prints gamepad controls as you move them, to fill in GamepadController's constants.

    python3 code_examples/probe_gamepad.py

Move one control at a time and note the number it reports. For each trigger,
squeeze it slowly: if the value changes gradually it is an analog axis, and its
released value is what TRIGGER_RELEASED should be set to.
"""

import fcntl
import os
import struct
import sys
import time

JS_EVENT = struct.Struct("IhBB")        # __u32 time, __s16 value, __u8 type, __u8 number
JS_EVENT_BUTTON = 0x01
JS_EVENT_AXIS = 0x02
JS_EVENT_INIT = 0x80
AXIS_MAX = 32767.0

JSIOCGAXES = 0x80016a11
JSIOCGBUTTONS = 0x80016a12
JSIOCGNAME = 0x80806a13


def describe(fd):
    size = bytearray(1)
    fcntl.ioctl(fd, JSIOCGAXES, size)
    axes = size[0]
    fcntl.ioctl(fd, JSIOCGBUTTONS, size)
    buttons = size[0]
    name = bytearray(128)
    fcntl.ioctl(fd, JSIOCGNAME, name)
    return name.split(bytes([0]))[0].decode(errors="replace"), axes, buttons


def main():
    device = sys.argv[1] if len(sys.argv) > 1 else "/dev/input/js0"
    try:
        fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
    except OSError as e:
        print(f"[probe] cannot open {device}: {e}")
        return 1

    name, axes, buttons = describe(fd)
    print(f"{name}: {axes} axes, {buttons} buttons")
    print("move one control at a time -- Ctrl-C to stop\n")

    extremes = {}                       # axis number -> [lowest, highest] seen
    try:
        while True:
            try:
                data = os.read(fd, JS_EVENT.size)
            except BlockingIOError:
                time.sleep(0.01)
                continue

            _, value, kind, number = JS_EVENT.unpack(data)
            if kind & JS_EVENT_INIT:    # replayed state, not a real reading
                continue

            if kind == JS_EVENT_AXIS:
                scaled = value / AXIS_MAX
                low, high = extremes.get(number, (scaled, scaled))
                extremes[number] = (min(low, scaled), max(high, scaled))
                low, high = extremes[number]
                print(f"  axis   {number:2}  = {scaled:+.3f}   (range so far {low:+.3f} .. {high:+.3f})")
            elif kind == JS_EVENT_BUTTON:
                print(f"  button {number:2}  = {'down' if value else 'up'}")
    except KeyboardInterrupt:
        print("\n\nsummary of axes you moved:")
        for number, (low, high) in sorted(extremes.items()):
            analog = "analog" if high - low > 0.05 and abs(high - low - 2.0) > 0.05 else ""
            print(f"  axis {number:2}: {low:+.3f} .. {high:+.3f}  {analog}")
    finally:
        os.close(fd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
