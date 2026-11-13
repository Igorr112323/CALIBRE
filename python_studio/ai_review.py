"""Build a human-readable review of the actual candidate, not of model claims."""

import difflib
import hashlib
import json

MISSING = object()
MAX_REVIEW_CHANGES = 300
MAX_REVIEW_CHARACTERS = 180_000


def child_path(path, key):
    key = str(key).replace('~', '~0').replace('/', '~1')
    return path + '/' + key


def list_key(value):
    if isinstance(value, dict):
        if isinstance(value.get('id'), str):
            return ('id', value['id'])
        if isinstance(value.get('position'), int):
            return ('position', value['position'])
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return ('content', hashlib.sha256(data).digest())


def review_value(value, key='', is_media=False):
    if isinstance(value, str) and (key == 'image' or is_media) and value:
        digest = hashlib.sha256(value.encode('utf-8')).hexdigest()[:12]
        return f'[Изображение: {len(value)} символов, SHA-256 {digest}; содержимое не показано]'
    if isinstance(value, dict):
        return {name: review_value(item, name, is_media or key == 'media') for name, item in value.items()}
    if isinstance(value, list):
        return [review_value(item, 'image' if key == 'images' else '', is_media) for item in value]
    return value


def display_value(value, key, path):
    value = review_value(value, key, bool(path and (path.startswith('/media/') or '/images/' in path)))
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2)


def build_review(before, after):
    """Report every changed value, including list insertions, deletions and sync effects.

    Image bytes are represented by length and hash, never as active markup. Oversized
    changes are rejected rather than silently truncating a preview the user approves.
    """
    changes = []
    size = 0

    def emit(old, new, old_path, new_path):
        nonlocal size
        old_present, new_present = old is not MISSING, new is not MISSING
        old_key = old_path.rsplit('/', 1)[-1] if old_path else ''
        new_key = new_path.rsplit('/', 1)[-1] if new_path else ''
        old_text = display_value(old, old_key, old_path) if old_present else ''
        new_text = display_value(new, new_key, new_path) if new_present else ''
        size += len(old_text) + len(new_text) + len(old_path or '') + len(new_path or '')
        if len(changes) >= MAX_REVIEW_CHANGES or size > MAX_REVIEW_CHARACTERS:
            raise ValueError('Изменений слишком много для полного просмотра. Откройте нужный пункт или воспользуйтесь ручным редактором; данные не изменены.')
        changes.append({'before_path': old_path, 'after_path': new_path, 'before_present': old_present,
                        'after_present': new_present, 'before': old_text, 'after': new_text})

    def walk(old, new, old_path='', new_path=''):
        if old is MISSING or new is MISSING:
            emit(old, new, old_path, new_path)
        elif old == new:
            return
        elif isinstance(old, dict) and isinstance(new, dict):
            for key in dict.fromkeys([*old, *new]):
                walk(old.get(key, MISSING), new.get(key, MISSING),
                     child_path(old_path, key) if key in old else None,
                     child_path(new_path, key) if key in new else None)
        elif isinstance(old, list) and isinstance(new, list):
            if len(old) == len(new) and all(not isinstance(item, (list, dict)) for item in old + new):
                for index, (old_item, new_item) in enumerate(zip(old, new)):
                    walk(old_item, new_item, child_path(old_path, index), child_path(new_path, index))
                return
            matcher = difflib.SequenceMatcher(None, [list_key(item) for item in old], [list_key(item) for item in new], autojunk=False)
            for kind, a, b, c, d in matcher.get_opcodes():
                if kind == 'equal' or (kind == 'replace' and b - a == d - c):
                    for i, j in zip(range(a, b), range(c, d)):
                        walk(old[i], new[j], child_path(old_path, i), child_path(new_path, j))
                else:
                    for i in range(a, b):
                        walk(old[i], MISSING, child_path(old_path, i), None)
                    for j in range(c, d):
                        walk(MISSING, new[j], None, child_path(new_path, j))
        else:
            emit(old, new, old_path, new_path)

    walk(before, after)
    return changes
