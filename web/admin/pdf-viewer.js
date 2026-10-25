(() => {
  const $ = id => document.getElementById(id);
  function setStatus(text, error = false) {
    $('status').textContent = text;
    $('status').style.color = error ? '#b3261e' : '#718077';
  }
  function pdfUrl(raw, page) {
    const url = new URL(raw, window.location.origin);
    if (url.origin !== window.location.origin || !/^\/api\/admin\/documents\/\d+\/file$/.test(url.pathname)) {
      throw new Error('Можно открыть только PDF из локальной базы Kolibri.');
    }
    url.hash = `page=${Math.max(1, Number(page) || 1)}`;
    return url;
  }
  function open() {
    try {
      const url = pdfUrl($('file-url').value.trim(), $('page-number').value);
      $('pdf-frame').src = url.href;
      $('pdf-frame').hidden = false;
      $('download').href = url.pathname;
      $('download').hidden = false;
      setStatus(`Открываем PDF, страница ${Math.max(1, Number($('page-number').value) || 1)}. Используется встроенный просмотрщик браузера.`);
    } catch (error) { setStatus(error.message, true); }
  }
  $('open').addEventListener('click', open);
  const params = new URLSearchParams(window.location.search);
  if (params.has('file')) $('file-url').value = params.get('file');
  if (params.has('page')) $('page-number').value = params.get('page');
  if (params.has('quote')) {
    $('quote-text').textContent = params.get('quote');
    $('quote-box').hidden = false;
  }
  if (params.has('file')) open();
})();
