/* PowerImager - Statusbar */
window.PIStatusbar = (function () {
  let coordEl, sizeEl, zoomEl, toolEl, modeEl, dpiEl, printSizeEl;

  function init() {
    coordEl = document.getElementById('sb-coord');
    sizeEl = document.getElementById('sb-size');
    zoomEl = document.getElementById('sb-zoom');
    toolEl = document.getElementById('sb-tool');
    modeEl = document.getElementById('sb-mode');
    dpiEl = document.getElementById('sb-dpi');
    printSizeEl = document.getElementById('sb-print-size');

    const viewport = PICanvasEngine.getViewport();
    viewport.addEventListener('mousemove', (e) => {
      const pos = PICanvasEngine.viewportToCanvas(e.clientX, e.clientY);
      if (coordEl) coordEl.textContent = Math.round(pos.x) + ', ' + Math.round(pos.y);
    });

    PIEventBus.on('canvas:resized', () => updateDocumentInfo());
    PIEventBus.on('document:dpi-changed', () => updateDocumentInfo());
    PIEventBus.on('canvas:zoom-changed', (z) => { if (zoomEl) zoomEl.textContent = Math.round(z * 100) + '%'; });
    PIEventBus.on('tool:activated', (d) => { if (toolEl) toolEl.textContent = d.name; });

    updateDocumentInfo();
  }

  function updateDocumentInfo() {
    const size = PICanvasEngine.getCanvasSize();
    const dpi = PICanvasEngine.getDpi();
    const physical = PICanvasEngine.getPhysicalSize('mm');
    if (sizeEl) sizeEl.textContent = size.width + ' x ' + size.height;
    if (dpiEl) dpiEl.textContent = String(dpi);
    if (printSizeEl) printSizeEl.textContent = physical.width.toFixed(1) + ' x ' + physical.height.toFixed(1) + ' mm';
  }

  function setMode(mode) {
    if (modeEl) modeEl.textContent = mode;
  }

  return { init, setMode, updateDocumentInfo };
})();
