import { Capacitor } from '@capacitor/core';
import demoData from './grn471-demo.json';

const isNative = Capacitor.isNativePlatform();
const API_PREFIX = '/api/mobile';
const BROWSER_ORIGIN = window.location.protocol.startsWith('http') ? window.location.origin : '';
function buildEmbeddedDemo() {
  const machine = {
    ...demoData.machine,
    id: 'demo-machine:GRN-471-KOLIBRI-V4',
    code: demoData.machine.code,
    version: 1,
    is_demo: true,
    label: 'Демо-срез: автопроверено, человеком не сверено; не использовать вместо руководства',
    characteristics: [
      ...demoData.general_facts.map(item => ({ ...item, label: `Из руководства: ${item.label}`, value: item.value, source_page: item.page, source_quote: item.quote })),
      ...demoData.maintenance.map(item => ({ ...item, label: `Обслуживание: ${item.label}`, value: item.value, source_page: item.page, source_quote: item.quote })),
      ...demoData.safety_warnings.map(item => ({ ...item, label: `Ограничение безопасности: ${item.label}`, value: item.value, source_page: item.page, source_quote: item.quote })),
      ...demoData.document_gaps.map(item => ({ ...item, label: `Не указано в документе: ${item.label}`, value: item.value, source_page: item.page, source_quote: item.quote })),
      ...demoData.norms.map(item => ({
        label: item.name,
        value: [item.value, item.unit, item.condition].filter(Boolean).join(' '),
        source_page: item.page,
        source_quote: item.quote,
        auto_verified: true,
        human_verified: false,
        verification_label: 'Автопроверено по цитате и числу; человеком не сверено',
      })),
    ],
    nodes: demoData.nodes.map(item => ({
      ...item,
      id: `demo-node:${item.key}`,
      source_page: item.page,
      source_quote: item.quote,
      adjustments: [],
      mistakes: [],
      related_node_ids: [],
    })),
    problems: [],
  };
  const nodeByKey = new Map(machine.nodes.map(node => [node.key, node]));
  demoData.adjustments.forEach((item, index) => {
    const node = nodeByKey.get(item.node_key);
    if (!node) return;
    const instructions = [...item.instructions];
    if (item.pages?.length) instructions.push(`Источник: руководство по эксплуатации, стр. ${item.pages.join(', ')}.`);
    if (item.safety_pages?.length) instructions.push(`Ограничения безопасности: стр. ${item.safety_pages.join(', ')}.`);
    node.adjustments.push({ id: `demo-adjustment:${index + 1}`, title: item.title, instructions, tool: '', norms: [] });
  });
  const normsByNode = new Map();
  demoData.norms.forEach(item => {
    const node = nodeByKey.get(item.node_key);
    if (!node) return;
    const fact = {
      id: `demo-norm:${item.id}`,
      name: item.name,
      condition: item.condition,
      value: item.value,
      unit: item.unit,
      source: { kind: 'packaged_demo', document: demoData.source.title, page_start: item.page, quote: item.quote },
      auto_verified: true,
      human_verified: false,
      verification_label: 'Автопроверено по цитате и числу; человеком не сверено',
    };
    if (!normsByNode.has(node.key)) normsByNode.set(node.key, []);
    normsByNode.get(node.key).push(fact);
  });
  for (const [key, norms] of normsByNode) {
    nodeByKey.get(key).adjustments.push({
      id: `demo-norms:${key}`,
      title: 'Нормы и числовые значения из руководства',
      instructions: [], tool: '', norms,
    });
  }
  machine.problems = demoData.problems.map(item => ({
    id: `demo-problem:${item.key}`,
    title: item.title,
    symptom: `Проявление: ${item.symptom}\nВероятная причина: ${item.cause}`,
    source_page: item.page,
    source_quote: item.quotes.map(quote => `«${quote}»`).join('\n'),
    steps: [{
      number: 1,
      instruction: item.remedy,
      source_page: item.page,
      source_quote: item.quotes[2],
      linked_node_id: nodeByKey.get(item.linked_node_key)?.id ?? null,
    }],
  }));
  return machine;
}

const EMBEDDED_DEMO = buildEmbeddedDemo();
const DEFAULT_CATALOG = { schema_version: 1, real_machines: [], demo_machines: [EMBEDDED_DEMO] };
const DEFAULT_STATE = {
  serverUrl: isNative ? '' : BROWSER_ORIGIN,
  catalog: structuredClone(DEFAULT_CATALOG),
  revision: null,
  lastSync: null,
  feedbackQueue: [],
  questionQueue: [],
  mediaCache: {},
};
const appState = structuredClone(DEFAULT_STATE);
const ui = {
  screen: 'home',
  history: [],
  machineId: null,
  nodeId: null,
  problemId: null,
  tab: 'scheme',
  message: '',
  messageKind: '',
  syncBusy: false,
  searchQuery: '',
};
let storage;
let storageLabel = 'выбирается';
let networkListener;

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
const newEventId = () => globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
const asArray = value => Array.isArray(value) ? value : [];

