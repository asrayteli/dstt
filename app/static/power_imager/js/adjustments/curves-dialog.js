/* PowerImager — CurvesDialog: ヒストグラム付き対話的トーンカーブ（RGB/チャンネル別） */
window.PICurvesDialog = (function () {
  function computeHistogram(layer) {
    const w = layer.canvas.width, h = layer.canvas.height;
    const d = layer.ctx.getImageData(0, 0, w, h).data;
    const hist = { r: new Float32Array(256), g: new Float32Array(256), b: new Float32Array(256), rgb: new Float32Array(256) };
    for (let i = 0; i < d.length; i += 4) {
      if (d[i + 3] < 8) continue;
      hist.r[d[i]]++; hist.g[d[i + 1]]++; hist.b[d[i + 2]]++;
      hist.rgb[Math.round(d[i] * 0.299 + d[i + 1] * 0.587 + d[i + 2] * 0.114)]++;
    }
    return hist;
  }

  function curveLUT(points) {
    const pts = [...points].sort((a, b) => a[0] - b[0]);
    const lut = new Uint8ClampedArray(256);
    for (let x = 0; x < 256; x++) {
      let y;
      if (x <= pts[0][0]) y = pts[0][1];
      else if (x >= pts[pts.length - 1][0]) y = pts[pts.length - 1][1];
      else {
        for (let k = 0; k < pts.length - 1; k++) {
          const a = pts[k], b = pts[k + 1];
          if (x >= a[0] && x <= b[0]) { const t = (x - a[0]) / ((b[0] - a[0]) || 1); y = a[1] + (b[1] - a[1]) * t; break; }
        }
      }
      lut[x] = Math.round(PIMathUtils.clamp(y, 0, 255));
    }
    return lut;
  }

  function show() {
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const backup = layer.ctx.getImageData(0, 0, layer.canvas.width, layer.canvas.height);
    const hist = computeHistogram(layer);
    const state = {
      channel: 'rgb',
      curves: { rgb: [[0, 0], [255, 255]], r: [[0, 0], [255, 255]], g: [[0, 0], [255, 255]], b: [[0, 0], [255, 255]] }
    };
    let canvas, ctx, dragIndex = -1;

    function composedLUTs() {
      const rgb = curveLUT(state.curves.rgb);
      const rl = curveLUT(state.curves.r), gl = curveLUT(state.curves.g), bl = curveLUT(state.curves.b);
      const R = new Uint8ClampedArray(256), G = new Uint8ClampedArray(256), B = new Uint8ClampedArray(256);
      for (let i = 0; i < 256; i++) { R[i] = rl[rgb[i]]; G[i] = gl[rgb[i]]; B[i] = bl[rgb[i]]; }
      return { R, G, B };
    }

    function applyToLayer() {
      const L = composedLUTs();
      layer.ctx.putImageData(backup, 0, 0);
      PISelection.applyImageData(layer, (d, ox, oy, w, h, bounds) => {
        for (let i = 0; i < d.length; i += 4) {
          const p = i / 4, x = ox + (p % w), y = oy + Math.floor(p / w);
          if (!PISelection.contains(bounds, x, y)) continue;
          d[i] = L.R[d[i]]; d[i + 1] = L.G[d[i + 1]]; d[i + 2] = L.B[d[i + 2]];
        }
      });
      PILayerManager.requestRender();
    }

    function draw() {
      const N = 256;
      ctx.clearRect(0, 0, N, N);
      ctx.fillStyle = '#1a1a28'; ctx.fillRect(0, 0, N, N);
      // grid
      ctx.strokeStyle = 'rgba(255,255,255,0.08)'; ctx.lineWidth = 1;
      for (let g = 0; g <= 4; g++) { const p = g * N / 4; ctx.beginPath(); ctx.moveTo(p, 0); ctx.lineTo(p, N); ctx.moveTo(0, p); ctx.lineTo(N, p); ctx.stroke(); }
      // histogram of current channel
      const hd = hist[state.channel]; let max = 0; for (let i = 0; i < 256; i++) max = Math.max(max, hd[i]);
      if (max > 0) {
        ctx.fillStyle = state.channel === 'r' ? 'rgba(255,80,80,0.5)' : state.channel === 'g' ? 'rgba(80,255,80,0.5)' : state.channel === 'b' ? 'rgba(90,120,255,0.6)' : 'rgba(200,200,220,0.45)';
        for (let i = 0; i < 256; i++) { const bh = (hd[i] / max) * N; ctx.fillRect(i, N - bh, 1, bh); }
      }
      // reference diagonal
      ctx.strokeStyle = 'rgba(255,255,255,0.2)'; ctx.beginPath(); ctx.moveTo(0, N); ctx.lineTo(N, 0); ctx.stroke();
      // curve
      const lut = curveLUT(state.curves[state.channel]);
      ctx.strokeStyle = '#7c6ff7'; ctx.lineWidth = 2; ctx.beginPath();
      for (let x = 0; x < 256; x++) { const y = N - lut[x]; if (x === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y); }
      ctx.stroke();
      // control points
      ctx.fillStyle = '#fff';
      state.curves[state.channel].forEach(pt => { ctx.beginPath(); ctx.arc(pt[0], N - pt[1], 4, 0, Math.PI * 2); ctx.fill(); });
    }

    function toData(e) {
      const rect = canvas.getBoundingClientRect();
      const x = Math.round((e.clientX - rect.left) * 256 / rect.width);
      const y = Math.round(255 - (e.clientY - rect.top) * 256 / rect.height);
      return [PIMathUtils.clamp(x, 0, 255), PIMathUtils.clamp(y, 0, 255)];
    }

    function nearestIndex(pt) {
      const pts = state.curves[state.channel]; let best = -1, bd = 14;
      pts.forEach((p, i) => { const dd = Math.hypot(p[0] - pt[0], p[1] - pt[1]); if (dd < bd) { bd = dd; best = i; } });
      return best;
    }

    function render(group) {
      const wrap = document.createElement('div');
      wrap.style.cssText = 'display:flex;flex-direction:column;gap:8px;align-items:center;';
      const chRow = document.createElement('div'); chRow.className = 'button-group'; chRow.style.width = '256px';
      [['rgb', 'RGB'], ['r', 'R'], ['g', 'G'], ['b', 'B']].forEach(([k, l]) => {
        const b = document.createElement('button'); b.textContent = l; if (state.channel === k) b.className = 'active';
        b.addEventListener('click', () => { state.channel = k; [...chRow.children].forEach(c => c.className = ''); b.className = 'active'; draw(); });
        chRow.appendChild(b);
      });
      wrap.appendChild(chRow);
      canvas = document.createElement('canvas'); canvas.width = 256; canvas.height = 256;
      canvas.style.cssText = 'width:256px;height:256px;border:1px solid var(--pi-border);border-radius:6px;cursor:crosshair;touch-action:none;';
      ctx = canvas.getContext('2d');
      wrap.appendChild(canvas);
      const reset = document.createElement('button'); reset.className = 'modal-btn modal-btn-secondary'; reset.textContent = '現在のチャンネルをリセット'; reset.style.cssText = 'width:256px;';
      reset.addEventListener('click', () => { state.curves[state.channel] = [[0, 0], [255, 255]]; draw(); applyToLayer(); });
      wrap.appendChild(reset);
      group.appendChild(wrap);

      canvas.addEventListener('mousedown', (e) => {
        const pt = toData(e); const idx = nearestIndex(pt);
        if (idx >= 0) dragIndex = idx;
        else { state.curves[state.channel].push(pt); state.curves[state.channel].sort((a, b) => a[0] - b[0]); dragIndex = state.curves[state.channel].findIndex(p => p[0] === pt[0] && p[1] === pt[1]); }
        draw(); applyToLayer();
      });
      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onUp);
      canvas.addEventListener('dblclick', (e) => {
        const idx = nearestIndex(toData(e)); const pts = state.curves[state.channel];
        if (idx > 0 && idx < pts.length - 1) { pts.splice(idx, 1); draw(); applyToLayer(); }
      });
      draw();
    }

    function onMove(e) {
      if (dragIndex < 0) return;
      const pt = toData(e); const pts = state.curves[state.channel];
      const minX = dragIndex === 0 ? 0 : pts[dragIndex - 1][0] + 1;
      const maxX = dragIndex === pts.length - 1 ? 255 : pts[dragIndex + 1][0] - 1;
      pts[dragIndex] = [PIMathUtils.clamp(pt[0], minX, maxX), pt[1]];
      // 端点はX固定
      if (dragIndex === 0) pts[0][0] = 0;
      if (dragIndex === pts.length - 1) pts[pts.length - 1][0] = 255;
      draw(); applyToLayer();
    }
    function onUp() { dragIndex = -1; }

    function cleanup() { window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp); }

    PIModal.show({
      title: 'トーンカーブ',
      description: 'ヒストグラムを見ながら制御点をドラッグ。RGB/各チャンネルを個別編集（ダブルクリックで点削除）。',
      content: [{ type: 'custom', key: 'curve', render }],
      confirmLabel: '適用',
      onConfirm: () => { applyToLayer(); PIHistoryManager.push('トーンカーブ'); cleanup(); },
      onCancel: () => { layer.ctx.putImageData(backup, 0, 0); PILayerManager.requestRender(); cleanup(); }
    });
  }

  return { show, curveLUT };
})();
