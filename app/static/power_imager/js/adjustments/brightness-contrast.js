/* PowerImager — BrightnessContrast */
window.PIBrightnessContrast = {
  showDialog() {
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const backup = layer.ctx.getImageData(0, 0, layer.canvas.width, layer.canvas.height);
    PIModal.show({
      title: '明るさ・コントラスト',
      content: [
        { type: 'slider', label: '明るさ', key: 'brightness', value: 0, min: -100, max: 100 },
        { type: 'slider', label: 'コントラスト', key: 'contrast', value: 0, min: -100, max: 100 }
      ],
      onPreview: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.apply(layer, v.brightness, v.contrast);
        PILayerManager.requestRender();
      },
      onConfirm: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.apply(layer, v.brightness, v.contrast);
        PIHistoryManager.push('明るさ・コントラスト');
        PILayerManager.requestRender();
      },
      onCancel: () => {
        layer.ctx.putImageData(backup, 0, 0);
        PILayerManager.requestRender();
      }
    });
  },
  apply(layer, brightness, contrast) {
    const b = brightness;
    const c = (259 * (contrast + 255)) / (255 * (259 - contrast));
    PISelection.applyImageData(layer, (d, ox, oy, w, h, bounds) => {
      for (let i = 0; i < d.length; i += 4) {
        const p = i / 4;
        const x = ox + (p % w);
        const y = oy + Math.floor(p / w);
        if (!PISelection.contains(bounds, x, y)) continue;
        d[i] = PIMathUtils.clamp(c * (d[i] - 128) + 128 + b, 0, 255);
        d[i + 1] = PIMathUtils.clamp(c * (d[i + 1] - 128) + 128 + b, 0, 255);
        d[i + 2] = PIMathUtils.clamp(c * (d[i + 2] - 128) + 128 + b, 0, 255);
      }
    });
  }
};
