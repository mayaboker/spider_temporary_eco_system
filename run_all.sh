#!/usr/bin/env bash
# Bring up the whole local Spider stack in one tmux session: four panes, one
# per component, started bottom-up in the order the README requires.
#
#   pane 0  simulation     spider_simulation/run_simulation.sh
#   pane 1  Teensy bridge  spider_driver/run_gz_bridge.sh      (waits for Gazebo)
#   pane 2  field computer spider_driver/run_field_local.sh    (waits for :8888)
#   pane 3  control board  spider_driver/run_mock_joystick.sh  (waits for :9999)
#
# Each stage blocks on a real readiness signal, not a fixed sleep, so a slow
# first-run colcon build or a cold Gazebo start just delays the stack instead
# of breaking it.
#
# Stop everything with ./stop_all.sh, or press prefix + K inside the session.
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
session="${SESSION:-spider}"

# Gazebo may run a colcon build on the very first launch, so allow minutes.
sim_timeout="${SIM_TIMEOUT:-600}"
port_timeout="${PORT_TIMEOUT:-60}"

driver="$script_dir/spider_driver"
simulation="$script_dir/spider_simulation"

# --- readiness gates -------------------------------------------------------
# Invoked as `run_all.sh --gate <name>` from inside the panes themselves, so
# the wait logic lives here once instead of being inlined into tmux strings.

udp_bound() { [[ -n "$(ss -lunH "sport = :$1" 2>/dev/null)" ]]; }
sim_up()    { pgrep -f -- 'gz sim spider_robot_world\.sdf' >/dev/null 2>&1; }

# wait_for <label> <timeout> <command...> -- polls the command until it succeeds.
wait_for() {
  local label="$1" timeout="$2"
  shift 2
  local deadline=$(( SECONDS + timeout ))
  echo "[run_all] waiting for $label (up to ${timeout}s)..."
  while (( SECONDS < deadline )); do
    if "$@"; then
      echo "[run_all] $label is up."
      return 0
    fi
    sleep 0.5
  done
  echo "[run_all] timed out after ${timeout}s waiting for $label." >&2
  return 1
}

if [[ "${1:-}" == "--gate" ]]; then
  case "${2:-}" in
    sim)    wait_for "Gazebo"           "$sim_timeout"  sim_up ;;
    bridge) wait_for "the bridge :8888" "$port_timeout" udp_bound 8888 ;;
    field)  wait_for "field :9999"      "$port_timeout" udp_bound 9999 ;;
    *) echo "unknown gate: ${2:-<none>}" >&2; exit 2 ;;
  esac
  exit
fi

# --- preflight -------------------------------------------------------------

if [[ $# -gt 0 ]]; then
  echo "Usage: $0        # no arguments; see SESSION / SIM_TIMEOUT / PORT_TIMEOUT" >&2
  exit 2
fi

command -v tmux >/dev/null || { echo "tmux is not installed." >&2; exit 1; }
command -v ss   >/dev/null || { echo "ss (iproute2) is not installed." >&2; exit 1; }

for s in "$simulation/run_simulation.sh" "$driver/run_gz_bridge.sh" \
         "$driver/run_field_local.sh" "$driver/run_mock_joystick.sh"; do
  [[ -x "$s" ]] || { echo "Not executable: $s" >&2; exit 1; }
done

if tmux has-session -t "=$session" 2>/dev/null; then
  echo "Session '$session' already exists. Attach with:  tmux attach -t $session" >&2
  echo "Or stop it first with: ./kill_all.sh && tmux kill-session -t $session" >&2
  exit 1
fi

if [[ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
  echo "Warning: no DISPLAY or WAYLAND_DISPLAY set." >&2
  echo "Gazebo and the Qt dashboard both need a graphical session." >&2
fi

# --- pane bodies -----------------------------------------------------------
# Panes are held open by the window's remain-on-exit option (set below) rather
# than by a trailing `read`: Ctrl-C reaches the pane's whole foreground process
# group, so a wrapper shell would be killed alongside the component and the
# pane would collapse before it could print anything.

self="$script_dir/run_all.sh"

pane_sim="cd '$simulation' && ./run_simulation.sh"
pane_bridge="cd '$driver' && '$self' --gate sim    && ./run_gz_bridge.sh"
pane_field="cd '$driver' && '$self' --gate bridge && ./run_field_local.sh"
pane_board="cd '$driver' && '$self' --gate field  && ./run_mock_joystick.sh"

# --- build the session -----------------------------------------------------

tmux new-session -d -s "$session" -n stack -c "$script_dir" "bash -c $(printf '%q' "$pane_sim")"
tmux split-window -t "$session:stack" -c "$script_dir" "bash -c $(printf '%q' "$pane_bridge")"
tmux split-window -t "$session:stack" -c "$script_dir" "bash -c $(printf '%q' "$pane_field")"
tmux split-window -t "$session:stack" -c "$script_dir" "bash -c $(printf '%q' "$pane_board")"
tmux select-layout -t "$session:stack" tiled

# Scoped to this session/window only -- `-g` here would rewrite the user's
# global tmux options and leak into their other sessions.
tmux set-option -t "$session" mouse on
tmux set-window-option -t "$session:stack" pane-border-status top
tmux set-window-option -t "$session:stack" remain-on-exit on
tmux select-pane -t "$session:stack.0" -T ' 1 simulation (Gazebo) '
tmux select-pane -t "$session:stack.1" -T ' 2 Teensy bridge :8888 '
tmux select-pane -t "$session:stack.2" -T ' 3 field :9999 '
tmux select-pane -t "$session:stack.3" -T ' 4 control board (G29 + dashboard) '
tmux select-pane -t "$session:stack.3"

# prefix + K stops the stack and tears down the session, so no idle shell pane
# is needed. Key tables are server-wide, so the binding is a no-op in any other
# session; it disappears with the server once the last session closes.
tmux bind-key -T prefix K run-shell -b \
  "if [ '#{session_name}' = '$session' ]; then '$script_dir/stop_all.sh'; fi"

tmux set-option -t "$session" status-right \
  " prefix + K = stop all #[default] "
tmux set-option -t "$session" status-right-length 40

echo "Started session '$session'. Panes come up in order as each stage is ready."
echo
echo "  prefix + K   stop the stack and close the session (same as ./stop_all.sh)"
echo "  prefix + c   scratch shell in a new window, if you want one"
echo "  tmux respawn-pane -t $session:stack.<0-3>   restart a component that exited"

if [[ -n "${TMUX:-}" ]]; then
  exec tmux switch-client -t "$session"
else
  exec tmux attach-session -t "$session"
fi