function openIndexedDb() {
  return new Promise((resolve, reject) => {
    if (!('indexedDB' in globalThis)) return reject(new Error('IndexedDB недоступна'));
    const request = indexedDB.open('calibre-mobile', 1);
    request.onupgradeneeded = () => request.result.createObjectStore('state', { keyPath: 'key' });
    request.onerror = () => reject(request.error ?? new Error('Не удалось открыть IndexedDB'));
    request.onsuccess = () => resolve(request.result);
  });
}

function indexedDbStorage(db) {
  return {
    async load() {
      return new Promise((resolve, reject) => {
        const request = db.transaction('state', 'readonly').objectStore('state').get('app');
        request.onsuccess = () => resolve(request.result?.value ?? null);
        request.onerror = () => reject(request.error);
      });
    },
    async save(value) {
      return new Promise((resolve, reject) => {
        const transaction = db.transaction('state', 'readwrite');
        transaction.objectStore('state').put({ key: 'app', value });
        transaction.oncomplete = () => resolve();
        transaction.onerror = () => reject(transaction.error);
        transaction.onabort = () => reject(transaction.error ?? new Error('Запись отменена'));
      });
    },
  };
}

function localStorageFallback() {
  return {
    async load() {
      const value = localStorage.getItem('calibre-mobile-state');
      return value ? JSON.parse(value) : null;
    },
    async save(value) {
      localStorage.setItem('calibre-mobile-state', JSON.stringify(value));
    },
  };
}

async function openStorage() {
  if (isNative) {
    try {
      const { CapacitorSQLite, SQLiteConnection } = await import('@capacitor-community/sqlite');
      const sqlite = new SQLiteConnection(CapacitorSQLite);
      await sqlite.createConnection('calibre', false, 'no-encryption', 1, false);
      const db = await sqlite.retrieveConnection('calibre', false);
      await db.open();
      await db.execute('CREATE TABLE IF NOT EXISTS app_state (key TEXT PRIMARY KEY NOT NULL, value TEXT NOT NULL)');
      storageLabel = 'SQLite на устройстве';
      return {
        async load() {
          const result = await db.query("SELECT value FROM app_state WHERE key = 'app'");
          const raw = result.values?.[0]?.value;
          return raw ? JSON.parse(raw) : null;
        },
        async save(value) {
          await db.run('INSERT OR REPLACE INTO app_state (key, value) VALUES (?, ?)', ['app', JSON.stringify(value)]);
        },
      };
    } catch (error) {
      console.warn('SQLite недоступна; включено локальное резервное хранилище.', error);
      try {
        const db = await openIndexedDb();
        storageLabel = 'резервное IndexedDB на устройстве';
        return indexedDbStorage(db);
      } catch {
        storageLabel = 'резервное хранилище браузера';
        return localStorageFallback();
      }
    }
  }
  try {
    const db = await openIndexedDb();
    storageLabel = 'IndexedDB в браузере';
    return indexedDbStorage(db);
  } catch (error) {
    console.warn('IndexedDB недоступна; включено резервное хранилище.', error);
    storageLabel = 'резервное хранилище браузера';
    return localStorageFallback();
  }
}

function mergeSavedState(saved) {
  if (!saved || typeof saved !== 'object') return;
  appState.serverUrl = typeof saved.serverUrl === 'string' ? saved.serverUrl : DEFAULT_STATE.serverUrl;
  if (saved.catalog && typeof saved.catalog === 'object') appState.catalog = ensureDemoCatalog(saved.catalog);
  appState.revision = typeof saved.revision === 'string' ? saved.revision : null;
  appState.lastSync = typeof saved.lastSync === 'string' ? saved.lastSync : null;
  appState.feedbackQueue = asArray(saved.feedbackQueue);
  appState.questionQueue = asArray(saved.questionQueue);
  appState.mediaCache = saved.mediaCache && typeof saved.mediaCache === 'object' ? saved.mediaCache : {};
  if (!isNative && window.location.pathname.startsWith('/preview')) {
    appState.serverUrl = window.location.origin;
  }
}

async function persist() {
  try {
    await storage.save(appState);
  } catch (error) {
    console.error('Не удалось сохранить данные локально', error);
    setMessage('Не удалось записать изменения в локальное хранилище.', 'bad');
  }
}

function setMessage(message, kind = '') {
  ui.message = message;
  ui.messageKind = kind;
}

