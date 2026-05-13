/* PowerImager — ScreenshotTool: 画面キャプチャ */
window.PIScreenshotTool = new (class extends PIToolBase {
  constructor() {
    super('screenshot', 'crosshair');
  }
  activate() {
    super.activate();
    this.captureScreen();
  }
  async captureScreen() {
    try {
      const stream = await navigator.mediaDevices.getDisplayMedia({ video: true });
      const track = stream.getVideoTracks()[0];
      const settings = track.getSettings();
      const video = document.createElement('video');
      video.srcObject = stream;
      video.autoplay = true;
      await new Promise(r => video.onloadedmetadata = r);
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

      const img = new Image();
      img.width = w; img.height = h;
      const dataURL = canvas.toDataURL('image/png');
      await new Promise((resolve) => {
        img.onload = resolve;
        img.src = dataURL;
      });

      const curSize = PICanvasEngine.getCanvasSize();
      if (w > curSize.width || h > curSize.height) {
        PICanvasEngine.setCanvasSize(Math.max(w, curSize.width), Math.max(h, curSize.height));
      }

      PILayerManager.addImageLayer(img, 'スクリーンショット');
      PICanvasEngine.fitToViewport();
      PIHistoryManager.push('スクリーンショット');
      PILayerManager.requestRender();

      PIEventBus.emit('tool:switch', 'crop');
    } catch (err) {
      console.log('Screenshot cancelled or failed:', err.message);
      PIEventBus.emit('tool:switch', 'move');
    }
  }
  getProperties() {
    return {
      title: 'スクリーンショット',
      fields: [
        {
          type: 'button', label: 'キャプチャ開始', key: 'capture',
          onClick: () => this.captureScreen()
        }
      ]
    };
  }
})();
