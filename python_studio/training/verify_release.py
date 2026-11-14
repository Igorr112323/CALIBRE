import argparse
import base64
import hashlib
import json
import math
import os
import re
import shutil
import sys
import zlib
from pathlib import Path

try:
    import gguf
except ImportError:
    gguf = None

from train_kolibri_gguf import BASE_MODEL, BASE_REVISION, MAX_MODEL_BYTES, MODEL_NAME, QUANTIZATION_FILE_TYPES, TARGET_MODULES, EarlyStopping, read_examples, sha256_file, validate_split

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
            'probe_loss_after': training.get('probe_loss_after'), 'steps': training.get('steps'),
            'epochs_completed': training.get('epochs_completed'), 'selected_step': training.get('selected_step'),
            'learning_rate': training.get('learning_rate'), 'validation_examples': training.get('validation_examples'),
            'early_stopping': training.get('early_stopping'),
            'original_exam': summarize_cases(report, 'cases', ORIGINAL_ORDER),
            'additional_exam': summarize_cases(report, 'additional_cases', EXTRA_ORDER),
            'source_sha256': {'training': optional_sha256(output / 'training-metrics.json'),
                              'conversion': optional_sha256(output / 'gguf-metrics.json'),
                              'exam': optional_sha256(report_path)},
            'input_errors': {key: error for key, error in [('training', training_error), ('conversion', conversion_error), ('exam', report_error)] if error}}


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def close_numbers(left, right):
    return finite_number(left) and finite_number(right) and math.isclose(left, right, rel_tol=0.00001, abs_tol=0.00001)


def valid_training_log(path, rows, training):
    try:
        events = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
        count = len(rows)
        epochs = training.get('epochs_completed')
        selected = training.get('selected_step')
        expected = {item['id']: item['category'] for item in rows}
        if (not count or epochs not in (1, 2) or len(events) != count * epochs or training.get('steps') != len(events) or
                not isinstance(selected, int) or selected < count or selected > len(events) or
                training.get('training_log_sha256') != sha256_file(path)):
            return False
        for index, item in enumerate(events):
            identifier = item.get('example')
            if (item.get('step') != index + 1 or identifier not in expected or item.get('category') != expected[identifier] or
                    not close_numbers(item.get('epoch'), round((index + 1) / count, 6)) or
                    not isinstance(item.get('tokens'), int) or not 0 < item['tokens'] <= training.get('actual_max_length', 0) or
                    any(not finite_number(item.get(key)) or item[key] < 0 for key in ('loss', 'seconds', 'gradient_norm'))):
                return False
        if any({event['example'] for event in events[start:start + count]} != set(expected)
               for start in range(0, len(events), count)):
            return False
        return (close_numbers(events[-1]['loss'], training.get('final_step_loss')) and
                close_numbers(events[selected - 1]['loss'], training.get('selected_step_loss')))
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return False


def valid_history(training, before, after):
    history = training.get('validation_history')
    stopping = training.get('early_stopping', {})
    examples = training.get('examples')
    validation_count = training.get('validation_examples')
    epochs = training.get('epochs_completed')
    max_epochs = training.get('max_epochs')
    selected = training.get('selected_step')
    if (not isinstance(examples, int) or examples < 1 or not isinstance(validation_count, int) or validation_count < 20 or
            epochs not in (1, 2) or max_epochs not in (1, 2) or epochs > max_epochs or training.get('steps') != epochs * examples or
            not isinstance(history, list) or len(history) != epochs + 1 or not isinstance(stopping, dict) or
            stopping.get('patience') != 1 or not finite_number(stopping.get('min_delta')) or not 0 <= stopping['min_delta'] <= 0.001):
        return False
    if [entry.get('step') for entry in history if isinstance(entry, dict)] != [examples * epoch for epoch in range(epochs + 1)]:
        return False
    for index, entry in enumerate(history):
        loss = entry.get('loss')
        individual = entry.get('individual')
        if (not finite_number(loss) or loss < 0 or not isinstance(individual, list) or len(individual) != validation_count or
                any(not finite_number(value) or value < 0 for value in individual) or
                not close_numbers(loss, sum(individual) / validation_count) or not close_numbers(entry.get('epoch'), index)):
            return False
    monitor = EarlyStopping(before, patience=1, min_delta=stopping['min_delta'])
    for index, entry in enumerate(history[1:], 1):
        saved, stopped = monitor.observe(entry['loss'], entry['step'])
        if entry.get('selected_at_time') is not saved or entry.get('stop') is not stopped or stopped and index != epochs:
            return False
    reason = stopping.get('stop_reason')
    if ((reason == 'heldout_loss_not_improved') != stopped or
            reason == 'time_budget_one_epoch' and (epochs != 1 or max_epochs != 2) or
            reason == 'epoch_limit' and epochs != max_epochs or
            reason not in ('heldout_loss_not_improved', 'time_budget_one_epoch', 'epoch_limit')):
        return False
    best = next(entry for entry in history[1:] if entry['step'] == monitor.best_step)
    before_values = training.get('probe_individual_before')
    after_values = training.get('probe_individual_after')
    if (not isinstance(before_values, list) or not isinstance(after_values, list) or
            len(before_values) != validation_count or len(after_values) != validation_count or
            any(not close_numbers(left, right) for left, right in zip(before_values, history[0]['individual'])) or
            any(not close_numbers(left, right) for left, right in zip(after_values, best['individual']))):
        return False
    checkpoint = stopping.get('best_adapter_sha256')
    return (close_numbers(history[0]['loss'], before) and close_numbers(best['loss'], after) and
            selected == best['step'] == stopping.get('best_step') and close_numbers(stopping.get('best_loss'), after) and
            isinstance(checkpoint, str) and re.fullmatch(r'[0-9a-f]{64}', checkpoint) is not None and
            checkpoint == stopping.get('restored_adapter_sha256') and stopping.get('restored_best') is True and
            stopping.get('checked_after_each_complete_epoch') is True)


