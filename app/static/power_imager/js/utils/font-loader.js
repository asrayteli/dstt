/* PowerImager — FontLoader: フォント管理 */
window.PIFontLoader = (function () {
  const systemFonts = ['sans-serif', 'serif', 'monospace', 'cursive', 'fantasy',
    'Arial', 'Helvetica', 'Verdana', 'Georgia', 'Times New Roman', 'Courier New',
    'Meiryo', 'Yu Gothic', 'MS Gothic', 'Hiragino Sans'];
  const googleFonts = ['Noto Sans JP', 'Noto Serif JP', 'M PLUS Rounded 1c',
    'Kosugi Maru', 'Sawarabi Gothic', 'Sawarabi Mincho',
    'Roboto', 'Open Sans', 'Lato', 'Montserrat', 'Poppins'];
  const loadedFonts = new Set();

  function loadGoogleFont(fontName) {
    if (loadedFonts.has(fontName)) return;
    loadedFonts.add(fontName);
    const link = document.createElement('link');
    link.rel = 'stylesheet';
    link.href = 'https://fonts.googleapis.com/css2?family=' + encodeURIComponent(fontName) + ':wght@400;700&display=swap';
    document.head.appendChild(link);
  }

  function getAvailableFonts() {
    return { system: systemFonts, google: googleFonts };
  }

  return { loadGoogleFont, getAvailableFonts };
})();
