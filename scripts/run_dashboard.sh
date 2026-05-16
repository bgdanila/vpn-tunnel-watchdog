#!/usr/bin/env bash
#
# Quick launcher for the local dashboard.
#
#   ./scripts/run_dashboard.sh                # http://127.0.0.1:8000
#   ./scripts/run_dashboard.sh 0.0.0.0:9000   # custom bind

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ -x "${PROJECT_DIR}/.venv/bin/python3" ]]; then
    PY="${PROJECT_DIR}/.venv/bin/python3"
else
    PY="$(command -v python3)"
fi

cd "${PROJECT_DIR}"
ADDR="${1:-127.0.0.1:8000}"

# Make the daemon package importable from inside the Django process.
export PYTHONPATH="${PROJECT_DIR}:${PYTHONPATH:-}"

echo "==> Starting dashboard on http://${ADDR}"
exec "${PY}" dashboard/manage.py runserver "${ADDR}"
