const $ = (selector, root = document) => root.querySelector(selector);
const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[char]));
const tabs = [
  ['adjustments','Регулировки'],
  ['parts','Детали'],
  ['maintenance','ТО и смазка'],
  ['nodes','Узлы'],
  ['storage','Хранение'],
  ['specs','ТТХ и агрегатирование'],
  ['faults','Неисправности и ТБ'],
];
const state = { catalog:null, version:null, structure:null, machineId:null, timer:null, saving:Promise.resolve(), dirty:false, mode:'developer' };
const machine = () => state.catalog?.machines.find(item => item.id === state.machineId);
const machineAdjustments = () => machine()?.adjustments || (machine()?.generated ? [] : state.structure.adjustments);
const machineGroups = () => machine()?.generated ? machine().partGroups || [] : state.structure.groups.map(group=>({...group,title:machine()?.partOverrides?.[group.id]?.title || group.title,items:group.items.map(item=>({...item,...machine()?.partOverrides?.[group.id]?.items?.[item.position]}))}));
const draftKey='kolibri-studio-draft';
function rememberDraft() { try { localStorage.setItem(draftKey,JSON.stringify({version:state.version,catalog:state.catalog})); } catch { notify('Не удалось сохранить локальную копию изменений.'); } }
let noticeTimer;
function notify(message) {
  const box=$('#notice');box.textContent=message;box.classList.add('show');
  clearTimeout(noticeTimer);noticeTimer=setTimeout(()=>box.classList.remove('show'),4500);
}
function setStatus(label,error=false) {
  const box=$('#saveStatus');box.classList.toggle('error',error);box.innerHTML=`<i></i> ${escapeHtml(label)}`;
}
async function request(url,options) {
  const response=await fetch(url,options);
  const result=await response.json();
  if(!response.ok)throw Object.assign(new Error(result.error || 'Ошибка запроса'),{status:response.status});
  return result;
}
function previewState() {
  try {
    const doc=$('#phonePreview').contentDocument, app=doc?.querySelector('#app');
    if(!app || !app.dataset.machineId)return null;
    const ds=app.dataset;
    return {machine:ds.machineId,tab:ds.tab || 'adjustments',screen:ds.screen || 'machine',adjustment:ds.adjustmentId || '',step:ds.step || '0',group:ds.group || '',position:ds.position || '',item:doc.querySelector('.machine-item.is-open')?.dataset.item || ''};
  } catch { return null; }
}
function refreshPreview() {
  const params=new URLSearchParams({skip:'1'}),prior=previewState();
  if(prior)for(const [key,value] of Object.entries(prior))if(value)params.set(key,value);
  $('#phonePreview').src=`/preview?${params.toString()}`;
}

