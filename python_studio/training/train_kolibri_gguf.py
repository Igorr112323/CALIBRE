import argparse
import hashlib
import json
import math
import os
import random
import statistics
import subprocess
import sys
import time
from collections import defaultdict, deque
from pathlib import Path

BASE_MODEL = 'Qwen/Qwen2.5-3B-Instruct'
BASE_REVISION = 'aa8e72537993ba99e69dfaafa59ed015b17504d1'
ROOT = Path(__file__).resolve().parents[1]
TARGET_MODULES = ['q_proj', 'k_proj', 'v_proj', 'o_proj', 'gate_proj', 'up_proj', 'down_proj']
MAX_MODEL_BYTES = 1_950_000_000
MODEL_NAME = 'kolibri-qwen2.5-mehfak.gguf'
QUANTIZATION_FILE_TYPES = {'Q3_K_M': 12, 'Q4_K_S': 14}


def sha256_file(path):
    value = hashlib.sha256()
    with path.open('rb') as handle:
        while chunk := handle.read(1024 * 1024):
            value.update(chunk)
    return value.hexdigest()


def emit(event, **values):
    print(json.dumps({'event': event, **values}, ensure_ascii=False), flush=True)


def read_examples(path):
    examples = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    if not examples or len({example['id'] for example in examples}) != len(examples):
        raise ValueError('Датасет пуст или содержит повторяющиеся идентификаторы.')
    for item in examples:
        if [message.get('role') for message in item.get('messages', [])] != ['system', 'user', 'assistant']:
            raise ValueError('Некорректные роли сообщения: ' + str(item.get('id')))
        answer = json.loads(item['messages'][2]['content'])
        if set(answer) != {'reply', 'operations'}:
            raise ValueError('Некорректный ответ Studio: ' + str(item['id']))
    return examples


def validate_split(training, validation, facts):
    from build_dataset import source_pages
    from operations import chat_messages

    if not training or not validation:
        raise ValueError('Обучающая и отложенная выборки должны быть непустыми.')
    train_ids = {item['id'] for item in training}
    validation_ids = {item['id'] for item in validation}
    train_pages = set().union(*(source_pages(item, facts) for item in training))
    validation_pages = set().union(*(source_pages(item, facts) for item in validation))
    train_answers = {item['messages'][2]['content'] for item in training}
    validation_answers = {item['messages'][2]['content'] for item in validation}
    if train_ids & validation_ids or train_pages & validation_pages or train_answers & validation_answers:
        raise ValueError('Обучение и отложенная выборка пересекаются по идентификаторам, страницам PDF или готовым ответам.')
    system = chat_messages({}, 'Проверка формата', [])[0]['content']
    for item in training + validation:
        messages = item['messages']
        payload = json.loads(messages[1]['content'])
        context = payload.get('контекст', {})
        if messages[0]['content'] != system or set(payload) != {'контекст', 'запрос'} or not isinstance(context, dict):
            raise ValueError('Формат примера не совпадает с реальным запросом Studio: ' + item['id'])
        if not {'screen', 'selection', 'model', 'machine_type', 'tab', 'allowed', 'context_truncated'} <= set(context):
            raise ValueError('Контекст примера не соответствует Studio: ' + item['id'])
    return {'training_pages': sorted(train_pages), 'validation_pages': sorted(validation_pages),
            'system_prompt_sha256': hashlib.sha256(system.encode('utf-8')).hexdigest()}


class EarlyStopping:
    def __init__(self, baseline, patience=1, min_delta=0.0001):
        if not math.isfinite(baseline) or patience < 1 or min_delta < 0:
            raise ValueError('Некорректные параметры отложенного контроля.')
        self.reference = baseline
        self.best_checkpoint = math.inf
        self.best_step = None
        self.failures = 0
        self.patience = patience
        self.min_delta = min_delta

    def observe(self, loss, step):
        if not math.isfinite(loss) or step < 1:
            raise ValueError('Некорректный loss отложенной выборки.')
        save = loss < self.best_checkpoint
        if save:
            self.best_checkpoint, self.best_step = loss, step
        if loss < self.reference - self.min_delta:
            self.reference, self.failures = loss, 0
        else:
            self.failures += 1
        return save, self.failures >= self.patience


