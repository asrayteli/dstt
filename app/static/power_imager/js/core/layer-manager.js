/* PowerImager — LayerManager: レイヤー管理 */
window.PILayerManager = (function () {
  let layers = [];
  let activeIndex = -1;
  let idCounter = 0;

  function init() {
    PIEventBus.on('canvas:resized', () => requestRender());
  }

  function createLayer(name, width, height) {
    const size = PICanvasEngine.getCanvasSize();
    const w = width || size.width;
    const h = height || size.height;
    const canvas = document.createElement('canvas');
    canvas.width = w; canvas.height = h;
    const ctx = canvas.getContext('2d');
    PICanvasEngine.configureContext(ctx);
    return {
      id: ++idCounter, name: name || 'Layer ' + idCounter,
      canvas: canvas, ctx: ctx,
      x: 0, y: 0, visible: true, locked: false,
      opacity: 1, blendMode: 'source-over',
      rotation: 0, scaleX: 1, scaleY: 1, clip: false, groupId: null,
      type: 'raster', textData: null, shapeData: null, mask: null, effects: null,
      adjustType: null, adjustParams: null
    };
  }

  // ===== レイヤーグループ（パネル上の整理・折りたたみ・一括表示） =====
  let groups = [];
  let groupIdCounter = 0;

  function getGroups() { return groups; }
  function getGroup(id) { return groups.find(g => g.id === id) || null; }
  function groupMembers(id) { return layers.filter(l => l.groupId === id); }

  function createGroupFromActive() {
    const layer = getActive();
    const id = ++groupIdCounter;
    groups.push({ id, name: 'グループ ' + id, collapsed: false });
    if (layer) layer.groupId = id;
    emitChange();
    return id;
  }

  function addActiveToGroup(groupId) {
    const layer = getActive();
    if (layer && getGroup(groupId)) { layer.groupId = groupId; emitChange(); }
  }

  function removeActiveFromGroup() {
    const layer = getActive();
    if (layer) { layer.groupId = null; emitChange(); }
  }

  function ungroup(groupId) {
    layers.forEach(l => { if (l.groupId === groupId) l.groupId = null; });
    groups = groups.filter(g => g.id !== groupId);
    emitChange();
  }

  function toggleGroupCollapse(id) { const g = getGroup(id); if (g) { g.collapsed = !g.collapsed; emitChange(); } }
  function renameGroup(id, name) { const g = getGroup(id); if (g) { g.name = name; emitChange(); } }
  function setGroupVisible(id, vis) { layers.forEach(l => { if (l.groupId === id) l.visible = vis; }); requestRender(); emitChange(); }

  // 調整レイヤー（非破壊）: 下位の合成結果に補正を適用する
  function addAdjustmentLayer(adjustType, params, name) {
    const layer = createLayer(name || '調整');
    layer.type = 'adjustment';
    layer.adjustType = adjustType;
    layer.adjustParams = params || {};
    // 画素は持たない（マスク/サムネ用にキャンバスは透明のまま）
    layers.push(layer);
    activeIndex = layers.length - 1;
    emitChange();
    return layer;
  }

  function addLayer(name, width, height) {
    const layer = createLayer(name, width, height);
    layers.push(layer);
    activeIndex = layers.length - 1;
    emitChange();
    return layer;
  }

  function addImageLayer(img, name) {
    const layer = createLayer(name || 'Image');
    layer.canvas.width = img.width || img.naturalWidth;
    layer.canvas.height = img.height || img.naturalHeight;
    PICanvasEngine.configureContext(layer.ctx);
    layer.ctx.drawImage(img, 0, 0);
    layers.push(layer);
    activeIndex = layers.length - 1;
    emitChange();
    return layer;
  }

  function addTextLayer(textData) {
    const layer = createLayer(textData.text.substring(0, 10));
    layer.type = 'text';
    layer.textData = { ...textData };
    renderTextLayer(layer);
    layer.x = textData.x || 0;
    layer.y = textData.y || 0;
    layers.push(layer);
    activeIndex = layers.length - 1;
    emitChange();
    return layer;
  }

  function renderTextLayer(layer) {
    if (!layer.textData) return;
    const td = layer.textData;
    const tempCanvas = document.createElement('canvas');
    const tempCtx = tempCanvas.getContext('2d');
    PICanvasEngine.configureContext(tempCtx);

    let fontStr = '';
    if (td.italic) fontStr += 'italic ';
    if (td.bold) fontStr += 'bold ';
    fontStr += td.size + 'px "' + td.fontFamily + '", sans-serif';
    tempCtx.font = fontStr;
    const letterSpacing = (td.letterSpacing || 0) + 'px';
    if ('letterSpacing' in tempCtx) tempCtx.letterSpacing = letterSpacing;

    const lines = td.text.split('\n');
    const lineH = td.size * (td.lineHeight || 1.3);
    let maxW = 0;
    lines.forEach(line => { maxW = Math.max(maxW, tempCtx.measureText(line).width); });

    const padX = (td.strokeWidth || 0) * 2 + 4;
    const padY = (td.strokeWidth || 0) * 2 + 4;
    const totalW = Math.ceil(maxW + padX * 2 + (td.shadowOffsetX || 0) + (td.shadowBlur || 0) * 2);
    const totalH = Math.ceil(lineH * lines.length + padY * 2 + (td.shadowOffsetY || 0) + (td.shadowBlur || 0) * 2);

    layer.canvas.width = totalW;
    layer.canvas.height = totalH;
    const ctx = layer.ctx;
    PICanvasEngine.configureContext(ctx);

    ctx.font = fontStr;
    if ('letterSpacing' in ctx) ctx.letterSpacing = letterSpacing;
    ctx.textBaseline = 'top';
    ctx.textAlign = td.align || 'left';

    let alignX = padX;
    if (td.align === 'center') alignX = totalW / 2;
    else if (td.align === 'right') alignX = totalW - padX;

    if (td.shadowBlur > 0 || td.shadowOffsetX || td.shadowOffsetY) {
      ctx.shadowColor = td.shadowColor || '#000';
      ctx.shadowOffsetX = td.shadowOffsetX || 0;
      ctx.shadowOffsetY = td.shadowOffsetY || 0;
      ctx.shadowBlur = td.shadowBlur || 0;
    }

    lines.forEach((line, i) => {
      const yy = padY + i * lineH;
      if (td.strokeWidth > 0) {
        ctx.strokeStyle = td.strokeColor || '#fff';
        ctx.lineWidth = td.strokeWidth * 2;
        ctx.lineJoin = 'round';
        ctx.strokeText(line, alignX, yy);
      }
      ctx.fillStyle = td.color || '#000';
      ctx.fillText(line, alignX, yy);

      if (td.underline || td.strikethrough) {
        const m = ctx.measureText(line);
        let lx = alignX;
        if (td.align === 'center') lx -= m.width / 2;
        else if (td.align === 'right') lx -= m.width;
        const lh2 = Math.max(1, td.size / 16);
        if (td.underline) ctx.fillRect(lx, yy + td.size + 2, m.width, lh2);
        if (td.strikethrough) ctx.fillRect(lx, yy + td.size * 0.55, m.width, lh2);
      }
    });

    ctx.shadowColor = 'transparent';
    ctx.shadowBlur = 0;
    // 位置はレイヤープロパティ(layer.x/y)で管理する。ここでは上書きしない。
    resyncMask(layer);
  }

  // ===== 図形オブジェクトレイヤー =====
  // shapeData は「ローカル座標（差分）」だけで完結させ、レイヤー位置に依存させない。
  // これにより、塗り/枠/形状などの再描画でも図形が動かない。
  const SHAPE_PAD = 48;

  function addShapeLayer(shapeData) {
    const layer = createLayer('図形');
    layer.type = 'shape';
    layer.shapeData = { ...shapeData };
    renderShapeLayer(layer);
    layer.x = Math.round((shapeData.bx || 0) - SHAPE_PAD);
    layer.y = Math.round((shapeData.by || 0) - SHAPE_PAD);
    layers.push(layer);
    activeIndex = layers.length - 1;
    emitChange();
    return layer;
  }

  function roundedRectPath(ctx, x, y, w, h, r) {
    const rr = Math.max(0, Math.min(r || 0, Math.min(w, h) / 2));
    ctx.beginPath();
    ctx.moveTo(x + rr, y);
    ctx.arcTo(x + w, y, x + w, y + h, rr);
    ctx.arcTo(x + w, y + h, x, y + h, rr);
    ctx.arcTo(x, y + h, x, y, rr);
    ctx.arcTo(x, y, x + w, y, rr);
    ctx.closePath();
  }

  function renderShapeLayer(layer) {
    const sd = layer.shapeData;
    if (!sd) return;
    const isLine = sd.shapeType === 'line' || sd.shapeType === 'arrow';
    let bw, bh;
    if (isLine) { bw = Math.abs(sd.x2 - sd.x1); bh = Math.abs(sd.y2 - sd.y1); }
    else { bw = sd.w; bh = sd.h; }
    const cw = Math.max(1, Math.ceil(bw + SHAPE_PAD * 2));
    const ch = Math.max(1, Math.ceil(bh + SHAPE_PAD * 2));
    layer.canvas.width = cw; layer.canvas.height = ch;
    const ctx = layer.ctx;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    PICanvasEngine.configureContext(ctx);
    ctx.clearRect(0, 0, cw, ch);
    ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    const ox = SHAPE_PAD, oy = SHAPE_PAD;
    const sw = sd.strokeWidth || 0;

    if (sd.shapeType === 'rect') {
      roundedRectPath(ctx, ox, oy, bw, bh, sd.cornerRadius || 0);
      if (sd.fillEnabled) { ctx.fillStyle = sd.fillColor; ctx.fill(); }
      if (sd.strokeEnabled && sw > 0) { ctx.strokeStyle = sd.strokeColor; ctx.lineWidth = sw; ctx.stroke(); }
    } else if (sd.shapeType === 'ellipse') {
      ctx.beginPath();
      ctx.ellipse(ox + bw / 2, oy + bh / 2, bw / 2, bh / 2, 0, 0, Math.PI * 2);
      if (sd.fillEnabled) { ctx.fillStyle = sd.fillColor; ctx.fill(); }
      if (sd.strokeEnabled && sw > 0) { ctx.strokeStyle = sd.strokeColor; ctx.lineWidth = sw; ctx.stroke(); }
    } else if (isLine) {
      const minX = Math.min(sd.x1, sd.x2), minY = Math.min(sd.y1, sd.y2);
      const lx1 = ox + (sd.x1 - minX), ly1 = oy + (sd.y1 - minY);
      const lx2 = ox + (sd.x2 - minX), ly2 = oy + (sd.y2 - minY);
      ctx.strokeStyle = sd.strokeColor; ctx.lineWidth = Math.max(1, sw);
      ctx.beginPath(); ctx.moveTo(lx1, ly1); ctx.lineTo(lx2, ly2); ctx.stroke();
      if (sd.shapeType === 'arrow') {
        const angle = Math.atan2(ly2 - ly1, lx2 - lx1);
        const headLen = 12 + sw * 2;
        ctx.beginPath();
        ctx.moveTo(lx2, ly2);
        ctx.lineTo(lx2 - headLen * Math.cos(angle - Math.PI / 6), ly2 - headLen * Math.sin(angle - Math.PI / 6));
        ctx.moveTo(lx2, ly2);
        ctx.lineTo(lx2 - headLen * Math.cos(angle + Math.PI / 6), ly2 - headLen * Math.sin(angle + Math.PI / 6));
        ctx.stroke();
      }
    }
    resyncMask(layer);
  }

  function removeLayer(index) {
    if (layers.length <= 1) return;
    layers.splice(index, 1);
    if (activeIndex >= layers.length) activeIndex = layers.length - 1;
    emitChange();
  }

  function duplicateLayer(index) {
    const src = layers[index];
    if (!src) return;
    const dup = createLayer(src.name + ' copy');
    dup.canvas.width = src.canvas.width;
    dup.canvas.height = src.canvas.height;
    PICanvasEngine.configureContext(dup.ctx);
    dup.ctx.drawImage(src.canvas, 0, 0);
    dup.x = src.x; dup.y = src.y;
    dup.opacity = src.opacity; dup.blendMode = src.blendMode;
    dup.rotation = src.rotation || 0; dup.scaleX = src.scaleX || 1; dup.scaleY = src.scaleY || 1;
    dup.clip = !!src.clip;
    dup.groupId = src.groupId || null;
    dup.type = src.type;
    if (src.textData) dup.textData = { ...src.textData };
    if (src.shapeData) dup.shapeData = { ...src.shapeData };
    if (src.effects) dup.effects = JSON.parse(JSON.stringify(src.effects));
    if (src.adjustType) { dup.adjustType = src.adjustType; dup.adjustParams = JSON.parse(JSON.stringify(src.adjustParams || {})); }
    if (src.mask) {
      const mc = document.createElement('canvas');
      mc.width = src.mask.width; mc.height = src.mask.height;
      mc.getContext('2d').drawImage(src.mask, 0, 0);
      dup.mask = mc;
    }
    layers.splice(index + 1, 0, dup);
    activeIndex = index + 1;
    emitChange();
  }

  function moveLayer(from, to) {
    if (from < 0 || from >= layers.length || to < 0 || to >= layers.length) return;
    const [item] = layers.splice(from, 1);
    layers.splice(to, 0, item);
    activeIndex = to;
    emitChange();
  }

  function mergeDown(index) {
    if (index <= 0 || index >= layers.length) return;
    // 変形を持つレイヤーは見た目どおりにラスター化してから結合する。
    bakeLayerToRaster(layers[index]);
    bakeLayerToRaster(layers[index - 1]);
    const upper = layers[index];
    const lower = layers[index - 1];
    lower.ctx.save();
    PICanvasEngine.configureContext(lower.ctx);
    lower.ctx.globalAlpha = upper.opacity;
    lower.ctx.globalCompositeOperation = upper.blendMode || 'source-over';
    lower.ctx.drawImage(upper.canvas, upper.x - lower.x, upper.y - lower.y);
    lower.ctx.restore();
    layers.splice(index, 1);
    activeIndex = index - 1;
    emitChange();
  }

  function flattenAll() {
    const size = PICanvasEngine.getCanvasSize();
    const result = document.createElement('canvas');
    result.width = size.width; result.height = size.height;
    const ctx = result.getContext('2d');
    PICanvasEngine.configureContext(ctx);
    PICanvasEngine.compositeStack(ctx, layers);
    return result;
  }

  // 回転・拡縮を含むレイヤーをその見た目どおりにラスター化し、軸並行レイヤーへ戻す。
  // 切り抜き/反転/サイズ変更/結合など、ピクセル直接操作の前に呼ぶ。
  function maskedCanvasOf(layer) {
    // マスクを適用した画素のコピーを返す（マスクが無ければ元canvas）
    if (!layer.mask) return layer.canvas;
    const c = document.createElement('canvas');
    c.width = layer.canvas.width; c.height = layer.canvas.height;
    const ctx = c.getContext('2d');
    PICanvasEngine.configureContext(ctx);
    ctx.drawImage(layer.canvas, 0, 0);
    ctx.globalCompositeOperation = 'destination-in';
    ctx.drawImage(layer.mask, 0, 0);
    return c;
  }

  function bakeLayerToRaster(layer) {
    const rot = layer.rotation || 0;
    const sx = layer.scaleX || 1, sy = layer.scaleY || 1;
    if (rot === 0 && sx === 1 && sy === 1) {
      if (layer.mask) {
        // 変形は無いがマスクはあるので画素へ焼き込む
        const baked = maskedCanvasOf(layer);
        layer.canvas = baked;
        layer.ctx = baked.getContext('2d');
        PICanvasEngine.configureContext(layer.ctx);
        layer.mask = null;
      }
      return layer;
    }
    const srcCanvas = maskedCanvasOf(layer);
    const w = layer.canvas.width, h = layer.canvas.height;
    // 回転後バウンディングボックス
    const corners = [[-w / 2, -h / 2], [w / 2, -h / 2], [w / 2, h / 2], [-w / 2, h / 2]];
    const cos = Math.cos(rot), sin = Math.sin(rot);
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    corners.forEach(([px, py]) => {
      const X = px * sx * cos - py * sy * sin;
      const Y = px * sx * sin + py * sy * cos;
      minX = Math.min(minX, X); maxX = Math.max(maxX, X);
      minY = Math.min(minY, Y); maxY = Math.max(maxY, Y);
    });
    const cx = layer.x + w / 2, cy = layer.y + h / 2;
    const nw = Math.max(1, Math.ceil(maxX - minX));
    const nh = Math.max(1, Math.ceil(maxY - minY));
    const baked = document.createElement('canvas');
    baked.width = nw; baked.height = nh;
    const bctx = baked.getContext('2d');
    PICanvasEngine.configureContext(bctx);
    bctx.translate(nw / 2, nh / 2);
    bctx.rotate(rot);
    bctx.scale(sx, sy);
    bctx.drawImage(srcCanvas, -w / 2, -h / 2);
    layer.canvas = baked;
    layer.ctx = baked.getContext('2d');
    PICanvasEngine.configureContext(layer.ctx);
    layer.x = Math.round(cx - nw / 2);
    layer.y = Math.round(cy - nh / 2);
    layer.rotation = 0; layer.scaleX = 1; layer.scaleY = 1;
    layer.type = 'raster'; layer.textData = null; layer.shapeData = null;
    layer.mask = null;
    return layer;
  }

  // ===== レイヤーマスク（非破壊：マスクのα＝可視度） =====
  let maskEditing = false;

  function addMask(layer) {
    if (!layer || layer.mask) return;
    const m = document.createElement('canvas');
    m.width = layer.canvas.width; m.height = layer.canvas.height;
    const mctx = m.getContext('2d');
    mctx.fillStyle = '#ffffff'; // 全面可視（α255）で開始
    mctx.fillRect(0, 0, m.width, m.height);
    layer.mask = m;
    emitChange();
  }

  function removeMask(layer, apply) {
    if (!layer || !layer.mask) return;
    if (apply) {
      // マスクを画素へ焼き込んでから削除
      const ctx = layer.ctx;
      ctx.save();
      ctx.globalCompositeOperation = 'destination-in';
      ctx.drawImage(layer.mask, 0, 0);
      ctx.restore();
    }
    layer.mask = null;
    if (getActive() !== layer) {} else if (maskEditing) maskEditing = false;
    emitChange();
  }

  function resyncMask(layer) {
    // レイヤーcanvasサイズ変更時にマスクを追従させる（縦横比に合わせて伸縮）
    if (!layer.mask) return;
    if (layer.mask.width === layer.canvas.width && layer.mask.height === layer.canvas.height) return;
    const m = document.createElement('canvas');
    m.width = layer.canvas.width; m.height = layer.canvas.height;
    const mctx = m.getContext('2d');
    PICanvasEngine.configureContext(mctx);
    mctx.drawImage(layer.mask, 0, 0, m.width, m.height);
    layer.mask = m;
  }

  function setMaskEditing(on) { maskEditing = !!on; PIEventBus.emit('mask:mode-changed', maskEditing); }
  function isMaskEditing(layer) { return maskEditing && !!(layer && layer.mask) && layer === getActive(); }
  function getMaskEditing() { return maskEditing; }

  // 描画系ツールが安全に描けるよう、変形（回転/拡縮）があれば見た目どおり確定する。
  // 透明背景のラスターはそのまま。戻り値は描画先レイヤー（描画不可なら null）。
  function ensurePaintable(layer) {
    if (!layer) return null;
    // 調整レイヤーは画素を持たないため描いても何も表示されない
    if (layer.type === 'adjustment') {
      PIEventBus.emit('toast', '調整レイヤーには描画できません。別のレイヤーを選択してください');
      return null;
    }
    const transformed = (layer.rotation || 0) !== 0 || (layer.scaleX || 1) !== 1 || (layer.scaleY || 1) !== 1;
    if (transformed) {
      bakeLayerToRaster(layer);
    } else if (layer.type === 'text' || layer.type === 'shape') {
      // テキスト/図形の画素へ直接描くと、後の再編集（再レンダリング）で描き込みが消えてしまう。
      // ラスター化してから描き、その旨を通知する。
      const kind = layer.type === 'text' ? 'テキスト' : '図形';
      layer.type = 'raster'; layer.textData = null; layer.shapeData = null;
      PIEventBus.emit('toast', kind + 'レイヤーをラスター化して描画します（' + kind + 'としての再編集は不可）');
      PIEventBus.emit('layers:changed', { layers: layers, activeIndex: activeIndex });
    }
    return layer;
  }

  // クリッピングマスクの切替（最下段は基底が無いのでクリップ不可）
  function toggleClip(index) {
    if (index <= 0 || index >= layers.length) return;
    layers[index].clip = !layers[index].clip;
    emitChange();
  }

  function getActive() { return layers[activeIndex] || null; }
  function getActiveIndex() { return activeIndex; }
  function setActiveIndex(i) { activeIndex = i; emitChange(); }
  function getAll() { return layers; }
  function getById(id) { return layers.find(l => l.id === id) || null; }

  function snapshot() {
    return layers.map(l => {
      const c = document.createElement('canvas');
      c.width = l.canvas.width; c.height = l.canvas.height;
      const ctx = c.getContext('2d');
      PICanvasEngine.configureContext(ctx);
      ctx.drawImage(l.canvas, 0, 0);
      let maskCopy = null;
      if (l.mask) {
        maskCopy = document.createElement('canvas');
        maskCopy.width = l.mask.width; maskCopy.height = l.mask.height;
        maskCopy.getContext('2d').drawImage(l.mask, 0, 0);
      }
      return {
        id: l.id, name: l.name, canvas: c, ctx: ctx,
        x: l.x, y: l.y, visible: l.visible, locked: l.locked,
        opacity: l.opacity, blendMode: l.blendMode,
        rotation: l.rotation || 0, scaleX: l.scaleX || 1, scaleY: l.scaleY || 1, clip: !!l.clip, groupId: l.groupId || null,
        type: l.type, textData: l.textData ? { ...l.textData } : null,
        shapeData: l.shapeData ? { ...l.shapeData } : null,
        mask: maskCopy,
        effects: l.effects ? JSON.parse(JSON.stringify(l.effects)) : null,
        adjustType: l.adjustType || null,
        adjustParams: l.adjustParams ? JSON.parse(JSON.stringify(l.adjustParams)) : null
      };
    });
  }

  function restore(snap) {
    layers = snap.map(s => {
      const c = document.createElement('canvas');
      c.width = s.canvas.width; c.height = s.canvas.height;
      const ctx = c.getContext('2d');
      PICanvasEngine.configureContext(ctx);
      ctx.drawImage(s.canvas, 0, 0);
      let maskCopy = null;
      if (s.mask) {
        maskCopy = document.createElement('canvas');
        maskCopy.width = s.mask.width; maskCopy.height = s.mask.height;
        maskCopy.getContext('2d').drawImage(s.mask, 0, 0);
      }
      return {
        id: s.id, name: s.name, canvas: c, ctx: ctx,
        x: s.x, y: s.y, visible: s.visible, locked: s.locked,
        opacity: s.opacity, blendMode: s.blendMode,
        rotation: s.rotation || 0, scaleX: s.scaleX || 1, scaleY: s.scaleY || 1, clip: !!s.clip, groupId: s.groupId || null,
        type: s.type, textData: s.textData ? { ...s.textData } : null,
        shapeData: s.shapeData ? { ...s.shapeData } : null,
        mask: maskCopy,
        effects: s.effects ? JSON.parse(JSON.stringify(s.effects)) : null,
        adjustType: s.adjustType || null,
        adjustParams: s.adjustParams ? JSON.parse(JSON.stringify(s.adjustParams)) : null
      };
    });
    if (activeIndex >= layers.length) activeIndex = layers.length - 1;
    emitChange();
  }

  function requestRender() {
    PICanvasEngine.renderLayers(layers);
  }

  function emitChange() {
    requestRender();
    PIEventBus.emit('layers:changed', { layers, activeIndex });
  }

  function clear() {
    layers = [];
    activeIndex = -1;
    idCounter = 0;
    groups = [];
  }

  return {
    init, addLayer, addImageLayer, addTextLayer, renderTextLayer,
    addShapeLayer, renderShapeLayer, addAdjustmentLayer, bakeLayerToRaster, ensurePaintable, SHAPE_PAD,
    addMask, removeMask, setMaskEditing, isMaskEditing, getMaskEditing, toggleClip,
    getGroups, getGroup, groupMembers, createGroupFromActive, addActiveToGroup,
    removeActiveFromGroup, ungroup, toggleGroupCollapse, renameGroup, setGroupVisible,
    removeLayer, duplicateLayer, moveLayer, mergeDown, flattenAll,
    getActive, getActiveIndex, setActiveIndex, getAll, getById,
    snapshot, restore, requestRender, clear, createLayer
  };
})();
