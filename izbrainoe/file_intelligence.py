"""Portable bounded file inspection and deterministic format defaults.

No archive members are extracted or executed. Limits bound preview work, not originals.
"""
import io
import codecs
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

TEXT_EXT=set('txt md markdown rst log csv tsv json jsonl ndjson yaml yml toml ini conf cfg env xml html htm css scss less js jsx ts tsx py pyi java c h cpp hpp cs go rs rb php sh bash zsh fish sql r lua swift kt kts dart vue svelte ipynb dockerfile makefile tex bib srt vtt ass properties gitignore editorconfig graphql proto diff patch bat ps1'.split())
CODE_EXT=set('py pyi js jsx ts tsx css scss less java c h cpp hpp cs go rs rb php sh bash zsh fish sql lua swift kt kts dart vue svelte graphql proto diff patch bat ps1'.split())
IMAGE_EXT=set('jpg jpeg png webp gif heic heif bmp tiff tif avif'.split())
AUDIO_EXT=set('mp3 flac m4a aac wav ogg opus wma aiff alac'.split())
VIDEO_EXT=set('mp4 mov mkv webm avi m4v mpg mpeg wmv flv 3gp'.split())
ARCHIVE_EXT=set('zip rar 7z tar gz bz2 xz tgz'.split())
SECRET_FIELD=re.compile(r'password|passwd|secret|(?:api|private)[_-]?key|access[_-]?token|refresh[_-]?token|bot[_-]?token|парол|privat',re.I)
MAX_READ=512*1024


def extension(filename):return Path(filename).suffix.lower().lstrip('.')


def file_kind(filename,mime='',original='document'):
    ext=extension(filename)
    if original=='voice':return 'voice'
    if mime.startswith('image/'):return 'photo'
    if mime.startswith('audio/'):return 'audio'
    if mime.startswith('video/'):return 'video'
    if mime in ('application/pdf','application/json','application/zip'):return 'document'
    if ext in IMAGE_EXT:return 'photo'
    if ext in AUDIO_EXT:return 'audio'
    if ext in VIDEO_EXT:return 'video'
    return original if original in ('note','link','photo','audio','video') else 'document'


def format_hint(item):
    name=item.get('filename') or item.get('title','');ext=extension(name)
    if item.get('mime')=='application/json':ext='json'
    if item.get('mime')=='application/zip' and ext not in ('docx','odt','pptx','odp','epub','xlsx','xlsm','ods'):ext='zip'
    kind=file_kind(name,item.get('mime',''),item.get('kind','document'))
    text=(name+' '+item.get('body','')[:6000]).lower()
    folder=typ=''
    if ext in ARCHIVE_EXT:folder,typ=('ZIP' if ext=='zip' else 'Архивы'),'архив '+ext.upper()
    elif kind=='audio':folder,typ='Музыка','аудиозапись'
    elif kind=='video':folder,typ='Видео','видеозапись'
    elif ext in CODE_EXT or Path(name).name.lower() in ('dockerfile','makefile','.gitignore','.editorconfig'):
        folder,typ='Программирование','исходный код'
    elif ext in ('ini','conf','cfg','toml','env','properties') or ext in ('json','yaml','yml','xml') and re.search(r'config|settings|package\.json|tsconfig|настрой|конфиг|server|proxy|logging|dependencies|endpoint|database',text):
        folder,typ='Программирование','конфигурация программы'
    elif ext in ('json','jsonl','ndjson','yaml','yml','xml','ipynb'):folder,typ='Программирование','структурированные данные'
    elif ext in ('xlsx','xlsm','xls','ods','csv','tsv'):folder,typ='Таблицы','электронная таблица'
    elif ext in ('pptx','ppt','odp'):folder,typ='Презентации','презентация'
    elif ext in ('epub','fb2','mobi'):folder,typ='Книги','электронная книга'
    elif ext in ('dwg','dxf','step','stp','stl','obj','blend'):folder,typ='Чертежи и модели','чертёж или 3D-модель'
    elif ext in ('apk','exe','msi','dmg','pkg','deb','rpm'):folder,typ='Программы','установочный пакет'
    elif ext in ('ttf','otf','woff','woff2'):folder,typ='Шрифты','шрифт'
    elif ext in ('doc','docx','odt','rtf','pdf'):folder,typ='Документы','документ'
    return {'extension':ext,'kind':kind,'folder':folder,'document_type':typ,
            'fixed_default':ext in ARCHIVE_EXT or kind in ('audio','video')}


