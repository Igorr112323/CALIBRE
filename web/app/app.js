import catalog from './catalog.json';

import machinePhoto from './kolibri-grn471.jpg';

const ui = {
  screen: 'home',
  history: [],
  machineId: null,
  nodeId: null,
  problemId: null,
  tab: 'adjustments',
  searchQuery: '',
};

const $ = (selector, root = document) => root.querySelector(selector);
let appReadyForIntro = false;
let introAnimationFinished = false;
function dismissIntroWhenReady() {
  if (!appReadyForIntro || !introAnimationFinished) return;
  $('#launchSplash')?.classList.add('dismiss');
}
function finishIntroAnimation() {
  if (introAnimationFinished) return;
  introAnimationFinished = true;
  if (launchAnimation && !launchAnimation.ended) launchAnimation.pause();
  dismissIntroWhenReady();
}
const launchAnimation = $('#launchAnimation');
if (launchAnimation) {
  launchAnimation.addEventListener('ended', finishIntroAnimation, { once: true });
  launchAnimation.addEventListener('error', finishIntroAnimation, { once: true });
  window.setTimeout(finishIntroAnimation, 5500);
} else {
  introAnimationFinished = true;
}
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[char]));
const asArray = value => Array.isArray(value) ? value : [];

function machineList() {
  return catalog.machines;
}
function findMachine(id = ui.machineId) {
  return machineList().find(machine => String(machine.id) === String(id)) ?? null;
}
function findNode(machine, id = ui.nodeId) {
  return asArray(machine?.nodes).find(node => String(node.id) === String(id)) ?? null;
}
function findProblem(machine, id = ui.problemId) {
  return asArray(machine?.problems).find(problem => String(problem.id) === String(id)) ?? null;
}
function titleForScreen() {
  const machine = findMachine();
  if (ui.screen === 'machine') return [machine?.model || 'Машина', machine?.machine_type || 'Карточка машины'];
  if (ui.screen === 'node') return ['Карточка узла', machine?.model || 'Машина'];
  if (ui.screen === 'problem') return ['Что-то не так', machine?.model || 'Машина'];
  return ['Kolibri', 'Сельхозтехника'];
}

function go(screen, values = {}) {
  ui.history.push({ screen: ui.screen, machineId: ui.machineId, nodeId: ui.nodeId, problemId: ui.problemId, tab: ui.tab });
  Object.assign(ui, values, { screen });
  ui.searchQuery = '';
  render();
}
function goBack() {
  const previous = ui.history.pop();
  if (!previous) { ui.screen = 'home'; render(); return; }
  Object.assign(ui, previous);
  ui.searchQuery = '';
  render();
}
function setScreen(screen) {
  ui.screen = screen;
  ui.history = [];
  ui.searchQuery = '';
  render();
}

function render() {
  const [title, subtitle] = titleForScreen();
  const titleElement = $('#title');
  titleElement.innerHTML = ui.screen === 'home'
    ? '<span class="logo">Kolibri</span><small>Справочник сельхозтехники</small>'
    : `${escapeHtml(title)}<small>${escapeHtml(subtitle)}</small>`;
  $('#back').classList.toggle('show', ['machine', 'node', 'problem'].includes(ui.screen));
  const main = $('#main');
  if (ui.screen === 'home') main.innerHTML = renderHome();
  else if (ui.screen === 'machine') main.innerHTML = renderMachine();
  else if (ui.screen === 'node') main.innerHTML = renderNodeScreen();
  else if (ui.screen === 'problem') main.innerHTML = renderProblemScreen();
  main.scrollTop = 0;
}