function ensureDemoCatalog(catalog) {
  const value = catalog && typeof catalog === 'object' ? catalog : {};
  const demos = asArray(value.demo_machines);
  return {
    schema_version: value.schema_version || 1,
    real_machines: asArray(value.real_machines),
    demo_machines: demos.some(machine => machine.code === demoData.machine.code) ? demos : [...demos, EMBEDDED_DEMO],
  };
}
function machineList() {
  appState.catalog = ensureDemoCatalog(appState.catalog);
  return [...appState.catalog.demo_machines, ...appState.catalog.real_machines];
}
function replaceCatalog(catalog) {
  const previousMachine = findMachine();
  const previousNode = findNode(previousMachine);
  const previousProblem = findProblem(previousMachine);
  appState.catalog = ensureDemoCatalog(catalog);
  const nextMachine = previousMachine
    ? machineList().find(machine => machine.code === previousMachine.code)
    : null;
  if (nextMachine) {
    ui.machineId = nextMachine.id;
    if (previousNode) ui.nodeId = asArray(nextMachine.nodes).find(node => Number(node.number) === Number(previousNode.number))?.id ?? null;
    if (previousProblem) ui.problemId = asArray(nextMachine.problems).find(problem => problem.title === previousProblem.title)?.id ?? null;
  }
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
    for (const node of asArray(machine.nodes)) {
      items.push({ type: 'Узел', kind: 'node', machineId: machine.id, id: node.id,
        title: node.title, text: `${node.purpose ?? ''} ${asArray(node.adjustments).map(a => a.title).join(' ')}`,
        snippet: node.purpose || 'Карточка узла' });
    }
    for (const problem of asArray(machine.problems)) {
      items.push({ type: 'Проблема', kind: 'problem', machineId: machine.id, id: problem.id,
        title: problem.title, text: `${problem.symptom ?? ''} ${asArray(problem.steps).map(step => step.instruction).join(' ')}`,
        snippet: problem.symptom });
    }
    for (const fact of asArray(machine.characteristics)) {
      items.push({ type: 'Сведения из руководства', kind: 'machine', machineId: machine.id,
        title: fact.label, text: `${fact.value ?? ''} ${fact.source_quote ?? ''}`, snippet: fact.value });
    }
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
    container.innerHTML = `<div class="empty">В базе этого нет. Приложение не будет придумывать ответ.<button class="btn ghost" data-action="ask" data-context="search">Задать вопрос экспертам</button></div>`;
    return;
  }
  container.innerHTML = results.map(item => `<article class="search-result" data-action="open-search-result" data-kind="${item.kind}" data-id="${escapeHtml(item.id)}" data-machine-id="${escapeHtml(item.machineId)}"><div class="type">${escapeHtml(item.type)}</div><h4>${escapeHtml(item.title)}</h4><p>${escapeHtml(item.snippet)}</p></article>`).join('');
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

function renderScheme() {
  const nodes = asArray(findMachine()?.nodes).filter(node => node.map && node.map.x != null && node.map.y != null);
  const hotspots = nodes.map(node => {
    const x = Math.round(Number(node.map.x) * 700);
    const y = Math.round(Number(node.map.y) * 300);
    return `<g class="hotspot" data-action="open-node" data-id="${escapeHtml(node.id)}" tabindex="0" role="button" aria-label="Узел ${escapeHtml(node.number)}: ${escapeHtml(node.title)}"><title>${escapeHtml(node.title)}</title><circle cx="${x}" cy="${y}" r="12" fill="#2e7d32" stroke="#fff" stroke-width="3"/><text x="${x}" y="${y + 4}" text-anchor="middle" font-size="11" font-weight="700" fill="#fff" pointer-events="none">${escapeHtml(node.number)}</text></g>`;
  }).join('');
  return `<section class="scheme" aria-label="Схема ГРН-471. Нажмите на номер узла"><svg viewBox="0 0 700 300" role="img" aria-label="Упрощённая схема граблей, подготовленная для демо"><rect x="0" y="0" width="700" height="300" rx="10" fill="#fff"/><path d="M86 146h235l78-36h80" fill="none" stroke="#34483a" stroke-width="14" stroke-linecap="round"/><path d="M316 129h130v47H316z" fill="#e8f5e9" stroke="#2e7d32" stroke-width="5"/><path d="M414 122l80-35m-80 55 88 0m-88 20 76 36" fill="none" stroke="#43a047" stroke-width="6" stroke-linecap="round"/><circle cx="548" cy="145" r="72" fill="#f3f7f3" stroke="#2e7d32" stroke-width="9"/><circle cx="548" cy="145" r="18" fill="#2e7d32"/><path d="M548 73v54m0 36v54m-72-72h54m36 0h54m-122-51 39 39m22 24 39 39m0-102-39 39m-22 24-39 39" stroke="#6d8d70" stroke-width="5" stroke-linecap="round"/><path d="M486 75q65-40 128 3m-127 135q62 39 128-1" fill="none" stroke="#a5b9a7" stroke-width="7"/><path d="M62 118h74v57H62z" fill="#e8f5e9" stroke="#2e7d32" stroke-width="5"/><circle cx="88" cy="204" r="22" fill="#34483a"/><circle cx="147" cy="204" r="16" fill="#34483a"/><path d="M136 145h58m0-10v20m-10-10h20" stroke="#485b4c" stroke-width="7" stroke-linecap="round"/><path d="M390 185l40 38m0-38-40 38" stroke="#8b9d8d" stroke-width="5"/>${hotspots}</svg></section>`;
}

