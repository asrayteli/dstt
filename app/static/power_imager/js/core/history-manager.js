/* PowerImager — HistoryManager: 履歴（Undo/Redo） */
window.PIHistoryManager = (function () {
  const MAX = 50;
  let states = [];
  let index = -1;

  function init() {
    push('初期状態');
  }

  function push(label) {
    const snap = PILayerManager.snapshot();
    const canvasSize = PICanvasEngine.getCanvasSize();
    if (index < states.length - 1) {
      states = states.slice(0, index + 1);
    }
    states.push({ label, snap, canvasSize: { ...canvasSize } });
    if (states.length > MAX) states.shift();
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
    PILayerManager.restore(state.snap);
  }

  function getStates() { return states; }
  function getIndex() { return index; }

  function clear() {
    states = [];
    index = -1;
  }

  return { init, push, undo, redo, goTo, getStates, getIndex, clear };
})();
