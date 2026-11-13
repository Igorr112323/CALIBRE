import copy
import hashlib
import json
import os
import re
import threading
import zlib
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'data'
SAMPLE = ROOT / 'examples' / 'sample-catalog.json'
PROJECT = DATA / 'project.json'
ID_RE = re.compile(r'^[A-Za-z0-9_-]{1,90}$')
SVG_TAGS = {'svg', 'g', 'defs', 'marker', 'path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon', 'text', 'tspan', 'title', 'desc', 'linearGradient', 'radialGradient', 'stop', 'clipPath', 'mask', 'pattern', 'image'}


def safe_svg(value):
    if not isinstance(value, str) or len(value) > 300000 or '<!' in value or '<?' in value:
        return False
    try:
        root = ET.fromstring(value)
    except ET.ParseError:
        return False
    if root.tag.split('}')[-1] != 'svg':
        return False
    for element in root.iter():
        if element.tag.split('}')[-1] not in SVG_TAGS:
            return False
        for raw, value in element.attrib.items():
            name = raw.split('}')[-1].lower()
            lower = value.lower().strip()
            if name.startswith('on') or name in ('src', 'srcdoc'):
                return False
            if name in ('href', 'xlink:href') and not (lower.startswith('#') or re.fullmatch(r'data:image/(png|jpeg|webp);base64,[a-z0-9+/=]+', lower)):
                return False
            if 'javascript:' in lower or 'data:text' in lower or 'http:' in lower or 'https:' in lower:
                return False
            if 'url(' in lower and not re.fullmatch(r'url\(\s*#[\w.-]+\s*\)', lower):
                return False
    return True


def validate_project(project):
    if not isinstance(project, dict) or not isinstance(project.get('machines'), list) or len(project['machines']) > 100:
        raise ValueError('Каталог должен содержать не более 100 машин.')
    ids = set()
    for machine in project['machines']:
        if not isinstance(machine, dict) or not isinstance(machine.get('id'), str) or not ID_RE.fullmatch(machine['id']) or machine['id'] in ids:
            raise ValueError('Неверный или повторный идентификатор машины.')
        ids.add(machine['id'])
        if not isinstance(machine.get('model'), str) or not machine['model'].strip() or len(machine['model']) > 200:
            raise ValueError('Укажите название машины.')
        if not isinstance(machine.get('machine_type'), str) or len(machine['machine_type']) > 200:
            raise ValueError('Неверный тип техники.')
        if not all(isinstance(machine.get(key), list) for key in ('sections', 'adjustments', 'partGroups')):
            raise ValueError('Неверная структура разделов.')
        media = machine.get('media', {})
        if not isinstance(media, dict) or len(media) > 6000:
            raise ValueError('Неверный список изображений.')
        for key, value in media.items():
            if not isinstance(key, str) or len(key) > 300 or not isinstance(value, str) or len(value) > 16 * 1024 * 1024 or not re.match(r'^data:image/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$', value):
                raise ValueError('Изображение должно быть PNG, JPEG или WebP размером до 12 МБ.')
        def unique_ids(items, label):
            seen = set()
            for item in items:
                if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not ID_RE.fullmatch(item['id']) or item['id'] in seen:
                    raise ValueError('Неверный или повторный идентификатор: ' + label)
                seen.add(item['id'])
        unique_ids(machine['sections'], 'раздел')
        unique_ids(machine['adjustments'], 'регулировка')
        unique_ids(machine['partGroups'], 'группа деталей')
        for section in machine['sections']:
            if not isinstance(section.get('items'), list) or len(section['items']) > 5000:
                raise ValueError('Неверная структура раздела.')
            unique_ids(section['items'], 'пункт раздела')
            for item in section['items']:
                if not isinstance(item.get('title'), str) or len(item['title']) > 500:
                    raise ValueError('Неверное название пункта раздела.')
                if 'steps' in item and (not isinstance(item['steps'], list) or not all(isinstance(step, str) for step in item['steps'])):
                    raise ValueError('Неверные действия пункта раздела.')
                if 'rows' in item and (not isinstance(item['rows'], list) or not all(isinstance(row, list) and len(row) == 2 for row in item['rows'])):
                    raise ValueError('Неверные строки раздела.')
        for adjustment in machine['adjustments']:
            if not isinstance(adjustment.get('title'), str) or not isinstance(adjustment.get('steps'), list) or not 1 <= len(adjustment['steps']) <= 500:
                raise ValueError('В регулировке нужен хотя бы один шаг.')
            for step in adjustment['steps']:
                if not isinstance(step, dict) or not isinstance(step.get('title'), str) or not isinstance(step.get('text'), str):
                    raise ValueError('Неверная структура шага регулировки.')
        for group in machine['partGroups']:
            if not isinstance(group.get('title'), str) or not isinstance(group.get('items'), list) or len(group['items']) > 5000:
                raise ValueError('Неверная группа деталей.')
            positions = set()
            for item in group['items']:
                if not isinstance(item, dict) or not isinstance(item.get('position'), int) or isinstance(item['position'], bool) or item['position'] < 1 or item['position'] in positions or not isinstance(item.get('name'), str):
                    raise ValueError('Неверная или повторная позиция детали.')
                positions.add(item['position'])
        def inspect(value, key='', depth=0):
            if depth > 40:
                raise ValueError('Слишком глубокая структура каталога.')
            if isinstance(value, str):
                if key == 'image' and value and not safe_svg(value):
                    raise ValueError('Небезопасная встроенная SVG-схема.')
                if key != 'media' and len(value) > 16 * 1024 * 1024:
                    raise ValueError('Слишком длинное поле каталога.')
            elif isinstance(value, list):
                if len(value) > 10000:
                    raise ValueError('Слишком большой список.')
                for item in value:
                    inspect(item, 'image' if key == 'images' else '', depth + 1)
            elif isinstance(value, dict):
                if any(key in value for key in ('__proto__', 'constructor', 'prototype')):
                    raise ValueError('Недопустимое поле каталога.')
                for name, item in value.items():
                    if name != 'media':
                        inspect(item, name, depth + 1)
        inspect(machine)
    return project


