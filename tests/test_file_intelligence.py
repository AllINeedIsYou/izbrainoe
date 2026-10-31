import asyncio
import base64
import json
import zipfile
from pathlib import Path
import pytest
from PIL import Image
import openpyxl
from izbrainoe.config import Config
from izbrainoe.store import Store
from izbrainoe.processing import Processor,sensitive
from izbrainoe.file_intelligence import inspect_file,format_hint,format_directive
from izbrainoe.gemini import visual_pages,GeminiError
from izbrainoe.provod import Provod


@pytest.mark.parametrize('name,contents,folder',[
 ('config.json','{"app":"snake","fps":60}','Программирование'),
 ('SETTINGS.YAML','server:\n  port: 9000','Программирование'),
 ('main.PY','print("hello")','Программирование'),
 ('Dockerfile','FROM python:3.14','Программирование'),
 ('config.json','{"продажи":[{"выручка":1200}]}','Финансы'),
 ('budget.csv','доходы,расходы\n1000,200','Финансы'),
 ('data.weird','Интеграл и линейная алгебра. Матрицы и определители.','Высшая математика'),
 ('notes.md','Резюме инженера. Опыт работы.','Работа'),
 ('Автобиография.docx','Автобиография. Место рождения и образование.','Личные документы'),
 ('Согласие.docx','Согласие на сдачу экзамена в ГИБДД.','Авто'),
 ('draft.docx','Договор аренды квартиры.','Дом'),
 ('blood.txt','Результат обследования. Анализ крови.','Здоровье'),
 ('unknown.pdf','','Документы'),
 ('plan.dwg','','Чертежи и модели'),
 ('installer.APK','','Программы'),
 ('bundle.7z','','Архивы'),
 ('song.MP3','','Музыка'),
 ('movie.MP4','','Видео'),
 ('data.ZIP','','ZIP'),
])
def test_fallback_is_meaningful_without_cloud(tmp_path,name,contents,folder):
 async def run():
  cfg=Config(data_dir=tmp_path);s=Store(tmp_path);p=Processor(cfg,s)
  item=s.create(101,'document',name,contents,filename=name)
  try:
   await p.index(101,item['id'])
   result=s.get(101,item['id'])
   assert result['groups']==[folder]
   assert len(result['groups'])<=1
  finally:await p.close()
 asyncio.run(run())


def test_json_secrets_redacted_and_large_input_bounded(tmp_path):
 path=tmp_path/'config.json'
 path.write_text(json.dumps({'host':'example.invalid','password':'TEST_SECRET_VALUE','api_key':'TEST_KEY_VALUE','nested':{'refresh_token':'TEST_TOKEN_VALUE'},'fps':60}))
 text,status=inspect_file(path,'application/json','config.json')
 assert 'example.invalid' in text and 'fps' in text
 assert not any(secret in text for secret in ('TEST_SECRET_VALUE','TEST_KEY_VALUE','TEST_TOKEN_VALUE'))
 assert not sensitive(text)
 path=tmp_path/'readme.log';path.write_text('строка\n'*200000)
 text,status=inspect_file(path,'text/plain',path.name)
 assert len(text)<=40000 and 'ограниченное' in status


def test_zip_preview_never_extracts_paths_or_nested_archive(tmp_path):
 path=tmp_path/'bundle.zip'
 with zipfile.ZipFile(path,'w') as z:
  z.writestr('../../escape.py','print("safe preview")')
  z.writestr('nested.zip',b'not executed')
  z.writestr('config.json','{"password":"TEST_SECRET_VALUE", "port":8000}')
 text,status=inspect_file(path,'application/zip',path.name)
 assert 'escape.py' in text and 'safe preview' in text and 'port' in text
 assert 'TEST_SECRET_VALUE' not in text
 assert list(tmp_path.iterdir())==[path]


def test_docx_tables_and_sheets_are_read_in_order(tmp_path):
 path=tmp_path/'test.docx'
 with zipfile.ZipFile(path,'w') as z:
  z.writestr('word/document.xml','<w:document xmlns:w="urn:word"><w:p><w:r><w:t>Заголовок</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>Ячейка таблицы</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:document>')
 text,_=inspect_file(path,'application/octet-stream',path.name)
 assert text=='Заголовок\n\nЯчейка таблицы'
 path=tmp_path/'sales.xlsx';book=openpyxl.Workbook();sheet=book.active;sheet.title='Продажи'
 sheet.append(['Товар','Выручка']);sheet.append(['Яблоки',100]);book.save(path)
 text,_=inspect_file(path,'application/octet-stream',path.name)
 assert all(part in text for part in ('Продажи','Товар','Выручка','Яблоки','100'))


