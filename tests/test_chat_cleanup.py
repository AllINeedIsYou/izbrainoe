"""Conversation lifecycle: keep materials, remove controls and completed workflows."""
import asyncio
import time
import pytest
from izbrainoe.bot import Bot, TelegramError
from izbrainoe.config import Config
from izbrainoe.processing import Processor
from izbrainoe.store import Store


def test_conversation_lifecycles_and_restart(tmp_path, monkeypatch):
    async def run():
        clock=[time.time()]
        monkeypatch.setattr('izbrainoe.bot.time.time',lambda:clock[0])
        cfg=Config(data_dir=tmp_path,allowed_ids=(101,202,))
        s=Store(tmp_path);p=Processor(cfg,s);b=Bot(cfg,s,p)
        calls=[];counter=[1000]
        async def fake(method,**kw):
            calls.append((method,kw))
            if method.startswith('send'):
                counter[0]+=1
                return {'message_id':counter[0]}
            return True
        async def index(*args):pass
        async def feedback(*args):pass
        b.call=fake;p.index=index;p.feedback=feedback
        def message(mid,text,**extra):
            return {'chat':{'type':'private'},'from':{'id':101},'message_id':mid,'text':text,**extra}
        def sent(prefix):
            return [kw for method,kw in calls if method=='sendMessage' and kw['text'].startswith(prefix)]
        def deleted():return {kw['message_id'] for method,kw in calls if method=='deleteMessage'}
        def due():return {row['message_id'] for row in s.due_messages(clock[0])}
        async def context(item):
            await b.callback({'from':{'id':101},'id':'q','data':'ctx:'+item['id']})
            return counter[0]
        try:
            # Plain saved messages remain; the acknowledgement and inline actions expire at 8s.
            await b.message(message(10,'Заметка для сохранения'))
            first=s.find_message(101,10);ack=counter[0]
            assert sent('Сохранено:')[-1]['reply_markup']['inline_keyboard']
            clock[0]+=7.9;await b.expire_due();assert ack not in deleted()
            clock[0]+=.2;await b.expire_due();assert ack in deleted() and 10 not in deleted()
            # Normal context: original stays, prompt + typed context + confirmation disappear at 5s.
            prompt=await context(first)
            await b.message(message(11,'Это мой учебный материал'))
            confirmation=counter[0]
            assert 'учебный' in s.get(101,first['id'])['context']
            clock[0]+=4.9;await b.expire_due();assert not {11,prompt,confirmation}&deleted()
            # Restart must not forget the pending cleanup.
            again=Bot(cfg,Store(tmp_path),p);again.call=fake
            clock[0]+=.2;await again.expire_due();await again.close()
            assert {11,prompt,confirmation}<=deleted() and 10 not in deleted()
            # Trash scenario: the material's own Telegram message is also removed.
            await b.message(message(20,'Временная заметка'))
            second=s.find_message(101,20);second_ack=counter[0]
            prompt=await context(second)
            clock[0]+=9;await b.expire_due();assert second_ack not in deleted()  # Held while entering context.
            await b.message(message(21,'удалить'))
            trash_reply=counter[0]
            assert s.get(101,second['id']) is None and s.get(101,second['id'],trash=True)
            clock[0]+=5.1;await b.expire_due()
            assert {20,21,second_ack,prompt,trash_reply}<=deleted()
            assert 10 not in deleted() and len(s.list(101))==1
            # Search cancellation removes the whole block, including both user controls.
            await b.message(message(30,'🔎 Найти'));prompt=counter[0]
            await b.message(message(31,'/cancel'));cancel=counter[0]
            clock[0]+=4.9;await b.expire_due();assert not {30,31,prompt,cancel}&deleted()
            clock[0]+=.2;await b.expire_due();assert {30,31,prompt,cancel}<=deleted()
            # Context /cancel preserves original and stored context.
            prompt=await context(first)
            await b.message(message(32,'/cancel'));cancel=counter[0]
            clock[0]+=5.1;await b.expire_due();assert {32,prompt,cancel}<=deleted()
            assert 10 not in deleted() and s.get(101,first['id'])['context']
            # Isolation: another user's message ID is never touched.
            s.record_outgoing(202,{'message_id':6000});s.expire_message(202,6000,clock[0]+100)
            assert 6000 not in due() and 6000 not in deleted()
            assert all(kw['chat_id']==101 for method,kw in calls if method=='deleteMessage')
        finally:await b.close();await p.close()
    asyncio.run(run())


