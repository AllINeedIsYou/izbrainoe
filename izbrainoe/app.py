import asyncio
import base64
import hashlib
import json
import re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Form, Depends
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .config import Config, ROOT
from .store import Store
from .processing import Processor, sensitive,delete_intent
from .auth import validate_telegram
from .bot import Bot, TelegramError

class Note(BaseModel):
    text: str = Field(min_length=1,max_length=50000)
    title: str = Field(default='',max_length=200)

class Change(BaseModel):
    title: str | None = Field(default=None,min_length=1,max_length=200)
    context: str | None = Field(default=None,max_length=10000)
    groups: list[str] | None = Field(default=None,max_length=5)
    automatic: bool = False

class VaultBody(BaseModel):
    version: int = Field(ge=0)
    envelope: dict

def create_app(config=None):
    config=config or Config.load()
    store=Store(config.data_dir); processor=Processor(config,store); bot=Bot(config,store,processor)
    tasks=set()

    async def review_done(uid,checked,changed):
        if config.token and uid>0 and store.allowed(uid,config.allowed_ids):
            await bot.send(uid,f'Проверил похожие материалы: {checked}. Обновил подборки у {changed}. Ручные подборки сохранил.')
    processor.review_notifier=review_done

    def background(coro):
        task=asyncio.create_task(coro); tasks.add(task)
        def done(t):
            tasks.discard(t)
            if not t.cancelled():
                try: t.result()
                except Exception: pass
        task.add_done_callback(done)
        return task

    @asynccontextmanager
    async def lifespan(app):
        await processor.probe()
        task=background(bot.run())
        yield
        for t in list(tasks): t.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        await processor.close()
        await bot.close()

    app=FastAPI(title='Изbrainое',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.store=store; app.state.processor=processor; app.state.bot=bot

    def local(request):
        host=urlparse('http://'+request.headers.get('host','')).hostname
        return host in ('127.0.0.1','localhost','::1','testserver') and request.client and request.client.host in ('127.0.0.1','::1','testclient')

    def local_session(request):
        return local(request) and secrets.compare_digest(request.cookies.get('iz_session',''),store.meta('local_key'))

    async def user(request: Request):
        if local_session(request):
            return int(store.meta('owner') or -1)
        header=request.headers.get('authorization','')
        if header.startswith('tma '):
            try: uid=validate_telegram(header[4:],config.token)
            except (ValueError,KeyError,TypeError,json.JSONDecodeError): raise HTTPException(401,'Откройте приложение из Telegram заново.')
            if store.allowed(uid,config.allowed_ids): return uid
            raise HTTPException(403,'Нужна личная ссылка-приглашение.')
        raise HTTPException(401,'Откройте Изbrainое через бота или ярлык запуска на Mac.')

    @app.middleware('http')
    async def headers(request,call_next):
        if request.headers.get('content-length','0').isdigit() and int(request.headers.get('content-length','0'))>(config.max_upload_mb+2)*1024*1024:
            return JSONResponse({'detail':f'Максимальный размер файла — {config.max_upload_mb} МБ.'},413)
        # Cookie-authenticated mutations need same-origin + a custom request header.
        if request.url.path.startswith('/api/') and request.method not in ('GET','HEAD','OPTIONS'):
            origin=request.headers.get('origin')
            allowed={str(request.base_url).rstrip('/'),config.public_url}
            if (origin and origin not in allowed) or request.headers.get('x-iz-request')!='1':
                return JSONResponse({'detail':'Запрос отклонён.'},403)
        response=await call_next(request)
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Cache-Control']='no-store'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self' https://telegram.org; connect-src 'self'; img-src 'self' blob: data:; style-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self' https://web.telegram.org https://*.telegram.org"
        return response

    @app.get('/local')
    async def enter(request:Request,key:str=''):
        if not local(request) or not secrets.compare_digest(key,store.meta('local_key')):
            raise HTTPException(403,'Используйте локальный ярлык запуска.')
        response=RedirectResponse('/',status_code=303)
        response.set_cookie('iz_session',key,httponly=True,samesite='strict',max_age=86400)
        return response

    @app.get('/health')
    async def health(): return {'ok':True,'app':'Изbrainое'}

    @app.get('/api/me')
    async def me(request:Request,uid=Depends(user)):
        await processor.probe()
        rows=store.list(uid)
        owner=store.meta('owner')
        groups={}
        for i in rows:
            for g in i['groups'] or ['Неразобранное']: groups[g]=groups.get(g,0)+1
        return {'id':uid,'local':bool(local_session(request)),'owner':bool(local_session(request) or str(uid)==owner),
                'groups':groups,'total':len(rows),'bot_username':config.bot_username,
                'bot_configured':bool(config.token),'bot_running':bot.running,'bot_error':bot.error,
                'public_url':config.public_url,'ai_ready':processor.ai_ready,'ai_error':processor.ai_error,
                'chat_model':bool(config.chat_model),'ocr_ready':(ROOT/'runtime'/'ocr').exists(),
                'voice_ready':processor.voice_ready(),'paired':bool(owner),
                'memory_examples':len(store.memories(uid)),'learning':store.learning_state(uid),
                'web_lookup':config.web_lookup,'pdf_pages':config.pdf_pages,'max_upload_mb':config.max_upload_mb,
                'pair_link':f'https://t.me/{config.bot_username}?start={store.meta("pair_code")}' if local_session(request) and not owner else None}

    @app.get('/api/items')
    async def items(q:str='',group:str='',kind:str='',offset:int=0,limit:int=40,trash:bool=False,uid=Depends(user)):
        if len(q)>500: raise HTTPException(400,'Запрос слишком длинный.')
        return await processor.search(uid,q,group,kind,max(0,offset),min(100,max(1,limit)),trash)

    @app.get('/api/items/{item_id}')
    async def get_item(item_id:str,uid=Depends(user)):
        i=store.get(uid,item_id,trash=True)
        if not i: raise HTTPException(404,'Материал не найден.')
        return i

    @app.post('/api/items',status_code=201)
    async def add_note(body:Note,uid=Depends(user)):
        if sensitive(body.text+' '+body.title): raise HTTPException(400,'Возможно, здесь есть секрет. Добавьте его в защищённом разделе «Пароли».')
        item=store.create(uid,'link' if re.search(r'https?://',body.text) else 'note',body.title or body.text.split('\n')[0][:100],body.text)
        await processor.index(uid,item['id'])
        return store.get(uid,item['id'])

    @app.post('/api/upload',status_code=201)
    async def upload(file:UploadFile=File(...),context:str=Form(''),uid=Depends(user)):
        if len(context)>10000 or sensitive(context): raise HTTPException(400,'Проверьте пояснение. Секреты добавляются в разделе «Пароли».')
        name=Path(file.filename or 'Файл').name[:180]
        suffix=Path(name).suffix.lower()
        if suffix in ('.mp4','.mov','.avi','.mkv','.webm'): raise HTTPException(400,'Видео пока не поддерживаются.')
        path=store.folder/'files'/(secrets.token_hex(16)+suffix[:12])
        digest=hashlib.sha256(); size=0
        try:
            with path.open('wb') as out:
                while data:=await file.read(65536):
                    size+=len(data)
                    if size>config.max_upload_mb*1024*1024: raise HTTPException(413,f'Максимальный размер файла — {config.max_upload_mb} МБ.')
                    out.write(data); digest.update(data)
            path.chmod(0o600)
            mime=file.content_type or 'application/octet-stream'
            text,status=await processor.extract(path,mime,name)
            if sensitive(text+' '+name): raise HTTPException(400,'В файле обнаружен возможный секрет. Он не добавлен в хранилище или поиск.')
            kind='photo' if mime.startswith('image/') else 'audio' if mime.startswith('audio/') else 'document'
            item=store.create(uid,kind,name,text,filename=name,path=str(path),mime=mime,digest=digest.hexdigest(),extraction=status)
            if context: store.update(uid,item['id'],context=context)
            await processor.index(uid,item['id'])
            return store.get(uid,item['id'])
        except BaseException:
            path.unlink(missing_ok=True); raise

    @app.patch('/api/items/{item_id}')
    async def change(item_id:str,body:Change,uid=Depends(user)):
        old=store.get(uid,item_id)
        if not old: raise HTTPException(404,'Материал не найден.')
        if body.context is not None and delete_intent(body.context):
            store.update(uid,item_id,deleted=time.time())
            return {**store.get(uid,item_id,trash=True),'message':'Запись перемещена в корзину.'}
        values=body.model_dump(exclude_none=True)
        automatic=values.pop('automatic')
        gs=values.pop('groups',None)
        if sensitive((body.context or '')+' '+(body.title or '')): raise HTTPException(400,'Секреты добавляются в разделе «Пароли».')
        if gs is not None:
            gs=list(dict.fromkeys(g.strip() for g in gs if g.strip()))
            if any(len(g)>40 or sensitive(g) for g in gs): raise HTTPException(400,'Проверьте названия подборок.')
            if gs!=old['groups'] or old['manual_groups']:
                values.update(groups_json=json.dumps(gs,ensure_ascii=False),manual_groups=1)
        if automatic: values['manual_groups']=0
        store.update(uid,item_id,**values)
        if body.context is not None or gs is not None:await processor.feedback(uid,item_id)
        else:await processor.index(uid,item_id)
        return {**store.get(uid,item_id),'learning':store.learning_state(uid)}

    @app.post('/api/items/{item_id}/attach')
    async def attach_original(item_id:str,file:UploadFile=File(...),uid=Depends(user)):
        old=store.get(uid,item_id,raw=True)
        if not old:raise HTTPException(404,'Материал не найден.')
        name=Path(file.filename or old['filename'] or 'Файл').name[:180]
        suffix=Path(name).suffix.lower();path=store.folder/'files'/(secrets.token_hex(16)+suffix[:12])
        digest=hashlib.sha256();size=0
        try:
            with path.open('wb') as out:
                while data:=await file.read(65536):
                    size+=len(data)
                    if size>config.max_upload_mb*1024*1024:raise HTTPException(413,f'Максимум {config.max_upload_mb} МБ.')
                    out.write(data);digest.update(data)
            path.chmod(0o600)
            text,status=await processor.extract(path,file.content_type or 'application/octet-stream',name)
            if sensitive(text):raise HTTPException(400,'Обнаружен возможный секрет. Используйте защищённый раздел.')
            body='\n\n'.join(x for x in (old.get('original_caption',''),text) if x)
            store.update(uid,item_id,path=str(path),body=body,extraction=status,digest=digest.hexdigest(),
                         filename=name,mime=file.content_type or 'application/octet-stream',source_label='')
            await processor.index(uid,item_id)
            return store.get(uid,item_id)
        except BaseException:path.unlink(missing_ok=True);raise

    @app.post('/api/items/{item_id}/analyse')
    async def analyse_again(item_id:str,uid=Depends(user)):
        item=store.get(uid,item_id,raw=True)
        if not item:raise HTTPException(404,'Материал не найден.')
        try:await processor.reread(uid,item_id)
        except ValueError as e:raise HTTPException(400,str(e))
        return store.get(uid,item_id)

    @app.delete('/api/items/{item_id}')
    async def remove(item_id:str,uid=Depends(user)):
        if not store.get(uid,item_id): raise HTTPException(404,'Материал не найден.')
        store.update(uid,item_id,deleted=time.time())
        return {'ok':True}

    @app.post('/api/items/{item_id}/restore')
    async def restore(item_id:str,uid=Depends(user)):
        if not store.get(uid,item_id,trash=True): raise HTTPException(404,'Материал не найден.')
        return store.update(uid,item_id,deleted=None)

    @app.get('/api/items/{item_id}/file')
    async def original(item_id:str,uid=Depends(user)):
        i=store.get(uid,item_id,raw=True)
        if not i or not i['path'] or not Path(i['path']).is_file(): raise HTTPException(404,'Оригинал доступен через бота или ещё не загружен.')
        # Untrusted originals always download as attachments; never execute HTML/SVG.
        return FileResponse(i['path'],filename=i['filename'] or 'Файл',media_type='application/octet-stream')

    @app.get('/api/items/{item_id}/preview')
    async def preview(item_id:str,uid=Depends(user)):
        i=store.get(uid,item_id,raw=True)
        if not i or not i['path'] or i['mime'] not in ('image/jpeg','image/png','image/webp','audio/ogg','audio/mpeg','audio/wav'):
            raise HTTPException(404,'Нет предварительного просмотра.')
        return FileResponse(i['path'],media_type=i['mime'])

    @app.post('/api/items/{item_id}/send')
    async def send_original(item_id:str,uid=Depends(user)):
        if not config.token or uid<0: raise HTTPException(409,'Сначала подключите Telegram-бота.')
        if not store.get(uid,item_id): raise HTTPException(404,'Материал не найден.')
        try: await bot.send_original(uid,item_id)
        except TelegramError as e: raise HTTPException(502,str(e))
        return {'ok':True}

    @app.get('/api/vault')
    async def vault(uid=Depends(user)): return store.vault(uid)

    @app.put('/api/vault')
    async def vault_save(body:VaultBody,uid=Depends(user)):
        e=body.envelope
        if set(e)!=set(('v','kdf','iterations','salt','iv','ciphertext')) or e.get('v')!=1 or e.get('kdf')!='PBKDF2-SHA256' or e.get('iterations')!=600000:
            raise HTTPException(400,'Неподдерживаемый формат шифрования.')
        try:
            lengths={k:len(base64.b64decode(e[k],validate=True)) for k in ('salt','iv','ciphertext')}
            if lengths['salt']!=16 or lengths['iv']!=12 or not 16<=lengths['ciphertext']<=2000000: raise ValueError()
        except (ValueError,TypeError): raise HTTPException(400,'Повреждён зашифрованный блок.')
        if not store.save_vault(uid,body.version,e): raise HTTPException(409,'Хранилище изменилось на другом устройстве. Заблокируйте и откройте его заново.')
        return {'version':body.version+1}

    @app.post('/api/invite')
    async def invite(request:Request,uid=Depends(user)):
        if not local_session(request) and str(uid)!=store.meta('owner'): raise HTTPException(403,'Приглашать может только владелец.')
        if not store.meta('owner'): raise HTTPException(409,'Сначала подключите владельца.')
        return {'url':f'https://t.me/{config.bot_username}?start={store.invite()}','expires_hours':24}

    @app.post('/api/reindex')
    async def reindex(uid=Depends(user)):
        queued=processor.rebuild(uid)
        return {'ok':True,'queued':queued,'learning':store.learning_state(uid)}

    @app.get('/api/export')
    async def export(uid=Depends(user)):
        # A JSON export contains normal material, never a plaintext vault.
        return JSONResponse({'app':'Изbrainое','items':store.list(uid),'vault':store.vault(uid)},headers={'Content-Disposition':'attachment; filename="izbrainoe-export.json"'})

    @app.get('/')
    async def index(): return FileResponse(ROOT/'izbrainoe'/'static'/'index.html')
    app.mount('/static',StaticFiles(directory=ROOT/'izbrainoe'/'static'),name='static')
    return app
