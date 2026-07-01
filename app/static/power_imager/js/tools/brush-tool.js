/* PowerImager — BrushTool: ブラシ/ペン
 * ストロークバッファ方式: 1ストロークぶんを全不透明度の専用バッファに描き、
 * 確定時にレイヤーへ「設定不透明度で1回だけ」合成する。これにより
 * ストローク内のスタンプ重なりで濃度が累積し不透明度設定が無効化される問題を防ぐ
 * （Photoshop の「不透明度」と同じ＝1ストロークの最大濃度は設定値で頭打ち）。
 */
window.PIBrushTool = new (class extends PIToolBase {
  constructor() {
    super('brush', 'none');
    this.drawing = false;
    this.lastX = 0; this.lastY = 0;
    this.brushSize = 12;
    this.brushType = 'pen';
    this.brushOpacity = 1;
    this.hardness = 85;
    this.buffer = null; this.bufferCtx = null;
    this.layerRef = null;
    this.maskStroke = false; this.maskLayer = null;
    this.colorSolid = 'rgba(0,0,0,1)';
    this.colorClear = 'rgba(0,0,0,0)';
  }

  getCursorRadius() { return this.brushSize / 2; }

  blendForType() { return this.brushType === 'marker' ? 'multiply' : 'source-over'; }

  cacheColor() {
    const hex = document.getElementById('fg-color-input').value;
    const rgb = PIColorUtils.hexToRgb(hex);
    this.colorSolid = 'rgba(' + rgb.r + ',' + rgb.g + ',' + rgb.b + ',1)';
    this.colorClear = 'rgba(' + rgb.r + ',' + rgb.g + ',' + rgb.b + ',0)';
  }

  onMouseDown(e) {
    // Alt押下中は一時スポイト（描画せず前景色を採取）
    if (e.altKey) { this.pickColor(e.canvasX, e.canvasY); return; }
    let layer = PILayerManager.getActive();
    if (!layer || layer.locked) return;
    // マスク編集中はマスクへ直接描画（ブラシ＝隠す）
    if (PILayerManager.isMaskEditing(layer)) {
      this.maskStroke = true;
      this.maskLayer = layer;
      const lp = PILayerTransform.canvasToLocal(layer, e.canvasX, e.canvasY);
      this.lastX = lp.x; this.lastY = lp.y;
      this.paintMask(layer, lp.x, lp.y, lp.x, lp.y);
      PILayerManager.requestRender();
      return;
    }
    layer = PILayerManager.ensurePaintable(layer);
    this.drawing = true;
    this.layerRef = layer;
    this.cacheColor();
    this.buffer = document.createElement('canvas');
    this.buffer.width = layer.canvas.width;
    this.buffer.height = layer.canvas.height;
    this.bufferCtx = this.buffer.getContext('2d');
    PICanvasEngine.configureContext(this.bufferCtx);
    this.lastX = e.canvasX - layer.x;
    this.lastY = e.canvasY - layer.y;
    if (this.brushType === 'airbrush') this.spray(this.lastX, this.lastY, this.lastX, this.lastY);
    else this.stampDab(this.lastX, this.lastY);
    this.updatePreview();
  }

  onMouseMove(e) {
    if (this.maskStroke) {
      const layer = this.maskLayer;
      const lp = PILayerTransform.canvasToLocal(layer, e.canvasX, e.canvasY);
      this.paintMask(layer, this.lastX, this.lastY, lp.x, lp.y);
      this.lastX = lp.x; this.lastY = lp.y;
      PILayerManager.requestRender();
      return;
    }
    if (!this.drawing) return;
    const layer = this.layerRef;
    if (!layer) return;
    const x = e.canvasX - layer.x;
    const y = e.canvasY - layer.y;
    if (this.brushType === 'airbrush') this.spray(this.lastX, this.lastY, x, y);
    else this.strokeTo(x, y);
    this.lastX = x; this.lastY = y;
    this.updatePreview();
  }

  onMouseUp() {
    if (this.maskStroke) {
      this.maskStroke = false; this.maskLayer = null;
      PIHistoryManager.push('マスク描画');
      PILayerManager.requestRender();
      return;
    }
    if (!this.drawing) return;
    this.drawing = false;
    this.commit();
  }

  // マスクへの描画（ブラシ＝隠す: destination-out）
  paintMask(layer, x1, y1, x2, y2) {
    if (!layer.mask) return;
    const ctx = layer.mask.getContext('2d');
    ctx.save();
    PICanvasEngine.configureContext(ctx);
    ctx.globalCompositeOperation = 'destination-out';
    ctx.globalAlpha = this.brushOpacity;
    ctx.strokeStyle = 'rgba(0,0,0,1)';
    ctx.lineWidth = this.brushSize;
    ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
    ctx.restore();
  }

  strokeTo(x2, y2) {
    const x1 = this.lastX, y1 = this.lastY;
    if (this.hardness >= 100) {
      // 硬縁: 連続ラインを全不透明度で描く（重なっても不透明のままなので筋ムラが出ない）
      const ctx = this.bufferCtx;
      ctx.strokeStyle = this.colorSolid;
      ctx.lineWidth = this.brushSize;
      ctx.lineCap = 'round'; ctx.lineJoin = 'round';
      ctx.beginPath();
      ctx.moveTo(x1, y1); ctx.lineTo(x2, y2);
      ctx.stroke();
    } else {
      // ぼけ足あり: 放射状グラデのスタンプを間隔で並べる
      const r = Math.max(0.5, this.brushSize / 2);
      const spacing = Math.max(0.5, r * 0.18);
      const dist = Math.hypot(x2 - x1, y2 - y1);
      const steps = Math.max(1, Math.round(dist / spacing));
      for (let i = 1; i <= steps; i++) {
        const t = i / steps;
        this.stampDab(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t);
      }
    }
  }

  stampDab(cx, cy) {
    const ctx = this.bufferCtx;
    const r = Math.max(0.5, this.brushSize / 2);
    if (this.hardness >= 100) {
      ctx.fillStyle = this.colorSolid;
      ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.fill();
      return;
    }
    const inner = Math.min(r - 0.01, r * (this.hardness / 100));
    const g = ctx.createRadialGradient(cx, cy, Math.max(0, inner), cx, cy, r);
    g.addColorStop(0, this.colorSolid);
    g.addColorStop(1, this.colorClear);
    ctx.fillStyle = g;
    ctx.beginPath(); ctx.arc(cx, cy, r, 0, Math.PI * 2); ctx.fill();
  }

  spray(x1, y1, x2, y2) {
    const ctx = this.bufferCtx;
    const r = Math.max(1, this.brushSize / 2);
    const dist = Math.hypot(x2 - x1, y2 - y1);
    const steps = Math.max(1, Math.floor(dist));
    ctx.fillStyle = this.colorSolid;
    for (let i = 0; i <= steps; i++) {
      const t = steps ? i / steps : 0;
      const cx = x1 + (x2 - x1) * t;
      const cy = y1 + (y2 - y1) * t;
      for (let j = 0; j < 6; j++) {
        const a = Math.random() * Math.PI * 2;
        const rr = Math.random() * r;
        ctx.globalAlpha = 0.08;
        ctx.beginPath();
        ctx.arc(cx + Math.cos(a) * rr, cy + Math.sin(a) * rr, 1, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    ctx.globalAlpha = 1;
  }

  updatePreview() {
    PICanvasEngine.setStrokePreview({
      layerId: this.layerRef.id,
      canvas: this.buffer,
      x: this.layerRef.x,
      y: this.layerRef.y,
      opacity: this.brushOpacity,
      blendMode: this.blendForType()
    });
    PILayerManager.requestRender();
  }

  commit() {
    const layer = this.layerRef;
    if (layer && this.buffer) {
      // 選択範囲があれば、ストロークを選択内へ切り抜く
      if (PISelection.has()) PISelection.maskCanvasToSelection(this.buffer, layer);
      const ctx = layer.ctx;
      ctx.save();
      PICanvasEngine.configureContext(ctx);
      ctx.globalAlpha = this.brushOpacity;
      ctx.globalCompositeOperation = this.blendForType();
      ctx.drawImage(this.buffer, 0, 0);
      ctx.restore();
    }
    PICanvasEngine.clearStrokePreview();
    this.buffer = null; this.bufferCtx = null; this.layerRef = null;
    PIHistoryManager.push('ブラシ');
    PILayerManager.requestRender();
  }

  pickColor(canvasX, canvasY) {
    const canvas = PICanvasEngine.getDisplayCanvas();
    const x = Math.round(canvasX), y = Math.round(canvasY);
    if (x < 0 || y < 0 || x >= canvas.width || y >= canvas.height) return;
    const px = canvas.getContext('2d').getImageData(x, y, 1, 1).data;
    const hex = PIColorUtils.rgbToHex(px[0], px[1], px[2]);
    document.getElementById('fg-color-input').value = hex;
    PIEventBus.emit('color:picked', hex);
  }

  getProperties() {
    const self = this;
    return {
      title: 'ブラシ',
      fields: [
        { type: 'slider', label: 'サイズ', key: 'size', value: self.brushSize, min: 1, max: 300, onChange: (v) => { self.brushSize = parseInt(v); PIEventBus.emit('brush:size-changed'); } },
        { type: 'slider', label: '不透明度', key: 'opacity', value: Math.round(self.brushOpacity * 100), min: 1, max: 100, unit: '%', onChange: (v) => { self.brushOpacity = parseInt(v) / 100; } },
        { type: 'slider', label: '硬さ', key: 'hardness', value: self.hardness, min: 1, max: 100, unit: '%', onChange: (v) => { self.hardness = parseInt(v); } },
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
