import json
import re
import uuid

from local_ai import CHAT_SCHEMA, SUMMARY_SCHEMA, SYSTEM_PROMPT

SECTION_IDS = ['adjustments', 'parts', 'maintenance', 'nodes', 'storage', 'specs', 'faults']
SECTION_TITLES = ['Регулировки', 'Детали', 'ТО и смазка', 'Узлы', 'Хранение', 'ТТХ и агрегатирование', 'Неисправности и ТБ']


def machine_by_id(project, machine_id):
    machine = next((item for item in project['machines'] if item['id'] == machine_id), None)
    if machine is None:
        raise ValueError('Машина не найдена.')
    return machine


def selected_context(machine, screen, selection):
    screen = screen if isinstance(screen, dict) else {}
    tab = str(screen.get('tab') or 'adjustments')
    if tab == 'problems':
        tab = 'faults'
    result = {'screen': screen, 'selection': selection, 'model': machine['model'], 'machine_type': machine['machine_type'], 'tab': tab, 'allowed': []}
    allowed = result['allowed']
    omitted = False
    if screen.get('screen') == 'wizard':
        index = next((i for i, item in enumerate(machine['adjustments']) if item['id'] == screen.get('adjustment')), None)
        if index is not None:
            item = machine['adjustments'][index]
            for field in ('title', 'subtitle'):
                allowed.append({'path': f'/adjustments/{index}/{field}', 'value': item.get(field, '')})
            for n, step in enumerate(item.get('steps', [])):
                for field in ('title', 'text'):
                    allowed.append({'path': f'/adjustments/{index}/steps/{n}/{field}', 'value': step.get(field, '')})
            result['add_step_path'] = f'/adjustments/{index}/steps'
            result['insert_step_paths'] = [f'/adjustments/{index}/steps/{n}' for n in range(len(item.get('steps', [])))]
            result['remove_step_paths'] = list(result['insert_step_paths'])
    elif screen.get('screen') == 'part':
        group_index = next((i for i, item in enumerate(machine['partGroups']) if item['id'] == screen.get('group')), None)
        if group_index is not None:
            group = machine['partGroups'][group_index]
            index = next((i for i, item in enumerate(group['items']) if str(item['position']) == str(screen.get('position'))), None)
            if index is not None:
                item = group['items'][index]
                for field in ('name', 'designation', 'quantity', 'note', 'purpose', 'inspection'):
                    allowed.append({'path': f'/partGroups/{group_index}/items/{index}/{field}', 'value': str(item.get(field, ''))})
    elif tab == 'adjustments':
        result['add_adjustment_path'] = '/adjustments'
        result['remove_item_paths'] = []
        omitted = len(machine['adjustments']) > 100
        for index, item in enumerate(machine['adjustments'][:100]):
            result['remove_item_paths'].append(f'/adjustments/{index}')
            for field in ('title', 'subtitle'):
                allowed.append({'path': f'/adjustments/{index}/{field}', 'value': item.get(field, '')})
    else:
        index = next((i for i, section in enumerate(machine['sections']) if section['id'] == tab), None)
        if index is not None:
            section = machine['sections'][index]
            result['section_title'] = section.get('title', '')
            result['add_item_path'] = f'/sections/{index}/items'
            result['remove_item_paths'] = []
            chosen = str(screen.get('item') or '')
            omitted = not chosen and len(section['items']) > 100
            items = section['items'] if chosen else section['items'][:100]
            for n, item in enumerate(items):
                if chosen and item['id'] != chosen:
                    continue
                result['remove_item_paths'].append(f'/sections/{index}/items/{n}')
                for field in ('title', 'text', 'summary', 'badge', 'reason', 'subtitle'):
                    if field in item or field in ('text', 'summary'):
                        allowed.append({'path': f'/sections/{index}/items/{n}/{field}', 'value': str(item.get(field, ''))})
                for step_index, step in enumerate(item.get('steps', [])):
                    if isinstance(step, str):
                        allowed.append({'path': f'/sections/{index}/items/{n}/steps/{step_index}', 'value': step})
    bounded = []
    remaining = 10000
    for entry in allowed[:250]:
        if remaining <= 100:
            break
        value = entry['value'][:min(650, remaining)]
        bounded.append({**entry, 'value': value, **({'truncated': True} if len(value) < len(entry['value']) else {})})
        remaining -= len(value) + len(entry['path']) + 40
    result['allowed'] = bounded
    result['context_truncated'] = omitted or len(bounded) < len(allowed) or any(item.get('truncated') for item in bounded)
    return result


