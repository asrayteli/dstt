/* PowerImager - Main Entry Point */
(function () {
  const launchImageKey = 'dstt.powerImager.launchImage';

  async function boot() {
    PICanvasEngine.init();
    PILayerManager.init();
    await PIProjectStore.init();
    PIModal.init();
    PIToolbar.init();
    PITopbar.init();
    PIPropertyPanel.init();
    PILayerPanel.init();
    PIHistoryPanel.init();
    PIStatusbar.init();
    PIModeSwitcher.init();
    PITooltipGuide.init();
    PIBrushCursor.init();
    PIColorPanel.init();
    PIGuides.init();

    setupFileInput();
    setupDragDrop();
    setupClipboardPaste();
    setupKeyboardShortcuts();
    setupPanelCollapse();
    setupPanelResize();
    setupZoomControls();

    const startup = getStartupParams();
    if (startup.dpi) PICanvasEngine.setDpi(startup.dpi);

    if (startup.projectId) {
      const loaded = await PIProjectStore.loadProjectData(startup.projectId);
      if (!loaded) {
        createWhiteBackground();
        PICanvasEngine.fitToViewport();
        PIHistoryManager.init();
        PILayerManager.requestRender();
        PIEventBus.emit('toast', 'プロジェクトが見つからなかったため、新規キャンバスを開きました');
      }
    } else {
      if (startup.width && startup.height) PICanvasEngine.setCanvasSize(startup.width, startup.height);
      createWhiteBackground();
      PICanvasEngine.fitToViewport();
      PIHistoryManager.init();
      PILayerManager.requestRender();
      await loadLaunchImage();
    }

    showWelcome();
  }

  function getStartupParams() {
    const params = new URLSearchParams(window.location.search);
    return {
      projectId: params.get('project'),
      width: parseInt(params.get('w'), 10) || null,
      height: parseInt(params.get('h'), 10) || null,
      dpi: parseInt(params.get('dpi'), 10) || null
    };
  }

  function createWhiteBackground(name) {
    PILayerManager.clear();
    const size = PICanvasEngine.getCanvasSize();
    PILayerManager.addLayer(name || '背景');
    const bg = PILayerManager.getActive();
    bg.ctx.fillStyle = '#ffffff';
    bg.ctx.fillRect(0, 0, size.width, size.height);
  }

  function setupFileInput() {
    const fileInput = document.getElementById('file-input');
    if (!fileInput) return;
    fileInput.addEventListener('change', async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      try {
        const asset = await PIImageIO.loadImageAsset(file);
        await importImageAsset(asset, { promptDpi: true, historyLabel: '画像追加' });
      } catch (error) {
        PIEventBus.emit('toast', error.message || '画像を読み込めませんでした');
      } finally {
        fileInput.value = '';
      }
    });
  }

  function setupDragDrop() {
    const vp = PICanvasEngine.getViewport();
    vp.addEventListener('dragover', (e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; });
    vp.addEventListener('drop', async (e) => {
      e.preventDefault();
      const file = e.dataTransfer.files[0];
      if (!file || !file.type.startsWith('image/')) return;
      try {
        const asset = await PIImageIO.loadImageAsset(file);
        await importImageAsset(asset, { promptDpi: true, historyLabel: '画像ドロップ' });
      } catch (error) {
        PIEventBus.emit('toast', error.message || '画像を読み込めませんでした');
      }
    });
  }

  function setupClipboardPaste() {
    document.addEventListener('paste', async (e) => {
      const items = e.clipboardData && e.clipboardData.items;
      if (!items) return;
      for (const item of items) {
        if (item.type.startsWith('image/')) {
          e.preventDefault();
          try {
            const file = item.getAsFile();
            const img = await PIImageIO.loadFromFile(file);
            PILayerManager.addImageLayer(img, 'Pasted');
            PIHistoryManager.push('ペースト');
            PILayerManager.requestRender();
          } catch (error) {
            PIEventBus.emit('toast', error.message || '貼り付けに失敗しました');
          }
          return;
        }
      }
    });
  }

  function askImportDpi(asset) {
    const image = asset.image;
    const detected = asset.metadata && asset.metadata.dpi;
    const currentDpi = PICanvasEngine.getDpi();
    const dpiValue = detected ? Math.round((detected.x + detected.y) / 2) : currentDpi;
    const source = detected ? '検出DPI: ' + detected.x + ' x ' + detected.y : 'DPI情報が見つからないため現在値を使います';
    return new Promise((resolve) => {
      PIModal.show({
        title: '画像読み込み設定',
        description: (asset.metadata.name || '画像') + ' / ' + image.naturalWidth + ' x ' + image.naturalHeight + ' px / ' + source,
        content: [
          { type: 'number', label: 'このドキュメントのDPI', key: 'dpi', value: dpiValue, min: 1, max: 2400 }
        ],
        sizePresets: [
          { label: 'Web 96dpi', onClick: (v) => { v.dpi = 96; } },
          { label: '印刷 300dpi', onClick: (v) => { v.dpi = 300; } },
          { label: '高精細 600dpi', onClick: (v) => { v.dpi = 600; } }
        ],
        confirmLabel: '読み込む',
        onConfirm: (values) => resolve(parseInt(values.dpi, 10) || currentDpi),
        onCancel: () => resolve(null)
      });
    });
  }

  async function importImageAsset(asset, options) {
    const settings = options || {};
    if (settings.promptDpi) {
      const dpi = await askImportDpi(asset);
      if (!dpi) return null;
      PICanvasEngine.setDpi(dpi);
    } else if (asset.metadata && asset.metadata.dpi && settings.applyDetectedDpi) {
      PICanvasEngine.setDpi(Math.round((asset.metadata.dpi.x + asset.metadata.dpi.y) / 2));
    }

    const img = asset.image;
    const size = PICanvasEngine.getCanvasSize();
    if (img.width > size.width || img.height > size.height) {
      PICanvasEngine.setCanvasSize(Math.max(img.width, size.width), Math.max(img.height, size.height));
    }
    PILayerManager.addImageLayer(img, (asset.metadata && asset.metadata.name) || 'Imported Image');
    PICanvasEngine.fitToViewport();
    PIHistoryManager.push(settings.historyLabel || '画像読み込み');
    PILayerManager.requestRender();
    return img;
  }

  function adjustBrushSize(delta) {
    const tool = PIToolbar.getCurrentTool();
    if (!tool) return false;
    const apply = (cur, set) => {
      const next = Math.max(1, Math.min(300, cur + delta));
      set(next);
      PIEventBus.emit('brush:size-changed');
      PIEventBus.emit('tool:properties-changed');
    };
    if (tool === PIBrushTool) { apply(PIBrushTool.brushSize, v => PIBrushTool.brushSize = v); return true; }
    if (tool === PIEraserTool) { apply(PIEraserTool.eraserSize, v => PIEraserTool.eraserSize = v); return true; }
    if (tool === PIRetouchTool) { apply(PIRetouchTool.size, v => PIRetouchTool.size = v); return true; }
    return false;
  }

  function deleteSelectionOrLayer() {
    const layer = PILayerManager.getActive();
    if (!layer || layer.locked) return;
    if (PISelection.has()) {
      const ll = PISelection.getLayerLocalMask(layer);
      const ctx = layer.ctx;
      ctx.save();
      ctx.globalCompositeOperation = 'destination-out';
      ctx.drawImage(ll, 0, 0);
      ctx.restore();
      PIHistoryManager.push('選択範囲を削除');
    } else {
      layer.ctx.clearRect(0, 0, layer.canvas.width, layer.canvas.height);
      PIHistoryManager.push('削除');
    }
    PILayerManager.requestRender();
  }

  function swapColors() {
    const fg = document.getElementById('fg-color-input');
    const bg = document.getElementById('bg-color-input');
    const tmp = fg.value; fg.value = bg.value; bg.value = tmp;
  }

  function defaultColors() {
    document.getElementById('fg-color-input').value = '#000000';
    document.getElementById('bg-color-input').value = '#ffffff';
  }

  function setupKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;
      if (PIModal.isOpen()) return;

      const ctrl = e.ctrlKey || e.metaKey;

      // 切り抜きツールの確定/取り消し
      if (PIToolbar.getCurrentTool() === PICropTool) {
        if (e.key === 'Enter') { e.preventDefault(); PICropTool.applyCrop(); return; }
        if (e.key === 'Escape') { e.preventDefault(); PICropTool.cancelCrop(); return; }
      }

      if (ctrl && e.key.toLowerCase() === 'z') { e.preventDefault(); if (e.shiftKey) PIHistoryManager.redo(); else PIHistoryManager.undo(); }
      else if (ctrl && e.key.toLowerCase() === 'y') { e.preventDefault(); PIHistoryManager.redo(); }
      else if (ctrl && e.key.toLowerCase() === 's') { e.preventDefault(); PIProjectStore.saveCurrentProject().then(() => PIEventBus.emit('toast', '保存しました')); }
      else if (ctrl && e.key.toLowerCase() === 'p') { e.preventDefault(); PITopbar.handleAction('print'); }
      else if (ctrl && e.key.toLowerCase() === 'c') { e.preventDefault(); PIImageIO.copyToClipboard(); PIEventBus.emit('toast', 'コピーしました'); }
      else if (ctrl && e.key.toLowerCase() === 'a') { e.preventDefault(); PIEventBus.emit('tool:switch', 'select'); if (window.PISelectTool) PISelectTool.selectAll(); }
      else if (ctrl && e.key.toLowerCase() === 'd') { e.preventDefault(); if (window.PISelectTool) PISelectTool.clearSelection(); }
      else if (ctrl && e.key.toLowerCase() === 't') { e.preventDefault(); PIEventBus.emit('tool:switch', 'transform'); }
      else if (ctrl && (e.key === '0')) { e.preventDefault(); PICanvasEngine.fitToViewport(); }
      else if (ctrl && (e.key === '1')) { e.preventDefault(); PICanvasEngine.setZoom(1); }
      else if (ctrl && (e.key === '+' || e.key === '=' || e.key === ';')) { e.preventDefault(); PICanvasEngine.setZoom(PICanvasEngine.getZoom() * 1.25); }
      else if (ctrl && (e.key === '-' || e.key === '_')) { e.preventDefault(); PICanvasEngine.setZoom(PICanvasEngine.getZoom() * 0.8); }
      else if (!ctrl && (e.key === '[' || e.key === ']')) { if (adjustBrushSize(e.key === '[' ? -2 : 2)) e.preventDefault(); }
      else if (!ctrl && e.key === 'v') PIEventBus.emit('tool:switch', 'move');
      else if (!ctrl && e.key === 'm') PIEventBus.emit('tool:switch', 'select');
      else if (!ctrl && e.key === 'c') PIEventBus.emit('tool:switch', 'crop');
      else if (!ctrl && e.key === 'b') PIEventBus.emit('tool:switch', 'brush');
      else if (!ctrl && e.key === 'e') PIEventBus.emit('tool:switch', 'eraser');
      else if (!ctrl && e.key === 't') PIEventBus.emit('tool:switch', 'text');
      else if (!ctrl && e.key === 'u') PIEventBus.emit('tool:switch', 'shape');
      else if (!ctrl && e.key === 'g') PIEventBus.emit('tool:switch', 'fill');
      else if (!ctrl && e.key === 'i') PIEventBus.emit('tool:switch', 'eyedropper');
      else if (!ctrl && e.key === 'x') swapColors();
      else if (!ctrl && e.key === 'd') defaultColors();
      else if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); deleteSelectionOrLayer(); }
    });
  }

  function setupPanelCollapse() {
    document.querySelectorAll('.panel-header').forEach(header => {
      header.addEventListener('click', () => {
        const body = header.nextElementSibling;
        if (body) body.classList.toggle('collapsed');
        const arrow = header.querySelector('.collapse-arrow');
        if (arrow) arrow.textContent = body.classList.contains('collapsed') ? '▶' : '▼';
      });
    });
  }

  // 右パネルの幅をドラッグで可変。localStorageに保存。
  function setupPanelResize() {
    const panel = document.getElementById('pi-panel');
    if (!panel) return;
    const DEFAULT_W = 260, MIN_W = 210, MAX_W = 560;
    const saved = parseInt(localStorage.getItem('pi-panel-w'), 10);
    if (saved && saved >= MIN_W && saved <= MAX_W) document.documentElement.style.setProperty('--pi-panel-w', saved + 'px');
    const resizer = document.createElement('div');
    resizer.className = 'panel-resizer';
    panel.appendChild(resizer);
    let resizing = false;
    resizer.addEventListener('mousedown', (e) => { resizing = true; e.preventDefault(); document.body.style.cursor = 'ew-resize'; });
    window.addEventListener('mousemove', (e) => {
      if (!resizing) return;
      let w = window.innerWidth - e.clientX;
      w = Math.max(MIN_W, Math.min(MAX_W, w));
      document.documentElement.style.setProperty('--pi-panel-w', w + 'px');
    });
    window.addEventListener('mouseup', () => {
      if (!resizing) return;
      resizing = false; document.body.style.cursor = '';
      const cur = getComputedStyle(document.documentElement).getPropertyValue('--pi-panel-w').trim();
      localStorage.setItem('pi-panel-w', parseInt(cur, 10) || DEFAULT_W);
      PIEventBus.emit('canvas:zoom-changed', PICanvasEngine.getZoom());
    });
    resizer.addEventListener('dblclick', () => {
      document.documentElement.style.setProperty('--pi-panel-w', DEFAULT_W + 'px');
      localStorage.setItem('pi-panel-w', DEFAULT_W);
    });
  }

  // ステータスバーのズーム表示をクリック操作に対応（− / 数値=100% / ＋ / 全体）
  function setupZoomControls() {
    const zoomEl = document.getElementById('sb-zoom');
    const statusbar = document.getElementById('pi-statusbar');
    if (!zoomEl || !statusbar) return;
    const wrap = zoomEl.parentElement;
    const mk = (txt, title, fn) => { const b = document.createElement('button'); b.className = 'sb-zoom-btn'; b.textContent = txt; b.title = title; b.addEventListener('click', fn); return b; };
    const group = document.createElement('span');
    group.className = 'sb-zoom-group';
    group.append(
      mk('−', '縮小', () => PICanvasEngine.setZoom(PICanvasEngine.getZoom() * 0.8)),
      mk('100%', '等倍', () => PICanvasEngine.setZoom(1)),
      mk('＋', '拡大', () => PICanvasEngine.setZoom(PICanvasEngine.getZoom() * 1.25)),
      mk('⊞', '全体表示', () => PICanvasEngine.fitToViewport())
    );
    statusbar.insertBefore(group, wrap ? wrap.nextSibling : null);
  }

  async function loadLaunchImage() {
    let payload = null;
    try {
      const raw = sessionStorage.getItem(launchImageKey);
      sessionStorage.removeItem(launchImageKey);
      if (raw) payload = JSON.parse(raw);
    } catch (error) {
      payload = null;
    }
    if (!payload || !payload.dataURL) return;

    try {
      const asset = await PIImageIO.loadFromDataURL(payload.dataURL, {
        name: payload.name || 'Imported Image',
        type: payload.type || '',
        dpi: payload.dpi || null
      });
      await importImageAsset(asset, { promptDpi: true, historyLabel: '画像読み込み' });
    } catch (error) {
      PIEventBus.emit('toast', error.message || '画像を読み込めませんでした');
    }
  }

  function showWelcome() {
    const shown = localStorage.getItem('pi-welcome-shown');
    if (shown) return;
    const guide = document.getElementById('welcome-guide');
    if (!guide) return;
    guide.classList.remove('hidden');
    const startBtn = guide.querySelector('.welcome-start-btn');
    if (startBtn) startBtn.addEventListener('click', () => {
      guide.classList.add('hidden');
      localStorage.setItem('pi-welcome-shown', '1');
    });
    const skipBtn = guide.querySelector('.welcome-skip-btn');
    if (skipBtn) skipBtn.addEventListener('click', () => {
      guide.classList.add('hidden');
      localStorage.setItem('pi-welcome-shown', '1');
    });
  }

  window.addEventListener('DOMContentLoaded', boot);
})();
