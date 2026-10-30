import asyncio,json,tempfile,time,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from izbrainoe.config import Config
from izbrainoe.store import Store
from izbrainoe.processing import Processor

ESSAY1='Почему человеку важно сохранять память о прошлом? Автор поднимает проблему отношения к истории своей семьи. Он показывает, как воспоминания помогают герою принять решение. Позиция автора понятна: прошлое связывает поколения. Я согласен с автором, потому что без памяти невозможно понять себя. Приведу пример из прочитанной книги: герой возвращается в родной город и меняет отношение к близким. Таким образом, память делает человека ответственным.'
ESSAY2='Можно ли считать равнодушие опасным? В центре текста находится человек, который проходит мимо чужой беды. Рассказчик противопоставляет ему героя, готового помочь. Сначала бездействие кажется безобидным, но постепенно приводит к несчастью. На мой взгляд, ответственность начинается с внимания к окружающим. Это подтверждает поступок героя, который поддержал незнакомца. Следовательно, забота о других определяет нравственный выбор человека.'

async def main():
    with tempfile.TemporaryDirectory() as tmp:
        cfg=Config(data_dir=Path(tmp),ollama_url='http://127.0.0.1:11435',embed_model='qwen3-embedding:0.6b',chat_model='qwen3:4b')
        s=Store(Path(tmp));p=Processor(cfg,s);await p.probe()
        a=s.create(101,'document','Материал 1.pdf',ESSAY1)
        b=s.create(101,'document','Материал 2.pdf',ESSAY2)
        c=s.create(101,'document','Алгебра.pdf','Задачи по математике. Решить квадратное уравнение x²-5x+6=0. Найти производную функции y=x³. Сформулировать свойства логарифма.')
        for i in (a,b,c):
            t=time.monotonic();await p.index(101,i['id']);r=s.get(101,i['id']);print(i['title'],r['groups'],r['analysis'].get('method'),round(time.monotonic()-t,2),flush=True)
        s.update(101,a['id'],context='Это сочинение по русскому языку, подготовка к ЕГЭ. Хочу хранить такие тексты в разделе «Русский язык / Сочинения».')
        await p.feedback(101,a['id'])
        await p.review_tasks[101]
        for i in (a,b,c):
            r=s.get(101,i['id']);print('После пояснения:',i['title'],r['groups'],r['analysis'].get('document_type'),r['analysis'].get('subject'),flush=True)
        print('Пересмотр:',s.learning_state(101),flush=True)
        assert 'Русский язык' in s.get(101,b['id'])['groups']
        assert not any('Сочинения' in g for g in s.get(101,c['id'])['groups'])
        print('Личное уточнение перенесено на связанный материал; математика не затронута.',flush=True)
        await p.close()

asyncio.run(main())
