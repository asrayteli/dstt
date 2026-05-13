/* PowerImager - Advanced filters */
window.PIAdvancedFilters = {
  showUnsharpMaskDialog() {
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const backup = layer.ctx.getImageData(0, 0, layer.canvas.width, layer.canvas.height);
    PIModal.show({
      title: 'アンシャープマスク',
      description: '半径、量、しきい値を使う写真向けの高品質シャープです。',
      content: [
        { type: 'slider', label: '半径', key: 'radius', value: 2, min: 1, max: 20 },
        { type: 'slider', label: '量', key: 'amount', value: 120, min: 0, max: 300, unit: '%' },
        { type: 'slider', label: 'しきい値', key: 'threshold', value: 5, min: 0, max: 80 }
      ],
      onPreview: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.applyUnsharpMask(layer, v.radius, v.amount / 100, v.threshold);
        PILayerManager.requestRender();
      },
      onConfirm: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.applyUnsharpMask(layer, v.radius, v.amount / 100, v.threshold);
        PIHistoryManager.push('アンシャープマスク');
        PILayerManager.requestRender();
      },
      onCancel: () => { layer.ctx.putImageData(backup, 0, 0); PILayerManager.requestRender(); }
    });
  },

  applyUnsharpMask(layer, radius, amount, threshold) {
    const w = layer.canvas.width;
    const h = layer.canvas.height;
    const original = layer.ctx.getImageData(0, 0, w, h);
    const blurredCanvas = document.createElement('canvas');
    blurredCanvas.width = w;
    blurredCanvas.height = h;
    const bctx = blurredCanvas.getContext('2d');
    PICanvasEngine.configureContext(bctx);
    bctx.filter = 'blur(' + radius + 'px)';
    bctx.drawImage(layer.canvas, 0, 0);
    bctx.filter = 'none';
    const blurred = bctx.getImageData(0, 0, w, h).data;
    const out = original.data;
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
          const diff = out[i + c] - blurred[i + c];
          if (Math.abs(diff) < threshold) continue;
          out[i + c] = PIMathUtils.clamp(out[i + c] + diff * amount, 0, 255);
        }
      }
    }
    layer.ctx.putImageData(original, 0, 0);
  },

  showVignetteDialog() {
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const backup = layer.ctx.getImageData(0, 0, layer.canvas.width, layer.canvas.height);
    PIModal.show({
      title: 'ビネット',
      description: '周辺光量を落として視線を中央へ誘導します。',
      content: [
        { type: 'slider', label: '強さ', key: 'strength', value: 45, min: -100, max: 100 },
        { type: 'slider', label: 'サイズ', key: 'size', value: 62, min: 10, max: 100, unit: '%' },
        { type: 'slider', label: 'フェザー', key: 'feather', value: 65, min: 1, max: 100, unit: '%' }
      ],
      onPreview: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.applyVignette(layer, v.strength, v.size / 100, v.feather / 100);
        PILayerManager.requestRender();
      },
      onConfirm: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.applyVignette(layer, v.strength, v.size / 100, v.feather / 100);
        PIHistoryManager.push('ビネット');
        PILayerManager.requestRender();
      },
      onCancel: () => { layer.ctx.putImageData(backup, 0, 0); PILayerManager.requestRender(); }
    });
  },

  applyVignette(layer, strength, size, feather) {
    const selection = PISelection.getLayerBounds(layer);
    const area = selection || { x: 0, y: 0, w: layer.canvas.width, h: layer.canvas.height };
    const cx = area.x + area.w / 2;
    const cy = area.y + area.h / 2;
    const inner = Math.max(0.01, Math.min(area.w, area.h) * size * 0.5);
    const outer = inner + Math.max(1, Math.max(area.w, area.h) * feather * 0.5);
    const power = strength / 100;

    PISelection.applyImageData(layer, (d, ox, oy, w, h, bounds) => {
      for (let i = 0; i < d.length; i += 4) {
        const p = i / 4;
        const x = ox + (p % w);
        const y = oy + Math.floor(p / w);
        if (!PISelection.contains(bounds, x, y)) continue;
        const dist = Math.hypot(x - cx, y - cy);
        const t = PIMathUtils.clamp((dist - inner) / (outer - inner), 0, 1);
        const eased = t * t * (3 - 2 * t);
        const factor = power >= 0 ? 1 - eased * power : 1 + eased * Math.abs(power);
        d[i] = PIMathUtils.clamp(d[i] * factor, 0, 255);
        d[i + 1] = PIMathUtils.clamp(d[i + 1] * factor, 0, 255);
        d[i + 2] = PIMathUtils.clamp(d[i + 2] * factor, 0, 255);
      }
    });
  }
};
