import asyncio
import time
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
        assert batch==[56,57] and 55 not in batch and 58 not in batch
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
