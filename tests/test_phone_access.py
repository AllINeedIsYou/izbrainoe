import asyncio
from types import SimpleNamespace
import httpx
import pytest

from izbrainoe.bot import Bot,TelegramError
from izbrainoe.config import Config
from izbrainoe.phone_access import PhoneAccess
from izbrainoe.processing import Processor
from izbrainoe.store import Store


def manager(tmp_path):
    cfg=Config(data_dir=tmp_path,token='TEST_ONLY_TOKEN')
    async def menus():pass
    bot=SimpleNamespace(verified=True,refresh_menus=menus)
    return PhoneAccess(cfg,bot,'test-instance',managed=True)


def test_health_checks_actual_instance_not_only_status_200(tmp_path):
    async def run():
        phone=manager(tmp_path)
        for payload,status,expected in (({'ok':True,'instance':'test-instance'},200,True),
                                        ({'ok':True,'instance':'another-server'},200,False),
                                        ({'ok':True,'instance':'test-instance'},503,False)):
            def response(request):return httpx.Response(status,json=payload)
            phone.sync_client_factory=lambda:httpx.Client(transport=httpx.MockTransport(response))
            assert await phone.probe('https://test.example') is expected
    asyncio.run(run())


def test_expired_cloudflare_tunnel_is_detected(tmp_path):
    phone=manager(tmp_path)
    phone.log_path.write_text('ERR Unauthorized: Tunnel not found')
    assert phone.fatal_tunnel_error()
    phone.log_path.write_text('INF Registered tunnel connection')
    assert not phone.fatal_tunnel_error()


def test_dns_fallback_preserves_tls_hostname_and_rejects_private_addresses(tmp_path):
    async def run():
        phone=manager(tmp_path);calls=[]
        def response(request):
            calls.append(request)
            assert 'cookie' not in request.headers
            if request.url.host=='fresh.trycloudflare.com':raise httpx.ConnectError('Broken DNS')
            if request.url.host in ('cloudflare-dns.com','1.1.1.1'):
                return httpx.Response(200,json={'Answer':[{'type':1,'data':'127.0.0.1'},{'type':1,'data':'104.16.230.132'}]})
            assert request.url.host=='104.16.230.132'
            assert request.headers['Host']=='fresh.trycloudflare.com'
            assert request.extensions['sni_hostname']=='fresh.trycloudflare.com'
            return httpx.Response(200,json={'ok':True,'instance':'test-instance'})
        phone.sync_client_factory=lambda:httpx.Client(transport=httpx.MockTransport(response))
        assert await phone.probe('https://fresh.trycloudflare.com')
        assert phone.dns_fallback
        assert await phone.probe('https://fresh.trycloudflare.com')
        assert sum(r.url.host in ('cloudflare-dns.com','1.1.1.1') for r in calls)==1
    asyncio.run(run())


def test_empty_dns_answer_is_not_cached_during_domain_warmup(tmp_path):
    async def run():
        phone=manager(tmp_path);queries=[]
        def response(request):
            if request.url.host=='fresh.trycloudflare.com':raise httpx.ConnectError('DNS warming up')
            if request.url.host=='1.1.1.1':
                queries.append(request)
                answers=[] if len(queries)==1 else [{'type':1,'data':'104.16.230.132'}]
                return httpx.Response(200,json={'Answer':answers})
            return httpx.Response(200,json={'ok':True,'instance':'test-instance'})
        phone.sync_client_factory=lambda:httpx.Client(transport=httpx.MockTransport(response))
        assert not await phone.probe('https://fresh.trycloudflare.com')
        assert 'fresh.trycloudflare.com' not in phone.dns_cache
        assert await phone.probe('https://fresh.trycloudflare.com')
        assert len(queries)==2
    asyncio.run(run())


def test_dead_public_url_recovers_and_republishes_menu(tmp_path):
    async def run():
        phone=manager(tmp_path);launched=[];published=[];recovered=[]
        class Process:
            def poll(self):return None
        async def launch():
            url='https://old.example' if not launched else 'https://new.example'
            launched.append(url);phone.process=Process();return url
        def stop():phone.process=None
        old_checks=iter([True,False,False])
        async def probe(url):return next(old_checks) if url=='https://old.example' else True
        async def publish():published.append(phone.config.public_url)
        async def pause(seconds):
            if phone.config.public_url=='https://new.example' and phone.ready:
                recovered.append(phone.status());raise asyncio.CancelledError()
        phone.launch=launch;phone.stop_process=stop;phone.probe=probe;phone.pause=pause
        phone.bot.refresh_menus=publish
        with pytest.raises(asyncio.CancelledError):await phone.run()
        assert launched==['https://old.example','https://new.example']
        assert published==launched and recovered[0]['ready']
    asyncio.run(run())


def test_menu_failure_never_reports_phone_ready(tmp_path):
    async def run():
        phone=manager(tmp_path)
        async def unavailable():raise TelegramError('Connection unavailable')
        phone.bot.refresh_menus=unavailable
        assert not await phone.publish('https://new.example')
        assert not phone.ready and phone.error
        phone.ready=True;phone.request_restart()
        assert not phone.ready and phone.restart.is_set()
    asyncio.run(run())


def test_bot_retries_startup_and_clears_stale_transport_error(tmp_path,monkeypatch):
    async def run():
        cfg=Config(data_dir=tmp_path,token='TEST_ONLY_TOKEN');store=Store(tmp_path);p=Processor(cfg,store);b=Bot(cfg,store,p)
        starts=polls=0
        async def no_delay(seconds):assert seconds==5
        monkeypatch.setattr('izbrainoe.bot.asyncio.sleep',no_delay)
        async def call(method,**kwargs):
            nonlocal starts,polls
            if method=='getMe':
                starts+=1
                if starts==1:raise TelegramError('Temporary startup failure')
                return {'username':cfg.bot_username}
            if method=='getWebhookInfo':return {'url':''}
            if method=='getUpdates':
                polls+=1
                if polls==1:raise TelegramError('Connection interrupted')
                if polls==2:
                    assert not b.running and b.error
                    return []
                assert b.running and b.error==''
                raise asyncio.CancelledError()
            return True
        b.call=call
        try:
            with pytest.raises(asyncio.CancelledError):await b.run()
            assert starts==2 and polls==3
        finally:await b.close();await p.close()
    asyncio.run(run())
