/* PowerImager — SelectTool: 選択範囲（ビットマップマスク方式）
 * 選択をキャンバスサイズのマスク（α＝選択度）で持つことで、矩形/楕円に加えて
 * 投げ縄・自動選択・追加/減算・ふちぼかし・反転・マーキー移動・新規レイヤーへコピー
 * に対応する。既存の調整/フィルタは PISelection の同名APIをそのまま使える。
 */
window.PISelectTool = new (class extends PIToolBase {
  constructor() {
    super('select', 'crosshair');
    this.shape = 'rect';     // rect | ellipse | lasso | wand
    this.op = 'replace';     // replace | add | subtract
    this.feather = 0;
    this.tolerance = 32;
    this.dragging = false;
    this.startX = 0; this.startY = 0;
    this.curX = 0; this.curY = 0;
    this.lassoPts = null;
    this.movingMarquee = false;
    this.moveStartX = 0; this.moveStartY = 0;
    this.marchOffset = 0;
    this.marchInterval = null;
  }

  activate() { super.activate(); this.startMarchingAnts(); this.redraw(); }
  deactivate() { super.deactivate(); this.stopMarchingAnts(); PICanvasEngine.clearOverlay(); }

  onMouseDown(e) {
    // 既存選択の内側を（追加/減算でなく）掴んだらマーキー移動
    if (this.op === 'replace' && PISelection.has() && PISelection.coverageAt(e.canvasX, e.canvasY) >= 128 && (this.shape === 'rect' || this.shape === 'ellipse' || this.shape === 'lasso' || this.shape === 'wand')) {
      this.movingMarquee = true;
      this.moveStartX = e.canvasX; this.moveStartY = e.canvasY;
      return;
    }
    if (this.shape === 'wand') {
      const layer = PILayerManager.getActive();
      PISelection.magicWand(layer, Math.round(e.canvasX), Math.round(e.canvasY), this.tolerance, this.op, this.feather);
      this.redraw();
      return;
    }
    this.dragging = true;
    this.startX = e.canvasX; this.startY = e.canvasY;
    this.curX = e.canvasX; this.curY = e.canvasY;
    if (this.shape === 'lasso') this.lassoPts = [[e.canvasX, e.canvasY]];
  }

  onMouseMove(e) {
    if (this.movingMarquee) {
      const dx = e.canvasX - this.moveStartX, dy = e.canvasY - this.moveStartY;
      this.moveStartX = e.canvasX; this.moveStartY = e.canvasY;
      PISelection.moveBy(dx, dy);
      this.redraw();
      return;
    }
    if (!this.dragging) return;
    this.curX = e.canvasX; this.curY = e.canvasY;
    if (this.shape === 'lasso') this.lassoPts.push([e.canvasX, e.canvasY]);
    this.drawPreview();
  }

  onMouseUp(e) {
    if (this.movingMarquee) { this.movingMarquee = false; PIEventBus.emit('selection:changed', true); return; }
    if (!this.dragging) return;
    this.dragging = false;
    if (this.shape === 'lasso') {
      if (this.lassoPts && this.lassoPts.length >= 3) PISelection.applyPolygon(this.lassoPts, this.op, this.feather);
      this.lassoPts = null;
      this.redraw();
      return;
    }
    const x = Math.min(this.startX, e.canvasX), y = Math.min(this.startY, e.canvasY);
    const w = Math.abs(e.canvasX - this.startX), h = Math.abs(e.canvasY - this.startY);
    if (w > 2 && h > 2) PISelection.applyRectEllipse(this.shape, x, y, w, h, this.op, this.feather);
    else if (this.op === 'replace') PISelection.clear();
    this.redraw();
  }

  // ライブプレビュー（確定前の形）を破線で描く
  drawPreview() {
    const ctx = PICanvasEngine.getOverlayCtx();
    PICanvasEngine.clearOverlay();
    this.paintSelectionTint(ctx);
    // 破線・線幅はズーム後の画面上で一定に見えるよう補正する
    const z = PICanvasEngine.getZoom() || 1;
    ctx.save();
    ctx.setLineDash([5 / z, 3 / z]); ctx.lineDashOffset = -this.marchOffset / z;
    ctx.strokeStyle = '#fff'; ctx.lineWidth = 1 / z;
    if (this.shape === 'lasso' && this.lassoPts) {
      ctx.beginPath();
      this.lassoPts.forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]));
      ctx.stroke();
    } else {
      const x = Math.min(this.startX, this.curX), y = Math.min(this.startY, this.curY);
      const w = Math.abs(this.curX - this.startX), h = Math.abs(this.curY - this.startY);
      if (this.shape === 'ellipse') { ctx.beginPath(); ctx.ellipse(x + w / 2, y + h / 2, w / 2, h / 2, 0, 0, Math.PI * 2); ctx.stroke(); }
      else ctx.strokeRect(x, y, w, h);
    }
    ctx.restore();
  }

  paintSelectionTint(ctx) {
    const m = PISelection.getMaskCanvas();
    if (!m) return;
    ctx.save();
    ctx.globalAlpha = 0.16;
    ctx.globalCompositeOperation = 'source-over';
    // accent色で選択領域を淡く塗る（マスクのαをそのまま使う）
    const tint = document.createElement('canvas'); tint.width = m.width; tint.height = m.height;
    const tctx = tint.getContext('2d');
    tctx.fillStyle = '#7c6ff7'; tctx.fillRect(0, 0, m.width, m.height);
    tctx.globalCompositeOperation = 'destination-in'; tctx.drawImage(m, 0, 0);
    ctx.drawImage(tint, 0, 0);
    ctx.restore();
  }

  redraw() {
    const ctx = PICanvasEngine.getOverlayCtx();
    PICanvasEngine.clearOverlay();
    if (!PISelection.has()) return;
    this.paintSelectionTint(ctx);
    const bb = PISelection.bbox();
    if (!bb) return;
    const z = PICanvasEngine.getZoom() || 1;
    ctx.save();
    ctx.setLineDash([6 / z, 4 / z]); ctx.lineDashOffset = -this.marchOffset / z;
    ctx.strokeStyle = '#fff'; ctx.lineWidth = 1 / z; ctx.strokeRect(bb.x, bb.y, bb.w, bb.h);
    ctx.strokeStyle = '#000'; ctx.lineDashOffset = -(this.marchOffset + 6) / z; ctx.strokeRect(bb.x, bb.y, bb.w, bb.h);
    ctx.restore();
  }

  startMarchingAnts() {
    this.marchInterval = setInterval(() => {
      this.marchOffset = (this.marchOffset + 1) % 20;
      if (PISelection.has() && !this.dragging) this.redraw();
      else if (this.dragging) this.drawPreview();
    }, 90);
  }
  stopMarchingAnts() { clearInterval(this.marchInterval); }

  clearSelection() { PISelection.clear(); PICanvasEngine.clearOverlay(); }
  selectAll() { PISelection.selectAll(); this.redraw(); }

  getProperties() {
    const self = this;
    const shapeBtn = (key, label) => ({ label, active: self.shape === key, onClick: () => { self.shape = key; PIEventBus.emit('tool:properties-changed'); } });
    const opBtn = (key, label) => ({ label, active: self.op === key, onClick: () => { self.op = key; PIEventBus.emit('tool:properties-changed'); } });
    const fields = [
      { type: 'button-group', label: '形状', key: 'shape', buttons: [shapeBtn('rect', '矩形'), shapeBtn('ellipse', '楕円'), shapeBtn('lasso', '投げ縄'), shapeBtn('wand', '自動')] },
      { type: 'button-group', label: 'モード', key: 'op', buttons: [opBtn('replace', '新規'), opBtn('add', '追加'), opBtn('subtract', '減算')] },
      { type: 'slider', label: 'ぼかし', key: 'feather', value: self.feather, min: 0, max: 50, unit: 'px', onChange: (v) => { self.feather = parseInt(v); } }
    ];
    if (self.shape === 'wand') fields.push({ type: 'slider', label: '許容値', key: 'tol', value: self.tolerance, min: 0, max: 255, onChange: (v) => { self.tolerance = parseInt(v); } });
    fields.push({ type: 'button', label: '全選択 (Ctrl+A)', key: 'all', onClick: () => self.selectAll() });
    fields.push({ type: 'button', label: '選択を反転', key: 'inv', onClick: () => { PISelection.invert(); self.redraw(); } });
    fields.push({ type: 'button', label: '新規レイヤーへコピー', key: 'copy', onClick: () => { PISelection.copyToNewLayer(); } });
    fields.push({ type: 'button', label: '選択解除 (Ctrl+D)', key: 'clear', onClick: () => self.clearSelection() });
    return { title: '選択', fields };
  }
})();

