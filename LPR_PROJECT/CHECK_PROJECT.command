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
"$TASK_PYTHON" "$SCRIPT_DIR/verify_package.py"
exec "$TASK_PYTHON" -m pytest -q -p no:cacheprovider project/tests/test_correction_contract.py project/tests/test_recorded_context.py project/tests/test_decision_engine.py project/tests/test_state_continuity.py project/tests/test_replay_fidelity.py code/test_integration_closure.py code/test_mac_runtime.py