def serialize(project):
    return json.dumps(project, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


class ConflictError(Exception):
    pass


class ProjectStore:
    def __init__(self, path=PROJECT, sample=SAMPLE):
        self.path = Path(path)
        self.sample = Path(sample)
        self.lock = threading.RLock()
        self.history = []
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._write(validate_project(json.loads(self.sample.read_text(encoding='utf-8'))))

    def _write(self, project):
        raw = serialize(project)
        if len(raw) > 180 * 1024 * 1024:
            raise ValueError('Проект превышает 180 МБ.')
        temporary = self.path.with_suffix('.tmp')
        temporary.write_bytes(raw)
        os.replace(temporary, self.path)
        return hashlib.sha256(raw).hexdigest()

    def get(self):
        with self.lock:
            raw = self.path.read_bytes()
            return json.loads(raw), hashlib.sha256(raw).hexdigest()

    def _remember(self, project):
        self.history.append(zlib.compress(serialize(project), 3))
        while len(self.history) > 12 or sum(map(len, self.history)) > 200 * 1024 * 1024:
            self.history.pop(0)

    def save(self, project, version, remember=True):
        validate_project(project)
        with self.lock:
            current, current_version = self.get()
            if not version or current_version != version.strip('"'):
                raise ConflictError('Каталог изменился в другой вкладке. Обновите страницу перед сохранением.')
            if remember:
                self._remember(current)
            return self._write(project)

    def mutate(self, version, function):
        with self.lock:
            current, current_version = self.get()
            if not version or current_version != version.strip('"'):
                raise ConflictError('Каталог изменился во время запроса; правки не применены.')
            modified = copy.deepcopy(current)
            result = function(modified)
            validate_project(modified)
            if modified != current:
                self._remember(current)
                current_version = self._write(modified)
            return modified, current_version, result

    def undo(self, version):
        with self.lock:
            _, current_version = self.get()
            if not version or current_version != version.strip('"'):
                raise ConflictError('Каталог изменился в другой вкладке.')
            if not self.history:
                raise ValueError('Нет изменений для отмены в текущем сеансе.')
            project = json.loads(zlib.decompress(self.history.pop()))
            return project, self._write(project)
