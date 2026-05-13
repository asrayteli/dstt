/* PowerImager — HueSaturation */
window.PIHueSaturation = {
  showDialog() {
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const backup = layer.ctx.getImageData(0, 0, layer.canvas.width, layer.canvas.height);
    PIModal.show({
      title: '色相・彩度',
      content: [
        { type: 'slider', label: '色相', key: 'hue', value: 0, min: -180, max: 180 },
        { type: 'slider', label: '彩度', key: 'saturation', value: 0, min: -100, max: 100 },
        { type: 'slider', label: '明度', key: 'lightness', value: 0, min: -100, max: 100 }
      ],
      onPreview: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.apply(layer, v.hue, v.saturation, v.lightness);
        PILayerManager.requestRender();
      },
      onConfirm: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.apply(layer, v.hue, v.saturation, v.lightness);
        PIHistoryManager.push('色相・彩度');
        PILayerManager.requestRender();
      },
      onCancel: () => { layer.ctx.putImageData(backup, 0, 0); PILayerManager.requestRender(); }
    });
  },
  apply(layer, hueShift, satShift, lightShift) {
    PISelection.applyImageData(layer, (d, ox, oy, w, h, bounds) => {
      for (let i = 0; i < d.length; i += 4) {
        const p = i / 4;
        const x = ox + (p % w);
        const y = oy + Math.floor(p / w);
        if (!PISelection.contains(bounds, x, y)) continue;
        const hsl = PIColorUtils.rgbToHsl(d[i], d[i + 1], d[i + 2]);
        hsl.h = (hsl.h + hueShift + 360) % 360;
        hsl.s = PIMathUtils.clamp(hsl.s + satShift, 0, 100);
        hsl.l = PIMathUtils.clamp(hsl.l + lightShift, 0, 100);
        const rgb = PIColorUtils.hslToRgb(hsl.h, hsl.s, hsl.l);
        d[i] = rgb.r; d[i + 1] = rgb.g; d[i + 2] = rgb.b;
      }
    });
  }
};