function enqueueSave() {
  clearTimeout(state.timer);
  state.dirty=true;
  rememberDraft();
  setStatus('Есть изменения');
  state.timer=setTimeout(()=>save(),500);
}
function save() {
  clearTimeout(state.timer);
  state.saving=state.saving.then(async()=>{
    if(!state.dirty)return;
    state.dirty=false;
    const snapshot=JSON.stringify(state.catalog);
    setStatus('Сохраняем');
    try {
      const result=await request('/api/catalog',{method:'PUT',headers:{'Content-Type':'application/json','If-Match':state.version},body:snapshot});
      state.version=result.version;
      if(state.dirty)rememberDraft();else {localStorage.removeItem(draftKey);$('#draftDownload').hidden=true;}
      setStatus('Все изменения сохранены');
      refreshPreview();
      if(state.dirty)setStatus('Есть изменения');
    } catch(error) {
      state.dirty=true;rememberDraft();$('#draftDownload').hidden=false;if(error.status===409)lockConflict();setStatus('Не сохранено',true);notify(error.message+' Локальная копия сохранена в браузере.');throw error;
    }
  }).catch(()=>{});
  return state.saving;
}
function slot(key,title,subtitle='') {
  const file=machine()?.media?.[key];
  const search=$('#treeSearch').value.trim().toLocaleLowerCase('ru');
  if(($('#missingOnly').checked && file) || (search && !`${title} ${subtitle}`.toLocaleLowerCase('ru').includes(search)))return '';
  return `<div class="tree-row image-slot" tabindex="0" data-slot="${escapeHtml(key)}" aria-label="${escapeHtml(title)}. Нажмите Ctrl+V или перетащите изображение">${file?`<img class="slot-thumb" src="/media/${escapeHtml(file)}" alt="Загруженное изображение">`:''}<div class="slot-copy"><strong>${escapeHtml(title)}</strong><small>${escapeHtml(subtitle || 'Заводская схема')}${file ? `<br>${escapeHtml(file)}` : ''}<br>Перетащите файл или вставьте Ctrl+V</small></div><div class="slot-actions"><label class="upload-label">${file?'Заменить':'＋ Схема'}<input type="file" hidden accept="image/png,image/jpeg,image/webp,image/svg+xml,.svg" data-upload="${escapeHtml(key)}"></label>${file?`<button type="button" class="remove-image" data-remove="${escapeHtml(key)}">Удалить мою схему</button>`:''}</div></div>`;
}
function photoSlot() {
  const file=machine()?.media?.photo;
  return `<div class="slot-main image-slot" tabindex="0" data-slot="photo" aria-label="Фото машины: вставьте изображение Ctrl+V или перетащите файл">${file?`<img class="slot-thumb" src="/media/${escapeHtml(file)}" alt="Загруженное фото">`:'<span class="slot-icon">▧</span>'}<div class="slot-copy"><strong>Главное фото машины</strong><small>${file?escapeHtml(file):machine()?.generated?'Фото машины ещё не загружено':'Используется текущее фото из приложения'}</small></div></div><div class="slot-actions"><label class="upload-label">${file?'Заменить фото':'＋ Загрузить фото'}<input type="file" hidden accept="image/png,image/jpeg,image/webp,image/svg+xml,.svg" data-upload="photo"></label>${file?'<button type="button" class="remove-image" data-remove="photo">Удалить мою схему</button>':''}</div>`;
}
function treeBody(id) {
  const m=machine();
  if(id==='adjustments')return machineAdjustments().map((item,i)=>`<div class="tree-parent">${i+1}. ${escapeHtml(item.title)}</div>${item.steps.map((step,n)=>slot(`adjustments/${item.id}/${n}`,`Шаг ${n+1} · ${step.title}`,step.text)).join('')}`).join('');
  if(id==='parts')return machineGroups().map(group=>`<details class="subgroup"><summary class="sub-summary"><span>${escapeHtml(group.title)}</span><span class="tree-meta">${group.items.length} поз.</span><span class="tree-chevron">›</span></summary><div class="sub-content">${slot(`groups/${group.id}`,`Общая схема · ${group.title}`)}${group.items.map(item=>slot(`parts/${group.id}/${item.position}`,`Поз. ${item.position} · ${item.name}`,item.designation)).join('')}</div></details>`).join('');
  const section=m.sections.find(item=>item.id===id);
  if(!section)return '<p class="empty-note">Пока нет пунктов в разделе.</p>';
  if(id==='nodes')return section.items.map(raw=>{const item={...raw,...m.guideOverrides?.[raw.id]};return slot(`nodes/${item.id}`,`${item.number || ''}. ${item.title}`,[item.badge,item.text,item.description,...(item.steps||[])].filter(Boolean).join(' · '));}).join('');
  return section.items.map(raw=>{const item={...raw,...m.guideOverrides?.[raw.id]};return slot(`${id}/${item.id}`,item.title,[item.badge,item.tool,item.text,item.description,...(item.steps || [])].filter(Boolean).join(' · '));}).join('');
}
function countFor(id) {
  if(id==='parts')return `${machineGroups().reduce((sum,group)=>sum+group.items.length,0)} позиций`;
  if(id==='adjustments')return `${machineAdjustments().length} настроек`;
  return `${machine().sections.find(item=>item.id===id)?.items.length || 0} пунктов`;
}
function renderTree(preserve=true) {
  const filtering=Boolean($('#treeSearch').value.trim() || $('#missingOnly').checked);
  const opened=preserve?[...document.querySelectorAll('.tree-group[open]')].map(el=>el.dataset.section):[];
  const subgroups=preserve?[...document.querySelectorAll('.subgroup[open]')].map(el=>el.querySelector('summary span')?.textContent):[];
  $('#structureModel').textContent=`/ ${machine().model}`;
  $('#photoSlot').innerHTML=photoSlot();
  $('#tree').innerHTML=tabs.map(([id,title],i)=>`<details class="tree-group" data-section="${id}" ${filtering || opened.includes(id)?'open':''}><summary class="tree-summary"><span class="tree-number">${String(i+1).padStart(2,'0')}</span><span class="tree-title">${escapeHtml(title)}</span><span class="tree-meta">${countFor(id)}</span><span class="tree-chevron">›</span></summary><div class="tree-content">${treeBody(id)}</div></details>`).join('');
  for(const sub of document.querySelectorAll('.subgroup'))if(filtering || subgroups.includes(sub.querySelector('summary span')?.textContent))sub.open=true;
}
function renderMachine() {
  const m=machine();if(!m)return;
  $('#machineSelect').innerHTML=state.catalog.machines.map(item=>`<option value="${escapeHtml(item.id)}" ${item.id===state.machineId?'selected':''}>${escapeHtml(item.model)}</option>`).join('');
  $('#machineModel').value=m.model;$('#machineType').value=m.machine_type;
  renderTree(false);
}
async function uploadFile(file,key,target=machine()) {
  if(!file || !target || !key)return;
  if(file.size>8*1024*1024){notify('Максимальный размер изображения — 8 МБ.');return;}
  setStatus('Загружаем изображение');
  try {
    const result=await request('/api/media',{method:'POST',headers:{'Content-Type':file.type==='image/svg+xml' || file.name?.toLowerCase().endsWith('.svg') ? 'image/svg+xml' : 'application/octet-stream'},body:file});
    target.media ||= {};target.media[key]=result.filename;
    renderTree();enqueueSave();await save();
    if(!state.dirty)notify('Изображение добавлено. Оно войдёт в следующую офлайн-сборку.');
  } catch(error){setStatus('Ошибка загрузки',true);notify(error.message);}
}
function lockConflict() { $('#developerPanel').inert=true;$('#machineSelect').disabled=true;$('#draftDiscard').hidden=false; }
async function selectPdf(file) {
  if(!file)return;
  if(file.type!=='application/pdf' && !file.name.toLowerCase().endsWith('.pdf')){notify('Выберите файл PDF.');return;}
  if(file.size>25*1024*1024){notify('PDF больше 25 МБ.');return;}
  if($('#updateMachine').checked && !confirm('Обновить выбранную машину данными из PDF? Предыдущее содержимое будет заменено.'))return;
  await save();if(state.dirty){notify('Сначала сохраните текущие изменения.');return;}
  $('#choosePdf').disabled=true;
  try {
    $('#pdfState').textContent='Чтение страниц PDF';
    const {id}=await request('/api/parse',{method:'POST',headers:{'Content-Type':'application/pdf'},body:file});
    const labels={reading:'Чтение страниц PDF',structure:'Выделение узлов и ТТХ',adjustments:'Построение пошаговых регулировок',parts:'Сборка каталога деталей до винтика',done:'Готово!'};
    let job;
    do { await new Promise(resolve=>setTimeout(resolve,350));job=await request(`/api/parse/${encodeURIComponent(id)}`);$('#pdfState').textContent=`${labels[job.stage] || 'Обработка'}${job.total ? ` · ${job.page}/${job.total}` : ''}`; } while(!['done','error'].includes(job.stage));
    if(job.stage==='error')throw new Error(job.error);
    let imported=job.machine;
    if($('#useAiPdf').checked){
      try {
        const {batches}=await request(`/api/parse/${id}/ai/prepare`,{method:'POST'}),answers=[];
        for(let i=0;i<batches.length;i++){$('#pdfState').textContent=`Пояснения ИИ: ${i+1} из ${batches.length}`;const result=await browserComplete(aiConfig(),batches[i],3000);answers.push(result.content);}
        imported=(await request(`/api/parse/${id}/ai/finish`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({answers})})).machine;
      } catch(error){imported.warnings.push('Облачный ИИ недоступен или ответ не прошёл проверку; использован встроенный разбор.');}
    }
    $('#pdfState').textContent='Готово!';
    if($('#updateMachine').checked){ imported.id=state.machineId;imported.media=machine().media || {};state.catalog.machines[state.catalog.machines.findIndex(item=>item.id===state.machineId)]=imported; }
    else {imported.id=`pdf-${crypto.randomUUID()}`;state.catalog.machines.push(imported);state.machineId=imported.id;}
    renderMachine();enqueueSave();await save();
    if(state.dirty)throw new Error('Карточка не сохранена; скачайте локальную копию.');
    $('#pdfWarnings').textContent=(imported.warnings || []).join(' ');
    $('#phonePreview').src=`/preview?skip=1&machine=${encodeURIComponent(state.machineId)}&tab=adjustments`;
    notify('Карточка создана. Проверьте распознанные сведения по руководству.');
  } catch(error){$('#pdfState').textContent='Ошибка разбора';notify(error.message);}
  finally {$('#choosePdf').disabled=false;$('#pdfInput').value='';}
}