def decode_text(raw):
    if not raw:return ''
    if raw.startswith((b'\xff\xfe',b'\xfe\xff')):return codecs.getincrementaldecoder('utf-16')().decode(raw,final=False)
    if b'\0' in raw[:4096]:raise ValueError('binary content')
    try:text=codecs.getincrementaldecoder('utf-8-sig')().decode(raw,final=False)
    except UnicodeDecodeError:
        from charset_normalizer import from_bytes
        guess=from_bytes(raw).best()
        if guess is None:raise ValueError('unknown encoding')
        text=str(guess)
    if sum(ord(c)<32 and c not in '\n\r\t' for c in text)>max(2,len(text)*.01):raise ValueError('binary content')
    return text


def redact_structure(value,depth=0):
    if depth>20:return '[вложенная структура]'
    if isinstance(value,dict):
        return {('[скрытое поле]' if SECRET_FIELD.search(str(k)) else k):('[скрыто]' if SECRET_FIELD.search(str(k)) else redact_structure(v,depth+1)) for k,v in list(value.items())[:150]}
    if isinstance(value,list):return [redact_structure(v,depth+1) for v in value[:100]]
    return value


def safe_text(text,ext):
    if ext in ('json','ipynb'):
        try:
            value=json.loads(text)
            if ext=='ipynb' and isinstance(value,dict):
                value={'cells':[{'cell_type':c.get('cell_type'),'source':c.get('source',[])} for c in value.get('cells',[])[:30]]}
            return json.dumps(redact_structure(value),ensure_ascii=False,indent=2)[:40000]
        except (ValueError,RecursionError):pass
    # Redact assignments in configuration/code previews; never leak a credential to AI/search.
    if ext in CODE_EXT|set('json jsonl ndjson yaml yml toml ini conf cfg env xml properties ipynb'.split()):
        lines=[]
        for line in text.splitlines():
            if re.search(r'(?:password|passwd|secret|api[_-]?key|private[_-]?key|access[_-]?token|refresh[_-]?token|bot[_-]?token|пароль)\s*["\']?\s*[:=]',line,re.I):
                lines.append('[скрытое поле]');continue
            lines.append(line)
        text='\n'.join(lines)
    return text[:40000]


def xml_text(data):
    if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():raise ValueError('XML declarations blocked')
    root=ET.fromstring(data)
    paragraphs=[]
    for node in root.iter():
        if node.tag.rsplit('}',1)[-1] in ('p','h'):
            # itertext on the whole subtree duplicates text in nested paragraphs/tables.
            parts=[n.text or '' for n in node.iter() if n.tag.rsplit('}',1)[-1] in ('t','span')]
            value=''.join(parts).strip() or ''.join(node.itertext()).strip()
            if value:paragraphs.append(value)
            if len(paragraphs)>=160 or sum(map(len,paragraphs))>40000:break
    return '\n\n'.join(paragraphs)[:40000]


def bounded_member(archive,name,limit=2*1024*1024):
    info=archive.getinfo(name)
    if info.file_size>limit or info.flag_bits&1:raise ValueError('large/encrypted member')
    with archive.open(info) as member:data=member.read(limit+1)
    if len(data)>limit:raise ValueError('large member')
    return data


