#!/usr/bin/env bash
# Use source to also change the calling shell's working directory.

_arc_copy_validation_tests() {
  if [[ $# -ne 2 ]]; then
    printf 'Usage: source ./prepare.sh <task> <output-directory>\n' >&2
    return 1
  fi

  local task_name="$1" output_directory="$2" project_root source_task setup_script destination
  if [[ ! "$task_name" =~ ^[a-zA-Z0-9_-]+$ ]]; then
    printf 'Invalid task name: %s\n' "$task_name" >&2
    return 1
  fi
  project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || return 1
  source_task="$project_root/arc-bench/arc-bench/$task_name"
  setup_script="$project_root/arc-bench/setup.sh"
  destination="$output_directory/$task_name"

  if [[ ! -d "$source_task" || ! -f "$setup_script" ]]; then
    printf 'Missing task directory or setup script: %s\n' "$task_name" >&2
    return 1
  fi
  mkdir -p -- "$destination" || return 1
  cp -a -- "$source_task/." "$destination/" || return 1
  cp -p -- "$setup_script" "$destination/setup.sh" || return 1
  chmod +x -- "$destination/setup.sh" || return 1
  cd -- "$destination" || return 1
  printf 'Task ready: %s\n' "$PWD"
}

if _arc_copy_validation_tests "$@"; then
  unset -f _arc_copy_validation_tests
else
  unset -f _arc_copy_validation_tests
  return 1 2>/dev/null || exit 1
fi
