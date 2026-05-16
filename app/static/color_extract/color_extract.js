(() => {
  const form = document.getElementById('colorExtractForm');
  const fileInput = document.getElementById('fileInput');
  const mode = document.getElementById('mode');
  const preset = document.getElementById('preset');
  const colorMetric = document.getElementById('colorMetric');
  const tolerance = document.getElementById('tolerance');
  const tolValue = document.getElementById('tolValue');
  const saturationMin = document.getElementById('saturationMin');
  const satValue = document.getElementById('satValue');
  const lightnessMax = document.getElementById('lightnessMax');
  const lightMaxValue = document.getElementById('lightMaxValue');
  const lightnessMin = document.getElementById('lightnessMin');
  const enablePreprocess = document.getElementById('enablePreprocess');
  const enablePostprocess = document.getElementById('enablePostprocess');
  const minComponentArea = document.getElementById('minComponentArea');
  const autoTuneBtn = document.getElementById('autoTuneBtn');
  const outputFormat = document.getElementById('outputFormat');
  const transparentOutput = document.getElementById('transparentOutput');
  const outputRegionPng = document.getElementById('outputRegionPng');
  const replacementWrap = document.getElementById('replacementWrap');
  const replacementColor = document.getElementById('replacementColor');
  const replacementHex = document.getElementById('replacementHex');
  const targetColorsInput = document.getElementById('targetColorsInput');
  const processRegionInput = document.getElementById('processRegionInput');
  const colorList = document.getElementById('colorList');
  const addColorBtn = document.getElementById('addColorBtn');
  const eyedropperBtn = document.getElementById('eyedropperBtn');
  const previewImage = document.getElementById('previewImage');
  const previewPlaceholder = document.getElementById('previewPlaceholder');
  const previewStatus = document.getElementById('previewStatus');
  const previewViewport = document.getElementById('previewViewport');
  const paintOverlay = document.getElementById('paintOverlay');
  const selectionBox = document.getElementById('selectionBox');
  const zoomOutBtn = document.getElementById('zoomOutBtn');
  const zoomInBtn = document.getElementById('zoomInBtn');
  const resetViewBtn = document.getElementById('resetViewBtn');
  const toolHandBtn = document.getElementById('toolHandBtn');
  const toolPickerBtn = document.getElementById('toolPickerBtn');
  const zoomLabel = document.getElementById('zoomLabel');
  const toggleRegionBtn = document.getElementById('toggleRegionBtn');
  const clearRegionBtn = document.getElementById('clearRegionBtn');
  const pdfPageWrap = document.getElementById('pdfPageWrap');
  const pdfPrevBtn = document.getElementById('pdfPrevBtn');
  const pdfNextBtn = document.getElementById('pdfNextBtn');
  const pdfPageInput = document.getElementById('pdfPageInput');
  const pdfPageTotalLabel = document.getElementById('pdfPageTotalLabel');
  const previewPageHidden = document.getElementById('previewPageHidden');
  const paintActionsInput = document.getElementById('paintActionsInput');
  const rectFillToolBtn = document.getElementById('rectFillToolBtn');
  const freeDrawToolBtn = document.getElementById('freeDrawToolBtn');
  const undoPaintBtn = document.getElementById('undoPaintBtn');
  const clearPaintBtn = document.getElementById('clearPaintBtn');
  const paintColor = document.getElementById('paintColor');
  const paintHex = document.getElementById('paintHex');
  const paintEyedropperBtn = document.getElementById('paintEyedropperBtn');
  const paintAlpha = document.getElementById('paintAlpha');
  const paintAlphaLabel = document.getElementById('paintAlphaLabel');
  const paintSize = document.getElementById('paintSize');
  const simpleModeBtn = document.getElementById('simpleModeBtn');
  const kodawariModeBtn = document.getElementById('kodawariModeBtn');
  const kodawariOnlyElements = Array.from(document.querySelectorAll('[data-kodawari-only]'));

  let currentPreviewUrl = null;
  let debounceTimer = null;
  let previewAbortController = null;
  let previewRequestSeq = 0;
  let colors = ['#ff0000'];
  const presetDefaults = {
    stamp_red: {
      mode: 'exclude',
      colors: ['#dd2222'],
      colorMetric: 'deltae',
      tolerance: 30,
      saturationMin: 35,
      lightnessMax: 240,
      minComponentArea: 30,
      transparentOutput: true,
    },
    blue_pen: {
      mode: 'exclude',
      colors: ['#0057d9'],
      colorMetric: 'deltae',
      tolerance: 28,
      saturationMin: 30,
      lightnessMax: 245,
      minComponentArea: 20,
      transparentOutput: true,
    },
    highlighter: {
      mode: 'exclude',
      colors: ['#fff04a'],
      colorMetric: 'deltae',
      tolerance: 32,
      saturationMin: 25,
      lightnessMax: 255,
      minComponentArea: 20,
      transparentOutput: true,
    },
    keep_black_text: {
      mode: 'extract',
      colors: ['#111111'],
      colorMetric: 'rgb',
      tolerance: 55,
      saturationMin: 0,
      lightnessMax: 120,
      minComponentArea: 8,
      transparentOutput: false,
    },
    whiten_background: {
      mode: 'replace',
      colors: ['#f4f4f4'],
      replacementColor: '#ffffff',
      colorMetric: 'rgb',
      tolerance: 38,
      saturationMin: 0,
      lightnessMax: 255,
      minComponentArea: 0,
      transparentOutput: false,
    },
  };
  let zoomLevel = 1;
  let panX = 0;
  let panY = 0;
  let isPanning = false;
  let panStartX = 0;
  let panStartY = 0;
  let activeTool = 'picker';

  let regionMode = false;
  let selectingRegion = false;
  let selectionStart = null;
  let selectedRegion = null;
  let pdfTotalPages = 1;
  let paintActions = [];
  let paintDrawing = false;
  let paintStart = null;
  let paintTempRect = null;
  let paintCurrentStroke = null;
  let isKodawariMode = false;

  function normalizeHex(value) {
    const v = (value || '').trim();
    const withHash = v.startsWith('#') ? v : `#${v}`;
    return /^#[0-9a-fA-F]{6}$/.test(withHash) ? withHash.toLowerCase() : null;
  }

  function normalizeHexRgba(value) {
    const v = (value || '').trim();
    const withHash = v.startsWith('#') ? v : `#${v}`;
    if (/^#[0-9a-fA-F]{8}$/.test(withHash)) return withHash.toUpperCase();
    if (/^#[0-9a-fA-F]{6}$/.test(withHash)) return `${withHash.toUpperCase()}${Number(paintAlpha.value).toString(16).padStart(2, '0').toUpperCase()}`;
    return null;
  }

  function currentPaintColorRgba() {
    const rgb = paintColor.value.toUpperCase();
    const a = Number(paintAlpha.value).toString(16).padStart(2, '0').toUpperCase();
    return `${rgb}${a}`;
  }

  function isPdfInput() {
    return !!(fileInput.files[0] && fileInput.files[0].name.toLowerCase().endsWith('.pdf'));
  }

  function currentPageForAction() {
    return isPdfInput() ? (Number(previewPageHidden.value) || 1) : null;
  }

  function clamp01(v) {
    return Math.min(1, Math.max(0, v));
  }

  function actionAppliesToCurrentPage(action) {
    if (!isPdfInput()) return true;
    return Number(action.page || 1) === (Number(previewPageHidden.value) || 1);
  }

  function undoPaintAction() {
    if (paintDrawing) return;
    if (isPdfInput()) {
      const currentPage = Number(previewPageHidden.value) || 1;
      for (let i = paintActions.length - 1; i >= 0; i -= 1) {
        if (Number(paintActions[i].page || 1) === currentPage) {
          paintActions.splice(i, 1);
          redrawPaintOverlay();
          schedulePreview();
          return;
        }
      }
      return;
    }
    if (paintActions.length === 0) return;
    paintActions.pop();
    redrawPaintOverlay();
    schedulePreview();
  }

  function redrawPaintOverlay() {
    if (!previewImage.src || !previewImage.naturalWidth || !previewImage.naturalHeight) {
      paintOverlay.classList.add('hidden');
      return;
    }
    const w = previewImage.naturalWidth;
    const h = previewImage.naturalHeight;
    if (paintOverlay.width !== w || paintOverlay.height !== h) {
      paintOverlay.width = w;
      paintOverlay.height = h;
    }
    paintOverlay.style.width = `${previewImage.clientWidth}px`;
    paintOverlay.style.height = `${previewImage.clientHeight}px`;
    paintOverlay.classList.remove('hidden');
    const ctx = paintOverlay.getContext('2d');
    ctx.clearRect(0, 0, w, h);

    const drawAction = (action) => {
      const color = String(action.color || '#000000FF');
      const r = parseInt(color.slice(1, 3), 16);
      const g = parseInt(color.slice(3, 5), 16);
      const b = parseInt(color.slice(5, 7), 16);
      const a = parseInt(color.slice(7, 9), 16) / 255.0;
      const size = Math.max(1, Number(action.size || 8));
      if (action.type === 'rect_fill') {
        const x = clamp01(Number(action.x || 0)) * w;
        const y = clamp01(Number(action.y || 0)) * h;
        const rw = clamp01(Number(action.w || 0)) * w;
        const rh = clamp01(Number(action.h || 0)) * h;
        if (a === 0) {
          ctx.save();
          ctx.globalCompositeOperation = 'destination-out';
          ctx.fillStyle = 'rgba(0,0,0,1)';
          ctx.fillRect(x, y, rw, rh);
          ctx.restore();
        } else {
          ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${a})`;
          ctx.fillRect(x, y, rw, rh);
        }
      } else if (action.type === 'free_draw' && Array.isArray(action.points) && action.points.length > 0) {
        const points = action.points.map((p) => ({
          x: clamp01(Number(p.x || 0)) * w,
          y: clamp01(Number(p.y || 0)) * h,
        }));
        if (points.length === 1) {
          ctx.beginPath();
          ctx.arc(points[0].x, points[0].y, size / 2, 0, Math.PI * 2);
          if (a === 0) {
            ctx.save();
            ctx.globalCompositeOperation = 'destination-out';
            ctx.fillStyle = 'rgba(0,0,0,1)';
            ctx.fill();
            ctx.restore();
          } else {
            ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${a})`;
            ctx.fill();
          }
          return;
        }
        ctx.lineWidth = size;
        ctx.lineJoin = 'round';
        ctx.lineCap = 'round';
        ctx.beginPath();
        ctx.moveTo(points[0].x, points[0].y);
        for (let i = 1; i < points.length; i += 1) ctx.lineTo(points[i].x, points[i].y);
        if (a === 0) {
          ctx.save();
          ctx.globalCompositeOperation = 'destination-out';
          ctx.strokeStyle = 'rgba(0,0,0,1)';
          ctx.stroke();
          ctx.restore();
        } else {
          ctx.strokeStyle = `rgba(${r}, ${g}, ${b}, ${a})`;
          ctx.stroke();
        }
      }
    };

    paintActions.filter(actionAppliesToCurrentPage).forEach(drawAction);
    if (paintTempRect && actionAppliesToCurrentPage(paintTempRect)) drawAction(paintTempRect);
    if (paintCurrentStroke && actionAppliesToCurrentPage(paintCurrentStroke)) drawAction(paintCurrentStroke);
  }

  function renderColorList() {
    colorList.innerHTML = '';
    colors.forEach((color, idx) => {
      const row = document.createElement('div');
      row.className = 'flex items-center gap-2';
      row.innerHTML = `
        <input type="color" class="h-10 w-14 border rounded" value="${color}" data-type="picker" data-idx="${idx}">
        <input type="text" class="flex-1 border rounded px-3 py-2 uppercase" value="${color}" data-type="hex" data-idx="${idx}">
        <button type="button" class="text-xs bg-red-100 border px-2 py-1 rounded hover:bg-red-200" data-type="remove" data-idx="${idx}">削除</button>
      `;
      colorList.appendChild(row);
    });
    targetColorsInput.value = JSON.stringify(colors);
  }

  function applyPreviewTransform() {
    previewImage.style.transformOrigin = 'center center';
    previewImage.style.transform = `translate(${panX}px, ${panY}px) scale(${zoomLevel})`;
    paintOverlay.style.transformOrigin = 'center center';
    paintOverlay.style.transform = `translate(${panX}px, ${panY}px) scale(${zoomLevel})`;
    zoomLabel.textContent = `${Math.round(zoomLevel * 100)}%`;

    if (regionMode || activeTool === 'picker' || activeTool === 'rect_fill' || activeTool === 'free_draw') {
      previewImage.style.cursor = 'crosshair';
      paintOverlay.style.cursor = 'crosshair';
    } else {
      previewImage.style.cursor = zoomLevel > 1 ? (isPanning ? 'grabbing' : 'grab') : 'grab';
      paintOverlay.style.cursor = previewImage.style.cursor;
    }
  }

  function changeZoom(step) {
    const next = Math.min(4, Math.max(0.25, zoomLevel + step));
    zoomLevel = Number(next.toFixed(2));
    if (zoomLevel <= 1) {
      panX = 0;
      panY = 0;
    }
    applyPreviewTransform();
    requestAnimationFrame(renderSelectionBox);
  }

  function resetPreviewView() {
    zoomLevel = 1;
    panX = 0;
    panY = 0;
    applyPreviewTransform();
    requestAnimationFrame(renderSelectionBox);
  }

  function setActiveTool(tool) {
    if (!isKodawariMode && tool !== 'picker') {
      tool = 'picker';
    }
    activeTool = tool;
    toolHandBtn.classList.toggle('bg-sky-300', tool === 'hand');
    toolPickerBtn.classList.toggle('bg-indigo-300', tool === 'picker');
    rectFillToolBtn.classList.toggle('bg-fuchsia-300', tool === 'rect_fill');
    freeDrawToolBtn.classList.toggle('bg-fuchsia-300', tool === 'free_draw');
    toolHandBtn.classList.toggle('font-semibold', tool === 'hand');
    toolPickerBtn.classList.toggle('font-semibold', tool === 'picker');
    rectFillToolBtn.classList.toggle('font-semibold', tool === 'rect_fill');
    freeDrawToolBtn.classList.toggle('font-semibold', tool === 'free_draw');
    applyPreviewTransform();
  }

  function updatePdfControlsVisibility() {
    const isPdf = isPdfInput();
    const showPdfControls = isKodawariMode && isPdf;
    pdfPageWrap.classList.toggle('hidden', !showPdfControls);
    pdfPageWrap.classList.toggle('flex', showPdfControls);
    outputFormat.disabled = !!isPdf;
    if (!isPdf) {
      pdfTotalPages = 1;
      previewPageHidden.value = '1';
      pdfPageInput.value = '1';
      pdfPageInput.max = '1';
      pdfPageTotalLabel.textContent = '/ 1';
    }
  }

  function clearKodawariStateForSimpleMode() {
    regionMode = false;
    selectingRegion = false;
    selectionStart = null;
    selectedRegion = null;
    selectionBox.classList.add('hidden');
    processRegionInput.value = '';
    outputRegionPng.checked = false;
    paintActions = [];
    paintDrawing = false;
    paintStart = null;
    paintTempRect = null;
    paintCurrentStroke = null;
    paintActionsInput.value = '[]';
    toggleRegionBtn.textContent = '範囲選択 OFF';
    toggleRegionBtn.classList.remove('bg-amber-300');
    redrawPaintOverlay();
    setActiveTool('picker');
  }

  function setKodawariMode(enabled) {
    isKodawariMode = !!enabled;
    kodawariOnlyElements.forEach((el) => {
      if (el === pdfPageWrap) return;
      el.classList.toggle('hidden', !isKodawariMode);
    });
    simpleModeBtn.classList.toggle('bg-white', !isKodawariMode);
    simpleModeBtn.classList.toggle('text-blue-700', !isKodawariMode);
    simpleModeBtn.classList.toggle('shadow-sm', !isKodawariMode);
    simpleModeBtn.classList.toggle('text-gray-600', isKodawariMode);
    kodawariModeBtn.classList.toggle('bg-white', isKodawariMode);
    kodawariModeBtn.classList.toggle('text-blue-700', isKodawariMode);
    kodawariModeBtn.classList.toggle('shadow-sm', isKodawariMode);
    kodawariModeBtn.classList.toggle('text-gray-600', !isKodawariMode);
    if (!isKodawariMode) {
      clearKodawariStateForSimpleMode();
    }
    updatePdfControlsVisibility();
    applyPreviewTransform();
    requestAnimationFrame(renderSelectionBox);
    schedulePreview();
  }

  function schedulePreview() {
    clearTimeout(debounceTimer);
    const isPdf = fileInput.files[0] && fileInput.files[0].name.toLowerCase().endsWith('.pdf');
    const delayMs = isPdf ? 700 : 350;
    debounceTimer = setTimeout(triggerPreview, delayMs);
  }

  function updateModeUI() {
    replacementWrap.classList.toggle('hidden', mode.value !== 'replace');
    schedulePreview();
  }

  function getNormalizedPosFromEvent(e) {
    const rect = previewImage.getBoundingClientRect();
    const x = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    const y = Math.min(1, Math.max(0, (e.clientY - rect.top) / rect.height));
    return { x, y, rect };
  }

  function renderSelectionBox() {
    if (!selectedRegion || !previewImage.src) {
      selectionBox.classList.add('hidden');
      return;
    }
    const imageRect = previewImage.getBoundingClientRect();
    const viewportRect = previewViewport.getBoundingClientRect();
    selectionBox.classList.remove('hidden');
    selectionBox.style.left = `${imageRect.left - viewportRect.left + imageRect.width * selectedRegion.x}px`;
    selectionBox.style.top = `${imageRect.top - viewportRect.top + imageRect.height * selectedRegion.y}px`;
    selectionBox.style.width = `${imageRect.width * selectedRegion.w}px`;
    selectionBox.style.height = `${imageRect.height * selectedRegion.h}px`;
  }

  function buildPreviewFormData() {
    const fd = new FormData();
    fd.append('file', fileInput.files[0]);
    fd.append('mode', mode.value);
    fd.append('preset', preset.value);
    fd.append('color_metric', colorMetric.value);
    fd.append('target_colors', JSON.stringify(colors));
    fd.append('tolerance', tolerance.value);
    fd.append('saturation_min', saturationMin.value);
    fd.append('lightness_min', lightnessMin.value);
    fd.append('lightness_max', lightnessMax.value);
    fd.append('min_component_area', minComponentArea.value || '0');
    fd.append('enable_preprocess', enablePreprocess.checked ? 'true' : 'false');
    fd.append('enable_postprocess', enablePostprocess.checked ? 'true' : 'false');
    fd.append('transparent_output', transparentOutput.checked ? 'true' : 'false');
    fd.append('output_region_png', outputRegionPng.checked ? 'true' : 'false');
    fd.append('preview_page', previewPageHidden.value || '1');
    fd.append('paint_actions', JSON.stringify(paintActions));
    fd.append('replacement_color', replacementColor.value);
    if (selectedRegion) {
      fd.append('process_region', JSON.stringify(selectedRegion));
    }
    return fd;
  }

  async function triggerPreview() {
    if (!fileInput.files.length || colors.length === 0) return;
    const fd = buildPreviewFormData();
    const requestSeq = ++previewRequestSeq;
    if (previewAbortController) {
      previewAbortController.abort();
    }
    previewAbortController = new AbortController();
    previewStatus.textContent = 'プレビュー更新中...';

    try {
      const response = await fetch('/tools/color_extract/preview', {
        method: 'POST',
        body: fd,
        signal: previewAbortController.signal,
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({ error: 'プレビュー更新に失敗しました' }));
        throw new Error(payload.error || 'プレビュー更新に失敗しました');
      }

      const blob = await response.blob();
      if (requestSeq !== previewRequestSeq) {
        return;
      }
      const totalPagesHeader = response.headers.get('X-PDF-Total-Pages');
      const currentPageHeader = response.headers.get('X-PDF-Page');
      if (totalPagesHeader) {
        pdfTotalPages = Math.max(1, Number(totalPagesHeader) || 1);
        const p = Math.max(1, Math.min(pdfTotalPages, Number(currentPageHeader || previewPageHidden.value || 1)));
        previewPageHidden.value = String(p);
        pdfPageInput.value = String(p);
        pdfPageInput.max = String(pdfTotalPages);
        pdfPageTotalLabel.textContent = `/ ${pdfTotalPages}`;
      }
      if (currentPreviewUrl) URL.revokeObjectURL(currentPreviewUrl);
      currentPreviewUrl = URL.createObjectURL(blob);
      previewImage.src = currentPreviewUrl;
      paintOverlay.width = previewImage.naturalWidth || paintOverlay.width;
      paintOverlay.height = previewImage.naturalHeight || paintOverlay.height;
      previewImage.classList.remove('hidden');
      previewPlaceholder.classList.add('hidden');
      applyPreviewTransform();
      requestAnimationFrame(renderSelectionBox);
      requestAnimationFrame(redrawPaintOverlay);
      previewStatus.textContent = 'プレビュー更新完了';
    } catch (err) {
      if (err && err.name === 'AbortError') {
        return;
      }
      previewStatus.textContent = `エラー: ${err.message}`;
    } finally {
      if (requestSeq === previewRequestSeq) {
        previewAbortController = null;
      }
    }
  }

  async function runAutoTune() {
    if (!fileInput.files.length || colors.length === 0) return;
    autoTuneBtn.disabled = true;
    autoTuneBtn.textContent = '自動推定中...';

    try {
      const fd = buildPreviewFormData();
      const response = await fetch('/tools/color_extract/auto_tune', { method: 'POST', body: fd });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({ error: '自動推定に失敗しました' }));
        throw new Error(payload.error || '自動推定に失敗しました');
      }
      const result = await response.json();
      tolerance.value = String(result.suggested_tolerance);
      tolValue.textContent = tolerance.value;
      if (typeof result.suggested_saturation_min === 'number') {
        saturationMin.value = String(result.suggested_saturation_min);
        satValue.textContent = saturationMin.value;
      }
      previewStatus.textContent = `自動推定: tolerance=${tolerance.value}, sat=${saturationMin.value}`;
      schedulePreview();
    } catch (err) {
      previewStatus.textContent = `エラー: ${err.message}`;
    } finally {
      autoTuneBtn.disabled = false;
      autoTuneBtn.textContent = '自動推定する';
    }
  }

  function addColor(hex) {
    const n = normalizeHex(hex);
    if (!n) return;
    if (!colors.includes(n)) {
      colors.push(n);
      renderColorList();
      schedulePreview();
    }
  }

  function applyPresetDefaults() {
    const defaults = presetDefaults[preset.value];
    if (!defaults) return;
    mode.value = defaults.mode;
    colors = [...defaults.colors];
    colorMetric.value = defaults.colorMetric;
    tolerance.value = String(defaults.tolerance);
    saturationMin.value = String(defaults.saturationMin);
    lightnessMax.value = String(defaults.lightnessMax);
    minComponentArea.value = String(defaults.minComponentArea);
    transparentOutput.checked = defaults.transparentOutput;
    if (defaults.replacementColor) {
      replacementColor.value = defaults.replacementColor;
      replacementHex.value = defaults.replacementColor.toUpperCase();
    }
    enablePreprocess.checked = true;
    enablePostprocess.checked = true;
    tolValue.textContent = tolerance.value;
    satValue.textContent = saturationMin.value;
    lightMaxValue.textContent = lightnessMax.value;
    renderColorList();
    updateModeUI();
  }

  colorList.addEventListener('input', (e) => {
    const idx = Number(e.target.dataset.idx);
    if (Number.isNaN(idx)) return;
    if (e.target.dataset.type === 'picker') {
      colors[idx] = e.target.value.toLowerCase();
      renderColorList();
      schedulePreview();
    }
    if (e.target.dataset.type === 'hex') {
      const normalized = normalizeHex(e.target.value);
      if (normalized) {
        colors[idx] = normalized;
        targetColorsInput.value = JSON.stringify(colors);
        schedulePreview();
      }
    }
  });

  colorList.addEventListener('click', (e) => {
    if (e.target.dataset.type !== 'remove') return;
    const idx = Number(e.target.dataset.idx);
    if (colors.length === 1) return;
    colors.splice(idx, 1);
    renderColorList();
    schedulePreview();
  });

  addColorBtn.addEventListener('click', () => addColor('#000000'));

  eyedropperBtn.addEventListener('click', async () => {
    if ('EyeDropper' in window) {
      try {
        const eyeDropper = new EyeDropper();
        const result = await eyeDropper.open();
        addColor(result.sRGBHex);
      } catch (_) {
        // cancelled
      }
      return;
    }
    previewStatus.textContent = 'このブラウザはEyeDropper API未対応です。';
  });

  replacementColor.addEventListener('input', () => {
    replacementHex.value = replacementColor.value.toUpperCase();
    schedulePreview();
  });

  replacementHex.addEventListener('input', () => {
    const normalized = normalizeHex(replacementHex.value);
    if (normalized) {
      replacementColor.value = normalized;
      schedulePreview();
    }
  });

  previewImage.addEventListener('click', (e) => {
    if (!previewImage.src || selectingRegion || regionMode || activeTool !== 'picker' || isPanning) return;
    const canvas = document.createElement('canvas');
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const rect = previewImage.getBoundingClientRect();
    const scaleX = previewImage.naturalWidth / rect.width;
    const scaleY = previewImage.naturalHeight / rect.height;

    canvas.width = previewImage.naturalWidth;
    canvas.height = previewImage.naturalHeight;
    ctx.drawImage(previewImage, 0, 0);

    const x = Math.max(0, Math.floor((e.clientX - rect.left) * scaleX));
    const y = Math.max(0, Math.floor((e.clientY - rect.top) * scaleY));
    const pixel = ctx.getImageData(x, y, 1, 1).data;
    const hex = `#${pixel[0].toString(16).padStart(2, '0')}${pixel[1].toString(16).padStart(2, '0')}${pixel[2].toString(16).padStart(2, '0')}`;
    addColor(hex);
  });

  previewImage.addEventListener('mousedown', (e) => {
    if (!previewImage.src) return;
    if (regionMode) {
      selectingRegion = true;
      const pos = getNormalizedPosFromEvent(e);
      selectionStart = { x: pos.x, y: pos.y };
      selectedRegion = { x: pos.x, y: pos.y, w: 0.0001, h: 0.0001 };
      processRegionInput.value = JSON.stringify(selectedRegion);
      renderSelectionBox();
      e.preventDefault();
      return;
    }
    if (activeTool === 'rect_fill') {
      const pos = getNormalizedPosFromEvent(e);
      paintDrawing = true;
      paintStart = { x: pos.x, y: pos.y };
      paintTempRect = {
        type: 'rect_fill',
        x: pos.x,
        y: pos.y,
        w: 0.0001,
        h: 0.0001,
        color: currentPaintColorRgba(),
        size: Number(paintSize.value) || 8,
        page: currentPageForAction(),
      };
      redrawPaintOverlay();
      e.preventDefault();
      return;
    }
    if (activeTool === 'free_draw') {
      const pos = getNormalizedPosFromEvent(e);
      paintDrawing = true;
      paintCurrentStroke = {
        type: 'free_draw',
        points: [{ x: pos.x, y: pos.y }],
        color: currentPaintColorRgba(),
        size: Number(paintSize.value) || 8,
        page: currentPageForAction(),
      };
      redrawPaintOverlay();
      e.preventDefault();
      return;
    }
    if (activeTool !== 'hand' || zoomLevel <= 1) return;
    isPanning = true;
    panStartX = e.clientX - panX;
    panStartY = e.clientY - panY;
    applyPreviewTransform();
    e.preventDefault();
  });

  window.addEventListener('mousemove', (e) => {
    if (isPanning) {
      panX = e.clientX - panStartX;
      panY = e.clientY - panStartY;
      applyPreviewTransform();
      requestAnimationFrame(renderSelectionBox);
      return;
    }
    if (paintDrawing) {
      const pos = getNormalizedPosFromEvent(e);
      if (activeTool === 'rect_fill' && paintStart) {
        const minX = Math.min(paintStart.x, pos.x);
        const minY = Math.min(paintStart.y, pos.y);
        const maxX = Math.max(paintStart.x, pos.x);
        const maxY = Math.max(paintStart.y, pos.y);
        paintTempRect = {
          type: 'rect_fill',
          x: minX,
          y: minY,
          w: Math.max(0.0001, maxX - minX),
          h: Math.max(0.0001, maxY - minY),
          color: currentPaintColorRgba(),
          size: Number(paintSize.value) || 8,
          page: currentPageForAction(),
        };
      } else if (activeTool === 'free_draw' && paintCurrentStroke) {
        paintCurrentStroke.points.push({ x: pos.x, y: pos.y });
      }
      redrawPaintOverlay();
      return;
    }
    if (!selectingRegion || !selectionStart) return;
    const pos = getNormalizedPosFromEvent(e);
    const minX = Math.min(selectionStart.x, pos.x);
    const minY = Math.min(selectionStart.y, pos.y);
    const maxX = Math.max(selectionStart.x, pos.x);
    const maxY = Math.max(selectionStart.y, pos.y);
    selectedRegion = {
      x: minX,
      y: minY,
      w: Math.max(0.0001, maxX - minX),
      h: Math.max(0.0001, maxY - minY),
    };
    processRegionInput.value = JSON.stringify(selectedRegion);
    renderSelectionBox();
  });

  window.addEventListener('mouseup', () => {
    if (isPanning) {
      isPanning = false;
      applyPreviewTransform();
    }
    if (paintDrawing) {
      paintDrawing = false;
      if (paintTempRect && paintTempRect.w >= 0.001 && paintTempRect.h >= 0.001) {
        paintActions.push(paintTempRect);
      }
      if (paintCurrentStroke && paintCurrentStroke.points && paintCurrentStroke.points.length > 0) {
        paintActions.push(paintCurrentStroke);
      }
      paintTempRect = null;
      paintCurrentStroke = null;
      redrawPaintOverlay();
      schedulePreview();
    }
    if (!selectingRegion) return;
    selectingRegion = false;
    if (selectedRegion && (selectedRegion.w < 0.01 || selectedRegion.h < 0.01)) {
      selectedRegion = null;
      processRegionInput.value = '';
      selectionBox.classList.add('hidden');
    }
    schedulePreview();
  });

  toggleRegionBtn.addEventListener('click', () => {
    regionMode = !regionMode;
    toggleRegionBtn.textContent = `範囲選択 ${regionMode ? 'ON' : 'OFF'}`;
    toggleRegionBtn.classList.toggle('bg-amber-300', regionMode);
    applyPreviewTransform();
  });

  clearRegionBtn.addEventListener('click', () => {
    selectedRegion = null;
    processRegionInput.value = '';
    selectionBox.classList.add('hidden');
    schedulePreview();
  });

  previewViewport.addEventListener('wheel', (e) => {
    if (!previewImage.src) return;
    e.preventDefault();
    changeZoom(e.deltaY > 0 ? -0.1 : 0.1);
  }, { passive: false });

  tolerance.addEventListener('input', () => {
    tolValue.textContent = tolerance.value;
    schedulePreview();
  });

  saturationMin.addEventListener('input', () => {
    satValue.textContent = saturationMin.value;
    schedulePreview();
  });

  lightnessMax.addEventListener('input', () => {
    lightMaxValue.textContent = lightnessMax.value;
    schedulePreview();
  });

  [enablePreprocess, enablePostprocess, minComponentArea, transparentOutput, colorMetric].forEach((el) => {
    el.addEventListener('change', schedulePreview);
  });
  outputRegionPng.addEventListener('change', schedulePreview);

  mode.addEventListener('change', updateModeUI);
  preset.addEventListener('change', () => {
    applyPresetDefaults();
    schedulePreview();
  });

  outputFormat.addEventListener('change', schedulePreview);
  zoomInBtn.addEventListener('click', () => changeZoom(0.25));
  zoomOutBtn.addEventListener('click', () => changeZoom(-0.25));
  resetViewBtn.addEventListener('click', resetPreviewView);
  toolHandBtn.addEventListener('click', () => setActiveTool('hand'));
  toolPickerBtn.addEventListener('click', () => setActiveTool('picker'));
  rectFillToolBtn.addEventListener('click', () => setActiveTool('rect_fill'));
  freeDrawToolBtn.addEventListener('click', () => setActiveTool('free_draw'));
  undoPaintBtn.addEventListener('click', undoPaintAction);
  clearPaintBtn.addEventListener('click', () => {
    if (isPdfInput()) {
      const page = Number(previewPageHidden.value) || 1;
      paintActions = paintActions.filter((a) => Number(a.page || 1) !== page);
    } else {
      paintActions = [];
    }
    paintTempRect = null;
    paintCurrentStroke = null;
    redrawPaintOverlay();
    schedulePreview();
  });

  paintColor.addEventListener('input', () => {
    paintHex.value = currentPaintColorRgba();
  });
  paintAlpha.addEventListener('input', () => {
    paintAlphaLabel.textContent = paintAlpha.value;
    paintHex.value = currentPaintColorRgba();
  });
  paintHex.addEventListener('input', () => {
    const n = normalizeHexRgba(paintHex.value);
    if (!n) return;
    paintHex.value = n;
    paintColor.value = n.slice(0, 7);
    paintAlpha.value = String(parseInt(n.slice(7, 9), 16));
    paintAlphaLabel.textContent = paintAlpha.value;
  });
  paintEyedropperBtn.addEventListener('click', async () => {
    if (!('EyeDropper' in window)) {
      previewStatus.textContent = 'このブラウザはEyeDropper API未対応です。';
      return;
    }
    try {
      const eyeDropper = new EyeDropper();
      const result = await eyeDropper.open();
      const rgb = (result.sRGBHex || '#000000').toUpperCase();
      paintColor.value = rgb;
      paintHex.value = `${rgb}${Number(paintAlpha.value).toString(16).padStart(2, '0').toUpperCase()}`;
    } catch (_) {
      // cancelled
    }
  });

  window.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && (e.key === 'z' || e.key === 'Z')) {
      e.preventDefault();
      undoPaintAction();
    }
  });
  fileInput.addEventListener('change', () => {
    updatePdfControlsVisibility();
    schedulePreview();
  });

  function setPdfPage(nextPage) {
    const p = Math.max(1, Math.min(pdfTotalPages, Number(nextPage) || 1));
    previewPageHidden.value = String(p);
    pdfPageInput.value = String(p);
    redrawPaintOverlay();
    schedulePreview();
  }

  pdfPrevBtn.addEventListener('click', () => setPdfPage((Number(previewPageHidden.value) || 1) - 1));
  pdfNextBtn.addEventListener('click', () => setPdfPage((Number(previewPageHidden.value) || 1) + 1));
  pdfPageInput.addEventListener('change', () => setPdfPage(pdfPageInput.value));

  autoTuneBtn.addEventListener('click', runAutoTune);
  simpleModeBtn.addEventListener('click', () => setKodawariMode(false));
  kodawariModeBtn.addEventListener('click', () => setKodawariMode(true));

  form.addEventListener('submit', () => {
    targetColorsInput.value = JSON.stringify(colors);
    processRegionInput.value = selectedRegion ? JSON.stringify(selectedRegion) : '';
    previewPageHidden.value = String(Math.max(1, Number(previewPageHidden.value) || 1));
    paintActionsInput.value = JSON.stringify(paintActions);
  });

  window.addEventListener('resize', () => requestAnimationFrame(renderSelectionBox));

  renderColorList();
  replacementHex.value = replacementColor.value.toUpperCase();
  paintHex.value = currentPaintColorRgba();
  paintAlphaLabel.textContent = paintAlpha.value;
  setKodawariMode(false);
  updateModeUI();
  setActiveTool('picker');
  applyPreviewTransform();
})();