/* ===== PISelection: 選択マスクの単一管理 ===== */
window.PISelection = (function () {
  let mask = null;            // キャンバスサイズ。α＝選択度
  let cache = null, cacheW = 0, cacheH = 0;
  let bboxCache; // undefined=未計算

  function csize() { return PICanvasEngine.getCanvasSize(); }

  function newMask() {
    const s = csize();
    const c = document.createElement('canvas');
    c.width = s.width; c.height = s.height;
    return c;
  }

  function invalidate() { cache = null; bboxCache = undefined; }

  function emptyCheck() {
    // マスクに選択画素が無ければ null 化（has()=false に）
    if (!mask) return;
    const d = ctxData();
    for (let i = 3; i < d.length; i += 4) { if (d[i] > 4) return; }
    mask = null; cache = null;
  }

  function ctxData() {
    if (!cache) { cache = mask.getContext('2d').getImageData(0, 0, mask.width, mask.height).data; cacheW = mask.width; cacheH = mask.height; }
    return cache;
  }

  function coverageAt(cx, cy) {
    if (!mask) return 255;
    const d = ctxData();
    cx = Math.floor(cx); cy = Math.floor(cy);
    if (cx < 0 || cy < 0 || cx >= cacheW || cy >= cacheH) return 0;
    return d[(cy * cacheW + cx) * 4 + 3];
  }

  function has() { return !!mask; }
  function getMaskCanvas() { return mask; }

  function bbox() {
    if (!mask) return null;
    if (bboxCache !== undefined) return bboxCache;
    const d = ctxData();
    let minX = cacheW, minY = cacheH, maxX = -1, maxY = -1;
    for (let y = 0; y < cacheH; y++) for (let x = 0; x < cacheW; x++) {
      if (d[(y * cacheW + x) * 4 + 3] > 4) { if (x < minX) minX = x; if (x > maxX) maxX = x; if (y < minY) minY = y; if (y > maxY) maxY = y; }
    }
    bboxCache = (maxX < minX) ? null : { x: minX, y: minY, w: maxX - minX + 1, h: maxY - minY + 1 };
    return bboxCache;
  }

  // ---- 選択の構築 ----
  function composite(drawFn, op, feather) {
    const next = newMask();
    const nctx = next.getContext('2d');
    nctx.fillStyle = '#fff';
    drawFn(nctx);
    if (feather > 0 && nctx.filter !== undefined) {
      const blurred = newMask();
      const bctx = blurred.getContext('2d');
      bctx.filter = 'blur(' + feather + 'px)';
      bctx.drawImage(next, 0, 0);
      next.getContext('2d').clearRect(0, 0, next.width, next.height);
      next.getContext('2d').drawImage(blurred, 0, 0);
    }
    if (!mask || op === 'replace') { mask = next; }
    else {
      const mctx = mask.getContext('2d');
      mctx.globalCompositeOperation = op === 'subtract' ? 'destination-out' : 'source-over';
      mctx.drawImage(next, 0, 0);
      mctx.globalCompositeOperation = 'source-over';
    }
    invalidate(); emptyCheck();
    PIEventBus.emit('selection:changed', has());
  }

  function applyRectEllipse(shape, x, y, w, h, op, feather) {
    composite((ctx) => {
      ctx.beginPath();
      if (shape === 'ellipse') ctx.ellipse(x + w / 2, y + h / 2, w / 2, h / 2, 0, 0, Math.PI * 2);
      else ctx.rect(x, y, w, h);
      ctx.fill();
    }, op, feather);
  }

  function applyPolygon(points, op, feather) {
    composite((ctx) => {
      ctx.beginPath();
      points.forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]));
      ctx.closePath(); ctx.fill();
    }, op, feather);
  }

  function magicWand(layer, sx, sy, tol, op, feather) {
    if (!layer) return;
    const lx = sx - layer.x, ly = sy - layer.y;
    const w = layer.canvas.width, h = layer.canvas.height;
    if (lx < 0 || ly < 0 || lx >= w || ly >= h) return;
    const img = layer.ctx.getImageData(0, 0, w, h).data;
    const idx0 = (ly * w + lx) * 4;
    const tr = img[idx0], tg = img[idx0 + 1], tb = img[idx0 + 2], ta = img[idx0 + 3];
    // フラッドフィル（許容値内）
    const visited = new Uint8Array(w * h);
    const stack = [[lx, ly]];
    const sel = new Uint8Array(w * h);
    while (stack.length) {
      const [x, y] = stack.pop();
      const pi = y * w + x;
      if (visited[pi]) continue; visited[pi] = 1;
      const i = pi * 4;
      if (Math.abs(img[i] - tr) > tol || Math.abs(img[i + 1] - tg) > tol || Math.abs(img[i + 2] - tb) > tol || Math.abs(img[i + 3] - ta) > tol) continue;
      sel[pi] = 1;
      if (x > 0) stack.push([x - 1, y]);
      if (x < w - 1) stack.push([x + 1, y]);
      if (y > 0) stack.push([x, y - 1]);
      if (y < h - 1) stack.push([x, y + 1]);
    }
    const lm = document.createElement('canvas'); lm.width = w; lm.height = h;
    const lmImg = lm.getContext('2d').createImageData(w, h);
    for (let p = 0; p < sel.length; p++) { if (sel[p]) { lmImg.data[p * 4 + 3] = 255; lmImg.data[p * 4] = lmImg.data[p * 4 + 1] = lmImg.data[p * 4 + 2] = 255; } }
    lm.getContext('2d').putImageData(lmImg, 0, 0);
    composite((ctx) => { ctx.drawImage(lm, layer.x, layer.y); }, op, feather);
  }

  function selectAll() {
    const s = csize();
    composite((ctx) => ctx.fillRect(0, 0, s.width, s.height), 'replace', 0);
  }

  function invert() {
    const s = csize();
    const inv = newMask();
    const ictx = inv.getContext('2d');
    ictx.fillStyle = '#fff'; ictx.fillRect(0, 0, s.width, s.height);
    if (mask) { ictx.globalCompositeOperation = 'destination-out'; ictx.drawImage(mask, 0, 0); }
    mask = inv; invalidate(); emptyCheck();
    PIEventBus.emit('selection:changed', has());
  }

  function moveBy(dx, dy) {
    if (!mask) return;
    const moved = newMask();
    moved.getContext('2d').drawImage(mask, Math.round(dx), Math.round(dy));
    mask = moved; invalidate(); emptyCheck();
    PIEventBus.emit('selection:changed', has());
  }

  function clear() { mask = null; cache = null; bboxCache = undefined; PIEventBus.emit('selection:changed', null); }

  // キャンバスサイズ変更時はマスクのサイズ不整合を避けるため選択を解除
  PIEventBus.on('canvas:resized', () => { mask = null; cache = null; bboxCache = undefined; });

  // ---- レイヤー連携（既存API互換） ----
  function getLayerLocalMask(layer) {
    if (!mask) return null;
    const c = document.createElement('canvas');
    c.width = layer.canvas.width; c.height = layer.canvas.height;
    c.getContext('2d').drawImage(mask, -layer.x, -layer.y);
    return c;
  }

  function getLayerBounds(layer) {
    if (!mask) return null;
    const bb = bbox(); if (!bb) return null;
    const left = Math.max(0, Math.floor(bb.x - layer.x));
    const top = Math.max(0, Math.floor(bb.y - layer.y));
    const right = Math.min(layer.canvas.width, Math.ceil(bb.x + bb.w - layer.x));
    const bottom = Math.min(layer.canvas.height, Math.ceil(bb.y + bb.h - layer.y));
    const w = right - left, h = bottom - top;
    if (w <= 0 || h <= 0) return null;
    return { x: left, y: top, w, h, layerX: layer.x, layerY: layer.y };
  }

  function contains(bounds, lx, ly) {
    if (!mask) return true;
    const ox = bounds ? bounds.layerX : 0;
    const oy = bounds ? bounds.layerY : 0;
    return coverageAt(lx + ox, ly + oy) >= 128;
  }

  function applyImageData(layer, processor) {
    const bounds = getLayerBounds(layer);
    if (!bounds) {
      if (has()) return;
      const imgData = layer.ctx.getImageData(0, 0, layer.canvas.width, layer.canvas.height);
      processor(imgData.data, 0, 0, layer.canvas.width, layer.canvas.height, null);
      layer.ctx.putImageData(imgData, 0, 0);
      return;
    }
    const imgData = layer.ctx.getImageData(bounds.x, bounds.y, bounds.w, bounds.h);
    processor(imgData.data, bounds.x, bounds.y, bounds.w, bounds.h, bounds);
    layer.ctx.putImageData(imgData, bounds.x, bounds.y);
  }

  // 選択範囲にだけ sourceCanvas（レイヤー全面サイズの加工結果）を反映
  function putCanvasEffect(layer, sourceCanvas) {
    const ctx = layer.ctx;
    if (!mask) {
      ctx.save(); PICanvasEngine.configureContext(ctx);
      ctx.clearRect(0, 0, layer.canvas.width, layer.canvas.height);
      ctx.drawImage(sourceCanvas, 0, 0); ctx.restore();
      return;
    }
    const llmask = getLayerLocalMask(layer);
    const tmp = document.createElement('canvas'); tmp.width = layer.canvas.width; tmp.height = layer.canvas.height;
    const tctx = tmp.getContext('2d');
    PICanvasEngine.configureContext(tctx);
    tctx.drawImage(sourceCanvas, 0, 0);
    tctx.globalCompositeOperation = 'destination-in'; tctx.drawImage(llmask, 0, 0);
    ctx.save(); PICanvasEngine.configureContext(ctx);
    ctx.globalCompositeOperation = 'destination-out'; ctx.drawImage(llmask, 0, 0);
    ctx.globalCompositeOperation = 'source-over'; ctx.drawImage(tmp, 0, 0);
    ctx.restore();
  }

  // 互換: 矩形/楕円の単純選択のみ ctx.clip() を張る。マスク方式では false を返し、
  // 呼び出し側はマスク合成（maskLayerLocal）にフォールバックする。
  function clipContextToSelection() { return false; }

  // 直接描画ツール（消しゴム等）向け: backup を使い、選択範囲外の変更を元に戻す
  function confineChangesToSelection(layer, backup) {
    if (!mask) return;
    const w = layer.canvas.width, h = layer.canvas.height;
    const outside = document.createElement('canvas'); outside.width = w; outside.height = h;
    const octx = outside.getContext('2d');
    octx.fillStyle = '#fff'; octx.fillRect(0, 0, w, h);
    octx.globalCompositeOperation = 'destination-out'; octx.drawImage(getLayerLocalMask(layer), 0, 0);
    const restore = document.createElement('canvas'); restore.width = w; restore.height = h;
    const rctx = restore.getContext('2d');
    rctx.drawImage(backup, 0, 0);
    rctx.globalCompositeOperation = 'destination-in'; rctx.drawImage(outside, 0, 0);
    const ctx = layer.ctx;
    ctx.save();
    ctx.globalCompositeOperation = 'destination-out'; ctx.drawImage(outside, 0, 0);
    ctx.globalCompositeOperation = 'source-over'; ctx.drawImage(restore, 0, 0);
    ctx.restore();
  }

  // 描画系が結果をマスクで切り抜くための補助
  function maskCanvasToSelection(targetCanvas, layer) {
    if (!mask) return;
    const llmask = getLayerLocalMask(layer);
    const tctx = targetCanvas.getContext('2d');
    tctx.save();
    tctx.globalCompositeOperation = 'destination-in';
    tctx.drawImage(llmask, 0, 0);
    tctx.restore();
  }

  // 選択ピクセルを新規レイヤーへコピー
  function copyToNewLayer() {
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const flat = PILayerManager.flattenAll();
    const out = document.createElement('canvas'); out.width = flat.width; out.height = flat.height;
    const octx = out.getContext('2d');
    octx.drawImage(flat, 0, 0);
    if (mask) { octx.globalCompositeOperation = 'destination-in'; octx.drawImage(mask, 0, 0); }
    const nl = PILayerManager.addLayer('選択コピー');
    nl.ctx.drawImage(out, 0, 0);
    PIHistoryManager.push('選択範囲をコピー');
    PILayerManager.requestRender();
    PIEventBus.emit('toast', '選択範囲を新規レイヤーへコピーしました');
  }

  return {
    has, getMaskCanvas, coverageAt, bbox,
    applyRectEllipse, applyPolygon, magicWand, selectAll, invert, moveBy, clear,
    getLayerLocalMask, getLayerBounds, contains, applyImageData, putCanvasEffect,
    clipContextToSelection, maskCanvasToSelection, confineChangesToSelection, copyToNewLayer
  };
})();
