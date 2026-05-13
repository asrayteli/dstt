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

    setupFileInput();
    setupDragDrop();
    setupClipboardPaste();
    setupKeyboardShortcuts();
    setupPanelCollapse();

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

  function setupKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') return;
      if (PIModal.isOpen()) return;

      if (e.ctrlKey && e.key === 'z') { e.preventDefault(); PIHistoryManager.undo(); }
      else if (e.ctrlKey && e.key === 'y') { e.preventDefault(); PIHistoryManager.redo(); }
      else if (e.ctrlKey && e.key === 's') { e.preventDefault(); PIProjectStore.saveCurrentProject().then(() => PIEventBus.emit('toast', '保存しました')); }
      else if (e.ctrlKey && e.key === 'c') { e.preventDefault(); PIImageIO.copyToClipboard(); }
      else if (e.key === 'v' && !e.ctrlKey) PIEventBus.emit('tool:switch', 'move');
      else if (e.key === 'm') PIEventBus.emit('tool:switch', 'select');
      else if (e.key === 'c' && !e.ctrlKey) PIEventBus.emit('tool:switch', 'crop');
      else if (e.key === 'b') PIEventBus.emit('tool:switch', 'brush');
      else if (e.key === 'e') PIEventBus.emit('tool:switch', 'eraser');
      else if (e.key === 't') PIEventBus.emit('tool:switch', 'text');
      else if (e.key === 'u') PIEventBus.emit('tool:switch', 'shape');
      else if (e.key === 'g') PIEventBus.emit('tool:switch', 'fill');
      else if (e.key === 'i') PIEventBus.emit('tool:switch', 'eyedropper');
      else if (e.key === 'Delete' || e.key === 'Backspace') {
        const layer = PILayerManager.getActive();
        if (layer && !layer.locked) {
          layer.ctx.clearRect(0, 0, layer.canvas.width, layer.canvas.height);
          PIHistoryManager.push('削除');
          PILayerManager.requestRender();
        }
      }
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
