(function () {
  const config = window.compressToolConfig || {};
  const form = document.getElementById("compressForm");
  const fileInput = document.getElementById("fileInput");
  const folderInput = document.getElementById("folderInput");
  const folderSelectBtn = document.getElementById("folderSelectBtn");
  const filePathFields = document.getElementById("filePathFields");
  const dropZone = document.getElementById("dropZone");
  const fileRows = document.getElementById("fileRows");
  const formatInput = document.getElementById("formatInput");
  const formatButtons = document.querySelectorAll("#formatModes .mode-btn");
  const passwordField = document.getElementById("passwordField");
  const passwordInput = document.getElementById("passwordInput");
  const togglePassword = document.getElementById("togglePassword");
  const archiveName = document.getElementById("archiveName");
  const rootFolder = document.getElementById("rootFolder");
  const compressLevel = document.getElementById("compressLevel");
  const excludeExts = document.getElementById("excludeExts");
  const excludePatterns = document.getElementById("excludePatterns");
  const resetBtn = document.getElementById("resetBtn");
  const previewName = document.getElementById("previewName");
  const warningList = document.getElementById("warningList");
  const historyRow = document.getElementById("historyRow");
  const metricTotal = document.getElementById("metricTotal");
  const metricIncluded = document.getElementById("metricIncluded");
  const metricExcluded = document.getElementById("metricExcluded");
  const metricSize = document.getElementById("metricSize");
  const metricRenamed = document.getElementById("metricRenamed");
  const storageKey = "dstt.compress.settings.history";

  let filesArray = [];
  let previewTimer = null;

  const formatBytes = (bytes) => {
    let size = Number(bytes || 0);
    const units = ["B", "KB", "MB", "GB"];
    let idx = 0;
    while (size >= 1024 && idx < units.length - 1) {
      size /= 1024;
      idx += 1;
    }
    return idx === 0 ? `${size.toFixed(0)} ${units[idx]}` : `${size.toFixed(1)} ${units[idx]}`;
  };

  const syncFileInput = () => {
    const dt = new DataTransfer();
    filePathFields.innerHTML = "";
    filesArray.forEach((entry) => {
      dt.items.add(entry.file);
      const field = document.createElement("input");
      field.type = "hidden";
      field.name = "file_paths";
      field.value = entry.path || entry.file.name;
      filePathFields.appendChild(field);
    });
    fileInput.files = dt.files;
  };

  const getSettings = () => ({
    format: formatInput.value,
    archive_name: archiveName.value,
    root_folder: rootFolder.value,
    compress_level: compressLevel.value,
    exclude_exts: excludeExts.value,
    exclude_patterns: excludePatterns.value,
  });

  const setSettings = (settings) => {
    formatInput.value = settings.format || "zip";
    archiveName.value = settings.archive_name || "";
    rootFolder.value = settings.root_folder || "";
    compressLevel.value = settings.compress_level || "standard";
    excludeExts.value = settings.exclude_exts || "";
    excludePatterns.value = settings.exclude_patterns || "";
    updateFormatUi();
    queuePreview();
  };

  const loadHistory = () => {
    try {
      return JSON.parse(localStorage.getItem(storageKey) || "[]");
    } catch (error) {
      return [];
    }
  };

  const saveHistory = () => {
    const settings = getSettings();
    const key = JSON.stringify(settings);
    const history = loadHistory().filter((item) => JSON.stringify(item.settings) !== key);
    history.unshift({
      label: settings.archive_name || `${settings.format.toUpperCase()} 設定`,
      savedAt: new Date().toISOString(),
      settings,
    });
    localStorage.setItem(storageKey, JSON.stringify(history.slice(0, 10)));
    renderHistory();
  };

  const renderHistory = () => {
    const history = loadHistory();
    historyRow.innerHTML = "";
    if (!history.length) {
      const empty = document.createElement("span");
      empty.className = "file-muted";
      empty.textContent = "まだ履歴がありません。";
      historyRow.appendChild(empty);
      return;
    }

    history.forEach((item) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "history-btn";
      btn.textContent = item.label;
      btn.title = "この設定を再利用";
      btn.addEventListener("click", () => setSettings(item.settings));
      historyRow.appendChild(btn);
    });
  };

  const updateFormatUi = () => {
    const fmt = formatInput.value;
    formatButtons.forEach((btn) => btn.classList.toggle("active", btn.dataset.format === fmt));
    const zipMode = fmt === "zip";
    passwordField.style.display = zipMode ? "" : "none";
    passwordInput.disabled = !zipMode;
    if (!zipMode) passwordInput.value = "";
  };

  const renderRows = (files) => {
    fileRows.innerHTML = "";
    if (!filesArray.length) {
      const row = document.createElement("tr");
      row.innerHTML = '<td colspan="4" class="file-muted">ファイルを選択すると、格納名と除外理由を確認できます。</td>';
      fileRows.appendChild(row);
      return;
    }

    files.forEach((item, idx) => {
      const row = document.createElement("tr");
      const statusClass = item.included ? "status-included" : "status-excluded";
      const statusText = item.included ? "対象" : "除外";
      const detailText = item.included ? item.archive_name : item.excluded_reason;
      row.innerHTML = `
        <td>
          <div class="file-name"></div>
          <div class="${statusClass}">${statusText}</div>
        </td>
        <td class="file-muted"></td>
        <td class="file-muted"></td>
        <td><button type="button" class="icon-btn" title="一覧から外す">削除</button></td>
      `;
      row.querySelector(".file-name").textContent = item.safe_name || filesArray[idx]?.file?.name || "";
      row.children[1].textContent = item.formatted_size || formatBytes(filesArray[idx]?.file?.size || 0);
      row.children[2].textContent = detailText || "";
      row.querySelector("button").addEventListener("click", () => {
        filesArray.splice(idx, 1);
        syncFileInput();
        queuePreview();
      });
      fileRows.appendChild(row);
    });
  };

  const renderWarnings = (warnings) => {
    warningList.innerHTML = "";
    (warnings || []).forEach((warning) => {
      const item = document.createElement("li");
      item.textContent = warning;
      warningList.appendChild(item);
    });
  };

  const requestPreview = async () => {
      const payload = {
      ...getSettings(),
      filenames: filesArray.map((entry) => entry.file.name),
      file_paths: filesArray.map((entry) => entry.path || entry.file.name),
      sizes: filesArray.map((entry) => entry.file.size || 0),
    };

    try {
      const res = await fetch(config.previewUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error("preview failed");
      const data = await res.json();
      previewName.textContent = data.archive_name || "dstt_compressed.zip";
      metricTotal.textContent = `${data.total || 0}件`;
      metricIncluded.textContent = `${data.included_count || 0}件`;
      metricExcluded.textContent = `${data.excluded_count || 0}件`;
      metricRenamed.textContent = `${data.renamed_count || 0}件`;
      metricSize.textContent = data.formatted_total_size || formatBytes(data.total_size || 0);
      renderWarnings(data.warnings || []);
      renderRows(data.files || []);
    } catch (error) {
      previewName.textContent = "プレビューを取得できません";
      renderWarnings(["プレビューの取得に失敗しました。"]);
      renderRows([]);
    }
  };

  const queuePreview = () => {
    window.clearTimeout(previewTimer);
    previewTimer = window.setTimeout(requestPreview, 120);
  };

  const addFiles = (newFiles, pathResolver) => {
    const incoming = Array.from(newFiles || []).map((file) => ({
      file,
      path: pathResolver ? pathResolver(file) : file.webkitRelativePath || file.name,
    }));
    filesArray = filesArray.concat(incoming);
    syncFileInput();
    queuePreview();
  };

  const readAllEntries = (reader) => new Promise((resolve, reject) => {
    const entries = [];
    const readBatch = () => {
      reader.readEntries((batch) => {
        if (!batch.length) {
          resolve(entries);
          return;
        }
        entries.push(...batch);
        readBatch();
      }, reject);
    };
    readBatch();
  });

  const fileFromEntry = (entry, path) => new Promise((resolve, reject) => {
    entry.file((file) => resolve({ file, path: `${path}${file.name}` }), reject);
  });

  const collectEntryFiles = async (entry, path = "") => {
    if (entry.isFile) {
      return [await fileFromEntry(entry, path)];
    }
    if (!entry.isDirectory) return [];
    const nextPath = `${path}${entry.name}/`;
    const children = await readAllEntries(entry.createReader());
    const nested = await Promise.all(children.map((child) => collectEntryFiles(child, nextPath)));
    return nested.flat();
  };

  const addDroppedItems = async (dataTransfer) => {
    const items = Array.from(dataTransfer.items || []);
    const entries = items
      .map((item) => (item.webkitGetAsEntry ? item.webkitGetAsEntry() : null))
      .filter(Boolean);

    if (!entries.length) {
      addFiles(dataTransfer.files, (file) => file.name);
      return;
    }

    const nested = await Promise.all(entries.map((entry) => collectEntryFiles(entry)));
    filesArray = filesArray.concat(nested.flat());
    syncFileInput();
    queuePreview();
  };

  dropZone.addEventListener("click", () => fileInput.click());
  folderSelectBtn.addEventListener("click", () => folderInput.click());
  fileInput.addEventListener("change", () => addFiles(fileInput.files, (file) => file.name));
  folderInput.addEventListener("change", () => addFiles(folderInput.files, (file) => file.webkitRelativePath || file.name));

  ["dragenter", "dragover"].forEach((eventName) => {
    dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      event.stopPropagation();
      dropZone.classList.add("dragover");
    });
  });

  ["dragleave", "drop"].forEach((eventName) => {
    dropZone.addEventListener(eventName, (event) => {
      event.preventDefault();
      event.stopPropagation();
      dropZone.classList.remove("dragover");
    });
  });

  dropZone.addEventListener("drop", (event) => addDroppedItems(event.dataTransfer));

  formatButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      formatInput.value = btn.dataset.format;
      updateFormatUi();
      queuePreview();
      saveHistory();
    });
  });

  [archiveName, rootFolder, compressLevel, excludeExts, excludePatterns].forEach((input) => {
    input.addEventListener("input", queuePreview);
    input.addEventListener("change", saveHistory);
  });

  document.querySelectorAll(".preset-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const preset = btn.dataset.preset;
      if (preset === "standard") {
        setSettings({ format: "zip", compress_level: "standard" });
      }
      if (preset === "password") {
        setSettings({ format: "zip", compress_level: "high" });
        passwordInput.focus();
      }
      if (preset === "logs") {
        setSettings({ format: "zip", compress_level: "standard", exclude_exts: "log,tmp,bak", exclude_patterns: "*_old.*" });
      }
      if (preset === "distribution") {
        setSettings({ format: "zip", root_folder: "配布用データ", compress_level: "standard" });
      }
      saveHistory();
    });
  });

  togglePassword.addEventListener("click", () => {
    const visible = passwordInput.type === "text";
    passwordInput.type = visible ? "password" : "text";
    togglePassword.textContent = visible ? "表示" : "隠す";
  });

  resetBtn.addEventListener("click", () => {
    filesArray = [];
    fileInput.value = "";
    folderInput.value = "";
    syncFileInput();
    passwordInput.value = "";
    setSettings({ format: "zip", compress_level: "standard" });
    queuePreview();
  });

  form.addEventListener("submit", () => {
    syncFileInput();
    saveHistory();
  });

  renderHistory();
  updateFormatUi();
  requestPreview();
})();
