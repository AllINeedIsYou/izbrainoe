import asyncio
import base64
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode
import pytest
from fastapi.testclient import TestClient
from izbrainoe.config import Config
from izbrainoe.app import create_app
from izbrainoe.auth import validate_telegram
from izbrainoe.bot import Bot
from izbrainoe.processing import sensitive

TOKEN='123456789:'+('test_only_'*4)

def signed(uid,token=TOKEN,age=0):
    d={'auth_date':str(int(time.time())-age),'user':json.dumps({'id':uid,'first_name':'Тест'})}
    check='\n'.join(f'{k}={v}' for k,v in sorted(d.items()))
    key=hmac.new(b'WebAppData',token.encode(),hashlib.sha256).digest()
    d['hash']=hmac.new(key,check.encode(),hashlib.sha256).hexdigest()
    return urlencode(d)

@pytest.fixture
def env(tmp_path):
    config=Config(data_dir=tmp_path,allowed_ids=(101,202))
    app=create_app(config)
    app.state.bot.config.token=''  # no external calls from unit tests
    with TestClient(app) as c:
        key=app.state.store.meta('local_key')
        c.get('/local',params={'key':key})
        c.headers['X-Iz-Request']='1'
        yield c,app

def test_signed_auth_rejects_forgery_and_expiration():
    assert validate_telegram(signed(101),TOKEN)==101
    for bad in (signed(101)+'&user='+json.dumps({'id':202}),signed(101,age=90000),signed(101).replace('101','202')):
        with pytest.raises(ValueError): validate_telegram(bad,TOKEN)

def test_local_login_cannot_be_used_on_public_host(env):
    c,app=env
    key=app.state.store.meta('local_key')
    assert c.get('/local',params={'key':key},headers={'Host':'evil.example'}).status_code==403
    assert c.get('/api/items',headers={'Host':'evil.example'}).status_code==401

def test_unauthorized_has_no_access(env):
    c,app=env;c.cookies.clear()
    assert c.get('/api/items').status_code==401
    assert c.get('/api/vault').status_code==401

def test_csrf_guard(env):
    c,_=env
    assert c.post('/api/items',json={'text':'Заметка'},headers={'Origin':'https://evil.example'}).status_code==403
    c.headers.pop('X-Iz-Request')
    assert c.post('/api/items',json={'text':'Заметка'}).status_code==403

def test_save_search_context_and_manual_groups(env):
    c,_=env
    i=c.post('/api/items',json={'text':'Договор на обучение в университете. Стоимость 120000 рублей.'}).json()
    assert 'Вуз' in i['groups']
    assert c.get('/api/items',params={'q':'стоимость обучения'}).json()['total']==1
    updated=c.patch('/api/items/'+i['id'],json={'groups':['Мой проект'],'context':'Оплата общежития'}).json()
    assert updated['groups']==['Мой проект']
    updated=c.patch('/api/items/'+i['id'],json={'context':'Материалы для поступления'}).json()
    assert updated['groups']==['Мой проект']
    updated=c.patch('/api/items/'+i['id'],json={'automatic':True}).json()
    assert 'Вуз' in updated['groups']

def test_number_search_is_precise(env):
    c,_=env
    for n in (18,19): c.post('/api/items',json={'text':f'Шпора. Задание №{n}.pdf'})
    r=c.get('/api/items',params={'q':'задание 19'}).json()
    assert r['total']==1 and '19' in r['items'][0]['title']

def test_soft_delete_restore_and_search(env):
    c,_=env
    i=c.post('/api/items',json={'text':'Сохранённый билет на поезд'}).json()
    assert c.delete('/api/items/'+i['id']).status_code==200
    assert c.get('/api/items',params={'q':'билет'}).json()['total']==0
    assert c.get('/api/items',params={'trash':'true'}).json()['total']==1
    c.post('/api/items/'+i['id']+'/restore')
    assert c.get('/api/items',params={'q':'билет'}).json()['total']==1

