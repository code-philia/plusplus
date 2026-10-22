#!/usr/bin/env bash
set -euo pipefail

# Usage: bash scripts/pressure_test.sh [env-file]
# Relative env-file paths are resolved from the project root.
project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$project_root"

export ARC_ENV_FILE="$(realpath -e -- "${1:-$project_root/.env.meter}")"
if [[ ! -f "$ARC_ENV_FILE" ]]; then
    printf 'Environment file is not a regular file: %s\n' "$ARC_ENV_FILE" >&2
    exit 1
fi

python_bin="${PYTHON_BIN:-python}"
command -v "$python_bin" >/dev/null

output_root="$project_root/workspace/pressure-test/output"
logs_dir="$output_root/logs"
mkdir -p -- "$logs_dir"

# Each invocation owns distinct outputs and logs.
batch_id="$(date +%Y%m%d-%H%M%S)-$$"
pid_file="$logs_dir/$batch_id.pids.tsv"
printf 'run\tport\tpid\toutput\tlog\n' > "$pid_file"

for ((index = 0; index < 60; index++)); do
    printf -v ordinal '%02d' "$((index + 1))"
    run_name="run-$batch_id-$ordinal"
    output_dir="$output_root/$run_name"
    log_file="$logs_dir/$run_name.log"
    port="$((34000 + index))"

    nohup "$python_bin" -u src/main.py compile example/smoke-smoke \
        -o "$output_dir" --port "$port" \
        > "$log_file" 2>&1 < /dev/null &
    pid=$!
    printf '%s\t%s\t%s\t%s\t%s\n' \
        "$run_name" "$port" "$pid" "$output_dir" "$log_file" >> "$pid_file"
    printf 'Started %s: port=%s pid=%s\n' "$ordinal" "$port" "$pid"
done

printf 'Launched 60 background processes. PID list: %s\n' "$pid_file"