function searchIndex() {
  const items = [];
  for (const machine of machineList()) {
    const add = (type, kind, tab, id, title, text, snippet = text) => items.push({ type, kind, tab, id, machineId: machine.id, title, text, snippet });
    for (const node of machine.nodes) add('Узел', 'node', 'nodes', node.id, node.title, node.purpose);
    for (const adjustment of machine.adjustments) add('Регулировка', 'section', 'adjustments', adjustment.id, adjustment.title, [adjustment.value, adjustment.tool, ...adjustment.steps].join(' '), adjustment.value);
    for (const problem of machine.problems) add('Неисправность', 'problem', 'problems', problem.id, problem.title, [problem.symptom, problem.cause, ...problem.steps].join(' '), problem.cause);
    add('ТО и смазка', 'section', 'maintenance', 'maintenance-steps', machine.maintenance.interval, machine.maintenance.steps.join(' '));
    for (const lubricant of machine.maintenance.lubricants) add('Смазка', 'section', 'maintenance', 'lubrication', lubricant, lubricant);
    for (const point of machine.maintenance.points) add('Точка смазки', 'section', 'maintenance', 'lubrication', point.label, point.value);
    add('Смазка', 'section', 'maintenance', 'lubrication', 'Схема смазки', machine.maintenance.warning);
    for (const [key, title] of [['requirements', 'Требования к хранению'], ['preparation', 'Постановка на хранение'], ['inspection', 'Осмотр при хранении'], ['return', 'Снятие с хранения']]) {
      add('Хранение', 'section', 'storage', `storage-${key}`, title, machine.storage[key].join(' '));
    }
    add('Хранение', 'section', 'storage', 'storage-requirements', 'Сроки хранения', machine.storage.warning);
    for (const fact of machine.characteristics) add('ТТХ', 'section', 'specs', 'characteristics', fact.label, fact.value);
    add('Агрегатирование', 'section', 'specs', 'preparation', 'Подготовка трактора', machine.aggregation.preparation.join(' '));
    add('Обкатка', 'section', 'specs', 'break-in', 'Обкатка', machine.aggregation.break_in.join(' '));
    for (const mode of machine.aggregation.field_modes) add('Работа в поле', 'section', 'specs', 'field-modes', mode.title, `${mode.route} ${mode.text}`);
    add('Безопасность', 'section', 'problems', 'safety', 'Правила безопасности', machine.safety.join(' '));
  }
  return items;
}
function normalize(text) { return String(text ?? '').toLocaleLowerCase('ru').replaceAll('ё', 'е').trim(); }
function renderSearchResults(query, containerId) {
  const container = $(`#${containerId}`);
  if (!container) return;
  const q = normalize(query);
  if (q.length < 2) { container.innerHTML = ''; return; }
  const words = q.split(/\s+/).filter(word => word.length > 1);
  const results = searchIndex().map(item => {
    const text = normalize(`${item.title} ${item.text}`);
    let score = 0;
    for (const word of words) {
      const stem = word.length > 5 ? word.slice(0, -2) : word;
      if (normalize(item.title).includes(stem)) score += 4;
      else if (text.includes(stem)) score += 1;
    }
    return { ...item, score };
  }).filter(item => item.score > 0).sort((a, b) => b.score - a.score).slice(0, 5);
  if (!results.length) {
    container.innerHTML = `<div class="empty">В базе этого нет. Приложение не будет придумывать ответ.</div>`;
    return;
  }
  container.innerHTML = results.map(item => `<article class="search-result" data-action="open-search-result" data-kind="${escapeHtml(item.kind)}" data-id="${escapeHtml(item.id)}" data-tab="${escapeHtml(item.tab)}" data-machine-id="${escapeHtml(item.machineId)}"><div class="type">${escapeHtml(item.type)}</div><h4>${escapeHtml(item.title)}</h4><p>${escapeHtml(item.snippet)}</p></article>`).join('');
}

function machineSearchField() {
  return `<section class="search"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.8" cy="10.8" r="6.6"/><path d="m16 16 5 5"/></svg><input id="homeSearch" value="${escapeHtml(ui.searchQuery)}" placeholder="Поиск по каталогу" autocomplete="off" aria-label="Поиск по каталогу"></section><div id="homeResults"></div>`;
}

