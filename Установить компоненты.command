#!/bin/zsh
set -e
cd "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install -r requirements.txt -r requirements-runtime.txt
.venv/bin/python scripts/setup_local.py
printf '\nНажмите Enter, чтобы закрыть окно.'
read
