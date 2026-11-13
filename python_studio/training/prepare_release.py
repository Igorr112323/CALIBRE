import argparse
import shutil
from pathlib import Path

from train_kolibri_gguf import MODEL_NAME, sha256_file


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
    license_file = args.output / 'QWEN_LICENSE.txt'
    shutil.copyfile(args.license, license_file)
    shutil.copyfile(args.manifest, args.output / 'dataset-manifest.json')
    if args.report.resolve() != (args.output / args.report.name).resolve():
        shutil.copyfile(args.report, args.output / args.report.name)
    notice = args.output / 'NOTICE.txt'
    notice.write_text('Qwen is licensed under the Qwen RESEARCH LICENSE AGREEMENT, Copyright (c) Alibaba Cloud. All Rights Reserved.\n'
                      'Improved using Qwen. This model is a modified, LoRA-fine-tuned and quantized derivative of Qwen2.5-3B-Instruct.\n'
                      'The Qwen Research License limits the standard grant to non-commercial research or evaluation.\n', encoding='utf-8')
    names = [MODEL_NAME, args.report.name, 'training-metrics.json', 'dataset-manifest.json', 'QWEN_LICENSE.txt', 'NOTICE.txt']
    paths = [args.output / name for name in names]
    if not all(path.is_file() for path in paths):
        raise ValueError('Не найдены все файлы релиза.')
    (args.output / 'SHA256SUMS.txt').write_text(''.join(f'{sha256_file(path)}  {path.name}\n' for path in paths), encoding='utf-8')
    print('Подготовлены контрольные суммы, лицензия и уведомление Qwen.')


if __name__ == '__main__':
    main()
