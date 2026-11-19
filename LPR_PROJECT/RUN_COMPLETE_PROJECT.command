#!/bin/zsh
set -eu
SCRIPT_DIR="${0:A:h}"
TASK_PYTHON="${SCRIPT_DIR}/.venv/bin/python"
if [[ ! -x "$TASK_PYTHON" ]]; then
  print "Python environment missing. Run: python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt"
  exit 1
fi
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$SCRIPT_DIR/code:$SCRIPT_DIR/project${PYTHONPATH:+:$PYTHONPATH}"
cd "$SCRIPT_DIR"
exec "$TASK_PYTHON" "$SCRIPT_DIR/code/complete_mac.py" "$@"
