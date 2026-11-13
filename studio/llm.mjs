export function parseObject(raw, accept=()=>true) {
  if(typeof raw!=='string')throw new Error('Модель не вернула текст');
  const text=raw.replace(/```(?:json)?/gi,'').replace(/```/g,'');
  for(let start=0;start<text.length;start++){
    if(text[start]!=='{')continue;
    let depth=0,quoted=false,escaped=false;
    for(let i=start;i<text.length;i++){
      const c=text[i];
      if(quoted){if(escaped)escaped=false;else if(c==='\\')escaped=true;else if(c==='"')quoted=false;continue;}
      if(c==='"'){quoted=true;continue;}
      if(c==='{')depth++;
      if(c==='}' && --depth===0){try{const result=JSON.parse(text.slice(start,i+1));if(result && typeof result==='object' && !Array.isArray(result) && accept(result))return result;}catch{}break;}
      if(c!=='}')depth+=0;
    }
  }
  throw new Error('Ответ модели не содержит корректный JSON');
}
