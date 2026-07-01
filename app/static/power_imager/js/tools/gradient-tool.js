/* PowerImager — GradientTool: グラデーション（線形/放射）
 * ドラッグで方向・範囲を決め、前景→背景 または 前景→透明 を塗る。選択範囲対応。
 */
window.PIGradientTool = new (class extends PIToolBase {
  constructor() {
    super('gradient', 'crosshair');
    this.gradType = 'linear';   // linear | radial
    this.colorMode = 'fg-bg';   // fg-bg | fg-transparent
    this.reverse = false;
    this.opacity = 1;
    this.dragging = false;
    this.x1 = 0; this.y1 = 0; this.x2 = 0; this.y2 = 0;
    this.layerRef = null;
  }

  onMouseDown(e) {
    let layer = PILayerManager.getActive();
    if (!layer || layer.locked) return;
    layer = PILayerManager.ensurePaintable(layer);
    this.layerRef = layer;
    this.dragging = true;
    this.x1 = e.canvasX; this.y1 = e.canvasY;
    this.x2 = e.canvasX; this.y2 = e.canvasY;
  }

  onMouseMove(e) {
    if (!this.dragging) return;
    this.x2 = e.canvasX; this.y2 = e.canvasY;
    if (e.shiftKey) this.constrainAngle();
    this.drawPreview();
  }

  onMouseUp(e) {
    if (!this.dragging) return;
    this.dragging = false;
    this.x2 = e.canvasX; this.y2 = e.canvasY;
    if (e.shiftKey) this.constrainAngle();
    PICanvasEngine.clearOverlay();
    if (Math.hypot(this.x2 - this.x1, this.y2 - this.y1) < 2) return;
    this.apply();
  }

  constrainAngle() {
    const dx = this.x2 - this.x1, dy = this.y2 - this.y1;
    const ang = Math.round(Math.atan2(dy, dx) / (Math.PI / 4)) * (Math.PI / 4);
    const len = Math.hypot(dx, dy);
    this.x2 = this.x1 + Math.cos(ang) * len;
    this.y2 = this.y1 + Math.sin(ang) * len;
  }

  buildGradient(ctx, ox, oy) {
    const x1 = this.x1 - ox, y1 = this.y1 - oy, x2 = this.x2 - ox, y2 = this.y2 - oy;
    const g = this.gradType === 'radial'
      ? ctx.createRadialGradient(x1, y1, 0, x1, y1, Math.max(1, Math.hypot(x2 - x1, y2 - y1)))
      : ctx.createLinearGradient(x1, y1, x2, y2);
    const fg = document.getElementById('fg-color-input').value;
    const bg = document.getElementById('bg-color-input').value;
    const rgb = PIColorUtils.hexToRgb(fg);
    let c0 = fg;
    let c1 = this.colorMode === 'fg-transparent' ? 'rgba(' + rgb.r + ',' + rgb.g + ',' + rgb.b + ',0)' : bg;
    if (this.reverse) { const t = c0; c0 = c1; c1 = t; }
    g.addColorStop(0, c0);
    g.addColorStop(1, c1);
    return g;
  }

  drawPreview() {
    const ctx = PICanvasEngine.getOverlayCtx();
    PICanvasEngine.clearOverlay();
    const size = PICanvasEngine.getCanvasSize();
    ctx.save();
    PICanvasEngine.configureContext(ctx);
    ctx.globalAlpha = this.opacity;
    ctx.fillStyle = this.buildGradient(ctx, 0, 0);
    ctx.fillRect(0, 0, size.width, size.height);
    ctx.restore();
    // 方向線
    ctx.save();
    ctx.globalAlpha = 1;
    ctx.strokeStyle = '#fff'; ctx.lineWidth = 1; ctx.setLineDash([4, 3]);
    ctx.beginPath(); ctx.moveTo(this.x1, this.y1); ctx.lineTo(this.x2, this.y2); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = '#7c6ff7';
    ctx.beginPath(); ctx.arc(this.x1, this.y1, 4, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath(); ctx.arc(this.x2, this.y2, 4, 0, Math.PI * 2); ctx.fill();
    ctx.restore();
  }

  apply() {
    const layer = this.layerRef;
    if (!layer) return;
    const w = layer.canvas.width, h = layer.canvas.height;
    const temp = document.createElement('canvas'); temp.width = w; temp.height = h;
    const tctx = temp.getContext('2d');
    PICanvasEngine.configureContext(tctx);
    tctx.fillStyle = this.buildGradient(tctx, layer.x, layer.y);
    tctx.fillRect(0, 0, w, h);
    if (PISelection.has()) PISelection.maskCanvasToSelection(temp, layer);
    const ctx = layer.ctx;
    ctx.save();
    PICanvasEngine.configureContext(ctx);
    ctx.globalAlpha = this.opacity;
    ctx.drawImage(temp, 0, 0);
    ctx.restore();
    PIHistoryManager.push('グラデーション');
    PILayerManager.requestRender();
  }

  getProperties() {
    const self = this;
    const typeBtn = (k, l) => ({ label: l, active: self.gradType === k, onClick: () => { self.gradType = k; PIEventBus.emit('tool:properties-changed'); } });
    const modeBtn = (k, l) => ({ label: l, active: self.colorMode === k, onClick: () => { self.colorMode = k; PIEventBus.emit('tool:properties-changed'); } });
    return {
      title: 'グラデーション',
      fields: [
        { type: 'button-group', label: '種類', key: 'type', buttons: [typeBtn('linear', '線形'), typeBtn('radial', '放射')] },
        { type: 'button-group', label: '配色', key: 'mode', buttons: [modeBtn('fg-bg', '前景→背景'), modeBtn('fg-transparent', '前景→透明')] },
        { type: 'slider', label: '不透明度', key: 'opacity', value: Math.round(self.opacity * 100), min: 1, max: 100, unit: '%', onChange: (v) => { self.opacity = parseInt(v) / 100; } },
        { type: 'checkbox', label: '方向を反転', key: 'reverse', value: self.reverse, onChange: (v) => { self.reverse = v; } }
      ]
    };
  }
})();
