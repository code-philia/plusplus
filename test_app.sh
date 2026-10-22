#!/usr/bin/env bash
# Deploy and test exactly one app; stop only child services started by this run.
set -euo pipefail
rex_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
rex_python="${REX_PYTHON:-$rex_root/.venv/bin/python}"
if [[ ! -x "$rex_python" ]]; then rex_python="python3"; fi
exec "$rex_python" "$rex_root/scripts/reproduce.py" test "$@"
