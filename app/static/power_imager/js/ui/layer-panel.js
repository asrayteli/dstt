/* PowerImager — LayerPanel: レイヤーパネル */
window.PILayerPanel = (function () {
  let container;
  let suppressClick = false; // ドラッグ直後のclickでアクティブ切替が走らないように

  function init() {
    container = document.getElementById('layer-panel-body');
    PIEventBus.on('layers:changed', () => render());
    PIEventBus.on('mask:mode-changed', () => render());
  }

  function render() {
    if (!container) return;
    container.innerHTML = '';

    const layers = PILayerManager.getAll();
    const activeIdx = PILayerManager.getActiveIndex();

    const actions = document.createElement('div');
    actions.className = 'layer-panel-actions';
    const addBtn = createBtn('+', () => { PILayerManager.addLayer(); PIHistoryManager.push('レイヤー追加'); }, '新規レイヤーを追加');
    const dupBtn = createBtn('複製', () => { PILayerManager.duplicateLayer(activeIdx); PIHistoryManager.push('複製'); }, 'アクティブレイヤーを複製');
    const delBtn = createBtn('削除', () => { PILayerManager.removeLayer(activeIdx); PIHistoryManager.push('削除'); }, 'アクティブレイヤーを削除');
    const upBtn = createBtn('↑', () => { if (activeIdx < layers.length - 1) PILayerManager.moveLayer(activeIdx, activeIdx + 1); }, '前面へ移動');
    const downBtn = createBtn('↓', () => { if (activeIdx > 0) PILayerManager.moveLayer(activeIdx, activeIdx - 1); }, '背面へ移動');
    const mergeBtn = createBtn('統合', () => { PILayerManager.mergeDown(activeIdx); PIHistoryManager.push('統合'); }, '下のレイヤーと統合');
    const maskBtn = createBtn('マスク', () => {
      const layer = PILayerManager.getActive();
      if (!layer) return;
      if (!layer.mask) { PILayerManager.addMask(layer); PILayerManager.setMaskEditing(true); PIHistoryManager.push('マスク追加'); }
      else { PILayerManager.setMaskEditing(!PILayerManager.getMaskEditing()); }
    }, 'レイヤーマスクを追加/編集');
    const editingMask = PILayerManager.isMaskEditing(PILayerManager.getActive());
    if (editingMask) maskBtn.classList.add('active');
    const fxBtn = createBtn('効果', () => openEffectsDialog(), 'レイヤースタイル（影・境界線・光彩）');
    const activeFx = PILayerManager.getActive();
    if (activeFx && activeFx.effects && hasFx(activeFx.effects)) fxBtn.classList.add('active');
    const clipBtn = createBtn('クリップ', () => { PILayerManager.toggleClip(activeIdx); PIHistoryManager.push('クリッピング'); }, '下のレイヤーの形にクリップ');
    if (activeFx && activeFx.clip) clipBtn.classList.add('active');
    const groupBtn = createBtn('グループ化', () => { PILayerManager.createGroupFromActive(); PIHistoryManager.push('グループ化'); }, 'アクティブレイヤーでグループを作成');
    actions.append(addBtn, dupBtn, delBtn, upBtn, downBtn, mergeBtn, maskBtn, fxBtn, clipBtn, groupBtn);
    container.appendChild(actions);

    if (editingMask) {
      const banner = document.createElement('div');
      banner.className = 'mask-edit-banner';
      banner.textContent = 'マスク編集中：ブラシ＝隠す / 消しゴム＝戻す';
      container.appendChild(banner);
    }

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
      if (layer) { layer.blendMode = blendSel.value; PILayerManager.requestRender(); PIHistoryManager.push('ブレンドモード'); }
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
    // ドラッグ確定時に履歴へ（Ctrl+Zで戻せるように）
    opacSlider.addEventListener('change', () => { PIHistoryManager.push('不透明度変更'); });
    opacDiv.append(opacLabel, opacSlider, opacVal);
    container.appendChild(opacDiv);

    const listEl = document.createElement('div');
    listEl.className = 'layer-list';

    const makeItem = (layer, i) => {
      const item = document.createElement('div');
      item.className = 'layer-item' + (i === activeIdx ? ' active' : '');
      item.dataset.idx = i;
      // 同じレイヤーの再クリックで再レンダリングしない（ダブルクリック操作を壊さない）
      item.addEventListener('click', () => {
        if (suppressClick) return;
        if (PILayerManager.getActiveIndex() !== i) PILayerManager.setActiveIndex(i);
      });
      // ドラッグで並べ替え（ボタン/入力欄/マスクサムネから始まるドラッグは除外）
      item.addEventListener('mousedown', (e) => {
        if (e.button !== 0) return;
        if (e.target.closest('button, input, .layer-mask-thumb')) return;
        startLayerDrag(e, layer, item, listEl);
      });
      if (layer.type === 'adjustment') {
        item.addEventListener('dblclick', () => { PILayerManager.setActiveIndex(i); PIAdjustmentLayer.editDialog(layer); });
      }

      const thumb = document.createElement('canvas');
      thumb.className = 'layer-thumb';
      thumb.width = 32; thumb.height = 32;
      const tctx = thumb.getContext('2d');
      PICanvasEngine.configureContext(tctx);
      if (layer.type === 'adjustment') {
        // 調整レイヤーは画素を持たないのでアイコン表示
        tctx.fillStyle = '#2b2b3c'; tctx.fillRect(0, 0, 32, 32);
        tctx.fillStyle = '#fff'; tctx.beginPath(); tctx.arc(16, 16, 9, -Math.PI / 2, Math.PI / 2); tctx.fill();
        tctx.strokeStyle = '#7c6ff7'; tctx.lineWidth = 1.5; tctx.beginPath(); tctx.arc(16, 16, 9, 0, Math.PI * 2); tctx.stroke();
      } else {
        const scale = Math.min(32 / layer.canvas.width, 32 / layer.canvas.height);
        const tw = layer.canvas.width * scale, th = layer.canvas.height * scale;
        tctx.drawImage(layer.canvas, (32 - tw) / 2, (32 - th) / 2, tw, th);
      }

      const name = document.createElement('span');
      name.className = 'layer-name';
      const prefix = (layer.clip ? '↳ ' : '') + (layer.type === 'adjustment' ? '◐ ' : '');
      name.textContent = prefix + layer.name;
      name.title = layer.clip ? '下のレイヤーにクリップ中' : 'ダブルクリックで名前を変更';
      if (layer.type === 'adjustment') name.title = 'ダブルクリックで再編集';
      // ダブルクリックでその場リネーム（調整レイヤーは再編集ダイアログを優先）
      if (layer.type !== 'adjustment') {
        name.addEventListener('dblclick', (ev) => {
          ev.stopPropagation();
          const input = document.createElement('input');
          input.type = 'text';
          input.value = layer.name;
          name.textContent = prefix;
          name.appendChild(input);
          const done = (commit) => {
            if (commit && input.value.trim()) layer.name = input.value.trim();
            render();
          };
          input.addEventListener('keydown', (ke) => {
            ke.stopPropagation();
            if (ke.key === 'Enter' && !ke.isComposing) done(true);
            else if (ke.key === 'Escape') done(false);
          });
          input.addEventListener('blur', () => done(true));
          input.addEventListener('click', (ce) => ce.stopPropagation());
          input.focus();
          input.select();
        });
      }

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

      if (layer.mask) {
        const mthumb = document.createElement('canvas');
        mthumb.className = 'layer-mask-thumb' + (PILayerManager.getMaskEditing() && i === activeIdx ? ' editing' : '');
        mthumb.width = 24; mthumb.height = 24;
        const mctx = mthumb.getContext('2d');
        mctx.fillStyle = '#3a3a4a'; mctx.fillRect(0, 0, 24, 24);
        const sc = Math.min(24 / layer.mask.width, 24 / layer.mask.height);
        const mw = layer.mask.width * sc, mh = layer.mask.height * sc;
        mctx.drawImage(layer.mask, (24 - mw) / 2, (24 - mh) / 2, mw, mh);
        mthumb.title = 'マスク：クリックで編集 / 右クリックで削除';
        mthumb.addEventListener('click', (e) => { e.stopPropagation(); PILayerManager.setActiveIndex(i); PILayerManager.setMaskEditing(true); });
        mthumb.addEventListener('contextmenu', (e) => { e.preventDefault(); e.stopPropagation(); PILayerManager.setActiveIndex(i); PILayerManager.removeMask(layer, false); PIHistoryManager.push('マスク削除'); });
        item.append(thumb, mthumb, name, actionsDiv);
      } else {
        item.append(thumb, name, actionsDiv);
      }
      return item;
    };

    // グループを考慮して一覧を構築（グループ所属はまとめて表示）
    const renderedIdx = new Set();
    const renderedGroups = new Set();
    for (let i = layers.length - 1; i >= 0; i--) {
      if (renderedIdx.has(i)) continue;
      const layer = layers[i];
      const grp = layer.groupId != null ? PILayerManager.getGroup(layer.groupId) : null;
      if (grp && !renderedGroups.has(grp.id)) {
        renderedGroups.add(grp.id);
        listEl.appendChild(buildGroupHeader(grp));
        for (let j = layers.length - 1; j >= 0; j--) {
          if (layers[j].groupId === grp.id) {
            renderedIdx.add(j);
            if (!grp.collapsed) { const it = makeItem(layers[j], j); it.classList.add('in-group'); listEl.appendChild(it); }
          }
        }
      } else if (grp) {
        renderedIdx.add(i);
      } else {
        listEl.appendChild(makeItem(layer, i));
        renderedIdx.add(i);
      }
    }

    container.appendChild(listEl);
  }

  // レイヤーのドラッグ並べ替え。挿入位置をインジケータ線で示し、
  // ドロップ時に配列を並べ替える。グループ内の2枚に挟まれた位置へ
  // 落とした場合のみ、そのグループへ所属させる。
  function startLayerDrag(e, layer, itemEl, listEl) {
    const startX = e.clientX, startY = e.clientY;
    const state = { started: false, slot: null, vis: null, indicator: null };

    const computeSlot = (clientY) => {
      const vis = state.vis;
      let slot = vis.length;
      for (let k = 0; k < vis.length; k++) {
        const r = vis[k].el.getBoundingClientRect();
        if (clientY < r.top + r.height / 2) { slot = k; break; }
      }
      return slot;
    };

    const onMove = (ev) => {
      if (!state.started) {
        if (Math.abs(ev.clientY - startY) < 5 && Math.abs(ev.clientX - startX) < 5) return;
        state.started = true;
        itemEl.classList.add('drag-source');
        document.body.style.cursor = 'grabbing';
        state.indicator = document.createElement('div');
        state.indicator.className = 'layer-drop-indicator';
        listEl.appendChild(state.indicator);
        state.vis = [...listEl.querySelectorAll('.layer-item')]
          .filter(el => el !== itemEl)
          .map(el => ({ el, idx: parseInt(el.dataset.idx, 10) }));
      }
      // パネル端に近づいたら自動スクロール
      const panel = document.getElementById('pi-panel');
      if (panel) {
        const pr = panel.getBoundingClientRect();
        if (ev.clientY < pr.top + 30) panel.scrollTop -= 10;
        else if (ev.clientY > pr.bottom - 30) panel.scrollTop += 10;
      }
      state.slot = computeSlot(ev.clientY);
      const vis = state.vis;
      const listRect = listEl.getBoundingClientRect();
      let y = 0;
      if (vis.length > 0) {
        if (state.slot < vis.length) y = vis[state.slot].el.getBoundingClientRect().top - listRect.top;
        else { const r = vis[vis.length - 1].el.getBoundingClientRect(); y = r.bottom - listRect.top; }
      }
      state.indicator.style.top = (y - 1) + 'px';
    };

    const onUp = () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      document.body.style.cursor = '';
      itemEl.classList.remove('drag-source');
      if (state.indicator) state.indicator.remove();
      if (!state.started) return;
      suppressClick = true;
      setTimeout(() => { suppressClick = false; }, 0);

      const vis = state.vis, slot = state.slot;
      if (!vis || vis.length === 0) return;
      const layers = PILayerManager.getAll();
      const from = layers.indexOf(layer);
      if (from < 0) return;
      let to;
      if (slot < vis.length) {
        // vis[slot] の1つ上に置く
        let refIdx = vis[slot].idx;
        if (from < refIdx) refIdx--;
        to = refIdx + 1;
      } else {
        // 最下段の下に置く
        let refIdx = vis[vis.length - 1].idx;
        if (from < refIdx) refIdx--;
        to = refIdx;
      }
      const above = slot > 0 ? layers[vis[slot - 1].idx] : null;
      const below = slot < vis.length ? layers[vis[slot].idx] : null;
      const g = (above && below && above.groupId != null && above.groupId === below.groupId) ? above.groupId : null;
      if (to === from && (layer.groupId || null) === (g || null)) return; // 位置も所属も変化なし
      layer.groupId = g;
      PILayerManager.moveLayer(from, to);
      PIHistoryManager.push('レイヤー並べ替え');
    };

    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }

  function buildGroupHeader(grp) {
    const members = PILayerManager.groupMembers(grp.id);
    const allVisible = members.length > 0 && members.every(m => m.visible);
    const header = document.createElement('div');
    header.className = 'layer-group-header';
    const tri = document.createElement('span'); tri.className = 'group-collapse'; tri.textContent = grp.collapsed ? '▶' : '▼';
    tri.addEventListener('click', (e) => { e.stopPropagation(); PILayerManager.toggleGroupCollapse(grp.id); });
    const eye = createSmallBtn(allVisible ? '👁' : '—', () => { PILayerManager.setGroupVisible(grp.id, !allVisible); });
    if (allVisible) eye.classList.add('active');
    const name = document.createElement('span'); name.className = 'group-name'; name.textContent = '📁 ' + grp.name + ' (' + members.length + ')';
    name.title = 'ダブルクリックで名称変更';
    name.addEventListener('dblclick', (e) => { e.stopPropagation(); const n = prompt('グループ名', grp.name); if (n != null) PILayerManager.renameGroup(grp.id, n); });
    const addBtn = createSmallBtn('＋', () => { PILayerManager.addActiveToGroup(grp.id); });
    addBtn.title = 'アクティブレイヤーをこのグループへ追加';
    const unBtn = createSmallBtn('✕', () => { PILayerManager.ungroup(grp.id); PIHistoryManager.push('グループ解除'); });
    unBtn.title = 'グループ解除';
    const acts = document.createElement('div'); acts.className = 'layer-actions'; acts.append(addBtn, unBtn, eye);
    header.append(tri, name, acts);
    return header;
  }

  function hasFx(fx) {
    return !!(fx && ((fx.dropShadow && fx.dropShadow.enabled) || (fx.stroke && fx.stroke.enabled) || (fx.outerGlow && fx.outerGlow.enabled)));
  }

  function openEffectsDialog() {
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const backup = layer.effects ? JSON.parse(JSON.stringify(layer.effects)) : null;
    const fx = layer.effects || {};
    const ds = fx.dropShadow || { enabled: false, color: '#000000', blur: 8, offsetX: 4, offsetY: 4, opacity: 0.5 };
    const st = fx.stroke || { enabled: false, color: '#ffffff', width: 3 };
    const gl = fx.outerGlow || { enabled: false, color: '#ffee66', blur: 14, opacity: 0.85 };
    const apply = (v) => {
      layer.effects = {
        dropShadow: { enabled: !!v.dsOn, color: v.dsColor, blur: +v.dsBlur, offsetX: +v.dsX, offsetY: +v.dsY, opacity: (+v.dsOp) / 100 },
        stroke: { enabled: !!v.stOn, color: v.stColor, width: +v.stW },
        outerGlow: { enabled: !!v.glOn, color: v.glColor, blur: +v.glBlur, opacity: (+v.glOp) / 100 }
      };
      PILayerManager.requestRender();
    };
    PIModal.show({
      title: 'レイヤースタイル',
      description: '影・境界線・光彩を非破壊で追加します（プレビュー反映）。',
      content: [
        { type: 'checkbox', label: 'ドロップシャドウを有効', key: 'dsOn', value: ds.enabled },
        { type: 'color', label: '影の色', key: 'dsColor', value: ds.color },
        { type: 'slider', label: '影ぼかし', key: 'dsBlur', value: ds.blur, min: 0, max: 50 },
        { type: 'slider', label: '影 X', key: 'dsX', value: ds.offsetX, min: -50, max: 50 },
        { type: 'slider', label: '影 Y', key: 'dsY', value: ds.offsetY, min: -50, max: 50 },
        { type: 'slider', label: '影の濃さ', key: 'dsOp', value: Math.round(ds.opacity * 100), min: 0, max: 100 },
        { type: 'checkbox', label: '境界線を有効', key: 'stOn', value: st.enabled },
        { type: 'color', label: '境界の色', key: 'stColor', value: st.color },
        { type: 'slider', label: '境界の太さ', key: 'stW', value: st.width, min: 0, max: 40 },
        { type: 'checkbox', label: '外側の光彩を有効', key: 'glOn', value: gl.enabled },
        { type: 'color', label: '光彩の色', key: 'glColor', value: gl.color },
        { type: 'slider', label: '光彩ぼかし', key: 'glBlur', value: gl.blur, min: 0, max: 60 },
        { type: 'slider', label: '光彩の強さ', key: 'glOp', value: Math.round(gl.opacity * 100), min: 0, max: 100 }
      ],
      confirmLabel: '適用',
      onPreview: (v) => apply(v),
      onConfirm: (v) => { apply(v); PIHistoryManager.push('レイヤースタイル'); render(); },
      onCancel: () => { layer.effects = backup; PILayerManager.requestRender(); }
    });
  }

  function createBtn(text, onClick, title) {
    const btn = document.createElement('button');
    btn.textContent = text;
    if (title) btn.title = title;
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
