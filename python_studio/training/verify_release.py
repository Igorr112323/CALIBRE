import argparse
import json
import math
import os
import shutil
import sys
from pathlib import Path

try:
    import gguf
except ImportError:
    gguf = None

from train_kolibri_gguf import BASE_MODEL, BASE_REVISION, MAX_MODEL_BYTES, MODEL_NAME, QUANTIZATION_FILE_TYPES, TARGET_MODULES, sha256_file

ORIGINAL_ORDER = ('question_only', 'exact_number_edit', 'preserve_number', 'append_step', 'unknown_torque', 'outside_scope', 'source_injection')
EXTRA_ORDER = ('rotor_mechanism', 'carriage_purpose', 'unknown_bearing', 'unknown_absolute_gap', 'retain_safety')
ORIGINAL_CASES = set(ORIGINAL_ORDER)
EXTRA_CASES = set(EXTRA_ORDER)
TARGET_MODEL_BYTES = 1_700_000_000


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


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict):
            raise ValueError('ожидался JSON-объект')
        return value, ''
    except (OSError, ValueError) as error:
        return {}, type(error).__name__ + ': ' + str(error)


def optional_sha256(path):
    try:
        return sha256_file(path)
    except OSError:
        return None


def summarize_cases(report, field, expected):
    items = report.get(field, [])
    if not isinstance(items, list):
        items = []
    items = [item for item in items if isinstance(item, dict)]
    results = []
    for name in expected:
        matches = [item for item in items if item.get('case') == name]
        if len(matches) != 1:
            results.append({'case': name, 'passed': None, 'reason': 'Результат отсутствует или повторён.'})
            continue
        item = matches[0]
        passed = item.get('heuristic_check') is True
        reason = item.get('error') or item.get('rejected_operations') or ('Проверка пройдена.' if passed else 'Не выполнены условия сценария; см. ответ и операции.')
        results.append({'case': name, 'passed': passed, 'reason': reason, 'reply': item.get('reply'),
                        'operations': item.get('operations', []), 'seconds': item.get('seconds')})
    return {'passed': sum(item['passed'] is True for item in results), 'total': len(expected), 'cases': results}


def diagnostic_snapshot(output, report_path):
    training, training_error = read_json(output / 'training-metrics.json')
    conversion, conversion_error = read_json(output / 'gguf-metrics.json')
    report, report_error = read_json(report_path)
    model = output / MODEL_NAME
    try:
        size = model.stat().st_size
    except OSError:
        size = None
    return {'model_bytes': size, 'limit_bytes_exclusive': MAX_MODEL_BYTES, 'target_bytes': TARGET_MODEL_BYTES,
            'under_target': size is not None and size < TARGET_MODEL_BYTES,
            'quantization_reported': conversion.get('quantization'),
            'final_step_loss': training.get('final_step_loss'), 'probe_loss_before': training.get('probe_loss_before'),
            'probe_loss_after': training.get('probe_loss_after'),
            'original_exam': summarize_cases(report, 'cases', ORIGINAL_ORDER),
            'additional_exam': summarize_cases(report, 'additional_cases', EXTRA_ORDER),
            'source_sha256': {'training': optional_sha256(output / 'training-metrics.json'),
                              'conversion': optional_sha256(output / 'gguf-metrics.json'),
                              'exam': optional_sha256(report_path)},
            'input_errors': {key: error for key, error in [('training', training_error), ('conversion', conversion_error), ('exam', report_error)] if error}}


def evaluate(output, dataset, manifest_path, report_path, expected_server_id, expected_quantization='Q3_K_M'):
    training = json.loads((output / 'training-metrics.json').read_text(encoding='utf-8'))
    conversion = json.loads((output / 'gguf-metrics.json').read_text(encoding='utf-8'))
    report = json.loads(report_path.read_text(encoding='utf-8'))
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    model = output / MODEL_NAME
    size = model.stat().st_size
    digest = sha256_file(model)
    if gguf is None:
        raise RuntimeError('Не установлен пакет gguf для проверки метаданных модели.')
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
    quant_type = QUANTIZATION_FILE_TYPES.get(expected_quantization)
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
        'qwen2_3b_expected_quantization': architecture == 'qwen2' and layers == 36 and hidden == 2048 and
                                         quant_type is not None and file_type == quant_type and conversion.get('quantization') == expected_quantization,
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
    snapshot = diagnostic_snapshot(output, report_path)
    snapshot['source_sha256']['manifest'] = sha256_file(manifest_path)
    reasons = []
    for name, passed in checks.items():
        if not passed:
            if name == 'gguf_under_exclusive_size_limit':
                reasons.append(f'Размер {size} байт не соответствует строгому пределу менее {MAX_MODEL_BYTES} байт.')
            elif name == 'original_cases_all_pass':
                exam = snapshot['original_exam']
                reasons.append(f"Исходный экзамен: {exam['passed']}/{exam['total']}; прошли не все сценарии.")
            elif name == 'extra_cases_all_pass':
                exam = snapshot['additional_exam']
                reasons.append(f"Дополнительный экзамен: {exam['passed']}/{exam['total']}; прошли не все сценарии.")
            else:
                reasons.append('Не пройдена проверка: ' + name)
    return {'passed': all(checks.values()), 'expected_quantization': expected_quantization, 'checks': checks,
            'failure_reasons': reasons, 'model_sha256': digest, **snapshot,
            'gguf': {'architecture': architecture, 'file_type': file_type, 'layers': layers, 'hidden_size': hidden,
                     'description': description, 'license': license_name},
            'note': 'Автоматические проверки не гарантируют безопасность или достоверность модели.'}


