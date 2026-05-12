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
    return {
      id: ++idCounter, name: name || 'Layer ' + idCounter,
      canvas: canvas, ctx: canvas.getContext('2d'),
      x: 0, y: 0, visible: true, locked: false,
      opacity: 1, blendMode: 'source-over',
      type: 'raster', textData: null
    };
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

    let fontStr = '';
    if (td.italic) fontStr += 'italic ';
    if (td.bold) fontStr += 'bold ';
    fontStr += td.size + 'px "' + td.fontFamily + '", sans-serif';
    tempCtx.font = fontStr;

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

    ctx.font = fontStr;
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

      if (td.underline) {
        const m = ctx.measureText(line);
        let lx = alignX;
        if (td.align === 'center') lx -= m.width / 2;
        else if (td.align === 'right') lx -= m.width;
        ctx.fillRect(lx, yy + td.size + 2, m.width, Math.max(1, td.size / 16));
      }
    });

    ctx.shadowColor = 'transparent';
    ctx.shadowBlur = 0;

    layer.x = td.x || 0;
    layer.y = td.y || 0;
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
    dup.ctx.drawImage(src.canvas, 0, 0);
    dup.x = src.x; dup.y = src.y;
    dup.opacity = src.opacity; dup.blendMode = src.blendMode;
    dup.type = src.type;
    if (src.textData) dup.textData = { ...src.textData };
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
    const upper = layers[index];
    const lower = layers[index - 1];
    lower.ctx.save();
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
    layers.forEach(l => {
      if (!l.visible) return;
      ctx.save();
      ctx.globalAlpha = l.opacity;
      ctx.globalCompositeOperation = l.blendMode || 'source-over';
      ctx.drawImage(l.canvas, l.x, l.y);
      ctx.restore();
    });
    return result;
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
      c.getContext('2d').drawImage(l.canvas, 0, 0);
      return {
        id: l.id, name: l.name, canvas: c, ctx: c.getContext('2d'),
        x: l.x, y: l.y, visible: l.visible, locked: l.locked,
        opacity: l.opacity, blendMode: l.blendMode,
        type: l.type, textData: l.textData ? { ...l.textData } : null
      };
    });
  }

  function restore(snap) {
    layers = snap.map(s => {
      const c = document.createElement('canvas');
      c.width = s.canvas.width; c.height = s.canvas.height;
      c.getContext('2d').drawImage(s.canvas, 0, 0);
      return {
        id: s.id, name: s.name, canvas: c, ctx: c.getContext('2d'),
        x: s.x, y: s.y, visible: s.visible, locked: s.locked,
        opacity: s.opacity, blendMode: s.blendMode,
        type: s.type, textData: s.textData ? { ...s.textData } : null
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
  }

  return {
    init, addLayer, addImageLayer, addTextLayer, renderTextLayer,
    removeLayer, duplicateLayer, moveLayer, mergeDown, flattenAll,
    getActive, getActiveIndex, setActiveIndex, getAll, getById,
    snapshot, restore, requestRender, clear, createLayer
  };
})();
