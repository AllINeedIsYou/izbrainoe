#!/bin/zsh
cd "${0:A:h}"
.venv/bin/python - <<'PY'
from pathlib import Path
import os,signal,subprocess
root=Path.cwd();pidfile=root/'data/server.pid'
if not pidfile.exists():raise SystemExit('Фоновый запуск не найден.')
try:
 pid=int(pidfile.read_text())
 args=subprocess.check_output(['ps','-p',str(pid),'-o','args='],text=True)
 if str(root/'run.py') not in args:raise SystemExit('Не удалось подтвердить фоновый процесс. Если приложение открыто в Терминале, нажмите там Ctrl+C.')
 os.kill(pid,signal.SIGINT)
 print('Отправлена команда остановки Изbrainого.')
except (ProcessLookupError,subprocess.CalledProcessError,ValueError):print('Изbrainое уже остановлено.')
PY
