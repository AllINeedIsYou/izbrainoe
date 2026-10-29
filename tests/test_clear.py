import asyncio
import time
import pytest
from izbrainoe.config import Config
from izbrainoe.store import Store
from izbrainoe.processing import Processor
from izbrainoe.bot import Bot,TelegramError


def test_clear_only_own_outgoing_and_preserves_archive(tmp_path):
    async def run():
        s=Store(tmp_path);b=Bot(Config(data_dir=tmp_path,allowed_ids=(101,)),s,Processor(Config(),s))
        item=s.create(101,'note','Важное','Сохраняется в архиве',tg_message=55)
        s.update(101,item['id'],bot_message=56)
        s.record_outgoing(101,{'message_id':56});s.record_outgoing(101,{'message_id':57})
        s.record_outgoing(202,{'message_id':58})
        calls=[]
        async def fake(method,**kw):
            calls.append((method,kw));return {'message_id':1000} if method=='sendMessage' else True
        b.call=fake
        await b.message({'chat':{'type':'private'},'from':{'id':101},'message_id':60,'text':'/clear'})
        batch=next(kw['message_ids'] for method,kw in calls if method=='deleteMessages')
        assert batch==[56,57,60] and 55 not in batch and 58 not in batch
        assert s.get(101,item['id'])['body']=='Сохраняется в архиве'
        assert len(s.list(101))==1 and s.outgoing(202)[0]['message_id']==58
        assert [r['message_id'] for r in s.outgoing(101)]==[1000]
        await b.close()
    asyncio.run(run())


def test_clear_batches_and_rejects_foreign_user(tmp_path):
    async def run():
        s=Store(tmp_path);cfg=Config(data_dir=tmp_path,allowed_ids=(101,));b=Bot(cfg,s,Processor(cfg,s));calls=[]
        for n in range(1,251):s.record_outgoing(101,{'message_id':n})
        s.record_outgoing(101,{'message_id':251,'date':time.time()-3*86400})
        async def fake(method,**kw):calls.append((method,kw));return {'message_id':1000} if method=='sendMessage' else True
        b.call=fake
        await b.message({'chat':{'type':'private'},'from':{'id':202},'message_id':999,'text':'/clear'})
        assert not calls
        result=await b.clear_chat(101)
        assert result=={'processed':250,'expired':1,'failed':0}
        batches=[kw['message_ids'] for method,kw in calls if method=='deleteMessages']
        assert list(map(len,batches))==[100,100,50]
        assert 251 not in sum(batches,[])
        await b.close()
    asyncio.run(run())


def test_clear_falls_back_without_forgetting_failed_messages(tmp_path):
    async def run():
        s=Store(tmp_path);cfg=Config(data_dir=tmp_path);b=Bot(cfg,s,Processor(cfg,s))
        for mid in (1,2,3):s.record_outgoing(101,{'message_id':mid})
        async def fake(method,**kw):
            if method=='deleteMessages':raise TelegramError('Some message cannot be deleted')
            if method=='deleteMessage' and kw['message_id']==2:raise TelegramError('Message cannot be deleted')
            if method=='deleteMessage' and kw['message_id']==3:raise TelegramError('Message to delete not found')
            return {'message_id':1000} if method=='sendMessage' else True
        b.call=fake
        r=await b.clear_chat(101)
        assert r['processed']==2 and r['failed']==1
        assert {row['message_id'] for row in s.outgoing(101)}=={2,1000}
        await b.close()
    asyncio.run(run())


def test_forwarded_clear_is_a_note_not_a_command(tmp_path):
    async def run():
        cfg=Config(data_dir=tmp_path,allowed_ids=(101,));s=Store(tmp_path);b=Bot(cfg,s,Processor(cfg,s));calls=[]
        async def fake(method,**kw):calls.append((method,kw));return {'message_id':999}
        b.call=fake
        await b.message({'chat':{'type':'private'},'from':{'id':101},'message_id':50,'text':'/clear','forward_origin':{'type':'hidden_user'}})
        assert len(s.list(101))==1 and s.list(101)[0]['body']=='/clear'
        assert not any(method.startswith('delete') for method,kw in calls)
        await b.close()
    asyncio.run(run())

