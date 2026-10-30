import asyncio
import base64
import hashlib
import json
import os
import time
import pytest
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from izbrainoe.store import Store
from izbrainoe.vault_access import VaultAccess,VaultAccessError
from tests.test_app import env,signed,TOKEN


def fixture_vault(store,uid=101):
    salt=os.urandom(16);iv=os.urandom(12)
    key=hashlib.pbkdf2_hmac('sha256',b'Test1234',salt,600000,32)
    plain=json.dumps({'check':'izbrainoe-v1','entries':[{'id':'fake','title':'РХТУ','login':'fake@example.invalid','password':'TEST_ONLY_SECRET'}]}).encode()
    encode=lambda value:base64.b64encode(value).decode()
    envelope={'v':1,'kdf':'PBKDF2-SHA256','iterations':600000,'salt':encode(salt),'iv':encode(iv),
              'ciphertext':encode(AESGCM(key).encrypt(iv,plain,b'izbrainoe-vault-v1'))}
    store.save_vault(uid,0,envelope)
    return key,envelope


def wrap(challenge,key,envelope):
    public=serialization.load_der_public_key(base64.b64decode(challenge['spki']))
    plain=json.dumps({'key':base64.b64encode(key).decode(),'salt':envelope['salt'],
                      'nonce':challenge['nonce'],'version':challenge['version']}).encode()
    ciphertext=public.encrypt(plain,padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
    return base64.b64encode(ciphertext).decode()


def test_phone_enables_and_revokes_access_without_sending_master_password(env,monkeypatch):
    client,app=env;store=app.state.store;key,envelope=fixture_vault(store)
    app.state.bot.config.token=TOKEN
    client.cookies.clear();client.headers['Authorization']='tma '+signed(101)
    calls=[]
    async def native(uid,operation,password=None,key=None):
        calls.append((uid,operation));assert password is None
        if operation=='enable_key':assert base64.b64decode(key)==expected
        return {'ok':True}
    expected=key;monkeypatch.setattr(app.state.bot.vault_access,'call',native)
    # Raw password requests from a phone remain forbidden.
    assert client.post('/api/vault/bot-access',json={'password':'Test1234'}).status_code==403
    challenge=client.get('/api/vault/bot-access-key').json()
    payload={'wrapped_key':wrap(challenge,key,envelope),'nonce':challenge['nonce'],
             'lookup_tokens':[[hashlib.sha256('рхту'.encode()).hexdigest()]]}
    assert 'Test1234' not in json.dumps(payload) and base64.b64encode(key).decode() not in json.dumps(payload)
    response=client.post('/api/vault/bot-access',json=payload)
    assert response.status_code==200 and response.json()['enabled']
    assert store.meta('vault_bot_101')=='1' and store.known_vault_query(101,['рхту'])
    assert not store.known_vault_query(202,['рхту'])
    assert client.post('/api/vault/bot-access',json=payload).status_code==400  # One-use handoff.
    assert client.delete('/api/vault/bot-access').status_code==200
    assert store.meta('vault_bot_101')=='0'
    assert (101,'enable_key') in calls and (101,'disable') in calls


@pytest.mark.parametrize('failure',['expired','changed','other_user'])
def test_handoff_is_bound_to_user_version_and_expiry(tmp_path,failure):
    async def run():
        store=Store(tmp_path);key,envelope=fixture_vault(store);access=VaultAccess(store)
        challenge=await access.handoff_key(101);wrapped=wrap(challenge,key,envelope)
        async def forbidden(*args,**kwargs):raise AssertionError('Invalid grant reached Keychain')
        access.call=forbidden
        uid=101
        if failure=='expired':access.challenges[101]['expires']=time.monotonic()-1
        elif failure=='changed':store.save_vault(101,1,envelope)
        else:uid=202
        with pytest.raises(VaultAccessError):await access.enable_wrapped(uid,wrapped,challenge['nonce'])
        assert store.meta('vault_bot_101') is None
    asyncio.run(run())
