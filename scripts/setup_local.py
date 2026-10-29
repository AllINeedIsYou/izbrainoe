"""Install local, free runtimes on an Apple Silicon Mac. Never reads BOT_TOKEN."""
import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
RUNTIME=ROOT/'runtime'
MODELS=('qwen3:4b','qwen3-embedding:0.6b')
OLLAMA_VERSION='v0.40.12'
WHISPER_VERSION='v1.9.5'


def download(url,path):
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():return
    partial=path.with_suffix(path.suffix+'.part')
    print('Скачиваю '+path.name+'…',flush=True)
    try:
        with urllib.request.urlopen(url,timeout=60) as response,partial.open('wb') as out:
            shutil.copyfileobj(response,out)
        partial.replace(path)
    finally:partial.unlink(missing_ok=True)


def unpack(archive,destination):
    destination.mkdir(parents=True,exist_ok=True)
    with tarfile.open(archive) as tar:tar.extractall(destination,filter='data')


def tags():
    try:
        with urllib.request.urlopen('http://127.0.0.1:11435/api/tags',timeout=2) as response:
            return {m['name'] for m in json.load(response).get('models',[])}
    except Exception:return None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true',help='Only inspect installed components')
    args=parser.parse_args()
    if args.check:
        missing=[]
        for name in ('ocr','cloudflared','ffmpeg','whisper-cli','ggml-base.bin','ollama/ollama'):
            ready=(RUNTIME/name).is_file();print(name+': '+('готов' if ready else 'отсутствует'))
            if not ready:missing.append(name)
        installed=tags()
        print('Модели: '+(', '.join(sorted(installed)) if installed is not None else 'проверка возможна при запущенном Ollama'))
        return 1 if missing else 0
    if sys.version_info<(3,12):raise SystemExit('Нужен Python 3.12 или новее.')
    if platform.system()!='Darwin' or platform.machine()!='arm64':
        raise SystemExit('Автоматическая установка рассчитана на macOS Apple Silicon. См. README для других систем.')
    if not shutil.which('swiftc') or not shutil.which('clang'):
        raise SystemExit('Сначала установите Apple Command Line Tools: xcode-select --install')
    RUNTIME.mkdir(exist_ok=True);scratch=RUNTIME/'.setup';scratch.mkdir(exist_ok=True)
    if not (RUNTIME/'ocr').exists() or (ROOT/'scripts/ocr.swift').stat().st_mtime>(RUNTIME/'ocr').stat().st_mtime:
        print('Собираю распознавание текста Apple Vision…',flush=True)
        subprocess.run(['swiftc','-O',str(ROOT/'scripts/ocr.swift'),'-o',str(RUNTIME/'ocr')],check=True)
    if not (RUNTIME/'ollama/ollama').exists():
        archive=scratch/'ollama-darwin.tgz'
        download(f'https://github.com/ollama/ollama/releases/download/{OLLAMA_VERSION}/ollama-darwin.tgz',archive)
        unpack(archive,RUNTIME/'ollama')
    if not (RUNTIME/'cloudflared').exists():
        archive=scratch/'cloudflared.tgz'
        download('https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-arm64.tgz',archive)
        unpack(archive,scratch/'cloudflared')
        binary=next((scratch/'cloudflared').rglob('cloudflared'))
        shutil.copy2(binary,RUNTIME/'cloudflared');(RUNTIME/'cloudflared').chmod(0o755)
    if not (RUNTIME/'ffmpeg').exists():
        import imageio_ffmpeg
        shutil.copy2(imageio_ffmpeg.get_ffmpeg_exe(),RUNTIME/'ffmpeg');(RUNTIME/'ffmpeg').chmod(0o755)
    if not (RUNTIME/'whisper-cli').exists():
        archive=scratch/'whisper-source.tgz'
        download(f'https://github.com/ggml-org/whisper.cpp/archive/refs/tags/{WHISPER_VERSION}.tar.gz',archive)
        unpack(archive,scratch/'whisper-source')
        source=next((scratch/'whisper-source').glob('whisper.cpp-*'));build=scratch/'whisper-build'
        subprocess.run(['cmake','-S',str(source),'-B',str(build),'-DCMAKE_BUILD_TYPE=Release',
                        '-DBUILD_SHARED_LIBS=OFF','-DGGML_METAL=OFF','-DWHISPER_BUILD_TESTS=OFF'],check=True)
        subprocess.run(['cmake','--build',str(build),'--config','Release','-j','4','--target','whisper-cli'],check=True)
        shutil.copy2(build/'bin/whisper-cli',RUNTIME/'whisper-cli')
    download('https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.bin',RUNTIME/'ggml-base.bin')
    env=os.environ.copy();env.update(OLLAMA_HOST='127.0.0.1:11435',OLLAMA_NO_CLOUD='1',OLLAMA_MODELS=str(RUNTIME/'models'))
    engine=None;log=None
    try:
        if tags() is None:
            log=(scratch/'setup-ollama.log').open('a')
            engine=subprocess.Popen([str(RUNTIME/'ollama/ollama'),'serve'],env=env,stdout=log,stderr=log)
            for _ in range(60):
                if tags() is not None:break
                if engine.poll() is not None:raise RuntimeError('Ollama не запустился. См. runtime/.setup/setup-ollama.log')
                time.sleep(.5)
            if tags() is None:raise RuntimeError('Нет ответа от локального Ollama.')
        for model in MODELS:
            if model not in (tags() or set()):
                print('Устанавливаю локальную модель '+model+'…',flush=True)
                subprocess.run([str(RUNTIME/'ollama/ollama'),'pull',model],env=env,check=True)
    finally:
        if engine:
            engine.terminate()
            try:engine.wait(timeout=10)
            except subprocess.TimeoutExpired:engine.kill();engine.wait()
        if log:log.close()
    settings=ROOT/'Настройки.env'
    if not settings.exists():shutil.copy2(ROOT/'.env.example',settings);settings.chmod(0o600)
    print('Готово. Добавьте токен в Настройки.env и откройте «Запустить Изbrainое.command».')
    return 0

if __name__=='__main__':raise SystemExit(main())
