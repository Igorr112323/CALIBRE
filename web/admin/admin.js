(() => {
  const $ = (selector) => document.querySelector(selector);
  const state = { screen: 'home', returnScreen: null, returnMachineId: null, machines: [], machineId: null, newFlow: false, newMachinePhotos: [], nodes: [], documents: [], photos: [], machineTab: 'scheme', movingNodeId: null };
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
  function renderNewPhotoPreviews() {
    const container = $('#new-machine-photo-previews');
    if (!container) return;
    let number = 1;
    container.innerHTML = state.newMachinePhotos.map((photo, photoIndex) => {
      const markers = photo.markers.map((marker) => {
        const markerNumber = number++;
        return { ...marker, markerNumber };
      });
      const pins = markers.map((marker) => `<span class="guided-photo-pin" style="left:${marker.x * 100}%;top:${marker.y * 100}%">${marker.markerNumber}</span>`).join('');
      const labels = markers.length ? markers.map((marker) => `
        <div class="guided-marker-row"><span class="guided-marker-number">${marker.markerNumber}</span><input type="text" maxlength="240" required placeholder="Название узла" value="${escapeHtml(marker.title)}" data-marker-title data-photo-index="${photoIndex}" data-marker-id="${escapeHtml(marker.id)}"><button type="button" class="button button-ghost marker-remove" data-remove-marker data-photo-index="${photoIndex}" data-marker-id="${escapeHtml(marker.id)}" aria-label="Удалить отметку ${marker.markerNumber}">×</button></div>`).join('') : '<p class="empty">Нажмите на фото, чтобы добавить первую отметку.</p>';
      return `<article class="guided-photo-card"><div class="guided-photo-heading"><strong>${escapeHtml(photo.file.name)}</strong><button class="button button-ghost marker-remove" type="button" data-remove-photo="${photoIndex}" aria-label="Удалить фото">Удалить</button></div><div class="guided-photo-stage" data-photo-stage data-photo-index="${photoIndex}"><img src="${escapeHtml(photo.url)}" alt="${escapeHtml(photo.file.name)}">${pins}</div><label class="guided-sync-toggle"><input type="checkbox" data-photo-sync-index="${photoIndex}" ${photo.syncToPhones ? 'checked' : ''}> Разрешить передачу этого фото в APK по локальной сети</label><div class="guided-marker-list">${labels}</div></article>`;
    }).join('');
  }
  function clearNewMachinePhotos() {
    state.newMachinePhotos.forEach((photo) => URL.revokeObjectURL(photo.url));
    state.newMachinePhotos = [];
    renderNewPhotoPreviews();
  }
  function adminTractorIllustration() {
    return `<svg class="tractor-art" viewBox="0 0 220 150" aria-hidden="true"><ellipse cx="128" cy="137" rx="91" ry="7" fill="#bdc9b8" opacity=".7"/><path d="M91 21q1-7 9-7h31q8 0 10 8l8 43h53q9 0 9 9v17q0 9-9 9h-70l-9-30H91z" fill="#3d7435"/><path d="M101 27h25l6 29H96z" fill="#f2f4eb"/><path d="M151 66h48q9 0 9 9v7h-57z" fill="#3d7435"/><rect x="177" y="39" width="7" height="28" rx="3.5" fill="#18251b"/><circle cx="94" cy="102" r="34" fill="#172019"/><circle cx="94" cy="102" r="15" fill="#f4f6ed"/><circle cx="94" cy="102" r="6" fill="#294b2b"/><circle cx="199" cy="111" r="22" fill="#172019"/><circle cx="199" cy="111" r="9" fill="#f4f6ed"/></svg>`;
  }
  function renderHomeMachineList() {
    const list = $('#admin-machine-list');
    if (!list) return;
    list.innerHTML = state.machines.length ? state.machines.map((machine, index) => `
      <button class="admin-machine-card card-tone-${index % 2} ${machine.status === 'published' ? 'is-published' : 'is-draft'}" type="button" data-open-machine="${machine.id}">
        <h3>${escapeHtml(machine.model || 'Машина')}</h3>
        <p>${escapeHtml(machine.manufacturer || '')}${machine.manufacturer && machine.machine_type ? ' · ' : ''}${escapeHtml(machine.machine_type || 'Сельхозтехника')}</p>
        <span class="machine-status-tag">${machine.status === 'published' ? 'Опубликовано' : 'Черновик'}</span>
        <span class="machine-open" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M5 12h14M12 5l7 7-7 7"/></svg></span>
        ${adminTractorIllustration()}
      </button>`).join('') : '<div class="admin-empty">Пока нет карточек техники.<br>Создайте первую карточку кнопкой выше.</div>';
  }
  function renderMachinePicker() {
    const wrap = $('#machine-picker-wrap');
    const select = $('#machine-select');
    const label = $('#machine-select-label');
    const hasMachines = state.machines.length > 0;
    wrap.hidden = state.screen === 'home' || state.newFlow || !hasMachines;
    select.hidden = !hasMachines;
    label.hidden = !hasMachines;
    select.innerHTML = state.machines.map((machine) => `<option value="${machine.id}">${escapeHtml(machine.manufacturer)} ${escapeHtml(machine.model)}</option>`).join('');
    if (state.machineId) select.value = String(state.machineId);
  }
  function render() {
    const machine = selectedMachine();
    const hasMachine = Boolean(machine);
    const onHome = state.screen === 'home';
    $('#admin-home').hidden = !onHome;
    $('#admin-workspace').hidden = onHome;
    $('#back-to-admin-home').hidden = onHome;
    $('.page').classList.toggle('admin-home-visible', onHome);
    $('#admin-footer').hidden = onHome;
    $('.local-pill').hidden = onHome;
    renderHomeMachineList();
    $('#machine-create-panel').hidden = !state.newFlow;
    $('#wizard-panel').hidden = hasMachine || state.newFlow;
    $('#machine-panel').hidden = !hasMachine || state.newFlow;
    $('.intro').hidden = hasMachine || state.newFlow;
    $('#page-heading').textContent = state.newFlow ? 'Добавим новую технику' : (hasMachine ? `${machine.manufacturer} ${machine.model}` : 'Соберём базу Kolibri вместе');
    $('#machine-card-heading').textContent = hasMachine ? `${machine.manufacturer} ${machine.model}` : 'Карточка машины';
    modelField.parentElement.hidden = hasMachine || state.newFlow;
    modelField.required = !hasMachine && !state.newFlow;
    if (hasMachine) {
      $('#machine-panel [name="manufacturer"]').value = machine.manufacturer || 'Kolibri';
      $('#machine-panel [name="model"]').value = machine.model || '';
      $('#machine-panel [name="machine_type"]').value = machine.machine_type || '';
      $('#machine-panel [name="description"]').value = machine.description || '';
      $('#machine-publish-toggle').checked = machine.status === 'published';
      $('#publication-state').textContent = machine.status === 'published'
        ? 'Опубликовано: карточка доступна в APK. Снятие галочки уберёт её из приложения.'
        : 'Черновик: карточка видна в каталоге админки серой, но не передаётся в APK.';
      $('#publication-review').classList.toggle('is-published', machine.status === 'published');
    }
    document.querySelectorAll('[data-machine-tab-panel]').forEach((panel) => {
      panel.hidden = panel.dataset.machineTabPanel !== state.machineTab;
    });
    document.querySelectorAll('[data-admin-tab]').forEach((tab) => {
      tab.classList.toggle('active', tab.dataset.adminTab === state.machineTab);
    });
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
      <article class="item-row node-editor-row"><span class="item-number">${node.number}</span><div class="node-editor-content"><strong>${escapeHtml(node.title)}</strong>${node.purpose ? `<small>${escapeHtml(node.purpose)}</small>` : ''}
        <details class="node-edit-details"><summary>Изменить</summary>
          <form class="node-edit-form" data-node-id="${node.id}">
            <label class="field"><span>Название узла</span><input name="title" required maxlength="240" value="${escapeHtml(node.title)}"></label>
            <label class="field"><span>Назначение <small>необязательно</small></span><textarea name="purpose" rows="2" maxlength="4000">${escapeHtml(node.purpose || '')}</textarea></label>
            <button class="button button-secondary" type="submit">Сохранить</button>
          </form>
          ${node.marker_photo_id ? `<button class="button button-ghost button-small" type="button" data-move-node-id="${node.id}">Переместить точку на фото</button>` : ''}
        </details>
      </div></article>`).join('') : '<p class="empty">Добавьте первый узел на фото или кнопкой ниже.</p>';
  }
  function renderPhotos() {
    const list = $('#photo-list');
    $('#photo-count').textContent = state.photos.length;
    list.innerHTML = state.photos.length ? state.photos.map((photo) => {
      const markers = state.nodes.filter((node) => node.marker_photo_id === photo.id && node.map_x != null && node.map_y != null);
      const pins = markers.map((node) => `<span class="admin-photo-pin" style="left:${Math.max(0, Math.min(100, node.map_x * 100))}%;top:${Math.max(0, Math.min(100, node.map_y * 100))}%" title="${escapeHtml(node.title)}">${node.number}</span>`).join('');
      const markerLabels = markers.map((node) => `<span class="photo-marker-label"><b>${node.number}</b> ${escapeHtml(node.title)}</span>`).join('');
      return `<article class="photo-item"><a class="admin-photo-canvas" data-photo-id="${photo.id}" href="${escapeHtml(photo.url)}" target="_blank" rel="noopener"><img src="${escapeHtml(photo.url)}" alt="${escapeHtml(photo.caption || 'Фото техники')}" loading="lazy">${pins}</a><p>${escapeHtml(photo.caption || photo.filename || 'Фото')}${photo.node_id ? ' · узел' : ''}</p>${markerLabels ? `<div class="photo-marker-labels">${markerLabels}</div>` : ''}<button type="button" class="button button-small ${photo.sync_to_phones ? 'button-secondary' : 'button-ghost'}" data-photo-sync="${photo.id}">${photo.sync_to_phones ? 'Не передавать в приложение' : 'Передавать в приложение'}</button><small>${photo.sync_to_phones ? 'Передача включена по локальной сети.' : 'Фото хранится только на этом компьютере.'}</small></article>`;
    }).join('') : '<p class="empty">Загруженные фото появятся здесь.</p>';
  }
  function renderDocuments() {
    const list = $('#document-list');
    $('#document-count').textContent = state.documents.length;
    list.innerHTML = state.documents.length ? state.documents.map((doc) => `
      <article class="item-row"><span class="item-number">PDF</span><div><strong><a href="${escapeHtml(doc.url)}" target="_blank" rel="noopener">${escapeHtml(doc.title || doc.filename)}</a></strong><small>${doc.page_count} страниц · сохранено на этом компьютере</small></div></article>`).join('') : '<p class="empty">Документы ещё не добавлены.</p>';
    if (state.documents.length) {
      const latest = state.documents[0];
      $('#document-summary').hidden = false;
      $('#document-summary').textContent = `Руководство загружено. В нём ${latest.page_count} страниц.`;
    } else $('#document-summary').hidden = true;
  }


  fileInput.addEventListener('change', () => {
    $('#manual-file-name').textContent = fileInput.files[0]?.name || 'Выберите руководство';
  });
  function startMachineCreate() {
    state.returnScreen = state.screen;
    state.returnMachineId = state.machineId;
    state.screen = 'workspace';
    state.machineId = null;
    state.newFlow = true;
    state.machineTab = 'scheme';
    clearNewMachinePhotos();
    $('#machine-create-form').reset();
    setStatus('#machine-create-status', '');
    render(); window.scrollTo({ top: 0, behavior: 'smooth' });
  }
  $('#new-machine').addEventListener('click', startMachineCreate);
  $('#home-create-machine').addEventListener('click', startMachineCreate);
  $('#new-machine-photos').addEventListener('change', (event) => {
    clearNewMachinePhotos();
    state.newMachinePhotos = Array.from(event.target.files || []).map((file) => ({
      file, url: URL.createObjectURL(file), markers: [], syncToPhones: false
    }));
    renderNewPhotoPreviews();
  });
  $('#new-machine-photo-previews').addEventListener('click', (event) => {
    const removePhoto = event.target.closest('[data-remove-photo]');
    if (removePhoto) {
      const [photo] = state.newMachinePhotos.splice(Number(removePhoto.dataset.removePhoto), 1);
      if (photo) URL.revokeObjectURL(photo.url);
      renderNewPhotoPreviews();
      return;
    }
    const removeMarker = event.target.closest('[data-remove-marker]');
    if (removeMarker) {
      const photo = state.newMachinePhotos[Number(removeMarker.dataset.photoIndex)];
      if (photo) photo.markers = photo.markers.filter((marker) => marker.id !== removeMarker.dataset.markerId);
      renderNewPhotoPreviews();
      return;
    }
    const stage = event.target.closest('[data-photo-stage]');
    if (!stage || event.target.closest('button')) return;
    const photo = state.newMachinePhotos[Number(stage.dataset.photoIndex)];
    const image = stage.querySelector('img');
    const rect = image.getBoundingClientRect();
    const x = (event.clientX - rect.left) / rect.width;
    const y = (event.clientY - rect.top) / rect.height;
    if (!photo || x < 0 || x > 1 || y < 0 || y > 1) return;
    const id = window.crypto?.randomUUID?.() || `${Date.now()}-${Math.random()}`;
    photo.markers.push({ id, title: '', x, y });
    renderNewPhotoPreviews();
    const input = Array.from($('#new-machine-photo-previews').querySelectorAll('[data-marker-title]'))
      .find((item) => item.dataset.markerId === id);
    input?.focus();
  });
  $('#new-machine-photo-previews').addEventListener('input', (event) => {
    if (!event.target.matches('[data-marker-title]')) return;
    const photo = state.newMachinePhotos[Number(event.target.dataset.photoIndex)];
    const marker = photo?.markers.find((item) => item.id === event.target.dataset.markerId);
    if (marker) marker.title = event.target.value;
  });
  $('#new-machine-photo-previews').addEventListener('change', (event) => {
    if (!event.target.matches('[data-photo-sync-index]')) return;
    const photo = state.newMachinePhotos[Number(event.target.dataset.photoSyncIndex)];
    if (photo) photo.syncToPhones = event.target.checked;
  });
  $('#admin-machine-list').addEventListener('click', async (event) => {
    const card = event.target.closest('[data-open-machine]');
    if (!card) return;
    state.screen = 'workspace';
    state.newFlow = false;
    state.machineTab = 'scheme';
    state.machineId = Number(card.dataset.openMachine) || null;
    try { await loadMachineData(); window.scrollTo({ top: 0, behavior: 'smooth' }); }
    catch (error) { setStatus('#setup-status', error.message, 'bad'); }
  });
  $('#back-to-admin-home').addEventListener('click', () => {
    state.screen = 'home';
    state.newFlow = false;
    state.returnMachineId = null;
    state.returnScreen = null;
    render(); window.scrollTo({ top: 0, behavior: 'smooth' });
  });
  $('#cancel-machine-create').addEventListener('click', async () => {
    clearNewMachinePhotos();
    state.newFlow = false;
    state.screen = state.returnScreen || 'home';
    state.machineId = state.returnMachineId || null;
    state.returnMachineId = null;
    state.returnScreen = null;
    if (state.screen === 'workspace' && state.machineId) await loadMachineData();
    else render();
  });
  $('#machine-create-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const model = form.elements.model.value.trim();
    const pdfFile = $('#new-machine-pdf').files[0] || null;
    const photos = state.newMachinePhotos.slice();
    const missingLabel = photos.some((photo) => photo.markers.some((marker) => !marker.title.trim()));
    if (missingLabel) {
      setStatus('#machine-create-status', 'Подпишите каждый пронумерованный узел или удалите ненужную отметку.', 'bad');
      form.querySelector('[data-marker-title]:invalid')?.focus();
      return;
    }
    if (!photos.length) {
      setStatus('#machine-create-status', 'Добавьте хотя бы одно фото техники.', 'bad');
      $('#new-machine-photos').focus();
      return;
    }
    if (pdfFile && !pdfFile.name.toLowerCase().endsWith('.pdf')) {
      setStatus('#machine-create-status', 'Выберите файл PDF.', 'bad');
      return;
    }
    const button = form.querySelector('button[type="submit"]');
    setBusy(button, true, 'Создаю черновик…');
    let createdMachine = null;
    let failure = null;
    try {
      createdMachine = await request('/api/admin/machines', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code: machineCode(model), manufacturer: 'Kolibri', model,
          machine_type: 'Сельскохозяйственная техника', description: '' }),
      });
      const uploadedPhotos = [];
      for (const photo of photos) {
        const body = new FormData();
        body.append('machine_id', createdMachine.id);
        body.append('file', photo.file);
        body.append('caption', photo.file.name);
        body.append('photo_kind', 'machine_scheme');
        body.append('sync_to_phones', String(photo.syncToPhones));
        const uploaded = await request('/api/admin/media', { method: 'POST', body });
        uploadedPhotos.push({ local: photo, id: uploaded.id });
        for (const marker of photo.markers) {
          await request('/api/admin/nodes', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ machine_id: createdMachine.id, title: marker.title.trim(), purpose: '',
              map_x: marker.x, map_y: marker.y, marker_photo_id: uploaded.id }),
          });
        }
      }
      if (pdfFile) {
        const body = new FormData();
        body.append('machine_id', createdMachine.id);
        body.append('title', pdfFile.name.replace(/\.pdf$/i, ''));
        body.append('file', pdfFile);
        await request('/api/admin/documents', { method: 'POST', body });
      }
    } catch (error) {
      failure = error;
    }
    if (createdMachine) {
      state.newFlow = false;
      state.screen = 'workspace';
      state.machineId = createdMachine.id;
      state.machineTab = 'scheme';
      state.returnMachineId = null;
      state.returnScreen = null;
      clearNewMachinePhotos();
      $('#new-machine-pdf').value = '';
      try { await loadMachines(createdMachine.id); }
      catch (error) { failure = failure || error; }
      setStatus('#card-status', failure
        ? `Карточка создана, но не все файлы удалось загрузить: ${failure.message}. Проверьте вкладку «Схема».`
        : 'Карточка создана как черновик. Проверьте её разделы; галочка выше публикует её в приложении.',
      failure ? 'bad' : 'good');
      if (failure) $('#card-status').scrollIntoView({ behavior: 'smooth', block: 'center' });
    } else {
      setStatus('#machine-create-status', failure?.message || 'Не удалось создать карточку.', 'bad');
    }
    setBusy(button, false);
  });
  $('#machine-publish-toggle').addEventListener('change', async (event) => {
    const toggle = event.currentTarget;
    const desired = toggle.checked;
    toggle.disabled = true;
    try {
      const result = await request(`/api/admin/machines/${state.machineId}/publication`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ published: desired }),
      });
      state.machines = state.machines.map((machine) => machine.id === result.id ? { ...machine, status: result.status } : machine);
      render();
      setStatus('#card-status', desired ? 'Карточка опубликована и попадёт в APK при следующей синхронизации телефона.' : 'Публикация снята; карточка осталась черновиком и скрыта в APK.', 'good');
    } catch (error) {
      toggle.checked = !desired;
      setStatus('#card-status', error.message, 'bad');
    } finally { toggle.disabled = false; }
  });
  $('#machine-panel').addEventListener('click', (event) => {
    const tab = event.target.closest('[data-admin-tab]');
    if (!tab) return;
    state.machineTab = tab.dataset.adminTab;
    document.querySelectorAll('[data-machine-tab-panel]').forEach((panel) => {
      panel.hidden = panel.dataset.machineTabPanel !== state.machineTab;
    });
    document.querySelectorAll('[data-admin-tab]').forEach((item) => {
      item.classList.toggle('active', item === tab);
    });
  });
  $('#machine-select').addEventListener('change', async (event) => {
    state.screen = 'workspace';
    state.machineId = Number(event.target.value) || null; state.newFlow = false; state.machineTab = 'scheme';
    try { await loadMachineData(); } catch (error) { setStatus('#setup-status', error.message, 'bad'); }
  });

  async function uploadManual(file, machineId, title, statusSelector, form, button) {
    const body = new FormData();
    body.append('machine_id', machineId);
    body.append('title', title || file.name.replace(/\.pdf$/i, ''));
    body.append('file', file);
    const result = await request('/api/admin/documents', { method: 'POST', body });
    setStatus(statusSelector, 'Руководство сохранено на этом компьютере.', 'good');
    form.reset();
    if (statusSelector === '#setup-status') $('#manual-file-name').textContent = 'Выберите руководство';
    await loadMachines(machineId);
  }
  setupForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = $('#process-pdf');
    const file = fileInput.files[0];
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.pdf')) return setStatus('#setup-status', 'Выберите файл PDF.', 'bad');
    setBusy(button, true, 'Обрабатываю PDF…');
    setStatus('#setup-status', 'Сохраняю файл, извлекаю текст и строю локальный индекс. Не закрывайте это окно.', '');
    try {
      let machineId = state.machineId;
      if (!machineId) {
        const model = modelField.value.trim();
        const machine = await request('/api/admin/machines', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ code: machineCode(model), manufacturer: 'Kolibri', model, machine_type: 'Сельскохозяйственная техника', description: '' })
        });
        machineId = machine.id;
      }
      state.newFlow = false;
      state.machineId = machineId;
      await uploadManual(file, machineId, '', '#setup-status', setupForm, button);
    } catch (error) {
      setStatus('#setup-status', error.message, 'bad');
      if (state.machineId) { try { await loadMachineData(); } catch (_) { /* leave original error */ } }
    } finally { setBusy(button, false); }
  });
  $('#manual-upload-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!state.machineId) return setStatus('#manual-upload-status', 'Сначала выберите или создайте машину.', 'bad');
    const form = event.currentTarget;
    const data = new FormData(form);
    const file = data.get('file');
    if (!(file instanceof File) || !file.size) return setStatus('#manual-upload-status', 'Выберите PDF.', 'bad');
    if (!file.name.toLowerCase().endsWith('.pdf')) return setStatus('#manual-upload-status', 'Можно загрузить только PDF.', 'bad');
    const button = form.querySelector('button[type="submit"]');
    setBusy(button, true, 'Обрабатываю…');
    try {
      await uploadManual(file, state.machineId, data.get('title').toString().trim(), '#manual-upload-status', form, button);
    } catch (error) { setStatus('#manual-upload-status', error.message, 'bad'); }
    finally { setBusy(button, false); }
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
  $('#node-list').addEventListener('submit', async (event) => {
    const form = event.target.closest('.node-edit-form');
    if (!form) return;
    event.preventDefault();
    const node = state.nodes.find((item) => String(item.id) === form.dataset.nodeId);
    if (!node) return;
    const data = Object.fromEntries(new FormData(form).entries());
    const button = form.querySelector('button[type="submit"]');
    setBusy(button, true, 'Сохраняю…');
    try {
      const updated = await request(`/api/admin/nodes/${node.id}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...data, map_x: node.map_x, map_y: node.map_y, marker_photo_id: node.marker_photo_id }),
      });
      await loadMachines(state.machineId);
      setStatus('#node-status', updated.machine_status === 'draft'
        ? 'Изменение сохранено. Карточка снова стала черновиком — проверьте и опубликуйте её заново.'
        : 'Изменение сохранено.', 'good');
    } catch (error) { setStatus('#node-status', error.message, 'bad'); }
    finally { setBusy(button, false); }
  });
  $('#node-list').addEventListener('click', (event) => {
    const button = event.target.closest('[data-move-node-id]');
    if (!button) return;
    state.movingNodeId = Number(button.dataset.moveNodeId);
    state.machineTab = 'scheme';
    document.querySelectorAll('[data-machine-tab-panel]').forEach((panel) => { panel.hidden = panel.dataset.machineTabPanel !== 'scheme'; });
    document.querySelectorAll('[data-admin-tab]').forEach((tab) => { tab.classList.toggle('active', tab.dataset.adminTab === 'scheme'); });
    setStatus('#photo-status', 'Нажмите на нужное место на фото, чтобы переместить точку.', '');
  });
  $('#photo-list').addEventListener('click', async (event) => {
    if (state.movingNodeId == null) return;
    const canvas = event.target.closest('.admin-photo-canvas');
    if (!canvas) return;
    event.preventDefault();
    const node = state.nodes.find((item) => item.id === state.movingNodeId);
    const photo = state.photos.find((item) => String(item.id) === canvas.dataset.photoId);
    const image = canvas.querySelector('img');
    if (!node || !photo || !image) return;
    const rect = image.getBoundingClientRect();
    const mapX = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    const mapY = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height));
    try {
      const updated = await request(`/api/admin/nodes/${node.id}`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: node.title, purpose: node.purpose || '', map_x: mapX, map_y: mapY, marker_photo_id: photo.id }),
      });
      state.movingNodeId = null;
      await loadMachines(state.machineId);
      state.machineTab = 'scheme';
      render();
      setStatus('#photo-status', updated.machine_status === 'draft'
        ? 'Точка перемещена. Карточка стала черновиком — проверьте и опубликуйте её заново.'
        : 'Точка перемещена.', 'good');
    } catch (error) {
      state.movingNodeId = null;
      setStatus('#photo-status', error.message, 'bad');
    }
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
  async function askAdminAssistant(form, resultBox) {
    const question = new FormData(form).get('question').toString().trim();
    const button = form.querySelector('button[type="submit"]');
    resultBox.hidden = false;
    resultBox.className = 'assistant-result';
    resultBox.textContent = 'Ищем ответ в руководстве…';
    setBusy(button, true, 'Ищем…');
    try {
      const result = await request('/api/admin/assistant/answer', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ machine_id: state.machineId, question })
      });
      const answerHtml = escapeHtml(result.answer || 'В руководстве не удалось найти подтверждённый ответ.').replace(/\n/g, '<br>');
      const citationHtml = (result.citations || []).map((citation) => {
        const sourceUrl = citationViewerUrl(citation);
        const quote = citation.quote ? `<blockquote>${escapeHtml(citation.quote)}</blockquote>` : '';
        return `<span><a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noopener">Открыть страницу ${Number(citation.page)}</a>${quote}</span>`;
      }).join('<br>');
      resultBox.innerHTML = `${answerHtml}${citationHtml ? `<br><br><strong>Источник:</strong><br>${citationHtml}` : ''}`;
    } catch (error) {
      resultBox.textContent = error.message;
    } finally { setBusy(button, false); }
  }
  $('#manual-question-form').addEventListener('submit', (event) => {
    event.preventDefault();
    void askAdminAssistant(event.currentTarget, $('#manual-question-result'));
  });

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
  loadMachines().catch((error) => {
    $('#wizard-panel').innerHTML = `<div class="error-box">Не удалось загрузить Kolibri: ${escapeHtml(error.message)}. Убедитесь, что локальный сервер запущен.</div>`;
  });
})();
