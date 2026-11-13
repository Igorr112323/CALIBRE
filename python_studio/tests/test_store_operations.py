import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from operations import apply_operations, chat, selected_context
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
