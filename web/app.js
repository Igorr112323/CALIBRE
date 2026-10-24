(() => {
  const indicator = document.getElementById('connection');
  if (!indicator) return;
  const update = () => {
    const online = navigator.onLine;
    indicator.innerHTML = `<i style="background:${online ? '#6aa77d' : '#d7ad43'}"></i> ${online ? 'онлайн' : 'офлайн'}`;
  };
  window.addEventListener('online', update);
  window.addEventListener('offline', update);
  update();
})();
