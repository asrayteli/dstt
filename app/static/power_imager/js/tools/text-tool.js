/* PowerImager — TextTool: テキスト配置・編集 */
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
    this.textData = {
      text: '', fontFamily: 'sans-serif', size: 32,
      color: '#000000', bold: false, italic: false,
      underline: false, strikethrough: false,
      align: 'left', lineHeight: 1.3, letterSpacing: 0,
      strokeColor: '#ffffff', strokeWidth: 0,
      shadowColor: '#000000', shadowOffsetX: 2, shadowOffsetY: 2, shadowBlur: 4,
      rotation: 0, x: 0, y: 0
    };
    this.presets = [
      { name: '赤い警告テキスト', color: '#ff0000', bold: true, size: 40, strokeColor: '#ffffff', strokeWidth: 3 },
      { name: '白縁取り見出し', color: '#ffffff', bold: true, size: 48, strokeColor: '#000000', strokeWidth: 4 },
      { name: 'シンプル黒', color: '#000000', bold: false, size: 24, strokeWidth: 0 },
      { name: '影付きタイトル', color: '#ffffff', bold: true, size: 36, strokeWidth: 0, shadowColor: '#000000', shadowOffsetX: 3, shadowOffsetY: 3, shadowBlur: 6 }
    ];
  }

  deactivate() {
    super.deactivate();
    this.pendingDragLayer = null;
    this.draggingLayer = false;
    this.cancelEdit();
  }

  onMouseDown(e) {
    if (this.isEditing) return;

    const existingTextLayer = this.findTextLayerAt(e.canvasX, e.canvasY);
    if (existingTextLayer) {
      const idx = PILayerManager.getAll().indexOf(existingTextLayer);
      if (idx >= 0) PILayerManager.setActiveIndex(idx);
      this.pendingDragLayer = existingTextLayer;
      this.draggingLayer = false;
      this.dragStartX = e.canvasX;
      this.dragStartY = e.canvasY;
      this.layerStartX = existingTextLayer.x;
      this.layerStartY = existingTextLayer.y;
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

    if (this.pendingDragLayer) {
      const dx = e.canvasX - this.dragStartX;
      const dy = e.canvasY - this.dragStartY;
      if (!this.draggingLayer && Math.hypot(dx, dy) >= this.dragThreshold) {
        this.draggingLayer = true;
        const vp = PICanvasEngine.getViewport();
        if (vp) vp.style.cursor = 'move';
      }
      if (this.draggingLayer) {
        this.moveTextLayer(this.pendingDragLayer, this.layerStartX + dx, this.layerStartY + dy);
      }
      return;
    }

    const vp = PICanvasEngine.getViewport();
    if (vp) vp.style.cursor = this.findTextLayerAt(e.canvasX, e.canvasY) ? 'move' : this.cursor;
  }

  onMouseUp(e) {
    if (!this.pendingDragLayer) return;
    const layer = this.pendingDragLayer;
    this.pendingDragLayer = null;

    if (this.draggingLayer) {
      this.draggingLayer = false;
      this.moveTextLayer(layer, layer.x, layer.y, true);
      PIHistoryManager.push('テキスト移動');
      PIEventBus.emit('tool:properties-changed');
      const vp = PICanvasEngine.getViewport();
      if (vp) vp.style.cursor = this.findTextLayerAt(e.canvasX, e.canvasY) ? 'move' : this.cursor;
      return;
    }

    this.editingLayer = layer;
    this.textData = { ...layer.textData, x: layer.x, y: layer.y };
    this.showEditBox();
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

  showEditBox() {
    this.isEditing = true;
    PIEventBus.emit('text:overlay-active', true);

    const overlay = document.getElementById('text-edit-overlay');
    overlay.classList.remove('hidden');
    overlay.innerHTML = '';

    const pos = PICanvasEngine.canvasToViewport(this.textData.x, this.textData.y);
    const vpRect = PICanvasEngine.getViewport().getBoundingClientRect();
    overlay.style.left = (pos.x - vpRect.left) + 'px';
    overlay.style.top = (pos.y - vpRect.top) + 'px';

    const wrapper = document.createElement('div');
    wrapper.style.cssText = 'display:flex;flex-direction:column;gap:4px;';
    wrapper.addEventListener('mousedown', (ev) => ev.stopPropagation());
    wrapper.addEventListener('mouseup', (ev) => ev.stopPropagation());
    wrapper.addEventListener('click', (ev) => ev.stopPropagation());

    const ta = document.createElement('textarea');
    ta.className = 'text-edit-input';
    ta.value = this.textData.text;
    ta.placeholder = 'テキストを入力...';
    const fontSize = Math.max(12, Math.min(this.textData.size * PICanvasEngine.getZoom(), 48));
    ta.style.cssText = 'min-width:220px;min-height:60px;max-width:500px;max-height:300px;' +
      'background:rgba(255,255,255,0.97);border:2px solid #7c6ff7;border-radius:6px;' +
      'padding:8px;font-size:' + fontSize + 'px;font-family:"' + this.textData.fontFamily + '",sans-serif;' +
      'color:' + this.textData.color + ';resize:both;outline:none;' +
      'user-select:text;-webkit-user-select:text;cursor:text;line-height:1.4;';
    if (this.textData.bold) ta.style.fontWeight = 'bold';
    if (this.textData.italic) ta.style.fontStyle = 'italic';

    ta.addEventListener('keydown', (ev) => {
      ev.stopPropagation();
      if (ev.key === 'Escape') { this.cancelEdit(); }
      if (ev.key === 'Enter' && ev.ctrlKey) { this.confirmEdit(ta.value); }
    });
    ta.addEventListener('keyup', (ev) => ev.stopPropagation());
    ta.addEventListener('keypress', (ev) => ev.stopPropagation());

    const btnRow = document.createElement('div');
    btnRow.style.cssText = 'display:flex;gap:6px;';

    const confirmBtn = document.createElement('button');
    confirmBtn.textContent = '確定 (Ctrl+Enter)';
    confirmBtn.style.cssText = 'padding:5px 14px;background:#7c6ff7;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:12px;font-weight:600;';
    confirmBtn.addEventListener('click', (ev) => {
      ev.stopPropagation();
      this.confirmEdit(ta.value);
    });

    const cancelBtn = document.createElement('button');
    cancelBtn.textContent = 'キャンセル';
    cancelBtn.style.cssText = 'padding:5px 14px;background:#555;color:#fff;border:none;border-radius:4px;cursor:pointer;font-size:12px;';
    cancelBtn.addEventListener('click', (ev) => {
      ev.stopPropagation();
      this.cancelEdit();
    });

    btnRow.appendChild(confirmBtn);
    btnRow.appendChild(cancelBtn);

    wrapper.appendChild(ta);
    wrapper.appendChild(btnRow);
    overlay.appendChild(wrapper);

    requestAnimationFrame(() => {
      ta.focus();
      ta.setSelectionRange(ta.value.length, ta.value.length);
    });
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
    PILayerManager.requestRender();
    PIHistoryManager.push('テキスト');
    PIEventBus.emit('tool:properties-changed');
  }

  cancelEdit() {
    this.hideEditBox();
  }

  hideEditBox() {
    this.isEditing = false;
    PIEventBus.emit('text:overlay-active', false);
    const overlay = document.getElementById('text-edit-overlay');
    if (overlay) {
      overlay.classList.add('hidden');
      overlay.innerHTML = '';
    }
  }

  // 選択中テキストレイヤー（編集対象）。無ければ次に作る新規テキスト設定を対象にする。
  targetLayer() {
    const active = PILayerManager.getActive();
    return (active && active.type === 'text') ? active : null;
  }

  applyEdit(label) {
    const layer = this.targetLayer();
    if (layer) {
      layer.name = (layer.textData.text || '').substring(0, 10);
      PILayerManager.renderTextLayer(layer);
      PILayerManager.requestRender();
      if (label) PIHistoryManager.pushDebounced(label);
    }
  }

  applyPreset(preset) {
    const layer = this.targetLayer();
    const target = layer ? layer.textData : this.textData;
    Object.assign(target, preset);
    this.applyEdit('テキスト書式');
    PIEventBus.emit('tool:properties-changed');
  }

  getProperties() {
    const self = this;
    const fonts = PIFontLoader.getAvailableFonts();
    const allFonts = [...fonts.system, ...fonts.google];
    const layer = self.targetLayer();
    const td = layer ? layer.textData : self.textData;
    const set = (mut, label) => { mut(); self.applyEdit(label); };
    return {
      title: layer ? 'テキスト（編集中）' : 'テキスト（新規）',
      fields: [
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
            { key: 'underline', label: 'U', active: td.underline, onChange: (v) => set(() => { td.underline = v; }, '下線') }
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
        { type: 'slider', label: '行間', key: 'lineHeight', value: td.lineHeight * 100, min: 80, max: 300, unit: '%', onChange: (v) => set(() => { td.lineHeight = parseInt(v) / 100; }, '行間'), advancedOnly: true },
        { type: 'color', label: '縁取り色', key: 'strokeColor', value: td.strokeColor, onChange: (v) => set(() => { td.strokeColor = v; }, '縁取り色'), advancedOnly: true },
        { type: 'slider', label: '縁取り太さ', key: 'strokeWidth', value: td.strokeWidth, min: 0, max: 20, onChange: (v) => set(() => { td.strokeWidth = parseInt(v); }, '縁取り'), advancedOnly: true },
        { type: 'color', label: '影の色', key: 'shadowColor', value: td.shadowColor, onChange: (v) => set(() => { td.shadowColor = v; }, '影色'), advancedOnly: true },
        { type: 'slider', label: '影ぼかし', key: 'shadowBlur', value: td.shadowBlur, min: 0, max: 30, onChange: (v) => set(() => { td.shadowBlur = parseInt(v); }, '影'), advancedOnly: true },
        {
          type: 'preset-list', label: 'プリセット', key: 'presets',
          presets: self.presets.map(p => ({ label: p.name, onClick: () => self.applyPreset(p) }))
        }
      ]
    };
  }
})();