def test_clear_notification_disappears(tmp_path,monkeypatch):
    async def no_delay(seconds):assert seconds==8
    monkeypatch.setattr('izbrainoe.bot.asyncio.sleep',no_delay)
    async def run():
        cfg=Config(data_dir=tmp_path);s=Store(tmp_path);b=Bot(cfg,s,Processor(cfg,s));calls=[]
        async def fake(method,**kw):calls.append((method,kw));return {'message_id':900} if method=='sendMessage' else True
        b.call=fake
        await b.clear_chat(101)
        await asyncio.gather(*list(b.tasks))
        assert ('deleteMessage',{'chat_id':101,'message_id':900}) in calls
        assert not s.outgoing(101)
        await b.close()
    asyncio.run(run())

def test_clear_reply_can_delete_untracked_legacy_bot_answer(tmp_path):
    async def run():
        cfg=Config(data_dir=tmp_path,allowed_ids=(101,),token='123:fake');s=Store(tmp_path);b=Bot(cfg,s,Processor(cfg,s));calls=[]
        async def fake(method,**kw):calls.append((method,kw));return {'message_id':900} if method=='sendMessage' else True
        b.call=fake
        await b.message({'chat':{'type':'private'},'from':{'id':101},'message_id':71,'text':'/clear',
                         'reply_to_message':{'message_id':70,'date':time.time()-3600,'from':{'id':123,'is_bot':True}}})
        assert ('deleteMessages',{'chat_id':101,'message_ids':[70,71]}) in calls
        import json
        assert json.loads(s.meta('last_clear_101'))['processed']==2
        await b.close()
    asyncio.run(run())


def test_clear_reply_does_not_delete_user_or_another_bots_message(tmp_path):
    async def run():
        cfg=Config(data_dir=tmp_path,allowed_ids=(101,),token='123:fake');s=Store(tmp_path);b=Bot(cfg,s,Processor(cfg,s));calls=[]
        async def fake(method,**kw):calls.append((method,kw));return {'message_id':900} if method=='sendMessage' else True
        b.call=fake
        for sender in ({'id':101,'is_bot':False},{'id':124,'is_bot':True,'username':cfg.bot_username}):
            b.observe_own_message(101,{'message_id':70,'from':sender})
        await b.clear_chat(101)
        assert not any(method=='deleteMessages' for method,kw in calls)
        assert any('нет служебных' in kw.get('text','') for method,kw in calls)
        await b.close()
    asyncio.run(run())


def test_clear_removes_controls_after_restart_and_keeps_materials(tmp_path):
    async def run():
        cfg=Config(data_dir=tmp_path,allowed_ids=(101,));s=Store(tmp_path);b=Bot(cfg,s,Processor(cfg,s));calls=[]
        item=s.create(101,'photo','Учебный план','Календарный план',tg_message=5)
        s.update(101,item['id'],context='Мой план учёбы')
        async def fake(method,**kw):
            calls.append((method,kw));return {'message_id':1000+len(calls)} if method=='sendMessage' else True
        b.call=fake
        controls=('/start','📁 Подборки','🗂️ Подборки','📂 Подборки','🔎 Найти','/cancel','/help@'+cfg.bot_username)
        for mid,text in enumerate(controls,10):
            await b.message({'chat':{'type':'private'},'from':{'id':101},'message_id':mid,'text':text})
        assert len(s.list(101))==1  # Button messages must never become archive notes.
        await b.close()
        s=Store(tmp_path);b=Bot(cfg,s,Processor(cfg,s));b.call=fake
        s.record_service(202,{'message_id':90})
        await b.message({'chat':{'type':'private'},'from':{'id':101},'message_id':20,'text':'/clear'})
        deleted={mid for method,kw in calls if method=='deleteMessages' for mid in kw['message_ids']}
        assert set(range(10,17))|{20} <= deleted
        assert 5 not in deleted and 90 not in deleted
        assert s.get(101,item['id'])['context']=='Мой план учёбы'
        with s.db() as c:
            assert c.execute('SELECT count(*) FROM service_messages WHERE user_id=101').fetchone()[0]==0
            assert c.execute('SELECT count(*) FROM service_messages WHERE user_id=202').fetchone()[0]==1
        await b.close()
    asyncio.run(run())


