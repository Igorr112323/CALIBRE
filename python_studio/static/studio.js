const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const token = $('meta[name=kolibri-token]').content;
const state = {project: null, version: '', machineId: '', mode: 'developer', selected: null, selection: '', scope: '', pdf: null, job: '', chatHistory: [], gallery: null, rectangle: null};
let toastTimer;
let enginePoll;

function notify(message) {
  const box = $('#toast');
  box.textContent = message;
  box.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => box.classList.remove('show'), 4500);
}
function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[char]));
}
async function request(url, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.method && options.method !== 'GET') headers.set('X-Kolibri-Token', token);
  const response = await fetch(url, {...options, headers});
  const type = response.headers.get('content-type') || '';
  if (!response.ok) {
    let reason = `Ошибка ${response.status}`;
    try { const data = await response.json(); reason = typeof data.detail === 'string' ? data.detail : reason; } catch {}
    throw Object.assign(new Error(reason), {status: response.status});
  }
  return type.includes('application/json') ? response.json() : response;
}
function jsonOptions(method, data) {
  return {method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)};
}
function config() {
  return {profile: $('#profile').value, custom: $('#customModel').value || '', acceleration: $('#acceleration').value};
}
function machine() {
  return state.project?.machines.find(item => item.id === state.machineId) || null;
}
async function loadProject(preferred = state.machineId, preserve = true) {
  const data = await request('/api/project');
  state.project = data.project;
  state.version = data.version;
  state.machineId = state.project.machines.some(item => item.id === preferred) ? preferred : state.project.machines[0]?.id || '';
  renderMachine();
  if (preserve) refreshPreview();
}
async function saveProject(project, preserve = true) {
  $('#saveStatus').textContent = 'Сохранение…';
  try {
    const data = await request('/api/project', jsonOptions('PUT', {project, version: state.version}));
    state.project = project;
    state.version = data.version;
    renderMachine();
    if (preserve) refreshPreview();
    $('#saveStatus').textContent = 'Каталог сохранён';
  } catch (error) {
    $('#saveStatus').textContent = 'Не сохранено';
    throw error;
  }
}
function renderMachine() {
  if (!state.project) return;
  const current = machine();
  $('#machineCount').textContent = state.project.machines.length;
  $('#machineSelect').innerHTML = state.project.machines.map(item => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.model)}</option>`).join('') || '<option value="">Каталог пуст</option>';
  $('#machineSelect').value = state.machineId;
  $('#machineModel').textContent = current?.model || 'Добавьте машину';
  $('#machineType').textContent = current?.machine_type || 'Импортируйте PDF или JSON';
  $('#machineTag').textContent = current?.id === 'grn471' ? 'ЭТАЛОН' : 'В КАТАЛОГЕ';
  $('#sectionCount').textContent = current ? 7 : 0;
  $('#groupCount').textContent = current?.partGroups?.length || 0;
  $('#partCount').textContent = current?.partGroups?.reduce((count, group) => count + group.items.length, 0) || 0;
  $('#machinePhoto').src = current?.media?.photo || '/static/phone/assets/kolibri-grn471-D_1QthtU.jpg';
  $('#machinePhoto').style.opacity = current?.media?.photo ? '1' : '.35';
  $('#openTree').disabled = !current;
  $('#deleteMachine').disabled = !current;
  const warnings = current?.warnings || [];
  $('#warnings').hidden = !warnings.length;
  $('#warnings').innerHTML = warnings.length ? `<strong>Проверьте исходные данные</strong><ul>${warnings.map(item => `<li>${escapeHtml(item)}</li>`).join('')}</ul>` : '';
}
function previewState() {
  try {
    const doc = $('#phonePreview').contentDocument;
    const app = doc?.querySelector('#app');
    if (!app?.dataset.machineId) return null;
    const ds = app.dataset;
    return {machine: ds.machineId, tab: ds.tab || 'adjustments', screen: ds.screen || 'machine', adjustment: ds.adjustmentId || '', step: ds.step || '0', group: ds.group || '', position: ds.position || '', item: doc.querySelector('.machine-item.is-open')?.dataset.item || '', scroll: String(doc.querySelector('#main')?.scrollTop || 0)};
  } catch { return null; }
}
function refreshPreview(current = previewState()) {
  const params = new URLSearchParams({skip: '1'});
  if (current && state.project?.machines.some(item => item.id === current.machine)) Object.entries(current).forEach(([key, value]) => { if (value) params.set(key, value); });
  $('#phonePreview').src = '/preview?' + params.toString();
}
function setMode(mode) {
  state.mode = mode;
  $('#developerPanel').hidden = mode !== 'developer';
  $('#userPanel').hidden = mode !== 'user';
  $$('#modeCaption')[0].textContent = mode === 'developer' ? 'Управляйте каталогом и исходными документами' : 'Выделите элемент в приложении и опишите правку';
  $$('[data-mode]').forEach(button => { const active = button.dataset.mode === mode; button.classList.toggle('active', active); button.setAttribute('aria-selected', String(active)); });
  if (mode === 'user') refreshPreview();
}
$$('[data-mode]').forEach(button => button.addEventListener('click', () => setMode(button.dataset.mode)));
$$('[data-view]').forEach(button => button.addEventListener('click', () => {
  $$('[data-view]').forEach(item => item.classList.toggle('active', item === button));
  const view = button.dataset.view;
  setMode('developer');
  const section = {catalog: '#catalogSection', documents: '#documentSection', engine: '#engineSection'}[view];
  if (section) $(section).scrollIntoView({behavior: 'smooth', block: 'start'});
  else window.scrollTo({top: 0, behavior: 'smooth'});
}));
$('#machineSelect').addEventListener('change', event => {state.machineId = event.target.value; renderMachine(); refreshPreview(null);});
$('#previewHome').addEventListener('click', () => {state.selection = ''; state.scope = ''; $('#selectionTitle').textContent = 'Откройте нужный экран'; refreshPreview(null);});
$('#userEngine').addEventListener('click', () => {setMode('developer'); $('#engineSection').scrollIntoView({behavior: 'smooth'});});
window.addEventListener('message', event => {
  if (event.origin !== location.origin || event.source !== $('#phonePreview').contentWindow || event.data?.type !== 'preview-selection') return;
  state.selection = event.data.label || '';
  const current = previewState();
  state.scope = current ? `${current.machine}:${current.tab}:${current.screen}:${current.adjustment}:${current.step}:${current.group}:${current.position}:${current.item}` : '';
  $('#selectionTitle').textContent = state.selection || 'Выбранный пункт';
});
$('#exportProject').addEventListener('click', () => {location.href = '/api/export';});
$('#importProject').addEventListener('change', async event => {
  const file = event.target.files[0];
  event.target.value = '';
  if (!file) return;
  try {
    if (file.size > 180 * 1024 * 1024) throw new Error('Файл каталога превышает 180 МБ.');
    const data = JSON.parse(await file.text());
    if (!confirm('Заменить текущий каталог импортированным? Изменение можно отменить в текущем сеансе.')) return;
    await saveProject(data, false);
    state.machineId = data.machines[0]?.id || '';
    renderMachine(); refreshPreview(null); notify('Каталог импортирован');
  } catch (error) {notify(error.message);}
});
$('#deleteMachine').addEventListener('click', async () => {
  if (!machine() || !confirm(`Удалить «${machine().model}» из каталога?`)) return;
  const project = structuredClone(state.project);
  project.machines = project.machines.filter(item => item.id !== state.machineId);
  try {await saveProject(project, false); state.machineId = project.machines[0]?.id || ''; renderMachine(); refreshPreview(null); notify('Машина удалена');} catch (error) {notify(error.message);}
});

function fileSize(bytes) {
  if (!bytes) return '0 МБ';
  return bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(2)} ГБ` : `${(bytes / 1024 ** 2).toFixed(1)} МБ`;
}
function updateModelSelect(models) {
  const select = $('#customModel');
  const prior = select.value;
  select.innerHTML = models.map(item => `<option value="${escapeHtml(item.name)}">${escapeHtml(item.name)} · ${fileSize(item.size)}</option>`).join('') || '<option value="">Нет файлов .gguf в models</option>';
  if (models.some(item => item.name === prior)) select.value = prior;
  select.hidden = $('#profile').value !== 'custom';
}
async function pollEngine() {
  try {
    const status = await request('/api/ai/status');
    updateModelSelect(status.models || []);
    $('#engineState').textContent = status.state === 'ready' ? 'Модель готова' : status.state === 'running' ? 'Подготовка модели' : status.state === 'error' ? 'Не удалось запустить' : 'Модель не запущена';
    $('#engineDescription').textContent = status.running ? `${status.backend} · ${status.model}` : status.stage;
    $('#enginePip').classList.toggle('ready', status.running);
    $('#engineError').textContent = status.error || '';
    $('#startAi').disabled = status.state === 'running';
    $('#engineProgress').hidden = status.state !== 'running';
    $('#engineProgressText').textContent = status.total ? `${fileSize(status.downloaded)} из ${fileSize(status.total)}` : status.stage;
    $('#enginePercent').textContent = status.total ? `${status.percent}%` : '';
    $('#engineBar').style.width = `${Math.max(0, Math.min(100, status.percent || 0))}%`;
  } catch (error) {$('#engineError').textContent = error.message;}
}
$('#profile').addEventListener('change', () => {$('#customModel').hidden = $('#profile').value !== 'custom';});
$('#startAi').addEventListener('click', async () => {
  try {await request('/api/ai/start', jsonOptions('POST', config())); await pollEngine();} catch (error) {notify(error.message);}
});
$('#stopAi').addEventListener('click', async () => {try {await request('/api/ai/stop', jsonOptions('POST', {})); await pollEngine();} catch (error) {notify(error.message);}});

