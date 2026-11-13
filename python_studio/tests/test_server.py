import copy
import io
import json
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

    def tearDown(self):
        self.client.close()
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
        self.assertEqual(result.status_code, 400, result.text)
        self.assertIn('не полностью', result.json()['detail'])
        saved = self.client.get('/api/project').json()
        self.assertEqual(saved['version'], version)
        self.assertEqual(saved['project']['machines'][0]['adjustments'][0]['steps'][0]['text'], 'А' * 651)

    def test_transactional_chat_with_fake_model(self):
        data = self.client.get('/api/project').json()
        machine = data['project']['machines'][0]
        screen = {'screen': 'wizard', 'tab': 'adjustments', 'adjustment': machine['adjustments'][0]['id'], 'step': '1'}
        context = selected_context(machine, screen, '')
        response = {'reply': 'Изменено', 'operations': [{'action': 'set', 'path': '/adjustments/0/steps/1/text', 'value': 'Тестовая правка.', 'title': ''}]}
        payload = {'machine_id': machine['id'], 'screen': screen, 'message': 'Измени шаг', 'version': data['version'], 'profile': '3b', 'history': []}
        with mock.patch.object(main, 'chat', return_value=(response, context)):
            result = self.client.post('/api/chat', json=payload, headers=self.headers)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['changed'], 1)
            self.assertEqual(result.json()['project']['machines'][0]['adjustments'][0]['steps'][1]['text'], 'Тестовая правка.')
            self.assertEqual(self.client.post('/api/chat', json=payload, headers=self.headers).status_code, 409)
        self.assertNotEqual(self.client.get('/api/project').json()['version'], data['version'])


if __name__ == '__main__':
    unittest.main()
