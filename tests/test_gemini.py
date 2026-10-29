import asyncio
import base64
import json
import stat
from datetime import datetime,timezone
from pathlib import Path
import httpx
import pytest
from fastapi.testclient import TestClient
from izbrainoe.app import create_app
from izbrainoe.config import Config,ROOT
from izbrainoe.store import Store
from izbrainoe.processing import Processor
from izbrainoe.gemini import Gemini,GeminiError,Usage,MODEL,prices,visual_pages

KEY='TEST_NOT_A_REAL_GEMINI_API_KEY_123456789'
RESULT={'document_type':'расписание','subject':'','groups':['Учёба / Расписание'],'summary':'Расписание школьных занятий.',
        'topics':['занятия'],'evidence':['Начало урока'],'confidence':.95}

def setup(tmp_path,limit=5):
    cfg=Config(data_dir=tmp_path,ai_provider='gemini',gemini_key=KEY,ai_monthly_limit_usd=limit)
    s=Store(tmp_path);return cfg,s,Gemini(cfg,s)

def mock_api(monkeypatch,store,mode='success'):
    calls=[]
    async def post(self,url,**kw):
        assert KEY not in url and kw['headers']['x-goog-api-key']==KEY
        calls.append((url,kw))
        if url.endswith(':countTokens'):
            assert 'generateContentRequest' in kw['json']
            return httpx.Response(200,json={'totalTokens':4000})
        assert url.endswith(':generateContent')
        assert Usage(store).summary()['reserved_usd']>0
        assert 'tools' not in kw['json']
        assert kw['json']['generationConfig']['thinkingConfig']['thinkingLevel']=='LOW'
        if mode=='timeout':raise httpx.ReadTimeout('unsafe error '+KEY)
        if mode=='denied':return httpx.Response(403,json={'error':{'message':KEY}})
        data={'usageMetadata':{'promptTokenCount':4000,'candidatesTokenCount':500,'thoughtsTokenCount':100},
              'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':json.dumps(RESULT)}]}}]}
        if mode=='invalid':data['candidates'][0]['content']['parts'][0]['text']='not json'
        return httpx.Response(200,json=data)
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    return calls

def test_usage_counts_thoughts_and_excludes_key(tmp_path,monkeypatch):
    cfg,s,g=setup(tmp_path);calls=mock_api(monkeypatch,s)
    raw,meta=asyncio.run(g.analyse({'user_id':101,'kind':'note'},{'content':'Начало урока'},'Classify',{}))
    assert raw['document_type']=='расписание'
    assert meta['usage']=={'input_tokens':4000,'output_tokens':600,'estimated_usd':.00525}
    assert g.usage.summary()['reserved_usd']==0
    assert len(calls)==2 and KEY not in json.dumps(meta)

def test_budget_blocks_paid_request(tmp_path,monkeypatch):
    cfg,s,g=setup(tmp_path,limit=.001);calls=mock_api(monkeypatch,s)
    with pytest.raises(GeminiError,match='лимит расходов'):
        asyncio.run(g.analyse({'user_id':101,'kind':'note'},{'content':'Text'},'Classify',{}))
    assert len(calls)==1 and calls[0][0].endswith(':countTokens')
    assert g.usage.summary()['requests']==0

def test_timeout_keeps_reservation_and_hides_key(tmp_path,monkeypatch):
    cfg,s,g=setup(tmp_path);mock_api(monkeypatch,s,'timeout')
    with pytest.raises(GeminiError) as err:asyncio.run(g.analyse({'user_id':101,'kind':'note'},{},'Classify',{}))
    assert KEY not in str(err.value)
    assert g.usage.summary()['reserved_usd']>0
    with s.db() as c:assert c.execute('SELECT status FROM ai_usage').fetchone()[0]=='unknown'

def test_rejected_request_releases_reservation(tmp_path,monkeypatch):
    cfg,s,g=setup(tmp_path);mock_api(monkeypatch,s,'denied')
    with pytest.raises(GeminiError) as err:asyncio.run(g.analyse({'user_id':101,'kind':'note'},{},'Classify',{}))
    assert KEY not in str(err.value)
    assert g.usage.summary()['reserved_usd']==0 and g.usage.summary()['estimated_usd']==0

def test_invalid_json_still_records_billable_usage(tmp_path,monkeypatch):
    cfg,s,g=setup(tmp_path);mock_api(monkeypatch,s,'invalid')
    with pytest.raises(GeminiError):asyncio.run(g.analyse({'user_id':101,'kind':'note'},{},'Classify',{}))
    assert g.usage.summary()['estimated_usd']==.00525
    assert g.usage.summary()['reserved_usd']==0

