import asyncio
import json
import socket

from izbrainoe.analysis_policy import Analysis,review_reasons
from izbrainoe.bot import Bot
from izbrainoe.config import Config
from izbrainoe.processing import Processor
from izbrainoe.provod import Provod
from izbrainoe.research import PublicResearch, safe_public_url, safe_topic
from izbrainoe.store import Store

GOOD = dict(document_type='расписание', subject='', topics=['занятия'], groups=['Учёба / Расписание'],
            summary='Расписание учебных занятий.', evidence=['Строки связывают предметы, время и кабинеты'], confidence=.97,
            visual_summary='Таблица: день, время, предмет, аудитория.', alternatives=[], uncertainties=[],
            research_topics=[], needs_web=False, needs_context=False)


def setup(tmp_path):
    cfg=Config(data_dir=tmp_path,ai_provider='provod',provod_key='TEST_ONLY_KEY_1234567890',web_lookup=True)
    store=Store(tmp_path);return cfg,store,Processor(cfg,store)


def meta():
    return dict(status='used',provider='provod',images=1,usage={'input_tokens':100,'output_tokens':100,'estimated_rub':.0364})


def test_clear_image_uses_one_request_and_no_search(tmp_path,monkeypatch):
    calls=[]
    async def analyse(self,*args):calls.append(args);return GOOD,meta()
    async def forbidden(*args):raise AssertionError('Unnecessary search')
    monkeypatch.setattr(Provod,'analyse',analyse);monkeypatch.setattr(PublicResearch,'verify',forbidden)
    async def run():
        cfg,s,p=setup(tmp_path)
        try:
            i=s.create(101,'photo','Фото','',path='/image.jpg')
            await p.index(101,i['id']);a=s.get(101,i['id'])['analysis']
            assert len(calls)==1 and a['cloud']['passes']==1 and a['web']['status']=='not_needed'
            assert a['visual_summary'].startswith('Таблица')
        finally:await p.close()
    asyncio.run(run())


def test_conflicting_ocr_triggers_one_research_and_visual_review(tmp_path,monkeypatch):
    calls=[];topics=[]
    async def analyse(self,item,payload,*args):
        calls.append(payload)
        if len(calls)==1:
            return {**GOOD,'document_type':'конспект','subject':'Физика','research_topics':['учебное расписание'],'needs_web':True},meta()
        assert payload['_analysis_stage']=='review' and len(payload['public_type_reference'])==2
        return GOOD,meta()
    async def research(self,values):
        topics.extend(values)
        return {'status':'checked','sources':[{'title':'А','url':'https://example.org/a','extract':'Признаки расписания'},
                                               {'title':'Б','url':'https://example.com/b','extract':'Связь событий со временем'}]}
    monkeypatch.setattr(Provod,'analyse',analyse);monkeypatch.setattr(PublicResearch,'verify',research)
    async def run():
        cfg,s,p=setup(tmp_path)
        try:
            i=s.create(101,'photo','Фото','Начало занятий. Понедельник 14:00 15:00. Среда 14:00 15:00.',path='/image.jpg')
            await p.index(101,i['id']);a=s.get(101,i['id'])['analysis']
            assert a['document_type']=='расписание' and a['subject']==''
            assert len(calls)==2 and topics==['учебное расписание']
            assert a['cloud']['reviewed'] and a['cloud']['usage']['input_tokens']==200
            assert 'layout_conflict' in a['cloud']['review_reasons']
        finally:await p.close()
    asyncio.run(run())


def test_unreadable_image_requests_context_without_repeated_calls(tmp_path,monkeypatch):
    calls=[]
    async def analyse(self,*args):
        calls.append(1)
        return {**GOOD,'document_type':'','groups':[],'evidence':[],'confidence':.2,'needs_context':True,
                'visual_summary':'Размытая таблица; мелкие надписи не читаются.'},meta()
    monkeypatch.setattr(Provod,'analyse',analyse)
    async def run():
        cfg,s,p=setup(tmp_path)
        try:
            i=s.create(101,'photo','Фото','',path='/image.jpg');await p.index(101,i['id'])
            r=s.get(101,i['id']);assert r['groups']==[] and r['analysis_state']=='needs_context' and len(calls)==1
        finally:await p.close()
    asyncio.run(run())