def split_path(path):
    if not isinstance(path, str) or not path.startswith('/') or '..' in path:
        raise ValueError('ИИ предложил неверный путь.')
    parts = path[1:].split('/')
    if not parts or any(not re.fullmatch(r'[A-Za-z0-9_-]+', part) for part in parts):
        raise ValueError('ИИ предложил неверный путь.')
    return parts


def locate(root, parts):
    node = root
    for part in parts:
        if isinstance(node, list):
            if not part.isdigit() or int(part) >= len(node):
                raise ValueError('Элемент, выбранный ИИ, не найден.')
            node = node[int(part)]
        elif isinstance(node, dict) and part in node:
            node = node[part]
        else:
            raise ValueError('Поле, выбранное ИИ, не найдено.')
    return node


def apply_operations(machine, context, operations):
    paths = {item['path'] for item in context['allowed']}
    truncated = {item['path'] for item in context['allowed'] if item.get('truncated')}
    prepared = []
    removals = [op['path'] for op in operations if op['action'] in ('remove_step', 'remove_item')]
    for operation in operations:
        action, path, value, title = (operation[key] for key in ('action', 'path', 'value', 'title'))
        if action not in ('remove_step', 'remove_item') and not value.strip():
            raise ValueError('ИИ предложил пустое содержимое; изменения не применены.')
        if action not in ('remove_step', 'remove_item') and any(path.startswith(prefix + '/') for prefix in removals):
            raise ValueError('Нельзя одновременно удалить элемент и изменить его содержимое.')
        parts = split_path(path)
        if action == 'set':
            if path not in paths:
                raise ValueError('ИИ предложил изменить поле вне выбранного экрана; изменения не применены.')
            if path in truncated:
                raise ValueError('Длинное поле показано ИИ не полностью. Измените его вручную; изменения не применены.')
            parent = locate(machine, parts[:-1])
            key = parts[-1]
            if isinstance(parent, list):
                if not key.isdigit() or int(key) >= len(parent):
                    raise ValueError('Некорректное поле для изменения.')
            elif not isinstance(parent, dict):
                raise ValueError('Некорректное поле для изменения.')
            prepared.append((action, parent, key, value, title, parts))
        elif action == 'add_step':
            if path == context.get('add_step_path'):
                prepared.append((action, locate(machine, parts), None, value, title, parts))
            elif path in context.get('insert_step_paths', []):
                target = locate(machine, parts[:-1])
                prepared.append((action, target, locate(machine, parts), value, title, parts))
            else:
                raise ValueError('ИИ предложил шаг вне выбранной регулировки.')
        elif action == 'add_item':
            if path != context.get('add_item_path'):
                raise ValueError('ИИ предложил пункт вне выбранного раздела.')
            prepared.append((action, locate(machine, parts), None, value, title, parts))
        elif action == 'add_adjustment':
            if path != context.get('add_adjustment_path'):
                raise ValueError('ИИ предложил регулировку вне выбранного раздела.')
            prepared.append((action, machine['adjustments'], None, value, title, parts))
        elif action in ('remove_step', 'remove_item'):
            if path not in context.get('remove_step_paths' if action == 'remove_step' else 'remove_item_paths', []):
                raise ValueError('ИИ предложил удаление вне выбранного экрана.')
            prepared.append((action, locate(machine, parts[:-1]), locate(machine, parts), value, title, parts))
        else:
            raise ValueError('Неизвестное действие ИИ.')
    changed = 0
    for action, target, anchor, value, title, parts in prepared:
        if action == 'set':
            if isinstance(target, list):
                target[int(anchor)] = value
            else:
                target[anchor] = value
            if len(parts) == 3 and parts[0] == 'adjustments' and parts[2] == 'title':
                section = next((item for item in machine['sections'] if item['id'] == 'adjustments'), None)
                if section:
                    match = next((item for item in section['items'] if item['id'] == target['id']), None)
                    if match:
                        match['title'] = value
        elif action == 'add_step':
            step = {'title': title or f'Шаг {len(target)+1}', 'text': value}
            if anchor is None:
                target.append(step)
            else:
                index = next((i for i, item in enumerate(target) if item is anchor), None)
                if index is None:
                    raise ValueError('Исходный шаг больше не найден.')
                target.insert(index + 1, step)
        elif action == 'add_item':
            target.append({'id': 'ai-' + uuid.uuid4().hex[:12], 'title': title or 'Новый пункт', 'text': value})
        elif action == 'add_adjustment':
            identifier = 'ai-' + uuid.uuid4().hex[:12]
            adjustment = {'id': identifier, 'title': title or 'Новая регулировка', 'subtitle': '', 'steps': [{'title': 'Шаг 1', 'text': value}]}
            target.append(adjustment)
            section = next((item for item in machine['sections'] if item['id'] == 'adjustments'), None)
            if section:
                section['items'].append({'id': identifier, 'title': adjustment['title'], 'badge': ''})
        else:
            if action == 'remove_step' and len(target) <= 1:
                raise ValueError('Нельзя удалить единственный шаг регулировки.')
            index = next((i for i, item in enumerate(target) if item is anchor), None)
            if index is None:
                raise ValueError('Удаляемый элемент больше не найден.')
            removed = target.pop(index)
            if action == 'remove_item' and parts[0] == 'adjustments':
                section = next((item for item in machine['sections'] if item['id'] == 'adjustments'), None)
                if section:
                    section['items'] = [item for item in section['items'] if item['id'] != removed['id']]
        changed += 1
    return changed


