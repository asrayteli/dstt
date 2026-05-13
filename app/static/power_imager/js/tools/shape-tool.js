/* PowerImager — ShapeTool: 図形描画 */
window.PIShapeTool = new (class extends PIToolBase {
  constructor() {
    super('shape', 'crosshair');
    this.drawing = false;
    this.startX = 0; this.startY = 0;
    this.shapeType = 'rect';
    this.fillShape = true;
    this.strokeShape = true;
    this.cornerRadius = 0;
    this.strokeWidth = 2;
  }
  onMouseDown(e) {
    const layer = PILayerManager.getActive();
    if (!layer || layer.locked) return;
    this.drawing = true;
    this.startX = e.canvasX; this.startY = e.canvasY;
  }
  onMouseMove(e) {
    if (!this.drawing) return;
    const ctx = PICanvasEngine.getOverlayCtx();
    PICanvasEngine.configureContext(ctx);
    PICanvasEngine.clearOverlay();
    const fgColor = document.getElementById('fg-color-input').value;
    const bgColor = document.getElementById('bg-color-input').value;
    this.drawShapeOnCtx(ctx, this.startX, this.startY, e.canvasX, e.canvasY, fgColor, bgColor, e.shiftKey);
  }
  onMouseUp(e) {
    if (!this.drawing) return;
    this.drawing = false;
    PICanvasEngine.clearOverlay();
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const fgColor = document.getElementById('fg-color-input').value;
    const bgColor = document.getElementById('bg-color-input').value;
    const ctx = layer.ctx;
    ctx.save();
    PICanvasEngine.configureContext(ctx);
    ctx.translate(-layer.x, -layer.y);
    this.drawShapeOnCtx(ctx, this.startX, this.startY, e.canvasX, e.canvasY, fgColor, bgColor, e.shiftKey);
    ctx.restore();
    PIHistoryManager.push('図形');
    PILayerManager.requestRender();
  }
  drawShapeOnCtx(ctx, x1, y1, x2, y2, fgColor, bgColor, shiftKey) {
    ctx.save();
    PICanvasEngine.configureContext(ctx);
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    const x = Math.min(x1, x2), y = Math.min(y1, y2);
    let w = Math.abs(x2 - x1), h = Math.abs(y2 - y1);
    if (shiftKey) { const s = Math.min(w, h); w = s; h = s; }
    if (this.shapeType === 'rect') {
      if (this.fillShape) { ctx.fillStyle = bgColor; ctx.fillRect(x, y, w, h); }
      if (this.strokeShape) { ctx.strokeStyle = fgColor; ctx.lineWidth = this.strokeWidth; ctx.strokeRect(x, y, w, h); }
    } else if (this.shapeType === 'ellipse') {
      ctx.beginPath();
      ctx.ellipse(x + w / 2, y + h / 2, w / 2, h / 2, 0, 0, Math.PI * 2);
      if (this.fillShape) { ctx.fillStyle = bgColor; ctx.fill(); }
      if (this.strokeShape) { ctx.strokeStyle = fgColor; ctx.lineWidth = this.strokeWidth; ctx.stroke(); }
    } else if (this.shapeType === 'line') {
      ctx.beginPath();
      ctx.moveTo(x1, y1); ctx.lineTo(x2, y2);
      ctx.strokeStyle = fgColor; ctx.lineWidth = this.strokeWidth; ctx.stroke();
    } else if (this.shapeType === 'arrow') {
      ctx.beginPath();
      ctx.moveTo(x1, y1); ctx.lineTo(x2, y2);
      ctx.strokeStyle = fgColor; ctx.lineWidth = this.strokeWidth; ctx.stroke();
      const angle = Math.atan2(y2 - y1, x2 - x1);
      const headLen = 12 + this.strokeWidth * 2;
      ctx.beginPath();
      ctx.moveTo(x2, y2);
      ctx.lineTo(x2 - headLen * Math.cos(angle - Math.PI / 6), y2 - headLen * Math.sin(angle - Math.PI / 6));
      ctx.moveTo(x2, y2);
      ctx.lineTo(x2 - headLen * Math.cos(angle + Math.PI / 6), y2 - headLen * Math.sin(angle + Math.PI / 6));
      ctx.stroke();
    }
    ctx.restore();
  }
  getProperties() {
    const self = this;
    return {
      title: '図形',
      fields: [
        {
          type: 'button-group', label: '形状', key: 'shapeType',
          buttons: [
            { label: '矩形', active: self.shapeType === 'rect', onClick: () => { self.shapeType = 'rect'; PIEventBus.emit('tool:properties-changed'); } },
            { label: '楕円', active: self.shapeType === 'ellipse', onClick: () => { self.shapeType = 'ellipse'; PIEventBus.emit('tool:properties-changed'); } },
            { label: '線', active: self.shapeType === 'line', onClick: () => { self.shapeType = 'line'; PIEventBus.emit('tool:properties-changed'); } },
            { label: '矢印', active: self.shapeType === 'arrow', onClick: () => { self.shapeType = 'arrow'; PIEventBus.emit('tool:properties-changed'); } }
          ]
        },
        { type: 'slider', label: '線幅', key: 'strokeWidth', value: self.strokeWidth, min: 1, max: 20, onChange: (v) => { self.strokeWidth = parseInt(v); } },
        { type: 'checkbox', label: '塗り', key: 'fill', value: self.fillShape, onChange: (v) => { self.fillShape = v; } },
        { type: 'checkbox', label: '枠線', key: 'stroke', value: self.strokeShape, onChange: (v) => { self.strokeShape = v; } }
      ]
    };
  }
})();
