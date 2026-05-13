/* PowerImager - Quick and pro color adjustments */
window.PIQuickAdjustments = {
  getTargetPixels(layer) {
    const bounds = PISelection.getLayerBounds(layer);
    if (!bounds && PISelection.has()) return null;
    const x0 = bounds ? bounds.x : 0;
    const y0 = bounds ? bounds.y : 0;
    const w = bounds ? bounds.w : layer.canvas.width;
    const h = bounds ? bounds.h : layer.canvas.height;
    return { bounds, x0, y0, w, h, imgData: layer.ctx.getImageData(x0, y0, w, h) };
  },

  forEachTargetPixel(target, fn) {
    const d = target.imgData.data;
    for (let i = 0; i < d.length; i += 4) {
      const p = i / 4;
      const x = target.x0 + (p % target.w);
      const y = target.y0 + Math.floor(p / target.w);
      if (!PISelection.contains(target.bounds, x, y)) continue;
      fn(d, i, x, y);
    }
  },

  commit(layer, target) {
    layer.ctx.putImageData(target.imgData, target.x0, target.y0);
  },

  applyAutoContrast() {
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const target = this.getTargetPixels(layer);
    if (!target) return;
    const min = [255, 255, 255];
    const max = [0, 0, 0];
    this.forEachTargetPixel(target, (d, i) => {
      for (let c = 0; c < 3; c++) {
        min[c] = Math.min(min[c], d[i + c]);
        max[c] = Math.max(max[c], d[i + c]);
      }
    });
    this.forEachTargetPixel(target, (d, i) => {
      for (let c = 0; c < 3; c++) {
        const range = Math.max(1, max[c] - min[c]);
        d[i + c] = PIMathUtils.clamp((d[i + c] - min[c]) * 255 / range, 0, 255);
      }
    });
    this.commit(layer, target);
    PIHistoryManager.push('自動コントラスト');
    PILayerManager.requestRender();
  },

  applyAutoLevels() {
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const target = this.getTargetPixels(layer);
    if (!target) return;
    let min = 255, max = 0;
    this.forEachTargetPixel(target, (d, i) => {
      const lum = d[i] * 0.299 + d[i + 1] * 0.587 + d[i + 2] * 0.114;
      min = Math.min(min, lum);
      max = Math.max(max, lum);
    });
    const range = Math.max(1, max - min);
    this.forEachTargetPixel(target, (d, i) => {
      for (let c = 0; c < 3; c++) d[i + c] = PIMathUtils.clamp((d[i + c] - min) * 255 / range, 0, 255);
    });
    this.commit(layer, target);
    PIHistoryManager.push('自動レベル');
    PILayerManager.requestRender();
  },

  applyWhiteBalance() {
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const target = this.getTargetPixels(layer);
    if (!target) return;
    const sum = [0, 0, 0];
    let count = 0;
    this.forEachTargetPixel(target, (d, i) => {
      sum[0] += d[i]; sum[1] += d[i + 1]; sum[2] += d[i + 2];
      count++;
    });
    if (!count) return;
    const avg = sum.map(v => v / count);
    const gray = (avg[0] + avg[1] + avg[2]) / 3;
    const gain = avg.map(v => gray / Math.max(1, v));
    this.forEachTargetPixel(target, (d, i) => {
      d[i] = PIMathUtils.clamp(d[i] * gain[0], 0, 255);
      d[i + 1] = PIMathUtils.clamp(d[i + 1] * gain[1], 0, 255);
      d[i + 2] = PIMathUtils.clamp(d[i + 2] * gain[2], 0, 255);
    });
    this.commit(layer, target);
    PIHistoryManager.push('自動ホワイトバランス');
    PILayerManager.requestRender();
  },

  showVibranceDialog() {
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const backup = layer.ctx.getImageData(0, 0, layer.canvas.width, layer.canvas.height);
    PIModal.show({
      title: '自然な彩度',
      description: '彩度が低い色を中心に持ち上げ、派手になりすぎるのを抑えます。',
      content: [
        { type: 'slider', label: '自然な彩度', key: 'vibrance', value: 35, min: -100, max: 100 },
        { type: 'slider', label: '彩度', key: 'saturation', value: 0, min: -100, max: 100 }
      ],
      onPreview: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.applyVibrance(layer, v.vibrance, v.saturation);
        PILayerManager.requestRender();
      },
      onConfirm: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.applyVibrance(layer, v.vibrance, v.saturation);
        PIHistoryManager.push('自然な彩度');
        PILayerManager.requestRender();
      },
      onCancel: () => { layer.ctx.putImageData(backup, 0, 0); PILayerManager.requestRender(); }
    });
  },

  applyVibrance(layer, vibrance, saturation) {
    PISelection.applyImageData(layer, (d, ox, oy, w, h, bounds) => {
      for (let i = 0; i < d.length; i += 4) {
        const p = i / 4;
        const x = ox + (p % w);
        const y = oy + Math.floor(p / w);
        if (!PISelection.contains(bounds, x, y)) continue;
        const hsl = PIColorUtils.rgbToHsl(d[i], d[i + 1], d[i + 2]);
        const vibBoost = vibrance * (1 - hsl.s / 100);
        hsl.s = PIMathUtils.clamp(hsl.s + saturation + vibBoost, 0, 100);
        const rgb = PIColorUtils.hslToRgb(hsl.h, hsl.s, hsl.l);
        d[i] = rgb.r; d[i + 1] = rgb.g; d[i + 2] = rgb.b;
      }
    });
  },

  showTemperatureDialog() {
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const backup = layer.ctx.getImageData(0, 0, layer.canvas.width, layer.canvas.height);
    PIModal.show({
      title: '色温度 / 色かぶり',
      description: '暖色/寒色とマゼンタ/グリーンのかぶりを補正します。',
      content: [
        { type: 'slider', label: '色温度', key: 'temperature', value: 0, min: -100, max: 100 },
        { type: 'slider', label: '色かぶり補正', key: 'tint', value: 0, min: -100, max: 100 }
      ],
      onPreview: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.applyTemperature(layer, v.temperature, v.tint);
        PILayerManager.requestRender();
      },
      onConfirm: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.applyTemperature(layer, v.temperature, v.tint);
        PIHistoryManager.push('色温度 / 色かぶり');
        PILayerManager.requestRender();
      },
      onCancel: () => { layer.ctx.putImageData(backup, 0, 0); PILayerManager.requestRender(); }
    });
  },

  applyTemperature(layer, temperature, tint) {
    PISelection.applyImageData(layer, (d, ox, oy, w, h, bounds) => {
      for (let i = 0; i < d.length; i += 4) {
        const p = i / 4;
        const x = ox + (p % w);
        const y = oy + Math.floor(p / w);
        if (!PISelection.contains(bounds, x, y)) continue;
        d[i] = PIMathUtils.clamp(d[i] + temperature * 0.9 + tint * 0.12, 0, 255);
        d[i + 1] = PIMathUtils.clamp(d[i + 1] - Math.abs(temperature) * 0.12 - tint * 0.55, 0, 255);
        d[i + 2] = PIMathUtils.clamp(d[i + 2] - temperature * 0.9 + tint * 0.12, 0, 255);
      }
    });
  },

  showChannelMixerDialog() {
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const backup = layer.ctx.getImageData(0, 0, layer.canvas.width, layer.canvas.height);
    PIModal.show({
      title: 'チャンネルミキサー',
      description: 'RGBチャンネルを混ぜて、色分解や赤外線風表現を作れます。',
      content: [
        { type: 'slider', label: '赤 <- 赤', key: 'rr', value: 100, min: -200, max: 200, unit: '%' },
        { type: 'slider', label: '赤 <- 緑', key: 'rg', value: 0, min: -200, max: 200, unit: '%' },
        { type: 'slider', label: '赤 <- 青', key: 'rb', value: 0, min: -200, max: 200, unit: '%' },
        { type: 'slider', label: '緑 <- 赤', key: 'gr', value: 0, min: -200, max: 200, unit: '%' },
        { type: 'slider', label: '緑 <- 緑', key: 'gg', value: 100, min: -200, max: 200, unit: '%' },
        { type: 'slider', label: '緑 <- 青', key: 'gb', value: 0, min: -200, max: 200, unit: '%' },
        { type: 'slider', label: '青 <- 赤', key: 'br', value: 0, min: -200, max: 200, unit: '%' },
        { type: 'slider', label: '青 <- 緑', key: 'bg', value: 0, min: -200, max: 200, unit: '%' },
        { type: 'slider', label: '青 <- 青', key: 'bb', value: 100, min: -200, max: 200, unit: '%' }
      ],
      onPreview: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.applyChannelMixer(layer, v);
        PILayerManager.requestRender();
      },
      onConfirm: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.applyChannelMixer(layer, v);
        PIHistoryManager.push('チャンネルミキサー');
        PILayerManager.requestRender();
      },
      onCancel: () => { layer.ctx.putImageData(backup, 0, 0); PILayerManager.requestRender(); }
    });
  },

  applyChannelMixer(layer, m) {
    PISelection.applyImageData(layer, (d, ox, oy, w, h, bounds) => {
      for (let i = 0; i < d.length; i += 4) {
        const p = i / 4;
        const x = ox + (p % w);
        const y = oy + Math.floor(p / w);
        if (!PISelection.contains(bounds, x, y)) continue;
        const r = d[i], g = d[i + 1], b = d[i + 2];
        d[i] = PIMathUtils.clamp(r * m.rr / 100 + g * m.rg / 100 + b * m.rb / 100, 0, 255);
        d[i + 1] = PIMathUtils.clamp(r * m.gr / 100 + g * m.gg / 100 + b * m.gb / 100, 0, 255);
        d[i + 2] = PIMathUtils.clamp(r * m.br / 100 + g * m.bg / 100 + b * m.bb / 100, 0, 255);
      }
    });
  }
};