def chat(ai, machine, screen, selection, instruction, history, profile, custom, acceleration):
    if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 4000:
        raise ValueError('Введите запрос длиной до 4000 символов.')
    context = selected_context(machine, screen, selection)
    rules = '''Доступные действия: set заменяет только поле из списка allowed по его path; add_step добавляет шаг по add_step_path в конец или по одному из insert_step_paths непосредственно после соответствующего шага; add_item добавляет пункт по add_item_path; add_adjustment создаёт регулировку по add_adjustment_path с первым шагом. remove_step и remove_item удаляют элемент только по соответствующему списку путей. Для каждого действия верни action, path, value и title. В set title оставь пустой строкой; при удалении value и title оставь пустыми. При добавлении нескольких шагов после одной позиции перечисли их в обратном порядке. Не изменяй ничего вне выбранного экрана. Если пользователь задаёт вопрос, а не просит изменить, верни пустой список operations. Не сокращай текст при правке, если этого не просили. Сохраняй точные числа, если пользователь не требует изменить их. Поля с truncated=true показаны не полностью: их нельзя изменить через set; для их правки предложи ручной редактор. Если контекст сокращён, объясни пользователю, что для правки остальных пунктов их нужно открыть. Для полей источника и изображений операций нет; объясни ограничение. Не используй дополнительные пути.'''
    safe_history = [entry for entry in history[-4:] if isinstance(entry, dict) and entry.get('role') in ('user', 'assistant') and isinstance(entry.get('content'), str)]
    messages = [{'role': 'system', 'content': SYSTEM_PROMPT + '\n' + rules}]
    messages.extend({'role': entry['role'], 'content': entry['content'][:750]} for entry in safe_history)
    messages.append({'role': 'user', 'content': json.dumps({'контекст': context, 'запрос': instruction}, ensure_ascii=False)})
    answer = ai.complete(messages, CHAT_SCHEMA, profile, custom, acceleration, 4096)
    allowed = {item['path'] for item in context['allowed']}
    for op in answer['operations']:
        if op['action'] == 'set' and op['path'] not in allowed:
            raise ValueError('ИИ предложил правку вне выбранного экрана; данные не изменены.')
    if context['context_truncated']:
        answer['reply'] = answer['reply'].rstrip() + '\n\nКонтекст ИИ сокращён. Откройте нужный пункт; поля, показанные не полностью, можно изменить вручную.'
    return answer, context


