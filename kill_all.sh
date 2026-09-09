#!/usr/bin/env bash
# Stop every process started by the local Spider simulation / mock stack.
set -euo pipefail

# Match complete, project-specific command fragments. Avoid broad names such as
# "python", "ros2", or "gz sim server", which could stop unrelated work.
patterns=(
  'ros2 launch spider_robot_sim spider_sim\.launch\.py'
  'gz sim spider_robot_world\.sdf'
  'spider_driver/code_examples/spider_gz_bridge\.py'
  'spider_driver/main\.py field'
  'spider_driver/main\.py home'
  'spider_driver/code_examples/virtual_g29\.py'
  'spider_driver/code_examples/spider_mock_server\.py'
  'spider_driver/run_mock_joystick\.sh'
)

matching_pids() {
  local pattern pid
  for pattern in "${patterns[@]}"; do
    while read -r pid; do
      [[ -n "$pid" && "$pid" != "$$" ]] && printf '%s\n' "$pid"
    done < <(pgrep -f -- "$pattern" || true)
  done | sort -un
}

process_tree() {
  local parent="$1" child
  printf '%s\n' "$parent"
  while read -r child; do
    [[ -n "$child" ]] && process_tree "$child"
  done < <(pgrep -P "$parent" || true)
}

find_pids() {
  local root
  while read -r root; do
    [[ -n "$root" ]] && process_tree "$root"
  done < <(matching_pids)
}

mapfile -t pids < <(find_pids | sort -un)
if (( ${#pids[@]} == 0 )); then
  echo "No Spider simulation or driver processes are running."
  exit 0
fi

echo "Stopping Spider processes: ${pids[*]}"
kill -TERM "${pids[@]}" 2>/dev/null || true

# Give launchers and the mock-joystick wrapper time to clean up their children.
for _ in {1..30}; do
  remaining=()
  for pid in "${pids[@]}"; do
    kill -0 "$pid" 2>/dev/null && remaining+=("$pid")
  done
  (( ${#remaining[@]} == 0 )) && break
  sleep 0.1
done

if (( ${#remaining[@]} > 0 )); then
  echo "Force-stopping remaining processes: ${remaining[*]}"
  kill -KILL "${remaining[@]}" 2>/dev/null || true
fi

echo "Spider simulation and driver stack stopped."
