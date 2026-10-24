import { Capacitor } from '@capacitor/core';

const isNative = Capacitor.isNativePlatform();
const API_PREFIX = '/api/mobile';
const BROWSER_ORIGIN = window.location.protocol.startsWith('http') ? window.location.origin : '';
const DEFAULT_STATE = {
  serverUrl: isNative ? '' : BROWSER_ORIGIN,
  catalog: { schema_version: 1, real_machines: [], demo_machines: [] },
  revision: null,
  lastSync: null,
  feedbackQueue: [],
  questionQueue: [],
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
  if (saved.catalog && typeof saved.catalog === 'object') appState.catalog = saved.catalog;
  appState.revision = typeof saved.revision === 'string' ? saved.revision : null;
  appState.lastSync = typeof saved.lastSync === 'string' ? saved.lastSync : null;
  appState.feedbackQueue = asArray(saved.feedbackQueue);
  appState.questionQueue = asArray(saved.questionQueue);
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
  renderStatus();
}

function renderStatus() {
  const strip = $('#statusStrip');
  if (!strip) return;
  const queueCount = appState.feedbackQueue.length + appState.questionQueue.length;
  const isDemo = asArray(appState.catalog.demo_machines).length > 0;
  const parts = [];
  if (isDemo) parts.push('ДЕМО — примерные данные, не применять для настройки техники');
  else parts.push(navigator.onLine === false ? 'ОФЛАЙН · сохранённые карточки доступны' : 'КАЛИБР · локальная база на устройстве');
  parts.push(`Хранилище: ${storageLabel}`);
  if (queueCount) parts.push(`В очереди отправки: ${queueCount}`);
  if (ui.message) parts.push(ui.message);
  strip.textContent = parts.join(' · ');
  strip.className = `status-strip${isDemo ? ' demo' : ''}${ui.messageKind === 'bad' ? ' error' : ui.messageKind === 'good' ? ' online' : ''}`;
}

