/* PowerImager - Topbar */
window.PITopbar = (function () {
  let openMenu = null;
  let menuTooltip = null;
  const actionDescriptions = {
    'auto-contrast': 'RGB各チャンネルの明暗を広げ、眠い画像をすばやく引き締めます。',
    'auto-levels': '輝度レンジを自動で広げ、黒から白まで自然に使う補正です。',
    'white-balance': '画像全体の色かぶりを平均化し、白を白らしく整えます。',
    brightness: '明るさとコントラストを基本的に調整します。',
    hue: '色相、彩度、明度をまとめて調整します。',
    vibrance: '派手になりすぎないよう、低彩度の色を中心に鮮やかにします。',
    temperature: '寒色/暖色、グリーン/マゼンタの色かぶりを補正します。',
    levels: 'シャドウ、中間調、ハイライトを使って階調を補正します。',
    'color-balance': 'RGBの色バランスを直接押し引きします。',
    curves: 'シャドウ、ミッドトーン、ハイライトを曲線的に補正するPRO向け調整です。',
    exposure: '写真向けに露光量、ガンマ、オフセットを調整します。',
    'channel-mixer': 'RGBチャンネルを混ぜ替えるPRO向けの色分解ツールです。',
    threshold: '輝度を基準に白黒へ2値化します。',
    posterize: '階調数を減らしてポスター調にします。',
    blur: '画像を柔らかくぼかします。',
    sharpen: '輪郭を強調してくっきりさせます。',
    mosaic: '指定ブロック単位でモザイク化します。',
    noise: '中央値処理で細かなノイズを抑えます。',
    glow: '明るい部分をにじませ、柔らかい発光感を足します。',
    'edge-detect': '輪郭成分を抽出します。',
    'unsharp-mask': '半径、量、しきい値で制御するPRO向けシャープです。',
    'high-pass': '低周波を除き、輪郭や質感だけを抽出します。',
    emboss: '凹凸のあるエンボス風に変換します。',
    halftone: 'ドットで濃淡を表現する印刷風フィルタです。',
    vignette: '周辺光量を落として視線を中央へ誘導します。',
    grayscale: '白黒画像へ変換します。',
    sepia: 'セピア調の色味に変換します。',
    invert: 'RGBを反転します。'
  };

  function init() {
    document.querySelectorAll('.topbar-menu-btn').forEach(btn => {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        toggleMenu(btn.dataset.menu);
      });
    });

    document.addEventListener('click', () => closeMenus());

    document.querySelectorAll('.topbar-dropdown-item[data-action]').forEach(item => {
      item.addEventListener('click', (e) => {
        e.stopPropagation();
        closeMenus();
        handleAction(item.dataset.action);
      });
      item.addEventListener('mouseenter', () => showMenuTooltip(item));
      item.addEventListener('mousemove', () => positionMenuTooltip(item));
      item.addEventListener('mouseleave', hideMenuTooltip);
      const desc = actionDescriptions[item.dataset.action];
      if (desc) item.title = desc;
    });

    const backBtn = document.getElementById('back-to-dstt');
    if (backBtn) backBtn.addEventListener('click', () => { window.location.href = '/tools/power_imager'; });
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
    hideMenuTooltip();
    openMenu = null;
  }

  function ensureMenuTooltip() {
    if (menuTooltip) return menuTooltip;
    menuTooltip = document.createElement('div');
    menuTooltip.className = 'pi-menu-tooltip';
    document.body.appendChild(menuTooltip);
    return menuTooltip;
  }

  function showMenuTooltip(item) {
    const desc = actionDescriptions[item.dataset.action];
    if (!desc) return;
    const tip = ensureMenuTooltip();
    tip.textContent = desc;
    positionMenuTooltip(item);
    tip.classList.add('visible');
  }

  function positionMenuTooltip(item) {
    if (!menuTooltip) return;
    const rect = item.getBoundingClientRect();
    const left = Math.min(window.innerWidth - 280, rect.right + 8);
    const top = Math.min(window.innerHeight - 80, rect.top);
    menuTooltip.style.left = Math.max(8, left) + 'px';
    menuTooltip.style.top = Math.max(8, top) + 'px';
  }

  function hideMenuTooltip() {
    if (menuTooltip) menuTooltip.classList.remove('visible');
  }

  function handleAction(action) {
    if (action.indexOf('adj-layer-') === 0) { PIAdjustmentLayer.add(action.slice('adj-layer-'.length)); return; }
    switch (action) {
      case 'new': showNewCanvasDialog(); break;
      case 'open': document.getElementById('file-input').click(); break;
      case 'save': PIProjectStore.saveCurrentProject().then(() => PIEventBus.emit('toast', '保存しました')); break;
      case 'export': showExportDialog(); break;
      case 'print': showPrintDialog(); break;
      case 'document-dpi': showDocumentDpiDialog(); break;
      case 'trim-transparent': trimTransparentPixels(); break;
      case 'flatten': flattenToOneLayer(); break;
      case 'undo': PIHistoryManager.undo(); break;
      case 'redo': PIHistoryManager.redo(); break;
      case 'copy': PIImageIO.copyToClipboard(); PIEventBus.emit('toast', 'クリップボードにコピーしました'); break;
      case 'paste':
        PIImageIO.loadFromClipboard().then(img => {
          if (img) {
            PILayerManager.addImageLayer(img, 'Pasted');
            PIHistoryManager.push('ペースト');
            PILayerManager.requestRender();
          }
        });
        break;
      case 'resize': showResizeDialog(); break;
      case 'flip-h': transformFlip('h'); break;
      case 'flip-v': transformFlip('v'); break;
      case 'rotate-cw': transformRotate(90); break;
      case 'rotate-ccw': transformRotate(-90); break;
      case 'auto-contrast': PIQuickAdjustments.applyAutoContrast(); break;
      case 'auto-levels': PIQuickAdjustments.applyAutoLevels(); break;
      case 'white-balance': PIQuickAdjustments.applyWhiteBalance(); break;
      case 'brightness': PIBrightnessContrast.showDialog(); break;
      case 'hue': PIHueSaturation.showDialog(); break;
      case 'vibrance': PIQuickAdjustments.showVibranceDialog(); break;
      case 'temperature': PIQuickAdjustments.showTemperatureDialog(); break;
      case 'levels': PILevels.showDialog(); break;
      case 'color-balance': PIColorBalance.showDialog(); break;
      case 'curves': PICurvesDialog.show(); break;
      case 'exposure': PIAdvancedAdjustments.showExposureDialog(); break;
      case 'channel-mixer': PIQuickAdjustments.showChannelMixerDialog(); break;
      case 'threshold': PIAdvancedAdjustments.showThresholdDialog(); break;
      case 'posterize': PIAdvancedAdjustments.showPosterizeDialog(); break;
      case 'blur': PIBlurFilter.showDialog(); break;
      case 'sharpen': PISharpenFilter.apply(); break;
      case 'mosaic': PIMosaicFilter.showDialog(); break;
      case 'noise': PINoiseFilter.apply(); break;
      case 'glow': PICreativeFilters.showGlowDialog(); break;
      case 'edge-detect': PICreativeFilters.applyEdgeDetect(); break;
      case 'unsharp-mask': PIAdvancedFilters.showUnsharpMaskDialog(); break;
      case 'high-pass': PICreativeFilters.showHighPassDialog(); break;
      case 'emboss': PICreativeFilters.applyEmboss(); break;
      case 'halftone': PICreativeFilters.showHalftoneDialog(); break;
      case 'vignette': PIAdvancedFilters.showVignetteDialog(); break;
      case 'grayscale': PIFilterWorker.apply('grayscale'); break;
      case 'sepia': PIFilterWorker.apply('sepia'); break;
      case 'invert': PIFilterWorker.apply('invert'); break;
      case 'fit': PICanvasEngine.fitToViewport(); break;
      case 'zoom-in': PICanvasEngine.setZoom(PICanvasEngine.getZoom() * 1.25); break;
      case 'zoom-out': PICanvasEngine.setZoom(PICanvasEngine.getZoom() * 0.8); break;
      case 'toggle-grid': { const on = PIGuides.toggleGrid(); PIEventBus.emit('toast', on ? 'グリッド表示ON' : 'グリッド表示OFF'); break; }
      case 'grid-size': showGridSizeDialog(); break;
      case 'add-guide-h': PIGuides.addGuide('h'); break;
      case 'add-guide-v': PIGuides.addGuide('v'); break;
      case 'clear-guides': PIGuides.clearGuides(); PIEventBus.emit('toast', 'ガイドを消去しました'); break;
      default: console.log('Unknown action:', action);
    }
  }

  function pxFromMm(mm, dpi) {
    return Math.round((mm / 25.4) * dpi);
  }

  function resetCanvas(width, height, dpi) {
    const size = PICanvasEngine.normalizeCanvasSize(width, height);
    PILayerManager.clear();
    PICanvasEngine.setDpi(dpi);
    PICanvasEngine.setCanvasSize(size.width, size.height);
    PILayerManager.addLayer('背景');
    const bg = PILayerManager.getActive();
    bg.ctx.fillStyle = '#ffffff';
    bg.ctx.fillRect(0, 0, size.width, size.height);
    PICanvasEngine.fitToViewport();
    PIHistoryManager.clear();
    PIHistoryManager.push('新規キャンバス');
    PILayerManager.requestRender();
  }

  function showNewCanvasDialog() {
    const currentDpi = PICanvasEngine.getDpi();
    PIModal.show({
      title: '新規キャンバス',
      description: 'DPIは印刷サイズとPNG/JPEG書き出しメタデータに反映されます。',
      content: [
        { type: 'number', label: '幅 (px)', key: 'width', value: 800, min: 1, max: 12000 },
        { type: 'number', label: '高さ (px)', key: 'height', value: 600, min: 1, max: 12000 },
        { type: 'number', label: 'DPI', key: 'dpi', value: currentDpi, min: 1, max: 2400 }
      ],
      sizePresets: [
        { label: 'HD 96dpi', onClick: (v) => { v.width = 1280; v.height = 720; v.dpi = 96; } },
        { label: 'Full HD 96dpi', onClick: (v) => { v.width = 1920; v.height = 1080; v.dpi = 96; } },
        { label: '4K 96dpi', onClick: (v) => { v.width = 3840; v.height = 2160; v.dpi = 96; } },
        { label: 'A4横 300dpi', onClick: (v) => { v.dpi = 300; v.width = pxFromMm(297, 300); v.height = pxFromMm(210, 300); } },
        { label: 'A4縦 300dpi', onClick: (v) => { v.dpi = 300; v.width = pxFromMm(210, 300); v.height = pxFromMm(297, 300); } },
        { label: '名刺 350dpi', onClick: (v) => { v.dpi = 350; v.width = pxFromMm(91, 350); v.height = pxFromMm(55, 350); } },
        { label: 'Instagram', onClick: (v) => { v.width = 1080; v.height = 1080; v.dpi = 96; } }
      ],
      confirmLabel: '作成',
      onConfirm: (values) => {
        resetCanvas(parseInt(values.width, 10) || 800, parseInt(values.height, 10) || 600, parseInt(values.dpi, 10) || currentDpi);
      }
    });
  }

  function showDocumentDpiDialog() {
    const physical = PICanvasEngine.getPhysicalSize('mm');
    PIModal.show({
      title: 'ドキュメントDPI',
      description: '現在の印刷サイズ: ' + physical.width.toFixed(1) + ' x ' + physical.height.toFixed(1) + ' mm',
      content: [
        { type: 'number', label: 'DPI', key: 'dpi', value: PICanvasEngine.getDpi(), min: 1, max: 2400 }
      ],
      sizePresets: [
        { label: 'Web 96dpi', onClick: (v) => { v.dpi = 96; } },
        { label: '印刷 300dpi', onClick: (v) => { v.dpi = 300; } },
        { label: '高精細 600dpi', onClick: (v) => { v.dpi = 600; } }
      ],
      confirmLabel: '適用',
      onConfirm: (values) => {
        PICanvasEngine.setDpi(parseInt(values.dpi, 10) || PICanvasEngine.getDpi());
        PIHistoryManager.push('DPI変更');
        PIEventBus.emit('toast', 'DPIを更新しました');
      }
    });
  }

  function showResizeDialog() {
    const size = PICanvasEngine.getCanvasSize();
    PIModal.show({
      title: 'キャンバスサイズ変更',
      content: [
        { type: 'number', label: '幅 (px)', key: 'width', value: size.width, min: 1, max: 12000 },
        { type: 'number', label: '高さ (px)', key: 'height', value: size.height, min: 1, max: 12000 }
      ],
      sizePresets: [
        { label: 'HD', onClick: (v) => { v.width = 1280; v.height = 720; } },
        { label: 'Full HD', onClick: (v) => { v.width = 1920; v.height = 1080; } },
        { label: '4K', onClick: (v) => { v.width = 3840; v.height = 2160; } }
      ],
      confirmLabel: '適用',
      onConfirm: (values) => {
        const next = PICanvasEngine.normalizeCanvasSize(parseInt(values.width, 10) || size.width, parseInt(values.height, 10) || size.height);
        PILayerManager.getAll().forEach(layer => {
          PILayerManager.bakeLayerToRaster(layer); // 変形を確定してからキャンバスサイズ変更
          const oldCanvas = layer.canvas;
          const newCanvas = document.createElement('canvas');
          newCanvas.width = next.width; newCanvas.height = next.height;
          const nctx = newCanvas.getContext('2d');
          PICanvasEngine.configureContext(nctx);
          nctx.drawImage(oldCanvas, 0, 0);
          layer.canvas = newCanvas;
          layer.ctx = nctx;
        });
        PICanvasEngine.setCanvasSize(next.width, next.height);
        PICanvasEngine.fitToViewport();
        PIHistoryManager.push('サイズ変更');
        PILayerManager.requestRender();
      }
    });
  }

  function showExportDialog() {
    PIModal.show({
      title: 'エクスポート',
      description: 'PNG/JPEGはDPIメタデータ付きで書き出します。WebPはブラウザ標準の画像データとして出力します。',
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
        {
          type: 'select', label: '範囲', key: 'region', value: 'all',
          options: [
            { value: 'all', label: 'キャンバス全体' },
            { value: 'selection', label: '選択範囲のみ' },
            { value: 'trim', label: '不透明部分のみ（余白トリム）' }
          ]
        },
        { type: 'slider', label: '拡大率', key: 'scale', value: 100, min: 10, max: 400, unit: '%' },
        { type: 'number', label: 'DPI', key: 'dpi', value: PICanvasEngine.getDpi(), min: 1, max: 2400 },
        { type: 'slider', label: '品質', key: 'quality', value: 92, min: 10, max: 100 }
      ],
      confirmLabel: 'ダウンロード',
      onConfirm: (values) => {
        const ext = values.format || 'png';
        const filename = safeFilename(values.filename || 'image') + '.' + ext;
        PIImageIO.downloadCanvas(filename, ext, (values.quality || 92) / 100, {
          dpi: values.dpi || PICanvasEngine.getDpi(),
          region: values.region || 'all',
          scale: (values.scale || 100) / 100
        });
      }
    });
  }

  function safeFilename(name) {
    return String(name).replace(/[\\/:*?"<>|]+/g, '_').trim() || 'image';
  }

  function showGridSizeDialog() {
    PIModal.show({
      title: 'グリッド間隔',
      content: [{ type: 'number', label: '間隔 (px)', key: 'size', value: 50, min: 2, max: 500 }],
      confirmLabel: '適用',
      onConfirm: (v) => { PIGuides.setGridSize(parseInt(v.size, 10) || 50); PIGuides.setGrid(true); }
    });
  }

  function showPrintDialog() {
    PIModal.show({
      title: '印刷',
      description: '編集中の画像を保存せずにそのまま印刷します。',
      content: [
        {
          type: 'select', label: 'サイズ', key: 'mode', value: 'fit',
          options: [
            { value: 'fit', label: '用紙に合わせる' },
            { value: 'actual', label: '実寸（DPIに従う）' }
          ]
        }
      ],
      confirmLabel: '印刷',
      onConfirm: (v) => printImage(v.mode || 'fit')
    });
  }

  // 統合画像を一時iframeに描き、ブラウザの印刷ダイアログを開く（保存不要）
  function printImage(mode) {
    const flat = PILayerManager.flattenAll();
    const dataURL = flat.toDataURL('image/png');
    const dpi = PICanvasEngine.getDpi() || 96;
    const wIn = flat.width / dpi, hIn = flat.height / dpi;
    const sizeCss = mode === 'actual'
      ? 'width:' + wIn + 'in;height:' + hIn + 'in;max-width:100%;max-height:100%;'
      : 'max-width:100%;max-height:100%;width:auto;height:auto;';
    const iframe = document.createElement('iframe');
    iframe.setAttribute('aria-hidden', 'true');
    iframe.style.cssText = 'position:fixed;right:0;bottom:0;width:0;height:0;border:0;';
    document.body.appendChild(iframe);
    const doc = iframe.contentWindow.document;
    doc.open();
    doc.write('<!DOCTYPE html><html><head><meta charset="utf-8"><title>PowerImager 印刷</title><style>' +
      '@page{margin:8mm;}html,body{margin:0;padding:0;height:100%;}' +
      'body{display:flex;align-items:center;justify-content:center;}' +
      'img{' + sizeCss + 'object-fit:contain;display:block;}' +
      '</style></head><body><img src="' + dataURL + '"></body></html>');
    doc.close();
    const done = () => {
      try { iframe.contentWindow.focus(); iframe.contentWindow.print(); }
      catch (e) { console.error('印刷に失敗しました', e); }
      setTimeout(() => { if (iframe.parentNode) iframe.parentNode.removeChild(iframe); }, 3000);
    };
    const img = doc.images[0];
    if (img && !img.complete) img.onload = () => setTimeout(done, 80);
    else setTimeout(done, 120);
    PIEventBus.emit('toast', '印刷ダイアログを開きます');
  }

  function flattenToOneLayer() {
    const flat = PILayerManager.flattenAll();
    PILayerManager.clear();
    const layer = PILayerManager.addLayer('統合画像', flat.width, flat.height);
    PICanvasEngine.configureContext(layer.ctx);
    layer.ctx.drawImage(flat, 0, 0);
    PIHistoryManager.push('レイヤー統合');
    PILayerManager.requestRender();
    PIEventBus.emit('toast', 'レイヤーを統合しました');
  }

  function trimTransparentPixels() {
    const flat = PILayerManager.flattenAll();
    const ctx = flat.getContext('2d');
    const data = ctx.getImageData(0, 0, flat.width, flat.height).data;
    let minX = flat.width, minY = flat.height, maxX = -1, maxY = -1;
    for (let y = 0; y < flat.height; y++) {
      for (let x = 0; x < flat.width; x++) {
        if (data[(y * flat.width + x) * 4 + 3] > 0) {
          if (x < minX) minX = x;
          if (y < minY) minY = y;
          if (x > maxX) maxX = x;
          if (y > maxY) maxY = y;
        }
      }
    }
    if (maxX < minX || maxY < minY) {
      PIEventBus.emit('toast', 'トリミングできる不透明部分がありません');
      return;
    }
    const nextW = maxX - minX + 1;
    const nextH = maxY - minY + 1;
    PILayerManager.getAll().forEach(layer => { layer.x -= minX; layer.y -= minY; });
    PICanvasEngine.setCanvasSize(nextW, nextH);
    PICanvasEngine.fitToViewport();
    PIHistoryManager.push('透明余白トリム');
    PILayerManager.requestRender();
  }

  function transformFlip(dir) {
    const layer = PILayerManager.getActive();
    if (!layer || layer.locked) return;
    PILayerManager.bakeLayerToRaster(layer);
    const temp = document.createElement('canvas');
    temp.width = layer.canvas.width; temp.height = layer.canvas.height;
    const tctx = temp.getContext('2d');
    PICanvasEngine.configureContext(tctx);
    tctx.save();
    if (dir === 'h') {
      tctx.translate(temp.width, 0); tctx.scale(-1, 1);
    } else {
      tctx.translate(0, temp.height); tctx.scale(1, -1);
    }
    tctx.drawImage(layer.canvas, 0, 0);
    tctx.restore();
    layer.ctx.clearRect(0, 0, layer.canvas.width, layer.canvas.height);
    PICanvasEngine.configureContext(layer.ctx);
    layer.ctx.drawImage(temp, 0, 0);
    PIHistoryManager.push('反転');
    PILayerManager.requestRender();
  }

  function transformRotate(deg) {
    const layer = PILayerManager.getActive();
    if (!layer || layer.locked) return;
    PILayerManager.bakeLayerToRaster(layer);
    const sw = layer.canvas.width, sh = layer.canvas.height;
    const isRight = Math.abs(deg) === 90;
    const nw = isRight ? sh : sw;
    const nh = isRight ? sw : sh;
    const temp = document.createElement('canvas');
    temp.width = nw; temp.height = nh;
    const tctx = temp.getContext('2d');
    PICanvasEngine.configureContext(tctx);
    tctx.save();
    tctx.translate(nw / 2, nh / 2);
    tctx.rotate(deg * Math.PI / 180);
    tctx.drawImage(layer.canvas, -sw / 2, -sh / 2);
    tctx.restore();
    layer.canvas.width = nw; layer.canvas.height = nh;
    PICanvasEngine.configureContext(layer.ctx);
    layer.ctx.drawImage(temp, 0, 0);
    PIHistoryManager.push('回転');
    PILayerManager.requestRender();
  }

  return { init, handleAction, showDocumentDpiDialog, printImage };
})();
