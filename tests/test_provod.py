import asyncio
import json
import stat
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient

from izbrainoe.app import create_app
from izbrainoe.config import Config
from izbrainoe.gemini import GeminiError, MODEL
from izbrainoe.knowledge import Analysis
from izbrainoe.processing import Processor
from izbrainoe.provod import API, Provod, RubUsage
from izbrainoe.store import Store

KEY = 'sk_TEST_ONLY_NOT_A_REAL_PROVOD_KEY_123456'
RESULT = dict(document_type='расписание', subject='', topics=['учёба'], groups=['Учёба / Расписание'],
              summary='Расписание учебных занятий.', evidence=['Начало урока'], confidence=.95)
CAPS = dict(id=MODEL, available=True, architecture={'input_modalities': ['text', 'image', 'file']},
            supported_parameters=['response_format', 'max_tokens', 'reasoning_effort'])


def setup(tmp_path, limit=1000):
    cfg = Config(data_dir=tmp_path, ai_provider='provod', provod_key=KEY, provod_monthly_limit_rub=limit)
    store = Store(tmp_path)
    return cfg, store, Provod(cfg, store)


def mock_api(monkeypatch, store, mode='ok'):
    calls = []
    async def get(self, url, **kw):
        assert url == API + '/models' and KEY not in url
        assert kw['headers']['Authorization'] == 'Bearer ' + KEY
        return httpx.Response(200, json={'data': [CAPS]})
    async def post(self, url, **kw):
        calls.append(kw['json'])
        assert url == API + '/chat/completions' and KEY not in url
        assert RubUsage(store).summary()['reserved_rub'] > 0
        assert kw['headers']['Authorization'] == 'Bearer ' + KEY
        if mode == 'timeout':
            raise httpx.ReadTimeout('untrusted key: ' + KEY)
        if mode in ('denied', 'server'):
            return httpx.Response(403 if mode == 'denied' else 503, json={'error': {'message': KEY}})
        return httpx.Response(200, json={'usage': {'prompt_tokens': 4000, 'completion_tokens': 600,
                                                 'completion_tokens_details': {'reasoning_tokens': 100}},
            'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(RESULT) if mode != 'invalid' else 'bad'}}]})
    monkeypatch.setattr(httpx.AsyncClient, 'get', get)
    monkeypatch.setattr(httpx.AsyncClient, 'post', post)
    return calls


def test_ruble_usage_and_vision_request(tmp_path, monkeypatch):
    cfg, store, p = setup(tmp_path)
    calls = mock_api(monkeypatch, store)
    async def previews(*args):
        return [{'number': n, 'data': '/9j/', 'mime_type': 'image/jpeg'} for n in range(1, 5)]
    monkeypatch.setattr('izbrainoe.provod.visual_pages', previews)
    result, meta = asyncio.run(p.analyse({'user_id': 101, 'kind': 'document'}, {'content': 'Начало урока'}, 'Classify', Analysis.model_json_schema()))
    assert result['document_type'] == 'расписание' and meta['images'] == 4
    assert meta['usage']['estimated_rub'] == .4258  # reasoning already in completion_tokens
    body = calls[0]
    assert body['model'] == MODEL and body['max_tokens'] == 2048
    assert len([part for part in body['messages'][1]['content'] if part['type'] == 'image_url']) == 4
    schema = body['response_format']['json_schema']['schema']
    assert schema['additionalProperties'] is False and set(schema['required']) == set(schema['properties'])
    assert p.usage.summary()['reserved_rub'] == 0
    assert KEY not in json.dumps(meta) and KEY not in json.dumps(p.status())


@pytest.mark.parametrize('mode,reserved,spent', [('timeout', True, False), ('server', True, False),
                                              ('denied', False, False), ('invalid', False, True)])
def test_errors_preserve_uncertain_cost_and_hide_credentials(tmp_path, monkeypatch, mode, reserved, spent):
    cfg, store, p = setup(tmp_path)
    mock_api(monkeypatch, store, mode)
    with pytest.raises(GeminiError) as error:
        asyncio.run(p.analyse({'user_id': 101, 'kind': 'note'}, {}, 'Classify', {}))
    assert KEY not in str(error.value) and KEY not in p.error
    assert bool(p.usage.summary()['reserved_rub']) is reserved
    assert bool(p.usage.summary()['estimated_rub']) is spent


def test_budget_prevents_generation_and_atomic_reservations(tmp_path, monkeypatch):
    cfg, store, p = setup(tmp_path, .01)
    calls = mock_api(monkeypatch, store)
    with pytest.raises(GeminiError, match='лимит расходов'):
        asyncio.run(p.analyse({'user_id': 101, 'kind': 'note'}, {}, 'Classify', {}))
    assert calls == []
    def reserve(n):
        try:
            return p.usage.reserve(101, 4000, 1, 0)
        except GeminiError:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        keys = list(pool.map(reserve, range(6)))
    assert len([key for key in keys if key]) == 1
    assert p.usage.summary()['reserved_rub'] <= 1


def test_model_must_support_images_and_schema(tmp_path, monkeypatch):
    cfg, store, p = setup(tmp_path)
    async def get(*args, **kwargs):
        return httpx.Response(200, json={'data': [{**CAPS, 'architecture': {'input_modalities': ['text']}}]})
    monkeypatch.setattr(httpx.AsyncClient, 'get', get)
    with pytest.raises(GeminiError, match='поддержку фото'):
        asyncio.run(p.check_key(KEY))


def test_key_local_only_hidden_and_google_key_preserved(tmp_path, monkeypatch):
    async def check(self, key):
        assert key == KEY
    monkeypatch.setattr(Provod, 'check_key', check)
    cfg = Config(data_dir=tmp_path, gemini_key='OLD_GOOGLE_KEY_KEPT')
    app = create_app(cfg)
    with TestClient(app) as client:
        client.headers['X-Iz-Request'] = '1'
        assert client.post('/api/ai/provod', json={'key': KEY}).status_code == 401
        client.get('/local', params={'key': app.state.store.meta('local_key')})
        r = client.post('/api/ai/provod', json={'key': KEY, 'monthly_limit_rub': 1000})
        assert r.status_code == 200 and cfg.ai_provider == 'provod'
        assert KEY not in r.text + client.get('/api/me').text + client.get('/api/export').text + repr(cfg)
        path = tmp_path / 'ai-settings.json'
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        saved = json.loads(path.read_text())
        assert saved['provod_key'] == KEY and saved['gemini_key'] == 'OLD_GOOGLE_KEY_KEPT'
        assert client.post('/api/ai/provod', json={'key': KEY}, headers={'Origin': 'https://evil.example'}).status_code == 403
        assert client.post('/api/ai/provod', json={'key': KEY, 'monthly_limit_rub': -1}).status_code == 422
        assert client.post('/api/ai/local').status_code == 200 and not app.state.processor.analyzer.provod.enabled


def test_provod_analyser_used_for_image_even_without_ocr(tmp_path, monkeypatch):
    async def fake(self, *args):
        return RESULT, dict(status='used', provider='provod', images=1, model=MODEL)
    monkeypatch.setattr(Provod, 'analyse', fake)
    async def run():
        cfg, store, p = setup(tmp_path)
        processor = Processor(cfg, store)
        try:
            item = store.create(101, 'photo', 'image.jpg', '')
            store.update(101, item['id'], path='/fake.jpg', mime='image/jpeg')
            await processor.index(101, item['id'])
            result = store.get(101, item['id'])['analysis']
            assert result['method'] == 'gemini' and result['cloud']['provider'] == 'provod'
        finally:
            await processor.close()
    asyncio.run(run())


def test_known_secrets_never_reach_provod(tmp_path, monkeypatch):
    async def forbidden(*args):
        raise AssertionError('Secret reached Provod')
    monkeypatch.setattr(Provod, 'analyse', forbidden)
    async def run():
        cfg, store, p = setup(tmp_path)
        processor = Processor(cfg, store)
        try:
            item = store.create(101, 'note', 'Секрет', 'password: FAKE_TEST_ONLY')
            await processor.index(101, item['id'])
            assert store.get(101, item['id'])['analysis_state'] == 'excluded'
        finally:
            await processor.close()
    asyncio.run(run())
