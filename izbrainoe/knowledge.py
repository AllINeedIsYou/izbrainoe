"""Grounded personal classification and privacy-preserving public topic checks."""
import asyncio
import hashlib
import json
import re
import time
import httpx
from .gemini import Gemini,GeminiError
from .provod import Provod
from .analysis_policy import Analysis,SYSTEM,REVIEW,review_reasons
from .research import PublicResearch

TYPE_LABELS={'essay':'Сочинения','сочинение':'Сочинения','сочинения':'Сочинения',
             'задачи':'Задачи','задача':'Задачи','конспект':'Конспекты','лекция':'Лекции',
             'договор':'Договоры','расписание':'Расписание','квитанция':'Оплата',
             'песня':'Музыка / Песни','стихотворение':'Стихи','приложение':'Приложения',
             'рецепт':'Рецепты','код':'Программирование','инструкция':'Инструкции','билет':'Поездки / Билеты'}
SUBJECTS={'русский язык':'Русский язык','русский':'Русский язык','математика':'Математика',
          'информатика':'Информатика','литература':'Литература','история':'История',
          'физика':'Физика','химия':'Химия','английский язык':'Английский язык',
          'обществознание':'Обществознание','биология':'Биология','геометрия':'Геометрия'}

DOMAIN_SIGNALS={
 'Геометрия':(r'треугольник',r'биссектрис',r'медиан',r'теорем[аы]\s+(?:синусов|косинусов|пифагора)',r'сумма\s+углов',r'параллелограмм'),
 'Математика':(r'производн',r'интеграл',r'логарифм',r'квадратн\w*\s+уравнен',r'тригонометр',r'таблиц\w*\s+(?:квадратов|степеней)'),
 'Информатика':(r'алгоритм',r'программирован',r'информационн\w*\s+безопасност',r'python',r'баз[аы]\s+данных'),
 'Физика':(r'закон\w*\s+(?:ньютона|ома|кулона)',r'электрическ\w*\s+(?:ток|пол)',r'кинетическ\w*\s+энерг'),
 'Химия':(r'химическ\w*\s+реакц',r'валентност',r'молярн\w*\s+масс'),
}

def inferred_subject(text):
    t=text.lower().replace('ё','е')
    scores=[(sum(bool(re.search(p,t)) for p in patterns),subject) for subject,patterns in DOMAIN_SIGNALS.items()]
    score,subject=max(scores)
    return subject if score>=2 else ''

def looks_like_schedule(text):
    t=text.lower().replace('ё','е')
    times=len(re.findall(r'\b(?:[01]?\d|2[0-3])[:.][0-5]\d\b',t))
    school_terms=bool(re.search(r'урок|перемен|начало|окончание|звонк|пара\b|заняти',t))
    if times>=3 and school_terms:return True
    subjects=sum(bool(re.search(p,t)) for p in ('истори','обществознан','английск','геометри','алгебр','литератур','биологи','хими','информатик','географ','физик','русск','обзр'))
    classrooms=len(re.findall(r'(?:№|n[ºo°])\s*\d+',t))
    lesson_numbers=len(re.findall(r'(?m)^\s*[1-9]\s*$',t))
    education=bool(re.search(r'очно.?заоч|форм\w*\s+обуч|каб|класс|урок',t))
    days=sum(bool(re.search(p,t)) for p in ('понедельник','вторник','среда','четверг','пятница','суббота'))
    return subjects>=3 and ((education and (classrooms>=2 or lesson_numbers>=3)) or days>=2) or days>=2 and times>=2

def supported_type(kind,text):
    t=text.lower().replace('ё','е')
    if kind=='расписание' and looks_like_schedule(t):return True
    strict={
      'договор':(r'договор|соглашение',r'обязуется.*сторон|стороны.*заключили'),
      'квитанция':(r'квитанц|чек|оплат|платеж',),
      'расписание':(r'расписан|понедельник|вторник|среда|четверг|пятница',),
      'сочинение':(r'сочинен|эссе|essay|позици[яю] автора|авторск\w*\s+позици|на мой взгляд|по моему мнению',),
      'задачи':(r'задач|задани|решит[ье]|вычисли|найдите|найти\s+(?:площадь|производную|корн)',),
    }
    return kind not in strict or any(re.search(p,t,re.S) for p in strict[kind])

