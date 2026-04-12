(() => {
  if (!window.pdfjsLib) return;

  const editorForm = document.getElementById('editorForm');
  if (!editorForm || !document.getElementById('referenceViewerPane')) return;
  if (window.__pdfPowerEnhancedEditorLoaded) return;
  window.__pdfPowerEnhancedEditorLoaded = true;

  const isEditorWindow = new URLSearchParams(window.location.search).get('editor_window') === '1';
  if (isEditorWindow) {
    const shell = document.querySelector('.pdf-power-shell');
    const hero = document.querySelector('.pdf-power-hero');
    const root = document.getElementById('pdfPowerRoot');
    const appSidebar = document.getElementById('app-sidebar');
    const sidebarToggle = document.getElementById('sidebar-toggle');
    const appMain = document.getElementById('app-main-content');
    if (appSidebar) appSidebar.style.display = 'none';
    if (sidebarToggle) sidebarToggle.style.display = 'none';
    if (appMain) {
      appMain.style.padding = '0';
      appMain.style.maxWidth = '100%';
    }
    if (hero) hero.remove();
    if (shell) {
      shell.style.padding = '0';
      shell.style.maxWidth = '100%';
      shell.style.borderRadius = '0';
      shell.style.background = 'transparent';
    }
    if (root) {
      root.style.maxWidth = 'none';
      root.style.margin = '0';
      root.style.marginTop = '0';
      root.style.borderRadius = '0';
      root.style.padding = '10px';
      const tabsBar = root.querySelector('.pdf-power-tabs');
      if (tabsBar) tabsBar.remove();
      root.querySelectorAll('.tab-content').forEach((el) => {
        if (el.id !== 'editor') el.remove();
      });
      const editorTab = document.getElementById('editor');
      if (editorTab) {
        editorTab.classList.add('active');
        editorTab.style.display = 'block';
      }
    }
    if (typeof window.switchTab === 'function') window.switchTab('editor');
    document.title = 'PDFPower Editor';
  }

  const get = (id) => document.getElementById(id);
  const q1 = (value) => Math.round(Number(value || 0) * 10) / 10;
  const qMin1 = (value) => Math.max(1, q1(value));
  const qMin01 = (value) => Math.max(0.1, q1(value));
  const deep = (value) => JSON.parse(JSON.stringify(value));
  const genId = () => `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  const styleToCss = (style) => (style === 'dashed' ? 'dashed' : style === 'dotted' ? 'dotted' : 'solid');

  const uploadMenuShell = get('editorUploadMenuShell');
  const uploadMenuBtn = get('uploadMenuBtn');
  const uploadMenu = get('uploadMenu');
  const settingsMenuBtn = get('settingsMenuBtn');
  const settingsMenu = get('settingsMenu');
  const clearReferencePdfBtn = get('clearReferencePdfBtn');
  const editorPdfInput = get('editorPdfInput');
  const referencePdfInput = get('referencePdfInput');
  const referenceVisibilityToggle = get('referenceVisibilityToggle');
  const referenceCopyBtn = get('referenceCopyBtn');
  const openEditorWindowBtn = get('openEditorWindowBtn');
  const editorSubmitBtn = get('editorSubmitBtn');
  const editPrevPageBtn = get('editPrevPageBtn');
  const editNextPageBtn = get('editNextPageBtn');
  const referencePrevPageBtn = get('referencePrevPageBtn');
  const referenceNextPageBtn = get('referenceNextPageBtn');
  const editPageInfo = get('editPageInfo');
  const referencePageInfo = get('referencePageInfo');
  const editZoomInfo = get('editZoomInfo');
  const editZoomPreset = get('editZoomPreset');
  const editZoomOutBtn = get('editZoomOutBtn');
  const editZoomInBtn = get('editZoomInBtn');
  const referenceZoomInfo = get('referenceZoomInfo');
  const referenceZoomPreset = get('referenceZoomPreset');
  const referenceZoomOutBtn = get('referenceZoomOutBtn');
  const referenceZoomInBtn = get('referenceZoomInBtn');
  const handToolBtn = get('handToolBtn');
  const modeBadge = get('modeBadge');
  const contextMenu = get('contextMenu');
  const canvas = get('pdfCanvas');
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  const overlayLayer = get('overlayLayer');
  const viewerWrap = get('viewerWrap');
  const canvasStage = get('canvasStage');
  const dragRect = get('dragRect');
  const referenceCanvas = get('referencePdfCanvas');
  const referenceCtx = referenceCanvas.getContext('2d', { willReadFrequently: true });
  const referenceOverlayLayer = get('referenceOverlayLayer');
  const referenceViewerWrap = get('referenceViewerWrap');
  const referenceCanvasStage = get('referenceCanvasStage');
  const referenceDragRect = get('referenceDragRect');
  const editorViewers = get('editorViewers');
  const editEmptyState = get('editEmptyState');
  const editorDocumentStrip = get('editorDocumentStrip');
  const referenceEmptyState = get('referenceEmptyState');
  const referenceViewerPane = get('referenceViewerPane');
  const editViewerTitle = get('editViewerTitle');
  const referenceViewerTitle = get('referenceViewerTitle');
  const editDocChip = get('editDocChip');
  const referenceDocChip = get('referenceDocChip');
  const keepEditZoomToggle = get('keepEditZoomToggle');
  const referenceLayoutToggle = get('referenceLayoutToggle');
  const referencePaneSizeRange = get('referencePaneSizeRange');
  const referencePaneSizeValue = get('referencePaneSizeValue');
  const operationsInput = get('operationsInput');
  const undoOpBtn = get('undoOpBtn');
  const redoOpBtn = get('redoOpBtn');
  const copySelectedBtn = get('copySelectedBtn');
  const pasteSelectedBtn = get('pasteSelectedBtn');
  const layerList = get('layerList');
  const layerBringFrontBtn = get('layerBringFrontBtn');
  const layerSendBackBtn = get('layerSendBackBtn');
  const layerMoveUpBtn = get('layerMoveUpBtn');
  const layerMoveDownBtn = get('layerMoveDownBtn');
  const lineStartX = get('lineStartX');
  const lineStartY = get('lineStartY');
  const lineEndX = get('lineEndX');
  const lineEndY = get('lineEndY');
  const addLineByPointBtn = get('addLineByPointBtn');

  const propEmpty = get('propEmpty');
  const propForm = get('propForm');
  const propType = get('propType');
  const propPage = get('propPage');
  const propStrokeWidth = get('propStrokeWidth');
  const propStrokeStyle = get('propStrokeStyle');
  const propX = get('propX');
  const propY = get('propY');
  const propW = get('propW');
  const propH = get('propH');
  const propText = get('propText');
  const propFontSize = get('propFontSize');
  const propTextColor = get('propTextColor');
  const propStrokeColor = get('propStrokeColor');
  const propFillColor = get('propFillColor');
  const propX2 = get('propX2');
  const propY2 = get('propY2');
  const deleteSelectedBtn = get('deleteSelectedBtn');
  const groupSize = get('groupSize');
  const groupText = get('groupText');
  const groupFont = get('groupFont');
  const groupShapeColors = get('groupShapeColors');
  const groupLine2 = get('groupLine2');
  const groupImageIndex = get('groupImageIndex');
  const groupStrokeStyle = get('groupStrokeStyle');

  const pastePreview = document.createElement('div');
  pastePreview.className = 'paste-preview hidden';
  overlayLayer.appendChild(pastePreview);

  const freehandPreview = document.createElement('div');
  freehandPreview.className = 'freehand-preview hidden';
  overlayLayer.appendChild(freehandPreview);

  let editPdfDoc = null;
  let referencePdfDoc = null;
  let editPdfFile = null;
  let referencePdfFile = null;
  let editPageNum = 1;
  let referencePageNum = 1;
  let editScale = 1;
  let referenceScale = 1;
  let referenceZoomDirty = false;
  let editRenderTask = null;
  let referenceRenderTask = null;
  let mode = 'idle';
  let lineStart = null;
  let copySource = null;
  let editDragStart = null;
  let referenceDragStart = null;
  let freehandCurrent = null;
  let handMode = false;
  let tempHandMode = false;
  let isPanning = false;
  let panStart = null;
  let moveDrag = null;
  let resizeDrag = null;
  let suppressClick = false;
  let referenceVisible = false;
  let referenceLayout = 'side';
  let referencePaneSize = Number(referencePaneSizeRange?.value || 36);
  let keepEditZoom = false;
  let contextPoint = { x: 0, y: 0 };
  let selectedIndex = -1;
  let opClipboard = null;
  let pasteOffset = 0;

  const historyStack = [];
  const redoStack = [];
  const ops = [];
  const imageFilesByRef = new Map();
  const imageUrlByRef = new Map();
  const copyPreviewCache = new Map();
  const copyPreviewTasks = new Map();

  const editPxToPdf = (value) => q1(value / editScale);
  const editPdfToPx = (value) => value * editScale;
  const referencePxToPdf = (value) => q1(value / referenceScale);
  const referencePdfToPx = (value) => value * referenceScale;
  const hasReferenceCopyOps = () => ops.some((op) => op.type === 'copy_region_paste' && op.source_document === 'reference');
  const settingsStorageKey = 'pdf_power_editor_settings_v1';

  const loadStoredSettings = () => {
    try {
      return JSON.parse(window.localStorage.getItem(settingsStorageKey) || '{}');
    } catch (_) {
      return {};
    }
  };

  const persistSettings = () => {
    try {
      window.localStorage.setItem(settingsStorageKey, JSON.stringify({
        referenceLayout,
        referencePaneSize,
        keepEditZoom,
        savedEditScale: keepEditZoom ? editScale : null,
      }));
    } catch (_) {}
  };

  const storedSettings = loadStoredSettings();
  if (storedSettings.referenceLayout === 'bottom' || storedSettings.referenceLayout === 'side') {
    referenceLayout = storedSettings.referenceLayout;
  }
  if (Number.isFinite(Number(storedSettings.referencePaneSize))) {
    referencePaneSize = Math.max(28, Math.min(52, Number(storedSettings.referencePaneSize)));
  }
  keepEditZoom = Boolean(storedSettings.keepEditZoom);
  if (keepEditZoom && Number.isFinite(Number(storedSettings.savedEditScale))) {
    editScale = Math.max(0.35, Math.min(3, Number(storedSettings.savedEditScale)));
  }

  const updateOpsField = () => {
    operationsInput.value = JSON.stringify(ops);
  };

  const getCopyPreviewSource = (sourceDocument) => (
    sourceDocument === 'reference'
      ? { doc: referencePdfDoc, file: referencePdfFile }
      : { doc: editPdfDoc, file: editPdfFile }
  );

  const getCopyPreviewKey = (region) => {
    const sourceDocument = region.source_document === 'reference' ? 'reference' : 'edit';
    const { doc, file } = getCopyPreviewSource(sourceDocument);
    const versionKey = doc?.fingerprints?.join('_') || `${file?.name || 'none'}:${file?.size || 0}`;
    return [
      sourceDocument,
      versionKey,
      Number(region.source_page || region.page || 1),
      q1(region.source_x ?? region.x ?? 0),
      q1(region.source_y ?? region.y ?? 0),
      q1(region.source_width ?? region.w ?? 0),
      q1(region.source_height ?? region.h ?? 0),
    ].join('|');
  };

  const clearCopyPreviewCache = () => {
    copyPreviewCache.clear();
    copyPreviewTasks.clear();
  };

  const getCachedCopyPreview = (region) => copyPreviewCache.get(getCopyPreviewKey(region)) || null;

  const buildCopyPreviewDataUrl = async (region) => {
    const sourceDocument = region.source_document === 'reference' ? 'reference' : 'edit';
    const { doc } = getCopyPreviewSource(sourceDocument);
    if (!doc) return null;
    const sourcePage = Number(region.source_page || region.page || 1);
    const sourceWidth = Math.max(1, Number(region.source_width ?? region.w ?? 1));
    const sourceHeight = Math.max(1, Number(region.source_height ?? region.h ?? 1));
    const renderScale = Math.max(1, Math.min(2.5, 900 / Math.max(sourceWidth, sourceHeight)));
    const page = await doc.getPage(sourcePage);
    const viewport = page.getViewport({ scale: renderScale });
    const pageCanvas = document.createElement('canvas');
    pageCanvas.width = Math.max(1, Math.ceil(viewport.width));
    pageCanvas.height = Math.max(1, Math.ceil(viewport.height));
    const pageCtx = pageCanvas.getContext('2d', { willReadFrequently: true });
    const renderTask = page.render({ canvasContext: pageCtx, viewport });
    await renderTask.promise;

    const sourceX = Math.max(0, Number(region.source_x ?? region.x ?? 0) * renderScale);
    const sourceY = Math.max(0, Number(region.source_y ?? region.y ?? 0) * renderScale);
    const sourceW = Math.max(1, Math.min(pageCanvas.width - sourceX, Math.round(sourceWidth * renderScale)));
    const sourceH = Math.max(1, Math.min(pageCanvas.height - sourceY, Math.round(sourceHeight * renderScale)));
    const previewCanvas = document.createElement('canvas');
    previewCanvas.width = sourceW;
    previewCanvas.height = sourceH;
    const previewCtx = previewCanvas.getContext('2d');
    previewCtx.drawImage(
      pageCanvas,
      Math.floor(sourceX),
      Math.floor(sourceY),
      sourceW,
      sourceH,
      0,
      0,
      sourceW,
      sourceH,
    );
    return previewCanvas.toDataURL('image/png');
  };

  const ensureCopyPreview = async (region) => {
    if (!region) return null;
    const key = getCopyPreviewKey(region);
    if (copyPreviewCache.has(key)) return copyPreviewCache.get(key);
    if (copyPreviewTasks.has(key)) return copyPreviewTasks.get(key);
    const task = (async () => {
      try {
        const dataUrl = await buildCopyPreviewDataUrl(region);
        if (dataUrl) copyPreviewCache.set(key, dataUrl);
        return dataUrl;
      } finally {
        copyPreviewTasks.delete(key);
      }
    })();
    copyPreviewTasks.set(key, task);
    const dataUrl = await task;
    if (dataUrl) renderOps();
    return dataUrl;
  };

  const normalizeOp = (op) => {
    if (!op.id) op.id = genId();
    if (op.type === 'add_text') {
      op.page = Number(op.page || editPageNum || 1);
      op.x = q1(op.x || 0);
      op.y = q1(op.y || 0);
      op.width = qMin1(op.width || 220);
      op.height = qMin1(op.height || 90);
      op.font_size = qMin01(op.font_size || 16);
    } else if (op.type === 'add_shape') {
      op.page = Number(op.page || editPageNum || 1);
      op.stroke_width = qMin01(op.stroke_width || 2);
      op.stroke_style = op.stroke_style || 'solid';
      if (op.shape === 'line') {
        op.x1 = q1(op.x1 || 0);
        op.y1 = q1(op.y1 || 0);
        op.x2 = q1(op.x2 || 120);
        op.y2 = q1(op.y2 || 120);
      } else {
        op.x = q1(op.x || 0);
        op.y = q1(op.y || 0);
        op.width = qMin1(op.width || 220);
        op.height = qMin1(op.height || 90);
      }
    } else if (op.type === 'add_image') {
      op.page = Number(op.page || editPageNum || 1);
      op.x = q1(op.x || 0);
      op.y = q1(op.y || 0);
      op.width = qMin1(op.width || 220);
      op.height = qMin1(op.height || 120);
    } else if (op.type === 'copy_region_paste') {
      op.source_document = op.source_document === 'reference' ? 'reference' : 'edit';
      op.source_page = Number(op.source_page || editPageNum || 1);
      op.target_page = Number(op.target_page || editPageNum || 1);
      op.source_x = q1(op.source_x || 0);
      op.source_y = q1(op.source_y || 0);
      op.source_width = qMin1(op.source_width || 120);
      op.source_height = qMin1(op.source_height || 80);
      op.target_x = q1(op.target_x || 0);
      op.target_y = q1(op.target_y || 0);
      op.target_width = qMin1(op.target_width || op.source_width || 120);
      op.target_height = qMin1(op.target_height || op.source_height || 80);
    } else if (op.type === 'add_freehand') {
      op.page = Number(op.page || editPageNum || 1);
      op.points = Array.isArray(op.points) ? op.points.map((point) => ({ x: q1(point.x || 0), y: q1(point.y || 0) })) : [];
      op.stroke_color = op.stroke_color || '#1d4ed8';
      op.stroke_width = qMin01(op.stroke_width || 2);
      op.stroke_style = op.stroke_style || 'solid';
    }
    return op;
  };

  const modeLabel = () => {
    if (handMode || tempHandMode) return '手のひら';
    if (mode === 'line') return '線の終点を指定';
    if (mode === 'freehand') return '自由描画';
    if (mode === 'copy_select') return '編集PDFの範囲選択';
    if (mode === 'reference_copy_select') return '参照PDFの範囲選択';
    if (mode === 'copy_paste' && copySource) {
      return copySource.source_document === 'reference' ? '参照PDFを貼り付け位置待ち' : '編集PDFを貼り付け位置待ち';
    }
    return '待機';
  };

  const updateModeBadge = () => {
    modeBadge.textContent = `Mode: ${modeLabel()}`;
  };

  const commitHistory = () => {
    historyStack.push(deep(ops));
    if (historyStack.length > 100) historyStack.shift();
    redoStack.length = 0;
  };

  const removeImageRefIfUnused = (ref) => {
    if (!ref) return;
    const stillUsed = ops.some((op) => op.type === 'add_image' && op.image_ref === ref);
    if (stillUsed) return;
    const url = imageUrlByRef.get(ref);
    if (url) URL.revokeObjectURL(url);
    imageUrlByRef.delete(ref);
    imageFilesByRef.delete(ref);
  };

  const clearAllImageRefs = () => {
    Array.from(imageUrlByRef.values()).forEach((url) => URL.revokeObjectURL(url));
    imageUrlByRef.clear();
    imageFilesByRef.clear();
  };

  const resetTransientModes = () => {
    mode = 'idle';
    lineStart = null;
    copySource = null;
    editDragStart = null;
    referenceDragStart = null;
    freehandCurrent = null;
    isPanning = false;
    panStart = null;
    moveDrag = null;
    resizeDrag = null;
    suppressClick = false;
    dragRect.classList.add('hidden');
    referenceDragRect.classList.add('hidden');
    pastePreview.classList.add('hidden');
    freehandPreview.classList.add('hidden');
    freehandPreview.innerHTML = '';
    referenceOverlayLayer.classList.remove('reference-selecting');
    updateModeBadge();
  };

  const resetOperations = () => {
    clearAllImageRefs();
    clearCopyPreviewCache();
    ops.length = 0;
    historyStack.length = 0;
    redoStack.length = 0;
    selectedIndex = -1;
    opClipboard = null;
    pasteOffset = 0;
    updateOpsField();
    renderLayerList();
    renderOps();
    selectOp(-1);
    updateSubmitState();
  };

  const restoreOpsState = (nextOps) => {
    const oldRefs = new Set(ops.filter((op) => op.type === 'add_image').map((op) => op.image_ref));
    ops.length = 0;
    nextOps.forEach((op) => ops.push(normalizeOp(op)));
    const newRefs = new Set(ops.filter((op) => op.type === 'add_image').map((op) => op.image_ref));
    oldRefs.forEach((ref) => {
      if (!newRefs.has(ref)) removeImageRefIfUnused(ref);
    });
    selectedIndex = Math.min(selectedIndex, ops.length - 1);
    updateOpsField();
    renderOps();
    renderLayerList();
    selectOp(selectedIndex);
    updateSubmitState();
  };

  const doUndo = () => {
    if (!historyStack.length) return;
    redoStack.push(deep(ops));
    restoreOpsState(historyStack.pop());
  };

  const doRedo = () => {
    if (!redoStack.length) return;
    historyStack.push(deep(ops));
    restoreOpsState(redoStack.pop());
  };

  const getPointFromLayer = (layer, event) => {
    const rect = layer.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  };

  const captureCenterPdfPoint = (wrap, currentScale) => {
    if (!wrap || wrap.classList.contains('hidden') || wrap.offsetParent === null) return null;
    return {
      x: q1((wrap.scrollLeft + wrap.clientWidth / 2) / currentScale),
      y: q1((wrap.scrollTop + wrap.clientHeight / 2) / currentScale),
    };
  };

  const restoreCenterPdfPoint = (wrap, point, currentScale) => {
    if (!wrap || !point) return;
    wrap.scrollLeft = Math.max(0, point.x * currentScale - wrap.clientWidth / 2);
    wrap.scrollTop = Math.max(0, point.y * currentScale - wrap.clientHeight / 2);
  };

  const calculateFitScale = async (doc, pageNumber, wrap, fallbackScale = 1) => {
    if (!doc || !wrap) return fallbackScale;
    const page = await doc.getPage(pageNumber);
    const viewport = page.getViewport({ scale: 1 });
    const wrapStyle = window.getComputedStyle(wrap);
    const availableWidth = Math.max(
      160,
      wrap.clientWidth - parseFloat(wrapStyle.paddingLeft || '0') - parseFloat(wrapStyle.paddingRight || '0') - 12,
    );
    const availableHeight = Math.max(
      160,
      wrap.clientHeight - parseFloat(wrapStyle.paddingTop || '0') - parseFloat(wrapStyle.paddingBottom || '0') - 12,
    );
    const widthScale = availableWidth / viewport.width;
    const heightScale = availableHeight / viewport.height;
    return Math.max(0.35, Math.min(1, widthScale, heightScale));
  };

  const refitEditScaleIfNeeded = async () => {
    if (!editPdfDoc || keepEditZoom) return;
    editScale = await calculateFitScale(editPdfDoc, editPageNum, viewerWrap, editScale);
    persistSettings();
    await renderEditPage();
    await renderReferenceWithCurrentZoom();
  };

  const refitReferenceScaleIfNeeded = async () => {
    if (!referencePdfDoc || !referenceVisible || referenceZoomDirty) return;
    referenceScale = await calculateFitScale(referencePdfDoc, referencePageNum, referenceViewerWrap, referenceScale);
    await renderReferencePage();
  };

  const renderReferenceWithCurrentZoom = async () => {
    if (!referencePdfDoc || !referenceVisible) return;
    if (referenceZoomDirty) {
      await renderReferencePage();
      return;
    }
    await refitReferenceScaleIfNeeded();
  };

  const syncViewerHeights = () => {
    const editTop = viewerWrap?.offsetParent === null ? null : viewerWrap.getBoundingClientRect().top;
    if (editTop !== null) {
      const editHeight = Math.max(320, Math.floor(window.innerHeight - editTop - 20));
      viewerWrap.style.height = `${editHeight}px`;
      if (referenceViewerWrap && referenceViewerWrap.offsetParent !== null) {
        if (referenceLayout === 'side') {
          referenceViewerWrap.style.height = `${editHeight}px`;
        } else {
          const referenceHeight = Math.max(220, Math.floor(editHeight * (referencePaneSize / 100)));
          referenceViewerWrap.style.height = `${referenceHeight}px`;
        }
      }
      return;
    }

    if (referenceViewerWrap && referenceViewerWrap.offsetParent !== null) {
      const top = referenceViewerWrap.getBoundingClientRect().top;
      const height = Math.max(220, Math.floor(window.innerHeight - top - 20));
      referenceViewerWrap.style.height = `${height}px`;
    }
  };

  const syncEditOverlay = () => {
    canvasStage.style.width = `${canvas.width}px`;
    canvasStage.style.height = `${canvas.height}px`;
    overlayLayer.style.width = `${canvas.width}px`;
    overlayLayer.style.height = `${canvas.height}px`;
    overlayLayer.classList.toggle('hand-mode', handMode || tempHandMode);
    overlayLayer.classList.toggle('panning', isPanning);
    handToolBtn.classList.toggle('active', handMode || tempHandMode);
    updateModeBadge();
  };

  const syncReferenceOverlay = () => {
    referenceCanvasStage.style.width = `${referenceCanvas.width}px`;
    referenceCanvasStage.style.height = `${referenceCanvas.height}px`;
    referenceOverlayLayer.style.width = `${referenceCanvas.width}px`;
    referenceOverlayLayer.style.height = `${referenceCanvas.height}px`;
    referenceOverlayLayer.classList.toggle('reference-selecting', mode === 'reference_copy_select');
  };

  const updateSubmitState = () => {
    editorSubmitBtn.disabled = !editPdfFile || !ops.length;
  };

  const updateReferencePaneSizeLabel = () => {
    if (referencePaneSizeValue) {
      referencePaneSizeValue.textContent = `${referencePaneSize}%`;
    }
  };

  const applyReferenceLayout = () => {
    editorViewers.style.setProperty('--reference-pane-size', `${referencePaneSize}%`);
    editorViewers.classList.toggle('reference-layout-side', referenceLayout === 'side');
    editorViewers.classList.toggle('reference-layout-bottom', referenceLayout === 'bottom');
    if (referenceLayoutToggle) {
      referenceLayoutToggle.checked = referenceLayout === 'side';
    }
    if (keepEditZoomToggle) {
      keepEditZoomToggle.checked = keepEditZoom;
    }
    if (referencePaneSizeRange) {
      referencePaneSizeRange.value = String(referencePaneSize);
    }
    updateReferencePaneSizeLabel();
    persistSettings();
    syncViewerHeights();
  };

  const updateReferenceVisibility = () => {
    const hasReferencePdf = Boolean(referencePdfDoc || referencePdfFile);
    const canShowReference = Boolean(referenceVisible && hasReferencePdf);
    referenceViewerPane.classList.toggle('hidden', !canShowReference);
    editorViewers.classList.toggle('reference-visible', canShowReference);
    if (referenceVisibilityToggle) {
      referenceVisibilityToggle.disabled = !referencePdfDoc;
      referenceVisibilityToggle.checked = canShowReference;
    }
    referenceCopyBtn.disabled = !referencePdfDoc || !editPdfDoc;
    referencePrevPageBtn.disabled = !referencePdfDoc || referencePageNum <= 1;
    referenceNextPageBtn.disabled = !referencePdfDoc || (referencePdfDoc ? referencePageNum >= referencePdfDoc.numPages : true);
    referenceZoomOutBtn.disabled = !referencePdfDoc;
    referenceZoomInBtn.disabled = !referencePdfDoc;
    referenceZoomPreset.disabled = !referencePdfDoc;
    referenceDocChip.classList.toggle('hidden', !referencePdfFile);
    syncViewerHeights();
  };

  const updateDocumentChips = () => {
    editDocChip.textContent = editPdfFile ? `編集PDF: ${editPdfFile.name}` : '編集PDF: 未アップロード';
    referenceDocChip.textContent = referencePdfFile ? `参照PDF: ${referencePdfFile.name}` : '参照PDF: 未アップロード';
    editViewerTitle.textContent = editPdfFile ? editPdfFile.name : '編集PDFをアップロードしてください';
    referenceViewerTitle.textContent = referencePdfFile ? referencePdfFile.name : '参照PDFは未アップロードです';
    if (typeof window.setPdfPowerHeroDetail === 'function') {
      const files = [editPdfFile, referencePdfFile].filter(Boolean);
      const totalSize = files.reduce((sum, file) => sum + Number(file.size || 0), 0);
      const note = files.length
        ? '編集PDFと参照PDFの合計容量です。'
        : '編集PDFと参照PDFの合計容量をここに表示します。';
      window.setPdfPowerHeroDetail('editor', { count: files.length, size: totalSize, note });
    }
  };

  const updateUploadAffordances = () => {
    const hasAnyPdf = Boolean(editPdfFile || referencePdfFile);
    editorDocumentStrip?.classList.toggle('hidden', !hasAnyPdf);
    if (!hasAnyPdf) hideUploadMenu();
  };

  const updateViewerVisibility = () => {
    if (editPdfDoc) {
      editEmptyState.classList.add('hidden');
      canvasStage.classList.remove('hidden');
      viewerWrap.classList.remove('viewer-empty');
    } else {
      editEmptyState.classList.remove('hidden');
      canvasStage.classList.add('hidden');
      viewerWrap.classList.add('viewer-empty');
    }
    editPrevPageBtn.disabled = !editPdfDoc || editPageNum <= 1;
    editNextPageBtn.disabled = !editPdfDoc || (editPdfDoc ? editPageNum >= editPdfDoc.numPages : true);
    editZoomOutBtn.disabled = !editPdfDoc;
    editZoomInBtn.disabled = !editPdfDoc;
    editZoomPreset.disabled = !editPdfDoc;

    if (referencePdfDoc) {
      referenceEmptyState.classList.add('hidden');
      referenceCanvasStage.classList.remove('hidden');
      referenceViewerWrap.classList.remove('viewer-empty');
    } else {
      referenceEmptyState.classList.remove('hidden');
      referenceCanvasStage.classList.add('hidden');
      referenceViewerWrap.classList.add('viewer-empty');
    }

    updateDocumentChips();
    updateUploadAffordances();
    updateReferenceVisibility();
    updateSubmitState();
  };

  const hideMenu = () => contextMenu.classList.add('hidden');
  const showMenu = (x, y) => {
    contextMenu.style.left = `${x}px`;
    contextMenu.style.top = `${y}px`;
    contextMenu.classList.remove('hidden');
  };

  const hideUploadMenu = () => uploadMenu.classList.add('hidden');
  const toggleUploadMenu = () => uploadMenu.classList.toggle('hidden');
  const hideSettingsMenu = () => settingsMenu.classList.add('hidden');
  const toggleSettingsMenu = () => settingsMenu.classList.toggle('hidden');

  const updatePastePreview = (px, py) => {
    if (!copySource || mode !== 'copy_paste') {
      pastePreview.classList.add('hidden');
      pastePreview.style.background = 'rgba(14, 165, 233, 0.15)';
      return;
    }
    const previewDataUrl = getCachedCopyPreview(copySource);
    if (!previewDataUrl) void ensureCopyPreview(copySource);
    pastePreview.style.left = `${px}px`;
    pastePreview.style.top = `${py}px`;
    pastePreview.style.width = `${editPdfToPx(copySource.w)}px`;
    pastePreview.style.height = `${editPdfToPx(copySource.h)}px`;
    pastePreview.style.background = previewDataUrl
      ? `rgba(255,255,255,0.82) url("${previewDataUrl}") center / 100% 100% no-repeat`
      : 'rgba(14, 165, 233, 0.15)';
    pastePreview.classList.remove('hidden');
  };

  const updateFreehandPreview = () => {
    if (mode !== 'freehand' || !freehandCurrent || freehandCurrent.length < 2) {
      freehandPreview.classList.add('hidden');
      freehandPreview.innerHTML = '';
      return;
    }
    const xs = freehandCurrent.map((point) => editPdfToPx(point.x));
    const ys = freehandCurrent.map((point) => editPdfToPx(point.y));
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    const width = Math.max(2, maxX - minX);
    const height = Math.max(2, maxY - minY);
    freehandPreview.style.left = `${minX}px`;
    freehandPreview.style.top = `${minY}px`;
    freehandPreview.style.width = `${width}px`;
    freehandPreview.style.height = `${height}px`;
    freehandPreview.innerHTML = `<svg width="${width}" height="${height}" style="overflow:visible"><polyline fill="none" stroke="#1d4ed8" stroke-width="${Math.max(1, editPdfToPx(2))}" points="${freehandCurrent.map((point) => `${editPdfToPx(point.x) - minX},${editPdfToPx(point.y) - minY}`).join(' ')}" /></svg>`;
    freehandPreview.classList.remove('hidden');
  };

  const renderLayerList = () => {
    layerList.innerHTML = '';
    if (!ops.length) {
      layerList.innerHTML = '<div class="text-xs text-gray-500">レイヤーはまだありません。</div>';
      return;
    }
    for (let index = ops.length - 1; index >= 0; index -= 1) {
      const op = ops[index];
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'layer-item';
      if (index === selectedIndex) row.classList.add('active');
      const page = op.page || op.target_page || '-';
      const typeLabel = op.type === 'copy_region_paste' && op.source_document === 'reference'
        ? 'reference_copy'
        : op.type === 'add_freehand'
          ? 'freehand'
          : op.type;
      row.innerHTML = `<span>${index + 1}. ${typeLabel}</span><span class="text-[10px] text-slate-500">p.${page}</span>`;
      row.dataset.idx = String(index);
      layerList.appendChild(row);
    }
  };

  const renderOps = () => {
    overlayLayer.querySelectorAll('.overlay-item').forEach((el) => el.remove());
    ops.forEach((op, index) => {
      const item = document.createElement('div');
      item.className = 'overlay-item';
      item.dataset.idx = String(index);
      if (index === selectedIndex) item.classList.add('selected');

      if (op.type === 'add_text' && op.page === editPageNum) {
        item.classList.add('text');
        item.style.left = `${editPdfToPx(op.x)}px`;
        item.style.top = `${editPdfToPx(op.y)}px`;
        item.style.width = `${editPdfToPx(op.width)}px`;
        item.style.height = `${editPdfToPx(op.height)}px`;
        item.style.color = op.color || '#0f172a';
        item.style.fontSize = `${Math.max(8, editPdfToPx(op.font_size || 14))}px`;
        item.textContent = op.text || '';
      } else if (op.type === 'add_shape' && op.page === editPageNum) {
        const cssStyle = styleToCss(op.stroke_style || 'solid');
        if (op.shape === 'line') {
          const x1 = editPdfToPx(op.x1);
          const y1 = editPdfToPx(op.y1);
          const x2 = editPdfToPx(op.x2);
          const y2 = editPdfToPx(op.y2);
          const length = Math.hypot(x2 - x1, y2 - y1);
          const angle = Math.atan2(y2 - y1, x2 - x1) * (180 / Math.PI);
          item.classList.add('line');
          item.style.left = `${x1}px`;
          item.style.top = `${y1}px`;
          item.style.width = `${length}px`;
          item.style.borderTopColor = op.stroke_color || '#1d4ed8';
          item.style.borderTopStyle = cssStyle;
          item.style.borderTopWidth = `${Math.max(1, editPdfToPx(op.stroke_width || 2))}px`;
          item.style.transform = `rotate(${angle}deg)`;
        } else {
          item.style.left = `${editPdfToPx(op.x)}px`;
          item.style.top = `${editPdfToPx(op.y)}px`;
          item.style.width = `${editPdfToPx(op.width)}px`;
          item.style.height = `${editPdfToPx(op.height)}px`;
          item.style.borderColor = op.stroke_color || '#1d4ed8';
          item.style.borderStyle = cssStyle;
          item.style.borderWidth = `${Math.max(1, editPdfToPx(op.stroke_width || 2))}px`;
          item.style.background = op.fill_color || 'transparent';
          if (op.shape === 'ellipse') item.style.borderRadius = '999px';
        }
      } else if (op.type === 'add_image' && op.page === editPageNum) {
        item.style.left = `${editPdfToPx(op.x)}px`;
        item.style.top = `${editPdfToPx(op.y)}px`;
        item.style.width = `${editPdfToPx(op.width)}px`;
        item.style.height = `${editPdfToPx(op.height)}px`;
        const url = imageUrlByRef.get(op.image_ref);
        item.style.background = url ? `url("${url}") center / cover no-repeat` : 'rgba(37,99,235,0.08)';
        item.style.borderColor = '#2563eb';
      } else if (op.type === 'copy_region_paste' && op.target_page === editPageNum) {
        item.style.left = `${editPdfToPx(op.target_x)}px`;
        item.style.top = `${editPdfToPx(op.target_y)}px`;
        item.style.width = `${editPdfToPx(op.target_width)}px`;
        item.style.height = `${editPdfToPx(op.target_height)}px`;
        item.style.borderColor = op.source_document === 'reference' ? '#0f766e' : '#0ea5e9';
        const previewDataUrl = getCachedCopyPreview(op);
        if (!previewDataUrl) void ensureCopyPreview(op);
        item.style.background = previewDataUrl
          ? `rgba(255,255,255,0.88) url("${previewDataUrl}") center / 100% 100% no-repeat`
          : (op.source_document === 'reference' ? 'rgba(15,118,110,0.12)' : 'rgba(14,165,233,0.1)');
      } else if (op.type === 'add_freehand' && op.page === editPageNum && Array.isArray(op.points) && op.points.length >= 2) {
        const xs = op.points.map((point) => editPdfToPx(point.x));
        const ys = op.points.map((point) => editPdfToPx(point.y));
        const minX = Math.min(...xs);
        const maxX = Math.max(...xs);
        const minY = Math.min(...ys);
        const maxY = Math.max(...ys);
        const width = Math.max(2, maxX - minX);
        const height = Math.max(2, maxY - minY);
        item.classList.add('freehand');
        item.style.left = `${minX}px`;
        item.style.top = `${minY}px`;
        item.style.width = `${width}px`;
        item.style.height = `${height}px`;
        const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
        svg.setAttribute('width', String(width));
        svg.setAttribute('height', String(height));
        svg.style.overflow = 'visible';
        const polyline = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
        polyline.setAttribute('fill', 'none');
        polyline.setAttribute('stroke', op.stroke_color || '#1d4ed8');
        polyline.setAttribute('stroke-width', String(Math.max(1, editPdfToPx(op.stroke_width || 2))));
        const dash = op.stroke_style === 'dashed' ? '8 4' : op.stroke_style === 'dotted' ? '2 4' : '';
        if (dash) polyline.setAttribute('stroke-dasharray', dash);
        polyline.setAttribute('points', op.points.map((point) => `${editPdfToPx(point.x) - minX},${editPdfToPx(point.y) - minY}`).join(' '));
        svg.appendChild(polyline);
        item.appendChild(svg);
      } else {
        return;
      }

      if (index === selectedIndex && op.type !== 'add_freehand' && (op.type !== 'add_shape' || op.shape !== 'line')) {
        ['nw', 'ne', 'sw', 'se'].forEach((dir) => {
          const handle = document.createElement('span');
          handle.className = `resize-handle ${dir}`;
          handle.dataset.dir = dir;
          item.appendChild(handle);
        });
      }
      overlayLayer.appendChild(item);
    });
  };

  const selectOp = (index) => {
    selectedIndex = index;
    renderOps();
    renderLayerList();
    const op = ops[index];
    if (!op) {
      propEmpty.classList.remove('hidden');
      propForm.classList.add('hidden');
      return;
    }

    propEmpty.classList.add('hidden');
    propForm.classList.remove('hidden');
    groupSize.classList.remove('hidden');
    groupText.classList.add('hidden');
    groupFont.classList.add('hidden');
    groupShapeColors.classList.add('hidden');
    groupLine2.classList.add('hidden');
    groupImageIndex.classList.add('hidden');
    groupStrokeStyle.classList.add('hidden');
    propType.value = op.type === 'copy_region_paste' && op.source_document === 'reference' ? 'copy_region_paste(reference)' : op.type;
    propPage.value = op.page || op.target_page || 1;
    propStrokeWidth.value = op.stroke_width || 2;
    propStrokeStyle.value = op.stroke_style || 'solid';
    propX.value = op.x ?? op.target_x ?? op.x1 ?? 0;
    propY.value = op.y ?? op.target_y ?? op.y1 ?? 0;
    propW.value = op.width ?? op.target_width ?? 100;
    propH.value = op.height ?? op.target_height ?? 50;

    if (op.type === 'add_text') {
      groupText.classList.remove('hidden');
      groupFont.classList.remove('hidden');
      propText.value = op.text || '';
      propFontSize.value = op.font_size || 14;
      propTextColor.value = op.color || '#0f172a';
    }

    if (op.type === 'add_shape') {
      groupShapeColors.classList.remove('hidden');
      groupStrokeStyle.classList.remove('hidden');
      propStrokeColor.value = op.stroke_color || '#1d4ed8';
      propFillColor.value = op.fill_color && op.fill_color.startsWith('#') ? op.fill_color : '#93c5fd';
      if (op.shape === 'line') {
        groupLine2.classList.remove('hidden');
        groupSize.classList.add('hidden');
        propX.value = op.x1;
        propY.value = op.y1;
        propX2.value = op.x2;
        propY2.value = op.y2;
      }
    }

    if (op.type === 'add_freehand') {
      groupSize.classList.add('hidden');
      groupShapeColors.classList.remove('hidden');
      groupStrokeStyle.classList.remove('hidden');
      propStrokeColor.value = op.stroke_color || '#1d4ed8';
      propFillColor.value = '#ffffff';
      propStrokeWidth.value = op.stroke_width || 2;
      propStrokeStyle.value = op.stroke_style || 'solid';
      propX.value = 0;
      propY.value = 0;
      propW.value = 0;
      propH.value = 0;
    }
  };

  const applyPropChanges = () => {
    const op = ops[selectedIndex];
    if (!op) return;
    const value = (input) => q1(input.value || 0);
    if (op.type === 'add_text') {
      op.page = value(propPage);
      op.x = value(propX);
      op.y = value(propY);
      op.width = qMin1(value(propW));
      op.height = qMin1(value(propH));
      op.text = propText.value || '';
      op.font_size = qMin01(value(propFontSize) || 14);
      op.color = propTextColor.value;
    } else if (op.type === 'add_shape') {
      op.page = value(propPage);
      op.stroke_style = propStrokeStyle.value;
      if (op.shape === 'line') {
        op.x1 = value(propX);
        op.y1 = value(propY);
        op.x2 = value(propX2);
        op.y2 = value(propY2);
      } else {
        op.x = value(propX);
        op.y = value(propY);
        op.width = qMin1(value(propW));
        op.height = qMin1(value(propH));
      }
      op.stroke_color = propStrokeColor.value;
      op.fill_color = propFillColor.value;
      op.stroke_width = qMin01(value(propStrokeWidth) || 2);
    } else if (op.type === 'add_image') {
      op.page = value(propPage);
      op.x = value(propX);
      op.y = value(propY);
      op.width = qMin1(value(propW));
      op.height = qMin1(value(propH));
    } else if (op.type === 'copy_region_paste') {
      op.target_page = value(propPage);
      op.target_x = value(propX);
      op.target_y = value(propY);
      op.target_width = qMin1(value(propW));
      op.target_height = qMin1(value(propH));
    } else if (op.type === 'add_freehand') {
      op.page = value(propPage);
      op.stroke_color = propStrokeColor.value;
      op.stroke_width = qMin01(value(propStrokeWidth) || 2);
      op.stroke_style = propStrokeStyle.value;
    }
    normalizeOp(op);
    updateOpsField();
    renderOps();
    renderLayerList();
  };

  const renderEditPage = async (anchor = null) => {
    if (!editPdfDoc) return;
    const page = await editPdfDoc.getPage(editPageNum);
    const viewport = page.getViewport({ scale: editScale });
    canvas.width = Math.ceil(viewport.width);
    canvas.height = Math.ceil(viewport.height);
    canvas.style.width = `${viewport.width}px`;
    canvas.style.height = `${viewport.height}px`;
    if (editRenderTask) {
      try { editRenderTask.cancel(); } catch (_) {}
    }
    editRenderTask = page.render({ canvasContext: ctx, viewport });
    await editRenderTask.promise;
    editRenderTask = null;
    editPageInfo.textContent = `${editPageNum} / ${editPdfDoc.numPages}`;
    editPrevPageBtn.disabled = editPageNum <= 1;
    editNextPageBtn.disabled = editPageNum >= editPdfDoc.numPages;
    editZoomInfo.textContent = `${Math.round(editScale * 100)}%`;
    if (editZoomPreset) editZoomPreset.value = String(editScale);
    syncEditOverlay();
    renderOps();
    restoreCenterPdfPoint(viewerWrap, anchor, editScale);
  };

  const renderReferencePage = async (anchor = null) => {
    if (!referencePdfDoc || !referenceVisible) return;
    const page = await referencePdfDoc.getPage(referencePageNum);
    const viewport = page.getViewport({ scale: referenceScale });
    referenceCanvas.width = Math.ceil(viewport.width);
    referenceCanvas.height = Math.ceil(viewport.height);
    referenceCanvas.style.width = `${viewport.width}px`;
    referenceCanvas.style.height = `${viewport.height}px`;
    if (referenceRenderTask) {
      try { referenceRenderTask.cancel(); } catch (_) {}
    }
    referenceRenderTask = page.render({ canvasContext: referenceCtx, viewport });
    await referenceRenderTask.promise;
    referenceRenderTask = null;
    referencePageInfo.textContent = `${referencePageNum} / ${referencePdfDoc.numPages}`;
    referencePrevPageBtn.disabled = referencePageNum <= 1;
    referenceNextPageBtn.disabled = referencePageNum >= referencePdfDoc.numPages;
    referenceZoomInfo.textContent = `${Math.round(referenceScale * 100)}%`;
    if (referenceZoomPreset) referenceZoomPreset.value = String(referenceScale);
    syncReferenceOverlay();
    restoreCenterPdfPoint(referenceViewerWrap, anchor, referenceScale);
  };

  const setEditZoom = async (nextScale) => {
    const editAnchor = captureCenterPdfPoint(viewerWrap, editScale);
    editScale = Math.max(0.35, Math.min(3.0, nextScale));
    persistSettings();
    await renderEditPage(editAnchor);
  };

  const setReferenceZoom = async (nextScale) => {
    if (!referencePdfDoc || !referenceVisible) return;
    const referenceAnchor = captureCenterPdfPoint(referenceViewerWrap, referenceScale);
    referenceScale = Math.max(0.35, Math.min(3.0, nextScale));
    referenceZoomDirty = true;
    await renderReferencePage(referenceAnchor);
  };

  const moveLayer = (direction) => {
    if (selectedIndex < 0) return;
    const target = direction === 'front'
      ? ops.length - 1
      : direction === 'back'
        ? 0
        : direction === 'up'
          ? Math.min(ops.length - 1, selectedIndex + 1)
          : Math.max(0, selectedIndex - 1);
    if (target === selectedIndex) return;
    commitHistory();
    const [item] = ops.splice(selectedIndex, 1);
    ops.splice(target, 0, item);
    selectedIndex = target;
    updateOpsField();
    renderOps();
    renderLayerList();
    selectOp(selectedIndex);
  };

  const pickImageFile = () => new Promise((resolve) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/*';
    input.addEventListener('change', () => resolve(input.files && input.files[0] ? input.files[0] : null), { once: true });
    input.click();
  });

  const addImageAt = async (x, y) => {
    if (!editPdfDoc) return;
    const file = await pickImageFile();
    if (!file) return;
    const ref = `img_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    imageFilesByRef.set(ref, file);
    imageUrlByRef.set(ref, URL.createObjectURL(file));
    commitHistory();
    ops.push(normalizeOp({ type: 'add_image', page: editPageNum, x, y, width: 220, height: 120, image_ref: ref }));
    updateOpsField();
    renderOps();
    renderLayerList();
    selectOp(ops.length - 1);
    updateSubmitState();
  };

  const cloneSelectedOp = () => {
    if (selectedIndex < 0 || !ops[selectedIndex]) return;
    opClipboard = deep(ops[selectedIndex]);
  };

  const pasteClonedOp = () => {
    if (!opClipboard || !editPdfDoc) return;
    commitHistory();
    pasteOffset += 14;
    const cloned = normalizeOp(deep(opClipboard));
    if ('x' in cloned) cloned.x += editPxToPdf(pasteOffset);
    if ('y' in cloned) cloned.y += editPxToPdf(pasteOffset);
    if ('x1' in cloned) {
      cloned.x1 += editPxToPdf(pasteOffset);
      cloned.y1 += editPxToPdf(pasteOffset);
      cloned.x2 += editPxToPdf(pasteOffset);
      cloned.y2 += editPxToPdf(pasteOffset);
    }
    if ('target_x' in cloned) cloned.target_x += editPxToPdf(pasteOffset);
    if ('target_y' in cloned) cloned.target_y += editPxToPdf(pasteOffset);
    ops.push(cloned);
    updateOpsField();
    renderOps();
    renderLayerList();
    selectOp(ops.length - 1);
    updateSubmitState();
  };

  const addByAction = (action) => {
    hideMenu();
    if (!editPdfDoc) return;
    const x = editPxToPdf(contextPoint.x);
    const y = editPxToPdf(contextPoint.y);
    if (action === 'copy_selected') return cloneSelectedOp();
    if (action === 'paste_selected') return pasteClonedOp();
    commitHistory();
    if (action === 'add_text') {
      ops.push(normalizeOp({ type: 'add_text', page: editPageNum, x, y, width: 220, height: 90, text: 'テキスト', font_size: 16, color: '#0f172a' }));
    } else if (action === 'add_rect') {
      ops.push(normalizeOp({ type: 'add_shape', page: editPageNum, shape: 'rect', x, y, width: 220, height: 90, stroke_color: '#1d4ed8', fill_color: '#93c5fd', stroke_width: 2, stroke_style: 'solid' }));
    } else if (action === 'add_ellipse') {
      ops.push(normalizeOp({ type: 'add_shape', page: editPageNum, shape: 'ellipse', x, y, width: 220, height: 90, stroke_color: '#1d4ed8', fill_color: '#93c5fd', stroke_width: 2, stroke_style: 'solid' }));
    } else if (action === 'add_image') {
      addImageAt(x, y);
      return;
    } else if (action === 'add_line') {
      mode = 'line';
      lineStart = { x, y };
      updateModeBadge();
      return;
    } else if (action === 'start_freehand') {
      mode = 'freehand';
      freehandCurrent = null;
      updateModeBadge();
      return;
    } else if (action === 'start_copy') {
      mode = 'copy_select';
      copySource = null;
      updateModeBadge();
      return;
    }
    updateOpsField();
    renderOps();
    renderLayerList();
    selectOp(ops.length - 1);
    updateSubmitState();
  };

  const requestUpload = (target) => {
    if (target === 'reference') referencePdfInput.click();
    else editorPdfInput.click();
  };

  const removeReferencePdf = () => {
    if (!referencePdfFile) return;
    if (hasReferenceCopyOps()) {
      const shouldRemove = window.confirm('参照PDFを外すと、参照PDFから作成した範囲コピーも削除されます。続行しますか。');
      if (!shouldRemove) return;
      commitHistory();
      const nextOps = ops.filter((op) => !(op.type === 'copy_region_paste' && op.source_document === 'reference'));
      restoreOpsState(nextOps);
    }
    if (referencePdfDoc && typeof referencePdfDoc.destroy === 'function') {
      try { referencePdfDoc.destroy(); } catch (_) {}
    }
    referencePdfDoc = null;
    referencePdfFile = null;
    referencePageNum = 1;
    referenceVisible = false;
    clearCopyPreviewCache();
    if (copySource && copySource.source_document === 'reference') copySource = null;
    resetTransientModes();
    updateViewerVisibility();
    updateDocumentChips();
  };

  const loadPdfFile = async (file, target) => {
    const data = await file.arrayBuffer();
    const doc = await pdfjsLib.getDocument({ data }).promise;
    clearCopyPreviewCache();
    if (target === 'reference') {
      if (referencePdfDoc && typeof referencePdfDoc.destroy === 'function') {
        try { await referencePdfDoc.destroy(); } catch (_) {}
      }
      referencePdfDoc = doc;
      referencePdfFile = file;
      referencePageNum = 1;
      referenceZoomDirty = false;
      updateViewerVisibility();
      if (referenceVisible) {
        referenceScale = await calculateFitScale(referencePdfDoc, referencePageNum, referenceViewerWrap, referenceScale);
        await renderReferencePage();
        referenceViewerWrap.scrollTop = 0;
      }
    } else {
      if (editPdfDoc && typeof editPdfDoc.destroy === 'function') {
        try { await editPdfDoc.destroy(); } catch (_) {}
      }
      editPdfDoc = doc;
      editPdfFile = file;
      editPageNum = 1;
      resetTransientModes();
      updateViewerVisibility();
      if (keepEditZoom) {
        editScale = Math.max(0.35, Math.min(3, editScale));
      } else {
        editScale = await calculateFitScale(editPdfDoc, editPageNum, viewerWrap, editScale);
      }
      persistSettings();
      await renderEditPage();
      viewerWrap.scrollTop = 0;
    }
  };

  [
    propPage,
    propStrokeWidth,
    propStrokeStyle,
    propX,
    propY,
    propW,
    propH,
    propText,
    propFontSize,
    propTextColor,
    propStrokeColor,
    propFillColor,
    propX2,
    propY2,
  ].forEach((input) => input.addEventListener('input', applyPropChanges));

  document.querySelectorAll('[data-upload-target]').forEach((button) => {
    button.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      hideUploadMenu();
      hideSettingsMenu();
      requestUpload(button.dataset.uploadTarget);
    });
  });

  uploadMenuBtn.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    hideSettingsMenu();
    toggleUploadMenu();
  });
  uploadMenu.addEventListener('click', (event) => event.stopPropagation());
  settingsMenuBtn.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    hideUploadMenu();
    toggleSettingsMenu();
  });
  settingsMenu.addEventListener('click', (event) => event.stopPropagation());
  clearReferencePdfBtn.addEventListener('click', (event) => {
    event.preventDefault();
    removeReferencePdf();
    hideUploadMenu();
  });
  referenceVisibilityToggle.addEventListener('change', async () => {
    if (!referencePdfDoc) {
      referenceVisibilityToggle.checked = false;
      return;
    }
    referenceVisible = referenceVisibilityToggle.checked;
    updateViewerVisibility();
    if (!keepEditZoom) {
      await refitEditScaleIfNeeded();
    } else if (referenceVisible) {
      await renderReferenceWithCurrentZoom();
    }
  });
  referenceLayoutToggle.addEventListener('change', async () => {
    referenceLayout = referenceLayoutToggle.checked ? 'side' : 'bottom';
    applyReferenceLayout();
    if (referenceVisible) {
      if (!keepEditZoom) await refitEditScaleIfNeeded();
      else await renderReferenceWithCurrentZoom();
    }
  });
  keepEditZoomToggle.addEventListener('change', () => {
    keepEditZoom = keepEditZoomToggle.checked;
    persistSettings();
  });
  referencePaneSizeRange.addEventListener('input', () => {
    referencePaneSize = Number(referencePaneSizeRange.value || referencePaneSize);
    applyReferenceLayout();
  });

  contextMenu.addEventListener('click', (event) => {
    const button = event.target.closest('button[data-action]');
    if (button) addByAction(button.dataset.action);
  });
  document.querySelectorAll('.editor-toolbar [data-action]').forEach((button) => {
    button.addEventListener('click', () => addByAction(button.dataset.action));
  });

  handToolBtn.addEventListener('click', () => {
    if (!editPdfDoc) return;
    handMode = !handMode;
    syncEditOverlay();
  });
  editZoomOutBtn.addEventListener('click', async () => setEditZoom(editScale - 0.1));
  editZoomInBtn.addEventListener('click', async () => setEditZoom(editScale + 0.1));
  editZoomPreset.addEventListener('change', async () => setEditZoom(Number(editZoomPreset.value || editScale)));
  referenceZoomOutBtn.addEventListener('click', async () => setReferenceZoom(referenceScale - 0.1));
  referenceZoomInBtn.addEventListener('click', async () => setReferenceZoom(referenceScale + 0.1));
  referenceZoomPreset.addEventListener('change', async () => setReferenceZoom(Number(referenceZoomPreset.value || referenceScale)));
  copySelectedBtn?.addEventListener('click', cloneSelectedOp);
  pasteSelectedBtn?.addEventListener('click', pasteClonedOp);
  undoOpBtn.addEventListener('click', doUndo);
  redoOpBtn.addEventListener('click', doRedo);

  layerBringFrontBtn.addEventListener('click', () => moveLayer('front'));
  layerSendBackBtn.addEventListener('click', () => moveLayer('back'));
  layerMoveUpBtn.addEventListener('click', () => moveLayer('up'));
  layerMoveDownBtn.addEventListener('click', () => moveLayer('down'));
  layerList.addEventListener('click', (event) => {
    const button = event.target.closest('.layer-item');
    if (button) selectOp(Number(button.dataset.idx));
  });

  addLineByPointBtn.addEventListener('click', () => {
    if (!editPdfDoc) return;
    commitHistory();
    ops.push(normalizeOp({
      type: 'add_shape',
      page: editPageNum,
      shape: 'line',
      x1: Number(lineStartX.value || 0),
      y1: Number(lineStartY.value || 0),
      x2: Number(lineEndX.value || 0),
      y2: Number(lineEndY.value || 0),
      stroke_color: '#1d4ed8',
      stroke_width: 2,
      stroke_style: 'solid',
    }));
    updateOpsField();
    renderOps();
    renderLayerList();
    selectOp(ops.length - 1);
    updateSubmitState();
  });

  deleteSelectedBtn.addEventListener('click', () => {
    if (selectedIndex < 0) return;
    commitHistory();
    const [removed] = ops.splice(selectedIndex, 1);
    selectedIndex = -1;
    if (removed?.type === 'add_image') removeImageRefIfUnused(removed.image_ref);
    updateOpsField();
    renderOps();
    renderLayerList();
    selectOp(-1);
    updateSubmitState();
  });

  editPrevPageBtn.addEventListener('click', async () => {
    if (!editPdfDoc || editPageNum <= 1) return;
    editPageNum -= 1;
    await renderEditPage();
  });
  editNextPageBtn.addEventListener('click', async () => {
    if (!editPdfDoc || editPageNum >= editPdfDoc.numPages) return;
    editPageNum += 1;
    await renderEditPage();
  });
  referencePrevPageBtn.addEventListener('click', async () => {
    if (!referencePdfDoc || referencePageNum <= 1) return;
    referencePageNum -= 1;
    await renderReferencePage();
  });
  referenceNextPageBtn.addEventListener('click', async () => {
    if (!referencePdfDoc || referencePageNum >= referencePdfDoc.numPages) return;
    referencePageNum += 1;
    await renderReferencePage();
  });

  referenceCopyBtn.addEventListener('click', async () => {
    if (!referencePdfDoc) return;
    referenceVisible = true;
    mode = 'reference_copy_select';
    copySource = null;
    updateViewerVisibility();
    if (!keepEditZoom) await refitEditScaleIfNeeded();
    else await renderReferenceWithCurrentZoom();
    updateModeBadge();
  });

  openEditorWindowBtn.addEventListener('click', () => {
    const width = Math.max(1200, Math.floor(window.screen.availWidth * 0.96));
    const height = Math.max(800, Math.floor(window.screen.availHeight * 0.94));
    window.open('/tools/pdf_power?editor_window=1', 'powerpdf_editor', `width=${width},height=${height},left=0,top=0,resizable=yes,scrollbars=yes`);
  });

  editorPdfInput.addEventListener('change', async (event) => {
    const file = event.target.files && event.target.files[0];
    event.target.value = '';
    if (!file) return;
    if (editPdfFile && ops.length) {
      const shouldReplace = window.confirm('編集PDFを差し替えると現在の編集内容はリセットされます。続行しますか。');
      if (!shouldReplace) return;
      resetOperations();
    }
    try {
      await loadPdfFile(file, 'edit');
    } catch (error) {
      window.alert(error.message || '編集PDFの読み込みに失敗しました。');
    }
  });

  referencePdfInput.addEventListener('change', async (event) => {
    const file = event.target.files && event.target.files[0];
    event.target.value = '';
    if (!file) return;
    if (referencePdfFile && hasReferenceCopyOps()) {
      const shouldReplace = window.confirm('参照PDFを差し替えると、参照PDFから作成した範囲コピーは削除されます。続行しますか。');
      if (!shouldReplace) return;
      commitHistory();
      restoreOpsState(ops.filter((op) => !(op.type === 'copy_region_paste' && op.source_document === 'reference')));
    }
    try {
      await loadPdfFile(file, 'reference');
    } catch (error) {
      window.alert(error.message || '参照PDFの読み込みに失敗しました。');
    }
  });

  document.addEventListener('click', () => {
    hideMenu();
    hideUploadMenu();
    hideSettingsMenu();
  });
  window.addEventListener('resize', syncViewerHeights);

  overlayLayer.addEventListener('contextmenu', (event) => {
    event.preventDefault();
    if (handMode || !editPdfDoc) return;
    const point = getPointFromLayer(overlayLayer, event);
    contextPoint = point;
    showMenu(point.x, point.y);
  });

  overlayLayer.addEventListener('click', (event) => {
    if (!editPdfDoc || handMode || isPanning) return;
    if (suppressClick) {
      suppressClick = false;
      return;
    }
    const item = event.target.closest('.overlay-item');
    if (item) {
      selectOp(Number(item.dataset.idx));
      return;
    }

    selectOp(-1);
    const point = getPointFromLayer(overlayLayer, event);
    if (mode === 'line' && lineStart) {
      commitHistory();
      ops.push(normalizeOp({ type: 'add_shape', page: editPageNum, shape: 'line', x1: lineStart.x, y1: lineStart.y, x2: editPxToPdf(point.x), y2: editPxToPdf(point.y), stroke_color: '#1d4ed8', stroke_width: 2, stroke_style: 'solid' }));
      resetTransientModes();
      updateOpsField();
      renderOps();
      renderLayerList();
      selectOp(ops.length - 1);
      updateSubmitState();
    } else if (mode === 'copy_paste' && copySource) {
      commitHistory();
      ops.push(normalizeOp({
        type: 'copy_region_paste',
        source_document: copySource.source_document,
        source_page: copySource.page,
        source_x: copySource.x,
        source_y: copySource.y,
        source_width: copySource.w,
        source_height: copySource.h,
        target_page: editPageNum,
        target_x: editPxToPdf(point.x),
        target_y: editPxToPdf(point.y),
        target_width: copySource.w,
        target_height: copySource.h,
      }));
      resetTransientModes();
      updateOpsField();
      renderOps();
      renderLayerList();
      selectOp(ops.length - 1);
      updateSubmitState();
    }
  });

  overlayLayer.addEventListener('mousedown', (event) => {
    const item = event.target.closest('.overlay-item');
    const handle = event.target.closest('.resize-handle');
    if (item && handle && !handMode && event.button === 0) {
      const index = Number(item.dataset.idx);
      const op = ops[index];
      if (!op) return;
      commitHistory();
      resizeDrag = { idx: index, dir: handle.dataset.dir, sx: event.clientX, sy: event.clientY, op: deep(op) };
      return;
    }

    if (item && !handMode && mode !== 'copy_select' && event.button === 0) {
      const index = Number(item.dataset.idx);
      const op = ops[index];
      if (!op) return;
      selectOp(index);
      moveDrag = { idx: index, sx: getPointFromLayer(overlayLayer, event).x, sy: getPointFromLayer(overlayLayer, event).y, moved: false };
      if (op.type === 'add_text' || op.type === 'add_image') {
        moveDrag.ox = op.x;
        moveDrag.oy = op.y;
      } else if (op.type === 'add_shape') {
        if (op.shape === 'line') {
          moveDrag.ox1 = op.x1;
          moveDrag.oy1 = op.y1;
          moveDrag.ox2 = op.x2;
          moveDrag.oy2 = op.y2;
        } else {
          moveDrag.ox = op.x;
          moveDrag.oy = op.y;
        }
      } else if (op.type === 'copy_region_paste') {
        moveDrag.tx = op.target_x;
        moveDrag.ty = op.target_y;
      } else if (op.type === 'add_freehand') {
        moveDrag.points = deep(op.points || []);
      }
      return;
    }

    if ((handMode || tempHandMode) && event.button === 0) {
      event.preventDefault();
      isPanning = true;
      panStart = { x: event.clientX, y: event.clientY, left: viewerWrap.scrollLeft, top: viewerWrap.scrollTop };
      syncEditOverlay();
      return;
    }

    if (mode === 'freehand' && !handMode && event.button === 0 && !item && !handle) {
      const point = getPointFromLayer(overlayLayer, event);
      freehandCurrent = [{ x: editPxToPdf(point.x), y: editPxToPdf(point.y) }];
      updateFreehandPreview();
      return;
    }

    if (mode === 'copy_select' && editPdfDoc) {
      editDragStart = getPointFromLayer(overlayLayer, event);
      dragRect.classList.remove('hidden');
      dragRect.style.left = `${editDragStart.x}px`;
      dragRect.style.top = `${editDragStart.y}px`;
      dragRect.style.width = '0px';
      dragRect.style.height = '0px';
    }
  });

  overlayLayer.addEventListener('mousemove', (event) => {
    if (resizeDrag) {
      const op = ops[resizeDrag.idx];
      if (!op) return;
      const dx = editPxToPdf(event.clientX - resizeDrag.sx);
      const dy = editPxToPdf(event.clientY - resizeDrag.sy);
      const source = resizeDrag.op;
      const ratio = (source.width || 1) / Math.max(1, source.height || 1);
      if ('x' in source && 'width' in source) {
        if (resizeDrag.dir.includes('e')) op.width = qMin1(source.width + dx);
        if (resizeDrag.dir.includes('s')) op.height = qMin1(source.height + dy);
        if (resizeDrag.dir.includes('w')) {
          op.x = q1(source.x + dx);
          op.width = qMin1(source.width - dx);
        }
        if (resizeDrag.dir.includes('n')) {
          op.y = q1(source.y + dy);
          op.height = qMin1(source.height - dy);
        }
        if (event.shiftKey) {
          if (op.width / op.height > ratio) op.width = qMin1(op.height * ratio);
          else op.height = qMin1(op.width / ratio);
        }
      } else if ('target_x' in source) {
        if (resizeDrag.dir.includes('e')) op.target_width = qMin1(source.target_width + dx);
        if (resizeDrag.dir.includes('s')) op.target_height = qMin1(source.target_height + dy);
      }
      updateOpsField();
      renderOps();
      if (selectedIndex === resizeDrag.idx) selectOp(resizeDrag.idx);
      return;
    }

    if (moveDrag) {
      const point = getPointFromLayer(overlayLayer, event);
      const dx = editPxToPdf(point.x - moveDrag.sx);
      const dy = editPxToPdf(point.y - moveDrag.sy);
      if (Math.abs(dx) > 0.2 || Math.abs(dy) > 0.2) moveDrag.moved = true;
      const op = ops[moveDrag.idx];
      if (!op) return;
      if (op.type === 'add_text' || op.type === 'add_image') {
        op.x = q1((moveDrag.ox ?? op.x ?? 0) + dx);
        op.y = q1((moveDrag.oy ?? op.y ?? 0) + dy);
      } else if (op.type === 'add_shape') {
        if (op.shape === 'line') {
          op.x1 = q1((moveDrag.ox1 ?? op.x1 ?? 0) + dx);
          op.y1 = q1((moveDrag.oy1 ?? op.y1 ?? 0) + dy);
          op.x2 = q1((moveDrag.ox2 ?? op.x2 ?? 0) + dx);
          op.y2 = q1((moveDrag.oy2 ?? op.y2 ?? 0) + dy);
        } else {
          op.x = q1((moveDrag.ox ?? op.x ?? 0) + dx);
          op.y = q1((moveDrag.oy ?? op.y ?? 0) + dy);
        }
      } else if (op.type === 'copy_region_paste') {
        op.target_x = q1((moveDrag.tx ?? op.target_x ?? 0) + dx);
        op.target_y = q1((moveDrag.ty ?? op.target_y ?? 0) + dy);
      } else if (op.type === 'add_freehand' && Array.isArray(op.points)) {
        op.points = (moveDrag.points || op.points).map((pointItem) => ({
          x: q1(pointItem.x + dx),
          y: q1(pointItem.y + dy),
        }));
      }
      updateOpsField();
      renderOps();
      if (selectedIndex === moveDrag.idx) selectOp(moveDrag.idx);
      return;
    }

    if (isPanning && panStart) {
      viewerWrap.scrollLeft = panStart.left - (event.clientX - panStart.x);
      viewerWrap.scrollTop = panStart.top - (event.clientY - panStart.y);
      return;
    }

    if (mode === 'freehand' && freehandCurrent) {
      const point = getPointFromLayer(overlayLayer, event);
      freehandCurrent.push({ x: editPxToPdf(point.x), y: editPxToPdf(point.y) });
      updateFreehandPreview();
      return;
    }

    if (mode === 'copy_paste' && copySource) {
      const point = getPointFromLayer(overlayLayer, event);
      updatePastePreview(point.x, point.y);
      return;
    }

    if (editDragStart && mode === 'copy_select') {
      const point = getPointFromLayer(overlayLayer, event);
      const left = Math.min(editDragStart.x, point.x);
      const top = Math.min(editDragStart.y, point.y);
      const width = Math.abs(point.x - editDragStart.x);
      const height = Math.abs(point.y - editDragStart.y);
      dragRect.style.left = `${left}px`;
      dragRect.style.top = `${top}px`;
      dragRect.style.width = `${width}px`;
      dragRect.style.height = `${height}px`;
    }
  });

  overlayLayer.addEventListener('mouseup', (event) => {
    if (resizeDrag) {
      resizeDrag = null;
      return;
    }
    if (moveDrag) {
      suppressClick = moveDrag.moved;
      moveDrag = null;
      return;
    }
    if (mode === 'freehand' && freehandCurrent) {
      if (freehandCurrent.length >= 2) {
        commitHistory();
        ops.push(normalizeOp({ type: 'add_freehand', page: editPageNum, points: freehandCurrent, stroke_color: '#1d4ed8', stroke_width: 2, stroke_style: 'solid' }));
        updateOpsField();
        renderOps();
        renderLayerList();
        selectOp(ops.length - 1);
        updateSubmitState();
      }
      freehandCurrent = null;
      updateFreehandPreview();
      return;
    }
    if (isPanning) {
      isPanning = false;
      panStart = null;
      syncEditOverlay();
      return;
    }
    if (!editDragStart || mode !== 'copy_select') return;
    const point = getPointFromLayer(overlayLayer, event);
    const left = Math.min(editDragStart.x, point.x);
    const top = Math.min(editDragStart.y, point.y);
    const width = Math.max(2, Math.abs(point.x - editDragStart.x));
    const height = Math.max(2, Math.abs(point.y - editDragStart.y));
    copySource = { source_document: 'edit', page: editPageNum, x: q1(editPxToPdf(left)), y: q1(editPxToPdf(top)), w: qMin1(editPxToPdf(width)), h: qMin1(editPxToPdf(height)) };
    editDragStart = null;
    dragRect.classList.add('hidden');
    mode = 'copy_paste';
    void ensureCopyPreview(copySource);
    updatePastePreview(point.x, point.y);
    updateModeBadge();
  });

  overlayLayer.addEventListener('mouseleave', () => {
    if (moveDrag) {
      suppressClick = moveDrag.moved;
      moveDrag = null;
    }
    pastePreview.classList.add('hidden');
    if (mode !== 'freehand') {
      freehandPreview.classList.add('hidden');
      freehandPreview.innerHTML = '';
    }
  });

  referenceOverlayLayer.addEventListener('mousedown', (event) => {
    if (mode !== 'reference_copy_select' || !referencePdfDoc || event.button !== 0) return;
    referenceDragStart = getPointFromLayer(referenceOverlayLayer, event);
    referenceDragRect.classList.remove('hidden');
    referenceDragRect.style.left = `${referenceDragStart.x}px`;
    referenceDragRect.style.top = `${referenceDragStart.y}px`;
    referenceDragRect.style.width = '0px';
    referenceDragRect.style.height = '0px';
  });

  referenceOverlayLayer.addEventListener('mousemove', (event) => {
    if (!referenceDragStart || mode !== 'reference_copy_select') return;
    const point = getPointFromLayer(referenceOverlayLayer, event);
    const left = Math.min(referenceDragStart.x, point.x);
    const top = Math.min(referenceDragStart.y, point.y);
    const width = Math.abs(point.x - referenceDragStart.x);
    const height = Math.abs(point.y - referenceDragStart.y);
    referenceDragRect.style.left = `${left}px`;
    referenceDragRect.style.top = `${top}px`;
    referenceDragRect.style.width = `${width}px`;
    referenceDragRect.style.height = `${height}px`;
  });

  referenceOverlayLayer.addEventListener('mouseup', (event) => {
    if (!referenceDragStart || mode !== 'reference_copy_select') return;
    const point = getPointFromLayer(referenceOverlayLayer, event);
    const left = Math.min(referenceDragStart.x, point.x);
    const top = Math.min(referenceDragStart.y, point.y);
    const width = Math.max(2, Math.abs(point.x - referenceDragStart.x));
    const height = Math.max(2, Math.abs(point.y - referenceDragStart.y));
    copySource = { source_document: 'reference', page: referencePageNum, x: q1(referencePxToPdf(left)), y: q1(referencePxToPdf(top)), w: qMin1(referencePxToPdf(width)), h: qMin1(referencePxToPdf(height)) };
    referenceDragStart = null;
    referenceDragRect.classList.add('hidden');
    mode = 'copy_paste';
    referenceOverlayLayer.classList.remove('reference-selecting');
    void ensureCopyPreview(copySource);
    updateModeBadge();
    viewerWrap.focus();
  });

  document.addEventListener('keydown', (event) => {
    const commandKey = event.ctrlKey || event.metaKey;
    const target = event.target;
    const isTyping = target && ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName);
    if (event.code === 'Space' && !tempHandMode && !isTyping) {
      tempHandMode = true;
      syncEditOverlay();
      event.preventDefault();
    }
    if (event.key === 'Escape') {
      resetTransientModes();
    }
    if (commandKey && event.key.toLowerCase() === 'c' && !isTyping) cloneSelectedOp();
    if (commandKey && event.key.toLowerCase() === 'v' && !isTyping) {
      pasteClonedOp();
      event.preventDefault();
    }
    if (commandKey && event.key.toLowerCase() === 'z' && event.shiftKey && !isTyping) {
      doRedo();
      event.preventDefault();
    } else if (commandKey && event.key.toLowerCase() === 'z' && !isTyping) {
      doUndo();
      event.preventDefault();
    }
  });
  document.addEventListener('keyup', (event) => {
    if (event.code === 'Space') {
      tempHandMode = false;
      syncEditOverlay();
    }
  });

  viewerWrap.setAttribute('tabindex', '0');
  referenceViewerWrap.setAttribute('tabindex', '0');

  editorForm.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (!editPdfFile) {
      window.alert('編集PDFをアップロードしてください。');
      return;
    }
    if (!ops.length) {
      window.alert('編集内容がありません。');
      return;
    }
    if (hasReferenceCopyOps() && !referencePdfFile) {
      window.alert('参照PDFからの範囲コピーを保存するには、参照PDFが必要です。');
      return;
    }

    updateOpsField();
    const originalText = editorSubmitBtn.textContent;
    editorSubmitBtn.disabled = true;
    editorSubmitBtn.textContent = '生成中...';
    try {
      const formData = new FormData();
      formData.append('pdf', editPdfFile, editPdfFile.name);
      if (referencePdfFile) formData.append('reference_pdf', referencePdfFile, referencePdfFile.name);
      formData.append('operations', operationsInput.value);
      imageFilesByRef.forEach((file, ref) => formData.append(ref, file, file.name));
      const response = await fetch(editorForm.action, { method: 'POST', body: formData });
      if (!response.ok) {
        let message = '編集PDFの生成に失敗しました。';
        try {
          const data = await response.json();
          if (data?.error) message = data.error;
        } catch (_) {}
        throw new Error(message);
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = 'powerpdf_edited_output.pdf';
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      window.alert(error.message || '編集PDFの生成に失敗しました。');
    } finally {
      editorSubmitBtn.disabled = false;
      editorSubmitBtn.textContent = originalText;
      updateSubmitState();
    }
  });

  updateOpsField();
  renderLayerList();
  applyReferenceLayout();
  updateViewerVisibility();
  updateModeBadge();
  syncViewerHeights();
  syncEditOverlay();
})();
