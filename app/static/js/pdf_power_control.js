(() => {
  const init = () => {
    const root = document.getElementById("pdfPowerRoot");
    const fileInput = document.getElementById("controlPdfInput");
    if (!root || !fileInput || !window.pdfjsLib) {
      return;
    }

    if (window.pdfjsLib.GlobalWorkerOptions) {
      window.pdfjsLib.GlobalWorkerOptions.workerSrc =
        "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
    }

    const controlProcessUrl = root.dataset.controlProcessUrl || "/tools/pdf_power/control_process";
    const uploadDrop = root.querySelector(".control-upload-drop");
    const docList = document.getElementById("controlDocList");
    const pageList = document.getElementById("controlPageList");
    const previewWrap = document.getElementById("controlPreviewWrap");
    const previewCanvas = document.getElementById("controlPreviewCanvas");
    const previewPlaceholder = document.getElementById("controlPreviewPlaceholder");
    const activePageLabel = document.getElementById("controlActivePageLabel");
    const activePageRotation = document.getElementById("controlActivePageRotation");
    const selectionSummary = document.getElementById("controlSelectionSummary");
    const statusBox = document.getElementById("controlStatus");
    const exportBtn = document.getElementById("controlExportBtn");
    const outputName = document.getElementById("controlOutputName");
    const metaTitle = document.getElementById("controlMetaTitle");
    const metaAuthor = document.getElementById("controlMetaAuthor");
    const metaSubject = document.getElementById("controlMetaSubject");
    const metaKeywords = document.getElementById("controlMetaKeywords");
    const outputPasswordEnabled = document.getElementById("controlOutputPasswordEnabled");
    const outputPassword = document.getElementById("controlOutputPassword");
    const watermarkEnabled = document.getElementById("controlWatermarkEnabled");
    const watermarkText = document.getElementById("controlWatermarkText");
    const watermarkPages = document.getElementById("controlWatermarkPages");
    const watermarkPosition = document.getElementById("controlWatermarkPosition");
    const watermarkColor = document.getElementById("controlWatermarkColor");
    const watermarkOpacity = document.getElementById("controlWatermarkOpacity");
    const watermarkFontSize = document.getElementById("controlWatermarkFontSize");
    const watermarkRotation = document.getElementById("controlWatermarkRotation");

    const state = {
      currentStep: 1,
      documents: [],
      pages: [],
      activePageId: null,
      previewTask: null,
      dragPageId: null,
      uploadDragDepth: 0,
      busy: false,
    };

    const ui = {
      stepTabs: Array.from(document.querySelectorAll("[data-control-step-nav]")),
      stepPanels: Array.from(document.querySelectorAll("[data-control-step-panel]")),
      toArrangeBtn: document.getElementById("controlToArrangeBtn"),
      toOutputBtn: document.getElementById("controlToOutputBtn"),
      backToUploadBtn: document.getElementById("controlBackToUploadBtn"),
      backToArrangeBtn: document.getElementById("controlBackToArrangeBtn"),
      arrangeCountBadge: document.getElementById("controlArrangeCountBadge"),
      outputCountBadge: document.getElementById("controlOutputCountBadge"),
    };

    const uid = (prefix) => `${prefix}-${Math.random().toString(36).slice(2, 10)}${Date.now().toString(36)}`;

    const normalizeRotation = (value) => {
      let rotation = Number.parseInt(value, 10);
      if (Number.isNaN(rotation)) {
        rotation = 0;
      }
      rotation %= 360;
      if (rotation < 0) {
        rotation += 360;
      }
      return rotation;
    };

    const escapeHtml = (value) =>
      String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");

    const getDocument = (documentId) => state.documents.find((doc) => doc.id === documentId) || null;
    const getPage = (pageId) => state.pages.find((page) => page.id === pageId) || null;
    const selectedPages = () => state.pages.filter((page) => page.included);

    const setStatus = (message, kind = "neutral") => {
      statusBox.textContent = message;
      statusBox.classList.remove("is-error", "is-success");
      if (kind === "error") {
        statusBox.classList.add("is-error");
      } else if (kind === "success") {
        statusBox.classList.add("is-success");
      }
    };

    const updateHeroDetail = () => {
      if (typeof window.setPdfPowerHeroDetail !== "function") {
        return;
      }
      const totalSize = state.documents.reduce((sum, doc) => sum + Number(doc.file?.size || 0), 0);
      window.setPdfPowerHeroDetail("control", {
        count: state.documents.length,
        size: totalSize,
        note: state.documents.length
          ? "コントロールに読み込んだPDFの合計容量です。"
          : "アップロードされたPDFの合計容量とファイル数をここに表示します。",
      });
    };

    const getMaxStep = () => (state.pages.length > 0 ? 3 : 1);

    const updateSelectionSummary = () => {
      const selected = selectedPages().length;
      const total = state.pages.length;
      const summary = `${selected} / ${total} ページを出力対象`;
      selectionSummary.textContent = summary;
      if (ui.arrangeCountBadge) {
        ui.arrangeCountBadge.textContent = total ? `${total}ページを読み込み済み` : "まだページがありません";
      }
      if (ui.outputCountBadge) {
        ui.outputCountBadge.textContent = total ? `${selected}ページを出力予定` : "0ページ";
      }
      if (ui.toArrangeBtn) {
        ui.toArrangeBtn.disabled = state.pages.length === 0;
      }
      if (ui.toOutputBtn) {
        ui.toOutputBtn.disabled = selected === 0;
      }
      exportBtn.disabled = state.busy || selected === 0;
    };

    const updateStepUI = () => {
      const maxStep = getMaxStep();
      ui.stepTabs.forEach((button) => {
        const step = Number.parseInt(button.dataset.controlStepNav || "1", 10);
        button.disabled = step > maxStep;
        button.classList.toggle("is-active", step === state.currentStep);
      });

      ui.stepPanels.forEach((panel) => {
        const step = Number.parseInt(panel.dataset.controlStepPanel || "1", 10);
        panel.hidden = step !== state.currentStep;
      });

      updateSelectionSummary();
    };

    const goToStep = (step) => {
      const maxStep = getMaxStep();
      if (step > maxStep) {
        if (step === 2) {
          setStatus("まずPDFを読み込んでください。", "error");
        } else if (step === 3) {
          setStatus("出力対象のページを選んでから進んでください。", "error");
        }
        return;
      }

      if (step === 3 && selectedPages().length === 0) {
        setStatus("出力対象のページを1つ以上選択してください。", "error");
        return;
      }

      state.currentStep = step;
      updateStepUI();
      if (step === 2) {
        renderActivePreview().catch(() => undefined);
      }
    };

    const destroyPdfHandle = async (doc) => {
      if (doc && doc.pdfHandle && typeof doc.pdfHandle.destroy === "function") {
        try {
          await doc.pdfHandle.destroy();
        } catch (_) {
          // ignore cleanup issues
        }
      }
      if (doc) {
        doc.pdfHandle = null;
      }
    };

    const isPasswordError = (error) => {
      if (!error) {
        return false;
      }
      const name = String(error.name || "");
      const message = String(error.message || "");
      return name.includes("Password") || /password/i.test(message);
    };

    const renderDocuments = () => {
      updateHeroDetail();

      if (state.documents.length === 0) {
        docList.innerHTML = '<div class="control-empty">まだPDFが追加されていません。</div>';
        return;
      }

      docList.innerHTML = state.documents
        .map((doc) => {
          const statusLabel =
            doc.status === "loading"
              ? "読み込み中"
              : doc.status === "locked"
                ? "パスワード待ち"
                : doc.status === "error"
                  ? "エラー"
                  : `${doc.pageCount}ページ`;

          return `
            <article class="control-doc-card" data-doc-id="${doc.id}">
              <div class="control-doc-head">
                <div>
                  <h5 class="control-doc-title">${escapeHtml(doc.name)}</h5>
                  <p class="control-doc-meta">${escapeHtml(statusLabel)}</p>
                </div>
                <button type="button" class="control-action-btn" data-doc-action="remove">削除</button>
              </div>
              <label>
                入力パスワード
                <input type="password" data-doc-field="password" value="${escapeHtml(doc.password || "")}" placeholder="必要な場合のみ">
              </label>
              <div class="control-doc-actions">
                <button type="button" class="control-action-btn" data-doc-action="reload">読み込み/再試行</button>
              </div>
              ${doc.error ? `<div class="control-status is-error">${escapeHtml(doc.error)}</div>` : ""}
            </article>
          `;
        })
        .join("");
    };

    const renderPages = () => {
      if (state.pages.length === 0) {
        pageList.innerHTML = '<div class="control-empty">読み込んだPDFのページがここに並びます。</div>';
        updateSelectionSummary();
        return;
      }

      pageList.innerHTML = state.pages
        .map((page) => {
          const doc = getDocument(page.documentId);
          const classes = [
            "control-page-card",
            page.id === state.activePageId ? "is-active" : "",
            page.included ? "" : "is-excluded",
          ]
            .filter(Boolean)
            .join(" ");
          return `
            <article class="${classes}" data-page-id="${page.id}" draggable="true">
              <label class="control-page-checkbox">
                <input type="checkbox" ${page.included ? "checked" : ""} aria-label="このページを出力対象に含める">
              </label>
              <img class="control-page-thumb" src="${page.thumbUrl || ""}" alt="${escapeHtml(page.label)}">
              <div class="control-page-main">
                <div>
                  <div class="control-page-title">${escapeHtml(page.label)}</div>
                  <div class="control-page-meta">${escapeHtml(doc ? doc.name : "")}</div>
                </div>
                <div class="control-page-actions">
                  <button type="button" class="control-action-btn" data-page-action="rotate-left">左90°</button>
                  <button type="button" class="control-action-btn" data-page-action="rotate-right">右90°</button>
                  <span class="control-chip is-rotation">${normalizeRotation(page.rotation)}°</span>
                </div>
              </div>
            </article>
          `;
        })
        .join("");

      updateSelectionSummary();
    };

    const clearPreview = (message) => {
      previewCanvas.width = 0;
      previewCanvas.height = 0;
      previewPlaceholder.textContent = message;
      previewPlaceholder.style.display = "flex";
      activePageLabel.textContent = "ページを選択してください";
      activePageRotation.textContent = "0°";
    };

    const renderThumbnail = async (pdfHandle, pageNumber, rotation) => {
      const page = await pdfHandle.getPage(pageNumber);
      const viewport = page.getViewport({ scale: 0.28, rotation: normalizeRotation(rotation) });
      const canvas = document.createElement("canvas");
      const context = canvas.getContext("2d", { alpha: false });
      canvas.width = Math.max(1, Math.floor(viewport.width));
      canvas.height = Math.max(1, Math.floor(viewport.height));
      const task = page.render({ canvasContext: context, viewport });
      await task.promise;
      return canvas.toDataURL("image/png");
    };

    const renderActivePreview = async () => {
      const activePage = getPage(state.activePageId);
      if (!activePage) {
        clearPreview("ページを選ぶと、ここにプレビューが表示されます。");
        return;
      }

      const doc = getDocument(activePage.documentId);
      if (!doc || !doc.pdfHandle) {
        clearPreview("元PDFをまだ読み込めていません。");
        return;
      }

      if (state.previewTask) {
        try {
          state.previewTask.cancel();
        } catch (_) {
          // ignore cancel issues
        }
      }

      const page = await doc.pdfHandle.getPage(activePage.pageNumber);
      const baseViewport = page.getViewport({ scale: 1, rotation: normalizeRotation(activePage.rotation) });
      const targetWidth = Math.max(320, previewWrap.clientWidth - 24);
      const targetHeight = Math.max(420, previewWrap.clientHeight - 24);
      const scale = Math.min(targetWidth / baseViewport.width, targetHeight / baseViewport.height, 2.2);
      const viewport = page.getViewport({ scale, rotation: normalizeRotation(activePage.rotation) });
      const context = previewCanvas.getContext("2d", { alpha: false });
      previewCanvas.width = Math.ceil(viewport.width);
      previewCanvas.height = Math.ceil(viewport.height);
      previewPlaceholder.style.display = "none";

      try {
        state.previewTask = page.render({ canvasContext: context, viewport });
        await state.previewTask.promise;
      } catch (error) {
        if (!String((error && error.name) || "").includes("RenderingCancelled")) {
          throw error;
        }
        return;
      } finally {
        state.previewTask = null;
      }

      activePageLabel.textContent = activePage.label;
      activePageRotation.textContent = `${normalizeRotation(activePage.rotation)}°`;
    };

    const removeDocumentPages = (documentId) => {
      state.pages = state.pages.filter((page) => page.documentId !== documentId);
      if (!state.pages.some((page) => page.id === state.activePageId)) {
        state.activePageId = state.pages[0] ? state.pages[0].id : null;
      }
    };

    const loadDocument = async (doc) => {
      doc.status = "loading";
      doc.error = "";
      renderDocuments();

      await destroyPdfHandle(doc);
      removeDocumentPages(doc.id);
      renderPages();

      try {
        const data = await doc.file.arrayBuffer();
        const loadingTask = window.pdfjsLib.getDocument({
          data,
          password: doc.password || undefined,
        });
        const pdfHandle = await loadingTask.promise;
        doc.pdfHandle = pdfHandle;
        doc.pageCount = pdfHandle.numPages;
        doc.status = "ready";

        const newPages = [];
        for (let pageNumber = 1; pageNumber <= pdfHandle.numPages; pageNumber += 1) {
          newPages.push({
            id: uid("page"),
            documentId: doc.id,
            pageNumber,
            label: `${doc.name} / ${pageNumber}ページ`,
            included: true,
            rotation: 0,
            thumbUrl: await renderThumbnail(pdfHandle, pageNumber, 0),
          });
        }

        state.pages = [...state.pages, ...newPages];
        if (!state.activePageId && newPages[0]) {
          state.activePageId = newPages[0].id;
        }

        renderDocuments();
        renderPages();
        updateStepUI();
        setStatus(`${doc.name} を読み込みました。`);
      } catch (error) {
        doc.pageCount = 0;
        doc.pdfHandle = null;
        doc.status = isPasswordError(error) ? "locked" : "error";
        doc.error = isPasswordError(error)
          ? doc.password
            ? "入力パスワードが正しくありません。"
            : "このPDFはパスワードで保護されています。"
          : "PDFの読み込みに失敗しました。";
        renderDocuments();
        renderPages();
        updateStepUI();
        setStatus(doc.error, "error");
      }
    };

    const addFiles = async (files) => {
      for (const file of files) {
        const doc = {
          id: uid("doc"),
          file,
          name: file.name,
          password: "",
          pageCount: 0,
          pdfHandle: null,
          status: "loading",
          error: "",
        };
        state.documents.push(doc);
        renderDocuments();
        await loadDocument(doc);
      }
      fileInput.value = "";
    };

    const clearUploadDragState = () => {
      if (uploadDrop) {
        state.uploadDragDepth = 0;
        uploadDrop.classList.remove("is-dragover");
      }
    };

    const clearPageDragState = () => {
      pageList.querySelectorAll(".is-dragging").forEach((item) => item.classList.remove("is-dragging"));
      pageList.querySelectorAll(".drag-over-top").forEach((item) => item.classList.remove("drag-over-top"));
      pageList.querySelectorAll(".drag-over-bottom").forEach((item) => item.classList.remove("drag-over-bottom"));
    };

    const clearPageDropMarkers = () => {
      pageList.querySelectorAll(".drag-over-top").forEach((item) => item.classList.remove("drag-over-top"));
      pageList.querySelectorAll(".drag-over-bottom").forEach((item) => item.classList.remove("drag-over-bottom"));
    };

    const getDropPlacement = (card, clientY) => {
      const rect = card.getBoundingClientRect();
      return clientY < rect.top + rect.height / 2 ? "before" : "after";
    };

    const rerenderPageThumb = async (page) => {
      const doc = getDocument(page.documentId);
      if (!doc || !doc.pdfHandle) {
        return;
      }
      page.thumbUrl = await renderThumbnail(doc.pdfHandle, page.pageNumber, page.rotation);
    };

    const rotatePages = async (pageIds, delta) => {
      if (pageIds.length === 0) {
        setStatus("回転するページを選択してください。", "error");
        return;
      }

      state.busy = true;
      updateSelectionSummary();
      try {
        for (const pageId of pageIds) {
          const page = getPage(pageId);
          if (!page) {
            continue;
          }
          page.rotation = normalizeRotation(page.rotation + delta);
          await rerenderPageThumb(page);
        }
        renderPages();
        await renderActivePreview();
        setStatus("ページ回転を反映しました。");
      } finally {
        state.busy = false;
        updateSelectionSummary();
      }
    };

    const buildPlan = () => {
      const selected = selectedPages();
      const usedDocumentIds = new Set(selected.map((page) => page.documentId));
      const documents = state.documents
        .filter((doc) => usedDocumentIds.has(doc.id))
        .map((doc, uploadIndex) => ({
          id: doc.id,
          uploadIndex,
          name: doc.name,
          password: doc.password || "",
        }));

      return {
        outputName: outputName.value,
        documents,
        pages: selected.map((page) => ({
          documentId: page.documentId,
          sourcePageIndex: page.pageNumber - 1,
          rotation: normalizeRotation(page.rotation),
        })),
        watermark: {
          enabled: watermarkEnabled.checked,
          text: watermarkText.value,
          pages: watermarkPages.value || "all",
          position: watermarkPosition.value,
          color: watermarkColor.value,
          opacity: watermarkOpacity.value,
          font_size: watermarkFontSize.value,
          rotation: watermarkRotation.value,
        },
        metadata: {
          title: metaTitle.value,
          author: metaAuthor.value,
          subject: metaSubject.value,
          keywords: metaKeywords.value,
        },
        outputPassword: {
          enabled: outputPasswordEnabled.checked,
          password: outputPassword.value,
        },
      };
    };

    const exportPdf = async () => {
      const selected = selectedPages();
      if (selected.length === 0) {
        setStatus("出力対象のページを1つ以上選択してください。", "error");
        return;
      }

      if (outputPasswordEnabled.checked && !outputPassword.value) {
        setStatus("出力パスワードを設定する場合はパスワードを入力してください。", "error");
        return;
      }

      const requiredLockedDoc = state.documents.find(
        (doc) => selected.some((page) => page.documentId === doc.id) && !doc.pdfHandle
      );
      if (requiredLockedDoc) {
        setStatus(`${requiredLockedDoc.name} を読み込めていません。入力パスワードを確認してください。`, "error");
        return;
      }

      state.busy = true;
      updateSelectionSummary();
      setStatus("PDFを書き出しています...");
      const loadingHandle = window.DSTTLoading && typeof window.DSTTLoading.show === 'function'
        ? window.DSTTLoading.show({ message: "PDFを書き出しています...", detail: "ページ数に応じて時間がかかる場合があります" })
        : null;

      try {
        const plan = buildPlan();
        const formData = new FormData();
        for (const doc of state.documents.filter((item) => plan.documents.some((planDoc) => planDoc.id === item.id))) {
          formData.append("pdfs", doc.file, doc.file.name);
        }
        formData.append("plan", JSON.stringify(plan));

        const response = await fetch(controlProcessUrl, {
          method: "POST",
          body: formData,
        });

        if (!response.ok) {
          const payload = await response.json().catch(() => ({}));
          throw new Error(payload.error || "PDF出力に失敗しました。");
        }

        const blob = await response.blob();
        const downloadUrl = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = downloadUrl;
        anchor.download = String(outputName.value || "control_output.pdf").trim() || "control_output.pdf";
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(downloadUrl);
        setStatus("PDFを書き出しました。", "success");
      } catch (error) {
        setStatus(String(error.message || error || "PDF出力に失敗しました。"), "error");
      } finally {
        state.busy = false;
        updateSelectionSummary();
        if (typeof loadingHandle === 'function') {
          loadingHandle();
        } else if (window.DSTTLoading && typeof window.DSTTLoading.hide === 'function') {
          window.DSTTLoading.hide();
        }
      }
    };

    fileInput.addEventListener("change", async (event) => {
      const files = Array.from(event.target.files || []);
      if (files.length === 0) {
        return;
      }
      await addFiles(files);
    });

    if (uploadDrop) {
      uploadDrop.addEventListener("keydown", (event) => {
        if (event.key !== "Enter" && event.key !== " ") {
          return;
        }
        event.preventDefault();
        fileInput.click();
      });

      uploadDrop.addEventListener("dragenter", (event) => {
        event.preventDefault();
        state.uploadDragDepth += 1;
        uploadDrop.classList.add("is-dragover");
      });

      uploadDrop.addEventListener("dragover", (event) => {
        event.preventDefault();
        if (!uploadDrop.classList.contains("is-dragover")) {
          uploadDrop.classList.add("is-dragover");
        }
      });

      uploadDrop.addEventListener("dragleave", (event) => {
        event.preventDefault();
        state.uploadDragDepth = Math.max(0, state.uploadDragDepth - 1);
        if (state.uploadDragDepth === 0) {
          clearUploadDragState();
        }
      });

      uploadDrop.addEventListener("dragend", clearUploadDragState);

      uploadDrop.addEventListener("drop", async (event) => {
        event.preventDefault();
        clearUploadDragState();
        const files = Array.from(event.dataTransfer?.files || []);
        if (files.length === 0) {
          return;
        }
        await addFiles(files);
      });
    }

    docList.addEventListener("input", (event) => {
      const card = event.target.closest("[data-doc-id]");
      if (!card || event.target.dataset.docField !== "password") {
        return;
      }
      const doc = getDocument(card.dataset.docId);
      if (doc) {
        doc.password = event.target.value;
      }
    });

    docList.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-doc-action]");
      if (!button) {
        return;
      }

      const card = button.closest("[data-doc-id]");
      const doc = card ? getDocument(card.dataset.docId) : null;
      if (!doc) {
        return;
      }

      if (button.dataset.docAction === "remove") {
        await destroyPdfHandle(doc);
        state.documents = state.documents.filter((item) => item.id !== doc.id);
        removeDocumentPages(doc.id);
        renderDocuments();
        renderPages();
        updateStepUI();
        await renderActivePreview();
        setStatus(`${doc.name} を一覧から外しました。`);
        return;
      }

      if (button.dataset.docAction === "reload") {
        await loadDocument(doc);
      }
    });

    pageList.addEventListener("change", async (event) => {
      const checkbox = event.target.closest('input[type="checkbox"]');
      if (!checkbox) {
        return;
      }

      const card = checkbox.closest("[data-page-id]");
      const page = card ? getPage(card.dataset.pageId) : null;
      if (!page) {
        return;
      }

      page.included = checkbox.checked;
      renderPages();
      if (state.currentStep === 2) {
        await renderActivePreview();
      }
    });

    pageList.addEventListener("click", async (event) => {
      if (event.target.closest('input[type="checkbox"]')) {
        return;
      }

      const card = event.target.closest("[data-page-id]");
      if (!card) {
        return;
      }

      const page = getPage(card.dataset.pageId);
      if (!page) {
        return;
      }

      const actionButton = event.target.closest("[data-page-action]");
      if (actionButton) {
        const action = actionButton.dataset.pageAction;
        if (action === "rotate-left") {
          await rotatePages([page.id], -90);
          return;
        }
        if (action === "rotate-right") {
          await rotatePages([page.id], 90);
          return;
        }
      }

      state.activePageId = page.id;
      renderPages();
      await renderActivePreview();
    });

    pageList.addEventListener("dragstart", (event) => {
      const card = event.target.closest("[data-page-id]");
      if (!card) {
        return;
      }
      if (event.target.closest("input,button")) {
        event.preventDefault();
        return;
      }
      state.dragPageId = card.dataset.pageId;
      card.classList.add("is-dragging");
      if (event.dataTransfer) {
        event.dataTransfer.effectAllowed = "move";
        try {
          event.dataTransfer.setData("text/plain", state.dragPageId);
        } catch (_) {
          // ignore browser-specific dataTransfer limitations
        }
      }
    });

    pageList.addEventListener("dragover", (event) => {
      event.preventDefault();
      const targetCard = event.target.closest("[data-page-id]");
      clearPageDropMarkers();
      if (!targetCard || !state.dragPageId || targetCard.dataset.pageId === state.dragPageId) {
        return;
      }
      const placement = getDropPlacement(targetCard, event.clientY);
      targetCard.classList.add(placement === "before" ? "drag-over-top" : "drag-over-bottom");
      if (event.dataTransfer) {
        event.dataTransfer.dropEffect = "move";
      }
    });

    pageList.addEventListener("drop", (event) => {
      event.preventDefault();
      const targetCard = event.target.closest("[data-page-id]");
      if (!targetCard || !state.dragPageId || targetCard.dataset.pageId === state.dragPageId) {
        clearPageDragState();
        state.dragPageId = null;
        return;
      }

      const fromIndex = state.pages.findIndex((page) => page.id === state.dragPageId);
      const targetIndex = state.pages.findIndex((page) => page.id === targetCard.dataset.pageId);
      if (fromIndex < 0 || targetIndex < 0) {
        clearPageDragState();
        state.dragPageId = null;
        return;
      }

      const placement = getDropPlacement(targetCard, event.clientY);
      let insertIndex = placement === "after" ? targetIndex + 1 : targetIndex;
      const [moved] = state.pages.splice(fromIndex, 1);
      if (fromIndex < insertIndex) {
        insertIndex -= 1;
      }
      state.pages.splice(insertIndex, 0, moved);

      clearPageDragState();
      state.dragPageId = null;
      renderPages();
      setStatus("ページ順を更新しました。");
    });

    pageList.addEventListener("dragend", () => {
      clearPageDragState();
      state.dragPageId = null;
    });

    document.querySelectorAll("[data-control-batch]").forEach((button) => {
      button.addEventListener("click", async () => {
        const action = button.dataset.controlBatch;
        if (action === "select-all") {
          state.pages.forEach((page) => {
            page.included = true;
          });
          renderPages();
          setStatus("すべてのページを出力対象にしました。");
          return;
        }

        if (action === "clear-selection") {
          state.pages.forEach((page) => {
            page.included = false;
          });
          renderPages();
          setStatus("すべてのページを出力対象から外しました。");
          return;
        }

        if (action === "rotate-left") {
          await rotatePages(selectedPages().map((page) => page.id), -90);
          return;
        }

        if (action === "rotate-right") {
          await rotatePages(selectedPages().map((page) => page.id), 90);
        }
      });
    });

    ui.stepTabs.forEach((button) => {
      button.addEventListener("click", () => {
        goToStep(Number.parseInt(button.dataset.controlStepNav || "1", 10));
      });
    });

    if (ui.toArrangeBtn) {
      ui.toArrangeBtn.addEventListener("click", () => goToStep(2));
    }
    if (ui.toOutputBtn) {
      ui.toOutputBtn.addEventListener("click", () => goToStep(3));
    }
    if (ui.backToUploadBtn) {
      ui.backToUploadBtn.addEventListener("click", () => goToStep(1));
    }
    if (ui.backToArrangeBtn) {
      ui.backToArrangeBtn.addEventListener("click", () => goToStep(2));
    }

    exportBtn.addEventListener("click", exportPdf);
    window.addEventListener("resize", () => {
      if (state.currentStep === 2) {
        renderActivePreview().catch(() => undefined);
      }
    });

    renderDocuments();
    renderPages();
    clearPreview("ページを選ぶと、ここにプレビューが表示されます。");
    updateStepUI();
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
