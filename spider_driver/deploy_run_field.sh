#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 4 ]]; then
  echo "Usage: $0 HOME_IP [ROBOT_IP] [RELAY_PORT] [ROBOT_PORT]" >&2
  echo "Defaults: ROBOT_IP=10.42.17.3 RELAY_PORT=9999 ROBOT_PORT=8888" >&2
  exit 2
fi

home_ip="$1"
robot_ip="${2:-10.42.17.3}"
relay_port="${3:-9999}"
robot_port="${4:-8888}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

exec python3 -u "$script_dir/main.py" field \
  --listen-host 0.0.0.0 \
  --listen-port "$relay_port" \
  --robot-host "$robot_ip" \
  --robot-port "$robot_port" \
  --allowed-home-ip "$home_ip" \
  --safety-timeout 0.5