function tractorIllustration() {
  return `<svg class="tractor-art" viewBox="0 0 220 150" aria-hidden="true"><ellipse cx="128" cy="137" rx="91" ry="7" fill="#bdc9b8" opacity=".7"/><path d="M91 21q1-7 9-7h31q8 0 10 8l8 43h53q9 0 9 9v17q0 9-9 9h-70l-9-30H91z" fill="#3d7435"/><path d="M101 27h25l6 29H96z" fill="#f2f4eb"/><path d="M151 66h48q9 0 9 9v7h-57z" fill="#3d7435"/><rect x="177" y="39" width="7" height="28" rx="3.5" fill="#18251b"/><circle cx="94" cy="102" r="34" fill="#172019"/><circle cx="94" cy="102" r="15" fill="#f4f6ed"/><circle cx="94" cy="102" r="6" fill="#294b2b"/><circle cx="199" cy="111" r="22" fill="#172019"/><circle cx="199" cy="111" r="9" fill="#f4f6ed"/></svg>`;
}

function renderMachineCard(machine, index = 0) {
  return `<article class="machine-card card-tone-${index % 2}" data-action="open-machine" data-id="${escapeHtml(machine.id)}"><h3>${escapeHtml(machine.model || 'Машина')}</h3><p>${escapeHtml(machine.machine_type || 'Сельхозтехника')}</p><span class="machine-open" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M5 12h14M12 5l7 7-7 7"/></svg></span>${tractorIllustration()}</article>`;
}

function renderMachineList() {
  const machines = machineList();
  return machines.map((machine, index) => renderMachineCard(machine, index)).join('') || '<div class="catalog-empty">Нет добавленных машин.</div>';
}

function renderHome() {
  return `${machineSearchField()}<h2 class="catalog-heading">Техника</h2><div class="machine-list">${renderMachineList()}</div>`;
}

const tabs = [
  ['adjustments', 'Регулировки'],
  ['maintenance', 'ТО и смазка'],
  ['nodes', 'Узлы'],
  ['storage', 'Хранение'],
  ['specs', 'ТТХ и агрегатирование'],
  ['problems', 'Неисправности и ТБ'],
];

