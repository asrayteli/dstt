/* PowerImager — FilterWorker: クイックフィルタ */
window.PIFilterWorker = {
  apply(filterName) {
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const imgData = layer.ctx.getImageData(0, 0, layer.canvas.width, layer.canvas.height);
    const d = imgData.data;

    if (filterName === 'grayscale') {
      for (let i = 0; i < d.length; i += 4) {
        const avg = d[i] * 0.299 + d[i + 1] * 0.587 + d[i + 2] * 0.114;
        d[i] = d[i + 1] = d[i + 2] = avg;
      }
    } else if (filterName === 'sepia') {
      for (let i = 0; i < d.length; i += 4) {
        const r = d[i], g = d[i + 1], b = d[i + 2];
        d[i] = PIMathUtils.clamp(r * 0.393 + g * 0.769 + b * 0.189, 0, 255);
        d[i + 1] = PIMathUtils.clamp(r * 0.349 + g * 0.686 + b * 0.168, 0, 255);
        d[i + 2] = PIMathUtils.clamp(r * 0.272 + g * 0.534 + b * 0.131, 0, 255);
      }
    } else if (filterName === 'invert') {
      for (let i = 0; i < d.length; i += 4) {
        d[i] = 255 - d[i]; d[i + 1] = 255 - d[i + 1]; d[i + 2] = 255 - d[i + 2];
      }
    }

    layer.ctx.putImageData(imgData, 0, 0);
    const labels = { grayscale: 'グレースケール', sepia: 'セピア', invert: '反転' };
    PIHistoryManager.push(labels[filterName] || filterName);
    PILayerManager.requestRender();
  }
};
