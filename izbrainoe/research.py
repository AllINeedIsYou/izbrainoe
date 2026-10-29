"""Bounded public concept research. No original files or document text are uploaded."""
import asyncio
import ipaddress
import re
import socket
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlparse,quote

import httpx
from bs4 import BeautifulSoup
import snowballstemmer

STEM=snowballstemmer.stemmer('russian')
USER_AGENT='Izbrainoe/0.3 (https://github.com/AllINeedIsYou/izbrainoe; public educational reference)'


def safe_topic(value):
    if not isinstance(value, str) or not 3 <= len(value.strip()) <= 80:
        return ''
    value = ' '.join(value.split())
    if len(value.split()) > 8 or re.search(r'[^\w\s()\-.,]', value):
        return ''
    if re.search(r'\d{3}|https?|www|парол|password|api.?key|токен|телефон|паспорт|фио|адрес|номер договора', value, re.I):
        return ''
    if re.search(r'\b(?:ул|улица|проспект|квартира|фамилия|логин|email)\b',value,re.I):
        return ''
    if re.search(r'\b[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+', value):
        return ''
    if re.search(r'\b(?:Иванов|Петров|Сидоров)\b', value, re.I):
        return ''
    return value


def safe_public_url(value):
    try:
        p = urlparse(value)
        if p.scheme != 'https' or not p.hostname or p.username or p.password or p.port not in (None, 443):
            return False
        if '.' not in p.hostname or p.hostname.endswith(('.local', '.internal', '.localhost')):
            return False
        addresses = socket.getaddrinfo(p.hostname, 443, type=socket.SOCK_STREAM)
        return bool(addresses) and all(ipaddress.ip_address(a[4][0]).is_global for a in addresses)
    except (ValueError, OSError):
        return False


def read_source(url):
    if not safe_public_url(url):
        return ''
    try:
        with httpx.Client(timeout=3, trust_env=False, follow_redirects=False) as c:
            with c.stream('GET', url, headers={'User-Agent': USER_AGENT}) as r:
                if not r.is_success or 'text/html' not in r.headers.get('content-type', ''):
                    return ''
                data = bytearray()
                for block in r.iter_bytes():
                    data.extend(block)
                    if len(data) > 250000:
                        return ''
        soup = BeautifulSoup(bytes(data), 'html.parser')
        for node in soup(['script', 'style', 'nav', 'footer', 'header', 'form']):
            node.decompose()
        return ' '.join((soup.find('main') or soup.find('article') or soup).get_text(' ', strip=True).split())[:2200]
    except (httpx.HTTPError, ValueError):
        return ''