function setMode(mode) {
  state.mode=mode;
  $('#developerPanel').hidden=mode!=='developer';$('#userPanel').hidden=mode!=='user';
  for(const button of document.querySelectorAll('[data-mode]')){const active=button.dataset.mode===mode;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));}
  if(mode==='user')fitPhone();
}
function fitPhone(){const screen=$('.phone-screen');const frame=$('#phonePreview');if(screen.clientWidth)frame.style.transform=`scale(${screen.clientWidth/390})`;}
for(const button of document.querySelectorAll('[data-mode]'))button.addEventListener('click',()=>setMode(button.dataset.mode));
$('#machineSelect').addEventListener('change',event=>{state.machineId=event.target.value;renderMachine();refreshPreview();});
for(const [selector,key] of [['#machineModel','model'],['#machineType','machine_type']])$(selector).addEventListener('input',event=>{
  const value=event.target.value;
  if(key==='model'&&!value.trim()){setStatus('Введите название',true);return;}
  machine()[key]=value;
  if(key==='model'){$('#structureModel').textContent=`/ ${value}`;$('#machineSelect').selectedOptions[0].textContent=value;}
  enqueueSave();
});

const providers={
  free:{url:'https://api.llm7.io/v1/chat/completions',models:['default','DeepSeek-V4-Flash-0731','GLM-5.3-Flash']},
  groq:{url:'https://api.groq.com/openai/v1/chat/completions',models:['openai/gpt-oss-120b','qwen/qwen3.8-27b']},
  mistral:{url:'https://api.mistral.ai/v1/chat/completions',models:['mistral-small-latest','mistral-large-latest']},
  openrouter:{url:'https://openrouter.ai/api/v1/chat/completions',models:['openrouter/free']},
  custom:{url:'',models:[]},
};
function updateProvider(){const p=$('#aiProvider').value,option=providers[p];$('#apiUrl').value=option.url;$('#apiUrl').readOnly=p!=='custom';$('#apiModel').value=option.models[0] || '';$('#modelOptions').innerHTML=option.models.map(model=>`<option value="${escapeHtml(model)}"></option>`).join('');$('#keyLabel').hidden=p==='free';$('#apiKey').value='';$('#aiTestResult').textContent='';}
function aiConfig(){return {provider:$('#aiProvider').value,url:$('#apiUrl').value.trim(),model:$('#apiModel').value.trim(),key:$('#apiKey').value.trim()};}
$('#aiProvider').addEventListener('change',updateProvider);updateProvider();
async function browserComplete(config,messages,tokens=2500,validate) {
  const p=providers[config.provider];if(!p)throw new Error('Неизвестный провайдер.');
  let url=config.url,model=config.model,key=config.provider==='free'?'unused':config.key;
  if(config.provider==='custom' && url && !/\/chat\/completions\/?(?:\?|$)/.test(url))url=url.replace(/\/$/,'')+'/chat/completions';
  if(!url || !model)throw new Error('Укажите адрес и модель.');
  const parsed=new URL(url,location.href);
  if(parsed.username || parsed.password || parsed.hash || (parsed.protocol!=='https:' && !(parsed.protocol==='http:' && ['localhost','127.0.0.1','[::1]'].includes(parsed.hostname))))throw new Error('Используйте HTTPS для внешнего API.');
  if(config.provider!=='free' && !key && !['localhost','127.0.0.1','[::1]'].includes(parsed.hostname))throw new Error('Укажите API-ключ.');
  const attempts=[{url:parsed.href,model,key,name:config.provider}];
  if(config.provider==='free')attempts.push({url:'https://text.pollinations.ai/openai',model:'openai',key:'',name:'pollinations'});
  for(const attempt of attempts){
    try {
      const headers={'Content-Type':'application/json'};if(attempt.key)headers.Authorization=`Bearer ${attempt.key}`;
      const response=await fetch(attempt.url,{method:'POST',mode:'cors',credentials:'omit',headers,signal:AbortSignal.timeout(25000),body:JSON.stringify({model:attempt.model,messages,temperature:0.2,max_tokens:tokens})});
      if(!response.ok)throw new Error(`API: ${response.status}`);
      const data=await response.json(),content=data.choices?.[0]?.message?.content;
      if(typeof content!=='string'||!content.trim()||content.length>500000)throw new Error('Некорректный ответ модели.');
      const validated=validate ? await validate(content) : null;
      return {content,validated,provider:attempt.name,model:attempt.model};
    } catch(error){if(error.status===409)throw error;if(attempt===attempts.at(-1))throw new Error('Не удалось обратиться к ИИ из браузера: проверьте сеть, CORS, ключ и доступность сервиса.');}
  }
}
$('#testAi').addEventListener('click',async()=>{
  const button=$('#testAi');button.disabled=true;$('#aiTestResult').textContent='Проверяем связь из браузера…';const start=performance.now();
  try{const result=await browserComplete(aiConfig(),[{role:'user',content:'Ответь одним словом: привет'}],40);$('#aiTestResult').textContent=`${result.provider} · ${result.model} · ${Math.round(performance.now()-start)} мс`;}
  catch(error){$('#aiTestResult').textContent=error.message;}
  finally{button.disabled=false;}
});

