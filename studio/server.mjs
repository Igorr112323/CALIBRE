import { createServer as createHttpServer } from 'node:http';
import { createHash } from 'node:crypto';
import { readFile, readdir, mkdir, rename, writeFile, stat, mkdtemp, rm, access } from 'node:fs/promises';
import os from 'node:os';
import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { DOMParser, XMLSerializer } from '../mobile/node_modules/@xmldom/xmldom/lib/index.js';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createServer as createViteServer } from '../mobile/node_modules/vite/dist/node/index.js';
import { adjustments } from '../web/app/wizard.js';
import { prepareChat, finishChat } from './chat.mjs';
import { pdfBatches, applyPdf } from './pdf-ai.mjs';
import { startBuild, getBuild, buildStand } from './build.mjs';

const studio = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(studio, '..');
const app = path.join(root, 'web/app');
const media = path.join(app, 'media');
const catalogPath = path.join(app, 'catalog.json');
const partsPath = path.join(app, 'parts-data.json');
const port = Number(process.env.PORT || 4175);
const maxImage = 8 * 1024 * 1024;
const hash = data => createHash('sha256').update(data).digest('hex');
let mutation = Promise.resolve();
const jobs = new Map();
const chatSessions = new Map();

function json(res, status, data, extra = {}) {
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', ...extra });
  res.end(JSON.stringify(data));
}
function body(req, limit) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on('data', chunk => {
      size += chunk.length;
      if (size > limit) { reject(Object.assign(new Error('Файл или запрос слишком большой'), { status: 413 })); req.destroy(); return; }
      chunks.push(chunk);
    });
    req.on('end', () => resolve(Buffer.concat(chunks)));
    req.on('error', reject);
  });
}
function sameOrigin(req) {
  if (req.headers['sec-fetch-site']) return req.headers['sec-fetch-site'] === 'same-origin';
  const source = req.headers.origin || req.headers.referer;
  if (!source) return false;
  try { return new URL(source).host === req.headers.host; } catch { return false; }
}
async function currentCatalog() {
  const raw = await readFile(catalogPath);
  return { catalog: JSON.parse(raw.toString('utf8')), version: hash(raw) };
}
function validCatalog(value) {
  if (!value || !Array.isArray(value.machines) || value.machines.length > 100) return false;
  const ids = new Set();
  for (const machine of value.machines) {
    if (!machine || typeof machine.id !== 'string' || !machine.id || ids.has(machine.id) || typeof machine.model !== 'string' || !machine.model.trim() || typeof machine.machine_type !== 'string' || !Array.isArray(machine.sections)) return false;
    ids.add(machine.id);
    if (machine.media !== undefined && (!machine.media || Array.isArray(machine.media) || typeof machine.media !== 'object' || Object.values(machine.media).some(file => typeof file !== 'string' || !/^[a-f0-9]{64}\.(png|jpg|webp|svg)$/.test(file)))) return false;
  }
  return true;
}
function detectImage(data) {
  if (data.length >= 8 && data.subarray(0, 8).equals(Buffer.from([137,80,78,71,13,10,26,10]))) return 'png';
  if (data.length >= 3 && data[0] === 255 && data[1] === 216 && data[2] === 255) return 'jpg';
  if (data.length >= 12 && data.toString('ascii', 0, 4) === 'RIFF' && data.toString('ascii', 8, 12) === 'WEBP') return 'webp';
  return null;
}