def test_concurrent_budget_reservations_are_atomic(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    cfg,s,g=setup(tmp_path)
    def reserve(n):
        try:return g.usage.reserve(101,4000,.02,0)
        except GeminiError:return None
    with ThreadPoolExecutor(max_workers=4) as pool:keys=list(pool.map(reserve,range(6)))
    assert len([k for k in keys if k])==1
    assert g.usage.summary()['reserved_usd']<=.02

def test_announced_price_change():
    assert prices(datetime(2026,12,31,tzinfo=timezone.utc))==(.75,3.75)
    assert prices(datetime(2027,1,1,tzinfo=timezone.utc))==(1.5,7.5)

def test_prepare_only_first_four_pdf_pages_and_one_photo(tmp_path):
    if not (ROOT/'runtime/ocr').exists():pytest.skip('Requires native helper')
    async def run():
        pdf={'path':str(Path(__file__).parent/'fixtures/scanned.pdf'),'kind':'document','filename':'scanned.pdf','mime':'application/pdf'}
        images=await visual_pages(pdf,4)
        assert len(images)==4 and [r['number'] for r in images]==[1,2,3,4]
        assert all(max(r['width'],r['height'])<=1536 for r in images)
        path=tmp_path/'photo.jpg';path.write_bytes(base64.b64decode(images[0]['data']))
        one=await visual_pages({'path':str(path),'kind':'photo','mime':'image/jpeg'},4)
        assert len(one)==1 and base64.b64decode(one[0]['data']).startswith(b'\xff\xd8')
    asyncio.run(run())

def test_connect_local_only_and_key_not_exported(tmp_path,monkeypatch):
    async def check(self,key):assert key==KEY
    monkeypatch.setattr(Gemini,'check_key',check)
    cfg=Config(data_dir=tmp_path);app=create_app(cfg)
    with TestClient(app) as c:
        c.get('/local',params={'key':app.state.store.meta('local_key')});c.headers['X-Iz-Request']='1'
        r=c.post('/api/ai/gemini',json={'key':KEY,'monthly_limit_usd':5})
        assert r.status_code==200 and r.json()['gemini']['key_configured']
        assert KEY not in r.text and KEY not in c.get('/api/me').text and KEY not in c.get('/api/export').text
        assert 'gemini_key' not in repr(cfg) and KEY not in repr(cfg)
        path=tmp_path/'ai-settings.json'
        assert stat.S_IMODE(path.stat().st_mode)==0o600
        assert json.loads(path.read_text())['gemini_key']==KEY
        assert c.post('/api/ai/gemini',json={'key':KEY},headers={'Host':'evil.example'}).status_code==401
        assert c.post('/api/ai/local').status_code==200 and cfg.ai_provider=='local'

def test_gemini_visual_evidence_works_when_ocr_is_empty(tmp_path,monkeypatch):
    async def fake(self,item,payload,system,schema):return RESULT,{'status':'used','images':1,'usage':None,'model':MODEL}
    monkeypatch.setattr(Gemini,'analyse',fake)
    async def run():
        cfg,s,g=setup(tmp_path);p=Processor(cfg,s)
        image=tmp_path/'example.jpg';image.write_bytes(b'fake image supplied by a stub')
        i=s.create(101,'photo','Фото.jpg','',path=str(image),mime='image/jpeg')
        await p.index(101,i['id']);r=s.get(101,i['id'])
        assert r['analysis']['method']=='gemini'
        assert r['groups']==['Учёба / Расписание']
        assert r['analysis_state']=='ready'
        found=await p.search(101,'школьных занятий')
        assert found['total']==1 and found['items'][0]['id']==i['id']
        await p.close()
    asyncio.run(run())

def test_gemini_failure_falls_back_without_losing_material(tmp_path,monkeypatch):
    async def fail(self,*args):raise GeminiError('Достигнут лимит','budget_limit')
    monkeypatch.setattr(Gemini,'analyse',fail)
    async def run():
        cfg,s,g=setup(tmp_path);p=Processor(cfg,s)
        i=s.create(101,'note','Материал','Сочинение по русскому языку о памяти и ответственности человека.')
        await p.index(101,i['id']);r=s.get(101,i['id'])
        assert r['groups']==['Русский язык / Сочинения']
        assert r['analysis']['cloud']['status']=='budget_limit' and r['body']
        await p.close()
    asyncio.run(run())

def test_secrets_never_reach_gemini(tmp_path,monkeypatch):
    async def forbidden(*args):raise AssertionError('Secret reached Gemini')
    monkeypatch.setattr(Gemini,'analyse',forbidden)
    async def run():
        cfg,s,g=setup(tmp_path);p=Processor(cfg,s)
        i=s.create(101,'note','Секрет','password: FAKE_TEST_ONLY')
        await p.index(101,i['id'])
        assert s.get(101,i['id'])['analysis_state']=='excluded'
        await p.close()
    asyncio.run(run())

def test_summary_cannot_override_exact_task_number(tmp_path):
    async def run():
        cfg=Config(data_dir=tmp_path);s=Store(tmp_path);p=Processor(cfg,s)
        i=s.create(101,'note','Задание 18','Решить задание 18 по математике.')
        s.update(101,i['id'],analysis_json=json.dumps({'summary':'Задание 19','topics':['задачи']}))
        assert (await p.search(101,'задание 19'))['total']==0
        assert (await p.search(101,'задание 18'))['total']==1
        await p.close()
    asyncio.run(run())

def test_disabling_during_preflight_prevents_generation(tmp_path,monkeypatch):
    cfg,s,g=setup(tmp_path);calls=[]
    async def post(self,url,**kw):
        calls.append(url);assert url.endswith(':countTokens')
        cfg.ai_provider='local'
        return httpx.Response(200,json={'totalTokens':4000})
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    with pytest.raises(GeminiError,match='отключена'):
        asyncio.run(g.analyse({'user_id':101,'kind':'note'},{},'Classify',{}))
    assert len(calls)==1 and g.usage.summary()['requests']==0

def test_overload_is_separate_from_billing_and_releases_reservation(tmp_path,monkeypatch):
    cfg,s,g=setup(tmp_path)
    async def post(self,url,**kw):
        return httpx.Response(200,json={'totalTokens':4000}) if url.endswith(':countTokens') else httpx.Response(503,json={'error':{'status':'UNAVAILABLE'}})
    monkeypatch.setattr(httpx.AsyncClient,'post',post)
    with pytest.raises(GeminiError) as err:asyncio.run(g.analyse({'user_id':101,'kind':'note'},{},'Classify',{}))
    assert err.value.code=='overloaded' and '503' in str(err.value)
    assert g.usage.summary()['reserved_usd']==0 and g.usage.summary()['estimated_usd']==0
    assert Gemini.api_error(402).code=='payment_required'
