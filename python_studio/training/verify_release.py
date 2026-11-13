import argparse
import json
import math
import sys
from pathlib import Path

import gguf

from train_kolibri_gguf import BASE_MODEL, BASE_REVISION, MAX_MODEL_BYTES, MODEL_NAME, TARGET_MODULES, sha256_file

ORIGINAL_CASES = {'question_only', 'exact_number_edit', 'preserve_number', 'append_step', 'unknown_torque', 'outside_scope', 'source_injection'}
EXTRA_CASES = {'rotor_mechanism', 'carriage_purpose', 'unknown_bearing', 'unknown_absolute_gap', 'retain_safety'}


def integer_field(reader, name):
    field = reader.get_field(name)
    if not field or len(field.data) != 1:
        raise ValueError('В GGUF не найдено поле ' + name)
    return int(field.parts[field.data[0]][0])


def text_field(reader, name):
    field = reader.get_field(name)
    if not field or len(field.data) != 1:
        raise ValueError('В GGUF не найдено поле ' + name)
    return bytes(field.parts[field.data[0]]).decode('utf-8')


def evaluate(output, dataset, manifest_path, report_path, expected_server_id):
    training = json.loads((output / 'training-metrics.json').read_text(encoding='utf-8'))
    conversion = json.loads((output / 'gguf-metrics.json').read_text(encoding='utf-8'))
    report = json.loads(report_path.read_text(encoding='utf-8'))
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    model = output / MODEL_NAME
    size = model.stat().st_size
    digest = sha256_file(model)
    reader = gguf.GGUFReader(model)
    architecture = text_field(reader, 'general.architecture')
    description = text_field(reader, 'general.description')
    license_name = text_field(reader, 'general.license.name')
    file_type = integer_field(reader, 'general.file_type')
    layers = integer_field(reader, 'qwen2.block_count')
    hidden = integer_field(reader, 'qwen2.embedding_length')
    cases = report.get('cases', [])
    additional = report.get('additional_cases', [])
    before = training.get('probe_loss_before')
    after = training.get('probe_loss_after')
    finite_losses = isinstance(before, (int, float)) and isinstance(after, (int, float)) and math.isfinite(before) and math.isfinite(after)
    checks = {
        'base_model_and_revision': training.get('base_model') == BASE_MODEL and training.get('revision') == BASE_REVISION,
        'dataset_hash_and_count': training.get('dataset_sha256') == manifest.get('dataset_sha256') == sha256_file(dataset) and
                                  training.get('examples') == manifest.get('examples') and 280 <= manifest.get('examples', 0) <= 350,
        'all_examples_seen': training.get('full_pass') is True and training.get('unique_examples_seen') == training.get('examples') and
                             training.get('steps', 0) >= training.get('examples', sys.maxsize),
        'cpu_bf16_four_threads': training.get('device') == 'cpu' and training.get('dtype') == 'bfloat16' and training.get('threads') == 4,
        'sequence_limit': training.get('max_length') == 448 and training.get('actual_max_length', 449) <= 448,
        'lora_configuration': training.get('layers_total') == 36 and training.get('lora_layers') == list(range(24, 36)) and
                              training.get('lora_rank') == 16 and training.get('lora_alpha') == 32 and training.get('lora_targets') == TARGET_MODULES,
        'actual_adapter_updates': training.get('lora_b_norm_after', 0) > training.get('lora_b_norm_before', 0),
        'probe_loss_decreased': finite_losses and after < before and training.get('probe_loss_decreased') is True,
        'qwen2_3b_q4_k_m': architecture == 'qwen2' and layers == 36 and hidden == 2048 and file_type == 15 and
                            conversion.get('quantization') == 'Q4_K_M',
        'embedded_license_and_modification_notice': license_name == 'Qwen Research License' and
                                                   'Modified Qwen2.5-3B-Instruct' in description and 'Improved using Qwen' in description,
        'gguf_under_exclusive_size_limit': 4 <= size < MAX_MODEL_BYTES and conversion.get('under_size_limit') is True,
        'gguf_hash_and_size_match': conversion.get('bytes') == size and conversion.get('sha256') == digest and
                                    report.get('model_file_sha256') == digest,
        'server_identity': report.get('server_model', {}).get('id') == expected_server_id,
        'original_cases_all_pass': len(cases) == len(ORIGINAL_CASES) and {item.get('case') for item in cases} == ORIGINAL_CASES and
                                   all(item.get('heuristic_check') is True for item in cases),
        'extra_cases_all_pass': len(additional) == len(EXTRA_CASES) and {item.get('case') for item in additional} == EXTRA_CASES and
                                all(item.get('heuristic_check') is True for item in additional),
        'complete_report': report.get('all_required_cases_present') is True and report.get('all_heuristics_passed') is True,
    }
    return {'passed': all(checks.values()), 'checks': checks, 'model_bytes': size, 'model_sha256': digest,
            'limit_bytes_exclusive': MAX_MODEL_BYTES, 'probe_loss_before': before, 'probe_loss_after': after,
            'gguf': {'architecture': architecture, 'file_type': file_type, 'layers': layers, 'hidden_size': hidden,
                     'description': description, 'license': license_name},
            'note': 'Автоматические проверки не гарантируют безопасность или достоверность модели.'}


def main():
    parser = argparse.ArgumentParser(description='Блокировка релиза при невыполнении требований к модели.')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--expected-server-id', required=True)
    args = parser.parse_args()
    try:
        result = evaluate(args.output, args.dataset, args.manifest, args.report, args.expected_server_id)
    except Exception as error:
        result = {'passed': False, 'error': type(error).__name__ + ': ' + str(error)}
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'release-gate.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