function renderMachine() {
  const machine = findMachine();
  if (!machine) return '<div class="catalog-empty">Машина не найдена.</div>';
  const tabs = `<div class="tabs"><button data-action="tab" data-tab="scheme" class="${ui.tab === 'scheme' ? 'on' : ''}">Схема</button><button data-action="tab" data-tab="nodes" class="${ui.tab === 'nodes' ? 'on' : ''}">Узлы</button><button data-action="tab" data-tab="problems" class="${ui.tab === 'problems' ? 'on' : ''}">Неисправности</button></div>`;
  let tabContent = '';
  if (ui.tab === 'scheme') {
    const characteristics = asArray(machine.characteristics).map(row => `<div class="settings-row"><span>${escapeHtml(row.label)}${row.source_page ? `<div class="metadata">Руководство · стр. ${escapeHtml(row.source_page)}${row.verification_label ? ` · ${escapeHtml(row.verification_label)}` : ''}${row.verification_note ? ` · ${escapeHtml(row.verification_note)}` : ''}</div>` : ''}${row.source_quote ? `<div class="metadata">«${escapeHtml(row.source_quote)}»</div>` : ''}</span><strong>${escapeHtml(row.value)}</strong></div>`).join('');
    tabContent = `${renderScheme(machine)}<article class="card"><div class="section" style="margin-top:0">Характеристики</div>${characteristics || '<div class="catalog-empty">Характеристики пока не добавлены.</div>'}</article>`;
  } else if (ui.tab === 'nodes') {
    tabContent = asArray(machine.nodes).map((node, index) => `<article class="list-item" data-action="open-node" data-id="${escapeHtml(node.id)}"><div class="num">${index + 1}</div><div><h4>${escapeHtml(node.title)}</h4><p>${escapeHtml(node.purpose || 'Карточка узла')}</p></div><span class="arrow">›</span></article>`).join('') || '<div class="catalog-empty">Узлы пока не добавлены.</div>';
  } else {
    tabContent = asArray(machine.problems).map(problem => `<article class="list-item" data-action="open-problem" data-id="${escapeHtml(problem.id)}"><div class="num warn">!</div><div><h4>${escapeHtml(problem.title)}</h4><p>${asArray(problem.steps).length} шагов проверки</p></div><span class="arrow">›</span></article>`).join('') || '<div class="catalog-empty">Неисправности пока не добавлены.</div>';
  }
  return `<div class="machine-line"><h2 class="screen-title">${escapeHtml(machine.model)}</h2></div><p class="hint">${escapeHtml(machine.machine_type || '')}${machine.label ? ` · ${escapeHtml(machine.label)}` : ''}</p>${tabs}${tabContent}`;
}

