(() => {
  const stage = document.getElementById('stage');
  const stageViewport = document.getElementById('stageViewport');
  const stageScroll = document.getElementById('stageScroll');
  const overlayLayer = document.getElementById('overlayLayer');
  const backgroundLayer = document.getElementById('backgroundLayer');
  const detectedSize = document.getElementById('detectedSize');
  const detectedFileType = document.getElementById('detectedFileType');
  const zoomInBtn = document.getElementById('zoomInBtn');
  const zoomOutBtn = document.getElementById('zoomOutBtn');
  const zoomResetBtn = document.getElementById('zoomResetBtn');
  const zoomLabel = document.getElementById('zoomLabel');

  if (!stage || !stageViewport || !overlayLayer || !backgroundLayer || !detectedSize || !detectedFileType) {
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
  let pdfjsLibPromise = null;
  let zoom = 1;
  const ZOOM_MIN = 0.25;
  const ZOOM_MAX = 4;
  const ZOOM_STEP = 0.1;

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
  }

  function setZoom(nextZoom) {
    zoom = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, nextZoom));
    applyZoom();
  }

  let sourceSize = { ...getStagePixelSize(), unit: 'px' };

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
    if (item) item.classList.add('selected');
  }

  function makeDraggable(item) {
    item.classList.add('stamp-item');
    item.style.left = '80px';
    item.style.top = '80px';

    item.addEventListener('mousedown', (event) => {
      selectItem(item);
      const rect = item.getBoundingClientRect();
      dragState = { item, dx: event.clientX - rect.left, dy: event.clientY - rect.top };
    });
  }

  document.addEventListener('mousemove', (event) => {
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

  document.addEventListener('mouseup', () => { dragState = null; });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Delete' && selected) {
      selected.remove();
      selected = null;
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
        const renderViewport = page.getViewport({ scale: 2 });
        const widthPt = sourceViewport.width;
        const heightPt = sourceViewport.height;
        const widthMm = widthPt * 25.4 / 72;
        const heightMm = heightPt * 25.4 / 72;

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
    if (width >= 100 && height >= 100) setCanvasSize(width, height);
  });

  document.getElementById('addTextBtn').addEventListener('click', () => {
    const div = document.createElement('div');
    div.textContent = document.getElementById('textContent').value || '入力文字';
    div.style.fontSize = `${Number(document.getElementById('textSize').value)}px`;
    div.style.color = document.getElementById('textColor').value;
    div.style.whiteSpace = 'pre';
    makeDraggable(div);
    overlayLayer.appendChild(div);
    selectItem(div);
  });

  document.getElementById('addShapeBtn').addEventListener('click', () => {
    const shape = document.createElement('div');
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

  document.getElementById('printBtn').addEventListener('click', () => window.print());

  zoomInBtn?.addEventListener('click', () => setZoom(zoom + ZOOM_STEP));
  zoomOutBtn?.addEventListener('click', () => setZoom(zoom - ZOOM_STEP));
  zoomResetBtn?.addEventListener('click', () => setZoom(1));

  stageScroll?.addEventListener('wheel', (event) => {
    if (!event.ctrlKey) return;
    event.preventDefault();
    const delta = event.deltaY > 0 ? -ZOOM_STEP : ZOOM_STEP;
    setZoom(zoom + delta);
  }, { passive: false });

  async function drawOverlayToCanvas(ctx, outputWidth, outputHeight) {
    const { width: stageWidth, height: stageHeight } = getStagePixelSize();
    const scaleX = outputWidth / Math.max(1, stageWidth);
    const scaleY = outputHeight / Math.max(1, stageHeight);

    for (const node of Array.from(overlayLayer.children)) {
      const x = parseFloat(node.style.left || '0') * scaleX;
      const y = parseFloat(node.style.top || '0') * scaleY;
      if (node.tagName === 'IMG') {
        await new Promise((resolve) => {
          const image = new Image();
          image.onload = () => {
            const w = parseFloat(node.style.width || image.naturalWidth) * scaleX;
            const h = parseFloat(node.style.height || image.naturalHeight) * scaleY;
            ctx.drawImage(image, x, y, w, h);
            resolve();
          };
          image.src = node.src;
        });
      } else if (node.style.border) {
        const w = parseFloat(node.style.width || '0') * scaleX;
        const h = parseFloat(node.style.height || '0') * scaleY;
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
        const fontSize = parseFloat(node.style.fontSize || '24') * Math.min(scaleX, scaleY);
        ctx.fillStyle = node.style.color || '#111';
        ctx.font = `${Math.max(8, fontSize)}px sans-serif`;
        const lines = (node.textContent || '').split('\n');
        const lineHeight = Math.max(10, fontSize * 1.2);
        lines.forEach((line, idx) => {
          ctx.fillText(line, x, y + lineHeight * (idx + 1));
        });
      }
    }
  }

  document.getElementById('exportBtn').addEventListener('click', async () => {
    const mode = document.querySelector('input[name="exportSizeMode"]:checked')?.value || 'source';
    const canvas = document.createElement('canvas');

    const stageSize = getStagePixelSize();
    const outWidth = mode === 'source' ? Math.round(sourceSize.width || stageSize.width) : stageSize.width;
    const outHeight = mode === 'source' ? Math.round(sourceSize.height || stageSize.height) : stageSize.height;

    canvas.width = Math.max(1, outWidth);
    canvas.height = Math.max(1, outHeight);
    const ctx = canvas.getContext('2d');

    await drawOverlayToCanvas(ctx, canvas.width, canvas.height);

    const link = document.createElement('a');
    link.href = canvas.toDataURL('image/png');
    link.download = mode === 'source' ? 'powerstamp-overlay-source-size.png' : 'powerstamp-overlay-custom-size.png';
    link.click();
  });

  applyZoom();
})();
