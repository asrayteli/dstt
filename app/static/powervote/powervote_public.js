(function () {
  const app = document.getElementById("pv-public-app");
  if (!app) return;

  const payloadEl = document.getElementById("pv-public-payload");
  const form = JSON.parse(payloadEl.textContent || "{}");
  const submitUrl = app.dataset.submitUrl;
  const alreadyAnswered = app.dataset.answered === "1";
  const previewMode = app.dataset.previewMode === "1";
  const formEl = document.getElementById("pv-public-form");
  const statusEl = document.getElementById("pv-public-status");
  let currentPage = 0;
  let pageNav = null;
  let prevButton = null;
  let nextButton = null;
  let submitButton = null;

  const accent = (form.theme && form.theme.accent) || "#2563eb";
  const baseText = normalizeRichColor(form.theme?.base_text || "#0f172a") || "#0f172a";
  app.style.setProperty("--pv-base-text", baseText);
  document.getElementById("pv-public-title").textContent = form.title || "PowerVote";
  applyFormattedText(
    document.getElementById("pv-public-description"),
    form.description || "",
    form,
    baseText,
  );
  document.getElementById("pv-public-header").style.background = accent;
  document.querySelector(".pv-public-submit")?.style.setProperty("background", accent);

  function showStatus(message, isError) {
    statusEl.textContent = message;
    statusEl.classList.remove("hidden");
    statusEl.style.background = isError ? "#fee2e2" : "#dcfce7";
    statusEl.style.color = isError ? "#991b1b" : "#166534";
  }

  function optionLabel(question, id) {
    const option = (question.options || []).find((item) => String(item.id) === String(id));
    return option ? option.label : id;
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
        const color = normalizeRichColor(styleMatch ? styleMatch[1] : node.getAttribute("color") || "");
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
    wrap.appendChild(fragment);
    return wrap.innerHTML;
  }

  function applyFormattedText(element, text, source = {}, inheritedColor = "") {
    element.replaceChildren();
    element.classList.add("pv-rich-text");
    const richHtml = source?.settings?.description_html || "";
    const color = normalizeRichColor(inheritedColor || source?.theme?.base_text || baseText);
    element.style.color = color || "";
    if (richHtml) {
      element.innerHTML = sanitizeRichHtml(richHtml);
      return;
    }
    appendTextWithBreaks(element, text);
    const format = source?.settings?.description_format || {};
    element.style.fontWeight = format.bold ? "800" : "";
    element.style.color = normalizeRichColor(format.color) || color || "";
  }

  function questionTitle(question) {
    const wrap = document.createElement("div");
    const h2 = document.createElement("h2");
    h2.textContent = question.title || "";
    if (question.required) {
      const required = document.createElement("span");
      required.className = "pv-required";
      required.textContent = " *";
      h2.appendChild(required);
    }
    wrap.appendChild(h2);
    if (question.description) {
      const p = document.createElement("p");
      applyFormattedText(p, question.description, question, baseText);
      wrap.appendChild(p);
    }
    return wrap;
  }

  function renderIdentity() {
    if (form.is_anonymous || form.identity_mode === "anonymous") return;
    const box = document.createElement("section");
    box.className = "pv-public-identity";
    const label = document.createElement("label");
    label.textContent = form.identity_mode === "required" ? "お名前 *" : "お名前";
    const input = document.createElement("input");
    input.name = "respondent_name";
    input.type = "text";
    input.required = form.identity_mode === "required";
    label.appendChild(input);
    box.appendChild(label);
    formEl.appendChild(box);
  }

  function renderQuestion(question) {
    if (question.type === "partition") {
      const partition = document.createElement("section");
      partition.className = "pv-public-partition hidden";
      partition.dataset.questionId = question.id;
      formEl.appendChild(partition);
      return;
    }
    if (question.type === "section" || question.type === "info") {
      const section = document.createElement("section");
      section.className = "pv-public-section";
      section.dataset.questionId = question.id;
      section.appendChild(questionTitle(question));
      formEl.appendChild(section);
      return;
    }

    const box = document.createElement("section");
    box.className = "pv-public-question";
    box.dataset.questionId = question.id;
    box.dataset.questionType = question.type;
    box.appendChild(questionTitle(question));

    const settings = question.settings || {};
    let control;

    if (question.type === "short_text" || question.type === "email" || question.type === "phone" || question.type === "url") {
      control = document.createElement("input");
      control.type = question.type === "email" ? "email" : question.type === "url" ? "url" : question.type === "phone" ? "tel" : "text";
      control.placeholder = settings.placeholder || "";
      control.maxLength = Number(settings.max_length || 5000);
      if (settings.min_length !== "" && settings.min_length != null) control.minLength = Number(settings.min_length);
      control.required = Boolean(question.required);
    } else if (question.type === "long_text") {
      control = document.createElement("textarea");
      control.placeholder = settings.placeholder || "";
      control.maxLength = Number(settings.max_length || 5000);
      if (settings.min_length !== "" && settings.min_length != null) control.minLength = Number(settings.min_length);
      control.required = Boolean(question.required);
    } else if (question.type === "number") {
      control = document.createElement("input");
      control.type = "number";
      if (settings.min !== "" && settings.min != null) control.min = settings.min;
      if (settings.max !== "" && settings.max != null) control.max = settings.max;
      if (settings.integer_only) control.step = "1";
      control.required = Boolean(question.required);
    } else if (question.type === "date") {
      control = document.createElement("input");
      control.type = "date";
      control.required = Boolean(question.required);
    } else if (question.type === "time") {
      control = document.createElement("input");
      control.type = "time";
      control.required = Boolean(question.required);
    } else if (question.type === "single_choice" || question.type === "multi_choice") {
      control = document.createElement("div");
      control.className = "pv-public-choice";
      (question.options || []).forEach((option) => {
        const label = document.createElement("label");
        const input = document.createElement("input");
        input.type = question.type === "single_choice" ? "radio" : "checkbox";
        input.name = `q_${question.id}`;
        input.value = option.id;
        input.required = question.type === "single_choice" && Boolean(question.required);
        label.append(input, document.createTextNode(option.label));
        control.appendChild(label);
      });
    } else if (question.type === "rating") {
      control = document.createElement("div");
      control.className = "pv-public-rating";
      const min = Number(settings.min || 1);
      const max = Number(settings.max || 5);
      for (let value = min; value <= max; value += 1) {
        const label = document.createElement("label");
        const input = document.createElement("input");
        input.type = "radio";
        input.name = `q_${question.id}`;
        input.value = String(value);
        input.required = Boolean(question.required);
        const edge = value === min && settings.min_label ? ` (${settings.min_label})` : value === max && settings.max_label ? ` (${settings.max_label})` : "";
        label.append(input, document.createTextNode(`${value}${edge}`));
        control.appendChild(label);
      }
    } else if (question.type === "consent") {
      const label = document.createElement("label");
      label.className = "pv-public-consent";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.required = Boolean(question.required);
      label.append(input, document.createTextNode(optionLabel(question, "同意します")));
      control = label;
    }

    if (control) {
      control.dataset.answerControl = "1";
      box.appendChild(control);
    }
    formEl.appendChild(box);
  }

  function answerValue(question) {
    const box = formEl.querySelector(`[data-question-id="${question.id}"]`);
    if (!box) return "";
    if (["short_text", "long_text", "number", "date", "time", "email", "phone", "url"].includes(question.type)) {
      return box.querySelector("[data-answer-control]")?.value || "";
    }
    if (question.type === "single_choice" || question.type === "rating") {
      return box.querySelector(`input[name="q_${question.id}"]:checked`)?.value || "";
    }
    if (question.type === "multi_choice") {
      return Array.from(box.querySelectorAll(`input[name="q_${question.id}"]:checked`)).map((input) => input.value);
    }
    if (question.type === "consent") {
      return Boolean(box.querySelector("input")?.checked);
    }
    return "";
  }

  function reachableQuestionIds() {
    const ids = new Set();
    const questions = form.questions || [];
    const indexById = new Map();
    questions.forEach((question, index) => {
      indexById.set(String(question.id), index);
      const key = question.settings && question.settings.flow ? question.settings.flow.key : "";
      if (key) indexById.set(String(key), index);
    });
    const manualFlow = form.settings && form.settings.flow_mode === "manual";
    const startTarget = form.settings ? form.settings.flow_start : "";
    if (manualFlow && !startTarget) return ids;
    if (startTarget === "__end__") return ids;
    let index = indexById.has(String(startTarget)) ? indexById.get(String(startTarget)) : 0;
    const seen = new Set();
    while (index >= 0 && index < questions.length && !seen.has(index)) {
      seen.add(index);
      const question = questions[index];
      ids.add(String(question.id));
      const settings = question.settings || {};
      const branches = settings.branches || {};
      const value = answerValue(question);
      const target = question.type === "single_choice" ? (branches[String(value)] || settings.default_next || "") : (settings.default_next || "");
      if (target === "__end__") break;
      if (target && indexById.has(String(target))) {
        index = indexById.get(String(target));
      } else if (manualFlow) {
        break;
      } else {
        index += 1;
      }
    }
    return ids;
  }

  function visiblePages(reachable) {
    const pages = [[]];
    (form.questions || []).forEach((question) => {
      if (!reachable.has(String(question.id))) return;
      if (question.type === "partition") {
        if (pages[pages.length - 1].length) pages.push([]);
        return;
      }
      if (question.type === "section" || question.type === "info") {
        pages[pages.length - 1].push({ id: String(question.id), section: true });
        return;
      }
      pages[pages.length - 1].push({ id: String(question.id), section: false });
    });
    return pages.filter((page) => page.length);
  }

  function pageHasQuestion(page, questionId) {
    return page.some((item) => item.id === String(questionId));
  }

  function validateCurrentPage() {
    const visibleControls = Array.from(
      formEl.querySelectorAll(".pv-public-question:not(.hidden) input, .pv-public-question:not(.hidden) textarea, .pv-public-question:not(.hidden) select")
    ).filter((control) => !control.disabled);
    for (const control of visibleControls) {
      if (!control.checkValidity()) {
        control.reportValidity();
        return false;
      }
    }
    return true;
  }

  function updateFlowVisibility() {
    const reachable = reachableQuestionIds();
    const pages = visiblePages(reachable);
    if (currentPage >= pages.length) currentPage = Math.max(0, pages.length - 1);
    const activePage = pages[currentPage] || [];
    formEl.querySelectorAll(".pv-public-question, .pv-public-section").forEach((box) => {
      const visible = !box.dataset.questionId || pageHasQuestion(activePage, box.dataset.questionId);
      box.classList.toggle("hidden", !visible);
      box.querySelectorAll("input, textarea, select").forEach((control) => {
        control.disabled = !visible;
      });
    });
    if (pageNav && submitButton) {
      const hasPages = pages.length > 1;
      pageNav.classList.toggle("hidden", !hasPages);
      prevButton.disabled = currentPage <= 0;
      nextButton.classList.toggle("hidden", !hasPages || currentPage >= pages.length - 1);
      submitButton.classList.toggle("hidden", hasPages && currentPage < pages.length - 1);
      pageNav.querySelector("span").textContent = pages.length ? `${currentPage + 1} / ${pages.length}` : "";
    }
  }

  function collectAnswers() {
    const answers = {};
    const reachable = reachableQuestionIds();
    (form.questions || []).forEach((question) => {
      if (question.type === "section" || question.type === "info" || question.type === "partition") return;
      if (!reachable.has(String(question.id))) return;
      const box = formEl.querySelector(`[data-question-id="${question.id}"]`);
      if (!box) return;
      if (["short_text", "long_text", "number", "date", "time", "email", "phone", "url"].includes(question.type)) {
        answers[String(question.id)] = box.querySelector("[data-answer-control]").value;
      } else if (question.type === "single_choice" || question.type === "rating") {
        const checked = box.querySelector(`input[name="q_${question.id}"]:checked`);
        answers[String(question.id)] = checked ? checked.value : "";
      } else if (question.type === "multi_choice") {
        answers[String(question.id)] = Array.from(box.querySelectorAll(`input[name="q_${question.id}"]:checked`)).map((input) => input.value);
      } else if (question.type === "consent") {
        answers[String(question.id)] = box.querySelector("input").checked;
      }
    });
    return answers;
  }

  function renderForm() {
    if (form.status !== "open") {
      showStatus("このPowerVoteは現在回答を受け付けていません。", true);
      return;
    }
    if (alreadyAnswered && form.cookie_limit_enabled) {
      showStatus("このブラウザでは回答済みです。", false);
      return;
    }
    renderIdentity();
    (form.questions || []).forEach(renderQuestion);
    pageNav = document.createElement("div");
    pageNav.className = "pv-public-page-nav hidden";
    prevButton = document.createElement("button");
    prevButton.type = "button";
    prevButton.textContent = "戻る";
    nextButton = document.createElement("button");
    nextButton.type = "button";
    nextButton.textContent = "次へ";
    const counter = document.createElement("span");
    pageNav.append(prevButton, counter, nextButton);
    formEl.appendChild(pageNav);
    submitButton = document.createElement("button");
    submitButton.className = "pv-public-submit";
    submitButton.type = "submit";
    submitButton.textContent = "送信";
    submitButton.style.background = accent;
    formEl.appendChild(submitButton);
    prevButton.addEventListener("click", () => {
      currentPage = Math.max(0, currentPage - 1);
      updateFlowVisibility();
    });
    nextButton.addEventListener("click", () => {
      if (!validateCurrentPage()) return;
      currentPage += 1;
      updateFlowVisibility();
    });
    formEl.addEventListener("change", updateFlowVisibility);
    formEl.addEventListener("input", updateFlowVisibility);
    updateFlowVisibility();
  }

  formEl.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (previewMode) {
      showStatus("プレビューのため送信はされません。", false);
      return;
    }
    const submit = formEl.querySelector(".pv-public-submit");
    submit.disabled = true;
    const payload = {
      respondent: {
        name: formEl.querySelector('[name="respondent_name"]')?.value || "",
      },
      answers: collectAnswers(),
    };
    try {
      const response = await fetch(submitUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify(payload),
      });
      const result = await response.json();
      if (!response.ok || result.ok === false) {
        throw new Error(result.error || "送信に失敗しました。");
      }
      formEl.replaceChildren();
      const done = document.createElement("div");
      done.className = "pv-public-done";
      done.textContent = "回答を送信しました。ありがとうございました。";
      formEl.appendChild(done);
    } catch (error) {
      submit.disabled = false;
      showStatus(error.message, true);
    }
  });

  renderForm();
})();
