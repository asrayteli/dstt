/* PowerImager — TooltipGuide: ツールチップガイド */
window.PITooltipGuide = (function () {
  const tips = {
    move: '移動ツール: クリックしたレイヤーを選択してドラッグで移動（矢印キーで微調整）',
    select: '選択ツール: 範囲を選択します',
    crop: '切り抜きツール: 画像を切り抜きます',
    brush: 'ブラシツール: フリーハンドで描画します',
    eraser: '消しゴムツール: 描画を消去します',
    text: 'テキスト: 空きをクリックで追加 / クリックで選択・移動 / ダブルクリックで編集',
    shape: '図形: ドラッグで作成 / クリックで選択し、ハンドルでサイズ変更・回転',
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
      if (PIModeSwitcher.getMode() !== 'normal') return;
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
