import { Capacitor } from '@capacitor/core';
import './stand.css';
import './machine.css';
import catalog from './catalog.json';

import machinePhoto from './kolibri-grn471.jpg';

const ui = {
  screen: 'home',
  machineId: null,
  searchQuery: '',
};

const $ = (selector, root = document) => root.querySelector(selector);
let appReadyForIntro = false;
let introAnimationFinished = false;
let introTimer;
let introPlayToken = 0;
function dismissIntroWhenReady() {
  if (!appReadyForIntro || !introAnimationFinished) return;
  $('#launchSplash')?.classList.add('dismiss');
}
function finishIntroAnimation() {
  if (introAnimationFinished) return;
  introAnimationFinished = true;
  introPlayToken += 1;
  window.clearTimeout(introTimer);
  if (launchAnimation && !launchAnimation.ended) launchAnimation.pause();
  dismissIntroWhenReady();
}
const launchAnimation = $('#launchAnimation');
if (launchAnimation) {
  launchAnimation.addEventListener('ended', finishIntroAnimation);
  launchAnimation.addEventListener('error', finishIntroAnimation);
  introTimer = window.setTimeout(finishIntroAnimation, 5500);
} else {
  introAnimationFinished = true;
}
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[char]));

function machineList() {
  return catalog.machines;
}
function findMachine(id = ui.machineId) {
  return machineList().find(machine => String(machine.id) === String(id)) ?? null;
}
function titleForScreen() {
  const machine = findMachine();
  if (ui.screen === 'machine') return [machine?.model || 'Машина', machine?.machine_type || 'Карточка машины'];
  return ['Kolibri', 'Сельхозтехника'];
}

function go(screen, values = {}) {
  Object.assign(ui, values, { screen });
  ui.searchQuery = '';
  render();
}
function setScreen(screen) {
  ui.screen = screen;
  ui.searchQuery = '';
  render();
}

function render() {
  const [title, subtitle] = titleForScreen();
  const titleElement = $('#title');
  titleElement.innerHTML = ui.screen === 'home'
    ? '<span class="logo">Kolibri</span><small>Справочник сельхозтехники</small>'
    : `${escapeHtml(title)}<small>${escapeHtml(subtitle)}</small>`;
  $('#back').classList.toggle('show', ui.screen === 'machine');
  $('#app').classList.toggle('machine-screen', ui.screen === 'machine');
  const main = $('#main');
  if (ui.screen === 'home') main.innerHTML = renderHome();
  else if (ui.screen === 'machine') main.innerHTML = renderMachine();
  main.scrollTop = 0;
}

