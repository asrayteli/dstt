/* PowerImager — MoveTool: レイヤー移動（キャンバスへのスナップ＋整列付き） */
window.PIMoveTool = new (class extends PIToolBase {
  constructor() {
    super('move', 'move');
    this.dragging = false;
    this.startX = 0; this.startY = 0;
    this.layerStartX = 0; this.layerStartY = 0;
  }
  deactivate() { super.deactivate(); PICanvasEngine.clearOverlay(); }
  onMouseDown(e) {
    const layer = PILayerManager.getActive();
    if (!layer || layer.locked) return;
    this.dragging = true;
    this.startX = e.canvasX; this.startY = e.canvasY;
    this.layerStartX = layer.x; this.layerStartY = layer.y;
    this.contentRect = PILayerTransform.contentLocalRect(layer);
  }
  onMouseMove(e) {
    if (!this.dragging) return;
    const layer = PILayerManager.getActive();
    if (!layer) return;
    layer.x = this.layerStartX + (e.canvasX - this.startX);
    layer.y = this.layerStartY + (e.canvasY - this.startY);
    // Ctrl押下中はスナップ無効
    let guide = { guideX: null, guideY: null };
    if (!e.ctrlKey) {
      const gx = window.PIGuides ? PIGuides.getSnapX() : [];
      const gy = window.PIGuides ? PIGuides.getSnapY() : [];
      guide = PILayerTransform.snapToCanvas(layer, 6 / PICanvasEngine.getZoom(), this.contentRect, gx, gy);
    }
    PILayerManager.requestRender();
    this.drawGuides(guide);
  }
  onMouseUp(e) {
    if (this.dragging) {
      this.dragging = false;
      const layer = PILayerManager.getActive();
      if (layer) { layer.x = Math.round(layer.x); layer.y = Math.round(layer.y); }
      PICanvasEngine.clearOverlay();
      PIHistoryManager.push('移動');
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
    const layer = PILayerManager.getActive();
    if (!layer) return null;
    const align = (m) => { PILayerTransform.alignInCanvas(layer, m); PILayerManager.requestRender(); PIHistoryManager.push('整列'); PIEventBus.emit('tool:properties-changed'); };
    return {
      title: '移動',
      fields: [
        { type: 'number', label: 'X', key: 'x', value: Math.round(layer.x), onChange: (v) => { layer.x = parseInt(v); PILayerManager.requestRender(); } },
        { type: 'number', label: 'Y', key: 'y', value: Math.round(layer.y), onChange: (v) => { layer.y = parseInt(v); PILayerManager.requestRender(); } },
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
