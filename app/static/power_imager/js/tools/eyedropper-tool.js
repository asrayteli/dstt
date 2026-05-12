/* PowerImager — EyedropperTool: スポイト */
window.PIEyedropperTool = new (class extends PIToolBase {
  constructor() {
    super('eyedropper', 'crosshair');
  }
  onMouseDown(e) {
    const canvas = PICanvasEngine.getDisplayCanvas();
    const ctx = canvas.getContext('2d');
    const x = Math.round(e.canvasX);
    const y = Math.round(e.canvasY);
    if (x < 0 || y < 0 || x >= canvas.width || y >= canvas.height) return;
    const pixel = ctx.getImageData(x, y, 1, 1).data;
    const hex = PIColorUtils.rgbToHex(pixel[0], pixel[1], pixel[2]);
    document.getElementById('fg-color-input').value = hex;
    PIEventBus.emit('color:picked', hex);
  }
  getProperties() {
    return { title: 'スポイト', fields: [] };
  }
})();
