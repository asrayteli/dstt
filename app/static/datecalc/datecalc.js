(function () {
  const root = document.querySelector(".datecalc-page");
  if (!root) return;

  const historyKey = "dstt.datecalc.history.v1";
  const tabs = Array.from(root.querySelectorAll("[data-datecalc-tab]"));
  const panels = Array.from(root.querySelectorAll("[data-datecalc-panel]"));
  const historyList = root.querySelector("[data-history-list]");

  function activePanel() {
    return root.querySelector(".dc-form.is-active");
  }

  function activate(mode) {
    tabs.forEach((tab) => tab.classList.toggle("is-active", tab.dataset.datecalcTab === mode));
    panels.forEach((panel) => panel.classList.toggle("is-active", panel.dataset.datecalcPanel === mode));
    root.dataset.activeMode = mode;
    updateWeekdayStyles();
  }

  function setValue(form, name, value) {
    if (!form) return;
    const fields = Array.from(form.querySelectorAll(`[name="${name}"]`));
    fields.forEach((field) => {
      if (field.type === "checkbox") {
        field.checked = Boolean(value);
      } else {
        field.value = value;
      }
    });
  }

  function setArrayValues(form, name, values) {
    if (!form) return;
    const selected = new Set((values || []).map(String));
    form.querySelectorAll(`[name="${name}"]`).forEach((field) => {
      if (field.type === "checkbox") {
        field.checked = selected.has(field.value);
      }
    });
  }

  function serializeForm(form) {
    const fields = {};
    Array.from(form.elements).forEach((field) => {
      if (!field.name || field.disabled) return;
      if (field.type === "checkbox" && field.name.endsWith("[]")) {
        fields[field.name] = fields[field.name] || [];
        if (field.checked) fields[field.name].push(field.value);
        return;
      }
      if (field.type === "checkbox") {
        fields[field.name] = field.checked;
        return;
      }
      fields[field.name] = field.value;
    });
    return fields;
  }

  function applyFields(mode, fields) {
    activate(mode);
    const form = activePanel();
    if (!form) return;

    Object.entries(fields || {}).forEach(([name, value]) => {
      if (Array.isArray(value)) {
        setArrayValues(form, name, value);
      } else {
        setValue(form, name, value);
      }
    });
    updateWeekdayStyles();
  }

  function readHistory() {
    try {
      return JSON.parse(localStorage.getItem(historyKey) || "[]");
    } catch (error) {
      return [];
    }
  }

  function writeHistory(items) {
    localStorage.setItem(historyKey, JSON.stringify(items.slice(0, 20)));
  }

  function renderHistory() {
    if (!historyList) return;
    const items = readHistory();
    if (!items.length) {
      historyList.innerHTML = '<p class="text-sm text-slate-500">保存済みの履歴はありません。</p>';
      return;
    }

    historyList.innerHTML = "";
    items.forEach((item, index) => {
      const row = document.createElement("div");
      row.className = "dc-history-item p-3";
      row.innerHTML = `
        <div class="flex items-start justify-between gap-3">
          <div class="min-w-0">
            <p class="break-words text-sm font-black text-slate-900"></p>
            <p class="mt-1 break-words text-xs text-slate-500"></p>
          </div>
          <button type="button" class="text-xs font-bold text-teal-700 hover:text-teal-900" data-history-index="${index}">再利用</button>
        </div>
      `;
      row.querySelector("p").textContent = item.summary || "";
      row.querySelectorAll("p")[1].textContent = item.inputSummary || item.createdAt || "";
      historyList.appendChild(row);
    });
  }

  function saveCurrentResult(button) {
    const form = activePanel();
    if (!form) return;

    const items = readHistory();
    items.unshift({
      mode: button.dataset.mode || root.dataset.activeMode || "addsub",
      summary: button.dataset.summary || "",
      inputSummary: button.dataset.inputSummary || "",
      copyText: button.dataset.copyText || "",
      createdAt: new Date().toISOString(),
      fields: serializeForm(form),
    });
    writeHistory(items);
    renderHistory();
    button.textContent = "保存済み";
    window.setTimeout(() => {
      button.textContent = "履歴に保存";
    }, 1200);
  }

  async function copyText(text, button) {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
    } catch (error) {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      textarea.remove();
    }
    if (button) {
      const original = button.textContent;
      button.textContent = "コピー済み";
      window.setTimeout(() => {
        button.textContent = original;
      }, 1200);
    }
  }

  function formatLocalDate(date) {
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  }

  function todayString() {
    return formatLocalDate(new Date());
  }

  function startOfThisMonth() {
    const now = new Date();
    return formatLocalDate(new Date(now.getFullYear(), now.getMonth(), 1));
  }

  function endOfThisMonth() {
    const now = new Date();
    return formatLocalDate(new Date(now.getFullYear(), now.getMonth() + 1, 0));
  }

  function applyPreset(name) {
    if (name === "next-month-end") {
      activate("month_shift");
      const form = activePanel();
      setValue(form, "month_base_date", endOfThisMonth());
      setValue(form, "month_shift", 1);
      setValue(form, "keep_month_end", true);
    }

    if (name === "thirty-days") {
      activate("addsub");
      const form = activePanel();
      setValue(form, "operation", "add");
      setValue(form, "years", 0);
      setValue(form, "months", 0);
      setValue(form, "days", 30);
      setValue(form, "hours", 0);
      setValue(form, "minutes", 0);
    }

    if (name === "this-month-weekends") {
      activate("count");
      const form = activePanel();
      setValue(form, "count_start_date", startOfThisMonth());
      setValue(form, "count_end_date", endOfThisMonth());
      setArrayValues(form, "count_weekdays[]", ["0", "6"]);
      setValue(form, "count_month_days", "");
      setValue(form, "count_exact_dates", "");
      setValue(form, "count_japan_holidays", false);
    }

    if (name === "this-month-holidays") {
      activate("count");
      const form = activePanel();
      setValue(form, "count_start_date", startOfThisMonth());
      setValue(form, "count_end_date", endOfThisMonth());
      setArrayValues(form, "count_weekdays[]", []);
      setValue(form, "count_month_days", "");
      setValue(form, "count_exact_dates", "");
      setValue(form, "count_japan_holidays", true);
    }

    if (name === "until-month-end") {
      activate("count");
      const form = activePanel();
      setValue(form, "count_start_date", todayString());
      setValue(form, "count_end_date", endOfThisMonth());
      setArrayValues(form, "count_weekdays[]", []);
      setValue(form, "count_month_days", "");
      setValue(form, "count_exact_dates", "");
      setValue(form, "count_japan_holidays", true);
    }

    updateWeekdayStyles();
  }

  function updateWeekdayStyles() {
    root.querySelectorAll('[name="count_weekdays[]"]').forEach((checkbox) => {
      const label = checkbox.closest("label");
      if (!label) return;
      label.classList.toggle("border-teal-700", checkbox.checked);
      label.classList.toggle("bg-teal-50", checkbox.checked);
      label.classList.toggle("text-teal-900", checkbox.checked);
    });
  }

  tabs.forEach((tab) => {
    tab.addEventListener("click", () => activate(tab.dataset.datecalcTab));
  });

  root.addEventListener("click", (event) => {
    const copyButton = event.target.closest("[data-copy-result]");
    if (copyButton) copyText(copyButton.dataset.copyResult, copyButton);

    const saveButton = event.target.closest("[data-save-history]");
    if (saveButton) saveCurrentResult(saveButton);

    const presetButton = event.target.closest("[data-preset]");
    if (presetButton) applyPreset(presetButton.dataset.preset);

    const reuseButton = event.target.closest("[data-history-index]");
    if (reuseButton) {
      const item = readHistory()[Number(reuseButton.dataset.historyIndex)];
      if (item) applyFields(item.mode, item.fields);
    }

    if (event.target.closest("[data-clear-history]")) {
      writeHistory([]);
      renderHistory();
    }
  });

  root.addEventListener("change", (event) => {
    if (event.target.matches('[name="count_weekdays[]"]')) updateWeekdayStyles();
  });

  activate(root.dataset.activeMode || "addsub");
  renderHistory();
})();
