/* PowerImager — SelectTool: 選択範囲 */
window.PISelectTool = new (class extends PIToolBase {
  constructor() {
    super('select', 'crosshair');
    this.selecting = false;
    this.selection = null;
    this.startX = 0; this.startY = 0;
    this.mode = 'rect';
    this.marchOffset = 0;
    this.marchInterval = null;
  }
  activate() {
    super.activate();
    this.startMarchingAnts();
  }
  deactivate() {
    super.deactivate();
    this.stopMarchingAnts();
    PICanvasEngine.clearOverlay();
  }
  onMouseDown(e) {
    this.selecting = true;
    this.startX = e.canvasX; this.startY = e.canvasY;
    this.selection = null;
    PICanvasEngine.clearOverlay();
  }
  onMouseMove(e) {
    if (!this.selecting) return;
    const x = Math.min(this.startX, e.canvasX);
    const y = Math.min(this.startY, e.canvasY);
    const w = Math.abs(e.canvasX - this.startX);
    const h = Math.abs(e.canvasY - this.startY);
    this.selection = { x, y, w, h, mode: this.mode };
    this.drawSelection();
  }
  onMouseUp(e) {
    this.selecting = false;
    if (this.selection && this.selection.w > 2 && this.selection.h > 2) {
      PIEventBus.emit('selection:changed', this.selection);
    }
  }
  drawSelection() {
    if (!this.selection) return;
    const ctx = PICanvasEngine.getOverlayCtx();
    PICanvasEngine.clearOverlay();
    const s = this.selection;
    ctx.save();
    ctx.setLineDash([6, 4]);
    ctx.lineDashOffset = -this.marchOffset;
    ctx.strokeStyle = '#fff';
    ctx.lineWidth = 1;
    if (s.mode === 'ellipse') {
      ctx.beginPath();
      ctx.ellipse(s.x + s.w / 2, s.y + s.h / 2, s.w / 2, s.h / 2, 0, 0, Math.PI * 2);
      ctx.stroke();
    } else {
      ctx.strokeRect(s.x, s.y, s.w, s.h);
    }
    ctx.strokeStyle = '#000';
    ctx.lineDashOffset = -(this.marchOffset + 6);
    if (s.mode === 'ellipse') {
      ctx.beginPath();
      ctx.ellipse(s.x + s.w / 2, s.y + s.h / 2, s.w / 2, s.h / 2, 0, 0, Math.PI * 2);
      ctx.stroke();
    } else {
      ctx.strokeRect(s.x, s.y, s.w, s.h);
    }
    ctx.restore();
  }
  startMarchingAnts() {
    this.marchInterval = setInterval(() => {
      this.marchOffset = (this.marchOffset + 1) % 20;
      if (this.selection) this.drawSelection();
    }, 80);
  }
  stopMarchingAnts() {
    clearInterval(this.marchInterval);
  }
  clearSelection() {
    this.selection = null;
    PICanvasEngine.clearOverlay();
    PIEventBus.emit('selection:changed', null);
  }
  getProperties() {
    return {
      title: '選択',
      fields: [
        {
          type: 'button-group', label: '形状', key: 'mode',
          buttons: [
            { label: '矩形', active: this.mode === 'rect', onClick: () => { this.mode = 'rect'; } },
            { label: '楕円', active: this.mode === 'ellipse', onClick: () => { this.mode = 'ellipse'; } }
          ]
        }
      ]
    };
  }
})();