def interleave(examples, seed):
    rng = random.Random(seed)
    buckets = defaultdict(list)
    for example in examples:
        buckets[example['category']].append(example)
    for bucket in buckets.values():
        rng.shuffle(bucket)
    queues = {key: deque(values) for key, values in buckets.items()}
    keys = sorted(queues)
    rng.shuffle(keys)
    ordered = [queues[key].popleft() for key in keys]
    remainder = []
    for key in sorted(queues):
        queue = queues[key]
        count = len(queue)
        for index, example in enumerate(queue):
            remainder.append(((index + rng.random()) / count, rng.random(), example))
    remainder.sort(key=lambda item: (item[0], item[1]))
    ordered.extend(item[2] for item in remainder)
    return ordered


def tokenize_examples(examples, tokenizer, max_length):
    rows = []
    too_long = []
    for example in examples:
        messages = example['messages']
        prompt = tokenizer.apply_chat_template(messages[:2], tokenize=True, add_generation_prompt=True)
        complete = tokenizer.apply_chat_template(messages, tokenize=True, add_generation_prompt=False)
        if complete[:len(prompt)] != prompt or len(complete) <= len(prompt):
            raise ValueError('Шаблон не сохраняет границу ответа: ' + example['id'])
        if len(complete) > max_length:
            too_long.append((example['id'], len(complete)))
        rows.append({'id': example['id'], 'category': example['category'], 'input_ids': complete,
                     'prompt_length': len(prompt)})
    if too_long:
        raise ValueError(f'Примеры не помещаются в {max_length} токенов; данные не обрезаны: {too_long[:10]}')
    return rows


def model_loss(model, row):
    import torch
    from torch.nn import functional as functional
    ids = torch.tensor([row['input_ids']], dtype=torch.long, device='cpu')
    count = ids.shape[1] - row['prompt_length']
    result = model(input_ids=ids[:, :-1], use_cache=False, logits_to_keep=count)
    targets = ids[:, row['prompt_length']:]
    if result.logits.shape[1] != targets.shape[1]:
        raise RuntimeError('Число выходных токенов не совпадает с ответом.')
    return functional.cross_entropy(result.logits.float().reshape(-1, result.logits.shape[-1]), targets.reshape(-1))


def probe_loss(model, rows):
    import torch
    model.eval()
    with torch.no_grad():
        losses = [float(model_loss(model, row).item()) for row in rows]
    model.train()
    return sum(losses) / len(losses), losses


def adapter_norm(model):
    import torch
    total = 0.0
    with torch.no_grad():
        for name, parameter in model.named_parameters():
            if parameter.requires_grad and 'lora_B' in name:
                total += float(parameter.float().pow(2).sum().item())
    return math.sqrt(total)


def adapter_sha256(named_parameters):
    import torch
    digest = hashlib.sha256()
    for name, parameter in named_parameters:
        encoded = name.encode('utf-8')
        digest.update(len(encoded).to_bytes(4, 'big'))
        digest.update(encoded)
        digest.update(memoryview(parameter.detach().cpu().contiguous().view(torch.uint8).numpy()))
    return digest.hexdigest()


