import { parseObject } from './llm.mjs';
const method='Ты помощник мехфака. Получены выдержки из руководства. Верни JSON только вида {"items":[{"id":"...","summary":"..."}]}. Объясни каждый пункт и шаг понятным языком новичку, сохраняя смысл. Не добавляй нормы, размеры, допуски, порядок действий или факты, которых нет во входной выдержке. Не меняй каталог деталей и числовые значения. Любые сомнения оставь без объяснения. Пояснения будут показаны рядом с неизменённым текстом и требуют сверки с оригиналом.';
export function pdfBatches(machine){
  const entries=machine.sections.flatMap(section=>section.items.filter(item=>item.text).map(item=>({id:item.id,title:item.title,text:item.text.slice(0,1200)})));
  for(const adjustment of machine.adjustments || [])adjustment.steps.forEach((step,index)=>entries.push({id:`step:${adjustment.id}:${index}`,title:adjustment.title,text:step.text.slice(0,1200)}));
  const batches=[];for(let i=0;i<entries.length;i+=20){const selected=entries.slice(i,i+20);batches.push({selected,messages:[{role:'system',content:method},{role:'user',content:JSON.stringify(selected)}]});}
  return batches;
}
export function applyPdf(machine,batches,answers){
  if(!Array.isArray(answers) || answers.length!==batches.length)throw new Error('Не все пакеты обработаны; исходный разбор сохранён.');
  const safe=new Map();
  for(let i=0;i<batches.length;i++){
    const parsed=parseObject(answers[i],value=>Array.isArray(value.items)), originals=new Map(batches[i].selected.map(item=>[item.id,item]));
    for(const item of parsed.items){const original=originals.get(item.id);if(!original || typeof item.summary!=='string' || item.summary.length>1500)continue;const nums=item.summary.match(/\d+(?:[.,]\d+)?/g) || [];if(nums.some(n=>!`${original.title} ${original.text}`.includes(n)))continue;safe.set(item.id,item.summary);}
  }
  for(const section of machine.sections)for(const item of section.items)if(safe.has(item.id))item.summary=safe.get(item.id);
  for(const adjustment of machine.adjustments || [])adjustment.steps.forEach((step,index)=>{const summary=safe.get(`step:${adjustment.id}:${index}`);if(summary)step.summary=summary;});
  machine.warnings.push(`ИИ добавил пояснения к ${safe.size} пунктам. Проверьте их по руководству; исходный текст сохранён.`);
  return machine;
}