function renderNodeScreen() {
  const machine = findMachine();
  const node = findNode(machine);
  if (!node) return '<div class="catalog-empty">Карточка узла не найдена.</div>';
  const adjustments = asArray(node.adjustments).map(adjustment => {
    const norms = asArray(adjustment.norms);
    const valueHtml = norms.length
      ? norms.map(norm => `<div class="value"><span style="display:block;font-size:10px">${escapeHtml(norm.name || '')}</span>${escapeHtml(norm.value)}${norm.unit ? ` ${escapeHtml(norm.unit)}` : ''}${norm.condition ? `<span style="display:block;font-size:10px;font-weight:600">${escapeHtml(norm.condition)}</span>` : ''}</div>`).join('')
      : '<div class="metadata">Значения не опубликованы.</div>';
    const steps = asArray(adjustment.instructions).map(step => `<li>${escapeHtml(step)}</li>`).join('');
    return `<article class="adjustment"><h4>${escapeHtml(adjustment.title)}</h4>${valueHtml}${steps ? `<ol>${steps}</ol>` : ''}${adjustment.tool ? `<div class="tool">🔧 ${escapeHtml(adjustment.tool)}</div>` : ''}${norms.map(norm => `<div class="metadata">Источник: ${escapeHtml(norm.source?.document || 'указан в карточке')}${norm.source?.page_start ? ` · стр. ${escapeHtml(norm.source.page_start)}` : ''}${norm.source?.quote ? ` · «${escapeHtml(norm.source.quote)}»` : ''}</div>`).join('')}</article>`;
  }).join('');
  const mistakes = asArray(node.mistakes).map(item => `<li>${escapeHtml(item.description)}${item.consequence ? ` — ${escapeHtml(item.consequence)}` : ''}</li>`).join('');
  const related = asArray(node.related_node_ids).map(id => findNode(machine, id)).filter(Boolean).map(relatedNode => `<article class="list-item" data-action="open-node" data-id="${escapeHtml(relatedNode.id)}"><div class="num">${escapeHtml(relatedNode.number)}</div><div><h4>${escapeHtml(relatedNode.title)}</h4></div><span class="arrow">›</span></article>`).join('');
  return `<div class="node-head"><div class="num">${escapeHtml(node.number)}</div><h2>${escapeHtml(node.title)}</h2></div><article class="card"><div class="section" style="margin-top:0">Описание</div><p class="what">${escapeHtml(node.purpose)}</p>${node.source_page ? `<div class="metadata">Руководство · стр. ${escapeHtml(node.source_page)} · «${escapeHtml(node.source_quote || '')}»</div>` : ''}</article><div class="section">Регулировки</div>${adjustments || '<div class="catalog-empty">Регулировки пока не добавлены.</div>'}${mistakes ? `<div class="errbox"><b>Типичные ошибки</b><ul>${mistakes}</ul></div>` : ''}${related ? `<div class="section">Связанные узлы</div>${related}` : ''}<article class="card"><b style="font-size:14px">Карточка помогла?</b><div class="fb"><button data-action="feedback" data-kind="node" data-id="${escapeHtml(node.id)}" data-helpful="true">👍 Да</button><button data-action="feedback" data-kind="node" data-id="${escapeHtml(node.id)}" data-helpful="false">👎 Нет</button></div></article><button class="btn ghost" data-action="ask" data-context="node">Задать вопрос</button>`;
}

function renderProblemScreen() {
  const machine = findMachine();
  const problem = findProblem(machine);
  if (!problem) return '<div class="catalog-empty">Неисправность не найдена.</div>';
  const steps = asArray(problem.steps).map((step, index) => {
    const linkedNode = step.linked_node_id ? findNode(machine, step.linked_node_id) : null;
    return `<article class="card step"><div class="step-n">${index + 1}</div><div><p>${escapeHtml(step.instruction)}</p>${step.source_page ? `<div class="metadata">РЭ · стр. ${escapeHtml(step.source_page)} · «${escapeHtml(step.source_quote || '')}»</div>` : ''}${linkedNode ? `<span class="link" data-action="open-node" data-id="${escapeHtml(linkedNode.id)}">→ Узел ${escapeHtml(linkedNode.number)}: ${escapeHtml(linkedNode.title)}</span>` : ''}</div></article>`;
  }).join('');
  return `<article class="card"><h2 style="font-size:16px;line-height:1.35;margin:0">${escapeHtml(problem.title)}</h2><p class="hint" style="margin:7px 0 0;white-space:pre-line">${escapeHtml(problem.symptom)}</p>${problem.source_page ? `<div class="metadata">РЭ · стр. ${escapeHtml(problem.source_page)} · ${escapeHtml(problem.source_quote || '')}</div>` : ''}</article>${steps || '<div class="catalog-empty">Шаги проверки пока не добавлены.</div>'}<article class="card"><b style="font-size:14px">Неисправность устранена?</b><div class="fb"><button data-action="feedback" data-kind="problem" data-id="${escapeHtml(problem.id)}" data-helpful="true">✅ Да</button><button data-action="feedback" data-kind="problem" data-id="${escapeHtml(problem.id)}" data-helpful="false">❌ Нет</button></div></article><button class="btn ghost" data-action="ask" data-context="problem">Задать вопрос</button>`;
}

