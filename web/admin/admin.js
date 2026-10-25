const $ = id => document.getElementById(id);
const state = {machines: [], selectedMachine: Number(localStorage.getItem('kolibri-admin-machine') || 0), toastTimer: null};
const statusText = {
  answered: 'Ответ прошёл проверку цитат и чисел', part_lookup: 'Сверенный ответ по индексу каталога; модель не вызывалась', no_evidence: 'В источниках подтверждение не найдено',
  not_configured: 'LLM не подключена; индекс и гипотезы доступны', remote_endpoint_requires_explicit_opt_in: 'Удалённая модель заблокирована без явного разрешения',
  model_error: 'Модель не ответила корректно', invalid_model_output: 'Формат ответа модели отклонён',
  uncited_answer_rejected: 'Ответ без проверяемой цитаты заблокирован', unsupported_numbers_rejected: 'Неподтверждённые числа заблокированы'
};

function toast(message, error = false) {
  const element = $('toast');
  element.textContent = message;
  element.classList.toggle('error', error);
  element.classList.add('show');
  clearTimeout(state.toastTimer);
  state.toastTimer = setTimeout(() => element.classList.remove('show'), 3300);
}

async function request(url, options = {}) {
  const response = await fetch(url, {headers: options.body instanceof FormData ? undefined : {'Content-Type': 'application/json'}, ...options});
  if (!response.ok) {
    let detail = `Ошибка ${response.status}`;
    try { const body = await response.json(); detail = body.detail || body.message || detail; } catch {}
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  if (response.status === 204) return null;
  return response.json();
}

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function showView(name) {
  document.querySelectorAll('.view').forEach(view => view.classList.toggle('active', view.id === `view-${name}`));
  document.querySelectorAll('.nav-item').forEach(button => button.classList.toggle('active', button.dataset.view === name));
  const titles = {overview: 'Рабочий стол', documents: 'Документы и фото', parts: 'Узлы и запчасти', assistant: 'Проверка ИИ'};
  $('page-title').textContent = titles[name] || titles.overview;
  if (name === 'documents') { loadDocuments(); loadImages(); }
  if (name === 'parts') loadParts();
}

document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => showView(button.dataset.view)));
document.querySelectorAll('[data-go]').forEach(button => button.addEventListener('click', () => showView(button.dataset.go)));

function selectedMachine() {
  const id = Number($('machine-select').value || state.selectedMachine || 0);
  return state.machines.find(item => item.id === id) || null;
}

async function loadMachines() {
  try {
    state.machines = await request('/api/admin/machines');
    const select = $('machine-select');
    select.replaceChildren();
    if (!state.machines.length) {
      select.add(new Option('Сначала добавьте технику', ''));
      state.selectedMachine = 0;
      $('machine-cards').replaceChildren(node('div', 'empty-box', 'Пока нет добавленной техники. Создайте карточку Kolibri ГРН-471, затем загрузите руководство.'));
      $('machine-form').closest('.machine-create').hidden = false;
    } else {
      state.machines.forEach(machine => select.add(new Option(`${machine.manufacturer} ${machine.model} · ${machine.code}`, String(machine.id))));
      const stored = state.machines.find(machine => machine.id === state.selectedMachine);
      state.selectedMachine = (stored || state.machines[0]).id;
      select.value = String(state.selectedMachine);
      localStorage.setItem('kolibri-admin-machine', String(state.selectedMachine));
      $('machine-form').closest('.machine-create').hidden = true;
      const cards = $('machine-cards');
      cards.replaceChildren();
      state.machines.forEach(machine => {
        const row = node('article', 'machine-row');
        row.append(node('span', 'machine-avatar', 'K'));
        const info = node('div');
        info.append(node('strong', '', `${machine.manufacturer} ${machine.model}`));
        info.append(node('small', '', `${machine.machine_type} · ${machine.code}`));
        row.append(info, node('span', 'machine-state', machine.has_documents ? 'Есть документы' : 'Нужен документ'));
        cards.append(row);
      });
    }
    $('machine-select').onchange = () => {
      state.selectedMachine = Number(select.value || 0);
      localStorage.setItem('kolibri-admin-machine', String(state.selectedMachine));
      loadDocuments(); loadImages(); loadParts();
    };
    await Promise.all([loadStats(), loadDocuments(), loadImages(), loadParts()]);
  } catch (error) {
    toast(`Не удалось загрузить технику: ${error.message}`, true);
  }
}

