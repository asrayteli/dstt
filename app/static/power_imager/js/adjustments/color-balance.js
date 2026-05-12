/* PowerImager — ColorBalance */
window.PIColorBalance = {
  showDialog() {
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const backup = layer.ctx.getImageData(0, 0, layer.canvas.width, layer.canvas.height);
    PIModal.show({
      title: 'カラーバランス',
      content: [
        { type: 'slider', label: 'シアン/赤', key: 'red', value: 0, min: -100, max: 100 },
        { type: 'slider', label: 'マゼンタ/緑', key: 'green', value: 0, min: -100, max: 100 },
        { type: 'slider', label: '黄/青', key: 'blue', value: 0, min: -100, max: 100 }
      ],
      onPreview: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.apply(layer, v.red, v.green, v.blue);
        PILayerManager.requestRender();
      },
      onConfirm: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.apply(layer, v.red, v.green, v.blue);
        PIHistoryManager.push('カラーバランス');
        PILayerManager.requestRender();
      },
      onCancel: () => { layer.ctx.putImageData(backup, 0, 0); PILayerManager.requestRender(); }
    });
  },
  apply(layer, r, g, b) {
    const imgData = layer.ctx.getImageData(0, 0, layer.canvas.width, layer.canvas.height);
    const d = imgData.data;
    for (let i = 0; i < d.length; i += 4) {
      d[i] = PIMathUtils.clamp(d[i] + r, 0, 255);
      d[i + 1] = PIMathUtils.clamp(d[i + 1] + g, 0, 255);
      d[i + 2] = PIMathUtils.clamp(d[i + 2] + b, 0, 255);
    }
    layer.ctx.putImageData(imgData, 0, 0);
  }
};
