#!/bin/zsh
set -e
cd "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
  echo 'Подготавливаю окружение Изbrainого…'
  python3 -m venv .venv
  .venv/bin/python -m pip install -r requirements.txt
fi
if [[ ! -f 'Настройки.env' ]]; then
  cp .env.example 'Настройки.env'
  chmod 600 'Настройки.env'
fi
.venv/bin/python run.py