function machineList() {
  return [...asArray(appState.catalog.real_machines), ...asArray(appState.catalog.demo_machines)];
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
function isDemo(machine) { return Boolean(machine?.is_demo); }
function demoBadge(machine) { return isDemo(machine) ? '<span class="badge demo">ДЕМО</span>' : ''; }
function titleForScreen() {
  const machine = findMachine();
  if (ui.screen === 'settings') return ['Настройки', 'Сервер, хранение и синхронизация'];
  if (ui.screen === 'catalog') return ['Машины в базе', 'Опубликованный справочник'];
  if (ui.screen === 'machine') return [isDemo(machine) ? 'Образец ДЕМО' : (machine?.model || 'Машина'), machine?.machine_type || 'Карточка машины'];
  if (ui.screen === 'node') return ['Карточка узла', machine?.model || 'ДЕМО'];
  if (ui.screen === 'problem') return ['Что-то не так', machine?.model || 'ДЕМО'];
  return ['КАЛИБР', 'Настройки сельхозмашин · офлайн'];
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
  titleElement.innerHTML = `${ui.screen === 'home' ? '<span class="logo">КАЛИБР</span>' : escapeHtml(title)}<small>${escapeHtml(subtitle)}</small>`;
  $('#back').classList.toggle('show', ui.screen !== 'home');
  document.querySelectorAll('.bottom-nav button').forEach(button => {
    button.classList.toggle('active', button.dataset.action === (ui.screen === 'catalog' || ui.screen === 'machine' || ui.screen === 'node' || ui.screen === 'problem' ? 'catalog' : ui.screen === 'settings' ? 'settings' : 'home'));
  });
  const main = $('#main');
  if (ui.screen === 'home') main.innerHTML = renderHome();
  else if (ui.screen === 'catalog') main.innerHTML = renderCatalog();
  else if (ui.screen === 'machine') main.innerHTML = renderMachine();
  else if (ui.screen === 'node') main.innerHTML = renderNodeScreen();
  else if (ui.screen === 'problem') main.innerHTML = renderProblemScreen();
  else if (ui.screen === 'settings') main.innerHTML = renderSettings();
  renderStatus();
  main.scrollTop = 0;
}

function searchIndex() {
  const items = [];
  for (const machine of machineList()) {
    for (const node of asArray(machine.nodes)) {
      items.push({ type: 'Узел', kind: 'node', machineId: machine.id, id: node.id,
        title: node.title, text: `${node.purpose ?? ''} ${asArray(node.adjustments).map(a => a.title).join(' ')}`,
        snippet: node.purpose || (isDemo(machine) ? 'ДЕМО: источник не проверен' : 'Карточка узла') });
    }
    for (const problem of asArray(machine.problems)) {
      items.push({ type: 'Проблема', kind: 'problem', machineId: machine.id, id: problem.id,
        title: problem.title, text: `${problem.symptom ?? ''} ${asArray(problem.steps).map(step => step.instruction).join(' ')}`,
        snippet: problem.symptom });
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
  container.innerHTML = results.map(item => `<article class="search-result" data-action="open-search-result" data-kind="${item.kind}" data-id="${escapeHtml(item.id)}" data-machine-id="${escapeHtml(item.machineId)}"><div class="type">${escapeHtml(item.type)}${isDemo(findMachine(item.machineId)) ? ' · ДЕМО' : ''}</div><h4>${escapeHtml(item.title)}</h4><p>${escapeHtml(item.snippet)}</p></article>`).join('');
}

function renderHome() {
  const machines = machineList();
  const realMachines = machines.filter(machine => !isDemo(machine));
  const demos = machines.filter(isDemo);
  const demoHtml = demos.map(machine => `<article class="card machine-card" data-action="open-machine" data-id="${escapeHtml(machine.id)}"><div class="machine-art" aria-hidden="true">◌</div><div class="machine-info"><div class="machine-line">${demoBadge(machine)}</div><h3>${escapeHtml(machine.model || 'Учебный образец')}</h3><p>${escapeHtml(machine.machine_type || 'Демонстрационная карточка')}</p><p>${asArray(machine.nodes).length} узлов · ${asArray(machine.problems).length} примеров проблем</p></div><span class="arrow">›</span></article>`).join('');
  const realHtml = realMachines.map(machine => `<article class="card machine-card" data-action="open-machine" data-id="${escapeHtml(machine.id)}"><div class="machine-art" aria-hidden="true">◌</div><div class="machine-info"><span class="badge verified">ОПУБЛИКОВАНО</span><h3>${escapeHtml(machine.model)}</h3><p>${escapeHtml(machine.machine_type)}</p></div><span class="arrow">›</span></article>`).join('');
  return `<section class="search"><span aria-hidden="true">🔍</span><input id="homeSearch" value="${escapeHtml(ui.searchQuery)}" placeholder="Например: высота, ремни, шум…" autocomplete="off" aria-label="Поиск по справочнику"></section><div class="hint">Поиск работает по сохранённым карточкам. Без интернета доступны данные последней синхронизации.</div><div id="homeResults"></div><button class="btn ghost" data-action="photo">📷 Найти узел по фото</button><div class="section">Машины в базе</div>${realHtml}${demoHtml}${machines.length ? '' : '<div class="catalog-empty">Сохранённых карточек пока нет.<br>Подключитесь к серверу и выполните синхронизацию.</div>'}<aside class="notice">Перед настройкой сверяйтесь с паспортом конкретной машины. Карточки со статусом «ДЕМО» — только для знакомства с приложением, не для выполнения работ.</aside>`;
}

function renderCatalog() {
  const machines = machineList();
  return `<h2 class="screen-title">Каталог машин</h2><p class="hint">Опубликованные карточки хранятся на устройстве и доступны без сети.</p>${machines.map(machine => `<article class="card machine-card" data-action="open-machine" data-id="${escapeHtml(machine.id)}"><div class="machine-art">◌</div><div class="machine-info">${demoBadge(machine)}${isDemo(machine) ? '' : '<span class="badge verified">ПРОВЕРЕНО</span>'}<h3>${escapeHtml(machine.model)}</h3><p>${escapeHtml(machine.machine_type)}</p><p>${asArray(machine.nodes).length} узлов · ${asArray(machine.problems).length} проблем</p></div><span class="arrow">›</span></article>`).join('') || '<div class="catalog-empty">Каталог пока пуст. Выполните синхронизацию с сервером.</div>'}`;
}

function renderScheme(machine) {
  const nodes = asArray(machine?.nodes);
  const circles = nodes.map((node, index) => {
    const x = Math.max(18, Math.min(342, Number(node.map?.x) * 360 || 180));
    const y = Math.max(26, Math.min(222, Number(node.map?.y) * 250 || 125));
    return `<g class="hotspot" data-action="open-node" data-id="${escapeHtml(node.id)}" tabindex="0" role="button" aria-label="ДЕМО, узел ${index + 1}"><circle cx="${x}" cy="${y}" r="13" fill="#f9a825" stroke="#fff" stroke-width="2.5"/><text x="${x}" y="${y + 4.5}" font-size="13" font-weight="800" text-anchor="middle" fill="#3e2723">${index + 1}</text></g>`;
  }).join('');
  return `<div class="scheme"><svg viewBox="0 0 360 250" role="img" aria-label="Упрощённая демонстрационная схема"><defs><pattern id="grass" width="10" height="10" patternUnits="userSpaceOnUse"><path d="M2 10L4 4M6 10L8 5" stroke="#9ccc65" stroke-width="1"/></pattern></defs><rect x="0" y="215" width="360" height="35" fill="url(#grass)"/><line x1="0" y1="215" x2="360" y2="215" stroke="#8d6e63" stroke-width="2"/><rect x="20" y="65" width="48" height="120" rx="9" fill="#90a4ae"/><circle cx="42" cy="190" r="22" fill="#37474f"/><circle cx="42" cy="190" r="9" fill="#90a4ae"/><line x1="68" y1="125" x2="110" y2="155" stroke="#455a64" stroke-width="5"/><rect x="103" y="135" width="45" height="32" rx="5" fill="#7cb342"/><line x1="130" y1="167" x2="315" y2="167" stroke="#2e7d32" stroke-width="13" stroke-linecap="round"/><ellipse cx="175" cy="165" rx="20" ry="8" fill="#43a047"/><ellipse cx="225" cy="165" rx="20" ry="8" fill="#43a047"/><ellipse cx="275" cy="165" rx="20" ry="8" fill="#43a047"/><line x1="130" y1="127" x2="200" y2="104" stroke="#689f38" stroke-width="4"/><rect x="180" y="92" width="54" height="29" rx="7" fill="#7cb342"/><text x="180" y="239" font-size="10" fill="#5f6b64" text-anchor="middle">СХЕМА ДЕМО · расположение условное</text>${circles}</svg></div><p class="hint" style="text-align:center">Нажмите на маркер, чтобы открыть карточку узла</p>`;
}

function renderMachine() {
  const machine = findMachine();
  if (!machine) return '<div class="catalog-empty">Машина не найдена. Возможно, каталог ещё не синхронизирован.</div>';
  const demo = isDemo(machine);
  const tabs = `<div class="tabs"><button data-action="tab" data-tab="scheme" class="${ui.tab === 'scheme' ? 'on' : ''}">Схема</button><button data-action="tab" data-tab="nodes" class="${ui.tab === 'nodes' ? 'on' : ''}">Узлы</button><button data-action="tab" data-tab="problems" class="${ui.tab === 'problems' ? 'on' : ''}">Что-то не так</button></div>`;
  let tabContent = '';
  if (ui.tab === 'scheme') {
    const characteristics = asArray(machine.characteristics).map(row => `<div class="settings-row"><span>${escapeHtml(row.label)}</span><strong>${demo ? '<span class="badge demo">ДЕМО</span>' : escapeHtml(row.value)}</strong></div>`).join('');
    tabContent = `${renderScheme(machine)}<article class="card"><div class="section" style="margin-top:0">Характеристики</div>${characteristics || `<div class="settings-row"><span>Технические значения</span><strong class="badge demo">ДЕМО</strong></div>`}<p class="metadata">${demo ? 'ДЕМО: технические значения не указаны и не являются инструкцией.' : 'Показаны только опубликованные сведения.'}</p></article>`;
  } else if (ui.tab === 'nodes') {
    tabContent = asArray(machine.nodes).map((node, index) => `<article class="list-item" data-action="open-node" data-id="${escapeHtml(node.id)}"><div class="num">${index + 1}</div><div><h4>${escapeHtml(node.title)}</h4><p>${escapeHtml(node.purpose || (demo ? 'ДЕМО: сведения не проверены' : 'Карточка узла'))}</p></div><span class="arrow">›</span></article>`).join('') || '<div class="catalog-empty">Опубликованных узлов нет.</div>';
  } else {
    tabContent = `<div class="hint">Выберите симптом из сохранённого справочника. Порядок шагов доступен без сети.</div>${asArray(machine.problems).map(problem => `<article class="list-item" data-action="open-problem" data-id="${escapeHtml(problem.id)}"><div class="num warn">!</div><div><h4>${escapeHtml(problem.title)}</h4><p>${asArray(problem.steps).length} шага проверки</p></div><span class="arrow">›</span></article>`).join('') || '<div class="catalog-empty">Опубликованных проблем нет.</div>'}`;
  }
  return `${demo ? '<div class="demo-banner">ДЕМО · не применять для настройки техники</div>' : ''}<div class="machine-line"><h2 class="screen-title">${escapeHtml(demo ? 'Учебный образец' : machine.model)}</h2>${demoBadge(machine)}</div><p class="hint">${escapeHtml(machine.machine_type || '')}</p>${tabs}${tabContent}`;
}

function renderNodeScreen() {
  const machine = findMachine();
  const node = findNode(machine);
  if (!node) return '<div class="catalog-empty">Карточка узла недоступна в сохранённой базе.</div>';
  const demo = isDemo(machine);
  const adjustments = asArray(node.adjustments).map(adjustment => {
    const norms = asArray(adjustment.norms);
    const valueHtml = demo
      ? '<div class="value demo">ДЕМО</div>'
      : norms.length
        ? norms.map(norm => `<div class="value">${escapeHtml(norm.value)}${norm.unit ? ` ${escapeHtml(norm.unit)}` : ''}</div>`).join('')
        : '<div class="metadata">Проверенные значения не опубликованы.</div>';
    const steps = asArray(adjustment.instructions).map(step => `<li>${escapeHtml(step)}</li>`).join('');
    return `<article class="adjustment"><h4>${escapeHtml(adjustment.title)}</h4>${valueHtml}${steps ? `<ol>${steps}</ol>` : ''}${adjustment.tool ? `<div class="tool">🔧 ${escapeHtml(demo ? 'ДЕМО' : adjustment.tool)}</div>` : ''}${norms.map(norm => `<div class="metadata">Источник: ${escapeHtml(norm.source?.document || 'проверенный источник')}${norm.source?.page_start ? ` · стр. ${escapeHtml(norm.source.page_start)}` : ''}${norm.source?.quote ? ` · «${escapeHtml(norm.source.quote)}»` : ''}</div>`).join('')}</article>`;
  }).join('');
  const mistakes = asArray(node.mistakes).map(item => `<li>${escapeHtml(item.description)}${item.consequence ? ` — ${escapeHtml(item.consequence)}` : ''}</li>`).join('');
  const related = asArray(node.related_node_ids).map(id => findNode(machine, id)).filter(Boolean).map(relatedNode => `<article class="list-item" data-action="open-node" data-id="${escapeHtml(relatedNode.id)}"><div class="num">${escapeHtml(relatedNode.number)}</div><div><h4>${escapeHtml(relatedNode.title)}</h4></div><span class="arrow">›</span></article>`).join('');
  return `${demo ? '<div class="demo-banner">ДЕМО · нет технических рекомендаций</div>' : ''}<div class="node-head"><div class="num">${escapeHtml(node.number)}</div><h2>${escapeHtml(node.title)}</h2></div><article class="card"><div class="section" style="margin-top:0">Зачем это нужно</div><p class="what">${escapeHtml(node.purpose)}</p>${demo ? '<p class="metadata">Эта карточка — учебный макет, а не сведения из паспорта.</p>' : ''}</article><div class="section">Регулировки</div>${adjustments || `<article class="adjustment"><h4>Проверенные сведения</h4><div class="value demo">${demo ? 'ДЕМО' : 'НЕТ ДАННЫХ'}</div><p>${demo ? 'ДЕМО: содержание появится только после проверки источника.' : 'Проверенная инструкция пока не опубликована.'}</p></article>`}${demo ? '<div class="warnbox">⚠️ Все названия и тексты этой машины показаны только как ДЕМО. Не выполняйте по ним регулировку.</div>' : ''}${mistakes ? `<div class="errbox"><b>Типичные ошибки</b><ul>${mistakes}</ul></div>` : ''}${related ? `<div class="section">Связанные узлы</div>${related}` : ''}<article class="card"><b style="font-size:14px">Помогла карточка?</b><div class="fb"><button data-action="feedback" data-kind="node" data-id="${escapeHtml(node.id)}" data-helpful="true">👍 Да</button><button data-action="feedback" data-kind="node" data-id="${escapeHtml(node.id)}" data-helpful="false">👎 Нет</button></div><p class="metadata">Ответ сохранится на устройстве и отправится серверу при подключении.</p></article><button class="btn ghost" data-action="ask" data-context="node">Задать вопрос по карточке</button>`;
}

function renderProblemScreen() {
  const machine = findMachine();
  const problem = findProblem(machine);
  if (!problem) return '<div class="catalog-empty">Проблема не найдена в сохранённой базе.</div>';
  const demo = isDemo(machine);
  const steps = asArray(problem.steps).map((step, index) => {
    const linkedNode = step.linked_node_id ? findNode(machine, step.linked_node_id) : null;
    return `<article class="card step"><div class="step-n">${index + 1}</div><div><p>${escapeHtml(step.instruction)}</p>${linkedNode ? `<span class="link" data-action="open-node" data-id="${escapeHtml(linkedNode.id)}">→ Узел ${escapeHtml(linkedNode.number)}: ${escapeHtml(linkedNode.title)}</span>` : ''}</div></article>`;
  }).join('');
  return `${demo ? '<div class="demo-banner">ДЕМО · пример симптома, не диагностика</div>' : ''}<article class="card"><h2 style="font-size:16px;line-height:1.35;margin:0">${escapeHtml(problem.title)}</h2><p class="hint" style="margin:7px 0 0">${escapeHtml(problem.symptom)}</p><p class="metadata">Проверяйте по шагам только по подтверждённым данным.</p></article>${steps || '<div class="catalog-empty">Шаги проверки не опубликованы.</div>'}<article class="card"><b style="font-size:14px">Проблема решена?</b><div class="fb"><button data-action="feedback" data-kind="problem" data-id="${escapeHtml(problem.id)}" data-helpful="true">✅ Да</button><button data-action="feedback" data-kind="problem" data-id="${escapeHtml(problem.id)}" data-helpful="false">❌ Нет</button></div><p class="metadata">Ответ сохранится в очереди и отправится при подключении.</p></article><button class="btn ghost" data-action="ask" data-context="problem">Задать вопрос экспертам</button>`;
}

function formatDate(value) {
  if (!value) return 'Ещё не синхронизировалось';
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? 'Дата не определена' : new Intl.DateTimeFormat('ru-RU', { dateStyle: 'medium', timeStyle: 'short' }).format(date);
}
function renderSettings() {
  const queueCount = appState.feedbackQueue.length + appState.questionQueue.length;
  return `<h2 class="screen-title">Настройки</h2><p class="hint">Адрес локального компьютера-сервера укажите из окна КАЛИБР. Телефон и компьютер должны быть в одной сети.</p><article class="card"><label class="field"><span>Адрес сервера</span><input id="serverUrl" type="url" inputmode="url" placeholder="http://192.168.…:8765" value="${escapeHtml(appState.serverUrl)}" autocomplete="url"></label><p class="metadata">Пример: http://192.168.1.20:8765 · в мобильном приложении не указывайте 127.0.0.1.</p><div class="sync-actions"><button class="btn ghost" data-action="check-server" ${ui.syncBusy ? 'disabled' : ''}>Проверить связь</button><button class="btn" data-action="sync" ${ui.syncBusy ? 'disabled' : ''}>${ui.syncBusy ? 'Синхронизация…' : 'Синхронизировать'}</button></div><div class="result-msg ${ui.messageKind === 'bad' ? 'bad' : ui.messageKind === 'good' ? 'good' : ''}">${escapeHtml(ui.message || '')}</div></article><article class="card"><div class="section" style="margin-top:0">Состояние устройства</div><div class="settings-row"><span>Локальное хранилище</span><strong>${escapeHtml(storageLabel)}</strong></div><div class="settings-row"><span>Последняя синхронизация</span><strong>${escapeHtml(formatDate(appState.lastSync))}</strong></div><div class="settings-row"><span>Состояние каталога</span><strong>${navigator.onLine === false ? 'Автономный режим' : 'Карточки сохранены'}</strong></div><div class="settings-row"><span>Ожидают отправки</span><strong class="queue-count">${queueCount}</strong></div><div class="settings-row"><span>Вопросы</span><strong>${appState.questionQueue.length}</strong></div><div class="settings-row"><span>Отзывы «Помогло?»</span><strong>${appState.feedbackQueue.length}</strong></div></article><div class="notice">Каталог и очередь сохраняются на этом устройстве. Отзывы и вопросы отправляются только на указанный сервер; сетевых сервисов или платных API нет.</div>`;
}

function getApiUrl(path) {
  if (import.meta.env.DEV && !isNative) return `${API_PREFIX}${path}`;
  const server = appState.serverUrl.trim().replace(/\/+$/, '');
  if (!server) throw new Error('Сначала укажите адрес сервера в настройках.');
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
async function flushQueues() {
  for (const entry of [...appState.feedbackQueue]) {
    const response = await apiFetch('/feedback', { method: 'POST', body: JSON.stringify(entry) });
    await responseJson(response);
    appState.feedbackQueue = appState.feedbackQueue.filter(item => item.client_event_id !== entry.client_event_id);
    await persist();
  }
  for (const entry of [...appState.questionQueue]) {
    const response = await apiFetch('/questions', { method: 'POST', body: JSON.stringify(entry) });
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
    if (syncResult.changed && syncResult.catalog) appState.catalog = syncResult.catalog;
    appState.revision = syncResult.revision;
    appState.lastSync = new Date().toISOString();
    await persist();
    await flushQueues();
    const queued = appState.feedbackQueue.length + appState.questionQueue.length;
    setMessage(queued ? `Каталог обновлён; не отправлено: ${queued}. Повторим при связи.` : 'Синхронизация завершена. Каталог и очередь обновлены.', 'good');
  } catch (error) {
    setMessage(error.message, 'bad');
  } finally {
    ui.syncBusy = false;
    render();
  }
}

async function queueFeedback(targetType, targetId, helpful) {
  appState.feedbackQueue.push({ client_event_id: newEventId(), target_type: targetType, target_id: Number(targetId), helpful: helpful === 'true' });
  await persist();
  setMessage('Ответ сохранён на устройстве и будет отправлен при подключении.', 'good');
  if (navigator.onLine !== false && (appState.serverUrl || (import.meta.env.DEV && !isNative))) {
    try { await flushQueues(); setMessage('Спасибо! Ответ отправлен серверу.', 'good'); }
    catch { setMessage('Спасибо! Ответ останется в очереди до связи с сервером.', 'good'); }
  }
  if (ui.screen === 'settings') render();
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
  if (node) context.node_id = node.id;
  if (problem) context.problem_id = problem.id;
  appState.questionQueue.push({ client_event_id: newEventId(), machine_id: machine?.id ?? null, question, context });
  await persist();
  closeModal();
  setMessage('Вопрос сохранён. Он отправится при подключении к серверу.', 'good');
  if (navigator.onLine !== false && (appState.serverUrl || (import.meta.env.DEV && !isNative))) {
    try { await flushQueues(); setMessage('Вопрос отправлен на сервер.', 'good'); }
    catch { setMessage('Вопрос останется в очереди до связи с сервером.', 'good'); }
  }
  if (ui.screen === 'settings') render();
}

function openPhotoDemo() {
  const demoMachine = asArray(appState.catalog.demo_machines)[0];
  const nodes = asArray(demoMachine?.nodes);
  openModal(`<h3>📷 Поиск узла по фото</h3><p>Распознавание камеры пока не реализовано. Ниже — только демонстрация перехода к карточке; фотография не загружается и не анализируется.</p><div class="photo-demo">ДЕМО · выбрать пример</div>${nodes.slice(0, 1).map(node => `<div class="option" data-action="photo-match" data-machine-id="${escapeHtml(demoMachine.id)}" data-id="${escapeHtml(node.id)}">🟢 ${escapeHtml(node.title)} · ДЕМО</div>`).join('')}<div class="option" data-action="photo-miss">🔴 Машина отсутствует в каталоге · ДЕМО</div><button class="btn ghost" data-action="close-modal">Отмена</button>`);
}

async function handleAction(action, element) {
  if (action === 'back') goBack();
  else if (action === 'home') setScreen('home');
  else if (action === 'catalog') setScreen('catalog');
  else if (action === 'settings') setScreen('settings');
  else if (action === 'open-machine') { ui.tab = 'scheme'; go('machine', { machineId: element.dataset.id }); }
  else if (action === 'open-node') go('node', { nodeId: element.dataset.id });
  else if (action === 'open-problem') go('problem', { problemId: element.dataset.id });
  else if (action === 'open-search-result') {
    if (element.dataset.kind === 'node') go('node', { machineId: element.dataset.machineId, nodeId: element.dataset.id });
    else go('problem', { machineId: element.dataset.machineId, problemId: element.dataset.id });
  } else if (action === 'tab') { ui.tab = element.dataset.tab; render(); }
  else if (action === 'settings') setScreen('settings');
  else if (action === 'photo') openPhotoDemo();
  else if (action === 'close-modal') closeModal();
  else if (action === 'ask') openQuestion(element.dataset.context);
  else if (action === 'feedback') await queueFeedback(element.dataset.kind, element.dataset.id, element.dataset.helpful);
  else if (action === 'check-server') await checkServer();
  else if (action === 'sync') await syncNow();
  else if (action === 'photo-match') { const machineId = element.dataset.machineId; const nodeId = element.dataset.id; closeModal(); ui.machineId = machineId; go('node', { machineId, nodeId }); }
  else if (action === 'photo-miss') openModal('<h3>🤷 Не распознано · ДЕМО</h3><p>Это демонстрационная заглушка. Фото не анализировалось, и приложение не делает выводов о машине.</p><button class="btn" data-action="close-modal">Понятно</button><button class="btn ghost" data-action="ask" data-context="search">Попросить добавить машину</button>');
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
  mergeSavedState(await storage.load());
  render();
  await setupNetworkListener();
  if (appState.serverUrl && navigator.onLine !== false) {
    void syncNow();
  }
}

void boot();