def evaluate(output, dataset, manifest_path, report_path, expected_server_id, expected_quantization='Q3_K_M', validation_path=None, training_log_path=None):
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
    template_sha256 = hashlib.sha256(text_field(reader, 'tokenizer.chat_template').encode('utf-8')).hexdigest()
    validation_path = validation_path or dataset.with_name('validation.jsonl')
    training_log_path = training_log_path or output / 'training-log.jsonl'
    facts_path = Path(__file__).with_name('manual_facts.json')
    try:
        training_rows = read_examples(dataset)
        validation_rows = read_examples(validation_path)
        facts = json.loads(facts_path.read_text(encoding='utf-8'))
        split = validate_split(training_rows, validation_rows, facts)
        split_error = ''
    except Exception as error:
        training_rows, validation_rows, split = [], [], {}
        split_error = type(error).__name__ + ': ' + str(error)
    cases = report.get('cases', [])
    additional = report.get('additional_cases', [])
    before = training.get('probe_loss_before')
    after = training.get('probe_loss_after')
    finite_losses = isinstance(before, (int, float)) and isinstance(after, (int, float)) and math.isfinite(before) and math.isfinite(after)
    quant_type = QUANTIZATION_FILE_TYPES.get(expected_quantization)
    checks = {
        'base_model_and_revision': training.get('base_model') == BASE_MODEL and training.get('revision') == BASE_REVISION,
        'dataset_hash_and_count': training.get('dataset_sha256') == manifest.get('dataset_sha256') == optional_sha256(dataset) and
                                  training.get('examples') == manifest.get('examples') == len(training_rows) and 280 <= len(training_rows) <= 350,
        'heldout_hash_and_count': training.get('validation_sha256') == manifest.get('validation_sha256') == optional_sha256(validation_path) and
                                  training.get('validation_examples') == manifest.get('validation_examples') == len(validation_rows) and len(validation_rows) >= 20 and
                                  training.get('facts_sha256') == manifest.get('facts_sha256') == optional_sha256(facts_path),
        'heldout_disjoint': not split_error and split.get('training_pages') == manifest.get('training_pages') == training.get('training_pages') and
                            split.get('validation_pages') == manifest.get('validation_pages') == training.get('validation_pages') and
                            len(training.get('probe_ids', [])) == len(validation_rows) and
                            set(training.get('probe_ids', [])) == {item['id'] for item in validation_rows},
        'studio_system_prompt': not split_error and split.get('system_prompt_sha256') == manifest.get('system_prompt_sha256') == training.get('system_prompt_sha256'),
        'all_examples_seen': training.get('full_pass') is True and training.get('unique_examples_seen') == training.get('examples') and
                             training.get('steps', 0) >= training.get('examples', sys.maxsize) and
                             training.get('selected_step', 0) >= training.get('examples', sys.maxsize) and
                             valid_training_log(training_log_path, training_rows, training),
        'one_or_two_epochs_and_learning_rate': training.get('max_epochs') in (1, 2) and training.get('epochs_completed') in (1, 2) and
                                              training.get('explicit_step_limit') == 0 and
                                              isinstance(training.get('learning_rate'), (int, float)) and 0 < training['learning_rate'] <= 0.00005,
        'cpu_bf16_four_threads': training.get('device') == 'cpu' and training.get('dtype') == 'bfloat16' and training.get('threads') == 4,
        'sequence_limit': training.get('max_length') == 2048 and training.get('actual_max_length', 2049) <= 2048,
        'matching_chat_template': template_sha256 == training.get('tokenizer_chat_template_sha256'),
        'lora_configuration': training.get('layers_total') == 36 and training.get('lora_layers') == list(range(24, 36)) and
                              training.get('lora_rank') == 16 and training.get('lora_alpha') == 32 and training.get('lora_targets') == TARGET_MODULES,
        'actual_adapter_updates': training.get('lora_b_norm_after', 0) > training.get('lora_b_norm_before', 0),
        'probe_loss_decreased': finite_losses and after < before and training.get('probe_loss_decreased') is True,
        'early_stopping_with_best_restore': finite_losses and valid_history(training, before, after),
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
    snapshot['source_sha256'].update({'manifest': sha256_file(manifest_path), 'dataset': optional_sha256(dataset),
                                      'validation': optional_sha256(validation_path), 'facts': optional_sha256(facts_path),
                                      'training_log': optional_sha256(training_log_path)})
    if split_error:
        snapshot['input_errors']['heldout'] = split_error
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
                     'description': description, 'license': license_name, 'chat_template_sha256': template_sha256},
            'note': 'Автоматические проверки не гарантируют безопасность или достоверность модели.'}