async function loadStats() {
  try {
    const data = await request('/api/admin/stats');
    $('stat-machines').textContent = data.machines;
    $('stat-documents').textContent = data.documents;
    $('stat-pages').textContent = `${data.pages} страниц проиндексировано`;
    $('stat-images').textContent = data.images;
    const model = data.ai;
    $('stat-ai').textContent = model.configured ? 'Подключена' : 'Не подключена';
    $('stat-ai-detail').textContent = model.configured ? model.model : 'экспертный поиск готов · LLM не встроена';
    const assistantStatus = $('assistant-status');
    assistantStatus.textContent = model.configured ? `Внешняя/локальная LLM: ${model.model}` :
      (model.remote_endpoint_blocked ? 'Подключение внешней LLM заблокировано' : 'Экспертный поиск Kolibri доступен · LLM не подключена');
    assistantStatus.style.color = model.configured ? '#397a4f' : '#8b743b';
    assistantStatus.style.background = model.configured ? '#edf6ef' : '#fff6df';
  } catch (error) {
    $('stat-ai').textContent = 'Недоступна';
    $('stat-ai-detail').textContent = error.message;
  }
}

function addDocRow(doc) {
  const row = node('article', 'document-row');
  row.append(node('span', 'doc-icon', 'PDF'));
  const info = node('div');
  info.append(node('strong', '', doc.title));
  let details = `${doc.filename} · ${doc.page_count || '—'} стр.`;
  const scope = doc.scope || {};
  if (scope.guide_start_page && scope.guide_end_page) details += ` · руководство ${scope.guide_start_page}–${scope.guide_end_page}`;
  if (scope.catalog_start_page && scope.catalog_end_page) details += ` · каталог ${scope.catalog_start_page}–${scope.catalog_end_page}`;
  info.append(node('small', '', details));
  row.append(info);
  const actions = node('div', 'doc-actions');
  const open = node('a', '', 'Открыть PDF');
  open.href = doc.url;
  open.target = '_blank';
  open.rel = 'noopener';
  actions.append(open);
  const remove = node('button', '', 'Удалить');
  remove.type = 'button';
  remove.addEventListener('click', async () => {
    if (!window.confirm(`Удалить документ «${doc.title}» и его локальную копию?`)) return;
    try { await request(`/api/admin/documents/${doc.id}`, {method: 'DELETE'}); toast('Документ удалён'); await loadDocuments(); await loadParts(); await loadStats(); }
    catch (error) { toast(error.message, true); }
  });
  actions.append(remove);
  row.append(actions);
  return row;
}

async function loadDocuments() {
  const list = $('document-list');
  if (!list) return;
  const machine = selectedMachine();
  if (!machine) { list.replaceChildren(node('div', 'empty-box', 'Сначала создайте карточку техники.')); return; }
  try {
    const docs = await request(`/api/admin/documents?machine_id=${machine.id}`);
    list.replaceChildren();
    if (!docs.length) list.append(node('div', 'empty-box', 'Для этой техники ещё не загружено руководство.'));
    docs.forEach(doc => list.append(addDocRow(doc)));
  } catch (error) { list.replaceChildren(node('div', 'empty-box', `Не удалось получить документы: ${error.message}`)); }
}

async function loadImages() {
  const list = $('image-list');
  if (!list) return;
  const machine = selectedMachine();
  if (!machine) { list.replaceChildren(node('div', 'empty-box', 'Сначала создайте карточку техники.')); return; }
  try {
    const images = await request(`/api/admin/media?machine_id=${machine.id}`);
    list.replaceChildren();
    if (!images.length) list.append(node('div', 'empty-box', 'Изображений пока нет.'));
    images.forEach(image => {
      const row = node('article', 'image-row');
      const preview = node('img'); preview.src = image.url; preview.alt = image.caption || 'Фото техники';
      const info = node('div'); info.append(node('strong', '', image.caption || 'Без подписи')); info.append(node('small', '', image.filename));
      row.append(preview, info);
      const open = node('a'); open.href = image.url; open.target = '_blank'; open.rel = 'noopener'; open.textContent = 'Открыть';
      row.append(open); list.append(row);
    });
  } catch (error) { list.replaceChildren(node('div', 'empty-box', `Не удалось получить изображения: ${error.message}`)); }
}