function choosePdf(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith('.pdf')) {notify('Выберите PDF-файл'); return;}
  state.pdf = file;
  $('#pdfDrop strong').textContent = file.name;
  $('#pdfDrop small').textContent = `${fileSize(file.size)} · нажмите для замены`;
  $('#importPdf').disabled = false;
}
$('#pdfFile').addEventListener('change', event => choosePdf(event.target.files[0]));
function dropEvents(element, callback) {
  ['dragenter', 'dragover'].forEach(name => element.addEventListener(name, event => {event.preventDefault(); element.classList.add('dragging');}));
  ['dragleave', 'drop'].forEach(name => element.addEventListener(name, event => {event.preventDefault(); element.classList.remove('dragging');}));
  element.addEventListener('drop', event => callback(event.dataTransfer.files[0]));
}
dropEvents($('#pdfDrop'), choosePdf);
function logPdf(message) {
  const log = $('#pdfLog');
  if (!message || log.lastElementChild?.textContent === message) return;
  const item = document.createElement('div'); item.textContent = message; log.append(item);
  while (log.children.length > 25) log.firstElementChild.remove();
  log.scrollTop = log.scrollHeight;
}
async function watchPdf(identifier) {
  while (true) {
    const job = await request('/api/jobs/' + identifier);
    $('#pdfStatus').textContent = job.message;
    logPdf(job.message);
    const done = job.stage === 'ai' ? job.current : job.page;
    const percent = job.total ? Math.min(100, Math.round(done * 100 / job.total)) : 0;
    $('#pdfPercent').textContent = job.total ? `${done} / ${job.total}` : '';
    $('#pdfBar').style.width = percent + '%';
    if (job.state === 'done') {
      await loadProject(job.machine_id);
      $('#pdfBar').style.width = '100%';
      $('#pdfResult').textContent = `Обработано страниц: ${job.pages}. Все таблицы и исходные страницы сохранены.`;
      notify('Документ добавлен в каталог');
      return;
    }
    if (job.state === 'conflict') {
      $('#pdfResult').innerHTML = '<button class="button secondary compact" id="recoverPdf">Обновить каталог и добавить результат</button>';
      $('#recoverPdf').addEventListener('click', async () => {
        try {
          await loadProject();
          const result = await request(`/api/documents/${identifier}/add`, jsonOptions('POST', {version: state.version}));
          state.project = result.project; state.version = result.version; state.machineId = result.machine_id; renderMachine(); refreshPreview(null); $('#pdfResult').textContent = 'Результат добавлен в каталог.';
        } catch (error) {notify(error.message);}
      });
      return;
    }
    if (job.state === 'error') throw new Error(job.message);
    await new Promise(resolve => setTimeout(resolve, 1100));
  }
}
$('#importPdf').addEventListener('click', async () => {
  if (!state.pdf) return;
  const form = new FormData(); form.append('file', state.pdf);
  const params = new URLSearchParams({...config(), version: state.version, ocr: String($('#useOcr').checked), ai: String($('#useAi').checked)});
  $('#importPdf').disabled = true; $('#pdfProgress').hidden = false; $('#pdfLog').textContent = ''; $('#pdfResult').textContent = ''; $('#pdfStatus').textContent = 'Загрузка файла…';
  try {const job = await request('/api/pdf?' + params.toString(), {method: 'POST', body: form}); state.job = job.id; await watchPdf(job.id);} catch (error) {$('#pdfStatus').textContent = error.message; notify(error.message);} finally {$('#importPdf').disabled = false;}
});

