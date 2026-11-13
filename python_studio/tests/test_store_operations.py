import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ai_review import build_review
from operations import apply_operations, chat, enrich, quantities, selected_context
from store import ConflictError, ProjectStore, validate_project


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ProjectStore(Path(self.temp.name) / 'project.json')
        self.project, self.version = self.store.get()

    def tearDown(self):
        self.temp.cleanup()

    def test_optimistic_lock_and_undo(self):
        project = copy.deepcopy(self.project)
        project['machines'][0]['model'] = 'Новая машина'
        version = self.store.save(project, self.version)
        with self.assertRaises(ConflictError):
            self.store.save(self.project, self.version)
        restored, _ = self.store.undo(version)
        self.assertEqual(restored['machines'][0]['model'], self.project['machines'][0]['model'])

    def test_reject_script_and_bad_identifier(self):
        project = copy.deepcopy(self.project)
        project['machines'][0]['adjustments'][0]['id'] = 'x" onclick="alert(1)'
        with self.assertRaises(ValueError):
            validate_project(project)
        project = copy.deepcopy(self.project)
        project['machines'][0]['adjustments'][0]['steps'][0]['image'] = '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"/>'
        with self.assertRaises(ValueError):
            validate_project(project)

    def test_ai_changes_only_selected_screen(self):
        machine = self.project['machines'][0]
        context = selected_context(machine, {'screen': 'wizard', 'tab': 'adjustments', 'adjustment': machine['adjustments'][0]['id'], 'step': '1'}, '')
        self.assertEqual(context['selected_step_path'], '/adjustments/0/steps/1')
        self.assertEqual({item['path'] for item in context['allowed']}, {'/adjustments/0/steps/1/title', '/adjustments/0/steps/1/text'})
        self.assertEqual(context['remove_step_paths'], ['/adjustments/0/steps/1'])
        self.assertEqual(context['adjustment_title'], machine['adjustments'][0]['title'])
        operation = {'action': 'set', 'path': '/adjustments/0/steps/1/text', 'value': 'Обновлённый шаг.', 'title': ''}
        self.assertEqual(apply_operations(machine, context, [operation]), 1)
        self.assertEqual(machine['adjustments'][0]['steps'][1]['text'], 'Обновлённый шаг.')
        operation['path'] = '/adjustments/1/steps/1/text'
        with self.assertRaises(ValueError):
            apply_operations(machine, context, [operation])

    def test_truncated_value_cannot_be_replaced_by_ai(self):
        machine = self.project['machines'][0]
        step = machine['adjustments'][0]['steps'][0]
        step['text'] = 'А' * 651
        original_title = step['title']
        context = selected_context(machine, {'screen': 'wizard', 'tab': 'adjustments', 'adjustment': machine['adjustments'][0]['id']}, '')
        field = next(item for item in context['allowed'] if item['path'] == '/adjustments/0/steps/0/text')
        self.assertEqual(len(field['value']), 650)
        self.assertTrue(field['truncated'])
        self.assertTrue(context['context_truncated'])
        operations = [
            {'action': 'set', 'path': '/adjustments/0/steps/0/title', 'value': 'Изменённый заголовок', 'title': ''},
            {'action': 'set', 'path': '/adjustments/0/steps/0/text', 'value': 'Сокращённый текст', 'title': ''},
        ]
        with self.assertRaisesRegex(ValueError, 'не полностью'):
            apply_operations(machine, context, operations)
        self.assertEqual(step['title'], original_title)
        self.assertEqual(step['text'], 'А' * 651)
        ai = mock.Mock()
        ai.complete.return_value = {'reply': 'Ответ', 'operations': []}
        answer, _ = chat(ai, machine, context['screen'], '', 'Объясни', [], '3b', '', 'cpu')
        self.assertIn('Контекст ИИ сокращён', answer['reply'])
        self.assertIn('вручную', answer['reply'])

    def test_untruncated_value_at_limit_can_be_edited(self):
        machine = self.project['machines'][0]
        step = machine['adjustments'][0]['steps'][0]
        step['text'] = 'А' * 650
        context = selected_context(machine, {'screen': 'wizard', 'tab': 'adjustments', 'adjustment': machine['adjustments'][0]['id']}, '')
        self.assertFalse(context['context_truncated'])
        operation = {'action': 'set', 'path': '/adjustments/0/steps/0/text', 'value': 'Новый текст', 'title': ''}
        self.assertEqual(apply_operations(machine, context, [operation]), 1)
        self.assertEqual(step['text'], 'Новый текст')

    def test_large_section_reports_omissions_and_selected_item_after_limit(self):
        machine = self.project['machines'][0]
        section_index = next(i for i, item in enumerate(machine['sections']) if item['id'] == 'storage')
        section = machine['sections'][section_index]
        section['items'] = [{'id': f'item-{index}', 'title': f'Пункт {index}', 'text': 'Текст'} for index in range(101)]
        full = selected_context(machine, {'screen': 'machine', 'tab': 'storage'}, '')
        self.assertTrue(full['context_truncated'])
        chosen = selected_context(machine, {'screen': 'machine', 'tab': 'storage', 'item': 'item-100'}, '')
        path = f'/sections/{section_index}/items/100/text'
        self.assertIn(path, [item['path'] for item in chosen['allowed']])
        self.assertFalse(chosen['context_truncated'])
        operation = {'action': 'set', 'path': path, 'value': 'Обновлённый текст', 'title': ''}
        apply_operations(machine, chosen, [operation])
        self.assertEqual(section['items'][100]['text'], 'Обновлённый текст')

    def test_new_or_changed_quantities_in_chat_are_flagged_not_treated_as_verified(self):
        machine = self.project['machines'][0]
        adjustment = machine['adjustments'][4]
        screen = {'screen': 'wizard', 'tab': 'adjustments', 'adjustment': adjustment['id'], 'step': '1'}
        response = {'reply': 'Слева 2 мм, справа 12 мм.', 'operations': [{'action': 'set', 'path': '/adjustments/4/steps/1/text', 'value': 'Слева 12 мм, момент 30 Н·м.', 'title': ''}]}
        ai = mock.Mock()
        ai.complete.return_value = response
        answer, _ = chat(ai, machine, screen, '', 'Замени 10 мм на 12 мм', [], '3b', '', 'cpu')
        self.assertIn('2 мм', answer['unverified_quantities'])
        self.assertIn('30 Н·м', answer['unverified_quantities'])
        self.assertNotIn('12 мм', answer['unverified_quantities'])
        self.assertEqual([key for key, _ in quantities('1,2 м и 1.2 м')][0], [key for key, _ in quantities('1,2 м и 1.2 м')][1])

    def test_import_summaries_do_not_insert_new_numbers_or_units(self):
        for generated in ['Зазор 12 мм.', 'Зазор 10 см.', 'Новый размер — 12.']:
            machine = {'adjustments': [], 'partGroups': [], 'sections': [{'id': 'maintenance', 'items': [{'id': 'one', 'title': 'Зазор', 'text': 'Установите зазор 10 мм.', 'summary': 'Старое пояснение'}]}], 'warnings': []}
            ai = mock.Mock()
            ai.complete.return_value = {'items': [{'id': 'one', 'summary': generated}]}
            errors = enrich(ai, machine, lambda *a, **k: None, '3b', '', 'cpu')
            self.assertTrue(errors, generated)
            self.assertEqual(machine['sections'][0]['items'][0]['summary'], 'Старое пояснение')
            self.assertIn('числовое значение или единицу', machine['warnings'][0])
        machine = {'adjustments': [], 'partGroups': [], 'sections': [{'id': 'maintenance', 'items': [{'id': 'one', 'title': 'Зазор', 'text': 'Установите зазор 10 мм.'}]}], 'warnings': []}
        ai = mock.Mock()
        ai.complete.return_value = {'items': [{'id': 'one', 'summary': 'Проверьте зазор 10 мм.'}]}
        self.assertEqual(enrich(ai, machine, lambda *a, **k: None, '3b', '', 'cpu'), [])
        self.assertEqual(machine['sections'][0]['items'][0]['summary'], 'Проверьте зазор 10 мм.')

    def test_review_shows_exact_values_and_removal_with_image_warning(self):
        before = self.project['machines'][0]
        after = copy.deepcopy(before)
        context = selected_context(after, {'screen': 'wizard', 'tab': 'adjustments', 'adjustment': after['adjustments'][0]['id'], 'step': '1'}, '')
        original_step = before['adjustments'][0]['steps'][1]
        apply_operations(after, context, [{'action': 'remove_step', 'path': '/adjustments/0/steps/1', 'value': '', 'title': ''}])
        changes = build_review(before, after)
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]['before_path'], '/adjustments/0/steps/1')
        self.assertFalse(changes[0]['after_present'])
        self.assertIn(original_step['title'], changes[0]['before'])
        self.assertIn(original_step['text'], changes[0]['before'])
        self.assertIn('Изображение:', changes[0]['before'])
        self.assertNotIn('<svg', changes[0]['before'])

    def test_review_includes_automatic_search_catalog_sync(self):
        before = self.project['machines'][0]
        after = copy.deepcopy(before)
        context = selected_context(after, {'screen': 'machine', 'tab': 'adjustments'}, '')
        apply_operations(after, context, [{'action': 'remove_item', 'path': '/adjustments/0', 'value': '', 'title': ''}])
        changes = build_review(before, after)
        paths = {change['before_path'] for change in changes}
        self.assertIn('/adjustments/0', paths)
        section_index = next(i for i, section in enumerate(before['sections']) if section['id'] == 'adjustments')
        item_index = next(i for i, item in enumerate(before['sections'][section_index]['items']) if item['id'] == before['adjustments'][0]['id'])
        self.assertIn(f'/sections/{section_index}/items/{item_index}', paths)
        self.assertTrue(all(not change['after_present'] for change in changes))

    def test_review_does_not_hide_non_image_text_that_looks_like_a_data_url(self):
        value = 'data:image/png;base64,QQ== последующий текст'
        change = build_review({'text': 'До'}, {'text': value})[0]
        self.assertEqual(change['after'], value)

    def test_review_rejects_unreviewably_large_change(self):
        before = {'steps': [{'title': 'Старое', 'text': 'А' * 1000}]}
        after = {'steps': []}
        with mock.patch('ai_review.MAX_REVIEW_CHARACTERS', 100):
            with self.assertRaisesRegex(ValueError, 'слишком много'):
                build_review(before, after)
        self.assertEqual(len(before['steps'][0]['text']), 1000)

    def test_contradictory_operation_fields_are_rejected_without_changes(self):
        machine = self.project['machines'][0]
        initial = copy.deepcopy(machine)
        screen = {'screen': 'wizard', 'tab': 'adjustments', 'adjustment': machine['adjustments'][0]['id'], 'step': '1'}
        context = selected_context(machine, screen, '')
        for op in [
            {'action': 'set', 'path': '/adjustments/0/steps/1/text', 'value': 'Новый текст', 'title': 'Лишний заголовок'},
            {'action': 'remove_step', 'path': '/adjustments/0/steps/1', 'value': 'Лишний текст', 'title': ''},
            {'action': 'remove_step', 'path': '/adjustments/0/steps/1', 'value': '', 'title': 'Лишний заголовок'},
        ]:
            with self.assertRaisesRegex(ValueError, 'лишн'):
                apply_operations(machine, context, [op])
            self.assertEqual(machine, initial)

    def test_selected_step_cannot_edit_or_delete_neighbor(self):
        machine = self.project['machines'][0]
        initial = copy.deepcopy(machine)
        screen = {'screen': 'wizard', 'tab': 'adjustments', 'adjustment': machine['adjustments'][0]['id'], 'step': '1'}
        context = selected_context(machine, screen, '')
        for op in [
            {'action': 'set', 'path': '/adjustments/0/steps/0/text', 'value': 'Чужой шаг', 'title': ''},
            {'action': 'set', 'path': '/adjustments/0/title', 'value': 'Чужой заголовок', 'title': ''},
            {'action': 'remove_step', 'path': '/adjustments/0/steps/0', 'value': '', 'title': ''},
        ]:
            with self.assertRaises(ValueError):
                apply_operations(machine, context, [op])
            self.assertEqual(machine, initial)

    def test_add_and_remove_steps(self):
        machine = self.project['machines'][0]
        adjustment = machine['adjustments'][0]
        context = selected_context(machine, {'screen': 'wizard', 'tab': 'adjustments', 'adjustment': adjustment['id']}, '')
        operation = {'action': 'add_step', 'path': '/adjustments/0/steps/0', 'value': 'Новая операция', 'title': 'После первого'}
        apply_operations(machine, context, [operation])
        self.assertEqual(adjustment['steps'][1]['text'], 'Новая операция')
        context = selected_context(machine, {'screen': 'wizard', 'tab': 'adjustments', 'adjustment': adjustment['id']}, '')
        apply_operations(machine, context, [{'action': 'remove_step', 'path': '/adjustments/0/steps/1', 'value': '', 'title': ''}])
        self.assertNotEqual(adjustment['steps'][1]['text'], 'Новая операция')

    def test_step_insert_and_delete_keep_uploaded_images_with_original_steps(self):
        machine = self.project['machines'][0]
        adjustment = machine['adjustments'][0]
        prefix = f"adjustments/{adjustment['id']}/"
        images = ['data:image/png;base64,QQ==', 'data:image/png;base64,Qg==', 'data:image/png;base64,Qw==']
        machine['media'].update({prefix + str(index): image for index, image in enumerate(images)})
        context = selected_context(machine, {'screen': 'wizard', 'tab': 'adjustments', 'adjustment': adjustment['id'], 'step': '0'}, '')
        apply_operations(machine, context, [{'action': 'add_step', 'path': '/adjustments/0/steps/0', 'value': 'Новый текст', 'title': 'Новый шаг'}])
        self.assertEqual(machine['media'][prefix + '0'], images[0])
        self.assertNotIn(prefix + '1', machine['media'])
        self.assertEqual(machine['media'][prefix + '2'], images[1])
        self.assertEqual(machine['media'][prefix + '3'], images[2])
        context = selected_context(machine, {'screen': 'wizard', 'tab': 'adjustments', 'adjustment': adjustment['id'], 'step': '2'}, '')
        apply_operations(machine, context, [{'action': 'remove_step', 'path': '/adjustments/0/steps/2', 'value': '', 'title': ''}])
        self.assertEqual(machine['media'][prefix + '0'], images[0])
        self.assertNotIn(prefix + '1', machine['media'])
        self.assertEqual(machine['media'][prefix + '2'], images[2])
        self.assertNotIn(prefix + '3', machine['media'])
        self.assertNotIn(images[1], machine['media'].values())

    def test_orphan_image_cannot_silently_attach_to_added_step(self):
        machine = self.project['machines'][0]
        adjustment = machine['adjustments'][0]
        orphan = f"adjustments/{adjustment['id']}/{len(adjustment['steps'])}"
        machine['media'][orphan] = 'data:image/png;base64,QQ=='
        before = copy.deepcopy(machine)
        context = selected_context(machine, {'screen': 'wizard', 'tab': 'adjustments', 'adjustment': adjustment['id'], 'step': '0'}, '')
        with self.assertRaisesRegex(ValueError, 'изображение вне списка'):
            apply_operations(machine, context, [{'action': 'add_step', 'path': '/adjustments/0/steps', 'value': 'Новый текст', 'title': 'Новый шаг'}])
        self.assertEqual(machine, before)

    def test_removing_adjustment_deletes_its_uploaded_images_in_review(self):
        before = self.project['machines'][0]
        adjustment = before['adjustments'][0]
        key = f"adjustments/{adjustment['id']}/1"
        before['media'][key] = 'data:image/png;base64,QQ=='
        after = copy.deepcopy(before)
        context = selected_context(after, {'screen': 'machine', 'tab': 'adjustments'}, '')
        apply_operations(after, context, [{'action': 'remove_item', 'path': '/adjustments/0', 'value': '', 'title': ''}])
        self.assertNotIn(key, after['media'])
        image_change = next(item for item in build_review(before, after) if item['before_path'] == '/media/adjustments~1' + adjustment['id'] + '~11')
        self.assertIn('Изображение:', image_change['before'])
        self.assertFalse(image_change['after_present'])

    def test_multiple_removals_use_original_targets(self):
        machine = self.project['machines'][0]
        original = [item['id'] for item in machine['adjustments']]
        context = selected_context(machine, {'screen': 'machine', 'tab': 'adjustments'}, '')
        operations = [
            {'action': 'remove_item', 'path': '/adjustments/0', 'value': '', 'title': ''},
            {'action': 'remove_item', 'path': '/adjustments/1', 'value': '', 'title': ''},
        ]
        apply_operations(machine, context, operations)
        self.assertEqual([item['id'] for item in machine['adjustments']], original[2:])
        section = next(item for item in machine['sections'] if item['id'] == 'adjustments')
        self.assertFalse(any(item['id'] in original[:2] for item in section['items']))

    def test_add_adjustment_keeps_search_catalog_in_sync(self):
        machine = self.project['machines'][0]
        context = selected_context(machine, {'screen': 'machine', 'tab': 'adjustments'}, '')
        op = {'action': 'add_adjustment', 'path': '/adjustments', 'value': 'Проверить крепление', 'title': 'Проверка'}
        apply_operations(machine, context, [op])
        self.assertEqual(machine['adjustments'][-1]['title'], 'Проверка')
        section = next(item for item in machine['sections'] if item['id'] == 'adjustments')
        self.assertEqual(section['items'][-1]['id'], machine['adjustments'][-1]['id'])
        validate_project(self.project)


if __name__ == '__main__':
    unittest.main()
