import copy
import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from local_ai import CHAT_SCHEMA, LocalAI, PROFILES, grammar_schema

SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['status'], 'properties': {'status': {'type': 'string', 'enum': ['ok']}}}


class Handler(BaseHTTPRequestHandler):
    requests = []
    result = '{"status":"ok"}'
    status = 200
    error = ''

    def do_POST(self):
        length = int(self.headers.get('Content-Length', '0'))
        body = json.loads(self.rfile.read(length))
        self.requests.append((self.path, body))
        response = {'choices': [{'message': {'content': self.result}}]} if self.status == 200 else {'error': {'message': self.error}}
        raw = json.dumps(response).encode()
        self.send_response(self.status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


class AITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join()

    def setUp(self):
        Handler.requests.clear()
        Handler.result = '{"status":"ok"}'
        Handler.status = 200
        Handler.error = ''
        self.ai = LocalAI()
        self.ai.backend = 'llama'
        self.ai.active_profile = 'custom'
        self.ai.model_name = 'test.gguf'
        self.ai.port = self.http.server_address[1]

    def test_schema_protocol_and_validation(self):
        output = self.ai.complete([{'role': 'user', 'content': 'Проверка'}], SCHEMA, 'custom', 'test.gguf')
        self.assertEqual(output, {'status': 'ok'})
        path, payload = Handler.requests[-1]
        self.assertEqual(path, '/v1/chat/completions')
        self.assertEqual(payload['response_format']['type'], 'json_schema')
        self.assertEqual(payload['response_format']['json_schema']['schema'], SCHEMA)
        Handler.result = '{"status":"bad"}'
        with self.assertRaisesRegex(ValueError, 'JSON'):
            self.ai.complete([{'role': 'user', 'content': 'Проверка'}], SCHEMA, 'custom', 'test.gguf')
        Handler.result = '{'
        with self.assertRaisesRegex(ValueError, 'JSON'):
            self.ai.complete([{'role': 'user', 'content': 'Проверка'}], SCHEMA, 'custom', 'test.gguf')

    def test_inference_http_and_connection_errors_are_explained(self):
        Handler.status = 400
        Handler.error = 'request (1478 tokens) exceeds the available context size (512 tokens)'
        with self.assertRaisesRegex(RuntimeError, 'Превышен контекст модели'):
            self.ai.complete([{'role': 'user', 'content': 'Проверка'}], SCHEMA, 'custom', 'test.gguf')
        Handler.status = 503
        Handler.error = 'model is loading'
        with self.assertRaisesRegex(RuntimeError, 'Код 503: model is loading'):
            self.ai.complete([{'role': 'user', 'content': 'Проверка'}], SCHEMA, 'custom', 'test.gguf')
        with mock.patch('local_ai.httpx.post', side_effect=httpx.ConnectError('offline')):
            with self.assertRaisesRegex(RuntimeError, 'Нет связи'):
                self.ai.complete([{'role': 'user', 'content': 'Проверка'}], SCHEMA, 'custom', 'test.gguf')

    def test_grammar_omits_unbounded_repetition_limits_but_keeps_shape(self):
        reduced = grammar_schema(CHAT_SCHEMA)
        self.assertNotIn('maxLength', reduced['properties']['reply'])
        self.assertNotIn('maxItems', reduced['properties']['operations'])
        self.assertEqual(reduced['required'], CHAT_SCHEMA['required'])
        self.assertEqual(reduced['properties']['operations']['items']['properties']['action']['enum'], CHAT_SCHEMA['properties']['operations']['items']['properties']['action']['enum'])
        self.assertIn('maxLength', CHAT_SCHEMA['properties']['reply'])

    @unittest.skipUnless(os.environ.get('KOLIBRI_TEST_LLAMA_PORT'), 'Реальный тестовый llama-server не указан')
    def test_real_application_grammar_starts_with_json_object(self):
        import httpx
        port = int(os.environ['KOLIBRI_TEST_LLAMA_PORT'])
        body = {'messages': [{'role': 'user', 'content': 'Return JSON.'}], 'temperature': 0, 'max_tokens': 30, 'stream': False, 'response_format': {'type': 'json_schema', 'json_schema': {'name': 'test', 'strict': True, 'schema': grammar_schema(CHAT_SCHEMA)}}}
        response = httpx.post(f'http://127.0.0.1:{port}/v1/chat/completions', json=body, timeout=30, trust_env=False)
        response.raise_for_status()
        content = response.json()['choices'][0]['message']['content']
        self.assertTrue(content.lstrip().startswith('{'), repr(content))

    def test_profiles_and_platform_selection(self):
        self.assertEqual(len(PROFILES['7b']['files']), 2)
        self.assertTrue(PROFILES['7b']['files'][0]['name'].endswith('00001-of-00002.gguf'))
        self.assertEqual(len(PROFILES['3b']['files'][0]['sha256']), 64)
        with mock.patch('local_ai.platform.system', return_value='Windows'), mock.patch('local_ai.platform.machine', return_value='AMD64'):
            self.assertEqual(self.ai._asset_pattern('cpu'), '-bin-win-cpu-x64.zip')
            self.assertEqual(self.ai._asset_pattern('vulkan'), '-bin-win-vulkan-x64.zip')
        with mock.patch('local_ai.platform.system', return_value='Linux'), mock.patch('local_ai.platform.machine', return_value='aarch64'):
            self.assertEqual(self.ai._asset_pattern('cpu'), '-bin-ubuntu-arm64.tar.gz')

    def test_safe_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'out'
            target.mkdir()
            bad_zip = root / 'bad.zip'
            with zipfile.ZipFile(bad_zip, 'w') as archive:
                archive.writestr('../outside', b'bad')
            with self.assertRaises(ValueError):
                self.ai._safe_extract(bad_zip, target)
            self.assertFalse((root / 'outside').exists())
            good_tar = root / 'good.tar.gz'
            with tarfile.open(good_tar, 'w:gz') as archive:
                value = b'library'
                file = tarfile.TarInfo('lib.so.1')
                file.size = len(value)
                archive.addfile(file, io.BytesIO(value))
                link = tarfile.TarInfo('lib.so')
                link.type = tarfile.SYMTYPE
                link.linkname = 'lib.so.1'
                archive.addfile(link)
            self.ai._safe_extract(good_tar, target)
            self.assertEqual((target / 'lib.so').read_bytes(), b'library')
            self.assertFalse((target / 'lib.so').is_symlink())

    @unittest.skipUnless(os.environ.get('KOLIBRI_TEST_LLAMA_PORT'), 'Реальный тестовый llama-server не указан')
    def test_real_llama_schema(self):
        self.ai.port = int(os.environ['KOLIBRI_TEST_LLAMA_PORT'])
        result = self.ai.complete([{'role': 'user', 'content': 'Return JSON.'}], SCHEMA, 'custom', 'test.gguf', max_tokens=100)
        self.assertEqual(result, {'status': 'ok'})


class ReleaseProfileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path_patch = mock.patch('local_ai.MODEL_DIR', self.root)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.ai = LocalAI()
        self.spec = copy.deepcopy(PROFILES['mehfak'])
        self.name = self.spec['files'][0]['name']
        self.content = b'GGUF-real-test-content'
        self.digest = hashlib.sha256(self.content).hexdigest()

    def test_profile_is_first_and_never_substitutes_an_ollama_model(self):
        self.assertEqual(next(iter(PROFILES)), 'mehfak')
        with mock.patch.object(self.ai, 'ollama_available', side_effect=AssertionError('Ollama must not be used')), \
             mock.patch.object(self.ai, 'ensure_model', return_value=self.root / self.name), \
             mock.patch.object(self.ai, 'ensure_engine', return_value=self.root / 'llama-server'), \
             mock.patch.object(self.ai, '_launch'):
            self.ai._start('mehfak', '', 'cpu')
        self.assertEqual(self.ai.active_profile, 'mehfak')
        self.assertEqual(self.ai.model_name, self.name)
        self.assertEqual(self.ai.status()['backend'], 'llama')

    def test_absent_release_is_explained_without_fallback(self):
        response = httpx.Response(404, request=httpx.Request('GET', self.spec['checksums']))
        with mock.patch('local_ai.httpx.get', return_value=response):
            with self.assertRaisesRegex(RuntimeError, 'ещё не опубликована'):
                self.ai.ensure_model('mehfak')
        self.assertFalse((self.root / self.name).exists())

    def test_download_is_verified_then_reused_offline_and_corruption_is_rejected(self):
        def download(urls, destination, label, **kwargs):
            self.assertEqual(urls, [self.spec['url']])
            self.assertEqual(kwargs['expected_sha'], self.digest)
            self.assertEqual(kwargs['max_bytes_exclusive'], self.spec['max_bytes_exclusive'])
            destination.write_bytes(self.content)
        with mock.patch.object(self.ai, '_release_checksum', return_value=self.digest), \
             mock.patch.object(self.ai, '_download', side_effect=download), \
             mock.patch('local_ai.shutil.disk_usage') as disk:
            disk.return_value.free = self.spec['max_bytes_exclusive'] + 500 * 1024 * 1024
            self.assertEqual(self.ai.ensure_model('mehfak').read_bytes(), self.content)
        sidecar = self.root / (self.name + '.sha256')
        self.assertEqual(sidecar.read_text(), self.digest + '  ' + self.name + '\n')
        with mock.patch.object(self.ai, '_release_checksum', side_effect=AssertionError('Offline cache should be used')):
            self.assertEqual(self.ai.ensure_model('mehfak'), self.root / self.name)
            (self.root / self.name).write_bytes(b'GGUF-corrupted')
            with self.assertRaisesRegex(RuntimeError, 'SHA-256'):
                self.ai.ensure_model('mehfak')

    def test_limit_is_exclusive_even_for_cached_model(self):
        (self.root / self.name).write_bytes(self.content)
        (self.root / (self.name + '.sha256')).write_text(self.digest + '  ' + self.name + '\n')
        self.spec['max_bytes_exclusive'] = len(self.content)
        with self.assertRaisesRegex(RuntimeError, 'Размер'):
            self.ai._ensure_release_model(self.spec)

    def test_malformed_or_ambiguous_checksums_and_symlinks_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'отсутствует'):
            self.ai._parse_release_checksum(self.digest + '  unrelated.gguf\n', self.name)
        with self.assertRaisesRegex(RuntimeError, 'неоднозначна'):
            self.ai._parse_release_checksum((self.digest + '  ' + self.name + '\n') * 2, self.name)
        target = self.root / 'target'
        target.write_bytes(self.content)
        (self.root / self.name).symlink_to(target)
        with self.assertRaisesRegex(RuntimeError, 'Символические ссылки'):
            self.ai._ensure_release_model(self.spec)

    def test_streaming_download_rejects_file_at_size_limit(self):
        real_client = httpx.Client
        transport = httpx.MockTransport(lambda request: httpx.Response(200, content=self.content))
        def client(**kwargs):
            return real_client(transport=transport, **kwargs)
        with mock.patch('local_ai.httpx.Client', side_effect=client):
            with self.assertRaisesRegex(RuntimeError, 'превышает допустимый размер'):
                self.ai._download(['https://example.test/model'], self.root / self.name, 'test', minimum=4,
                                  expected_sha=self.digest, check_gguf=True, max_bytes_exclusive=len(self.content))
        self.assertFalse((self.root / self.name).exists())
        self.assertFalse((self.root / (self.name + '.part')).exists())


if __name__ == '__main__':
    unittest.main()
