/* PowerImager — BlurFilter */
window.PIBlurFilter = {
  showDialog() {
    const layer = PILayerManager.getActive();
    if (!layer) return;
    const backup = layer.ctx.getImageData(0, 0, layer.canvas.width, layer.canvas.height);
    PIModal.show({
      title: 'ぼかし',
      content: [
        { type: 'slider', label: '半径', key: 'radius', value: 3, min: 1, max: 20 }
      ],
      onPreview: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.apply(layer, v.radius);
        PILayerManager.requestRender();
      },
      onConfirm: (v) => {
        layer.ctx.putImageData(backup, 0, 0);
        this.apply(layer, v.radius);
        PIHistoryManager.push('ぼかし');
        PILayerManager.requestRender();
      },
      onCancel: () => { layer.ctx.putImageData(backup, 0, 0); PILayerManager.requestRender(); }
    });
  },
  apply(layer, radius) {
    const ctx = layer.ctx;
    ctx.save();
    PICanvasEngine.configureContext(ctx);
    ctx.filter = 'blur(' + radius + 'px)';
    const temp = document.createElement('canvas');
    temp.width = layer.canvas.width; temp.height = layer.canvas.height;
    const tctx = temp.getContext('2d');
    PICanvasEngine.configureContext(tctx);
    tctx.drawImage(layer.canvas, 0, 0);
    PISelection.putCanvasEffect(layer, temp);
    ctx.restore();
  }
};
