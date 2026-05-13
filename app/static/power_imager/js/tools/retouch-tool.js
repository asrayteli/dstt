/* PowerImager - RetouchTool: dodge/burn/sponge/blur/sharpen brush */
window.PIRetouchTool = new (class extends PIToolBase {
  constructor() {
    super('retouch', 'crosshair');
    this.drawing = false;
    this.lastX = 0;
    this.lastY = 0;
    this.size = 36;
    this.strength = 35;
    this.mode = 'dodge';
  }

  onMouseDown(e) {
    const layer = PILayerManager.getActive();
    if (!layer || layer.locked) return;
    this.drawing = true;
    this.lastX = e.canvasX;
    this.lastY = e.canvasY;
    this.applyAt(layer, e.canvasX, e.canvasY);
  }

  onMouseMove(e) {
    if (!this.drawing) return;
    const layer = PILayerManager.getActive();
    if (!layer || layer.locked) return;
    const dist = Math.hypot(e.canvasX - this.lastX, e.canvasY - this.lastY);
    const steps = Math.max(1, Math.ceil(dist / Math.max(2, this.size / 4)));
    for (let i = 1; i <= steps; i++) {
      const t = i / steps;
      const x = this.lastX + (e.canvasX - this.lastX) * t;
      const y = this.lastY + (e.canvasY - this.lastY) * t;
      this.applyAt(layer, x, y);
    }
    this.lastX = e.canvasX;
    this.lastY = e.canvasY;
    PILayerManager.requestRender();
  }

  onMouseUp() {
    if (!this.drawing) return;
    this.drawing = false;
    PIHistoryManager.push('レタッチ');
    PILayerManager.requestRender();
  }

  applyAt(layer, canvasX, canvasY) {
    const radius = Math.max(1, this.size / 2);
    const cx = canvasX - layer.x;
    const cy = canvasY - layer.y;
    const x0 = Math.max(0, Math.floor(cx - radius - 1));
    const y0 = Math.max(0, Math.floor(cy - radius - 1));
    const x1 = Math.min(layer.canvas.width, Math.ceil(cx + radius + 1));
    const y1 = Math.min(layer.canvas.height, Math.ceil(cy + radius + 1));
    const w = x1 - x0;
    const h = y1 - y0;
    if (w <= 0 || h <= 0) return;

    const selection = PISelection.getLayerBounds(layer);
    if (!selection && PISelection.has()) return;

    const imgData = layer.ctx.getImageData(x0, y0, w, h);
    const src = new Uint8ClampedArray(imgData.data);
    const d = imgData.data;
    const amount = PIMathUtils.clamp(this.strength / 100, 0, 1);

    for (let py = 0; py < h; py++) {
      for (let px = 0; px < w; px++) {
        const lx = x0 + px;
        const ly = y0 + py;
        if (!PISelection.contains(selection, lx, ly)) continue;
        const dist = Math.hypot(lx + 0.5 - cx, ly + 0.5 - cy);
        if (dist > radius) continue;
        const falloff = (1 - dist / radius) * amount;
        const i = (py * w + px) * 4;

        if (this.mode === 'dodge') {
          for (let c = 0; c < 3; c++) d[i + c] = PIMathUtils.clamp(d[i + c] + (255 - d[i + c]) * falloff, 0, 255);
        } else if (this.mode === 'burn') {
          for (let c = 0; c < 3; c++) d[i + c] = PIMathUtils.clamp(d[i + c] * (1 - falloff), 0, 255);
        } else if (this.mode === 'sponge') {
          const hsl = PIColorUtils.rgbToHsl(d[i], d[i + 1], d[i + 2]);
          hsl.s = PIMathUtils.clamp(hsl.s + 45 * falloff, 0, 100);
          const rgb = PIColorUtils.hslToRgb(hsl.h, hsl.s, hsl.l);
          d[i] = rgb.r; d[i + 1] = rgb.g; d[i + 2] = rgb.b;
        } else if (this.mode === 'desponge') {
          const hsl = PIColorUtils.rgbToHsl(d[i], d[i + 1], d[i + 2]);
          hsl.s = PIMathUtils.clamp(hsl.s - 45 * falloff, 0, 100);
          const rgb = PIColorUtils.hslToRgb(hsl.h, hsl.s, hsl.l);
          d[i] = rgb.r; d[i + 1] = rgb.g; d[i + 2] = rgb.b;
        } else if (this.mode === 'blur' || this.mode === 'sharpen') {
          const avg = this.neighborAverage(src, px, py, w, h);
          for (let c = 0; c < 3; c++) {
            if (this.mode === 'blur') {
              d[i + c] = PIMathUtils.clamp(d[i + c] * (1 - falloff) + avg[c] * falloff, 0, 255);
            } else {
              d[i + c] = PIMathUtils.clamp(d[i + c] + (d[i + c] - avg[c]) * falloff * 2.2, 0, 255);
            }
          }
        }
      }
    }

    layer.ctx.putImageData(imgData, x0, y0);
  }

  neighborAverage(src, px, py, w, h) {
    const sum = [0, 0, 0];
    let count = 0;
    for (let yy = Math.max(0, py - 1); yy <= Math.min(h - 1, py + 1); yy++) {
      for (let xx = Math.max(0, px - 1); xx <= Math.min(w - 1, px + 1); xx++) {
        const i = (yy * w + xx) * 4;
        sum[0] += src[i]; sum[1] += src[i + 1]; sum[2] += src[i + 2];
        count++;
      }
    }
    return sum.map(v => v / Math.max(1, count));
  }

  getProperties() {
    return {
      title: 'レタッチ',
      fields: [
        {
          type: 'button-group', label: '効果', key: 'mode',
          buttons: [
            { label: '覆い焼き', active: this.mode === 'dodge', onClick: () => { this.mode = 'dodge'; } },
            { label: '焼き込み', active: this.mode === 'burn', onClick: () => { this.mode = 'burn'; } },
            { label: '彩度+', active: this.mode === 'sponge', onClick: () => { this.mode = 'sponge'; } },
            { label: '彩度-', active: this.mode === 'desponge', onClick: () => { this.mode = 'desponge'; } },
            { label: 'ぼかし', active: this.mode === 'blur', onClick: () => { this.mode = 'blur'; } },
            { label: 'シャープ', active: this.mode === 'sharpen', onClick: () => { this.mode = 'sharpen'; } }
          ]
        },
        { type: 'slider', label: 'サイズ', key: 'size', value: this.size, min: 4, max: 180, onChange: (v) => { this.size = parseInt(v); } },
        { type: 'slider', label: '強さ', key: 'strength', value: this.strength, min: 1, max: 100, unit: '%', onChange: (v) => { this.strength = parseInt(v); } }
      ]
    };
  }
})();