function sanitizeSvg(data) {
  const text=data.toString('utf8');
  if (/<!DOCTYPE|<!ENTITY|<\?|<script|<foreignObject/i.test(text)) return null;
  const errors=[];
  const doc=new DOMParser({onError:(level,message)=>errors.push(message)}).parseFromString(text,'image/svg+xml');
  const allowed=new Set(['svg','g','path','rect','circle','ellipse','line','polyline','polygon','text','tspan','defs','linearGradient','radialGradient','stop','clipPath','mask','pattern','title','desc']);
  if(errors.length || doc.documentElement?.localName!=='svg')return null;
  function visit(node) {
    if(node.nodeType===1){
      if(!allowed.has(node.localName))return false;
      for(let i=0;i<node.attributes.length;i++){
        const attr=node.attributes.item(i),name=attr.name.toLowerCase(),value=attr.value;
        if(name==='xmlns' && value==='http://www.w3.org/2000/svg')continue;
        if(name.startsWith('on') || name.includes('href') || name==='style' || /(?:javascript:|data:|https?:|file:|\/\/|expression\s*\()/i.test(value) || (/url\s*\(/i.test(value) && !/^url\(#[A-Za-z0-9_.:-]+\)$/.test(value.trim())))return false;
      }
    }
    for(let child=node.firstChild;child;child=child.nextSibling)if(!visit(child))return false;
    return true;
  }
  return visit(doc.documentElement) ? Buffer.from(new XMLSerializer().serializeToString(doc.documentElement)) : null;
}
async function parseJob(job, data) {
  const folder=await mkdtemp(path.join(os.tmpdir(),'kolibri-pdf-'));
  const file=path.join(folder,'input.pdf');
  try {
    await writeFile(file,data);
    let python=process.env.KOLIBRI_PYTHON || path.join(studio,'.venv/bin/python');
    try { await access(python); } catch { python='python3'; }
    await new Promise((resolve,reject)=>{
      const child=spawn(python,[path.join(studio,'parser.py'),file],{stdio:['ignore','pipe','pipe']});
      let buffer='',errors='';
      child.stdout.setEncoding('utf8');child.stderr.setEncoding('utf8');
      child.stdout.on('data',chunk=>{buffer+=chunk;let at;while((at=buffer.indexOf('\n'))>=0){const line=buffer.slice(0,at);buffer=buffer.slice(at+1);try{const event=JSON.parse(line);if(event.stage==='done'){job.machine=event.machine;job.stage='parts';}else Object.assign(job,event);}catch{}}});
      child.stderr.on('data',chunk=>{errors+=chunk.slice(0,2000)});
      child.on('error',reject);
      child.on('close',code=>code===0 ? resolve() : reject(new Error(job.error || (errors.includes('pdfplumber') ? 'Установите зависимости студии: studio/.venv/bin/pip install -r studio/requirements.txt' : 'Не удалось разобрать PDF.'))));
    });
    job.stage='done';job.finished=Date.now();
  } catch(error){job.stage='error';job.error=error.message;job.finished=Date.now();}
  finally {await rm(folder,{recursive:true,force:true});}
}

function enqueue(fn) {
  const task = mutation.then(fn);
  mutation = task.catch(() => {});
  return task;
}
const previewBridge = `<style>.studio-selected{outline:2px solid #82a96d!important;outline-offset:2px}</style><script>window.androidBridge={};window.addEventListener('DOMContentLoaded',()=>{const skip=()=>{const splash=document.querySelector('#launchSplash');if(splash)splash.classList.add('dismiss');const video=document.querySelector('#launchAnimation');if(video)video.pause()};const params=new URLSearchParams(location.search);if(params.has('skip'))skip();const machine=params.get('machine');if(machine){const card=[...document.querySelectorAll('.machine-card')].find(el=>el.dataset.id===machine);card?.click();const tab=params.get('tab');if(tab)[...document.querySelectorAll('[data-tab]')].find(el=>el.dataset.tab===tab)?.click();if(params.get('screen')==='wizard'){[...document.querySelectorAll('[data-action=open-adjustment]')].find(el=>el.dataset.id===params.get('adjustment'))?.click();const n=Math.min(100,Number(params.get('step'))||0);for(let i=0;i<n;i++)document.querySelector('[data-action=wizard-next-step]')?.click()}else if(params.get('screen')==='part'){[...document.querySelectorAll('[data-action=toggle-parts-group]')].find(el=>el.dataset.group===params.get('group'))?.click();[...document.querySelectorAll('.part-row')].find(el=>el.dataset.group===params.get('group')&&el.dataset.position===params.get('position'))?.click()}else if(tab==='parts'&&params.get('group'))[...document.querySelectorAll('[data-action=toggle-parts-group]')].find(el=>el.dataset.group===params.get('group'))?.click();else if(params.get('item'))[...document.querySelectorAll('.machine-item')].find(el=>el.dataset.item===params.get('item'))?.querySelector('.machine-item-toggle')?.click()}window.addEventListener('message',e=>{if(e.origin!==location.origin||e.source!==window.parent)return;if(e.data?.type==='skip')skip()});document.addEventListener('click',e=>{const target=e.target.closest('.machine-item-inner p,.machine-item-inner li,.machine-item-toggle,.adjustment-choice,.part-row,.parts-group-toggle,.wizard h2,.wizard h3,.wizard-text,.part-info,.part-card h2');if(target){document.querySelectorAll('.studio-selected').forEach(el=>el.classList.remove('studio-selected'));target.classList.add('studio-selected');window.parent.postMessage({type:'preview-selection',label:(target.innerText||target.textContent||'').trim().replace(/\\s+/g,' ').slice(0,150)},location.origin)}})});</script>`;
const server = createHttpServer();
const vite = await createViteServer({
  configFile: path.join(root, 'mobile/vite.config.mjs'),
  appType: 'custom',
  server: { middlewareMode: true, allowedHosts: true, hmr: false, ws: false, fs: { strict: true, allow: [app, path.join(root, 'mobile/node_modules')] } },
});
await mkdir(media, { recursive: true });
server.on('request', async (req, res) => {
  try {
    const pathname = new URL(req.url, 'http://localhost').pathname;
    if (pathname.startsWith('/api/') && !['GET', 'HEAD'].includes(req.method) && !sameOrigin(req)) return json(res, 403, { error: 'Запрос должен приходить со страницы этой студии.' });
    if (pathname === '/api/catalog' && req.method === 'GET') return json(res, 200, await currentCatalog());
    if (pathname === '/api/catalog' && req.method === 'PUT') {
      if (!req.headers['content-type']?.startsWith('application/json')) return json(res, 415, { error: 'Нужен JSON.' });
      const raw = await body(req, 3 * 1024 * 1024);
      let next;
      try { next = JSON.parse(raw.toString('utf8')); } catch { return json(res, 400, { error: 'Некорректный JSON.' }); }
      if (!validCatalog(next)) return json(res, 400, { error: 'Некорректная структура каталога или имени изображения.' });
      return await enqueue(async () => {
        const before = await currentCatalog();
        if (!req.headers['if-match'] || req.headers['if-match'] !== before.version) return json(res, 409, { error: 'Каталог изменился в другой вкладке. Обновите страницу.', version: before.version });
        for (const machine of next.machines) for (const filename of Object.values(machine.media || {})) {
          try { if (!(await stat(path.join(media, filename))).isFile()) throw new Error(); } catch { return json(res, 400, { error: 'Изображение не найдено в папке media.' }); }
        }
        const output = Buffer.from(JSON.stringify(next, null, 2) + '\n');
        const temporary = path.join(app, `.catalog-${process.pid}-${Date.now()}.tmp`);
        await writeFile(temporary, output);
        await rename(temporary, catalogPath);
        return json(res, 200, { version: hash(output) });
      });
    }
    if (pathname === '/api/structure' && req.method === 'GET') {
      const { catalog } = await currentCatalog();
      const groups = JSON.parse(await readFile(partsPath, 'utf8'));
      return json(res, 200, { machines: catalog.machines, adjustments: adjustments.map(a => ({ id: a.id, title: a.title, steps: a.steps.map(s => ({title:s.title,text:s.text})) })), groups: groups.map(g => ({ id: g.id, title: g.title, items: g.items.map(i => ({ position: i.position, name: i.name, designation: i.designation })) })) });
    }
    if (pathname === '/api/media' && req.method === 'GET') {
      const names = (await readdir(media)).filter(name => /^[a-f0-9]{64}\.(png|jpg|webp|svg)$/.test(name));
      return json(res, 200, { files: names });
    }
    if (pathname === '/api/media' && req.method === 'POST') {
      let data = await body(req, maxImage);
      let ext = detectImage(data);
      if (!ext && (req.headers['content-type'] || '').includes('svg')) { data=sanitizeSvg(data); if(data)ext='svg'; }
      if (!ext) return json(res, 415, { error: 'Нужен PNG, JPEG, WebP или безопасный SVG без скриптов и внешних ссылок.' });
      const filename = `${hash(data)}.${ext}`;
      await writeFile(path.join(media, filename), data, { flag: 'w' });
      return json(res, 201, { filename, url: `/media/${filename}` });
    }
    if (pathname === '/api/build' && req.method === 'POST') {
      let args;try{args=JSON.parse((await body(req,8192)).toString('utf8'));}catch{return json(res,400,{error:'Некорректный запрос сборки.'});}
      try{return json(res,202,startBuild(args.version));}catch(error){return json(res,409,{error:error.message});}
    }
    if (pathname.startsWith('/api/build/') && req.method === 'GET') {
      const suffix=pathname.slice('/api/build/'.length),isQr=suffix.endsWith('/qr'),id=isQr?suffix.slice(0,-3):suffix,job=getBuild(id);
      if(!job)return json(res,404,{error:'Сборка не найдена.'});
      if(isQr){if(!job.qr)return json(res,404,{error:'QR-код ещё не готов.'});res.writeHead(200,{'Content-Type':'image/svg+xml','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});return res.end(job.qr);}
      const {qr,...status}=job;return json(res,200,status);
    }
    if (pathname === '/api/stand' && req.method === 'POST') {
      try{const html=await buildStand();res.writeHead(200,{'Content-Type':'text/html; charset=utf-8','Content-Disposition':'attachment; filename="Kolibri-stend.html"','Cache-Control':'no-store','X-Content-Type-Options':'nosniff'});return res.end(html);}catch(error){return json(res,409,{error:error.message || 'Ошибка сборки стенда.'});}
    }
    if (pathname === '/api/chat/prepare' && req.method === 'POST') {
      let args;
      try {args=JSON.parse((await body(req,128*1024)).toString('utf8'));}catch{return json(res,400,{error:'Некорректный запрос чата.'});}
      if(typeof args.message!=='string' || !args.message.trim() || args.message.length>4000 || !args.screen || !['adjustments','parts','maintenance','nodes','storage','specs','faults','problems'].includes(args.screen.tab))return json(res,400,{error:'Нужны сообщение и корректный раздел.'});
      const current=await currentCatalog();
      if(args.version!==current.version)return json(res,409,{error:'Каталог изменился в другой вкладке. Обновите студию.'});
      const machine=current.catalog.machines.find(m=>m.id===args.machineId);
      if(!machine)return json(res,404,{error:'Машина не найдена.'});
      try {
        const guideMap=(await vite.ssrLoadModule('/guides.js')).guides;
        const rawGroups=JSON.parse(await readFile(partsPath,'utf8'));
        const {partExplanation}=await vite.ssrLoadModule('/parts.js');
        const groups=rawGroups.map(g=>({...g,items:g.items.map(i=>({...i,...partExplanation(g,i)}))}));
        const prepared=prepareChat(machine,args.screen,args.selection,args.message,args.history,adjustments,guideMap,groups);
        for(const [id,session] of chatSessions)if(Date.now()-session.created>15*60*1000)chatSessions.delete(id);
        if(chatSessions.size>=100)return json(res,429,{error:'Слишком много запросов. Повторите позже.'});
        const id=randomUUID();chatSessions.set(id,{created:Date.now(),version:current.version,machineId:machine.id,view:prepared.view});
        return json(res,200,{id,messages:prepared.messages});
      } catch(error){return json(res,400,{error:error.message || 'Ошибка подготовки контекста.'});}
    }
    if (pathname === '/api/chat/finish' && req.method === 'POST') {
      let args;try{args=JSON.parse((await body(req,1024*1024)).toString('utf8'));}catch{return json(res,400,{error:'Некорректный ответ модели.'});}
      const session=chatSessions.get(args.id);
      if(!session || Date.now()-session.created>15*60*1000)return json(res,404,{error:'Контекст запроса истёк. Повторите запрос.'});
      if(typeof args.response!=='string' || args.response.length>500000)return json(res,400,{error:'Ответ модели не содержит текст.'});
      return await enqueue(async()=>{
        if(chatSessions.get(args.id)!==session)return json(res,409,{error:'Ответ уже обработан.'});
        const current=await currentCatalog();
        if(current.version!==session.version){chatSessions.delete(args.id);return json(res,409,{error:'Каталог изменился во время ответа. Изменения модели не применены.'});}
        const machine=current.catalog.machines.find(m=>m.id===session.machineId);
        if(!machine)return json(res,404,{error:'Машина не найдена.'});
        try {
          const result=finishChat(machine,session.view,args.response,adjustments);
          if(!result.changed){chatSessions.delete(args.id);return json(res,200,{...result,version:current.version});}
          if(!validCatalog(current.catalog))return json(res,400,{error:'Ответ модели содержит недопустимые данные.'});
          const output=Buffer.from(JSON.stringify(current.catalog,null,2)+'\n');
          if(output.length>3*1024*1024)return json(res,413,{error:'Каталог слишком велик; изменения не применены.'});
          const temporary=path.join(app,`.catalog-${process.pid}-${Date.now()}.tmp`);await writeFile(temporary,output);await rename(temporary,catalogPath);
          chatSessions.delete(args.id);return json(res,200,{...result,version:hash(output),catalog:current.catalog});
        } catch(error){return json(res,400,{error:error.message || 'Ответ модели не прошёл проверку.'});}
      });
    }
    if (pathname === '/api/parse' && req.method === 'POST') {
      const data=await body(req,25*1024*1024);
      if(data.subarray(0,5).toString()!=='%PDF-')return json(res,415,{error:'Нужен корректный файл PDF.'});
      for(const [id,old] of jobs)if(old.finished && Date.now()-old.finished>3600000)jobs.delete(id);
      if([...jobs.values()].filter(item=>!item.finished).length>=2)return json(res,429,{error:'Дождитесь завершения текущего разбора.'});
      const id=randomUUID(),job={stage:'reading',created:Date.now()};jobs.set(id,job);
      parseJob(job,data);
      return json(res,202,{id});
    }
    if (pathname.startsWith('/api/parse/') && pathname.endsWith('/ai/prepare') && req.method === 'POST') {
      const id=pathname.slice('/api/parse/'.length,-'/ai/prepare'.length),job=jobs.get(id);
      if(!job || job.stage!=='done')return json(res,404,{error:'Задание не найдено или не завершено.'});
      const batches=pdfBatches(job.machine);job.batches=batches;
      return json(res,200,{batches:batches.map(batch=>batch.messages)});
    }
    if (pathname.startsWith('/api/parse/') && pathname.endsWith('/ai/finish') && req.method === 'POST') {
      const id=pathname.slice('/api/parse/'.length,-'/ai/finish'.length),job=jobs.get(id);
      if(!job?.batches)return json(res,404,{error:'Пакеты PDF не подготовлены.'});
      let args;try{args=JSON.parse((await body(req,4*1024*1024)).toString('utf8'));}catch{return json(res,400,{error:'Некорректные ответы модели.'});}
      try{const machine=applyPdf(job.machine,job.batches,args.answers);delete job.batches;return json(res,200,{machine});}catch(error){return json(res,400,{error:error.message});}
    }
    if (pathname.startsWith('/api/parse/') && req.method === 'GET') {
      const job=jobs.get(pathname.slice('/api/parse/'.length));
      return job ? json(res,200,job) : json(res,404,{error:'Задание не найдено.'});
    }
    if (pathname === '/preview' && req.method === 'GET') {
      const html = (await readFile(path.join(app, 'index.html'), 'utf8')).replace('<head>', `<head>${previewBridge}`);
      const transformed = (await vite.transformIndexHtml('/preview', html)).replace(/<script type="module" src="[^"]*\/@vite\/client"><\/script>/, '');
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' });
      return res.end(transformed);
    }
    if (pathname === '/@vite/client' && req.method === 'GET') {
      res.writeHead(200, { 'Content-Type': 'text/javascript; charset=utf-8', 'Cache-Control': 'no-store' });
      return res.end("const styles=new Map();export function updateStyle(id,css){let el=styles.get(id);if(!el){el=document.createElement('style');document.head.appendChild(el);styles.set(id,el)}el.textContent=css}export function removeStyle(id){styles.get(id)?.remove();styles.delete(id)}export function createHotContext(){return {accept(){},prune(){},dispose(){},on(){},invalidate(){}}}");
    }
    const staticFiles = { '/': ['index.html','text/html; charset=utf-8'], '/studio.js': ['studio.js','text/javascript; charset=utf-8'], '/studio.css': ['studio.css','text/css; charset=utf-8'] };
    if (staticFiles[pathname] && req.method === 'GET') {
      const [file, type] = staticFiles[pathname];
      res.writeHead(200, { 'Content-Type': type, 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' });
      return res.end(await readFile(path.join(studio, file)));
    }
    if (pathname.startsWith('/api/')) return json(res, 404, { error: 'Эндпоинт не найден.' });
    return vite.middlewares(req, res, () => { res.writeHead(404); res.end('Not found'); });
  } catch (error) {
    if (!res.headersSent) json(res, error.status || 500, { error: error.status ? error.message : 'Ошибка сервера студии.' });
    else res.end();
    console.error(error);
  }
});
server.listen(port, '0.0.0.0', () => console.log(`Kolibri Studio listening on 0.0.0.0:${port}`));