function treeButton(label, path, media, kind) {
  return `<button class="tree-item" data-path="${escapeHtml(path)}" data-media="${escapeHtml(media || '')}" data-kind="${escapeHtml(kind || '')}">${escapeHtml(label)}</button>`;
}
function renderTree() {
  const current = machine();
  if (!current) return;
  const sections = current.sections || [];
  let html = treeButton('◉  Карточка машины', '', 'photo', 'Машина');
  html += `<details open><summary>Регулировки · ${(current.adjustments || []).length}</summary><div class="tree-nested">`;
  (current.adjustments || []).forEach((item, i) => {
    html += `<details><summary>${escapeHtml(item.title)}</summary>${treeButton('Название регулировки', `adjustments.${i}`, '', 'Регулировка')}`;
    (item.steps || []).forEach((step, n) => {html += treeButton(`${n + 1}. ${step.title || 'Шаг'}`, `adjustments.${i}.steps.${n}`, `adjustments/${item.id}/${n}`, 'Шаг регулировки');});
    html += '</details>';
  });
  html += '</div></details>';
  html += `<details><summary>Детали · ${(current.partGroups || []).length} групп</summary><div class="tree-nested">`;
  (current.partGroups || []).forEach((group, i) => {
    html += `<details><summary>${escapeHtml(group.title)} · ${group.items.length}</summary>${treeButton('Название и схема группы', `partGroups.${i}`, `groups/${group.id}`, 'Группа деталей')}`;
    group.items.forEach((item, n) => {html += treeButton(`${item.position}. ${item.name}`, `partGroups.${i}.items.${n}`, `parts/${group.id}/${item.position}`, 'Деталь');});
    html += '</details>';
  });
  html += '</div></details>';
  sections.forEach((section, i) => {
    if (section.id === 'adjustments' || section.id === 'parts') return;
    html += `<details><summary>${escapeHtml(section.title)} · ${section.items.length}</summary><div class="tree-nested">`;
    section.items.forEach((item, n) => {html += treeButton(item.title, `sections.${i}.items.${n}`, `${section.id}/${item.id}`, section.title);});
    html += '</div></details>';
  });
  if (current.source?.document_id) html += `<div class="tree-nested"><a class="tree-item" href="/api/documents/${encodeURIComponent(current.source.document_id)}/source" target="_blank">↗ Оригинальный PDF</a><a class="tree-item" href="/api/documents/${encodeURIComponent(current.source.document_id)}/raw" target="_blank">↗ Все исходные страницы и таблицы</a></div>`;
  $('#tree').innerHTML = html;
  $$('.tree-item[data-path]', $('#tree')).forEach(button => button.addEventListener('click', () => selectTree(button.dataset.path, button.dataset.media, button.dataset.kind, button)));
}
function atPath(root, path) {
  return path ? path.split('.').reduce((node, key) => node?.[key], root) : root;
}
const FIELD_LABELS = {model: 'Название модели', machine_type: 'Тип техники', title: 'Название', subtitle: 'Подзаголовок', text: 'Описание', summary: 'Краткое пояснение', badge: 'Метка', tool: 'Инструмент', reason: 'Возможная причина', name: 'Наименование', designation: 'Обозначение', quantity: 'Количество', note: 'Примечание', purpose: 'Назначение', inspection: 'Осмотр', steps: 'Действия · по одному на строке'};
function fieldsFor(object, path) {
  if (!path) return ['model', 'machine_type'];
  if (/^adjustments\.\d+$/.test(path)) return ['title', 'subtitle'];
  if (/^adjustments\.\d+\.steps\.\d+$/.test(path)) return ['title', 'text', 'summary'];
  if (/^partGroups\.\d+$/.test(path)) return ['title'];
  if (/^partGroups\.\d+\.items\.\d+$/.test(path)) return ['name', 'designation', 'quantity', 'note', 'purpose', 'inspection'];
  return ['title', 'subtitle', 'text', 'summary', 'badge', 'tool', 'reason', ...(Array.isArray(object.steps) && object.steps.every(item => typeof item === 'string') ? ['steps'] : [])];
}
function selectTree(path, media, kind, button) {
  const object = atPath(machine(), path);
  if (!object) return;
  state.selected = {path, media, kind};
  $$('.tree-item.selected', $('#tree')).forEach(item => item.classList.remove('selected'));
  button?.classList.add('selected');
  $('#editEmpty').hidden = true; $('#editForm').hidden = false; $('#mediaEditor').hidden = !media;
  $('#editKind').textContent = kind || 'Элемент';
  $('#editTitle').textContent = object.title || object.name || object.model || 'Редактирование';
  $('#editFields').innerHTML = fieldsFor(object, path).map(field => {
    const value = field === 'steps' ? (object[field] || []).join('\n') : String(object[field] ?? '');
    const multiline = ['text', 'summary', 'note', 'purpose', 'inspection', 'reason', 'steps'].includes(field);
    return `<label class="edit-field"><span>${FIELD_LABELS[field]}</span>${multiline ? `<textarea data-field="${field}">${escapeHtml(value)}</textarea>` : `<input type="text" data-field="${field}" value="${escapeHtml(value)}">`}</label>`;
  }).join('');
  const image = machine()?.media?.[media];
  $('#mediaCurrent').hidden = !image;
  if (image) $('#mediaCurrent').src = image;
  $('#mediaStatus').textContent = '';
  $('#showGallery').disabled = !machine()?.source?.document_id;
}
$('#openTree').addEventListener('click', () => {renderTree(); $('#editEmpty').hidden = false; $('#editForm').hidden = true; $('#mediaEditor').hidden = true; state.selected = null; $('#treeDialog').showModal();});
$('#editForm').addEventListener('submit', async event => {
  event.preventDefault();
  if (!state.selected) return;
  const project = structuredClone(state.project);
  const current = project.machines.find(item => item.id === state.machineId);
  const object = atPath(current, state.selected.path);
  try {
    $$('[data-field]', $('#editFields')).forEach(field => {object[field.dataset.field] = field.dataset.field === 'steps' ? field.value.split('\n').map(line => line.trim()).filter(Boolean) : field.value.trim();});
    const selected = {...state.selected};
    await saveProject(project);
    $$('.tree-item', $('#tree')).find(item => item.dataset.path === selected.path)?.classList.add('selected');
    selectTree(selected.path, selected.media, selected.kind); notify('Изменения сохранены');
  } catch (error) {notify(error.message);}
});
async function applyMedia(image) {
  if (!state.selected?.media) throw new Error('Выберите элемент с иллюстрацией.');
  const project = structuredClone(state.project);
  const current = project.machines.find(item => item.id === state.machineId);
  current.media ||= {};
  if (image) current.media[state.selected.media] = image; else delete current.media[state.selected.media];
  await saveProject(project);
  $('#mediaCurrent').hidden = !image;
  if (image) $('#mediaCurrent').src = image;
  $('#mediaStatus').textContent = image ? 'Изображение сохранено в каталоге.' : 'Изображение удалено.';
}
async function uploadImage(file) {
  if (!file) return;
  try {
    const form = new FormData(); form.append('file', file, file.name || 'image.png');
    $('#mediaStatus').textContent = 'Обрабатываем изображение…';
    const result = await request('/api/media', {method: 'POST', body: form});
    await applyMedia(result.image); notify('Изображение добавлено');
  } catch (error) {$('#mediaStatus').textContent = error.message; notify(error.message);}
}
$('#mediaFile').addEventListener('change', event => {uploadImage(event.target.files[0]); event.target.value = '';});
dropEvents($('#mediaDrop'), uploadImage);
$('#deleteMedia').addEventListener('click', async () => {try {await applyMedia('');} catch (error) {notify(error.message);}});
window.addEventListener('paste', event => {
  if (!$('#treeDialog').open || $('#mediaEditor').hidden) return;
  const item = [...(event.clipboardData?.items || [])].find(entry => entry.type.startsWith('image/'));
  if (item) {event.preventDefault(); uploadImage(item.getAsFile());}
});