def grounded_features(text,context=''):
    t=(context+'\n'+text).lower().replace('ё','е')
    subject='';kind=''
    # The user's explicit explanation is the strongest evidence.
    for source in (context.lower(),text.lower()):
        for key,value in SUBJECTS.items():
            if key=='история' and not re.search(r'(?:урок|предмет|по|учебник|конспект)\s+истори|^история\s*$',source):continue
            if re.search(r'\b'+re.escape(key)+r'\b',source):subject=value;break
        if subject:break
    if looks_like_schedule(t):kind='расписание'
    elif re.search(r'сочинен|эссе|essay',t):kind='сочинение'
    elif re.search(r'авторск\w*\s+позици|позици[яю]\s+автора',t) and re.search(r'вопрос|проблем|рассужд|почему',t) and re.search(r'пример|аргумент|таким образом',t):
        kind='сочинение'
    elif sum(bool(re.search(p,t)) for p in ('позици[яю] автора','автор.*проблем','я согласен','таким образом','привед[еу].*пример'))>=3:
        kind='сочинение'
    elif sum(bool(re.search(p,t)) for p in (r'\?',r'на мой взгляд',r'по моему мнению',r'следовательно',r'это подтверждает',r'таким образом',r'в заключение'))>=3:
        kind='сочинение'
    elif re.search(r'договор|соглашение',t):kind='договор'
    elif re.search(r'расписание',t):kind='расписание'
    elif re.search(r'квитанц|чек об оплате',t):kind='квитанция'
    elif re.search(r'конспект',t):kind='конспект'
    elif re.search(r'лекци',context.lower()) or re.search(r'\bлекция(?:\s+\d|\s+по\b|\s*:)',text[:500].lower()):kind='лекция'
    elif re.search(r'задач[аи]|задани[ея]',t):kind='задачи'
    elif re.search(r'куплет|припев',t) and re.search(r'темп|аккорд|\b(?:am|dm|em|gsus2)\b',t):kind='песня'
    elif re.search(r'\.apk\b|приложени[ея]\s+для\s+android|android\s+приложен',t):kind='приложение'
    # Common inflected school subject names.
    if not subject:
        for pattern,label in [(r'русск\w*\s+язык','Русский язык'),(r'математик','Математика'),(r'информатик','Информатика'),(r'литератур','Литература')]:
            if re.search(pattern,t):subject=label;break
    subject=subject or inferred_subject(t)
    if kind=='расписание':subject=''
    return kind,subject

def canonical_groups(kind,subject,proposed,fallback):
    name=TYPE_LABELS.get(kind.lower())
    normalized=SUBJECTS.get(subject.lower(),subject.strip())
    if name and normalized:return [normalized+' / '+name]
    if name:return list(dict.fromkeys([*fallback,name]))[:3] if fallback and kind not in ('сочинение','essay','сочинения') else [name]
    if normalized in SUBJECTS.values():return [normalized]
    return list(dict.fromkeys(proposed or fallback))[:3]

