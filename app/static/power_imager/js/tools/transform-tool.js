/* PowerImager — TransformTool: 自由変形（移動・拡縮・回転）
 * アクティブレイヤーにバウンディングボックスとハンドルを表示し、
 *  - 枠内ドラッグ: 移動
 *  - 角/辺ハンドル: 拡縮（Shiftで縦横比固定）
 *  - 上の丸ハンドル: 回転（Shiftで15°スナップ）
 * 確定（Enter/ツール切替）時、ラスターはピクセルへベイク、図形/テキストは
 * 拡縮を形状・文字サイズへ畳み込み（鮮明）・回転は非破壊で保持。
 */
window.PITransformTool = new (class extends PIToolBase {
  constructor() {
    super('transform', 'default');
    this.mode = null;       // 'move' | 'scale' | 'rotate'
    this.handle = null;
    this.start = null;
  }

  activate() {
    super.activate();
    this.redraw();
    if (!this._bound) {
      this._bound = true;
      PIEventBus.on('layers:changed', () => { if (PIToolbar.getCurrentTool() === this) this.redraw(); });
      PIEventBus.on('canvas:zoom-changed', () => { if (PIToolbar.getCurrentTool() === this) this.redraw(); });
    }
  }

  deactivate() {
    super.deactivate();
    this.commit();
    PICanvasEngine.clearOverlay();
  }

  effSize(layer) {
    return { ew: layer.canvas.width * (layer.scaleX || 1), eh: layer.canvas.height * (layer.scaleY || 1) };
  }

  handlePositions(layer) {
    const { ew, eh } = this.effSize(layer);
    const rot = layer.rotation || 0;
    const c = PILayerTransform.center(layer);
    const zoom = PICanvasEngine.getZoom();
    const rotOff = 26 / zoom;
    const local = {
      nw: [-ew / 2, -eh / 2], n: [0, -eh / 2], ne: [ew / 2, -eh / 2],
      w: [-ew / 2, 0], e: [ew / 2, 0],
      sw: [-ew / 2, eh / 2], s: [0, eh / 2], se: [ew / 2, eh / 2],
      rot: [0, -eh / 2 - rotOff]
    };
    const cos = Math.cos(rot), sin = Math.sin(rot);
    const out = {};
    for (const k in local) {
      const [lx, ly] = local[k];
      out[k] = { x: c.cx + lx * cos - ly * sin, y: c.cy + lx * sin + ly * cos };
    }
    return out;
  }

  redraw() {
    const layer = PILayerManager.getActive();
    const ctx = PICanvasEngine.getOverlayCtx();
    PICanvasEngine.clearOverlay();
    if (!layer) return;
    const zoom = PICanvasEngine.getZoom();
    const hs = 5 / zoom;
    const H = this.handlePositions(layer);
    ctx.save();
    PICanvasEngine.configureContext(ctx);
    // 枠線
    ctx.strokeStyle = '#7c6ff7';
    ctx.lineWidth = 1.5 / zoom;
    ctx.beginPath();
    ctx.moveTo(H.nw.x, H.nw.y); ctx.lineTo(H.ne.x, H.ne.y);
    ctx.lineTo(H.se.x, H.se.y); ctx.lineTo(H.sw.x, H.sw.y);
    ctx.closePath(); ctx.stroke();
    // 回転ハンドルへの脚
    ctx.beginPath(); ctx.moveTo(H.n.x, H.n.y); ctx.lineTo(H.rot.x, H.rot.y); ctx.stroke();
    // 拡縮ハンドル（四角）
    ctx.fillStyle = '#ffffff'; ctx.strokeStyle = '#7c6ff7';
    ['nw', 'n', 'ne', 'w', 'e', 'sw', 's', 'se'].forEach(k => {
      ctx.beginPath();
      ctx.rect(H[k].x - hs, H[k].y - hs, hs * 2, hs * 2);
      ctx.fill(); ctx.stroke();
    });
    // 回転ハンドル（丸）
    ctx.beginPath(); ctx.arc(H.rot.x, H.rot.y, hs * 1.2, 0, Math.PI * 2);
    ctx.fillStyle = '#7c6ff7'; ctx.fill();
    ctx.restore();
  }

  hitHandle(layer, px, py) {
    const zoom = PICanvasEngine.getZoom();
    const r = 9 / zoom;
    const H = this.handlePositions(layer);
    if (Math.hypot(px - H.rot.x, py - H.rot.y) <= r) return 'rot';
    for (const k of ['nw', 'ne', 'sw', 'se', 'n', 's', 'w', 'e']) {
      if (Math.hypot(px - H[k].x, py - H[k].y) <= r) return k;
    }
    return null;
  }

  insideBox(layer, px, py) {
    const lp = PILayerTransform.canvasToLocal(layer, px, py);
    return lp.x >= 0 && lp.y >= 0 && lp.x <= layer.canvas.width && lp.y <= layer.canvas.height;
  }

  onMouseDown(e) {
    let layer = PILayerManager.getActive();
    // 別レイヤー上をクリックしたら、そのレイヤーを掴む
    if (!layer || (!this.hitHandle(layer, e.canvasX, e.canvasY) && !this.insideBox(layer, e.canvasX, e.canvasY))) {
      const picked = this.pickLayerAt(e.canvasX, e.canvasY);
      if (picked) { PILayerManager.setActiveIndex(PILayerManager.getAll().indexOf(picked)); layer = picked; }
    }
    if (!layer || layer.locked) return;

    const handle = this.hitHandle(layer, e.canvasX, e.canvasY);
    const c = PILayerTransform.center(layer);
    const base = {
      x: layer.x, y: layer.y, rotation: layer.rotation || 0,
      scaleX: layer.scaleX || 1, scaleY: layer.scaleY || 1,
      cx: c.cx, cy: c.cy, mx: e.canvasX, my: e.canvasY,
      ew: layer.canvas.width * (layer.scaleX || 1), eh: layer.canvas.height * (layer.scaleY || 1),
      cw: layer.canvas.width, ch: layer.canvas.height
    };
    if (handle === 'rot') {
      this.mode = 'rotate';
      base.startAngle = Math.atan2(e.canvasY - c.cy, e.canvasX - c.cx);
      base.startRotation = layer.rotation || 0;
    } else if (handle) {
      this.mode = 'scale';
      this.handle = handle;
    } else if (this.insideBox(layer, e.canvasX, e.canvasY)) {
      this.mode = 'move';
    } else {
      this.mode = null;
      return;
    }
    this.start = base;
  }

  onMouseMove(e) {
    if (!this.mode) {
      // ホバーでカーソル変更
      const layer = PILayerManager.getActive();
      const vp = PICanvasEngine.getViewport();
      if (layer && vp) {
        const h = this.hitHandle(layer, e.canvasX, e.canvasY);
        vp.style.cursor = h === 'rot' ? 'grab' : h ? 'nwse-resize' : (this.insideBox(layer, e.canvasX, e.canvasY) ? 'move' : 'default');
      }
      return;
    }
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const s = this.start;
    if (this.mode === 'move') {
      layer.x = s.x + (e.canvasX - s.mx);
      layer.y = s.y + (e.canvasY - s.my);
    } else if (this.mode === 'rotate') {
      let ang = Math.atan2(e.canvasY - s.cy, e.canvasX - s.cx) - s.startAngle + s.startRotation;
      if (e.shiftKey) { const step = Math.PI / 12; ang = Math.round(ang / step) * step; }
      layer.rotation = ang;
    } else if (this.mode === 'scale') {
      this.applyScale(layer, e);
    }
    PILayerManager.requestRender();
    this.redraw();
  }

  applyScale(layer, e) {
    const s = this.start;
    const sign = { nw: [-1, -1], n: [0, -1], ne: [1, -1], w: [-1, 0], e: [1, 0], sw: [-1, 1], s: [0, 1], se: [1, 1] }[this.handle];
    // マウスを「回転前のボックス座標（中心基準）」へ変換
    const cos = Math.cos(-s.rotation), sin = Math.sin(-s.rotation);
    const dx = e.canvasX - s.cx, dy = e.canvasY - s.cy;
    const mx = dx * cos - dy * sin;
    const my = dx * sin + dy * cos;
    let ew = s.ew, eh = s.eh, clx = 0, cly = 0;
    const MIN = 4;
    if (sign[0] !== 0) {
      const anchorX = -sign[0] * s.ew / 2;
      ew = Math.max(MIN, Math.abs(mx - anchorX));
      clx = anchorX + sign[0] * ew / 2;
    }
    if (sign[1] !== 0) {
      const anchorY = -sign[1] * s.eh / 2;
      eh = Math.max(MIN, Math.abs(my - anchorY));
      cly = anchorY + sign[1] * eh / 2;
    }
    if (e.shiftKey && sign[0] !== 0 && sign[1] !== 0) {
      const f = Math.max(ew / s.ew, eh / s.eh);
      ew = s.ew * f; eh = s.eh * f;
      clx = -sign[0] * s.ew / 2 + sign[0] * ew / 2;
      cly = -sign[1] * s.eh / 2 + sign[1] * eh / 2;
    }
    // 新中心（回転を戻してキャンバス座標へ）
    const c2 = Math.cos(s.rotation), s2 = Math.sin(s.rotation);
    const ncx = s.cx + clx * c2 - cly * s2;
    const ncy = s.cy + clx * s2 + cly * c2;
    layer.scaleX = ew / s.cw;
    layer.scaleY = eh / s.ch;
    layer.x = ncx - s.cw / 2;
    layer.y = ncy - s.ch / 2;
  }

  onMouseUp() {
    if (!this.mode) return;
    const moved = this.mode;
    this.mode = null; this.handle = null; this.start = null;
    PIHistoryManager.push('変形');
    this.redraw();
  }

  pickLayerAt(px, py) {
    const layers = PILayerManager.getAll();
    for (let i = layers.length - 1; i >= 0; i--) {
      const l = layers[i];
      if (l.visible && !l.locked && PILayerTransform.hitTest(l, px, py)) return l;
    }
    return null;
  }

  // 確定: ラスターはベイク、図形/テキストは拡縮を畳み込み回転は保持
  commit() {
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const sx = layer.scaleX || 1, sy = layer.scaleY || 1;
    if (Math.abs(sx - 1) < 1e-4 && Math.abs(sy - 1) < 1e-4) return; // 拡縮なしなら畳み込み不要
    if (layer.type === 'shape' && layer.shapeData) this.foldScaleIntoShape(layer);
    else if (layer.type === 'text' && layer.textData) this.foldScaleIntoText(layer);
    else PILayerManager.bakeLayerToRaster(layer);
    PILayerManager.requestRender();
  }

  keepCenterReRender(layer, renderFn) {
    const cx = layer.x + layer.canvas.width / 2;
    const cy = layer.y + layer.canvas.height / 2;
    renderFn();
    layer.x = Math.round(cx - layer.canvas.width / 2);
    layer.y = Math.round(cy - layer.canvas.height / 2);
    layer.scaleX = 1; layer.scaleY = 1;
  }

  foldScaleIntoShape(layer) {
    const sx = layer.scaleX || 1, sy = layer.scaleY || 1;
    const avg = (Math.abs(sx) + Math.abs(sy)) / 2;
    const sd = layer.shapeData;
    sd.w = Math.max(1, sd.w * Math.abs(sx));
    sd.h = Math.max(1, sd.h * Math.abs(sy));
    if (sd.x1 !== undefined) { sd.x1 *= sx; sd.y1 *= sy; sd.x2 *= sx; sd.y2 *= sy; }
    sd.strokeWidth = Math.max(1, sd.strokeWidth * avg);
    if (sd.cornerRadius) sd.cornerRadius *= avg;
    this.keepCenterReRender(layer, () => PILayerManager.renderShapeLayer(layer));
  }

  foldScaleIntoText(layer) {
    const sx = layer.scaleX || 1, sy = layer.scaleY || 1;
    const avg = (Math.abs(sx) + Math.abs(sy)) / 2;
    const td = layer.textData;
    td.size = Math.max(4, Math.round(td.size * avg));
    if (td.strokeWidth) td.strokeWidth *= avg;
    if (td.shadowBlur) td.shadowBlur *= avg;
    this.keepCenterReRender(layer, () => PILayerManager.renderTextLayer(layer));
  }

  getProperties() {
    const self = this;
    const layer = PILayerManager.getActive();
    const rotDeg = layer ? Math.round((layer.rotation || 0) * 180 / Math.PI) : 0;
    return {
      title: '変形',
      fields: [
        { type: 'slider', label: '角度', key: 'angle', value: ((rotDeg % 360) + 360) % 360, min: 0, max: 360, unit: '°', onChange: (v) => { const l = PILayerManager.getActive(); if (l) { l.rotation = parseInt(v) * Math.PI / 180; PILayerManager.requestRender(); self.redraw(); } } },
        { type: 'button', label: '90°右回転', key: 'r90', onClick: () => { const l = PILayerManager.getActive(); if (l) { l.rotation = (l.rotation || 0) + Math.PI / 2; PILayerManager.requestRender(); self.redraw(); PIHistoryManager.push('回転'); } } },
        { type: 'button', label: '変形を確定（ベイク）', key: 'apply', onClick: () => { self.commit(); PIHistoryManager.push('変形確定'); self.redraw(); } },
        { type: 'button', label: '回転リセット', key: 'reset', onClick: () => { const l = PILayerManager.getActive(); if (l) { l.rotation = 0; PILayerManager.requestRender(); self.redraw(); } } }
      ]
    };
  }
})();
