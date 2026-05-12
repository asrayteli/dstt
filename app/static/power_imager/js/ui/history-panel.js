/* PowerImager — HistoryPanel: 履歴パネル */
window.PIHistoryPanel = (function () {
  let container;

  function init() {
    container = document.getElementById('history-panel-body');
    PIEventBus.on('history:changed', () => render());
  }

  function render() {
    if (!container) return;
    container.innerHTML = '';
    const states = PIHistoryManager.getStates();
    const idx = PIHistoryManager.getIndex();

    const list = document.createElement('div');
    list.className = 'history-list';
    states.forEach((state, i) => {
      const item = document.createElement('div');
      item.className = 'history-item';
      if (i === idx) item.classList.add('active');
      if (i > idx) item.classList.add('future');
      item.textContent = state.label;
      item.addEventListener('click', () => PIHistoryManager.goTo(i));
      list.appendChild(item);
    });
    container.appendChild(list);
  }

  return { init, render };
})();
