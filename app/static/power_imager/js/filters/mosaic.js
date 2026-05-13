/* PowerImager — MosaicFilter */
window.PIMosaicFilter = {
  showDialog() {
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const backup = layer.ctx.getImageData(0, 0, layer.canvas.width, layer.canvas.height);
    PIModal.show({
      title: 'モザイク',
      content: [
        { type: 'slider', label: 'ブロックサイズ', key: 'blockSize', value: 10, min: 2, max: 50 }
      ],
      onPreview: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.apply(layer, v.blockSize);
        PILayerManager.requestRender();
      },
      onConfirm: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.apply(layer, v.blockSize);
        PIHistoryManager.push('モザイク');
        PILayerManager.requestRender();
      },
      onCancel: () => { layer.ctx.putImageData(backup, 0, 0); PILayerManager.requestRender(); }
    });
  },
  apply(layer, blockSize) {
    const bounds = PISelection.getLayerBounds(layer);
    if (!bounds && PISelection.has()) return;
    const x0 = bounds ? bounds.x : 0;
    const y0 = bounds ? bounds.y : 0;
    const w = bounds ? bounds.w : layer.canvas.width;
    const h = bounds ? bounds.h : layer.canvas.height;
    const imgData = layer.ctx.getImageData(x0, y0, w, h);
    const d = imgData.data;
    const bs = Math.max(2, blockSize);
    for (let by = 0; by < h; by += bs) {
      for (let bx = 0; bx < w; bx += bs) {
        let r = 0, g = 0, b = 0, count = 0;
        for (let y = by; y < Math.min(by + bs, h); y++) {
          for (let x = bx; x < Math.min(bx + bs, w); x++) {
            if (!PISelection.contains(bounds, x0 + x, y0 + y)) continue;
            const i = (y * w + x) * 4;
            r += d[i]; g += d[i + 1]; b += d[i + 2]; count++;
          }
        }
        if (!count) continue;
        r = Math.round(r / count); g = Math.round(g / count); b = Math.round(b / count);
        for (let y = by; y < Math.min(by + bs, h); y++) {
          for (let x = bx; x < Math.min(bx + bs, w); x++) {
            if (!PISelection.contains(bounds, x0 + x, y0 + y)) continue;
            const i = (y * w + x) * 4;
            d[i] = r; d[i + 1] = g; d[i + 2] = b;
          }
        }
      }
    }
    layer.ctx.putImageData(imgData, x0, y0);
  }
};
