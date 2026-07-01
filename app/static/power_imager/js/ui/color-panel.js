/* PowerImager — ColorPanel: HSVピッカー＋履歴色＋スウォッチ
 * 既存ツールは #fg-color-input / #bg-color-input の hex 値を読むため、その input を
 * 値ストアとして温存し、見た目のスウォッチをクリックすると本パネルが開く。
 */
window.PIColorPanel = (function () {
  const RECENT_KEY = 'pi-recent-colors';
  const SWATCHES = [
    '#000000', '#ffffff', '#7f7f7f', '#c3c3c3',
    '#ff0000', '#ff7f00', '#ffff00', '#00ff00',
    '#00ffff', '#0000ff', '#7f00ff', '#ff00ff',
    '#9c5a3c', '#ed1c24', '#f5821f', '#fff200',
    '#22b14c', '#00a2e8', '#3f48cc', '#a349a4'
  ];

  let pop, targetInput, anchorEl;
  let hsv = { h: 0, s: 0, v: 0 };
  let svBox, svCursor, hueBar, hueCursor, hexInput, previewEl, recentWrap;
  let dragging = null;

  function init() {
    bindSwatch('.color-swatch-fg', 'fg-color-input');
    bindSwatch('.color-swatch-bg', 'bg-color-input');
    PIEventBus.on('color:picked', (hex) => addRecent(hex));
    document.addEventListener('mousedown', (e) => {
      if (pop && !pop.classList.contains('hidden') && !pop.contains(e.target) && !(anchorEl && anchorEl.contains(e.target))) close();
    });
  }

  function bindSwatch(sel, inputId) {
    const wrap = document.querySelector(sel);
    const input = document.getElementById(inputId);
    if (!wrap || !input) return;
    input.style.pointerEvents = 'none'; // ネイティブピッカーを抑止
    wrap.style.cursor = 'pointer';
    wrap.addEventListener('click', (e) => { e.stopPropagation(); open(input, wrap); });
  }

  function getRecent() {
    try { return JSON.parse(localStorage.getItem(RECENT_KEY) || '[]'); } catch (e) { return []; }
  }
  function addRecent(hex) {
    hex = (hex || '').toLowerCase();
    if (!/^#[0-9a-f]{6}$/.test(hex)) return;
    let list = getRecent().filter(c => c !== hex);
    list.unshift(hex);
    list = list.slice(0, 14);
    localStorage.setItem(RECENT_KEY, JSON.stringify(list));
    if (recentWrap) renderRecent();
  }

  function ensurePop() {
    if (pop) return;
    pop = document.createElement('div');
    pop.id = 'pi-color-pop';
    pop.className = 'hidden';
    pop.innerHTML =
      '<div class="cp-sv"><canvas class="cp-sv-canvas" width="180" height="140"></canvas><div class="cp-sv-cursor"></div></div>' +
      '<div class="cp-hue"><canvas class="cp-hue-canvas" width="180" height="14"></canvas><div class="cp-hue-cursor"></div></div>' +
      '<div class="cp-row"><span class="cp-preview"></span><input class="cp-hex" type="text" maxlength="7" spellcheck="false"></div>' +
      '<div class="cp-label">履歴</div><div class="cp-recent"></div>' +
      '<div class="cp-label">スウォッチ</div><div class="cp-swatches"></div>';
    document.body.appendChild(pop);

    svBox = pop.querySelector('.cp-sv');
    svCursor = pop.querySelector('.cp-sv-cursor');
    hueBar = pop.querySelector('.cp-hue');
    hueCursor = pop.querySelector('.cp-hue-cursor');
    hexInput = pop.querySelector('.cp-hex');
    previewEl = pop.querySelector('.cp-preview');
    recentWrap = pop.querySelector('.cp-recent');

    drawHue();
    const swWrap = pop.querySelector('.cp-swatches');
    SWATCHES.forEach(c => {
      const b = document.createElement('button');
      b.className = 'cp-swatch'; b.style.background = c; b.title = c;
      b.addEventListener('click', () => { setHex(c); commit(); });
      swWrap.appendChild(b);
    });

    svBox.addEventListener('mousedown', (e) => { dragging = 'sv'; onSV(e); });
    hueBar.addEventListener('mousedown', (e) => { dragging = 'hue'; onHue(e); });
    window.addEventListener('mousemove', (e) => { if (dragging === 'sv') onSV(e); else if (dragging === 'hue') onHue(e); });
    window.addEventListener('mouseup', () => { if (dragging) { dragging = null; commit(); } });
    hexInput.addEventListener('change', () => { let v = hexInput.value.trim(); if (!v.startsWith('#')) v = '#' + v; if (/^#[0-9a-fA-F]{6}$/.test(v)) { setHex(v); commit(); } });
  }

  function drawHue() {
    const c = pop.querySelector('.cp-hue-canvas');
    const ctx = c.getContext('2d');
    const g = ctx.createLinearGradient(0, 0, c.width, 0);
    for (let i = 0; i <= 6; i++) { const rgb = PIColorUtils.hsvToRgb(i * 60, 1, 1); g.addColorStop(i / 6, 'rgb(' + rgb.r + ',' + rgb.g + ',' + rgb.b + ')'); }
    ctx.fillStyle = g; ctx.fillRect(0, 0, c.width, c.height);
  }

  function drawSV() {
    const c = pop.querySelector('.cp-sv-canvas');
    const ctx = c.getContext('2d');
    const base = PIColorUtils.hsvToRgb(hsv.h, 1, 1);
    ctx.fillStyle = 'rgb(' + base.r + ',' + base.g + ',' + base.b + ')';
    ctx.fillRect(0, 0, c.width, c.height);
    const gx = ctx.createLinearGradient(0, 0, c.width, 0);
    gx.addColorStop(0, 'rgba(255,255,255,1)'); gx.addColorStop(1, 'rgba(255,255,255,0)');
    ctx.fillStyle = gx; ctx.fillRect(0, 0, c.width, c.height);
    const gy = ctx.createLinearGradient(0, 0, 0, c.height);
    gy.addColorStop(0, 'rgba(0,0,0,0)'); gy.addColorStop(1, 'rgba(0,0,0,1)');
    ctx.fillStyle = gy; ctx.fillRect(0, 0, c.width, c.height);
  }

  function onSV(e) {
    const r = svBox.querySelector('.cp-sv-canvas').getBoundingClientRect();
    const x = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
    const y = Math.max(0, Math.min(1, (e.clientY - r.top) / r.height));
    hsv.s = x; hsv.v = 1 - y;
    reflect();
  }
  function onHue(e) {
    const r = hueBar.querySelector('.cp-hue-canvas').getBoundingClientRect();
    const x = Math.max(0, Math.min(1, (e.clientX - r.left) / r.width));
    hsv.h = Math.round(x * 360);
    drawSV(); reflect();
  }

  function reflect() {
    const rgb = PIColorUtils.hsvToRgb(hsv.h, hsv.s, hsv.v);
    const hex = PIColorUtils.rgbToHex(rgb.r, rgb.g, rgb.b);
    previewEl.style.background = hex;
    hexInput.value = hex;
    const svc = svBox.querySelector('.cp-sv-canvas').getBoundingClientRect();
    svCursor.style.left = (hsv.s * svc.width) + 'px';
    svCursor.style.top = ((1 - hsv.v) * svc.height) + 'px';
    const huec = hueBar.querySelector('.cp-hue-canvas');
    hueCursor.style.left = ((hsv.h / 360) * huec.width) + 'px';
    // 値ストアの input を更新して全ツールに反映
    if (targetInput) { targetInput.value = hex; targetInput.dispatchEvent(new Event('input', { bubbles: true })); }
  }

  function setHex(hex) {
    const rgb = PIColorUtils.hexToRgb(hex);
    hsv = PIColorUtils.rgbToHsv(rgb.r, rgb.g, rgb.b);
    drawSV(); reflect();
  }

  function commit() {
    if (targetInput) addRecent(targetInput.value);
  }

  function renderRecent() {
    recentWrap.innerHTML = '';
    const list = getRecent();
    if (!list.length) { recentWrap.innerHTML = '<span class="cp-empty">なし</span>'; return; }
    list.forEach(c => {
      const b = document.createElement('button');
      b.className = 'cp-swatch'; b.style.background = c; b.title = c;
      b.addEventListener('click', () => { setHex(c); commit(); });
      recentWrap.appendChild(b);
    });
  }

  function open(input, anchor) {
    ensurePop();
    targetInput = input; anchorEl = anchor;
    setHex(input.value || '#000000');
    renderRecent();
    pop.classList.remove('hidden');
    const r = anchor.getBoundingClientRect();
    let left = r.right + 8;
    if (left + 210 > window.innerWidth) left = r.left - 218;
    pop.style.left = Math.max(8, left) + 'px';
    pop.style.top = Math.min(window.innerHeight - 320, Math.max(8, r.top)) + 'px';
    requestAnimationFrame(() => reflect());
  }

  function close() { if (pop) pop.classList.add('hidden'); }

  return { init, open, addRecent };
})();