function getApiUrl(path) {
  if (import.meta.env.DEV && !isNative) return `${API_PREFIX}${path}`;
  const server = appState.serverUrl.trim().replace(/\/+$/, '');
  if (!server) throw new Error('Адрес сервера не задан.');
  let parsed;
  try { parsed = new URL(server); } catch { throw new Error('Введите полный адрес, например http://192.168.1.20:8765.'); }
  if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error('Допустимы адреса HTTP или HTTPS.');
  return `${server}${API_PREFIX}${path}`;
}
async function apiFetch(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12000);
  try {
    return await fetch(getApiUrl(path), { ...options, signal: controller.signal, headers: { 'Content-Type': 'application/json', ...(options.headers || {}) } });
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('Сервер не ответил за отведённое время. Проверьте адрес и сеть.');
    throw new Error(navigator.onLine === false ? 'Нет подключения. Данные и отправка сохранены в очереди.' : 'Не удалось связаться с сервером. Проверьте адрес, Wi-Fi и запуск сервера.');
  } finally {
    clearTimeout(timeout);
  }
}
async function responseJson(response) {
  let body = {};
  try { body = await response.json(); } catch { /* server may not return JSON */ }
  if (!response.ok) throw new Error(body.detail || body.message || `Ошибка сервера: ${response.status}`);
  return body;
}
function blobAsDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = () => reject(reader.error || new Error('Не удалось сохранить изображение локально.'));
    reader.readAsDataURL(blob);
  });
}
async function syncInternalMedia() {
  const photos = machineList().flatMap(machine => asArray(machine.nodes).flatMap(node => asArray(node.photos)))
    .filter(photo => photo.internal_lan_only && photo.url && !appState.mediaCache[String(photo.id)]);
  let downloaded = 0;
  let failed = 0;
  let cachedBytes = Object.values(appState.mediaCache).reduce((sum, value) => sum + Math.floor(String(value).length * 0.75), 0);
  for (const photo of photos) {
    try {
      const response = await apiFetch(photo.url);
      if (!response.ok) { await responseJson(response); failed += 1; continue; }
      const blob = await response.blob();
      if (!blob.type.startsWith('image/') || blob.size > 1_500_000 || cachedBytes + blob.size > 12_000_000) {
        failed += 1;
        continue;
      }
      appState.mediaCache[String(photo.id)] = await blobAsDataUrl(blob);
      cachedBytes += blob.size;
      downloaded += 1;
    } catch (_) { failed += 1; }
  }
  if (downloaded) await persist();
  return { downloaded, failed };
}
function currentServerAddress() {
  if (import.meta.env.DEV && !isNative) return 'локальный сервер через loopback-прокси';
  return appState.serverUrl;
}
async function checkServer() {
  appState.serverUrl = $('#serverUrl')?.value.trim() || appState.serverUrl;
  await persist();
  setMessage('Проверяем связь…');
  try {
    const response = await apiFetch('/ping');
    const body = await responseJson(response);
    setMessage(body.message || 'Связь с сервером есть.', 'good');
  } catch (error) {
    setMessage(error.message, 'bad');
  }
}
function findStableMachine(code) {
  return machineList().find(machine => machine.code === code) ?? null;
}
function resolveQueuedFeedback(entry) {
  const ref = entry.stable_ref;
  if (!ref) return Number(entry.target_id) > 0 ? Number(entry.target_id) : null;
  const machine = findStableMachine(ref.machine_code);
  if (!machine) return null;
  if (ref.node_number != null) {
    const node = asArray(machine.nodes).find(item => Number(item.number) === Number(ref.node_number));
    return node && Number(node.id) > 0 ? Number(node.id) : null;
  }
  if (ref.problem_title) {
    const problem = asArray(machine.problems).find(item => item.title === ref.problem_title);
    return problem && Number(problem.id) > 0 ? Number(problem.id) : null;
  }
  return Number(entry.target_id) > 0 ? Number(entry.target_id) : null;
}
async function flushQueues() {
  // Queue entries created on a first-use offline install contain stable source
  // references. Fetch the PC catalogue before attempting to map them to DB IDs.
  if (appState.revision == null) {
    const initial = await apiFetch('/sync');
    const synced = await responseJson(initial);
    if (synced.changed && synced.catalog) replaceCatalog(synced.catalog);
    appState.revision = synced.revision || null;
    appState.lastSync = new Date().toISOString();
    await persist();
  }
  for (const entry of [...appState.feedbackQueue]) {
    const targetId = resolveQueuedFeedback(entry);
    if (!targetId) continue;
    const response = await apiFetch('/feedback', {
      method: 'POST',
      body: JSON.stringify({ ...entry, target_id: targetId }),
    });
    await responseJson(response);
    appState.feedbackQueue = appState.feedbackQueue.filter(item => item.client_event_id !== entry.client_event_id);
    await persist();
  }
  for (const entry of [...appState.questionQueue]) {
    const machine = entry.machine_code ? findStableMachine(entry.machine_code) : null;
    const payload = { ...entry, machine_id: machine && Number(machine.id) > 0 ? Number(machine.id) : null };
    delete payload.machine_code;
    if (payload.context?.node_number != null && machine) {
      const node = asArray(machine.nodes).find(item => Number(item.number) === Number(payload.context.node_number));
      if (node) payload.context.node_id = Number(node.id) > 0 ? Number(node.id) : null;
      delete payload.context.node_number;
    }
    if (payload.context?.problem_title && machine) {
      const problem = asArray(machine.problems).find(item => item.title === payload.context.problem_title);
      if (problem) payload.context.problem_id = Number(problem.id) > 0 ? Number(problem.id) : null;
      delete payload.context.problem_title;
    }
    const response = await apiFetch('/questions', { method: 'POST', body: JSON.stringify(payload) });
    await responseJson(response);
    appState.questionQueue = appState.questionQueue.filter(item => item.client_event_id !== entry.client_event_id);
    await persist();
  }
}
async function syncNow() {
  if (ui.syncBusy) return;
  appState.serverUrl = $('#serverUrl')?.value.trim() || appState.serverUrl;
  await persist();
  if (!appState.serverUrl && !(import.meta.env.DEV && !isNative)) {
    setMessage('Укажите адрес сервера в настройках.', 'bad');
    return;
  }
  ui.syncBusy = true;
  render();
  setMessage('Синхронизируем каталог и очередь…');
  try {
    const suffix = appState.revision ? `?revision=${encodeURIComponent(appState.revision)}` : '';
    const syncResponse = await apiFetch(`/sync${suffix}`);
    const syncResult = await responseJson(syncResponse);
    if (syncResult.changed && syncResult.catalog) replaceCatalog(syncResult.catalog);
    appState.revision = syncResult.revision;
    appState.lastSync = new Date().toISOString();
    await persist();
    const mediaResult = await syncInternalMedia();
    await flushQueues();
    const queued = appState.feedbackQueue.length + appState.questionQueue.length;
    const mediaMessage = mediaResult.failed ? ` Не удалось сохранить изображений: ${mediaResult.failed}; повторите синхронизацию при связи.` :
      mediaResult.downloaded ? ` Внутренних изображений передано по LAN: ${mediaResult.downloaded}.` : '';
    const queueMessage = queued ? ` Не отправлено записей очереди: ${queued}. Повторим при связи.` : '';
    setMessage(`Синхронизация завершена. Каталог обновлён.${mediaMessage}${queueMessage}`, mediaResult.failed ? 'bad' : 'good');
  } catch (error) {
    setMessage(error.message, 'bad');
  } finally {
    ui.syncBusy = false;
    render();
  }
}