def assess_candidate(output, dataset, manifest_path, report_path, expected_server_id, quantization, validation_path=None, training_log_path=None):
    try:
        return evaluate(output, dataset, manifest_path, report_path, expected_server_id, quantization, validation_path, training_log_path)
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


def run_gate(output, dataset, manifest_path, report_path, expected_server_id, secondary=None, validation_path=None):
    output.mkdir(parents=True, exist_ok=True)
    training_log_path = output / 'training-log.jsonl'
    primary = assess_candidate(output, dataset, manifest_path, report_path, expected_server_id, 'Q3_K_M', validation_path, training_log_path)
    results = {'Q3_K_M': primary}
    alternative = None
    if secondary is not None:
        alternative = assess_candidate(secondary, dataset, manifest_path, secondary / report_path.name, expected_server_id, 'Q4_K_S', validation_path, training_log_path)
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
            'probe_loss_before': candidate.get('probe_loss_before'), 'probe_loss_after': candidate.get('probe_loss_after'),
            'steps': candidate.get('steps'), 'epochs_completed': candidate.get('epochs_completed'),
            'selected_step': candidate.get('selected_step'), 'learning_rate': candidate.get('learning_rate'),
            'original_exam': case_statuses('original_exam'), 'additional_exam': case_statuses('additional_exam'),
            'failed_checks': [key for key, value in candidate.get('checks', {}).items() if not value],
            'failure_reasons': [str(reason)[:120] for reason in reasons[:3]], 'failure_reason_count': len(reasons)}


def emit_diagnostics(output, report_path, secondary, result):
    directories = {'Q3_K_M': output / 'diagnostic-Q3_K_M' if (output / 'diagnostic-Q3_K_M').is_dir() else output}
    if secondary is not None:
        directories['Q4_K_S'] = secondary
    candidates = {}
    for name, directory in directories.items():
        report, error = read_json(directory / report_path.name)
        if not error:
            conversion, _ = read_json(directory / 'gguf-metrics.json')
            candidates[name] = {'evaluation': report, 'conversion': conversion}
    training, _ = read_json(output / 'training-metrics.json')
    if not candidates and not training:
        return
    data = {'training': training, 'candidates': candidates, 'gate': result}
    compressed = zlib.compress(json.dumps(data, ensure_ascii=False).encode('utf-8'), 9)
    encoded = base64.b64encode(compressed).decode('ascii')
    parts = [encoded[index:index + 2500] for index in range(0, len(encoded), 2500)]
    if len(parts) > 46:
        print('::warning file=.github/workflows/train-kolibri-gguf.yml,line=1::Полные ответы слишком велики для аннотаций; смотрите artifact.', flush=True)
        return
    for index, part in enumerate(parts, 1):
        print(f'::notice file=.github/workflows/train-kolibri-gguf.yml,line=1,title=Training diagnostics {index} of {len(parts)}::TRAINING_DIAGNOSTIC_{index:03}_{len(parts):03}_{part}', flush=True)


def main():
    parser = argparse.ArgumentParser(description='Блокировка релиза при невыполнении требований к модели.')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--validation-dataset', type=Path)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--expected-server-id', required=True)
    parser.add_argument('--secondary-output', type=Path)
    args = parser.parse_args()
    try:
        result = run_gate(args.output, args.dataset, args.manifest, args.report, args.expected_server_id, args.secondary_output, args.validation_dataset)
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
        emit_diagnostics(args.output, args.report, args.secondary_output, result)
        summary = os.environ.get('GITHUB_STEP_SUMMARY')
        if summary:
            with open(summary, 'a', encoding='utf-8') as handle:
                handle.write('### Проверка выпуска\n\n```json\n' + rendered + '\n```\n')
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