async function renderGallery(page) {
  const gallery = state.gallery;
  const info = gallery.gallery.find(item => item.page === page) || {figures: []};
  const id = machine().source.document_id;
  $('#galleryBody').innerHTML = `<div class="gallery-top"><label for="galleryPage">Страница</label><select id="galleryPage">${Array.from({length: gallery.pages}, (_, i) => `<option value="${i + 1}" ${i + 1 === page ? 'selected' : ''}>${i + 1} / ${gallery.pages}</option>`).join('')}</select><button class="button primary compact" id="cropSave" disabled>Использовать выделение</button><small>Выделите область мышью или пальцем</small></div><div class="gallery-layout"><div><div class="crop-wrap" id="cropWrap"><img id="cropImage" draggable="false" src="/api/documents/${id}/pages/${page}" alt="Страница ${page}"><div class="crop-selection" id="cropSelection" hidden></div></div></div><div class="gallery-figures"><p>Найденные иллюстрации · нажмите, чтобы использовать</p>${info.figures.map(figure => `<img data-name="${escapeHtml(figure.name)}" src="/api/documents/${id}/figures/${escapeHtml(figure.name)}" alt="Иллюстрация страницы ${page}">`).join('') || '<p>Автоматических иллюстраций на странице нет. Выделите область вручную.</p>'}</div></div>`;
  $('#galleryPage').addEventListener('change', event => renderGallery(Number(event.target.value)));
  $$('.gallery-figures img').forEach(image => image.addEventListener('click', async () => {
    try {const blob = await (await fetch(image.src)).blob(); await uploadImage(new File([blob], 'figure.jpg', {type: 'image/jpeg'})); $('#galleryDialog').close();} catch (error) {notify(error.message);}
  }));
  const wrap = $('#cropWrap'), selection = $('#cropSelection');
  let start = null;
  const point = event => {const rect = wrap.getBoundingClientRect(); return [Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)), Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height))];};
  wrap.addEventListener('dragstart', event => event.preventDefault());
  wrap.addEventListener('pointerdown', event => {event.preventDefault(); start = point(event); wrap.setPointerCapture(event.pointerId); selection.hidden = false; state.rectangle = null; $('#cropSave').disabled = true;});
  wrap.addEventListener('pointermove', event => {
    if (!start) return;
    const end = point(event), x0 = Math.min(start[0], end[0]), y0 = Math.min(start[1], end[1]), x1 = Math.max(start[0], end[0]), y1 = Math.max(start[1], end[1]);
    state.rectangle = [x0, y0, x1, y1];
    Object.assign(selection.style, {left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%`});
  });
  const endSelection = () => {start = null; $('#cropSave').disabled = !state.rectangle || (state.rectangle[2] - state.rectangle[0]) * (state.rectangle[3] - state.rectangle[1]) < .0001;};
  wrap.addEventListener('pointerup', endSelection);
  wrap.addEventListener('pointercancel', endSelection);
  $('#cropSave').addEventListener('click', async () => {
    try {const result = await request(`/api/documents/${id}/crop`, jsonOptions('POST', {page, rect: state.rectangle})); await applyMedia(result.image); $('#galleryDialog').close(); notify('Область страницы добавлена');} catch (error) {notify(error.message);}
  });
}
$('#showGallery').addEventListener('click', async () => {
  try {state.gallery = await request('/api/documents/' + machine().source.document_id); await renderGallery(1); $('#galleryDialog').showModal();} catch (error) {notify(error.message);}
});
$$('[data-close]').forEach(button => button.addEventListener('click', () => $('#' + button.dataset.close).close()));

function chatLine(text, type) {
  const item = document.createElement('div'); item.className = 'chat-message ' + type; item.textContent = text; $('#chatLog').append(item); $('#chatLog').scrollTop = $('#chatLog').scrollHeight;
}
function reviewPath(path) {
  if (!path) return '';
  const labels = {adjustments: 'Регулировки', steps: 'Шаги', sections: 'Разделы', items: 'Пункты', partGroups: 'Группы деталей', title: 'Название', subtitle: 'Подзаголовок', text: 'Текст', summary: 'Пояснение', name: 'Наименование', designation: 'Обозначение', quantity: 'Количество', note: 'Примечание', purpose: 'Назначение', inspection: 'Осмотр', badge: 'Метка', reason: 'Причина', image: 'Изображение'};
  return path.split('/').slice(1).map(part => {part = part.replace(/~1/g, '/').replace(/~0/g, '~'); return /^\d+$/.test(part) ? String(Number(part) + 1) : (labels[part] || part);}).join(' › ');
}
function showProposal(proposal) {
  const card = document.createElement('section'); card.className = 'chat-review';
  const heading = document.createElement('strong'); heading.textContent = `Предложение ИИ · ${proposal.machine}`; card.append(heading);
  const note = document.createElement('p'); note.textContent = 'Данные ещё не изменены. Просмотрите все поля, удаления и числа; сверьте их с оригиналом. Связанные схемы и старые пояснения могут не обновиться — проверьте их отдельно.'; card.append(note);
  if (proposal.changes.some(change => change.before_path?.endsWith('/text') && change.before.length >= 80 && change.after_present && change.after.length < change.before.length * .4)) {
    const warning = document.createElement('p'); warning.className = 'chat-review-warning'; warning.textContent = 'Внимание: текст сильно сокращён. Проверьте, не исчезли ли важные действия и меры безопасности.'; card.append(warning);
  }
  const list = document.createElement('div'); list.className = 'chat-review-list'; list.tabIndex = 0;
  for (const change of proposal.changes) {
    const item = document.createElement('article'); item.className = 'chat-review-change';
    const path = document.createElement('strong');
    const oldPath = reviewPath(change.before_path), newPath = reviewPath(change.after_path);
    path.textContent = oldPath && newPath && oldPath !== newPath ? `${oldPath} → ${newPath}` : (oldPath || newPath);
    item.append(path);
    for (const [label, present, value] of [['Было', change.before_present, change.before], ['Станет', change.after_present, change.after]]) {
      const group = document.createElement('div'); const caption = document.createElement('small'); caption.textContent = label;
      const text = document.createElement('pre'); text.textContent = !present ? 'Нет элемента' : value === '' ? 'Пустая строка' : value;
      group.append(caption, text); item.append(group);
    }
    list.append(item);
  }
  card.append(list);
  const acknowledgment = document.createElement('label'); acknowledgment.className = 'chat-review-ack';
  const checkbox = document.createElement('input'); checkbox.type = 'checkbox';
  acknowledgment.append(checkbox, document.createTextNode('Я просмотрел все предложенные изменения'));
  card.append(acknowledgment);
  const actions = document.createElement('div'); actions.className = 'chat-review-actions';
  const accept = document.createElement('button'); accept.type = 'button'; accept.className = 'button primary compact'; accept.textContent = 'Сохранить изменения'; accept.disabled = true;
  const discard = document.createElement('button'); discard.type = 'button'; discard.className = 'button secondary compact'; discard.textContent = 'Отклонить';
  actions.append(accept, discard); card.append(actions);
  const status = document.createElement('p'); status.className = 'chat-review-status'; status.setAttribute('aria-live', 'polite'); card.append(status);
  checkbox.addEventListener('change', () => {accept.disabled = !checkbox.checked;});
  discard.addEventListener('click', async () => {
    discard.disabled = true; accept.disabled = true; checkbox.disabled = true;
    try {
      await request('/api/chat/discard', jsonOptions('POST', {proposal_id: proposal.id}));
      status.textContent = 'Предложение отклонено. Его правки не применены.';
      state.chatHistory.push({role: 'assistant', content: 'Пользователь отклонил предложенные изменения; эти правки не применялись.'});
    } catch (error) {status.textContent = error.message; discard.disabled = false; checkbox.disabled = false; accept.disabled = !checkbox.checked;}
  });
  accept.addEventListener('click', async () => {
    accept.disabled = true; discard.disabled = true; checkbox.disabled = true;
    const expectedVersion = state.version;
    try {
      const result = await request('/api/chat/apply', jsonOptions('POST', {proposal_id: proposal.id}));
      if (state.version !== expectedVersion) await loadProject();
      else {state.project = result.project; state.version = result.version; renderMachine(); refreshPreview();}
      status.textContent = 'Изменения сохранены. Их можно отменить в текущем сеансе.';
      state.chatHistory.push({role: 'assistant', content: 'Пользователь подтвердил и сохранил предложенные изменения.'});
    } catch (error) {
      let hint = 'Отправьте запрос заново.';
      if (error.status === 409) {
        try {await loadProject(); hint = 'Каталог обновлён. Отправьте запрос заново.';} catch {hint = 'Обновите страницу и отправьте запрос заново.';}
      }
      status.textContent = `Не сохранено: ${error.message} ${hint}`;
      discard.disabled = false;
    }
  });
  $('#chatLog').append(card); $('#chatLog').scrollTop = $('#chatLog').scrollHeight;
}
$('#chatForm').addEventListener('submit', async event => {
  event.preventDefault();
  const message = $('#chatInput').value.trim();
  if (!message) return;
  const screen = previewState() || {machine: state.machineId, screen: 'machine', tab: 'adjustments', step: '0'};
  const scope = `${screen.machine}:${screen.tab}:${screen.screen}:${screen.adjustment || ''}:${screen.step || ''}:${screen.group || ''}:${screen.position || ''}:${screen.item || ''}`;
  const selected = state.scope === scope ? state.selection : '';
  const expectedVersion = state.version;
  chatLine(message, 'user'); $('#chatInput').value = ''; $('#chatSend').disabled = true; $('#phonePreview').style.pointerEvents = 'none';
  try {
    const result = await request('/api/chat', jsonOptions('POST', {...config(), machine_id: screen.machine || state.machineId, screen, selection: selected, message, history: state.chatHistory.slice(-6), version: expectedVersion}));
    chatLine(result.reply, 'assistant');
    const status = result.proposal ? 'Предложенные изменения ещё не сохранены.' : result.warning || 'Каталог не изменён.';
    state.chatHistory.push({role: 'user', content: message}, {role: 'assistant', content: result.reply + '\n' + status});
    if (state.version !== expectedVersion) {
      if (result.proposal) await request('/api/chat/discard', jsonOptions('POST', {proposal_id: result.proposal.id}));
      chatLine('Каталог изменился во время ответа. Предложение отклонено; отправьте запрос заново.', 'error');
    } else {
      state.project = result.project; state.version = result.version; renderMachine();
      if (result.unverified_quantities?.length) chatLine(`В ответе или правках есть значения, не найденные в показанном источнике или запросе: ${result.unverified_quantities.join(', ')}. Они не проверены. Сверьте с руководством; автоматическая проверка не гарантирует точности остальных значений.`, 'error');
      if (result.warning) chatLine(result.warning, 'error');
      if (result.proposal) showProposal(result.proposal);
      else if (!result.warning) chatLine('Каталог не изменён. Значения и меры безопасности сверьте с руководством.', 'assistant');
    }
  } catch (error) {
    chatLine(error.message, 'error');
    if (error.status === 409) {
      try {await loadProject(); chatLine('Каталог обновлён. Отправьте запрос заново.', 'assistant');} catch {chatLine('Обновите страницу перед повторным запросом.', 'error');}
    }
  } finally {$('#chatSend').disabled = false; $('#phonePreview').style.pointerEvents = '';}
});
$('#undo').addEventListener('click', async () => {
  try {const result = await request('/api/undo', jsonOptions('POST', {version: state.version})); state.project = result.project; state.version = result.version; if (!machine()) state.machineId = state.project.machines[0]?.id || ''; renderMachine(); refreshPreview(); notify('Последнее изменение отменено');} catch (error) {notify(error.message);}
});
$('#manualEdit').addEventListener('click', () => {
  const screen = previewState();
  if (screen?.machine) {state.machineId = screen.machine; renderMachine();}
  renderTree(); $('#editEmpty').hidden = false; $('#editForm').hidden = true; $('#mediaEditor').hidden = true; $('#treeDialog').showModal();
  const current = machine();
  if (!current) {$('#treeDialog').close(); notify('Сначала добавьте машину в каталог.'); return;}
  let path = '';
  if (screen?.screen === 'wizard') {const i = current.adjustments.findIndex(item => item.id === screen.adjustment); if (i >= 0) path = `adjustments.${i}.steps.${Number(screen.step) || 0}`;}
  else if (screen?.screen === 'part') {const g = current.partGroups.findIndex(item => item.id === screen.group); const i = g >= 0 ? current.partGroups[g].items.findIndex(item => String(item.position) === screen.position) : -1; if (i >= 0) path = `partGroups.${g}.items.${i}`;}
  else if (screen?.item) {const section = screen.tab === 'problems' ? 'faults' : screen.tab; const i = current.sections.findIndex(item => item.id === section); const n = i >= 0 ? current.sections[i].items.findIndex(item => item.id === screen.item) : -1; if (n >= 0) path = `sections.${i}.items.${n}`;}
  const button = $$('.tree-item[data-path]', $('#tree')).find(item => item.dataset.path === path);
  if (button) {let parent = button.parentElement; while (parent && parent !== $('#tree')) {if (parent.tagName === 'DETAILS') parent.open = true; parent = parent.parentElement;} button.click(); button.scrollIntoView({block: 'nearest'});}
});

function openBuild() {$('#buildStatus').textContent = ''; $('#buildDialog').showModal();}
$('#buildApk').addEventListener('click', openBuild);
$('#buildApkCard').addEventListener('click', openBuild);
$('#confirmBuild').addEventListener('click', async () => {
  const button = $('#confirmBuild'); button.disabled = true; $('#buildStatus').textContent = 'Собираем и подписываем APK…';
  try {
    const response = await request('/api/build', jsonOptions('POST', {version: state.version}));
    const blob = await response.blob(); const url = URL.createObjectURL(blob); const link = document.createElement('a'); link.href = url; link.download = 'Kolibri.apk'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 60000);
    $('#buildStatus').textContent = `Готово · ${fileSize(blob.size)}. Проверьте установку на своём устройстве.`; notify('APK собран');
  } catch (error) {$('#buildStatus').textContent = error.message;} finally {button.disabled = false;}
});

loadProject().then(() => {pollEngine(); enginePoll = setInterval(pollEngine, 2000);}).catch(error => notify(error.message));