def train(args):
    import torch
    from peft import LoraConfig, TaskType, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    args.output.mkdir(parents=True, exist_ok=True)
    if (args.output / 'training-metrics.json').exists():
        raise ValueError('Для обучения нужен чистый выходной каталог.')
    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(1)
    torch.manual_seed(args.seed)
    random.seed(args.seed)
    training = read_examples(args.dataset)
    validation = read_examples(args.validation_dataset)
    facts = json.loads(args.facts.read_text(encoding='utf-8'))
    split = validate_split(training, validation, facts)
    examples = interleave(training, args.seed)
    validation_examples = interleave(validation, args.seed + 1)
    source = args.model
    revision = args.revision if source == BASE_MODEL else None
    emit('environment', model=source, revision=revision, threads=torch.get_num_threads(),
         torch=torch.__version__, cpu_count=os.cpu_count(), precision='bfloat16', device='cpu')
    tokenizer = AutoTokenizer.from_pretrained(source, revision=revision, trust_remote_code=False)
    if not isinstance(tokenizer.chat_template, str) or not tokenizer.chat_template:
        raise ValueError('У токенизатора отсутствует ChatML-шаблон.')
    template_sha256 = hashlib.sha256(tokenizer.chat_template.encode('utf-8')).hexdigest()
    rows = tokenize_examples(examples, tokenizer, args.max_length)
    probe = tokenize_examples(validation_examples, tokenizer, args.max_length)
    lengths = [len(row['input_ids']) for row in rows + probe]
    emit('dataset', examples=len(rows), validation_examples=len(probe), min_tokens=min(lengths), max_tokens=max(lengths),
         mean_tokens=round(statistics.mean(lengths), 2), max_length=args.max_length, sha256=sha256_file(args.dataset),
         validation_sha256=sha256_file(args.validation_dataset), **split)
    model = AutoModelForCausalLM.from_pretrained(source, revision=revision, torch_dtype=torch.bfloat16,
                                                  low_cpu_mem_usage=True, attn_implementation='sdpa',
                                                  trust_remote_code=False, use_safetensors=True)
    if getattr(model.config, 'model_type', None) != 'qwen2':
        raise ValueError('Ожидается модель архитектуры Qwen2.')
    model.to('cpu')
    model.config.use_cache = False
    layer_count = int(model.config.num_hidden_layers)
    selected_layers = list(range(layer_count - math.ceil(layer_count / 3), layer_count))
    config = LoraConfig(r=16, lora_alpha=32, lora_dropout=0.0, bias='none', task_type=TaskType.CAUSAL_LM,
                        target_modules=TARGET_MODULES, layers_to_transform=selected_layers, layers_pattern='layers')
    model = get_peft_model(model, config)
    trainable = [(name, parameter) for name, parameter in model.named_parameters() if parameter.requires_grad]
    if not trainable or any('lora_' not in name for name, _ in trainable):
        raise RuntimeError('Не удалось ограничить обучение адаптером LoRA.')
    for layer in selected_layers:
        for projection in TARGET_MODULES:
            if not any(f'.layers.{layer}.' in name and f'.{projection}.' in name for name, _ in trainable):
                raise RuntimeError(f'LoRA не установлена на {layer}.{projection}.')
    if any(not any(f'.layers.{layer}.' in name for layer in selected_layers) for name, _ in trainable):
        raise RuntimeError('Обучаемый параметр вне верхней трети слоёв.')
    total_parameters = sum(parameter.numel() for parameter in model.parameters())
    trainable_parameters = sum(parameter.numel() for _, parameter in trainable)
    emit('lora', layers=selected_layers, projections=TARGET_MODULES, rank=16, alpha=32,
         trainable_parameters=trainable_parameters, total_parameters=total_parameters)
    optimizer = torch.optim.AdamW([parameter for _, parameter in trainable], lr=args.learning_rate, foreach=False)
    before, before_individual = probe_loss(model, probe)
    norm_before = adapter_norm(model)
    monitor = EarlyStopping(before, args.patience, args.min_delta)
    validation_history = [{'step': 0, 'epoch': 0, 'loss': before, 'individual': before_individual}]
    emit('probe_before', loss=before, individual=before_individual, examples=[row['id'] for row in probe])
    durations = []
    losses = []
    seen = set()
    steps = 0
    planned = min(args.max_steps, len(rows) * args.max_epochs) if args.max_steps else len(rows) * args.max_epochs
    started = time.monotonic()
    best_state = None
    best_step_loss = None
    best_adapter_sha256 = None
    stop_reason = 'epoch_limit' if not args.max_steps else 'smoke_step_limit'
    epoch_rows = []
    current_epoch = -1
    log_path = args.output / 'training-log.jsonl'
    with log_path.open('w', encoding='utf-8') as log:
        while steps < planned:
            epoch = steps // len(rows)
            if epoch != current_epoch:
                epoch_rows = list(rows)
                if epoch:
                    random.Random(args.seed + epoch).shuffle(epoch_rows)
                current_epoch = epoch
            row = epoch_rows[steps % len(rows)]
            tick = time.monotonic()
            optimizer.zero_grad(set_to_none=True)
            loss = model_loss(model, row)
            if not torch.isfinite(loss):
                raise RuntimeError('Loss стал бесконечным или NaN.')
            loss.backward()
            norm = torch.nn.utils.clip_grad_norm_([parameter for _, parameter in trainable], 1.0)
            if not torch.isfinite(norm):
                raise RuntimeError('Градиент стал бесконечным или NaN.')
            optimizer.step()
            duration = time.monotonic() - tick
            steps += 1
            seen.add(row['id'])
            durations.append(duration)
            losses.append(float(loss.detach().item()))
            event = {'step': steps, 'epoch': round(steps / len(rows), 6), 'example': row['id'], 'category': row['category'],
                     'tokens': len(row['input_ids']), 'loss': losses[-1], 'seconds': round(duration, 3),
                     'gradient_norm': float(norm)}
            log.write(json.dumps(event, ensure_ascii=False) + '\n')
            log.flush()
            emit('step', **event)
            if steps == 3:
                seconds_per_step = statistics.mean(durations[:3])
                one_pass_seconds = seconds_per_step * len(rows)
                if not args.max_steps and args.max_epochs > 1 and one_pass_seconds * args.max_epochs > args.target_minutes * 60:
                    planned = len(rows)
                    stop_reason = 'time_budget_one_epoch'
                emit('calibration', first_three_seconds=[round(value, 3) for value in durations[:3]],
                     mean_seconds_per_step=round(seconds_per_step, 3), estimated_one_pass_minutes=round(one_pass_seconds / 60, 2),
                     requested_minutes=args.target_minutes, planned_steps=planned,
                     note='Планируются только полные эпохи; при медленном CPU один проход может превысить бюджет.')
            if steps % len(rows) == 0 or args.max_steps and steps == planned:
                current, individual = probe_loss(model, probe)
                save, should_stop = monitor.observe(current, steps)
                if save:
                    best_state = [parameter.detach().clone() for _, parameter in trainable]
                    best_adapter_sha256 = adapter_sha256(trainable)
                    best_step_loss = losses[-1]
                observation = {'step': steps, 'epoch': round(steps / len(rows), 6), 'loss': current,
                               'individual': individual, 'selected_at_time': save, 'stop': should_stop}
                validation_history.append(observation)
                emit('probe_checkpoint', **observation)
                if should_stop:
                    stop_reason = 'heldout_loss_not_improved'
                    break
    if best_state is None or monitor.best_step is None:
        raise RuntimeError('Не получен контрольный снимок обучаемого адаптера.')
    with torch.no_grad():
        for (_, parameter), saved in zip(trainable, best_state):
            parameter.copy_(saved)
    del best_state
    after, after_individual = probe_loss(model, probe)
    restored_adapter_sha256 = adapter_sha256(trainable)
    if restored_adapter_sha256 != best_adapter_sha256:
        raise RuntimeError('Веса восстановленного адаптера не совпадают с лучшим снимком.')
    if not math.isclose(after, monitor.best_checkpoint, rel_tol=0.00001, abs_tol=0.00001):
        raise RuntimeError('Loss восстановленного адаптера не совпадает с лучшим снимком.')
    norm_after = adapter_norm(model)
    if norm_after <= norm_before:
        raise RuntimeError('Веса LoRA не изменились.')
    elapsed = time.monotonic() - started
    metrics = {
        'base_model': source, 'revision': revision, 'dataset_sha256': sha256_file(args.dataset),
        'validation_sha256': sha256_file(args.validation_dataset), 'facts_sha256': sha256_file(args.facts),
        'training_log_sha256': sha256_file(log_path),
        'examples': len(rows), 'validation_examples': len(probe), 'steps': steps, 'selected_step': monitor.best_step,
        'epochs_completed': steps // len(rows), 'epochs_seen': round(steps / len(rows), 6),
        'selected_epoch': round(monitor.best_step / len(rows), 6), 'max_epochs': args.max_epochs,
        'explicit_step_limit': args.max_steps, 'unique_examples_seen': len(seen),
        'full_pass': len(seen) == len(rows) and monitor.best_step >= len(rows),
        'max_length': args.max_length, 'actual_max_length': max(lengths),
        'threads': args.threads, 'dtype': 'bfloat16', 'device': 'cpu', 'target_minutes': args.target_minutes,
        'training_seconds': round(elapsed, 3), 'first_three_step_seconds': durations[:3],
        'final_step_loss': losses[-1], 'selected_step_loss': best_step_loss, 'learning_rate': args.learning_rate,
        'layers_total': layer_count, 'lora_layers': selected_layers, 'lora_rank': 16, 'lora_alpha': 32,
        'lora_targets': TARGET_MODULES, 'trainable_parameters': trainable_parameters, 'total_parameters': total_parameters,
        'lora_b_norm_before': norm_before, 'lora_b_norm_after': norm_after,
        'probe_ids': [row['id'] for row in probe], 'probe_loss_before': before, 'probe_loss_after': after,
        'probe_individual_before': before_individual, 'probe_individual_after': after_individual,
        'probe_loss_decreased': after < before, 'validation_history': validation_history,
        'early_stopping': {'stop_reason': stop_reason, 'patience': args.patience, 'min_delta': args.min_delta,
                           'best_step': monitor.best_step, 'best_loss': monitor.best_checkpoint,
                           'best_adapter_sha256': best_adapter_sha256, 'restored_adapter_sha256': restored_adapter_sha256,
                           'restored_best': True, 'checked_after_each_complete_epoch': not bool(args.max_steps)},
        'tokenizer_chat_template_sha256': template_sha256, **split,
        'loss_note': 'Отложенная выборка не пересекается с обучающей по страницам PDF, идентификаторам и готовым ответам; это не экзамен и не гарантия отсутствия близких по смыслу фактов.',
        'torch_version': torch.__version__,
    }
    (args.output / 'training-metrics.json').write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    emit('probe_after', loss=after, individual=after_individual, decreased=after < before, selected_step=monitor.best_step,
         stop_reason=stop_reason, lora_b_norm=norm_after, training_seconds=round(elapsed, 3))
    model.save_pretrained(args.output / 'adapter', safe_serialization=True)
    merged = model.merge_and_unload(safe_merge=False)
    merged.config.use_cache = True
    merged.save_pretrained(args.output / 'merged', safe_serialization=True, max_shard_size='2GB')
    tokenizer.save_pretrained(args.output / 'merged')
    emit('saved', adapter=str(args.output / 'adapter'), merged=str(args.output / 'merged'))


