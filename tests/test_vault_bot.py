import asyncio
import json
import time
import pytest
from izbrainoe.bot import Bot,TelegramError
from izbrainoe.config import Config
from izbrainoe.processing import Processor
from izbrainoe.store import Store
from izbrainoe.vault_access import VaultAccessError,credential_matches
from tests.test_app import env,signed


ENTRY={'id':'test-only','title':'РХТУ','login':'test@example.invalid','password':'TEST_ONLY_<secret>&'}


@pytest.mark.parametrize('query',['рхту','данные рхту','Логин и пароль РХТУ','найди аккаунт рхту'])
def test_credential_name_matching(query):
    assert credential_matches(query,[ENTRY])==[ENTRY]
    assert not credential_matches(query,[{**ENTRY,'title':'Другой сервис'}])
    assert not credential_matches(ENTRY['password'],[ENTRY])  # Password is never an index.


def test_aliases_and_ambiguity_are_explicit():
    assert credential_matches('рхту',[{**ENTRY,'title':'Личный кабинет','aliases':'рхту'}])
    assert not credential_matches('данные',[ENTRY])
    assert len(credential_matches('рхту',[{**ENTRY,'title':'РХТУ почта'},{**ENTRY,'title':'РХТУ кабинет'}]))==2


def test_vault_lookup_spoiler_and_expiry_never_reach_ai_or_archive(tmp_path):
    async def run():
        s=Store(tmp_path);cfg=Config(data_dir=tmp_path,allowed_ids=(101,));p=Processor(cfg,s);b=Bot(cfg,s,p);calls=[]
        s.save_vault(101,0,{'encrypted':'test'});s.meta('vault_bot_101','1')
        async def entries(uid):assert uid==101;return [ENTRY]
        async def fake(method,**kw):calls.append((method,kw));return {'message_id':50}
        async def forbidden(*args):raise AssertionError('Credentials must not reach processing or a model')
        b.vault_access.entries=entries;b.call=fake;p.index=forbidden;p.search=forbidden
        try:
            start=time.time()
            await b.message({'chat':{'type':'private'},'from':{'id':101},'message_id':40,'text':'данные рхту'})
            message=next(kw for method,kw in calls if method=='sendMessage')
            assert message['parse_mode']=='HTML' and message['chat_id']==101
            secret=message['text'].split('<tg-spoiler>')[1].split('</tg-spoiler>')[0]
            assert ENTRY['login'] in secret and 'TEST_ONLY_&lt;secret&gt;&amp;' in secret
            assert not s.list(101)
            assert not s.due_messages(start+299)
            assert s.due_messages(time.time()+301)[0]['message_id']==50
            # New bot instance simulates a restart; no secret is needed to delete.
            again=Bot(cfg,Store(tmp_path),p);again.call=fake
            with s.db() as c:c.execute('UPDATE expiring_messages SET next_attempt=0')
            await again.expire_due()
            assert ('deleteMessage',{'chat_id':101,'message_id':50}) in calls
            assert not s.due_messages(time.time()+1000)
            assert not s.outgoing(101)
            await again.close()
            with s.db() as c:
                for table in ('items','chunks','memories','web_cache','analysis_cache'):
                    assert c.execute(f'SELECT count(*) FROM {table}').fetchone()[0]==0
        finally:await b.close();await p.close()
    asyncio.run(run())


def test_expiry_retries_connection_failure_and_clear_forgets_timer(tmp_path):
    async def run():
        s=Store(tmp_path);cfg=Config(data_dir=tmp_path);p=Processor(cfg,s);b=Bot(cfg,s,p)
        s.record_outgoing(101,{'message_id':50});s.expire_message(101,50,0)
        async def fail(*a,**kw):raise TelegramError('Нет соединения')
        b.call=fail
        try:
            await b.expire_due()
            assert not s.due_messages(time.time()+25)
            assert s.due_messages(time.time()+35)[0]['message_id']==50
            s.forget_chat_messages(101,[50])
            assert not s.due_messages(time.time()+10000)
        finally:await b.close();await p.close()
    asyncio.run(run())


def test_bot_access_accepts_eight_and_rejects_short_without_echo(env,monkeypatch):
    client,app=env
    app.state.store.meta('owner','101')
    async def enable(uid,password):
        assert uid==101 and password=='Test1234'
    monkeypatch.setattr(app.state.bot.vault_access,'enable',enable)
    short='Short77'
    response=client.post('/api/vault/bot-access',json={'password':short})
    assert response.status_code==400 and short not in response.text
    response=client.post('/api/vault/bot-access',json={'password':'Test1234'})
    assert response.status_code==200 and 'Test1234' not in response.text
    client.cookies.clear()
    assert client.post('/api/vault/bot-access',json={'password':'Test1234'}).status_code==401


def test_no_broad_credential_dump_or_ambiguous_selection(tmp_path):
    async def run():
        s=Store(tmp_path);cfg=Config(data_dir=tmp_path,allowed_ids=(101,));p=Processor(cfg,s);b=Bot(cfg,s,p);calls=[]
        s.save_vault(101,0,{'encrypted':'test'});s.meta('vault_bot_101','1')
        async def entries(uid):return [{**ENTRY,'title':'РХТУ почта'},{**ENTRY,'title':'РХТУ кабинет'}]
        async def fake(method,**kw):calls.append(kw);return {'message_id':50}
        b.vault_access.entries=entries;b.call=fake
        try:
            assert await b.vault_lookup(101,'данные')
            assert await b.vault_lookup(101,'рхту')
            assert all(ENTRY['password'] not in kw['text'] and '<tg-spoiler>' not in kw['text'] for kw in calls)
        finally:await b.close();await p.close()
    asyncio.run(run())
