/* PowerImager - ImageIO: file input, metadata, export */
window.PIImageIO = (function () {
  const MAX_FILE_BYTES = 80 * 1024 * 1024;
  const MAX_DECODED_PIXELS = 80000000;
  const SUPPORTED_IMAGE_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp', 'image/gif', 'image/bmp']);

  const crcTable = (() => {
    const table = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = (c & 1) ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
      table[n] = c >>> 0;
    }
    return table;
  })();

  function isSupportedImage(file) {
    return file && (SUPPORTED_IMAGE_TYPES.has(file.type) || /^image\//.test(file.type || ''));
  }

  function assertSafeFile(file) {
    if (!file) throw new Error('画像ファイルが選択されていません');
    if (!isSupportedImage(file)) throw new Error('対応していない画像形式です');
    if (file.size > MAX_FILE_BYTES) throw new Error('画像ファイルが大きすぎます。80MB以下にしてください');
  }

  function readAsArrayBuffer(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(new Error('画像ファイルを読み込めませんでした'));
      reader.readAsArrayBuffer(file);
    });
  }

  function loadImageFromBlob(blob) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(blob);
      const img = new Image();
      img.onload = () => {
        URL.revokeObjectURL(url);
        const width = img.naturalWidth || img.width;
        const height = img.naturalHeight || img.height;
        if (!width || !height) {
          reject(new Error('画像サイズを取得できませんでした'));
          return;
        }
        if (width * height > MAX_DECODED_PIXELS) {
          reject(new Error('画像のピクセル数が大きすぎます'));
          return;
        }
        resolve(img);
      };
      img.onerror = () => {
        URL.revokeObjectURL(url);
        reject(new Error('画像をデコードできませんでした'));
      };
      img.src = url;
    });
  }

  function roundDpi(value) {
    const n = Number(value);
    if (!Number.isFinite(n) || n <= 0) return null;
    return Math.round(n);
  }

  function normalizeDpi(dpi) {
    if (!dpi) return null;
    const x = roundDpi(dpi.x || dpi.horizontal || dpi.value || dpi);
    const y = roundDpi(dpi.y || dpi.vertical || dpi.value || dpi);
    if (!x && !y) return null;
    return { x: x || y, y: y || x };
  }

  function bytesToAscii(bytes, start, length) {
    let s = '';
    for (let i = 0; i < length; i++) s += String.fromCharCode(bytes[start + i] || 0);
    return s;
  }

  function readU16(bytes, offset, little) {
    return little
      ? bytes[offset] | (bytes[offset + 1] << 8)
      : (bytes[offset] << 8) | bytes[offset + 1];
  }

  function readU32(bytes, offset, little) {
    return little
      ? ((bytes[offset]) | (bytes[offset + 1] << 8) | (bytes[offset + 2] << 16) | (bytes[offset + 3] << 24)) >>> 0
      : (((bytes[offset] << 24) | (bytes[offset + 1] << 16) | (bytes[offset + 2] << 8) | bytes[offset + 3]) >>> 0);
  }

  function parsePngDpi(bytes) {
    if (bytes.length < 33) return null;
    const isPng = bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47;
    if (!isPng) return null;
    let offset = 8;
    while (offset + 12 <= bytes.length) {
      const length = readU32(bytes, offset, false);
      const type = bytesToAscii(bytes, offset + 4, 4);
      const data = offset + 8;
      if (type === 'pHYs' && length >= 9 && data + 9 <= bytes.length) {
        const xppm = readU32(bytes, data, false);
        const yppm = readU32(bytes, data + 4, false);
        const unit = bytes[data + 8];
        if (unit === 1) return normalizeDpi({ x: xppm * 0.0254, y: yppm * 0.0254 });
        return null;
      }
      offset += 12 + length;
    }
    return null;
  }

  function parseExifDpi(bytes, start, length) {
    if (bytesToAscii(bytes, start, 6) !== 'Exif\0\0') return null;
    const tiff = start + 6;
    if (tiff + 8 > start + length) return null;
    const order = bytesToAscii(bytes, tiff, 2);
    const little = order === 'II';
    if (!little && order !== 'MM') return null;
    const magic = readU16(bytes, tiff + 2, little);
    if (magic !== 42) return null;
    const ifdOffset = readU32(bytes, tiff + 4, little);
    const ifd = tiff + ifdOffset;
    if (ifd + 2 > start + length) return null;
    const count = readU16(bytes, ifd, little);
    let xRes = null, yRes = null, unit = 2;
    for (let i = 0; i < count; i++) {
      const entry = ifd + 2 + i * 12;
      if (entry + 12 > start + length) break;
      const tag = readU16(bytes, entry, little);
      const type = readU16(bytes, entry + 2, little);
      const valueOffset = readU32(bytes, entry + 8, little);
      if ((tag === 0x011a || tag === 0x011b) && type === 5) {
        const pos = tiff + valueOffset;
        if (pos + 8 <= start + length) {
          const num = readU32(bytes, pos, little);
          const den = readU32(bytes, pos + 4, little) || 1;
          if (tag === 0x011a) xRes = num / den;
          if (tag === 0x011b) yRes = num / den;
        }
      } else if (tag === 0x0128) {
        unit = type === 3 ? readU16(bytes, entry + 8, little) : valueOffset;
      }
    }
    if (!xRes && !yRes) return null;
    const factor = unit === 3 ? 2.54 : 1;
    return normalizeDpi({ x: (xRes || yRes) * factor, y: (yRes || xRes) * factor });
  }

  function parseJpegDpi(bytes) {
    if (bytes.length < 4 || bytes[0] !== 0xff || bytes[1] !== 0xd8) return null;
    let offset = 2;
    while (offset + 4 < bytes.length && bytes[offset] === 0xff) {
      const marker = bytes[offset + 1];
      if (marker === 0xda || marker === 0xd9) break;
      const length = readU16(bytes, offset + 2, false);
      const data = offset + 4;
      if (length < 2 || data + length - 2 > bytes.length) break;
      if (marker === 0xe0 && bytesToAscii(bytes, data, 5) === 'JFIF\0') {
        const unit = bytes[data + 7];
        const x = readU16(bytes, data + 8, false);
        const y = readU16(bytes, data + 10, false);
        if (unit === 1) return normalizeDpi({ x, y });
        if (unit === 2) return normalizeDpi({ x: x * 2.54, y: y * 2.54 });
      }
      if (marker === 0xe1) {
        const exif = parseExifDpi(bytes, data, length - 2);
        if (exif) return exif;
      }
      offset += 2 + length;
    }
    return null;
  }

  function parseDpiFromBytes(bytes, mimeType) {
    const dpi = mimeType === 'image/png' ? parsePngDpi(bytes) : mimeType === 'image/jpeg' ? parseJpegDpi(bytes) : null;
    return dpi || null;
  }

  async function loadImageAsset(file) {
    assertSafeFile(file);
    const buffer = await readAsArrayBuffer(file);
    const bytes = new Uint8Array(buffer);
    const metadata = {
      name: file.name || 'Image',
      type: file.type || '',
      size: file.size || bytes.length,
      dpi: parseDpiFromBytes(bytes, file.type || '')
    };
    const img = await loadImageFromBlob(new Blob([bytes], { type: file.type || 'application/octet-stream' }));
    img.piMetadata = metadata;
    return { image: img, metadata };
  }

  async function loadFromFile(file) {
    const asset = await loadImageAsset(file);
    return asset.image;
  }

  async function loadFromDataURL(dataURL, metadata) {
    const parts = dataURLToParts(dataURL);
    const blob = new Blob([parts.bytes], { type: parts.mimeType });
    const inferred = parseDpiFromBytes(parts.bytes, parts.mimeType);
    const img = await loadImageFromBlob(blob);
    img.piMetadata = { ...(metadata || {}), type: parts.mimeType, dpi: (metadata && metadata.dpi) || inferred };
    return { image: img, metadata: img.piMetadata };
  }

  async function loadFromClipboard() {
    try {
      const items = await navigator.clipboard.read();
      for (const item of items) {
        for (const type of item.types) {
          if (type.startsWith('image/')) {
            const blob = await item.getType(type);
            const file = new File([blob], 'clipboard.png', { type: type || blob.type || 'image/png' });
            return loadFromFile(file);
          }
        }
      }
    } catch (e) { console.log('Clipboard read failed:', e); }
    return null;
  }

  function crc32(bytes) {
    let c = 0xffffffff;
    for (let i = 0; i < bytes.length; i++) c = crcTable[(c ^ bytes[i]) & 0xff] ^ (c >>> 8);
    return (c ^ 0xffffffff) >>> 0;
  }

  function writeU32(bytes, offset, value) {
    bytes[offset] = (value >>> 24) & 0xff;
    bytes[offset + 1] = (value >>> 16) & 0xff;
    bytes[offset + 2] = (value >>> 8) & 0xff;
    bytes[offset + 3] = value & 0xff;
  }

  function writeU16(bytes, offset, value) {
    bytes[offset] = (value >>> 8) & 0xff;
    bytes[offset + 1] = value & 0xff;
  }

  function base64ToBytes(base64) {
    const binary = atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return bytes;
  }

  function bytesToBase64(bytes) {
    let binary = '';
    const chunk = 0x8000;
    for (let i = 0; i < bytes.length; i += chunk) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
    }
    return btoa(binary);
  }

  function dataURLToParts(dataURL) {
    const match = String(dataURL || '').match(/^data:([^;]+);base64,(.*)$/);
    if (!match) throw new Error('Invalid data URL');
    return { mimeType: match[1], base64: match[2], bytes: base64ToBytes(match[2]) };
  }

  function dataURLToBlob(dataURL) {
    const parts = dataURLToParts(dataURL);
    return new Blob([parts.bytes], { type: parts.mimeType });
  }

  function createPngPhysChunk(dpi) {
    const ppu = Math.max(1, Math.round(dpi * 39.37007874015748));
    const chunk = new Uint8Array(4 + 4 + 9 + 4);
    writeU32(chunk, 0, 9);
    chunk.set([0x70, 0x48, 0x59, 0x73], 4);
    writeU32(chunk, 8, ppu);
    writeU32(chunk, 12, ppu);
    chunk[16] = 1;
    writeU32(chunk, 17, crc32(chunk.subarray(4, 17)));
    return chunk;
  }

  function patchPngDpi(bytes, dpi) {
    if (bytes.length < 8) return bytes;
    let offset = 8;
    const chunk = createPngPhysChunk(dpi);
    while (offset + 12 <= bytes.length) {
      const length = readU32(bytes, offset, false);
      const type = bytesToAscii(bytes, offset + 4, 4);
      if (type === 'pHYs') {
        const out = new Uint8Array(bytes.length - (12 + length) + chunk.length);
        out.set(bytes.subarray(0, offset), 0);
        out.set(chunk, offset);
        out.set(bytes.subarray(offset + 12 + length), offset + chunk.length);
        return out;
      }
      if (type === 'IDAT') break;
      offset += 12 + length;
    }
    const out = new Uint8Array(bytes.length + chunk.length);
    out.set(bytes.subarray(0, offset), 0);
    out.set(chunk, offset);
    out.set(bytes.subarray(offset), offset + chunk.length);
    return out;
  }

  function createJfifSegment(dpi) {
    const density = Math.max(1, Math.min(65535, Math.round(dpi)));
    const segment = new Uint8Array(18);
    segment.set([0xff, 0xe0], 0);
    writeU16(segment, 2, 16);
    segment.set([0x4a, 0x46, 0x49, 0x46, 0x00], 4);
    segment[9] = 1; segment[10] = 2;
    segment[11] = 1;
    writeU16(segment, 12, density);
    writeU16(segment, 14, density);
    segment[16] = 0; segment[17] = 0;
    return segment;
  }

  function patchJpegDpi(bytes, dpi) {
    if (bytes.length < 4 || bytes[0] !== 0xff || bytes[1] !== 0xd8) return bytes;
    let offset = 2;
    if (bytes[offset] === 0xff && bytes[offset + 1] === 0xe0) {
      const length = readU16(bytes, offset + 2, false);
      const data = offset + 4;
      if (bytesToAscii(bytes, data, 5) === 'JFIF\0' && data + 12 <= bytes.length) {
        const out = new Uint8Array(bytes);
        out[data + 7] = 1;
        writeU16(out, data + 8, Math.max(1, Math.min(65535, Math.round(dpi))));
        writeU16(out, data + 10, Math.max(1, Math.min(65535, Math.round(dpi))));
        return out;
      }
      offset += 2 + length;
    }
    const segment = createJfifSegment(dpi);
    const out = new Uint8Array(bytes.length + segment.length);
    out.set(bytes.subarray(0, 2), 0);
    out.set(segment, 2);
    out.set(bytes.subarray(2), 2 + segment.length);
    return out;
  }

  function patchDpiDataURL(dataURL, format, dpi) {
    const targetDpi = roundDpi(dpi);
    if (!targetDpi) return dataURL;
    const parts = dataURLToParts(dataURL);
    let bytes = parts.bytes;
    if (format === 'png' || parts.mimeType === 'image/png') bytes = patchPngDpi(bytes, targetDpi);
    else if (format === 'jpeg' || parts.mimeType === 'image/jpeg') bytes = patchJpegDpi(bytes, targetDpi);
    else return dataURL;
    return 'data:' + parts.mimeType + ';base64,' + bytesToBase64(bytes);
  }

  function exportCanvas(format, quality, options) {
    const flat = PILayerManager.flattenAll();
    PICanvasEngine.configureContext(flat.getContext('2d'));
    const mimeType = format === 'jpeg' ? 'image/jpeg' : format === 'webp' ? 'image/webp' : 'image/png';
    const dataURL = flat.toDataURL(mimeType, quality || 0.92);
    return patchDpiDataURL(dataURL, format || 'png', (options && options.dpi) || PICanvasEngine.getDpi());
  }

  function downloadCanvas(filename, format, quality, options) {
    const dataURL = exportCanvas(format, quality, options);
    const a = document.createElement('a');
    a.href = dataURL;
    a.download = filename || 'image.' + (format || 'png');
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  function copyToClipboard() {
    const flat = PILayerManager.flattenAll();
    flat.toBlob(async (blob) => {
      try {
        await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
      } catch (e) { console.error('Clipboard write failed:', e); }
    }, 'image/png');
  }

  return {
    loadFromFile,
    loadImageAsset,
    loadFromDataURL,
    loadFromClipboard,
    parseDpiFromBytes,
    patchDpiDataURL,
    exportCanvas,
    downloadCanvas,
    copyToClipboard,
    normalizeDpi
  };
})();
