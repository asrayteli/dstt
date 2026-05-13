/* PowerImager — Levels */
window.PILevels = {
  showDialog() {
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const backup = layer.ctx.getImageData(0, 0, layer.canvas.width, layer.canvas.height);
    PIModal.show({
      title: 'レベル補正',
      content: [
        { type: 'slider', label: 'シャドウ', key: 'shadows', value: 0, min: 0, max: 255 },
        { type: 'slider', label: '中間調', key: 'midtones', value: 128, min: 0, max: 255 },
        { type: 'slider', label: 'ハイライト', key: 'highlights', value: 255, min: 0, max: 255 }
      ],
      onPreview: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.apply(layer, v.shadows, v.midtones, v.highlights);
        PILayerManager.requestRender();
      },
      onConfirm: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.apply(layer, v.shadows, v.midtones, v.highlights);
        PIHistoryManager.push('レベル補正');
        PILayerManager.requestRender();
      },
      onCancel: () => { layer.ctx.putImageData(backup, 0, 0); PILayerManager.requestRender(); }
    });
  },
  apply(layer, shadows, midtones, highlights) {
    const range = Math.max(1, highlights - shadows);
    const gamma = midtones / 128;
    PISelection.applyImageData(layer, (d, ox, oy, w, h, bounds) => {
      for (let i = 0; i < d.length; i += 4) {
        const p = i / 4;
        const x = ox + (p % w);
        const y = oy + Math.floor(p / w);
        if (!PISelection.contains(bounds, x, y)) continue;
        for (let c = 0; c < 3; c++) {
          let v = (d[i + c] - shadows) / range;
          v = PIMathUtils.clamp(v, 0, 1);
          v = Math.pow(v, 1 / gamma);
          d[i + c] = Math.round(v * 255);
        }
      }
    });
  }
};
