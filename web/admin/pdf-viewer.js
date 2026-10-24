import { GlobalWorkerOptions, TextLayer, getDocument } from '/admin/static/admin/vendor/pdfjs/build/pdf.min.mjs';

const base = '/admin/static/admin/vendor/pdfjs/';
GlobalWorkerOptions.workerSrc = `${base}build/pdf.worker.min.mjs`;
const $ = id => document.getElementById(id);
let documentHandle = null;
let renderedPage = null;
let currentPage = 1;
let zoom = 1;
let highlightedQuote = '';

function setStatus(message, error = false) {
  $('status').textContent = message;
  $('status').style.color = error ? '#b3261e' : '#718077';
}

function allowedPdfUrl(raw) {
  const url = new URL(raw, window.location.origin);
  if (url.origin !== window.location.origin || !url.pathname.startsWith('/api/admin/documents/')) {
    throw new Error('Открывать можно только PDF, загруженные в локальную базу КАЛИБР.');
  }
  return url.href;
}

function compactText(text) {
  return Array.from(text.normalize('NFKC').toLocaleLowerCase('ru').replace(/ё/g, 'е'))
    .filter(char => /[\p{L}\p{N}]/u.test(char)).join('');
}

function highlightQuote(layer, quote) {
  const needle = compactText(quote);
  if (!needle) return false;
  const spans = layer.textDivs;
  const compact = [];
  const owner = [];
  spans.forEach((span, index) => {
    for (const char of compactText(span.textContent || '')) {
      compact.push(char);
      owner.push(index);
    }
  });
  const found = compact.join('').indexOf(needle);
  if (found < 0) return false;
  const selected = new Set(owner.slice(found, found + needle.length));
  for (const index of selected) spans[index]?.classList.add('cite-highlight');
  return true;
}

async function showPage(pageNumber) {
  if (!documentHandle) return;
  currentPage = Math.max(1, Math.min(documentHandle.numPages, Number(pageNumber) || 1));
  $('page-number').value = String(currentPage);
  $('page-total').textContent = String(documentHandle.numPages);
  if (renderedPage) renderedPage.cleanup();
  const page = await documentHandle.getPage(currentPage);
  const fit = Math.min(1.35, Math.max(.25, ($('pdf-page').parentElement.clientWidth - 28) / page.getViewport({scale: 1}).width));
  const scale = fit * zoom;
  const viewport = page.getViewport({scale});
  const canvas = $('canvas');
  const context = canvas.getContext('2d', {alpha: false});
  const outputScale = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.ceil(viewport.width * outputScale);
  canvas.height = Math.ceil(viewport.height * outputScale);
  canvas.style.width = `${Math.ceil(viewport.width)}px`;
  canvas.style.height = `${Math.ceil(viewport.height)}px`;
  context.setTransform(outputScale, 0, 0, outputScale, 0, 0);
  $('pdf-page').style.width = `${Math.ceil(viewport.width)}px`;
  $('pdf-page').style.height = `${Math.ceil(viewport.height)}px`;
  $('pdf-page').hidden = false;
  const layerContainer = $('text-layer');
  layerContainer.replaceChildren();
  layerContainer.style.width = `${Math.ceil(viewport.width)}px`;
  layerContainer.style.height = `${Math.ceil(viewport.height)}px`;
  const [, textContent] = await Promise.all([
    page.render({canvasContext: context, viewport, background: 'white'}).promise,
    page.getTextContent()
  ]);
  const layer = new TextLayer({textContentSource: textContent, container: layerContainer, viewport});
  await layer.render();
  renderedPage = page;
  $('zoom-label').textContent = `${Math.round(zoom * 100)}%`;
  if (highlightedQuote) {
    if (highlightQuote(layer, highlightedQuote)) setStatus(`Страница ${currentPage}: найденная цитата подсвечена.`);
    else setStatus(`Страница ${currentPage} открыта, но точное совпадение цитаты на ней не найдено.`, true);
  } else {
    setStatus(`Страница ${currentPage} из ${documentHandle.numPages}.`);
  }
}

async function openDocument() {
  try {
    const url = allowedPdfUrl($('file-url').value.trim());
    highlightedQuote = $('quote').value.trim();
    currentPage = Math.max(1, Number($('page-number').value) || 1);
    setStatus('Открываем PDF из локальной базы…');
    $('pdf-page').hidden = true;
    documentHandle?.destroy();
    const loadingTask = getDocument({
      url,
      cMapUrl: `${base}cmaps/`,
      cMapPacked: true,
      standardFontDataUrl: `${base}standard_fonts/`,
      wasmUrl: `${base}wasm/`
    });
    documentHandle = await loadingTask.promise;
    await showPage(currentPage);
  } catch (error) {
    setStatus(error?.message || 'Не удалось открыть PDF. Проверьте адрес и наличие документа.', true);
  }
}

$('open').addEventListener('click', openDocument);
$('previous').addEventListener('click', () => showPage(currentPage - 1));
$('next').addEventListener('click', () => showPage(currentPage + 1));
$('page-number').addEventListener('change', event => showPage(event.target.value));
$('zoom-in').addEventListener('click', () => { zoom = Math.min(2.5, zoom + .15); showPage(currentPage); });
$('zoom-out').addEventListener('click', () => { zoom = Math.max(.5, zoom - .15); showPage(currentPage); });
window.addEventListener('resize', () => { if (documentHandle) showPage(currentPage); });

const params = new URLSearchParams(window.location.search);
if (params.has('file')) $('file-url').value = params.get('file');
if (params.has('page')) $('page-number').value = params.get('page');
if (params.has('quote')) $('quote').value = params.get('quote');
if (params.has('file')) openDocument();
