/* PowerImager — ToolBase: ツール基底クラス */
class PIToolBase {
  constructor(name, cursor) {
    this.name = name;
    this.cursor = cursor || 'crosshair';
  }
  activate() {
    const vp = PICanvasEngine.getViewport();
    if (vp) vp.style.cursor = this.cursor;
  }
  deactivate() {}
  onMouseDown(e) {}
  onMouseMove(e) {}
  onMouseUp(e) {}
  onKeyDown(e) {}
  getProperties() { return null; }
}
