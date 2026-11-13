import { Capacitor } from '@capacitor/core';
import './stand.css';
import './machine.css';
import defaultCatalog from './catalog.json';
import { adjustments } from './wizard.js';
import { partGroups, partAt, partTitle, partSearchText, normalizeParts, renderParts, renderPartCard, renderFigureZoom } from './parts.js';
import './parts.css';
import { guideFor, guideSearchText } from './guides.js';
import { renderGeneratedParts, renderGeneratedPart, generatedPartAt } from './generated-data.js';
import './guides.css';

import machinePhoto from './kolibri-grn471.jpg';

const catalog = window.__KOLIBRI_CATALOG__ || defaultCatalog;

const mediaAssets = import.meta.glob('./media/*.{png,jpg,jpeg,webp,svg}', { eager: true, query: '?inline', import: 'default' });

const ui = {
  screen: 'home',
  machineId: null,
  searchQuery: '',
  tab: 'adjustments',
  adjustmentId: null,
  step: 0,
  partsFilter: '',
  partsGroup: null,
  partPosition: 1,
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
function mediaFor(key) {
  const value = findMachine()?.media?.[key];
  if (typeof value === 'string' && value.startsWith('data:')) return value;
  if (typeof value === 'string') return mediaAssets[`./media/${value}`] || null;
  return null;
}
function currentAdjustments() { return findMachine()?.adjustments || (findMachine()?.generated ? [] : adjustments); }
function currentPartAt(group, position) { return findMachine()?.generated ? generatedPartAt(findMachine(), group, position) : partAt(group, position); }
function partsView() { return findMachine()?.generated ? renderGeneratedParts(findMachine(), ui.partsFilter, ui.partsGroup, mediaFor) : renderParts(ui.partsFilter, ui.partsGroup, mediaFor, findMachine()?.partOverrides || {}); }
function titleForScreen() {
  const machine = findMachine();
  if (ui.screen === 'machine' || ui.screen === 'wizard' || ui.screen === 'part') return [machine?.model || 'Машина', machine?.machine_type || 'Карточка машины'];
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
  $('#back').classList.toggle('show', ui.screen === 'machine' || ui.screen === 'wizard' || ui.screen === 'part');
  Object.assign($('#app').dataset,{machineId:ui.machineId || '',tab:ui.tab || '',screen:ui.screen,adjustmentId:ui.adjustmentId || '',step:String(ui.step),group:ui.partsGroup || '',position:String(ui.partPosition)});
  $('#app').classList.toggle('machine-screen', ui.screen === 'machine' || ui.screen === 'wizard' || ui.screen === 'part');
  const main = $('#main');
  if (ui.screen === 'home') main.innerHTML = renderHome();
  else if (ui.screen === 'machine') main.innerHTML = renderMachine();
  else if (ui.screen === 'wizard') main.innerHTML = renderWizard();
  else if (ui.screen === 'part') main.innerHTML = findMachine()?.generated ? renderGeneratedPart(findMachine(), ui.partsGroup, ui.partPosition, mediaFor) : renderPartCard(ui.partsGroup, ui.partPosition, mediaFor(`parts/${ui.partsGroup}/${ui.partPosition}`), findMachine()?.partOverrides || {});
  main.scrollTop = 0;
}

function searchIndex() {
  const items = [];
  for (const machine of machineList()) {
    for (const section of machine.sections) {
      for (const item of section.items) {
        const adjustment = section.id === 'adjustments' ? (machine.adjustments || (machine.generated ? [] : adjustments)).find(entry => entry.id === item.id) : null;
        const guide = machine.generated ? item : { ...(guideFor(item.id) || item), ...(machine.guideOverrides?.[item.id] || {}) };
        const text = [item.badge, item.tool, item.text, ...(item.steps || []), ...(item.rows || []).flatMap(row => [row.label, row.value]), ...(adjustment?.steps || []).flatMap(step => [step.title, step.text]), guideSearchText(guide)].filter(Boolean).join(' ');
        items.push({ sectionId: section.id, itemId: item.id, machineId: machine.id, type: section.title, title: adjustment?.title || guide?.title || item.title, text, snippet: adjustment?.subtitle || guide?.subtitle || guide?.reason || item.badge || item.text || item.tool || '' });
      }
    }
    for (const group of (machine.generated ? machine.partGroups || [] : partGroups)) {
      for (const baseItem of group.items) {
        const item={...baseItem,...machine.partOverrides?.[group.id]?.items?.[baseItem.position]};
        items.push({ sectionId: 'parts', itemId: String(item.position), groupId: group.id, machineId: machine.id, type: `Детали · ${group.title}`, title: machine.generated ? item.name : partTitle(group, item), text: machine.generated ? [item.name,item.designation,item.note].join(' ') : partSearchText(group, item), snippet: `Поз. ${item.position} · ${item.designation || item.name}` });
      }
    }
  }
  return items;
}
function normalize(text) { return normalizeParts(text); }
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
      const stem = /^[а-я]+$/.test(word) && word.length > 5 ? word.slice(0, -2) : word;
      if (normalize(item.title).includes(stem)) score += 4;
      else if (text.includes(stem)) score += 1;
    }
    return { ...item, score };
  }).filter(item => item.score > 0).sort((a, b) => b.score - a.score);
  if (!results.length) {
    container.innerHTML = `<div class="empty">В базе этого нет. Приложение не будет придумывать ответ.</div>`;
    return;
  }
  container.innerHTML = results.map(item => `<article class="search-result" data-action="open-search-result" data-section-id="${escapeHtml(item.sectionId)}" data-item-id="${escapeHtml(item.itemId)}" data-machine-id="${escapeHtml(item.machineId)}" data-group-id="${escapeHtml(item.groupId || '')}"><div class="type">${escapeHtml(item.type)}</div><h4>${escapeHtml(item.title)}</h4><p>${escapeHtml(item.snippet)}</p></article>`).join('');
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

