/* PowerImager — Toolbar: 左ツールバー */
window.PIToolbar = (function () {
  const toolMap = {};
  let currentTool = null;
  let _textOverlayActive = false;

  function init() {
    toolMap['move'] = PIMoveTool;
    toolMap['select'] = PISelectTool;
    toolMap['crop'] = PICropTool;
    toolMap['brush'] = PIBrushTool;
    toolMap['eraser'] = PIEraserTool;
    toolMap['retouch'] = PIRetouchTool;
    toolMap['text'] = PITextTool;
    toolMap['shape'] = PIShapeTool;
    toolMap['fill'] = PIFillTool;
    toolMap['eyedropper'] = PIEyedropperTool;
    toolMap['screenshot'] = PIScreenshotTool;

    document.querySelectorAll('.tool-btn[data-tool]').forEach(btn => {
      btn.addEventListener('click', () => switchTool(btn.dataset.tool));
    });

    const swapBtn = document.getElementById('swap-colors');
    if (swapBtn) {
      swapBtn.addEventListener('click', () => {
        const fg = document.getElementById('fg-color-input');
        const bg = document.getElementById('bg-color-input');
        const tmp = fg.value;
        fg.value = bg.value;
        bg.value = tmp;
      });
    }

    PIEventBus.on('tool:switch', (toolName) => switchTool(toolName));
    PIEventBus.on('text:overlay-active', (active) => { _textOverlayActive = active; });

    const viewport = PICanvasEngine.getViewport();
    viewport.addEventListener('mousedown', onViewportMouse);
    window.addEventListener('mousemove', onViewportMouseMove);
    window.addEventListener('mouseup', onViewportMouseUp);

    switchTool('move');
  }

  function switchTool(name) {
    if (currentTool) currentTool.deactivate();
    document.querySelectorAll('.tool-btn').forEach(b => b.classList.remove('active'));
    const btn = document.querySelector('.tool-btn[data-tool="' + name + '"]');
    if (btn) btn.classList.add('active');

    currentTool = toolMap[name] || null;
    if (currentTool) {
      currentTool.activate();
      PIEventBus.emit('tool:activated', { name, tool: currentTool });
    }
  }

  function onViewportMouse(e) {
    if (!currentTool) return;
    if (PIModal.isOpen()) return;
    if (_textOverlayActive) return;
    const target = e.target;
    if (target.closest('#text-edit-overlay')) return;
    if (PICanvasEngine.isPanningNow()) return;
    const pos = PICanvasEngine.viewportToCanvas(e.clientX, e.clientY);
    const data = {
      canvasX: pos.x, canvasY: pos.y,
      clientX: e.clientX, clientY: e.clientY,
      buttons: e.buttons, shiftKey: e.shiftKey, ctrlKey: e.ctrlKey, altKey: e.altKey
    };
    currentTool.onMouseDown(data);
  }

  function onViewportMouseMove(e) {
    if (!currentTool) return;
    if (PIModal.isOpen()) return;
    const pos = PICanvasEngine.viewportToCanvas(e.clientX, e.clientY);
    const data = {
      canvasX: pos.x, canvasY: pos.y,
      clientX: e.clientX, clientY: e.clientY,
      buttons: e.buttons, shiftKey: e.shiftKey, ctrlKey: e.ctrlKey, altKey: e.altKey
    };
    currentTool.onMouseMove(data);
  }

  function onViewportMouseUp(e) {
    if (!currentTool) return;
    if (PIModal.isOpen()) return;
    const pos = PICanvasEngine.viewportToCanvas(e.clientX, e.clientY);
    const data = {
      canvasX: pos.x, canvasY: pos.y,
      clientX: e.clientX, clientY: e.clientY,
      buttons: e.buttons, shiftKey: e.shiftKey, ctrlKey: e.ctrlKey, altKey: e.altKey
    };
    currentTool.onMouseUp(data);
  }

  function getCurrentTool() { return currentTool; }
  function getCurrentToolName() {
    for (const [name, tool] of Object.entries(toolMap)) {
      if (tool === currentTool) return name;
    }
    return null;
  }

  return { init, switchTool, getCurrentTool, getCurrentToolName, toolMap };
})();
