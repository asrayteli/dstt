/* PowerImager — CanvasEngine: キャンバス描画エンジン */
window.PICanvasEngine = (function () {
  let viewport, wrapper, displayCanvas, displayCtx, overlayCanvas, overlayCtx;
  let canvasW = 800, canvasH = 600;
  let zoom = 1, panX = 0, panY = 0;
  let isPanning = false, panStartX = 0, panStartY = 0;
  let spaceDown = false;

  function init() {
    viewport = document.getElementById('pi-viewport');
    wrapper = viewport.querySelector('.canvas-wrapper');
    displayCanvas = document.getElementById('display-canvas');
    displayCtx = displayCanvas.getContext('2d');
    overlayCanvas = document.getElementById('overlay-canvas');
    overlayCtx = overlayCanvas.getContext('2d');

    viewport.addEventListener('wheel', onWheel, { passive: false });
    viewport.addEventListener('mousedown', onPanStart);
    window.addEventListener('mousemove', onPanMove);
    window.addEventListener('mouseup', onPanEnd);
    window.addEventListener('keydown', (e) => { if (e.code === 'Space' && !e.repeat) { spaceDown = true; viewport.style.cursor = 'grab'; } });
    window.addEventListener('keyup', (e) => { if (e.code === 'Space') { spaceDown = false; if (!isPanning) viewport.style.cursor = 'crosshair'; } });

    setCanvasSize(canvasW, canvasH);
    fitToViewport();
  }

  function setCanvasSize(w, h) {
    canvasW = w; canvasH = h;
    displayCanvas.width = w; displayCanvas.height = h;
    overlayCanvas.width = w; overlayCanvas.height = h;
    wrapper.style.width = w + 'px';
    wrapper.style.height = h + 'px';
    updateTransform();
    PIEventBus.emit('canvas:resized', { width: w, height: h });
  }

  function getCanvasSize() { return { width: canvasW, height: canvasH }; }

  function updateTransform() {
    wrapper.style.transform = 'translate(' + panX + 'px,' + panY + 'px) scale(' + zoom + ')';
  }

  function fitToViewport() {
    const vw = viewport.clientWidth;
    const vh = viewport.clientHeight;
    const scaleX = (vw - 40) / canvasW;
    const scaleY = (vh - 40) / canvasH;
    zoom = Math.min(scaleX, scaleY, 4);
    zoom = Math.max(zoom, 0.05);
    panX = (vw - canvasW * zoom) / 2;
    panY = (vh - canvasH * zoom) / 2;
    updateTransform();
    PIEventBus.emit('canvas:zoom-changed', zoom);
  }

  function onWheel(e) {
    e.preventDefault();
    const rect = viewport.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const oldZoom = zoom;
    const factor = e.deltaY < 0 ? 1.1 : 0.9;
    zoom = Math.max(0.05, Math.min(zoom * factor, 32));
    panX = mx - (mx - panX) * (zoom / oldZoom);
    panY = my - (my - panY) * (zoom / oldZoom);
    updateTransform();
    PIEventBus.emit('canvas:zoom-changed', zoom);
  }

  function onPanStart(e) {
    if (!spaceDown && e.button !== 1) return;
    isPanning = true;
    panStartX = e.clientX - panX;
    panStartY = e.clientY - panY;
    viewport.style.cursor = 'grabbing';
    e.preventDefault();
  }

  function onPanMove(e) {
    if (!isPanning) return;
    panX = e.clientX - panStartX;
    panY = e.clientY - panStartY;
    updateTransform();
  }

  function onPanEnd() {
    if (!isPanning) return;
    isPanning = false;
    viewport.style.cursor = spaceDown ? 'grab' : 'crosshair';
  }

  function viewportToCanvas(clientX, clientY) {
    const rect = viewport.getBoundingClientRect();
    const vx = clientX - rect.left;
    const vy = clientY - rect.top;
    return {
      x: (vx - panX) / zoom,
      y: (vy - panY) / zoom
    };
  }

  function canvasToViewport(cx, cy) {
    const rect = viewport.getBoundingClientRect();
    return {
      x: cx * zoom + panX + rect.left,
      y: cy * zoom + panY + rect.top
    };
  }

  function renderLayers(layers) {
    displayCtx.clearRect(0, 0, canvasW, canvasH);
    layers.forEach(layer => {
      if (!layer.visible) return;
      displayCtx.save();
      displayCtx.globalAlpha = layer.opacity;
      displayCtx.globalCompositeOperation = layer.blendMode || 'source-over';
      displayCtx.drawImage(layer.canvas, layer.x, layer.y);
      displayCtx.restore();
    });
  }

  function clearOverlay() { overlayCtx.clearRect(0, 0, canvasW, canvasH); }

  function getDisplayCanvas() { return displayCanvas; }
  function getDisplayCtx() { return displayCtx; }
  function getOverlayCanvas() { return overlayCanvas; }
  function getOverlayCtx() { return overlayCtx; }
  function getViewport() { return viewport; }
  function getZoom() { return zoom; }
  function setZoom(z) { zoom = Math.max(0.05, Math.min(z, 32)); updateTransform(); PIEventBus.emit('canvas:zoom-changed', zoom); }
  function isPanningNow() { return isPanning; }

  return {
    init, setCanvasSize, getCanvasSize, fitToViewport,
    viewportToCanvas, canvasToViewport,
    renderLayers, clearOverlay,
    getDisplayCanvas, getDisplayCtx, getOverlayCanvas, getOverlayCtx,
    getViewport, getZoom, setZoom, isPanningNow
  };
})();