# Generic fallback concepts; proposed public terms also pass the research privacy filter.
PUBLIC_QUERIES={
 'сочинение':'сочинение рассуждение русский язык структура признаки',
 'essay':'сочинение рассуждение русский язык структура признаки',
 'конспект':'учебный конспект лекции структура признаки',
 'лекция':'учебный конспект лекции структура признаки',
 'договор':'договор на обучение содержание реквизиты',
 'квитанция':'квитанция об оплате содержание реквизиты',
 'расписание':'расписание учебных занятий структура',
 'задачи':'учебные задачи задания решения признаки',
}
class PersonalAnalyzer:
    def __init__(self,config,store,embed,cosine):
        self.config=config;self.store=store;self.embed=embed;self.cosine=cosine;self.chat_lock=asyncio.Semaphore(1)
        self.gemini=Gemini(config,store)
        self.provod=Provod(config,store)
        self.research=PublicResearch(config,store)
        self.cache_locks={}

    async def analyse(self,item,fallback,vector=None,force=False):
        start=time.monotonic()
        cloud=self.provod if self.config.ai_provider=='provod' else self.gemini
        is_file=bool(item.get('path'))
        cacheable=cloud.enabled and (bool(item.get('digest')) if is_file else len(item.get('body',''))>=35)
        if not cacheable:
            result=await self._analyse(item,fallback,vector)
            result['elapsed_ms']=round((time.monotonic()-start)*1000)
            return result
        memories=self.store.memories(item['user_id'])
        fingerprint={'policy':'vision-v2','provider':self.config.ai_provider,'model':self.config.gemini_model,
                     'web':self.config.web_lookup,'digest':item.get('digest',''),'kind':item['kind'],
                     'title':item['title'] if not is_file else '', 'body':item['body'],'context':item['context'],
                     'groups':item['groups_json'] if item['manual_groups'] else '',
                     'memories':[(m['item_id'],m['updated'],m['context'],m['groups_json']) for m in memories]}
        key=hashlib.sha256(json.dumps(fingerprint,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        async with self.cache_locks.setdefault((item['user_id'],key),asyncio.Lock()):
            with self.store.db() as c:
                row=c.execute('SELECT result FROM analysis_cache WHERE user_id=? AND cache_key=? AND created>?',
                              (item['user_id'],key,time.time()-7*86400)).fetchone()
            if row and not force:
                result=json.loads(row[0]);result['cloud'].update(cached=True,usage=None)
            else:
                result=await self._analyse(item,fallback,vector)
                if result['method']=='gemini' and result['confidence']>=.85 and not result.get('needs_context'):
                    with self.store.db() as c:
                        c.execute('INSERT OR REPLACE INTO analysis_cache VALUES(?,?,?,?)',
                                  (item['user_id'],key,json.dumps(result,ensure_ascii=False),time.time()))
                        c.execute('DELETE FROM analysis_cache WHERE created<?',(time.time()-7*86400,))
            result['elapsed_ms']=round((time.monotonic()-start)*1000)
            return result

    async def _analyse(self,item,fallback,vector=None):
        cloud=self.provod if self.config.ai_provider=='provod' else self.gemini
        text=item['title']+'\n'+item['body'];context=item['context']
        kind,subject=grounded_features(text,context)
        memories=self.store.memories(item['user_id'])
        scored=[]
        for m in memories:
            if m['item_id']==item['id']:continue
            similarity=self.cosine(vector,json.loads(m['embedding'])) if vector and m['embedding'] and m['model']==self.config.embed_model else 0
            mk,ms=grounded_features(m['body'],m['context'])
            lexical=(kind and kind==mk) or (subject and subject==ms)
            if similarity>=.30 or lexical:scored.append((similarity,m))
        scored.sort(key=lambda x:x[0],reverse=True)
        examples=[{'material':m['body'][:320] or m['title'],'explanation':m['context'][:320],
                   'groups':json.loads(m['groups_json'])} for _,m in scored[:2]]
        result=Analysis(document_type=kind,subject=subject,groups=canonical_groups(kind,subject,[],fallback),
                        summary='',confidence=.8 if kind and subject else .55 if fallback else .2)
        method='local_rules'
        reference={'status':'not_needed','sources':[]}
        # A bare opaque filename does not contain content to reason about.
        enough=len((item['body']+' '+context).strip())>=35 or bool(context) or (cloud.enabled and bool(item.get('path')) and (item['kind']=='photo' or item.get('mime','').startswith('image/') or item.get('filename','').lower().endswith('.pdf')))
        cloud_meta={'status':'disabled'}
        cloud_done=False
        if enough:
            content='\n'.join(part[:1400] for part in re.split(r'\[Страница \d+\]',item['body'])[:5])[:6500]
            payload={'filename_or_title':item['title'],'content':content,'user_explanation':context[:1600],
                     'personal_examples':examples,'current_manual_groups':json.loads(item['groups_json']) if item['manual_groups'] else [],
                     'public_type_reference':reference.get('sources',[])[:2]}
            system=SYSTEM
            if cloud.enabled:
                try:
                    deadline=time.monotonic()+40
                    raw,cloud_meta=await asyncio.wait_for(cloud.analyse(item,payload,system,Analysis.model_json_schema()),30)
                    candidate=Analysis.model_validate(raw)
                    normalize=lambda value:re.sub(r'\s+',' ',value.lower()).strip()
                    source=normalize(item['body']+' '+context)
                    quoted=[q for q in candidate.evidence if 3<=len(q)<=500 and (cloud_meta['images'] or normalize(q) in source)]
                    candidate.evidence=quoted
                    reasons=review_reasons(candidate,kind,cloud_meta.get('images',0),cloud_meta.get('can_zoom',False))
                    cloud_meta.update(passes=1,reviewed=False,cached=False)
                    if reasons:
                        if not candidate.needs_context or candidate.needs_web:
                            topics=candidate.research_topics or [PUBLIC_QUERIES.get(candidate.document_type,candidate.document_type),*candidate.alternatives]
                            reference=await self.research.verify(topics)
                        remaining=deadline-time.monotonic()
                        if remaining>3:
                            try:
                                review_payload={**payload,'_analysis_stage':'review','previous_analysis':candidate.model_dump(),
                                                'review_reasons':reasons,'public_type_reference':reference.get('sources',[])[:2]}
                                second,meta2=await asyncio.wait_for(cloud.analyse(item,review_payload,system+'\n'+REVIEW,Analysis.model_json_schema()),remaining)
                                checked=Analysis.model_validate(second)
                                checked.evidence=[q for q in checked.evidence if 3<=len(q)<=500 and (meta2.get('images') or normalize(q) in source)]
                                if checked.evidence or checked.needs_context:
                                    candidate=checked
                                else:
                                    candidate.needs_context=True;candidate.confidence=min(candidate.confidence,.6)
                                usage={}
                                for meta in (cloud_meta,meta2):
                                    for k,v in (meta.get('usage') or {}).items():
                                        if isinstance(v,(int,float)):usage[k]=usage.get(k,0)+v
                                cloud_meta.update(passes=2,reviewed=True,review_reasons=reasons,usage=usage or None)
                            except (GeminiError,ValueError,TimeoutError):
                                cloud_meta['review_error']='Повторная проверка недоступна; сомнение сохранено.'
                                candidate.needs_context=True
                                if candidate.confidence<.75 or 'layout_conflict' in reasons:candidate.confidence=min(candidate.confidence,.6)
                        else:
                            candidate.needs_context=True
                    if not candidate.evidence and not candidate.needs_context:
                        raise GeminiError('Gemini не дала достаточных оснований. Использован локальный анализ.','unverified')
                    if candidate.confidence<.65:
                        candidate.document_type='';candidate.subject='';candidate.groups=[];candidate.needs_context=True
                    explicit_kind,explicit_subject=grounded_features('',context)
                    if explicit_kind:candidate.document_type=explicit_kind
                    if explicit_subject:candidate.subject=explicit_subject
                    if explicit_kind or explicit_subject:candidate.groups=canonical_groups(candidate.document_type,candidate.subject,[],fallback)
                    result=candidate;method='gemini';cloud_done=True
                except (GeminiError,ValueError,TimeoutError) as e:
                    message=str(e) if isinstance(e,GeminiError) else 'Gemini вернула неподходящий формат. Использован локальный анализ.'
                    cloud_meta={'status':getattr(e,'code','invalid_result'),'error':message,'images':0}
                    cloud.error=message
            # A cloud outage must not start a slow second model for every queued photo.
            if not cloud_done and not cloud.enabled and self.config.chat_model and self.config.ollama_url:
                try:
                    async with self.chat_lock,httpx.AsyncClient(timeout=35,trust_env=False) as c:
                        r=await c.post(self.config.ollama_url+'/api/chat',json={'model':self.config.chat_model,'stream':False,'think':False,
                            'format':Analysis.model_json_schema(),'options':{'temperature':0,'num_ctx':8192,'num_predict':650},
                            'messages':[{'role':'system','content':system},{'role':'user','content':json.dumps(payload,ensure_ascii=False)}]})
                        r.raise_for_status();candidate=Analysis.model_validate_json(r.json()['message']['content'])
                    normalize=lambda s:re.sub(r'\s+',' ',s.lower()).strip()
                    source=normalize(item['body']+' '+context)
                    evidence=[q for q in candidate.evidence if 5<=len(q)<=500 and normalize(q) in source]
                    if evidence and candidate.confidence>=.5:
                        candidate.evidence=evidence
                        if not supported_type(candidate.document_type,item['body']+' '+context):
                            candidate.document_type='';candidate.groups=[]
                        if candidate.subject and not subject and not inferred_subject(source):
                            # A source quote unrelated to the claimed school subject is insufficient.
                            words=[k for k,v in SUBJECTS.items() if v==candidate.subject]
                            if not any(re.search(r'\b'+re.escape(w)+r'\w*',source) for w in words):
                                candidate.subject='';candidate.groups=[]
                        # Explicit labels in feedback remain authoritative, even when the model hesitates.
                        candidate.document_type=kind or candidate.document_type
                        candidate.subject=subject or candidate.subject
                        if candidate.document_type=='сочинение' and not subject:candidate.subject=''
                        result=candidate;method='local_model'
                except Exception:method='local_rules_model_unavailable'
        if result.document_type=='расписание':result.subject=''
        if result.needs_context and result.confidence<.65:result.groups=[]
        elif not cloud_done or not result.groups:result.groups=canonical_groups(result.document_type,result.subject,result.groups,fallback)
        # Carry a personal subject to a genuinely matching type, not to unrelated documents.
        if result.document_type.lower() in ('сочинение','essay') and not result.subject and not result.needs_context:
            matching=[m for _,m in scored if grounded_features(m['body'],m['context'])[0]=='сочинение']
            subjects={grounded_features(m['body'],m['context'])[1] for m in matching if grounded_features(m['body'],m['context'])[1]}
            if len(subjects)==1:
                result.subject=subjects.pop();result.groups=canonical_groups(result.document_type,result.subject,[],fallback)
        # Reuse a deliberate folder preference only for a matching type and subject.
        preferred=[]
        for similarity,m in scored:
            mk,ms=grounded_features(m['body'],m['context'])
            if m['manual_groups'] and mk and mk==result.document_type and ((ms and ms==result.subject) or similarity>=.65):
                preferred.append(tuple(json.loads(m['groups_json'])))
        if preferred and len(set(preferred))==1:result.groups=list(preferred[0])[:3]
        d=result.model_dump();d.update(method=method,model=self.config.gemini_model if method=='gemini' else self.config.chat_model if method=='local_model' else '',memory_examples_used=len(examples),cloud=cloud_meta)
        d['web']=reference
        return d
