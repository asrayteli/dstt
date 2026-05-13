/* PowerImager — CanvasEngine: キャンバス描画エンジン */
window.PICanvasEngine = (function () {
  let viewport, wrapper, displayCanvas, displayCtx, overlayCanvas, overlayCtx;
  const DEFAULT_DPI = 96;
  const MIN_DPI = 1;
  const MAX_DPI = 2400;
  const MAX_DIMENSION = 12000;
  const MAX_PIXELS = 80000000;

  let canvasW = 800, canvasH = 600;
  let dpi = DEFAULT_DPI;
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
    configureContext(displayCtx);
    configureContext(overlayCtx);

    viewport.addEventListener('wheel', onWheel, { passive: false });
    viewport.addEventListener('mousedown', onPanStart);
    window.addEventListener('mousemove', onPanMove);
    window.addEventListener('mouseup', onPanEnd);
    window.addEventListener('keydown', (e) => { if (e.code === 'Space' && !e.repeat) { spaceDown = true; viewport.style.cursor = 'grab'; } });
    window.addEventListener('keyup', (e) => { if (e.code === 'Space') { spaceDown = false; if (!isPanning) viewport.style.cursor = 'crosshair'; } });

    setCanvasSize(canvasW, canvasH);
    fitToViewport();
  }

  function clampNumber(value, min, max, fallback) {
    const n = Number(value);
    if (!Number.isFinite(n)) return fallback;
    return Math.min(max, Math.max(min, Math.round(n)));
  }

  function normalizeCanvasSize(w, h) {
    let nextW = clampNumber(w, 1, MAX_DIMENSION, canvasW);
    let nextH = clampNumber(h, 1, MAX_DIMENSION, canvasH);
    const pixels = nextW * nextH;
    if (pixels > MAX_PIXELS) {
      const scale = Math.sqrt(MAX_PIXELS / pixels);
      nextW = Math.max(1, Math.floor(nextW * scale));
      nextH = Math.max(1, Math.floor(nextH * scale));
      PIEventBus.emit('toast', 'キャンバスが大きすぎるため、安全なサイズに調整しました');
    }
    return { width: nextW, height: nextH };
  }

  function setCanvasSize(w, h) {
    const size = normalizeCanvasSize(w, h);
    canvasW = size.width; canvasH = size.height;
    displayCanvas.width = canvasW; displayCanvas.height = canvasH;
    overlayCanvas.width = canvasW; overlayCanvas.height = canvasH;
    configureContext(displayCtx);
    configureContext(overlayCtx);
    wrapper.style.width = canvasW + 'px';
    wrapper.style.height = canvasH + 'px';
    updateTransform();
    PIEventBus.emit('canvas:resized', { width: canvasW, height: canvasH });
  }

  function getCanvasSize() { return { width: canvasW, height: canvasH }; }

  function getDpi() { return dpi; }

  function setDpi(value) {
    const next = clampNumber(value, MIN_DPI, MAX_DPI, dpi);
    if (next === dpi) return dpi;
    dpi = next;
    PIEventBus.emit('document:dpi-changed', dpi);
    return dpi;
  }

  function getPhysicalSize(unit) {
    const inchesW = canvasW / dpi;
    const inchesH = canvasH / dpi;
    if (unit === 'in') return { width: inchesW, height: inchesH, unit: 'in' };
    return { width: inchesW * 25.4, height: inchesH * 25.4, unit: 'mm' };
  }

  function configureContext(ctx, options) {
    if (!ctx) return ctx;
    const quality = (options && options.quality) || 'high';
    if ('imageSmoothingEnabled' in ctx) ctx.imageSmoothingEnabled = true;
    if ('imageSmoothingQuality' in ctx) ctx.imageSmoothingQuality = quality;
    return ctx;
  }

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
    configureContext(displayCtx);
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
    init, setCanvasSize, getCanvasSize, getDpi, setDpi, getPhysicalSize,
    normalizeCanvasSize, configureContext, fitToViewport,
    viewportToCanvas, canvasToViewport,
    renderLayers, clearOverlay,
    getDisplayCanvas, getDisplayCtx, getOverlayCanvas, getOverlayCtx,
    getViewport, getZoom, setZoom, isPanningNow
  };
})();
