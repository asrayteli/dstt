/* PowerImager — CropTool: 切り抜き */
window.PICropTool = new (class extends PIToolBase {
  constructor() {
    super('crop', 'crosshair');
    this.cropping = false;
    this.cropRect = null;
    this.startX = 0; this.startY = 0;
    this.dragHandle = null;
    this.aspect = null;
  }
  activate() {
    super.activate();
    this.cropRect = null;
    const overlay = document.getElementById('crop-overlay');
    if (overlay) overlay.classList.remove('hidden');
  }
  deactivate() {
    super.deactivate();
    this.cropRect = null;
    const overlay = document.getElementById('crop-overlay');
    if (overlay) overlay.classList.add('hidden');
    PICanvasEngine.clearOverlay();
  }
  onMouseDown(e) {
    if (this.cropRect) {
      const handle = this.hitHandle(e.canvasX, e.canvasY);
      if (handle) {
        this.dragHandle = handle;
        this.startX = e.canvasX; this.startY = e.canvasY;
        return;
      }
      if (this.pointInCrop(e.canvasX, e.canvasY)) {
        this.dragHandle = 'move';
        this.startX = e.canvasX; this.startY = e.canvasY;
        return;
      }
    }
    this.cropping = true;
    this.startX = e.canvasX; this.startY = e.canvasY;
    this.cropRect = null;
  }
  onMouseMove(e) {
    if (this.cropping) {
      let w = Math.abs(e.canvasX - this.startX);
      let h = Math.abs(e.canvasY - this.startY);
      if (this.aspect) {
        // アスペクト比を固定（ドラッグ量の大きい方に合わせる）
        if (w / this.aspect >= h) h = w / this.aspect; else w = h * this.aspect;
      }
      const x = e.canvasX >= this.startX ? this.startX : this.startX - w;
      const y = e.canvasY >= this.startY ? this.startY : this.startY - h;
      this.cropRect = { x, y, w, h };
      this.drawCrop();
      return;
    }
    if (this.dragHandle && this.cropRect) {
      const dx = e.canvasX - this.startX;
      const dy = e.canvasY - this.startY;
      const r = this.cropRect;
      if (this.dragHandle === 'move') {
        r.x += dx; r.y += dy;
      } else if (this.dragHandle === 'se') {
        r.w += dx; r.h += dy;
      } else if (this.dragHandle === 'sw') {
        r.x += dx; r.w -= dx; r.h += dy;
      } else if (this.dragHandle === 'ne') {
        r.w += dx; r.y += dy; r.h -= dy;
      } else if (this.dragHandle === 'nw') {
        r.x += dx; r.y += dy; r.w -= dx; r.h -= dy;
      } else if (this.dragHandle === 'n') {
        r.y += dy; r.h -= dy;
      } else if (this.dragHandle === 's') {
        r.h += dy;
      } else if (this.dragHandle === 'e') {
        r.w += dx;
      } else if (this.dragHandle === 'w') {
        r.x += dx; r.w -= dx;
      }
      this.startX = e.canvasX; this.startY = e.canvasY;
      this.drawCrop();
    }
  }
  onMouseUp(e) {
    this.cropping = false;
    this.dragHandle = null;
  }
  hitHandle(x, y) {
    if (!this.cropRect) return null;
    const r = this.cropRect;
    const hs = 6;
    const handles = {
      'nw': [r.x, r.y], 'n': [r.x + r.w / 2, r.y],
      'ne': [r.x + r.w, r.y], 'w': [r.x, r.y + r.h / 2],
      'e': [r.x + r.w, r.y + r.h / 2], 'sw': [r.x, r.y + r.h],
      's': [r.x + r.w / 2, r.y + r.h], 'se': [r.x + r.w, r.y + r.h]
    };
    for (const [key, [hx, hy]] of Object.entries(handles)) {
      if (Math.abs(x - hx) <= hs && Math.abs(y - hy) <= hs) return key;
    }
    return null;
  }
  pointInCrop(x, y) {
    if (!this.cropRect) return false;
    const r = this.cropRect;
    return x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h;
  }
  drawCrop() {
    const ctx = PICanvasEngine.getOverlayCtx();
    PICanvasEngine.configureContext(ctx);
    const size = PICanvasEngine.getCanvasSize();
    PICanvasEngine.clearOverlay();
    if (!this.cropRect) return;
    const r = this.cropRect;
    ctx.save();
    ctx.fillStyle = 'rgba(0,0,0,0.5)';
    ctx.fillRect(0, 0, size.width, size.height);
    ctx.clearRect(r.x, r.y, r.w, r.h);
    ctx.strokeStyle = '#fff';
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.strokeRect(r.x, r.y, r.w, r.h);
    ctx.setLineDash([]);
    const third_w = r.w / 3, third_h = r.h / 3;
    ctx.strokeStyle = 'rgba(255,255,255,0.3)';
    ctx.beginPath();
    ctx.moveTo(r.x + third_w, r.y); ctx.lineTo(r.x + third_w, r.y + r.h);
    ctx.moveTo(r.x + third_w * 2, r.y); ctx.lineTo(r.x + third_w * 2, r.y + r.h);
    ctx.moveTo(r.x, r.y + third_h); ctx.lineTo(r.x + r.w, r.y + third_h);
    ctx.moveTo(r.x, r.y + third_h * 2); ctx.lineTo(r.x + r.w, r.y + third_h * 2);
    ctx.stroke();
    const handles = [
      [r.x, r.y], [r.x + r.w / 2, r.y], [r.x + r.w, r.y],
      [r.x, r.y + r.h / 2], [r.x + r.w, r.y + r.h / 2],
      [r.x, r.y + r.h], [r.x + r.w / 2, r.y + r.h], [r.x + r.w, r.y + r.h]
    ];
    ctx.fillStyle = '#fff';
    handles.forEach(([hx, hy]) => { ctx.fillRect(hx - 4, hy - 4, 8, 8); });
    ctx.restore();
  }
  applyCrop() {
    if (!this.cropRect || this.cropRect.w < 1 || this.cropRect.h < 1) return;
    const r = this.cropRect;
    const rx = Math.round(r.x), ry = Math.round(r.y);
    const rw = Math.round(r.w), rh = Math.round(r.h);
    PILayerManager.getAll().forEach(layer => {
      PILayerManager.bakeLayerToRaster(layer); // 回転/拡縮を見た目どおり確定してから切り抜く
      const newCanvas = document.createElement('canvas');
      newCanvas.width = rw; newCanvas.height = rh;
      const nctx = newCanvas.getContext('2d');
      PICanvasEngine.configureContext(nctx);
      nctx.drawImage(layer.canvas, layer.x - rx, layer.y - ry);
      layer.canvas.width = rw; layer.canvas.height = rh;
      PICanvasEngine.configureContext(layer.ctx);
      layer.ctx.drawImage(newCanvas, 0, 0);
      layer.x = 0; layer.y = 0;
    });
    PICanvasEngine.setCanvasSize(rw, rh);
    PICanvasEngine.fitToViewport();
    this.cropRect = null;
    PICanvasEngine.clearOverlay();
    PIHistoryManager.push('切り抜き');
    PILayerManager.requestRender();
    PIEventBus.emit('tool:properties-changed');
  }
  cancelCrop() {
    this.cropRect = null;
    this.cropping = false;
    this.dragHandle = null;
    PICanvasEngine.clearOverlay();
  }
  setAspect(ratio) {
    this.aspect = ratio;
    if (this.cropRect && ratio) {
      // 既存の枠にも即座に比率を反映
      this.cropRect.h = this.cropRect.w / ratio;
      this.drawCrop();
    }
    PIEventBus.emit('tool:properties-changed');
  }
  getProperties() {
    const self = this;
    const ar = self.aspect;
    const isFree = !ar;
    const near = (r) => ar && Math.abs(ar - r) < 0.001;
    return {
      title: '切り抜き',
      fields: [
        {
          type: 'button-group', label: '比率', key: 'aspect',
          buttons: [
            { label: '自由', active: isFree, onClick: () => self.setAspect(null) },
            { label: '1:1', active: near(1), onClick: () => self.setAspect(1) },
            { label: '4:3', active: near(4 / 3), onClick: () => self.setAspect(4 / 3) },
            { label: '3:2', active: near(3 / 2), onClick: () => self.setAspect(3 / 2) },
            { label: '16:9', active: near(16 / 9), onClick: () => self.setAspect(16 / 9) }
          ]
        },
        {
          type: 'button', label: '適用 (Enter)', key: 'apply',
          onClick: () => self.applyCrop()
        },
        {
          type: 'button', label: 'キャンバス全体を選択', key: 'selectAll',
          onClick: () => {
            const s = PICanvasEngine.getCanvasSize();
            self.cropRect = { x: 0, y: 0, w: s.width, h: s.height };
            self.drawCrop();
          }
        }
      ]
    };
  }
})();
