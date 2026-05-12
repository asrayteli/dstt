/* PowerImager — BrushTool: ブラシ/ペン */
window.PIBrushTool = new (class extends PIToolBase {
  constructor() {
    super('brush', 'crosshair');
    this.drawing = false;
    this.lastX = 0; this.lastY = 0;
    this.brushSize = 5;
    this.brushType = 'pen';
    this.brushOpacity = 1;
  }
  onMouseDown(e) {
    const layer = PILayerManager.getActive();
    if (!layer || layer.locked) return;
    this.drawing = true;
    this.lastX = e.canvasX - layer.x;
    this.lastY = e.canvasY - layer.y;
    this.drawSegment(layer, this.lastX, this.lastY, this.lastX, this.lastY);
  }
  onMouseMove(e) {
    if (!this.drawing) return;
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const x = e.canvasX - layer.x;
    const y = e.canvasY - layer.y;
    this.drawSegment(layer, this.lastX, this.lastY, x, y);
    this.lastX = x; this.lastY = y;
  }
  onMouseUp(e) {
    if (this.drawing) {
      this.drawing = false;
      PIHistoryManager.push('ブラシ');
      PILayerManager.requestRender();
    }
  }
  drawSegment(layer, x1, y1, x2, y2) {
    const ctx = layer.ctx;
    const color = document.getElementById('fg-color-input').value;
    ctx.save();
    if (this.brushType === 'pen') {
      ctx.globalAlpha = this.brushOpacity;
      ctx.strokeStyle = color;
      ctx.lineWidth = this.brushSize;
      ctx.lineCap = 'round'; ctx.lineJoin = 'round';
      ctx.beginPath();
      ctx.moveTo(x1, y1); ctx.lineTo(x2, y2);
      ctx.stroke();
    } else if (this.brushType === 'airbrush') {
      const dist = Math.hypot(x2 - x1, y2 - y1);
      const steps = Math.max(1, Math.floor(dist));
      for (let i = 0; i < steps; i++) {
        const t = i / steps;
        const cx = x1 + (x2 - x1) * t;
        const cy = y1 + (y2 - y1) * t;
        for (let j = 0; j < 8; j++) {
          const angle = Math.random() * Math.PI * 2;
          const radius = Math.random() * this.brushSize;
          ctx.globalAlpha = 0.02 * this.brushOpacity;
          ctx.fillStyle = color;
          ctx.beginPath();
          ctx.arc(cx + Math.cos(angle) * radius, cy + Math.sin(angle) * radius, 1, 0, Math.PI * 2);
          ctx.fill();
        }
      }
    } else {
      ctx.globalAlpha = 0.3 * this.brushOpacity;
      ctx.strokeStyle = color;
      ctx.lineWidth = this.brushSize * 2;
      ctx.lineCap = 'round'; ctx.lineJoin = 'round';
      ctx.beginPath();
      ctx.moveTo(x1, y1); ctx.lineTo(x2, y2);
      ctx.stroke();
    }
    ctx.restore();
    PILayerManager.requestRender();
  }
  getProperties() {
    const self = this;
    return {
      title: 'ブラシ',
      fields: [
        { type: 'slider', label: 'サイズ', key: 'size', value: self.brushSize, min: 1, max: 100, onChange: (v) => { self.brushSize = parseInt(v); } },
        { type: 'slider', label: '不透明度', key: 'opacity', value: Math.round(self.brushOpacity * 100), min: 1, max: 100, unit: '%', onChange: (v) => { self.brushOpacity = parseInt(v) / 100; } },
        {
          type: 'button-group', label: 'タイプ', key: 'type',
          buttons: [
            { label: 'ペン', active: self.brushType === 'pen', onClick: () => { self.brushType = 'pen'; PIEventBus.emit('tool:properties-changed'); } },
            { label: 'マーカー', active: self.brushType === 'marker', onClick: () => { self.brushType = 'marker'; PIEventBus.emit('tool:properties-changed'); } },
            { label: 'エアブラシ', active: self.brushType === 'airbrush', onClick: () => { self.brushType = 'airbrush'; PIEventBus.emit('tool:properties-changed'); } }
          ]
        }
      ]
    };
  }
})();
