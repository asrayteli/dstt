/* PowerImager — LayerPanel: レイヤーパネル */
window.PILayerPanel = (function () {
  let container;

  function init() {
    container = document.getElementById('layer-panel-body');
    PIEventBus.on('layers:changed', () => render());
  }

  function render() {
    if (!container) return;
    container.innerHTML = '';

    const layers = PILayerManager.getAll();
    const activeIdx = PILayerManager.getActiveIndex();

    const actions = document.createElement('div');
    actions.className = 'layer-panel-actions';
    const addBtn = createBtn('+', () => { PILayerManager.addLayer(); PIHistoryManager.push('レイヤー追加'); });
    const dupBtn = createBtn('複製', () => { PILayerManager.duplicateLayer(activeIdx); PIHistoryManager.push('複製'); });
    const delBtn = createBtn('削除', () => { PILayerManager.removeLayer(activeIdx); PIHistoryManager.push('削除'); });
    const upBtn = createBtn('↑', () => { if (activeIdx < layers.length - 1) PILayerManager.moveLayer(activeIdx, activeIdx + 1); });
    const downBtn = createBtn('↓', () => { if (activeIdx > 0) PILayerManager.moveLayer(activeIdx, activeIdx - 1); });
    const mergeBtn = createBtn('統合', () => { PILayerManager.mergeDown(activeIdx); PIHistoryManager.push('統合'); });
    actions.append(addBtn, dupBtn, delBtn, upBtn, downBtn, mergeBtn);
    container.appendChild(actions);

    const blendDiv = document.createElement('div');
    blendDiv.className = 'layer-blend-mode';
    blendDiv.style.padding = '4px 6px';
    const blendSel = document.createElement('select');
    ['source-over','multiply','screen','overlay','darken','lighten','color-dodge','color-burn','hard-light','soft-light','difference','exclusion'].forEach(mode => {
      const o = document.createElement('option');
      o.value = mode; o.textContent = mode;
      const active = PILayerManager.getActive();
      if (active && active.blendMode === mode) o.selected = true;
      blendSel.appendChild(o);
    });
    blendSel.addEventListener('change', () => {
      const layer = PILayerManager.getActive();
      if (layer) { layer.blendMode = blendSel.value; PILayerManager.requestRender(); }
    });
    blendDiv.appendChild(blendSel);
    container.appendChild(blendDiv);

    const opacDiv = document.createElement('div');
    opacDiv.className = 'layer-opacity';
    const opacLabel = document.createElement('span');
    opacLabel.textContent = '不透明度';
    const opacSlider = document.createElement('input');
    opacSlider.type = 'range'; opacSlider.min = 0; opacSlider.max = 100;
    const active = PILayerManager.getActive();
    opacSlider.value = active ? Math.round(active.opacity * 100) : 100;
    const opacVal = document.createElement('span');
    opacVal.textContent = opacSlider.value + '%';
    opacSlider.addEventListener('input', () => {
      const layer = PILayerManager.getActive();
      if (layer) { layer.opacity = parseInt(opacSlider.value) / 100; PILayerManager.requestRender(); }
      opacVal.textContent = opacSlider.value + '%';
    });
    opacDiv.append(opacLabel, opacSlider, opacVal);
    container.appendChild(opacDiv);

    const listEl = document.createElement('div');
    listEl.className = 'layer-list';

    for (let i = layers.length - 1; i >= 0; i--) {
      const layer = layers[i];
      const item = document.createElement('div');
      item.className = 'layer-item' + (i === activeIdx ? ' active' : '');
      item.addEventListener('click', () => PILayerManager.setActiveIndex(i));

      const thumb = document.createElement('canvas');
      thumb.className = 'layer-thumb';
      thumb.width = 32; thumb.height = 32;
      const tctx = thumb.getContext('2d');
      PICanvasEngine.configureContext(tctx);
      const scale = Math.min(32 / layer.canvas.width, 32 / layer.canvas.height);
      const tw = layer.canvas.width * scale, th = layer.canvas.height * scale;
      tctx.drawImage(layer.canvas, (32 - tw) / 2, (32 - th) / 2, tw, th);

      const name = document.createElement('span');
      name.className = 'layer-name';
      name.textContent = layer.name;

      const actionsDiv = document.createElement('div');
      actionsDiv.className = 'layer-actions';
      const visBtn = createSmallBtn(layer.visible ? '👁' : '—', () => {
        layer.visible = !layer.visible; PILayerManager.requestRender();
        PIEventBus.emit('layers:changed', { layers: PILayerManager.getAll(), activeIndex: PILayerManager.getActiveIndex() });
      });
      if (layer.visible) visBtn.classList.add('active');
      const lockBtn = createSmallBtn(layer.locked ? '🔒' : '🔓', () => {
        layer.locked = !layer.locked;
        PIEventBus.emit('layers:changed', { layers: PILayerManager.getAll(), activeIndex: PILayerManager.getActiveIndex() });
      });
      actionsDiv.append(visBtn, lockBtn);

      item.append(thumb, name, actionsDiv);
      listEl.appendChild(item);
    }

    container.appendChild(listEl);
  }

  function createBtn(text, onClick) {
    const btn = document.createElement('button');
    btn.textContent = text;
    btn.addEventListener('click', (e) => { e.stopPropagation(); onClick(); });
    return btn;
  }
  function createSmallBtn(text, onClick) {
    const btn = document.createElement('button');
    btn.textContent = text;
    btn.addEventListener('click', (e) => { e.stopPropagation(); onClick(); });
    return btn;
  }

  return { init, render };
})();
