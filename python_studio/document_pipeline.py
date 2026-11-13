import base64
import io
import re
import threading
from pathlib import Path

import cv2
import numpy as np
import pdfplumber
import pypdfium2 as pdfium
import tesserocr
from PIL import Image
from rapidocr_onnxruntime import RapidOCR

ROOT = Path(__file__).resolve().parent
OCR_DIR = ROOT / 'assets' / 'ocr'
OCR_LOCK = threading.Lock()
OCR_DETECTOR = None


def clean(value):
    return re.sub(r'\s+', ' ', value or '').strip()


def part_purpose(name):
    lowered = name.lower()
    for word, purpose in [('болт', 'Болт соединяет детали.'), ('гайка', 'Гайка удерживает резьбовое соединение.'), ('шайба', 'Шайба распределяет нагрузку крепежа.'), ('шплинт', 'Шплинт удерживает палец или ось от выпадения.'), ('подшипник', 'Подшипник поддерживает вращающуюся деталь.'), ('масленка', 'Через маслёнку подают смазку.')]:
        if word in lowered:
            return purpose
    return 'Назначение и совместимость уточните по руководству производителя.'


def structure_pages(pages, emit=lambda *args, **kwargs: None):
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
            first = clean((page['text'].splitlines() or [''])[0])[:120]
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


def detector():
    global OCR_DETECTOR
    if OCR_DETECTOR is None:
        OCR_DETECTOR = RapidOCR(rec_model_path=str(OCR_DIR / 'cyrillic_PP-OCRv3_rec_mobile.onnx'), intra_op_num_threads=2, inter_op_num_threads=1, print_verbose=False)
    return OCR_DETECTOR


def recognize_image(image, include_detector=True):
    rgb = image.convert('RGB')
    detected = []
    errors = []
    with OCR_LOCK:
        if include_detector:
            try:
                found, _ = detector()(np.asarray(rgb), use_det=True, use_cls=False, use_rec=True)
                detected = found or []
            except Exception as error:
                errors.append('RapidOCR ONNX: ' + str(error))
        words = []
        text = ''
        try:
            with tesserocr.PyTessBaseAPI(path=str(OCR_DIR), lang='rus+eng', psm=tesserocr.PSM.AUTO) as api:
                api.SetImage(rgb)
                text = api.GetUTF8Text() or ''
                iterator = api.GetIterator()
                if iterator is not None:
                    for item in tesserocr.iterate_level(iterator, tesserocr.RIL.WORD):
                        word = (item.GetUTF8Text(tesserocr.RIL.WORD) or '').strip()
                        box = item.BoundingBox(tesserocr.RIL.WORD)
                        if word and box:
                            words.append({'text': word, 'box': list(box), 'confidence': float(item.Confidence(tesserocr.RIL.WORD))})
        except Exception as error:
            errors.append('Tesseract: ' + str(error))
    onnx_text = '\n'.join(item[1] for item in detected if len(item) >= 2 and isinstance(item[1], str))
    if len(re.findall(r'\w', text)) < 35 and len(onnx_text) > len(text):
        text = onnx_text
    if errors and not text:
        raise RuntimeError('; '.join(errors))
    return {'text': text, 'onnx_text': onnx_text, 'words': words, 'detected_lines': len(detected), 'engine': 'RapidOCR Cyrillic PP-OCRv3 ONNX + Tesseract rus+eng', 'errors': errors}


