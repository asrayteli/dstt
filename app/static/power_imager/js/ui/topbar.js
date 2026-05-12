/* PowerImager — Topbar: トップバー・メニュー */
window.PITopbar = (function () {
  let openMenu = null;

  function init() {
    document.querySelectorAll('.topbar-menu-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        const menuId = btn.dataset.menu;
        toggleMenu(menuId);
      });
    });

    document.addEventListener('click', () => closeMenus());

    document.querySelectorAll('.topbar-dropdown-item[data-action]').forEach(item => {
      item.addEventListener('click', (e) => {
        e.stopPropagation();
        closeMenus();
        handleAction(item.dataset.action);
      });
    });

    const backBtn = document.getElementById('back-to-dstt');
    if (backBtn) {
      backBtn.addEventListener('click', () => {
        window.location.href = '/tools/power_imager';
      });
    }
  }

  function toggleMenu(menuId) {
    const menu = document.getElementById(menuId);
    if (!menu) return;
    const wasOpen = !menu.classList.contains('hidden');
    closeMenus();
    if (!wasOpen) {
      menu.classList.remove('hidden');
      openMenu = menuId;
    }
  }

  function closeMenus() {
    document.querySelectorAll('.topbar-dropdown').forEach(d => d.classList.add('hidden'));
    openMenu = null;
  }

  function handleAction(action) {
    switch (action) {
      case 'new': showNewCanvasDialog(); break;
      case 'open': document.getElementById('file-input').click(); break;
      case 'save': PIProjectStore.saveCurrentProject().then(() => PIEventBus.emit('toast', '保存しました')); break;
      case 'export': showExportDialog(); break;
      case 'undo': PIHistoryManager.undo(); break;
      case 'redo': PIHistoryManager.redo(); break;
      case 'copy': PIImageIO.copyToClipboard(); PIEventBus.emit('toast', 'クリップボードにコピーしました'); break;
      case 'paste': PIImageIO.loadFromClipboard().then(img => { if (img) { PILayerManager.addImageLayer(img, 'Pasted'); PIHistoryManager.push('ペースト'); PILayerManager.requestRender(); } }); break;
      case 'resize': showResizeDialog(); break;
      case 'flip-h': transformFlip('h'); break;
      case 'flip-v': transformFlip('v'); break;
      case 'rotate-cw': transformRotate(90); break;
      case 'rotate-ccw': transformRotate(-90); break;
      case 'brightness': PIBrightnessContrast.showDialog(); break;
      case 'hue': PIHueSaturation.showDialog(); break;
      case 'levels': PILevels.showDialog(); break;
      case 'color-balance': PIColorBalance.showDialog(); break;
      case 'blur': PIBlurFilter.showDialog(); break;
      case 'sharpen': PISharpenFilter.apply(); break;
      case 'mosaic': PIMosaicFilter.showDialog(); break;
      case 'noise': PINoiseFilter.apply(); break;
      case 'grayscale': PIFilterWorker.apply('grayscale'); break;
      case 'sepia': PIFilterWorker.apply('sepia'); break;
      case 'invert': PIFilterWorker.apply('invert'); break;
      case 'flatten': PILayerManager.flattenAll(); PIEventBus.emit('toast', 'レイヤーを統合しました'); break;
      case 'fit': PICanvasEngine.fitToViewport(); break;
      case 'zoom-in': PICanvasEngine.setZoom(PICanvasEngine.getZoom() * 1.25); break;
      case 'zoom-out': PICanvasEngine.setZoom(PICanvasEngine.getZoom() * 0.8); break;
      default: console.log('Unknown action:', action);
    }
  }

  function showNewCanvasDialog() {
    PIModal.show({
      title: '新規キャンバス',
      content: [
        { type: 'number', label: '幅 (px)', key: 'width', value: 800, min: 1, max: 8000 },
        { type: 'number', label: '高さ (px)', key: 'height', value: 600, min: 1, max: 8000 }
      ],
      sizePresets: [
        { label: 'HD (1280x720)', onClick: (v) => { v.width = 1280; v.height = 720; } },
        { label: 'Full HD (1920x1080)', onClick: (v) => { v.width = 1920; v.height = 1080; } },
        { label: '4K (3840x2160)', onClick: (v) => { v.width = 3840; v.height = 2160; } },
        { label: 'A4 横 (297x210mm)', onClick: (v) => { v.width = 3508; v.height = 2480; } },
        { label: 'A4 縦 (210x297mm)', onClick: (v) => { v.width = 2480; v.height = 3508; } },
        { label: 'Instagram (1080x1080)', onClick: (v) => { v.width = 1080; v.height = 1080; } },
        { label: 'Twitter Header (1500x500)', onClick: (v) => { v.width = 1500; v.height = 500; } }
      ],
      confirmLabel: '作成',
      onConfirm: (values) => {
        const w = parseInt(values.width) || 800;
        const h = parseInt(values.height) || 600;
        PILayerManager.clear();
        PICanvasEngine.setCanvasSize(w, h);
        PILayerManager.addLayer('背景');
        const bg = PILayerManager.getActive();
        bg.ctx.fillStyle = '#ffffff';
        bg.ctx.fillRect(0, 0, w, h);
        PICanvasEngine.fitToViewport();
        PIHistoryManager.clear();
        PIHistoryManager.push('新規キャンバス');
        PILayerManager.requestRender();
      }
    });
  }

  function showResizeDialog() {
    const size = PICanvasEngine.getCanvasSize();
    PIModal.show({
      title: 'キャンバスサイズ変更',
      content: [
        { type: 'number', label: '幅 (px)', key: 'width', value: size.width, min: 1, max: 8000 },
        { type: 'number', label: '高さ (px)', key: 'height', value: size.height, min: 1, max: 8000 }
      ],
      sizePresets: [
        { label: 'HD', onClick: (v) => { v.width = 1280; v.height = 720; } },
        { label: 'Full HD', onClick: (v) => { v.width = 1920; v.height = 1080; } },
        { label: '4K', onClick: (v) => { v.width = 3840; v.height = 2160; } }
      ],
      confirmLabel: '適用',
      onConfirm: (values) => {
        const w = parseInt(values.width) || size.width;
        const h = parseInt(values.height) || size.height;
        PILayerManager.getAll().forEach(layer => {
          const newCanvas = document.createElement('canvas');
          newCanvas.width = w; newCanvas.height = h;
          const nctx = newCanvas.getContext('2d');
          nctx.drawImage(layer.canvas, 0, 0);
          layer.canvas.width = w; layer.canvas.height = h;
          layer.ctx.drawImage(newCanvas, 0, 0);
        });
        PICanvasEngine.setCanvasSize(w, h);
        PICanvasEngine.fitToViewport();
        PIHistoryManager.push('サイズ変更');
        PILayerManager.requestRender();
      }
    });
  }

  function showExportDialog() {
    PIModal.show({
      title: 'エクスポート',
      content: [
        { type: 'text', label: 'ファイル名', key: 'filename', value: 'image' },
        {
          type: 'select', label: '形式', key: 'format', value: 'png',
          options: [
            { value: 'png', label: 'PNG' },
            { value: 'jpeg', label: 'JPEG' },
            { value: 'webp', label: 'WebP' }
          ]
        },
        { type: 'slider', label: '品質', key: 'quality', value: 92, min: 10, max: 100 }
      ],
      confirmLabel: 'ダウンロード',
      onConfirm: (values) => {
        const ext = values.format || 'png';
        const filename = (values.filename || 'image') + '.' + ext;
        PIImageIO.downloadCanvas(filename, ext, (values.quality || 92) / 100);
      }
    });
  }

  function transformFlip(dir) {
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const temp = document.createElement('canvas');
    temp.width = layer.canvas.width; temp.height = layer.canvas.height;
    const tctx = temp.getContext('2d');
    tctx.save();
    if (dir === 'h') {
      tctx.translate(temp.width, 0); tctx.scale(-1, 1);
    } else {
      tctx.translate(0, temp.height); tctx.scale(1, -1);
    }
    tctx.drawImage(layer.canvas, 0, 0);
    tctx.restore();
    layer.ctx.clearRect(0, 0, layer.canvas.width, layer.canvas.height);
    layer.ctx.drawImage(temp, 0, 0);
    PIHistoryManager.push('反転');
    PILayerManager.requestRender();
  }

  function transformRotate(deg) {
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const sw = layer.canvas.width, sh = layer.canvas.height;
    const isRight = Math.abs(deg) === 90;
    const nw = isRight ? sh : sw;
    const nh = isRight ? sw : sh;
    const temp = document.createElement('canvas');
    temp.width = nw; temp.height = nh;
    const tctx = temp.getContext('2d');
    tctx.save();
    tctx.translate(nw / 2, nh / 2);
    tctx.rotate(deg * Math.PI / 180);
    tctx.drawImage(layer.canvas, -sw / 2, -sh / 2);
    tctx.restore();
    layer.canvas.width = nw; layer.canvas.height = nh;
    layer.ctx.drawImage(temp, 0, 0);
    PIHistoryManager.push('回転');
    PILayerManager.requestRender();
  }

  return { init, handleAction };
})();
