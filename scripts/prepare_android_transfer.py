"""Create a private settings-only QR. Never read or export archive contents.

The QR carries only the Gemini provider key, model and spending limit. No server,
archive identity, documents, passwords or collection rules are included.
"""
from __future__ import annotations
import argparse
import base64
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from izbrainoe.config import Config

PRIVATE = ROOT / 'data' / 'android-transfer'


def private_write(path: Path, value: bytes):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'wb') as out:
        out.write(value)


def prepare(open_browser=False):
    import qrcode
    config = Config.load()
    setup = dict(v=1, mode='settings_only', provod_key=config.provod_key,
                 model=config.gemini_model, limit_rub=config.provod_monthly_limit_rub)
    raw = 'izbrainoe-setup:' + base64.urlsafe_b64encode(
        json.dumps(setup, separators=(',', ':')).encode()).decode().rstrip('=')
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=8, border=4)
    qr.add_data(raw)
    qr.make(fit=True)
    output = io.BytesIO()
    qr.make_image(fill_color='black', back_color='white').save(output, format='PNG')
    image = base64.b64encode(output.getvalue()).decode()
    brand = base64.b64encode((ROOT / 'izbrainoe/static/logo.png').read_bytes()).decode()
    html = f'''<!doctype html><meta charset="utf-8"><meta name="referrer" content="no-referrer"><title>Подключение Gemini · Изbrainое</title>
<style>body{{background:#101010;color:#f4f4f4;font:17px -apple-system,sans-serif;max-width:650px;margin:40px auto;padding:20px;text-align:center}}.brand{{width:88px;height:88px;border-radius:20px}}.qr{{width:min(100%,420px);border-radius:18px}}h1{{font-size:30px}}p{{color:#bcbcbc;line-height:1.6}}strong{{color:#fff}}</style>
<img class="brand" alt="Изbrainое" src="data:image/png;base64,{brand}"><h1>Подключить Gemini</h1>
<p>В Изbrainом 0.2.4 или новее открой<br><strong>Настройки → Настроить по QR</strong><br>и отсканируй код.</p>
<img class="qr" alt="Личный код настройки Gemini" src="data:image/png;base64,{image}">
<p>Только ключ Gemini, модель и лимит расходов.<br>Файлов, заметок, папок и паролей в этом коде нет.</p>
<p>Для чистого старта сначала нажми «Очистить абсолютно всё» на телефоне, затем отсканируй этот код.<br>Не пересылай код другим людям: в нём твой API-ключ.</p>'''
    PRIVATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Remove only generated transfer copies, never the original Mac archive.
    for child in PRIVATE.iterdir():
        if child.name in {'active-setup.json', 'setup-qr.png', 'Настройка.html'}:
            continue
        if child.is_symlink() or child.is_file():
            child.unlink()
        elif child.is_dir():
            shutil.rmtree(child)
    private_write(PRIVATE / 'active-setup.json', json.dumps(setup).encode())
    private_write(PRIVATE / 'setup-qr.png', output.getvalue())
    private_write(PRIVATE / 'Настройка.html', html.encode())
    if open_browser:
        subprocess.Popen(['open', str(PRIVATE / 'Настройка.html')], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print('Готово: QR содержит только настройки Gemini. Файлы, заметки, папки и пароли исключены.')
    return setup


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--open', action='store_true', help='Open the private QR page')
    parser.add_argument('--serve', action='store_true', help='Compatibility alias for --open; no server is started')
    parser.add_argument('--headless', action='store_true')
    args = parser.parse_args()
    prepare(open_browser=(args.open or args.serve) and not args.headless)