async function queueFeedback(targetType, targetId, helpful) {
  const machine = findMachine();
  const node = targetType === 'node' ? findNode(machine, targetId) : null;
  const problem = targetType === 'problem' ? findProblem(machine, targetId) : null;
  const numericId = Number(targetId);
  appState.feedbackQueue.push({
    client_event_id: newEventId(),
    target_type: targetType,
    target_id: Number.isSafeInteger(numericId) && numericId > 0 ? numericId : null,
    helpful: helpful === 'true',
    stable_ref: machine?.code ? {
      machine_code: machine.code,
      ...(node ? { node_number: node.number } : {}),
      ...(problem ? { problem_title: problem.title } : {}),
    } : null,
  });
  await persist();
  setMessage('Ответ сохранён на устройстве и будет отправлен при подключении.', 'good');
  if (navigator.onLine !== false && (appState.serverUrl || (import.meta.env.DEV && !isNative))) {
    try { await flushQueues(); setMessage('Спасибо! Ответ отправлен серверу.', 'good'); }
    catch { setMessage('Спасибо! Ответ останется в очереди до связи с сервером.', 'good'); }
  }
}

function openModal(content) {
  $('#sheet').innerHTML = content;
  $('#modal').classList.add('show');
  $('#modal').setAttribute('aria-hidden', 'false');
  const focusable = $('#sheet input, #sheet textarea, #sheet button');
  focusable?.focus();
}
function closeModal() {
  $('#modal').classList.remove('show');
  $('#modal').setAttribute('aria-hidden', 'true');
}
function openQuestion(context) {
  const machine = findMachine();
  const node = findNode(machine);
  const problem = findProblem(machine);
  const description = context === 'node' ? node?.title : context === 'problem' ? problem?.title : 'поиск по справочнику';
  openModal(`<h3>Вопрос экспертам</h3><p>Вопрос сохранится на устройстве. Отправка на сервер произойдёт при подключении.</p><form id="questionForm"><label class="field"><span>Ваш вопрос</span><textarea id="questionText" maxlength="2000" required placeholder="Опишите, что нужно уточнить"></textarea></label><button class="btn" type="submit">Сохранить вопрос</button><button class="btn ghost" type="button" data-action="close-modal">Отмена</button></form><p class="metadata">Контекст: ${escapeHtml(description || 'справочник')}. Имя пользователя не запрашивается.</p>`);
}
async function saveQuestion(event) {
  event.preventDefault();
  const question = $('#questionText')?.value.trim();
  if (!question) return;
  const machine = findMachine();
  const node = findNode(machine);
  const problem = findProblem(machine);
  const context = { screen: ui.screen };
  if (node) context.node_number = node.number;
  if (problem) context.problem_title = problem.title;
  const numericMachineId = Number(machine?.id);
  appState.questionQueue.push({
    client_event_id: newEventId(),
    machine_id: Number.isSafeInteger(numericMachineId) && numericMachineId > 0 ? numericMachineId : null,
    machine_code: machine?.code ?? null,
    question,
    context,
  });
  await persist();
  closeModal();
  setMessage('Вопрос сохранён. Он отправится при подключении к серверу.', 'good');
  if (navigator.onLine !== false && (appState.serverUrl || (import.meta.env.DEV && !isNative))) {
    try { await flushQueues(); setMessage('Вопрос отправлен на сервер.', 'good'); }
    catch { setMessage('Вопрос останется в очереди до связи с сервером.', 'good'); }
  }
}


