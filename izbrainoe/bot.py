import asyncio
import hashlib
import re
import secrets
import time
import json
import html
from pathlib import Path
import httpx
from .processing import sensitive,delete_intent
from .vault_access import VaultAccess,VaultAccessError,credential_matches,credential_query

class TelegramError(Exception): pass

class Bot:
    def __init__(self, config, store, processor):
        self.config=config; self.store=store; self.processor=processor
        self.client=httpx.AsyncClient(timeout=45)
        self.running=False; self.error=''; self.tasks=set();self.verified=False
        self.batch_users=set();self.batch_saved={};self.clear_locks={}
        self.media_limit=asyncio.Semaphore(3)
        self.vault_access=VaultAccess(store)
        self.keyboard_ready=set();self.keyboard_locks={}

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
        except (ValueError,TypeError,KeyError,AttributeError):
            raise TelegramError('Telegram вернул некорректный ответ. Повторю подключение.') from None

    def keyboard(self):
        return {'keyboard':[[{'text':'🔎 Найти'},{'text':'🗂 Подборки'}]],'resize_keyboard':True,'is_persistent':True,'one_time_keyboard':False}

    def web_button(self, label='Открыть подборки', page=''):
        if not self.config.public_url: return None
        url=self.config.public_url+('/?page='+page if page in ('vault','settings') else '')
        return {'inline_keyboard':[[{'text':label,'web_app':{'url':url}}]]}

    async def configure_menu(self, uid):
        if self.config.public_url:
            await self.call('setChatMenuButton',chat_id=uid,menu_button={
                'type':'web_app','text':'Подборки','web_app':{'url':self.config.public_url}})

    async def refresh_menus(self):
        if not self.config.public_url:return
        await self.call('setChatMenuButton',menu_button={'type':'web_app','text':'Подборки','web_app':{'url':self.config.public_url}})
        with self.store.db() as c:
            users={r[0] for r in c.execute('SELECT id FROM users')} | set(self.config.allowed_ids)
        for uid in users:await self.configure_menu(uid)

    def migrate_chat_cleanup(self):
        if self.store.meta('automatic_chat_cleanup_v1'):return
        with self.store.db() as c:
            users={r[0] for r in c.execute('SELECT user_id FROM outgoing_messages UNION SELECT user_id FROM service_messages')}
        now=time.time()
        for uid in users:
            with self.store.db() as c:
                scheduled={r[0] for r in c.execute('SELECT message_id FROM expiring_messages WHERE user_id=?',(uid,))}
            for row in self.store.chat_messages(uid):
                if row['message_id'] not in scheduled and row['created']>now-48*3600:
                    self.store.expire_message(uid,row['message_id'],now+8)
        self.store.meta('automatic_chat_cleanup_v1','1')

    async def send(self, uid, text, markup=None, ttl=8, **kw):
        if markup and 'inline_keyboard' in markup:
            async with self.keyboard_locks.setdefault(uid,asyncio.Lock()):
                if uid not in self.keyboard_ready:
                    notice=await self.call('sendMessage',chat_id=uid,text='Кнопки «Найти» и «Подборки» — внизу.',reply_markup=self.keyboard())
                    self.store.record_outgoing(uid,notice)
                    self.store.expire_message(uid,notice['message_id'],time.time()+8)
                    self.store.meta(f'keyboard_notice_{uid}',str(notice['message_id']))
                    self.keyboard_ready.add(uid)
        else:self.keyboard_ready.add(uid)
        result=await self.call('sendMessage',chat_id=uid,text=text[:4000],reply_markup=markup or self.keyboard(),**kw)
        self.store.record_outgoing(uid,result)
        if ttl is not None:self.store.expire_message(uid,result['message_id'],time.time()+ttl)
        return result

    async def dismiss_search_prompt(self,uid):
        mid=self.store.meta(f'search_prompt_{uid}')
        if mid:
            self.store.expire_message(uid,int(mid),time.time())
            self.store.meta(f'search_prompt_{uid}','')
            await self.expire_due()

    def finish_search(self,uid):
        self.store.finish_flow(uid,'search',5)

    async def search_send(self,uid,text,markup=None):
        result=await self.send(uid,text,markup,ttl=None)
        self.store.flow_messages(uid,'search',result['message_id'])
        # Abandoned results are temporary too; interactions extend their lifetime.
        for mid in self.store.flow_messages(uid,'search'):
            self.store.expire_message(uid,mid,time.time()+300)
        return result

    async def context_reply(self,uid,item_id,message,text,deleted=False):
        flow='context:'+item_id
        self.store.record_service(uid,message)
        self.store.flow_messages(uid,flow,message['message_id'])
        notice=self.store.meta(f'keyboard_notice_{uid}')
        if notice:self.store.flow_messages(uid,flow,int(notice))
        item=self.store.get(uid,item_id,raw=True,trash=True)
        if item:
            for mid in (item.get('bot_message'),item.get('tg_message') if deleted else None):
                if mid:self.store.flow_messages(uid,flow,mid)
        result=await self.send(uid,text)
        self.store.flow_messages(uid,flow,result['message_id'])
        self.store.finish_flow(uid,flow,5)
        self.finish_search(uid)
        self.store.meta(f'input_deadline_{uid}','')

    def wait_for_input(self,uid,flow):
        deadline=time.time()+300
        for mid in self.store.flow_messages(uid,flow):self.store.expire_message(uid,mid,deadline)
        self.store.meta(f'input_deadline_{uid}',str(deadline))

    def observe_own_message(self,uid,message):
        if not isinstance(message,dict):return
        sender=message.get('from') or {}
        chat=(message.get('chat') or {}).get('id',uid)
        prefix=self.config.token.split(':',1)[0]
        own=(sender.get('id')==int(prefix)) if prefix.isdigit() else sender.get('username','').lower()==self.config.bot_username.lower()
        if sender.get('is_bot') and own and chat==uid:self.store.record_outgoing(uid,message)

    def service_action(self,message):
        """Only standalone controls; forwarded messages and media captions are material."""
        if message.get('forward_origin') or message.get('forward_date') or message.get('is_automatic_forward'):return None
        if any(k in message for k in ('photo','document','video','video_note','animation','voice','audio','sticker','contact','location','venue','poll','dice')):return None
        text=message.get('text','').strip().replace('\ufe0f','')
        match=re.fullmatch(r'/(start|help|cancel|clear)(?:@'+re.escape(self.config.bot_username)+r')?(?:\s+(.+))?',text,re.I|re.S)
        if match:
            action=match[1].lower()
            return action if action=='start' or match[2] is None else None
        if text in ('🗂 Подборки','📁 Подборки','📂 Подборки'):return 'collections'
        if text in ('🔎 Найти','🔍 Найти'):return 'search'
        return None

    def observe_service_message(self,uid,message):
        if not isinstance(message,dict):return
        sender=message.get('from') or {};chat=message.get('chat') or {}
        if sender.get('id')!=uid or sender.get('is_bot') or chat.get('id',uid)!=uid or chat.get('type','private')!='private':return
        if self.service_action(message):self.store.record_service(uid,message)

    async def clear_chat(self,uid):
        async with self.clear_locks.setdefault(uid,asyncio.Lock()):
            rows=self.store.chat_messages(uid);cutoff=time.time()-48*3600
            recent=[r['message_id'] for r in rows if not r['created'] or r['created']>cutoff]
            expired=len(rows)-len(recent);processed=failed=0
            for start in range(0,len(recent),100):
                batch=recent[start:start+100]
                try:
                    await self.call('deleteMessages',chat_id=uid,message_ids=batch)
                    self.store.forget_chat_messages(uid,batch);processed+=len(batch)
                except TelegramError:
                    # One undeletable legacy message must not block the rest of the batch.
                    for mid in batch:
                        try:
                            await self.call('deleteMessage',chat_id=uid,message_id=mid)
                            self.store.forget_chat_messages(uid,[mid]);processed+=1
                        except TelegramError as e:
                            if 'not found' in str(e).lower():self.store.forget_chat_messages(uid,[mid]);processed+=1
                            else:failed+=1
            self.store.mode(uid,'')
            self.store.meta(f'last_clear_{uid}',json.dumps({'requested_at':time.time(),'processed':processed,'expired':expired,'failed':failed}))
            summary=f'Обработано служебных сообщений и ответов бота: {processed}.' if processed else 'В учёте нет служебных сообщений или ответов бота, которые удалось удалить.'
            summary+=' Фото, заметки, пояснения и сохранённые материалы остались.'
            if expired:summary+=f'\nСтарше 48 часов: {expired}.'
            if failed:summary+=f'\nTelegram не дал удалить: {failed}. Попробуй позже.'
            summary+='\nСтарые неучтённые команды, нажатия кнопок и ответы бота: ответь на нужное сообщение командой /clear. Уведомление исчезнет через 8 секунд.'
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
            self.store.meta(f'last_empty_search_{uid}',json.dumps({'query':query,'at':time.time()}))
            message=(data.get('warning','')+'\n' if data.get('warning') else '')+'В обычных материалах пока ничего не нашлось.'
            if self.store.vault(uid)['envelope'] and self.store.meta(f'vault_bot_{uid}')!='1':
                message+=' Если ищешь логин и пароль: выдача в Telegram выключена. Открой «Пароли» → «Включить выдачу в Telegram». Это можно сделать прямо на телефоне.'
                await self.search_send(uid,message,self.web_button('Открыть пароли','vault'))
            else:await self.search_send(uid,message+' Попробуй название, фразу из материала или другое описание.')
            self.finish_search(uid)
            return
        await self.search_send(uid,f'Нашлось: {data["total"]}. '+('Поиск по смыслу через Gemini.' if data['mode']=='gemini' else 'Поиск по словам и описанию.' if data['mode']=='hybrid' else 'Сейчас ищу по словам.')+('\n'+data['warning'] if data.get('warning') else ''))
        for i in data['items']:
            markup={'inline_keyboard':[[{'text':'Получить оригинал','callback_data':'get:'+i['id']},
                                       {'text':'Добавить контекст','callback_data':'ctx:'+i['id']}]]}
            await self.search_send(uid,f'{i["title"]}\n{" · ".join(i["groups"]) or "Неразобранное"}\n\n{i.get("snippet","")[:650]}',markup)
        if offset+4<data['total']:
            # Query state is per-user; don't put arbitrary query text in callback data.
            self.store.meta(f'query_{uid}',query)
            await self.search_send(uid,'Ещё результаты',{'inline_keyboard':[[{'text':'Показать ещё','callback_data':f'more:{offset+4}'}]]})

    async def vault_lookup(self,uid,text):
        if not self.store.vault(uid)['envelope']:return False
        explicit=bool(re.search(r'\b(?:данные|логин|парол\w*|аккаунт\w*)\b',text,re.I))
        known=self.store.known_vault_query(uid,credential_query(text))
        if self.store.meta(f'vault_bot_{uid}')!='1':
            if explicit or known:
                self.store.mode(uid,'')
                await self.send(uid,'Выдача паролей в Telegram выключена. Открой «Пароли» → «Включить выдачу в Telegram», введи главный пароль. Это работает и на телефоне.',self.web_button('Открыть пароли','vault'))
            return explicit or known
        try:entries=await self.vault_access.entries(uid)
        except VaultAccessError:
            if explicit or known:
                self.store.mode(uid,'')
                await self.send(uid,'Доступ к паролям закрыт. Открой «Пароли» и включи выдачу в Telegram заново.',self.web_button('Открыть пароли','vault'))
            return explicit or known
        matches=credential_matches(text,entries)
        if not matches:
            if explicit or known:
                self.store.mode(uid,'')
                await self.send(uid,'Не нашёл такой аккаунт. Укажи название сервиса из раздела «Пароли».')
            return explicit or known
        self.store.mode(uid,'')
        if len(matches)>1:
            await self.send(uid,'Нашёл несколько аккаунтов. Уточни полное название:\n'+'\n'.join(str(e.get('title',''))[:120] for e in matches[:8]))
            return True
        entry=matches[0]
        title=html.escape(str(entry.get('title',''))[:120])
        login=html.escape(str(entry.get('login',''))[:300])
        password=html.escape(str(entry.get('password',''))[:2000])
        result=await self.call('sendMessage',chat_id=uid,
            text=f'{title}\n<tg-spoiler>Логин: {login}\nПароль: {password}</tg-spoiler>\n\nУдалю это сообщение через 5 минут.',
            parse_mode='HTML',reply_markup=self.keyboard())
        self.store.record_outgoing(uid,result)
        self.store.expire_message(uid,result['message_id'],time.time()+300)
        return True

    async def expire_due(self):
        for row in self.store.due_messages(time.time()):
            uid,mid=row['user_id'],row['message_id']
            deadline=self.store.meta(f'input_deadline_{uid}')
            if deadline and float(deadline)<=time.time():
                mode=self.store.mode(uid)
                if mode=='search' or mode.startswith('context:'):
                    self.store.mode(uid,'');self.store.finish_flow(uid,mode,0)
                self.store.meta(f'input_deadline_{uid}','')
            try:
                await self.call('deleteMessage',chat_id=uid,message_id=mid)
                self.store.forget_chat_messages(uid,[mid])
            except TelegramError as error:
                if 'not found' in str(error).lower():self.store.forget_chat_messages(uid,[mid])
                else:self.store.retry_expiring(uid,mid,time.time()+30)

    async def expiry_loop(self):
        while True:
            try:await self.expire_due()
            except Exception:self.error='Автоудаление временно недоступно. Повторю попытку.'
            await asyncio.sleep(1)

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
            result=await self.call(method,chat_id=uid,reply_markup=self.keyboard(),**{param:item['tg_file_id']})
            self.store.record_outgoing(uid,result)
            self.store.link_message(uid,result['message_id'],item_id)
            return result
        if item['path']:
            try:
                with open(item['path'],'rb') as f:
                    r=await self.client.post(f'https://api.telegram.org/bot{self.config.token}/sendDocument',data={'chat_id':str(uid),'reply_markup':json.dumps(self.keyboard())},files={'document':(item['filename'],f,'application/octet-stream')})
                if not r.json().get('ok'): raise TelegramError('Не удалось отправить файл')
                self.store.record_outgoing(uid,r.json()['result'])
                self.store.link_message(uid,r.json()['result']['message_id'],item_id)
                return
            except httpx.HTTPError: raise TelegramError('Не удалось отправить файл') from None
        result=await self.send(uid,item['body'] or item['title'],ttl=None)
        self.store.link_message(uid,result['message_id'],item_id)

    async def callback(self, q):
        uid=q['from']['id']
        self.keyboard_ready.discard(uid)
        if not self.store.allowed(uid,self.config.allowed_ids):
            await self.call('answerCallbackQuery',callback_query_id=q['id'],text='Нет доступа')
            return
        self.observe_own_message(uid,q.get('message'))
        data=q.get('data',''); action,_,value=data.partition(':')
        await self.call('answerCallbackQuery',callback_query_id=q['id'])
        if action=='get':
            await self.send_original(uid,value)
            await self.dismiss_search_prompt(uid)
            self.finish_search(uid)
        elif action=='ctx' and self.store.get(uid,value):
            previous=self.store.mode(uid)
            if previous.startswith('context:') and previous!='context:'+value:self.store.finish_flow(uid,previous)
            self.store.mode(uid,'context:'+value)
            item=self.store.get(uid,value)
            mid=item.get('bot_message')
            if mid:
                self.store.hold_message(uid,mid)
                self.store.flow_messages(uid,'context:'+value,mid)
            result=await self.send(uid,'Напиши пояснение к этому материалу. /cancel — отмена.',ttl=None)
            self.store.flow_messages(uid,'context:'+value,result['message_id'])
            self.wait_for_input(uid,'context:'+value)
        elif action=='more':
            query=self.store.meta(f'query_{uid}')
            if query: await self.results(uid,query,int(value))

    async def message(self, m, edited=False):
        if m.get('chat',{}).get('type')!='private': return
        uid=m['from']['id']; text=m.get('text',m.get('caption',''))
        self.keyboard_ready.discard(uid)
        action=None if edited else self.service_action(m)
        if action=='start' and len(text.strip().split(None,1))==2:
            code=text.strip().split(None,1)[1].strip()
            if self.store.pair(uid,code):
                self.observe_service_message(uid,m)
                await self.configure_menu(uid)
                await self.send(uid,'Ты подключён к Изbrainому. Пересылай сюда важное. Чтобы найти — напиши «Найди…» или нажми кнопку поиска.')
                return
        if not self.store.allowed(uid,self.config.allowed_ids):
            if action=='start':
                await self.call('sendMessage',chat_id=uid,text='Изbrainое пока в закрытом тесте. Нужна личная ссылка-приглашение от владельца.')
            return
        self.observe_own_message(uid,m.get('reply_to_message'))
        if action:self.observe_service_message(uid,m)
        if action=='clear':
            self.finish_search(uid)
            mode=self.store.mode(uid)
            if mode.startswith('context:'):self.store.finish_flow(uid,mode)
            self.store.meta(f'input_deadline_{uid}','')
            self.observe_service_message(uid,m.get('reply_to_message'))
            await self.report_saved(uid)
            await self.clear_chat(uid);return
        if edited:
            old=self.store.find_message(uid,m['message_id'])
            if old and not sensitive(text):
                self.store.update(uid,old['id'],body=text)
                await self.processor.index(uid,old['id'])
            return
        if action in ('start','help'):
            self.store.expire_message(uid,m['message_id'],time.time()+8)
            await self.configure_menu(uid)
            await self.send(uid,'Пересылай сообщения, фото, PDF и голосовые. Я сохраню их.\n\n«Найди…» — поиск. Ответ на сохранённое сообщение — добавить контекст. Пояснения помогают пересматривать и похожие материалы.\n\nНапиши «удалить» в пояснении или ответом на нужный материал — запись попадёт в корзину. Восстановить можно в настройках. /cancel — отменить ввод.\n\n/clear — убрать ответы бота, команды /start, /help, /cancel, /clear и нажатия «Подборки»/«Найти» за последние 48 часов. Фото, заметки, пояснения и архив сохранятся. Старые неучтённые служебные сообщения можно убрать, ответив на них командой /clear.\n\nДля анализа PDF читаются первые 4 страницы, включая сканы. Пароли добавляй в защищённом разделе подборок.',self.web_button() or self.keyboard(),ttl=8)
            return
        if action=='cancel':
            mode=self.store.mode(uid)
            flow=mode if mode=='search' or mode.startswith('context:') else 'cancel'
            self.store.flow_messages(uid,flow,m['message_id'])
            result=await self.send(uid,'Отменено. Следующее сообщение сохраню как заметку.')
            self.store.flow_messages(uid,flow,result['message_id'])
            self.store.finish_flow(uid,flow,5)
            if flow.startswith('context:'):self.finish_search(uid)
            self.store.meta(f'search_prompt_{uid}','')
            self.store.meta(f'input_deadline_{uid}','')
            self.store.mode(uid,'');return
        if action=='search':
            previous=self.store.mode(uid)
            if previous=='search' or previous.startswith('context:'):self.store.finish_flow(uid,previous)
            self.finish_search(uid)
            self.store.mode(uid,'search')
            self.store.flow_messages(uid,'search',m['message_id'])
            result=await self.send(uid,'Что найти? Напиши название, фразу или описание. /cancel — отмена.',ttl=None)
            self.store.flow_messages(uid,'search',result['message_id'])
            self.wait_for_input(uid,'search')
            self.store.meta(f'search_prompt_{uid}',str(result['message_id']));return
        if action=='collections':
            self.store.expire_message(uid,m['message_id'],time.time()+8)
            if self.config.public_url: await self.send(uid,'Все материалы и подборки:',self.web_button(),ttl=8)
            else:
                groups={}
                for i in self.store.list(uid):
                    for g in i['groups'] or ['Неразобранное']: groups[g]=groups.get(g,0)+1
                await self.send(uid,'\n'.join(f'{g}: {n}' for g,n in groups.items())+'\n\nПодборки с просмотром файлов готовы на Mac. Для телефона нужно подключить HTTPS-адрес.' if groups else 'Пока пусто. Перешли первый материал.',ttl=8)
            return
        if (m.get('text') and not m.get('forward_origin') and not m.get('forward_date')
                and not any(k in m for k in ('photo','document','video','voice','audio','animation'))
                and not m.get('reply_to_message') and self.store.mode(uid) in ('','search')
                and await self.vault_lookup(uid,text)):
            self.store.record_service(uid,m)
            self.store.expire_message(uid,m['message_id'],time.time()+5)
            await self.dismiss_search_prompt(uid)
            self.finish_search(uid)
            return
        if m.get('text') and not m.get('forward_origin') and not m.get('forward_date') and not m.get('reply_to_message') and self.store.mode(uid) in ('','search') and not any(k in m for k in ('photo','document','voice','audio')):
            previous=json.loads(self.store.meta(f'last_empty_search_{uid}') or '{}')
            if previous.get('query','').strip().casefold()==text.strip().casefold() and time.time()-previous.get('at',0)<300:
                self.store.record_service(uid,m)
                self.store.flow_messages(uid,'search',m['message_id'])
                await self.results(uid,text)
                await self.dismiss_search_prompt(uid);return
        if any(k in m for k in ('video','video_note','animation')):
            await self.send(uid,'Видео пока не поддерживаются. Можно сохранить ссылку или текстовое пояснение.'); return
        if sensitive(text):
            await self.send(uid,'Похоже, здесь есть пароль или другой секрет. Я не добавил его в базу и поиск. Для паролей используй защищённый раздел в подборках. Исходное сообщение остаётся в Telegram.',self.web_button('Открыть защищённый раздел') or self.keyboard())
            return
        mode=self.store.mode(uid)
        deadline=self.store.meta(f'input_deadline_{uid}')
        if deadline and float(deadline)<=time.time():
            if mode=='search' or mode.startswith('context:'):self.store.finish_flow(uid,mode,0)
            self.store.mode(uid,'');self.store.meta(f'input_deadline_{uid}','');mode=''
        # Forwarded text remains a save, even if it starts with “Найди”.
        forwarded=bool(m.get('forward_origin'))
        if mode=='search' or (not forwarded and re.match(r'^найди\b',text,re.I)):
            if mode!='search':self.finish_search(uid)
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
            self.store.meta(f'input_deadline_{uid}','')
            self.store.record_service(uid,m)
            self.store.flow_messages(uid,'search',m['message_id'])
            await self.results(uid,re.sub(r'^найди\s*','',text,flags=re.I))
            await self.dismiss_search_prompt(uid);return
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
                    await self.context_reply(uid,context_item,m,'Запись перемещена в корзину. Восстановить можно в «Настройки → Корзина».',deleted=True);return
                self.store.update(uid,context_item,context=(item['context']+'\n'+text).strip()[:10000])
                await self.processor.feedback(uid,context_item)
                self.store.mode(uid,'')
                new=self.store.get(uid,context_item)
                await self.context_reply(uid,context_item,m,'Пояснение запомнил. Подборки: '+(' · '.join(new['groups']) or 'Неразобранное')+'\nПроверяю похожие материалы в твоём архиве.')
            else:
                self.store.mode(uid,''); await self.context_reply(uid,context_item,m,'Материал больше недоступен. Пояснение не сохранено.')
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
            confirmation=await self.send(uid,'Сохранено: '+(' · '.join(i['groups']) or 'Неразобранное')+('\nЕсть файл с таким же содержимым.' if duplicate else '')+('\n'+status if status else ''),markup,ttl=8)
            self.store.update(uid,i['id'],bot_message=confirmation['message_id'])

    async def handle_update(self,u):
        try:
            if 'message' in u:await self.message(u['message'])
            elif 'edited_message' in u:await self.message(u['edited_message'],True)
            elif 'callback_query' in u:await self.callback(u['callback_query'])
        except Exception:
            self.error='Не удалось обработать сообщение. Попробуйте ещё раз.'
            m=u.get('message') or u.get('edited_message')
            if m and self.store.allowed(m.get('from',{}).get('id'),self.config.allowed_ids):
                try:await self.send(m['from']['id'],self.error)
                except Exception:pass

    def parallel_media(self,u):
        m=u.get('message',{});uid=m.get('from',{}).get('id')
        text=m.get('text',m.get('caption',''))
        return bool(uid and (m.get('photo') or m.get('document')) and not m.get('reply_to_message')
                    and not self.store.mode(uid) and not text.startswith('/') and not re.match(r'^найди\b',text,re.I)
                    and not sensitive(text) and self.store.allowed(uid,self.config.allowed_ids))

    async def report_saved(self,uid):
        items=self.batch_saved.pop(uid,[])
        if not items:return
        groups=list(dict.fromkeys(g for i in items for g in (i['groups'] or ['Неразобранное'])))
        await self.send(uid,f'Сохранено материалов: {len(items)}.\nПодборки: '+', '.join(groups)+'\n\nЧтобы добавить пояснение, ответь на конкретное исходное сообщение.',self.web_button() or self.keyboard(),ttl=8)

    async def handle_updates(self,updates):
        """Parallelize independent saves, flush before every ordered interaction."""
        counts={}
        for u in updates:
            uid=u.get('message',{}).get('from',{}).get('id')
            if uid:counts[uid]=counts.get(uid,0)+1
        self.batch_users={uid for uid,n in counts.items() if n>1};self.batch_saved={}
        pending=[]
        async def media(u):
            async with self.media_limit:await self.handle_update(u)
        async def flush():
            if pending:
                await asyncio.gather(*(media(u) for u in pending));pending.clear()
        try:
            for u in updates:
                if self.parallel_media(u):pending.append(u)
                else:
                    await flush()
                    await self.handle_update(u)
            await flush()
            for uid in list(self.batch_saved):await self.report_saved(uid)
        finally:
            self.batch_users=set();self.batch_saved={}

    async def run(self):
        if not self.config.token:return
        offset=int(self.store.meta('update_offset') or 0)
        expiry=asyncio.create_task(self.expiry_loop());self.tasks.add(expiry);expiry.add_done_callback(self.tasks.discard)
        try:
            while True:
                try:
                    me=await self.call('getMe')
                    if me.get('username','').lower()!=self.config.bot_username.lower():
                        self.error='Токен принадлежит другому боту';return
                    webhook=await self.call('getWebhookInfo')
                    if webhook.get('url'):
                        self.error='У бота уже настроен webhook. Отключите его перед локальным запуском.';return
                    self.verified=True
                    await self.call('setMyCommands',commands=[{'command':'start','description':'Начать'},{'command':'help','description':'Как пользоваться'},{'command':'clear','description':'Убрать команды и ответы, сохранить архив'},{'command':'cancel','description':'Отменить ввод'}])
                    await self.refresh_menus()
                    self.migrate_chat_cleanup()
                    self.running=True;self.error=''
                    while True:
                        try:
                            updates=await self.call('getUpdates',offset=offset,timeout=25,allowed_updates=['message','edited_message','callback_query'])
                            self.running=True;self.error=''
                            await self.handle_updates(updates)
                            if updates:
                                offset=updates[-1]['update_id']+1;self.store.meta('update_offset',offset)
                        except TelegramError:
                            self.running=False
                            self.error='Нет соединения с Telegram или бот запущен в другом месте'
                            await asyncio.sleep(5)
                except TelegramError:
                    self.running=False;self.error='Не удалось подключиться к Telegram. Повторю попытку.'
                    await asyncio.sleep(5)
        finally:
            self.running=False

    async def close(self):
        tasks=list(self.tasks)
        for t in tasks:t.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        await self.client.aclose()
