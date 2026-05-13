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
        if (PIMathUtils.pointInRect(x, y, l.x, l.y, l.canvas.width, l.canvas.height)) {
          return l;
        }
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

  applyPreset(preset) {
    Object.assign(this.textData, preset);
    PIEventBus.emit('tool:properties-changed');
  }

  getProperties() {
    const self = this;
    const fonts = PIFontLoader.getAvailableFonts();
    const allFonts = [...fonts.system, ...fonts.google];
    return {
      title: 'テキスト',
      fields: [
        {
          type: 'select', label: 'フォント', key: 'fontFamily', value: self.textData.fontFamily,
          options: allFonts.map(f => ({ value: f, label: f })),
          onChange: (v) => {
            self.textData.fontFamily = v;
            if (fonts.google.includes(v)) PIFontLoader.loadGoogleFont(v);
          }
        },
        { type: 'slider', label: 'サイズ', key: 'size', value: self.textData.size, min: 8, max: 200, onChange: (v) => { self.textData.size = parseInt(v); } },
        { type: 'color', label: '色', key: 'color', value: self.textData.color, onChange: (v) => { self.textData.color = v; } },
        {
          type: 'toggle-group', label: 'スタイル', key: 'style',
          toggles: [
            { key: 'bold', label: 'B', active: self.textData.bold, onChange: (v) => { self.textData.bold = v; } },
            { key: 'italic', label: 'I', active: self.textData.italic, onChange: (v) => { self.textData.italic = v; } },
            { key: 'underline', label: 'U', active: self.textData.underline, onChange: (v) => { self.textData.underline = v; } }
          ]
        },
        {
          type: 'button-group', label: '配置', key: 'align',
          buttons: [
            { label: '左', active: self.textData.align === 'left', onClick: () => { self.textData.align = 'left'; } },
            { label: '中', active: self.textData.align === 'center', onClick: () => { self.textData.align = 'center'; } },
            { label: '右', active: self.textData.align === 'right', onClick: () => { self.textData.align = 'right'; } }
          ]
        },
        { type: 'slider', label: '行間', key: 'lineHeight', value: self.textData.lineHeight * 100, min: 80, max: 300, unit: '%', onChange: (v) => { self.textData.lineHeight = parseInt(v) / 100; }, advancedOnly: true },
        { type: 'color', label: '縁取り色', key: 'strokeColor', value: self.textData.strokeColor, onChange: (v) => { self.textData.strokeColor = v; }, advancedOnly: true },
        { type: 'slider', label: '縁取り太さ', key: 'strokeWidth', value: self.textData.strokeWidth, min: 0, max: 20, onChange: (v) => { self.textData.strokeWidth = parseInt(v); }, advancedOnly: true },
        { type: 'color', label: '影の色', key: 'shadowColor', value: self.textData.shadowColor, onChange: (v) => { self.textData.shadowColor = v; }, advancedOnly: true },
        { type: 'slider', label: '影ぼかし', key: 'shadowBlur', value: self.textData.shadowBlur, min: 0, max: 30, onChange: (v) => { self.textData.shadowBlur = parseInt(v); }, advancedOnly: true },
        {
          type: 'preset-list', label: 'プリセット', key: 'presets',
          presets: self.presets.map(p => ({ label: p.name, onClick: () => self.applyPreset(p) }))
        }
      ]
    };
  }
})();
