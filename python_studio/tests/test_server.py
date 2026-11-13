import copy
import io
import json
from concurrent.futures import ThreadPoolExecutor
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import main
from operations import selected_context
from store import ProjectStore


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original = main.STORE
        main.STORE = ProjectStore(Path(self.temp.name) / 'project.json')
        self.client = TestClient(main.app)
        self.headers = {'X-Kolibri-Token': main.TOKEN}
        main.PROPOSALS.clear()

    def tearDown(self):
        self.client.close()
        main.PROPOSALS.clear()
        main.STORE = self.original
        self.temp.cleanup()

    def test_csrf_project_and_undo(self):
        data = self.client.get('/api/project').json()
        changed = copy.deepcopy(data['project'])
        changed['machines'][0]['model'] = 'Новое название'
        request = {'project': changed, 'version': data['version']}
        self.assertEqual(self.client.put('/api/project', json=request).status_code, 403)
        result = self.client.put('/api/project', json=request, headers=self.headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.client.put('/api/project', json=request, headers=self.headers).status_code, 409)
        result = self.client.post('/api/undo', json={'version': result.json()['version']}, headers=self.headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['project'], data['project'])

    def test_image_upload_and_preview(self):
        raw = io.BytesIO()
        Image.new('RGB', (20, 20), (50, 100, 50)).save(raw, 'PNG')
        response = self.client.post('/api/media', files={'file': ('test.png', raw.getvalue(), 'image/png')}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['image'].startswith('data:image/jpeg;base64,'))
        self.assertEqual(self.client.post('/api/media', files={'file': ('bad.png', b'not an image', 'image/png')}, headers=self.headers).status_code, 400)
        preview = self.client.get('/preview').text
        self.assertIn('/preview/catalog.js', preview)
        self.assertIn('preview.js', preview)
        self.assertIn('window.__KOLIBRI_CATALOG__', self.client.get('/preview/catalog.js').text)

    def test_local_ai_errors_are_returned_without_changing_project(self):
        data = self.client.get('/api/project').json()
        machine = data['project']['machines'][0]
        payload = {'machine_id': machine['id'], 'screen': {'screen': 'machine', 'tab': 'adjustments'}, 'message': 'Объясни', 'version': data['version'], 'profile': '3b', 'history': []}
        with mock.patch.object(main.AI, 'complete', side_effect=RuntimeError('Превышен контекст модели. Данные не изменены.')):
            result = self.client.post('/api/chat', json=payload, headers=self.headers)
        self.assertEqual(result.status_code, 503, result.text)
        self.assertIn('Превышен контекст модели', result.json()['detail'])
        self.assertEqual(self.client.get('/api/project').json()['version'], data['version'])

    def test_ai_cannot_overwrite_partially_shown_long_field(self):
        data = self.client.get('/api/project').json()
        machine = data['project']['machines'][0]
        step = machine['adjustments'][0]['steps'][0]
        step['text'] = 'А' * 651
        result = self.client.put('/api/project', json={'project': data['project'], 'version': data['version']}, headers=self.headers)
        self.assertEqual(result.status_code, 200, result.text)
        version = result.json()['version']
        screen = {'screen': 'wizard', 'tab': 'adjustments', 'adjustment': machine['adjustments'][0]['id']}
        context = selected_context(machine, screen, '')
        response = {'reply': 'Изменено', 'operations': [{'action': 'set', 'path': '/adjustments/0/steps/0/text', 'value': 'Короткий текст', 'title': ''}]}
        payload = {'machine_id': machine['id'], 'screen': screen, 'message': 'Измени шаг', 'version': version, 'profile': '3b', 'history': []}
        with mock.patch.object(main, 'chat', return_value=(response, context)):
            result = self.client.post('/api/chat', json=payload, headers=self.headers)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertIsNone(result.json()['proposal'])
        self.assertIn('не полностью', result.json()['warning'])
        saved = self.client.get('/api/project').json()
        self.assertEqual(saved['version'], version)
        self.assertEqual(saved['project']['machines'][0]['adjustments'][0]['steps'][0]['text'], 'А' * 651)

    def proposal_payload(self):
        data = self.client.get('/api/project').json()
        machine = data['project']['machines'][0]
        screen = {'screen': 'wizard', 'tab': 'adjustments', 'adjustment': machine['adjustments'][0]['id'], 'step': '1'}
        context = selected_context(machine, screen, '')
        response = {'reply': 'Объяснение.', 'operations': [{'action': 'set', 'path': '/adjustments/0/steps/1/text', 'value': 'Тестовая правка.', 'title': ''}]}
        payload = {'machine_id': machine['id'], 'screen': screen, 'message': 'Объясни, ничего не меняй', 'version': data['version'], 'profile': '3b', 'history': []}
        return data, context, response, payload

    def test_chat_never_saves_before_review_and_can_discard(self):
        data, context, response, payload = self.proposal_payload()
        with mock.patch.object(main, 'chat', return_value=(response, context)):
            result = self.client.post('/api/chat', json=payload, headers=self.headers)
        self.assertEqual(result.status_code, 200, result.text)
        body = result.json()
        self.assertEqual(body['changed'], 0)
        self.assertEqual(body['project'], data['project'])
        self.assertEqual(self.client.get('/api/project').json()['version'], data['version'])
        proposal = body['proposal']
        self.assertEqual(len(proposal['changes']), 1)
        change = proposal['changes'][0]
        self.assertEqual(change['before_path'], '/adjustments/0/steps/1/text')
        self.assertEqual(change['before'], data['project']['machines'][0]['adjustments'][0]['steps'][1]['text'])
        self.assertEqual(change['after'], 'Тестовая правка.')
        self.assertEqual(self.client.post('/api/chat/apply', json={'proposal_id': proposal['id']}).status_code, 403)
        self.assertEqual(self.client.post('/api/chat/discard', json={'proposal_id': proposal['id']}, headers=self.headers).status_code, 200)
        self.assertEqual(self.client.post('/api/chat/apply', json={'proposal_id': proposal['id']}, headers=self.headers).status_code, 410)
        self.assertEqual(self.client.get('/api/project').json()['version'], data['version'])

    def test_confirmation_applies_exact_reviewed_snapshot_once_and_supports_undo(self):
        data, context, response, payload = self.proposal_payload()
        with mock.patch.object(main, 'chat', return_value=(response, context)):
            proposal = self.client.post('/api/chat', json=payload, headers=self.headers).json()['proposal']
        tampered = {'proposal_id': proposal['id'], 'operations': [{'action': 'set', 'value': 'ПОДМЕНА'}], 'version': 'bad'}
        result = self.client.post('/api/chat/apply', json=tampered, headers=self.headers)
        self.assertEqual(result.status_code, 200, result.text)
        body = result.json()
        self.assertEqual(body['changed'], 1)
        self.assertEqual(body['project']['machines'][0]['adjustments'][0]['steps'][1]['text'], 'Тестовая правка.')
        self.assertEqual(self.client.post('/api/chat/apply', json=tampered, headers=self.headers).status_code, 410)
        restored = self.client.post('/api/undo', json={'version': body['version']}, headers=self.headers)
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()['project'], data['project'])

    def test_simultaneous_confirmation_consumes_proposal_once(self):
        data, context, response, payload = self.proposal_payload()
        with mock.patch.object(main, 'chat', return_value=(response, context)):
            proposal = self.client.post('/api/chat', json=payload, headers=self.headers).json()['proposal']
        def apply(_):
            return self.client.post('/api/chat/apply', json={'proposal_id': proposal['id']}, headers=self.headers).status_code
        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(apply, range(2)))
        self.assertEqual(sorted(statuses), [200, 410])
        self.assertEqual(self.client.get('/api/project').json()['project']['machines'][0]['adjustments'][0]['steps'][1]['text'], 'Тестовая правка.')
        self.assertEqual(len(main.STORE.history), 1)

    def test_stale_or_expired_proposal_cannot_be_applied(self):
        data, context, response, payload = self.proposal_payload()
        with mock.patch.object(main, 'chat', return_value=(response, context)):
            stale = self.client.post('/api/chat', json=payload, headers=self.headers).json()['proposal']
            expired = self.client.post('/api/chat', json=payload, headers=self.headers).json()['proposal']
        main.PROPOSALS[expired['id']]['expires'] = 0
        self.assertEqual(self.client.post('/api/chat/apply', json={'proposal_id': expired['id']}, headers=self.headers).status_code, 410)
        changed = copy.deepcopy(data['project'])
        changed['machines'][0]['model'] = 'Новое название вручную'
        saved = self.client.put('/api/project', json={'project': changed, 'version': data['version']}, headers=self.headers)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(self.client.post('/api/chat/apply', json={'proposal_id': stale['id']}, headers=self.headers).status_code, 409)
        self.assertEqual(self.client.post('/api/chat/apply', json={'proposal_id': stale['id']}, headers=self.headers).status_code, 410)
        self.assertEqual(self.client.get('/api/project').json()['project'], changed)

    def test_invalid_or_unchanged_operations_never_create_a_proposal(self):
        data, context, response, payload = self.proposal_payload()
        response['operations'][0]['path'] = '/adjustments/0/steps/0/text'
        with mock.patch.object(main, 'chat', return_value=(response, context)):
            result = self.client.post('/api/chat', json=payload, headers=self.headers)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertIsNone(result.json()['proposal'])
        self.assertIn('отклонены', result.json()['warning'])
        self.assertEqual(result.json()['reply'], 'Объяснение.')
        response['operations'][0]['path'] = '/adjustments/0/steps/1/text'
        response['operations'][0]['value'] = data['project']['machines'][0]['adjustments'][0]['steps'][1]['text']
        with mock.patch.object(main, 'chat', return_value=(response, context)):
            result = self.client.post('/api/chat', json=payload, headers=self.headers)
        self.assertIsNone(result.json()['proposal'])
        self.assertIn('не предложил фактических', result.json()['warning'])
        self.assertEqual(self.client.get('/api/project').json()['version'], data['version'])

    def test_version_change_during_inference_is_rejected(self):
        data, context, response, payload = self.proposal_payload()
        changed = copy.deepcopy(data['project'])
        changed['machines'][0]['model'] = 'Изменение во время ответа'
        def slow_chat(*args):
            main.STORE.save(changed, data['version'])
            return response, context
        with mock.patch.object(main, 'chat', side_effect=slow_chat):
            result = self.client.post('/api/chat', json=payload, headers=self.headers)
        self.assertEqual(result.status_code, 409, result.text)
        self.assertEqual(self.client.get('/api/project').json()['project'], changed)
        self.assertFalse(main.PROPOSALS)


if __name__ == '__main__':
    unittest.main()
