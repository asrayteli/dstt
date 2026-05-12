/* PowerImager — ImageIO: ファイル入出力 */
window.PIImageIO = {
  loadFromFile(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = (e) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = reject;
        img.src = e.target.result;
      };
      reader.onerror = reject;
      reader.readAsDataURL(file);
    });
  },

  async loadFromClipboard() {
    try {
      const items = await navigator.clipboard.read();
      for (const item of items) {
        for (const type of item.types) {
          if (type.startsWith('image/')) {
            const blob = await item.getType(type);
            return this.loadFromFile(blob);
          }
        }
      }
    } catch (e) { console.log('Clipboard read failed:', e); }
    return null;
  },

  exportCanvas(format, quality) {
    const flat = PILayerManager.flattenAll();
    const mimeType = format === 'jpeg' ? 'image/jpeg' : format === 'webp' ? 'image/webp' : 'image/png';
    return flat.toDataURL(mimeType, quality || 0.92);
  },

  downloadCanvas(filename, format, quality) {
    const dataURL = this.exportCanvas(format, quality);
    const a = document.createElement('a');
    a.href = dataURL;
    a.download = filename || 'image.' + (format || 'png');
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  },

  copyToClipboard() {
    const flat = PILayerManager.flattenAll();
    flat.toBlob(async (blob) => {
      try {
        await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
      } catch (e) { console.error('Clipboard write failed:', e); }
    }, 'image/png');
  }
};
