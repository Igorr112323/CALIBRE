import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { readFile, readdir } from 'node:fs/promises';
import { createHash, randomUUID } from 'node:crypto';
import path from 'node:path';
import QRCode from 'qrcode';

const exec=promisify(execFile);
const root=path.resolve(import.meta.dirname,'..');
const mobile=path.join(root,'mobile');
const repository='Igorr112323/CALIBRE';
const jobs=new Map();
let busy=false;
let branchAtStart;
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function command(file,args,options={}){
  const {stdout}=await exec(file,args,{cwd:root,timeout:30000,maxBuffer:5*1024*1024,...options});return stdout.trim();
}
async function branch(){return command('git',['branch','--show-current']);}
async function snapshot(){
  const digest=createHash('sha256');digest.update(await readFile(path.join(root,'web/app/catalog.json')));
  const folder=path.join(root,'web/app/media');const names=(await readdir(folder)).filter(name=>/^[a-f0-9]{64}\.(png|jpg|webp|svg)$/.test(name)).sort();
  for(const name of names){digest.update(name);digest.update(await readFile(path.join(folder,name)));}
  return digest.digest('hex');
}
async function ensureBranch(){
  const current=await branch();
  if(!branchAtStart)branchAtStart=current;
  if(!current || current!==branchAtStart || !current.startsWith('arena/'))throw new Error('Текущая ветка изменилась или недоступна; сборка остановлена.');
  return current;
}
async function noOtherBuildChanges(){
  const {stdout:output}=await exec('git',['status','--porcelain=v1','--untracked-files=all'],{cwd:root,timeout:30000,maxBuffer:5*1024*1024});
  const entries=output.split('\n').filter(Boolean);
  const blocked=entries.filter(line=>{const file=line.slice(3);return (file.startsWith('web/app/') && file!=='web/app/catalog.json' && !file.startsWith('web/app/media/')) || file.startsWith('mobile/') || file.startsWith('.github/workflows/');});
  if(blocked.length)throw new Error('Есть незакоммиченные изменения кода сборки. Сначала сохраните их в текущей ветке; иначе APK не будет соответствовать предпросмотру.');
  if(await command('git',['diff','--cached','--name-only']))throw new Error('В индексе Git уже есть подготовленные изменения. Сборка не будет коммитить их автоматически.');
}
async function run(job,expectedVersion){
  try {
    const branchName=await ensureBranch();await noOtherBuildChanges();
    const remote=await command('git',['remote','get-url','origin']);
    if(!/^(https:\/\/github\.com\/|git@github\.com:)Igorr112323\/CALIBRE(?:\.git)?\/?$/.test(remote))throw new Error('Удалённый origin не соответствует Igorr112323/CALIBRE.');
    await command('gh',['api',`repos/${repository}`,'--jq','.full_name']);
    await command('gh',['auth','status']);
    const catalog=await readFile(path.join(root,'web/app/catalog.json'));
    if(createHash('sha256').update(catalog).digest('hex')!==expectedVersion)throw new Error('Каталог изменился: обновите студию перед сборкой.');
    const before=await snapshot();job.stage='packing';job.message='Упаковка карточек и схем';
    await command('npm',['run','cap:sync'],{cwd:mobile,timeout:240000});
    await command('node',['build-stand.mjs'],{cwd:mobile,timeout:120000});
    if(await snapshot()!==before)throw new Error('Каталог или медиа изменились во время упаковки. Повторите сборку.');
    await ensureBranch();await noOtherBuildChanges();
    job.stage='publishing';job.message='Сохранение снимка и отправка в GitHub';
    await command('git',['add','-A','--','web/app/catalog.json','web/app/media']);
    const after=await snapshot();
    if(after!==before)throw new Error('Каталог изменился перед коммитом. Повторите сборку.');
    await command('git',['commit','--allow-empty','-m','Build Kolibri APK from studio']);
    const sha=await command('git',['rev-parse','HEAD']);job.sha=sha;
    await ensureBranch();
    await command('git',['push','origin',branchName],{timeout:180000});
    job.stage='queued';job.message='Ожидание GitHub Actions';
    const deadline=Date.now()+20*60*1000;let run;
    while(Date.now()<deadline){
      const list=JSON.parse(await command('gh',['run','list','--repo',repository,'--workflow','build-android.yml','--branch',branchName,'--limit','30','--json','databaseId,headSha,status,conclusion,url,event']));
      run=list.find(item=>item.headSha===sha && item.event==='push');
      if(run)break;await wait(4000);
    }
    if(!run)throw new Error('GitHub Actions не запустился для этого коммита.');
    job.runId=run.databaseId;job.runUrl=run.url;job.stage='building';job.message='Облачная компиляция Android Gradle';
    while(Date.now()<deadline){
      const info=JSON.parse(await command('gh',['run','view',String(run.databaseId),'--repo',repository,'--json','status,conclusion,jobs,url']));
      const steps=(info.jobs || []).flatMap(item=>item.steps || []);
      if(steps.some(step=>step.name==='Publish latest APK release' && step.status==='in_progress')){job.stage='release';job.message='Публикация APK';}
      if(info.status==='completed'){
        if(info.conclusion!=='success')throw new Error(`GitHub Actions завершился с результатом: ${info.conclusion || 'ошибка'}.`);
        const remote=JSON.parse(await command('gh',['api',`repos/${repository}/actions/runs/${run.databaseId}`,'--jq','{run_attempt:.run_attempt,head_sha:.head_sha}']));
        if(remote.head_sha!==sha)throw new Error('Версия сборки не совпадает с отправленным коммитом.');
        const tag=`kolibri-mehfak-${run.databaseId}-${remote.run_attempt}`;
        const release=JSON.parse(await command('gh',['release','view',tag,'--repo',repository,'--json','tagName,isDraft,isPrerelease,assets']));
        const asset=release.assets?.find(item=>item.name==='Kolibri-Mehfak.apk' && item.state==='uploaded' && item.size>0);
        if(release.isDraft || release.isPrerelease || !asset)throw new Error('Опубликованный релиз не содержит готовый APK.');
        const latest=JSON.parse(await command('gh',['release','view','--repo',repository,'--json','tagName']));
        job.downloadUrl=asset.url;job.tag=tag;job.latest=latest.tagName===tag;job.qr=await QRCode.toString(asset.url,{type:'svg',margin:1,width:220});job.stage='done';job.message=job.latest?'Готово!':'Готово; в репозитории уже есть другой latest-релиз.';job.finished=Date.now();return;
      }
      await wait(5000);
    }
    throw new Error('Время ожидания GitHub Actions истекло. Проверьте сборку по ссылке.');
  } catch(error){job.stage='error';job.message=error.message || 'Ошибка сборки';job.finished=Date.now();}
  finally{busy=false;}
}
export function startBuild(version){
  if(busy)throw new Error('Сборка уже выполняется.');
  if(typeof version!=='string' || !/^[a-f0-9]{64}$/.test(version))throw new Error('Каталог не загружен.');
  busy=true;const job={id:randomUUID(),stage:'starting',message:'Подготовка',created:Date.now()};jobs.set(job.id,job);
  for(const [id,item] of jobs)if(item.finished && Date.now()-item.finished>24*60*60*1000)jobs.delete(id);
  run(job,version);return job;
}
export function getBuild(id){return jobs.get(id);}
export async function buildStand(){
  if(busy)throw new Error('Дождитесь окончания сборки APK.');
  busy=true;try{await command('npm',['run','build:stand'],{cwd:mobile,timeout:240000});return await readFile(path.join(root,'dist/Kolibri-stend.html'));}finally{busy=false;}
}