def test_search_results_cleanup_after_original_keeps_original(tmp_path,monkeypatch):
    async def run():
        clock=[time.time()];monkeypatch.setattr('izbrainoe.bot.time.time',lambda:clock[0])
        cfg=Config(data_dir=tmp_path,allowed_ids=(101,));s=Store(tmp_path);p=Processor(cfg,s);b=Bot(cfg,s,p)
        item=s.create(101,'document','Учебный файл',tg_message=1,tg_file_id='FAKE_FILE_ID')
        calls=[];counter=[100]
        async def fake(method,**kw):
            calls.append((method,kw))
            if method.startswith('send'):counter[0]+=1;return {'message_id':counter[0]}
            return True
        async def search(*a,**kw):return {'items':[{**item,'snippet':'Описание'}],'total':1,'mode':'keyword'}
        b.call=fake;p.search=search
        message=lambda mid,text:{'chat':{'type':'private'},'from':{'id':101},'message_id':mid,'text':text}
        deleted=lambda:{kw['message_id'] for method,kw in calls if method=='deleteMessage'}
        try:
            await b.message(message(2,'🔎 Найти'));prompt=counter[0]
            await b.message(message(3,'учебный файл'));card=counter[0];count=card-1
            assert prompt in deleted()  # Immediate, before asking for the original.
            clock[0]+=10;await b.expire_due();assert not {2,3,count,card}&deleted()
            await b.callback({'id':'q','from':{'id':101},'data':'get:'+item['id']});original=counter[0]
            assert any(method=='sendDocument' and kw['document']=='FAKE_FILE_ID' and kw['reply_markup']['is_persistent'] for method,kw in calls)
            clock[0]+=5.1;await b.expire_due()
            assert {2,3,count,card}<=deleted() and original not in deleted() and 1 not in deleted()
            assert len(s.list(101))==1 and s.find_message(101,original)['id']==item['id']
            assert not s.due_messages(clock[0]+1000)
            assert original not in {r['message_id'] for r in s.chat_messages(101)}
        finally:await b.close();await p.close()
    asyncio.run(run())


def test_abandoned_results_expire_and_inline_actions_restore_keyboard(tmp_path,monkeypatch):
    async def run():
        clock=[time.time()];monkeypatch.setattr('izbrainoe.bot.time.time',lambda:clock[0])
        cfg=Config(data_dir=tmp_path,allowed_ids=(101,),public_url='https://example.invalid')
        s=Store(tmp_path);p=Processor(cfg,s);b=Bot(cfg,s,p);calls=[];counter=[100]
        async def fake(method,**kw):
            calls.append((method,kw))
            if method.startswith('send'):counter[0]+=1;return {'message_id':counter[0]}
            return True
        async def search(*a,**kw):return {'items':[],'total':0,'mode':'keyword'}
        b.call=fake;p.search=search
        try:
            await b.message({'chat':{'type':'private'},'from':{'id':101},'message_id':1,'text':'🗂 Подборки'})
            sent=[kw for method,kw in calls if method=='sendMessage']
            assert sent[0]['reply_markup']['keyboard'] and sent[0]['reply_markup']['is_persistent']
            assert not sent[0]['reply_markup']['one_time_keyboard']
            assert sent[1]['reply_markup']['inline_keyboard']
            assert not any(method.startswith('edit') for method,kw in calls)
            await b.message({'chat':{'type':'private'},'from':{'id':101},'message_id':2,'text':'найди отсутствующий файл'})
            result=counter[0]
            clock[0]+=6;await b.expire_due()
            assert {2,result}<={kw['message_id'] for method,kw in calls if method=='deleteMessage'}
            clock[0]+=3;await b.expire_due()
            assert 1 in {kw['message_id'] for method,kw in calls if method=='deleteMessage'}
            assert not s.list(101)
        finally:await b.close();await p.close()
    asyncio.run(run())


def test_pending_input_times_out_without_capturing_next_note(tmp_path,monkeypatch):
    async def run():
        clock=[time.time()];monkeypatch.setattr('izbrainoe.bot.time.time',lambda:clock[0])
        cfg=Config(data_dir=tmp_path,allowed_ids=(101,));s=Store(tmp_path);p=Processor(cfg,s);b=Bot(cfg,s,p);calls=[];counter=[100]
        async def fake(method,**kw):
            calls.append((method,kw))
            if method.startswith('send'):counter[0]+=1;return {'message_id':counter[0]}
            return True
        async def index(*args):pass
        b.call=fake;p.index=index
        try:
            await b.message({'chat':{'type':'private'},'from':{'id':101},'message_id':1,'text':'🔎 Найти'})
            prompt=counter[0]
            clock[0]+=301;await b.expire_due()
            assert s.mode(101)==''
            assert {1,prompt}<={kw['message_id'] for method,kw in calls if method=='deleteMessage'}
            await b.message({'chat':{'type':'private'},'from':{'id':101},'message_id':2,'text':'Новая обычная заметка'})
            assert s.find_message(101,2)['body']=='Новая обычная заметка'
        finally:await b.close();await p.close()
    asyncio.run(run())


def test_existing_chat_cleanup_preserves_files_and_password_timer(tmp_path):
    async def run():
        cfg=Config(data_dir=tmp_path);s=Store(tmp_path);p=Processor(cfg,s);b=Bot(cfg,s,p)
        item=s.create(101,'document','Сохранённый файл',tg_message=1)
        for mid in (2,3,4):s.record_outgoing(101,{'message_id':mid})
        s.link_message(101,2,item['id'])  # Returned original must stay.
        s.record_service(101,{'message_id':5})
        s.record_outgoing(101,{'message_id':6,'date':time.time()-49*3600})
        s.expire_message(101,4,time.time()+300)  # Credential message keeps its five minutes.
        try:
            b.migrate_chat_cleanup()
            soon={r['message_id'] for r in s.due_messages(time.time()+9)}
            assert soon=={3,5}
            assert {r['message_id'] for r in s.due_messages(time.time()+301)}=={3,4,5}
            s.record_outgoing(101,{'message_id':7})
            b.migrate_chat_cleanup()
            assert 7 not in {r['message_id'] for r in s.due_messages(time.time()+1000)}
            assert len(s.list(101))==1
        finally:await b.close();await p.close()
    asyncio.run(run())
