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
            c.execute(f'DROP TRIGGER {table}_topic_folder_update')
            c.execute(f'UPDATE {table} SET groups_json=?,analysis_json=?',(json.dumps(old),json.dumps({'topics':['интегралы']})))
        c.execute('UPDATE items SET manual_groups=1')
    store=Store(tmp_path)
    saved=store.get(101,item['id'])
    assert saved['groups']==['Высшая математика']
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
    assert result.groups==['Программирование']
    assert set(result.topics)=={'Python','Программирование / Проекты','IT / Проекты','Работа'}
    assert Analysis.model_json_schema()['properties']['groups']['maxItems']==1


def test_aliases_across_records_share_one_folder(tmp_path):
    store=Store(tmp_path)
    a=store.create(101,'note','Первый','Заметка')
    b=store.create(101,'note','Второй','Заметка')
    store.update(101,a['id'],groups_json=json.dumps(['IT/Проекты','Работа']))
    store.update(101,b['id'],groups_json=json.dumps(['Программирование / Проекты']))
    assert store.folder_names(101)==['Программирование']
    assert len(store.list(101,'Программирование'))==2


def test_same_topic_different_types_share_folder_and_filter_inside_it(tmp_path):
    c,app=client(tmp_path)
    try:
        app.state.store.meta('owner','101')
        store=app.state.store
        book=store.create(101,'document','Учебник.pdf','Задачи и решения')
        link=store.create(101,'link','Облако вышмат','Ссылка на материалы')
        notes=store.create(101,'photo','Лекция.jpg','Конспект')
        for item,group,kind in ((book,'Высшая математика / Учебники','задачник'),
                                (link,'Высшая математика / Материалы','ссылка'),
                                (notes,'Вышмат / Конспекты','конспект')):
            store.update(101,item['id'],groups_json=json.dumps([group]),analysis_json=json.dumps({'document_type':kind}))
        me=c.get('/api/me').json()
        assert me['groups']=={'Высшая математика':3}
        assert me['group_types']['Высшая математика']=={'Учебники и задачники':1,'Ссылка':1,'Конспекты и лекции':1}
        found=c.get('/api/items',params={'group':'Высшая математика','document_type':'Учебники и задачники'}).json()
        assert found['total']==1 and found['items'][0]['id']==book['id']
        assert c.get('/api/items',params={'group':'Высшая математика'}).json()['total']==3
        assert all('/' not in item['groups'][0] for item in store.list(101))
        with pytest.raises(sqlite3.IntegrityError):
            with store.db() as db:db.execute('UPDATE items SET groups_json=? WHERE id=?',(json.dumps(['Высшая математика / Учебники']),book['id']))
    finally:c.__exit__(None,None,None)


def test_migrate_previous_single_folder_archive_to_topic_folders(tmp_path):
    store=Store(tmp_path)
    book=store.create(101,'document','Учебник.pdf','Важный текст',path='/unchanged/file.pdf')
    link=store.create(101,'link','Материалы','Важная ссылка')
    with store.db() as db:
        db.execute("DELETE FROM meta WHERE key='topic_folders_v1'")
        db.execute('DROP TRIGGER items_topic_folder_update')
        db.execute('UPDATE items SET groups_json=? WHERE id=?',(json.dumps(['Высшая математика / Учебники']),book['id']))
        db.execute('UPDATE items SET groups_json=? WHERE id=?',(json.dumps(['Высшая математика / Материалы']),link['id']))
    store=Store(tmp_path)
    assert store.folder_names(101)==['Высшая математика']
    assert len(store.list(101,'Высшая математика'))==2
    assert store.get(101,book['id'],raw=True)['path']=='/unchanged/file.pdf'
    assert 'Высшая математика / Учебники' in store.get(101,book['id'])['analysis']['topics']


def test_higher_math_variants_do_not_create_new_topic():
    from izbrainoe.folders import primary_folder
    from izbrainoe.knowledge import grounded_features,canonical_groups
    for label in ('Вышмат','Высшая математика / Учебники','Материалы по высшей математике','вышмат / конспекты'):
        assert primary_folder([label])==['Высшая математика']
    kind,subject=grounded_features('Сборник задач по высшей математике','Мои материалы для вышмата')
    assert subject=='Высшая математика'
    assert canonical_groups(kind,subject,[],[])==['Высшая математика']
