import asyncio
import json
import sqlite3
import pytest

from izbrainoe.analysis_policy import Analysis
from izbrainoe.processing import Processor
from izbrainoe.config import Config
from izbrainoe.store import Store
from tests.test_brain import client


def test_migration_removes_multiple_folders_and_preserves_search_terms(tmp_path):
    store=Store(tmp_path)
    item=store.create(101,'document','Справочник','Учебный материал')
    store.remember(101,item['id'],'Пояснение',['Моя папка'],{})
    old=['Вышмат / Учебники','Учёба / Задачники','Высшая математика / Материалы']
    with store.db() as c:
        c.execute("DELETE FROM meta WHERE key='single_folder_v1'")
        for table in ('items','memories'):
            c.execute(f'DROP TRIGGER {table}_single_folder_update')
            c.execute(f'UPDATE {table} SET groups_json=?,analysis_json=?',(json.dumps(old),json.dumps({'topics':['интегралы']})))
        c.execute('UPDATE items SET manual_groups=1')
    store=Store(tmp_path)
    saved=store.get(101,item['id'])
    assert saved['groups']==['Высшая математика / Учебники']
    assert saved['analysis']['groups']==saved['groups']
    assert set(old)|{'интегралы'} <= set(saved['analysis']['topics'])
    assert saved['manual_groups']==1
    memory=store.memories(101)[0]
    assert json.loads(memory['groups_json'])==saved['groups']
    p=Processor(Config(data_dir=tmp_path),store)
    async def run():
        try:
            found=await p.search(101,'Задачники')
            assert found['total']==1 and found['items'][0]['id']==item['id']
        finally:await p.close()
    asyncio.run(run())
    assert Store(tmp_path).get(101,item['id'])==saved  # Migration is idempotent.


def test_database_and_api_enforce_one_folder(tmp_path):
    c,app=client(tmp_path)
    try:
        item=c.post('/api/items',json={'text':'Материал для моей работы'}).json()
        response=c.patch('/api/items/'+item['id'],json={'groups':['Первая','Вторая']})
        assert response.status_code==422
        with pytest.raises(sqlite3.IntegrityError):
            with app.state.store.db() as db:
                db.execute('UPDATE items SET groups_json=? WHERE id=?',(json.dumps(['Первая','Вторая']),item['id']))
        response=c.patch('/api/items/'+item['id'],json={'groups':['Мой проект']})
        assert response.status_code==200 and response.json()['groups']==['Мой проект']
    finally:c.__exit__(None,None,None)


def test_model_multiple_groups_become_search_topics():
    result=Analysis(groups=['Программирование / Проекты','IT / Проекты','Работа'],topics=['Python'])
    assert result.groups==['Программирование / Проекты']
    assert set(result.topics)=={'Python','IT / Проекты','Работа'}
    assert Analysis.model_json_schema()['properties']['groups']['maxItems']==1


def test_aliases_across_records_share_one_folder(tmp_path):
    store=Store(tmp_path)
    a=store.create(101,'note','Первый','Заметка')
    b=store.create(101,'note','Второй','Заметка')
    store.update(101,a['id'],groups_json=json.dumps(['IT/Проекты','Работа']))
    store.update(101,b['id'],groups_json=json.dumps(['Программирование / Проекты']))
    assert store.folder_names(101)==['Программирование / Проекты']
    assert len(store.list(101,'Программирование / Проекты'))==2