def grid_tables(image, words):
    if not words:
        return []
    gray = cv2.cvtColor(np.asarray(image.convert('RGB')), cv2.COLOR_RGB2GRAY)
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 12)
    height, width = binary.shape
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (max(25, width // 45), 1)))
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, height // 70))))
    mask = cv2.dilate(cv2.bitwise_or(horizontal, vertical), np.ones((3, 3), np.uint8), iterations=1)
    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    cells = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if 20 <= w <= width * .95 and 10 <= h <= height * .2 and w * h > 250:
            cells.append((x, y, x + w, y + h))
    if len(cells) < 4:
        return []
    cells.sort(key=lambda item: (item[1], item[0]))
    rows = []
    for x0, y0, x1, y1 in cells:
        assigned = [word for word in words if x0 <= (word['box'][0] + word['box'][2]) / 2 <= x1 and y0 <= (word['box'][1] + word['box'][3]) / 2 <= y1]
        if not assigned:
            continue
        assigned.sort(key=lambda item: (item['box'][1] // 8, item['box'][0]))
        value = clean(' '.join(item['text'] for item in assigned))
        group = next((row for row in rows if abs(row['y'] - y0) <= max(5, (y1 - y0) * .22)), None)
        if group is None:
            group = {'y': y0, 'cells': []}
            rows.append(group)
        group['cells'].append((x0, value))
    usable = [sorted(row['cells']) for row in sorted(rows, key=lambda row: row['y']) if len(row['cells']) >= 2]
    if len(usable) < 2:
        return []
    columns = []
    for row in usable:
        for x, _ in row:
            if not any(abs(x - known) < 12 for known in columns):
                columns.append(x)
    columns.sort()
    if len(columns) > 16:
        return []
    result = []
    for row in usable:
        values = [''] * len(columns)
        for x, text in row:
            index = min(range(len(columns)), key=lambda idx: abs(columns[idx] - x))
            values[index] = clean(values[index] + ' ' + text)
        result.append(values)
    return [result] if len(result) >= 2 else []


def image_data_url(image, quality=88):
    stream = io.BytesIO()
    image.convert('RGB').save(stream, format='JPEG', quality=quality, optimize=True)
    return 'data:image/jpeg;base64,' + base64.b64encode(stream.getvalue()).decode('ascii')


def crop_page(pdf_path, page_number, rect):
    if not isinstance(page_number, int) or not isinstance(rect, list) or len(rect) != 4:
        raise ValueError('Неверная область страницы.')
    x0, y0, x1, y1 = [float(value) for value in rect]
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1) or (x1 - x0) * (y1 - y0) < 0.0001:
        raise ValueError('Выберите область на странице.')
    pdf = pdfium.PdfDocument(str(pdf_path))
    if page_number < 1 or page_number > len(pdf):
        raise ValueError('Страница не найдена.')
    page = pdf[page_number - 1]
    image = page.render(scale=2).to_pil().convert('RGB')
    bounds = (int(x0 * image.width), int(y0 * image.height), int(x1 * image.width), int(y1 * image.height))
    return image_data_url(image.crop(bounds))


def parse_document(pdf_path, document_dir=None, progress=lambda *args, **kwargs: None, use_ocr=True):
    source = Path(pdf_path)
    if not source.is_file() or source.stat().st_size > 150 * 1024 * 1024:
        raise ValueError('PDF не найден или превышает 150 МБ.')
    images_dir = Path(document_dir) / 'pages' if document_dir else None
    figures_dir = Path(document_dir) / 'figures' if document_dir else None
    if images_dir:
        images_dir.mkdir(parents=True, exist_ok=True)
        figures_dir.mkdir(parents=True, exist_ok=True)
    pdf = pdfium.PdfDocument(str(source))
    total = len(pdf)
    if not 1 <= total <= 500:
        raise ValueError('В PDF должно быть от 1 до 500 страниц.')
    pages = []
    gallery = []
    scanned = []
    ocr_errors = []
    with pdfplumber.open(source) as plumber:
        for index in range(total):
            page = plumber.pages[index]
            progress('reading', page=index + 1, total=total, message=f'Извлекаем страницу {index + 1} из {total}')
            try:
                text = page.extract_text() or ''
            except Exception:
                text = ''
            try:
                tables = page.extract_tables() or []
            except Exception:
                tables = []
            scanned_page = len(re.findall(r'\w', text)) < 35
            image = None
            ocr_result = None
            if scanned_page and use_ocr:
                progress('ocr', page=index + 1, total=total, message=f'Локальный OCR: страница {index + 1} из {total}')
                try:
                    image = pdf[index].render(scale=2).to_pil().convert('RGB')
                    ocr_result = recognize_image(image)
                    if ocr_result['text'].strip():
                        text = ocr_result['text']
                    if not tables:
                        tables = grid_tables(image, ocr_result['words'])
                    scanned.append(index + 1)
                    ocr_errors.extend(f'Страница {index + 1}: {message}' for message in ocr_result['errors'])
                except Exception as error:
                    ocr_errors.append(f'Страница {index + 1}: {error}')
            if images_dir:
                preview = pdf[index].render(scale=1.15).to_pil().convert('RGB')
                preview.thumbnail((1000, 1400))
                preview.save(images_dir / f'{index + 1}.jpg', 'JPEG', quality=78, optimize=True)
                found_figures = []
                for item in page.images[:25]:
                    try:
                        x0, top, x1, bottom = (float(item[key]) for key in ('x0', 'top', 'x1', 'bottom'))
                        area = (x1 - x0) * (bottom - top)
                        if area < page.width * page.height * 0.015 or area > page.width * page.height * 0.85:
                            continue
                        rect = [max(0, x0 / page.width), max(0, top / page.height), min(1, x1 / page.width), min(1, bottom / page.height)]
                        if rect[0] >= rect[2] or rect[1] >= rect[3]:
                            continue
                        x0p, y0p, x1p, y1p = [int(v * scale) for v, scale in zip(rect, [preview.width, preview.height, preview.width, preview.height])]
                        name = f'{index + 1}-{len(found_figures) + 1}.jpg'
                        preview.crop((x0p, y0p, x1p, y1p)).save(figures_dir / name, 'JPEG', quality=85)
                        found_figures.append({'name': name, 'rect': rect})
                        if len(found_figures) >= 6:
                            break
                    except (KeyError, ValueError, TypeError):
                        continue
                gallery.append({'page': index + 1, 'figures': found_figures, 'ocr': scanned_page})
            pages.append({'number': index + 1, 'text': text, 'tables': tables, 'ocr': scanned_page, 'ocr_words': len(ocr_result['words']) if ocr_result else 0, 'onnx_text': ocr_result['onnx_text'] if ocr_result else '', 'onnx_lines': ocr_result['detected_lines'] if ocr_result else 0})
    if sum(len(page['text']) for page in pages) < 100:
        raise ValueError('Не удалось извлечь текст из PDF. Проверьте качество скана и установки OCR.')
    if document_dir:
        (Path(document_dir) / 'raw-pages.json').write_text(__import__('json').dumps(pages, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
        (Path(document_dir) / 'gallery.json').write_text(__import__('json').dumps(gallery, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    progress('structure', page=total, total=total, message='Собираем семь разделов и таблицы')
    machine = structure_pages(pages, progress)
    machine['id'] = 'pdf-' + __import__('uuid').uuid4().hex[:12]
    machine['media'] = {}
    machine['source']['ocr_pages'] = scanned
    machine['source']['table_count'] = sum(len(page['tables']) for page in pages)
    machine['source']['document_id'] = Path(document_dir).name if document_dir else ''
    if scanned:
        machine['warnings'].append('Для сканов применены локальные RapidOCR ONNX и Tesseract; ошибки букв, чисел и таблиц возможны. Обязательно сверьте каждую позицию с оригиналом.')
    if ocr_errors:
        machine['warnings'].append('Ошибки OCR: ' + '; '.join(ocr_errors[:5]))
    machine['warnings'] = [warning for warning in machine['warnings'] if warning != 'На некоторых страницах мало текста: возможно, это изображения или скан. OCR не выполнялся.']
    progress('done', page=total, total=total, message=f'Готово: {total} страниц, {sum(len(group["items"]) for group in machine["partGroups"])} деталей')
    return {'machine': machine, 'pages': total, 'gallery': gallery, 'ocr_pages': scanned, 'ocr_errors': ocr_errors}
