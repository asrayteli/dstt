/* PowerImager — EraserTool: 消しゴム */
window.PIEraserTool = new (class extends PIToolBase {
  constructor() {
    super('eraser', 'crosshair');
    this.erasing = false;
    this.lastX = 0; this.lastY = 0;
    this.eraserSize = 20;
  }
  onMouseDown(e) {
    const layer = PILayerManager.getActive();
    if (!layer || layer.locked) return;
    this.erasing = true;
    this.lastX = e.canvasX - layer.x;
    this.lastY = e.canvasY - layer.y;
    this.erase(layer, this.lastX, this.lastY, this.lastX, this.lastY);
  }
  onMouseMove(e) {
    if (!this.erasing) return;
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const x = e.canvasX - layer.x;
    const y = e.canvasY - layer.y;
    this.erase(layer, this.lastX, this.lastY, x, y);
    this.lastX = x; this.lastY = y;
  }
  onMouseUp(e) {
    if (this.erasing) {
      this.erasing = false;
      PIHistoryManager.push('消しゴム');
      PILayerManager.requestRender();
    }
  }
  erase(layer, x1, y1, x2, y2) {
    const ctx = layer.ctx;
    ctx.save();
    PICanvasEngine.configureContext(ctx);
    ctx.globalCompositeOperation = 'destination-out';
    ctx.strokeStyle = 'rgba(0,0,0,1)';
    ctx.lineWidth = this.eraserSize;
    ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    ctx.beginPath();
    ctx.moveTo(x1, y1); ctx.lineTo(x2, y2);
    ctx.stroke();
    ctx.restore();
    PILayerManager.requestRender();
  }
  getProperties() {
    const self = this;
    return {
      title: '消しゴム',
      fields: [
        { type: 'slider', label: 'サイズ', key: 'size', value: self.eraserSize, min: 1, max: 200, onChange: (v) => { self.eraserSize = parseInt(v); } }
      ]
    };
  }
})();