def test_user_isolation_all_item_routes(env):
    c,app=env
    store=app.state.store
    other=store.create(202,'note','Чужая запись','Чужие материалы')
    for route in ('','/file','/preview'):
        assert c.get('/api/items/'+other['id']+route).status_code==404
    assert c.patch('/api/items/'+other['id'],json={'title':'Подмена'}).status_code==404
    assert c.delete('/api/items/'+other['id']).status_code==404
    assert c.post('/api/items/'+other['id']+'/restore').status_code==404
    assert c.get('/api/items').json()['total']==0

def test_pair_is_one_time_and_invites_expire(env):
    _,app=env;s=app.state.store
    assert s.pair(101,s.meta('pair_code'))
    assert not s.pair(202,'wrong')
    code=s.invite()
    assert s.pair(202,code)
    assert not s.pair(303,code)
    code=s.invite()
    with s.db() as db: db.execute('UPDATE invites SET expires=0 WHERE code=?',(code,))
    assert not s.pair(404,code)

def test_upload_text_and_duplicate_hash(env):
    c,app=env
    for _ in range(2):
        r=c.post('/api/upload',files={'file':('../../sample.txt','Поступление в университет'.encode(),'text/plain')})
        assert r.status_code==201
        assert r.json()['filename']=='sample.txt'
    with app.state.store.db() as db:
        hashes=[r[0] for r in db.execute('SELECT digest FROM items')]
    assert hashes[0]==hashes[1]

def test_html_original_is_attachment(env):
    c,_=env
    i=c.post('/api/upload',files={'file':('x.html',b'<script>alert(1)</script>','text/html')}).json()
    r=c.get('/api/items/'+i['id']+'/file')
    assert r.headers['content-type']=='application/octet-stream'
    assert 'attachment' in r.headers['content-disposition']
    assert c.get('/api/items/'+i['id']+'/preview').status_code==404

def test_secret_not_saved_or_sent_to_index(env):
    c,app=env
    assert c.post('/api/items',json={'text':'Пароль от сервиса: TEST-NOT-REAL'}).status_code==400
    assert c.get('/api/items').json()['total']==0
    assert sensitive('API_KEY = TEST')
    assert c.post('/api/upload',files={'file':('secret.txt',b'password = TEST-NOT-REAL','text/plain')}).status_code==400
    assert not list((app.state.store.folder/'files').iterdir())

def test_vault_ciphertext_only_and_optimistic_lock(env):
    c,app=env
    e={'v':1,'kdf':'PBKDF2-SHA256','iterations':600000,
       'salt':base64.b64encode(bytes(16)).decode(),'iv':base64.b64encode(bytes(12)).decode(),
       'ciphertext':base64.b64encode(bytes(64)).decode()}
    assert c.put('/api/vault',json={'version':0,'envelope':e}).status_code==200
    assert c.put('/api/vault',json={'version':0,'envelope':e}).status_code==409
    assert c.get('/api/vault').json()['version']==1
    assert app.state.store.vault(202)['envelope'] is None
    e['plaintext']='TEST-NOT-REAL'
    assert c.put('/api/vault',json={'version':1,'envelope':e}).status_code==400

def test_bot_save_context_forwarded_query_and_isolation(env):
    _,app=env;b=app.state.bot;store=app.state.store
    sent=[]
    async def fake(method,**kw):
        sent.append((method,kw));return {'message_id':1000+len(sent)}
    b.call=fake
    def msg(text,mid,**extra):return {'chat':{'type':'private'},'from':{'id':101},'message_id':mid,'text':text,**extra}
    asyncio.run(b.message(msg('Найди эту статью позже',1,forward_origin={'type':'hidden_user'})))
    assert len(store.list(101))==1
    asyncio.run(b.message(msg('Это про поступление в вуз',2,reply_to_message={'message_id':1})))
    i=store.list(101)[0]
    assert 'поступление' in i['context'] and 'Вуз' in i['groups']
    asyncio.run(b.message(msg('Повтор',1)))
    assert len(store.list(101))==1
    asyncio.run(b.message({'chat':{'type':'private'},'from':{'id':999},'message_id':3,'text':'Чужой'}))
    assert not store.list(999)

