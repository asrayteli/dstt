/* PowerImager - Advanced adjustments */
window.PIAdvancedAdjustments = {
  showCurvesDialog() {
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const backup = layer.ctx.getImageData(0, 0, layer.canvas.width, layer.canvas.height);
    PIModal.show({
      title: 'トーンカーブ',
      description: 'シャドウ、ミッドトーン、ハイライトを個別に押し引きします。',
      content: [
        { type: 'slider', label: 'シャドウ', key: 'shadows', value: 0, min: -100, max: 100 },
        { type: 'slider', label: 'ミッドトーン', key: 'midtones', value: 0, min: -100, max: 100 },
        { type: 'slider', label: 'ハイライト', key: 'highlights', value: 0, min: -100, max: 100 },
        { type: 'slider', label: 'Sカーブ', key: 'contrast', value: 0, min: -100, max: 100 }
      ],
      onPreview: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.applyCurves(layer, v);
        PILayerManager.requestRender();
      },
      onConfirm: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.applyCurves(layer, v);
        PIHistoryManager.push('トーンカーブ');
        PILayerManager.requestRender();
      },
      onCancel: () => { layer.ctx.putImageData(backup, 0, 0); PILayerManager.requestRender(); }
    });
  },

  applyCurves(layer, values) {
    const shadows = values.shadows / 100;
    const highlights = values.highlights / 100;
    const contrast = values.contrast / 100;
    const gamma = Math.pow(2, -values.midtones / 50);
    const lut = new Uint8ClampedArray(256);
    for (let i = 0; i < 256; i++) {
      let x = i / 255;
      x = Math.pow(x, gamma);
      x += shadows * Math.pow(1 - x, 2) * 0.45;
      x += highlights * Math.pow(x, 2) * 0.45;
      x += contrast * (x - 0.5) * 4 * x * (1 - x);
      lut[i] = Math.round(PIMathUtils.clamp(x, 0, 1) * 255);
    }
    this.applyLut(layer, lut);
  },

  showExposureDialog() {
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const backup = layer.ctx.getImageData(0, 0, layer.canvas.width, layer.canvas.height);
    PIModal.show({
      title: '露光 / ガンマ',
      description: '写真補正向けに露光量、ガンマ、オフセットを調整します。',
      content: [
        { type: 'slider', label: '露光量(EV)', key: 'exposure', value: 0, min: -5, max: 5 },
        { type: 'slider', label: 'ガンマ', key: 'gamma', value: 100, min: 10, max: 300, unit: '%' },
        { type: 'slider', label: 'オフセット', key: 'offset', value: 0, min: -100, max: 100 }
      ],
      onPreview: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.applyExposure(layer, v.exposure, v.gamma / 100, v.offset);
        PILayerManager.requestRender();
      },
      onConfirm: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.applyExposure(layer, v.exposure, v.gamma / 100, v.offset);
        PIHistoryManager.push('露光 / ガンマ');
        PILayerManager.requestRender();
      },
      onCancel: () => { layer.ctx.putImageData(backup, 0, 0); PILayerManager.requestRender(); }
    });
  },

  applyExposure(layer, exposure, gamma, offset) {
    const gain = Math.pow(2, exposure);
    const invGamma = 1 / Math.max(0.01, gamma);
    PISelection.applyImageData(layer, (d, ox, oy, w, h, bounds) => {
      for (let i = 0; i < d.length; i += 4) {
        const p = i / 4;
        const x = ox + (p % w);
        const y = oy + Math.floor(p / w);
        if (!PISelection.contains(bounds, x, y)) continue;
        for (let c = 0; c < 3; c++) {
          const v = PIMathUtils.clamp((d[i + c] / 255) * gain + offset / 255, 0, 1);
          d[i + c] = Math.round(Math.pow(v, invGamma) * 255);
        }
      }
    });
  },

  showThresholdDialog() {
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const backup = layer.ctx.getImageData(0, 0, layer.canvas.width, layer.canvas.height);
    PIModal.show({
      title: 'しきい値',
      description: '輝度を基準に2値化します。マスク作成や線画抽出の下処理に使えます。',
      content: [
        { type: 'slider', label: 'しきい値', key: 'threshold', value: 128, min: 0, max: 255 }
      ],
      onPreview: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.applyThreshold(layer, v.threshold);
        PILayerManager.requestRender();
      },
      onConfirm: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.applyThreshold(layer, v.threshold);
        PIHistoryManager.push('しきい値');
        PILayerManager.requestRender();
      },
      onCancel: () => { layer.ctx.putImageData(backup, 0, 0); PILayerManager.requestRender(); }
    });
  },

  applyThreshold(layer, threshold) {
    PISelection.applyImageData(layer, (d, ox, oy, w, h, bounds) => {
      for (let i = 0; i < d.length; i += 4) {
        const p = i / 4;
        const x = ox + (p % w);
        const y = oy + Math.floor(p / w);
        if (!PISelection.contains(bounds, x, y)) continue;
        const lum = d[i] * 0.299 + d[i + 1] * 0.587 + d[i + 2] * 0.114;
        const v = lum >= threshold ? 255 : 0;
        d[i] = d[i + 1] = d[i + 2] = v;
      }
    });
  },

  showPosterizeDialog() {
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const backup = layer.ctx.getImageData(0, 0, layer.canvas.width, layer.canvas.height);
    PIModal.show({
      title: 'ポスタライズ',
      description: '色階調を減らしてポスター調にします。',
      content: [
        { type: 'slider', label: '階調数', key: 'levels', value: 6, min: 2, max: 32 }
      ],
      onPreview: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.applyPosterize(layer, v.levels);
        PILayerManager.requestRender();
      },
      onConfirm: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.applyPosterize(layer, v.levels);
        PIHistoryManager.push('ポスタライズ');
        PILayerManager.requestRender();
      },
      onCancel: () => { layer.ctx.putImageData(backup, 0, 0); PILayerManager.requestRender(); }
    });
  },

  applyPosterize(layer, levels) {
    const step = 255 / Math.max(1, levels - 1);
    PISelection.applyImageData(layer, (d, ox, oy, w, h, bounds) => {
      for (let i = 0; i < d.length; i += 4) {
        const p = i / 4;
        const x = ox + (p % w);
        const y = oy + Math.floor(p / w);
        if (!PISelection.contains(bounds, x, y)) continue;
        d[i] = Math.round(Math.round(d[i] / step) * step);
        d[i + 1] = Math.round(Math.round(d[i + 1] / step) * step);
        d[i + 2] = Math.round(Math.round(d[i + 2] / step) * step);
      }
    });
  },

  applyLut(layer, lut) {
    PISelection.applyImageData(layer, (d, ox, oy, w, h, bounds) => {
      for (let i = 0; i < d.length; i += 4) {
        const p = i / 4;
        const x = ox + (p % w);
        const y = oy + Math.floor(p / w);
        if (!PISelection.contains(bounds, x, y)) continue;
        d[i] = lut[d[i]];
        d[i + 1] = lut[d[i + 1]];
        d[i + 2] = lut[d[i + 2]];
      }
    });
  }
};
