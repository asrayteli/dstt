/* PowerImager — BrushCursor: ブラシ/消しゴム/レタッチのサイズを示すリングカーソル
 * 描画系ツールでポインタ位置にズーム連動の円を表示し、塗る範囲を可視化する。
 */
window.PIBrushCursor = (function () {
  let ring, viewport;
  let currentTool = null;
  let inside = false;
  let lastClientX = 0, lastClientY = 0;

  function init() {
    viewport = PICanvasEngine.getViewport();
    ring = document.createElement('div');
    ring.id = 'pi-brush-cursor';
    ring.className = 'hidden';
    viewport.appendChild(ring);

    viewport.addEventListener('mouseenter', () => { inside = true; update(); });
    viewport.addEventListener('mouseleave', () => { inside = false; update(); });
    viewport.addEventListener('mousemove', (e) => {
      lastClientX = e.clientX; lastClientY = e.clientY;
      position();
    });

    PIEventBus.on('tool:activated', (d) => { currentTool = d.tool; update(); });
    PIEventBus.on('brush:size-changed', () => position());
    PIEventBus.on('canvas:zoom-changed', () => position());
    PIEventBus.on('tool:properties-changed', () => position());
  }

  function hasRing() {
    return !!(currentTool && typeof currentTool.getCursorRadius === 'function');
  }

  function update() {
    if (!ring) return;
    if (hasRing() && inside) { ring.classList.remove('hidden'); position(); }
    else ring.classList.add('hidden');
  }

  function position() {
    if (!ring || ring.classList.contains('hidden')) {
      if (hasRing() && inside) ring.classList.remove('hidden'); else return;
    }
    if (!hasRing()) return;
    const radius = currentTool.getCursorRadius();
    const zoom = PICanvasEngine.getZoom();
    const d = Math.max(4, radius * 2 * zoom);
    const rect = viewport.getBoundingClientRect();
    const left = lastClientX - rect.left;
    const top = lastClientY - rect.top;
    ring.style.width = d + 'px';
    ring.style.height = d + 'px';
    ring.style.left = (left - d / 2) + 'px';
    ring.style.top = (top - d / 2) + 'px';
  }

  return { init };
})();
