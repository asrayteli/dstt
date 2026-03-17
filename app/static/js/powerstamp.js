(() => {
  const stage = document.getElementById('stage');
  const stageViewport = document.getElementById('stageViewport');
  const stageScroll = document.getElementById('stageScroll');
  const guideLayer = document.getElementById('guideLayer');
  const overlayLayer = document.getElementById('overlayLayer');
  const backgroundLayer = document.getElementById('backgroundLayer');
  const detectedSize = document.getElementById('detectedSize');
  const detectedFileType = document.getElementById('detectedFileType');
  const zoomInBtn = document.getElementById('zoomInBtn');
  const zoomOutBtn = document.getElementById('zoomOutBtn');
  const zoomResetBtn = document.getElementById('zoomResetBtn');
  const zoomLabel = document.getElementById('zoomLabel');
  const zoomFitBtn = document.getElementById('zoomFitBtn');
  const zoomSlider = document.getElementById('zoomSlider');
  const outsideAreaColorInput = document.getElementById('outsideAreaColor');
  const outsideAreaColorResetBtn = document.getElementById('outsideAreaColorResetBtn');
  const calibOffsetXInput = document.getElementById('calibOffsetX');
  const calibOffsetYInput = document.getElementById('calibOffsetY');
  const calibScaleXInput = document.getElementById('calibScaleX');
  const calibScaleYInput = document.getElementById('calibScaleY');
  const calibResetBtn = document.getElementById('calibResetBtn');
  const applyCalibrationOnExport = document.getElementById('applyCalibrationOnExport');
  const templateNameInput = document.getElementById('templateNameInput');
  const saveTemplateBtn = document.getElementById('saveTemplateBtn');
  const templateSelect = document.getElementById('templateSelect');
  const loadTemplateBtn = document.getElementById('loadTemplateBtn');
  const deleteTemplateBtn = document.getElementById('deleteTemplateBtn');
  const openPreviewTabBtn = document.getElementById('openPreviewTabBtn');
  const openVerifyTabBtn = document.getElementById('openVerifyTabBtn');
  const useAnchorOrigin = document.getElementById('useAnchorOrigin');
  const setAnchorBtn = document.getElementById('setAnchorBtn');
  const anchorStatus = document.getElementById('anchorStatus');
  const anchorMarker = document.getElementById('anchorMarker');
  const paperSizeSelect = document.getElementById('paperSizeSelect');
  const paperOrientation = document.getElementById('paperOrientation');
  const paperDpiInput = document.getElementById('paperDpiInput');
  const textFontFamily = document.getElementById('textFontFamily');
  const textVertical = document.getElementById('textVertical');
  const toggleSelectedTextDirectionBtn = document.getElementById('toggleSelectedTextDirectionBtn');
  const postalCodePanel = document.getElementById('postalCodePanel');
  const postalCodeInput = document.getElementById('postalCodeInput');
  const applyPostalCodeBtn = document.getElementById('applyPostalCodeBtn');

  if (!stage || !stageViewport || !guideLayer || !overlayLayer || !backgroundLayer || !detectedSize || !detectedFileType) {
    return;
  }

  const PAPER_MM = [
    { name: 'A3', w: 297, h: 420 },
    { name: 'A4', w: 210, h: 297 },
    { name: 'B4', w: 257, h: 364 },
    { name: 'B5', w: 182, h: 257 },
  ];

  let selected = null;
  let dragState = null;
  let resizeState = null;
  let pdfjsLibPromise = null;
  let clipboardStamp = null;
  let zoom = 1;
  const ZOOM_MIN = 0.05;
  const ZOOM_MAX = 4;
  const ZOOM_STEP = 0.1;

  const PAPER_MAP_MM = { A3: {w:297,h:420}, A4:{w:210,h:297}, B4:{w:257,h:364}, B5:{w:182,h:257} };
  let anchorPoint = null;
  let waitingAnchorPick = false;
  let activeGuide = null;
  let activePaper = null;
  let contextMenu = null;
  let contextTarget = null;
  let contextPoint = null;
  let contextColorTarget = null;
  let colorDialog = null;
  let colorDialogInput = null;
  let activeTemplateName = '';

  
  
  function mmToCanvasPx(mm, dpi = 300) {
    return (mm / 25.4) * dpi;
  }

  function clearGuide() {
    if (guideLayer) guideLayer.innerHTML = '';
  }


  function resolveGuideBoxes(guide, paper = null) {
    if (!guide) return [];

    if (Array.isArray(guide.boxes_mm) && paper && paper.w_mm && paper.h_mm) {
      const canvas = getStagePixelSize();
      const sx = canvas.width / paper.w_mm;
      const sy = canvas.height / paper.h_mm;
      return guide.boxes_mm.map((b) => ({
        ...b,
        x: (b.x_mm || 0) * sx,
        y: (b.y_mm || 0) * sy,
        w: (b.w_mm || 0) * sx,
        h: (b.h_mm || 0) * sy,
        gap: (b.gap_mm || 0) * sx,
      }));
    }

    if (Array.isArray(guide.boxes)) {
      return guide.boxes;
    }

    return [];
  }

  function clearAutoPostalCodeStamps() {
    Array.from(overlayLayer.querySelectorAll('.auto-postal-code')).forEach((node) => node.remove());
  }

  function normalizePostalCode(raw) {
    return String(raw || '').replace(/\D/g, '').slice(0, 7);
  }

  function isEnvelopeTemplateName(name) {
    const n = String(name || '').toLowerCase();
    return (
      n.includes('長3') ||
      n.includes('長３') ||
      n.includes('角2') ||
      n.includes('角２') ||
      n.includes('角2号') ||
      n.includes('角２号') ||
      n.includes('n3') ||
      n.includes('k2') ||
      n.includes('long3') ||
      n.includes('kaku2')
    );
  }

  function updatePostalCodePanel() {
    if (!postalCodePanel) return;
    const show = isEnvelopeTemplateName(activeTemplateName);
    postalCodePanel.classList.toggle('hidden', !show);
    if (!show) {
      clearAutoPostalCodeStamps();
    } else if (postalCodeInput?.value) {
      triggerPostalCodeApply();
    }
  }

  function applyPostalCodeToGuide(rawCode) {
    const zipCode = normalizePostalCode(rawCode);
    if (!zipCode) {
      clearAutoPostalCodeStamps();
      return;
    }

    const boxes = resolveGuideBoxes(activeGuide, activePaper);
    const zipGuide = boxes.find((b) => b && b.type === 'zip7');
    if (!zipGuide) {
      alert('現在のテンプレートに郵便番号欄がありません。封筒テンプレートを読み込んでください。');
      return;
    }

    clearAutoPostalCodeStamps();

    const n1 = Number(zipGuide.digits_first || 3);
    const n2 = Number(zipGuide.digits_second || 4);
    const count = n1 + n2;
    const gap = Math.max(2, Number(zipGuide.gap || 0));
    const totalW = Math.max(0, Number(zipGuide.w || 0));
    const cellW = (totalW - gap) / count;
    const baseX = Number(zipGuide.x || 0);
    const baseY = Number(zipGuide.y || 0);
    const h = Math.max(0, Number(zipGuide.h || 0));
    const digits = zipCode.padEnd(count, ' ');

    for (let i = 0; i < count; i++) {
      const inSecond = i >= n1;
      const x = baseX + (i * cellW) + (inSecond ? gap : 0);
      const text = document.createElement('div');
      text.className = 'stamp-item auto-postal-code';
      text.dataset.autoPostalCode = 'true';
      text.textContent = digits[i] || ' ';
      text.style.left = `${Math.max(0, x)}px`;
      text.style.top = `${Math.max(0, baseY)}px`;
      text.style.width = `${Math.max(1, cellW)}px`;
      text.style.height = `${Math.max(1, h)}px`;
      text.style.display = 'flex';
      text.style.alignItems = 'center';
      text.style.justifyContent = 'center';
      text.style.fontFamily = '"Noto Sans JP", sans-serif';
      text.style.fontWeight = '700';
      text.style.fontSize = `${Math.max(12, Math.round(h * 0.72))}px`;
      text.style.lineHeight = '1';
      text.style.color = '#111';
      text.style.whiteSpace = 'pre';
      overlayLayer.appendChild(text);
    }
  }

  function triggerPostalCodeApply() {
    if (postalCodePanel?.classList.contains('hidden')) return;
    applyPostalCodeToGuide(postalCodeInput?.value || '');
  }

  function renderGuide(guide, paper = null) {
    clearGuide();
    if (!guideLayer || !guide) return;

    let boxes = resolveGuideBoxes(guide, paper);

    for (const b of boxes) {
      if (b.type === 'zip7') {
        const n1 = Number(b.digits_first || 3);
        const n2 = Number(b.digits_second || 4);
        const count = n1 + n2;
        const gap = Math.max(2, Number(b.gap || 0));
        const totalW = Math.max(0, Number(b.w || 0));
        const cellW = (totalW - gap) / count;
        const y = Math.max(0, Number(b.y || 0));
        const h = Math.max(0, Number(b.h || 0));
        for (let i = 0; i < count; i++) {
          const inSecond = i >= n1;
          const x = (Number(b.x || 0)) + (i * cellW) + (inSecond ? gap : 0);
          const box = document.createElement('div');
          box.className = 'guide-box';
          box.style.left = `${Math.max(0, x)}px`;
          box.style.top = `${y}px`;
          box.style.width = `${Math.max(0, cellW - 1)}px`;
          box.style.height = `${h}px`;
          guideLayer.appendChild(box);
        }
      } else {
        const box = document.createElement('div');
        box.className = 'guide-box';
        box.style.left = `${Math.max(0, b.x || 0)}px`;
        box.style.top = `${Math.max(0, b.y || 0)}px`;
        box.style.width = `${Math.max(0, b.w || 0)}px`;
        box.style.height = `${Math.max(0, b.h || 0)}px`;
        guideLayer.appendChild(box);
      }

      if (b.label) {
        const label = document.createElement('div');
        label.className = 'guide-label';
        label.textContent = b.label;
        label.style.left = `${Math.max(0, (b.x || 0) + 2)}px`;
        label.style.top = `${Math.max(0, (b.y || 0) - 18)}px`;
        guideLayer.appendChild(label);
      }
    }
  }

  function buildEnvelopeGuideByName(name) {
    if (!name) return null;
    const n = String(name);
    if (n.includes('長3') || n.includes('長３') || n.toLowerCase().includes('n3') || n.toLowerCase().includes('long3')) {
      return {
        boxes_mm: [
          { type: 'zip7', x_mm: 57, y_mm: 12, w_mm: 55, h_mm: 12, label: '郵便番号', digits_first: 3, digits_second: 4, gap_mm: 4 },
          { x_mm: 72, y_mm: 45, w_mm: 38, h_mm: 155, label: '宛名（縦書き想定）' },
          { x_mm: 10, y_mm: 185, w_mm: 45, h_mm: 38, label: '差出人' }
        ]
      };
    }
    if (n.includes('角2') || n.includes('角２') || n.includes('角2号') || n.includes('角２号') || n.toLowerCase().includes('k2') || n.toLowerCase().includes('kaku2')) {
      return {
        boxes_mm: [
          { type: 'zip7', x_mm: 170, y_mm: 14, w_mm: 60, h_mm: 13, label: '郵便番号', digits_first: 3, digits_second: 4, gap_mm: 4 },
          { x_mm: 145, y_mm: 70, w_mm: 75, h_mm: 210, label: '宛名（縦書き想定）' },
          { x_mm: 18, y_mm: 270, w_mm: 70, h_mm: 45, label: '差出人' }
        ]
      };
    }
    return null;
  }

function mmToPx(mm, dpi) {
    return (mm / 25.4) * dpi;
  }

  function getPaperOutputSize() {
    const key = paperSizeSelect?.value || 'A4';
    const p = PAPER_MAP_MM[key] || PAPER_MAP_MM.A4;
    const isLandscape = (paperOrientation?.value || 'portrait') === 'landscape';
    const dpi = Math.max(72, Number(paperDpiInput?.value || 300));
    const wMm = isLandscape ? p.h : p.w;
    const hMm = isLandscape ? p.w : p.h;
    return {
      width: Math.round(mmToPx(wMm, dpi)),
      height: Math.round(mmToPx(hMm, dpi)),
      label: `${key} ${isLandscape ? '横' : '縦'} @${dpi}dpi`
    };
  }

  function setAnchorUI(point) {
    if (!anchorMarker || !anchorStatus) return;
    if (!point) {
      anchorMarker.classList.add('hidden');
      anchorStatus.textContent = '未設定';
      return;
    }
    anchorMarker.classList.remove('hidden');
    anchorMarker.style.left = `${point.x}px`;
    anchorMarker.style.top = `${point.y}px`;
    anchorStatus.textContent = `(${Math.round(point.x)}, ${Math.round(point.y)})`;
  }

  function fitStageToScreen() {
    if (!stageScroll) return;
    const { width, height } = getStagePixelSize();
    const style = window.getComputedStyle(stageScroll);
    const padX = (parseFloat(style.paddingLeft || '0') + parseFloat(style.paddingRight || '0'));
    const padY = (parseFloat(style.paddingTop || '0') + parseFloat(style.paddingBottom || '0'));
    const safety = 4;
    const availW = Math.max(120, stageScroll.clientWidth - padX - safety);
    const availH = Math.max(120, stageScroll.clientHeight - padY - safety);
    const zw = availW / Math.max(1, width);
    const zh = availH / Math.max(1, height);
    const target = Math.min(1, zw, zh);
    setZoom(target);
  }

  function getStagePixelSize() {
    const w = Number(document.getElementById('canvasWidth')?.value) || parseFloat(stage.style.width) || stage.getBoundingClientRect().width;
    const h = Number(document.getElementById('canvasHeight')?.value) || parseFloat(stage.style.height) || stage.getBoundingClientRect().height;
    return { width: Math.max(1, Math.round(w)), height: Math.max(1, Math.round(h)) };
  }

  function applyZoom() {
    const { width, height } = getStagePixelSize();
    stage.style.transform = `scale(${zoom})`;
    stageViewport.style.width = `${Math.round(width * zoom)}px`;
    stageViewport.style.height = `${Math.round(height * zoom)}px`;
    if (zoomLabel) zoomLabel.textContent = `${Math.round(zoom * 100)}%`;
    if (zoomSlider) zoomSlider.value = String(Math.round(zoom * 100));
  }

  function setZoom(nextZoom) {
    zoom = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, nextZoom));
    applyZoom();
  }

  let sourceSize = { ...getStagePixelSize(), unit: 'px' };

  const CALIB_KEY = 'powerstampCalibrationV1';
  const OUTSIDE_AREA_COLOR_KEY = 'powerstampOutsideAreaColorV1';
  const OUTSIDE_AREA_DEFAULT = '#f3f4f6';

  function getCalibration() {
    const offsetX = Number(calibOffsetXInput?.value || 0);
    const offsetY = Number(calibOffsetYInput?.value || 0);
    const scaleX = Number(calibScaleXInput?.value || 100) / 100;
    const scaleY = Number(calibScaleYInput?.value || 100) / 100;
    return {
      offsetX: Number.isFinite(offsetX) ? offsetX : 0,
      offsetY: Number.isFinite(offsetY) ? offsetY : 0,
      scaleX: Number.isFinite(scaleX) && scaleX > 0 ? scaleX : 1,
      scaleY: Number.isFinite(scaleY) && scaleY > 0 ? scaleY : 1,
    };
  }

  function saveCalibration() {
    try {
      localStorage.setItem(CALIB_KEY, JSON.stringify(getCalibration()));
    } catch (_) {}
  }

  function loadCalibration() {
    try {
      const raw = localStorage.getItem(CALIB_KEY);
      if (!raw) return;
      const c = JSON.parse(raw);
      if (calibOffsetXInput) calibOffsetXInput.value = String(c.offsetX ?? 0);
      if (calibOffsetYInput) calibOffsetYInput.value = String(c.offsetY ?? 0);
      if (calibScaleXInput) calibScaleXInput.value = String(((c.scaleX ?? 1) * 100));
      if (calibScaleYInput) calibScaleYInput.value = String(((c.scaleY ?? 1) * 100));
    } catch (_) {}
  }

  function applyOutsideAreaColor(color) {
    if (!stageScroll) return;
    const safe = /^#[0-9a-fA-F]{6}$/.test(String(color || '')) ? color : OUTSIDE_AREA_DEFAULT;
    stageScroll.style.backgroundColor = safe;
    if (outsideAreaColorInput) outsideAreaColorInput.value = safe;
  }

  function loadOutsideAreaColor() {
    try {
      const stored = localStorage.getItem(OUTSIDE_AREA_COLOR_KEY);
      applyOutsideAreaColor(stored || outsideAreaColorInput?.value || OUTSIDE_AREA_DEFAULT);
    } catch (_) {
      applyOutsideAreaColor(outsideAreaColorInput?.value || OUTSIDE_AREA_DEFAULT);
    }
  }

  function saveOutsideAreaColor(color) {
    try {
      localStorage.setItem(OUTSIDE_AREA_COLOR_KEY, color);
    } catch (_) {}
  }

  function getPdfJs() {
    if (!pdfjsLibPromise) {
      pdfjsLibPromise = import('https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.7.76/pdf.min.mjs')
        .then((pdfjsLib) => {
          if (pdfjsLib?.GlobalWorkerOptions) {
            pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.7.76/pdf.worker.min.mjs';
          }
          return pdfjsLib;
        });
    }
    return pdfjsLibPromise;
  }

  function setCanvasSize(width, height) {
    const safeW = Math.max(100, Math.round(width));
    const safeH = Math.max(100, Math.round(height));
    document.getElementById('canvasWidth').value = safeW;
    document.getElementById('canvasHeight').value = safeH;
    stage.style.width = `${safeW}px`;
    stage.style.height = `${safeH}px`;
    applyZoom();
  }

  function closestPaperSizeByRatio(width, height) {
    const ratio = Math.max(width, height) / Math.min(width, height);
    let best = null;
    for (const paper of PAPER_MM) {
      const pRatio = Math.max(paper.w, paper.h) / Math.min(paper.w, paper.h);
      const diff = Math.abs(ratio - pRatio);
      if (!best || diff < best.diff) best = { paper, diff };
    }
    return best;
  }

  function closestPaperSizeByMm(widthMm, heightMm) {
    const longSide = Math.max(widthMm, heightMm);
    const shortSide = Math.min(widthMm, heightMm);
    let best = null;
    for (const paper of PAPER_MM) {
      const pLong = Math.max(paper.w, paper.h);
      const pShort = Math.min(paper.w, paper.h);
      const diff = Math.abs(longSide - pLong) + Math.abs(shortSide - pShort);
      if (!best || diff < best.diff) best = { paper, diff };
    }
    return best;
  }

  function detectFileTypeFromSize({ widthPx, heightPx, widthMm = null, heightMm = null, sourceKind }) {
    if (widthMm && heightMm) {
      const best = closestPaperSizeByMm(widthMm, heightMm);
      if (best && best.diff < 16) {
        return `${sourceKind} / ${best.paper.name} 推定（実寸ベース）`;
      }
    }

    const bestByRatio = closestPaperSizeByRatio(widthPx, heightPx);
    if (bestByRatio) {
      const pct = ((bestByRatio.diff / (Math.max(widthPx, heightPx) / Math.min(widthPx, heightPx))) * 100).toFixed(2);
      return `${sourceKind} / ${bestByRatio.paper.name} 近似（比率差 ${pct}%）`;
    }

    return sourceKind;
  }

  function updateDetectedInfo(sizeText, typeText) {
    detectedSize.textContent = sizeText;
    detectedFileType.textContent = `ファイルタイプ: ${typeText}`;
  }

  function selectItem(item) {
    document.querySelectorAll('.stamp-item').forEach((el) => el.classList.remove('selected'));
    selected = item;
    if (item) {
      item.classList.add('selected');
      if (textVertical && item.dataset.stampType === 'text') {
        const mode = item.style.writingMode || window.getComputedStyle(item).writingMode || 'horizontal-tb';
        textVertical.checked = mode.startsWith('vertical');
      }
    }
  }

  function setTextDirection(node, vertical) {
    if (!node) return;
    if (vertical) {
      node.style.writingMode = 'vertical-rl';
      node.style.textOrientation = 'upright';
      node.style.lineHeight = node.style.lineHeight || '1.1';
    } else {
      node.style.writingMode = 'horizontal-tb';
      node.style.textOrientation = 'mixed';
      node.style.lineHeight = node.style.lineHeight || '1.2';
    }
  }

  function ensureResizeHandle(item) {
    if (!item || item.querySelector('.stamp-resize-handle')) return;
    const handle = document.createElement('div');
    handle.className = 'stamp-resize-handle';
    handle.addEventListener('mousedown', (event) => {
      event.preventDefault();
      event.stopPropagation();
      selectItem(item);
      const rect = item.getBoundingClientRect();
      const fontSize = parseFloat(item.style.fontSize || window.getComputedStyle(item).fontSize || '24');
      resizeState = {
        item,
        startX: event.clientX,
        startY: event.clientY,
        startWidth: Math.max(1, rect.width / (zoom || 1)),
        startHeight: Math.max(1, rect.height / (zoom || 1)),
        startFontSize: Number.isFinite(fontSize) ? fontSize : 24,
        isText: item.dataset.stampType === 'text',
      };
      dragState = null;
    });
    item.appendChild(handle);
  }

  function clampItemWithinStage(item) {
    if (!item) return;
    const stageSize = getStagePixelSize();
    const rect = item.getBoundingClientRect();
    const scale = zoom || 1;
    const w = Math.max(1, rect.width / scale);
    const h = Math.max(1, rect.height / scale);
    const maxX = Math.max(0, stageSize.width - w);
    const maxY = Math.max(0, stageSize.height - h);
    const x = Math.max(0, Math.min(maxX, parseFloat(item.style.left || '0')));
    const y = Math.max(0, Math.min(maxY, parseFloat(item.style.top || '0')));
    item.style.left = `${x}px`;
    item.style.top = `${y}px`;
  }

  function makeDraggable(item) {
    item.classList.add('stamp-item');
    item.style.left = item.style.left || '80px';
    item.style.top = item.style.top || '80px';
    ensureResizeHandle(item);

    item.addEventListener('mousedown', (event) => {
      if (event.target?.classList?.contains('stamp-resize-handle')) return;
      selectItem(item);
      const rect = item.getBoundingClientRect();
      dragState = { item, dx: event.clientX - rect.left, dy: event.clientY - rect.top };
    });

    item.addEventListener('dblclick', (event) => {
      if (item.dataset.stampType !== 'text') return;
      event.preventDefault();
      event.stopPropagation();
      selectItem(item);
      const current = item.textContent || '';
      const next = window.prompt('テキスト内容を編集', current);
      if (next === null) return;
      item.textContent = next;
      ensureResizeHandle(item);
    });
  }

  function isEditableTarget(target) {
    if (!target) return false;
    const tag = target.tagName?.toLowerCase();
    return tag === 'input' || tag === 'textarea' || target.isContentEditable;
  }

  function serializeStamp(node) {
    if (!node) return null;
    const base = {
      left: parseFloat(node.style.left || '0'),
      top: parseFloat(node.style.top || '0'),
    };

    if (node.tagName === 'IMG') {
      return {
        type: 'image',
        ...base,
        src: node.src,
        width: parseFloat(node.style.width || '0'),
        height: parseFloat(node.style.height || '0'),
      };
    }

    if (node.style.border) {
      return {
        type: 'shape',
        ...base,
        width: parseFloat(node.style.width || '0'),
        height: parseFloat(node.style.height || '0'),
        border: node.style.border,
        borderColor: node.style.borderColor,
        background: node.style.background,
        borderRadius: node.style.borderRadius || '',
      };
    }

    return {
      type: 'text',
      ...base,
      text: node.textContent || '',
      fontSize: node.style.fontSize || '24px',
      color: node.style.color || '#111',
      whiteSpace: node.style.whiteSpace || 'pre',
      width: parseFloat(node.style.width || '0'),
      height: parseFloat(node.style.height || '0'),
      fontFamily: node.style.fontFamily || '',
      fontWeight: node.style.fontWeight || '',
      lineHeight: node.style.lineHeight || '',
      writingMode: node.style.writingMode || '',
      textOrientation: node.style.textOrientation || '',
    };
  }

  function buildStampFromData(data) {
    if (!data) return null;
    let node = null;

    if (data.type === 'image') {
      node = document.createElement('img');
      node.dataset.stampType = 'image';
      node.src = data.src;
      if (data.width) node.style.width = `${data.width}px`;
      if (data.height) node.style.height = `${data.height}px`;
    } else if (data.type === 'shape') {
      node = document.createElement('div');
      node.dataset.stampType = 'shape';
      if (data.width) node.style.width = `${data.width}px`;
      if (data.height) node.style.height = `${data.height}px`;
      if (data.border) node.style.border = data.border;
      if (data.borderColor) node.style.borderColor = data.borderColor;
      if (data.background) node.style.background = data.background;
      if (data.borderRadius) node.style.borderRadius = data.borderRadius;
    } else if (data.type === 'text') {
      node = document.createElement('div');
      node.dataset.stampType = 'text';
      node.textContent = data.text || '入力文字';
      node.style.fontSize = data.fontSize || '24px';
      node.style.color = data.color || '#111';
      node.style.whiteSpace = data.whiteSpace || 'pre';
      if (data.width) node.style.width = `${data.width}px`;
      if (data.height) node.style.height = `${data.height}px`;
      if (data.fontFamily) node.style.fontFamily = data.fontFamily;
      if (data.fontWeight) node.style.fontWeight = data.fontWeight;
      if (data.lineHeight) node.style.lineHeight = data.lineHeight;
      if (data.writingMode) node.style.writingMode = data.writingMode;
      if (data.textOrientation) node.style.textOrientation = data.textOrientation;
    }

    if (!node) return null;

    const offset = 20;
    node.style.left = `${Math.max(0, (data.left || 0) + offset)}px`;
    node.style.top = `${Math.max(0, (data.top || 0) + offset)}px`;
    makeDraggable(node);
    return node;
  }

  function copySelectedStamp() {
    if (!selected) return false;
    clipboardStamp = serializeStamp(selected);
    return !!clipboardStamp;
  }

  function pasteStamp() {
    const node = buildStampFromData(clipboardStamp);
    if (!node) return false;
    overlayLayer.appendChild(node);
    selectItem(node);
    return true;
  }

  function createTextStamp(text) {
    const div = document.createElement('div');
    div.dataset.stampType = 'text';
    div.textContent = text || document.getElementById('textContent').value || '入力文字';
    div.style.fontSize = `${Number(document.getElementById('textSize').value)}px`;
    div.style.color = document.getElementById('textColor').value;
    div.style.whiteSpace = 'pre';
    div.style.fontFamily = textFontFamily?.value || '"Noto Sans JP", sans-serif';
    div.style.fontWeight = '500';
    div.style.lineHeight = '1.2';
    setTextDirection(div, !!textVertical?.checked);
    makeDraggable(div);
    return div;
  }

  function toStagePointFromEvent(event) {
    const rect = stage.getBoundingClientRect();
    const scale = zoom || 1;
    const x = (event.clientX - rect.left) / scale;
    const y = (event.clientY - rect.top) / scale;
    return {
      x: Math.max(0, Math.min(getStagePixelSize().width, x)),
      y: Math.max(0, Math.min(getStagePixelSize().height, y)),
    };
  }

  function hideContextMenu() {
    if (contextMenu) contextMenu.classList.add('hidden');
    contextTarget = null;
    contextPoint = null;
  }

  function ensureContextMenu() {
    if (contextMenu) return contextMenu;
    const menu = document.createElement('div');
    menu.className = 'powerstamp-context-menu hidden';
    menu.id = 'powerstampContextMenu';
    document.body.appendChild(menu);

    menu.addEventListener('click', async (event) => {
      const button = event.target.closest('button[data-action]');
      if (!button) return;
      const action = button.dataset.action;
      const point = contextPoint;
      const target = contextTarget;
      hideContextMenu();

      if (action === 'add-text' && point) {
        const node = createTextStamp();
        node.style.left = `${point.x}px`;
        node.style.top = `${point.y}px`;
        overlayLayer.appendChild(node);
        selectItem(node);
        return;
      }

      if (action === 'paste' && point) {
        try {
          const plain = await navigator.clipboard.readText();
          if (plain) {
            const node = createTextStamp(plain);
            node.style.left = `${point.x}px`;
            node.style.top = `${point.y}px`;
            overlayLayer.appendChild(node);
            selectItem(node);
            return;
          }
        } catch (_) {}

        if (clipboardStamp) {
          const node = buildStampFromData({ ...clipboardStamp, left: point.x, top: point.y });
          if (node) {
            node.style.left = `${point.x}px`;
            node.style.top = `${point.y}px`;
            overlayLayer.appendChild(node);
            selectItem(node);
          }
        }
        return;
      }

      if (!target || target.dataset.stampType !== 'text') return;
      selectItem(target);

      if (action === 'copy-text') {
        copySelectedStamp();
        try { await navigator.clipboard.writeText(target.textContent || ''); } catch (_) {}
        return;
      }

      if (action === 'toggle-direction') {
        const mode = target.style.writingMode || window.getComputedStyle(target).writingMode || 'horizontal-tb';
        const toVertical = !mode.startsWith('vertical');
        setTextDirection(target, toVertical);
        if (textVertical) textVertical.checked = toVertical;
        return;
      }

      if (action === 'change-color') {
        openColorDialogForTarget(target);
        return;
      }

      if (action === 'delete-text') {
        target.remove();
        if (selected === target) selected = null;
      }
    });

    contextMenu = menu;
    return menu;
  }

  function ensureColorDialog() {
    if (colorDialog) return colorDialog;
    const dialog = document.createElement('div');
    dialog.className = 'powerstamp-color-dialog hidden';
    dialog.innerHTML = `
      <div class="powerstamp-color-dialog-panel">
        <h4>テキスト色を変更</h4>
        <input type="color" id="powerstampColorDialogInput" value="#111111" />
        <div class="powerstamp-color-dialog-actions">
          <button type="button" data-action="cancel">キャンセル</button>
          <button type="button" data-action="apply" class="primary">OK</button>
        </div>
      </div>
    `;
    document.body.appendChild(dialog);
    colorDialog = dialog;
    colorDialogInput = dialog.querySelector('#powerstampColorDialogInput');

    dialog.addEventListener('click', (event) => {
      const action = event.target?.dataset?.action;
      if (event.target === dialog || action === 'cancel') {
        dialog.classList.add('hidden');
        contextColorTarget = null;
        return;
      }
      if (action === 'apply' && contextColorTarget && colorDialogInput) {
        contextColorTarget.style.color = colorDialogInput.value;
        dialog.classList.add('hidden');
        contextColorTarget = null;
      }
    });

    return dialog;
  }

  function openColorDialogForTarget(target) {
    if (!target || target.dataset.stampType !== 'text') return;
    const dialog = ensureColorDialog();
    const now = target.style.color || '#111111';
    const hex = rgbToHex(now) || '#111111';
    contextColorTarget = target;
    if (colorDialogInput) colorDialogInput.value = hex;
    dialog.classList.remove('hidden');
  }

  function rgbToHex(color) {
    if (/^#[0-9a-fA-F]{6}$/.test(String(color || ''))) return color;
    const m = String(color || '').match(/^rgb\(\s*(\d+),\s*(\d+),\s*(\d+)\s*\)$/i);
    if (!m) return null;
    return `#${[m[1], m[2], m[3]].map((n) => Number(n).toString(16).padStart(2, '0')).join('')}`;
  }

  function showContextMenu(event, target, point) {
    const menu = ensureContextMenu();
    const isText = !!target && target.dataset.stampType === 'text';
    menu.innerHTML = `
      <button data-action="add-text">ここにテキスト追加</button>
      <button data-action="paste">ここにペースト</button>
      ${isText ? '<button data-action="copy-text">コピー</button>' : ''}
      ${isText ? '<button data-action="toggle-direction">縦書き/横書き切替</button>' : ''}
      ${isText ? '<button data-action="change-color">色を変更</button>' : ''}
      ${isText ? '<button data-action="delete-text" class="danger">削除</button>' : ''}
    `;
    contextTarget = target || null;
    contextPoint = point || null;

    const pad = 6;
    const x = Math.min(window.innerWidth - 210, event.clientX + pad);
    const y = Math.min(window.innerHeight - 220, event.clientY + pad);
    menu.style.left = `${Math.max(6, x)}px`;
    menu.style.top = `${Math.max(6, y)}px`;
    menu.classList.remove('hidden');
  }

  function serializeAllStamps() {
    return Array.from(overlayLayer.children)
      .map((node) => serializeStamp(node))
      .filter(Boolean);
  }

  function restoreAllStamps(stamps) {
    overlayLayer.innerHTML = '';
    selected = null;
    if (!Array.isArray(stamps)) return;
    for (const stamp of stamps) {
      const node = buildStampFromData({ ...stamp, left: stamp.left || 0, top: stamp.top || 0 });
      if (!node) continue;
      node.style.left = `${Math.max(0, Number(stamp.left || 0))}px`;
      node.style.top = `${Math.max(0, Number(stamp.top || 0))}px`;
      overlayLayer.appendChild(node);
    }
  }

  function currentTemplatePayload() {
    return {
      version: 1,
      sourceSize,
      canvas: getStagePixelSize(),
      calibration: getCalibration(),
      stamps: serializeAllStamps(),
    };
  }

  async function refreshTemplateList(selectId = null) {
    if (!templateSelect) return;
    const res = await fetch('/tools/powerstamp/api/templates');
    const json = await res.json();
    templateSelect.innerHTML = '';

    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = `テンプレート選択（${json.count || 0}/${json.max || 20}）`;
    templateSelect.appendChild(placeholder);

    for (const tpl of (json.templates || [])) {
      const opt = document.createElement('option');
      opt.value = tpl.id;
      opt.textContent = tpl.name;
      templateSelect.appendChild(opt);
    }

    if (selectId) templateSelect.value = selectId;
  }

  async function saveTemplate() {
    const name = (templateNameInput?.value || '').trim();
    if (!name) {
      alert('テンプレート名を入力してください');
      return;
    }

    const payload = {
      name,
      data: currentTemplatePayload(),
    };

    const res = await fetch('/tools/powerstamp/api/templates', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const json = await res.json();
    if (!res.ok || !json.ok) {
      alert(json.error || 'テンプレート保存に失敗しました');
      return;
    }
    await refreshTemplateList(json.id);
  }

  async function loadTemplate() {
    const id = templateSelect?.value;
    if (!id) {
      alert('テンプレートを選択してください');
      return;
    }

    const res = await fetch(`/tools/powerstamp/api/templates/${id}`);
    const json = await res.json();
    if (!res.ok || !json.template) {
      alert(json.error || 'テンプレート読込に失敗しました');
      return;
    }

    const data = json.template.data || {};
    activeTemplateName = json.template.name || '';
    const canvas = data.canvas || {};
    if (canvas.width && canvas.height) setCanvasSize(canvas.width, canvas.height);

    if (data.calibration) {
      const ox = Number(data.calibration.offsetX ?? 0);
      const oy = Number(data.calibration.offsetY ?? 0);
      const sx = Number(data.calibration.scaleX ?? 1);
      const sy = Number(data.calibration.scaleY ?? 1);
      if (calibOffsetXInput) calibOffsetXInput.value = String(ox);
      if (calibOffsetYInput) calibOffsetYInput.value = String(oy);
      if (calibScaleXInput) calibScaleXInput.value = String(sx * 100);
      if (calibScaleYInput) calibScaleYInput.value = String(sy * 100);
      if (applyCalibrationOnExport) {
        applyCalibrationOnExport.checked = (ox !== 0 || oy !== 0 || sx !== 1 || sy !== 1);
      }
      saveCalibration();
    }

    sourceSize = data.sourceSize || sourceSize;
    restoreAllStamps(data.stamps || []);

    const envelopeGuide = buildEnvelopeGuideByName(activeTemplateName);
    const guide = envelopeGuide || (data.guide_mm ? { boxes_mm: data.guide_mm.boxes } : data.guide);
    activeGuide = guide || null;
    activePaper = data.paper || null;
    renderGuide(activeGuide, activePaper);
    updatePostalCodePanel();
    if (postalCodeInput?.value && !postalCodePanel?.classList.contains('hidden')) triggerPostalCodeApply();
  }

  async function deleteTemplate() {
    const id = templateSelect?.value;
    if (!id) {
      alert('削除するテンプレートを選択してください');
      return;
    }
    if (!confirm('選択中テンプレートを削除します。よろしいですか？')) return;

    const res = await fetch(`/tools/powerstamp/api/templates/${id}`, { method: 'DELETE' });
    const json = await res.json();
    if (!res.ok || !json.ok) {
      alert(json.error || 'テンプレート削除に失敗しました');
      return;
    }
    await refreshTemplateList();
  }

    stage.addEventListener('click', (event) => {
    if (!waitingAnchorPick) return;
    const rect = stage.getBoundingClientRect();
    const scale = zoom || 1;
    anchorPoint = {
      x: (event.clientX - rect.left) / scale,
      y: (event.clientY - rect.top) / scale,
    };
    waitingAnchorPick = false;
    setAnchorUI(anchorPoint);
  });

  stage.addEventListener('contextmenu', (event) => {
    const stamp = event.target?.closest?.('.stamp-item');
    const inStage = stage.contains(event.target);
    if (!inStage) return;
    event.preventDefault();
    const point = toStagePointFromEvent(event);
    if (stamp) selectItem(stamp);
    showContextMenu(event, stamp || null, point);
  });


  function buildPreviewState() {
    return {
      canvas: getStagePixelSize(),
      backgroundSrc: backgroundLayer?.src || '',
      stamps: serializeAllStamps(),
    };
  }

  function preparePreviewStateForNewTab(event) {
    try {
      localStorage.setItem('powerstampPreviewState', JSON.stringify(buildPreviewState()));
    } catch (e) {
      event.preventDefault();
      alert(`プレビュー状態の保存に失敗しました: ${e.message}`);
    }
  }

document.addEventListener('mousemove', (event) => {
    if (resizeState) {
      const scale = zoom || 1;
      const dx = (event.clientX - resizeState.startX) / scale;
      const dy = (event.clientY - resizeState.startY) / scale;
      const nextW = Math.max(20, resizeState.startWidth + dx);
      const nextH = Math.max(20, resizeState.startHeight + dy);

      if (resizeState.isText) {
        const baseW = Math.max(1, resizeState.startWidth);
        const baseH = Math.max(1, resizeState.startHeight);
        const ratio = Math.max(nextW / baseW, nextH / baseH);
        const nextFont = Math.max(8, resizeState.startFontSize * ratio);
        resizeState.item.style.fontSize = `${Math.round(nextFont)}px`;
      } else {
        resizeState.item.style.width = `${Math.round(nextW)}px`;
        resizeState.item.style.height = `${Math.round(nextH)}px`;
      }

      clampItemWithinStage(resizeState.item);
      return;
    }

    if (!dragState) return;
    const stageRect = stage.getBoundingClientRect();
    const itemRect = dragState.item.getBoundingClientRect();
    const scale = zoom || 1;
    const logicalStageW = stageRect.width / scale;
    const logicalStageH = stageRect.height / scale;
    const logicalItemW = itemRect.width / scale;
    const logicalItemH = itemRect.height / scale;
    const maxX = Math.max(0, logicalStageW - logicalItemW);
    const maxY = Math.max(0, logicalStageH - logicalItemH);
    const x = (event.clientX - stageRect.left - dragState.dx) / scale;
    const y = (event.clientY - stageRect.top - dragState.dy) / scale;
    dragState.item.style.left = `${Math.min(maxX, Math.max(0, x))}px`;
    dragState.item.style.top = `${Math.min(maxY, Math.max(0, y))}px`;
  });

  document.addEventListener('mouseup', () => {
    dragState = null;
    if (resizeState) {
      clampItemWithinStage(resizeState.item);
      resizeState = null;
    }
  });

  document.addEventListener('click', (event) => {
    if (contextMenu && !contextMenu.contains(event.target)) {
      hideContextMenu();
    }
  });

  stageScroll?.addEventListener('scroll', hideContextMenu);

  document.addEventListener('keydown', (event) => {
    if (isEditableTarget(event.target)) return;

    const key = event.key.toLowerCase();
    const withMeta = event.ctrlKey || event.metaKey;

    if (event.key === 'Delete' && selected) {
      selected.remove();
      selected = null;
      return;
    }

    if (withMeta && key === 'c' && selected) {
      event.preventDefault();
      copySelectedStamp();
      return;
    }

    if (withMeta && key === 'v') {
      event.preventDefault();
      pasteStamp();
    }

    if (event.key === 'Escape') {
      hideContextMenu();
    }
  });

  document.getElementById('backgroundInput').addEventListener('change', async (event) => {
    const file = event.target.files[0];
    if (!file) return;

    if (file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf')) {
      try {
        const data = await file.arrayBuffer();
        const pdfjsLib = await getPdfJs();
        const loadingTask = pdfjsLib.getDocument({ data });
        const pdf = await loadingTask.promise;
        const page = await pdf.getPage(1);

        const sourceViewport = page.getViewport({ scale: 1 });
        const renderViewport = sourceViewport;
        const widthPt = sourceViewport.width;
        const heightPt = sourceViewport.height;
        const widthMm = widthPt * 25.4 / 72;
        const heightMm = heightPt * 25.4 / 72;

        // PDFはページ実寸ptをそのまま基準寸法として扱う（背景ファイルサイズ準拠）。
        sourceSize = { width: widthPt, height: heightPt, unit: 'pt' };
        const sourceType = detectFileTypeFromSize({
          widthPx: widthPt,
          heightPx: heightPt,
          widthMm,
          heightMm,
          sourceKind: 'PDF'
        });

        const canvas = document.createElement('canvas');
        const ctx = canvas.getContext('2d');
        canvas.width = renderViewport.width;
        canvas.height = renderViewport.height;

        await page.render({ canvasContext: ctx, viewport: renderViewport }).promise;
        backgroundLayer.src = canvas.toDataURL('image/png');

        setCanvasSize(widthPt, heightPt);
        updateDetectedInfo(
          `PDF 1ページ目: ${Math.round(widthPt)}×${Math.round(heightPt)}pt（約 ${widthMm.toFixed(1)}×${heightMm.toFixed(1)}mm）`,
          sourceType
        );
      } catch (error) {
        updateDetectedInfo(`PDF読込失敗: ${error.message}`, '判定失敗');
      }
      return;
    }

    const reader = new FileReader();
    reader.onload = (e) => {
      const img = new Image();
      img.onload = () => {
        backgroundLayer.src = e.target.result;
        sourceSize = { width: img.naturalWidth, height: img.naturalHeight, unit: 'px' };
        const sourceType = detectFileTypeFromSize({
          widthPx: img.naturalWidth,
          heightPx: img.naturalHeight,
          sourceKind: '画像'
        });
        setCanvasSize(img.naturalWidth, img.naturalHeight);
        updateDetectedInfo(`画像: ${img.naturalWidth}×${img.naturalHeight}px`, sourceType);
      };
      img.src = e.target.result;
    };
    reader.readAsDataURL(file);
  });

  document.getElementById('resizeCanvasBtn').addEventListener('click', () => {
    const width = Number(document.getElementById('canvasWidth').value);
    const height = Number(document.getElementById('canvasHeight').value);
    if (width >= 100 && height >= 100) {
      setCanvasSize(width, height);
      const id = templateSelect?.value;
      if (id) {
        loadTemplate().catch(() => {});
      } else if (postalCodeInput?.value) {
        triggerPostalCodeApply();
      }
    }
  });

  document.getElementById('addTextBtn').addEventListener('click', () => {
    const div = createTextStamp();
    overlayLayer.appendChild(div);
    selectItem(div);
  });

  toggleSelectedTextDirectionBtn?.addEventListener('click', () => {
    if (!selected || selected.dataset.stampType !== 'text') {
      alert('テキストスタンプを選択してから実行してください。');
      return;
    }
    const mode = selected.style.writingMode || window.getComputedStyle(selected).writingMode || 'horizontal-tb';
    const toVertical = !mode.startsWith('vertical');
    setTextDirection(selected, toVertical);
    if (textVertical) textVertical.checked = toVertical;
  });

  document.getElementById('addShapeBtn').addEventListener('click', () => {
    const shape = document.createElement('div');
    shape.dataset.stampType = 'shape';
    shape.style.width = `${Number(document.getElementById('shapeWidth').value)}px`;
    shape.style.height = `${Number(document.getElementById('shapeHeight').value)}px`;
    shape.style.border = `2px solid ${document.getElementById('shapeStroke').value}`;
    shape.style.background = document.getElementById('shapeFill').value;
    if (document.getElementById('shapeType').value === 'ellipse') shape.style.borderRadius = '9999px';
    makeDraggable(shape);
    overlayLayer.appendChild(shape);
    selectItem(shape);
  });

  document.getElementById('addImageBtn').addEventListener('click', () => {
    const file = document.getElementById('stampImageInput').files[0];
    if (!file) return alert('画像スタンプを選択してください');

    const scale = Number(document.getElementById('imageScale').value) / 100;
    const reader = new FileReader();
    reader.onload = (event) => {
      const img = document.createElement('img');
      img.dataset.stampType = 'image';
      img.src = event.target.result;
      img.onload = () => {
        img.style.width = `${Math.max(20, img.naturalWidth * scale)}px`;
        img.style.height = `${Math.max(20, img.naturalHeight * scale)}px`;
      };
      makeDraggable(img);
      overlayLayer.appendChild(img);
      selectItem(img);
    };
    reader.readAsDataURL(file);
  });

  document.getElementById('clearBtn').addEventListener('click', () => {
    overlayLayer.innerHTML = '';
    selectItem(null);
  });

    async function printOverlayDirect(win) {
    const stageSize = getStagePixelSize();
    const mode = document.querySelector('input[name="exportSizeMode"]:checked')?.value || 'source';

    let outWidth = stageSize.width;
    let outHeight = stageSize.height;

    if (mode === 'source') {
      outWidth = Math.round(sourceSize.width || stageSize.width);
      outHeight = Math.round(sourceSize.height || stageSize.height);
    } else if (mode === 'paper') {
      const paper = getPaperOutputSize();
      outWidth = paper.width;
      outHeight = paper.height;
    }

    if (useAnchorOrigin?.checked && !anchorPoint) {
      alert('左上基準点が未設定です。');
      if (win) win.close();
      return;
    }

    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, outWidth);
    canvas.height = Math.max(1, outHeight);
    const ctx = canvas.getContext('2d');
    await drawOverlayToCanvas(ctx, canvas.width, canvas.height);
    const dataUrl = canvas.toDataURL('image/png');

    let pageWmm = null;
    let pageHmm = null;
    if (mode === 'paper') {
      const key = paperSizeSelect?.value || 'A4';
      const p = PAPER_MAP_MM[key] || PAPER_MAP_MM.A4;
      const isLandscape = (paperOrientation?.value || 'portrait') === 'landscape';
      pageWmm = isLandscape ? p.h : p.w;
      pageHmm = isLandscape ? p.w : p.h;
    } else if (sourceSize.unit === 'pt') {
      pageWmm = (outWidth * 25.4) / 72;
      pageHmm = (outHeight * 25.4) / 72;
    }

    if (!win) {
      alert('印刷ウィンドウを開けません。ポップアップを許可してください。');
      return;
    }

    const pageSizeCss = (pageWmm && pageHmm)
      ? `@page { size: ${pageWmm.toFixed(2)}mm ${pageHmm.toFixed(2)}mm; margin: 0; }`
      : '@page { margin: 0; }';

    win.document.write(`<!doctype html><html><head><meta charset="utf-8"><title>PowerSTAMP Print</title><style>${pageSizeCss} html,body{margin:0;padding:0;} .sheet{width:100vw;height:100vh;display:flex;align-items:stretch;justify-content:stretch;} img{width:100%;height:100%;object-fit:fill;}</style></head><body><div class="sheet"><img src="${dataUrl}" /></div><script>window.onload=()=>{setTimeout(()=>{window.print();},150)}<\/script></body></html>`);
    win.document.close();
  }

  document.getElementById('printBtn').addEventListener('click', () => {
    const win = window.open('', '_blank', 'noopener');
    if (!win) {
      alert('印刷ウィンドウを開けません。ポップアップを許可してください。');
      return;
    }
    printOverlayDirect(win).catch((e) => {
      try { win.close(); } catch (_) {}
      alert(`印刷準備エラー: ${e.message}`);
    });
  });

  zoomInBtn?.addEventListener('click', () => setZoom(zoom + ZOOM_STEP));
  zoomOutBtn?.addEventListener('click', () => setZoom(zoom - ZOOM_STEP));
  zoomResetBtn?.addEventListener('click', () => setZoom(1));
  zoomFitBtn?.addEventListener('click', () => fitStageToScreen());
  zoomSlider?.addEventListener('input', () => {
    const pct = Number(zoomSlider.value || 100);
    setZoom(pct / 100);
  });
  outsideAreaColorInput?.addEventListener('input', () => {
    const color = outsideAreaColorInput.value || OUTSIDE_AREA_DEFAULT;
    applyOutsideAreaColor(color);
    saveOutsideAreaColor(color);
  });
  outsideAreaColorResetBtn?.addEventListener('click', () => {
    applyOutsideAreaColor(OUTSIDE_AREA_DEFAULT);
    saveOutsideAreaColor(OUTSIDE_AREA_DEFAULT);
  });

  stageScroll?.addEventListener('wheel', (event) => {
    if (!event.ctrlKey) return;
    event.preventDefault();
    const delta = event.deltaY > 0 ? -ZOOM_STEP : ZOOM_STEP;
    setZoom(zoom + delta);
  }, { passive: false });

  [calibOffsetXInput, calibOffsetYInput, calibScaleXInput, calibScaleYInput].forEach((input) => {
    input?.addEventListener('change', saveCalibration);
  });

  calibResetBtn?.addEventListener('click', () => {
    if (calibOffsetXInput) calibOffsetXInput.value = '0';
    if (calibOffsetYInput) calibOffsetYInput.value = '0';
    if (calibScaleXInput) calibScaleXInput.value = '100';
    if (calibScaleYInput) calibScaleYInput.value = '100';
    saveCalibration();
  });

  saveTemplateBtn?.addEventListener('click', () => {
    saveTemplate().catch((e) => alert(`テンプレ保存エラー: ${e.message}`));
  });
  loadTemplateBtn?.addEventListener('click', () => {
    loadTemplate().catch((e) => alert(`テンプレ読込エラー: ${e.message}`));
  });
  deleteTemplateBtn?.addEventListener('click', () => {
    deleteTemplate().catch((e) => alert(`テンプレ削除エラー: ${e.message}`));
  });

  applyPostalCodeBtn?.addEventListener('click', triggerPostalCodeApply);
  postalCodeInput?.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      triggerPostalCodeApply();
    }
  });
  postalCodeInput?.addEventListener('input', () => {
    const normalized = normalizePostalCode(postalCodeInput.value || '');
    if (postalCodeInput.value !== normalized) {
      postalCodeInput.value = normalized;
    }
  });

  openPreviewTabBtn?.addEventListener('click', preparePreviewStateForNewTab);

  setAnchorBtn?.addEventListener('click', () => {
    waitingAnchorPick = true;
    if (anchorStatus) anchorStatus.textContent = 'クリック待ち...';
  });

  useAnchorOrigin?.addEventListener('change', () => {
    if (!useAnchorOrigin.checked) {
      waitingAnchorPick = false;
    }
  });

  async function drawOverlayToCanvas(ctx, outputWidth, outputHeight) {
    const { width: stageWidth, height: stageHeight } = getStagePixelSize();
    const scaleX = outputWidth / Math.max(1, stageWidth);
    const scaleY = outputHeight / Math.max(1, stageHeight);
    const ax = (useAnchorOrigin?.checked && anchorPoint) ? anchorPoint.x : 0;
    const ay = (useAnchorOrigin?.checked && anchorPoint) ? anchorPoint.y : 0;
    const calibEnabled = !!applyCalibrationOnExport?.checked;
    const calib = calibEnabled ? getCalibration() : { offsetX: 0, offsetY: 0, scaleX: 1, scaleY: 1 };

    const applyCalibX = (v) => (v * calib.scaleX) + calib.offsetX;
    const applyCalibY = (v) => (v * calib.scaleY) + calib.offsetY;
    const applyCalibW = (v) => v * calib.scaleX;
    const applyCalibH = (v) => v * calib.scaleY;

    for (const node of Array.from(overlayLayer.children)) {
      const baseX = (parseFloat(node.style.left || '0') - ax) * scaleX;
      const baseY = (parseFloat(node.style.top || '0') - ay) * scaleY;
      const x = applyCalibX(baseX);
      const y = applyCalibY(baseY);
      if (node.tagName === 'IMG') {
        await new Promise((resolve) => {
          const image = new Image();
          image.onload = () => {
            const w = applyCalibW(parseFloat(node.style.width || image.naturalWidth) * scaleX);
            const h = applyCalibH(parseFloat(node.style.height || image.naturalHeight) * scaleY);
            ctx.drawImage(image, x, y, w, h);
            resolve();
          };
          image.src = node.src;
        });
      } else if (node.style.border) {
        const w = applyCalibW(parseFloat(node.style.width || '0') * scaleX);
        const h = applyCalibH(parseFloat(node.style.height || '0') * scaleY);
        if (node.style.borderRadius) {
          ctx.beginPath();
          ctx.ellipse(x + w / 2, y + h / 2, w / 2, h / 2, 0, 0, Math.PI * 2);
          ctx.fillStyle = node.style.background || 'transparent';
          ctx.fill();
          ctx.lineWidth = 2;
          ctx.strokeStyle = node.style.borderColor || '#000';
          ctx.stroke();
        } else {
          ctx.fillStyle = node.style.background || 'transparent';
          ctx.fillRect(x, y, w, h);
          ctx.lineWidth = 2;
          ctx.strokeStyle = node.style.borderColor || '#000';
          ctx.strokeRect(x, y, w, h);
        }
      } else {
        const computed = window.getComputedStyle(node);
        const baseFontSize = parseFloat(computed.fontSize || node.style.fontSize || '24') * Math.min(scaleX, scaleY);
        const fontSize = Math.max(8, baseFontSize * Math.min(calib.scaleX, calib.scaleY));
        const fontFamily = node.style.fontFamily || computed.fontFamily || '"Noto Sans JP", sans-serif';
        const fontWeight = node.style.fontWeight || computed.fontWeight || '400';
        const rawLineHeight = node.style.lineHeight || computed.lineHeight || 'normal';
        let lineHeight = fontSize * 1.2;
        if (rawLineHeight !== 'normal') {
          if (rawLineHeight.endsWith('px')) {
            lineHeight = parseFloat(rawLineHeight) * Math.min(scaleX, scaleY) * Math.min(calib.scaleX, calib.scaleY);
          } else {
            const ratio = parseFloat(rawLineHeight);
            if (Number.isFinite(ratio) && ratio > 0) lineHeight = fontSize * ratio;
          }
        }

        ctx.fillStyle = node.style.color || computed.color || '#111';
        ctx.textBaseline = 'top';
        ctx.font = `${fontWeight} ${fontSize}px ${fontFamily}`;

        const lines = (node.textContent || '').split('\n');
        const writingMode = node.style.writingMode || computed.writingMode || 'horizontal-tb';
        if (writingMode.startsWith('vertical')) {
          const columnGap = fontSize * 1.05;
          lines.forEach((line, colIdx) => {
            let cursorY = y;
            const columnX = x + (columnGap * colIdx);
            for (const ch of Array.from(line)) {
              ctx.fillText(ch, columnX, cursorY);
              cursorY += lineHeight;
            }
          });
        } else {
          lines.forEach((line, idx) => {
            ctx.fillText(line, x, y + lineHeight * idx);
          });
        }
      }
    }
  }

  document.getElementById('exportBtn').addEventListener('click', async () => {
    const mode = document.querySelector('input[name="exportSizeMode"]:checked')?.value || 'source';
    const canvas = document.createElement('canvas');

    const stageSize = getStagePixelSize();
    // 寸法仕様:
    // - 画像背景: sourceSize は px
    // - PDF背景: sourceSize は pt(1/72inch)
    // 「背景元サイズ」出力では sourceSize の数値をそのまま出力キャンバス寸法として採用する。
    let outWidth = stageSize.width;
    let outHeight = stageSize.height;

    if (mode === 'source') {
      outWidth = Math.round(sourceSize.width || stageSize.width);
      outHeight = Math.round(sourceSize.height || stageSize.height);
    } else if (mode === 'paper') {
      const paper = getPaperOutputSize();
      outWidth = paper.width;
      outHeight = paper.height;
    }

    canvas.width = Math.max(1, outWidth);
    canvas.height = Math.max(1, outHeight);
    const ctx = canvas.getContext('2d');

    if (useAnchorOrigin?.checked && !anchorPoint) {
      alert('左上基準点が未設定です。「基準点を設定」を押して背景左上をクリックしてください。');
      return;
    }

    await drawOverlayToCanvas(ctx, canvas.width, canvas.height);

    const link = document.createElement('a');
    link.href = canvas.toDataURL('image/png');
    link.download = mode === 'source' ? 'powerstamp-overlay-source-size.png' : 'powerstamp-overlay-custom-size.png';
    link.click();
  });

  setAnchorUI(null);
  updatePostalCodePanel();
  loadCalibration();
  loadOutsideAreaColor();
  applyZoom();
  refreshTemplateList().catch(() => {});
})();