def enrich(ai, machine, progress, profile, custom, acceleration):
    records = []
    section_by_id = {section['id']: section for section in machine['sections']}
    for section_id, section_title in zip(SECTION_IDS, SECTION_TITLES):
        items = []
        if section_id == 'adjustments':
            items = [{'key': item['id'], 'title': item['title'], 'text': ' '.join(step['text'] for step in item['steps']), 'target': item} for item in machine['adjustments']]
        elif section_id == 'parts':
            items = [{'key': group['id'] + ':' + str(item['position']), 'title': item['name'], 'text': ' '.join(str(item.get(key, '')) for key in ('designation', 'quantity', 'note', 'purpose')), 'target': item} for group in machine['partGroups'] for item in group['items']]
        else:
            items = [{'key': item['id'], 'title': item['title'], 'text': item.get('text', ''), 'target': item} for item in section_by_id.get(section_id, {}).get('items', [])]
        records.append((section_title, items))
    total = sum((len(items) + 4) // 5 for _, items in records)
    done = 0
    errors = []
    for section_title, items in records:
        if not items:
            progress('ai', current=done, total=total, message=f'{section_title}: исходные данные не найдены')
        for offset in range(0, len(items), 5):
            batch = items[offset:offset + 5]
            progress('ai', current=done, total=total, message=f'Локальный ИИ: {section_title}, пакет {offset // 5 + 1}')
            prompt = 'Для каждого элемента подготовь краткое объяснение по исходному тексту, не меняя и не выдумывая числовые значения. Верни исходные id без изменений. Исходный текст остаётся в каталоге.\n' + json.dumps([{'id': item['key'], 'title': item['title'], 'text': item['text'][:1800]} for item in batch], ensure_ascii=False)
            try:
                result = ai.complete([{'role': 'system', 'content': SYSTEM_PROMPT}, {'role': 'user', 'content': prompt}], SUMMARY_SCHEMA, profile, custom, acceleration, 1500)
                mapping = {item['key']: item['target'] for item in batch}
                seen = set()
                for item in result['items']:
                    if item['id'] not in mapping or item['id'] in seen:
                        raise ValueError('ИИ вернул неизвестный или повторный id.')
                    seen.add(item['id'])
                if seen != set(mapping):
                    raise ValueError('ИИ пропустил элементы пакета.')
                for item in result['items']:
                    target = mapping[item['id']]
                    if 'steps' in target and target['steps'] and isinstance(target['steps'][0], dict):
                        target['steps'][0]['summary'] = item['summary']
                    elif 'position' in target:
                        target['purpose'] = (str(target.get('purpose') or '') + ' Пояснение ИИ: ' + item['summary']).strip()
                    else:
                        target['summary'] = item['summary']
            except Exception as error:
                errors.append(f'{section_title}, пакет {offset // 5 + 1}: {str(error)[:150]}')
            done += 1
    if errors:
        machine['warnings'].append('Часть ИИ-объяснений не создана; исходные данные сохранены. ' + '; '.join(errors[:5]))
    progress('ai', current=total, total=total, message=f'ИИ-обработка завершена; ошибок пакетов: {len(errors)}')
    return errors
