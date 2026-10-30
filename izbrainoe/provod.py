"""Provod's OpenAI-compatible Gemini route, with a separate ruble ledger."""
import asyncio
import copy
import json
import time
import uuid

import httpx

from .gemini import GeminiError, MODEL, OUTPUT_CAP, month, visual_pages

API = 'https://api.provod.ai/v1'
# Conservative rounded estimates from the public RUB tariff checked 2026-10-29.
# Provider billing and hidden reasoning can differ; these are not bank limits.
INPUT_PRICE = 61.0
OUTPUT_PRICE = 303.0


def strict_schema(schema):
    value = copy.deepcopy(schema)
    def visit(node):
        if isinstance(node, dict):
            node.pop('default', None)
            node.pop('title', None)
            if node.get('type') == 'object':
                node['additionalProperties'] = False
                node['required'] = list(node.get('properties', {}))
            for child in list(node.values()):
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)
    visit(value)
    return value


class RubUsage:
    def __init__(self, store):
        self.store = store

    def summary(self):
        with self.store.db() as c:
            r = c.execute("SELECT COALESCE(SUM(cost_rub),0),COALESCE(SUM(reserved_rub),0),COUNT(*),COALESCE(SUM(CASE WHEN status='done' THEN input_tokens ELSE 0 END),0),COALESCE(SUM(output_tokens),0),COALESCE(SUM(status='rejected'),0) FROM provod_usage WHERE month=?", (month(),)).fetchone()
        return dict(month=month(), estimated_rub=round(r[0], 4), reserved_rub=round(r[1], 4),
                    requests=r[2], input_tokens=r[3], output_tokens=r[4], failed_requests=r[5])

    def reserve(self, uid, incoming, limit, images, output_cap=OUTPUT_CAP):
        amount = (incoming * INPUT_PRICE + output_cap * OUTPUT_PRICE) / 1e6
        key = uuid.uuid4().hex
        with self.store.db() as c:
            c.execute('BEGIN IMMEDIATE')
            spent = c.execute('SELECT COALESCE(SUM(cost_rub+reserved_rub),0) FROM provod_usage WHERE month=?', (month(),)).fetchone()[0]
            if limit <= 0 or spent + amount > limit + 1e-9:
                raise GeminiError('Достигнут лимит расходов Provod. Материал разобран локально.', 'budget_limit')
            c.execute('INSERT INTO provod_usage(id,user_id,month,created,model,status,input_tokens,reserved_rub,images) VALUES(?,?,?,?,?,?,?,?,?)',
                      (key, uid, month(), time.time(), MODEL, 'pending', incoming, amount, images))
        return key

    def complete(self, key, usage):
        if not isinstance(usage, dict) or not all(isinstance(usage.get(k), int) and usage[k] >= 0 for k in ('prompt_tokens', 'completion_tokens')):
            self.unknown(key)
            return None
        incoming, outgoing = usage['prompt_tokens'], usage['completion_tokens']
        # OpenAI-compatible completion_tokens already includes reasoning tokens.
        cost = (incoming * INPUT_PRICE + outgoing * OUTPUT_PRICE) / 1e6
        with self.store.db() as c:
            c.execute("UPDATE provod_usage SET status='done',input_tokens=?,output_tokens=?,cost_rub=?,reserved_rub=0 WHERE id=?",
                      (incoming, outgoing, cost, key))
        return dict(input_tokens=incoming, output_tokens=outgoing, estimated_rub=round(cost, 4))

    def reject(self, key):
        with self.store.db() as c:
            c.execute("UPDATE provod_usage SET status='rejected',reserved_rub=0 WHERE id=?", (key,))

    def unknown(self, key):
        with self.store.db() as c:
            c.execute("UPDATE provod_usage SET status='unknown' WHERE id=? AND status='pending'", (key,))


