#!/bin/zsh
# Flowchart pipeline: shared inputs -> Elmer Ru -> cell interface -> LTspice -> P3 decision.
# Double-click runs all five branch scenarios, one after another. Add --jobs 5 --overlap to run them at the same time. Terminal: ./RUN_FLOWCHART_PIPELINE.command --scenario gain_fault
set -eu
SCRIPT_DIR="${0:A:h}"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$SCRIPT_DIR/code:$SCRIPT_DIR/project${PYTHONPATH:+:$PYTHONPATH}"
cd "$SCRIPT_DIR"
# P3 records a git revision. This folder is not its own git repo, so without a
# ceiling git would find the home-folder repo, scan all of ~ and print
# "could not open directory" warnings (and record an unrelated revision).
export GIT_CEILING_DIRECTORIES="$HOME"
if [[ $# -eq 0 ]]; then set -- --all-branches; fi
"$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/code/flowchart_pipeline.py" "$@" 2> >(grep -v "^fatal: not a git repository" >&2)
