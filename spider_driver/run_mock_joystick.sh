#!/usr/bin/env bash
# Same as run_home_local.sh, but drives the dashboard from the virtual G29
# instead of real hardware: starts the mock joystick, waits for its FIFO, then
# runs home against the wheel config it generates. The joystick is stopped when
# home exits.
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

field_ip="${1:-127.0.0.1}"
device="${VIRTUAL_G29_DEVICE:-/tmp/virtual_g29_js}"
config="${VIRTUAL_G29_CONFIG:-/tmp/virtual_g29.json}"

# Home needs PySide6, so prefer the project venv when one is present.
if [[ -n "${PYTHON:-}" ]]; then
  python_bin="$PYTHON"
elif [[ -x "$script_dir/.venv/bin/python" ]]; then
  python_bin="$script_dir/.venv/bin/python"
else
  python_bin="python3"
fi

joystick_pid=""
home_pid=""
cleanup() {
  trap - EXIT INT TERM
  for pid in "$home_pid" "$joystick_pid"; do
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

"$python_bin" -u "$script_dir/code_examples/virtual_g29.py" \
  --wheel-config "$script_dir/g29_config.json" \
  --device "$device" \
  --config-out "$config" &
joystick_pid=$!

# virtual_g29.py writes the generated config and creates the FIFO on startup.
for _ in $(seq 1 50); do
  if [[ -r "$config" && -p "$device" ]]; then break; fi
  sleep 0.1
done
if [[ ! -r "$config" || ! -p "$device" ]]; then
  echo "virtual G29 did not start (config=$config device=$device)" >&2
  exit 1
fi

# Backgrounded rather than exec'd so the trap above still runs and reaps the
# joystick, and so a signal sent to this script is handled while home is up
# (bash defers traps until a foreground child returns).
"$python_bin" "$script_dir/main.py" home \
  --field-host "$field_ip" \
  --wheel-config "$config" &
home_pid=$!

status=0
wait "$home_pid" || status=$?
exit "$status"
