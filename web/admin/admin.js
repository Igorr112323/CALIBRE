(() => {
  const $ = (selector) => document.querySelector(selector);
  const state = { machines: [], machineId: null, newFlow: false, nodes: [], documents: [], photos: [], analysisRuns: [], analysisRun: null, activeClaimIndex: null, ollamaStatus: null };
  const setupForm = $('#setup-form');
  const modelField = setupForm.elements.model;
  const fileInput = $('#manual-file');

  const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[char]);
  function citationViewerUrl(citation) {
    const url = new URL('/admin/static/admin/pdf-viewer.html', window.location.origin);
    url.searchParams.set('file', `/api/admin/documents/${Number(citation.document_id)}/file`);
    url.searchParams.set('page', String(Number(citation.page) || 1));
    if (citation.quote) url.searchParams.set('quote', citation.quote);
    return url.href;
  }
  const setStatus = (selector, message, kind = '') => {
    const element = $(selector);
    element.textContent = message;
    element.className = `status${kind ? ` ${kind}` : ''}`;
  };
  async function request(url, options = {}) {
    const response = await fetch(url, options);
    let body = {};
    try { body = await response.json(); } catch (_) { /* empty response */ }
    if (!response.ok) throw new Error(body.detail || body.message || `Ошибка ${response.status}`);
    return body;
  }
  function selectedMachine() {
    return state.machines.find((machine) => machine.id === Number(state.machineId));
  }
  function setStep(step) {
    document.querySelectorAll('.steps li').forEach((item) => {
      const value = Number(item.dataset.step);
      item.classList.toggle('current', value === step);
      item.classList.toggle('done', value < step);
    });
  }
  function setBusy(button, busy, text) {
    button.disabled = busy;
    if (busy) {
      button.dataset.label = button.innerHTML;
      button.textContent = text;
    } else if (button.dataset.label) {
      button.innerHTML = button.dataset.label;
      delete button.dataset.label;
    }
  }
  function machineCode(model) {
    const transliteration = { 'а':'A','б':'B','в':'V','г':'G','д':'D','е':'E','ё':'E','ж':'ZH','з':'Z','и':'I','й':'I','к':'K','л':'L','м':'M','н':'N','о':'O','п':'P','р':'R','с':'S','т':'T','у':'U','ф':'F','х':'H','ц':'TS','ч':'CH','ш':'SH','щ':'SHCH','ъ':'','ы':'Y','ь':'','э':'E','ю':'YU','я':'YA' };
    let suffix = model.toLowerCase().replace(/[а-яё]/g, (char) => transliteration[char] || '').toUpperCase().replace(/[^A-Z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 48) || 'MACHINE';
    let code = `KOLIBRI-${suffix}`;
    let index = 2;
    while (state.machines.some((machine) => machine.code.toLowerCase() === code.toLowerCase())) code = `KOLIBRI-${suffix}-${index++}`;
    return code;
  }
  function renderMachinePicker() {
    const wrap = $('#machine-picker-wrap');
    const select = $('#machine-select');
    wrap.hidden = !state.machines.length;
    select.innerHTML = state.machines.map((machine) => `<option value="${machine.id}">${escapeHtml(machine.manufacturer)} ${escapeHtml(machine.model)}</option>`).join('');
    if (state.machineId) select.value = String(state.machineId);
  }
  function render() {
    const machine = selectedMachine();
    const hasMachine = Boolean(machine);
    $('#wizard-panel').hidden = hasMachine && !state.newFlow;
    $('#machine-panel').hidden = !hasMachine;
    $('#content-panel').hidden = !hasMachine;
    $('#page-heading').textContent = state.newFlow ? 'Добавим новую технику' : (hasMachine ? `${machine.manufacturer} ${machine.model}` : 'Соберём базу Kolibri вместе');
    if (hasMachine) {
      $('#machine-panel [name="manufacturer"]').value = machine.manufacturer || 'Kolibri';
      $('#machine-panel [name="model"]').value = machine.model || '';
      $('#machine-panel [name="machine_type"]').value = machine.machine_type || '';
      $('#machine-panel [name="description"]').value = machine.description || '';
      modelField.parentElement.hidden = !state.newFlow;
      modelField.required = state.newFlow;
      $('#manual-state').textContent = state.documents.length ? `${state.documents.length} PDF обработано` : 'Добавьте руководство';
      $('#manual-state').classList.toggle('ready', state.documents.length > 0);
      setStep(state.documents.length ? 3 : 2);
    } else {
      modelField.parentElement.hidden = false;
      modelField.required = true;
      setStep(1);
    }
    renderMachinePicker();
    renderNodes();
    renderPhotos();
    renderDocuments();
    renderAnalysis();
    renderOllamaStatus();
  }
  async function loadMachineData() {
    if (!state.machineId) {
      state.nodes = []; state.documents = []; state.photos = []; state.analysisRuns = []; state.analysisRun = null; render(); return;
    }
    const id = encodeURIComponent(state.machineId);
    const results = await Promise.all([
      request(`/api/admin/nodes?machine_id=${id}`),
      request(`/api/admin/documents?machine_id=${id}`),
      request(`/api/admin/media?machine_id=${id}`),
      request(`/api/admin/analysis?machine_id=${id}`)
    ]);
    [state.nodes, state.documents, state.photos, state.analysisRuns] = results;
    if (state.analysisRuns.length) state.analysisRun = state.analysisRuns[0];
    else state.analysisRun = null;
    render();
  }
  async function loadMachines(preferredId = null) {
    state.machines = await request('/api/admin/machines');
    if (preferredId && state.machines.some((machine) => machine.id === Number(preferredId))) state.machineId = Number(preferredId);
    else if (state.machineId && state.machines.some((machine) => machine.id === Number(state.machineId))) state.machineId = Number(state.machineId);
    else state.machineId = state.machines[0]?.id ?? null;
    renderMachinePicker();
    await loadMachineData();
  }
  function renderNodes() {
    const list = $('#node-list');
    $('#node-count').textContent = state.nodes.length;
    const photoSelect = $('#photo-node');
    const current = photoSelect.value;
    photoSelect.innerHTML = '<option value="">Фото всей машины / без узла</option>' + state.nodes.map((node) => `<option value="${node.id}">${node.number}. ${escapeHtml(node.title)}</option>`).join('');
    if (state.nodes.some((node) => String(node.id) === current)) photoSelect.value = current;
    list.innerHTML = state.nodes.length ? state.nodes.map((node) => `
      <article class="item-row"><span class="item-number">${node.number}</span><div><strong>${escapeHtml(node.title)}</strong>${node.purpose ? `<small>${escapeHtml(node.purpose)}</small>` : '<small>Назначение можно добавить в заметке при следующем обновлении карточки.</small>'}</div></article>`).join('') : '<p class="empty">Добавьте первый узел — например, раму или колесо.</p>';
  }
  function renderPhotos() {
    const list = $('#photo-list');
    $('#photo-count').textContent = state.photos.length;
    list.innerHTML = state.photos.length ? state.photos.map((photo) => `
      <article class="photo-item"><a href="${escapeHtml(photo.url)}" target="_blank" rel="noopener"><img src="${escapeHtml(photo.url)}" alt="${escapeHtml(photo.caption || 'Фото техники')}" loading="lazy"></a><p>${escapeHtml(photo.caption || photo.filename || 'Фото')}${photo.node_id ? ' · узел' : ''}</p><button type="button" class="button button-small ${photo.sync_to_phones ? 'button-secondary' : 'button-ghost'}" data-photo-sync="${photo.id}">${photo.sync_to_phones ? 'Отключить LAN-sync' : 'Разрешить передачу по LAN'}</button><small>${photo.sync_to_phones ? 'Фото остаётся внутренним; только локальная сеть.' : 'Сейчас доступно только на ПК.'}</small></article>`).join('') : '<p class="empty">Загруженные фото появятся здесь.</p>';
  }
  function renderDocuments() {
    const list = $('#document-list');
    $('#document-count').textContent = state.documents.length;
    list.innerHTML = state.documents.length ? state.documents.map((doc) => `
      <article class="item-row"><span class="item-number">PDF</span><div><strong><a href="${escapeHtml(doc.url)}" target="_blank" rel="noopener">${escapeHtml(doc.title || doc.filename)}</a></strong><small>${doc.page_count} стр. · ${doc.pages_indexed ? 'текст проиндексирован' : 'индекс не найден'} · ${escapeHtml(doc.filename)}</small></div></article>`).join('') : '<p class="empty">Документы ещё не добавлены.</p>';
    if (state.documents.length) {
      const latest = state.documents[0];
      $('#document-summary').hidden = false;
      $('#document-summary').textContent = `Обработано страниц: ${latest.page_count}. Страниц с доступным текстом: ${latest.pages_indexed ? 'индекс готов' : 'нет текстового индекса'}. Поиск в ответах сможет ссылаться на страницы PDF. Это локальная индексация, не обучение языковой модели.`;
    } else $('#document-summary').hidden = true;
  }


  const sectionNames = {
    components: 'Узел', adjustments: 'Регулировка', technical_norms: 'Техническая норма',
    maintenance_norms: 'Норма обслуживания', maintenance: 'Обслуживание', faults: 'Неисправность',
    safety: 'Безопасность', gaps: 'Пробел документа'
  };
  let analysisPollTimer = null;
  let ollamaPollTimer = null;
  function claimHeading(claim) {
    return claim.title || claim.name || claim.symptom || claim.description || claim.text || 'Карточка без заголовка';
  }
  function claimStateLabel(claim) {
    if (claim.review_status === 'verified') return 'Проверено человеком';
    if (claim.review_status === 'auto_verified' && claim.auto_verified) return 'Автопроверено · не сверено человеком';
    if (claim.review_status === 'rejected') return 'Отклонено';
    if (claim.review_status === 'edited') return 'Изменено · нужна проверка';
    return 'Нужна проверка';
  }
  function renderClaims() {
    const run = state.analysisRun;
    const result = run?.result || {};
    const claims = Array.isArray(result.claims) ? result.claims : [];
    const filter = $('#claim-filter')?.value || 'all';
    const visible = claims.map((claim, index) => ({ claim, index }))
      .filter(({ claim }) => filter === 'all' || claim.kind === filter);
    const list = $('#claim-list');
    if (!list) return;
    list.innerHTML = visible.length ? visible.map(({ claim, index }) => {
      const status = claim.review_status === 'verified' ? 'human' : claim.review_status === 'rejected' ? 'rejected' : claim.review_status === 'auto_verified' ? 'auto' : '';
      return `<article class="claim-card${state.activeClaimIndex === index ? ' selected' : ''}" data-claim-index="${index}"><strong>${escapeHtml(claimHeading(claim))}</strong><p>${escapeHtml(sectionNames[claim.kind] || claim.kind || 'Карточка')} · PDF стр. ${escapeHtml(claim.page ?? '—')}</p><span class="claim-tag ${status}">${escapeHtml(claimStateLabel(claim))}</span></article>`;
    }).join('') : '<p class="empty">В этой категории карточек нет.</p>';
    if (state.activeClaimIndex != null && claims[state.activeClaimIndex]) {
      const claim = claims[state.activeClaimIndex];
      $('#claim-title').textContent = claimHeading(claim);
      $('#claim-status').textContent = `${claimStateLabel(claim)}${claim.validation_errors?.length ? ` · ${claim.validation_errors.join('; ')}` : ''}`;
      $('#claim-editor').disabled = false;
      if (!$('#claim-editor').dataset.dirty) $('#claim-editor').value = JSON.stringify(claim, null, 2);
      for (const id of ['edit-claim', 'verify-claim', 'reject-claim']) $(`#${id}`).disabled = false;
      const pdfFrame = $('#analysis-pdf');
      const documentId = run.source_document_id;
      if (documentId && claim.page) {
        const params = new URLSearchParams({ file: `/api/admin/documents/${documentId}/file`, page: String(claim.page) });
        if (claim.quote) params.set('quote', claim.quote);
        pdfFrame.src = `/admin/static/admin/pdf-viewer.html?${params.toString()}`;
      } else pdfFrame.removeAttribute('src');
    } else {
      $('#claim-title').textContent = 'Выберите карточку';
      $('#claim-status').textContent = '';
      $('#claim-editor').disabled = true;
      $('#claim-editor').value = '';
      for (const id of ['edit-claim', 'verify-claim', 'reject-claim']) $(`#${id}`).disabled = true;
      $('#analysis-pdf').removeAttribute('src');
    }
  }
  function renderAnalysis() {
    const panel = $('#analysis-panel');
    const run = state.analysisRun || state.analysisRuns?.[0];
    state.analysisRun = run || null;
    panel.hidden = !selectedMachine() || !run;
    if (!run) return;
    const stageNames = { queued: 'В очереди', running: 'Выполняется', indexing: 'Индекс', waiting_for_model: 'Ожидает модель', review: 'Готов к проверке', error: 'Приостановлен' };
    $('#analysis-state').textContent = stageNames[run.status] || run.status;
    $('#analysis-progress').value = Number(run.progress_percent) || 0;
    $('#analysis-stage').textContent = run.stage || 'Ожидание';
    $('#resume-analysis').hidden = !['waiting_for_model', 'error', 'queued'].includes(run.status);
    const canReview = ['review', 'error'].includes(run.status);
    ['edit-claim', 'verify-claim', 'reject-claim'].forEach((id) => { $(`#${id}`).disabled = !canReview; });
    const claims = run.result?.claims || [];
    const canAuto = claims.some((claim) => claim.review_status === 'auto_verified' && claim.auto_verified && claim.kind !== 'gaps');
    const canHuman = claims.some((claim) => claim.review_status === 'verified' && claim.human_verified);
    $('#publish-auto').disabled = !canAuto;
    $('#publish-human').disabled = !canHuman;
    renderClaims();
    if (['queued', 'running', 'indexing'].includes(run.status)) {
      if (analysisPollTimer) clearTimeout(analysisPollTimer);
      analysisPollTimer = setTimeout(async () => {
        try {
          state.analysisRun = await request(`/api/admin/analysis/${run.id}`);
          state.analysisRuns = state.analysisRuns.map((item) => item.id === run.id ? state.analysisRun : item);
          renderAnalysis();
        } catch (_) { /* A later interaction will retry the status fetch. */ }
      }, 1800);
    } else if (analysisPollTimer) {
      clearTimeout(analysisPollTimer); analysisPollTimer = null;
    }
  }
  function renderOllamaStatus() {
    const status = state.ollamaStatus;
    if (!status) return;
    $('#ollama-chat-model').value = status.chat_model || 'qwen3:8b';
    $('#ollama-search-model').value = status.search_model || 'bge-m3';
    const badge = $('#ollama-state');
    badge.textContent = status.chat_model_ready ? 'Локальная модель готова' : status.running ? 'Ollama запущена · модели нет' : status.installed ? 'Ollama установлена · не запущена' : 'Ollama не установлена';
    badge.classList.toggle('ready', Boolean(status.chat_model_ready));
    const detail = $('#ollama-status-detail');
    const names = (status.installed_models || []).join(', ') || 'пока нет загруженных моделей';
    detail.textContent = `${status.inference_network || 'Только локальный адрес'} · ${status.version ? `версия ${status.version}` : ''} · доступные модели: ${names}. ` +
      (status.reason === 'ollama_not_installed' ? 'Установите Ollama отдельно, затем нажмите кнопку загрузки модели.' :
       status.reason === 'service_not_running' ? 'Запустите Ollama в Windows и повторите проверку.' :
       status.chat_model_ready ? 'Новые разборы и вопросы доступны локально.' : 'Скачайте выбранные модели; готовые данные доступны и без Ollama.');
    const progress = $('#ollama-download-progress');
    const download = status.download;
    progress.hidden = !download || !['queued', 'running'].includes(download.status);
    if (download && !progress.hidden) {
      const bar = progress.querySelector('progress');
      bar.value = Number.isFinite(download.progress) ? download.progress : 0;
      $('#ollama-download-label').textContent = `${download.model}: ${download.message || download.status}${Number.isFinite(download.progress) ? ` · ${download.progress}%` : ''}`;
    } else if (download?.status === 'error') {
      detail.textContent += ` Последняя загрузка: ${download.message}`;
    }
    if (progress.hidden && download?.status === 'running') {
      if (ollamaPollTimer) clearTimeout(ollamaPollTimer);
      ollamaPollTimer = setTimeout(loadOllamaStatus, 1800);
    }
  }
  async function loadOllamaStatus() {
    try {
      state.ollamaStatus = await request('/api/admin/ollama/status');
      renderOllamaStatus();
      if (state.ollamaStatus.download && ['queued', 'running'].includes(state.ollamaStatus.download.status)) {
        if (ollamaPollTimer) clearTimeout(ollamaPollTimer);
        ollamaPollTimer = setTimeout(loadOllamaStatus, 1800);
      }
    } catch (error) {
      $('#ollama-status-detail').textContent = error.message;
    }
  }
  async function reviewClaim(action) {
    const run = state.analysisRun;
    const index = state.activeClaimIndex;
    if (!run || index == null) return;
    let changes = {};
    if (action === 'edit') {
      try {
        const edited = JSON.parse($('#claim-editor').value);
        const fields = ['title', 'purpose', 'text', 'name', 'value', 'unit', 'condition', 'page', 'quote', 'symptom', 'cause', 'remedy', 'node', 'steps', 'description'];
        changes = Object.fromEntries(fields.filter((key) => key in edited).map((key) => [key, edited[key]]));
      } catch { $('#claim-status').textContent = 'JSON карточки не разобран; исправьте синтаксис.'; return; }
    }
    const payload = {
      claim_index: index,
      action,
      reviewer: $('#reviewer-name').value.trim(),
      reason: $('#reviewer-note').value.trim(),
      changes,
    };
    try {
      const result = await request(`/api/admin/analysis/${run.id}/review`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      state.analysisRun = result.analysis;
      state.analysisRuns = state.analysisRuns.map((item) => item.id === run.id ? result.analysis : item);
      $('#claim-editor').dataset.dirty = '';
      renderAnalysis();
    } catch (error) { $('#claim-status').textContent = error.message; }
  }
  async function publishClaims(mode) {
    const run = state.analysisRun;
    if (!run) return;
    try {
      const result = await request(`/api/admin/analysis/${run.id}/publish`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode, reviewer: $('#reviewer-name').value.trim() }),
      });
      $('#analysis-stage').textContent = `Опубликовано: ${result.published} карточек · ${result.label}`;
      await loadMachineData();
    } catch (error) { $('#analysis-stage').textContent = error.message; }
  }

  fileInput.addEventListener('change', () => {
    $('#manual-file-name').textContent = fileInput.files[0]?.name || 'Выберите руководство';
  });
  $('#new-machine').addEventListener('click', () => {
    state.machineId = null; state.newFlow = true;
    setupForm.reset(); modelField.value = 'ГРН-471';
    $('#manual-file-name').textContent = 'Выберите руководство';
    setStatus('#setup-status', '');
    render(); window.scrollTo({ top: 0, behavior: 'smooth' });
  });
  $('#machine-select').addEventListener('change', async (event) => {
    state.machineId = Number(event.target.value) || null; state.newFlow = false;
    try { await loadMachineData(); } catch (error) { setStatus('#setup-status', error.message, 'bad'); }
  });

  setupForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = $('#process-pdf');
    const file = fileInput.files[0];
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.pdf')) return setStatus('#setup-status', 'Выберите файл PDF.', 'bad');
    setBusy(button, true, 'Обрабатываю PDF…');
    setStatus('#setup-status', 'Сохраняю файл, извлекаю текст и строю локальный индекс. Не закрывайте это окно.', '');
    try {
      if (!state.machineId) {
        const model = modelField.value.trim();
        const machine = await request('/api/admin/machines', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code: machineCode(model), manufacturer: 'Kolibri', model, machine_type: 'Сельскохозяйственная техника', description: '' })
        });
        state.machines.push(machine); state.machineId = machine.id; state.newFlow = false;
        await loadMachineData();
      }
      const body = new FormData();
      body.append('machine_id', state.machineId);
      body.append('title', file.name.replace(/\.pdf$/i, ''));
      body.append('file', file);
      const result = await request('/api/admin/documents', { method: 'POST', body });
      setStatus('#setup-status', `PDF сохранён локально (${result.page_count} стр.). Разбор поставлен в очередь; прогресс сохраняется на ПК и продолжится после перезапуска.`, 'good');
      setupForm.reset(); $('#manual-file-name').textContent = 'Выберите руководство';
      await loadMachines(state.machineId);
      if (result.analysis_run_id) {
        state.analysisRun = await request(`/api/admin/analysis/${result.analysis_run_id}`);
        renderAnalysis();
        document.querySelector('#analysis-panel')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
      state.newFlow = false; render();
    } catch (error) {
      setStatus('#setup-status', error.message, 'bad');
      if (state.machineId) { try { await loadMachineData(); } catch (_) { /* leave original error */ } }
    } finally { setBusy(button, false); }
  });

  $('#card-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const body = Object.fromEntries(new FormData(form).entries());
    const button = form.querySelector('button[type="submit"]');
    setBusy(button, true, 'Сохраняю…');
    try {
      const updated = await request(`/api/admin/machines/${state.machineId}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
      });
      state.machines = state.machines.map((machine) => machine.id === updated.id ? { ...machine, ...updated } : machine);
      setStatus('#card-status', 'Карточка сохранена на этом компьютере.', 'good');
      render();
    } catch (error) { setStatus('#card-status', error.message, 'bad'); }
    finally { setBusy(button, false); }
  });
  $('#node-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const data = Object.fromEntries(new FormData(form).entries());
    const button = form.querySelector('button[type="submit"]');
    setBusy(button, true, 'Добавляю…');
    try {
      await request('/api/admin/nodes', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ machine_id: state.machineId, ...data }) });
      form.reset(); setStatus('#node-status', 'Узел добавлен.', 'good'); await loadMachineData();
    } catch (error) { setStatus('#node-status', error.message, 'bad'); }
    finally { setBusy(button, false); }
  });
  $('#photo-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const body = new FormData(form); body.append('machine_id', state.machineId);
    if (!body.get('node_id')) body.delete('node_id');
    const button = form.querySelector('button[type="submit"]');
    setBusy(button, true, 'Загружаю…');
    try {
      await request('/api/admin/media', { method: 'POST', body });
      form.reset(); setStatus('#photo-status', 'Фото сохранено локально.', 'good'); await loadMachineData();
    } catch (error) { setStatus('#photo-status', error.message, 'bad'); }
    finally { setBusy(button, false); }
  });
  async function askAdminAssistant(form, resultBox, statusText) {
    const question = new FormData(form).get('question').toString().trim();
    const button = form.querySelector('button[type="submit"]');
    resultBox.hidden = false;
    resultBox.className = 'diagnostic-result';
    resultBox.textContent = statusText;
    setBusy(button, true, 'Проверяю…');
    try {
      const result = await request('/api/admin/assistant/answer', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ machine_id: state.machineId, question })
      });
      const answerHtml = escapeHtml(result.answer || 'Ответ не сформирован.').replace(/\n/g, '<br>');
      const citationHtml = (result.citations || []).map((citation) => {
        const sourceUrl = citationViewerUrl(citation);
        const quote = citation.quote ? `<blockquote>${escapeHtml(citation.quote)}</blockquote>` : '';
        return `<span><a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noopener">PDF, стр. ${Number(citation.page)}</a>${quote}</span>`;
      }).join('<br>');
      const success = ['manual_diagnosis', 'part_lookup', 'local_grounded_answer'].includes(result.status);
      resultBox.className = `diagnostic-result${success ? '' : ' bad'}`;
      resultBox.innerHTML = `${answerHtml}${citationHtml ? `<br><br><strong>Проверить источник:</strong><br>${citationHtml}` : ''}`;
    } catch (error) {
      resultBox.className = 'diagnostic-result bad';
      resultBox.textContent = error.message;
    } finally { setBusy(button, false); }
  }
  $('#photo-list').addEventListener('click', async (event) => {
    const button = event.target.closest('[data-photo-sync]');
    if (!button) return;
    const photo = state.photos.find((item) => String(item.id) === button.dataset.photoSync);
    if (!photo) return;
    setBusy(button, true, 'Сохраняю…');
    try {
      await request(`/api/admin/media/${photo.id}/sync`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !photo.sync_to_phones }),
      });
      await loadMachineData();
    } catch (error) {
      setStatus('#photo-status', error.message, 'bad');
      setBusy(button, false);
    }
  });
  $('#diagnostic-form').addEventListener('submit', (event) => {
    event.preventDefault();
    void askAdminAssistant(event.currentTarget, $('#diagnostic-result'),
      'Сверяю проявление с таблицей неисправностей и страницами руководства…');
  });
  $('#manual-question-form').addEventListener('submit', (event) => {
    event.preventDefault();
    void askAdminAssistant(event.currentTarget, $('#manual-question-result'),
      'Ищу подтверждающие страницы и обращаюсь только к локальной Ollama…');
  });


  $('#claim-list').addEventListener('click', (event) => {
    const card = event.target.closest('[data-claim-index]');
    if (!card) return;
    state.activeClaimIndex = Number(card.dataset.claimIndex);
    $('#claim-editor').dataset.dirty = '';
    renderAnalysis();
  });
  $('#claim-filter').addEventListener('change', renderClaims);
  $('#claim-editor').addEventListener('input', () => { $('#claim-editor').dataset.dirty = '1'; });
  $('#edit-claim').addEventListener('click', () => void reviewClaim('edit'));
  $('#verify-claim').addEventListener('click', () => void reviewClaim('verify'));
  $('#reject-claim').addEventListener('click', () => void reviewClaim('reject'));
  $('#resume-analysis').addEventListener('click', async () => {
    const run = state.analysisRun;
    if (!run) return;
    try {
      await request(`/api/admin/analysis/${run.id}/resume`, { method: 'POST' });
      state.analysisRun = await request(`/api/admin/analysis/${run.id}`);
      renderAnalysis();
    } catch (error) { $('#analysis-stage').textContent = error.message; }
  });
  $('#publish-auto').addEventListener('click', () => void publishClaims('auto_verified'));
  $('#publish-human').addEventListener('click', () => void publishClaims('human_verified'));
  $('#save-ollama').addEventListener('click', async () => {
    try {
      state.ollamaStatus = await request('/api/admin/ollama/settings', {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_model: $('#ollama-chat-model').value.trim(), search_model: $('#ollama-search-model').value.trim() }),
      }).then((result) => result.status);
      renderOllamaStatus();
      $('#ollama-status-detail').textContent = 'Названия моделей сохранены в локальной базе.';
    } catch (error) { $('#ollama-status-detail').textContent = error.message; }
  });
  async function downloadOllamaModel(profile, button) {
    setBusy(button, true, 'Запускаю…');
    try {
      await request('/api/admin/ollama/download', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ profile }),
      });
      await loadOllamaStatus();
    } catch (error) { $('#ollama-status-detail').textContent = error.message; }
    finally { setBusy(button, false); }
  }
  $('#download-chat-model').addEventListener('click', (event) => void downloadOllamaModel('chat', event.currentTarget));
  $('#download-search-model').addEventListener('click', (event) => void downloadOllamaModel('search', event.currentTarget));

  void loadOllamaStatus();
  loadMachines().catch((error) => {
    $('#wizard-panel').innerHTML = `<div class="error-box">Не удалось загрузить Kolibri: ${escapeHtml(error.message)}. Убедитесь, что локальный сервер запущен.</div>`;
  });
})();
