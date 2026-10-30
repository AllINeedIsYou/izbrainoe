"""One home per material; additional meanings remain searchable topics."""
import re


ALIASES = {
    'it': 'Программирование', 'ит': 'Программирование',
    'разработка': 'Программирование', 'программирование': 'Программирование',
    'it-проекты': 'Программирование',
    'вышмат': 'Высшая математика', 'высшая математика': 'Высшая математика',
    'русский': 'Русский язык', 'русский язык': 'Русский язык',
    'учеба': 'Учёба', 'учёба': 'Учёба', 'вуз': 'Вуз',
    'сочинения': 'Учёба', 'лекции': 'Учёба', 'конспекты': 'Учёба',
    'расписание': 'Учёба', 'задачи': 'Учёба', 'учебники': 'Учёба',
    'задачники': 'Учёба', 'договоры': 'Документы',
}

SUBJECTS = {'математика','высшая математика','вышмат','русский язык','физика','химия',
            'информатика','история','литература','геометрия','английский язык','электроника'}


def clean_label(value):
    return re.sub(r'\s*/\s*', ' / ', re.sub(r'\s+', ' ', value).strip())


def primary_folder(groups):
    for value in groups:
        if not isinstance(value, str):
            continue
        label = clean_label(value)
        if label and label.casefold() != 'неразобранное':
            if re.search(r'\bвышмат\w*\b|\bвысш\w*\s+математ\w*\b',label,re.I):
                return ['Высшая математика']
            parts=label.split(' / ')
            # A topic is a folder; format/purpose below it becomes a filter.
            topic=parts[0]
            if topic.casefold() in ('учёба','учеба','вуз','материалы','документы'):
                topic=next((part for part in parts[1:] if part.casefold() in SUBJECTS),topic)
            return [ALIASES.get(topic.casefold(),topic[:1].upper()+topic[1:].lower() if topic.casefold() in SUBJECTS else topic)]
    return []


def single_folder_analysis(analysis, groups=None):
    result = dict(analysis)
    proposed = result.get('groups', [])
    chosen_from = proposed if groups is None else groups
    primary = primary_folder(chosen_from)
    topics = []
    for label in [*result.get('topics', []), *chosen_from, *proposed]:
        if not isinstance(label, str):
            continue
        label = clean_label(label)
        if label and label not in primary and label.casefold() not in {t.casefold() for t in topics}:
            topics.append(label)
    result.update(groups=primary, topics=topics)
    return result


def document_category(analysis):
    kind=analysis.get('document_type','').strip().casefold()
    if not kind:
        # Legacy folder suffixes retain their role after the topic migration.
        kind=next((clean_label(t).split(' / ')[-1].casefold() for t in analysis.get('topics',[])
                   if isinstance(t,str) and '/' in t),'')
    if any(word in kind for word in ('учебник','задачник','учебное пособие','сборник задач')):return 'Учебники и задачники'
    if any(word in kind for word in ('расписан','календар','учебный процесс')):return 'Учебные планы и расписания'
    if 'конспект' in kind or 'лекци' in kind:return 'Конспекты и лекции'
    if 'сочинен' in kind or kind in ('эссе','essay'):return 'Сочинения'
    if 'задач' in kind or 'задани' in kind:return 'Задания'
    return kind[:1].upper()+kind[1:] if kind else 'Другие материалы'
