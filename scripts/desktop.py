"""Private JSON bridge for the macOS launcher; stdout goes only to its parent app."""
import fcntl
import json
import os
from pathlib import Path
import shlex
import signal
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from izbrainoe.config import Config
import httpx


def server_pid(data_dir):
    try:
        pid = int((data_dir / 'server.pid').read_text())
        if pid <= 1:
            return None
        result = subprocess.run(['ps', '-p', str(pid), '-o', 'args='], capture_output=True, text=True)
        args = shlex.split(result.stdout)
        return pid if result.returncode == 0 and str(ROOT / 'run.py') in args else None
    except (OSError, ValueError):
        return None


def local_state(config):
    path = config.data_dir / 'izbrainoe.sqlite3'
    if not path.exists():
        return None
    try:
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1) as db:
            row = db.execute("SELECT value FROM meta WHERE key='local_key'").fetchone()
        if not row:
            return None
        base = f'http://127.0.0.1:{config.port}'
        with httpx.Client(base_url=base, cookies={'iz_session': row[0]}, timeout=3, trust_env=False) as client:
            response = client.get('/api/me')
            response.raise_for_status()
            me = response.json()
        return {'running': True, 'bot_running': bool(me.get('bot_running')),
                'bot_configured': bool(me.get('bot_configured')), 'bot_error': bool(me.get('bot_error')),
                'phone_required': bool(me.get('phone_access',{}).get('required')),
                'phone_ready': bool(me.get('phone_access',{}).get('ready')),
                'phone_error': me.get('phone_access',{}).get('error',''),
                'url': base + '/local?key=' + row[0]}
    except (OSError, sqlite3.Error, httpx.HTTPError, ValueError):
        return None


def fully_ready(state):
    return bool(state and state.get('bot_running') and (not state.get('phone_required') or state.get('phone_ready')))


def start(config, timeout=110):
    config.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (config.data_dir / 'desktop.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = local_state(config)
        if fully_ready(state):
            return state
        if not state and not server_pid(config.data_dir):
            with (config.data_dir / 'server.log').open('a') as log:
                subprocess.Popen([str(ROOT / '.venv/bin/python'), str(ROOT / 'run.py')], cwd=ROOT,
                                 env={**os.environ, 'IZ_NO_BROWSER': '1'}, stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=log, start_new_session=True)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = local_state(config)
            if state and (fully_ready(state) or not state['bot_configured'] or state['bot_error']):
                return state
            time.sleep(.5)
        if state:
            return state
        raise RuntimeError('Не удалось запустить приложение. Проверь интернет и повтори запуск. Подробности — в data/server.log.')


def reconnect(config):
    state=local_state(config)
    if state:
        with sqlite3.connect(config.data_dir/'izbrainoe.sqlite3') as db:
            key=db.execute("SELECT value FROM meta WHERE key='local_key'").fetchone()[0]
        with httpx.Client(base_url=f'http://127.0.0.1:{config.port}',cookies={'iz_session':key},
                          headers={'X-Iz-Request':'1'},timeout=5,trust_env=False) as client:
            response=client.post('/api/network/reconnect')
            if not response.is_success:raise RuntimeError('Не удалось запросить восстановление. Перезапусти бота кнопкой остановки и запуска.')
    return start(config)


def stop(config, timeout=25):
    pid = server_pid(config.data_dir)
    if pid is None:
        return {'running': False, 'bot_running': False}
    os.kill(pid, signal.SIGINT)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server_pid(config.data_dir) is None:
            return {'running': False, 'bot_running': False}
        time.sleep(.2)
    raise RuntimeError('Бот завершает текущие задачи. Подожди немного и нажми «Остановить» ещё раз.')


def main():
    os.umask(0o077)
    try:
        config = Config.load()
        action = sys.argv[1] if len(sys.argv) > 1 else 'start'
        if action == 'start':
            result = start(config)
        elif action == 'stop':
            result = stop(config)
        elif action == 'status':
            result = local_state(config) or {'running': False, 'bot_running': False}
        elif action == 'reconnect':
            result = reconnect(config)
        else:
            raise RuntimeError('Неизвестное действие запуска.')
        print(json.dumps(result, ensure_ascii=False))
    except RuntimeError as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False))
        sys.exit(1)
    except Exception:
        # Config and HTTP exceptions can contain credentials. Never expose them.
        print(json.dumps({'error': 'Не удалось прочитать настройки или запустить бота. Проверь, что папка проекта не перемещена.'}, ensure_ascii=False))
        sys.exit(1)


if __name__ == '__main__':
    main()
