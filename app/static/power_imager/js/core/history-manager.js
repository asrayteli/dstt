/* PowerImager — HistoryManager: 履歴（Undo/Redo） */
window.PIHistoryManager = (function () {
  const MAX = 50;
  const MAX_BYTES = 256 * 1024 * 1024; // 履歴スナップショットのおおよそのメモリ上限
  let states = [];
  let index = -1;
  let debounceTimer = null;

  function init() {
    push('初期状態');
  }

  // スライダー等の連続編集を1つの履歴へまとめる（最後の操作から delay ms 後に1回 push）
  function pushDebounced(label, delay) {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => { debounceTimer = null; push(label); }, delay || 450);
  }

  function flushDebounced() {
    if (debounceTimer) { clearTimeout(debounceTimer); debounceTimer = null; }
  }

  function stateBytes(state) {
    let b = 0;
    state.snap.forEach(l => { b += (l.canvas.width * l.canvas.height * 4); });
    return b;
  }

  function trimToBudget() {
    // 件数上限とメモリ予算（約256MB）の両方で古い履歴を捨てる。
    while (states.length > MAX) states.shift();
    let total = 0;
    for (let i = 0; i < states.length; i++) total += stateBytes(states[i]);
    while (states.length > 1 && total > MAX_BYTES) {
      total -= stateBytes(states[0]);
      states.shift();
    }
  }

  function push(label) {
    flushDebounced();
    const snap = PILayerManager.snapshot();
    const canvasSize = PICanvasEngine.getCanvasSize();
    if (index < states.length - 1) {
      states = states.slice(0, index + 1);
    }
    states.push({ label, snap, canvasSize: { ...canvasSize }, dpi: PICanvasEngine.getDpi() });
    trimToBudget();
    index = states.length - 1;
    PIEventBus.emit('history:changed', { states, index });
  }

  function undo() {
    if (index <= 0) return;
    index--;
    applyState(states[index]);
    PIEventBus.emit('history:changed', { states, index });
  }

  function redo() {
    if (index >= states.length - 1) return;
    index++;
    applyState(states[index]);
    PIEventBus.emit('history:changed', { states, index });
  }

  function goTo(i) {
    if (i < 0 || i >= states.length) return;
    index = i;
    applyState(states[index]);
    PIEventBus.emit('history:changed', { states, index });
  }

  function applyState(state) {
    if (state.canvasSize) {
      const cur = PICanvasEngine.getCanvasSize();
      if (cur.width !== state.canvasSize.width || cur.height !== state.canvasSize.height) {
        PICanvasEngine.setCanvasSize(state.canvasSize.width, state.canvasSize.height);
      }
    }
    if (state.dpi) PICanvasEngine.setDpi(state.dpi);
    PILayerManager.restore(state.snap);
  }

  function getStates() { return states; }
  function getIndex() { return index; }

  function clear() {
    states = [];
    index = -1;
  }

  return { init, push, pushDebounced, flushDebounced, undo, redo, goTo, getStates, getIndex, clear };
})();