def test_bad_containers_and_binary_do_not_claim_content_was_read(tmp_path):
 async def run():
  cfg=Config(data_dir=tmp_path);s=Store(tmp_path);p=Processor(cfg,s)
  try:
   for name,data in [('bad.zip',b'not a zip'),('bad.docx',b'not a docx'),('bad.xlsx',b'not a workbook'),('binary.bin',b'\x00\xff\x00\xfe')]:
    path=tmp_path/name;path.write_bytes(data)
    text,status=await p.extract(path,'application/octet-stream',name)
    assert not text and 'Не удалось' in status
   # Wrong filename and claimed MIME cannot prevent readable content inspection.
   path=tmp_path/'wrong.jpg';path.write_text('{"server":"example.invalid","port":8000}')
   text,_=await p.extract(path,'image/jpeg',path.name)
   i=s.create(101,'photo',path.name,text,filename=path.name,path=str(path),mime='image/jpeg')
   await p.index(101,i['id'])
   assert s.get(101,i['id'])['kind']=='document'
   assert s.get(101,i['id'])['groups']==['Программирование']
  finally:await p.close()
 asyncio.run(run())


def test_portable_vision_does_not_require_native_ocr(tmp_path,monkeypatch):
 async def run():
  path=tmp_path/'image.png';Image.new('RGB',(2400,1200),'white').save(path)
  images=await visual_pages({'path':str(path),'kind':'photo','mime':'image/png','filename':path.name})
  assert len(images)==1 and max(images[0]['width'],images[0]['height'])<=1536
  assert base64.b64decode(images[0]['data']).startswith(b'\xff\xd8')
  path=Path(__file__).parent/'fixtures/scanned.pdf'
  images=await visual_pages({'path':str(path),'kind':'document','mime':'application/pdf','filename':'renamed.bin'})
  assert len(images)==4
  bad=tmp_path/'bad.png';bad.write_bytes(b'not image')
  with pytest.raises(GeminiError):await visual_pages({'path':str(bad),'kind':'photo','mime':'image/png'})
 asyncio.run(run())


def test_personal_format_rules_apply_retroactively_and_are_private(tmp_path):
 async def run():
  cfg=Config(data_dir=tmp_path);s=Store(tmp_path);p=Processor(cfg,s)
  a=s.create(101,'audio','Song A',filename='a.mp3')
  b=s.create(101,'document','Song B',filename='b.mp3')
  other=s.create(202,'audio','Song C',filename='c.mp3')
  rule=s.create(101,'note','Мои треки')
  s.update(101,rule['id'],context='mp3 файлы')
  try:
   await p.feedback(101,rule['id'])
   await asyncio.gather(*p.review_tasks.values())
   assert s.get(101,a['id'])['groups']==['Мои треки']
   assert s.get(101,b['id'])['groups']==['Мои треки']
   await p.index(202,other['id'])
   assert s.get(202,other['id'])['groups']==['Музыка']
   future=s.create(101,'document','New song',filename='d.mp3')
   await p.index(101,future['id'])
   assert s.get(101,future['id'])['groups']==['Мои треки']
   assert not format_directive('source.py','В файле перечислены импорты')
  finally:await p.close()
 asyncio.run(run())


def test_explicit_context_overrides_media_format(tmp_path,monkeypatch):
 async def cloud(self,item,payload,*args):
  assert payload['user_explanation']=='Лекция по физике, хранить в Физика'
  return dict(document_type='лекция',subject='Физика',groups=['Физика'],summary='Лекция по физике.',evidence=['Лекция по физике'],confidence=.98),{'status':'used','images':0}
 monkeypatch.setattr(Provod,'analyse',cloud)
 async def run():
  cfg=Config(data_dir=tmp_path,ai_provider='provod',provod_key='TEST_ONLY');s=Store(tmp_path);p=Processor(cfg,s)
  i=s.create(101,'audio','audio.mp3','Аудиозапись',filename='audio.mp3');s.update(101,i['id'],context='Лекция по физике, хранить в Физика')
  try:
   await p.index(101,i['id'])
   assert s.get(101,i['id'])['groups']==['Физика']
  finally:await p.close()
 asyncio.run(run())


