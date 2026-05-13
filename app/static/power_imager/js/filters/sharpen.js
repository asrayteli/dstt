/* PowerImager — SharpenFilter */
window.PISharpenFilter = {
  apply() {
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const w = layer.canvas.width, h = layer.canvas.height;
    const bounds = PISelection.getLayerBounds(layer);
    if (!bounds && PISelection.has()) return;
    const xStart = bounds ? Math.max(1, bounds.x) : 1;
    const yStart = bounds ? Math.max(1, bounds.y) : 1;
    const xEnd = bounds ? Math.min(w - 1, bounds.x + bounds.w) : w - 1;
    const yEnd = bounds ? Math.min(h - 1, bounds.y + bounds.h) : h - 1;
    const imgData = layer.ctx.getImageData(0, 0, w, h);
    const src = new Uint8ClampedArray(imgData.data);
    const d = imgData.data;
    const kernel = [0, -1, 0, -1, 5, -1, 0, -1, 0];
    for (let y = yStart; y < yEnd; y++) {
      for (let x = xStart; x < xEnd; x++) {
        if (!PISelection.contains(bounds, x, y)) continue;
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
