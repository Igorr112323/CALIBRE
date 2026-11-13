import groups from './parts-data.json';
import f36 from './parts-figures/36.jpg?inline';
import f37 from './parts-figures/37.jpg?inline';
import f40 from './parts-figures/40.jpg?inline';
import f42 from './parts-figures/42.jpg?inline';
import f44 from './parts-figures/44.jpg?inline';
import f46 from './parts-figures/46.jpg?inline';
import f48 from './parts-figures/48.jpg?inline';
import f50 from './parts-figures/50.jpg?inline';
import f52 from './parts-figures/52.jpg?inline';

export const partGroups = groups;
const images = { 36:f36, 37:f37, 40:f40, 42:f42, 44:f44, 46:f46, 48:f48, 50:f50, 52:f52 };
const bounds = { 36:[102,73,890,590], 37:[97,73,902,602], 40:[129,73,840,592], 42:[115,73,902,604], 44:[65,73,965,569], 46:[82,73,932,596], 48:[97,73,900,584], 50:[90,73,916,591], 52:[101,73,917,508] };
const green = '#326c38';
const yellow = '#f9a825';
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));

export function normalizeParts(value) {
  return String(value ?? '').toLocaleLowerCase('ru').replaceAll('ё','е').replaceAll('m','м').replaceAll('x','х').replaceAll('×','х').replaceAll('*','х').replace(/\s+/g,' ').trim();
}
export function partGroup(id) { return groups.find(group => group.id === id); }
export function partAt(id, position) { return partGroup(id)?.items.find(item => item.position === Number(position)); }
function kind(group, item) {
  const n = item.name.toLowerCase();
  if (group.id === 'tine' && item.position === 2) return 'tooth';
  if (n.includes('шплинт') && (n.includes('пружинный') || n.includes('ост'))) return 'springpin';
  if (n.includes('шплинт')) return 'pin';
  if (n.includes('шайба') && n.includes('6402')) return 'springwasher';
  if (n.includes('шайба') && n.includes('стопорная')) return 'lockwasher';
  if (n.includes('шайба')) return 'washer';
  if (n.includes('болт')) return 'bolt';
  if (n.includes('гайка')) return 'nut';
  if (n.includes('корпус подшипника')) return 'housing';
  if (n.includes('подшипник')) return 'bearing';
  if (n.includes('контрпривод')) return 'drive';
  if (n.includes('каретка')) return 'carriage';
  if (n.includes('навеска')) return 'hitch';
  if (n.includes('кольцо') && (n.includes('гост') || group.id === 'drive')) return 'ring';
  if (n.includes('кольцо')) return 'spacer';
  if (n.includes('шпонка')) return 'key';
  if (n.includes('ось')) return 'axle';
  if (n.includes('шлицевой')) return 'shaft';
  if (n.includes('кардан')) return 'cardan';
  if (n.includes('заглушка')) return 'plug';
  if (n.includes('масленка')) return 'greaser';
  if (n.includes('шина')) return 'tyre';
  if (n.includes('обод')) return 'rim';
  if (n.includes('колесо')) return 'tyre';
  if (n.includes('ступица')) return 'hub';
  if (n.includes('стяжка')) return 'tie';
  if (n.includes('отражатель')) return 'curtain';
  if (n.includes('штанга')) return 'tube';
  if (n.includes('винт') || n.includes('шпилька')) return 'screw';
  if (n.includes('редуктор')) return 'gearbox';
  if (n.includes('кожух')) return 'cover';
  if (n.includes('тубус')) return 'tubeholder';
  if (n.includes('граблина')) return 'tine';
  if (n.includes('фиксатор') || n.includes('стопор')) return 'catch';
  if (n.includes('коромысло')) return 'rocker';
  if (n.includes('стойка')) return 'stand';
  if (n.includes('балка') || n.includes('рама') || n.includes('рамка') || n.includes('ограждение')) return 'frame';
  if (n.includes('кронштейн') || n.includes('державка') || n.includes('прижим') || n.includes('накладка') || n.includes('вкладыш')) return 'bracket';
  if (n.includes('распорка') || n.includes('упор')) return 'spacerbar';
  return 'assembly';
}
const explanations = {
 housing:['Корпус держит подшипники и помогает расположить вал на раме.','Проверьте корпус и крепления на трещины и ослабление.'],
 drive:['Контрпривод передаёт вращение от одного кардана к другому.','При выключенном ВОМ осмотрите корпус и проверьте крепления.'],
 carriage:['Каретка на колёсах поддерживает грабли и помогает им идти над землёй.','Проверьте колёса, крепления и детали регулировки.'],
 hitch:['Навеска соединяет грабли с трёхточечной навеской трактора.','Проверьте пальцы, шплинты и места крепления перед работой.'],
 bolt:['Болт вместе с гайкой стягивает детали соединения.','Проверьте затяжку и резьбу. При замене подберите размер и класс прочности по паспорту.'],
 nut:['Гайка наворачивается на резьбу и удерживает детали вместе.','Проверьте, не ослабла ли она и не повреждена ли резьба.'],
 washer:['Плоская шайба распределяет нажим крепежа и защищает поверхность детали.','Проверьте, что шайба на месте и не погнута.'],
 springwasher:['Разрезная пружинная шайба помогает соединению сохранять натяг.','Осмотрите шайбу на трещины и сильное сплющивание.'],
 lockwasher:['Стопорная шайба помогает удерживать детали от смещения.','Проверьте, что шайба стоит на месте и не повреждена.'],
 pin:['Шплинт проходит через отверстие и не даёт оси выйти из соединения.','Проверьте, что шплинт цел и зафиксирован; повреждённый замените.'],
 springpin:['Пружинный шплинт удерживает палец или фиксатор от выпадения.','Проверьте, что пружинная часть держится и шплинт не потерян.'],
 bearing:['Подшипник помогает валу или колесу вращаться с меньшим трением.','При остановленной машине проверьте люфт, шум и повреждения; номер замены должен совпадать.'],
 ring:['Стопорное кольцо входит в канавку и удерживает детали от сдвига.','Проверьте, что кольцо полностью сидит в канавке и не сломано.'],
 spacer:['Кольцо помогает расположить детали колеса относительно друг друга.','При разборке запомните положение и проверьте кольцо на износ.'],
 key:['Шпонка входит в паз вала и передаёт вращение соединённой детали.','Проверьте износ шпонки и пазов; замену подбирайте по размеру.'],
 axle:['Ось соединяет детали узла и удерживает их в нужном положении.','Проверьте износ оси и наличие удерживающих шплинтов или фиксаторов.'],
 shaft:['Шлицевой вал передаёт вращение через выступы на концах.','При остановленной машине проверьте шлицы и посадочные места на износ.'],
 cardan:['Карданный вал передаёт вращение между узлами привода.','При выключенном ВОМ проверьте крепление, защиту и состояние соединений.'],
 plug:['Заглушка закрывает конец трубы или отверстие.','Проверьте, что она на месте и не треснула.'],
 greaser:['Через маслёнку подают смазку в узел.','Перед смазкой очистите маслёнку; повреждённую замените.'],
 tyre:['Шина помогает колесу катиться по полю.','Осмотрите резину на порезы и проверьте давление по руководству.'],
 rim:['Обод поддерживает шину и соединяется со ступицей колеса.','Проверьте обод и крепёж на трещины и ослабление.'],
 hub:['Ступица соединяет колесо с осью и подшипниками.','При остановленной машине проверьте люфт и состояние ступицы.'],
 tie:['Стяжка удерживает закреплённые вместе детали.','Убедитесь, что она цела и надёжно держит детали.'],
 curtain:['Отражатель направляет сено и помогает сформировать валок.','Осмотрите полотно и крепления: повреждённый фартук плохо удерживает сено.'],
 tube:['Штанга служит основой граблины, на ней закреплены рабочие зубья.','Проверьте трубу и места крепления зубьев на изгибы и трещины.'],
 screw:['Резьбовая деталь соединяет или регулирует положение частей узла.','Проверьте резьбу и отсутствие изгибов; не применяйте силу к повреждённой резьбе.'],
 gearbox:['Редуктор передаёт вращение к ротору граблей.','При остановленной машине осмотрите корпус и проверьте, нет ли течи или повреждений.'],
 cover:['Кожух закрывает опасные вращающиеся части привода.','Не запускайте ВОМ без исправного кожуха и его крепления.'],
 tubeholder:['Тубус служит для хранения документов машины.','Проверьте крепление и сохранность крышки.'],
 tine:['Граблина с зубьями собирает траву при вращении ротора.','При остановленной машине проверьте крепление и состояние пальцев.'],
 tooth:['Двойной пружинный зуб подхватывает траву и помогает собрать её в валок.','Проверьте обе ветви зуба и крепление: сломанный зуб замените.'],
 catch:['Фиксатор удерживает детали в заданном положении.','Проверьте, что он входит до конца и не выпадает.'],
 rocker:['Коромысло соединяет колёса тележки и помогает им проходить неровности.','Проверьте соединения и наличие заметного люфта или трещин.'],
 stand:['Стойка поддерживает и соединяет части узла.','Проверьте стойку и её крепления на изгибы и трещины.'],
 frame:['Рама или рамка объединяет и поддерживает части узла.','Осмотрите сварные швы и крепления на повреждения.'],
 bracket:['Эта деталь помогает закрепить соседние части сборки.','Проверьте её крепление, отсутствие трещин и деформации.'],
 spacerbar:['Деталь поддерживает или фиксирует расстояние между частями узла.','Проверьте крепление и отсутствие изгибов.'],
 assembly:['Это часть сборочного узла, показанная под своим номером на заводском рисунке.','Осмотрите деталь и её соединения; перед заменой сверьте заводское обозначение.']
};
export function partTitle(group, item) {
 const name=item.name;
 if(item._editedName)return name;
 if(group.id==='tine' && item.position===2) return 'Зуб пружинный двойной';
 if(group.id==='hitch' && item.position===5) return 'Стояночная стойка';
 if(group.id==='wheel' && [2,3].includes(item.position)) return `Полуобод ${item.position===2?'первый':'второй'}`;
 if(group.id==='carriage' && [3,4].includes(item.position)) return `Стойка каретки ${item.position===3?'первая':'вторая'}`;
 if(group.id==='carriage' && [5,6].includes(item.position)) return `Коромысло каретки ${item.position===5?'первое':'второе'}`;
 if(group.id==='tine' && item.position===4) return 'Гайка М12 с контргайкой';
 const type=kind(group,item);
 const bolt=name.match(/М(\d+)-[^*]+\*(\d+)/i);
 if(type==='bolt' && bolt) return `Болт М${bolt[1]}×${bolt[2]}`;
 if(type==='nut') return `Гайка ${name.match(/М\d+/)?.[0] || ''}`.trim();
 if(type==='springwasher') return `Пружинная шайба ${name.match(/\d+Т/)?.[0] || ''}`.trim();
 if(type==='washer' && name.includes('ГОСТ')) return `Плоская ${name.replace('Шайба ','шайба ').split(' ГОСТ')[0]}`;
 return name.replace(/ (ГОСТ|ОСТ|ТУ) .*/,'');
}
export function partSearchText(group, item) { return [partTitle(group,item),item.name,item.designation,item.note,group.code,group.title,`позиция ${item.position}`,`поз ${item.position}`].join(' '); }
function sameKey(item) { return normalizeParts(item.designation || item.name); }
function machineQuantity(item) {
  return groups.reduce((total, group) => total + group.items.filter(other => sameKey(other) === sameKey(item)).reduce((sum, other) => sum + other.quantity * group.multiplier, 0), 0);
}
function figureNumber(page) { return ({36:1,37:2,40:3,42:4,44:5,46:6,48:7,50:8,52:9})[page]; }
function imageSvg(group, page, selected = null, interactive = false) {
 const [x,y,w,h] = bounds[page];
 const markers = group.items.filter(item => item.figure === page && (interactive || item.position === selected)).map(item => {
  const [cx,cy]=item.point;
  if (interactive) return `<g class="figure-hotspot" role="button" tabindex="0" aria-label="Открыть позицию ${item.position}: ${esc(partTitle(group,item))}" data-action="open-part" data-group="${group.id}" data-position="${item.position}"><circle cx="${cx}" cy="${cy}" r="20" fill="${item.position === Number(selected) ? yellow : '#fff8e1'}" fill-opacity=".95" stroke="${green}" stroke-width="3"/><text x="${cx}" y="${cy+6}" text-anchor="middle" font-family="Arial,sans-serif" font-size="19" font-weight="bold" fill="${green}">${item.position}</text></g>`;
  return `<circle cx="${cx}" cy="${cy}" r="24" fill="${yellow}" fill-opacity=".35" stroke="${green}" stroke-width="5"/>`;
 }).join('');
 return `<svg viewBox="${x} ${y} ${w} ${h}" role="img" aria-label="Заводской рисунок ${figureNumber(page)}: ${esc(group.title)}"><image href="${images[page]}" x="0" y="0" width="1095" height="774"/>${markers}</svg>`;
}
function match(group,item,filter) {
 const q=normalizeParts(filter); if(!q)return true;
 const terms=q.split(' ').filter(Boolean);
 const haystack=normalizeParts(partSearchText(group,item));
 return terms.every(term=>haystack.includes(term));
}
export function renderParts(filter, openId, mediaFor = () => null, overrides = {}) {
 const displayGroups=groups.map(group=>({...group,title:overrides[group.id]?.title || group.title,items:group.items.map(item=>({...item,...overrides[group.id]?.items?.[item.position],_editedName:overrides[group.id]?.items?.[item.position]?.name !== undefined && overrides[group.id].items[item.position].name !== item.name}))}));
 const q=normalizeParts(filter);
 const resultCount=displayGroups.reduce((n,g)=>n+g.items.filter(item=>match(g,item,filter)).length,0);
 return `<div class="parts-view"><label class="parts-filter"><span aria-hidden="true">⌕</span><input id="partsFilter" type="search" autocomplete="off" value="${esc(filter)}" placeholder="Найти деталь, болт, подшипник или номер..." aria-label="Найти деталь, болт, подшипник или номер"></label>${q ? `<div class="parts-count">Найдено позиций: ${resultCount}</div>` : ''}<div class="parts-groups">${displayGroups.map((group,i)=>{
 const items=group.items.filter(item=>match(group,item,filter));
 if(q && !items.length)return '';
 const opened=q ? true : openId===group.id;
 const custom=mediaFor(`groups/${group.id}`);
 const figures=custom ? `<div class="parts-figure"><img src="${esc(custom)}" alt="${esc(group.title)}" data-action="zoom-image"></div>` : group.figures.map(page=>`<div class="parts-figure"><div class="figure-heading">Рис. ${figureNumber(page)} · нажмите на зелёный номер</div>${imageSvg(group,page,null,true)}<button type="button" class="figure-zoom" data-action="zoom-figure" data-group="${group.id}" data-figure="${page}">Увеличить схему ↗</button></div>`).join('');
 return `<section class="parts-group${opened?' is-open':''}" data-group="${group.id}"><button class="parts-group-toggle" type="button" data-action="toggle-parts-group" data-group="${group.id}" aria-expanded="${opened}"><span class="choice-number">${i+1}</span><span class="choice-copy"><strong>${esc(group.title)}</strong><small>${esc(group.subtitle)}</small></span><span class="parts-badge">${group.items.length} поз.</span><span class="parts-chevron" aria-hidden="true"></span></button>${opened?`<div class="parts-group-content">${q?'':figures}<div class="parts-position-list">${items.map(item=>`<button type="button" class="part-row" data-action="open-part" data-group="${group.id}" data-position="${item.position}"><span class="part-pos">${item.position}</span><span class="part-name">${esc(partTitle(group,item))}</span><span class="parts-badge">${item.quantity} шт.</span><span class="choice-arrow" aria-hidden="true">›</span></button>`).join('')}</div></div>`:''}</section>`;
 }).join('') || '<div class="parts-empty">Таких деталей не найдено. Попробуйте другое название или номер.</div>'}</div></div>`;
}
function partShape(type) {
 const base=`fill="#e7efdf" stroke="${green}" stroke-width="4" stroke-linejoin="round" stroke-linecap="round"`;
 const strokes=`fill="none" stroke="${green}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"`;
 const shapes={
 housing:`<rect x="31" y="16" width="98" height="64" rx="4" ${base}/><circle cx="80" cy="48" r="25" fill="#fff" stroke="${green}" stroke-width="5"/><circle cx="42" cy="27" r="4" fill="${yellow}"/><circle cx="118" cy="69" r="4" fill="${yellow}"/>`,
 drive:`<rect x="53" y="25" width="54" height="46" rx="5" ${base}/><circle cx="80" cy="48" r="16" fill="#fff" stroke="${green}" stroke-width="4"/><path d="M15 48 H64 M96 48 H145" ${strokes}/><circle cx="80" cy="48" r="5" fill="${yellow}"/>`,
 carriage:`<path d="M22 44 H138 M54 44 V61 M106 44 V61" ${strokes}/><circle cx="45" cy="65" r="14" ${base}/><circle cx="115" cy="65" r="14" ${base}/><path d="M64 40 V19 H96 V40" ${strokes}/><circle cx="80" cy="44" r="5" fill="${yellow}"/>`,
 hitch:`<path d="M35 76 V20 H125 V76 M35 35 H125" ${strokes}/><path d="M80 22 V55 M60 57 H100" ${strokes}/><circle cx="35" cy="76" r="5" fill="${yellow}"/><circle cx="125" cy="76" r="5" fill="${yellow}"/>`,
 bolt:`<path d="M28 47 l12 -14 h20 l12 14 -12 14 H40z" ${base}/><path d="M70 40 H144 V54 H70z" ${base}/><path d="M90 40 l-5 14 m16 -14 l-5 14 m16 -14 l-5 14 m16 -14 l-5 14 m16 -14 l-5 14" ${strokes}/>`,
 nut:`<path d="M45 24 h56 l22 24 -22 24 H45 L23 48z" ${base}/><circle cx="73" cy="48" r="15" fill="#fff" stroke="${green}" stroke-width="4"/>`,
 washer:`<ellipse cx="79" cy="48" rx="48" ry="27" ${base}/><ellipse cx="79" cy="48" rx="19" ry="10" fill="#fff" stroke="${green}" stroke-width="4"/>`,
 springwasher:`<path d="M113 27 A48 26 0 1 0 126 55 L117 57 M113 27 L105 35" ${strokes}/><path d="M125 55 l-8 12 M113 27 l7 -10" stroke="${yellow}" stroke-width="5"/>`,
 lockwasher:`<path d="M32 48 a46 30 0 1 0 92 0 a46 30 0 1 0 -92 0 M60 48 a18 12 0 1 0 36 0 a18 12 0 1 0 -36 0" fill="#e7efdf" fill-rule="evenodd" stroke="${green}" stroke-width="4"/><path d="M67 20 l5 -12 h15 l5 12" ${base}/>`,
 pin:`<circle cx="43" cy="49" r="11" ${base}/><path d="M53 42 H127 M53 56 l70 16 M127 42 v8 M123 72 l5 -7" ${strokes}/>`,
 springpin:`<path d="M38 48 q-5 -18 10 -21 q13 -2 12 12 l-2 42 M51 49 h76 q20 0 12 13 q-5 7 -16 4 H60" ${strokes}/><circle cx="53" cy="48" r="7" fill="${yellow}"/>`,
 bearing:`<circle cx="80" cy="48" r="39" ${base}/><circle cx="80" cy="48" r="16" fill="#fff" stroke="${green}" stroke-width="4"/>${[[80,19],[100,27],[109,48],[100,69],[80,77],[60,69],[51,48],[60,27]].map(([x,y])=>`<circle cx="${x}" cy="${y}" r="5" fill="${yellow}" stroke="${green}" stroke-width="2"/>`).join('')}`,
 ring:`<path d="M62 16 A36 36 0 1 0 98 16" ${strokes}/><circle cx="62" cy="16" r="4" fill="${yellow}"/><circle cx="98" cy="16" r="4" fill="${yellow}"/>`,
 spacer:`<ellipse cx="80" cy="48" rx="45" ry="28" ${base}/><ellipse cx="80" cy="48" rx="25" ry="14" fill="#fff" stroke="${green}" stroke-width="4"/>`,
 key:`<rect x="28" y="35" width="105" height="26" rx="9" ${base}/><path d="M39 38 h83" stroke="${yellow}" stroke-width="4"/>`,
 axle:`<path d="M23 41 h115 v16 H23z" ${base}/><circle cx="111" cy="49" r="4" fill="${yellow}" stroke="${green}" stroke-width="2"/>`,
 shaft:`<path d="M20 38 h118 v20 H20z" ${base}/><path d="M25 39 v18 m8 -18 v18 m8 -18 v18 m67 -18 v18 m8 -18 v18 m8 -18 v18" ${strokes}/>`,
 cardan:`<path d="M40 38 H120 V58 H40z" ${base}/><path d="M20 33 v30 M140 33 v30 M20 48 H40 M120 48 H140" ${strokes}/><circle cx="20" cy="48" r="5" fill="${yellow}"/><circle cx="140" cy="48" r="5" fill="${yellow}"/>`,
 plug:`<path d="M35 29 H114 L132 48 L114 67 H35z" ${base}/><path d="M48 29 v38 M58 29 v38" ${strokes}/>`,
 greaser:`<path d="M57 66 V45 l12 -10 V22 h22 v13 l12 10 v21z" ${base}/><circle cx="80" cy="22" r="5" fill="${yellow}"/>`,
 tyre:`<ellipse cx="80" cy="48" rx="42" ry="40" fill="#303a32" stroke="${green}" stroke-width="3"/><ellipse cx="80" cy="48" rx="21" ry="22" fill="#fff" stroke="${yellow}" stroke-width="4"/>`,
 rim:`<ellipse cx="80" cy="48" rx="44" ry="37" ${base}/><ellipse cx="80" cy="48" rx="26" ry="22" fill="#fff" stroke="${green}" stroke-width="4"/><circle cx="80" cy="48" r="8" fill="${yellow}"/>`,
 hub:`<path d="M32 32 H128 V64 H32z" ${base}/><ellipse cx="80" cy="48" rx="27" ry="30" ${base}/><circle cx="80" cy="48" r="12" fill="#fff" stroke="${green}" stroke-width="4"/>`,
 tie:`<path d="M30 22 H110 Q130 22 130 43 V64 Q130 74 120 74 H48 Q30 74 30 58z" ${strokes}/><path d="M32 45 H110" stroke="${yellow}" stroke-width="6"/><rect x="108" y="37" width="15" height="17" ${base}/>`,
 curtain:`<path d="M30 25 H130 L120 77 Q80 87 40 77z" fill="#303a32" stroke="${green}" stroke-width="4"/><path d="M31 29 H129" stroke="${yellow}" stroke-width="8"/>`,
 tube:`<path d="M21 39 H139 V57 H21z" ${base}/><circle cx="40" cy="48" r="4" fill="${yellow}"/><circle cx="65" cy="48" r="4" fill="${yellow}"/><circle cx="90" cy="48" r="4" fill="${yellow}"/>`,
 screw:`<path d="M25 42 H135 V55 H25z" ${base}/><path d="M42 42 l-5 13 m17 -13 l-5 13 m17 -13 l-5 13 m17 -13 l-5 13 m17 -13 l-5 13 m17 -13 l-5 13" ${strokes}/>`,
 gearbox:`<circle cx="80" cy="49" r="34" ${base}/><circle cx="80" cy="49" r="16" fill="#fff" stroke="${green}" stroke-width="5"/><path d="M80 15 V7 M80 83 v8 M46 49 h-9 M114 49 h9" stroke="${yellow}" stroke-width="7"/>`,
 cover:`<path d="M20 35 h120 v30 H20z" ${base}/><path d="M40 32 q10 -13 20 0 M95 32 q10 -13 20 0" ${strokes}/>`,
 tubeholder:`<rect x="27" y="32" width="106" height="34" rx="16" ${base}/><path d="M42 32 v34 M120 32 v34" ${strokes}/>`,
 tooth:`<circle cx="70" cy="31" r="14" ${strokes}/><circle cx="92" cy="31" r="14" ${strokes}/><path d="M57 33 q-15 25 -19 49 M105 33 q15 25 19 49" ${strokes}/><path d="M63 31 h36" stroke="${yellow}" stroke-width="4"/>`,
 tine:`<path d="M22 28 H138" stroke="${green}" stroke-width="9"/>${[42,68,94,120].map(x=>`<path d="M${x} 29 v45" stroke="${green}" stroke-width="4"/>`).join('')}`,
 catch:`<path d="M35 69 h74 V29 H35z" ${base}/><path d="M70 26 V14 h42" ${strokes}/><circle cx="69" cy="48" r="8" fill="${yellow}"/>`,
 rocker:`<path d="M20 52 L140 35 M22 63 L142 46" ${strokes}/><circle cx="80" cy="48" r="8" fill="${yellow}" stroke="${green}" stroke-width="3"/>`,
 stand:`<path d="M68 14 H91 V76 H68z M45 77 H115 L108 88 H52z" ${base}/><circle cx="80" cy="33" r="4" fill="${yellow}"/>`,
 frame:`<path d="M25 71 V29 H135 V71 M25 43 H135" ${strokes}/><circle cx="25" cy="71" r="5" fill="${yellow}"/><circle cx="135" cy="71" r="5" fill="${yellow}"/>`,
 bracket:`<path d="M40 25 H65 V62 H125 V78 H40z" ${base}/><circle cx="53" cy="39" r="5" fill="${yellow}"/><circle cx="100" cy="70" r="5" fill="${yellow}"/>`,
 spacerbar:`<path d="M23 38 H137 V58 H23z" ${base}/><circle cx="36" cy="48" r="5" fill="${yellow}"/><circle cx="124" cy="48" r="5" fill="${yellow}"/>`
 };
 return shapes[type] || `<text x="80" y="48" font-family="Arial,sans-serif" font-size="12" text-anchor="middle" fill="${green}">См. рисунок слева</text>`;
}
const specialExplanations = {
 'carriage:7':['Винт помогает выставить высоту граблей над землёй.','Проверьте резьбу и состояние винта перед регулировкой.'],
 'hitch:5':['Стояночная стойка поддерживает машину, когда она отсоединена от трактора.','Проверьте стойку и её фиксатор перед отсоединением машины.'],
 'tine:4':['Две гайки на болте работают в паре: вторая помогает не дать первой отвернуться.','Проверьте обе гайки и целостность резьбы при остановленной машине.']
};
function detailIllustration(group,item) {
 const [x,y,w,h]=bounds[item.figure];
 const [cx,cy]=item.point;
 return `<svg viewBox="0 0 340 190" role="img" aria-label="Позиция ${item.position} на заводском рисунке ${figureNumber(item.figure)} и условный вид детали"><rect width="340" height="190" rx="14" fill="#f5f8f1"/><text x="14" y="20" font-family="Arial,sans-serif" font-weight="bold" font-size="12" fill="${green}">Заводской рисунок ${figureNumber(item.figure)}</text><svg x="8" y="28" width="195" height="142" viewBox="${x} ${y} ${w} ${h}"><image href="${images[item.figure]}" x="0" y="0" width="1095" height="774"/><circle cx="${cx}" cy="${cy}" r="28" fill="${yellow}" fill-opacity=".48" stroke="${green}" stroke-width="7"/></svg><rect x="208" y="34" width="124" height="110" rx="12" fill="#fff8e1" stroke="${yellow}" stroke-width="2"/><svg x="212" y="38" width="116" height="83" viewBox="0 0 160 96">${partShape(kind(group,item))}</svg><text x="270" y="138" text-anchor="middle" font-family="Arial,sans-serif" font-weight="bold" font-size="13" fill="${green}">Поз. ${item.position}</text><text x="270" y="160" text-anchor="middle" font-family="Arial,sans-serif" font-size="10" fill="#596159">Условный вид</text><text x="14" y="181" font-family="Arial,sans-serif" font-size="10" fill="#596159">Выделен номер на заводской схеме</text></svg>`;
}
export function partExplanation(group,item) { const [purpose,inspection]=specialExplanations[`${group.id}:${item.position}`] || explanations[kind(group,item)];return {purpose,inspection}; }
export function renderPartCard(groupId,position,customImage = null, overrides = {}) {
 const original=partGroup(groupId), base=partAt(groupId,position);
 if(!original || !base)return '<div class="parts-empty">Деталь не найдена.</div>';
 const group={...original,title:overrides[groupId]?.title || original.title};
 const changed=overrides[groupId]?.items?.[position] || {};
 const item={...base,...changed,_editedName:changed.name !== undefined && changed.name !== base.name};
 const defaults=partExplanation(group,item);
 const purpose=changed.purpose || defaults.purpose;
 const inspection=changed.inspection || defaults.inspection;
 const all=machineQuantity(item);
 const perMachine=item.quantity*group.multiplier;
 const machineLabel=(changed.quantity!==undefined && changed.quantity!==base.quantity || changed.name!==undefined && changed.name!==base.name || changed.designation!==undefined && changed.designation!==base.designation) ? 'Уточните по изменённому каталогу' : all===perMachine ? `${all} шт.${group.multiplier>1?` (${item.quantity} × ${group.multiplier} узлов)`:''}` : `${all} шт. всего по каталогу; из них ${perMachine} шт. в этой группе${group.multiplier>1?` (${item.quantity} × ${group.multiplier})`:''}`;
 const note=item.note ? `<div class="part-note"><strong>Примечание завода:</strong> ${esc(item.note)}</div>` : '';
 return `<div class="part-card"><button type="button" class="part-return" data-action="back-parts">‹ К списку узла</button><div class="part-kicker">${esc(group.title)} · ПОЗИЦИЯ ${item.position} ИЗ ${group.items.length}</div><h2>${esc(partTitle(group,item))}</h2><div class="part-detail-image">${customImage ? `<img src="${esc(customImage)}" alt="${esc(partTitle(group,item))}" data-action="zoom-image">` : detailIllustration(group,item)}</div><button type="button" class="figure-zoom" data-action="zoom-figure" data-group="${group.id}" data-figure="${item.figure}" data-selected="${item.position}">Открыть заводской рисунок крупно ↗</button><div class="part-info"><h3>Паспорт детали</h3><dl><div><dt>Наименование</dt><dd>${esc(item.name)}</dd></div>${item.designation?`<div><dt>Обозначение</dt><dd>${esc(item.designation)}</dd></div>`:''}<div><dt>На один узел</dt><dd>${item.quantity} шт.</dd></div><div><dt>На машину</dt><dd>${machineLabel}</dd></div><div><dt>Источник</dt><dd>Рис. ${figureNumber(item.figure)}, стр. ${item.page}</dd></div></dl>${note}</div><div class="part-info"><h3>За что отвечает простыми словами</h3><p>${esc(purpose)}</p></div><div class="part-info"><h3>Важно знать при осмотре</h3><p>${esc(inspection)}</p></div><p class="part-safety">Осматривайте и разбирайте только остановленную машину с выключенным ВОМ и заглушенным двигателем. Схематичный вид не заменяет заводской чертёж.</p><div class="part-nav"><button type="button" data-action="part-prev" ${item.position===1?'disabled':''}>${item.position===1?'Начало узла':`← Поз. ${item.position-1}`}</button><button type="button" data-action="part-next" ${item.position===group.items.length?'disabled':''}>${item.position===group.items.length?'Конец узла':`Поз. ${item.position+1} →`}</button></div><button type="button" class="part-return-bottom" data-action="back-parts">Вернуться к списку узла</button></div>`;
}
export function renderFigureZoom(groupId,page,selected) {
 const group=partGroup(groupId); if(!group || !group.figures.includes(Number(page)))return '';
 return `<h3>Рисунок ${figureNumber(page)} · ${esc(group.title)}</h3><p class="figure-modal-note">Нажмите на зелёный номер позиции. Схему можно прокручивать в стороны; исходные чёрные номера служат подсказкой.</p><div class="figure-modal-canvas">${imageSvg(group,Number(page),selected || null,true)}</div><button type="button" class="btn ghost" data-action="close-modal">Закрыть</button>`;
}
