import json
import re
import sys
import pdfplumber


def emit(stage, **values):
    print(json.dumps({'stage': stage, **values}, ensure_ascii=False), flush=True)


def clean(value):
    return re.sub(r'\s+', ' ', value or '').strip()


def part_purpose(name):
    lowered = name.lower()
    for word, purpose in [('болт', 'Болт соединяет детали.'), ('гайка', 'Гайка удерживает резьбовое соединение.'), ('шайба', 'Шайба распределяет нагрузку крепежа.'), ('шплинт', 'Шплинт удерживает палец или ось от выпадения.'), ('подшипник', 'Подшипник поддерживает вращающуюся деталь.'), ('масленка', 'Через маслёнку подают смазку.')]:
        if word in lowered:
            return purpose
    return 'Назначение и совместимость уточните по руководству производителя.'


def parse(filename):
    emit('reading')
    pages = []
    with pdfplumber.open(filename) as document:
        if len(document.pages) > 500:
            raise ValueError('В PDF более 500 страниц. Разделите руководство на части.')
        for index, page in enumerate(document.pages):
            text = page.extract_text() or ''
            tables = page.extract_tables() or []
            pages.append({'number': index + 1, 'text': text, 'tables': tables})
            if index % 5 == 0:
                emit('reading', page=index + 1, total=len(document.pages))
    if sum(len(p['text']) for p in pages) < 100:
        raise ValueError('Текст в PDF не найден. Для сканированных страниц необходим OCR, который пока не подключён.')
    emit('structure')
    cover = (pages[0]['text'] or '').splitlines()
    model = next((clean(line).strip('«»') for line in cover if re.search(r'[А-ЯA-Z]{2,}[-– ]?\d', line, re.I) and len(line) < 100), '')
    name = clean(' '.join(line for line in cover[:3] if not re.search(r'руководство|каталог|^\d+$', line, re.I)))[:100]
    model = model or name or 'Машина из PDF'
    title = clean(' '.join(cover[:4]))[:100] or model
    machine_type = clean(' '.join(line for line in cover[:2] if not re.search(r'\d', line)))[:100] or 'Техника'
    categories = {'adjustments': [], 'nodes': [], 'maintenance': [], 'storage': [], 'specs': [], 'faults': []}
    current = None
    current_title = ''
    chunks = []
    heading = re.compile(r'^(\d+(?:\.\d+){0,4})\s+([А-ЯA-ZЁ][^\n]{2,110})$')
    def classify(title):
        t = title.lower()
        if re.search(r'неисправ|безопас|охран[аы] труда', t): return 'faults'
        if re.search(r'хранен|консервац', t): return 'storage'
        if re.search(r'техническ.*обслуж|смазк|регламент|\bето\b', t): return 'maintenance'
        if re.search(r'регулир|настрой|налад|подготовк|досборк|обкатк|агрегатирован|эксплуатационн.*огранич|правила эксплуатации', t): return 'adjustments'
        if re.search(r'техническ.*данн|характеристик', t): return 'specs'
        if re.search(r'устройств|состав изделия|узлы|ротор|каретк|рама|навеск|огражден|трансмисси', t): return 'nodes'
        return None
    for page in pages:
        lines = page['text'].splitlines()
        if any('содержание' in line.lower() for line in lines[:2]) or any('номерной указатель' in line.lower() for line in lines[:2]):
            continue
        for line in lines:
            line = clean(line)
            if not line or re.fullmatch(r'\d+', line): continue
            found = heading.match(line)
            if found and '.' not in found.group(1) and not re.match(r'^(введение|техническое|указания|описание|досборка|правила|транспортирование|хранение|возможные|предельные|вывод|требования|устройство|безопасность|состав)', found.group(2), re.I):
                found = None
            if found and '.....' not in line:
                if current and chunks:
                    categories[current].append((current_title, clean(' '.join(chunks)), start))
                current_title = clean(found.group(2))
                current = classify(current_title)
                chunks = []
                start = page['number']
            elif current:
                chunks.append(line)
    if current and chunks:
        categories[current].append((current_title, clean(' '.join(chunks)), start))
    emit('adjustments')
    adjustment_items = []
    for n, (heading_text, text, page) in enumerate(categories['adjustments']):
        sentences = [clean(x) for x in re.split(r'(?<=[.!?])\s+(?=[А-ЯЁA-Z])', text) if len(clean(x)) > 12]
        steps = [{'title': f'Шаг {i+1}', 'text': s, 'page': page} for i, s in enumerate(sentences[:30])]
        if steps: adjustment_items.append({'id': f'adj-{n+1}', 'title': heading_text, 'subtitle': f'Источник: стр. {page}', 'steps': steps})
    emit('parts')
    groups = []
    for page in pages:
        for table in page['tables']:
            if not table or len(table[0]) < 5: continue
            header = ' '.join(clean(x).lower() for x in table[0])
            if not ('позиц' in header and 'наименован' in header and 'количеств' in header): continue
            rows = []
            for row in table[1:]:
                if len(row) < 5: continue
                pos = clean(row[1]); name = clean(row[3]); quantity = clean(row[4])
                if not re.fullmatch(r'\d+', pos) or not name: continue
                rows.append({'position': int(pos), 'designation': clean(row[2]), 'name': name, 'quantity': quantity, 'note': clean(row[5]) if len(row) > 5 else '', 'page': page['number'], 'purpose': part_purpose(name)})
            if not rows: continue
            first = clean(page['text'].splitlines()[0])[:120]
            if groups and rows[0]['position'] > 1 and groups[-1]['title'] == first:
                groups[-1]['items'].extend(rows)
            else:
                groups.append({'id': f'group-{len(groups)+1}', 'title': first or f'Группа {len(groups)+1}', 'items': rows})
    table_items = {'specs': [], 'faults': [], 'maintenance': []}
    for page in pages:
        for table in page['tables']:
            if not table: continue
            header = ' '.join(clean(x).lower() for x in table[0])
            if 'неисправност' in header and ('причин' in header or 'устранен' in header):
                for row in table[1:]:
                    if len(row) < 4 or not clean(row[1]): continue
                    text = ' '.join(filter(None, [f'Вероятная причина: {clean(row[2])}.' if clean(row[2]) else '', f'Метод устранения: {clean(row[3])}.' if clean(row[3]) else '']))
                    table_items['faults'].append({'id': f'fault-table-{page["number"]}-{len(table_items["faults"])+1}', 'title': clean(row[1]), 'text': text or 'Подробности сверьте с таблицей руководства.', 'badge': f'Источник: стр. {page["number"]}', 'page': page['number']})
            elif 'наименован' in header and any(re.search(r'^значен', clean(x), re.I) for x in table[0]) and 'позиц' not in header:
                for row in table[1:]:
                    if len(row) < 2 or not clean(row[0]): continue
                    table_items['specs'].append({'id': f'spec-table-{page["number"]}-{len(table_items["specs"])+1}', 'title': clean(row[0]), 'text': ' · '.join(clean(x) for x in row[1:] if clean(x)) or 'Значение сверьте с таблицей руководства.', 'badge': f'Источник: стр. {page["number"]}', 'page': page['number']})
            elif 'смазк' in header and ('периодичност' in header or 'объект' in header):
                for row in table[1:]:
                    if not clean(row[0]): continue
                    table_items['maintenance'].append({'id': f'lube-table-{page["number"]}-{len(table_items["maintenance"])+1}', 'title': clean(row[0]), 'text': ' · '.join(clean(x) for x in row[1:] if clean(x)), 'badge': f'Источник: стр. {page["number"]}', 'page': page['number']})
    names = {'adjustments':'Регулировки','parts':'Детали','maintenance':'ТО и смазка','nodes':'Узлы','storage':'Хранение','specs':'ТТХ и агрегатирование','faults':'Неисправности и ТБ'}
    sections = []
    for section_id, section_title in names.items():
        if section_id == 'adjustments':
            items = [{'id': x['id'], 'title': x['title'], 'badge': x['subtitle']} for x in adjustment_items]
        elif section_id == 'parts':
            items = []
        else:
            items = [{'id': f'{section_id}-{n+1}', 'title': title_, 'text': text[:12000], 'badge': f'Источник: стр. {page}', 'page': page, **({'number': n+1} if section_id == 'nodes' else {})} for n, (title_, text, page) in enumerate(categories[section_id]) if text]
        items.extend(table_items.get(section_id, []))
        sections.append({'id': section_id, 'title': section_title, 'items': items})
    warnings = []
    for section in sections:
        if section['id'] == 'parts' and not groups or section['id'] != 'parts' and not section['items']:
            warnings.append(f'Не найден раздел «{section["title"]}»; проверьте руководство вручную.')
    if any(len(p['text']) < 30 for p in pages): warnings.append('На некоторых страницах мало текста: возможно, это изображения или скан. OCR не выполнялся.')
    warnings.append('Проверьте распознанные данные и последовательность работ по оригиналу перед применением.')
    return {'model': title, 'machine_type': machine_type, 'sections': sections, 'adjustments': adjustment_items, 'partGroups': groups, 'warnings': warnings, 'source': {'pages': len(pages), 'model': model}, 'generated': True}


try:
    result = parse(sys.argv[1])
    emit('done', machine=result)
except Exception as error:
    emit('error', error=str(error))
    sys.exit(1)