def quantize_gguf(source, output, executable, quantization, threads):
    if quantization not in QUANTIZATION_FILE_TYPES or not source.is_file() or not executable.is_file():
        raise ValueError('Не найдены исходный GGUF, квантователь или поддерживаемый тип квантования.')
    with source.open('rb') as handle:
        if handle.read(4) != b'GGUF':
            raise ValueError('Исходный файл не является GGUF.')
    output.mkdir(parents=True, exist_ok=True)
    result = output / MODEL_NAME
    metrics_path = output / 'gguf-metrics.json'
    if result.exists() or metrics_path.exists():
        raise ValueError('Файл модели или её метрики уже существуют; используйте чистый каталог.')
    emit('quantize_start', input_bytes=source.stat().st_size, quantization=quantization)
    subprocess.run([str(executable), str(source), str(result), quantization, str(threads)], check=True)
    with result.open('rb') as handle:
        if handle.read(4) != b'GGUF':
            raise ValueError('Квантователь не создал GGUF.')
    info = {'model_file': MODEL_NAME, 'quantization': quantization, 'bytes': result.stat().st_size,
            'sha256': sha256_file(result), 'limit_bytes_exclusive': MAX_MODEL_BYTES,
            'under_size_limit': result.stat().st_size < MAX_MODEL_BYTES}
    metrics_path.write_text(json.dumps(info, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    emit('quantized', **info)
    return info


def convert(args):
    converter = args.llama_source / 'convert_hf_to_gguf.py'
    merged = args.output / 'merged'
    if not converter.is_file() or not args.quantize.is_file() or not (merged / 'config.json').is_file():
        raise ValueError('Не найдены конвертер, llama-quantize или объединённая модель.')
    bf16 = args.output / 'merged-bf16.gguf'
    if bf16.exists() or (args.output / MODEL_NAME).exists() or (args.output / 'gguf-metrics.json').exists():
        raise ValueError('Файлы GGUF уже существуют; используйте чистый каталог вывода.')
    metrics = json.loads((args.output / 'training-metrics.json').read_text(encoding='utf-8'))
    is_qwen3b = metrics.get('base_model') == BASE_MODEL and metrics.get('revision') == BASE_REVISION
    metadata = {'general.name': 'Kolibri Qwen2.5-3B Mehfak' if is_qwen3b else 'Kolibri Qwen2 local smoke test',
                'general.description': 'Modified Qwen2.5-3B-Instruct: LoRA fine-tuned on GRN-471 and quantized. Improved using Qwen.' if is_qwen3b else 'Local LoRA and GGUF conversion smoke test, not Qwen2.5-3B.',
                'general.finetune': 'GRN-471 LoRA'}
    if is_qwen3b:
        metadata.update({'general.license.name': 'Qwen Research License',
                         'general.license.link': 'https://huggingface.co/Qwen/Qwen2.5-3B-Instruct/blob/main/LICENSE',
                         'general.source.repo_url': 'https://huggingface.co/Qwen/Qwen2.5-3B-Instruct'})
    metadata_path = args.output / 'gguf-metadata.json'
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    emit('convert_start', source=str(merged))
    subprocess.run([sys.executable, str(converter), str(merged), '--outtype', 'bf16', '--metadata', str(metadata_path), '--outfile', str(bf16)], check=True)
    with bf16.open('rb') as handle:
        if handle.read(4) != b'GGUF':
            raise ValueError('Конвертер не создал GGUF.')
    quantize_gguf(bf16, args.output, args.quantize, args.quantization, args.threads)
    if not args.keep_bf16:
        bf16.unlink()


def main():
    parser = argparse.ArgumentParser(description='Реальное CPU LoRA-обучение Qwen2 и конвертация в GGUF.')
    parser.add_argument('--model', default=BASE_MODEL)
    parser.add_argument('--revision', default=BASE_REVISION)
    parser.add_argument('--dataset', type=Path, default=ROOT / 'training' / 'train.jsonl')
    parser.add_argument('--validation-dataset', type=Path, default=ROOT / 'training' / 'validation.jsonl')
    parser.add_argument('--facts', type=Path, default=ROOT / 'training' / 'manual_facts.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'training' / 'output')
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--max-length', type=int, default=2048)
    parser.add_argument('--target-minutes', type=float, default=32)
    parser.add_argument('--max-epochs', type=int, default=2)
    parser.add_argument('--max-steps', type=int, default=0)
    parser.add_argument('--learning-rate', type=float, default=0.00005)
    parser.add_argument('--patience', type=int, default=1)
    parser.add_argument('--min-delta', type=float, default=0.0001)
    parser.add_argument('--seed', type=int, default=471)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--convert-only', action='store_true')
    mode.add_argument('--quantize-only', action='store_true')
    parser.add_argument('--llama-source', type=Path)
    parser.add_argument('--quantize', type=Path)
    parser.add_argument('--source-gguf', type=Path)
    parser.add_argument('--quantization', choices=tuple(QUANTIZATION_FILE_TYPES), default='Q3_K_M')
    parser.add_argument('--keep-bf16', action='store_true')
    args = parser.parse_args()
    if (args.threads < 1 or args.max_length < 32 or args.target_minutes <= 0 or not 1 <= args.max_epochs <= 2 or
            args.max_steps < 0 or not 0 < args.learning_rate <= 0.00005 or args.patience < 1 or args.min_delta < 0):
        parser.error('Некорректные параметры обучения.')
    if args.convert_only:
        if not args.llama_source or not args.quantize:
            parser.error('Для конвертации нужны --llama-source и --quantize.')
        convert(args)
    elif args.quantize_only:
        if not args.source_gguf or not args.quantize:
            parser.error('Для квантования нужны --source-gguf и --quantize.')
        quantize_gguf(args.source_gguf, args.output, args.quantize, args.quantization, args.threads)
    else:
        train(args)


if __name__ == '__main__':
    main()