function statusLabel(status) {
  if (status === 'explicit_or_component_named_in_manual') return ['Функция подтверждена текстом', 'direct'];
  if (status === 'assembly_role_only') return ['Описана только функция узла', 'assembly'];
  return ['Индивидуальная функция не указана', 'unknown'];
}

function partCard(part, document) {
  const card = node('article', 'part-card');
  const identity = node('div', 'part-ident');
  identity.append(node('strong', '', part.name));
  identity.append(node('small', '', `${part.designation || 'Обозначение в таблице не заполнено'} · позиция ${part.position} · количество ${part.quantity} шт.`));
  identity.append(node('small', '', `Рисунок ${part.figure_refs.join(', ')} · каталог PDF, стр. ${part.catalog_page}${part.note ? ` · ${part.note}` : ''}`));
  card.append(identity);

  const assembly = node('div', 'part-assembly');
  assembly.append(node('strong', '', part.assembly));
  assembly.append(node('small', '', `Сборочная единица ${part.assembly_code}`));
  const [label, css] = statusLabel(part.individual_role_status);
  assembly.append(node('span', `part-status ${css}`, label));
  card.append(assembly);

  const explanation = node('div', 'part-function');
  explanation.textContent = part.individual_role || 'В руководстве индивидуальная функция не установлена.';
  if (part.individual_role_caveat) explanation.append(node('div', 'small-note', part.individual_role_caveat));
  const source = part.individual_role_source;
  if (source?.quote) {
    const quote = node('blockquote', '', `«${source.quote}»`);
    explanation.append(quote);
  }
  const details = node('div', 'part-details');
  details.append(explanation);

  const assessment = part.expert_assessment;
  if (assessment?.hypothesis) {
    const expert = node('div', 'part-expert');
    expert.append(node('strong', '', `Экспертная гипотеза · уверенность: ${{high: 'высокая', medium: 'средняя', low: 'низкая'}[assessment.confidence] || 'не оценена'}`));
    expert.append(node('p', '', assessment.hypothesis));
    expert.append(node('small', '', 'Инженерный вывод по названию и взрыв-схеме; не подтверждён изготовителем и не является фактом из руководства.'));
    details.append(expert);
  }
  card.append(details);

  const links = node('div', 'part-source');
  const catalog = node('a', '', `Каталог, стр. ${part.catalog_page} →`);
  catalog.href = `/api/admin/documents/${document.document_id}/file#page=${part.catalog_page}`;
  catalog.target = '_blank'; catalog.rel = 'noopener'; links.append(catalog);
  if (source?.page) {
    const manual = node('a', '', `Функция: руководство, стр. ${source.page} →`);
    manual.href = `/api/admin/documents/${document.document_id}/file#page=${source.page}`;
    manual.target = '_blank'; manual.rel = 'noopener'; links.append(manual);
  }
  for (const page of assessment?.drawing_pages || []) {
    const drawing = node('a', '', `Схема (основание гипотезы), стр. ${page} →`);
    drawing.href = `/api/admin/documents/${document.document_id}/file#page=${page}`;
    drawing.target = '_blank'; drawing.rel = 'noopener'; links.append(drawing);
  }
  card.append(links);
  return card;
}

