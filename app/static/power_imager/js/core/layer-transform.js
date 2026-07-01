/* PowerImager — LayerTransform: レイヤーの回転・拡縮を考慮した座標/ヒット計算
 * レイヤーは「中心（x+w/2, y+h/2）」基準で scaleX/scaleY → rotation の順に変形される。
 */
window.PILayerTransform = (function () {
  function center(layer) {
    return { cx: layer.x + layer.canvas.width / 2, cy: layer.y + layer.canvas.height / 2 };
  }

  // ローカル画素座標 → キャンバス座標
  function localToCanvas(layer, lx, ly) {
    const w = layer.canvas.width, h = layer.canvas.height;
    const sx = layer.scaleX || 1, sy = layer.scaleY || 1;
    const rot = layer.rotation || 0;
    const { cx, cy } = center(layer);
    let dx = (lx - w / 2) * sx;
    let dy = (ly - h / 2) * sy;
    const cos = Math.cos(rot), sin = Math.sin(rot);
    return { x: cx + dx * cos - dy * sin, y: cy + dx * sin + dy * cos };
  }

  // キャンバス座標 → ローカル画素座標
  function canvasToLocal(layer, px, py) {
    const w = layer.canvas.width, h = layer.canvas.height;
    const sx = layer.scaleX || 1, sy = layer.scaleY || 1;
    const rot = layer.rotation || 0;
    const { cx, cy } = center(layer);
    const cos = Math.cos(-rot), sin = Math.sin(-rot);
    let dx = px - cx, dy = py - cy;
    const rx = dx * cos - dy * sin;
    const ry = dx * sin + dy * cos;
    return { x: rx / sx + w / 2, y: ry / sy + h / 2 };
  }

  // 変形後の4隅（キャンバス座標）。順序: tl, tr, br, bl
  function corners(layer) {
    const w = layer.canvas.width, h = layer.canvas.height;
    return [
      localToCanvas(layer, 0, 0),
      localToCanvas(layer, w, 0),
      localToCanvas(layer, w, h),
      localToCanvas(layer, 0, h)
    ];
  }

  // 軸並行バウンディングボックス（キャンバス座標）
  function bbox(layer) {
    const c = corners(layer);
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    c.forEach(p => {
      minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x);
      minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y);
    });
    return { x: minX, y: minY, w: maxX - minX, h: maxY - minY };
  }

  // キャンバス座標がレイヤーの不透明画素に当たるか（透明な余白は当たり判定外）
  function hitTest(layer, px, py) {
    const w = layer.canvas.width, h = layer.canvas.height;
    const lp = canvasToLocal(layer, px, py);
    const lx = Math.floor(lp.x), ly = Math.floor(lp.y);
    if (lx < 0 || ly < 0 || lx >= w || ly >= h) return false;
    try {
      const a = layer.ctx.getImageData(lx, ly, 1, 1).data[3];
      return a > 8;
    } catch (e) {
      return true;
    }
  }

  // レイヤーの不透明画素のローカル矩形（空なら全キャンバス）
  function contentLocalRect(layer) {
    const w = layer.canvas.width, h = layer.canvas.height;
    let d;
    try { d = layer.ctx.getImageData(0, 0, w, h).data; } catch (e) { return { x: 0, y: 0, w, h }; }
    let minX = w, minY = h, maxX = -1, maxY = -1;
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      if (d[(y * w + x) * 4 + 3] > 8) { if (x < minX) minX = x; if (x > maxX) maxX = x; if (y < minY) minY = y; if (y > maxY) maxY = y; }
    }
    if (maxX < minX) return { x: 0, y: 0, w, h };
    return { x: minX, y: minY, w: maxX - minX + 1, h: maxY - minY + 1 };
  }

  // ローカル矩形を変形してキャンバス座標の軸並行bboxへ
  function rectToCanvasBBox(layer, rect) {
    const pts = [[rect.x, rect.y], [rect.x + rect.w, rect.y], [rect.x + rect.w, rect.y + rect.h], [rect.x, rect.y + rect.h]]
      .map(([lx, ly]) => localToCanvas(layer, lx, ly));
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    pts.forEach(p => { minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x); minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y); });
    return { x: minX, y: minY, w: maxX - minX, h: maxY - minY };
  }

  function contentBBox(layer) { return rectToCanvasBBox(layer, contentLocalRect(layer)); }

  // アクティブレイヤーをキャンバスに整列（不透明コンテンツbbox基準）
  function alignInCanvas(layer, mode) {
    const bb = contentBBox(layer);
    const s = PICanvasEngine.getCanvasSize();
    if (mode === 'left') layer.x += -bb.x;
    else if (mode === 'hcenter') layer.x += (s.width - bb.w) / 2 - bb.x;
    else if (mode === 'right') layer.x += s.width - bb.w - bb.x;
    else if (mode === 'top') layer.y += -bb.y;
    else if (mode === 'vcenter') layer.y += (s.height - bb.h) / 2 - bb.y;
    else if (mode === 'bottom') layer.y += s.height - bb.h - bb.y;
    layer.x = Math.round(layer.x); layer.y = Math.round(layer.y);
  }

  // 移動中にレイヤーbboxの端/中心を、キャンバスの端/中心＋追加ターゲット（ガイド/グリッド）へスナップ。
  // cachedRect: ドラッグ開始時に取得した不透明矩形（毎フレームの走査を避ける）
  function snapToCanvas(layer, thr, cachedRect, extraX, extraY) {
    const bb = rectToCanvasBBox(layer, cachedRect || contentLocalRect(layer));
    const s = PICanvasEngine.getCanvasSize();
    const targetsX = [0, s.width / 2, s.width].concat(extraX || []);
    const targetsY = [0, s.height / 2, s.height].concat(extraY || []);
    const candsX = [bb.x, bb.x + bb.w / 2, bb.x + bb.w];
    const candsY = [bb.y, bb.y + bb.h / 2, bb.y + bb.h];
    let bestX = null, guideX = null, bestY = null, guideY = null;
    candsX.forEach(c => targetsX.forEach(t => { const dd = Math.abs(c - t); if (dd <= thr && (bestX === null || dd < Math.abs(bestX))) { bestX = t - c; guideX = t; } }));
    candsY.forEach(c => targetsY.forEach(t => { const dd = Math.abs(c - t); if (dd <= thr && (bestY === null || dd < Math.abs(bestY))) { bestY = t - c; guideY = t; } }));
    if (bestX !== null) layer.x += bestX;
    if (bestY !== null) layer.y += bestY;
    return { guideX, guideY };
  }

  return { center, localToCanvas, canvasToLocal, corners, bbox, hitTest, alignInCanvas, snapToCanvas, contentLocalRect, contentBBox };
})();
