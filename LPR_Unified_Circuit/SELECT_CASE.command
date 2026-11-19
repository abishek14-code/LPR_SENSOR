#!/bin/zsh
set -eu
cd "${0:A:h}"
python3 select_case.py --interactive
read -k 1 "?Press any key to close."
