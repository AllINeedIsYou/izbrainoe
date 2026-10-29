import asyncio
import hashlib
import re
import secrets
import time
import json
from pathlib import Path
import httpx
from .processing import sensitive,delete_intent

class TelegramError(Exception): pass

class Bot:
    def __init__(self, config, store, processor):
        self.config=config; self.store=store; self.processor=processor
        self.client=httpx.AsyncClient(timeout=45)
        self.running=False; self.error=''; self.tasks=set()
        self.batch_users=set();self.batch_saved={};self.clear_locks={}

    async def call(self, method, **data):
        try:
            r=await self.client.post(f'https://api.telegram.org/bot{self.config.token}/{method}',json=data)
            payload=r.json()
            if not payload.get('ok'): raise TelegramError(payload.get('description','Ошибка Telegram'))
            result=payload['result']
            if method.startswith('send') and isinstance(data.get('chat_id'),int):
                for message in result if isinstance(result,list) else [result]:self.store.record_outgoing(data['chat_id'],message)
            return result
        except httpx.HTTPError:
            # httpx exception strings contain the token-bearing URL. Never log them.
            raise TelegramError('Нет соединения с Telegram') from None

    def keyboard(self):
        return {'keyboard':[[{'text':'🔎 Найти'},{'text':'🗂 Подборки'}]],'resize_keyboard':True,'is_persistent':True}

    def web_button(self, label='Открыть подборки'):
        if not self.config.public_url: return None
        return {'inline_keyboard':[[{'text':label,'web_app':{'url':self.config.public_url}}]]}

    async def configure_menu(self, uid):
        if self.config.public_url:
            await self.call('setChatMenuButton',chat_id=uid,menu_button={
                'type':'web_app','text':'Подборки','web_app':{'url':self.config.public_url}})

    async def send(self, uid, text, markup=None, **kw):
        result=await self.call('sendMessage',chat_id=uid,text=text[:4000],reply_markup=markup or self.keyboard(),**kw)
        self.store.record_outgoing(uid,result)
        return result

    def observe_own_message(self,uid,message):
        if not isinstance(message,dict):return
        sender=message.get('from') or {}
        chat=(message.get('chat') or {}).get('id',uid)
        prefix=self.config.token.split(':',1)[0]
        own=(sender.get('id')==int(prefix)) if prefix.isdigit() else sender.get('username','').lower()==self.config.bot_username.lower()
        if sender.get('is_bot') and own and chat==uid:self.store.record_outgoing(uid,message)

    async def clear_chat(self,uid):
        async with self.clear_locks.setdefault(uid,asyncio.Lock()):
            rows=self.store.outgoing(uid);cutoff=time.time()-48*3600
            recent=[r['message_id'] for r in rows if not r['created'] or r['created']>cutoff]
            expired=len(rows)-len(recent);processed=failed=0
            for start in range(0,len(recent),100):
                batch=recent[start:start+100]
                try:
                    await self.call('deleteMessages',chat_id=uid,message_ids=batch)
                    self.store.forget_outgoing(uid,batch);processed+=len(batch)
                except TelegramError:
                    # One undeletable legacy message must not block the rest of the batch.
                    for mid in batch:
                        try:
                            await self.call('deleteMessage',chat_id=uid,message_id=mid)
                            self.store.forget_outgoing(uid,[mid]);processed+=1
                        except TelegramError as e:
                            if 'not found' in str(e).lower():self.store.forget_outgoing(uid,[mid]);processed+=1
                            else:failed+=1
            self.store.mode(uid,'')
            self.store.meta(f'last_clear_{uid}',json.dumps({'requested_at':time.time(),'processed':processed,'expired':expired,'failed':failed}))
            summary=f'Обработано известных ответов бота: {processed}.' if processed else 'В учёте нет ответов бота, которые удалось удалить.'
            summary+=' Твои сообщения и сохранённые материалы остались.'
            if expired:summary+=f'\nСтарше 48 часов: {expired}.'
            if failed:summary+=f'\nTelegram не дал удалить: {failed}. Попробуй позже.'
            summary+='\nСтарые ответы, которые прежняя версия не учитывала, могут остаться. Ответь на такой ответ командой /clear, чтобы убрать его. Уведомление исчезнет через 8 секунд.'
            result=await self.send(uid,summary)
            async def hide_notice():
                await asyncio.sleep(8)
                try:
                    await self.call('deleteMessage',chat_id=uid,message_id=result['message_id'])
                    self.store.forget_outgoing(uid,[result['message_id']])
                except (TelegramError,KeyError):pass
            task=asyncio.create_task(hide_notice());self.tasks.add(task);task.add_done_callback(self.tasks.discard)
            return {'processed':processed,'expired':expired,'failed':failed}

    async def results(self, uid, query, offset=0):
        data=await self.processor.search(uid,query,offset=offset,limit=4)
        if not data['items']:
            await self.send(uid,'Пока ничего не нашлось. Попробуй название, фразу из материала или другое описание.')
            return
        await self.send(uid,f'Нашлось: {data["total"]}. '+('Поиск по словам и описанию.' if data['mode']=='hybrid' else 'Сейчас ищу по словам.'))
        for i in data['items']:
            markup={'inline_keyboard':[[{'text':'Получить оригинал','callback_data':'get:'+i['id']},
                                       {'text':'Добавить контекст','callback_data':'ctx:'+i['id']}]]}
            await self.send(uid,f'{i["title"]}\n{" · ".join(i["groups"]) or "Неразобранное"}\n\n{i.get("snippet","")[:650]}',markup)
        if offset+4<data['total']:
            # Query state is per-user; don't put arbitrary query text in callback data.
            self.store.meta(f'query_{uid}',query)
            await self.send(uid,'Ещё результаты',{'inline_keyboard':[[{'text':'Показать ещё','callback_data':f'more:{offset+4}'}]]})

    async def download(self, uid, file, filename):
        if file.get('file_size',0)>20*1024*1024:
            return '', '', 'Оригинал в Telegram. Из-за лимита Bot API файл больше 20 МБ пока не скачан. Открой карточку и добавь оригинал до 100 МБ: прочитаю первые 4 страницы.'
        f=await self.call('getFile',file_id=file['file_id'])
        path=self.store.folder/'files'/(secrets.token_hex(16)+Path(filename).suffix[:12])
        size=0; digest=hashlib.sha256()
        try:
            async with self.client.stream('GET',f'https://api.telegram.org/file/bot{self.config.token}/{f["file_path"]}') as r:
                r.raise_for_status()
                with path.open('wb') as out:
                    async for b in r.aiter_bytes():
                        size+=len(b)
                        if size>20*1024*1024: raise TelegramError('Файл слишком большой')
                        out.write(b); digest.update(b)
            path.chmod(0o600)
            return str(path),digest.hexdigest(),''
        except Exception:
            path.unlink(missing_ok=True)
            return '', '', 'Не удалось скачать содержимое. Файл доступен через Telegram.'

    async def send_original(self, uid, item_id):
        item=self.store.get(uid,item_id,raw=True)
        if not item: raise TelegramError('Материал не найден')
        if item['tg_file_id']:
            methods={'photo':('sendPhoto','photo'),'voice':('sendVoice','voice'),'audio':('sendAudio','audio')}
            method,param=methods.get(item['kind'],('sendDocument','document'))
            result=await self.call(method,chat_id=uid,**{param:item['tg_file_id']})
            self.store.record_outgoing(uid,result)
            self.store.link_message(uid,result['message_id'],item_id)
            return result
        if item['path']:
            try:
                with open(item['path'],'rb') as f:
                    r=await self.client.post(f'https://api.telegram.org/bot{self.config.token}/sendDocument',data={'chat_id':str(uid)},files={'document':(item['filename'],f,'application/octet-stream')})
                if not r.json().get('ok'): raise TelegramError('Не удалось отправить файл')
                self.store.record_outgoing(uid,r.json()['result'])
                self.store.link_message(uid,r.json()['result']['message_id'],item_id)
                return
            except httpx.HTTPError: raise TelegramError('Не удалось отправить файл') from None
        result=await self.send(uid,item['body'] or item['title'])
        self.store.link_message(uid,result['message_id'],item_id)

    async def callback(self, q):
        uid=q['from']['id']
        if not self.store.allowed(uid,self.config.allowed_ids):
            await self.call('answerCallbackQuery',callback_query_id=q['id'],text='Нет доступа')
            return
        self.observe_own_message(uid,q.get('message'))
        data=q.get('data',''); action,_,value=data.partition(':')
        await self.call('answerCallbackQuery',callback_query_id=q['id'])
        if action=='get': await self.send_original(uid,value)
        elif action=='ctx' and self.store.get(uid,value):
            self.store.mode(uid,'context:'+value)
            await self.send(uid,'Напиши пояснение к этому материалу. /cancel — отмена.')
        elif action=='more':
            query=self.store.meta(f'query_{uid}')
            if query: await self.results(uid,query,int(value))

    async def message(self, m, edited=False):
        if m.get('chat',{}).get('type')!='private': return
        uid=m['from']['id']; text=m.get('text',m.get('caption',''))
        if text.startswith('/start '):
            code=text.split(' ',1)[1].strip()
            if self.store.pair(uid,code):
                await self.configure_menu(uid)
                await self.send(uid,'Ты подключён к Изbrainому. Пересылай сюда важное. Чтобы найти — напиши «Найди…» или нажми кнопку поиска.')
                return
        if not self.store.allowed(uid,self.config.allowed_ids):
            if text.startswith('/start'):
                await self.call('sendMessage',chat_id=uid,text='Изbrainое пока в закрытом тесте. Нужна личная ссылка-приглашение от владельца.')
            return
        self.observe_own_message(uid,m.get('reply_to_message'))
        if not edited and not m.get('forward_origin') and re.fullmatch(r'/clear(?:@'+re.escape(self.config.bot_username)+r')?\s*',text,re.I):
            await self.clear_chat(uid);return
        if edited:
            old=self.store.find_message(uid,m['message_id'])
            if old and not sensitive(text):
                self.store.update(uid,old['id'],body=text)
                await self.processor.index(uid,old['id'])
            return
        if text in ('/start','/help'):
            await self.configure_menu(uid)
            await self.send(uid,'Пересылай сообщения, фото, PDF и голосовые. Я сохраню их.\n\n«Найди…» — поиск. Ответ на сохранённое сообщение — добавить контекст. Пояснения помогают пересматривать и похожие материалы.\n\nНапиши «удалить» в пояснении или ответом на нужный материал — запись попадёт в корзину. Восстановить можно в настройках. /cancel — отменить ввод.\n\n/clear — убрать известные ответы бота за последние 48 часов. Твои сообщения и архив сохранятся. Старые неучтённые ответы могут остаться.\n\nДля анализа PDF читаются первые 4 страницы, включая сканы. Пароли добавляй в защищённом разделе подборок.',self.web_button() or self.keyboard())
            return
        if text=='/cancel':
            self.store.mode(uid,''); await self.send(uid,'Отменено. Следующее сообщение сохраню как заметку.'); return
        if text=='🔎 Найти':
            self.store.mode(uid,'search'); await self.send(uid,'Что найти? Напиши название, фразу или описание. /cancel — отмена.'); return
        if text=='🗂 Подборки':
            if self.config.public_url: await self.send(uid,'Все материалы и подборки:',self.web_button())
            else:
                groups={}
                for i in self.store.list(uid):
                    for g in i['groups'] or ['Неразобранное']: groups[g]=groups.get(g,0)+1
                await self.send(uid,'\n'.join(f'{g}: {n}' for g,n in groups.items())+'\n\nПодборки с просмотром файлов готовы на Mac. Для телефона нужно подключить HTTPS-адрес.' if groups else 'Пока пусто. Перешли первый материал.')
            return
        if any(k in m for k in ('video','video_note','animation')):
            await self.send(uid,'Видео пока не поддерживаются. Можно сохранить ссылку или текстовое пояснение.'); return
        if sensitive(text):
            await self.send(uid,'Похоже, здесь есть пароль или другой секрет. Я не добавил его в базу и поиск. Для паролей используй защищённый раздел в подборках. Исходное сообщение остаётся в Telegram.',self.web_button('Открыть защищённый раздел') or self.keyboard())
            return
        mode=self.store.mode(uid)
        # Forwarded text remains a save, even if it starts with “Найди”.
        forwarded=bool(m.get('forward_origin'))
        if mode=='search' or (not forwarded and re.match(r'^найди\b',text,re.I)):
            if not text and m.get('voice') and self.processor.voice_ready():
                path,_,_=await self.download(uid,m['voice'],'Голосовое.ogg')
                if path:
                    try:text,_=await self.processor.transcribe(path,'Голосовое.ogg')
                    finally:Path(path).unlink(missing_ok=True)
                text=re.sub(r'\[[^\]]+\]','',text).strip()
            if not text:
                await self.send(uid,'Не удалось прочитать запрос. Напиши его текстом.'); return
            if sensitive(text):
                await self.send(uid,'Запрос может содержать секрет. Найди запись внутри защищённого раздела.');return
            self.store.mode(uid,'')
            await self.results(uid,re.sub(r'^найди\s*','',text,flags=re.I)); return
        context_item=mode.split(':',1)[1] if mode.startswith('context:') else None
        if not context_item and not forwarded and m.get('reply_to_message'):
            item=self.store.find_message(uid,m['reply_to_message']['message_id'])
            if item: context_item=item['id']
        if context_item:
            if not text:
                await self.send(uid,'Пояснение пока нужно написать текстом.'); return
            item=self.store.get(uid,context_item)
            if item:
                if delete_intent(text):
                    self.store.update(uid,context_item,deleted=time.time())
                    self.store.mode(uid,'')
                    await self.send(uid,'Запись перемещена в корзину. Восстановить можно в «Настройки → Корзина».');return
                self.store.update(uid,context_item,context=(item['context']+'\n'+text).strip()[:10000])
                await self.processor.feedback(uid,context_item)
                self.store.mode(uid,'')
                new=self.store.get(uid,context_item)
                await self.send(uid,'Пояснение запомнил. Подборки: '+(' · '.join(new['groups']) or 'Неразобранное')+'\nПроверяю похожие материалы в твоём архиве.')
            else:
                self.store.mode(uid,''); await self.send(uid,'Материал больше недоступен. Пояснение не сохранено.')
            return
        # Idempotency for redelivered Telegram updates.
        with self.store.db() as c:
            exists=c.execute('SELECT 1 FROM items WHERE user_id=? AND tg_message=?',(uid,m['message_id'])).fetchone()
        if exists: return
        caption=text
        file=None; kind='note'; filename=''; mime=''; path=''; digest=''; status=''
        if m.get('document'):
            file=m['document']; filename=file.get('file_name','Документ'); mime=file.get('mime_type','application/octet-stream'); kind='document'
        elif m.get('photo'):
            file=m['photo'][-1]; filename='Фото.jpg'; mime='image/jpeg'; kind='photo'
        elif m.get('voice'):
            file=m['voice']; filename='Голосовое.ogg'; mime='audio/ogg'; kind='voice'
        elif m.get('audio'):
            file=m['audio']; filename=file.get('file_name','Аудио.mp3'); mime=file.get('mime_type','audio/mpeg'); kind='audio'
        elif re.search(r'https?://',text): kind='link'
        if not file and not text: return
        if file:
            path,digest,status=await self.download(uid,file,filename)
            if path:
                extracted,status=await self.processor.extract(path,mime,filename)
                if sensitive(extracted):
                    Path(path).unlink(missing_ok=True)
                    await self.send(uid,'В содержимом обнаружен возможный секрет. Материал не добавлен в локальное хранилище или поиск. Исходник остался в Telegram.'); return
                text='\n\n'.join(x for x in [text,extracted] if x)
        title=filename or text.split('\n')[0][:100]
        duplicate=self.store.duplicate(uid,digest)
        i=self.store.create(uid,kind,title,text,filename=filename,mime=mime,path=path,digest=digest,
                            tg_file_id=file['file_id'] if file else '',tg_message=m['message_id'],extraction=status,original_caption=caption if file else '')
        await self.processor.index(uid,i['id'])
        i=self.store.get(uid,i['id'])
        markup={'inline_keyboard':[[{'text':'Добавить контекст','callback_data':'ctx:'+i['id']}]]}
        if file and not path and self.config.public_url:
            markup['inline_keyboard'].append([{'text':'Открыть карточку и добавить оригинал','web_app':{'url':self.config.public_url+'/?item='+i['id']}}])
        if uid in self.batch_users:
            self.batch_saved.setdefault(uid,[]).append(i)
        else:
            confirmation=await self.send(uid,'Сохранено: '+(' · '.join(i['groups']) or 'Неразобранное')+('\nЕсть файл с таким же содержимым.' if duplicate else '')+('\n'+status if status else ''),markup)
            self.store.update(uid,i['id'],bot_message=confirmation['message_id'])

    async def run(self):
        if not self.config.token: return
        offset=int(self.store.meta('update_offset') or 0)
        try:
            me=await self.call('getMe')
            if me.get('username','').lower()!=self.config.bot_username.lower():
                self.error='Токен принадлежит другому боту'; return
            webhook=await self.call('getWebhookInfo')
            if webhook.get('url'):
                self.error='У бота уже настроен webhook. Отключите его перед локальным запуском.'; return
            await self.call('setMyCommands',commands=[{'command':'start','description':'Начать'},{'command':'help','description':'Как пользоваться'},{'command':'clear','description':'Убрать ответы бота, сохранить архив'},{'command':'cancel','description':'Отменить ввод'}])
            if self.config.public_url:
                await self.call('setChatMenuButton',menu_button={'type':'web_app','text':'Подборки','web_app':{'url':self.config.public_url}})
                with self.store.db() as c:
                    users={r[0] for r in c.execute('SELECT id FROM users')} | set(self.config.allowed_ids)
                for uid in users:
                    await self.configure_menu(uid)
            self.running=True
            while True:
                try:
                    updates=await self.call('getUpdates',offset=offset,timeout=25,allowed_updates=['message','edited_message','callback_query'])
                    counts={}
                    for u in updates:
                        m=u.get('message',{})
                        uid=m.get('from',{}).get('id')
                        if uid:counts[uid]=counts.get(uid,0)+1
                    self.batch_users={uid for uid,n in counts.items() if n>1}
                    self.batch_saved={}
                    for u in updates:
                        try:
                            if 'message' in u: await self.message(u['message'])
                            elif 'edited_message' in u: await self.message(u['edited_message'],True)
                            elif 'callback_query' in u: await self.callback(u['callback_query'])
                        except Exception:
                            self.error='Не удалось обработать сообщение. Попробуйте ещё раз.'
                            m=u.get('message') or u.get('edited_message')
                            if m and self.store.allowed(m.get('from',{}).get('id'),self.config.allowed_ids):
                                try: await self.send(m['from']['id'],self.error)
                                except Exception: pass
                        offset=u['update_id']+1; self.store.meta('update_offset',offset)
                    for uid,items in self.batch_saved.items():
                        groups=list(dict.fromkeys(g for i in items for g in (i['groups'] or ['Неразобранное'])))
                        await self.send(uid,f'Сохранено материалов: {len(items)}.\nПодборки: '+', '.join(groups)+'\n\nЧтобы добавить пояснение, ответь на конкретное исходное сообщение.',self.web_button() or self.keyboard())
                    self.batch_users=set();self.batch_saved={}
                except TelegramError:
                    self.error='Нет соединения с Telegram или бот запущен в другом месте'
                    await asyncio.sleep(5)
        except TelegramError as e:
            self.error=str(e)
        finally:
            self.running=False

    async def close(self):
        tasks=list(self.tasks)
        for t in tasks:t.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        await self.client.aclose()
