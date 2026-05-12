/* PowerImager — TooltipGuide: ツールチップガイド */
window.PITooltipGuide = (function () {
  const tips = {
    move: '移動ツール: レイヤーをドラッグして移動します',
    select: '選択ツール: 範囲を選択します',
    crop: '切り抜きツール: 画像を切り抜きます',
    brush: 'ブラシツール: フリーハンドで描画します',
    eraser: '消しゴムツール: 描画を消去します',
    text: 'テキストツール: クリックしてテキストを配置します',
    shape: '図形ツール: 矩形・楕円・線・矢印を描画します',
    fill: '塗りつぶしツール: 同色の領域を塗りつぶします',
    eyedropper: 'スポイトツール: キャンバスの色を拾います',
    screenshot: 'スクリーンショットツール: 画面をキャプチャします'
  };

  let toastEl;

  function init() {
    toastEl = document.createElement('div');
    toastEl.style.cssText = 'position:fixed;bottom:40px;left:50%;transform:translateX(-50%);' +
      'background:rgba(30,30,46,0.95);color:#e0e0e8;padding:8px 16px;border-radius:8px;' +
      'font-size:13px;z-index:600;pointer-events:none;opacity:0;transition:opacity 0.3s;' +
      'white-space:nowrap;box-shadow:0 4px 12px rgba(0,0,0,0.3);';
    document.body.appendChild(toastEl);

    PIEventBus.on('tool:activated', (d) => {
      if (PIModeSwitcher.getMode() !== 'beginner') return;
      showTip(d.name);
    });

    PIEventBus.on('toast', (msg) => showToast(msg));
  }

  function showTip(toolName) {
    const tip = tips[toolName];
    if (!tip) return;
    showToast(tip);
  }

  function showToast(msg) {
    toastEl.textContent = msg;
    toastEl.style.opacity = '1';
    setTimeout(() => { toastEl.style.opacity = '0'; }, 2500);
  }

  return { init };
})();