function renderSteps(steps) {
  return steps.map((step, index) => `<article class="card step"><div class="step-n">${index + 1}</div><p>${escapeHtml(step)}</p></article>`).join('');
}
function renderRows(rows) {
  return rows.map(row => `<div class="settings-row"><span>${escapeHtml(row.label)}</span><strong>${escapeHtml(row.value)}</strong></div>`).join('');
}
function renderAdjustments(adjustments) {
  return adjustments.map(item => `<article class="adjustment" data-section="${escapeHtml(item.id)}"><h4>${escapeHtml(item.title)}</h4><div class="value">${escapeHtml(item.value)}</div><ol>${item.steps.map(step => `<li>${escapeHtml(step)}</li>`).join('')}</ol><div class="tool">🔧 ${escapeHtml(item.tool)}</div></article>`).join('') || '<div class="catalog-empty">Регулировки для этого узла не указаны.</div>';
}
function renderMachineTab(machine) {
  if (ui.tab === 'adjustments') return `<div class="section">Регулировки машины</div><div class="warnbox">Перед регулировкой заглушите двигатель, отключите ВОМ и дождитесь полной остановки вращающихся частей.</div>${renderAdjustments(machine.adjustments)}`;
  if (ui.tab === 'maintenance') {
    const data = machine.maintenance;
    return `<div class="section" data-section="maintenance-steps">${escapeHtml(data.interval)}</div>${renderSteps(data.steps)}<div class="section" data-section="lubrication">Смазочные материалы и точки смазки</div><article class="card">${data.lubricants.map(text => `<p class="what">${escapeHtml(text)}</p>`).join('')}${renderRows(data.points)}</article><div class="warnbox">${escapeHtml(data.warning)}</div>`;
  }
  if (ui.tab === 'nodes') return machine.nodes.map(node => `<article class="list-item" data-action="open-node" data-id="${escapeHtml(node.id)}"><div class="num">${escapeHtml(node.number)}</div><div><h4>${escapeHtml(node.title)}</h4><p>${escapeHtml(node.purpose)}</p></div><span class="arrow">›</span></article>`).join('');
  if (ui.tab === 'storage') {
    const data = machine.storage;
    return `<div class="section" data-section="storage-requirements">Площадка и сроки</div><article class="card">${data.requirements.map(text => `<p class="what">${escapeHtml(text)}</p>`).join('')}</article><div class="warnbox">${escapeHtml(data.warning)}</div><div class="section" data-section="storage-preparation">Постановка на хранение</div>${renderSteps(data.preparation)}<div class="section" data-section="storage-inspection">Осмотры при хранении</div>${renderSteps(data.inspection)}<div class="section" data-section="storage-return">Снятие с хранения</div>${renderSteps(data.return)}`;
  }
  if (ui.tab === 'specs') {
    const data = machine.aggregation;
    return `<div class="section" data-section="characteristics">Технические характеристики</div><article class="card">${renderRows(machine.characteristics)}</article><div class="section" data-section="preparation">Подготовка трактора и агрегатирование</div>${renderSteps(data.preparation)}<div class="section" data-section="break-in">Обкатка</div>${renderSteps(data.break_in)}<div class="section" data-section="field-modes">Схемы работы в поле</div>${data.field_modes.map(mode => `<article class="card"><h4>${escapeHtml(mode.title)}</h4><div class="value">${escapeHtml(mode.route)}</div><p class="what">${escapeHtml(mode.text)}</p></article>`).join('')}`;
  }
  return `<div class="section">Неисправности</div>${machine.problems.map(problem => `<article class="list-item" data-action="open-problem" data-id="${escapeHtml(problem.id)}"><div class="num warn">!</div><div><h4>${escapeHtml(problem.title)}</h4><p>${escapeHtml(problem.cause)}</p></div><span class="arrow">›</span></article>`).join('')}<div class="section" data-section="safety">Техника безопасности</div><div class="errbox"><b>Перед обслуживанием остановите машину и заглушите двигатель.</b></div><div class="warnbox"><ul>${machine.safety.map(rule => `<li>${escapeHtml(rule)}</li>`).join('')}</ul></div>`;
}
function renderMachine() {
  const machine = findMachine();
  if (!machine) return '<div class="catalog-empty">Машина не найдена.</div>';
  const photo = `<button class="scheme machine-photo" data-action="open-photo" aria-label="Открыть фотографию крупно"><img src="${escapeHtml(machinePhoto)}" alt="Роторные грабли ГРН-471 Kolibri"></button>`;
  return `<div class="machine-line"><h2 class="screen-title">${escapeHtml(machine.model)}</h2></div><p class="hint">${escapeHtml(machine.subtitle)}</p>${photo}<div class="machine-overview"><div class="overview-tags">${machine.quick_facts.map(fact => `<span>${escapeHtml(fact)}</span>`).join('')}</div></div><div class="tabs" role="tablist" aria-label="Разделы карточки">${tabs.map(([id, title]) => `<button data-action="tab" data-tab="${id}" role="tab" aria-selected="${ui.tab === id}" class="${ui.tab === id ? 'on' : ''}">${escapeHtml(title)}</button>`).join('')}</div>${renderMachineTab(machine)}`;
}
function renderNodeScreen() {
  const machine = findMachine();
  const node = findNode(machine);
  if (!node) return '<div class="catalog-empty">Карточка узла не найдена.</div>';
  return `<div class="node-head"><div class="num">${escapeHtml(node.number)}</div><h2>${escapeHtml(node.title)}</h2></div><article class="card"><div class="section" style="margin-top:0">Описание</div><p class="what">${escapeHtml(node.purpose)}</p></article><div class="section">Связанные регулировки</div>${renderAdjustments(machine.adjustments.filter(item => item.node_ids.includes(node.id)))}`;
}
function renderProblemScreen() {
  const machine = findMachine();
  const problem = findProblem(machine);
  if (!problem) return '<div class="catalog-empty">Неисправность не найдена.</div>';
  const node = findNode(machine, problem.node_id);
  return `<article class="card"><h2 class="screen-title">${escapeHtml(problem.title)}</h2><p class="what">${escapeHtml(problem.symptom)}</p><p class="what"><b>Возможная причина:</b> ${escapeHtml(problem.cause)}</p></article><div class="errbox">Перед проверкой остановите машину, отключите ВОМ и заглушите двигатель.</div>${renderSteps(problem.steps)}${node ? `<article class="list-item" data-action="open-node" data-id="${escapeHtml(node.id)}"><div class="num">${escapeHtml(node.number)}</div><div><h4>${escapeHtml(node.title)}</h4><p>Связанный узел</p></div><span class="arrow">›</span></article>` : ''}`;
}
function closeModal() {
  const modal = $('#modal');
  modal.classList.remove('show');
  modal.setAttribute('aria-hidden', 'true');
  $('#sheet').innerHTML = '';
  $('[data-action="open-photo"]')?.focus();
}
function openPhoto() {
  const modal = $('#modal');
  $('#sheet').innerHTML = `<h3>ГРН-471 Kolibri</h3><img src="${escapeHtml(machinePhoto)}" alt="Роторные грабли ГРН-471 Kolibri"><button class="btn ghost" data-action="close-modal">Закрыть</button>`;
  modal.classList.add('show');
  modal.setAttribute('aria-hidden', 'false');
  $('[data-action="close-modal"]').focus();
}
function handleAction(action, element) {
  if (action === 'back') goBack();
  else if (action === 'home') setScreen('home');
  else if (action === 'open-photo') openPhoto();
  else if (action === 'close-modal') closeModal();
  else if (action === 'open-machine') go('machine', { machineId: element.dataset.id, tab: 'adjustments' });
  else if (action === 'open-node') go('node', { nodeId: element.dataset.id });
  else if (action === 'open-problem') go('problem', { problemId: element.dataset.id });
  else if (action === 'open-search-result') {
    const values = { machineId: element.dataset.machineId, tab: element.dataset.tab };
    if (element.dataset.kind === 'node') go('node', { ...values, nodeId: element.dataset.id });
    else if (element.dataset.kind === 'problem') go('problem', { ...values, problemId: element.dataset.id });
    else {
      go('machine', values);
      const target = [...document.querySelectorAll('[data-section]')].find(item => item.dataset.section === element.dataset.id);
      target?.scrollIntoView?.({ block: 'start' });
    }
  } else if (action === 'tab') {
    const scrollLeft = element.parentElement.scrollLeft;
    ui.tab = element.dataset.tab;
    render();
    const tabList = $('.tabs');
    tabList.scrollLeft = scrollLeft;
    const selected = $('.tabs .on');
    const bounds = tabList.getBoundingClientRect();
    const selectedBounds = selected.getBoundingClientRect();
    if (selectedBounds.left < bounds.left) tabList.scrollLeft -= bounds.left - selectedBounds.left;
    else if (selectedBounds.right > bounds.right) tabList.scrollLeft += selectedBounds.right - bounds.right;
  }
}

$('#app').addEventListener('click', event => {
  if (event.target.id === 'modal') { closeModal(); return; }
  const actionElement = event.target.closest('[data-action]');
  if (!actionElement) return;
  handleAction(actionElement.dataset.action, actionElement);
});
$('#app').addEventListener('input', event => {
  if (event.target.id === 'homeSearch') {
    ui.searchQuery = event.target.value;
    renderSearchResults(event.target.value, 'homeResults');
  }
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && $('#modal').classList.contains('show')) closeModal();
});

render();
appReadyForIntro = true;
dismissIntroWhenReady();
