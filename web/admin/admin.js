(() => {
  const $ = (selector) => document.querySelector(selector);
  const state = { machines: [], machineId: null, newFlow: false, nodes: [], documents: [], photos: [] };
  const setupForm = $('#setup-form');
  const modelField = setupForm.elements.model;
  const fileInput = $('#manual-file');

  const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[char]);
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
  }
  async function loadMachineData() {
    if (!state.machineId) {
      state.nodes = []; state.documents = []; state.photos = []; render(); return;
    }
    const id = encodeURIComponent(state.machineId);
    const results = await Promise.all([
      request(`/api/admin/nodes?machine_id=${id}`),
      request(`/api/admin/documents?machine_id=${id}`),
      request(`/api/admin/media?machine_id=${id}`)
    ]);
    [state.nodes, state.documents, state.photos] = results;
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
      <article class="photo-item"><a href="${escapeHtml(photo.url)}" target="_blank" rel="noopener"><img src="${escapeHtml(photo.url)}" alt="${escapeHtml(photo.caption || 'Фото техники')}" loading="lazy"></a><p>${escapeHtml(photo.caption || photo.filename || 'Фото')}${photo.node_id ? ' · узел' : ''}</p></article>`).join('') : '<p class="empty">Загруженные фото появятся здесь.</p>';
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
      setStatus('#setup-status', `Готово: обработано ${result.page_count} стр. и сохранён локальный индекс. Языковая модель не дообучалась.`, 'good');
      setupForm.reset(); $('#manual-file-name').textContent = 'Выберите руководство';
      await loadMachines(state.machineId);
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
  $('#diagnostic-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const question = new FormData(form).get('question').toString().trim();
    const button = form.querySelector('button[type="submit"]');
    const resultBox = $('#diagnostic-result');
    resultBox.hidden = false;
    resultBox.className = 'diagnostic-result';
    resultBox.textContent = 'Сверяю проявление с таблицей неисправностей и страницами руководства…';
    setBusy(button, true, 'Проверяю…');
    try {
      const result = await request('/api/admin/assistant/answer', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ machine_id: state.machineId, question })
      });
      const answerHtml = escapeHtml(result.answer || 'Ответ не сформирован.').replace(/\n/g, '<br>');
      const citationHtml = (result.citations || []).map((citation) =>
        `<a href="${escapeHtml(citation.viewer_url || '#')}" target="_blank" rel="noopener">PDF, стр. ${Number(citation.page)}</a>`
      ).join(' · ');
      resultBox.className = `diagnostic-result${result.status === 'manual_diagnosis' ? '' : ' bad'}`;
      resultBox.innerHTML = `${answerHtml}${citationHtml ? `<br><br><strong>Проверить источник:</strong> ${citationHtml}` : ''}`;
    } catch (error) {
      resultBox.className = 'diagnostic-result bad';
      resultBox.textContent = error.message;
    } finally { setBusy(button, false); }
  });

  loadMachines().catch((error) => {
    $('#wizard-panel').innerHTML = `<div class="error-box">Не удалось загрузить Kolibri: ${escapeHtml(error.message)}. Убедитесь, что локальный сервер запущен.</div>`;
  });
})();