function renderItem(item, sectionId) {
  const guide = findMachine()?.generated ? item : { ...(guideFor(item.id) || item), ...(findMachine()?.guideOverrides?.[item.id] || {}) };
  if (!guide) return '';
  const number = item.number ? `<span class="machine-item-number">${escapeHtml(item.number)}</span>` : '';
  const subtitle = guide.subtitle ? `<small>${escapeHtml(guide.subtitle)}</small>` : '';
  const custom = mediaFor(`${sectionId}/${item.id}`);
  const sources = guide.images?.length ? guide.images : custom ? [''] : [];
  const images = sources.map((image, i) => {
    const replacement = i === 0 ? custom : null;
    const visual = replacement ? `<img src="${escapeHtml(replacement)}" alt="${escapeHtml(guide.title)}" data-action="zoom-image">` : image;
    return `<figure class="guide-figure">${visual}${guide.imageCaptions?.[i] ? `<figcaption>${escapeHtml(guide.imageCaptions[i])}</figcaption>` : ''}</figure>`;
  }).join('');
  const reason = guide.reason ? `<p class="guide-reason"><strong>Возможная причина:</strong> ${escapeHtml(guide.reason)}</p>` : '';
  const text = guide.text ? `${guide.summary ? `<p><strong>Кратко, ИИ: </strong>${escapeHtml(guide.summary)}</p>` : ''}<p>${item.number && !findMachine()?.generated ? '<strong>Простыми словами: </strong>' : ''}${escapeHtml(guide.text)}</p>` : '';
  const steps = guide.steps ? `<ol>${guide.steps.map(step => `<li>${escapeHtml(step)}</li>`).join('')}</ol>` : '';
  const rows = guide.rows ? `<div class="machine-specs">${guide.rows.map(([label,value]) => `<div class="settings-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join('')}</div>` : '';
  const links = guide.links ? `<div class="guide-links">${guide.links.map(link => `<button type="button" data-action="guide-link" data-kind="${escapeHtml(link.kind)}" data-id="${escapeHtml(link.id)}" data-position="${escapeHtml(link.position || '')}" data-section="${escapeHtml(link.section || '')}">${escapeHtml(link.label)}</button>`).join('')}</div>` : '';
  return `<article class="machine-item${item.id === 'safety' ? ' machine-item-safety' : ''}${item.number ? ' machine-node' : ''}" data-item="${escapeHtml(item.id)}"><button class="machine-item-toggle" type="button" data-action="toggle-item" aria-expanded="false">${number}<span class="machine-item-title"><strong>${escapeHtml(guide.title)}</strong>${subtitle}</span><span class="machine-chevron" aria-hidden="true"></span></button><div class="machine-item-panel" aria-hidden="true" inert><div class="machine-item-inner">${images}${reason}${text}${steps}${rows}${links}${images ? '<p class="guide-disclaimer">Схема условная. Перед работой сверьтесь с руководством производителя.</p>' : ''}</div></div></article>`;
}
const tabs = [
  ['adjustments', 'Регулировки'],
  ['parts', 'Детали'],
  ['maintenance', 'ТО и смазка'],
  ['nodes', 'Узлы'],
  ['storage', 'Хранение'],
  ['specs', 'ТТХ и агрегатирование'],
  ['problems', 'Неисправности и ТБ'],
];

