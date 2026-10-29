"""Local launcher. No paid services and no token in command arguments/logs."""
import os
import subprocess
import sys
import threading
import time
import webbrowser
import re
import fcntl
from pathlib import Path
import httpx
import uvicorn
from izbrainoe.config import Config,ROOT
from izbrainoe.app import create_app

def main():
    os.umask(0o077)
    config=Config.load()
    engine=None
    tunnel=None
    (ROOT/'data').mkdir(exist_ok=True,mode=0o700)
    instance=(ROOT/'data'/'server.lock').open('w')
    try:fcntl.flock(instance,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        print('Изbrainое уже запущено. Второй экземпляр не нужен.')
        if not os.getenv('IZ_NO_BROWSER'):
            from izbrainoe.store import Store
            store=Store(config.data_dir)
            webbrowser.open(f'http://127.0.0.1:{config.port}/local?key={store.meta("local_key")}')
        return
    (ROOT/'data'/'server.pid').write_text(str(os.getpid()))
    binary=ROOT/'runtime'/'ollama'/'ollama'
    if binary.exists() and config.ollama_url:
        try: ready=httpx.get(config.ollama_url+'/api/tags',timeout=2).status_code==200
        except Exception: ready=False
        if not ready:
            env=os.environ.copy();env.update(OLLAMA_HOST='127.0.0.1:11435',OLLAMA_NO_CLOUD='1',OLLAMA_MODELS=str(ROOT/'runtime'/'models'))
            log=(ROOT/'data');log.mkdir(exist_ok=True)
            with (log/'local-ai.log').open('a') as out:
                engine=subprocess.Popen([str(binary),'serve'],env=env,stdout=out,stderr=out)
            for _ in range(40):
                try:
                    if httpx.get(config.ollama_url+'/api/tags',timeout=1).is_success: break
                except Exception: pass
                time.sleep(.25)
    # Quick Tunnel is optional and starts only after a bot token is supplied.
    tunnel_bin=ROOT/'runtime'/'cloudflared'
    if config.token and not config.public_url and config.enable_tunnel and tunnel_bin.exists():
        print('Подключаю временный HTTPS-адрес для Telegram…')
        log_path=ROOT/'data'/'tunnel.log'
        with log_path.open('w') as out:
            tunnel=subprocess.Popen([str(tunnel_bin),'tunnel','--protocol','http2','--url',f'http://127.0.0.1:{config.port}','--no-autoupdate'],stdout=out,stderr=out)
        candidate=''
        for _ in range(180):
            content=log_path.read_text(errors='replace')
            match=re.search(r'https://[a-z0-9-]+\.trycloudflare\.com',content)
            if match:candidate=match[0]
            if candidate and 'Registered tunnel connection' in content:
                config.public_url=candidate;break
            if tunnel.poll() is not None:break
            time.sleep(.25)
        if not config.public_url:print('Временный адрес недоступен. Бот и локальные подборки продолжат работу.')
    app=create_app(config)
    store=app.state.store
    def open_ui():
        for _ in range(60):
            try:
                if httpx.get(f'http://127.0.0.1:{config.port}/health',timeout=1).is_success:
                    webbrowser.open(f'http://127.0.0.1:{config.port}/local?key={store.meta("local_key")}');return
            except Exception: pass
            time.sleep(.25)
    if not os.getenv('IZ_NO_BROWSER'):threading.Thread(target=open_ui,daemon=True).start()
    print('Изbrainое запущено на этом Mac. Для остановки нажмите Ctrl+C.')
    print('Токен подключён.' if config.token else 'Токен ещё не добавлен. Интерфейс и локальное сохранение доступны.')
    try: uvicorn.run(app,host='127.0.0.1',port=config.port,access_log=False,log_level='warning',proxy_headers=False)
    finally:
        if tunnel:
            tunnel.terminate()
            try:tunnel.wait(timeout=10)
            except subprocess.TimeoutExpired:tunnel.kill()
        if engine:
            engine.terminate()
            try:engine.wait(timeout=10)
            except subprocess.TimeoutExpired:engine.kill()

if __name__=='__main__':main()
