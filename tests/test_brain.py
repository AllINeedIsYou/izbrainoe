import asyncio
import io
import json
import tempfile
import zipfile
from pathlib import Path
from fastapi.testclient import TestClient
from izbrainoe.app import create_app
from izbrainoe.config import Config
from izbrainoe.store import Store
from izbrainoe.processing import Processor,delete_intent
from izbrainoe.knowledge import PUBLIC_QUERIES

def client(tmp_path):
    app=create_app(Config(data_dir=tmp_path,allowed_ids=(101,)))
    c=TestClient(app);c.__enter__()
    c.get('/local',params={'key':app.state.store.meta('local_key')})
    c.headers['X-Iz-Request']='1'
    return c,app

def test_exact_delete_in_explanation(tmp_path):
    c,app=client(tmp_path)
    try:
        i=c.post('/api/items',json={'text':'Моя запись'}).json()
        r=c.patch('/api/items/'+i['id'],json={'context':' УДАЛИТЬ. '})
        assert r.status_code==200 and r.json()['deleted']
        assert c.get('/api/items').json()['total']==0
        assert c.get('/api/items',params={'trash':True}).json()['total']==1
    finally:c.__exit__(None,None,None)

def test_delete_not_a_keyword_anywhere():
    for text in ['не удалять','удалить позже','можно удалить эту строку','удалить всё']:
        assert not delete_intent(text)
    assert delete_intent('удалить')

def test_unchanged_groups_dont_freeze_classification(tmp_path):
    c,app=client(tmp_path)
    try:
        i=c.post('/api/items',json={'text':'Запись без понятного назначения'}).json()
        r=c.patch('/api/items/'+i['id'],json={'context':'Это сочинение по русскому языку','groups':i['groups']}).json()
        assert r['groups']==['Русский язык']
        assert r['manual_groups']==0
    finally:c.__exit__(None,None,None)

def test_feedback_revisits_only_related_own_items(tmp_path):
    async def run():
        s=Store(tmp_path);p=Processor(Config(data_dir=tmp_path),s)
        a=s.create(101,'note','Сочинение 1','Сочинение о дружбе. В тексте показаны отношения между людьми и нравственный выбор человека.')
        b=s.create(101,'note','Сочинение 2','Сочинение о памяти. Автор рассказывает о герое и отношении человека к собственной истории.')
        math=s.create(101,'note','Математика','Задачи по математике: решить уравнения, найти производную и построить графики функций.')
        other=s.create(202,'note','Сочинение чужого пользователя','Сочинение другого пользователя о дружбе, ответственности и нравственном выборе.')
        fixed=s.create(101,'note','Сочинение вручную','Сочинение, которое пользователь намеренно положил в отдельную подборку для своего проекта.')
        for i in (a,b,math,other,fixed):await p.index(101 if i!=other else 202,i['id'])
        s.update(101,fixed['id'],groups_json=json.dumps(['Мой проект']),manual_groups=1)
        before_other=s.get(202,other['id'])['groups']
        s.update(101,a['id'],context='Это сочинение по русскому языку')
        await p.feedback(101,a['id']);await p.review_tasks[101]
        assert s.get(101,b['id'])['groups']==['Русский язык']
        assert s.get(101,fixed['id'])['groups']==['Мой проект']
        assert s.get(202,other['id'])['groups']==before_other
        assert 'Сочинения' not in ' '.join(s.get(101,math['id'])['groups'])
        assert s.learning_state(101)['changed']==1
        await p.close()
    asyncio.run(run())

def test_memory_excludes_deleted_anchors(tmp_path):
    s=Store(tmp_path);i=s.create(101,'note','Текст','Сочинение')
    s.remember(101,i['id'],'Русский язык',['Русский язык / Сочинения'],{})
    assert len(s.memories(101))==1
    s.update(101,i['id'],deleted=1)
    assert not s.memories(101)

def test_docx_text_is_read(tmp_path):
    path=tmp_path/'test.docx'
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Сочинение по русскому языку</w:t></w:r></w:p></w:body></w:document>')
    p=Processor(Config(data_dir=tmp_path),Store(tmp_path))
    body,status=asyncio.run(p.extract(path,'application/octet-stream','test.docx'))
    assert 'Сочинение по русскому языку' in body

def test_attach_respects_ownership(tmp_path):
    c,app=client(tmp_path)
    try:
        i=app.state.store.create(202,'document','Другой файл.pdf')
        assert c.post('/api/items/'+i['id']+'/attach',files={'file':('test.txt',b'Test','text/plain')}).status_code==404
        own=c.post('/api/items',json={'text':'Моя запись'}).json()
        r=c.post('/api/items/'+own['id']+'/attach',files={'file':('test.txt','Сочинение по русскому языку'.encode(),'text/plain')})
        assert r.status_code==200 and r.json()['has_local_file']
    finally:c.__exit__(None,None,None)

def test_web_query_catalog_has_no_personal_input():
    assert PUBLIC_QUERIES['сочинение']=='сочинение рассуждение русский язык структура признаки'
    assert 'private custom topic' not in PUBLIC_QUERIES

def test_personal_manual_folder_preference(tmp_path):
    async def run():
        s=Store(tmp_path);p=Processor(Config(data_dir=tmp_path),s)
        a=s.create(101,'note','Образец','Сочинение по русскому языку о памяти и связи поколений.')
        b=s.create(101,'note','Другой текст','Сочинение по русскому языку о дружбе и ответственности человека.')
        s.update(101,a['id'],context='Сочинения по русскому для экзамена',groups_json=json.dumps(['Мои сочинения']),manual_groups=1)
        await p.feedback(101,a['id']);await p.review_tasks[101]
        assert s.get(101,b['id'])['groups']==['Мои сочинения']
        await p.close()
    asyncio.run(run())

