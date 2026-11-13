window.androidBridge = {};
window.addEventListener('DOMContentLoaded', () => {
  const params = new URLSearchParams(location.search);
  const skip = () => {
    document.querySelector('#launchSplash')?.classList.add('dismiss');
    document.querySelector('#launchAnimation')?.pause();
  };
  if (params.has('skip')) skip();
  const machine = params.get('machine');
  if (machine) {
    [...document.querySelectorAll('.machine-card')].find(el => el.dataset.id === machine)?.click();
    const tab = params.get('tab');
    if (tab) [...document.querySelectorAll('[data-tab]')].find(el => el.dataset.tab === tab)?.click();
    if (params.get('screen') === 'wizard') {
      [...document.querySelectorAll('[data-action=open-adjustment]')].find(el => el.dataset.id === params.get('adjustment'))?.click();
      const n = Math.min(100, Number(params.get('step')) || 0);
      for (let i = 0; i < n; i += 1) document.querySelector('[data-action=wizard-next-step]')?.click();
    } else if (params.get('screen') === 'part') {
      [...document.querySelectorAll('[data-action=toggle-parts-group]')].find(el => el.dataset.group === params.get('group'))?.click();
      [...document.querySelectorAll('.part-row')].find(el => el.dataset.group === params.get('group') && el.dataset.position === params.get('position'))?.click();
    } else if (tab === 'parts' && params.get('group')) {
      [...document.querySelectorAll('[data-action=toggle-parts-group]')].find(el => el.dataset.group === params.get('group'))?.click();
    } else if (params.get('item')) {
      [...document.querySelectorAll('.machine-item')].find(el => el.dataset.item === params.get('item'))?.querySelector('.machine-item-toggle')?.click();
    }
  }
  if (params.get('scroll')) document.querySelector('#main').scrollTop = Number(params.get('scroll')) || 0;
  window.addEventListener('message', event => {
    if (event.origin === location.origin && event.source === window.parent && event.data?.type === 'skip') skip();
  });
  document.addEventListener('click', event => {
    const target = event.target.closest('.machine-item-inner p,.machine-item-inner li,.machine-item-toggle,.adjustment-choice,.part-row,.parts-group-toggle,.wizard h2,.wizard h3,.wizard-text,.part-info,.part-card h2');
    if (!target) return;
    document.querySelectorAll('.studio-selected').forEach(el => el.classList.remove('studio-selected'));
    target.classList.add('studio-selected');
    window.parent.postMessage({type: 'preview-selection', label: (target.innerText || target.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 150), item: target.closest('.machine-item')?.dataset.item || ''}, location.origin);
  });
});
