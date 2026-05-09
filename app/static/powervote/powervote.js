(function () {
  const root = document.getElementById("powervote-app");
  if (!root) return;

  const apiBase = root.dataset.apiBase;
  const state = { forms: [], selected: null, dirty: false, view: "builder" };
  const $ = (id) => document.getElementById(id);

  const TYPES = {
    short_text: "短文",
    long_text: "長文",
    single_choice: "単一選択",
    multi_choice: "複数選択",
    number: "数値",
    rating: "評価",
    date: "日付",
    time: "時刻",
    email: "メール",
    phone: "電話",
    url: "URL",
    consent: "同意",
    info: "見出し・説明",
    section: "見出し・説明",
    partition: "パーテーション",
  };

  function uid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return `local-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function ensureFlowKey(question) {
    question.settings = question.settings || {};
    question.settings.flow = question.settings.flow || {};
    if (!question.settings.flow.key) question.settings.flow.key = `flow-${uid()}`;
    return question.settings.flow.key;
  }

  function questionTargetRef(question) {
    if (!question) return "";
    return question.settings?.flow?.key || (question.id != null ? String(question.id) : ensureFlowKey(question));
  }

  function ensureFormSettings() {
    state.selected.settings = state.selected.settings || {};
    return state.selected.settings;
  }

  function appendTextWithBreaks(parent, text) {
    String(text || "").split("\n").forEach((line, lineIndex) => {
      if (lineIndex > 0) parent.appendChild(document.createElement("br"));
      const parts = line.split(/(\*\*[^*]+\*\*)/g);
      parts.forEach((part) => {
        if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
          const strong = document.createElement("strong");
          strong.textContent = part.slice(2, -2);
          parent.appendChild(strong);
        } else if (part) {
          parent.appendChild(document.createTextNode(part));
        }
      });
    });
  }

  function normalizeRichColor(value) {
    const color = String(value || "").trim();
    const hex = color.match(/^#[0-9a-fA-F]{6}$/);
    if (hex) return color.toLowerCase();
    const rgb = color.match(/^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})(?:\s*,\s*(?:0|1|0?\.\d+))?\s*\)$/i);
    if (!rgb) return "";
    return `#${rgb.slice(1, 4).map((part) => {
      const value = Math.max(0, Math.min(255, Number(part) || 0));
      return value.toString(16).padStart(2, "0");
    }).join("")}`;
  }

  function richImageSettings(node) {
    const style = String(node.getAttribute("style") || "");
    const widthMatch = style.match(/(?:^|;)\s*width\s*:\s*(\d{1,3})%\s*(?:;|$)/i);
    const rawWidth = node.getAttribute("data-width") || (widthMatch ? widthMatch[1] : "100");
    const width = Math.max(10, Math.min(100, Number(rawWidth) || 100));
    const rawAlign = (node.getAttribute("data-align") || "").toLowerCase();
    let align = ["left", "center", "right"].includes(rawAlign) ? rawAlign : "left";
    if (!rawAlign) {
      const normalizedStyle = style.toLowerCase();
      const leftAuto = /margin-left\s*:\s*auto/.test(normalizedStyle);
      const rightAuto = /margin-right\s*:\s*auto/.test(normalizedStyle);
      if (leftAuto && rightAuto) align = "center";
      else if (leftAuto) align = "right";
    }
    return { width, align };
  }

  function applyRichImageStyle(img, width = 100, align = "left") {
    const normalizedWidth = Math.max(10, Math.min(100, Number(width) || 100));
    const normalizedAlign = ["left", "center", "right"].includes(align) ? align : "left";
    img.dataset.width = String(normalizedWidth);
    img.dataset.align = normalizedAlign;
    img.style.width = `${normalizedWidth}%`;
    img.style.height = "auto";
    img.style.maxWidth = "100%";
    img.style.display = "block";
    if (normalizedAlign === "center") {
      img.style.marginLeft = "auto";
      img.style.marginRight = "auto";
    } else if (normalizedAlign === "right") {
      img.style.marginLeft = "auto";
      img.style.marginRight = "0";
    } else {
      img.style.marginLeft = "0";
      img.style.marginRight = "auto";
    }
  }

  function selectionInside(editor) {
    const selection = window.getSelection();
    if (!selection || !selection.rangeCount) return null;
    const range = selection.getRangeAt(0);
    if (!editor.contains(range.commonAncestorContainer)) return null;
    return range;
  }

  function insertNodeAtSelection(editor, node) {
    editor.focus();
    const range = selectionInside(editor);
    if (!range) {
      editor.appendChild(node);
      return;
    }
    range.deleteContents();
    range.insertNode(node);
    range.setStartAfter(node);
    range.collapse(true);
    const selection = window.getSelection();
    selection.removeAllRanges();
    selection.addRange(range);
  }

  function selectedRichText(editor) {
    const range = selectionInside(editor);
    return range ? String(range.toString() || "").trim() : "";
  }

  function sanitizeRichHtml(html) {
    const template = document.createElement("template");
    template.innerHTML = String(html || "");
    const fragment = document.createDocumentFragment();

    function walk(node, parent) {
      if (node.nodeType === Node.TEXT_NODE) {
        parent.appendChild(document.createTextNode(node.textContent || ""));
        return;
      }
      if (node.nodeType !== Node.ELEMENT_NODE) return;
      const tag = node.tagName.toLowerCase();
      if (tag === "script" || tag === "style") return;
      let nextParent = parent;
      if (tag === "strong" || tag === "b") {
        nextParent = document.createElement("strong");
        parent.appendChild(nextParent);
      } else if (tag === "u") {
        nextParent = document.createElement("u");
        parent.appendChild(nextParent);
      } else if (tag === "span" || tag === "font") {
        const styleMatch = String(node.getAttribute("style") || "").match(/(?:^|;)\s*color\s*:\s*([^;]+)\s*(?:;|$)/i);
        const attrColor = node.getAttribute("color") || "";
        const color = normalizeRichColor(styleMatch ? styleMatch[1] : attrColor);
        const span = document.createElement("span");
        if (color) span.style.color = color;
        if (span.getAttribute("style")) {
          nextParent = span;
          parent.appendChild(span);
        }
      } else if (tag === "a") {
        const href = node.getAttribute("href") || "";
        if (/^(https?:\/\/|mailto:)/i.test(href)) {
          const a = document.createElement("a");
          a.href = href;
          a.target = "_blank";
          a.rel = "noopener noreferrer";
          nextParent = a;
          parent.appendChild(a);
        }
      } else if (tag === "img") {
        const src = node.getAttribute("src") || "";
        if (/^\/tools\/powervote\/uploads\/\d+\/[A-Za-z0-9_.-]+$/.test(src)) {
          const img = document.createElement("img");
          img.src = src;
          img.alt = node.getAttribute("alt") || "";
          const settings = richImageSettings(node);
          applyRichImageStyle(img, settings.width, settings.align);
          parent.appendChild(img);
        }
        return;
      } else if (tag === "br") {
        parent.appendChild(document.createElement("br"));
        return;
      } else if (tag === "div" || tag === "p") {
        const styleMatch = String(node.getAttribute("style") || "").match(/(?:^|;)\s*text-align\s*:\s*(left|center|right)\s*(?:;|$)/i);
        const align = styleMatch ? styleMatch[1].toLowerCase() : (node.getAttribute("align") || "").toLowerCase();
        if (parent.childNodes.length) parent.appendChild(document.createElement("br"));
        if (["left", "center", "right"].includes(align)) {
          const div = document.createElement("div");
          div.style.textAlign = align;
          nextParent = div;
          parent.appendChild(div);
        }
      }
      Array.from(node.childNodes).forEach((child) => walk(child, nextParent));
    }

    Array.from(template.content.childNodes).forEach((node) => walk(node, fragment));
    const wrap = document.createElement("div");
    wrap.appendChild(fragment.cloneNode(true));
    return wrap.innerHTML;
  }

  function plainTextFromHtml(html) {
    const div = document.createElement("div");
    div.innerHTML = sanitizeRichHtml(html);
    return div.innerText || div.textContent || "";
  }

  async function uploadRichImage(file) {
    if (!state.selected) throw new Error("フォームを選択してください。");
    const formData = new FormData();
    formData.append("image", file);
    const response = await fetch(`${apiBase}/forms/${state.selected.id}/images`, {
      method: "POST",
      body: formData,
      headers: { Accept: "application/json" },
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || "画像アップロードに失敗しました。");
    }
    return payload.url;
  }

  function richDescriptionHtml(source) {
    return source?.settings?.description_html || "";
  }

  function baseTextColor(form = state.selected) {
    return normalizeRichColor(form?.theme?.base_text || "#0f172a") || "#0f172a";
  }

  function applyFormattedText(element, text, source = {}, inheritedColor = "") {
    element.replaceChildren();
    element.classList.add("pv-rich-text");
    const html = richDescriptionHtml(source);
    const baseColor = normalizeRichColor(inheritedColor || source?.theme?.base_text || "");
    element.style.color = baseColor || "";
    if (html) {
      element.innerHTML = sanitizeRichHtml(html);
      return;
    }
    appendTextWithBreaks(element, text);
    const format = source?.settings?.description_format || {};
    element.style.fontWeight = format.bold ? "800" : "";
    element.style.color = normalizeRichColor(format.color) || baseColor || "";
  }

  function setQuestionSetting(question, key, value) {
    question.settings = question.settings || {};
    question.settings[key] = value;
  }

  function setDirty(dirty) {
    state.dirty = dirty;
    $("pv-save-state").textContent = dirty ? "未保存" : "保存済み";
  }

  async function api(path, options) {
    const response = await fetch(apiBase + path, {
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      ...options,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.error || "通信に失敗しました。");
    }
    return payload;
  }

  function questionTemplate(type) {
    const base = {
      id: null,
      type,
      title: "",
      description: "",
      required: false,
      sort_order: state.selected ? state.selected.questions.length : 0,
      options: [],
      settings: {},
    };
    if (type === "short_text") {
      base.title = "お名前または回答";
      base.settings = { placeholder: "", min_length: "", max_length: 200 };
    } else if (type === "long_text") {
      base.title = "補足事項";
      base.settings = { placeholder: "", min_length: "", max_length: 2000 };
    } else if (type === "single_choice" || type === "multi_choice") {
      base.title = type === "single_choice" ? "選択してください" : "該当するものを選んでください";
      base.options = [{ id: uid(), label: "選択肢1" }, { id: uid(), label: "選択肢2" }];
    } else if (type === "rating") {
      base.title = "評価してください";
      base.settings = { min: 1, max: 5, min_label: "低い", max_label: "高い" };
    } else if (type === "number") {
      base.title = "数値を入力してください";
      base.settings = { min: "", max: "", integer_only: false };
    } else if (type === "date") {
      base.title = "日付を選択してください";
    } else if (type === "time") {
      base.title = "時刻を選択してください";
    } else if (type === "email") {
      base.title = "メールアドレス";
      base.settings = { placeholder: "name@example.com", max_length: 240 };
    } else if (type === "phone") {
      base.title = "電話番号";
      base.settings = { placeholder: "090-0000-0000", max_length: 80 };
    } else if (type === "url") {
      base.title = "関連URL";
      base.settings = { placeholder: "https://example.com", max_length: 500 };
    } else if (type === "consent") {
      base.title = "内容に同意します";
      base.required = true;
    } else if (type === "info") {
      base.title = "見出し";
      base.description = "説明文";
    } else if (type === "partition") {
      base.title = "次のページ";
      base.settings = { label: "次のページ" };
    }
    ensureFlowKey(base);
    return base;
  }

  function formTemplates(name) {
    if (name === "attendance") {
      return [
        { ...questionTemplate("single_choice"), title: "参加できますか？", required: true, options: [
          { id: "yes", label: "参加する" },
          { id: "no", label: "参加しない" },
          { id: "maybe", label: "未定" },
        ] },
        { ...questionTemplate("short_text"), title: "参加者名", required: true },
        { ...questionTemplate("long_text"), title: "連絡事項", required: false },
      ];
    }
    if (name === "consent") {
      return [
        { ...questionTemplate("info"), title: "確認事項", description: "内容を読んだうえで回答してください。" },
        { ...questionTemplate("consent"), title: "上記の内容を確認し、同意します", required: true },
        { ...questionTemplate("short_text"), title: "氏名", required: true },
      ];
    }
    return [
      { ...questionTemplate("rating"), title: "全体の満足度", required: true },
      { ...questionTemplate("single_choice"), title: "また利用したいですか？", required: true, options: [
        { id: "yes", label: "利用したい" },
        { id: "no", label: "利用しない" },
        { id: "unknown", label: "まだ分からない" },
      ] },
      { ...questionTemplate("long_text"), title: "改善してほしいこと", required: false },
    ];
  }

  function statusLabel(status) {
    return { draft: "下書き", open: "受付中", closed: "締切" }[status] || status;
  }

  function typeLabel(type) {
    return TYPES[type] || type;
  }

  function renderFormList() {
    const list = $("pv-form-list");
    list.replaceChildren();
    $("pv-form-count").textContent = String(state.forms.length);
    state.forms.forEach((form) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "pv-form-item" + (state.selected && state.selected.id === form.id ? " active" : "") + (form.is_shared_to_me ? " shared" : "");
      button.innerHTML = "<strong></strong><span></span>";
      button.querySelector("strong").textContent = form.title;
      button.querySelector("span").textContent = form.is_shared_to_me
        ? `${statusLabel(form.status)} / ${form.owner_display_name || form.owner_user_id}さんから共有`
        : `${statusLabel(form.status)} / 回答 ${form.response_count || 0}件`;
      button.addEventListener("click", () => selectForm(form.id));
      list.appendChild(button);
    });
  }

  function syncEditorInputs() {
    if (!state.selected) return;
    const form = state.selected;
    $("pv-title").value = form.title || "";
    $("pv-description").value = form.description || "";
    $("pv-description").dataset.plainEdited = "0";
    $("pv-status").value = form.status || "draft";
    $("pv-identity-mode").value = form.identity_mode || "anonymous";
    $("pv-anonymous").checked = Boolean(form.is_anonymous);
    $("pv-cookie-limit").checked = Boolean(form.cookie_limit_enabled);
    $("pv-accent").value = (form.theme && form.theme.accent) || "#2563eb";
    $("pv-base-text").value = baseTextColor(form);
    $("pv-public-url").href = form.public_url;
    $("pv-public-url").textContent = form.public_url;
    $("pv-shared-editors").value = (form.shared_editors || form.settings?.shared_editors || []).join(",");
    $("pv-shared-editor-search").disabled = form.can_manage_shares === false;
    $("pv-editor-title").textContent = form.title || "PowerVote";
    $("pv-export-csv").href = `${apiBase}/forms/${form.id}/export.csv`;
    renderSharedEditors();
  }

  function readFormInputs() {
    if (!state.selected) return;
    state.selected.title = $("pv-title").value.trim();
    state.selected.description = $("pv-description").value;
    state.selected.status = $("pv-status").value;
    state.selected.identity_mode = $("pv-identity-mode").value;
    state.selected.is_anonymous = $("pv-anonymous").checked;
    state.selected.cookie_limit_enabled = $("pv-cookie-limit").checked;
    state.selected.theme = {
      ...(state.selected.theme || {}),
      accent: $("pv-accent").value,
      base_text: $("pv-base-text").value,
    };
    const settings = ensureFormSettings();
    if ($("pv-description").dataset.plainEdited === "1") {
      delete settings.description_html;
    }
    if (state.selected.can_manage_shares !== false) {
      settings.shared_editors = $("pv-shared-editors").value
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);
      state.selected.shared_editors = settings.shared_editors;
    }
  }

  function normalizeQuestionType(question) {
    if (question.type === "section") question.type = "info";
    return question;
  }

  function isAnswerlessQuestion(question) {
    return question.type === "info" || question.type === "section" || question.type === "partition";
  }

  function renderQuestionList() {
    const list = $("pv-question-list");
    list.replaceChildren();
    if (!state.selected) return;
    state.selected.questions = (state.selected.questions || []).map(normalizeQuestionType);
    state.selected.questions.forEach((question, index) => {
      question.sort_order = index;
      if (question.type === "partition") {
        const card = document.createElement("article");
        card.className = "pv-question-card pv-partition-card";
        card.dataset.index = String(index);
        card.innerHTML = `
          <div class="pv-question-label"><em>${index + 1}</em><span>パーテーション</span></div>
          <div class="pv-partition-line"><span>ここから次のページ</span></div>
          <label><span>表示名</span><input data-field="title" type="text" maxlength="300"></label>
          <div class="pv-question-actions">
            <button type="button" data-action="up">上へ</button>
            <button type="button" data-action="down">下へ</button>
            <button type="button" data-action="delete" class="pv-danger">削除</button>
          </div>`;
        card.querySelector('[data-field="title"]').value = question.title || "次のページ";
        bindQuestionCard(card, question, index);
        list.appendChild(card);
        return;
      }
      const requiredLabel = question.required ? "回答必須" : "任意回答";
      const card = document.createElement("article");
      card.className = "pv-question-card";
      card.dataset.index = String(index);
      card.innerHTML = `
        <div class="pv-question-label"><em>${index + 1}</em><span>${typeLabel(question.type)}</span><strong>${requiredLabel}</strong></div>
        <div class="pv-question-top">
          <label><span>質問文</span><input data-field="title" type="text" maxlength="300"></label>
          <label><span>種類</span><select data-field="type">
            <option value="short_text">短文</option>
            <option value="long_text">長文</option>
            <option value="single_choice">単一選択</option>
            <option value="multi_choice">複数選択</option>
            <option value="number">数値</option>
            <option value="rating">評価</option>
            <option value="date">日付</option>
            <option value="time">時刻</option>
            <option value="email">メール</option>
            <option value="phone">電話</option>
            <option value="url">URL</option>
            <option value="consent">同意</option>
            <option value="info">見出し・説明</option>
            <option value="partition">パーテーション</option>
          </select></label>
          <label><span>回答</span><select data-field="required">
            <option value="false">任意回答</option>
            <option value="true">回答必須</option>
          </select></label>
        </div>
        <div class="pv-question-body">
          <label><span>補足説明</span><textarea data-field="description" rows="2"></textarea></label>
          <div class="pv-description-tools"></div>
          <div class="pv-dynamic"></div>
          <details>
            <summary>詳細JSON（上級者向け）</summary>
            <label><span>詳細データ</span><textarea data-field="settings-json" rows="4"></textarea></label>
          </details>
          <div class="pv-question-actions">
            <button type="button" data-action="add-after">下に追加</button>
            <button type="button" data-action="up">上へ</button>
            <button type="button" data-action="down">下へ</button>
            <button type="button" data-action="duplicate">複製</button>
            <button type="button" data-action="delete" class="pv-danger">削除</button>
          </div>
        </div>`;
      card.querySelector('[data-field="title"]').value = question.title || "";
      card.querySelector('[data-field="type"]').value = question.type;
      card.querySelector('[data-field="required"]').value = question.required ? "true" : "false";
      card.querySelector('[data-field="description"]').value = question.description || "";
      card.querySelector('[data-field="settings-json"]').value = JSON.stringify(question.settings || {}, null, 2);
      bindQuestionCard(card, question, index);
      renderDescriptionTools(card.querySelector(".pv-description-tools"), question);
      renderDynamicSettings(card.querySelector(".pv-dynamic"), question);
      list.appendChild(card);
    });
    renderPreview();
  }

  function bindQuestionCard(card, question, index) {
    card.querySelectorAll("[data-field]").forEach((input) => {
      input.addEventListener("input", () => {
        const field = input.dataset.field;
        if (field === "title") question.title = input.value;
        if (field === "description") {
          question.description = input.value;
          if (question.settings) delete question.settings.description_html;
        }
        if (field === "required") question.required = input.value === "true";
        if (field === "type") {
          const replacement = questionTemplate(input.value);
          replacement.id = question.id;
          replacement.title = question.title;
          replacement.description = question.description;
          replacement.required = question.required;
          replacement.settings = {
            ...replacement.settings,
            flow: { key: ensureFlowKey(question) },
            default_next: question.settings?.default_next || "",
          };
          state.selected.questions[index] = replacement;
          setDirty(true);
          renderQuestionList();
          return;
        }
        if (field === "settings-json") {
          try {
            question.settings = JSON.parse(input.value || "{}");
            input.setCustomValidity("");
          } catch (error) {
            input.setCustomValidity("入力形式を確認してください。");
          }
        }
        setDirty(true);
        renderPreview();
      });
    });
    card.querySelectorAll("[data-action]").forEach((button) => {
      button.addEventListener("click", () => {
        const questions = state.selected.questions;
        const action = button.dataset.action;
        if (action === "delete") questions.splice(index, 1);
        if (action === "add-after") questions.splice(index + 1, 0, questionTemplate("short_text"));
        if (action === "duplicate") {
          const copy = JSON.parse(JSON.stringify(question));
          copy.id = null;
          copy.title = `${copy.title} コピー`;
          copy.options = (copy.options || []).map((option) => ({ id: uid(), label: option.label }));
          copy.settings = {
            ...(copy.settings || {}),
            flow: { key: `flow-${uid()}` },
            default_next: "",
            branches: {},
          };
          questions.splice(index + 1, 0, copy);
        }
        if (action === "up" && index > 0) [questions[index - 1], questions[index]] = [questions[index], questions[index - 1]];
        if (action === "down" && index < questions.length - 1) [questions[index + 1], questions[index]] = [questions[index], questions[index + 1]];
        setDirty(true);
        renderQuestionList();
      });
    });
  }

  function openRichTextDialog({ title, text, html, onCommit }) {
    const overlay = document.createElement("div");
    overlay.className = "pv-rich-dialog";
    overlay.innerHTML = `
      <div class="pv-rich-dialog-card" role="dialog" aria-modal="true">
        <div class="pv-rich-dialog-head">
          <h2></h2>
          <button type="button" data-rich-close aria-label="閉じる">×</button>
        </div>
        <div class="pv-rich-toolbar">
          <button type="button" data-rich-bold><strong>B</strong></button>
          <button type="button" data-rich-underline><u>U</u></button>
          <button type="button" data-rich-link>リンク</button>
          <button type="button" data-rich-image>画像</button>
          <button type="button" data-rich-align="left">左</button>
          <button type="button" data-rich-align="center">中央</button>
          <button type="button" data-rich-align="right">右</button>
          <label><span>色</span><input type="color" data-rich-color value="#2563eb"></label>
          <span class="pv-rich-image-tools" data-rich-image-tools hidden>
            <label><span>画像幅</span><input type="range" min="10" max="100" value="100" data-rich-image-width></label>
            <button type="button" data-rich-image-align="left">画像左</button>
            <button type="button" data-rich-image-align="center">画像中央</button>
            <button type="button" data-rich-image-align="right">画像右</button>
          </span>
          <input type="file" data-rich-image-input accept="image/png,image/jpeg,image/gif,image/webp" hidden>
        </div>
        <div class="pv-rich-editor" contenteditable="true"></div>
        <div class="pv-rich-dialog-actions">
          <button type="button" data-rich-cancel>キャンセル</button>
          <button type="button" class="pv-primary" data-rich-save>反映</button>
        </div>
      </div>
    `;
    overlay.querySelector("h2").textContent = title;
    const editor = overlay.querySelector(".pv-rich-editor");
    const close = () => overlay.remove();
    editor.innerHTML = sanitizeRichHtml(html) || "";
    if (!editor.innerHTML && text) appendTextWithBreaks(editor, text);
    overlay.querySelector("[data-rich-bold]").addEventListener("click", () => {
      editor.focus();
      document.execCommand("bold", false);
    });
    overlay.querySelector("[data-rich-underline]").addEventListener("click", () => {
      editor.focus();
      document.execCommand("underline", false);
    });
    overlay.querySelector("[data-rich-link]").addEventListener("click", () => {
      const label = window.prompt("表示名を入力してください", selectedRichText(editor) || "");
      if (!label) return;
      const href = window.prompt("URLを入力してください", "https://");
      if (!href || !/^(https?:\/\/|mailto:)/i.test(href)) return;
      const link = document.createElement("a");
      link.href = href;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = label;
      insertNodeAtSelection(editor, link);
    });
    overlay.querySelectorAll("[data-rich-align]").forEach((button) => {
      button.addEventListener("click", () => {
        editor.focus();
        const command = { left: "justifyLeft", center: "justifyCenter", right: "justifyRight" }[button.dataset.richAlign];
        document.execCommand(command, false);
      });
    });
    const imageInput = overlay.querySelector("[data-rich-image-input]");
    const imageTools = overlay.querySelector("[data-rich-image-tools]");
    const imageWidth = overlay.querySelector("[data-rich-image-width]");
    let selectedImage = null;
    const selectImage = (img) => {
      if (selectedImage) selectedImage.classList.remove("pv-rich-image-selected");
      selectedImage = img;
      if (!selectedImage) {
        imageTools.hidden = true;
        return;
      }
      const settings = richImageSettings(selectedImage);
      applyRichImageStyle(selectedImage, settings.width, settings.align);
      selectedImage.classList.add("pv-rich-image-selected");
      imageWidth.value = String(settings.width);
      imageTools.hidden = false;
    };
    editor.addEventListener("click", (event) => {
      selectImage(event.target instanceof HTMLImageElement ? event.target : null);
    });
    imageWidth.addEventListener("input", () => {
      if (!selectedImage) return;
      applyRichImageStyle(selectedImage, imageWidth.value, selectedImage.dataset.align || "left");
    });
    overlay.querySelectorAll("[data-rich-image-align]").forEach((button) => {
      button.addEventListener("click", () => {
        if (!selectedImage) return;
        applyRichImageStyle(selectedImage, imageWidth.value, button.dataset.richImageAlign);
      });
    });
    overlay.querySelector("[data-rich-image]").addEventListener("click", () => imageInput.click());
    imageInput.addEventListener("change", async () => {
      const file = imageInput.files?.[0];
      if (!file) return;
      try {
        const url = await uploadRichImage(file);
        const img = document.createElement("img");
        img.src = url;
        img.alt = file.name || "";
        applyRichImageStyle(img, 100, "left");
        insertNodeAtSelection(editor, img);
        selectImage(img);
      } catch (error) {
        alert(error.message);
      } finally {
        imageInput.value = "";
      }
    });
    overlay.querySelector("[data-rich-color]").addEventListener("input", (event) => {
      editor.focus();
      document.execCommand("foreColor", false, event.target.value);
    });
    overlay.querySelector("[data-rich-close]").addEventListener("click", close);
    overlay.querySelector("[data-rich-cancel]").addEventListener("click", close);
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) close();
    });
    overlay.querySelector("[data-rich-save]").addEventListener("click", () => {
      const cleanHtml = sanitizeRichHtml(editor.innerHTML);
      onCommit({
        html: cleanHtml,
        text: plainTextFromHtml(cleanHtml),
      });
      close();
    });
    document.body.appendChild(overlay);
    editor.focus();
  }

  function renderDescriptionTools(container, question) {
    if (!container) return;
    container.className = "pv-format-row pv-description-tools";
    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "pv-secondary";
    edit.textContent = "詳細編集";
    edit.addEventListener("click", () => {
      openRichTextDialog({
        title: "補足説明を編集",
        text: question.description || "",
        html: richDescriptionHtml(question),
        onCommit: ({ html, text }) => {
          question.description = text;
          question.settings = question.settings || {};
          question.settings.description_html = html;
          setDirty(true);
          renderQuestionList();
        },
      });
    });
    container.appendChild(edit);
  }

  function renderDynamicSettings(container, question) {
    container.replaceChildren();
    if (question.type === "single_choice" || question.type === "multi_choice") {
      const wrap = document.createElement("div");
      wrap.className = "pv-options";
      (question.options || []).forEach((option, optionIndex) => {
        const row = document.createElement("div");
        row.className = "pv-option-row";
        const input = document.createElement("input");
        input.type = "text";
        input.value = option.label || "";
        input.addEventListener("input", () => {
          option.label = input.value;
          setDirty(true);
          renderPreview();
        });
        const remove = document.createElement("button");
        remove.type = "button";
        remove.textContent = "削除";
        remove.addEventListener("click", () => {
          question.options.splice(optionIndex, 1);
          setDirty(true);
          renderQuestionList();
        });
        row.append(input, remove);
        wrap.appendChild(row);
      });
      const add = document.createElement("button");
      add.type = "button";
      add.className = "pv-secondary";
      add.textContent = "選択肢を追加";
      add.addEventListener("click", () => {
        question.options.push({ id: uid(), label: `選択肢${question.options.length + 1}` });
        setDirty(true);
        renderQuestionList();
      });
      container.append(wrap, add);
      if (question.type === "single_choice") {
        container.appendChild(branchEditor(question));
      }
    } else if (question.type === "rating") {
      container.appendChild(settingsGrid(question, ["min", "max", "min_label", "max_label"]));
    } else if (question.type === "number") {
      container.appendChild(settingsGrid(question, ["min", "max", "integer_only"]));
    } else if (["short_text", "long_text", "email", "phone", "url"].includes(question.type)) {
      container.appendChild(settingsGrid(question, ["placeholder", "min_length", "max_length"]));
    }
  }

  function branchEditor(question) {
    question.settings = question.settings || {};
    question.settings.branches = question.settings.branches || {};
    const panel = document.createElement("div");
    panel.className = "pv-branch-panel";
    const title = document.createElement("div");
    title.className = "pv-branch-title";
    title.textContent = "回答によって次を変える";
    panel.appendChild(title);

    (question.options || []).forEach((option) => {
      const row = document.createElement("label");
      row.className = "pv-branch-row";
      const span = document.createElement("span");
      span.textContent = option.label || "選択肢";
      const select = branchSelect(question.settings.branches[option.id] || "");
      select.addEventListener("change", () => {
        if (select.value) {
          question.settings.branches[option.id] = select.value;
        } else {
          delete question.settings.branches[option.id];
        }
        setDirty(true);
      });
      row.append(span, select);
      panel.appendChild(row);
    });

    const defaultRow = document.createElement("label");
    defaultRow.className = "pv-branch-row";
    const defaultLabel = document.createElement("span");
    defaultLabel.textContent = "それ以外";
    const defaultSelect = branchSelect(question.settings.default_next || "");
    defaultSelect.addEventListener("change", () => {
      question.settings.default_next = defaultSelect.value;
      setDirty(true);
    });
    defaultRow.append(defaultLabel, defaultSelect);
    panel.appendChild(defaultRow);
    return panel;
  }

  function branchSelect(value) {
    const select = document.createElement("select");
    const choices = [
      ["", "次の質問へ"],
      ["__end__", "ここで終了"],
      ...state.selected.questions
        .map((question, index) => [questionTargetRef(question), `${index + 1}. ${question.title || typeLabel(question.type)}`]),
    ];
    choices.forEach(([optionValue, label]) => {
      const option = document.createElement("option");
      option.value = optionValue;
      option.textContent = label;
      option.selected = String(value) === String(optionValue);
      select.appendChild(option);
    });
    return select;
  }

  function settingsGrid(question, keys) {
    const grid = document.createElement("div");
    grid.className = "pv-settings-grid";
    const labels = {
      placeholder: "入力例",
      min_length: "最小文字数",
      max_length: "最大文字数",
      min: "最小値",
      max: "最大値",
      min_label: "最小側ラベル",
      max_label: "最大側ラベル",
      integer_only: "整数のみ",
    };
    keys.forEach((key) => {
      const label = document.createElement("label");
      const span = document.createElement("span");
      span.textContent = labels[key] || key;
      const input = document.createElement("input");
      input.type = key === "integer_only" ? "checkbox" : ["min", "max", "min_length", "max_length"].includes(key) ? "number" : "text";
      if (key === "integer_only") {
        input.checked = Boolean(question.settings && question.settings[key]);
      } else {
        input.value = question.settings && question.settings[key] != null ? question.settings[key] : "";
      }
      input.addEventListener("input", () => {
        question.settings = question.settings || {};
        question.settings[key] = key === "integer_only" ? input.checked : input.value;
        setDirty(true);
        renderPreview();
      });
      label.append(span, input);
      grid.appendChild(label);
    });
    return grid;
  }

  function renderPreview() {
    const wrap = $("pv-live-preview");
    if (!wrap || !state.selected) return;
    readFormInputs();
    wrap.replaceChildren();
    const baseColor = baseTextColor();
    wrap.style.setProperty("--pv-base-text", baseColor);
    $("pv-preview-count").textContent = `${state.selected.questions.filter((q) => !isAnswerlessQuestion(q)).length}問`;

    const titleBlock = document.createElement("section");
    titleBlock.className = "pv-preview-title";
    titleBlock.innerHTML = "<h3></h3><p></p>";
    titleBlock.querySelector("h3").textContent = state.selected.title || "タイトル";
    applyFormattedText(
      titleBlock.querySelector("p"),
      state.selected.description || "説明文",
      state.selected,
      baseColor,
    );
    wrap.appendChild(titleBlock);

    state.selected.questions.forEach((question) => {
      const block = document.createElement("section");
      block.className = "pv-preview-block";
      const heading = document.createElement("h4");
      heading.style.color = baseColor;
      heading.textContent = question.title || (question.type === "info" ? "見出し" : "質問文");
      if (question.required && question.type !== "info") {
        const required = document.createElement("span");
        required.className = "pv-preview-required";
        required.textContent = " *";
        heading.appendChild(required);
      }
      block.appendChild(heading);
      if (question.description) {
        const desc = document.createElement("p");
        applyFormattedText(desc, question.description, question, baseColor);
        block.appendChild(desc);
      }
      if (question.type === "partition") {
        block.classList.add("pv-preview-partition");
        heading.textContent = question.title || "次のページ";
        wrap.appendChild(block);
        return;
      }
      if (question.type === "info" || question.type === "section") {
        wrap.appendChild(block);
        return;
      }
      if (["single_choice", "multi_choice"].includes(question.type)) {
        (question.options || []).forEach((option) => {
          const row = document.createElement("div");
          row.className = "pv-preview-option";
          row.style.color = baseColor;
          row.innerHTML = `<span class="pv-preview-dot ${question.type === "multi_choice" ? "square" : ""}"></span><span></span>`;
          row.querySelector("span:last-child").textContent = option.label || "選択肢";
          block.appendChild(row);
        });
      } else if (question.type === "rating") {
        const fake = document.createElement("div");
        fake.className = "pv-preview-fake";
        fake.textContent = `${question.settings?.min || 1} から ${question.settings?.max || 5} で選択`;
        block.appendChild(fake);
      } else if (question.type === "consent") {
        const row = document.createElement("div");
        row.className = "pv-preview-option";
        row.style.color = baseColor;
        row.innerHTML = '<span class="pv-preview-dot square"></span><span>同意する</span>';
        block.appendChild(row);
      } else {
        const fake = document.createElement("div");
        fake.className = "pv-preview-fake";
        fake.textContent = typeLabel(question.type);
        block.appendChild(fake);
      }
      wrap.appendChild(block);
    });
  }

  async function loadForms() {
    const payload = await api("/forms");
    state.forms = payload.forms || [];
    renderFormList();
    if (!state.selected && state.forms.length) await selectForm(state.forms[0].id);
  }

  async function selectForm(id) {
    if (state.dirty && !confirm("未保存の変更があります。移動しますか？")) return;
    const payload = await api(`/forms/${id}`);
    state.selected = payload.form;
    $("pv-empty-state").classList.add("hidden");
    $("pv-editor").classList.remove("hidden");
    state.view = "builder";
    syncEditorInputs();
    renderQuestionList();
    renderFormList();
    switchView("builder");
    setDirty(false);
  }

  async function refreshSelectedFormFromServer(id) {
    if (!state.selected || String(state.selected.id) !== String(id) || state.dirty) return;
    const payload = await api(`/forms/${id}`);
    state.selected = payload.form;
    state.forms = state.forms.map((form) => (String(form.id) === String(id) ? payload.form : form));
    syncEditorInputs();
    renderQuestionList();
    renderFormList();
    if (state.view === "results") loadResults().catch((error) => alert(error.message));
    setDirty(false);
  }

  async function createForm() {
    const payload = await api("/forms", { method: "POST", body: JSON.stringify({ title: "新しいPowerVote" }) });
    state.forms.unshift(payload.form);
    state.selected = payload.form;
    $("pv-empty-state").classList.add("hidden");
    $("pv-editor").classList.remove("hidden");
    syncEditorInputs();
    renderQuestionList();
    renderFormList();
    switchView("builder");
    setDirty(false);
  }

  async function saveForm() {
    if (!state.selected) return;
    readFormInputs();
    const payload = await api(`/forms/${state.selected.id}`, { method: "PUT", body: JSON.stringify(state.selected) });
    state.selected = payload.form;
    await loadForms();
    syncEditorInputs();
    renderQuestionList();
    setDirty(false);
  }

  async function deleteForm() {
    if (!state.selected || !confirm("このPowerVoteを削除します。回答も消えます。")) return;
    await api(`/forms/${state.selected.id}`, { method: "DELETE" });
    state.selected = null;
    setDirty(false);
    $("pv-editor").classList.add("hidden");
    $("pv-empty-state").classList.remove("hidden");
    await loadForms();
  }

  async function loadResults() {
    if (!state.selected) return;
    const payload = await api(`/forms/${state.selected.id}/results`);
    $("pv-response-count").textContent = String((payload.responses || []).length);
    renderSummary(payload.summary || []);
    renderResponses(payload.responses || [], payload.form.questions || []);
  }

  function renderSummary(summary) {
    const wrap = $("pv-results-summary");
    wrap.replaceChildren();
    summary.forEach((item) => {
      const card = document.createElement("article");
      card.className = "pv-result-card";
      const title = document.createElement("h3");
      title.textContent = item.title;
      card.appendChild(title);
      const counts = item.counts || {};
      const total = Object.values(counts).reduce((sum, value) => sum + value, 0) || 1;
      if (Object.keys(counts).length) {
        Object.entries(counts).forEach(([label, count]) => {
          const row = document.createElement("div");
          row.className = "pv-bar";
          row.innerHTML = '<span></span><div class="pv-bar-track"><div class="pv-bar-fill"></div></div><strong></strong>';
          row.querySelector("span").textContent = label;
          row.querySelector(".pv-bar-fill").style.width = `${Math.round((count / total) * 100)}%`;
          row.querySelector("strong").textContent = String(count);
          card.appendChild(row);
        });
      } else if (item.number) {
        const p = document.createElement("p");
        p.textContent = `件数 ${item.number.count} / 平均 ${item.number.average.toFixed(2)} / 最小 ${item.number.min} / 最大 ${item.number.max}`;
        card.appendChild(p);
      } else if (item.texts && item.texts.length) {
        item.texts.slice(0, 5).forEach((text) => {
          const p = document.createElement("p");
          p.textContent = text;
          card.appendChild(p);
        });
      } else {
        const p = document.createElement("p");
        p.textContent = "回答はまだありません。";
        card.appendChild(p);
      }
      wrap.appendChild(card);
    });
  }

  function renderResponses(responses, questions) {
    const wrap = $("pv-response-list");
    wrap.replaceChildren();
    responses.slice(0, 50).forEach((response) => {
      const card = document.createElement("article");
      card.className = "pv-response-card";
      const h3 = document.createElement("h3");
      h3.textContent = response.submitted_at || "submitted";
      card.appendChild(h3);
      questions.filter((q) => !isAnswerlessQuestion(q)).forEach((question) => {
        const p = document.createElement("p");
        const value = response.answers ? response.answers[String(question.id)] : "";
        p.textContent = `${question.title}: ${formatAnswer(question, value)}`;
        card.appendChild(p);
      });
      wrap.appendChild(card);
    });
  }

  function formatAnswer(question, value) {
    if (Array.isArray(value)) return value.map((item) => optionLabel(question, item)).join(", ");
    if (question.type === "single_choice") return optionLabel(question, value);
    if (value === true) return "同意";
    if (value === false) return "未同意";
    return String(value == null ? "" : value);
  }

  function optionLabel(question, id) {
    const option = (question.options || []).find((item) => String(item.id) === String(id));
    return option ? option.label : String(id == null ? "" : id);
  }

  function sharedEditorDetails() {
    if (Array.isArray(state.selected.shared_editor_details)) return state.selected.shared_editor_details;
    const details = [];
    const byUsername = new Map();
    (state.selected.shared_editors || state.selected.settings?.shared_editors || []).forEach((username) => {
      if (!byUsername.has(username)) byUsername.set(username, { username, name: username });
    });
    details.push(...byUsername.values());
    state.selected.shared_editor_details = details;
    return details;
  }

  function syncSharedEditorHidden() {
    const usernames = sharedEditorDetails().map((user) => user.username);
    $("pv-shared-editors").value = usernames.join(",");
    state.selected.shared_editors = usernames;
    ensureFormSettings().shared_editors = usernames;
  }

  function renderSharedEditors() {
    if (!state.selected) return;
    const list = $("pv-shared-editor-list");
    list.replaceChildren();
    sharedEditorDetails().forEach((user) => {
      const pill = document.createElement("span");
      pill.className = "pv-shared-editor-pill";
      const name = document.createElement("strong");
      name.textContent = user.name || user.username;
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "×";
      remove.disabled = state.selected.can_manage_shares === false;
      remove.addEventListener("click", () => {
        state.selected.shared_editor_details = sharedEditorDetails().filter((item) => item.username !== user.username);
        syncSharedEditorHidden();
        renderSharedEditors();
        setDirty(true);
      });
      pill.append(name, remove);
      list.appendChild(pill);
    });
    syncSharedEditorHidden();
  }

  function addSharedEditor(user) {
    state.selected.shared_editor_details = sharedEditorDetails();
    if (!state.selected.shared_editor_details.some((item) => item.username === user.username)) {
      state.selected.shared_editor_details.push(user);
      syncSharedEditorHidden();
      renderSharedEditors();
      setDirty(true);
    }
  }

  async function searchSharedEditors(query) {
    const results = $("pv-shared-editor-results");
    results.replaceChildren();
    if (!query.trim() || !state.selected || state.selected.can_manage_shares === false) return;
    const payload = await api(`/users?query=${encodeURIComponent(query.trim())}`);
    (payload.users || []).forEach((user) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = user.name || user.username;
      button.addEventListener("click", () => {
        addSharedEditor(user);
        $("pv-shared-editor-search").value = "";
        results.replaceChildren();
      });
      results.appendChild(button);
    });
  }

  function switchView(view) {
    state.view = view;
    $("pv-builder-view").classList.toggle("hidden", view !== "builder");
    $("pv-results-view").classList.toggle("hidden", view !== "results");
    $("pv-builder-tab").classList.toggle("active", view === "builder");
    $("pv-results-tab").classList.toggle("active", view === "results");
    if (view === "results") loadResults().catch((error) => alert(error.message));
  }

  function setupBuilderActionPanel() {
    const actions = document.querySelector(".pv-builder-actions");
    const questionList = $("pv-question-list");
    if (!actions || !questionList || document.querySelector(".pv-builder-action-panel")) return;

    if (!actions.querySelector('[data-add-question="partition"]')) {
      const partition = document.createElement("button");
      partition.type = "button";
      partition.dataset.addQuestion = "partition";
      partition.textContent = "パーテーション";
      actions.appendChild(partition);
    }

    const panel = document.createElement("aside");
    panel.className = "pv-builder-action-panel";
    const head = document.createElement("div");
    head.className = "pv-action-panel-head";
    head.innerHTML = `
      <strong>質問を追加</strong>
      <label><span>表示</span><select id="pv-action-mode">
        <option value="dock">常に表示</option>
        <option value="popup">ポップアップ</option>
      </select></label>
    `;
    const toggle = document.createElement("button");
    toggle.id = "pv-action-popover-toggle";
    toggle.type = "button";
    toggle.className = "pv-primary";
    toggle.textContent = "質問を追加";
    actions.parentNode.insertBefore(panel, actions);
    panel.append(head, toggle, actions);

    const modeSelect = $("pv-action-mode");
    const savedMode = localStorage.getItem("powervote:action-mode") || "dock";
    modeSelect.value = savedMode === "popup" ? "popup" : "dock";
    applyActionMode(modeSelect.value);
    modeSelect.addEventListener("change", () => {
      localStorage.setItem("powervote:action-mode", modeSelect.value);
      applyActionMode(modeSelect.value);
    });
    toggle.addEventListener("click", () => {
      const nextOpen = !actions.classList.contains("open");
      if (nextOpen) positionActionPopover(panel, toggle, actions);
      actions.classList.toggle("open", nextOpen);
    });
    window.addEventListener("resize", () => {
      if (actions.classList.contains("open")) positionActionPopover(panel, toggle, actions);
    });
    window.addEventListener("scroll", () => {
      if (actions.classList.contains("open")) positionActionPopover(panel, toggle, actions);
    }, true);
    document.addEventListener("click", (event) => {
      if (!panel.contains(event.target)) actions.classList.remove("open");
    });
  }

  function positionActionPopover(panel, toggle, actions) {
    const toggleRect = toggle.getBoundingClientRect();
    const openUp = toggleRect.top > window.innerHeight - toggleRect.bottom;
    panel.classList.toggle("drop-up", openUp);
    actions.classList.toggle("drop-up", openUp);
  }

  function applyActionMode(mode) {
    root.classList.toggle("pv-actions-popover-mode", mode === "popup");
    root.classList.toggle("pv-actions-dock-mode", mode !== "popup");
    const actions = document.querySelector(".pv-builder-actions");
    actions?.classList.remove("open", "drop-up");
    document.querySelector(".pv-builder-action-panel")?.classList.remove("drop-up");
  }

  function bindGlobalEvents() {
    setupBuilderActionPanel();
    $("pv-create-form").addEventListener("click", () => createForm().catch((error) => alert(error.message)));
    $("pv-refresh").addEventListener("click", () => loadForms().catch((error) => alert(error.message)));
    $("pv-save").addEventListener("click", () => saveForm().catch((error) => alert(error.message)));
    $("pv-delete-form").addEventListener("click", () => deleteForm().catch((error) => alert(error.message)));
    $("pv-preview-form").addEventListener("click", async () => {
      if (!state.selected) return;
      const previewWindow = window.open("about:blank", "_blank");
      if (state.dirty) await saveForm();
      const url = `/tools/powervote/preview/${state.selected.id}?editor_window=1`;
      if (previewWindow) previewWindow.location = url;
      else window.location.href = url;
    });
    $("pv-template-menu").addEventListener("click", () => $("pv-template-modal").classList.remove("hidden"));
    $("pv-flow-editor").addEventListener("click", async () => {
      if (!state.selected) return;
      const flowWindow = window.open("about:blank", "_blank");
      if (state.dirty) await saveForm();
      const url = `/tools/powervote/flow/${state.selected.id}?editor_window=1`;
      if (flowWindow) flowWindow.location = url;
      else window.location.href = url;
    });
    $("pv-builder-tab").addEventListener("click", () => switchView("builder"));
    $("pv-results-tab").addEventListener("click", () => switchView("results"));
    $("pv-load-results").addEventListener("click", () => loadResults().catch((error) => alert(error.message)));
    window.addEventListener("message", (event) => {
      if (event.origin !== window.location.origin || event.data?.type !== "powervote:form-saved") return;
      refreshSelectedFormFromServer(event.data.formId).catch((error) => alert(error.message));
    });
    window.addEventListener("storage", (event) => {
      if (event.key !== "powervote:last-saved" || !event.newValue) return;
      try {
        const payload = JSON.parse(event.newValue);
        if (payload.type === "powervote:form-saved") {
          refreshSelectedFormFromServer(payload.formId).catch((error) => alert(error.message));
        }
      } catch (error) {
        // Ignore unrelated or malformed storage events.
      }
    });

    document.querySelectorAll("[data-template]").forEach((button) => {
      button.addEventListener("click", () => {
        if (!state.selected) return;
        if (state.selected.questions.length && !confirm("現在の質問をテンプレートで置き換えます。")) return;
        state.selected.questions = formTemplates(button.dataset.template);
        $("pv-template-modal").classList.add("hidden");
        setDirty(true);
        renderQuestionList();
      });
    });

    document.querySelectorAll("[data-add-question]").forEach((button) => {
      button.addEventListener("click", () => {
        if (!state.selected) return;
        state.selected.questions.push(questionTemplate(button.dataset.addQuestion));
        setDirty(true);
        renderQuestionList();
      });
    });

    $("pv-description-edit-rich").addEventListener("click", () => {
      if (!state.selected) return;
      openRichTextDialog({
        title: "説明文を編集",
        text: state.selected.description || "",
        html: richDescriptionHtml(state.selected),
        onCommit: ({ html, text }) => {
          state.selected.description = text;
          $("pv-description").value = text;
          $("pv-description").dataset.plainEdited = "0";
          const settings = ensureFormSettings();
          settings.description_html = html;
          setDirty(true);
          renderPreview();
        },
      });
    });

    let sharedSearchTimer = null;
    $("pv-shared-editor-search").addEventListener("input", () => {
      clearTimeout(sharedSearchTimer);
      const query = $("pv-shared-editor-search").value;
      sharedSearchTimer = setTimeout(() => {
        searchSharedEditors(query).catch((error) => alert(error.message));
      }, 180);
    });

    [
      "pv-title",
      "pv-description",
      "pv-status",
      "pv-identity-mode",
      "pv-anonymous",
      "pv-cookie-limit",
      "pv-accent",
      "pv-base-text",
    ].forEach((id) => {
      $(id).addEventListener("input", () => {
        if (id === "pv-description") $("pv-description").dataset.plainEdited = "1";
        readFormInputs();
        if (id !== "pv-description") syncEditorInputs();
        setDirty(true);
        renderPreview();
      });
    });

    $("pv-copy-url").addEventListener("click", async () => {
      if (!state.selected) return;
      await navigator.clipboard.writeText(state.selected.public_url);
      alert("URLをコピーしました。");
    });
    $("pv-regenerate-token").addEventListener("click", async () => {
      if (!state.selected || !confirm("公開URLを再発行します。古いURLは使えなくなります。")) return;
      const payload = await api(`/forms/${state.selected.id}/regenerate-token`, { method: "POST" });
      state.selected = payload.form;
      syncEditorInputs();
      renderPreview();
      setDirty(false);
    });
    $("pv-show-qr").addEventListener("click", async () => {
      if (!state.selected) return;
      const payload = await api(`/forms/${state.selected.id}/qr`);
      $("pv-qr-url").textContent = payload.url;
      $("pv-qr-image").src = payload.qr_data ? `data:image/png;base64,${payload.qr_data}` : "";
      $("pv-qr-modal").classList.remove("hidden");
    });
    $("pv-close-qr").addEventListener("click", () => $("pv-qr-modal").classList.add("hidden"));
    $("pv-close-template").addEventListener("click", () => $("pv-template-modal").classList.add("hidden"));
    $("pv-qr-modal").addEventListener("click", (event) => {
      if (event.target.id === "pv-qr-modal") $("pv-qr-modal").classList.add("hidden");
    });
    $("pv-template-modal").addEventListener("click", (event) => {
      if (event.target.id === "pv-template-modal") $("pv-template-modal").classList.add("hidden");
    });
  }

  bindGlobalEvents();
  loadForms().catch((error) => alert(error.message));
})();
