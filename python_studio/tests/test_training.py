import copy
import hashlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TRAINING = ROOT / 'training'
sys.path.insert(0, str(TRAINING))
sys.path.insert(0, str(ROOT / 'tests'))
import build_dataset
import evaluate_real_model
import prepare_release
import train_kolibri_gguf
import verify_release


class WorkflowTests(unittest.TestCase):
    def test_runner_temp_is_initialized_inside_a_step(self):
        workflow = ROOT.parent / '.github' / 'workflows' / 'train-kolibri-gguf.yml'
        if not workflow.is_file():
            self.skipTest('Workflow GitHub не входит в переносимый архив Studio.')
        text = workflow.read_text(encoding='utf-8')
        job_env = re.search(r'(?ms)^    env:\n(.*?)(?=^    steps:)', text)
        self.assertIsNotNone(job_env)
        self.assertNotIn('${{ runner.', job_env.group(1))
        self.assertIn('HF_HOME=$RUNNER_TEMP/kolibri-huggingface', text)
        self.assertIn('KOLIBRI_WORK=$RUNNER_TEMP/kolibri-model-work', text)


class DatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pdf = ROOT / 'examples' / 'РуководствоПоГраблям.pdf'
        cls.catalog = ROOT / 'examples' / 'sample-catalog.json'
        cls.examples, cls.facts = build_dataset.build(cls.pdf, cls.catalog, TRAINING / 'manual_facts.json')
        cls.manifest = json.loads((TRAINING / 'dataset-manifest.json').read_text(encoding='utf-8'))

    def test_reproducible_dataset_and_manifest(self):
        serialized = ''.join(json.dumps(item, ensure_ascii=False) + '\n' for item in self.examples)
        self.assertEqual(serialized, (TRAINING / 'train.jsonl').read_text(encoding='utf-8'))
        self.assertEqual(len(self.examples), 348)
        self.assertEqual(self.manifest['examples'], 348)
        self.assertEqual(self.manifest['dataset_sha256'], hashlib.sha256(serialized.encode()).hexdigest())
        self.assertEqual(self.manifest['manual_facts'], len(self.facts))

    def test_every_catalog_position_has_its_pdf_source(self):
        sample = json.loads(self.catalog.read_text(encoding='utf-8'))['machines'][0]
        expected = {(group['id'], item['position'], item['page']) for group in sample['partGroups'] for item in group['items']}
        actual = {(item['source']['group'], item['source']['position'], item['source']['page'])
                  for item in self.examples if item['category'] == 'parts'}
        self.assertEqual(len(actual), 124)
        self.assertEqual(actual, expected)
        self.assertEqual(len({entry[0] for entry in actual}), 8)

    def test_questions_refusals_and_source_injections_never_propose_edits(self):
        for item in self.examples:
            answer = json.loads(item['messages'][2]['content'])
            if item['category'] not in ('number_edit', 'add_step'):
                self.assertEqual(answer['operations'], [], item['id'])
            user = json.loads(item['messages'][1]['content'])
            self.assertFalse(any(entry['path'].endswith(('/purpose', '/inspection'))
                                 for entry in user['контекст']['allowed']), item['id'])
        for item in self.examples:
            if item['category'] == 'number_edit':
                answer = json.loads(item['messages'][2]['content'])
                self.assertEqual(len(answer['operations']), 1)
                self.assertEqual(answer['operations'][0]['action'], 'set')
                self.assertIn('не заводская норма', answer['reply'])

    def test_original_seven_are_not_copied_as_training_instructions(self):
        instructions = {json.loads(item['messages'][1]['content'])['запрос'] for item in self.examples}
        self.assertFalse(instructions.intersection(case[3] for case in evaluate_real_model.CASES))

    def test_changed_source_is_rejected(self):
        machine = json.loads(self.catalog.read_text(encoding='utf-8'))['machines'][0]
        with tempfile.TemporaryDirectory() as directory:
            altered = Path(directory) / 'catalog.json'
            altered.write_bytes(self.catalog.read_bytes() + b' ')
            with self.assertRaisesRegex(ValueError, 'не совпадает'):
                build_dataset.verify_sources(self.pdf, altered, self.facts, machine)

    def test_related_table_cells_must_come_from_the_same_row(self):
        machine = json.loads(self.catalog.read_text(encoding='utf-8'))['machines'][0]
        facts = copy.deepcopy(self.facts)
        fact = next(item for item in facts if item.get('rows') == [['Редуктор', '13/0,1', '10']])
        fact['rows'] = [['Редуктор', '6/0,1', '10']]
        with self.assertRaisesRegex(ValueError, 'строка таблицы'):
            build_dataset.verify_sources(self.pdf, self.catalog, facts, machine)

    def test_no_missing_or_duplicate_examples_in_training_order(self):
        first = train_kolibri_gguf.interleave(self.examples, 471)
        second = train_kolibri_gguf.interleave(self.examples, 471)
        self.assertEqual([item['id'] for item in first], [item['id'] for item in second])
        self.assertEqual({item['id'] for item in first}, {item['id'] for item in self.examples})
        self.assertEqual(len(first), len(self.examples))
        self.assertGreater(len({item['category'] for item in first[:16]}), 10)

    def test_tokenizer_never_silently_truncates_answer(self):
        class FakeTokenizer:
            def apply_chat_template(self, messages, tokenize, add_generation_prompt):
                return [1, 2, 3] if add_generation_prompt else [1, 2, 3, 4, 5]
        example = self.examples[0]
        rows = train_kolibri_gguf.tokenize_examples([example], FakeTokenizer(), 5)
        self.assertEqual(rows[0]['input_ids'], [1, 2, 3, 4, 5])
        self.assertEqual(rows[0]['prompt_length'], 3)
        with self.assertRaisesRegex(ValueError, 'не обрезаны'):
            train_kolibri_gguf.tokenize_examples([example], FakeTokenizer(), 4)


class ReleaseGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.dataset = self.directory / 'train.jsonl'
        self.dataset.write_text('real test data\n', encoding='utf-8')
        data_hash = train_kolibri_gguf.sha256_file(self.dataset)
        self.manifest = self.directory / 'manifest.json'
        self.manifest.write_text(json.dumps({'dataset_sha256': data_hash, 'examples': 328}), encoding='utf-8')
        self.model = self.directory / train_kolibri_gguf.MODEL_NAME
        self.model.write_bytes(b'GGUF test payload')
        self.model_hash = train_kolibri_gguf.sha256_file(self.model)
        self.training = {
            'base_model': train_kolibri_gguf.BASE_MODEL, 'revision': train_kolibri_gguf.BASE_REVISION,
            'dataset_sha256': data_hash, 'examples': 328, 'steps': 328, 'unique_examples_seen': 328, 'full_pass': True,
            'device': 'cpu', 'dtype': 'bfloat16', 'threads': 4, 'max_length': 448, 'actual_max_length': 427,
            'layers_total': 36, 'lora_layers': list(range(24, 36)), 'lora_rank': 16, 'lora_alpha': 32,
            'lora_targets': train_kolibri_gguf.TARGET_MODULES, 'lora_b_norm_before': 0, 'lora_b_norm_after': 1,
            'probe_loss_before': 3.0, 'probe_loss_after': 2.8, 'probe_loss_decreased': True,
        }
        self.conversion = {'bytes': self.model.stat().st_size, 'sha256': self.model_hash, 'under_size_limit': True, 'quantization': 'Q4_K_M'}
        self.report = {
            'model_file_sha256': self.model_hash, 'server_model': {'id': 'expected'},
            'cases': [{'case': name, 'heuristic_check': True} for name in verify_release.ORIGINAL_CASES],
            'additional_cases': [{'case': name, 'heuristic_check': True} for name in verify_release.EXTRA_CASES],
            'all_required_cases_present': True, 'all_heuristics_passed': True,
        }
        self.report_path = self.directory / 'report.json'
        self.save()

    def save(self):
        (self.directory / 'training-metrics.json').write_text(json.dumps(self.training), encoding='utf-8')
        (self.directory / 'gguf-metrics.json').write_text(json.dumps(self.conversion), encoding='utf-8')
        self.report_path.write_text(json.dumps(self.report), encoding='utf-8')

    def gate(self):
        values = {'general.file_type': 15, 'qwen2.block_count': 36, 'qwen2.embedding_length': 2048}
        texts = {'general.architecture': 'qwen2', 'general.license.name': 'Qwen Research License',
                 'general.description': 'Modified Qwen2.5-3B-Instruct. Improved using Qwen.'}
        with mock.patch.object(verify_release.gguf, 'GGUFReader', return_value=object()), \
             mock.patch.object(verify_release, 'integer_field', side_effect=lambda reader, key: values[key]), \
             mock.patch.object(verify_release, 'text_field', side_effect=lambda reader, key: texts[key]):
            return verify_release.evaluate(self.directory, self.dataset, self.manifest, self.report_path, 'expected')

    def test_valid_fixture_passes_and_limit_is_exclusive(self):
        self.assertTrue(self.gate()['passed'])
        with mock.patch.object(verify_release, 'MAX_MODEL_BYTES', self.model.stat().st_size):
            result = self.gate()
        self.assertFalse(result['passed'])
        self.assertFalse(result['checks']['gguf_under_exclusive_size_limit'])

    def test_failed_exam_missing_examples_or_bad_loss_cannot_publish(self):
        self.report['cases'][0]['heuristic_check'] = False
        self.save()
        self.assertFalse(self.gate()['checks']['original_cases_all_pass'])
        self.report['cases'][0]['heuristic_check'] = True
        self.training['unique_examples_seen'] = 2
        self.training['probe_loss_after'] = 3.0
        self.save()
        checks = self.gate()['checks']
        self.assertFalse(checks['all_examples_seen'])
        self.assertFalse(checks['probe_loss_decreased'])

    def test_model_or_evaluation_hash_mismatch_fails(self):
        self.report['model_file_sha256'] = '0' * 64
        self.save()
        self.assertFalse(self.gate()['checks']['gguf_hash_and_size_match'])

    def test_release_preparation_has_license_notice_and_verifiable_sums(self):
        license_source = self.directory / 'source-license.txt'
        license_source.write_text('Test-only license fixture', encoding='utf-8')
        argv = ['prepare_release.py', '--output', str(self.directory), '--license', str(license_source),
                '--manifest', str(self.manifest), '--report', str(self.report_path)]
        with mock.patch.object(sys, 'argv', argv):
            prepare_release.main()
        notice = (self.directory / 'NOTICE.txt').read_text(encoding='utf-8')
        self.assertIn('Qwen RESEARCH LICENSE AGREEMENT', notice)
        self.assertIn('modified, LoRA-fine-tuned and quantized', notice)
        sums = (self.directory / 'SHA256SUMS.txt').read_text(encoding='utf-8').splitlines()
        self.assertEqual(len(sums), 6)
        for line in sums:
            value, name = line.split('  ', 1)
            self.assertEqual(value, train_kolibri_gguf.sha256_file(self.directory / name))

    def test_tiny_qwen_architecture_is_not_accepted_as_real_3b(self):
        texts = {'general.architecture': 'qwen2', 'general.license.name': 'Qwen Research License',
                 'general.description': 'Modified Qwen2.5-3B-Instruct. Improved using Qwen.'}
        with mock.patch.object(verify_release.gguf, 'GGUFReader', return_value=object()), \
             mock.patch.object(verify_release, 'text_field', side_effect=lambda reader, key: texts[key]), \
             mock.patch.object(verify_release, 'integer_field', side_effect=lambda reader, key: {'general.file_type': 15, 'qwen2.block_count': 3, 'qwen2.embedding_length': 64}[key]):
            result = verify_release.evaluate(self.directory, self.dataset, self.manifest, self.report_path, 'expected')
        self.assertFalse(result['checks']['qwen2_3b_q4_k_m'])
        self.assertFalse(result['passed'])


if __name__ == '__main__':
    unittest.main()
