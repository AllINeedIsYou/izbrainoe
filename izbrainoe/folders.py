"""One home per material; additional meanings remain searchable topics."""
import re


ALIASES = {
    'it / проекты': 'Программирование / Проекты',
    'ит / проекты': 'Программирование / Проекты',
    'программирование / проекты': 'Программирование / Проекты',
}


def clean_label(value):
    return re.sub(r'\s*/\s*', ' / ', re.sub(r'\s+', ' ', value).strip())


def primary_folder(groups):
    for value in groups:
        if not isinstance(value, str):
            continue
        label = clean_label(value)
        if label and label.casefold() != 'неразобранное':
            parts=label.split(' / ')
            if parts[0].casefold() in ('вышмат','высшая математика'):
                parts[0]='Высшая математика';label=' / '.join(parts)
            return [ALIASES.get(label.casefold(), label)]
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
