/* PowerImager — Main Entry Point */
(function () {
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

    PILayerManager.addLayer('背景');
    const bg = PILayerManager.getActive();
    bg.ctx.fillStyle = '#ffffff';
    bg.ctx.fillRect(0, 0, PICanvasEngine.getCanvasSize().width, PICanvasEngine.getCanvasSize().height);
    PICanvasEngine.fitToViewport();
    PIHistoryManager.init();
    PILayerManager.requestRender();

    setupFileInput();
    setupDragDrop();
    setupClipboardPaste();
    setupKeyboardShortcuts();
    setupPanelCollapse();
    checkURLParams();
    showWelcome();
  }

  function setupFileInput() {
    const fileInput = document.getElementById('file-input');
    if (!fileInput) return;
    fileInput.addEventListener('change', async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      const img = await PIImageIO.loadFromFile(file);
      const size = PICanvasEngine.getCanvasSize();
      if (img.width > size.width || img.height > size.height) {
        PICanvasEngine.setCanvasSize(Math.max(img.width, size.width), Math.max(img.height, size.height));
      }
      PILayerManager.addImageLayer(img, file.name);
      PICanvasEngine.fitToViewport();
      PIHistoryManager.push('画像追加');
      PILayerManager.requestRender();
      fileInput.value = '';
    });
  }

  function setupDragDrop() {
    const vp = PICanvasEngine.getViewport();
    vp.addEventListener('dragover', (e) => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; });
    vp.addEventListener('drop', async (e) => {
      e.preventDefault();
      const file = e.dataTransfer.files[0];
      if (!file || !file.type.startsWith('image/')) return;
      const img = await PIImageIO.loadFromFile(file);
      PILayerManager.addImageLayer(img, file.name);
      PICanvasEngine.fitToViewport();
      PIHistoryManager.push('画像ドロップ');
      PILayerManager.requestRender();
    });
  }

  function setupClipboardPaste() {
    document.addEventListener('paste', async (e) => {
      const items = e.clipboardData && e.clipboardData.items;
      if (!items) return;
      for (const item of items) {
        if (item.type.startsWith('image/')) {
          e.preventDefault();
          const file = item.getAsFile();
          const img = await PIImageIO.loadFromFile(file);
          PILayerManager.addImageLayer(img, 'Pasted');
          PIHistoryManager.push('ペースト');
          PILayerManager.requestRender();
          return;
        }
      }
    });
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
        if (arrow) arrow.textContent = body.classList.contains('collapsed') ? '▸' : '▾';
      });
    });
  }

  function checkURLParams() {
    const params = new URLSearchParams(window.location.search);
    const projectId = params.get('project');
    if (projectId) {
      PIProjectStore.loadProjectData(projectId);
    }
    const w = params.get('w');
    const h = params.get('h');
    if (w && h) {
      PICanvasEngine.setCanvasSize(parseInt(w), parseInt(h));
      PICanvasEngine.fitToViewport();
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
