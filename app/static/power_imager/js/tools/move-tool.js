/* PowerImager — MoveTool: レイヤー移動（キャンバスへのスナップ＋整列付き）
 * 自動選択（既定ON）: クリック位置の最前面レイヤーを選んでから移動する。
 * 背景（最下層）は誤操作を防ぐため、既にアクティブな場合のみ対象にする。
 */
window.PIMoveTool = new (class extends PIToolBase {
  constructor() {
    super('move', 'move');
    this.dragging = false;
    this.startX = 0; this.startY = 0;
    this.layerStartX = 0; this.layerStartY = 0;
    this.altDup = false;
    this.dupDone = false;
    this.snapTargets = null;
    this._bound = false;
  }
  activate() {
    super.activate();
    if (!this._bound) {
      this._bound = true;
      PIEventBus.on('layers:changed', () => { if (PIToolbar.getCurrentTool() === this) this.redrawHandles(); });
      PIEventBus.on('canvas:zoom-changed', () => { if (PIToolbar.getCurrentTool() === this) this.redrawHandles(); });
    }
    this.redrawHandles();
  }
  deactivate() { super.deactivate(); PICanvasEngine.clearOverlay(); }
  // アクティブレイヤーの輪郭を表示（何が動くのかを見えるようにする）
  redrawHandles() {
    if (this.dragging) return; // ドラッグ中はスナップガイドを優先
    const ctx = PICanvasEngine.getOverlayCtx();
    if (!ctx) return;
    PICanvasEngine.clearOverlay();
    const layer = PILayerManager.getActive();
    if (!layer || !layer.visible) return;
    const z = PICanvasEngine.getZoom() || 1;
    let rect;
    if (layer.type === 'shape' && layer.shapeData && window.PIShapeTool) rect = PIShapeTool.contentRect(layer);
    else rect = { x: 0, y: 0, w: layer.canvas.width, h: layer.canvas.height };
    const pts = [[rect.x, rect.y], [rect.x + rect.w, rect.y], [rect.x + rect.w, rect.y + rect.h], [rect.x, rect.y + rect.h]]
      .map(([lx, ly]) => PILayerTransform.localToCanvas(layer, lx, ly));
    ctx.save();
    ctx.strokeStyle = 'rgba(124,111,247,0.9)';
    ctx.lineWidth = 1 / z;
    ctx.setLineDash([4 / z, 3 / z]);
    ctx.beginPath();
    ctx.moveTo(pts[0].x, pts[0].y);
    for (let i = 1; i < 4; i++) ctx.lineTo(pts[i].x, pts[i].y);
    ctx.closePath();
    ctx.stroke();
    ctx.restore();
  }
  autoSelect() { return localStorage.getItem('pi-move-autoselect') !== '0'; }
  pickLayerAt(px, py) {
    const layers = PILayerManager.getAll();
    for (let i = layers.length - 1; i >= 0; i--) {
      const l = layers[i];
      if (l.visible && !l.locked && l.type !== 'adjustment' && PILayerTransform.hitTest(l, px, py)) return l;
    }
    return null;
  }
  onMouseDown(e) {
    let layer = PILayerManager.getActive();
    if (this.autoSelect()) {
      const hit = this.pickLayerAt(e.canvasX, e.canvasY);
      // 最下層（通常は背景）は、明示的に選択している場合を除いて自動選択しない
      if (hit && hit !== layer && PILayerManager.getAll().indexOf(hit) > 0) {
        PILayerManager.setActiveIndex(PILayerManager.getAll().indexOf(hit));
        layer = hit;
      } else if (hit !== layer) {
        // クリック位置に対象が無い（または背景）のに別レイヤーが動くのは誤操作のもと
        return;
      }
    }
    if (!layer) return;
    if (layer.locked) { this.warnLocked(); return; }
    this.dragging = true;
    this.altDup = !!e.altKey;
    this.dupDone = false;
    this.startX = e.canvasX; this.startY = e.canvasY;
    this.layerStartX = layer.x; this.layerStartY = layer.y;
    this.contentRect = PILayerTransform.contentLocalRect(layer);
    this.snapTargets = PILayerTransform.snapTargetsFromLayers(layer);
  }
  onMouseMove(e) {
    if (!this.dragging) {
      // ホバーで移動可能なレイヤーを示す
      const vp = PICanvasEngine.getViewport();
      if (vp && this.autoSelect()) {
        const hit = this.pickLayerAt(e.canvasX, e.canvasY);
        vp.style.cursor = hit ? 'move' : 'default';
      }
      return;
    }
    let layer = PILayerManager.getActive();
    if (!layer) return;
    // Alt+ドラッグ: 動かし始めた時点で複製を作り、そちらを動かす
    if (this.altDup && !this.dupDone && Math.hypot(e.canvasX - this.startX, e.canvasY - this.startY) >= 3) {
      this.dupDone = true;
      PILayerManager.duplicateLayer(PILayerManager.getAll().indexOf(layer));
      layer = PILayerManager.getActive();
      this.snapTargets = PILayerTransform.snapTargetsFromLayers(layer);
    }
    let dx = e.canvasX - this.startX, dy = e.canvasY - this.startY;
    if (e.shiftKey) { if (Math.abs(dx) > Math.abs(dy)) dy = 0; else dx = 0; } // 軸固定
    layer.x = this.layerStartX + dx;
    layer.y = this.layerStartY + dy;
    // Ctrl押下中はスナップ無効
    let guide = { guideX: null, guideY: null };
    if (!e.ctrlKey) {
      const gx = (window.PIGuides ? PIGuides.getSnapX() : []).concat(this.snapTargets ? this.snapTargets.xs : []);
      const gy = (window.PIGuides ? PIGuides.getSnapY() : []).concat(this.snapTargets ? this.snapTargets.ys : []);
      guide = PILayerTransform.snapToCanvas(layer, 6 / PICanvasEngine.getZoom(), this.contentRect, gx, gy);
    }
    if (layer.textData) layer.textData = { ...layer.textData, x: layer.x, y: layer.y };
    PILayerManager.requestRender();
    this.drawGuides(guide);
  }
  onMouseUp(e) {
    if (this.dragging) {
      this.dragging = false;
      const layer = PILayerManager.getActive();
      if (layer) {
        layer.x = Math.round(layer.x); layer.y = Math.round(layer.y);
        if (layer.textData) layer.textData = { ...layer.textData, x: layer.x, y: layer.y };
      }
      PICanvasEngine.clearOverlay();
      PIHistoryManager.push(this.dupDone ? 'レイヤーを複製' : '移動');
      this.altDup = false; this.dupDone = false;
      PIEventBus.emit('tool:properties-changed');
      this.redrawHandles();
    }
  }
  drawGuides(g) {
    const ctx = PICanvasEngine.getOverlayCtx();
    PICanvasEngine.clearOverlay();
    if (g.guideX == null && g.guideY == null) return;
    const s = PICanvasEngine.getCanvasSize();
    ctx.save();
    ctx.strokeStyle = '#ff3db4'; ctx.lineWidth = 1 / PICanvasEngine.getZoom();
    if (g.guideX != null) { ctx.beginPath(); ctx.moveTo(g.guideX, 0); ctx.lineTo(g.guideX, s.height); ctx.stroke(); }
    if (g.guideY != null) { ctx.beginPath(); ctx.moveTo(0, g.guideY); ctx.lineTo(s.width, g.guideY); ctx.stroke(); }
    ctx.restore();
  }
  getProperties() {
    const self = this;
    const layer = PILayerManager.getActive();
    if (!layer) return null;
    const align = (m) => { PILayerTransform.alignInCanvas(layer, m); PILayerManager.requestRender(); PIHistoryManager.push('整列'); PIEventBus.emit('tool:properties-changed'); };
    return {
      title: '移動',
      fields: [
        {
          type: 'checkbox', label: '自動選択（クリックしたレイヤーを選ぶ）', key: 'autoselect',
          value: self.autoSelect(),
          onChange: (v) => localStorage.setItem('pi-move-autoselect', v ? '1' : '0')
        },
        { type: 'number', label: 'X', key: 'x', value: Math.round(layer.x), onChange: (v) => { const n = parseInt(v, 10); if (Number.isFinite(n)) { layer.x = n; PILayerManager.requestRender(); } } },
        { type: 'number', label: 'Y', key: 'y', value: Math.round(layer.y), onChange: (v) => { const n = parseInt(v, 10); if (Number.isFinite(n)) { layer.y = n; PILayerManager.requestRender(); } } },
        { type: 'button-group', label: '横整列', key: 'ha', buttons: [
          { label: '左', onClick: () => align('left') },
          { label: '中央', onClick: () => align('hcenter') },
          { label: '右', onClick: () => align('right') }
        ] },
        { type: 'button-group', label: '縦整列', key: 'va', buttons: [
          { label: '上', onClick: () => align('top') },
          { label: '中央', onClick: () => align('vcenter') },
          { label: '下', onClick: () => align('bottom') }
        ] }
      ]
    };
  }
})();
