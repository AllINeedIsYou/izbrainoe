import hashlib
import json
import os
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager

class Store:
    def __init__(self, folder):
        self.folder = folder
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(folder, 0o700)
        (folder / 'files').mkdir(exist_ok=True, mode=0o700)
        self.path = folder / 'izbrainoe.sqlite3'
        with self.db() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,created REAL);
            CREATE TABLE IF NOT EXISTS invites(code TEXT PRIMARY KEY,expires REAL,used_by INTEGER);
            CREATE TABLE IF NOT EXISTS items(
              id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,kind TEXT NOT NULL,title TEXT NOT NULL,
              body TEXT NOT NULL DEFAULT '',context TEXT NOT NULL DEFAULT '',groups_json TEXT NOT NULL DEFAULT '[]',
              manual_groups INTEGER NOT NULL DEFAULT 0,created REAL NOT NULL,updated REAL NOT NULL,
              filename TEXT NOT NULL DEFAULT '',path TEXT NOT NULL DEFAULT '',mime TEXT NOT NULL DEFAULT '',
              tg_file_id TEXT NOT NULL DEFAULT '',tg_message INTEGER,bot_message INTEGER,
              extraction TEXT NOT NULL DEFAULT '',digest TEXT NOT NULL DEFAULT '',embedding TEXT,
              embed_model TEXT NOT NULL DEFAULT '',deleted REAL,source_label TEXT NOT NULL DEFAULT '',
              UNIQUE(user_id,tg_message));
            CREATE INDEX IF NOT EXISTS items_user ON items(user_id,deleted,created);
            CREATE TABLE IF NOT EXISTS chunks(item_id TEXT NOT NULL,user_id INTEGER NOT NULL,
              n INTEGER NOT NULL,text TEXT NOT NULL,embedding TEXT,model TEXT NOT NULL DEFAULT '',
              PRIMARY KEY(item_id,n));
            CREATE TABLE IF NOT EXISTS vault(user_id INTEGER PRIMARY KEY,version INTEGER NOT NULL,
              envelope TEXT NOT NULL,updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS modes(user_id INTEGER PRIMARY KEY,mode TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS message_links(user_id INTEGER NOT NULL,message_id INTEGER NOT NULL,item_id TEXT NOT NULL,
              PRIMARY KEY(user_id,message_id));
            CREATE TABLE IF NOT EXISTS memories(user_id INTEGER NOT NULL,item_id TEXT NOT NULL,context TEXT NOT NULL,
              groups_json TEXT NOT NULL,analysis_json TEXT NOT NULL,embedding TEXT,model TEXT NOT NULL,updated REAL,
              PRIMARY KEY(user_id,item_id));
            CREATE TABLE IF NOT EXISTS web_cache(query TEXT PRIMARY KEY,result TEXT NOT NULL,created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS analysis_cache(user_id INTEGER NOT NULL,cache_key TEXT NOT NULL,result TEXT NOT NULL,created REAL NOT NULL,
              PRIMARY KEY(user_id,cache_key));
            CREATE TABLE IF NOT EXISTS outgoing_messages(user_id INTEGER NOT NULL,message_id INTEGER NOT NULL,created REAL NOT NULL,
              PRIMARY KEY(user_id,message_id));
            CREATE TABLE IF NOT EXISTS ai_usage(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,month TEXT NOT NULL,created REAL NOT NULL,
              model TEXT NOT NULL,status TEXT NOT NULL,input_tokens INTEGER NOT NULL DEFAULT 0,output_tokens INTEGER NOT NULL DEFAULT 0,
              cost_usd REAL NOT NULL DEFAULT 0,reserved_usd REAL NOT NULL DEFAULT 0,images INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS provod_usage(id TEXT PRIMARY KEY,user_id INTEGER NOT NULL,month TEXT NOT NULL,created REAL NOT NULL,
              model TEXT NOT NULL,status TEXT NOT NULL,input_tokens INTEGER NOT NULL DEFAULT 0,output_tokens INTEGER NOT NULL DEFAULT 0,
              cost_rub REAL NOT NULL DEFAULT 0,reserved_rub REAL NOT NULL DEFAULT 0,images INTEGER NOT NULL DEFAULT 0);
            ''')
            columns={r[1] for r in c.execute('PRAGMA table_info(items)')}
            for name,definition in {'analysis_json':"TEXT NOT NULL DEFAULT '{}'",'analysis_state':"TEXT NOT NULL DEFAULT 'pending'",'original_caption':"TEXT NOT NULL DEFAULT ''"}.items():
                if name not in columns:c.execute(f'ALTER TABLE items ADD COLUMN {name} {definition}')
            if not c.execute("SELECT 1 FROM meta WHERE key='outgoing_backfill_v1'").fetchone():
                c.execute('INSERT OR IGNORE INTO outgoing_messages SELECT user_id,bot_message,created FROM items WHERE user_id>0 AND bot_message IS NOT NULL')
                c.execute('INSERT OR IGNORE INTO outgoing_messages SELECT user_id,message_id,0 FROM message_links WHERE user_id>0')
                c.execute("INSERT INTO meta VALUES('outgoing_backfill_v1','1')")
        os.chmod(self.path, 0o600)
        if not self.meta('local_key'):
            self.meta('local_key', secrets.token_urlsafe(32))
        if not self.meta('pair_code'):
            self.meta('pair_code', secrets.token_urlsafe(24))

    @contextmanager
    def db(self):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA journal_mode=WAL')
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def meta(self, key, value=None):
        with self.db() as c:
            if value is not None:
                c.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key, str(value)))
            r=c.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
            return r[0] if r else None

    def allowed(self, uid, config_ids=()):
        if uid in config_ids:
            return True
        with self.db() as c:
            return bool(c.execute('SELECT 1 FROM users WHERE id=?',(uid,)).fetchone())

    def pair(self, uid, code):
        with self.db() as c:
            owner=c.execute("SELECT value FROM meta WHERE key='owner'").fetchone()
            secret=c.execute("SELECT value FROM meta WHERE key='pair_code'").fetchone()
            if not owner and secret and secrets.compare_digest(code,secret[0]):
                c.execute("INSERT INTO meta VALUES('owner',?)",(str(uid),))
                c.execute("DELETE FROM meta WHERE key='pair_code'")
                # The local workspace belongs to the owner who pairs on this Mac.
                c.execute('UPDATE items SET user_id=? WHERE user_id=-1',(uid,))
                c.execute('UPDATE chunks SET user_id=? WHERE user_id=-1',(uid,))
                c.execute('UPDATE memories SET user_id=? WHERE user_id=-1',(uid,))
                c.execute('UPDATE vault SET user_id=? WHERE user_id=-1 AND NOT EXISTS(SELECT 1 FROM vault WHERE user_id=?)',(uid,uid))
            else:
                r=c.execute('SELECT * FROM invites WHERE code=? AND used_by IS NULL AND expires>?',(code,time.time())).fetchone()
                if not r:
                    return False
                c.execute('UPDATE invites SET used_by=? WHERE code=?',(uid,code))
            c.execute('INSERT OR IGNORE INTO users VALUES(?,?)',(uid,time.time()))
            return True

    def invite(self):
        code=secrets.token_urlsafe(24)
        with self.db() as c:
            c.execute('INSERT INTO invites VALUES(?,?,NULL)',(code,time.time()+86400))
        return code

    def encode(self, r):
        if r is None: return None
        d=dict(r)
        d['groups']=json.loads(d.pop('groups_json'))
        d['analysis']=json.loads(d.pop('analysis_json','{}'))
        d['has_file']=bool(d['path'] or d['tg_file_id'])
        d['has_local_file']=bool(d['path'])
        for key in ('embedding','embed_model','path','tg_file_id','digest','user_id'):
            d.pop(key,None)
        return d

    def get(self, uid, item_id, raw=False, trash=False):
        with self.db() as c:
            r=c.execute('SELECT * FROM items WHERE user_id=? AND id=?'+('' if trash else ' AND deleted IS NULL'),(uid,item_id)).fetchone()
            return dict(r) if r and raw else self.encode(r)

    def list(self, uid, group='', kind='', trash=False):
        with self.db() as c:
            rows=c.execute('SELECT * FROM items WHERE user_id=? AND deleted IS '+('NOT NULL' if trash else 'NULL')+' ORDER BY created DESC',(uid,)).fetchall()
        out=[self.encode(r) for r in rows]
        if group:
            out=[r for r in out if group in r['groups'] or (group=='Неразобранное' and not r['groups'])]
        if kind: out=[r for r in out if r['kind']==kind]
        return out

    def create(self, uid, kind, title, body='', **kwargs):
        now=time.time(); item_id=uuid.uuid4().hex
        keys=['id','user_id','kind','title','body','created','updated']
        vals=[item_id,uid,kind,title[:200],body[:200000],now,now]
        allowed={'filename','path','mime','tg_file_id','tg_message','bot_message','extraction','digest','source_label','original_caption'}
        for k,v in kwargs.items():
            if k in allowed: keys.append(k); vals.append(v)
        with self.db() as c:
            c.execute(f'INSERT INTO items({",".join(keys)}) VALUES({",".join("?" for _ in vals)})',vals)
        return self.get(uid,item_id)

    def update(self, uid, item_id, **values):
        allowed={'title','body','context','manual_groups','groups_json','extraction','bot_message','embedding','embed_model','deleted','analysis_json','analysis_state','path','mime','filename','digest','source_label','original_caption'}
        values={k:v for k,v in values.items() if k in allowed}
        values['updated']=time.time()
        with self.db() as c:
            c.execute('UPDATE items SET '+','.join(f'{k}=?' for k in values)+' WHERE user_id=? AND id=?',[*values.values(),uid,item_id])
        return self.get(uid,item_id,trash=True)

    def find_message(self, uid, mid):
        with self.db() as c:
            r=c.execute('SELECT * FROM items WHERE user_id=? AND (tg_message=? OR bot_message=? OR id IN (SELECT item_id FROM message_links WHERE user_id=? AND message_id=?)) AND deleted IS NULL',(uid,mid,mid,uid,mid)).fetchone()
            return self.encode(r)

    def link_message(self, uid, mid, item_id):
        if not self.get(uid,item_id):return
        with self.db() as c:
            c.execute('INSERT OR REPLACE INTO message_links VALUES(?,?,?)',(uid,mid,item_id))

    def replace_chunks(self, uid, item_id, chunks, vectors=None, model=''):
        with self.db() as c:
            c.execute('DELETE FROM chunks WHERE item_id=? AND user_id=?',(item_id,uid))
            for i,t in enumerate(chunks):
                v=json.dumps(vectors[i]) if vectors else None
                c.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?)',(item_id,uid,i,t,v,model))

    def search_chunks(self, uid, model=''):
        with self.db() as c:
            return [dict(r) for r in c.execute('SELECT chunks.* FROM chunks JOIN items ON items.id=chunks.item_id WHERE chunks.user_id=? AND items.deleted IS NULL',(uid,))]

    def add_chunk(self,uid,item_id,n,text,vector=None,model=''):
        with self.db() as c:
            c.execute('INSERT OR REPLACE INTO chunks VALUES(?,?,?,?,?,?)',(item_id,uid,n,text,json.dumps(vector) if vector else None,model if vector else ''))

    def duplicate(self, uid, digest):
        if not digest: return None
        with self.db() as c:
            r=c.execute('SELECT id FROM items WHERE user_id=? AND digest=? AND deleted IS NULL',(uid,digest)).fetchone()
            return r[0] if r else None

    def mode(self, uid, value=None):
        with self.db() as c:
            if value is not None: c.execute('INSERT OR REPLACE INTO modes VALUES(?,?)',(uid,value))
            r=c.execute('SELECT mode FROM modes WHERE user_id=?',(uid,)).fetchone()
            return r[0] if r else ''

    def vault(self, uid):
        with self.db() as c:
            r=c.execute('SELECT version,envelope FROM vault WHERE user_id=?',(uid,)).fetchone()
            return {'version':r[0],'envelope':json.loads(r[1])} if r else {'version':0,'envelope':None}

    def save_vault(self, uid, version, envelope):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            r=c.execute('SELECT version FROM vault WHERE user_id=?',(uid,)).fetchone()
            current=r[0] if r else 0
            if version!=current: return False
            c.execute('INSERT OR REPLACE INTO vault VALUES(?,?,?,?)',(uid,current+1,json.dumps(envelope),time.time()))
            return True

    def remember(self, uid, item_id, context, groups, analysis, vector=None, model=''):
        if not self.get(uid,item_id):return
        with self.db() as c:
            c.execute('INSERT OR REPLACE INTO memories VALUES(?,?,?,?,?,?,?,?)',
                      (uid,item_id,context,json.dumps(groups,ensure_ascii=False),json.dumps(analysis,ensure_ascii=False),json.dumps(vector) if vector else None,model,time.time()))

    def memories(self, uid):
        with self.db() as c:
            return [dict(r) for r in c.execute('SELECT memories.*,items.body,items.title,items.manual_groups FROM memories JOIN items ON items.id=memories.item_id WHERE memories.user_id=? AND items.deleted IS NULL ORDER BY memories.updated DESC',(uid,))]

    def learning_state(self, uid, value=None):
        if value is not None:self.meta(f'learning_{uid}',json.dumps(value,ensure_ascii=False))
        return json.loads(self.meta(f'learning_{uid}') or '{}')

    def record_outgoing(self,uid,message):
        if not isinstance(message,dict):return
        mid=message.get('message_id')
        if not isinstance(uid,int) or uid<=0 or not isinstance(mid,int) or isinstance(mid,bool):return
        with self.db() as c:
            c.execute('INSERT OR IGNORE INTO outgoing_messages VALUES(?,?,?)',(uid,mid,message.get('date') or time.time()))

    def outgoing(self,uid):
        with self.db() as c:
            return [dict(r) for r in c.execute('SELECT message_id,created FROM outgoing_messages WHERE user_id=? ORDER BY message_id',(uid,))]

    def forget_outgoing(self,uid,mids):
        with self.db() as c:c.executemany('DELETE FROM outgoing_messages WHERE user_id=? AND message_id=?',[(uid,m) for m in mids])
