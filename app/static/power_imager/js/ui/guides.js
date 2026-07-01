/* PowerImager — Guides: グリッド表示・ガイド線（ドラッグ移動）・スナップ対象の提供 */
window.PIGuides = (function () {
  let wrapper, layerEl, gridEl;
  let gridEnabled = false;
  let gridSize = 50;
  let guides = []; // { axis:'h'|'v', pos, el }
  let dragging = null;

  function init() {
    wrapper = document.querySelector('.canvas-wrapper');
    if (!wrapper) return;
    layerEl = document.createElement('div');
    layerEl.id = 'pi-guides';
    gridEl = document.createElement('div');
    gridEl.className = 'pi-grid hidden';
    layerEl.appendChild(gridEl);
    wrapper.appendChild(layerEl);
    resize();
    PIEventBus.on('canvas:resized', resize);
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }

  function resize() {
    const s = PICanvasEngine.getCanvasSize();
    if (layerEl) { layerEl.style.width = s.width + 'px'; layerEl.style.height = s.height + 'px'; }
    applyGrid();
    renderGuides();
  }

  function applyGrid() {
    if (!gridEl) return;
    gridEl.classList.toggle('hidden', !gridEnabled);
    gridEl.style.backgroundSize = gridSize + 'px ' + gridSize + 'px';
  }

  function setGrid(on) { gridEnabled = on; applyGrid(); }
  function toggleGrid() { setGrid(!gridEnabled); return gridEnabled; }
  function setGridSize(n) { gridSize = Math.max(2, n | 0); applyGrid(); }
  function isGridOn() { return gridEnabled; }

  function addGuide(axis, pos) {
    const s = PICanvasEngine.getCanvasSize();
    const p = pos != null ? pos : (axis === 'h' ? s.height / 2 : s.width / 2);
    const el = document.createElement('div');
    el.className = 'pi-guide pi-guide-' + axis;
    const guide = { axis, pos: Math.round(p), el };
    el.addEventListener('mousedown', (e) => { e.stopPropagation(); e.preventDefault(); dragging = guide; });
    layerEl.appendChild(el);
    guides.push(guide);
    positionGuide(guide);
  }

  function positionGuide(g) {
    if (g.axis === 'h') { g.el.style.top = g.pos + 'px'; }
    else { g.el.style.left = g.pos + 'px'; }
  }

  function renderGuides() { guides.forEach(positionGuide); }

  function clearGuides() {
    guides.forEach(g => g.el.remove());
    guides = [];
  }

  function onMove(e) {
    if (!dragging) return;
    const pos = PICanvasEngine.viewportToCanvas(e.clientX, e.clientY);
    const s = PICanvasEngine.getCanvasSize();
    if (dragging.axis === 'h') dragging.pos = Math.round(PIMathUtils.clamp(pos.y, 0, s.height));
    else dragging.pos = Math.round(PIMathUtils.clamp(pos.x, 0, s.width));
    positionGuide(dragging);
  }
  function onUp() { dragging = null; }

  // スナップ対象（キャンバス座標）
  function getSnapX() {
    const xs = guides.filter(g => g.axis === 'v').map(g => g.pos);
    if (gridEnabled) { const s = PICanvasEngine.getCanvasSize(); for (let x = 0; x <= s.width; x += gridSize) xs.push(x); }
    return xs;
  }
  function getSnapY() {
    const ys = guides.filter(g => g.axis === 'h').map(g => g.pos);
    if (gridEnabled) { const s = PICanvasEngine.getCanvasSize(); for (let y = 0; y <= s.height; y += gridSize) ys.push(y); }
    return ys;
  }

  return { init, setGrid, toggleGrid, setGridSize, isGridOn, addGuide, clearGuides, getSnapX, getSnapY };
})();
