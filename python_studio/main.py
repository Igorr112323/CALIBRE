import asyncio
import copy
import io
import json
import os
import re
import secrets
import threading
import time
import uuid
import zlib
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps

from ai_review import build_review
from apk_builder import build_apk
from document_pipeline import crop_page, image_data_url, parse_document
from local_ai import LocalAI
from operations import apply_operations, chat, enrich, machine_by_id
from store import ConflictError, ProjectStore, serialize, validate_project

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / 'static'
DOCUMENTS = ROOT / 'data' / 'documents'
STORE = ProjectStore()
AI = LocalAI()
JOBS = {}
JOB_LOCK = threading.RLock()
TOKEN = secrets.token_urlsafe(24)
MAX_JSON = 180 * 1024 * 1024
PROPOSALS = {}
PROPOSAL_LOCK = threading.RLock()
PROPOSAL_TTL = 30 * 60
PROPOSAL_COUNT_LIMIT = 8
PROPOSAL_BYTES_LIMIT = 120 * 1024 * 1024


def prepare_proposal(project, machine, context, operations):
    candidate = copy.deepcopy(machine)
    apply_operations(candidate, context, operations)
    candidate_project = dict(project)
    candidate_project['machines'] = [candidate if item['id'] == machine['id'] else item for item in project['machines']]
    validate_project(candidate_project)
    if len(serialize(candidate_project)) > MAX_JSON:
        raise ValueError('Проект с предложенными правками превышает 180 МБ; данные не изменены.')
    changes = build_review(machine, candidate)
    return candidate, changes


def register_proposal(candidate, version, changes):
    data = zlib.compress(json.dumps(candidate, ensure_ascii=False, separators=(',', ':')).encode('utf-8'), 3)
    if len(data) > PROPOSAL_BYTES_LIMIT:
        raise ValueError('Предложение слишком велико для безопасного просмотра. Используйте ручной редактор.')
    now = time.monotonic()
    with PROPOSAL_LOCK:
        for identifier, pending in list(PROPOSALS.items()):
            if pending['expires'] <= now:
                del PROPOSALS[identifier]
        while PROPOSALS and (len(PROPOSALS) >= PROPOSAL_COUNT_LIMIT or sum(len(item['data']) for item in PROPOSALS.values()) + len(data) > PROPOSAL_BYTES_LIMIT):
            del PROPOSALS[next(iter(PROPOSALS))]
        identifier = secrets.token_urlsafe(24)
        PROPOSALS[identifier] = {'data': data, 'version': version, 'machine_id': candidate['id'],
                                 'changed': len(changes), 'expires': now + PROPOSAL_TTL}
    return {'id': identifier, 'machine': candidate['model'], 'changes': changes, 'expires_seconds': PROPOSAL_TTL}


def take_proposal(identifier):
    if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z0-9_-]{20,64}', identifier):
        raise HTTPException(410, 'Предложение не найдено или устарело. Отправьте запрос заново.')
    with PROPOSAL_LOCK:
        pending = PROPOSALS.pop(identifier, None)
    if not pending or pending['expires'] <= time.monotonic():
        raise HTTPException(410, 'Предложение не найдено или устарело. Отправьте запрос заново.')
    return pending


@asynccontextmanager
async def lifespan(app):
    yield
    AI.stop()