def inspect_file(path,mime,filename):
    path=Path(path);ext=extension(filename)
    if ext in ('docx','odt','pptx','odp','epub'):
        with zipfile.ZipFile(path) as z:
            if len(z.infolist())>10000:raise ValueError('too many members')
            if ext=='docx':names=['word/document.xml']
            elif ext in ('odt','odp'):names=['content.xml']
            elif ext=='pptx':names=sorted((n for n in z.namelist() if re.fullmatch(r'ppt/slides/slide\d+\.xml',n)),key=lambda n:int(re.search(r'(\d+)\.xml',n)[1]))[:12]
            else:names=[n for n in z.namelist() if n.endswith(('.xhtml','.html','.htm'))][:8]
            texts=[]
            for n in names:
                data=bounded_member(z,n)
                if ext=='epub':
                    from bs4 import BeautifulSoup
                    text=BeautifulSoup(data,'html.parser').get_text(' ',strip=True)
                else:text=xml_text(data)
                texts.append(text)
            return '\n\n'.join(texts)[:40000],'Прочитано начало документа; оригинал сохранён.'
    if ext=='ods':
        with zipfile.ZipFile(path) as z:
            root=ET.fromstring(bounded_member(z,'content.xml'))
        return '\n'.join(' '.join(n.itertext()) for n in root.iter() if n.tag.rsplit('}',1)[-1]=='table-row')[:40000],'Прочитан текст строк ODS; оригинал сохранён.'
    if ext=='xls':
        import xlrd
        if path.stat().st_size>10*1024*1024:raise ValueError('large legacy workbook')
        book=xlrd.open_workbook(path,on_demand=True)
        try:
            lines=[]
            for sheet in book.sheets()[:4]:
                lines.append('[Лист: '+sheet.name+']')
                for n in range(min(40,sheet.nrows)):lines.append('\t'.join(str(v)[:180] for v in sheet.row_values(n,0,min(30,sheet.ncols))))
            return '\n'.join(lines)[:40000],'Прочитаны первые строки XLS; оригинал сохранён.'
        finally:book.release_resources()
    if ext=='rtf':
        from striprtf.striprtf import rtf_to_text
        with path.open('rb') as f:text=f.read(MAX_READ).decode('latin1')
        return rtf_to_text(text)[:40000],'Прочитан текст RTF; оригинал сохранён.'
    if ext in ('xlsx','xlsm'):

        import openpyxl
        with zipfile.ZipFile(path) as z:
            if sum(i.file_size for i in z.infolist())>40*1024*1024 or len(z.infolist())>10000:raise ValueError('large workbook')
        book=openpyxl.load_workbook(path,read_only=True,data_only=False,keep_links=False)
        try:
            parts=[]
            for sheet in list(book.worksheets)[:4]:
                parts.append('[Лист: '+sheet.title+']')
                for row in sheet.iter_rows(max_row=40,max_col=30,values_only=True):
                    values=[str(v)[:180] if v is not None else '' for v in row]
                    if any(values):parts.append('\t'.join(values).rstrip())
            return '\n'.join(parts)[:40000],'Прочитаны заголовки и до 40 строк первых 4 листов; формулы не исполняются.'
        finally:book.close()
    if ext=='zip':
        with zipfile.ZipFile(path) as z:
            infos=z.infolist()
            parts=[f'ZIP-архив: {len(infos)} элементов. Содержимое:']
            for info in infos[:100]:parts.append(info.filename[:200])
            # Sample only small readable members; no paths written, no nested archives.
            for info in [i for i in infos if extension(i.filename) in TEXT_EXT and not i.is_dir() and i.file_size<=32000][:4]:
                try:
                    text=safe_text(decode_text(bounded_member(z,info.filename,32000)),extension(info.filename))
                    parts.append('[Файл внутри архива: '+info.filename[:120]+']\n'+text[:1500])
                except (ValueError,RuntimeError,UnicodeError):continue
            return '\n'.join(parts)[:16000],'Прочитан список ZIP и начало небольших текстовых файлов; архив не распаковывался.'
    kind=file_kind(filename,mime)
    if kind in ('audio','video'):
        parts=[('Аудиозапись' if kind=='audio' else 'Видеозапись')+': '+filename]
        if kind=='audio':
            try:
                import mutagen
                media=mutagen.File(path,easy=True)
                if media:
                    for key in ('title','artist','album','genre','date'):
                        if media.get(key):parts.append(key+': '+', '.join(media[key])[:250])
                    if getattr(media,'info',None):parts.append('Длительность: '+str(round(media.info.length))+' с')
            except Exception:pass
        return '\n'.join(parts),'Прочитаны имя, формат и доступные метаданные; полная расшифровка не запускалась.'
    if ext in ARCHIVE_EXT:return filename,'Архив сохранён. Просмотр содержимого этого формата пока не поддерживается.'
    if ext in TEXT_EXT or mime.startswith('text/') or ext not in IMAGE_EXT and not mime.startswith(('image/','audio/','video/')):
        with path.open('rb') as f:raw=f.read(MAX_READ)
        text=decode_text(raw)
        return safe_text(text,ext),'Прочитан текст файла'+(' (ограниченное начало)' if path.stat().st_size>MAX_READ else '')+'. Оригинал сохранён.'
    return '', 'Формат сохранён; содержимое этого типа пока не читается.'


