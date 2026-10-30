"""Supervise temporary phone access and publish only a verified live address."""
import asyncio
import ipaddress
import re
import subprocess
import time
from urllib.parse import urlsplit
from .config import ROOT
import httpx


class PhoneAccess:
    def __init__(self, config, bot, instance, managed=False):
        self.config=config;self.bot=bot;self.instance=instance
        self.enabled=bool(managed and config.token and (config.enable_tunnel or config.public_url))
        self.fixed_url=config.public_url
        self.temporary=not bool(self.fixed_url)
        self.process=None;self.client=None;self.restart=asyncio.Event()
        self.state='connecting' if self.enabled else 'disabled'
        self.ready=False;self.error='';self.checked=0;self.published=''
        self.log_path=config.data_dir/'tunnel.log'
        self.dns_cache={};self.dns_fallback=False
        self.probe_error=''
        self.sync_client_factory=lambda:httpx.Client(timeout=5,trust_env=False,follow_redirects=False)

    def status(self):
        return {'required':self.enabled,'ready':self.ready,'state':self.state,'error':self.error,
                'checked_at':self.checked,'temporary':self.temporary,'url':self.config.public_url,
                'dns_fallback':self.dns_fallback,'last_probe_error':self.probe_error}

    def request_restart(self):
        if self.enabled:
            self.ready=False;self.state='recovering';self.error='Переподключаю доступ с телефона.'
            self.restart.set()

    async def pause(self, seconds):
        try:await asyncio.wait_for(self.restart.wait(),seconds)
        except TimeoutError:pass

    def fatal_tunnel_error(self):
        if not self.temporary:return False
        try:text=self.log_path.read_text(errors='replace')[-12000:]
        except OSError:return False
        return 'Unauthorized: Tunnel not found' in text

    def stop_process(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.terminate()
                try:self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=5)
            self.process=None

    async def launch(self):
        await asyncio.to_thread(self.stop_process)
        binary=ROOT/'runtime/cloudflared'
        if not binary.exists():raise RuntimeError('Не установлен компонент доступа с телефона.')
        self.config.data_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
        with self.log_path.open('w') as out:
            self.process=subprocess.Popen([str(binary),'tunnel','--protocol','http2','--url',
                f'http://127.0.0.1:{self.config.port}','--no-autoupdate'],stdin=subprocess.DEVNULL,stdout=out,stderr=out)
        deadline=time.monotonic()+45
        while time.monotonic()<deadline and not self.restart.is_set():
            if self.process.poll() is not None or self.fatal_tunnel_error():break
            text=self.log_path.read_text(errors='replace')
            match=re.search(r'https://[a-z0-9-]+\.trycloudflare\.com',text)
            if match and 'Registered tunnel connection' in text:return match[0]
            await self.pause(.5)
        raise RuntimeError('Не удалось создать внешний адрес. Повторю подключение автоматически.')

    async def probe(self, url):
        # Dedicated short-lived clients isolate DNS/TLS state from a previous
        # temporary hostname. Blocking network I/O runs off the application loop.
        return await asyncio.to_thread(self.probe_sync,url)

    def probe_sync(self, url):
        self.checked=time.time();self.dns_fallback=False;self.probe_error=''
        def valid(response):
            if response.status_code!=200:self.probe_error=f'HTTP {response.status_code}';return False
            value=response.json()
            correct=value.get('ok') is True and value.get('instance')==self.instance
            if not correct:self.probe_error='Unexpected application instance'
            return correct
        host=urlsplit(url).hostname or ''
        with self.sync_client_factory() as client:
            try:return valid(client.get(url.rstrip('/')+'/health'))
            except (httpx.ConnectError,httpx.TimeoutException) as error:self.probe_error='direct: '+type(error).__name__
            except (httpx.HTTPError,ValueError,AttributeError):return False
            if not self.temporary or not re.fullmatch(r'[a-z0-9-]+\.trycloudflare\.com',host):return False
            try:
                cached=self.dns_cache.get(host)
                if cached and cached[1] and cached[0]>time.monotonic():addresses=cached[1]
                else:
                    answer=None
                    for resolver in ('https://1.1.1.1/dns-query','https://cloudflare-dns.com/dns-query'):
                        try:
                            answer=client.get(resolver,params={'name':host,'type':'A'},headers={'Accept':'application/dns-json'})
                            answer.raise_for_status();break
                        except httpx.HTTPError as error:self.probe_error='dns: '+type(error).__name__
                    if answer is None:return False
                    addresses=[]
                    for record in answer.json().get('Answer',[]):
                        if record.get('type')!=1:continue
                        address=str(ipaddress.ip_address(record.get('data','')))
                        if ipaddress.ip_address(address).is_global:addresses.append(address)
                    addresses=list(dict.fromkeys(addresses))[:2]
                    # Quick Tunnel DNS may appear a few seconds after creation.
                    # Never cache an empty answer and pin a working tunnel down.
                    if addresses:self.dns_cache={host:(time.monotonic()+300,addresses)}
                    else:self.dns_cache.pop(host,None)
                # Preserve certificate validation for the original hostname.
                # Never share an IP-keyed TLS connection across temporary names.
                with self.sync_client_factory() as edge:
                    for address in addresses:
                        try:
                            response=edge.get('https://'+address+'/health',headers={'Host':host},extensions={'sni_hostname':host})
                            if valid(response):self.dns_fallback=True;self.probe_error='';return True
                        except (httpx.HTTPError,ValueError,AttributeError) as error:self.probe_error='edge: '+type(error).__name__
            except (httpx.HTTPError,ValueError,TypeError,AttributeError) as error:self.probe_error='probe: '+type(error).__name__
        return False

    async def publish(self, url):
        if not self.bot.verified:return False
        self.config.public_url=url
        try:await self.bot.refresh_menus()
        except Exception:
            self.error='Адрес доступен, но Telegram пока не обновил кнопку. Повторю попытку.'
            return False
        self.published=url
        return True

    async def monitor(self, url):
        deadline=time.monotonic()+120;failures=0;was_ready=False
        while not self.restart.is_set():
            if self.temporary and (self.process.poll() is not None or self.fatal_tunnel_error()):
                raise RuntimeError('Временный адрес перестал работать. Создаю новый.')
            reachable=await self.probe(url)
            if reachable:
                failures=0
                published=self.published==url or await self.publish(url)
                self.ready=bool(published)
                self.state='ready' if published else 'publishing'
                if published:self.error='';was_ready=True
            else:
                failures+=1;self.ready=False;self.state='recovering'
                self.error='Подборки на телефоне недоступны. Восстанавливаю подключение.'
                if (was_ready and failures>=2) or (not was_ready and time.monotonic()>=deadline):
                    raise RuntimeError(self.error)
            await self.pause(15 if self.ready else 2)

    async def run(self):
        if not self.enabled:return
        backoff=3
        try:
            async with httpx.AsyncClient(timeout=6,trust_env=False,follow_redirects=False) as self.client:
                while True:
                    self.restart.clear();self.ready=False;self.published='';self.state='connecting'
                    if self.temporary:self.config.public_url=''
                    try:
                        url=await self.launch() if self.temporary else self.fixed_url
                        await self.monitor(url)
                        backoff=3
                    except (RuntimeError,OSError,subprocess.SubprocessError):
                        self.ready=False;self.state='recovering'
                        self.error='Нет доступа к подборкам с телефона. Повторяю подключение автоматически.'
                        await self.pause(backoff);backoff=min(30,backoff*2)
                    finally:
                        if self.temporary:await asyncio.to_thread(self.stop_process)
        finally:
            self.ready=False;self.state='stopped'
            await asyncio.to_thread(self.stop_process)

    async def close(self):
        await asyncio.to_thread(self.stop_process)
