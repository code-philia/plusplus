#!/usr/bin/env bash
# Sequential generation only: bash batch_run.sh --model gpt|deepseek
# The Python runner sets ARC_ENV_FILE to .env.gpt or .env.deepseek from --model.
set -euo pipefail
rex_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
rex_python="${REX_PYTHON:-$rex_root/.venv/bin/python}"
if [[ ! -x "$rex_python" ]]; then rex_python="python3"; fi
exec "$rex_python" "$rex_root/scripts/reproduce.py" batch "$@"
