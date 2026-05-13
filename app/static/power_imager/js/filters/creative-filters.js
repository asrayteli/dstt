/* PowerImager - Creative and pro filters */
window.PICreativeFilters = {
  applyConvolution(layer, kernel, divisor, bias, label) {
    const w = layer.canvas.width;
    const h = layer.canvas.height;
    const bounds = PISelection.getLayerBounds(layer);
    if (!bounds && PISelection.has()) return;
    const xStart = bounds ? Math.max(1, bounds.x) : 1;
    const yStart = bounds ? Math.max(1, bounds.y) : 1;
    const xEnd = bounds ? Math.min(w - 1, bounds.x + bounds.w) : w - 1;
    const yEnd = bounds ? Math.min(h - 1, bounds.y + bounds.h) : h - 1;
    const imgData = layer.ctx.getImageData(0, 0, w, h);
    const src = new Uint8ClampedArray(imgData.data);
    const d = imgData.data;
    const div = divisor || 1;
    for (let y = yStart; y < yEnd; y++) {
      for (let x = xStart; x < xEnd; x++) {
        if (!PISelection.contains(bounds, x, y)) continue;
        for (let c = 0; c < 3; c++) {
          let sum = 0;
          for (let ky = -1; ky <= 1; ky++) {
            for (let kx = -1; kx <= 1; kx++) {
              const idx = ((y + ky) * w + (x + kx)) * 4 + c;
              sum += src[idx] * kernel[(ky + 1) * 3 + (kx + 1)];
            }
          }
          d[(y * w + x) * 4 + c] = PIMathUtils.clamp(sum / div + (bias || 0), 0, 255);
        }
      }
    }
    layer.ctx.putImageData(imgData, 0, 0);
    PIHistoryManager.push(label);
    PILayerManager.requestRender();
  },

  applyEdgeDetect() {
    const layer = PILayerManager.getActive();
    if (!layer) return;
    this.applyConvolution(layer, [-1, -1, -1, -1, 8, -1, -1, -1, -1], 1, 0, '輪郭抽出');
  },

  applyEmboss() {
    const layer = PILayerManager.getActive();
    if (!layer) return;
    this.applyConvolution(layer, [-2, -1, 0, -1, 1, 1, 0, 1, 2], 1, 128, 'エンボス');
  },

  showGlowDialog() {
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const backup = layer.ctx.getImageData(0, 0, layer.canvas.width, layer.canvas.height);
    PIModal.show({
      title: 'ソフトグロー',
      description: '明るい部分をにじませて柔らかい光を足します。',
      content: [
        { type: 'slider', label: '半径', key: 'radius', value: 8, min: 1, max: 40 },
        { type: 'slider', label: '強さ', key: 'amount', value: 35, min: 0, max: 100, unit: '%' }
      ],
      onPreview: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.applyGlow(layer, v.radius, v.amount / 100);
        PILayerManager.requestRender();
      },
      onConfirm: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.applyGlow(layer, v.radius, v.amount / 100);
        PIHistoryManager.push('ソフトグロー');
        PILayerManager.requestRender();
      },
      onCancel: () => { layer.ctx.putImageData(backup, 0, 0); PILayerManager.requestRender(); }
    });
  },

  applyGlow(layer, radius, amount) {
    const w = layer.canvas.width;
    const h = layer.canvas.height;
    const source = layer.ctx.getImageData(0, 0, w, h);
    const glow = document.createElement('canvas');
    glow.width = w;
    glow.height = h;
    const gctx = glow.getContext('2d');
    PICanvasEngine.configureContext(gctx);
    gctx.filter = 'brightness(1.15) blur(' + radius + 'px)';
    gctx.drawImage(layer.canvas, 0, 0);
    gctx.filter = 'none';
    const glowData = gctx.getImageData(0, 0, w, h).data;
    const d = source.data;
    const bounds = PISelection.getLayerBounds(layer);
    if (!bounds && PISelection.has()) return;
    const xStart = bounds ? bounds.x : 0;
    const yStart = bounds ? bounds.y : 0;
    const xEnd = bounds ? bounds.x + bounds.w : w;
    const yEnd = bounds ? bounds.y + bounds.h : h;
    for (let y = yStart; y < yEnd; y++) {
      for (let x = xStart; x < xEnd; x++) {
        if (!PISelection.contains(bounds, x, y)) continue;
        const i = (y * w + x) * 4;
        for (let c = 0; c < 3; c++) {
          d[i + c] = PIMathUtils.clamp(d[i + c] + glowData[i + c] * amount, 0, 255);
        }
      }
    }
    layer.ctx.putImageData(source, 0, 0);
  },

  showHighPassDialog() {
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const backup = layer.ctx.getImageData(0, 0, layer.canvas.width, layer.canvas.height);
    PIModal.show({
      title: 'ハイパス',
      description: '低周波を取り除き、輪郭・質感成分を抽出します。',
      content: [
        { type: 'slider', label: '半径', key: 'radius', value: 4, min: 1, max: 30 },
        { type: 'slider', label: 'コントラスト', key: 'contrast', value: 100, min: 0, max: 250, unit: '%' }
      ],
      onPreview: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.applyHighPass(layer, v.radius, v.contrast / 100);
        PILayerManager.requestRender();
      },
      onConfirm: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.applyHighPass(layer, v.radius, v.contrast / 100);
        PIHistoryManager.push('ハイパス');
        PILayerManager.requestRender();
      },
      onCancel: () => { layer.ctx.putImageData(backup, 0, 0); PILayerManager.requestRender(); }
    });
  },

  applyHighPass(layer, radius, contrast) {
    const w = layer.canvas.width;
    const h = layer.canvas.height;
    const source = layer.ctx.getImageData(0, 0, w, h);
    const blur = document.createElement('canvas');
    blur.width = w;
    blur.height = h;
    const bctx = blur.getContext('2d');
    PICanvasEngine.configureContext(bctx);
    bctx.filter = 'blur(' + radius + 'px)';
    bctx.drawImage(layer.canvas, 0, 0);
    bctx.filter = 'none';
    const blurred = bctx.getImageData(0, 0, w, h).data;
    const d = source.data;
    const bounds = PISelection.getLayerBounds(layer);
    if (!bounds && PISelection.has()) return;
    const xStart = bounds ? bounds.x : 0;
    const yStart = bounds ? bounds.y : 0;
    const xEnd = bounds ? bounds.x + bounds.w : w;
    const yEnd = bounds ? bounds.y + bounds.h : h;
    for (let y = yStart; y < yEnd; y++) {
      for (let x = xStart; x < xEnd; x++) {
        if (!PISelection.contains(bounds, x, y)) continue;
        const i = (y * w + x) * 4;
        for (let c = 0; c < 3; c++) {
          d[i + c] = PIMathUtils.clamp(128 + (d[i + c] - blurred[i + c]) * contrast, 0, 255);
        }
      }
    }
    layer.ctx.putImageData(source, 0, 0);
  },

  showHalftoneDialog() {
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const backup = layer.ctx.getImageData(0, 0, layer.canvas.width, layer.canvas.height);
    PIModal.show({
      title: 'ハーフトーン',
      description: 'ドット密度で濃淡を表現する印刷風フィルタです。',
      content: [
        { type: 'slider', label: 'ドット間隔', key: 'spacing', value: 10, min: 4, max: 40 },
        { type: 'slider', label: '強さ', key: 'strength', value: 80, min: 0, max: 100, unit: '%' }
      ],
      onPreview: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.applyHalftone(layer, v.spacing, v.strength / 100);
        PILayerManager.requestRender();
      },
      onConfirm: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.applyHalftone(layer, v.spacing, v.strength / 100);
        PIHistoryManager.push('ハーフトーン');
        PILayerManager.requestRender();
      },
      onCancel: () => { layer.ctx.putImageData(backup, 0, 0); PILayerManager.requestRender(); }
    });
  },

  applyHalftone(layer, spacing, strength) {
    const bounds = PISelection.getLayerBounds(layer);
    if (!bounds && PISelection.has()) return;
    const area = bounds || { x: 0, y: 0, w: layer.canvas.width, h: layer.canvas.height };
    const source = layer.ctx.getImageData(0, 0, layer.canvas.width, layer.canvas.height);
    const overlay = document.createElement('canvas');
    overlay.width = layer.canvas.width;
    overlay.height = layer.canvas.height;
    const ctx = overlay.getContext('2d');
    PICanvasEngine.configureContext(ctx);
    ctx.fillStyle = '#fff';
    ctx.fillRect(area.x, area.y, area.w, area.h);
    ctx.fillStyle = '#000';
    const d = source.data;
    for (let cy = area.y; cy < area.y + area.h; cy += spacing) {
      for (let cx = area.x; cx < area.x + area.w; cx += spacing) {
        if (!PISelection.contains(bounds, cx, cy)) continue;
        const sx = Math.min(layer.canvas.width - 1, Math.round(cx));
        const sy = Math.min(layer.canvas.height - 1, Math.round(cy));
        const i = (sy * layer.canvas.width + sx) * 4;
        const lum = (d[i] * 0.299 + d[i + 1] * 0.587 + d[i + 2] * 0.114) / 255;
        const r = (spacing * 0.5) * (1 - lum) * strength;
        ctx.beginPath();
        ctx.arc(cx, cy, r, 0, Math.PI * 2);
        ctx.fill();
      }
    }
    const o = ctx.getImageData(0, 0, layer.canvas.width, layer.canvas.height).data;
    for (let y = area.y; y < area.y + area.h; y++) {
      for (let x = area.x; x < area.x + area.w; x++) {
        if (!PISelection.contains(bounds, x, y)) continue;
        const i = (y * layer.canvas.width + x) * 4;
        d[i] = d[i] * (1 - strength) + o[i] * strength;
        d[i + 1] = d[i + 1] * (1 - strength) + o[i + 1] * strength;
        d[i + 2] = d[i + 2] * (1 - strength) + o[i + 2] * strength;
      }
    }
    layer.ctx.putImageData(source, 0, 0);
  }
};
