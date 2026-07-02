/* PowerImager — EraserTool: 消しゴム */
window.PIEraserTool = new (class extends PIToolBase {
  constructor() {
    super('eraser', 'none');
    this.erasing = false;
    this.lastX = 0; this.lastY = 0;
    this.eraserSize = 20;
    this.maskStroke = false; this.maskLayer = null;
    this.backup = null;
  }
  getCursorRadius() { return this.eraserSize / 2; }
  onMouseDown(e) {
    let layer = PILayerManager.getActive();
    if (!layer) return;
    if (layer.locked) { this.warnLocked(); return; }
    // マスク編集中はマスクへ直接描画（消しゴム＝戻す/表示）
    if (PILayerManager.isMaskEditing(layer)) {
      this.maskStroke = true; this.maskLayer = layer;
      const lp = PILayerTransform.canvasToLocal(layer, e.canvasX, e.canvasY);
      this.lastX = lp.x; this.lastY = lp.y;
      this.paintMask(layer, lp.x, lp.y, lp.x, lp.y);
      PILayerManager.requestRender();
      return;
    }
    layer = PILayerManager.ensurePaintable(layer);
    if (!layer) return;
    this.erasing = true;
    // 選択範囲がある場合は範囲外を後で戻すためのバックアップを取る
    if (PISelection.has()) {
      this.backup = document.createElement('canvas');
      this.backup.width = layer.canvas.width; this.backup.height = layer.canvas.height;
      this.backup.getContext('2d').drawImage(layer.canvas, 0, 0);
    } else { this.backup = null; }
    this.lastX = e.canvasX - layer.x;
    this.lastY = e.canvasY - layer.y;
    this.erase(layer, this.lastX, this.lastY, this.lastX, this.lastY);
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
    if (!this.erasing) return;
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const x = e.canvasX - layer.x;
    const y = e.canvasY - layer.y;
    this.erase(layer, this.lastX, this.lastY, x, y);
    this.lastX = x; this.lastY = y;
  }
  onMouseUp(e) {
    if (this.maskStroke) {
      this.maskStroke = false; this.maskLayer = null;
      PIHistoryManager.push('マスク復元');
      PILayerManager.requestRender();
      return;
    }
    if (this.erasing) {
      this.erasing = false;
      // 選択範囲外を元に戻す（選択内のみ消える）
      if (this.backup) { PISelection.confineChangesToSelection(PILayerManager.getActive(), this.backup); this.backup = null; }
      PIHistoryManager.push('消しゴム');
      PILayerManager.requestRender();
    }
  }
  // マスクへの描画（消しゴム＝戻す: 不透明白を source-over）
  paintMask(layer, x1, y1, x2, y2) {
    if (!layer.mask) return;
    const ctx = layer.mask.getContext('2d');
    ctx.save();
    PICanvasEngine.configureContext(ctx);
    ctx.globalCompositeOperation = 'source-over';
    ctx.strokeStyle = '#ffffff';
    ctx.lineWidth = this.eraserSize;
    ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
    ctx.restore();
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
        { type: 'slider', label: 'サイズ', key: 'size', value: self.eraserSize, min: 1, max: 300, onChange: (v) => { self.eraserSize = parseInt(v); PIEventBus.emit('brush:size-changed'); } }
      ]
    };
  }
})();
