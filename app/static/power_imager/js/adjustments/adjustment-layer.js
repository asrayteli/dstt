/* PowerImager — AdjustmentLayer: 非破壊の調整レイヤー
 * 下位レイヤーの合成結果へ補正を適用する。マスク/不透明度/クリップに対応。
 */
window.PIAdjustmentLayer = (function () {
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

  const TYPES = {
    brightness: { name: '明るさ・コントラスト', params: { brightness: 0, contrast: 0 } },
    hue: { name: '色相・彩度', params: { hue: 0, sat: 0, light: 0 } },
    posterize: { name: 'ポスタライズ', params: { levels: 6 } },
    threshold: { name: 'しきい値', params: { threshold: 128 } },
    grayscale: { name: 'グレースケール', params: {} },
    sepia: { name: 'セピア', params: {} },
    invert: { name: '色反転', params: {} }
  };

  function applyPixels(data, type, p) {
    p = p || {};
    if (type === 'invert') { for (let i = 0; i < data.length; i += 4) { data[i] = 255 - data[i]; data[i + 1] = 255 - data[i + 1]; data[i + 2] = 255 - data[i + 2]; } return; }
    if (type === 'grayscale') { for (let i = 0; i < data.length; i += 4) { const l = data[i] * 0.299 + data[i + 1] * 0.587 + data[i + 2] * 0.114; data[i] = data[i + 1] = data[i + 2] = l; } return; }
    if (type === 'sepia') { for (let i = 0; i < data.length; i += 4) { const r = data[i], g = data[i + 1], b = data[i + 2]; data[i] = Math.min(255, r * 0.393 + g * 0.769 + b * 0.189); data[i + 1] = Math.min(255, r * 0.349 + g * 0.686 + b * 0.168); data[i + 2] = Math.min(255, r * 0.272 + g * 0.534 + b * 0.131); } return; }
    if (type === 'brightness') { const b = p.brightness || 0; const cc = p.contrast || 0; const c = (259 * (cc + 255)) / (255 * (259 - cc)); for (let i = 0; i < data.length; i += 4) { for (let k = 0; k < 3; k++) data[i + k] = clamp(c * (data[i + k] - 128) + 128 + b, 0, 255); } return; }
    if (type === 'posterize') { const step = 255 / Math.max(1, (p.levels || 6) - 1); for (let i = 0; i < data.length; i += 4) { for (let k = 0; k < 3; k++) data[i + k] = Math.round(Math.round(data[i + k] / step) * step); } return; }
    if (type === 'threshold') { const t = p.threshold || 128; for (let i = 0; i < data.length; i += 4) { const l = data[i] * 0.299 + data[i + 1] * 0.587 + data[i + 2] * 0.114; const v = l >= t ? 255 : 0; data[i] = data[i + 1] = data[i + 2] = v; } return; }
    if (type === 'hue') { const dh = p.hue || 0, ds = p.sat || 0, dl = p.light || 0; for (let i = 0; i < data.length; i += 4) { const hsl = PIColorUtils.rgbToHsl(data[i], data[i + 1], data[i + 2]); hsl.h = (hsl.h + dh + 360) % 360; hsl.s = clamp(hsl.s * (1 + ds / 100), 0, 100); hsl.l = clamp(hsl.l + dl, 0, 100); const rgb = PIColorUtils.hslToRgb(hsl.h, hsl.s, hsl.l); data[i] = rgb.r; data[i + 1] = rgb.g; data[i + 2] = rgb.b; } return; }
  }

  // 合成先 ctx の内容へ調整を適用（不透明度・マスクで加減）
  function applyToCtx(ctx, adj, w, h) {
    const img = ctx.getImageData(0, 0, w, h);
    const data = img.data;
    const orig = new Uint8ClampedArray(data);
    applyPixels(data, adj.adjustType, adj.adjustParams);
    const op = adj.opacity != null ? adj.opacity : 1;
    let md = null;
    if (adj.mask) md = adj.mask.getContext('2d').getImageData(0, 0, adj.mask.width, adj.mask.height).data;
    if (op < 1 || md) {
      for (let i = 0; i < data.length; i += 4) {
        let a = op;
        if (md) a *= md[i + 3] / 255;
        if (a >= 1) continue;
        data[i] = orig[i] * (1 - a) + data[i] * a;
        data[i + 1] = orig[i + 1] * (1 - a) + data[i + 1] * a;
        data[i + 2] = orig[i + 2] * (1 - a) + data[i + 2] * a;
      }
    }
    ctx.putImageData(img, 0, 0);
  }

  function content(type, p) {
    if (type === 'brightness') return [
      { type: 'slider', label: '明るさ', key: 'brightness', value: p.brightness, min: -100, max: 100 },
      { type: 'slider', label: 'コントラスト', key: 'contrast', value: p.contrast, min: -100, max: 100 }
    ];
    if (type === 'hue') return [
      { type: 'slider', label: '色相', key: 'hue', value: p.hue, min: -180, max: 180 },
      { type: 'slider', label: '彩度', key: 'sat', value: p.sat, min: -100, max: 100 },
      { type: 'slider', label: '明度', key: 'light', value: p.light, min: -100, max: 100 }
    ];
    if (type === 'posterize') return [{ type: 'slider', label: '階調数', key: 'levels', value: p.levels, min: 2, max: 32 }];
    if (type === 'threshold') return [{ type: 'slider', label: 'しきい値', key: 'threshold', value: p.threshold, min: 0, max: 255 }];
    return null;
  }

  function editDialog(layer) {
    if (!layer || layer.type !== 'adjustment') return;
    const type = layer.adjustType;
    const p = layer.adjustParams;
    const fields = content(type, p);
    if (!fields) return; // パラメータ無し（グレースケール等）
    const backup = JSON.parse(JSON.stringify(p));
    PIModal.show({
      title: TYPES[type].name + '（調整レイヤー）',
      description: '下のレイヤーへ非破壊で適用します。マスクや不透明度でも加減できます。',
      content: fields,
      onPreview: (v) => { Object.assign(p, numeric(v)); PILayerManager.requestRender(); },
      confirmLabel: '適用',
      onConfirm: (v) => { Object.assign(p, numeric(v)); PIHistoryManager.push('調整レイヤー'); PILayerManager.requestRender(); },
      onCancel: () => { Object.keys(p).forEach(k => delete p[k]); Object.assign(p, backup); PILayerManager.requestRender(); }
    });
  }

  function numeric(v) { const o = {}; Object.keys(v).forEach(k => { o[k] = Number(v[k]); }); return o; }

  function add(type) {
    if (!TYPES[type]) return;
    const layer = PILayerManager.addAdjustmentLayer(type, JSON.parse(JSON.stringify(TYPES[type].params)), TYPES[type].name);
    const fields = content(type, layer.adjustParams);
    PILayerManager.requestRender();
    if (fields) editDialog(layer);
    else PIHistoryManager.push('調整レイヤー');
    PIEventBus.emit('toast', TYPES[type].name + 'の調整レイヤーを追加');
  }

  function typeName(type) { return TYPES[type] ? TYPES[type].name : type; }

  return { applyToCtx, applyPixels, editDialog, add, typeName, TYPES };
})();