function renderMachine() {
  const machine = findMachine();
  if (!machine) return '<div class="catalog-empty">Машина не найдена.</div>';
  const photoSource=mediaFor('photo') || (machine.generated ? null : machinePhoto);
  const photo = photoSource ? `<button class="scheme machine-photo" data-action="open-photo" aria-label="Открыть фотографию крупно"><img src="${escapeHtml(photoSource)}" alt="${escapeHtml(machine.model)}"></button>` : '<div class="scheme machine-photo empty">Фото машины не загружено</div>';
  const tabbar = `<div class="tabs" role="tablist" aria-label="Разделы карточки">${tabs.map(([id, title]) => `<button type="button" role="tab" id="tab-${id}" data-action="select-tab" data-tab="${id}" aria-controls="machine-panel" aria-selected="${ui.tab === id}" class="${ui.tab === id ? 'on' : ''}">${title}</button>`).join('')}</div>`;
  let content;
  if (ui.tab === 'adjustments') {
    content = `<div class="adjustment-list">${currentAdjustments().map((item, index) => `<button type="button" class="adjustment-choice" data-action="open-adjustment" data-id="${item.id}"><span class="choice-number">${index + 1}</span><span class="choice-copy"><strong>${escapeHtml(item.title)}</strong><small>${escapeHtml(item.subtitle)}</small></span><span class="choice-arrow" aria-hidden="true">›</span></button>`).join('')}</div>`;
  } else if (ui.tab === 'parts') {
    content = partsView();
  } else {
    const sectionId = ui.tab === 'problems' ? 'faults' : ui.tab;
    const section = machine.sections.find(entry => entry.id === sectionId);
    const items = sectionId === 'faults' && section ? [...section.items].sort((a, b) => (a.id === 'safety' ? -1 : b.id === 'safety' ? 1 : 0)) : section?.items;
    content = items ? `<div class="machine-items">${items.map(item => renderItem(item, sectionId)).join('')}</div>` : '';
  }
  return `${photo}${tabbar}<section id="machine-panel" class="machine-tab-panel" role="tabpanel" aria-labelledby="tab-${ui.tab}">${content}</section>`;
}

function renderWizard() {
  const list = currentAdjustments();
  const index = list.findIndex(item => item.id === ui.adjustmentId);
  const item = list[index];
  if (!item) return '<div class="catalog-empty">Регулировка не найдена.</div>';
  const step = item.steps[ui.step];
  const image = mediaFor(`adjustments/${item.id}/${ui.step}`);
  const visual = image ? `<img src="${escapeHtml(image)}" alt="${escapeHtml(step.title)}" data-action="zoom-image">` : (step.image || '<div class="empty">Схема не загружена</div>');
  const last = ui.step === item.steps.length - 1;
  const next = list[index + 1];
  return `<div class="wizard"><button type="button" class="wizard-list-link" data-action="wizard-list">‹ Все регулировки</button><div class="wizard-kicker">РЕГУЛИРОВКА ${index + 1} ИЗ ${list.length}</div><h2>${escapeHtml(item.title)}</h2><div class="wizard-progress"><span>Шаг ${ui.step + 1} из ${item.steps.length}</span><span>${item.steps.map((_, i) => `<i class="${i <= ui.step ? 'done' : ''}"></i>`).join('')}</span></div><div class="wizard-image">${visual}</div><div class="diagram-legend"><span>Действие</span><span>Важно</span><span>Нельзя</span></div><h3>${escapeHtml(step.title)}</h3>${step.summary ? `<p class="wizard-text"><strong>Пояснение ИИ: </strong>${escapeHtml(step.summary)}</p>` : ''}<p class="wizard-text">${escapeHtml(step.text)}</p><p class="wizard-note">Схема условная. Перед работой сверьтесь с руководством производителя.</p><div class="wizard-controls"><button type="button" class="wizard-back" data-action="wizard-back">← Назад</button>${last ? `<button type="button" class="wizard-next" data-action="${next ? 'wizard-next-adjustment' : 'wizard-finish'}">${next ? `Следующая регулировка: ${escapeHtml(next.title)} →` : 'Завершить регулировки ✓'}</button>` : '<button type="button" class="wizard-next" data-action="wizard-next-step">Дальше →</button>'}</div></div>`;
}

