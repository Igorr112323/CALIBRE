import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import zlib
from pathlib import Path

import gguf

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'python_studio' / 'training'))
from evaluate_candidates import REPORT_NAME, examine
from train_kolibri_gguf import BASE_MODEL, BASE_REVISION, MODEL_NAME, sha256_file
from verify_release import EXTRA_ORDER, ORIGINAL_ORDER, integer_field, summarize_cases, text_field

FILE_TYPES = {'Q3_K_M': 12, 'Q4_K_S': 14, 'Q4_K_M': 15}


def encoded_notices(label, payload):
    encoded = base64.b64encode(zlib.compress(json.dumps(payload, ensure_ascii=False).encode('utf-8'), 9)).decode('ascii')
    pieces = [encoded[index:index + 2500] for index in range(0, len(encoded), 2500)]
    for index, piece in enumerate(pieces, 1):
        print(f'::notice file=.github/workflows/benchmark-kolibri-baseline.yml,line=1,title=Baseline {label} fragment {index} of {len(pieces)}::BASELINE_{label}_{index:03}_{len(pieces):03}_{piece}', flush=True)


def main():
    parser = argparse.ArgumentParser(description='Проверка неизменённой базовой Qwen в том же сервере и квантовании.')
    parser.add_argument('--source-gguf', type=Path, required=True)
    parser.add_argument('--source-manifest', type=Path)
    parser.add_argument('--quantize', type=Path, required=True)
    parser.add_argument('--server', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--quantization', choices=tuple(FILE_TYPES), required=True)
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--port', type=int, default=18951)
    parser.add_argument('--host', choices=('127.0.0.1', '0.0.0.0'), default='127.0.0.1')
    parser.add_argument('--baseline-case', action='append', default=[])
    parser.add_argument('--extra-case', action='append', default=[])
    parser.add_argument('--allow-tiny-smoke', action='store_true')
    args = parser.parse_args()
    if args.threads < 1 or not 1 <= args.port <= 65535 or not all(path.is_file() for path in (args.source_gguf, args.quantize, args.server)):
        parser.error('Не найдены входные файлы или заданы некорректные параметры.')
    if not args.allow_tiny_smoke and not args.source_manifest:
        parser.error('Для 3B требуется манифест исходного снимка весов.')
    source = gguf.GGUFReader(args.source_gguf)
    architecture = text_field(source, 'general.architecture')
    layers = integer_field(source, 'qwen2.block_count')
    hidden = integer_field(source, 'qwen2.embedding_length')
    is_3b = architecture == 'qwen2' and layers == 36 and hidden == 2048
    if not is_3b and not args.allow_tiny_smoke:
        raise ValueError('Исходный BF16 GGUF не является Qwen2.5-3B.')
    manifest = json.loads(args.source_manifest.read_text(encoding='utf-8')) if args.source_manifest else {}
    if not args.allow_tiny_smoke and (manifest.get('model') != BASE_MODEL or manifest.get('revision') != BASE_REVISION or not manifest.get('weight_files')):
        raise ValueError('Не подтверждён закреплённый снимок исходной модели.')
    args.output.mkdir(parents=True, exist_ok=True)
    model = args.output / MODEL_NAME
    report_path = args.output / REPORT_NAME
    final_report = args.output / 'AI_EVALUATION_QWEN25_3B_BASELINE.json'
    if model.exists() or report_path.exists() or final_report.exists():
        raise ValueError('Используйте чистый каталог для каждого варианта.')
    source_template = text_field(source, 'tokenizer.chat_template')
    source_template_sha = hashlib.sha256(source_template.encode('utf-8')).hexdigest()
    if not args.allow_tiny_smoke and source_template_sha != manifest.get('chat_template_sha256'):
        raise ValueError('ChatML-шаблон в GGUF отличается от исходного токенизатора.')
    source_fields = {'architecture': architecture, 'layers': layers, 'hidden_size': hidden, 'sha256': sha256_file(args.source_gguf),
                     'chat_template_sha256': source_template_sha}
    provenance = {'base_model': BASE_MODEL if is_3b else None, 'revision': BASE_REVISION if is_3b else None,
                  'untrained_baseline': is_3b and not args.allow_tiny_smoke, 'source_gguf': source_fields,
                  'source_manifest_sha256': sha256_file(args.source_manifest) if args.source_manifest else None,
                  'quantization': args.quantization, 'llama_cpp': 'b7571-2a85f720b8aa87488468024c0112542b7b742a0b',
                  'exam_scripts_sha256': {str(path.relative_to(ROOT)): sha256_file(path) for path in [
                      ROOT / 'python_studio' / 'operations.py', ROOT / 'python_studio' / 'local_ai.py',
                      ROOT / 'python_studio' / 'tests' / 'evaluate_real_model.py',
                      ROOT / 'python_studio' / 'training' / 'evaluate_finetuned.py']}}
    try:
        with (args.output / 'quantize.log').open('w', encoding='utf-8') as log:
            subprocess.run([str(args.quantize), str(args.source_gguf), str(model), args.quantization, str(args.threads)],
                           stdout=log, stderr=subprocess.STDOUT, check=True)
        reader = gguf.GGUFReader(model)
        quantized_template = text_field(reader, 'tokenizer.chat_template')
        if integer_field(reader, 'general.file_type') != FILE_TYPES[args.quantization] or quantized_template != source_template:
            raise ValueError('Тип квантования или ChatML-шаблон GGUF не совпадает с исходными данными.')
        provenance.update(model_bytes=model.stat().st_size, model_sha256=sha256_file(model),
                          gguf_file_type=integer_field(reader, 'general.file_type'))
        del reader
        (args.output / 'baseline-model-metrics.json').write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        result = examine(args.output, args.server, args.host, args.port, args.threads, args.baseline_case, args.extra_case, 360)
        report = json.loads(report_path.read_text(encoding='utf-8')) if report_path.is_file() else {}
        if report and report.get('model_file_sha256') != provenance['model_sha256']:
            raise ValueError('SHA-256 экзамена отличается от реально квантованного файла.')
        original = summarize_cases(report, 'cases', ORIGINAL_ORDER)
        additional = summarize_cases(report, 'additional_cases', EXTRA_ORDER)
        complete = (result.get('status') == 'examined' and report.get('all_required_cases_present') is True and
                    len(report.get('cases', [])) == len(ORIGINAL_ORDER) and len(report.get('additional_cases', [])) == len(EXTRA_ORDER))
        payload = {'provenance': provenance, 'status': result, 'complete': complete, 'original_exam': original,
                   'additional_exam': additional, 'evaluation': report,
                   'note': 'Исходная модель без обучения. Эвристики не заменяют ручной проверки ответов.'}
        (args.output / 'baseline-summary.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        if report_path.exists():
            os.replace(report_path, final_report)
        brief = {'quantization': args.quantization, 'untrained_baseline': provenance['untrained_baseline'],
                 'model_bytes': provenance['model_bytes'], 'model_sha256': provenance['model_sha256'], 'complete': complete,
                 'original_passed': original['passed'], 'original_total': original['total'],
                 'additional_passed': additional['passed'], 'additional_total': additional['total'],
                 'cases': {item['case']: item['passed'] for item in original['cases'] + additional['cases']}}
        print(json.dumps(brief, ensure_ascii=False), flush=True)
        if os.environ.get('GITHUB_ACTIONS') == 'true':
            print('::notice file=.github/workflows/benchmark-kolibri-baseline.yml,line=1,title=Baseline ' + args.quantization + '::' + json.dumps(brief, ensure_ascii=False, separators=(',', ':')), flush=True)
            encoded_notices(args.quantization, payload)
        if not complete and not (args.allow_tiny_smoke and (args.baseline_case or args.extra_case)):
            raise RuntimeError('Экзамен базовой модели не завершил все 12 сценариев.')
    finally:
        if model.exists():
            model.unlink()


if __name__ == '__main__':
    main()