async function loadParts() {
  const list = $('part-list');
  if (!list) return;
  const machine = selectedMachine();
  if (!machine) { list.replaceChildren(node('div', 'empty-box', 'Сначала создайте карточку техники.')); return; }
  try {
    const q = $('parts-search')?.value.trim() || '';
    const data = await request(`/api/admin/parts?machine_id=${machine.id}&q=${encodeURIComponent(q)}&limit=250`);
    list.replaceChildren();
    const docs = data.documents || [];
    const indexedDocs = docs.filter(doc => doc.extraction?.positions_total);
    if (!indexedDocs.length) {
      $('parts-heading').textContent = 'Каталожный индекс отсутствует';
      $('parts-summary').textContent = 'Для точного PDF Kolibri ГРН-471 v4 позиции будут разобраны локально; другие версии не подменяются похожим каталогом.';
      $('parts-coverage').replaceChildren();
      list.append(node('div', 'empty-box', 'Загрузите руководство Kolibri ГРН-471 v4, чтобы создать проверяемую таблицу запчастей.'));
      $('parts-gaps').replaceChildren();
      return;
    }
    const total = indexedDocs.reduce((sum, doc) => sum + (doc.extraction?.positions_total || doc.parts.length), 0);
    const visible = indexedDocs.reduce((sum, doc) => sum + doc.parts.length, 0);
    $('parts-heading').textContent = q ? `${visible} найдено · ${total} позиций в базе` : `${total} каталожных позиций`;
    $('parts-summary').textContent = 'Локальная экспертная база, не переобученная LLM и не утверждённый эталон. Дословные данные руководства показаны отдельно от инженерных гипотез по каталоговым названиям и взрыв-схемам; гипотезы не подтверждены изготовителем.';
    const coverage = $('parts-coverage'); coverage.replaceChildren();
    for (const [label, text] of [
      ['Фигуры', '1–9'], ['Позиции', String(total)],
      ['Функция подтверждена', String(indexedDocs.reduce((sum, doc) => sum + (doc.extraction?.role_status_counts?.explicit_or_component_named_in_manual || 0), 0))],
      ['Только функция узла', String(indexedDocs.reduce((sum, doc) => sum + (doc.extraction?.role_status_counts?.assembly_role_only || 0), 0))],
      ['Без индивидуальной функции', String(indexedDocs.reduce((sum, doc) => sum + (doc.extraction?.role_status_counts?.catalog_membership_only || 0), 0))],
      ['Экспертных гипотез', String(indexedDocs.reduce((sum, doc) => sum + doc.parts.filter(part => part.expert_assessment?.status === 'engineering_hypothesis').length, 0))],
    ]) coverage.append(node('span', 'coverage-chip', `${label}: ${text}`));

    for (const doc of indexedDocs) {
      for (const part of doc.parts) list.append(partCard(part, doc));
    }
    if (!visible) list.append(node('div', 'empty-box', 'По этому запросу каталожных позиций не найдено.'));
    const gaps = $('parts-gaps'); gaps.replaceChildren();
    for (const doc of indexedDocs) {
      for (const gap of doc.known_catalog_gaps || []) {
        const item = node('div', 'part-gap');
        item.textContent = `${gap.component}: ${gap.instruction} Статус: ${gap.status}.`;
        gaps.append(item);
      }
    }
  } catch (error) {
    list.replaceChildren(node('div', 'empty-box', `Не удалось загрузить структуру запчастей: ${error.message}`));
  }
}

$('machine-form').addEventListener('submit', async event => {
  event.preventDefault();
  const status = $('machine-status'); status.textContent = 'Создаём карточку…';
  const body = Object.fromEntries(new FormData(event.currentTarget).entries());
  try {
    const machine = await request('/api/admin/machines', {method: 'POST', body: JSON.stringify(body)});
    state.selectedMachine = machine.id;
    localStorage.setItem('kolibri-admin-machine', String(machine.id));
    toast(`Добавлена техника ${machine.model}`);
    await loadMachines();
  } catch (error) { status.textContent = error.message; toast(error.message, true); }
});

$('pdf-form').addEventListener('submit', async event => {
  event.preventDefault();
  const machine = selectedMachine();
  if (!machine) { toast('Сначала создайте карточку техники', true); return; }
  const form = event.currentTarget;
  const status = $('pdf-status');
  const data = new FormData(form);
  data.set('machine_id', String(machine.id));
  status.textContent = 'Сохраняем файл и разбираем страницы PDF…';
  const button = form.querySelector('button[type=submit]'); button.disabled = true;
  try {
    const result = await request('/api/admin/documents', {method: 'POST', body: data});
    status.textContent = `Готово: ${result.page_count} страниц, ${result.pages_with_text} страниц с текстом${result.parts_indexed ? `, ${result.parts_indexed} позиций каталога связаны с узлами` : ''}.`;
    toast(`Руководство проиндексировано: ${result.page_count} стр.`);
    form.reset();
    await loadDocuments(); await loadParts(); await loadStats();
  } catch (error) { status.textContent = error.message; toast(error.message, true); }
  finally { button.disabled = false; }
});

