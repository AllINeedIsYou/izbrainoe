"""Opt-in bot access using the macOS Keychain. No model or plaintext disk index."""
import asyncio
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import secrets
import time
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import padding,rsa
from .config import ROOT


class VaultAccessError(Exception):
    pass


class VaultAccess:
    def __init__(self, store):
        self.store = store
        self.binary = ROOT / 'runtime/vault-keychain'
        self.scope = hashlib.sha256(str(store.folder.resolve()).encode()).hexdigest()[:24]
        self.build_lock = asyncio.Lock()
        self.challenges={}

    async def call(self, uid, operation, password=None, key=None):
        async with self.build_lock:
            if not self.binary.exists() or (ROOT/'scripts/vault-keychain.swift').stat().st_mtime>self.binary.stat().st_mtime:
                def build():
                    self.binary.parent.mkdir(parents=True, exist_ok=True)
                    subprocess.run(['xcrun', 'swiftc', str(ROOT / 'scripts/vault-keychain.swift'), '-o', str(self.binary)],
                                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
                    subprocess.run(['codesign', '--force', '--sign', '-', str(self.binary)], check=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
                try: await asyncio.to_thread(build)
                except Exception: raise VaultAccessError('Не удалось подготовить доступ к Связке ключей на этом Mac.') from None
        envelope = self.store.vault(uid)['envelope']
        if not envelope and operation != 'disable':
            raise VaultAccessError('Сначала создай хранилище паролей.')
        payload = {'operation': operation, 'account': self.scope + ':' + str(uid), 'envelope': envelope}
        if password is not None: payload['password'] = password
        if key is not None:payload['key']=key
        def execute():
            return subprocess.run([str(self.binary)], input=json.dumps(payload).encode(), capture_output=True, timeout=30)
        try:
            response = await asyncio.to_thread(execute)
            result = json.loads(response.stdout)
        except Exception:
            raise VaultAccessError('Связка ключей недоступна. Разблокируй хранилище на Mac.') from None
        if not result.get('ok'):
            if result.get('code') == 'wrong_password': raise VaultAccessError('Неверный главный пароль.')
            raise VaultAccessError('Доступ бота выключен или Связка ключей заблокирована. Разблокируй хранилище на Mac.')
        return result

    async def enabled(self, uid):
        # The preference avoids compiling or invoking Keychain for ordinary saves.
        if self.store.meta(f'vault_bot_{uid}') != '1': return False
        try:
            await self.call(uid, 'status')
            return True
        except VaultAccessError: return False

    async def enable(self, uid, password):
        await self.call(uid, 'enable', password)
        self.store.meta(f'vault_bot_{uid}', '1')

    async def handoff_key(self,uid):
        vault=self.store.vault(uid)
        if not vault['envelope']:raise VaultAccessError('Сначала создай хранилище паролей.')
        private=await asyncio.to_thread(rsa.generate_private_key,public_exponent=65537,key_size=2048)
        nonce=secrets.token_urlsafe(18)
        self.challenges={u:c for u,c in self.challenges.items() if c['expires']>time.monotonic()}
        self.challenges[uid]={'key':private,'nonce':nonce,'expires':time.monotonic()+120,'version':vault['version']}
        def expire():
            current=self.challenges.get(uid)
            if current and current['nonce']==nonce:self.challenges.pop(uid,None)
        asyncio.get_running_loop().call_later(120,expire)
        public=private.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)
        return {'spki':base64.b64encode(public).decode(),'nonce':nonce,'version':vault['version']}

    async def enable_wrapped(self,uid,wrapped_key,nonce):
        challenge=self.challenges.pop(uid,None)
        if not challenge or challenge['expires']<time.monotonic() or not secrets.compare_digest(challenge['nonce'],nonce):
            raise VaultAccessError('Время подключения истекло. Нажми «Включить выдачу» ещё раз.')
        vault=self.store.vault(uid)
        try:
            plain=challenge['key'].decrypt(base64.b64decode(wrapped_key,validate=True),
                padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
            value=json.loads(plain)
            if value['nonce']!=challenge['nonce'] or value['version']!=vault['version'] or vault['version']!=challenge['version']:raise ValueError()
            if value['salt']!=vault['envelope']['salt'] or len(base64.b64decode(value['key'],validate=True))!=32:raise ValueError()
        except (ValueError,KeyError,TypeError):raise VaultAccessError('Не удалось подтвердить ключ. Повтори подключение хранилища.') from None
        await self.call(uid,'enable_key',key=value['key'])
        self.store.meta(f'vault_bot_{uid}','1')

    async def disable(self, uid):
        self.store.meta(f'vault_bot_{uid}', '0')
        await self.call(uid, 'disable')

    async def entries(self, uid):
        if self.store.meta(f'vault_bot_{uid}') != '1': return []
        return (await self.call(uid, 'read')).get('entries', [])


FILLERS = set('данные аккаунт аккаунта учетные учётные логин пароль пароли доступ вход входа покажи пришли дай найди найти мой моя мои мне для от в на и к пожалуйста'.split())


def credential_query(text):
    words=re.findall(r'[\w]+',text.casefold().replace('ё','е'))
    return [word for word in words if word not in FILLERS and len(word)>1]


def credential_matches(text, entries):
    words=credential_query(text)
    if not words: return []
    matches=[]
    for entry in entries:
        name=str(entry.get('title','')).casefold().replace('ё','е')
        aliases=str(entry.get('aliases','')).casefold().replace('ё','е')
        terms=set(re.findall(r'[\w]+',name+' '+aliases))
        if all(word in terms for word in words): matches.append(entry)
    exact=[entry for entry in matches if str(entry.get('title','')).casefold().replace('ё','е')==' '.join(words)]
    return exact or matches
