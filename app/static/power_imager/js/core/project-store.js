/* PowerImager — ProjectStore: プロジェクト保存 (IndexedDB) */
window.PIProjectStore = (function () {
  const DB_NAME = 'PowerImagerDB';
  const DB_VERSION = 2;
  const STORE_NAME = 'projects';
  let db = null;

  function init() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = (e) => {
        const d = e.target.result;
        if (!d.objectStoreNames.contains(STORE_NAME)) {
          d.createObjectStore(STORE_NAME, { keyPath: 'id' });
        }
      };
      req.onsuccess = (e) => { db = e.target.result; resolve(); };
      req.onerror = (e) => { console.error('IndexedDB error:', e); reject(e); };
    });
  }

  function saveProject(project) {
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, 'readwrite');
      tx.objectStore(STORE_NAME).put(project);
      tx.oncomplete = () => resolve();
      tx.onerror = (e) => reject(e);
    });
  }

  function loadProject(id) {
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, 'readonly');
      const req = tx.objectStore(STORE_NAME).get(id);
      req.onsuccess = () => resolve(req.result || null);
      req.onerror = (e) => reject(e);
    });
  }

  function listProjects() {
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, 'readonly');
      const req = tx.objectStore(STORE_NAME).getAll();
      req.onsuccess = () => resolve(req.result || []);
      req.onerror = (e) => reject(e);
    });
  }

  function removeProject(id) {
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE_NAME, 'readwrite');
      tx.objectStore(STORE_NAME).delete(id);
      tx.oncomplete = () => resolve();
      tx.onerror = (e) => reject(e);
    });
  }

  async function saveCurrentProject(name) {
    const size = PICanvasEngine.getCanvasSize();
    const flat = PILayerManager.flattenAll();
    const thumbnail = document.createElement('canvas');
    thumbnail.width = 160; thumbnail.height = 120;
    const tctx = thumbnail.getContext('2d');
    PICanvasEngine.configureContext(tctx);
    const scale = Math.min(160 / flat.width, 120 / flat.height);
    const tw = flat.width * scale, th = flat.height * scale;
    tctx.drawImage(flat, (160 - tw) / 2, (120 - th) / 2, tw, th);

    const layerData = PILayerManager.getAll().map(l => ({
      name: l.name, x: l.x, y: l.y, visible: l.visible,
      opacity: l.opacity, blendMode: l.blendMode,
      type: l.type, textData: l.textData, shapeData: l.shapeData,
      rotation: l.rotation || 0, scaleX: l.scaleX || 1, scaleY: l.scaleY || 1,
      locked: !!l.locked, clip: !!l.clip,
      adjustType: l.adjustType || null, adjustParams: l.adjustParams || null,
      dataURL: l.canvas.toDataURL('image/png')
    }));

    const project = {
      id: name || 'project_' + Date.now(),
      name: name || '無題プロジェクト',
      width: size.width, height: size.height,
      dpi: PICanvasEngine.getDpi(),
      thumbnail: thumbnail.toDataURL('image/jpeg', 0.7),
      layers: layerData,
      savedAt: new Date().toISOString()
    };
    await saveProject(project);
    return project;
  }

  async function loadProjectData(id) {
    const project = await loadProject(id);
    if (!project) return null;

    PILayerManager.clear();
    PICanvasEngine.setDpi(project.dpi || 96);
    PICanvasEngine.setCanvasSize(project.width, project.height);

    for (const ld of project.layers) {
      const img = new Image();
      await new Promise((resolve) => {
        img.onload = resolve;
        img.src = ld.dataURL;
      });
      const layer = PILayerManager.addImageLayer(img, ld.name);
      layer.x = ld.x; layer.y = ld.y;
      layer.visible = ld.visible; layer.opacity = ld.opacity;
      layer.blendMode = ld.blendMode; layer.type = ld.type;
      layer.rotation = ld.rotation || 0;
      layer.scaleX = ld.scaleX || 1; layer.scaleY = ld.scaleY || 1;
      layer.locked = !!ld.locked; layer.clip = !!ld.clip;
      if (ld.textData) layer.textData = ld.textData;
      if (ld.shapeData) layer.shapeData = ld.shapeData;
      if (ld.adjustType) { layer.adjustType = ld.adjustType; layer.adjustParams = ld.adjustParams || {}; }
    }

    PICanvasEngine.fitToViewport();
    PIHistoryManager.clear();
    PIHistoryManager.push('プロジェクト読込');
    PILayerManager.requestRender();
    return project;
  }

  return { init, saveProject, loadProject, listProjects, removeProject, saveCurrentProject, loadProjectData };
})();