$('image-form').addEventListener('submit', async event => {
  event.preventDefault();
  const machine = selectedMachine();
  if (!machine) { toast('Сначала создайте карточку техники', true); return; }
  const form = event.currentTarget, status = $('image-status'), button = form.querySelector('button[type=submit]');
  const data = new FormData(form); data.set('machine_id', String(machine.id));
  status.textContent = 'Проверяем и сохраняем изображение…'; button.disabled = true;
  try {
    await request('/api/admin/media', {method: 'POST', body: data});
    status.textContent = 'Изображение сохранено в локальной базе.'; toast('Фото добавлено'); form.reset();
    await loadImages(); await loadStats();
  } catch (error) { status.textContent = error.message; toast(error.message, true); }
  finally { button.disabled = false; }
});

function addCitation(citation) {
  const card = node('article', 'citation-card');
  card.append(node('strong', '', `${citation.document_title} · PDF, стр. ${citation.page}`));
  card.append(node('p', '', `«${citation.quote}»`));
  const link = node('a');
  link.href = citation.viewer_url || `${citation.url || `/api/admin/documents/${citation.document_id}/file`}#page=${citation.page}`;
  link.target = '_blank'; link.rel = 'noopener'; link.textContent = 'Открыть страницу источника →';
  card.append(link); return card;
}

function addEvidence(hit) {
  const card = node('article', 'evidence-card');
  const sectionLabel = hit.section === 'parts_catalog' ? 'КАТАЛОГ ДЕТАЛЕЙ' : (hit.section === 'manual' ? 'РУКОВОДСТВО' : 'ДОКУМЕНТ');
  card.append(node('strong', '', `${sectionLabel} · ${hit.document_title} · стр. ${hit.page}`));
  card.append(node('p', '', hit.text));
  const link = node('a'); link.href = `/api/admin/documents/${hit.document_id}/file#page=${hit.page}`;
  link.target = '_blank'; link.rel = 'noopener'; link.textContent = 'Открыть PDF →'; card.append(link);
  return card;
}

$('question-form').addEventListener('submit', async event => {
  event.preventDefault();
  const machine = selectedMachine();
  if (!machine) { toast('Сначала создайте карточку техники', true); return; }
  const question = $('question-input').value.trim();
  const status = $('question-status'), button = event.currentTarget.querySelector('button[type=submit]');
  if (!question) return;
  $('answer-card').hidden = true; status.textContent = 'Ищем релевантные страницы и проверяем ответ…'; button.disabled = true;
  try {
    const result = await request('/api/admin/assistant/answer', {method: 'POST', body: JSON.stringify({machine_id: machine.id, question})});
    $('answer-card').hidden = false;
    $('answer-heading').textContent = result.status === 'answered' ? 'Ответ с источниками' : (result.status === 'part_lookup' ? 'Ответ из локального справочника' : 'Проверка не пройдена');
    $('answer-badge').textContent = statusText[result.status] || result.status;
    $('answer-text').textContent = result.answer || 'Ответ отсутствует';
    const citations = $('answer-citations'); citations.replaceChildren();
    (result.citations || []).forEach(item => citations.append(addCitation(item)));
    const evidence = $('answer-evidence'); evidence.replaceChildren();
    (result.evidence || []).forEach(item => evidence.append(addEvidence(item)));
    status.textContent = result.status === 'answered' ? 'Ответ прошёл проверки.' : (result.status === 'part_lookup' ? 'Справочник не использовал генеративную модель; ссылки ведут к каталогу и руководству.' : 'Ни один неподтверждённый ответ не показан.');
  } catch (error) { status.textContent = error.message; toast(error.message, true); }
  finally { button.disabled = false; }
});

document.querySelectorAll('[data-question]').forEach(button => button.addEventListener('click', () => {
  $('question-input').value = button.dataset.question;
  $('question-input').focus();
}));
$('refresh-docs').addEventListener('click', () => { loadDocuments(); loadImages(); loadParts(); loadStats(); });
$('refresh-parts').addEventListener('click', loadParts);
$('parts-search').addEventListener('input', () => {
  clearTimeout(state.partsSearchTimer);
  state.partsSearchTimer = setTimeout(loadParts, 180);
});

loadMachines();