class Provod:
    def __init__(self, config, store):
        self.config = config
        self.usage = RubUsage(store)
        self.lock = asyncio.Semaphore(3)
        self.preflight_lock = asyncio.Lock()
        self.client = None
        self.error = ''
        self.capabilities = None

    async def close(self):
        if self.client is not None:
            await self.client.aclose()
            self.client = None

    @property
    def enabled(self):
        return self.config.ai_provider == 'provod' and bool(self.config.provod_key)

    def status(self):
        return dict(provider='provod', model=MODEL, key_configured=bool(self.config.provod_key),
                    error=self.error, monthly_limit_rub=self.config.provod_monthly_limit_rub,
                    usage=self.usage.summary(), input_price=INPUT_PRICE, output_price=OUTPUT_PRICE,
                    currency='RUB', prices_checked='2026-10-29', analysis_policy='vision-v4-topic-folders')

    @staticmethod
    def api_error(status):
        messages = {
            400: ('Provod отклонил формат запроса. Использован локальный анализ.', 'invalid_request'),
            401: ('Provod отклонил ключ. Проверь обычный API-ключ в кабинете.', 'access_denied'),
            403: ('Нет доступа к Gemini через Provod. Проверь ключ и первое пополнение.', 'access_denied'),
            402: ('На балансе Provod недостаточно средств или достигнут лимит ключа.', 'payment_required'),
            404: ('Gemini 3.8 Flash недоступна в Provod.', 'model_unavailable'),
            429: ('Достигнут лимит Provod. Проверь ограничения ключа и статистику.', 'quota'),
            422: ('Provod отклонил параметры запроса. Использован локальный анализ.', 'invalid_request'),
        }
        message, code = messages.get(status, ('Provod временно недоступен. Материал разобран локально.', 'unavailable'))
        return GeminiError(message, code)

    async def check_key(self, key):
        try:
            async with httpx.AsyncClient(timeout=20, trust_env=False, follow_redirects=False) as c:
                r = await c.get(API + '/models', headers={'Authorization': 'Bearer ' + key})
            if not r.is_success:
                raise self.api_error(r.status_code)
            model = next((m for m in r.json()['data'] if m.get('id') == MODEL), None)
            if not model or model.get('available') is not True:
                raise GeminiError('Gemini 3.8 Flash пока недоступна этому аккаунту Provod.', 'model_unavailable')
            inputs = model.get('architecture', {}).get('input_modalities', [])
            params = model.get('supported_parameters', [])
            if 'image' not in inputs or not any(p in params for p in ('response_format', 'structured_outputs')):
                raise GeminiError('Provod не подтвердил поддержку фото и JSON для Gemini 3.8 Flash.', 'capabilities')
            if not any(p in params for p in ('max_completion_tokens', 'max_tokens')):
                raise GeminiError('Provod не подтвердил ограничение длины ответа.', 'capabilities')
            self.capabilities = model
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            raise GeminiError('Не удалось проверить ключ Provod. Значение ключа не показывается.', 'connection') from None

    async def analyse(self, item, payload, system, schema):
        if not self.enabled:
            raise GeminiError('Нужен ключ Provod.', 'not_configured')
        key = None
        try:
            async with self.lock:
                if self.capabilities is None:
                    async with self.preflight_lock:
                        if self.capabilities is None:
                            await self.check_key(self.config.provod_key)
                review = payload.get('_analysis_stage') == 'review'
                output_cap = 1600 if review else 1200
                visuals = await visual_pages(item, self.config.pdf_pages, 2048 if review else 1536)
                content = [{'type': 'text', 'text': json.dumps(payload, ensure_ascii=False)}]
                for im in visuals:
                    content.extend([{'type': 'text', 'text': f'Страница {im["number"]}.'},
                                    {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + im['data']}}])
                instruction = system
                params = self.capabilities['supported_parameters']
                cap = 'max_completion_tokens' if 'max_completion_tokens' in params else 'max_tokens'
                body = dict(model=MODEL, messages=[{'role': 'system', 'content': instruction}, {'role': 'user', 'content': content}],
                            response_format={'type': 'json_schema', 'json_schema': {'name': 'archive_analysis', 'strict': True, 'schema': strict_schema(schema)}},
                            stream=False, **{cap: output_cap})
                if 'reasoning_effort' in params:
                    body['reasoning_effort'] = 'medium' if review else 'low'
                # No paid token-count endpoint: overestimate text by UTF-8 byte count
                # and reserve a generous bound per 1536px preview. This is an estimate.
                incoming = len((instruction + json.dumps(payload, ensure_ascii=False)).encode('utf-8')) + 512 + len(visuals) * 16384
                if incoming > 100000:
                    raise GeminiError('Материал слишком большой для одного разбора Provod.', 'too_large')
                if not self.enabled:
                    raise GeminiError('Provod отключён. Использован локальный анализ.', 'disabled')
                key = self.usage.reserve(item['user_id'], incoming, self.config.provod_monthly_limit_rub, len(visuals), output_cap)
                if self.client is None:
                    self.client = httpx.AsyncClient(timeout=httpx.Timeout(30, connect=8), trust_env=False, follow_redirects=False,
                                                    limits=httpx.Limits(max_connections=3, max_keepalive_connections=3))
                r = await self.client.post(API + '/chat/completions', headers={'Authorization': 'Bearer ' + self.config.provod_key}, json=body)
                if not r.is_success:
                    if r.status_code in (400, 401, 402, 403, 404, 422, 429):
                        self.usage.reject(key)
                    else:
                        self.usage.unknown(key)
                    raise self.api_error(r.status_code)
                data = r.json()
                usage = self.usage.complete(key, data.get('usage'))
                choice = data['choices'][0]
                if choice.get('finish_reason') != 'stop':
                    raise GeminiError('Gemini через Provod не завершила разбор. Использован локальный анализ.', 'incomplete')
                result = json.loads(choice['message']['content'])
                if not isinstance(result, dict):
                    raise ValueError()
                self.error = ''
                return result, dict(status='used', provider='provod', images=len(visuals), usage=usage, model=MODEL,
                                    can_zoom=any(max(im.get('width',0),im.get('height',0))>=1536 for im in visuals))
        except GeminiError as e:
            self.error = str(e)
            raise
        except (httpx.HTTPError, TimeoutError, OSError, ValueError, KeyError, TypeError, IndexError):
            if key:
                self.usage.unknown(key)
            self.error = 'Provod не ответил корректно. Использован локальный анализ; неопределённый расход оставлен в резерве.'
            raise GeminiError(self.error) from None
        except asyncio.CancelledError:
            if key:
                self.usage.unknown(key)
            raise
