/* PowerImager — CloneStampTool: コピースタンプ
 * Alt+クリックで複製元を指定し、ドラッグでその内容を別位置へ複製する（写真レタッチの定番）。
 */
window.PICloneStampTool = new (class extends PIToolBase {
  constructor() {
    super('clone', 'none');
    this.size = 40;
    this.opacity = 1;
    this.hardness = 60;
    this.source = null;     // {x,y} キャンバス座標
    this.offset = null;     // 複製元→描画先のオフセット（キャンバス＝レイヤーローカル差分）
    this.painting = false;
    this.layerRef = null;
    this.snapshot = null;
    this.lastX = 0; this.lastY = 0;
  }

  getCursorRadius() { return this.size / 2; }

  onMouseDown(e) {
    if (e.altKey) {
      this.source = { x: e.canvasX, y: e.canvasY };
      this.offset = null;
      PIEventBus.emit('toast', '複製元を設定しました');
      return;
    }
    if (!this.source) { PIEventBus.emit('toast', 'Alt+クリックで複製元を指定してください'); return; }
    let layer = PILayerManager.getActive();
    if (!layer) return;
    if (layer.locked) { this.warnLocked(); return; }
    layer = PILayerManager.ensurePaintable(layer);
    if (!layer) return;
    this.layerRef = layer;
    // ストローク開始時のスナップショット（複製の再帰的にじみを防ぐ）
    this.snapshot = document.createElement('canvas');
    this.snapshot.width = layer.canvas.width; this.snapshot.height = layer.canvas.height;
    this.snapshot.getContext('2d').drawImage(layer.canvas, 0, 0);
    if (!this.offset) this.offset = { x: e.canvasX - this.source.x, y: e.canvasY - this.source.y };
    this.painting = true;
    this.lastX = e.canvasX; this.lastY = e.canvasY;
    this.stamp(e.canvasX, e.canvasY);
  }

  onMouseMove(e) {
    if (!this.painting) return;
    const dist = Math.hypot(e.canvasX - this.lastX, e.canvasY - this.lastY);
    const step = Math.max(1, this.size / 6);
    const steps = Math.max(1, Math.ceil(dist / step));
    for (let i = 1; i <= steps; i++) {
      const t = i / steps;
      this.stamp(this.lastX + (e.canvasX - this.lastX) * t, this.lastY + (e.canvasY - this.lastY) * t);
    }
    this.lastX = e.canvasX; this.lastY = e.canvasY;
  }

  onMouseUp() {
    if (!this.painting) return;
    this.painting = false;
    this.snapshot = null;
    PIHistoryManager.push('コピースタンプ');
    PILayerManager.requestRender();
  }

  stamp(destX, destY) {
    const layer = this.layerRef;
    if (!layer || !this.snapshot) return;
    const r = this.size / 2;
    const destLX = destX - layer.x, destLY = destY - layer.y;
    const pad = 1;
    const d = Math.ceil(r * 2) + pad * 2;
    const originX = destLX - r - pad, originY = destLY - r - pad;
    const tmp = document.createElement('canvas'); tmp.width = d; tmp.height = d;
    const tctx = tmp.getContext('2d');
    PICanvasEngine.configureContext(tctx);
    // 複製元領域を tmp へ（destと同じ相対位置に来るよう offset ぶんずらす）
    tctx.drawImage(this.snapshot, -(originX - this.offset.x), -(originY - this.offset.y));
    // 円形（ソフト）マスク
    tctx.globalCompositeOperation = 'destination-in';
    const cx = destLX - originX, cy = destLY - originY;
    const inner = Math.max(0, r * (this.hardness / 100) - 0.5);
    const g = tctx.createRadialGradient(cx, cy, Math.min(inner, r - 0.01), cx, cy, r);
    g.addColorStop(0, 'rgba(0,0,0,1)'); g.addColorStop(1, 'rgba(0,0,0,0)');
    tctx.fillStyle = g;
    tctx.beginPath(); tctx.arc(cx, cy, r, 0, Math.PI * 2); tctx.fill();
    // 選択範囲があれば交差
    if (PISelection.has()) {
      const ll = PISelection.getLayerLocalMask(layer);
      tctx.globalCompositeOperation = 'destination-in';
      tctx.drawImage(ll, -originX, -originY);
    }
    const ctx = layer.ctx;
    ctx.save();
    PICanvasEngine.configureContext(ctx);
    ctx.globalAlpha = this.opacity;
    ctx.drawImage(tmp, originX, originY);
    ctx.restore();
    PILayerManager.requestRender();
  }

  getProperties() {
    const self = this;
    return {
      title: 'コピースタンプ',
      fields: [
        { type: 'slider', label: 'サイズ', key: 'size', value: self.size, min: 2, max: 300, onChange: (v) => { self.size = parseInt(v); PIEventBus.emit('brush:size-changed'); } },
        { type: 'slider', label: '不透明度', key: 'opacity', value: Math.round(self.opacity * 100), min: 1, max: 100, unit: '%', onChange: (v) => { self.opacity = parseInt(v) / 100; } },
        { type: 'slider', label: '硬さ', key: 'hardness', value: self.hardness, min: 1, max: 100, unit: '%', onChange: (v) => { self.hardness = parseInt(v); } },
        { type: 'button', label: '複製元をリセット', key: 'reset', onClick: () => { self.source = null; self.offset = null; PIEventBus.emit('toast', 'Alt+クリックで複製元を指定'); } }
      ]
    };
  }
})();