def test_bot_secrets_and_video_not_saved(env):
    _,app=env;b=app.state.bot;sent=[]
    async def fake(method,**kw):sent.append(kw);return {'message_id':1}
    b.call=fake
    common={'chat':{'type':'private'},'from':{'id':101},'message_id':4}
    asyncio.run(b.message({**common,'text':'password: FAKE-ONLY'}))
    asyncio.run(b.message({**common,'video':{'file_id':'fake'}}))
    assert not app.state.store.list(101)
    assert len(sent)==2

def test_upload_size_limit(env):
    c,_=env
    assert c.post('/api/upload',headers={'Content-Length':str(103*1024*1024)}).status_code==413

def test_security_headers(env):
    c,_=env;r=c.get('/')
    assert r.status_code==200
    assert "object-src 'none'" in r.headers['content-security-policy']
    assert r.headers['referrer-policy']=='no-referrer'

def test_reply_to_returned_original_adds_context(env):
    _,app=env;b=app.state.bot;s=app.state.store
    i=s.create(101,'note','Расписание','Расписание занятий')
    async def fake(method,**kw):return {'message_id':777}
    b.call=fake
    asyncio.run(b.send_original(101,i['id']))
    assert s.find_message(101,777)['id']==i['id']
    assert s.find_message(202,777) is None
    asyncio.run(b.message({'chat':{'type':'private'},'from':{'id':101},'message_id':778,
                          'reply_to_message':{'message_id':777},'text':'Это расписание университета'}))
    assert 'университета' in s.get(101,i['id'])['context']

def test_missing_context_stays_unassigned(env):
    c,_=env
    for text in ['-', 'ЦЕЛЕВОЕ', 'фулл биз\\инф']:
        assert c.post('/api/items',json={'text':text}).json()['groups']==[]

def test_owner_pairing_keeps_local_material_and_separates_testers(env):
    c,app=env;s=app.state.store
    item=c.post('/api/items',json={'text':'Моя локальная заметка'}).json()
    assert s.pair(101,s.meta('pair_code'))
    assert s.get(101,item['id']) is not None
    assert s.get(-1,item['id']) is None
    assert s.get(202,item['id']) is None
    assert c.get('/api/me').json()['id']==101

def test_start_updates_menu_for_this_user(env):
    _,app=env;b=app.state.bot;b.config.public_url='https://new-address.example.invalid'
    calls=[]
    async def fake(method,**kw):calls.append((method,kw));return {'message_id':1}
    b.call=fake
    asyncio.run(b.message({'chat':{'type':'private'},'from':{'id':101},'message_id':99,'text':'/start'}))
    menu=next(kw for method,kw in calls if method=='setChatMenuButton')
    assert menu['chat_id']==101
    assert menu['menu_button']['web_app']['url']=='https://new-address.example.invalid'

def test_startup_refreshes_menu_for_existing_users(env):
    _,app=env;b=app.state.bot;b.config.token=TOKEN;b.config.public_url='https://new-address.example.invalid'
    calls=[]
    async def fake(method,**kw):
        calls.append((method,kw))
        if method=='getMe':return {'username':'izbrainoe_bot'}
        if method=='getWebhookInfo':return {'url':''}
        if method=='getUpdates':raise asyncio.CancelledError()
        return True
    b.call=fake
    with pytest.raises(asyncio.CancelledError):asyncio.run(b.run())
    users={kw.get('chat_id') for method,kw in calls if method=='setChatMenuButton'}
    assert {101,202}.issubset(users)
