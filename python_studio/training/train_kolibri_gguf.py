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


def interleave(examples, seed):
    rng = random.Random(seed)
    buckets = defaultdict(list)
    for example in examples:
        buckets[example['category']].append(example)
    for bucket in buckets.values():
        rng.shuffle(bucket)
    queues = {key: deque(values) for key, values in buckets.items()}
    ordered = []
    while queues:
        keys = sorted(queues)
        rng.shuffle(keys)
        for key in keys:
            ordered.append(queues[key].popleft())
            if not queues[key]:
                del queues[key]
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


def train(args):
    import torch
    from peft import LoraConfig, TaskType, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    args.output.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(args.threads)
    torch.set_num_interop_threads(1)
    torch.manual_seed(args.seed)
    random.seed(args.seed)
    examples = interleave(read_examples(args.dataset), args.seed)
    source = args.model
    revision = args.revision if source == BASE_MODEL else None
    emit('environment', model=source, revision=revision, threads=torch.get_num_threads(),
         torch=torch.__version__, cpu_count=os.cpu_count(), precision='bfloat16', device='cpu')
    tokenizer = AutoTokenizer.from_pretrained(source, revision=revision, trust_remote_code=False)
    rows = tokenize_examples(examples, tokenizer, args.max_length)
    lengths = [len(row['input_ids']) for row in rows]
    emit('dataset', examples=len(rows), min_tokens=min(lengths), max_tokens=max(lengths),
         mean_tokens=round(statistics.mean(lengths), 2), max_length=args.max_length, sha256=sha256_file(args.dataset))
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
    probe_count = min(4, len(rows), args.max_steps if args.max_steps else len(rows))
    probe = rows[:probe_count]
    before, before_individual = probe_loss(model, probe)
    norm_before = adapter_norm(model)
    emit('probe_before', loss=before, individual=before_individual, examples=[row['id'] for row in probe])
    durations = []
    losses = []
    steps = 0
    planned = args.max_steps if args.max_steps else len(rows)
    started = time.monotonic()
    log_path = args.output / 'training-log.jsonl'
    with log_path.open('w', encoding='utf-8') as log:
        while steps < planned:
            row = rows[steps % len(rows)]
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
            durations.append(duration)
            losses.append(float(loss.detach().item()))
            event = {'step': steps, 'example': row['id'], 'category': row['category'],
                     'tokens': len(row['input_ids']), 'loss': losses[-1], 'seconds': round(duration, 3),
                     'gradient_norm': float(norm)}
            log.write(json.dumps(event, ensure_ascii=False) + '\n')
            log.flush()
            emit('step', **event)
            if steps == 3:
                seconds_per_step = statistics.mean(durations[:3])
                budget_steps = max(1, int(args.target_minutes * 60 / seconds_per_step))
                if not args.max_steps:
                    planned = max(len(rows), min(len(rows) * args.max_epochs, budget_steps))
                emit('calibration', first_three_seconds=[round(value, 3) for value in durations[:3]],
                     mean_seconds_per_step=round(seconds_per_step, 3),
                     estimated_one_pass_minutes=round(seconds_per_step * len(rows) / 60, 2),
                     requested_minutes=args.target_minutes, planned_steps=planned,
                     note=('Явный лимит шагов: неполный smoke-тест не годится для выпуска.' if args.max_steps else
                           'Минимум один проход по всем примерам; при медленном CPU цель по времени может быть превышена.'))
    elapsed = time.monotonic() - started
    after, after_individual = probe_loss(model, probe)
    norm_after = adapter_norm(model)
    if norm_after <= norm_before:
        raise RuntimeError('Веса LoRA не изменились.')
    metrics = {
        'base_model': source, 'revision': revision, 'dataset_sha256': sha256_file(args.dataset),
        'examples': len(rows), 'steps': steps, 'unique_examples_seen': len({rows[index % len(rows)]['id'] for index in range(steps)}),
        'full_pass': steps >= len(rows), 'max_length': args.max_length, 'actual_max_length': max(lengths),
        'threads': args.threads, 'dtype': 'bfloat16', 'device': 'cpu', 'target_minutes': args.target_minutes,
        'training_seconds': round(elapsed, 3), 'first_three_step_seconds': durations[:3],
        'layers_total': layer_count, 'lora_layers': selected_layers, 'lora_rank': 16, 'lora_alpha': 32,
        'lora_targets': TARGET_MODULES, 'trainable_parameters': trainable_parameters, 'total_parameters': total_parameters,
        'lora_b_norm_before': norm_before, 'lora_b_norm_after': norm_after,
        'probe_ids': [row['id'] for row in probe], 'probe_loss_before': before, 'probe_loss_after': after,
        'probe_individual_before': before_individual, 'probe_individual_after': after_individual,
        'probe_loss_decreased': after < before, 'loss_note': 'Одинаковая диагностическая подвыборка из обучающих примеров, не экзамен и не оценка обобщения.',
        'torch_version': torch.__version__,
    }
    (args.output / 'training-metrics.json').write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    emit('probe_after', loss=after, individual=after_individual, decreased=after < before,
         lora_b_norm=norm_after, training_seconds=round(elapsed, 3))
    model.save_pretrained(args.output / 'adapter', safe_serialization=True)
    merged = model.merge_and_unload(safe_merge=False)
    merged.config.use_cache = True
    merged.save_pretrained(args.output / 'merged', safe_serialization=True, max_shard_size='2GB')
    tokenizer.save_pretrained(args.output / 'merged')
    emit('saved', adapter=str(args.output / 'adapter'), merged=str(args.output / 'merged'))


