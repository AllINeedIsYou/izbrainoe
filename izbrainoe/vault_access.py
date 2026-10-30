"""Opt-in bot access using the macOS Keychain. No model or plaintext disk index."""
import asyncio
import hashlib
import json
from pathlib import Path
import re
import subprocess
from .config import ROOT


class VaultAccessError(Exception):
    pass


class VaultAccess:
    def __init__(self, store):
        self.store = store
        self.binary = ROOT / 'runtime/vault-keychain'
        self.scope = hashlib.sha256(str(store.folder.resolve()).encode()).hexdigest()[:24]
        self.build_lock = asyncio.Lock()

    async def call(self, uid, operation, password=None):
        async with self.build_lock:
            if not self.binary.exists():
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