function searchIndex() {
  const items = [];
  for (const machine of machineList()) {
    for (const section of machine.sections) {
      for (const item of section.items) {
        const text = [item.badge, item.tool, item.text, ...(item.steps || []), ...(item.rows || []).flatMap(row => [row.label, row.value])].filter(Boolean).join(' ');
        items.push({ sectionId: section.id, itemId: item.id, machineId: machine.id, type: section.title, title: item.title, text, snippet: item.badge || item.text || item.tool || '' });
      }
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
    const text = normalize(`${item.title} ${item.text} ${item.type}`);
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
  container.innerHTML = results.map(item => `<article class="search-result" data-action="open-search-result" data-section-id="${escapeHtml(item.sectionId)}" data-item-id="${escapeHtml(item.itemId)}" data-machine-id="${escapeHtml(item.machineId)}"><div class="type">${escapeHtml(item.type)}</div><h4>${escapeHtml(item.title)}</h4><p>${escapeHtml(item.snippet)}</p></article>`).join('');
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

function renderItem(item) {
  const number = item.number ? `<span class="machine-item-number">${escapeHtml(item.number)}</span>` : '';
  const badge = item.badge ? `<span class="machine-item-badge">${escapeHtml(item.badge)}</span>` : '';
  const tool = item.tool ? `<div class="machine-item-tool">${escapeHtml(item.tool)}</div>` : '';
  const steps = item.steps ? `<ol>${item.steps.map(step => `<li>${escapeHtml(step)}</li>`).join('')}</ol>` : '';
  const text = item.text ? `<p>${escapeHtml(item.text)}</p>` : '';
  const rows = item.rows ? `<div class="machine-specs">${item.rows.map(row => `<div class="settings-row"><span>${escapeHtml(row.label)}</span><strong>${escapeHtml(row.value)}</strong></div>`).join('')}</div>` : '';
  return `<article class="machine-item${item.id === 'safety' ? ' machine-item-safety' : ''}" data-item="${escapeHtml(item.id)}"><button class="machine-item-toggle" type="button" data-action="toggle-item" aria-expanded="false">${number}<span class="machine-item-title">${escapeHtml(item.title)}</span>${badge}<span class="machine-chevron" aria-hidden="true"></span></button><div class="machine-item-panel" aria-hidden="true" inert><div class="machine-item-inner">${tool}${text}${steps}${rows}</div></div></article>`;
}
function renderMachine() {
  const machine = findMachine();
  if (!machine) return '<div class="catalog-empty">Машина не найдена.</div>';
  const photo = `<button class="scheme machine-photo" data-action="open-photo" aria-label="Открыть фотографию крупно"><img src="${escapeHtml(machinePhoto)}" alt="Роторные грабли ГРН-471 Kolibri"></button>`;
  const facts = `<div class="machine-quick-facts">${machine.quick_facts.map(fact => `<span>${escapeHtml(fact)}</span>`).join('')}</div>`;
  const sections = machine.sections.map(section => `<section class="machine-section" data-section="${escapeHtml(section.id)}"><button class="machine-section-toggle" type="button" data-action="toggle-section" aria-expanded="false"><span>${escapeHtml(section.title)}</span><span class="machine-section-end"><span class="machine-section-badge">${escapeHtml(section.badge)}</span><span class="machine-chevron" aria-hidden="true"></span></span></button><div class="machine-section-panel" aria-hidden="true" inert><div class="machine-section-inner">${section.items.map(renderItem).join('')}</div></div></section>`).join('');
  return `${photo}${facts}<div class="machine-sections" aria-label="Разделы машины">${sections}</div>`;
}
function setAccordionOpen(root, open) {
  const button = root.firstElementChild;
  const panel = root.children[1];
  root.classList.toggle('is-open', open);
  button.setAttribute('aria-expanded', String(open));
  panel.setAttribute('aria-hidden', String(!open));
  panel.toggleAttribute('inert', !open);
}
function toggleSection(section, shouldOpen = !section.classList.contains('is-open')) {
  for (const other of $('#main').querySelectorAll('.machine-section')) {
    if (other !== section && other.classList.contains('is-open')) {
      setAccordionOpen(other, false);
      for (const item of other.querySelectorAll('.machine-item.is-open')) setAccordionOpen(item, false);
    }
  }
  setAccordionOpen(section, shouldOpen);
  if (!shouldOpen) for (const item of section.querySelectorAll('.machine-item.is-open')) setAccordionOpen(item, false);
}
function toggleItem(item, shouldOpen = !item.classList.contains('is-open')) {
  for (const other of item.closest('.machine-section').querySelectorAll('.machine-item.is-open')) {
    if (other !== item) setAccordionOpen(other, false);
  }
  setAccordionOpen(item, shouldOpen);
}
function openSearchTarget(sectionId, itemId) {
  const section = [...$('#main').querySelectorAll('.machine-section')].find(element => element.dataset.section === sectionId);
  if (!section) return;
  const item = [...section.querySelectorAll('.machine-item')].find(element => element.dataset.item === itemId);
  if (!item) return;
  toggleSection(section, true);
  toggleItem(item, true);
  item.classList.add('search-highlight');
  item.firstElementChild.focus({ preventScroll: true });
  window.setTimeout(() => {
    if (!item.isConnected) return;
    const main = $('#main');
    const top = main.scrollTop + item.getBoundingClientRect().top - main.getBoundingClientRect().top - 8;
    if (typeof main.scrollTo === 'function') main.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
    else main.scrollTop = Math.max(0, top);
  }, 230);
  window.setTimeout(() => item.classList.remove('search-highlight'), 2600);
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
  if (action === 'back') setScreen('home');
  else if (action === 'home') setScreen('home');
  else if (action === 'open-photo') openPhoto();
  else if (action === 'close-modal') closeModal();
  else if (action === 'open-machine') go('machine', { machineId: element.dataset.id });
  else if (action === 'toggle-section') toggleSection(element.closest('.machine-section'));
  else if (action === 'toggle-item') toggleItem(element.closest('.machine-item'));
  else if (action === 'open-search-result') {
    const { machineId, sectionId, itemId } = element.dataset;
    go('machine', { machineId });
    openSearchTarget(sectionId, itemId);
  }
}

function replayIntro() {
  if (!launchAnimation) return;
  window.clearTimeout(introTimer);
  const token = ++introPlayToken;
  introAnimationFinished = false;
  $('#launchSplash').classList.remove('dismiss');
  launchAnimation.currentTime = 0;
  const playback = launchAnimation.play();
  playback?.catch(() => {
    if (token === introPlayToken) finishIntroAnimation();
  });
  introTimer = window.setTimeout(() => {
    if (token === introPlayToken) finishIntroAnimation();
  }, 5500);
}

function setupPreviewStand() {
  if (Capacitor.isNativePlatform()) {
    document.body.classList.add('native-mode');
    return;
  }
  document.body.classList.add('browser-mode');
  const app = $('#app');
  const stand = document.createElement('div');
  stand.className = 'preview-stand';
  stand.innerHTML = `<aside class="preview-panel" aria-label="Управление стендом">
    <div class="preview-brand"><span class="preview-brand-icon" aria-hidden="true">K</span><span>KOLIBRI <small>МЕХФАК</small></span></div>
    <div class="preview-kicker"><span></span> ВЕБ-СТЕНД · OFFLINE FIRST</div>
    <h2>Kolibri — стенд<br>предпросмотра APK</h2>
    <p class="preview-description">Изучайте и отлаживайте справочник прямо в браузере. Интерфейс в макете работает так же, как на телефоне.</p>
    <a class="preview-download" href="https://github.com/Igorr112323/CALIBRE/releases/latest/download/Kolibri-Mehfak.apk"><span>Скачать актуальный APK</span><span aria-hidden="true">↓</span></a>
    <a class="preview-releases" href="https://github.com/Igorr112323/CALIBRE/releases" target="_blank" rel="noopener noreferrer">Все сборки на GitHub <span aria-hidden="true">↗</span></a>
    <div class="preview-divider"></div>
    <div class="preview-controls-title">БЫСТРАЯ ОТЛАДКА <span>01 — 03</span></div>
    <div class="preview-controls"><button type="button" data-preview="home"><span>На главный экран</span><span aria-hidden="true">↗</span></button><button type="button" data-preview="replay"><span>Повторить заставку</span><span aria-hidden="true">↻</span></button><button type="button" data-preview="skip"><span>Пропустить заставку</span><span aria-hidden="true">→</span></button></div>
    <div class="preview-note"><span class="preview-note-dot"></span><span>Живой макет · Данные встроены в приложение</span></div>
  </aside><div class="preview-device-area"><div class="preview-device-label"><span>ИНТЕРАКТИВНЫЙ МАКЕТ</span><span>390 × 844</span></div><div class="preview-device"><div class="preview-device-top"><span></span></div><div class="preview-device-screen"></div></div><p class="preview-device-caption">ПРОСМОТР ПРИЛОЖЕНИЯ <span>·</span> ANDROID</p></div>`;
  document.body.insertBefore(stand, app);
  $('.preview-device-screen', stand).append(app);
  if (launchAnimation && !introAnimationFinished && launchAnimation.paused) {
    const playback = launchAnimation.play();
    playback?.catch(() => {});
  }
  stand.addEventListener('click', event => {
    const action = event.target.closest('[data-preview]')?.dataset.preview;
    if (action === 'home') {
      if ($('#modal').classList.contains('show')) closeModal();
      ui.machineId = null;
      setScreen('home');
      finishIntroAnimation();
    } else if (action === 'replay') replayIntro();
    else if (action === 'skip') finishIntroAnimation();
  });
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

setupPreviewStand();
render();
appReadyForIntro = true;
dismissIntroWhenReady();