class PublicResearch:
    def __init__(self, config, store):
        self.config, self.store = config, store
        self.lock = asyncio.Semaphore(2)

    def search(self, topic):
        query = topic + ' что это назначение отличительные признаки'
        wiki_sources=[]
        try:
            r=httpx.get('https://ru.wikipedia.org/w/api.php',params={'action':'query','list':'search','srsearch':topic,'srlimit':2,'format':'json'},
                        headers={'User-Agent':USER_AGENT},timeout=3,trust_env=False)
            r.raise_for_status();entries=r.json().get('query',{}).get('search',[])
            if entries:
                r=httpx.get('https://ru.wikipedia.org/w/api.php',params={'action':'query','prop':'extracts|extlinks','exintro':1,'explaintext':1,
                            'exchars':2200,'ellimit':5,'pageids':'|'.join(str(int(e['pageid'])) for e in entries),'format':'json'},
                            headers={'User-Agent':USER_AGENT},timeout=3,trust_env=False)
                r.raise_for_status();pages=r.json().get('query',{}).get('pages',{})
                for entry in entries:
                    page=pages.get(str(entry['pageid']),{});text=page.get('extract','')[:2200]
                    if text:
                        wiki_sources.append(dict(title=entry['title'],url='https://ru.wikipedia.org/wiki/'+quote(entry['title'].replace(' ','_')),
                                                 snippet=BeautifulSoup(entry.get('snippet',''),'html.parser').get_text(' ',strip=True)[:500],extract=text))
                if wiki_sources:
                    # Prefer a cited external source to a second encyclopedia page.
                    attempts=0
                    for page in pages.values():
                        for link in page.get('extlinks',[]):
                            if attempts>=2:break
                            url=link.get('*','')
                            if not safe_public_url(url) or any(x in urlparse(url).hostname for x in ('wikipedia','wikimedia','archive.org')):continue
                            attempts+=1
                            text=read_source(url)
                            if text:
                                wiki_sources=[wiki_sources[0],dict(title='Источник из справочной статьи',url=url,snippet='',extract=text)]
                                break
                        if len(wiki_sources)>1 and urlparse(wiki_sources[-1]['url']).hostname!='ru.wikipedia.org':break
                    return dict(status='checked',query=topic,sources=wiki_sources[:2],engine='wikipedia',
                                note='Прочитаны общие справочные материалы и доступные источники; личный файл не загружался.')
        except (httpx.HTTPError,ValueError,KeyError,TypeError):pass
        # RSS is much faster and more predictable than scraping a search UI.
        try:
            r=httpx.get('https://www.bing.com/search',params={'q':query,'format':'rss'},timeout=4,trust_env=False,
                        headers={'User-Agent':USER_AGENT})
            r.raise_for_status()
            if len(r.content)>250000:raise ValueError('oversized search response')
            root=ET.fromstring(r.content)
            results=[dict(title=i.findtext('title',''),href=i.findtext('link',''),body=i.findtext('description',''))
                     for i in root.findall('./channel/item')[:10]]
        except (httpx.HTTPError,ET.ParseError,ValueError):
            from ddgs import DDGS
            results=DDGS(timeout=4).text(query,region='ru-ru',max_results=4,backend=self.config.web_backend)
        words=lambda text:{STEM.stemWord(w) for w in re.findall(r'[\w-]+',text.lower()) if len(w)>3}
        terms=words(topic)-{'форма','таблица'}
        results=sorted(results,key=lambda r:len(terms & words(r.get('title','')+' '+r.get('body',''))),reverse=True)
        sources, domains = [], set()
        for row in results:
            matches=terms & words(row.get('title','')+' '+row.get('body',''))
            if terms and len(matches)<min(2,len(terms)):
                continue
            url = row.get('href', '')
            if not safe_public_url(url):
                continue
            domain = urlparse(url).hostname.removeprefix('www.')
            if domain in domains:
                continue
            domains.add(domain)
            snippet = BeautifulSoup(row.get('body', ''), 'html.parser').get_text(' ', strip=True)[:600]
            sources.append(dict(title=row.get('title', '')[:180], url=url, snippet=snippet))
            if len(sources) == 2:
                break
        # Fetch at most two public pages, concurrently, never linked follow-up URLs.
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            extracts = list(pool.map(read_source, [s['url'] for s in sources]))
        for source, text in zip(sources, extracts):
            source['extract'] = text
        status='checked' if sources and all(s['extract'] for s in sources) else 'partial' if sources else 'no_results'
        return dict(status=status, query=query, sources=sources,
                    note='Сопоставлены общие признаки; личный материал в поиск не загружался.')

    async def verify(self, topics):
        if not self.config.web_lookup:
            return dict(status='disabled', sources=[])
        safe = list(dict.fromkeys(t for v in topics[:2] if (t := safe_topic(v))))
        if not safe:
            return dict(status='no_safe_query', sources=[])
        # One focused query; a second term refines the same query, not another crawl.
        topic = ' '.join(safe)[:100]
        cache_key = 'research-v1:' + topic
        with self.store.db() as c:
            row = c.execute('SELECT result FROM web_cache WHERE query=? AND created>?', (cache_key, time.time()-86400)).fetchone()
        if row:
            import json
            return {**json.loads(row[0]), 'cached': True}
        async with self.lock:
            try:
                result = await asyncio.wait_for(asyncio.to_thread(self.search, topic), 10)
            except (Exception, TimeoutError):
                return dict(status='unavailable', sources=[], query=topic)
        if result['status'] in ('checked','partial'):
            import json
            with self.store.db() as c:
                c.execute('INSERT OR REPLACE INTO web_cache VALUES(?,?,?)', (cache_key, json.dumps(result, ensure_ascii=False), time.time()))
        return result