def convert(args):
    converter = args.llama_source / 'convert_hf_to_gguf.py'
    merged = args.output / 'merged'
    if not converter.is_file() or not args.quantize.is_file() or not (merged / 'config.json').is_file():
        raise ValueError('Не найдены конвертер, llama-quantize или объединённая модель.')
    bf16 = args.output / 'merged-bf16.gguf'
    result = args.output / MODEL_NAME
    if bf16.exists() or result.exists():
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
    emit('quantize_start', input_bytes=bf16.stat().st_size, quantization='Q4_K_M')
    subprocess.run([str(args.quantize), str(bf16), str(result), 'Q4_K_M', str(args.threads)], check=True)
    with result.open('rb') as handle:
        if handle.read(4) != b'GGUF':
            raise ValueError('Квантователь не создал GGUF.')
    info = {'model_file': MODEL_NAME, 'quantization': 'Q4_K_M', 'bytes': result.stat().st_size,
            'sha256': sha256_file(result), 'limit_bytes_exclusive': MAX_MODEL_BYTES,
            'under_size_limit': result.stat().st_size < MAX_MODEL_BYTES}
    (args.output / 'gguf-metrics.json').write_text(json.dumps(info, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if not args.keep_bf16:
        bf16.unlink()
    emit('quantized', **info)


def main():
    parser = argparse.ArgumentParser(description='Реальное CPU LoRA-обучение Qwen2 и конвертация в GGUF.')
    parser.add_argument('--model', default=BASE_MODEL)
    parser.add_argument('--revision', default=BASE_REVISION)
    parser.add_argument('--dataset', type=Path, default=ROOT / 'training' / 'train.jsonl')
    parser.add_argument('--output', type=Path, default=ROOT / 'training' / 'output')
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--max-length', type=int, default=448)
    parser.add_argument('--target-minutes', type=float, default=32)
    parser.add_argument('--max-epochs', type=int, default=2)
    parser.add_argument('--max-steps', type=int, default=0)
    parser.add_argument('--learning-rate', type=float, default=0.0002)
    parser.add_argument('--seed', type=int, default=471)
    parser.add_argument('--convert-only', action='store_true')
    parser.add_argument('--llama-source', type=Path)
    parser.add_argument('--quantize', type=Path)
    parser.add_argument('--keep-bf16', action='store_true')
    args = parser.parse_args()
    if args.threads < 1 or args.max_length < 32 or args.target_minutes <= 0 or args.max_epochs < 1 or args.max_steps < 0:
        parser.error('Некорректные параметры обучения.')
    if args.convert_only:
        if not args.llama_source or not args.quantize:
            parser.error('Для конвертации нужны --llama-source и --quantize.')
        convert(args)
    else:
        train(args)


if __name__ == '__main__':
    main()
