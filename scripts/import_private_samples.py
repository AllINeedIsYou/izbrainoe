"""Explicit local-only importer. UI transcriptions are not original attachments."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
from datetime import datetime
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from izbrainoe.config import Config
from izbrainoe.store import Store
from izbrainoe.processing import Processor

async def run():
    p=argparse.ArgumentParser();p.add_argument('path',type=Path);p.add_argument('--user',type=int,default=-1)
    args=p.parse_args();data=json.loads(args.path.read_text())
    config=Config.load();store=Store(config.data_dir);processor=Processor(config,store)
    await processor.probe()
    for record in data['items']:
        key=f'import_{args.user}_{record["id"]}'
        if store.meta(key):continue
        title=record.get('filename') or (record.get('content') or 'Материал').split('\n')[0][:100]
        body=record.get('content') or ''
        if record.get('urls'):body+='\n'+'\n'.join(record['urls'])
        kind={'message':'note','message_with_links':'link','archive':'document'}.get(record['kind'],record['kind'])
        item=store.create(args.user,kind,title,body,filename=record.get('filename',''),
            source_label='Тест: текст с экрана' if body else 'Тест: только название файла',
            extraction='Оригинал не импортирован. '+('Текст частично переписан с экрана; OCR не проверялся.' if kind=='photo' else 'Содержимое вложения не прочитано.' if not body else 'Текст записан с экрана.'))
        if record.get('date'):
            with store.db() as c:
                c.execute('UPDATE items SET created=? WHERE id=?',(datetime.fromisoformat(record['date']+'T12:00:00+03:00').timestamp(),item['id']))
        await processor.index(args.user,item['id']);store.meta(key,item['id'])
    print(f'Локальных тестовых записей: {len(store.list(args.user))}. Оригиналы не импортированы.')

if __name__=='__main__':asyncio.run(run())
