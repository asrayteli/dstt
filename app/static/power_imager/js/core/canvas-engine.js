/* PowerImager — CanvasEngine: キャンバス描画エンジン */
window.PICanvasEngine = (function () {
  let viewport, wrapper, displayCanvas, displayCtx, overlayCanvas, overlayCtx;
  const DEFAULT_DPI = 96;
  const MIN_DPI = 1;
  const MAX_DPI = 2400;
  const MAX_DIMENSION = 12000;
  const MAX_PIXELS = 80000000;

  let canvasW = 800, canvasH = 600;
  let dpi = DEFAULT_DPI;
  let zoom = 1, panX = 0, panY = 0;
  let isPanning = false, panStartX = 0, panStartY = 0;
  let spaceDown = false;
  // 描画中ストロークのライブプレビュー（確定前のブラシ/消しゴムを所属レイヤーの直上に重ねる）
  let previewStroke = null;
  // レイヤーマスク合成用の使い回しキャンバス
  let maskTmp = null, maskTmpCtx = null;
  // クリッピンググループ合成用の使い回しキャンバス
  let groupTmp = null, groupTmpCtx = null, baseAlphaTmp = null, baseAlphaCtx = null;

  function init() {
    viewport = document.getElementById('pi-viewport');
    wrapper = viewport.querySelector('.canvas-wrapper');
    displayCanvas = document.getElementById('display-canvas');
    displayCtx = displayCanvas.getContext('2d');
    overlayCanvas = document.getElementById('overlay-canvas');
    overlayCtx = overlayCanvas.getContext('2d');
    configureContext(displayCtx);
    configureContext(overlayCtx);

    viewport.addEventListener('wheel', onWheel, { passive: false });
    viewport.addEventListener('mousedown', onPanStart);
    window.addEventListener('mousemove', onPanMove);
    window.addEventListener('mouseup', onPanEnd);
    window.addEventListener('keydown', (e) => { if (e.code === 'Space' && !e.repeat) { spaceDown = true; viewport.style.cursor = 'grab'; } });
    window.addEventListener('keyup', (e) => { if (e.code === 'Space') { spaceDown = false; if (!isPanning) viewport.style.cursor = 'crosshair'; } });

    setCanvasSize(canvasW, canvasH);
    fitToViewport();
  }

  function clampNumber(value, min, max, fallback) {
    const n = Number(value);
    if (!Number.isFinite(n)) return fallback;
    return Math.min(max, Math.max(min, Math.round(n)));
  }

  function normalizeCanvasSize(w, h) {
    let nextW = clampNumber(w, 1, MAX_DIMENSION, canvasW);
    let nextH = clampNumber(h, 1, MAX_DIMENSION, canvasH);
    const pixels = nextW * nextH;
    if (pixels > MAX_PIXELS) {
      const scale = Math.sqrt(MAX_PIXELS / pixels);
      nextW = Math.max(1, Math.floor(nextW * scale));
      nextH = Math.max(1, Math.floor(nextH * scale));
      PIEventBus.emit('toast', 'キャンバスが大きすぎるため、安全なサイズに調整しました');
    }
    return { width: nextW, height: nextH };
  }

  function setCanvasSize(w, h) {
    const size = normalizeCanvasSize(w, h);
    canvasW = size.width; canvasH = size.height;
    displayCanvas.width = canvasW; displayCanvas.height = canvasH;
    overlayCanvas.width = canvasW; overlayCanvas.height = canvasH;
    configureContext(displayCtx);
    configureContext(overlayCtx);
    wrapper.style.width = canvasW + 'px';
    wrapper.style.height = canvasH + 'px';
    updateTransform();
    PIEventBus.emit('canvas:resized', { width: canvasW, height: canvasH });
  }

  function getCanvasSize() { return { width: canvasW, height: canvasH }; }

  function getDpi() { return dpi; }

  function setDpi(value) {
    const next = clampNumber(value, MIN_DPI, MAX_DPI, dpi);
    if (next === dpi) return dpi;
    dpi = next;
    PIEventBus.emit('document:dpi-changed', dpi);
    return dpi;
  }

  function getPhysicalSize(unit) {
    const inchesW = canvasW / dpi;
    const inchesH = canvasH / dpi;
    if (unit === 'in') return { width: inchesW, height: inchesH, unit: 'in' };
    return { width: inchesW * 25.4, height: inchesH * 25.4, unit: 'mm' };
  }

  function configureContext(ctx, options) {
    if (!ctx) return ctx;
    const quality = (options && options.quality) || 'high';
    if ('imageSmoothingEnabled' in ctx) ctx.imageSmoothingEnabled = true;
    if ('imageSmoothingQuality' in ctx) ctx.imageSmoothingQuality = quality;
    return ctx;
  }

  function updateTransform() {
    wrapper.style.transform = 'translate(' + panX + 'px,' + panY + 'px) scale(' + zoom + ')';
  }

  function fitToViewport() {
    const vw = viewport.clientWidth;
    const vh = viewport.clientHeight;
    const scaleX = (vw - 40) / canvasW;
    const scaleY = (vh - 40) / canvasH;
    zoom = Math.min(scaleX, scaleY, 4);
    zoom = Math.max(zoom, 0.05);
    panX = (vw - canvasW * zoom) / 2;
    panY = (vh - canvasH * zoom) / 2;
    updateTransform();
    PIEventBus.emit('canvas:zoom-changed', zoom);
  }

  function onWheel(e) {
    e.preventDefault();
    const rect = viewport.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const oldZoom = zoom;
    const factor = e.deltaY < 0 ? 1.1 : 0.9;
    zoom = Math.max(0.05, Math.min(zoom * factor, 32));
    panX = mx - (mx - panX) * (zoom / oldZoom);
    panY = my - (my - panY) * (zoom / oldZoom);
    updateTransform();
    PIEventBus.emit('canvas:zoom-changed', zoom);
  }

  function onPanStart(e) {
    if (!spaceDown && e.button !== 1) return;
    isPanning = true;
    panStartX = e.clientX - panX;
    panStartY = e.clientY - panY;
    viewport.style.cursor = 'grabbing';
    e.preventDefault();
  }

  function onPanMove(e) {
    if (!isPanning) return;
    panX = e.clientX - panStartX;
    panY = e.clientY - panStartY;
    updateTransform();
  }

  function onPanEnd() {
    if (!isPanning) return;
    isPanning = false;
    viewport.style.cursor = spaceDown ? 'grab' : 'crosshair';
  }

  function viewportToCanvas(clientX, clientY) {
    const rect = viewport.getBoundingClientRect();
    const vx = clientX - rect.left;
    const vy = clientY - rect.top;
    return {
      x: (vx - panX) / zoom,
      y: (vy - panY) / zoom
    };
  }

  function canvasToViewport(cx, cy) {
    const rect = viewport.getBoundingClientRect();
    return {
      x: cx * zoom + panX + rect.left,
      y: cy * zoom + panY + rect.top
    };
  }

  // レイヤーを回転・拡縮（中心基準）を考慮して ctx へ合成する共通関数。
  // raster は通常 identity、text/shape は rotation を保持、変形プレビュー中は一時的に scale も持つ。
  function drawLayerTo(ctx, layer, opts) {
    const o = opts || {};
    const w = layer.canvas.width, h = layer.canvas.height;
    const rot = layer.rotation || 0;
    const sx = layer.scaleX || 1, sy = layer.scaleY || 1;
    // マスクがあればレイヤー画素にマスクのα（可視度）を掛けた合成結果を描画元にする
    let source = layer.canvas;
    if (layer.mask) {
      if (!maskTmp) { maskTmp = document.createElement('canvas'); maskTmpCtx = maskTmp.getContext('2d'); }
      if (maskTmp.width !== w || maskTmp.height !== h) { maskTmp.width = w; maskTmp.height = h; }
      maskTmpCtx.globalCompositeOperation = 'source-over';
      maskTmpCtx.clearRect(0, 0, w, h);
      maskTmpCtx.drawImage(layer.canvas, 0, 0);
      maskTmpCtx.globalCompositeOperation = 'destination-in';
      maskTmpCtx.drawImage(layer.mask, 0, 0);
      maskTmpCtx.globalCompositeOperation = 'source-over';
      source = maskTmp;
    }
    // レイヤースタイル（影/縁取り/光彩）があれば効果込みの描画元へ差し替え
    let drawSrc = source, ox = 0, oy = 0;
    if (hasAnyEffect(layer.effects)) {
      const fx = buildEffects(source, layer.effects);
      drawSrc = fx.canvas; ox = -fx.padX; oy = -fx.padY;
    }
    ctx.save();
    if (!o.ignoreCompositing) {
      ctx.globalAlpha = layer.opacity;
      ctx.globalCompositeOperation = layer.blendMode || 'source-over';
    }
    if (rot === 0 && sx === 1 && sy === 1) {
      ctx.drawImage(drawSrc, layer.x + ox, layer.y + oy);
    } else {
      const cx = layer.x + w / 2, cy = layer.y + h / 2;
      ctx.translate(cx, cy);
      ctx.rotate(rot);
      ctx.scale(sx, sy);
      ctx.drawImage(drawSrc, -w / 2 + ox, -h / 2 + oy);
    }
    ctx.restore();
  }

  function hasAnyEffect(fx) {
    return !!(fx && ((fx.dropShadow && fx.dropShadow.enabled) || (fx.stroke && fx.stroke.enabled) || (fx.outerGlow && fx.outerGlow.enabled)));
  }

  function makeSilhouette(source, color) {
    const c = document.createElement('canvas');
    c.width = source.width; c.height = source.height;
    const cx = c.getContext('2d');
    cx.drawImage(source, 0, 0);
    cx.globalCompositeOperation = 'source-in';
    cx.fillStyle = color;
    cx.fillRect(0, 0, c.width, c.height);
    return c;
  }

  // source（レイヤー画素）の周囲に効果を描いた拡張キャンバスを返す
  function buildEffects(source, fx) {
    const ds = fx.dropShadow, st = fx.stroke, gl = fx.outerGlow;
    let padX = 0, padY = 0;
    if (ds && ds.enabled) { padX = Math.max(padX, ds.blur + Math.abs(ds.offsetX)); padY = Math.max(padY, ds.blur + Math.abs(ds.offsetY)); }
    if (gl && gl.enabled) { padX = Math.max(padX, gl.blur); padY = Math.max(padY, gl.blur); }
    if (st && st.enabled) { padX = Math.max(padX, st.width); padY = Math.max(padY, st.width); }
    padX = Math.ceil(padX) + 3; padY = Math.ceil(padY) + 3;
    const W = source.width + padX * 2, H = source.height + padY * 2;
    const out = document.createElement('canvas'); out.width = W; out.height = H;
    const octx = out.getContext('2d');
    configureContext(octx);
    if (gl && gl.enabled) {
      const sil = makeSilhouette(source, gl.color);
      octx.save();
      octx.globalAlpha = gl.opacity != null ? gl.opacity : 0.8;
      octx.filter = 'blur(' + gl.blur + 'px)';
      for (let k = 0; k < 3; k++) octx.drawImage(sil, padX, padY);
      octx.restore();
    }
    if (ds && ds.enabled) {
      const sil = makeSilhouette(source, ds.color);
      octx.save();
      octx.globalAlpha = ds.opacity != null ? ds.opacity : 0.5;
      octx.filter = 'blur(' + ds.blur + 'px)';
      octx.drawImage(sil, padX + ds.offsetX, padY + ds.offsetY);
      octx.restore();
    }
    if (st && st.enabled && st.width > 0) {
      const sil = makeSilhouette(source, st.color);
      const N = Math.max(12, Math.round(st.width * 4));
      octx.save();
      for (let i = 0; i < N; i++) {
        const a = (i / N) * Math.PI * 2;
        octx.drawImage(sil, padX + Math.cos(a) * st.width, padY + Math.sin(a) * st.width);
      }
      octx.restore();
    }
    octx.drawImage(source, padX, padY);
    return { canvas: out, padX, padY };
  }

  function ensureGroupTmps() {
    if (!groupTmp) { groupTmp = document.createElement('canvas'); groupTmpCtx = groupTmp.getContext('2d'); }
    if (!baseAlphaTmp) { baseAlphaTmp = document.createElement('canvas'); baseAlphaCtx = baseAlphaTmp.getContext('2d'); }
    if (groupTmp.width !== canvasW || groupTmp.height !== canvasH) { groupTmp.width = canvasW; groupTmp.height = canvasH; }
    if (baseAlphaTmp.width !== canvasW || baseAlphaTmp.height !== canvasH) { baseAlphaTmp.width = canvasW; baseAlphaTmp.height = canvasH; }
  }

  // 表示レイヤーを、クリッピンググループを考慮して ctx へ合成する。
  // clip=true のレイヤーは直下（同グループの基底）レイヤーの不透明形状にクリップされる。
  function compositeStack(ctx, layers) {
    let i = 0;
    while (i < layers.length) {
      const base = layers[i];
      // このグループにぶら下がるクリップレイヤーを収集（非表示でもグループは消費）
      let j = i + 1;
      const clips = [];
      while (j < layers.length && layers[j].clip) { if (layers[j].visible) clips.push(layers[j]); j++; }
      if (!base.visible) { i = j; continue; }
      // 調整レイヤー: 下位の合成結果に非破壊で補正を適用
      if (base.type === 'adjustment') {
        if (window.PIAdjustmentLayer) PIAdjustmentLayer.applyToCtx(ctx, base, canvasW, canvasH);
        clips.forEach(cl => { if (cl.type === 'adjustment') { if (window.PIAdjustmentLayer) PIAdjustmentLayer.applyToCtx(ctx, cl, canvasW, canvasH); } else drawLayerTo(ctx, cl); });
        i = j; continue;
      }
      if (clips.length === 0) {
        drawLayerTo(ctx, base);
      } else {
        ensureGroupTmps();
        groupTmpCtx.clearRect(0, 0, canvasW, canvasH);
        // 基底を素の状態でグループへ（不透明度/blendは最後にまとめて適用）
        drawLayerTo(groupTmpCtx, base, { ignoreCompositing: true });
        baseAlphaCtx.clearRect(0, 0, canvasW, canvasH);
        baseAlphaCtx.drawImage(groupTmp, 0, 0); // 基底シルエットを保存
        clips.forEach(cl => { if (cl.type === 'adjustment') { if (window.PIAdjustmentLayer) PIAdjustmentLayer.applyToCtx(groupTmpCtx, cl, canvasW, canvasH); } else drawLayerTo(groupTmpCtx, cl); });
        groupTmpCtx.save();
        groupTmpCtx.globalCompositeOperation = 'destination-in';
        groupTmpCtx.drawImage(baseAlphaTmp, 0, 0); // グループ全体を基底形状でクリップ
        groupTmpCtx.restore();
        ctx.save();
        ctx.globalAlpha = base.opacity;
        ctx.globalCompositeOperation = base.blendMode || 'source-over';
        ctx.drawImage(groupTmp, 0, 0);
        ctx.restore();
      }
      i = j;
    }
  }

  function renderLayers(layers) {
    configureContext(displayCtx);
    displayCtx.clearRect(0, 0, canvasW, canvasH);
    compositeStack(displayCtx, layers);
    if (previewStroke) {
      const owner = layers.find(l => l.id === previewStroke.layerId);
      if (owner && owner.visible) {
        displayCtx.save();
        displayCtx.globalAlpha = previewStroke.opacity;
        displayCtx.globalCompositeOperation = previewStroke.blendMode || 'source-over';
        displayCtx.drawImage(previewStroke.canvas, previewStroke.x, previewStroke.y);
        displayCtx.restore();
      }
    }
  }

  // 確定前ストロークのプレビュー設定。caller 側で requestRender() を呼ぶ。
  function setStrokePreview(p) { previewStroke = p; }
  function clearStrokePreview() { previewStroke = null; }

  function clearOverlay() { overlayCtx.clearRect(0, 0, canvasW, canvasH); }

  function getDisplayCanvas() { return displayCanvas; }
  function getDisplayCtx() { return displayCtx; }
  function getOverlayCanvas() { return overlayCanvas; }
  function getOverlayCtx() { return overlayCtx; }
  function getViewport() { return viewport; }
  function getZoom() { return zoom; }
  function setZoom(z) { zoom = Math.max(0.05, Math.min(z, 32)); updateTransform(); PIEventBus.emit('canvas:zoom-changed', zoom); }
  function isPanningNow() { return isPanning; }

  return {
    init, setCanvasSize, getCanvasSize, getDpi, setDpi, getPhysicalSize,
    normalizeCanvasSize, configureContext, fitToViewport,
    viewportToCanvas, canvasToViewport,
    renderLayers, drawLayerTo, compositeStack, clearOverlay, setStrokePreview, clearStrokePreview,
    getDisplayCanvas, getDisplayCtx, getOverlayCanvas, getOverlayCtx,
    getViewport, getZoom, setZoom, isPanningNow
  };
})();
