/* PowerImager — FillTool: 塗りつぶし */
window.PIFillTool = new (class extends PIToolBase {
  constructor() {
    super('fill', 'crosshair');
    this.tolerance = 32;
  }
  onMouseDown(e) {
    let layer = PILayerManager.getActive();
    if (!layer) return;
    if (layer.locked) { this.warnLocked(); return; }
    layer = PILayerManager.ensurePaintable(layer);
    if (!layer) return;
    const color = document.getElementById('fg-color-input').value;
    const rgb = PIColorUtils.hexToRgb(color);
    const lx = Math.round(e.canvasX - layer.x);
    const ly = Math.round(e.canvasY - layer.y);
    if (lx < 0 || ly < 0 || lx >= layer.canvas.width || ly >= layer.canvas.height) {
      // 無反応だと故障に見えるため理由を通知（図形/テキストレイヤーは範囲が狭い）
      PIEventBus.emit('toast', 'クリック位置がアクティブレイヤー「' + layer.name + '」の範囲外です');
      return;
    }
    if (!this.floodFill(layer, lx, ly, rgb)) return;
    PIHistoryManager.push('塗りつぶし');
    PILayerManager.requestRender();
  }
  floodFill(layer, sx, sy, fillRgb) {
    const w = layer.canvas.width, h = layer.canvas.height;
    // 選択範囲があれば、その内側のみ塗る
    const selBounds = PISelection.getLayerBounds(layer);
    if ((!selBounds && PISelection.has()) || (selBounds && !PISelection.contains(selBounds, sx, sy))) {
      PIEventBus.emit('toast', '選択範囲の外側です（Ctrl+Dで選択解除）');
      return false;
    }
    const imgData = layer.ctx.getImageData(0, 0, w, h);
    const data = imgData.data;
    const idx = (sy * w + sx) * 4;
    const tr = data[idx], tg = data[idx + 1], tb = data[idx + 2], ta = data[idx + 3];
    if (tr === fillRgb.r && tg === fillRgb.g && tb === fillRgb.b && ta === 255) return false;
    const tol = this.tolerance;
    const stack = [[sx, sy]];
    const visited = new Uint8Array(w * h);
    while (stack.length > 0) {
      const [x, y] = stack.pop();
      const pi = y * w + x;
      if (visited[pi]) continue;
      if (selBounds && !PISelection.contains(selBounds, x, y)) continue;
      const i = pi * 4;
      if (Math.abs(data[i] - tr) > tol || Math.abs(data[i + 1] - tg) > tol ||
          Math.abs(data[i + 2] - tb) > tol || Math.abs(data[i + 3] - ta) > tol) continue;
      visited[pi] = 1;
      data[i] = fillRgb.r; data[i + 1] = fillRgb.g; data[i + 2] = fillRgb.b; data[i + 3] = 255;
      if (x > 0) stack.push([x - 1, y]);
      if (x < w - 1) stack.push([x + 1, y]);
      if (y > 0) stack.push([x, y - 1]);
      if (y < h - 1) stack.push([x, y + 1]);
    }
    layer.ctx.putImageData(imgData, 0, 0);
    return true;
  }
  getProperties() {
    const self = this;
    return {
      title: '塗りつぶし',
      fields: [
        { type: 'slider', label: '許容値', key: 'tolerance', value: self.tolerance, min: 0, max: 255, onChange: (v) => { self.tolerance = parseInt(v); } }
      ]
    };
  }
})();
