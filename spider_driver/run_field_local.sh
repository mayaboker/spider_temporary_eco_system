#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 -u "$script_dir/main.py" field \
  --listen-host 127.0.0.1 \
  --robot-host 127.0.0.1