async function handleAction(action, element) {
  if (action === 'back') goBack();
  else if (action === 'home') setScreen('home');
  else if (action === 'open-machine') { ui.tab = 'scheme'; go('machine', { machineId: element.dataset.id }); }
  else if (action === 'open-node') go('node', { nodeId: element.dataset.id });
  else if (action === 'open-problem') go('problem', { problemId: element.dataset.id });
  else if (action === 'open-search-result') {
    if (element.dataset.kind === 'node') go('node', { machineId: element.dataset.machineId, nodeId: element.dataset.id });
    else if (element.dataset.kind === 'problem') go('problem', { machineId: element.dataset.machineId, problemId: element.dataset.id });
    else { ui.tab = 'scheme'; go('machine', { machineId: element.dataset.machineId }); }
  } else if (action === 'tab') { ui.tab = element.dataset.tab; render(); }
  else if (action === 'close-modal') closeModal();
  else if (action === 'ask') openQuestion(element.dataset.context);
  else if (action === 'feedback') await queueFeedback(element.dataset.kind, element.dataset.id, element.dataset.helpful);
  else if (action === 'check-server') await checkServer();
  else if (action === 'sync') await syncNow();
}

$('#app').addEventListener('click', async event => {
  if (event.target.id === 'modal') { closeModal(); return; }
  const actionElement = event.target.closest('[data-action]');
  if (!actionElement) return;
  await handleAction(actionElement.dataset.action, actionElement);
});
$('#app').addEventListener('input', event => {
  if (event.target.id === 'homeSearch') {
    ui.searchQuery = event.target.value;
    renderSearchResults(event.target.value, 'homeResults');
  }
});
$('#app').addEventListener('submit', event => {
  if (event.target.id === 'questionForm') void saveQuestion(event);
});
$('#app').addEventListener('change', event => {
  if (event.target.id === 'serverUrl') {
    appState.serverUrl = event.target.value.trim();
    void persist();
  }
});

document.addEventListener('keydown', event => {
  if (event.key === 'Escape') closeModal();
  if ((event.key === 'Enter' || event.key === ' ') && event.target.matches('.hotspot')) {
    event.preventDefault();
    event.target.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  }
});

window.addEventListener('online', () => {
  setMessage('Сеть появилась. Отправляем очередь…', 'good');
  void syncNow();
});
window.addEventListener('offline', () => setMessage('Офлайн-режим: очередь сохранена на устройстве.', 'bad'));
window.addEventListener('focus', () => {
  if (navigator.onLine !== false && (appState.feedbackQueue.length || appState.questionQueue.length)) void syncNow();
});

async function setupNetworkListener() {
  if (!isNative) return;
  try {
    const { Network } = await import('@capacitor/network');
    networkListener = await Network.addListener('networkStatusChange', status => {
      if (status.connected) {
        setMessage('Сеть появилась. Отправляем очередь…', 'good');
        void syncNow();
      } else setMessage('Нет сети. Сохранённые карточки доступны офлайн.', 'bad');
    });
  } catch (error) {
    console.warn('Слушатель сети недоступен; синхронизацию можно запустить вручную.', error);
  }
}

async function boot() {
  storage = await openStorage();
  try {
    mergeSavedState(await storage.load());
  } catch (error) {
    console.warn('Не удалось прочитать локальное состояние; открываем сохранённый каталог по умолчанию.', error);
  }
  render();
  appReadyForIntro = true;
  dismissIntroWhenReady();
  await setupNetworkListener();
  if (appState.serverUrl && navigator.onLine !== false) {
    void syncNow();
  }
}

void boot();
