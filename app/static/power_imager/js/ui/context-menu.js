/* PowerImager — ContextMenu: キャンバスの右クリックメニュー
 * 市販ソフト同等の操作を提供する:
 *  - オブジェクト上: 編集 / 切り取り / コピー / 複製 / 削除 / 重ね順 / 整列 /
 *                    回転リセット / ラスタライズ / ロック
 *  - 空き領域: 貼り付け / テキスト追加 / 画像を開く / 全選択 / 表示操作
 *  - 選択範囲があるとき: 新規レイヤーへコピー / 削除 / 反転 / 解除
 * レイヤーのコピー/切り取りは内部クリップボード（スタイル・種別を保持）。
 */
window.PIContextMenu = (function () {
  let menuEl = null;
  let layerClipboard = null; // レイヤーのスナップショット（textData/shapeDataごと保持）

  function init() {
    const vp = PICanvasEngine.getViewport();
    vp.addEventListener('contextmenu', onContextMenu);
    window.addEventListener('mousedown', (e) => {
      if (menuEl && !menuEl.contains(e.target)) close();
    }, true);
    window.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); }, true);
    window.addEventListener('blur', () => close());
    PIEventBus.on('canvas:zoom-changed', () => close());
  }

  function close() {
    if (menuEl) { menuEl.remove(); menuEl = null; }
  }

  function onContextMenu(e) {
    // テキスト編集ボックス内はネイティブメニュー（コピー/貼り付け/IME）を使わせる
    if (e.target.closest && e.target.closest('#text-edit-overlay')) return;
    e.preventDefault();
    if (PIModal.isOpen()) return;
    // 入力中の右クリックは左クリック同様「確定」として扱う
    if (window.PITextTool && PITextTool.isEditing) PITextTool.commitIfEditing();

    const pos = PICanvasEngine.viewportToCanvas(e.clientX, e.clientY);
    const layer = pickLayerAt(pos.x, pos.y);
    const layers = PILayerManager.getAll();
    let items;
    if (layer && !isBackgroundLike(layer)) {
      // 対象レイヤーを選択してからメニューを出す（背景相当のレイヤーは空き領域として扱う）
      PILayerManager.setActiveIndex(layers.indexOf(layer));
      PIEventBus.emit('tool:properties-changed');
      items = selectionItems().concat(buildLayerMenu(layer, pos));
    } else {
      items = selectionItems().concat(buildCanvasMenu(pos));
    }
    open(items, e.clientX, e.clientY);
  }

  function pickLayerAt(x, y) {
    const layers = PILayerManager.getAll();
    for (let i = layers.length - 1; i >= 0; i--) {
      const l = layers[i];
      // ロック中レイヤーも対象にする（メニューからロック解除できるように）
      if (l.visible && l.type !== 'adjustment' && PILayerTransform.hitTest(l, x, y)) return l;
    }
    return null;
  }

  // 最下層にあるキャンバス全面のラスター＝背景相当。
  // 右クリックでは空き領域として扱い、重ね順操作でもこの下には送らない。
  function isBackgroundLike(layer) {
    const layers = PILayerManager.getAll();
    if (layers.indexOf(layer) !== 0 || layer.type !== 'raster' || (layer.rotation || 0) !== 0) return false;
    const s = PICanvasEngine.getCanvasSize();
    return layer.x <= 0 && layer.y <= 0 &&
      layer.canvas.width * (layer.scaleX || 1) >= s.width &&
      layer.canvas.height * (layer.scaleY || 1) >= s.height;
  }

  // ===== メニュー定義 =====

  function selectionItems() {
    if (!(window.PISelection && PISelection.has())) return [];
    return [
      { label: '選択範囲を新規レイヤーへコピー', action: () => PISelection.copyToNewLayer() },
      { label: '選択範囲を削除', action: deleteSelectionPixels },
      { label: '選択範囲を反転', action: () => { PISelection.invert(); const t = PIToolbar.getCurrentTool(); if (t && typeof t.redraw === 'function') t.redraw(); } },
      { label: '選択解除', shortcut: 'Ctrl+D', action: () => { if (window.PISelectTool) PISelectTool.clearSelection(); } },
      '-'
    ];
  }

  function buildLayerMenu(layer, pos) {
    const layers = PILayerManager.getAll();
    const idx = layers.indexOf(layer);
    const locked = !!layer.locked;
    // 背景相当のレイヤーより下へは送らない（見えなくなるだけなので）
    const bottom = (layers.length && isBackgroundLike(layers[0]) && idx !== 0) ? 1 : 0;
    const items = [];

    if (layer.type === 'text') {
      items.push({ label: '✎ テキストを編集', disabled: locked, action: () => { PIEventBus.emit('tool:switch', 'text'); PITextTool.beginEditLayer(layer); } });
    }
    if (layer.type === 'shape') {
      items.push({ label: '図形を編集', disabled: locked, action: () => { PIEventBus.emit('tool:switch', 'shape'); PIEventBus.emit('tool:properties-changed'); } });
    }
    if (items.length) items.push('-');

    items.push({ label: '切り取り', shortcut: 'Ctrl+X', disabled: locked || layers.length <= 1, action: () => cutLayer(layer) });
    items.push({ label: 'コピー', action: () => copyLayer(layer) });
    items.push({ label: '貼り付け', action: () => pasteAt(pos.x, pos.y) });
    items.push({ label: '複製', shortcut: 'Ctrl+J', action: () => { PILayerManager.duplicateLayer(idx); PIHistoryManager.push('レイヤー複製'); } });
    items.push({ label: '削除', shortcut: 'Del', disabled: locked || layers.length <= 1, danger: true, action: () => { PILayerManager.removeLayer(idx); PIHistoryManager.push('レイヤー削除'); PIEventBus.emit('tool:properties-changed'); } });
    items.push('-');
    items.push({
      label: '重ね順', children: [
        { label: '最前面へ', disabled: idx >= layers.length - 1, action: () => reorder(idx, layers.length - 1) },
        { label: '前面へ', disabled: idx >= layers.length - 1, action: () => reorder(idx, idx + 1) },
        { label: '背面へ', disabled: idx <= bottom, action: () => reorder(idx, idx - 1) },
        { label: '最背面へ', disabled: idx <= bottom, action: () => reorder(idx, bottom) }
      ]
    });
    items.push({
      label: 'キャンバスに整列', children: [
        ['left', '左端'], ['hcenter', '左右中央'], ['right', '右端'],
        ['top', '上端'], ['vcenter', '上下中央'], ['bottom', '下端']
      ].map(([mode, label]) => ({
        label, disabled: locked,
        action: () => { PILayerTransform.alignInCanvas(layer, mode); PILayerManager.requestRender(); PIHistoryManager.push('整列'); PIEventBus.emit('tool:properties-changed'); }
      }))
    });
    items.push('-');
    if ((layer.rotation || 0) !== 0) {
      items.push({ label: '回転をリセット', disabled: locked, action: () => { layer.rotation = 0; if (layer.textData) layer.textData = { ...layer.textData, rotation: 0 }; emitLayers(); PIHistoryManager.push('回転リセット'); } });
    }
    if (layer.type === 'text' || layer.type === 'shape') {
      items.push({ label: 'ラスタライズ', disabled: locked, action: () => { PILayerManager.bakeLayerToRaster(layer); emitLayers(); PIHistoryManager.push('ラスタライズ'); PIEventBus.emit('tool:properties-changed'); } });
    }
    items.push({ label: locked ? 'ロック解除' : 'ロック', action: () => { layer.locked = !locked; emitLayers(); } });
    return items;
  }

  function buildCanvasMenu(pos) {
    const items = [];
    items.push({ label: '貼り付け', disabled: false, action: () => pasteAt(pos.x, pos.y) });
    items.push('-');
    items.push({ label: 'ここにテキストを追加', action: () => addTextAt(pos) });
    items.push({ label: '画像を開く...', action: () => { const fi = document.getElementById('file-input'); if (fi) fi.click(); } });
    items.push('-');
    items.push({ label: 'すべて選択', shortcut: 'Ctrl+A', action: () => { PIEventBus.emit('tool:switch', 'select'); if (window.PISelectTool) PISelectTool.selectAll(); } });
    items.push('-');
    items.push({ label: '画面に合わせる', shortcut: 'Ctrl+0', action: () => PICanvasEngine.fitToViewport() });
    items.push({ label: '100%表示', shortcut: 'Ctrl+1', action: () => PICanvasEngine.setZoom(1) });
    items.push({ label: 'グリッド表示 切替', action: () => PITopbar.handleAction('toggle-grid') });
    return items;
  }

  // ===== レイヤーの内部クリップボード =====

  function snapshotLayer(l) {
    const c = document.createElement('canvas');
    c.width = l.canvas.width; c.height = l.canvas.height;
    const ctx = c.getContext('2d');
    PICanvasEngine.configureContext(ctx);
    ctx.drawImage(l.canvas, 0, 0);
    return {
      name: l.name, canvas: c, w: l.canvas.width, h: l.canvas.height,
      opacity: l.opacity, blendMode: l.blendMode,
      rotation: l.rotation || 0, scaleX: l.scaleX || 1, scaleY: l.scaleY || 1,
      type: l.type,
      textData: l.textData ? { ...l.textData } : null,
      shapeData: l.shapeData ? { ...l.shapeData } : null,
      effects: l.effects ? JSON.parse(JSON.stringify(l.effects)) : null
    };
  }

  function copyLayer(layer) {
    layerClipboard = snapshotLayer(layer);
    PIEventBus.emit('toast', 'レイヤーをコピーしました（右クリック→貼り付け）');
  }

  function cutLayer(layer) {
    const layers = PILayerManager.getAll();
    if (layers.length <= 1 || layer.locked) return;
    layerClipboard = snapshotLayer(layer);
    PILayerManager.removeLayer(layers.indexOf(layer));
    PIHistoryManager.push('切り取り');
    PIEventBus.emit('tool:properties-changed');
    PIEventBus.emit('toast', 'レイヤーを切り取りました（右クリック→貼り付け）');
  }

  function cutActiveLayer() {
    const layer = PILayerManager.getActive();
    if (!layer) return;
    if (layer.locked) { PIEventBus.emit('toast', 'レイヤーがロックされています'); return; }
    if (PILayerManager.getAll().length <= 1) { PIEventBus.emit('toast', '最後のレイヤーは切り取れません'); return; }
    cutLayer(layer);
  }

  function pasteAt(cx, cy) {
    if (layerClipboard) {
      const s = layerClipboard;
      const layer = PILayerManager.addLayer(s.name);
      layer.canvas.width = s.w; layer.canvas.height = s.h;
      PICanvasEngine.configureContext(layer.ctx);
      layer.ctx.drawImage(s.canvas, 0, 0);
      layer.opacity = s.opacity; layer.blendMode = s.blendMode;
      layer.rotation = s.rotation; layer.scaleX = s.scaleX; layer.scaleY = s.scaleY;
      layer.type = s.type;
      if (s.textData) layer.textData = { ...s.textData };
      if (s.shapeData) layer.shapeData = { ...s.shapeData };
      if (s.effects) layer.effects = JSON.parse(JSON.stringify(s.effects));
      layer.x = Math.round(cx - s.w / 2);
      layer.y = Math.round(cy - s.h / 2);
      if (layer.textData) layer.textData = { ...layer.textData, x: layer.x, y: layer.y };
      emitLayers();
      PIHistoryManager.push('貼り付け');
      PIEventBus.emit('tool:properties-changed');
      return;
    }
    // 内部クリップボードが空ならOSのクリップボード画像を試す
    PIImageIO.loadFromClipboard().then(img => {
      if (img) {
        const layer = PILayerManager.addImageLayer(img, 'Pasted');
        layer.x = Math.round(cx - layer.canvas.width / 2);
        layer.y = Math.round(cy - layer.canvas.height / 2);
        PIHistoryManager.push('ペースト');
        PILayerManager.requestRender();
      } else {
        PIEventBus.emit('toast', '貼り付けられる内容がありません（画像は Ctrl+V でも貼り付けできます）');
      }
    }).catch(() => {
      PIEventBus.emit('toast', '貼り付けられる内容がありません（画像は Ctrl+V でも貼り付けできます）');
    });
  }

  function hasClipboard() { return !!layerClipboard; }

  // ===== その他アクション =====

  function addTextAt(pos) {
    PIEventBus.emit('tool:switch', 'text');
    PITextTool.textData.x = pos.x;
    PITextTool.textData.y = pos.y;
    PITextTool.textData.text = '';
    PITextTool.editingLayer = null;
    PITextTool.showEditBox();
  }

  function deleteSelectionPixels() {
    const layer = PILayerManager.getActive();
    if (!layer || layer.locked || !PISelection.has()) return;
    const ll = PISelection.getLayerLocalMask(layer);
    const ctx = layer.ctx;
    ctx.save();
    ctx.globalCompositeOperation = 'destination-out';
    ctx.drawImage(ll, 0, 0);
    ctx.restore();
    PIHistoryManager.push('選択範囲を削除');
    PILayerManager.requestRender();
  }

  function reorder(from, to) {
    PILayerManager.moveLayer(from, to);
    PIHistoryManager.push('重ね順変更');
  }

  function emitLayers() {
    PILayerManager.requestRender();
    PIEventBus.emit('layers:changed', { layers: PILayerManager.getAll(), activeIndex: PILayerManager.getActiveIndex() });
  }

  // ===== 描画 =====

  function open(items, x, y) {
    close();
    menuEl = document.createElement('div');
    menuEl.className = 'pi-context-menu';
    buildItems(menuEl, items);
    document.body.appendChild(menuEl);
    const r = menuEl.getBoundingClientRect();
    let px = x, py = y;
    if (px + r.width > window.innerWidth - 4) px = window.innerWidth - r.width - 4;
    if (py + r.height > window.innerHeight - 4) py = window.innerHeight - r.height - 4;
    menuEl.style.left = Math.max(4, px) + 'px';
    menuEl.style.top = Math.max(4, py) + 'px';
  }

  function buildItems(parent, items) {
    // 先頭/末尾/連続の区切り線は出さない
    let pendingSep = false, hasAny = false;
    items.forEach(it => {
      if (it === '-') { if (hasAny) pendingSep = true; return; }
      if (pendingSep) {
        const s = document.createElement('div');
        s.className = 'pi-context-sep';
        parent.appendChild(s);
        pendingSep = false;
      }
      hasAny = true;
      const el = document.createElement('div');
      el.className = 'pi-context-item' + (it.disabled ? ' disabled' : '') + (it.danger ? ' danger' : '') + (it.children ? ' has-sub' : '');
      const lab = document.createElement('span');
      lab.className = 'pi-context-label';
      lab.textContent = it.label;
      el.appendChild(lab);
      if (it.shortcut) {
        const sc = document.createElement('span');
        sc.className = 'pi-context-shortcut';
        sc.textContent = it.shortcut;
        el.appendChild(sc);
      }
      if (it.children) {
        const arrow = document.createElement('span');
        arrow.className = 'pi-context-arrow';
        arrow.textContent = '▸';
        el.appendChild(arrow);
        const sub = document.createElement('div');
        sub.className = 'pi-context-menu pi-context-sub';
        buildItems(sub, it.children);
        el.appendChild(sub);
      } else if (!it.disabled) {
        el.addEventListener('click', (ev) => {
          ev.stopPropagation();
          close();
          if (it.action) it.action();
        });
      }
      parent.appendChild(el);
    });
  }

  return { init, close, cutActiveLayer, copyLayer, pasteAt, hasClipboard };
})();