def assess_candidate(output, dataset, manifest_path, report_path, expected_server_id, quantization):
    try:
        return evaluate(output, dataset, manifest_path, report_path, expected_server_id, quantization)
    except Exception as error:
        snapshot = diagnostic_snapshot(output, report_path)
        detail = type(error).__name__ + ': ' + str(error)
        return {'passed': False, 'expected_quantization': quantization, 'checks': {},
                'failure_reasons': ['Не удалось выполнить все проверки: ' + detail], 'error': detail, **snapshot}


def choose_candidate(primary, secondary=None):
    choices = [('Q3_K_M', primary)]
    if secondary is not None:
        choices.append(('Q4_K_S', secondary))
    eligible = [(name, result) for name, result in choices if result.get('passed') is True]
    if not eligible:
        return None
    return max(eligible, key=lambda item: (item[1]['original_exam']['passed'] + item[1]['additional_exam']['passed'], item[0] == 'Q3_K_M'))[0]


def preserve_primary_diagnostics(output, report_path):
    destination = output / 'diagnostic-Q3_K_M'
    destination.mkdir(parents=True, exist_ok=True)
    for source in (output / 'gguf-metrics.json', report_path, output / 'llama-server.log'):
        if source.is_file():
            shutil.copy2(source, destination / source.name)


def activate_secondary(output, secondary, report_path, expected_sha):
    model = secondary / MODEL_NAME
    if sha256_file(model) != expected_sha:
        raise ValueError('SHA-256 выбранной модели изменился до публикации.')
    temporary = []
    for source, destination in [(secondary / 'gguf-metrics.json', output / 'gguf-metrics.json'),
                                (secondary / report_path.name, report_path)]:
        target = destination.with_name(destination.name + '.selected')
        shutil.copy2(source, target)
        temporary.append((target, destination))
    os.replace(model, output / MODEL_NAME)
    for source, destination in temporary:
        os.replace(source, destination)
    if sha256_file(output / MODEL_NAME) != expected_sha:
        raise ValueError('SHA-256 выбранной модели изменился после перемещения.')


def run_gate(output, dataset, manifest_path, report_path, expected_server_id, secondary=None):
    output.mkdir(parents=True, exist_ok=True)
    primary = assess_candidate(output, dataset, manifest_path, report_path, expected_server_id, 'Q3_K_M')
    results = {'Q3_K_M': primary}
    alternative = None
    if secondary is not None:
        alternative = assess_candidate(secondary, dataset, manifest_path, secondary / report_path.name, expected_server_id, 'Q4_K_S')
        results['Q4_K_S'] = alternative
    selected = choose_candidate(primary, alternative)
    result = {'passed': False, 'selected_quantization': None, 'candidates': results,
              'failure_reasons': [], 'limit_bytes_exclusive': MAX_MODEL_BYTES,
              'note': 'К выпуску допускается только вариант, прошедший все проверки; равный балл в пользу основного Q3_K_M.'}
    try:
        if selected == 'Q4_K_S':
            preserve_primary_diagnostics(output, report_path)
            activate_secondary(output, secondary, report_path, alternative['model_sha256'])
        if selected:
            result['passed'] = True
            result['selected_quantization'] = selected
        else:
            result['failure_reasons'].append('Ни один вариант не прошёл все обязательные ворота.')
    except Exception as error:
        result['failure_reasons'].append('Не удалось подготовить выбранную модель: ' + type(error).__name__ + ': ' + str(error))
    return result


def annotation_summary(name, candidate):
    def case_statuses(field):
        exam = candidate.get(field, {})
        return {'passed': exam.get('passed'), 'total': exam.get('total'),
                'cases': {item['case']: item.get('passed') for item in exam.get('cases', [])}}

    reasons = candidate.get('failure_reasons', [])
    return {'quantization': name, 'passed': candidate.get('passed', False), 'bytes': candidate.get('model_bytes'),
            'limit_bytes_exclusive': MAX_MODEL_BYTES, 'final_step_loss': candidate.get('final_step_loss'),
            'original_exam': case_statuses('original_exam'), 'additional_exam': case_statuses('additional_exam'),
            'failed_checks': [key for key, value in candidate.get('checks', {}).items() if not value],
            'failure_reasons': [str(reason)[:120] for reason in reasons[:3]], 'failure_reason_count': len(reasons)}


def main():
    parser = argparse.ArgumentParser(description='Блокировка релиза при невыполнении требований к модели.')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--expected-server-id', required=True)
    parser.add_argument('--secondary-output', type=Path)
    args = parser.parse_args()
    try:
        result = run_gate(args.output, args.dataset, args.manifest, args.report, args.expected_server_id, args.secondary_output)
    except Exception as error:
        result = {'passed': False, 'selected_quantization': None,
                  'failure_reasons': ['Ошибка выпускной проверки: ' + type(error).__name__ + ': ' + str(error)],
                  'candidates': {'Q3_K_M': diagnostic_snapshot(args.output, args.report)}}
    args.output.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    (args.output / 'release-gate.json').write_text(rendered + '\n', encoding='utf-8')
    print(rendered, flush=True)
    if os.environ.get('GITHUB_ACTIONS') == 'true':
        for name, candidate in result.get('candidates', {}).items():
            brief = annotation_summary(name, candidate)
            text = json.dumps(brief, ensure_ascii=False, separators=(',', ':')).replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
            print(f'::notice file=.github/workflows/train-kolibri-gguf.yml,line=1,title=Release gate {name}::{text}', flush=True)
        summary = os.environ.get('GITHUB_STEP_SUMMARY')
        if summary:
            with open(summary, 'a', encoding='utf-8') as handle:
                handle.write('### Проверка выпуска\n\n```json\n' + rendered + '\n```\n')
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
