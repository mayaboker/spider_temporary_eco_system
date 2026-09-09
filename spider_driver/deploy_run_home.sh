#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 3 ]]; then
  echo "Usage: $0 FIELD_IP [RELAY_PORT] [WHEEL_CONFIG]" >&2
  echo "Defaults: RELAY_PORT=9999 WHEEL_CONFIG=./g29_config.json" >&2
  exit 2
fi

field_ip="$1"
relay_port="${2:-9999}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
wheel_config="${3:-$script_dir/g29_config.json}"

exec python3 "$script_dir/main.py" home \
  --field-host "$field_ip" \
  --field-port "$relay_port" \
  --wheel-config "$wheel_config"
