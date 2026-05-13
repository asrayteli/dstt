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
    } else {
      this.clearSelection();
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
  getSelection() {
    return this.selection ? { ...this.selection } : null;
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
        },
        {
          type: 'button', label: '選択解除', key: 'clear',
          onClick: () => this.clearSelection()
        }
      ]
    };
  }
})();

window.PISelection = {
  get() {
    return window.PISelectTool && PISelectTool.getSelection ? PISelectTool.getSelection() : null;
  },

  has() {
    const s = this.get();
    return !!(s && s.w > 0 && s.h > 0);
  },

  getLayerBounds(layer) {
    const s = this.get();
    if (!s || !layer) return null;

    const left = Math.max(0, Math.floor(s.x - layer.x));
    const top = Math.max(0, Math.floor(s.y - layer.y));
    const right = Math.min(layer.canvas.width, Math.ceil(s.x + s.w - layer.x));
    const bottom = Math.min(layer.canvas.height, Math.ceil(s.y + s.h - layer.y));
    const w = right - left;
    const h = bottom - top;
    if (w <= 0 || h <= 0) return null;

    return {
      x: left, y: top, w, h,
      mode: s.mode || 'rect',
      sourceX: s.x - layer.x,
      sourceY: s.y - layer.y,
      sourceW: s.w,
      sourceH: s.h
    };
  },

  contains(bounds, x, y) {
    if (!bounds) return true;
    if (x < bounds.x || y < bounds.y || x >= bounds.x + bounds.w || y >= bounds.y + bounds.h) return false;
    if (bounds.mode !== 'ellipse') return true;
    const cx = bounds.sourceX + bounds.sourceW / 2;
    const cy = bounds.sourceY + bounds.sourceH / 2;
    const rx = Math.max(0.0001, bounds.sourceW / 2);
    const ry = Math.max(0.0001, bounds.sourceH / 2);
    const nx = (x + 0.5 - cx) / rx;
    const ny = (y + 0.5 - cy) / ry;
    return nx * nx + ny * ny <= 1;
  },

  applyImageData(layer, processor) {
    const bounds = this.getLayerBounds(layer);
    if (!bounds) {
      if (this.has()) return;
      const imgData = layer.ctx.getImageData(0, 0, layer.canvas.width, layer.canvas.height);
      processor(imgData.data, 0, 0, layer.canvas.width, layer.canvas.height, null);
      layer.ctx.putImageData(imgData, 0, 0);
      return;
    }

    const imgData = layer.ctx.getImageData(bounds.x, bounds.y, bounds.w, bounds.h);
    processor(imgData.data, bounds.x, bounds.y, bounds.w, bounds.h, bounds);
    layer.ctx.putImageData(imgData, bounds.x, bounds.y);
  },

  clipContextToSelection(ctx, layer) {
    const bounds = this.getLayerBounds(layer);
    if (!bounds) return false;
    ctx.beginPath();
    if (bounds.mode === 'ellipse') {
      ctx.ellipse(
        bounds.sourceX + bounds.sourceW / 2,
        bounds.sourceY + bounds.sourceH / 2,
        bounds.sourceW / 2,
        bounds.sourceH / 2,
        0,
        0,
        Math.PI * 2
      );
    } else {
      ctx.rect(bounds.x, bounds.y, bounds.w, bounds.h);
    }
    ctx.clip();
    return true;
  },

  putCanvasEffect(layer, sourceCanvas) {
    const ctx = layer.ctx;
    ctx.save();
    PICanvasEngine.configureContext(ctx);
    const clipped = this.clipContextToSelection(ctx, layer);
    if (!clipped && this.has()) {
      ctx.restore();
      return;
    }
    if (!clipped) ctx.clearRect(0, 0, layer.canvas.width, layer.canvas.height);
    ctx.drawImage(sourceCanvas, 0, 0);
    ctx.restore();
  }
};
