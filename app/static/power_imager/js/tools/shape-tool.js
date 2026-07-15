/* PowerImager — ShapeTool: 図形をオブジェクトレイヤーとして作成・再編集する
 * 描いた瞬間に焼き付けず shapeData を持つレイヤーを作るので、後から
 * 移動・塗り/枠の変更・透明化・形状変更・角丸調整が何度でもできる。
 * 選択中の図形にはハンドルを表示し、ツール内で
 *  - 8方向ハンドル: サイズ変更（Shiftで縦横比固定・線幅は不変でシャープ）
 *  - 上の丸ハンドル: 回転（Shiftで15°スナップ）
 *  - 線/矢印: 端点ドラッグで変更（Shiftで45°スナップ）
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
    this.strokeStyle = 'solid';
    // 既存図形の移動用
    this.pendingLayer = null;
    this.movingLayer = false;
    this.dragStartX = 0; this.dragStartY = 0;
    this.layerStartX = 0; this.layerStartY = 0;
    this.dragThreshold = 3;
    this.altDup = false;
    this.dupDone = false;
    this.snapTargets = null;
    this.moveSnapRect = null;
    // ハンドル操作
    this.resizing = null;      // { layer, handle, w0, h0, snap }
    this.rotating = null;      // { layer, cx, cy, startAngle, startRotation }
    this.endpointDrag = null;  // { layer, which, fixed }
    this._bound = false;
  }

  activate() {
    super.activate();
    if (!this._bound) {
      this._bound = true;
      PIEventBus.on('layers:changed', () => { if (PIToolbar.getCurrentTool() === this) this.redrawHandles(); });
      PIEventBus.on('canvas:zoom-changed', () => { if (PIToolbar.getCurrentTool() === this) this.redrawHandles(); });
    }
    this.redrawHandles();
  }

  deactivate() {
    super.deactivate();
    this.drawing = false;
    this.pendingLayer = null;
    this.movingLayer = false;
    this.resizing = null;
    this.rotating = null;
    this.endpointDrag = null;
    PICanvasEngine.clearOverlay();
  }

  isLineType(sd) { return sd.shapeType === 'line' || sd.shapeType === 'arrow'; }

  activeShape() {
    const l = PILayerManager.getActive();
    return (l && l.type === 'shape' && l.shapeData && l.visible) ? l : null;
  }

  findShapeLayerAt(cx, cy) {
    const layers = PILayerManager.getAll();
    for (let i = layers.length - 1; i >= 0; i--) {
      const l = layers[i];
      if (l.type === 'shape' && l.visible && !l.locked && PILayerTransform.hitTest(l, cx, cy)) return l;
    }
    return null;
  }

  // ===== ジオメトリ（ハンドル配置用） =====

  // 見えているコンテンツのローカル矩形（線幅・矢印の頭を含む／ピクセル走査不要）
  contentRect(layer) {
    const sd = layer.shapeData;
    const PAD = PILayerManager.SHAPE_PAD;
    const isLine = this.isLineType(sd);
    const sw = (sd.strokeEnabled || isLine) ? (sd.strokeWidth || 0) : 0;
    let extra = sw / 2;
    if (sd.shapeType === 'arrow') extra += 12 + (sd.strokeWidth || 0) * 2;
    const bw = isLine ? Math.abs(sd.x2 - sd.x1) : sd.w;
    const bh = isLine ? Math.abs(sd.y2 - sd.y1) : sd.h;
    const x0 = Math.max(0, PAD - extra), y0 = Math.max(0, PAD - extra);
    const x1 = Math.min(layer.canvas.width, PAD + bw + extra);
    const y1 = Math.min(layer.canvas.height, PAD + bh + extra);
    return { x: x0, y: y0, w: Math.max(1, x1 - x0), h: Math.max(1, y1 - y0) };
  }

  _geom(layer) {
    const cr = this.contentRect(layer);
    const c = PILayerTransform.localToCanvas(layer, cr.x + cr.w / 2, cr.y + cr.h / 2);
    const rot = layer.rotation || 0;
    return { c, ew: cr.w * (layer.scaleX || 1), eh: cr.h * (layer.scaleY || 1), cos: Math.cos(rot), sin: Math.sin(rot) };
  }

  _pt(g, lx, ly) { return { x: g.c.x + lx * g.cos - ly * g.sin, y: g.c.y + lx * g.sin + ly * g.cos }; }

  handlePositions(layer) {
    const g = this._geom(layer);
    const z = PICanvasEngine.getZoom() || 1;
    const hw = g.ew / 2, hh = g.eh / 2;
    return {
      nw: this._pt(g, -hw, -hh), n: this._pt(g, 0, -hh), ne: this._pt(g, hw, -hh),
      w: this._pt(g, -hw, 0), e: this._pt(g, hw, 0),
      sw: this._pt(g, -hw, hh), s: this._pt(g, 0, hh), se: this._pt(g, hw, hh),
      rot: this._pt(g, 0, -hh - 26 / z)
    };
  }

  // 線/矢印の端点（キャンバス座標）
  endpointPositions(layer) {
    const sd = layer.shapeData;
    const PAD = PILayerManager.SHAPE_PAD;
    const minX = Math.min(sd.x1, sd.x2), minY = Math.min(sd.y1, sd.y2);
    return [
      PILayerTransform.localToCanvas(layer, PAD + sd.x1 - minX, PAD + sd.y1 - minY),
      PILayerTransform.localToCanvas(layer, PAD + sd.x2 - minX, PAD + sd.y2 - minY)
    ];
  }

  hitHandle(layer, x, y) {
    const z = PICanvasEngine.getZoom() || 1;
    const r = 9 / z;
    if (this.isLineType(layer.shapeData)) {
      const eps = this.endpointPositions(layer);
      if (Math.hypot(x - eps[0].x, y - eps[0].y) <= r) return 'p1';
      if (Math.hypot(x - eps[1].x, y - eps[1].y) <= r) return 'p2';
      return null;
    }
    const H = this.handlePositions(layer);
    if (Math.hypot(x - H.rot.x, y - H.rot.y) <= 10 / z) return 'rot';
    for (const k of ['nw', 'ne', 'sw', 'se', 'n', 's', 'w', 'e']) {
      if (Math.hypot(x - H[k].x, y - H[k].y) <= r) return k;
    }
    return null;
  }

  redrawHandles() {
    const ctx = PICanvasEngine.getOverlayCtx();
    if (!ctx) return;
    PICanvasEngine.clearOverlay();
    if (this.drawing) return;
    const layer = this.activeShape();
    if (!layer || layer.locked) return;
    const z = PICanvasEngine.getZoom() || 1;
    const hs = 5 / z;
    ctx.save();
    PICanvasEngine.configureContext(ctx);
    ctx.strokeStyle = '#7c6ff7';
    ctx.lineWidth = 1 / z;

    if (this.isLineType(layer.shapeData)) {
      // 線/矢印: 端点ハンドル
      const eps = this.endpointPositions(layer);
      ctx.setLineDash([4 / z, 3 / z]);
      ctx.beginPath(); ctx.moveTo(eps[0].x, eps[0].y); ctx.lineTo(eps[1].x, eps[1].y); ctx.stroke();
      ctx.setLineDash([]);
      eps.forEach(p => {
        ctx.beginPath(); ctx.arc(p.x, p.y, 6 / z, 0, Math.PI * 2);
        ctx.fillStyle = '#ffffff'; ctx.fill();
        ctx.lineWidth = 1.5 / z; ctx.strokeStyle = '#7c6ff7'; ctx.stroke();
      });
      ctx.restore();
      return;
    }

    const H = this.handlePositions(layer);
    // コンテンツ境界（破線）
    ctx.setLineDash([4 / z, 3 / z]);
    ctx.beginPath();
    ctx.moveTo(H.nw.x, H.nw.y); ctx.lineTo(H.ne.x, H.ne.y);
    ctx.lineTo(H.se.x, H.se.y); ctx.lineTo(H.sw.x, H.sw.y);
    ctx.closePath(); ctx.stroke();
    ctx.setLineDash([]);
    // 回転ハンドルへの脚
    ctx.beginPath(); ctx.moveTo(H.n.x, H.n.y); ctx.lineTo(H.rot.x, H.rot.y); ctx.stroke();
    // 拡縮ハンドル（四角）
    ctx.fillStyle = '#ffffff'; ctx.strokeStyle = '#7c6ff7'; ctx.lineWidth = 1.5 / z;
    ['nw', 'n', 'ne', 'w', 'e', 'sw', 's', 'se'].forEach(k => {
      ctx.beginPath(); ctx.rect(H[k].x - hs, H[k].y - hs, hs * 2, hs * 2);
      ctx.fill(); ctx.stroke();
    });
    // 回転ハンドル（丸）
    ctx.beginPath(); ctx.arc(H.rot.x, H.rot.y, 6 / z, 0, Math.PI * 2);
    ctx.fillStyle = '#7c6ff7'; ctx.fill();
    ctx.lineWidth = 1.5 / z; ctx.strokeStyle = '#fff'; ctx.stroke();
    // 回転中は角度を表示
    if (this.rotating) {
      const deg = Math.round(((layer.rotation || 0) * 180 / Math.PI) % 360);
      const label = ((deg % 360) + 360) % 360 + '°';
      const fontPx = 12 / z;
      ctx.font = fontPx + 'px sans-serif';
      const tw = ctx.measureText(label).width;
      const pad = 4 / z;
      ctx.fillStyle = 'rgba(20,20,32,0.85)';
      ctx.fillRect(H.rot.x + 10 / z, H.rot.y - fontPx / 2 - pad, tw + pad * 2, fontPx + pad * 2);
      ctx.fillStyle = '#fff';
      ctx.textBaseline = 'top';
      ctx.fillText(label, H.rot.x + 10 / z + pad, H.rot.y - fontPx / 2);
    }
    ctx.restore();
  }

  // ===== マウス操作 =====

  onMouseDown(e) {
    const active = this.activeShape();
    if (active && !active.locked) {
      const h = this.hitHandle(active, e.canvasX, e.canvasY);
      if (h === 'rot') {
        const g = this._geom(active);
        this.rotating = {
          layer: active, cx: g.c.x, cy: g.c.y,
          startAngle: Math.atan2(e.canvasY - g.c.y, e.canvasX - g.c.x),
          startRotation: active.rotation || 0
        };
        return;
      }
      if (h === 'p1' || h === 'p2') {
        const eps = this.endpointPositions(active);
        this.endpointDrag = { layer: active, which: h === 'p1' ? 0 : 1, fixed: eps[h === 'p1' ? 1 : 0] };
        return;
      }
      if (h) { this.beginResize(active, h); return; }
    }

    const existing = this.findShapeLayerAt(e.canvasX, e.canvasY);
    if (existing) {
      // 既存図形をクリック → 選択して移動待機（Alt+ドラッグで複製）
      const idx = PILayerManager.getAll().indexOf(existing);
      if (idx >= 0) PILayerManager.setActiveIndex(idx);
      this.pendingLayer = existing;
      this.movingLayer = false;
      this.altDup = !!e.altKey;
      this.dupDone = false;
      this.dragStartX = e.canvasX; this.dragStartY = e.canvasY;
      this.layerStartX = existing.x; this.layerStartY = existing.y;
      this.moveSnapRect = this.contentRect(existing);
      this.snapTargets = PILayerTransform.snapTargetsFromLayers(existing);
      PIEventBus.emit('tool:properties-changed');
      return;
    }
    // 空き領域 → 新規図形の描画開始
    this.drawing = true;
    PICanvasEngine.clearOverlay();
    this.startX = e.canvasX; this.startY = e.canvasY;
  }

  // スナップで吸着した位置をガイド線で示す（オーバーレイに追記する）
  drawGuideLines(g) {
    if (!g || (g.guideX == null && g.guideY == null)) return;
    const ctx = PICanvasEngine.getOverlayCtx();
    const s = PICanvasEngine.getCanvasSize();
    ctx.save();
    ctx.strokeStyle = '#ff3db4'; ctx.lineWidth = 1 / PICanvasEngine.getZoom();
    if (g.guideX != null) { ctx.beginPath(); ctx.moveTo(g.guideX, 0); ctx.lineTo(g.guideX, s.height); ctx.stroke(); }
    if (g.guideY != null) { ctx.beginPath(); ctx.moveTo(0, g.guideY); ctx.lineTo(s.width, g.guideY); ctx.stroke(); }
    ctx.restore();
  }

  drawSizeBadge(ctx, x, y, text) {
    const z = PICanvasEngine.getZoom() || 1;
    const fontPx = 12 / z;
    ctx.save();
    ctx.font = fontPx + 'px sans-serif';
    const tw = ctx.measureText(text).width;
    const pad = 4 / z;
    ctx.fillStyle = 'rgba(20,20,32,0.85)';
    ctx.fillRect(x + 10 / z, y + 10 / z, tw + pad * 2, fontPx + pad * 2);
    ctx.fillStyle = '#fff';
    ctx.textBaseline = 'top';
    ctx.fillText(text, x + 10 / z + pad, y + 10 / z + pad);
    ctx.restore();
  }

  onMouseMove(e) {
    if (this.rotating) {
      const s = this.rotating;
      let ang = Math.atan2(e.canvasY - s.cy, e.canvasX - s.cx) - s.startAngle + s.startRotation;
      if (e.shiftKey) { const step = Math.PI / 12; ang = Math.round(ang / step) * step; } // 15°スナップ
      s.layer.rotation = ang;
      PILayerManager.requestRender();
      this.redrawHandles();
      return;
    }
    if (this.endpointDrag) { this.applyEndpointDrag(e); return; }
    if (this.resizing) { this.applyResize(e); return; }

    if (this.pendingLayer) {
      const dx = e.canvasX - this.dragStartX;
      const dy = e.canvasY - this.dragStartY;
      if (!this.movingLayer && Math.hypot(dx, dy) >= this.dragThreshold) {
        this.movingLayer = true;
        if (this.altDup && !this.dupDone) {
          // Alt+ドラッグ: 複製を作ってそちらを動かす
          this.dupDone = true;
          const idx = PILayerManager.getAll().indexOf(this.pendingLayer);
          PILayerManager.duplicateLayer(idx);
          this.pendingLayer = PILayerManager.getActive();
          this.snapTargets = PILayerTransform.snapTargetsFromLayers(this.pendingLayer);
        }
        const vp = PICanvasEngine.getViewport(); if (vp) vp.style.cursor = 'move';
      }
      if (this.movingLayer) {
        let dx2 = dx, dy2 = dy;
        if (e.shiftKey) { if (Math.abs(dx2) > Math.abs(dy2)) dy2 = 0; else dx2 = 0; } // 軸固定
        this.pendingLayer.x = this.layerStartX + dx2;
        this.pendingLayer.y = this.layerStartY + dy2;
        let guide = { guideX: null, guideY: null };
        if (!e.ctrlKey) {
          const gx = (window.PIGuides ? PIGuides.getSnapX() : []).concat(this.snapTargets ? this.snapTargets.xs : []);
          const gy = (window.PIGuides ? PIGuides.getSnapY() : []).concat(this.snapTargets ? this.snapTargets.ys : []);
          guide = PILayerTransform.snapToCanvas(this.pendingLayer, 6 / PICanvasEngine.getZoom(), this.moveSnapRect, gx, gy);
        }
        PILayerManager.requestRender();
        this.redrawHandles();
        this.drawGuideLines(guide);
      }
      return;
    }
    if (!this.drawing) {
      const vp = PICanvasEngine.getViewport();
      if (vp) {
        const act = this.activeShape();
        const h = (act && !act.locked) ? this.hitHandle(act, e.canvasX, e.canvasY) : null;
        let cursor = null;
        if (h === 'rot') cursor = 'grab';
        else if (h === 'p1' || h === 'p2') cursor = 'move';
        else if (h === 'n' || h === 's') cursor = 'ns-resize';
        else if (h === 'w' || h === 'e') cursor = 'ew-resize';
        else if (h === 'nw' || h === 'se') cursor = 'nwse-resize';
        else if (h === 'ne' || h === 'sw') cursor = 'nesw-resize';
        if (!cursor) cursor = this.findShapeLayerAt(e.canvasX, e.canvasY) ? 'move' : this.cursor;
        vp.style.cursor = cursor;
      }
      return;
    }
    // 新規描画のライブプレビュー（オーバーレイ）
    const ctx = PICanvasEngine.getOverlayCtx();
    PICanvasEngine.configureContext(ctx);
    PICanvasEngine.clearOverlay();
    this.drawPreview(ctx, this.startX, this.startY, e.canvasX, e.canvasY, e.shiftKey, e.altKey);
  }

  onMouseUp(e) {
    if (this.rotating) {
      this.rotating = null;
      PIHistoryManager.push('図形を回転');
      PIEventBus.emit('tool:properties-changed');
      this.redrawHandles();
      return;
    }
    if (this.endpointDrag) {
      const layer = this.endpointDrag.layer;
      this.endpointDrag = null;
      const sd = layer.shapeData;
      sd.x1 = Math.round(sd.x1); sd.y1 = Math.round(sd.y1);
      sd.x2 = Math.round(sd.x2); sd.y2 = Math.round(sd.y2);
      PILayerManager.renderShapeLayer(layer);
      layer.x = Math.min(sd.x1, sd.x2) - PILayerManager.SHAPE_PAD;
      layer.y = Math.min(sd.y1, sd.y2) - PILayerManager.SHAPE_PAD;
      PILayerManager.requestRender();
      PIHistoryManager.push('図形の端点変更');
      PIEventBus.emit('tool:properties-changed');
      this.redrawHandles();
      return;
    }
    if (this.resizing) {
      const layer = this.resizing.layer;
      this.resizing = null;
      const sd = layer.shapeData;
      sd.w = Math.max(2, Math.round(sd.w)); sd.h = Math.max(2, Math.round(sd.h));
      PILayerManager.renderShapeLayer(layer);
      layer.x = Math.round(layer.x); layer.y = Math.round(layer.y);
      PILayerManager.requestRender();
      PIHistoryManager.push('図形をリサイズ');
      PIEventBus.emit('tool:properties-changed');
      this.redrawHandles();
      return;
    }
    if (this.pendingLayer) {
      const layer = this.pendingLayer;
      this.pendingLayer = null;
      if (this.movingLayer) {
        this.movingLayer = false;
        layer.x = Math.round(layer.x); layer.y = Math.round(layer.y);
        PILayerManager.requestRender();
        PIHistoryManager.push(this.dupDone ? '図形を複製' : '図形を移動');
      }
      this.altDup = false; this.dupDone = false;
      const vp = PICanvasEngine.getViewport();
      if (vp) vp.style.cursor = this.findShapeLayerAt(e.canvasX, e.canvasY) ? 'move' : this.cursor;
      this.redrawHandles();
      return;
    }
    if (!this.drawing) return;
    this.drawing = false;
    PICanvasEngine.clearOverlay();

    let x1 = this.startX, y1 = this.startY, x2 = e.canvasX, y2 = e.canvasY;
    const isLine = this.shapeType === 'line' || this.shapeType === 'arrow';
    if (e.shiftKey && !isLine) {
      const s = Math.min(Math.abs(x2 - x1), Math.abs(y2 - y1));
      x2 = x1 + Math.sign(x2 - x1 || 1) * s;
      y2 = y1 + Math.sign(y2 - y1 || 1) * s;
    }
    let w = Math.abs(x2 - x1), h = Math.abs(y2 - y1);
    let bx = Math.min(x1, x2), by = Math.min(y1, y2);
    if (e.altKey && !isLine) {
      // Alt: 始点を中心として描く
      bx = x1 - w; by = y1 - h; w *= 2; h *= 2;
    }
    if (Math.max(w, h) < 2) { this.redrawHandles(); return; } // 小さすぎる操作は無視

    const fg = document.getElementById('fg-color-input').value;
    const bg = document.getElementById('bg-color-input').value;
    const shapeData = {
      shapeType: this.shapeType,
      fillEnabled: this.fillEnabled && !isLine,
      fillColor: bg,
      strokeEnabled: this.strokeEnabled || isLine,
      strokeColor: fg,
      strokeWidth: this.strokeWidth,
      strokeStyle: this.strokeStyle,
      cornerRadius: this.cornerRadius,
      w: w, h: h,
      x1: x1, y1: y1, x2: x2, y2: y2,
      bx: bx, by: by
    };
    PILayerManager.addShapeLayer(shapeData);
    PIHistoryManager.push('図形');
    PIEventBus.emit('tool:properties-changed');
  }

  // ===== リサイズ（shapeData の w/h に畳み込む＝線幅は変わらずシャープ） =====

  beginResize(layer, handle) {
    const sd = layer.shapeData;
    this.resizing = {
      layer, handle,
      w0: sd.w, h0: sd.h,
      snap: {
        x: layer.x, y: layer.y,
        cw: layer.canvas.width, ch: layer.canvas.height,
        sx: layer.scaleX || 1, sy: layer.scaleY || 1,
        rot: layer.rotation || 0
      }
    };
  }

  // ドラッグ開始時点のジオメトリでキャンバス座標⇔ローカル座標を変換する
  // （再レンダリングでレイヤーcanvasの寸法が変わるため、蓄積誤差を避ける）
  _snapCanvasToLocal(snap, px, py) {
    const cx = snap.x + snap.cw / 2, cy = snap.y + snap.ch / 2;
    const cos = Math.cos(-snap.rot), sin = Math.sin(-snap.rot);
    const dx = px - cx, dy = py - cy;
    const rx = dx * cos - dy * sin, ry = dx * sin + dy * cos;
    return { x: rx / snap.sx + snap.cw / 2, y: ry / snap.sy + snap.ch / 2 };
  }

  _snapLocalToCanvas(snap, lx, ly) {
    const cx = snap.x + snap.cw / 2, cy = snap.y + snap.ch / 2;
    const cos = Math.cos(snap.rot), sin = Math.sin(snap.rot);
    const dx = (lx - snap.cw / 2) * snap.sx, dy = (ly - snap.ch / 2) * snap.sy;
    return { x: cx + dx * cos - dy * sin, y: cy + dx * sin + dy * cos };
  }

  applyResize(e) {
    const r = this.resizing;
    const layer = r.layer;
    const sd = layer.shapeData;
    const PAD = PILayerManager.SHAPE_PAD;
    const sign = { nw: [-1, -1], n: [0, -1], ne: [1, -1], w: [-1, 0], e: [1, 0], sw: [-1, 1], s: [0, 1], se: [1, 1] }[r.handle];
    const m = this._snapCanvasToLocal(r.snap, e.canvasX, e.canvasY);
    const MIN = 2;
    let w = r.w0, h = r.h0;
    if (sign[0] === 1) w = Math.max(MIN, m.x - PAD);
    else if (sign[0] === -1) w = Math.max(MIN, PAD + r.w0 - m.x);
    if (sign[1] === 1) h = Math.max(MIN, m.y - PAD);
    else if (sign[1] === -1) h = Math.max(MIN, PAD + r.h0 - m.y);
    if (e.shiftKey && sign[0] !== 0 && sign[1] !== 0) {
      const f = Math.max(w / r.w0, h / r.h0);
      w = Math.max(MIN, r.w0 * f); h = Math.max(MIN, r.h0 * f);
    }
    // アンカー（ドラッグと反対側）の位置を固定する
    const ax0 = sign[0] === 1 ? PAD : sign[0] === -1 ? PAD + r.w0 : PAD + r.w0 / 2;
    const ay0 = sign[1] === 1 ? PAD : sign[1] === -1 ? PAD + r.h0 : PAD + r.h0 / 2;
    const anchorCanvas = this._snapLocalToCanvas(r.snap, ax0, ay0);
    sd.w = w; sd.h = h;
    PILayerManager.renderShapeLayer(layer);
    // 新しいジオメトリでアンカーが同じキャンバス座標に来るよう位置決めする
    const ax1 = sign[0] === 1 ? PAD : sign[0] === -1 ? PAD + w : PAD + w / 2;
    const ay1 = sign[1] === 1 ? PAD : sign[1] === -1 ? PAD + h : PAD + h / 2;
    const cw = layer.canvas.width, ch = layer.canvas.height;
    const cos = Math.cos(r.snap.rot), sin = Math.sin(r.snap.rot);
    const dx = (ax1 - cw / 2) * r.snap.sx, dy = (ay1 - ch / 2) * r.snap.sy;
    layer.x = anchorCanvas.x - (dx * cos - dy * sin) - cw / 2;
    layer.y = anchorCanvas.y - (dx * sin + dy * cos) - ch / 2;
    PILayerManager.requestRender();
    this.redrawHandles();
    this.drawSizeBadge(PICanvasEngine.getOverlayCtx(), e.canvasX, e.canvasY, Math.round(w) + ' × ' + Math.round(h));
  }

  // ===== 線/矢印の端点編集 =====

  applyEndpointDrag(e) {
    const d = this.endpointDrag;
    const layer = d.layer;
    const sd = layer.shapeData;
    let px = e.canvasX, py = e.canvasY;
    if (e.shiftKey) {
      // 固定端点を基準に45°スナップ
      const ang = Math.atan2(py - d.fixed.y, px - d.fixed.x);
      const dist = Math.hypot(px - d.fixed.x, py - d.fixed.y);
      const snapAng = Math.round(ang / (Math.PI / 4)) * (Math.PI / 4);
      px = d.fixed.x + dist * Math.cos(snapAng);
      py = d.fixed.y + dist * Math.sin(snapAng);
    }
    const p1 = d.which === 0 ? { x: px, y: py } : d.fixed;
    const p2 = d.which === 1 ? { x: px, y: py } : d.fixed;
    // 端点をキャンバス座標で持ち直し、レイヤーの回転・拡縮は端点へ畳み込む
    sd.x1 = p1.x; sd.y1 = p1.y; sd.x2 = p2.x; sd.y2 = p2.y;
    layer.rotation = 0; layer.scaleX = 1; layer.scaleY = 1;
    PILayerManager.renderShapeLayer(layer);
    layer.x = Math.min(p1.x, p2.x) - PILayerManager.SHAPE_PAD;
    layer.y = Math.min(p1.y, p2.y) - PILayerManager.SHAPE_PAD;
    PILayerManager.requestRender();
    this.redrawHandles();
  }

  drawPreview(ctx, x1, y1, x2, y2, shiftKey, altKey) {
    ctx.save();
    PICanvasEngine.configureContext(ctx);
    ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    const fg = document.getElementById('fg-color-input').value;
    const bg = document.getElementById('bg-color-input').value;
    const isLine = this.shapeType === 'line' || this.shapeType === 'arrow';
    let x = Math.min(x1, x2), y = Math.min(y1, y2);
    let w = Math.abs(x2 - x1), h = Math.abs(y2 - y1);
    if (shiftKey && !isLine) { const s = Math.min(w, h); w = s; h = s; }
    if (altKey && !isLine) { x = x1 - w; y = y1 - h; w *= 2; h *= 2; } // 中心から描く
    if (!isLine) {
      PILayerManager.traceShapePath(ctx, this.shapeType, x, y, w, h, this.cornerRadius);
      if (this.fillEnabled) { ctx.fillStyle = bg; ctx.fill(); }
      if (this.strokeEnabled) {
        ctx.strokeStyle = fg; ctx.lineWidth = this.strokeWidth;
        ctx.setLineDash(PILayerManager.strokeDashFor(this.strokeStyle, this.strokeWidth));
        ctx.stroke();
        ctx.setLineDash([]);
      }
    } else {
      ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2);
      ctx.strokeStyle = fg; ctx.lineWidth = this.strokeWidth;
      ctx.setLineDash(PILayerManager.strokeDashFor(this.strokeStyle, this.strokeWidth));
      ctx.stroke();
      ctx.setLineDash([]);
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
    // 描画中のサイズをカーソル脇に表示
    const label = isLine
      ? Math.round(Math.hypot(x2 - x1, y2 - y1)) + ' px'
      : Math.round(w) + ' × ' + Math.round(h);
    this.drawSizeBadge(ctx, x2, y2, label);
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
    const TYPE_LABELS = { rect: '矩形', ellipse: '楕円', triangle: '三角', star: '星', line: '線', arrow: '矢印' };
    const TYPES = ['rect', 'ellipse', 'triangle', 'star', 'line', 'arrow'];
    const STYLES = [['solid', '実線'], ['dash', '破線'], ['dot', '点線']];
    const active = PILayerManager.getActive();
    if (active && active.type === 'shape') {
      const sd = active.shapeData;
      const isLine = sd.shapeType === 'line' || sd.shapeType === 'arrow';
      const fields = [
        {
          type: 'button-group', label: '形状', key: 'stype',
          buttons: TYPES.map(t => ({
            label: TYPE_LABELS[t],
            active: sd.shapeType === t,
            onClick: () => self.editActiveShape(d => self.setShapeTypeFor(active, t), '図形の形状変更')
          }))
        }
      ];
      if (!isLine) {
        fields.push({ type: 'checkbox', label: '塗り', key: 'fillEnabled', value: sd.fillEnabled, onChange: (v) => self.editActiveShape(d => d.fillEnabled = v, '図形の塗り') });
        fields.push({
          type: 'button-group', label: '塗りタイプ', key: 'fillType',
          buttons: [['solid', '単色'], ['linear', '線形グラデ'], ['radial', '円形グラデ']].map(([v, lab]) => ({
            label: lab, active: (sd.fillType || 'solid') === v,
            onClick: () => self.editActiveShape(d => { d.fillType = v; d.fillEnabled = true; if (v !== 'solid' && !d.fillColor2) d.fillColor2 = '#8ec5ff'; }, '図形の塗りタイプ')
          }))
        });
        fields.push({ type: 'color', label: (sd.fillType === 'linear' || sd.fillType === 'radial') ? '塗り色（開始）' : '塗り色', key: 'fillColor', value: sd.fillColor, onChange: (v) => self.editActiveShape(d => { d.fillColor = v; d.fillEnabled = true; }, '図形の塗り色') });
        if (sd.fillType === 'linear' || sd.fillType === 'radial') {
          fields.push({ type: 'color', label: '塗り色（終了）', key: 'fillColor2', value: sd.fillColor2 || '#8ec5ff', onChange: (v) => self.editActiveShape(d => { d.fillColor2 = v; }, '図形の塗り色') });
          if (sd.fillType === 'linear') {
            fields.push({ type: 'slider', label: 'グラデ角度', key: 'gradientAngle', value: sd.gradientAngle || 0, min: 0, max: 360, unit: '°', onChange: (v) => self.editActiveShape(d => { d.gradientAngle = parseInt(v); }, 'グラデ角度') });
          }
        }
      }
      fields.push({ type: 'checkbox', label: '枠線', key: 'strokeEnabled', value: sd.strokeEnabled, onChange: (v) => self.editActiveShape(d => d.strokeEnabled = v, '図形の枠線') });
      fields.push({ type: 'color', label: '枠色', key: 'strokeColor', value: sd.strokeColor, onChange: (v) => self.editActiveShape(d => { d.strokeColor = v; d.strokeEnabled = true; }, '図形の枠色') });
      fields.push({ type: 'slider', label: '線幅', key: 'strokeWidth', value: sd.strokeWidth, min: 1, max: 40, onChange: (v) => self.editActiveShape(d => d.strokeWidth = parseInt(v), '図形の線幅') });
      fields.push({
        type: 'button-group', label: '線種', key: 'strokeStyle',
        buttons: STYLES.map(([v, lab]) => ({
          label: lab, active: (sd.strokeStyle || 'solid') === v,
          onClick: () => self.editActiveShape(d => { d.strokeStyle = v; d.strokeEnabled = true; }, '図形の線種')
        }))
      });
      if (sd.shapeType === 'rect') {
        fields.push({ type: 'slider', label: '角丸', key: 'cornerRadius', value: sd.cornerRadius || 0, min: 0, max: 200, onChange: (v) => self.editActiveShape(d => d.cornerRadius = parseInt(v), '図形の角丸') });
      }
      // ドロップシャドウ
      fields.push({
        type: 'checkbox', label: '影', key: 'shadowEnabled', value: !!sd.shadowEnabled,
        onChange: (v) => self.editActiveShape(d => {
          d.shadowEnabled = v;
          if (v) {
            if (d.shadowColor == null) d.shadowColor = '#000000';
            if (d.shadowOpacity == null) d.shadowOpacity = 50;
            if (d.shadowBlur == null) d.shadowBlur = 8;
            if (d.shadowDistance == null) d.shadowDistance = 4;
          }
        }, '図形の影')
      });
      if (sd.shadowEnabled) {
        fields.push({ type: 'color', label: '影色', key: 'shadowColor', value: sd.shadowColor || '#000000', onChange: (v) => self.editActiveShape(d => { d.shadowColor = v; }, '影色') });
        fields.push({ type: 'slider', label: '影の濃さ', key: 'shadowOpacity', value: sd.shadowOpacity == null ? 50 : sd.shadowOpacity, min: 0, max: 100, unit: '%', onChange: (v) => self.editActiveShape(d => { d.shadowOpacity = parseInt(v); }, '影の濃さ') });
        fields.push({ type: 'slider', label: '影ぼかし', key: 'shadowBlur', value: sd.shadowBlur == null ? 8 : sd.shadowBlur, min: 0, max: 30, onChange: (v) => self.editActiveShape(d => { d.shadowBlur = parseInt(v); }, '影ぼかし') });
        fields.push({ type: 'slider', label: '影の距離', key: 'shadowDistance', value: sd.shadowDistance == null ? 4 : sd.shadowDistance, min: 0, max: 15, onChange: (v) => self.editActiveShape(d => { d.shadowDistance = parseInt(v); }, '影の距離') });
      }
      return { title: '図形（選択中）', fields };
    }

    // 新規作成のデフォルト設定
    const fgInput = document.getElementById('fg-color-input');
    const bgInput = document.getElementById('bg-color-input');
    const isLineType = self.shapeType === 'line' || self.shapeType === 'arrow';
    const fields = [
      {
        type: 'button-group', label: '形状', key: 'shapeType',
        buttons: TYPES.map(t => ({
          label: TYPE_LABELS[t], active: self.shapeType === t,
          onClick: () => { self.shapeType = t; PIEventBus.emit('tool:properties-changed'); }
        }))
      },
      { type: 'slider', label: '線幅', key: 'strokeWidth', value: self.strokeWidth, min: 1, max: 40, onChange: (v) => { self.strokeWidth = parseInt(v); } },
      {
        type: 'button-group', label: '線種', key: 'strokeStyle',
        buttons: STYLES.map(([v, lab]) => ({
          label: lab, active: (self.strokeStyle || 'solid') === v,
          onClick: () => { self.strokeStyle = v; PIEventBus.emit('tool:properties-changed'); }
        }))
      }
    ];
    if (self.shapeType === 'rect') {
      fields.push({ type: 'slider', label: '角丸', key: 'cornerRadius', value: self.cornerRadius, min: 0, max: 200, onChange: (v) => { self.cornerRadius = parseInt(v); } });
    }
    if (!isLineType) {
      fields.push({ type: 'checkbox', label: '塗り', key: 'fill', value: self.fillEnabled, onChange: (v) => { self.fillEnabled = v; } });
      fields.push({ type: 'color', label: '塗り色', key: 'fillColor', value: bgInput ? bgInput.value : '#ffffff', onChange: (v) => { if (bgInput) bgInput.value = v; self.fillEnabled = true; } });
      fields.push({ type: 'checkbox', label: '枠線', key: 'stroke', value: self.strokeEnabled, onChange: (v) => { self.strokeEnabled = v; } });
    }
    fields.push({ type: 'color', label: isLineType ? '線の色' : '枠色', key: 'strokeColor', value: fgInput ? fgInput.value : '#000000', onChange: (v) => { if (fgInput) fgInput.value = v; self.strokeEnabled = true; } });
    return { title: '図形（新規）', fields };
  }
})();
