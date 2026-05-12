/* PowerImager — Statusbar: ステータスバー */
window.PIStatusbar = (function () {
  let coordEl, sizeEl, zoomEl, toolEl, modeEl;

  function init() {
    coordEl = document.getElementById('sb-coord');
    sizeEl = document.getElementById('sb-size');
    zoomEl = document.getElementById('sb-zoom');
    toolEl = document.getElementById('sb-tool');
    modeEl = document.getElementById('sb-mode');

    const viewport = PICanvasEngine.getViewport();
    viewport.addEventListener('mousemove', (e) => {
      const pos = PICanvasEngine.viewportToCanvas(e.clientX, e.clientY);
      if (coordEl) coordEl.textContent = Math.round(pos.x) + ', ' + Math.round(pos.y);
    });

    PIEventBus.on('canvas:resized', (s) => { if (sizeEl) sizeEl.textContent = s.width + ' x ' + s.height; });
    PIEventBus.on('canvas:zoom-changed', (z) => { if (zoomEl) zoomEl.textContent = Math.round(z * 100) + '%'; });
    PIEventBus.on('tool:activated', (d) => { if (toolEl) toolEl.textContent = d.name; });

    const size = PICanvasEngine.getCanvasSize();
    if (sizeEl) sizeEl.textContent = size.width + ' x ' + size.height;
  }

  function setMode(mode) {
    if (modeEl) modeEl.textContent = mode;
  }

  return { init, setMode };
})();