@pytest.mark.parametrize('text',('/start','📁 Подборки','/clear'))
def test_reply_clear_adopts_old_own_control(tmp_path,text):
    async def run():
        cfg=Config(data_dir=tmp_path,allowed_ids=(101,));s=Store(tmp_path);b=Bot(cfg,s,Processor(cfg,s));calls=[]
        async def fake(method,**kw):calls.append((method,kw));return {'message_id':900} if method=='sendMessage' else True
        b.call=fake
        await b.message({'chat':{'type':'private'},'from':{'id':101},'message_id':71,'text':'/clear',
                         'reply_to_message':{'message_id':70,'text':text,'date':time.time()-3600,'from':{'id':101}}})
        assert ('deleteMessages',{'chat_id':101,'message_ids':[70,71]}) in calls
        await b.close()
    asyncio.run(run())


@pytest.mark.parametrize('reply',[
    {'text':'Пояснение к учебному плану'},
    {'caption':'/clear','photo':[{'file_id':'photo'}]},
    {'text':'/start','forward_origin':{'type':'hidden_user'}},
    {'text':'📁 Подборки','from':{'id':202}},
    {'text':'/clear@another_bot'},
])
def test_reply_clear_preserves_material_and_foreign_messages(tmp_path,reply):
    async def run():
        cfg=Config(data_dir=tmp_path,allowed_ids=(101,));s=Store(tmp_path);b=Bot(cfg,s,Processor(cfg,s));calls=[]
        async def fake(method,**kw):calls.append((method,kw));return {'message_id':900} if method=='sendMessage' else True
        b.call=fake
        original={'message_id':70,'from':{'id':101},**reply}
        await b.message({'chat':{'type':'private'},'from':{'id':101},'message_id':71,'text':'/clear','reply_to_message':original})
        assert ('deleteMessages',{'chat_id':101,'message_ids':[71]}) in calls
        await b.close()
    asyncio.run(run())


def test_photo_clear_caption_is_saved_and_clear_keeps_photo(tmp_path):
    async def run():
        cfg=Config(data_dir=tmp_path,allowed_ids=(101,));s=Store(tmp_path);b=Bot(cfg,s,Processor(cfg,s));calls=[]
        async def fake(method,**kw):calls.append((method,kw));return {'message_id':900+len(calls)} if method=='sendMessage' else True
        async def download(*args):return '','',''
        async def index(*args):pass
        b.call=fake;b.download=download;b.processor.index=index
        await b.message({'chat':{'type':'private'},'from':{'id':101},'message_id':70,'caption':'/clear','photo':[{'file_id':'photo'}]})
        assert s.list(101)[0]['body']=='/clear' and s.list(101)[0]['kind']=='photo'
        assert not any(method.startswith('delete') for method,kw in calls)
        await b.message({'chat':{'type':'private'},'from':{'id':101},'message_id':71,'text':'/clear'})
        assert 70 not in {mid for method,kw in calls if method=='deleteMessages' for mid in kw['message_ids']}
        assert len(s.list(101))==1
        await b.close()
    asyncio.run(run())


def test_clear_after_media_batch_leaves_no_late_confirmation(tmp_path):
    async def run():
        cfg=Config(data_dir=tmp_path,allowed_ids=(101,));s=Store(tmp_path);b=Bot(cfg,s,Processor(cfg,s));calls=[]
        async def fake(method,**kw):calls.append((method,kw));return {'message_id':900+len(calls)} if method=='sendMessage' else True
        async def download(*args):return '','',''
        async def index(*args):pass
        b.call=fake;b.download=download;b.processor.index=index
        common={'chat':{'type':'private'},'from':{'id':101}}
        await b.handle_updates([{'message':{**common,'message_id':70,'photo':[{'file_id':'photo'}]}},
                                {'message':{**common,'message_id':71,'text':'/clear'}}])
        sent=[kw for method,kw in calls if method=='sendMessage']
        assert len(sent)==2 and sent[-1]['text'].startswith('Обработано служебных')
        assert ('deleteMessages',{'chat_id':101,'message_ids':[71,901]}) in calls
        assert len(s.list(101))==1
        await b.close()
    asyncio.run(run())