app = FastAPI(title='Kolibri Python Studio', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware('http')
async def security(request: Request, call_next):
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and not secrets.compare_digest(request.headers.get('x-kolibri-token', ''), TOKEN):
        return JSONResponse({'detail': 'Сеанс не подтверждён. Обновите страницу.'}, status_code=403)
    origin = request.headers.get('origin')
    if request.method not in ('GET', 'HEAD', 'OPTIONS') and origin and origin != str(request.base_url).rstrip('/'):
        return JSONResponse({'detail': 'Запрос с другого источника запрещён.'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


@app.exception_handler(ValueError)
async def invalid_handler(request, error):
    return JSONResponse({'detail': str(error)}, status_code=400)


@app.exception_handler(ConflictError)
async def conflict_handler(request, error):
    return JSONResponse({'detail': str(error)}, status_code=409)


@app.exception_handler(RuntimeError)
async def runtime_handler(request, error):
    return JSONResponse({'detail': str(error)}, status_code=503)


def status_job(identifier, **updates):
    with JOB_LOCK:
        if identifier in JOBS:
            JOBS[identifier].update(updates)


def safe_document(identifier):
    if not re.fullmatch(r'[a-f0-9]{32}', identifier or ''):
        raise ValueError('Документ не найден.')
    path = DOCUMENTS / identifier
    if not path.is_dir() or path.is_symlink():
        raise ValueError('Документ не найден.')
    return path


def job_data(identifier):
    with JOB_LOCK:
        job = JOBS.get(identifier)
        if not job:
            raise HTTPException(404, 'Задача не найдена.')
        return copy.deepcopy({key: value for key, value in job.items() if key != 'thread'})


def profile_options(body):
    profile = body.get('profile', 'mehfak')
    custom = body.get('custom', '')
    acceleration = body.get('acceleration', 'cpu')
    if profile not in ('mehfak', '3b', '7b', 'custom') or acceleration not in ('cpu', 'vulkan') or not isinstance(custom, str):
        raise ValueError('Неизвестный профиль ИИ.')
    return profile, custom, acceleration


@app.get('/')
def index():
    content = (STATIC / 'index.html').read_text(encoding='utf-8').replace('__SESSION_TOKEN__', TOKEN)
    return HTMLResponse(content, headers={'Cache-Control': 'no-store'})


@app.get('/api/project')
def get_project():
    project, version = STORE.get()
    return {'project': project, 'version': version, 'can_undo': bool(STORE.history)}


@app.put('/api/project')
async def save_project(request: Request):
    length = int(request.headers.get('content-length') or 0)
    if length > MAX_JSON:
        raise ValueError('Проект превышает допустимый размер.')
    body = await request.json()
    version = STORE.save(body.get('project'), body.get('version', ''))
    return {'version': version}


@app.post('/api/undo')
async def undo(request: Request):
    body = await request.json()
    project, version = STORE.undo(body.get('version', ''))
    return {'project': project, 'version': version, 'can_undo': bool(STORE.history)}


@app.get('/api/export')
def export_project():
    project, _ = STORE.get()
    return Response(json.dumps(project, ensure_ascii=False, indent=2), media_type='application/json', headers={'Content-Disposition': 'attachment; filename="kolibri-catalog.json"'})


@app.get('/api/ai/status')
def ai_status():
    return AI.status()


@app.post('/api/ai/start')
async def ai_start(request: Request):
    body = await request.json()
    return AI.start(*profile_options(body))


@app.post('/api/ai/stop')
def ai_stop():
    AI.stop()
    return AI.status()


def run_pdf(identifier, version, options):
    directory = DOCUMENTS / identifier
    def progress(stage, **details):
        status_job(identifier, stage=stage, message=details.get('message', stage), page=details.get('page', 0), total=details.get('total', 0), current=details.get('current', 0))
    try:
        result = parse_document(directory / 'source.pdf', directory, progress, options['ocr'])
        machine = result['machine']
        if options['ai']:
            progress('ai', message='Ожидание локальной модели')
            try:
                AI.wait_ready(*options['profile'])
                enrich(AI, machine, progress, *options['profile'])
            except Exception as error:
                machine['warnings'].append('Локальная ИИ-обработка не выполнена: ' + str(error)[:300] + '. Исходные данные сохранены.')
        (directory / 'machine.json').write_text(json.dumps(machine, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
        (directory / 'summary.json').write_text(json.dumps({key: value for key, value in result.items() if key != 'machine'}, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
        try:
            _, new_version, _ = STORE.mutate(version, lambda project: project['machines'].append(machine))
            status_job(identifier, state='done', stage='done', message='PDF добавлен в каталог. Проверьте источники и предупреждения.', version=new_version, machine_id=machine['id'], document_id=identifier, warnings=machine['warnings'], pages=result['pages'])
        except ConflictError:
            status_job(identifier, state='conflict', stage='conflict', message='Каталог изменился во время обработки. Результат сохранён, добавьте его после обновления.', document_id=identifier, warnings=machine['warnings'], pages=result['pages'])
    except Exception as error:
        status_job(identifier, state='error', stage='error', message=str(error), error=type(error).__name__)


@app.post('/api/pdf')
async def upload_pdf(request: Request, file: UploadFile = File(...)):
    query = request.query_params
    version = query.get('version', '')
    _, current = STORE.get()
    if version != current:
        raise ConflictError('Каталог изменился. Обновите страницу перед загрузкой PDF.')
    if not file.filename or not file.filename.lower().endswith('.pdf'):
        raise ValueError('Выберите PDF-файл.')
    identifier = uuid.uuid4().hex
    directory = DOCUMENTS / identifier
    directory.mkdir(parents=True)
    size = 0
    try:
        with (directory / 'source.pdf').open('wb') as target:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > 150 * 1024 * 1024:
                    raise ValueError('PDF превышает 150 МБ.')
                target.write(chunk)
        with (directory / 'source.pdf').open('rb') as source:
            if source.read(5) != b'%PDF-':
                raise ValueError('Файл не является PDF.')
        options = {'ocr': query.get('ocr', 'true') == 'true', 'ai': query.get('ai', 'false') == 'true', 'profile': profile_options(dict(query))}
        with JOB_LOCK:
            JOBS[identifier] = {'id': identifier, 'state': 'running', 'stage': 'upload', 'message': 'PDF получен', 'page': 0, 'total': 0, 'current': 0}
        threading.Thread(target=run_pdf, args=(identifier, version, options), daemon=True).start()
        return job_data(identifier)
    except Exception:
        import shutil
        shutil.rmtree(directory, ignore_errors=True)
        raise
    finally:
        await file.close()


@app.get('/api/jobs/{identifier}')
def get_job(identifier: str):
    return job_data(identifier)


@app.post('/api/documents/{identifier}/add')
async def add_pdf_result(identifier: str, request: Request):
    directory = safe_document(identifier)
    body = await request.json()
    machine = json.loads((directory / 'machine.json').read_text(encoding='utf-8'))
    def add(project):
        if any(item['id'] == machine['id'] for item in project['machines']):
            raise ValueError('Эта машина уже добавлена.')
        project['machines'].append(machine)
    project, version, _ = STORE.mutate(body.get('version', ''), add)
    return {'project': project, 'version': version, 'machine_id': machine['id']}


@app.get('/api/documents/{identifier}')
def document_metadata(identifier: str):
    directory = safe_document(identifier)
    path = directory / 'summary.json'
    if not path.is_file():
        raise HTTPException(404, 'Обработка документа ещё не завершена.')
    return json.loads(path.read_text(encoding='utf-8'))


@app.get('/api/documents/{identifier}/pages/{page}')
def document_page(identifier: str, page: int):
    path = safe_document(identifier) / 'pages' / f'{page}.jpg'
    if page < 1 or not path.is_file():
        raise HTTPException(404, 'Страница не найдена.')
    return FileResponse(path, media_type='image/jpeg')


@app.get('/api/documents/{identifier}/figures/{name}')
def document_figure(identifier: str, name: str):
    if not re.fullmatch(r'\d+-\d+\.jpg', name):
        raise HTTPException(404, 'Иллюстрация не найдена.')
    path = safe_document(identifier) / 'figures' / name
    if not path.is_file():
        raise HTTPException(404, 'Иллюстрация не найдена.')
    return FileResponse(path, media_type='image/jpeg')


@app.get('/api/documents/{identifier}/source')
def document_source(identifier: str):
    return FileResponse(safe_document(identifier) / 'source.pdf', media_type='application/pdf', filename='source.pdf')


@app.get('/api/documents/{identifier}/raw')
def document_raw(identifier: str):
    path = safe_document(identifier) / 'raw-pages.json'
    if not path.is_file():
        raise HTTPException(404, 'Исходные страницы ещё не извлечены.')
    return FileResponse(path, media_type='application/json', filename='raw-pages.json')


@app.post('/api/documents/{identifier}/crop')
async def document_crop(identifier: str, request: Request):
    body = await request.json()
    image = await asyncio.to_thread(crop_page, safe_document(identifier) / 'source.pdf', body.get('page'), body.get('rect'))
    return {'image': image}


@app.post('/api/media')
async def upload_media(file: UploadFile = File(...)):
    data = await file.read(12 * 1024 * 1024 + 1)
    await file.close()
    if len(data) > 12 * 1024 * 1024:
        raise ValueError('Изображение превышает 12 МБ.')
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.width * source.height > 40_000_000 or source.format not in ('PNG', 'JPEG', 'WEBP'):
                raise ValueError('Разрешены PNG, JPEG и WebP размером до 40 мегапикселей.')
            image = ImageOps.exif_transpose(source).convert('RGB')
            image.thumbnail((2600, 2600))
            return {'image': image_data_url(image, 88)}
    except ValueError:
        raise
    except Exception as error:
        raise ValueError('Не удалось открыть изображение.') from error


@app.post('/api/chat')
async def chat_request(request: Request):
    body = await request.json()
    project, version = STORE.get()
    if version != body.get('version'):
        raise ConflictError('Каталог изменился. Обновите страницу.')
    machine = machine_by_id(project, body.get('machine_id'))
    screen = body.get('screen') or {}
    selection = body.get('selection') or ''
    history = body.get('history') or []
    if not isinstance(history, list) or not isinstance(selection, str) or len(selection) > 4000:
        raise ValueError('Неверная история диалога или слишком длинное выделение.')
    answer, context = await asyncio.to_thread(chat, AI, machine, screen, selection, body.get('message'), history, *profile_options(body))
    if STORE.get()[1] != version:
        raise ConflictError('Каталог изменился во время ответа ИИ. Отправьте запрос заново.')
    proposal = None
    warning = ''
    if answer['operations']:
        try:
            candidate, changes = await asyncio.to_thread(prepare_proposal, project, machine, context, answer['operations'])
            if STORE.get()[1] != version:
                raise ConflictError('Каталог изменился во время подготовки правок. Отправьте запрос заново.')
            if changes:
                proposal = await asyncio.to_thread(register_proposal, candidate, version, changes)
            else:
                warning = 'ИИ не предложил фактических изменений. Данные не изменены.'
        except ValueError as error:
            warning = 'Предложенные ИИ правки отклонены: ' + str(error)
    return {'reply': answer['reply'], 'project': project, 'version': version, 'changed': 0,
            'proposal': proposal, 'warning': warning, 'unverified_quantities': answer.get('unverified_quantities', [])}


@app.post('/api/chat/apply')
async def apply_chat_proposal(request: Request):
    body = await request.json()
    pending = take_proposal(body.get('proposal_id'))
    candidate = json.loads(zlib.decompress(pending['data']))

    def change(current):
        for index, item in enumerate(current['machines']):
            if item['id'] == pending['machine_id']:
                current['machines'][index] = candidate
                return pending['changed']
        raise ConflictError('Машина больше не найдена; правки не применены.')

    project, version, changed = await asyncio.to_thread(STORE.mutate, pending['version'], change)
    return {'project': project, 'version': version, 'changed': changed}


@app.post('/api/chat/discard')
async def discard_chat_proposal(request: Request):
    body = await request.json()
    identifier = body.get('proposal_id')
    if isinstance(identifier, str):
        with PROPOSAL_LOCK:
            PROPOSALS.pop(identifier, None)
    return {'discarded': True}


@app.post('/api/build')
async def create_apk(request: Request):
    body = await request.json()
    project, version = STORE.get()
    if version != body.get('version'):
        raise ConflictError('Каталог изменился. Обновите страницу перед сборкой.')
    validate_project(project)
    data = await asyncio.to_thread(build_apk, project)
    return Response(data, media_type='application/vnd.android.package-archive', headers={'Content-Disposition': 'attachment; filename="Kolibri.apk"', 'Cache-Control': 'no-store'})


@app.get('/download/Kolibri-Python-Studio.zip')
def download_studio():
    archive = ROOT.parent / 'Kolibri-Python-Studio.zip'
    if not archive.is_file():
        raise HTTPException(404, 'Архив не найден.')
    return FileResponse(archive, media_type='application/zip', filename='Kolibri-Python-Studio.zip')


@app.get('/preview')
def preview():
    html = (STATIC / 'phone' / 'index.html').read_text(encoding='utf-8')
    html = html.replace('./runtime-catalog.js', '/preview/catalog.js').replace('./assets/', '/static/phone/assets/')
    html = html.replace('</head>', '<link rel="stylesheet" href="/static/preview.css"><script defer src="/static/preview.js"></script></head>')
    return HTMLResponse(html, headers={'Cache-Control': 'no-store'})


@app.get('/preview/catalog.js')
def preview_catalog():
    project, _ = STORE.get()
    value = json.dumps(project, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    return Response('window.__KOLIBRI_CATALOG__=' + value + ';', media_type='text/javascript', headers={'Cache-Control': 'no-store'})


app.mount('/static', StaticFiles(directory=STATIC), name='static')


if __name__ == '__main__':
    host = os.environ.get('KOLIBRI_HOST', '127.0.0.1')
    port = int(os.environ.get('KOLIBRI_PORT', '8765'))
    uvicorn.run(app, host=host, port=port, proxy_headers=True, forwarded_allow_ips='*')
