import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

from train_kolibri_gguf import MODEL_NAME

ROOT = Path(__file__).resolve().parents[1]
REPORT_NAME = 'AI_EVALUATION_QWEN25_3B_FINETUNED.json'


def wait_for_server(process, port, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('llama-server завершился до готовности.')
        try:
            response = httpx.get(f'http://127.0.0.1:{port}/health', timeout=2, trust_env=False)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(2)
    raise TimeoutError('llama-server не подготовил модель вовремя.')


def examine(directory, server, host, port, threads, baseline_cases, extra_cases, timeout):
    model = directory / MODEL_NAME
    report = directory / REPORT_NAME
    if not model.is_file():
        return {'directory': str(directory), 'status': 'not_built', 'error': 'GGUF-файл не найден.'}
    if report.exists():
        raise ValueError('Отчёт уже существует; используйте чистый каталог.')
    command = [str(server), '--model', str(model), '--alias', MODEL_NAME, '--host', host, '--port', str(port),
               '--ctx-size', '8192', '--threads', str(threads), '--parallel', '1', '--jinja', '-ngl', '0']
    result = {'directory': str(directory), 'status': 'error'}
    with (directory / 'llama-server.log').open('w', encoding='utf-8') as log:
        process = None
        try:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            wait_for_server(process, port, timeout)
            evaluation = [sys.executable, '-u', str(ROOT / 'training' / 'evaluate_finetuned.py'), '--port', str(port),
                          '--model-file', str(model), '--expected-server-id', MODEL_NAME, '--out', str(report)]
            for name in baseline_cases:
                evaluation.extend(['--baseline-case', name])
            for name in extra_cases:
                evaluation.extend(['--extra-case', name])
            subprocess.run(evaluation, check=True, timeout=1800)
            data = json.loads(report.read_text(encoding='utf-8'))
            result.update(status='examined', original_passed=data.get('heuristic_checks_passed'),
                          original_total=len(data.get('cases', [])), extra_passed=data.get('additional_heuristic_checks_passed'),
                          extra_total=len(data.get('additional_cases', [])), all_passed=data.get('all_heuristics_passed'))
        except Exception as error:
            result['error'] = type(error).__name__ + ': ' + str(error)
            (directory / 'examination-error.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=15)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description='Экзамен каждого фактически собранного GGUF-варианта.')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--secondary-output', type=Path)
    parser.add_argument('--server', type=Path, required=True)
    parser.add_argument('--host', choices=('127.0.0.1', '0.0.0.0'), default='127.0.0.1')
    parser.add_argument('--port', type=int, default=18951)
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--startup-timeout', type=int, default=360)
    parser.add_argument('--baseline-case', action='append', default=[])
    parser.add_argument('--extra-case', action='append', default=[])
    args = parser.parse_args()
    if not args.server.is_file() or args.threads < 1 or args.startup_timeout < 1 or not 1 <= args.port <= 65535:
        parser.error('Некорректный сервер, порт или параметры.')
    args.output.mkdir(parents=True, exist_ok=True)
    directories = [args.output] + ([args.secondary_output] if args.secondary_output is not None else [])
    results = [examine(directory, args.server, args.host, args.port, args.threads, args.baseline_case, args.extra_case, args.startup_timeout)
               for directory in directories]
    (args.output / 'candidate-examinations.json').write_text(json.dumps(results, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