function setAccordionOpen(root, open) {
  const button = root.firstElementChild;
  const panel = root.children[1];
  root.classList.toggle('is-open', open);
  button.setAttribute('aria-expanded', String(open));
  panel.setAttribute('aria-hidden', String(!open));
  panel.toggleAttribute('inert', !open);
}
function toggleItem(item, shouldOpen = !item.classList.contains('is-open')) {
  for (const other of item.closest('.machine-items').querySelectorAll('.machine-item.is-open')) {
    if (other !== item) setAccordionOpen(other, false);
  }
  setAccordionOpen(item, shouldOpen);
}
function openSearchTarget(sectionId, itemId) {
  if (sectionId === 'adjustments') {
    const item = currentAdjustments().find(entry => entry.id === itemId);
    if (!item) return;
    ui.adjustmentId = item.id;
    ui.step = 0;
    setScreen('wizard');
    return;
  }
  ui.tab = sectionId === 'faults' ? 'problems' : sectionId;
  render();
  const item = [...$('#main').querySelectorAll('.machine-item')].find(element => element.dataset.item === itemId);
  if (!item) return;
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
function refreshParts(keepFocus = false) {
  $('#app').dataset.group = ui.partsGroup || '';
  const current = $('.parts-view');
  if (!current) return;
  const main = $('#main');
  const top = main.scrollTop;
  const cursor = keepFocus ? $('#partsFilter').selectionStart : null;
  const holder = document.createElement('div');
  holder.innerHTML = partsView();
  current.replaceWith(holder.firstElementChild);
  main.scrollTop = top;
  if (keepFocus) {
    const input = $('#partsFilter');
    input.focus({ preventScroll: true });
    if (cursor !== null) input.setSelectionRange(cursor, cursor);
  }
}
function returnToPartList() {
  ui.tab = 'parts';
  ui.partsFilter = '';
  setScreen('machine');
  const target = $(`.part-row[data-group="${ui.partsGroup}"][data-position="${ui.partPosition}"]`);
  if (!target) return;
  const main = $('#main');
  const top = main.scrollTop + target.getBoundingClientRect().top - main.getBoundingClientRect().top - 16;
  main.scrollTop = Math.max(0, top);
  target.focus({ preventScroll: true });
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
  $('#sheet').innerHTML = `<h3>${escapeHtml(findMachine()?.model || 'Машина')}</h3><img src="${escapeHtml(mediaFor('photo') || (findMachine()?.generated ? '' : machinePhoto))}" alt="${escapeHtml(findMachine()?.model || 'Машина')}"><button class="btn ghost" data-action="close-modal">Закрыть</button>`;
  modal.classList.add('show');
  modal.setAttribute('aria-hidden', 'false');
  $('[data-action="close-modal"]').focus();
}
function handleAction(action, element) {
  if (action === 'back') {
    if (ui.screen === 'part') returnToPartList();
    else if (ui.screen === 'wizard') { ui.tab = 'adjustments'; setScreen('machine'); }
    else setScreen('home');
  } else if (action === 'home') setScreen('home');
  else if (action === 'open-photo') openPhoto();
  else if (action === 'close-modal') closeModal();
  else if (action === 'zoom-image') { const modal=$('#modal'); $('#sheet').innerHTML=`<img src="${escapeHtml(element.currentSrc || element.src)}" alt="${escapeHtml(element.alt || 'Схема')}"><button class="btn ghost" data-action="close-modal">Закрыть</button>`;modal.classList.add('show');modal.setAttribute('aria-hidden','false'); }
  else if (action === 'open-machine') { ui.tab = 'adjustments'; go('machine', { machineId: element.dataset.id }); }
  else if (action === 'select-tab') { ui.tab = element.dataset.tab; ui.partsFilter = ''; ui.partsGroup = null; render(); }
  else if (action === 'toggle-parts-group') { ui.partsGroup = ui.partsGroup === element.dataset.group ? null : element.dataset.group; refreshParts(); }
  else if (action === 'open-part') {
    const groupId = element.dataset.group;
    const position = Number(element.dataset.position);
    if (!currentPartAt(groupId, position)) return;
    if ($('#modal').classList.contains('show')) closeModal();
    ui.partsGroup = groupId; ui.partPosition = position; ui.partsFilter = ''; setScreen('part');
  } else if (action === 'zoom-figure') {
    const modal = $('#modal');
    $('#sheet').innerHTML = renderFigureZoom(element.dataset.group, element.dataset.figure, Number(element.dataset.selected));
    modal.classList.add('show'); modal.setAttribute('aria-hidden', 'false');
    $('[data-action=close-modal]')?.focus();
  } else if (action === 'back-parts') returnToPartList();
  else if (action === 'part-prev' || action === 'part-next') {
    const group=findMachine()?.generated ? findMachine().partGroups.find(entry=>entry.id===ui.partsGroup) : null;
    const index=group?.items.findIndex(item=>item.position===ui.partPosition);
    const position = group ? group.items[index + (action === 'part-next' ? 1 : -1)]?.position : ui.partPosition + (action === 'part-next' ? 1 : -1);
    if (position !== undefined && currentPartAt(ui.partsGroup, position)) { ui.partPosition = position; render(); }
  }
  else if (action === 'open-adjustment') { ui.adjustmentId = element.dataset.id; ui.step = 0; setScreen('wizard'); }
  else if (action === 'wizard-list' || action === 'wizard-finish') { ui.tab = 'adjustments'; setScreen('machine'); }
  else if (action === 'wizard-back') {
    if (ui.step > 0) { ui.step -= 1; render(); }
    else { ui.tab = 'adjustments'; setScreen('machine'); }
  } else if (action === 'wizard-next-step') { ui.step += 1; render(); }
  else if (action === 'wizard-next-adjustment') {
    const list = currentAdjustments();
    const index = list.findIndex(item => item.id === ui.adjustmentId);
    if (list[index + 1]) { ui.adjustmentId = list[index + 1].id; ui.step = 0; render(); }
  } else if (action === 'guide-link') {
    const { kind, id, position, section } = element.dataset;
    if (kind === 'adjustment' && adjustments.some(item => item.id === id)) { ui.adjustmentId = id; ui.step = 0; setScreen('wizard'); }
    else if (kind === 'parts' && partGroups.some(group => group.id === id)) {
      ui.tab = 'parts'; ui.partsGroup = id; ui.partsFilter = ''; setScreen('machine');
      const group = $(`.parts-group[data-group="${id}"]`);
      if (group) { const main = $('#main'); main.scrollTop = Math.max(0, main.scrollTop + group.getBoundingClientRect().top - main.getBoundingClientRect().top - 8); }
    } else if (kind === 'part' && partAt(id, position)) {
      ui.partsGroup = id; ui.partPosition = Number(position); ui.partsFilter = ''; ui.tab = 'parts'; setScreen('part');
    } else if (kind === 'guide') openSearchTarget(section, id);
  } else if (action === 'toggle-item') toggleItem(element.closest('.machine-item'));
  else if (action === 'open-search-result') {
    const { machineId, sectionId, itemId, groupId } = element.dataset;
    if (sectionId === 'parts') {
      ui.machineId = machineId; if (!currentPartAt(groupId, itemId)) return; ui.partsGroup = groupId; ui.partPosition = Number(itemId); ui.partsFilter = ''; ui.tab = 'parts'; setScreen('part');
    } else { go('machine', { machineId }); openSearchTarget(sectionId, itemId); }
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
      ui.tab = 'adjustments';
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
  if (event.target.id === 'partsFilter') { ui.partsFilter = event.target.value; refreshParts(true); }
  if (event.target.id === 'homeSearch') {
    ui.searchQuery = event.target.value;
    renderSearchResults(event.target.value, 'homeResults');
  }
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && $('#modal').classList.contains('show')) closeModal();
  const svgAction = event.target.closest('.figure-hotspot');
  if (svgAction && ['Enter', ' '].includes(event.key)) { event.preventDefault(); handleAction('open-part', svgAction); return; }
  const tab = event.target.closest('[role=tab]');
  if (!tab || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
  event.preventDefault();
  const index = tabs.findIndex(([id]) => id === tab.dataset.tab);
  const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
  ui.tab = tabs[next][0];
  ui.partsFilter = ''; ui.partsGroup = null;
  render();
  const selected = $('#tab-' + ui.tab);
  selected.focus({ preventScroll: true });
});

setupPreviewStand();
render();
appReadyForIntro = true;
dismissIntroWhenReady();
