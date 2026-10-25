(() => {
  const $ = id => document.getElementById(id);
  let currentImageUrl = null;
  function setStatus(text, error = false) {
    $('status').textContent = text;
    $('status').style.color = error ? '#b3261e' : '#718077';
  }
  function pdfUrl(raw) {
    const url = new URL(raw, window.location.origin);
    if (url.origin !== window.location.origin || !/^\/api\/admin\/documents\/\d+\/file$/.test(url.pathname)) {
      throw new Error('Можно открыть только PDF из локальной базы Kolibri.');
    }
    return url;
  }
  async function open() {
    try {
      const url = pdfUrl($('file-url').value.trim());
      const page = Math.max(1, Number($('page-number').value) || 1);
      const documentId = url.pathname.match(/\/documents\/(\d+)\/file$/)[1];
      const quote = $('quote-text').textContent.trim();
      const imageUrl = new URL(`/api/admin/documents/${documentId}/page/${page}/image`, window.location.origin);
      if (quote) imageUrl.searchParams.set('quote', quote);
      setStatus(`Загружаем локальную страницу ${page}…`);
      $('pdf-frame').hidden = true;
      $('page-image').hidden = true;
      const response = await fetch(imageUrl, { credentials: 'same-origin', cache: 'no-store' });
      if (!response.ok) throw new Error(`Не удалось отрисовать страницу (${response.status}).`);
      const verified = response.headers.get('X-Calibre-Quote-Verified') === '1';
      const highlighted = response.headers.get('X-Calibre-Quote-Highlighted') === '1';
      const blob = await response.blob();
      if (currentImageUrl) URL.revokeObjectURL(currentImageUrl);
      currentImageUrl = URL.createObjectURL(blob);
      $('page-image').src = currentImageUrl;
      $('page-image').hidden = false;
      $('download').href = url.pathname;
      $('download').hidden = false;
      if (quote && highlighted) setStatus(`Страница ${page}. Цитата сверена с текстом PDF и подсвечена.`);
      else if (quote && verified) setStatus(`Страница ${page}. Цитата сверена с текстом PDF, но координаты подсветки не найдены; цитата показана отдельно.`, true);
      else if (quote) setStatus(`Страница ${page}. Подсветка не подтверждена; проверьте распознавание и цитату вручную.`, true);
      else setStatus(`Страница ${page} из локального PDF.`);
    } catch (error) {
      setStatus(error.message, true);
    }
  }
  $('open').addEventListener('click', open);
  $('page-number').addEventListener('keydown', event => { if (event.key === 'Enter') open(); });
  const params = new URLSearchParams(window.location.search);
  if (params.has('file')) $('file-url').value = params.get('file');
  if (params.has('page')) $('page-number').value = params.get('page');
  if (params.has('quote')) {
    $('quote-text').textContent = params.get('quote');
    $('quote-box').hidden = false;
  }
  if (params.has('file')) open();
})();
