#!/bin/zsh
# LPR 3D simulator: one local web page. Python runs Elmer, LTspice and Darshan's checks behind it.
# Double-click, then use the page that opens at http://127.0.0.1:8765 . Close this window (or Ctrl+C) to stop.
set -eu
SCRIPT_DIR="${0:A:h}"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$SCRIPT_DIR/code:$SCRIPT_DIR/project${PYTHONPATH:+:$PYTHONPATH}"
export GIT_CEILING_DIRECTORIES="$HOME"   # see RUN_FLOWCHART_PIPELINE.command
cd "$SCRIPT_DIR"
"$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/webapp/server.py" "$@" 2> >(grep -v "^fatal: not a git repository" >&2)
