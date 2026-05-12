/* PowerImager — NoiseFilter (median) */
window.PINoiseFilter = {
  apply() {
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const w = layer.canvas.width, h = layer.canvas.height;
    const imgData = layer.ctx.getImageData(0, 0, w, h);
    const src = new Uint8ClampedArray(imgData.data);
    const d = imgData.data;
    for (let y = 1; y < h - 1; y++) {
      for (let x = 1; x < w - 1; x++) {
        for (let c = 0; c < 3; c++) {
          const vals = [];
          for (let ky = -1; ky <= 1; ky++) {
            for (let kx = -1; kx <= 1; kx++) {
              vals.push(src[((y + ky) * w + (x + kx)) * 4 + c]);
            }
          }
          vals.sort((a, b) => a - b);
          d[(y * w + x) * 4 + c] = vals[4];
        }
      }
    }
    layer.ctx.putImageData(imgData, 0, 0);
    PIHistoryManager.push('ノイズ除去');
    PILayerManager.requestRender();
  }
};