def test_failed_cloud_is_queued_and_recovers_without_losing_original(tmp_path,monkeypatch):
 calls=[]
 async def cloud(self,*args):
  calls.append(1)
  if len(calls)==1:raise GeminiError('Network unavailable','connection')
  return dict(document_type='документ',groups=['Работа'],summary='Рабочие материалы.',evidence=['рабочий материал для собеседования'],confidence=.97),{'status':'used','images':0}
 monkeypatch.setattr(Provod,'analyse',cloud)
 async def run():
  cfg=Config(data_dir=tmp_path,ai_provider='provod',provod_key='TEST_ONLY');s=Store(tmp_path);p=Processor(cfg,s)
  i=s.create(101,'document','interview.txt','Это рабочий материал для собеседования.',filename='interview.txt')
  try:
   await p.index(101,i['id'])
   assert s.get(101,i['id'])['analysis_state']=='retry_pending'
   with s.db() as db:assert db.execute('SELECT count(*) FROM analysis_retries').fetchone()[0]==1
   again=Store(tmp_path);assert again.get(101,i['id'])['body']==i['body']
   await p.index(101,i['id'],force=True)
   assert s.get(101,i['id'])['groups']==['Работа']
   with s.db() as db:assert db.execute('SELECT count(*) FROM analysis_retries').fetchone()[0]==0
  finally:await p.close()
 asyncio.run(run())


def test_ten_images_are_bounded_to_three_cloud_calls_in_flight(tmp_path,monkeypatch):
 active=0;peak=0;calls=0
 async def cloud(self,item,*args):
  nonlocal active,peak,calls
  active+=1;peak=max(peak,active);calls+=1
  try:
   await asyncio.sleep(.01)
   return dict(document_type='учебный материал',subject='Линейная алгебра',groups=['Высшая математика'],summary='Формулы матриц.',evidence=['Определители и матрицы'],confidence=.98,recognized_text='Матрицы и определители'),{'status':'used','images':1}
  finally:active-=1
 monkeypatch.setattr(Provod,'analyse',cloud)
 async def run():
  cfg=Config(data_dir=tmp_path,ai_provider='provod',provod_key='TEST_ONLY');s=Store(tmp_path);p=Processor(cfg,s)
  items=[s.create(101,'photo','image-'+str(n),path='/unavailable/image-'+str(n)+'.png',mime='image/png',digest=str(n)) for n in range(10)]
  try:
   await asyncio.gather(*(p.index(101,i['id']) for i in items))
   assert calls==10 and peak==3
   assert all(s.get(101,i['id'])['groups']==['Высшая математика'] for i in items)
   assert all('Матрицы' in s.get(101,i['id'])['body'] for i in items)
  finally:await p.close()
 asyncio.run(run())


def test_automatic_retries_stop_instead_of_spending_forever(tmp_path,monkeypatch):
 async def fail(*args):raise GeminiError('network unavailable','connection')
 monkeypatch.setattr(Provod,'analyse',fail)
 async def run():
  cfg=Config(data_dir=tmp_path,ai_provider='provod',provod_key='TEST_ONLY');s=Store(tmp_path);p=Processor(cfg,s)
  i=s.create(101,'document','readme.txt','Working notes for a software application and a new feature.',filename='readme.txt')
  try:
   for _ in range(3):await p.index(101,i['id'])
   with s.db() as db:assert db.execute('SELECT count(*) FROM analysis_retries').fetchone()[0]==0
   assert s.get(101,i['id'])['analysis_state']!='retry_pending'
   assert s.get(101,i['id'])['body']==i['body']
  finally:await p.close()
 asyncio.run(run())


def test_specific_grounded_topic_beats_generic_folders_but_not_manual_context():
 from izbrainoe.file_intelligence import stabilize_folder
 item={'body':'Согласие законных представителей на экзамен в ГИБДД.','context':'','manual_groups':False}
 a={'groups':['Личные документы'],'document_type':'согласие','confidence':.95}
 assert stabilize_folder(item,a)['groups']==['Авто']
 a={'groups':['Учёба'],'document_type':'учебный материал','subject':'Линейная алгебра','confidence':.95,'cloud':{'images':1},'summary':'Формулы определителя матрицы.'}
 assert stabilize_folder({'body':'','context':''},a)['groups']==['Высшая математика']
 a['subject']='Численные методы';a['summary']='Учебные формулы.'
 assert stabilize_folder({'body':'','context':''},a)['groups']==['Высшая математика']
 assert stabilize_folder({**item,'context':'Все мои документы хранить вместе'},a)['groups']==['Учёба']


def test_byte_signatures_win_over_misleading_names_and_mime(tmp_path):
 from izbrainoe.file_intelligence import file_kind,sniff_mime
 path=tmp_path/'not-really-json.json';Image.new('RGB',(40,40),'white').save(path,format='PNG')
 assert file_kind(path.name,sniff_mime(path))=='photo'
 path=tmp_path/'not-really-photo.jpg';path.write_bytes(b'%PDF-1.7\n')
 assert file_kind(path.name,sniff_mime(path),'photo')=='document'
