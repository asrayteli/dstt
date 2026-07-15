/* PowerImager — TextTool: テキスト配置・編集
 * 操作モデル:
 *  - 空き領域クリック: 新規テキストの入力ボックスを開く
 *  - 既存テキストをクリック: 選択（ハンドル表示）・そのままドラッグで移動
 *  - ダブルクリック: 内容の編集（移動/変形/図形ツールからでも可）
 *  - コーナーハンドル: ドラッグで文字サイズ変更 / 上の丸ハンドル: 回転
 */
window.PITextTool = new (class extends PIToolBase {
  constructor() {
    super('text', 'text');
    this.editingLayer = null;
    this.isEditing = false;
    this.pendingDragLayer = null;
    this.draggingLayer = false;
    this.dragStartX = 0;
    this.dragStartY = 0;
    this.layerStartX = 0;
    this.layerStartY = 0;
    this.dragThreshold = 4;
    // 回転・サイズ変更ハンドル
    this.rotating = false;
    this.rotStart = null;
    this.resizing = null;
    this._cr = null;          // コンテンツ矩形キャッシュ
    this._boundRedraw = false;
    this.textData = {
      text: '', fontFamily: 'sans-serif', size: 32,
      color: '#000000', bold: false, italic: false,
      underline: false, strikethrough: false,
      align: 'left', lineHeight: 1.3, letterSpacing: 0,
      vertical: false, bgEnabled: false, bgColor: '#ffffff',
      strokeColor: '#ffffff', strokeWidth: 0,
      // 影は既定でオフ（にじんで見えるため）。プリセットやPROモードで有効化する。
      shadowColor: '#000000', shadowOffsetX: 0, shadowOffsetY: 0, shadowBlur: 0,
      rotation: 0, x: 0, y: 0
    };
    this.altDup = false;
    this.dupDone = false;
    this.snapTargets = null;
    this.moveSnapRect = null;
    this.presets = [
      { name: '赤い警告テキスト', color: '#ff0000', bold: true, size: 40, strokeColor: '#ffffff', strokeWidth: 3 },
      { name: '白縁取り見出し', color: '#ffffff', bold: true, size: 48, strokeColor: '#000000', strokeWidth: 4 },
      { name: 'シンプル黒', color: '#000000', bold: false, size: 24, strokeWidth: 0 },
      { name: '影付きタイトル', color: '#ffffff', bold: true, size: 36, strokeWidth: 0, shadowColor: '#000000', shadowOffsetX: 3, shadowOffsetY: 3, shadowBlur: 6 }
    ];
  }

  activate() {
    super.activate();
    if (!this._boundRedraw) {
      this._boundRedraw = true;
      PIEventBus.on('layers:changed', () => { if (PIToolbar.getCurrentTool() === this) { this._cr = null; this.redrawHandles(); } });
      PIEventBus.on('canvas:zoom-changed', () => { if (PIToolbar.getCurrentTool() === this) this.redrawHandles(); });
    }
    this.redrawHandles();
  }

  deactivate() {
    super.deactivate();
    this.pendingDragLayer = null;
    this.draggingLayer = false;
    this.rotating = false;
    this.rotStart = null;
    this.resizing = null;
    // ツール切替で入力中テキストが消えると入力が無駄になるため、内容があれば確定する
    this.commitIfEditing();
    PICanvasEngine.clearOverlay();
  }

  commitIfEditing() {
    if (!this.isEditing) return;
    const ta = this._activeTextarea;
    if (ta && ta.value.trim()) this.confirmEdit(ta.value);
    else this.cancelEdit();
  }

  // 指定レイヤーの内容編集を開始（ダブルクリック/編集ボタンから）
  beginEditLayer(layer) {
    if (!layer || layer.type !== 'text' || layer.locked) return;
    const idx = PILayerManager.getAll().indexOf(layer);
    if (idx >= 0) PILayerManager.setActiveIndex(idx);
    this.editingLayer = layer;
    this.textData = { ...layer.textData, x: layer.x, y: layer.y };
    this.showEditBox();
  }

  onMouseDown(e) {
    if (this.isEditing) {
      // 編集ボックスの外側クリック＝確定（ボックス内は stopPropagation 済み）
      this.commitIfEditing();
      return;
    }

    const active = this.targetLayer();
    if (active && !active.locked && active.visible) {
      // コーナーハンドル＝文字サイズ変更
      const rh = this.hitResizeHandle(active, e.canvasX, e.canvasY);
      if (rh) {
        const g = this._handleGeom(active);
        const d0 = Math.hypot(e.canvasX - g.c.x, e.canvasY - g.c.y);
        if (d0 > 0.001) {
          const td = active.textData;
          this.resizing = {
            layer: active, d0, cx: g.c.x, cy: g.c.y,
            size0: td.size, stroke0: td.strokeWidth || 0, blur0: td.shadowBlur || 0,
            shx0: td.shadowOffsetX || 0, shy0: td.shadowOffsetY || 0
          };
          return;
        }
      }
      // 回転ハンドル
      if (this.hitRotHandle(active, e.canvasX, e.canvasY)) {
        const g = this._handleGeom(active);
        this.rotating = true;
        this.rotStart = {
          layer: active, cx: g.c.x, cy: g.c.y,
          startAngle: Math.atan2(e.canvasY - g.c.y, e.canvasX - g.c.x),
          startRotation: active.rotation || 0
        };
        return;
      }
    }

    const existingTextLayer = this.findTextLayerAt(e.canvasX, e.canvasY);
    if (existingTextLayer) {
      const idx = PILayerManager.getAll().indexOf(existingTextLayer);
      if (idx >= 0) PILayerManager.setActiveIndex(idx);
      this.pendingDragLayer = existingTextLayer;
      this.draggingLayer = false;
      this.altDup = !!e.altKey;
      this.dupDone = false;
      this.dragStartX = e.canvasX;
      this.dragStartY = e.canvasY;
      this.layerStartX = existingTextLayer.x;
      this.layerStartY = existingTextLayer.y;
      this.moveSnapRect = PILayerTransform.contentLocalRect(existingTextLayer);
      this.snapTargets = PILayerTransform.snapTargetsFromLayers(existingTextLayer);
      return;
    }

    this.textData.x = e.canvasX;
    this.textData.y = e.canvasY;
    this.editingLayer = null;
    this.textData.text = '';
    this.showEditBox();
  }

  onMouseMove(e) {
    if (this.isEditing) return;

    if (this.resizing) {
      const r = this.resizing;
      const f = Math.hypot(e.canvasX - r.cx, e.canvasY - r.cy) / r.d0;
      const size = Math.max(4, Math.min(500, Math.round(r.size0 * f)));
      const layer = r.layer;
      const td = layer.textData;
      if (size !== td.size) {
        const sf = size / r.size0;
        td.size = size;
        td.strokeWidth = Math.round(r.stroke0 * sf * 10) / 10;
        td.shadowBlur = Math.round(r.blur0 * sf * 10) / 10;
        td.shadowOffsetX = Math.round(r.shx0 * sf * 10) / 10;
        td.shadowOffsetY = Math.round(r.shy0 * sf * 10) / 10;
        // 中心固定で再レンダリング
        const ccx = layer.x + layer.canvas.width / 2;
        const ccy = layer.y + layer.canvas.height / 2;
        PILayerManager.renderTextLayer(layer);
        layer.x = Math.round(ccx - layer.canvas.width / 2);
        layer.y = Math.round(ccy - layer.canvas.height / 2);
        layer.textData = { ...layer.textData, x: layer.x, y: layer.y };
        this._cr = null;
        PILayerManager.requestRender();
        this.redrawHandles();
      }
      return;
    }

    if (this.rotating) {
      const s = this.rotStart;
      let ang = Math.atan2(e.canvasY - s.cy, e.canvasX - s.cx) - s.startAngle + s.startRotation;
      if (e.shiftKey) { const step = Math.PI / 12; ang = Math.round(ang / step) * step; } // 15°スナップ
      s.layer.rotation = ang;
      PILayerManager.requestRender();
      this.redrawHandles();
      return;
    }

    if (this.pendingDragLayer) {
      const dx = e.canvasX - this.dragStartX;
      const dy = e.canvasY - this.dragStartY;
      if (!this.draggingLayer && Math.hypot(dx, dy) >= this.dragThreshold) {
        this.draggingLayer = true;
        if (this.altDup && !this.dupDone) {
          // Alt+ドラッグ: 複製を作ってそちらを動かす
          this.dupDone = true;
          const idx = PILayerManager.getAll().indexOf(this.pendingDragLayer);
          PILayerManager.duplicateLayer(idx);
          this.pendingDragLayer = PILayerManager.getActive();
          this.snapTargets = PILayerTransform.snapTargetsFromLayers(this.pendingDragLayer);
        }
        const vp = PICanvasEngine.getViewport();
        if (vp) vp.style.cursor = 'move';
      }
      if (this.draggingLayer) {
        const layer = this.pendingDragLayer;
        let dx2 = dx, dy2 = dy;
        if (e.shiftKey) { if (Math.abs(dx2) > Math.abs(dy2)) dy2 = 0; else dx2 = 0; } // 軸固定
        layer.x = this.layerStartX + dx2;
        layer.y = this.layerStartY + dy2;
        let guide = { guideX: null, guideY: null };
        if (!e.ctrlKey) {
          const gx = (window.PIGuides ? PIGuides.getSnapX() : []).concat(this.snapTargets ? this.snapTargets.xs : []);
          const gy = (window.PIGuides ? PIGuides.getSnapY() : []).concat(this.snapTargets ? this.snapTargets.ys : []);
          guide = PILayerTransform.snapToCanvas(layer, 6 / PICanvasEngine.getZoom(), this.moveSnapRect, gx, gy);
        }
        if (layer.textData) layer.textData = { ...layer.textData, x: layer.x, y: layer.y };
        PILayerManager.requestRender();
        this.redrawHandles();
        this.drawGuideLines(guide);
      }
      return;
    }

    const vp = PICanvasEngine.getViewport();
    if (vp) {
      const act = this.targetLayer();
      let cursor = null;
      if (act && !act.locked && act.visible) {
        const rh = this.hitResizeHandle(act, e.canvasX, e.canvasY);
        if (rh) cursor = (rh === 'nw' || rh === 'se') ? 'nwse-resize' : 'nesw-resize';
        else if (this.hitRotHandle(act, e.canvasX, e.canvasY)) cursor = 'grab';
      }
      if (!cursor) cursor = this.findTextLayerAt(e.canvasX, e.canvasY) ? 'move' : this.cursor;
      vp.style.cursor = cursor;
    }
  }

  onMouseUp(e) {
    if (this.resizing) {
      this.resizing = null;
      PIHistoryManager.push('テキストサイズ変更');
      PIEventBus.emit('tool:properties-changed');
      this.redrawHandles();
      return;
    }
    if (this.rotating) {
      this.rotating = false;
      this.rotStart = null;
      PIHistoryManager.push('テキスト回転');
      PIEventBus.emit('tool:properties-changed');
      this.redrawHandles();
      return;
    }
    if (!this.pendingDragLayer) return;
    const layer = this.pendingDragLayer;
    this.pendingDragLayer = null;

    if (this.draggingLayer) {
      this.draggingLayer = false;
      this.moveTextLayer(layer, layer.x, layer.y, true);
      PIHistoryManager.push(this.dupDone ? 'テキストを複製' : 'テキスト移動');
      this.altDup = false; this.dupDone = false;
      PIEventBus.emit('tool:properties-changed');
      const vp = PICanvasEngine.getViewport();
      if (vp) vp.style.cursor = this.findTextLayerAt(e.canvasX, e.canvasY) ? 'move' : this.cursor;
      return;
    }

    // クリック＝選択のみ（編集はダブルクリック / プロパティの編集ボタン）
    PIEventBus.emit('tool:properties-changed');
    this.redrawHandles();
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

  moveTextLayer(layer, x, y, final) {
    if (!layer || layer.locked) return;
    layer.x = x;
    layer.y = y;
    if (layer.textData) {
      layer.textData = { ...layer.textData, x, y };
    }
    if (final) {
      layer.x = Math.round(layer.x);
      layer.y = Math.round(layer.y);
      if (layer.textData) layer.textData = { ...layer.textData, x: layer.x, y: layer.y };
    }
    PILayerManager.requestRender();
    this.redrawHandles();
  }

  findTextLayerAt(x, y) {
    const layers = PILayerManager.getAll();
    for (let i = layers.length - 1; i >= 0; i--) {
      const l = layers[i];
      if (l.type === 'text' && l.visible && !l.locked) {
        // 回転/拡縮を考慮した当たり判定（透明余白は除外）
        if (PILayerTransform.hitTest(l, x, y)) return l;
      }
    }
    return null;
  }

  // ===== ハンドル（アクティブなテキストレイヤーに表示） =====

  contentRect(layer) {
    const key = layer.id + ':' + layer.canvas.width + 'x' + layer.canvas.height;
    if (this._cr && this._cr.key === key && this._cr.layer === layer) return this._cr.rect;
    const rect = PILayerTransform.contentLocalRect(layer); // テキストcanvasは小さいので走査は軽い
    this._cr = { key, layer, rect };
    return rect;
  }

  _handleGeom(layer) {
    const cr = this.contentRect(layer);
    const c = PILayerTransform.localToCanvas(layer, cr.x + cr.w / 2, cr.y + cr.h / 2);
    const rot = layer.rotation || 0;
    return {
      c,
      ew: cr.w * (layer.scaleX || 1),
      eh: cr.h * (layer.scaleY || 1),
      cos: Math.cos(rot), sin: Math.sin(rot)
    };
  }

  _cornerPositions(layer) {
    const g = this._handleGeom(layer);
    const pt = (lx, ly) => ({ x: g.c.x + lx * g.cos - ly * g.sin, y: g.c.y + lx * g.sin + ly * g.cos });
    return {
      nw: pt(-g.ew / 2, -g.eh / 2), ne: pt(g.ew / 2, -g.eh / 2),
      se: pt(g.ew / 2, g.eh / 2), sw: pt(-g.ew / 2, g.eh / 2)
    };
  }

  hitResizeHandle(layer, x, y) {
    const z = PICanvasEngine.getZoom() || 1;
    const r = 9 / z;
    const corners = this._cornerPositions(layer);
    for (const k of ['nw', 'ne', 'se', 'sw']) {
      if (Math.hypot(x - corners[k].x, y - corners[k].y) <= r) return k;
    }
    return null;
  }

  rotHandlePos(layer) {
    const g = this._handleGeom(layer);
    const z = PICanvasEngine.getZoom() || 1;
    const ly = -g.eh / 2 - 26 / z;
    return { x: g.c.x - ly * g.sin, y: g.c.y + ly * g.cos };
  }

  hitRotHandle(layer, x, y) {
    const h = this.rotHandlePos(layer);
    const z = PICanvasEngine.getZoom() || 1;
    return Math.hypot(x - h.x, y - h.y) <= 10 / z;
  }

  redrawHandles() {
    const ctx = PICanvasEngine.getOverlayCtx();
    if (!ctx) return;
    PICanvasEngine.clearOverlay();
    if (this.isEditing) return;
    const layer = this.targetLayer();
    if (!layer || !layer.visible) return;
    const z = PICanvasEngine.getZoom() || 1;
    const g = this._handleGeom(layer);
    const pt = (lx, ly) => ({ x: g.c.x + lx * g.cos - ly * g.sin, y: g.c.y + lx * g.sin + ly * g.cos });
    const corners = [pt(-g.ew / 2, -g.eh / 2), pt(g.ew / 2, -g.eh / 2), pt(g.ew / 2, g.eh / 2), pt(-g.ew / 2, g.eh / 2)];
    ctx.save();
    PICanvasEngine.configureContext(ctx);
    // コンテンツ境界（破線）
    ctx.strokeStyle = '#7c6ff7';
    ctx.lineWidth = 1 / z;
    ctx.setLineDash([4 / z, 3 / z]);
    ctx.beginPath();
    ctx.moveTo(corners[0].x, corners[0].y);
    for (let i = 1; i < 4; i++) ctx.lineTo(corners[i].x, corners[i].y);
    ctx.closePath();
    ctx.stroke();
    ctx.setLineDash([]);
    if (layer.locked) { ctx.restore(); return; }
    // 回転ハンドルへの脚と丸ハンドル
    const top = pt(0, -g.eh / 2);
    const h = this.rotHandlePos(layer);
    ctx.beginPath(); ctx.moveTo(top.x, top.y); ctx.lineTo(h.x, h.y); ctx.stroke();
    ctx.beginPath(); ctx.arc(h.x, h.y, 6 / z, 0, Math.PI * 2);
    ctx.fillStyle = '#7c6ff7'; ctx.fill();
    ctx.lineWidth = 1.5 / z; ctx.strokeStyle = '#fff'; ctx.stroke();
    // コーナーの拡縮ハンドル（ドラッグで文字サイズ変更）
    const hs = 5 / z;
    ctx.fillStyle = '#ffffff';
    ctx.strokeStyle = '#7c6ff7';
    ctx.lineWidth = 1.5 / z;
    corners.forEach(p => {
      ctx.beginPath();
      ctx.rect(p.x - hs, p.y - hs, hs * 2, hs * 2);
      ctx.fill(); ctx.stroke();
    });
    // 回転中は角度を表示
    if (this.rotating) {
      const deg = Math.round(((layer.rotation || 0) * 180 / Math.PI) % 360);
      const label = ((deg % 360) + 360) % 360 + '°';
      const fontPx = 12 / z;
      ctx.font = fontPx + 'px sans-serif';
      const tw = ctx.measureText(label).width;
      const pad = 4 / z;
      ctx.fillStyle = 'rgba(20,20,32,0.85)';
      ctx.fillRect(h.x + 10 / z, h.y - fontPx / 2 - pad, tw + pad * 2, fontPx + pad * 2);
      ctx.fillStyle = '#fff';
      ctx.textBaseline = 'top';
      ctx.fillText(label, h.x + 10 / z + pad, h.y - fontPx / 2);
    }
    ctx.restore();
  }

  // 編集ボックスはキャンバス座標系にそのまま置く（＝ズームと一緒に拡縮）。
  // フォント・縁取り・影・背景プレート・縦書きをCSSで再現し、
  // 確定前から実際の見た目（WYSIWYG）で入力できる。
  showEditBox() {
    this.isEditing = true;
    PICanvasEngine.clearOverlay(); // 編集中はハンドル類を消す
    PIEventBus.emit('text:overlay-active', true);

    // 既存レイヤーの編集中は、二重表示を避けるためレイヤーを一時的に隠す
    if (this.editingLayer && this.editingLayer.visible) {
      this._hiddenLayer = this.editingLayer;
      this.editingLayer.visible = false;
      PILayerManager.requestRender();
    }

    const overlay = document.getElementById('text-edit-overlay');
    overlay.classList.remove('hidden');
    overlay.innerHTML = '';
    overlay.style.left = this.textData.x + 'px';
    overlay.style.top = this.textData.y + 'px';
    // 回転したテキストは概ね同じ見た目になるよう箱ごと回す
    const rot = (this.editingLayer && this.editingLayer.rotation) || 0;
    overlay.style.transformOrigin = '50% 50%';
    overlay.style.transform = rot ? 'rotate(' + rot + 'rad)' : '';

    const wrapper = document.createElement('div');
    wrapper.style.cssText = 'display:flex;flex-direction:column;gap:4px;align-items:flex-start;';
    wrapper.addEventListener('mousedown', (ev) => ev.stopPropagation());
    wrapper.addEventListener('mouseup', (ev) => ev.stopPropagation());
    wrapper.addEventListener('click', (ev) => ev.stopPropagation());
    wrapper.addEventListener('dblclick', (ev) => ev.stopPropagation());
    wrapper.addEventListener('wheel', (ev) => ev.stopPropagation());

    const ta = document.createElement('textarea');
    ta.className = 'text-edit-input';
    ta.value = this.textData.text;
    ta.placeholder = 'テキストを入力...';
    ta.wrap = 'off';
    ta.rows = 1;
    ta.style.cssText = 'display:block;min-width:24px;min-height:1.2em;max-width:none;max-height:none;' +
      'border:none;outline:1.5px dashed #7c6ff7;outline-offset:2px;resize:none;overflow:hidden;' +
      'user-select:text;-webkit-user-select:text;cursor:text;caret-color:#7c6ff7;field-sizing:content;';
    this._activeTextarea = ta;
    this.styleEditBox(ta);

    ta.addEventListener('keydown', (ev) => {
      ev.stopPropagation();
      if (ev.key === 'Escape') { this.cancelEdit(); }
      if (ev.key === 'Enter' && ev.ctrlKey) { this.confirmEdit(ta.value); }
    });
    ta.addEventListener('keyup', (ev) => ev.stopPropagation());
    ta.addEventListener('keypress', (ev) => ev.stopPropagation());
    ta.addEventListener('input', () => this.autosizeEditBox(ta));

    const btnRow = document.createElement('div');
    this._btnRow = btnRow;
    btnRow.style.cssText = 'display:flex;gap:6px;margin-top:6px;';
    this.applyBtnRowScale();

    const confirmBtn = document.createElement('button');
    confirmBtn.textContent = '確定 (Ctrl+Enter)';
    confirmBtn.style.cssText = 'padding:5px 14px;background:#7c6ff7;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:12px;font-weight:600;white-space:nowrap;';
    confirmBtn.addEventListener('click', (ev) => {
      ev.stopPropagation();
      this.confirmEdit(ta.value);
    });

    const cancelBtn = document.createElement('button');
    cancelBtn.textContent = 'キャンセル';
    cancelBtn.style.cssText = 'padding:5px 14px;background:#555;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:12px;white-space:nowrap;';
    cancelBtn.addEventListener('click', (ev) => {
      ev.stopPropagation();
      this.cancelEdit();
    });

    btnRow.appendChild(confirmBtn);
    btnRow.appendChild(cancelBtn);

    wrapper.appendChild(ta);
    wrapper.appendChild(btnRow);
    overlay.appendChild(wrapper);

    // ズーム変更時も操作ボタンは常に画面上で同じ大きさに保つ
    if (!this._zoomFollow) {
      this._zoomFollow = () => {
        if (!this.isEditing) return;
        this.applyBtnRowScale();
      };
      PIEventBus.on('canvas:zoom-changed', this._zoomFollow);
    }

    requestAnimationFrame(() => {
      this.autosizeEditBox(ta);
      ta.focus();
      ta.setSelectionRange(ta.value.length, ta.value.length);
    });
    PIEventBus.emit('tool:properties-changed'); // パネルを「入力中」表示へ
  }

  // 入力中のスタイル（textData）をCSSへ反映する＝WYSIWYGの中核。
  // レンダラー(renderTextLayer)の余白計算と揃えて位置ずれを抑える。
  styleEditBox(ta) {
    const td = this.textData;
    const platePad = td.bgEnabled ? Math.max(6, Math.round(td.size * 0.3)) : 0;
    const padX = (td.strokeWidth || 0) * 2 + 4 + platePad;
    const padY = (td.strokeWidth || 0) * 2 + 4 + platePad;
    ta.style.fontFamily = '"' + td.fontFamily + '", sans-serif';
    ta.style.fontSize = td.size + 'px';
    ta.style.fontWeight = td.bold ? 'bold' : 'normal';
    ta.style.fontStyle = td.italic ? 'italic' : 'normal';
    ta.style.color = td.color || '#000';
    ta.style.lineHeight = String(td.lineHeight || 1.3);
    ta.style.letterSpacing = (td.letterSpacing || 0) + 'px';
    ta.style.textAlign = td.align || 'left';
    ta.style.writingMode = td.vertical ? 'vertical-rl' : 'horizontal-tb';
    const deco = [];
    if (td.underline) deco.push('underline');
    if (td.strikethrough) deco.push('line-through');
    ta.style.textDecoration = deco.length ? deco.join(' ') : 'none';
    // Canvasの縁取りは「文字の背面に lineWidth=太さ×2」で描かれる。
    // paint-order対応ブラウザなら同じ見た目（背面ストローク）を再現できる。
    if (this._paintOrder === undefined) {
      this._paintOrder = !!(window.CSS && CSS.supports && CSS.supports('paint-order', 'stroke'));
    }
    if (td.strokeWidth > 0) {
      const cssSw = this._paintOrder ? td.strokeWidth * 2 : td.strokeWidth;
      ta.style.webkitTextStroke = cssSw + 'px ' + (td.strokeColor || '#fff');
      ta.style.paintOrder = this._paintOrder ? 'stroke fill' : '';
    } else {
      ta.style.webkitTextStroke = '';
      ta.style.paintOrder = '';
    }
    const hasShadow = (td.shadowBlur > 0 || td.shadowOffsetX || td.shadowOffsetY);
    ta.style.textShadow = hasShadow
      ? (td.shadowOffsetX || 0) + 'px ' + (td.shadowOffsetY || 0) + 'px ' + (td.shadowBlur || 0) + 'px ' + (td.shadowColor || '#000')
      : 'none';
    ta.style.background = td.bgEnabled ? (td.bgColor || '#ffffff') : 'transparent';
    ta.style.borderRadius = (td.bgEnabled ? Math.min(16, Math.round(td.size * 0.25)) : 4) + 'px';
    ta.style.padding = padY + 'px ' + padX + 'px';
    this.autosizeEditBox(ta);
  }

  // field-sizing: content 非対応ブラウザ用のフォールバック
  autosizeEditBox(ta) {
    if (this._fieldSizing === undefined) {
      this._fieldSizing = !!(window.CSS && CSS.supports && CSS.supports('field-sizing', 'content'));
    }
    if (this._fieldSizing) return;
    ta.style.width = 'auto';
    ta.style.height = 'auto';
    ta.style.width = Math.max(24, ta.scrollWidth) + 'px';
    ta.style.height = Math.max(24, ta.scrollHeight) + 'px';
  }

  // 確定/キャンセルボタンはズームに関係なく常に読める大きさで表示する
  applyBtnRowScale() {
    if (!this._btnRow) return;
    const zoom = PICanvasEngine.getZoom() || 1;
    this._btnRow.style.transformOrigin = '0 0';
    this._btnRow.style.transform = 'scale(' + (1 / zoom) + ')';
  }

  confirmEdit(text) {
    if (!text || !text.trim()) { this.cancelEdit(); return; }
    this.textData.text = text;

    if (this.editingLayer) {
      this.editingLayer.textData = { ...this.textData };
      this.editingLayer.name = text.substring(0, 10);
      PILayerManager.renderTextLayer(this.editingLayer);
    } else {
      PILayerManager.addTextLayer({ ...this.textData });
    }

    this.hideEditBox();
    this._cr = null;
    PILayerManager.requestRender();
    PIHistoryManager.push('テキスト');
    PIEventBus.emit('tool:properties-changed');
    this.redrawHandles();
  }

  cancelEdit() {
    this.hideEditBox();
    PIEventBus.emit('tool:properties-changed');
    this.redrawHandles();
  }

  hideEditBox() {
    this.isEditing = false;
    this._activeTextarea = null;
    this._btnRow = null;
    // 編集のために隠していたレイヤーを戻す
    if (this._hiddenLayer) {
      this._hiddenLayer.visible = true;
      this._hiddenLayer = null;
      PILayerManager.requestRender();
    }
    PIEventBus.emit('text:overlay-active', false);
    const overlay = document.getElementById('text-edit-overlay');
    if (overlay) {
      overlay.classList.add('hidden');
      overlay.innerHTML = '';
      overlay.style.transform = '';
    }
  }

  // 選択中テキストレイヤー（編集対象）。無ければ次に作る新規テキスト設定を対象にする。
  targetLayer() {
    const active = PILayerManager.getActive();
    return (active && active.type === 'text') ? active : null;
  }

  applyEdit(label) {
    if (this.isEditing) {
      // 入力中はスタイルを編集ボックスへライブ反映し、レイヤーへは確定時にまとめて書き込む
      if (this._activeTextarea) this.styleEditBox(this._activeTextarea);
      return;
    }
    const layer = this.targetLayer();
    if (layer) {
      layer.name = (layer.textData.text || '').substring(0, 10);
      PILayerManager.renderTextLayer(layer);
      this._cr = null; // 再レンダリングでコンテンツ境界が変わる
      PILayerManager.requestRender();
      if (label) PIHistoryManager.pushDebounced(label);
      this.redrawHandles();
    }
  }

  applyPreset(preset) {
    const layer = this.targetLayer();
    const target = this.isEditing ? this.textData : (layer ? layer.textData : this.textData);
    // 前のプリセットの縁取り/影が残らないよう、基本状態へ戻してから適用する
    const base = {
      bold: false, italic: false, underline: false, strikethrough: false,
      strokeWidth: 0, shadowBlur: 0, shadowOffsetX: 0, shadowOffsetY: 0
    };
    const style = { ...preset };
    delete style.name;
    Object.assign(target, base, style);
    this.applyEdit('テキスト書式');
    PIEventBus.emit('tool:properties-changed');
  }

  getProperties() {
    const self = this;
    const fonts = PIFontLoader.getAvailableFonts();
    const allFonts = [...fonts.system, ...fonts.google];
    const layer = self.targetLayer();
    // 入力中はプロパティ変更を編集ボックスへライブ反映する（確定時にレイヤーへ書き込み）
    const td = self.isEditing ? self.textData : (layer ? layer.textData : self.textData);
    const set = (mut, label) => { mut(); self.applyEdit(label); };
    const fields = [
        {
          type: 'select', label: 'フォント', key: 'fontFamily', value: td.fontFamily,
          options: allFonts.map(f => ({ value: f, label: f })),
          onChange: (v) => set(() => { td.fontFamily = v; if (fonts.google.includes(v)) PIFontLoader.loadGoogleFont(v); }, 'フォント変更')
        },
        { type: 'slider', label: 'サイズ', key: 'size', value: td.size, min: 8, max: 400, onChange: (v) => set(() => { td.size = parseInt(v); }, 'サイズ変更') },
        { type: 'color', label: '色', key: 'color', value: td.color, onChange: (v) => set(() => { td.color = v; }, '色変更') },
        {
          type: 'toggle-group', label: 'スタイル', key: 'style',
          toggles: [
            { key: 'bold', label: 'B', active: td.bold, onChange: (v) => set(() => { td.bold = v; }, '太字') },
            { key: 'italic', label: 'I', active: td.italic, onChange: (v) => set(() => { td.italic = v; }, '斜体') },
            { key: 'underline', label: 'U', active: td.underline, onChange: (v) => set(() => { td.underline = v; }, '下線') },
            { key: 'strikethrough', label: 'S', active: td.strikethrough, onChange: (v) => set(() => { td.strikethrough = v; }, '取り消し線') }
          ]
        },
        {
          type: 'button-group', label: '配置', key: 'align',
          buttons: [
            { label: '左', active: td.align === 'left', onClick: () => set(() => { td.align = 'left'; }, '配置') },
            { label: '中', active: td.align === 'center', onClick: () => set(() => { td.align = 'center'; }, '配置') },
            { label: '右', active: td.align === 'right', onClick: () => set(() => { td.align = 'right'; }, '配置') }
          ]
        },
        {
          type: 'button-group', label: '文字方向', key: 'vertical',
          buttons: [
            { label: '横書き', active: !td.vertical, onClick: () => set(() => { td.vertical = false; }, '文字方向') },
            { label: '縦書き', active: !!td.vertical, onClick: () => set(() => { td.vertical = true; }, '文字方向') }
          ]
        },
        { type: 'checkbox', label: '背景プレート', key: 'bgEnabled', value: !!td.bgEnabled, onChange: (v) => set(() => { td.bgEnabled = v; }, 'テキスト背景') },
        { type: 'color', label: '背景色', key: 'bgColor', value: td.bgColor || '#ffffff', onChange: (v) => set(() => { td.bgColor = v; td.bgEnabled = true; }, 'テキスト背景色') },
        { type: 'slider', label: '行間', key: 'lineHeight', value: td.lineHeight * 100, min: 80, max: 300, unit: '%', onChange: (v) => set(() => { td.lineHeight = parseInt(v) / 100; }, '行間'), advancedOnly: true },
        { type: 'color', label: '縁取り色', key: 'strokeColor', value: td.strokeColor, onChange: (v) => set(() => { td.strokeColor = v; }, '縁取り色'), advancedOnly: true },
        { type: 'slider', label: '縁取り太さ', key: 'strokeWidth', value: td.strokeWidth, min: 0, max: 20, onChange: (v) => set(() => { td.strokeWidth = parseInt(v); }, '縁取り'), advancedOnly: true },
        { type: 'color', label: '影の色', key: 'shadowColor', value: td.shadowColor, onChange: (v) => set(() => { td.shadowColor = v; }, '影色'), advancedOnly: true },
        { type: 'slider', label: '影ぼかし', key: 'shadowBlur', value: td.shadowBlur, min: 0, max: 30, onChange: (v) => set(() => { td.shadowBlur = parseInt(v); if (td.shadowBlur > 0 && !td.shadowOffsetX && !td.shadowOffsetY) { td.shadowOffsetX = 2; td.shadowOffsetY = 2; } }, '影'), advancedOnly: true },
        {
          type: 'preset-list', label: 'プリセット', key: 'presets',
          presets: self.presets.map(p => ({ label: p.name, onClick: () => self.applyPreset(p) }))
        }
      ];
    if (layer && !self.isEditing) {
      // 内容の編集はダブルクリックでも開ける（発見性のためボタンも用意）
      fields.unshift({
        type: 'button', label: '✎ テキストを編集（ダブルクリック）', key: 'edit',
        onClick: () => self.beginEditLayer(layer)
      });
      const rotDeg = ((Math.round((layer.rotation || 0) * 180 / Math.PI) % 360) + 360) % 360;
      fields.splice(6, 0, {
        type: 'slider', label: '回転', key: 'rotation', value: rotDeg, min: 0, max: 360, unit: '°',
        onChange: (v) => {
          layer.rotation = parseInt(v) * Math.PI / 180;
          PILayerManager.requestRender();
          self.redrawHandles();
          PIHistoryManager.pushDebounced('テキスト回転');
        }
      });
    }
    const title = self.isEditing ? 'テキスト（入力中・ライブ反映）' : (layer ? 'テキスト（選択中）' : 'テキスト（新規）');
    return { title, fields };
  }
})();
