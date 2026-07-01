/* PowerImager — ShapeTool: 図形をオブジェクトレイヤーとして作成・再編集する
 * 描いた瞬間に焼き付けず shapeData を持つレイヤーを作るので、後から
 * 移動・塗り/枠の変更・透明化・形状変更・角丸調整が何度でもできる。
 * （拡縮・回転は変形ツール T-handle で行う）
 */
window.PIShapeTool = new (class extends PIToolBase {
  constructor() {
    super('shape', 'crosshair');
    this.drawing = false;
    this.startX = 0; this.startY = 0;
    this.shapeType = 'rect';
    this.fillEnabled = true;
    this.strokeEnabled = true;
    this.cornerRadius = 0;
    this.strokeWidth = 3;
    // 既存図形の移動用
    this.pendingLayer = null;
    this.movingLayer = false;
    this.dragStartX = 0; this.dragStartY = 0;
    this.layerStartX = 0; this.layerStartY = 0;
    this.dragThreshold = 3;
  }

  deactivate() {
    super.deactivate();
    this.drawing = false;
    this.pendingLayer = null;
    this.movingLayer = false;
    PICanvasEngine.clearOverlay();
  }

  findShapeLayerAt(cx, cy) {
    const layers = PILayerManager.getAll();
    for (let i = layers.length - 1; i >= 0; i--) {
      const l = layers[i];
      if (l.type === 'shape' && l.visible && !l.locked && PILayerTransform.hitTest(l, cx, cy)) return l;
    }
    return null;
  }

  onMouseDown(e) {
    const existing = this.findShapeLayerAt(e.canvasX, e.canvasY);
    if (existing) {
      // 既存図形をクリック → 選択して移動待機
      const idx = PILayerManager.getAll().indexOf(existing);
      if (idx >= 0) PILayerManager.setActiveIndex(idx);
      this.pendingLayer = existing;
      this.movingLayer = false;
      this.dragStartX = e.canvasX; this.dragStartY = e.canvasY;
      this.layerStartX = existing.x; this.layerStartY = existing.y;
      PIEventBus.emit('tool:properties-changed');
      return;
    }
    // 空き領域 → 新規図形の描画開始
    this.drawing = true;
    this.startX = e.canvasX; this.startY = e.canvasY;
  }

  onMouseMove(e) {
    if (this.pendingLayer) {
      const dx = e.canvasX - this.dragStartX;
      const dy = e.canvasY - this.dragStartY;
      if (!this.movingLayer && Math.hypot(dx, dy) >= this.dragThreshold) {
        this.movingLayer = true;
        const vp = PICanvasEngine.getViewport(); if (vp) vp.style.cursor = 'move';
      }
      if (this.movingLayer) {
        this.pendingLayer.x = this.layerStartX + dx;
        this.pendingLayer.y = this.layerStartY + dy;
        PILayerManager.requestRender();
      }
      return;
    }
    if (!this.drawing) {
      const vp = PICanvasEngine.getViewport();
      if (vp) vp.style.cursor = this.findShapeLayerAt(e.canvasX, e.canvasY) ? 'move' : this.cursor;
      return;
    }
    // 新規描画のライブプレビュー（オーバーレイ）
    const ctx = PICanvasEngine.getOverlayCtx();
    PICanvasEngine.configureContext(ctx);
    PICanvasEngine.clearOverlay();
    this.drawPreview(ctx, this.startX, this.startY, e.canvasX, e.canvasY, e.shiftKey);
  }

  onMouseUp(e) {
    if (this.pendingLayer) {
      const layer = this.pendingLayer;
      this.pendingLayer = null;
      if (this.movingLayer) {
        this.movingLayer = false;
        layer.x = Math.round(layer.x); layer.y = Math.round(layer.y);
        PILayerManager.requestRender();
        PIHistoryManager.push('図形を移動');
      }
      const vp = PICanvasEngine.getViewport();
      if (vp) vp.style.cursor = this.findShapeLayerAt(e.canvasX, e.canvasY) ? 'move' : this.cursor;
      return;
    }
    if (!this.drawing) return;
    this.drawing = false;
    PICanvasEngine.clearOverlay();

    let x1 = this.startX, y1 = this.startY, x2 = e.canvasX, y2 = e.canvasY;
    if (e.shiftKey && (this.shapeType === 'rect' || this.shapeType === 'ellipse')) {
      const s = Math.min(Math.abs(x2 - x1), Math.abs(y2 - y1));
      x2 = x1 + Math.sign(x2 - x1 || 1) * s;
      y2 = y1 + Math.sign(y2 - y1 || 1) * s;
    }
    const w = Math.abs(x2 - x1), h = Math.abs(y2 - y1);
    if (Math.max(w, h) < 2) return; // 小さすぎる操作は無視

    const fg = document.getElementById('fg-color-input').value;
    const bg = document.getElementById('bg-color-input').value;
    const isLine = this.shapeType === 'line' || this.shapeType === 'arrow';
    const shapeData = {
      shapeType: this.shapeType,
      fillEnabled: this.fillEnabled && !isLine,
      fillColor: bg,
      strokeEnabled: this.strokeEnabled || isLine,
      strokeColor: fg,
      strokeWidth: this.strokeWidth,
      cornerRadius: this.cornerRadius,
      w: w, h: h,
      x1: x1, y1: y1, x2: x2, y2: y2,
      bx: Math.min(x1, x2), by: Math.min(y1, y2)
    };
    PILayerManager.addShapeLayer(shapeData);
    PIHistoryManager.push('図形');
    PIEventBus.emit('tool:properties-changed');
  }

  drawPreview(ctx, x1, y1, x2, y2, shiftKey) {
    ctx.save();
    PICanvasEngine.configureContext(ctx);
    ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    const fg = document.getElementById('fg-color-input').value;
    const bg = document.getElementById('bg-color-input').value;
    let x = Math.min(x1, x2), y = Math.min(y1, y2);
    let w = Math.abs(x2 - x1), h = Math.abs(y2 - y1);
    if (shiftKey && (this.shapeType === 'rect' || this.shapeType === 'ellipse')) { const s = Math.min(w, h); w = s; h = s; }
    if (this.shapeType === 'rect') {
      this.roundRect(ctx, x, y, w, h, this.cornerRadius);
      if (this.fillEnabled) { ctx.fillStyle = bg; ctx.fill(); }
      if (this.strokeEnabled) { ctx.strokeStyle = fg; ctx.lineWidth = this.strokeWidth; ctx.stroke(); }
    } else if (this.shapeType === 'ellipse') {
      ctx.beginPath(); ctx.ellipse(x + w / 2, y + h / 2, w / 2, h / 2, 0, 0, Math.PI * 2);
      if (this.fillEnabled) { ctx.fillStyle = bg; ctx.fill(); }
      if (this.strokeEnabled) { ctx.strokeStyle = fg; ctx.lineWidth = this.strokeWidth; ctx.stroke(); }
    } else {
      ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2);
      ctx.strokeStyle = fg; ctx.lineWidth = this.strokeWidth; ctx.stroke();
      if (this.shapeType === 'arrow') {
        const angle = Math.atan2(y2 - y1, x2 - x1);
        const headLen = 12 + this.strokeWidth * 2;
        ctx.beginPath();
        ctx.moveTo(x2, y2); ctx.lineTo(x2 - headLen * Math.cos(angle - Math.PI / 6), y2 - headLen * Math.sin(angle - Math.PI / 6));
        ctx.moveTo(x2, y2); ctx.lineTo(x2 - headLen * Math.cos(angle + Math.PI / 6), y2 - headLen * Math.sin(angle + Math.PI / 6));
        ctx.stroke();
      }
    }
    ctx.restore();
  }

  roundRect(ctx, x, y, w, h, r) {
    const rr = Math.max(0, Math.min(r || 0, Math.min(w, h) / 2));
    ctx.beginPath();
    ctx.moveTo(x + rr, y);
    ctx.arcTo(x + w, y, x + w, y + h, rr);
    ctx.arcTo(x + w, y + h, x, y + h, rr);
    ctx.arcTo(x, y + h, x, y, rr);
    ctx.arcTo(x, y, x + w, y, rr);
    ctx.closePath();
  }

  // --- 既存図形の再編集 ---
  editActiveShape(mutator, label) {
    const layer = PILayerManager.getActive();
    if (!layer || layer.type !== 'shape') return;
    mutator(layer.shapeData);
    PILayerManager.renderShapeLayer(layer);
    PILayerManager.requestRender();
    if (label) PIHistoryManager.pushDebounced(label);
    PIEventBus.emit('tool:properties-changed');
  }

  setShapeTypeFor(layer, type) {
    const sd = layer.shapeData;
    const wasLine = sd.shapeType === 'line' || sd.shapeType === 'arrow';
    const toLine = type === 'line' || type === 'arrow';
    if (wasLine && !toLine) {
      // 線 → 矩形/楕円: 端点の対角を枠に
      sd.w = Math.max(2, Math.abs(sd.x2 - sd.x1));
      sd.h = Math.max(2, Math.abs(sd.y2 - sd.y1));
    } else if (!wasLine && toLine) {
      // 矩形/楕円 → 線: 枠の対角を端点に
      sd.x1 = 0; sd.y1 = 0; sd.x2 = sd.w; sd.y2 = sd.h;
      sd.strokeEnabled = true;
    }
    sd.shapeType = type;
  }

  getProperties() {
    const self = this;
    const active = PILayerManager.getActive();
    if (active && active.type === 'shape') {
      const sd = active.shapeData;
      const isLine = sd.shapeType === 'line' || sd.shapeType === 'arrow';
      const fields = [
        {
          type: 'button-group', label: '形状', key: 'stype',
          buttons: ['rect', 'ellipse', 'line', 'arrow'].map(t => ({
            label: { rect: '矩形', ellipse: '楕円', line: '線', arrow: '矢印' }[t],
            active: sd.shapeType === t,
            onClick: () => self.editActiveShape(d => self.setShapeTypeFor(active, t), '図形の形状変更')
          }))
        }
      ];
      if (!isLine) {
        fields.push({ type: 'checkbox', label: '塗り', key: 'fillEnabled', value: sd.fillEnabled, onChange: (v) => self.editActiveShape(d => d.fillEnabled = v, '図形の塗り') });
        fields.push({ type: 'color', label: '塗り色', key: 'fillColor', value: sd.fillColor, onChange: (v) => self.editActiveShape(d => { d.fillColor = v; d.fillEnabled = true; }, '図形の塗り色') });
      }
      fields.push({ type: 'checkbox', label: '枠線', key: 'strokeEnabled', value: sd.strokeEnabled, onChange: (v) => self.editActiveShape(d => d.strokeEnabled = v, '図形の枠線') });
      fields.push({ type: 'color', label: '枠色', key: 'strokeColor', value: sd.strokeColor, onChange: (v) => self.editActiveShape(d => { d.strokeColor = v; d.strokeEnabled = true; }, '図形の枠色') });
      fields.push({ type: 'slider', label: '線幅', key: 'strokeWidth', value: sd.strokeWidth, min: 1, max: 40, onChange: (v) => self.editActiveShape(d => d.strokeWidth = parseInt(v), '図形の線幅') });
      if (sd.shapeType === 'rect') {
        fields.push({ type: 'slider', label: '角丸', key: 'cornerRadius', value: sd.cornerRadius || 0, min: 0, max: 200, onChange: (v) => self.editActiveShape(d => d.cornerRadius = parseInt(v), '図形の角丸') });
      }
      return { title: '図形（編集中）', fields };
    }

    // 新規作成のデフォルト設定
    return {
      title: '図形（新規）',
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
        { type: 'slider', label: '線幅', key: 'strokeWidth', value: self.strokeWidth, min: 1, max: 40, onChange: (v) => { self.strokeWidth = parseInt(v); } },
        { type: 'slider', label: '角丸', key: 'cornerRadius', value: self.cornerRadius, min: 0, max: 200, onChange: (v) => { self.cornerRadius = parseInt(v); } },
        { type: 'checkbox', label: '塗り（背景色）', key: 'fill', value: self.fillEnabled, onChange: (v) => { self.fillEnabled = v; } },
        { type: 'checkbox', label: '枠線（前景色）', key: 'stroke', value: self.strokeEnabled, onChange: (v) => { self.strokeEnabled = v; } }
      ]
    };
  }
})();