const chatHistory=[];
function chatLine(text,type){const box=document.createElement('div');box.className=`chat-message ${type || ''}`;box.textContent=text;$('#chatLog').append(box);$('#chatLog').scrollTop=$('#chatLog').scrollHeight;}
$('#chatSettings').addEventListener('click',()=>{setMode('developer');$('.engine-card').scrollIntoView({behavior:'smooth',block:'center'});});
$('#chatForm').addEventListener('submit',async event=>{
  event.preventDefault();const message=$('#chatInput').value.trim();if(!message)return;
  await save();if(state.dirty){chatLine('Сначала сохраните изменения каталога.','error');return;}
  const previous=previewState(),machineId=previous?.machine || state.machineId;
  const screen=previous || {tab:'adjustments',screen:'home',step:'0'};
  const selectionIsCurrent=state.selectionScope===`${screen.machine || machineId}:${screen.tab}:${screen.screen}:${screen.adjustment || ''}:${screen.step || ''}`;
  const selected=screen.item ? {id:screen.item,label:selectionIsCurrent?state.selection || '':''} : selectionIsCurrent && state.selection ? {label:state.selection} : null;
  const prior=chatHistory.slice(-8);chatLine(message,'user');$('#chatInput').value='';$('#chatSend').disabled=true;$('#developerPanel').inert=true;$('#machineSelect').disabled=true;$('#phonePreview').style.pointerEvents='none';
  try {
    const prepared=await request('/api/chat/prepare',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({machineId,screen,selection:selected,message,history:prior,version:state.version})});
    const model=await browserComplete(aiConfig(),prepared.messages,8000,response=>request('/api/chat/finish',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:prepared.id,response})}));
    const result=model.validated;
    chatLine(result.reply,'assistant');chatHistory.push({role:'user',content:message},{role:'assistant',content:result.reply});
    if(result.changed){state.catalog=result.catalog;state.version=result.version;state.machineId=machineId;renderMachine();setStatus('Все изменения сохранены');refreshPreview();chatLine('Изменения сохранены. Сверьте значения с руководством: схемы автоматически не перерисовываются.','assistant');}
  } catch(error){chatLine(error.message,'error');if(error.status===409)notify('Каталог изменился в другой вкладке. Обновите страницу.');}
  finally{$('#chatSend').disabled=false;$('#developerPanel').inert=false;$('#machineSelect').disabled=false;$('#phonePreview').style.pointerEvents='';}
});
let pollingBuild=false;
$('#openBuild').addEventListener('click',()=>$('#buildDialog').showModal());
$('#closeBuild').addEventListener('click',()=>$('#buildDialog').close());
async function watchBuild(id){
  if(pollingBuild)return;pollingBuild=true;$('#startBuild').disabled=true;$('#buildResult').hidden=true;
  try {
    let job;
    do {
      job=await request(`/api/build/${encodeURIComponent(id)}`);
      $('#buildStatus').textContent=job.message;
      if(job.runUrl){$('#buildRun').href=job.runUrl;$('#buildRun').hidden=false;}
      if(!['done','error'].includes(job.stage))await new Promise(resolve=>setTimeout(resolve,2500));
    } while(!['done','error'].includes(job.stage));
    if(job.stage==='done'){$('#downloadApk').href=job.downloadUrl;$('#buildQr').src=`/api/build/${encodeURIComponent(id)}/qr`;$('#buildResult').hidden=false;}
    sessionStorage.removeItem('kolibri-build-id');
  } catch(error){$('#buildStatus').textContent=error.message;}
  finally{pollingBuild=false;$('#startBuild').disabled=false;}
}
$('#startBuild').addEventListener('click',async()=>{
  await save();if(state.dirty){$('#buildStatus').textContent='Сначала сохраните каталог.';return;}
  $('#startBuild').disabled=true;$('#buildStatus').textContent='Подготавливаем сборку…';
  try {const job=await request('/api/build',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({version:state.version})});sessionStorage.setItem('kolibri-build-id',job.id);await watchBuild(job.id);}
  catch(error){$('#buildStatus').textContent=error.message;$('#startBuild').disabled=false;}
});
$('#downloadStand').addEventListener('click',async()=>{
  await save();if(state.dirty){$('#standStatus').textContent='Сначала сохраните каталог.';return;}
  const button=$('#downloadStand');button.disabled=true;$('#standStatus').textContent='Собираем стенд…';
  try{const response=await fetch('/api/stand',{method:'POST'});if(!response.ok){const data=await response.json();throw new Error(data.error || 'Ошибка сборки стенда.');}const blob=await response.blob(),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download='Kolibri-stend.html';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);$('#standStatus').textContent='Стенд готов.';}
  catch(error){$('#standStatus').textContent=error.message;}
  finally{button.disabled=false;}
});
const rememberedBuild=sessionStorage.getItem('kolibri-build-id');if(rememberedBuild)watchBuild(rememberedBuild);
$('#treeSearch').addEventListener('input',()=>renderTree());
$('#missingOnly').addEventListener('change',()=>renderTree());
$('#deleteMachine').addEventListener('click',()=>{if(state.machineId==='grn471'){notify('Эталонную карточку удалить нельзя.');return;}if(state.catalog.machines.length<=1){notify('Последнюю машину удалить нельзя.');return;}if(!confirm(`Удалить «${machine().model}» из каталога?`))return;state.catalog.machines=state.catalog.machines.filter(item=>item.id!==state.machineId);state.machineId=state.catalog.machines[0].id;renderMachine();enqueueSave();});
for(const root of [$('#tree'),$('#photoSlot')]){
  root.addEventListener('dragover',event=>{const slot=event.target.closest('[data-slot]');if(slot){event.preventDefault();slot.classList.add('dragging');}});
  root.addEventListener('dragleave',event=>event.target.closest('[data-slot]')?.classList.remove('dragging'));
  root.addEventListener('drop',event=>{const slot=event.target.closest('[data-slot]');if(!slot)return;event.preventDefault();slot.classList.remove('dragging');uploadFile(event.dataTransfer.files?.[0],slot.dataset.slot);});
  root.addEventListener('paste',event=>{const slot=event.target.closest('[data-slot]');if(!slot)return;const file=[...(event.clipboardData?.items || [])].find(item=>item.kind==='file' && item.type.startsWith('image/'))?.getAsFile();if(file){event.preventDefault();uploadFile(file,slot.dataset.slot);}});
}
$('#tree').addEventListener('change',event=>{if(event.target.matches('[data-upload]'))uploadFile(event.target.files?.[0],event.target.dataset.upload);});
$('#photoSlot').addEventListener('change',event=>{if(event.target.matches('[data-upload]'))uploadFile(event.target.files?.[0],event.target.dataset.upload);});
document.addEventListener('click',event=>{
  const remove=event.target.closest('[data-remove]');if(!remove)return;
  delete machine().media[remove.dataset.remove];renderTree();enqueueSave();
});
$('#choosePdf').addEventListener('click',()=>$('#pdfInput').click());
$('#addPdf').addEventListener('click',()=>{setMode('developer');$('#pdfZone').scrollIntoView({behavior:'smooth',block:'center'});$('#pdfInput').click();});
$('#pdfInput').addEventListener('change',event=>selectPdf(event.target.files?.[0]));
for(const type of ['dragenter','dragover'])$('#pdfZone').addEventListener(type,event=>{event.preventDefault();$('#pdfZone').classList.add('dragging');});
for(const type of ['dragleave','drop'])$('#pdfZone').addEventListener(type,event=>{event.preventDefault();$('#pdfZone').classList.remove('dragging');if(type==='drop')selectPdf(event.dataTransfer.files?.[0]);});
$('#previewHome').addEventListener('click',()=>{$('#phonePreview').src='/preview?skip=1';state.selection='';$('#selectionTitle').textContent='Откройте раздел на телефоне или задайте вопрос';$('#selectionHint').textContent='Если пункт не выделен, используется открытый раздел.';});
$('#previewSkip').addEventListener('click',()=>$('#phonePreview').contentWindow.postMessage({type:'skip'},location.origin));
window.addEventListener('message',event=>{
  if(event.origin!==location.origin||event.source!==$('#phonePreview').contentWindow||event.data?.type!=='preview-selection')return;
  $('#selectionTitle').textContent=event.data.label||'Выбранный пункт';$('#selectionHint').textContent='Для общей правки можно просто описать весь открытый раздел.';state.selection=event.data.label || '';const scope=previewState();state.selectionScope=scope?`${scope.machine}:${scope.tab}:${scope.screen}:${scope.adjustment || ''}:${scope.step || ''}`:'';
});
$('#draftDiscard').addEventListener('click',()=>{if(confirm('Удалить локальную копию и загрузить текущий каталог?')){localStorage.removeItem(draftKey);state.dirty=false;location.reload();}});
$('#draftDownload').addEventListener('click',()=>{const data=localStorage.getItem(draftKey);if(!data)return;const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([data],{type:'application/json'}));a.download='kolibri-studio-draft.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);});
new ResizeObserver(fitPhone).observe($('.phone-screen'));
window.addEventListener('beforeunload',event=>{if(state.dirty){event.preventDefault();event.returnValue='';}});
try {
  const [catalog,structure]=await Promise.all([request('/api/catalog'),request('/api/structure')]);
  state.catalog=catalog.catalog;state.version=catalog.version;state.structure=structure;
  try { const draft=JSON.parse(localStorage.getItem(draftKey));if(draft?.version===state.version&&draft.catalog?.machines){state.catalog=draft.catalog;state.dirty=true;notify('Восстановлены несохранённые изменения.');}else if(draft){notify('Есть локальная копия после конфликта. Скачайте её, прежде чем продолжить.');$('#draftDownload').hidden=false;lockConflict();} } catch {}
  state.machineId=state.catalog.machines[0]?.id;
  if(!machine())throw new Error('В каталоге нет машины.');
  renderMachine();if(state.dirty){setStatus('Есть изменения');enqueueSave();}else setStatus('Все изменения сохранены');
} catch(error){setStatus('Ошибка загрузки',true);notify(error.message);}
