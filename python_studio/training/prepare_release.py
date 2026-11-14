import argparse
import json
import shutil
from pathlib import Path

from train_kolibri_gguf import MAX_MODEL_BYTES, MODEL_NAME, QUANTIZATION_FILE_TYPES, sha256_file


def main():
    parser = argparse.ArgumentParser(description='Подготовить лицензии и контрольные суммы для проверенного релиза.')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--license', required=True, type=Path)
    parser.add_argument('--manifest', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    model = args.output / MODEL_NAME
    if not model.is_file() or not args.license.is_file() or not args.report.is_file():
        raise ValueError('Не найдены модель, лицензия Qwen или отчёт экзамена.')
    gate = json.loads((args.output / 'release-gate.json').read_text(encoding='utf-8'))
    selected = gate.get('selected_quantization')
    candidate = gate.get('candidates', {}).get(selected, {})
    size = model.stat().st_size
    sources = {'training': sha256_file(args.output / 'training-metrics.json'),
               'conversion': sha256_file(args.output / 'gguf-metrics.json'),
               'exam': sha256_file(args.report), 'manifest': sha256_file(args.manifest)}
    if (gate.get('passed') is not True or selected not in QUANTIZATION_FILE_TYPES or
            candidate.get('passed') is not True or candidate.get('model_bytes') != size or
            not 4 <= size < MAX_MODEL_BYTES or candidate.get('model_sha256') != sha256_file(model) or
            candidate.get('source_sha256') != sources):
        raise ValueError('Файлы изменились после проверки или выпускной контроль не пройден.')
    license_file = args.output / 'QWEN_LICENSE.txt'
    shutil.copyfile(args.license, license_file)
    shutil.copyfile(args.manifest, args.output / 'dataset-manifest.json')
    if args.report.resolve() != (args.output / args.report.name).resolve():
        shutil.copyfile(args.report, args.output / args.report.name)
    notice = args.output / 'NOTICE.txt'
    notice.write_text('Qwen is licensed under the Qwen RESEARCH LICENSE AGREEMENT, Copyright (c) Alibaba Cloud. All Rights Reserved.\n'
                      'Improved using Qwen. This model is a modified, LoRA-fine-tuned and quantized derivative of Qwen2.5-3B-Instruct.\n'
                      'The Qwen Research License limits the standard grant to non-commercial research or evaluation.\n', encoding='utf-8')
    names = [MODEL_NAME, args.report.name, 'training-metrics.json', 'dataset-manifest.json',
             'gguf-metrics.json', 'release-gate.json', 'QWEN_LICENSE.txt', 'NOTICE.txt']
    paths = [args.output / name for name in names]
    if not all(path.is_file() for path in paths):
        raise ValueError('Не найдены все файлы релиза.')
    (args.output / 'SHA256SUMS.txt').write_text(''.join(f'{sha256_file(path)}  {path.name}\n' for path in paths), encoding='utf-8')
    print('Подготовлены контрольные суммы, лицензия и уведомление Qwen.')


if __name__ == '__main__':
    main()
