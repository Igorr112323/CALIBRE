const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
export function generatedPartAt(machine, groupId, position) {
  return machine.partGroups?.find(group => group.id === groupId)?.items.find(item => item.position === Number(position));
}
export function renderGeneratedParts(machine, filter, openId, mediaFor) {
  const q=filter.toLocaleLowerCase('ru').trim();
  const groups=(machine.partGroups || []).map((group,index)=>{
    const items=group.items.filter(item=>!q || `${item.name} ${item.designation} ${item.position}`.toLocaleLowerCase('ru').includes(q));
    if(!items.length && q)return '';
    const open=q || openId===group.id, image=mediaFor(`groups/${group.id}`);
    return `<section class="parts-group${open?' is-open':''}" data-group="${esc(group.id)}"><button class="parts-group-toggle" data-action="toggle-parts-group" data-group="${esc(group.id)}" aria-expanded="${Boolean(open)}"><span class="choice-number">${index+1}</span><span class="choice-copy"><strong>${esc(group.title)}</strong></span><span class="parts-badge">${group.items.length} поз.</span><span class="parts-chevron"></span></button>${open?`<div class="parts-group-content">${image?`<div class="parts-figure"><img src="${esc(image)}" alt="${esc(group.title)}" data-action="zoom-image"></div>`:'<div class="empty">Схема группы не загружена</div>'}<div class="parts-position-list">${items.map(item=>`<button class="part-row" data-action="open-part" data-group="${esc(group.id)}" data-position="${esc(item.position)}"><span class="part-pos">${esc(item.position)}</span><span class="part-name">${esc(item.name)}</span><span class="parts-badge">${esc(item.quantity)} шт.</span></button>`).join('')}</div></div>`:''}</section>`;
  }).join('');
  return `<div class="parts-view"><label class="parts-filter"><span>⌕</span><input id="partsFilter" type="search" value="${esc(filter)}" placeholder="Найти деталь или номер" aria-label="Найти деталь"></label><div class="parts-groups">${groups || '<div class="parts-empty">Детали не найдены в руководстве.</div>'}</div></div>`;
}
export function renderGeneratedPart(machine, groupId, position, mediaFor) {
  const group=machine.partGroups?.find(item=>item.id===groupId), item=generatedPartAt(machine,groupId,position);
  if(!item)return '<div class="empty">Деталь не найдена</div>';
  const image=mediaFor(`parts/${groupId}/${position}`) || mediaFor(`groups/${groupId}`), index=group.items.indexOf(item);
  return `<div class="part-card"><button class="part-return" data-action="back-parts">‹ К списку узла</button><div class="part-kicker">${esc(group.title)} · ПОЗИЦИЯ ${esc(position)}</div><h2>${esc(item.name)}</h2><div class="part-detail-image">${image?`<img src="${esc(image)}" alt="${esc(item.name)}" data-action="zoom-image">`:'<div class="empty">Схема детали не загружена</div>'}</div><div class="part-info"><h3>Паспорт детали</h3><dl><div><dt>Обозначение</dt><dd>${esc(item.designation || 'Не указано')}</dd></div><div><dt>Количество</dt><dd>${esc(item.quantity)}</dd></div><div><dt>Источник</dt><dd>стр. ${esc(item.page)}</dd></div></dl>${item.note?`<p>${esc(item.note)}</p>`:''}</div><div class="part-info"><h3>Назначение</h3><p>${esc(item.purpose)}</p></div>${item.inspection?`<div class="part-info"><h3>Важно знать при осмотре</h3><p>${esc(item.inspection)}</p></div>`:''}<p class="part-safety">Перед работой сверьтесь с руководством производителя.</p><div class="part-nav"><button data-action="part-prev" ${index===0?'disabled':''}>← Назад</button><button data-action="part-next" ${index===group.items.length-1?'disabled':''}>Далее →</button></div></div>`;
}