def test_bulk_reanalysis_keeps_manual_and_deleted(tmp_path):
    async def run():
        s=Store(tmp_path);p=Processor(Config(data_dir=tmp_path),s)
        a=s.create(101,'note','Образец','Сочинение о памяти и истории моей страны.')
        b=s.create(101,'note','Второй','Сочинение о дружбе и нравственном выборе человека.')
        gone=s.create(101,'note','Удалено','Сочинение по русскому языку о доброте.')
        s.update(101,a['id'],context='Русский язык',groups_json=json.dumps(['Русский язык / Сочинения']),manual_groups=1)
        s.update(101,gone['id'],deleted=1)
        assert p.rebuild(101) and not p.rebuild(101)
        await p.rebuild_tasks[101]
        assert s.get(101,b['id'])['groups']==['Русский язык']
        assert s.get(101,gone['id']) is None
        assert s.learning_state(101)['checked']==2
        await p.close()
    asyncio.run(run())

def test_first_four_pdf_pages_only(tmp_path):
    import pytest
    from izbrainoe.config import ROOT
    if not (ROOT/'runtime/ocr').exists():pytest.skip('Requires Apple Vision helper: scripts/setup_local.py')
    async def run():
        p=Processor(Config(data_dir=tmp_path),Store(tmp_path))
        for name in ('digital.pdf','scanned.pdf'):
            body,status=await p.extract(Path(__file__).parent/'fixtures'/name,'application/pdf',name)
            assert '[Страница 4]' in body and '[Страница 5]' not in body
            assert 'DO_NOT_READ' not in body and 'Сочинение' in body
            assert '4 из 6' in status
            if name=='scanned.pdf':assert 'OCR: 4' in status
    asyncio.run(run())

def test_blank_pdf_is_not_mistaken_for_extracted_content(tmp_path,monkeypatch):
    from pypdf import PdfWriter
    from izbrainoe.config import ROOT
    path=tmp_path/'blank.pdf';writer=PdfWriter()
    for _ in range(6):writer.add_blank_page(width=595,height=842)
    with path.open('wb') as out:writer.write(out)
    original=Path.exists
    monkeypatch.setattr(Path,'exists',lambda self:False if self==ROOT/'runtime/ocr' else original(self))
    p=Processor(Config(data_dir=tmp_path),Store(tmp_path))
    text,status=asyncio.run(p.extract(path,'application/pdf','blank.pdf'))
    assert text=='' and 'не прочитан' in status

def test_geometry_from_content_not_filename():
    from izbrainoe.knowledge import grounded_features,supported_type
    text='Сумма углов треугольника 180 градусов. Биссектриса делит угол пополам. Медиана делит треугольник на равные площади.'
    assert grounded_features(text)[1]=='Геометрия'
    assert grounded_features('Сочинение. История моей семьи. Память объединяет поколения.')[1]==''
    assert not supported_type('договор','Финансовый университет при Правительстве Российской Федерации.')
    assert not supported_type('договор','Информационная безопасность. Ссылка на образовательную программу.')
    assert supported_type('договор','Договор оказания услуг. Стороны заключили настоящее соглашение.')

def test_model_quote_does_not_justify_invented_contract(tmp_path,monkeypatch):
    import httpx
    async def fake_chat(self,url,**kwargs):
        result={'document_type':'договор','subject':'Физика','groups':['Физика / Договоры'],'summary':'Университет','evidence':['Финансовый университет'],'confidence':.95}
        return httpx.Response(200,json={'message':{'content':json.dumps(result)}},request=httpx.Request('POST',url))
    monkeypatch.setattr(httpx.AsyncClient,'post',fake_chat)
    async def run():
        s=Store(tmp_path);p=Processor(Config(data_dir=tmp_path,ollama_url='http://127.0.0.1:11435',chat_model='test'),s)
        i=s.create(101,'photo','Фото','Финансовый университет при Правительстве Российской Федерации.')
        await p.index(101,i['id']);r=s.get(101,i['id'])
        assert r['groups']==['Вуз']
        assert r['analysis']['document_type']=='' and r['analysis']['subject']==''
    asyncio.run(run())

def test_essay_about_lectures_is_still_essay():
    from izbrainoe.knowledge import grounded_features,canonical_groups
    text='Почему важно говорить выразительно? Авторская позиция ясна: культура речи необходима. Два примера-аргумента показывают, как скучными лекциями можно убить интерес к науке. Таким образом, слово влияет на людей.'
    assert grounded_features(text)[0]=='сочинение'
    assert canonical_groups('лекция','',[],[])==['Учёба']
    assert grounded_features('| Темп |: 120. | Куплет |: Am Dm G C. Текст песни.')[0]=='песня'
    assert grounded_features('Example_v1.apk')[0]=='приложение'

def test_timetable_from_ocr_structure_without_complete_title():
    from izbrainoe.knowledge import grounded_features,canonical_groups,looks_like_schedule
    subjects='Для обучающихся очно-заочной формы обуч\n10-А\nкаб\n1\n2\n3\n4\nИстория\nОбществознание\nАнглийский язык / Геометрия\nАлгебра\nРусский язык\nNº 14\nNº 14\nNº 25'
    bells='списание звонков и перемен очно-заочная форма\nУрок\n1 урок\n2 урок\nНачало урока\n14.10\n15.00\n15.50\nОкончание урока\n14.50\n15.40\n16.30\nПеремена\n10 минут'
    for text in (subjects,bells):
        kind,subject=grounded_features(text)
        assert (kind,subject)==('расписание','')
        assert canonical_groups(kind,subject,[],[])==['Учёба']
    assert not looks_like_schedule('Очно-заочное обучение. Вступительные предметы: русский язык, математика, обществознание.')
