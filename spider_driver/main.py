import argparse
from pathlib import Path
import sys

from definitions import SERVER_IP, SERVER_PORT


def build_parser():
    parser = argparse.ArgumentParser(description="Split home/field Spider controller")
    subparsers = parser.add_subparsers(dest="mode", required=True)

    field = subparsers.add_parser("field", help="relay Teensy API packets to the robot or mock")
    field.add_argument("--listen-host", default="0.0.0.0")
    field.add_argument("--listen-port", type=int, default=9999)
    field.add_argument("--robot-host", default=SERVER_IP)
    field.add_argument("--robot-port", type=int, default=SERVER_PORT)
    field.add_argument("--allowed-home-ip", help="optional source-IP allowlist")
    field.add_argument("--safety-timeout", type=float, default=0.5)

    home = subparsers.add_parser("home", help="run the PySide6 G29 dashboard")
    home.add_argument("--field-host", required=True)
    home.add_argument("--field-port", type=int, default=9999)
    home.add_argument(
        "--wheel-config",
        default=str(Path(__file__).with_name("g29_config.json")),
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.mode == "field":
        from field_process import FieldRelay

        if args.safety_timeout <= 0:
            raise SystemExit("--safety-timeout must be greater than zero")
        FieldRelay(
            args.listen_host,
            args.listen_port,
            args.robot_host,
            args.robot_port,
            args.safety_timeout,
            args.allowed_home_ip,
        ).run()
        return 0

    from home_process import run_home

    return run_home(args.field_host, args.field_port, args.wheel_config)


if __name__ == "__main__":
    sys.exit(main())
