"""Create an encrypted Android snapshot without changing the Telegram archive.

The QR is private. Its key and provider token are never embedded in the APK,
printed, committed, or sent to an external service. Optional LAN transfer uses
TLS with a certificate fingerprint delivered in the QR.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import http.server
import io
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from izbrainoe.config import Config

PRIVATE = ROOT / 'data' / 'android-transfer'
CHUNK = 1024 * 1024

def private_write(path: Path, value: bytes):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'wb') as out:
        out.write(value)


def setup_identity():
    path = PRIVATE / 'identity.json'
    if path.exists():
        return json.loads(path.read_text())
    code = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip('=')
    identity = dict(archive_code=code, device=secrets.token_hex(16))
    private_write(path, json.dumps(identity).encode())
    return identity


def prepare():
    PRIVATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    config, identity = Config.load(), setup_identity()
    key = base64.urlsafe_b64decode(identity['archive_code'] + '=')
    archive_id = hashlib.sha256(key).hexdigest()[:32]
    def seal(value: bytes, purpose: str):
        nonce = secrets.token_bytes(12)
        encrypted = AESGCM(key).encrypt(nonce, value, f'izbrainoe-v1:{archive_id}:{purpose}'.encode())
        return b'\x01' + nonce + encrypted[-16:] + encrypted[:-16]
    connection = sqlite3.connect(f'file:{config.data_dir / "izbrainoe.sqlite3"}?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute('BEGIN')
    owner = connection.execute("SELECT value FROM meta WHERE key='owner'").fetchone()
    uid = int(owner[0]) if owner else -1
    rows = [dict(row) for row in connection.execute('SELECT * FROM items WHERE user_id=? ORDER BY created', (uid,))]
    vault = connection.execute('SELECT envelope FROM vault WHERE user_id=?', (uid,)).fetchone()
    connection.close()
    bundle = PRIVATE / 'Архив Изbrainого.izbrainoe'
    temporary = bundle.with_suffix('.part')
    blobs, clock, missing = set(), 0, 0
    with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as z:
        z.writestr('manifest.json', json.dumps(dict(app='izbrainoe', v=1, archive=archive_id)))
        def revision(record_id, data):
            nonlocal clock
            clock += 1
            event_id = hashlib.sha256((record_id + json.dumps(data, sort_keys=True, ensure_ascii=False)).encode()).hexdigest()[:32]
            item = dict(v=1, id=event_id, record=record_id, device=identity['device'], clock=clock,
                        created=int(time.time()*1000), parents=[], data=data)
            z.writestr(f'events/{event_id}.enc', seal(json.dumps(item, ensure_ascii=False).encode(), f'event:{event_id}'))
        for row in rows:
            analysis = json.loads(row.get('analysis_json') or '{}')
            groups = json.loads(row['groups_json'])
            kind = row['kind']
            if kind == 'voice':
                kind = 'audio'
            data = dict(title=row['title'], body=row['body'], kind=kind, filename=row['filename'],
                        context=row['context'], folder=groups[0] if groups else 'Неразобранное',
                        manual_folder=bool(row['manual_groups']), created=int(row['created']*1000),
                        summary=analysis.get('summary',''), text=row['extraction'], topics=analysis.get('topics',[]),
                        document_type=analysis.get('document_type',''), deleted=row['deleted'] is not None,
                        analysis_state='done', reading_note='Перенесено из Telegram; прежний разбор сохранён.', source='Telegram')
            source = Path(row['path']) if row['path'] else None
            if (source is None or not source.is_file()) and row['tg_file_id'] and config.token:
                # Download only files of the archive owner, using the existing bot.
                try:
                    import httpx
                    with httpx.Client(timeout=40, trust_env=False) as client:
                        response=client.post(f'https://api.telegram.org/bot{config.token}/getFile',json={'file_id':row['tg_file_id']})
                        info=response.json()
                        if info.get('ok'):
                            source=PRIVATE/'download-cache'/hashlib.sha256(row['tg_file_id'].encode()).hexdigest()
                            if not source.exists():
                                body=client.get(f'https://api.telegram.org/file/bot{config.token}/'+info['result']['file_path'])
                                if body.is_success and len(body.content)<=100*1024*1024: private_write(source,body.content)
                except Exception:
                    source=None
            if source is not None and source.is_file():
                hasher=hashlib.sha256()
                with source.open('rb') as f:
                    while block:=f.read(CHUNK):hasher.update(block)
                h,size=hasher.hexdigest(),source.stat().st_size
                data['blob']=dict(hash=h,size=size,chunks=(size+CHUNK-1)//CHUNK)
                if h not in blobs:
                    with source.open('rb') as f:
                        number=0
                        while block:=f.read(CHUNK):
                            z.writestr(f'blobs/{h}/{number}.enc',seal(block,f'blob:{h}:{number}'));number+=1
                    blobs.add(h)
            elif row['filename'] or row['tg_file_id']:
                missing+=1
                data['reading_note']='Перенесены описание и текст. Оригинал остался в Telegram и пока недоступен на этом устройстве.'
            record_id=row['id'] if len(row['id'])==32 and all(c in '0123456789abcdef' for c in row['id']) else hashlib.sha256(row['id'].encode()).hexdigest()[:32]
            revision(record_id,data)
        if vault:
            # The old WebCrypto envelope is copied encrypted, without a password.
            envelope=json.loads(vault[0])
            if envelope:
                revision(hashlib.sha256(f'legacy-vault:{uid}'.encode()).hexdigest()[:32],
                         dict(kind='vault',title='Хранилище паролей',envelope=envelope,services=[]))
    os.replace(temporary,bundle);os.chmod(bundle,0o600)
    metadata=dict(archive_id=archive_id,items=len(rows),files=len(blobs),missing=missing,bundle=str(bundle),prepared=time.time())
    private_write(PRIVATE/'snapshot.json',json.dumps(metadata).encode())
    return metadata


def local_address():
    for interface in ('en0','en1','en2','en3'):
        try:
            address=subprocess.check_output(['ipconfig','getifaddr',interface],stderr=subprocess.DEVNULL,text=True).strip()
            if address.startswith(('192.168.','10.','172.')):return address
        except subprocess.CalledProcessError:pass
    return '127.0.0.1'


def serve(open_browser=True):
    import qrcode
    config,identity=Config.load(),setup_identity()
    snapshot=json.loads((PRIVATE/'snapshot.json').read_text())
    cert,key=PRIVATE/'certificate.pem',PRIVATE/'tls-key.pem'
    if not cert.exists():
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','30','-subj','/CN=Izbrainoe local transfer','-keyout',str(key),'-out',str(cert)],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        os.chmod(key,0o600)
    der=ssl.PEM_cert_to_DER_cert(cert.read_text())
    fingerprint=hashlib.sha256(der).hexdigest()
    nonce=secrets.token_hex(32)
    deadline=time.time()+8*3600
    bundle=Path(snapshot['bundle'])
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            if self.path!=f'/archive/{nonce}.izbrainoe' or time.time()>deadline:
                self.send_error(404);return
            self.send_response(200)
            self.send_header('Content-Type','application/octet-stream')
            self.send_header('Content-Length',str(bundle.stat().st_size))
            self.send_header('Cache-Control','no-store')
            self.end_headers()
            try:
                with bundle.open('rb') as incoming:shutil.copyfileobj(incoming,self.wfile,CHUNK)
            except (BrokenPipeError,ConnectionResetError):pass
    server=http.server.ThreadingHTTPServer(('0.0.0.0',0),Handler)
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert,key);server.socket=context.wrap_socket(server.socket,server_side=True)
    setup=dict(v=1,archive_code=identity['archive_code'],provod_key=config.provod_key,model=config.gemini_model,
               limit_rub=config.provod_monthly_limit_rub,
               bundle_url=f'https://{local_address()}:{server.server_port}/archive/{nonce}.izbrainoe',certificate_sha256=fingerprint)
    private_write(PRIVATE/'active-setup.json',json.dumps(setup).encode())
    raw='izbrainoe-setup:'+base64.urlsafe_b64encode(json.dumps(setup,separators=(',',':')).encode()).decode().rstrip('=')
    qr=qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,box_size=8,border=4);qr.add_data(raw);qr.make(fit=True)
    output=io.BytesIO();qr.make_image(fill_color='black',back_color='white').save(output,format='PNG')
    private_write(PRIVATE/'setup-qr.png',output.getvalue())
    image=base64.b64encode(output.getvalue()).decode()
    html=f'''<!doctype html><meta charset="utf-8"><meta name="referrer" content="no-referrer"><title>Настройка Изbrainого</title>
<style>body{{background:#10151e;color:#f0f3ed;font:17px -apple-system,sans-serif;max-width:650px;margin:40px auto;padding:20px;text-align:center}}img{{width:min(100%,460px);border-radius:18px}}h1{{font-size:30px}}p{{color:#a5b2ad;line-height:1.6}}strong{{color:#c0f58c}}</style>
<h1>Твоё Изbrainое — на телефоне</h1><p>Установи APK. В приложении открой<br><strong>Настройки → Перенести с Mac</strong><br>и наведи камеру на этот код.</p>
<img alt="Личный код переноса" src="data:image/png;base64,{image}"><p>Будут перенесены {snapshot['items']} записей, {snapshot['files']} оригиналов и настройки Gemini.<br>Главный пароль хранилища остаётся прежним.</p>
<p>Телефон и Mac должны быть в одной домашней сети.<br>Код действует 8 часов. Не пересылай его другим людям.</p>'''
    private_write(PRIVATE/'Настройка.html',html.encode())
    if open_browser:
        subprocess.Popen(['open',str(PRIVATE/'Настройка.html')],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    server.timeout=10
    while time.time()<deadline:server.handle_request()
    server.server_close()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--serve',action='store_true');parser.add_argument('--headless',action='store_true');args=parser.parse_args()
    if args.serve:serve(not args.headless)
    else:
        result=prepare();print(json.dumps({k:result[k] for k in ('items','files','missing')},ensure_ascii=False))
