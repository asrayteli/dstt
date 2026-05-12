/* PowerImager — SharpenFilter */
window.PISharpenFilter = {
  apply() {
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const w = layer.canvas.width, h = layer.canvas.height;
    const imgData = layer.ctx.getImageData(0, 0, w, h);
    const src = new Uint8ClampedArray(imgData.data);
    const d = imgData.data;
    const kernel = [0, -1, 0, -1, 5, -1, 0, -1, 0];
    for (let y = 1; y < h - 1; y++) {
      for (let x = 1; x < w - 1; x++) {
        for (let c = 0; c < 3; c++) {
          let sum = 0;
          for (let ky = -1; ky <= 1; ky++) {
            for (let kx = -1; kx <= 1; kx++) {
              const idx = ((y + ky) * w + (x + kx)) * 4 + c;
              sum += src[idx] * kernel[(ky + 1) * 3 + (kx + 1)];
            }
          }
          d[(y * w + x) * 4 + c] = PIMathUtils.clamp(sum, 0, 255);
        }
      }
    }
    layer.ctx.putImageData(imgData, 0, 0);
    PIHistoryManager.push('シャープ');
    PILayerManager.requestRender();
  }
};
