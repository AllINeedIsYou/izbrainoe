import asyncio
import json
import pytest
from izbrainoe.config import Config
from izbrainoe.gemini import GeminiError
from izbrainoe.processing import Processor
from izbrainoe.store import Store


def setup(tmp_path):
    config=Config(data_dir=tmp_path,ai_provider='provod',provod_key='TEST_ONLY_KEY')
    store=Store(tmp_path)
    return store,Processor(config,store)


def test_gemini_semantic_search_pagination_cache_and_revision(tmp_path):
    async def run():
        s,p=setup(tmp_path);calls=[]
        a=s.create(101,'photo','Календарный план','Дни отдыха и периоды обучения')
        b=s.create(101,'note','Покупки','Овощи и молоко')
        async def analyse(item,payload,system,schema):
            calls.append(payload)
            assert payload['_task']=='search' and 'path' not in item
            index=next(v['index'] for v in payload['catalog'] if v['title']=='Календарный план')
            return {'matches':[{'index':index,'score':.96}]},{}
        async def forbidden(*args):raise AssertionError('Cloud search must not embed queries locally')
        p.analyzer.provod.analyse=analyse;p.ai_ready=True;p.embed=forbidden
        try:
            found=await p.search(101,'когда у меня перерыв между семестрами',limit=1)
            assert found['mode']=='gemini' and found['total']==1 and found['items'][0]['id']==a['id']
            assert found['items'][0]['match']=='По смыслу · Gemini'
            second=await p.search(101,'когда у меня перерыв между семестрами',offset=1)
            assert second['cached'] and second['total']==1 and not second['items'] and len(calls)==1
            s.update(101,b['id'],context='Новая подробность')
            await p.search(101,'когда у меня перерыв между семестрами')
            assert len(calls)==2
        finally:await p.close()
    asyncio.run(run())


def test_search_never_sends_other_users_trash_secrets_or_wrong_numbers(tmp_path):
    async def run():
        s,p=setup(tmp_path);calls=[]
        own=s.create(101,'note','Задание 18','Математическая задача номер 18')
        s.create(101,'note','Задание 19','Математическая задача номер 19')
        s.create(202,'note','Чужое задание 18','Математика 18')
        gone=s.create(101,'note','Корзина 18','Математика 18');s.update(101,gone['id'],deleted=1)
        s.create(101,'note','Секрет 18','password: FAKE_TEST_ONLY 18')
        async def analyse(item,payload,*args):
            calls.append(payload)
            assert [row['title'] for row in payload['catalog']]==['Задание 18']
            return {'matches':[{'index':0,'score':.9}]},{}
        p.analyzer.provod.analyse=analyse
        try:
            found=await p.search(101,'задача 18')
            assert found['mode']=='gemini' and found['items'][0]['id']==own['id']
            await p.search(101,'password: FAKE_TEST_ONLY')
            assert len(calls)==1
        finally:await p.close()
    asyncio.run(run())


def test_cloud_failure_uses_keyword_search_without_local_embedding(tmp_path):
    async def run():
        s,p=setup(tmp_path);s.create(101,'note','Учебник','Высшая математика')
        async def fail(*args):raise GeminiError('Лимит расходов','budget_limit')
        async def forbidden(*args):raise AssertionError('Unexpected local model query')
        p.analyzer.provod.analyse=fail;p.ai_ready=True;p.embed=forbidden
        try:
            found=await p.search(101,'математика')
            assert found['total']==1 and found['mode']=='keyword' and found['warning']
        finally:await p.close()
    asyncio.run(run())


def test_large_archive_is_fully_scanned_in_bounded_batches(tmp_path):
    async def run():
        s,p=setup(tmp_path);calls=[]
        for n in range(125):s.create(101,'note',f'Материал {n}','Краткое содержание')
        async def analyse(item,payload,*args):
            calls.append(payload);assert len(payload['catalog'])<=60
            return {'matches':[{'index':i['index'],'score':.9} for i in payload['catalog'] if i['title']=='Материал 0']},{}
        p.analyzer.provod.analyse=analyse
        try:
            found=await p.search(101,'нужное мне содержание')
            assert found['mode']=='gemini' and found['items'][0]['title']=='Материал 0'
            assert len(calls)==3 and sum(len(c['catalog']) for c in calls)==125
        finally:await p.close()
    asyncio.run(run())