def content_hint(text,context=''):
    t=(context+'\n'+text).lower().replace('ё','е')
    rules=[
        (r'гибдд|водительск\w*\s+удостовер|автошкол|регистрационно.?\s*экзаменацион|обучени\w*\s+вождению','Авто','документ для вождения'),
        (r'автобиограф|свидетельство\s+о\s+рождении|заявлени\w*\s+на\s+загранпаспорт','Личные документы','личный документ'),
        (r'определител\w*\s+матриц|линейн\w*\s+алгебр|матриц|дифференциал|интеграл|предел\w*\s+функц','Высшая математика','учебный материал'),
        (r'договор\w*\s+(?:аренд|найм)|коммунальн|жилищн','Дом','документ'),
        (r'анализ\w*\s+крови|медицинск|рецепт\w*\s+врача|результат\w*\s+обследован','Здоровье','медицинский документ'),
        (r'продажи|выручка|бюджет|доходы|расходы|инвойс|invoice','Финансы','финансовые данные'),
        (r'резюме|ваканси|трудов\w*\s+договор','Работа','рабочий документ'),
    ]
    for pattern,folder,kind in rules:
        if re.search(pattern,t):return folder,kind
    return '', ''


def format_directive(title,context,groups=(),manual=False):
    """Only explicit human explanations teach extension rules; never file contents."""
    exts=set(re.findall(r'(?<!\w)\.?('+ '|'.join(sorted(TEXT_EXT|AUDIO_EXT|VIDEO_EXT|ARCHIVE_EXT,key=len,reverse=True))+r')(?!\w)',context.lower()))
    if not exts:return None
    label=groups[0] if manual and groups else title.strip()
    if not label or len(label)>40 or extension(label) or '\n' in label:return None
    # A plain label such as 'Музыка' + 'mp3 файлы' is an explicit collection preference.
    if not (manual or len(label.split())<=4):return None
    return label,sorted(exts)


def sniff_mime(path):
    """Trust known byte signatures over filename/MIME, never execute a decoder here."""
    try:
        with Path(path).open('rb') as f:head=f.read(512)
    except OSError:return ''
    for prefix,mime in ((b'%PDF-','application/pdf'),(b'\x89PNG\r\n\x1a\n','image/png'),(b'\xff\xd8\xff','image/jpeg'),(b'GIF8','image/gif'),(b'ID3','audio/mpeg'),(b'Rar!','application/vnd.rar'),(b'7z\xbc\xaf\x27\x1c','application/x-7z-compressed')):
        if head.startswith(prefix):return mime
    if head.startswith(b'PK\x03\x04'):return 'application/zip'
    if head.startswith(b'RIFF'):
        if head[8:12]==b'WEBP':return 'image/webp'
        if head[8:12]==b'WAVE':return 'audio/wav'
        if head[8:12]==b'AVI ':return 'video/avi'
    if head.lstrip().startswith((b'{',b'[')):
        try:decode_text(head);return 'application/json'
        except (ValueError,UnicodeError):pass
    return ''


def stabilize_folder(item,analysis):
    """A supported specific topic beats a generic container, without extra AI calls."""
    from .folders import primary_folder
    result=dict(analysis)
    groups=result.get('groups',[])
    if item.get('context','').strip() or item.get('manual_groups') or result.get('confidence',0)<.65:return result
    if not groups or groups[0] not in ('Учёба','Документы','Личные документы','Материалы'):return result
    source=item.get('body','')+'\n'+result.get('recognized_text','')
    topic,_=content_hint(source)
    if not topic and result.get('cloud',{}).get('images',0):
        topic,_=content_hint(result.get('summary','')+' '+result.get('subject',''))
    if not topic and re.search(r'учеб|зада|лекц|конспект|формул',result.get('document_type','')):
        subject=result.get('subject','')
        if subject and subject.casefold() not in ('учёба','образование','общее'):topic=subject
    if topic:result['groups']=primary_folder([topic])
    return result