def test_duplicate_cache_is_private_contextual_and_explicit_reread_is_fresh(tmp_path,monkeypatch):
    calls=[]
    async def analyse(self,*args):calls.append(1);return GOOD,meta()
    monkeypatch.setattr(Provod,'analyse',analyse)
    async def run():
        cfg,s,p=setup(tmp_path)
        try:
            a=s.create(101,'photo','Фото','',path='/image.jpg',digest='same-bytes')
            b=s.create(101,'photo','Другое имя','',path='/image.jpg',digest='same-bytes')
            await asyncio.gather(p.index(101,a['id']),p.index(101,b['id']))
            assert len(calls)==1 and s.get(101,b['id'])['analysis']['cloud']['cached']
            other=s.create(202,'photo','Фото','',path='/image.jpg',digest='same-bytes')
            await p.index(202,other['id']);assert len(calls)==2
            s.update(101,b['id'],context='Этот файл относится к моим занятиям')
            await p.index(101,b['id']);assert len(calls)==3
            await p.reread(101,a['id']);assert len(calls)==4
            assert not s.get(101,a['id'])['analysis']['cloud']['cached']
        finally:await p.close()
    asyncio.run(run())


def test_safe_topics_reject_private_identifiers():
    for topic in ('Иванов Иван Иванович','номер договора 145663','пароль пользователя',
                  'https://private.example','someone@example.com','ул. Пушкина 15','79991234567'):
        assert not safe_topic(topic),topic
    assert safe_topic('расходный кассовый ордер')
    assert safe_topic('форма Т-53')


def test_source_fetch_rejects_private_dns_and_credentials(monkeypatch):
    def addresses(host,*args,**kwargs):
        return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1' if host=='private.example' else '93.184.216.34',443))]
    monkeypatch.setattr(socket,'getaddrinfo',addresses)
    for url in ('http://example.org','https://private.example/a','https://user:pass@example.org',
                'https://localhost/a','https://example.org:8080/a'):
        assert not safe_public_url(url)
    assert safe_public_url('https://example.org/a')


def test_document_types_are_not_limited_to_school_subjects():
    a=Analysis(document_type='электрическая схема',subject='Электроника')
    assert a.document_type=='электрическая схема'
    assert Analysis(document_type='Расписание занятий').document_type=='расписание'


def test_small_text_only_gets_an_extra_pass_if_more_detail_is_available():
    a=Analysis(needs_context=True,confidence=.3,visual_summary='Таблица с мелким неразборчивым текстом')
    assert review_reasons(a,images=1,can_zoom=False)==[]
    assert review_reasons(a,images=1,can_zoom=True)==['small_text']
    a.visual_summary='Пустой серый лист'
    assert review_reasons(a,images=1,can_zoom=True)==[]


def test_public_research_cache_and_disabled_mode(tmp_path,monkeypatch):
    calls=[]
    def search(self,topic):
        calls.append(topic)
        return {'status':'checked','sources':[{'url':'https://example.org','extract':'Общие признаки'}]}
    monkeypatch.setattr(PublicResearch,'search',search)
    async def run():
        cfg,s,p=setup(tmp_path)
        try:
            first=await p.analyzer.research.verify(['кассовый ордер'])
            second=await p.analyzer.research.verify(['кассовый ордер'])
            assert first['status']=='checked' and second['cached'] and calls==['кассовый ордер']
            assert (await p.analyzer.research.verify(['79991234567']))['status']=='no_safe_query'
            cfg.web_lookup=False
            assert (await p.analyzer.research.verify(['кассовый ордер']))['status']=='disabled'
        finally:await p.close()
    asyncio.run(run())


def test_failed_embedding_is_not_retried_for_each_queued_photo(tmp_path,monkeypatch):
    import httpx
    calls=[]
    async def post(*args,**kwargs):
        calls.append(1);raise httpx.ReadTimeout('temporary')
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    async def run():
        cfg,s,p=setup(tmp_path)
        try:
            await asyncio.gather(*(p.embed(['Текст']) for _ in range(3)),return_exceptions=True)
            assert len(calls)==1
        finally:await p.close()
    asyncio.run(run())


def test_media_batch_is_parallel_bounded_and_commands_are_barriers(tmp_path):
    async def run():
        cfg,s,p=setup(tmp_path);cfg.allowed_ids=(101,)
        b=Bot(cfg,s,p);active=peak=0;done=[]
        async def message(m,edited=False):
            nonlocal active,peak
            if m.get('text'):
                assert active==0 and len(done)==6
                done.append('command');return
            active+=1;peak=max(peak,active)
            await asyncio.sleep(.01)
            done.append(m['message_id']);active-=1
        b.message=message
        updates=[{'update_id':i,'message':{'from':{'id':101},'chat':{'type':'private'},'message_id':i,'photo':[{'file_id':str(i)}]}} for i in range(6)]
        updates.append({'update_id':6,'message':{'from':{'id':101},'message_id':6,'text':'/help'}})
        try:
            await b.handle_updates(updates)
            assert peak==3 and done[-1]=='command'
            s.mode(101,'context:record')
            assert not b.parallel_media(updates[0])
        finally:await b.close();await p.close()
    asyncio.run(run())
