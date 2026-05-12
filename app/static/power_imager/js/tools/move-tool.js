/* PowerImager — MoveTool: レイヤー移動 */
window.PIMoveTool = new (class extends PIToolBase {
  constructor() {
    super('move', 'move');
    this.dragging = false;
    this.startX = 0; this.startY = 0;
    this.layerStartX = 0; this.layerStartY = 0;
  }
  onMouseDown(e) {
    const layer = PILayerManager.getActive();
    if (!layer || layer.locked) return;
    this.dragging = true;
    this.startX = e.canvasX; this.startY = e.canvasY;
    this.layerStartX = layer.x; this.layerStartY = layer.y;
  }
  onMouseMove(e) {
    if (!this.dragging) return;
    const layer = PILayerManager.getActive();
    if (!layer) return;
    layer.x = this.layerStartX + (e.canvasX - this.startX);
    layer.y = this.layerStartY + (e.canvasY - this.startY);
    PILayerManager.requestRender();
  }
  onMouseUp(e) {
    if (this.dragging) {
      this.dragging = false;
      PIHistoryManager.push('移動');
    }
  }
  getProperties() {
    const layer = PILayerManager.getActive();
    if (!layer) return null;
    return {
      title: '移動',
      fields: [
        { type: 'number', label: 'X', key: 'x', value: Math.round(layer.x), onChange: (v) => { layer.x = parseInt(v); PILayerManager.requestRender(); } },
        { type: 'number', label: 'Y', key: 'y', value: Math.round(layer.y), onChange: (v) => { layer.y = parseInt(v); PILayerManager.requestRender(); } }
      ]
    };
  }
})();
