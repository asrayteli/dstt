/* PowerImager — ScreenshotTool: 画面キャプチャ（範囲指定対応）
 * ブラウザのセキュリティ上、モニター上で直接ドラッグ選択（Snipping Tool方式）は
 * Webページからは行えない。そのため、
 *   1) getDisplayMedia のピッカーで「画面全体 / ウィンドウ / タブ」を選ぶ
 *   2) 取得した静止フレームの上で取り込みたい範囲をドラッグ指定する
 * の2段階で「モニターに映っている任意の場所の指定領域切り取り」を実現する。
 */
window.PIScreenshotTool = new (class extends PIToolBase {
  constructor() {
    super('screenshot', 'crosshair');
    this._picker = null;
  }
  activate() {
    super.activate();
    this.captureScreen();
  }
  async captureScreen() {
    if (this._picker) return; // 領域選択中は多重起動しない
    let stream = null;
    try {
      stream = await navigator.mediaDevices.getDisplayMedia({ video: true });
      const track = stream.getVideoTracks()[0];
      const settings = track.getSettings();
      const video = document.createElement('video');
      video.srcObject = stream;
      video.autoplay = true;
      // 環境によってはフレームが流れずメタデータが届かないことがある。
      // 無言でハングしないようタイムアウトで打ち切って通知する。
      await new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('キャプチャ映像を取得できませんでした')), 8000);
        video.onloadedmetadata = () => { clearTimeout(timer); resolve(); };
      });
      await video.play();
      await new Promise(r => setTimeout(r, 200));

      const w = settings.width || video.videoWidth;
      const h = settings.height || video.videoHeight;
      const canvas = document.createElement('canvas');
      canvas.width = w; canvas.height = h;
      const ctx = canvas.getContext('2d');
      PICanvasEngine.configureContext(ctx);
      ctx.drawImage(video, 0, 0, w, h);

      track.stop();
      stream.getTracks().forEach(t => t.stop());

      this.openRegionPicker(canvas);
    } catch (err) {
      // タイムアウト等で失敗した場合も共有を確実に止める（共有中インジケータが残らないように）
      if (stream) { try { stream.getTracks().forEach(t => t.stop()); } catch (e) { /* no-op */ } }
      console.log('Screenshot cancelled or failed:', err.message);
      // ツール切替のヒントに上書きされないよう、切替→通知の順にする
      PIEventBus.emit('tool:switch', 'move');
      PIEventBus.emit('toast', '画面キャプチャを開始できませんでした（キャンセルまたは非対応の環境です）');
    }
  }

  // キャプチャした静止フレームの上で、取り込み範囲をドラッグ指定するオーバーレイ
  openRegionPicker(srcCanvas) {
    this.closeRegionPicker();
    const overlay = document.createElement('div');
    overlay.id = 'pi-shot-region';
    overlay.innerHTML =
      '<div class="sr-toolbar">' +
        '<span class="sr-hint">ドラッグで取り込む範囲を選択（ドラッグし直しでやり直し） — Enter: 確定 / Esc: キャンセル</span>' +
        '<span class="sr-size"></span>' +
        '<button class="sr-btn sr-all">全体を取り込む</button>' +
        '<button class="sr-btn sr-ok" disabled>選択範囲を取り込む</button>' +
        '<button class="sr-btn sr-cancel">キャンセル</button>' +
      '</div>' +
      '<div class="sr-stage"><canvas class="sr-canvas"></canvas></div>';
    document.body.appendChild(overlay);

    const stage = overlay.querySelector('.sr-stage');
    const disp = overlay.querySelector('.sr-canvas');
    const sizeEl = overlay.querySelector('.sr-size');
    const okBtn = overlay.querySelector('.sr-ok');
    const dctx = disp.getContext('2d');
    let sel = null; // ソースpx {x,y,w,h}
    let dragging = false, ax = 0, ay = 0;
    let scale = 1, offX = 0, offY = 0;

    const layout = () => {
      const rect = stage.getBoundingClientRect();
      disp.width = Math.max(1, Math.floor(rect.width));
      disp.height = Math.max(1, Math.floor(rect.height));
      scale = Math.min(disp.width / srcCanvas.width, disp.height / srcCanvas.height, 1);
      offX = (disp.width - srcCanvas.width * scale) / 2;
      offY = (disp.height - srcCanvas.height * scale) / 2;
      draw();
    };

    const toSrc = (clientX, clientY) => {
      const r = disp.getBoundingClientRect();
      const x = (clientX - r.left - offX) / scale;
      const y = (clientY - r.top - offY) / scale;
      return {
        x: Math.max(0, Math.min(srcCanvas.width, x)),
        y: Math.max(0, Math.min(srcCanvas.height, y))
      };
    };

    const draw = () => {
      dctx.clearRect(0, 0, disp.width, disp.height);
      PICanvasEngine.configureContext(dctx);
      dctx.drawImage(srcCanvas, offX, offY, srcCanvas.width * scale, srcCanvas.height * scale);
      dctx.fillStyle = 'rgba(0,0,0,0.55)';
      dctx.fillRect(0, 0, disp.width, disp.height);
      if (sel && sel.w >= 1 && sel.h >= 1) {
        const dx = offX + sel.x * scale, dy = offY + sel.y * scale;
        const dw = sel.w * scale, dh = sel.h * scale;
        dctx.drawImage(srcCanvas, sel.x, sel.y, sel.w, sel.h, dx, dy, dw, dh); // 選択部分だけ明るく
        dctx.strokeStyle = '#7c6ff7';
        dctx.lineWidth = 2;
        dctx.setLineDash([6, 4]);
        dctx.strokeRect(dx, dy, dw, dh);
        dctx.setLineDash([]);
        sizeEl.textContent = Math.round(sel.w) + ' × ' + Math.round(sel.h) + ' px';
        okBtn.disabled = false;
      } else {
        sizeEl.textContent = srcCanvas.width + ' × ' + srcCanvas.height + ' px（全体）';
        okBtn.disabled = true;
      }
    };

    const onDown = (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      dragging = true;
      const p = toSrc(e.clientX, e.clientY);
      ax = p.x; ay = p.y;
      sel = { x: p.x, y: p.y, w: 0, h: 0 };
      const onMove = (ev) => {
        if (!dragging) return;
        const q = toSrc(ev.clientX, ev.clientY);
        sel = {
          x: Math.min(ax, q.x), y: Math.min(ay, q.y),
          w: Math.abs(q.x - ax), h: Math.abs(q.y - ay)
        };
        draw();
      };
      const onUp = () => {
        dragging = false;
        window.removeEventListener('mousemove', onMove);
        window.removeEventListener('mouseup', onUp);
        draw();
      };
      window.addEventListener('mousemove', onMove);
      window.addEventListener('mouseup', onUp);
      draw();
    };
    disp.addEventListener('mousedown', onDown);

    const cancel = () => {
      this.closeRegionPicker();
      PIEventBus.emit('tool:switch', 'move');
      PIEventBus.emit('toast', 'キャプチャの取り込みをキャンセルしました');
    };
    const confirmSel = () => {
      const r = sel;
      this.closeRegionPicker();
      this.importRegion(srcCanvas, r);
    };
    const confirmAll = () => {
      this.closeRegionPicker();
      this.importRegion(srcCanvas, { x: 0, y: 0, w: srcCanvas.width, h: srcCanvas.height });
    };

    okBtn.addEventListener('click', () => { if (sel && sel.w >= 2 && sel.h >= 2) confirmSel(); });
    overlay.querySelector('.sr-all').addEventListener('click', confirmAll);
    overlay.querySelector('.sr-cancel').addEventListener('click', cancel);

    // 領域選択中は他のショートカットを奪わない・奪われないようにcaptureで処理
    const keyHandler = (e) => {
      e.stopPropagation();
      if (e.key === 'Escape') { e.preventDefault(); cancel(); }
      else if (e.key === 'Enter') {
        e.preventDefault();
        if (sel && sel.w >= 2 && sel.h >= 2) confirmSel(); else confirmAll();
      }
    };
    document.addEventListener('keydown', keyHandler, true);
    const onResize = () => layout();
    window.addEventListener('resize', onResize);

    this._picker = { overlay, keyHandler, onResize };
    layout();
  }

  closeRegionPicker() {
    if (!this._picker) return;
    document.removeEventListener('keydown', this._picker.keyHandler, true);
    window.removeEventListener('resize', this._picker.onResize);
    this._picker.overlay.remove();
    this._picker = null;
  }

  // 指定領域をレイヤーとして取り込む
  importRegion(srcCanvas, r) {
    const rx = Math.max(0, Math.round(r.x));
    const ry = Math.max(0, Math.round(r.y));
    const rw = Math.max(1, Math.min(srcCanvas.width - rx, Math.round(r.w)));
    const rh = Math.max(1, Math.min(srcCanvas.height - ry, Math.round(r.h)));
    const out = document.createElement('canvas');
    out.width = rw; out.height = rh;
    const octx = out.getContext('2d');
    PICanvasEngine.configureContext(octx);
    octx.drawImage(srcCanvas, rx, ry, rw, rh, 0, 0, rw, rh);

    const curSize = PICanvasEngine.getCanvasSize();
    if (rw > curSize.width || rh > curSize.height) {
      PICanvasEngine.setCanvasSize(Math.max(rw, curSize.width), Math.max(rh, curSize.height));
    }
    PILayerManager.addImageLayer(out, 'スクリーンショット');
    PICanvasEngine.fitToViewport();
    PIHistoryManager.push('スクリーンショット');
    PILayerManager.requestRender();
    PIEventBus.emit('tool:switch', 'move');
    PIEventBus.emit('toast', rw + ' × ' + rh + ' px を取り込みました');
  }

  getProperties() {
    return {
      title: 'スクリーンショット',
      fields: [
        {
          type: 'button', label: 'キャプチャ開始（画面/ウィンドウ/タブを選択）', key: 'capture',
          onClick: () => this.captureScreen()
        }
      ]
    };
  }
})();
