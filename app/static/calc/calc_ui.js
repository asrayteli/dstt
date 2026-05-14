(function () {
  "use strict";

  const root = document.querySelector("[data-calc-root]");
  if (!root || !window.CalcEngine) return;

  const historyKey = "dstt.calc.history.v1";
  const taxRateKey = "dstt.calc.taxRate.v1";
  const expressionEl = root.querySelector("[data-expression]");
  const resultEl = root.querySelector("[data-result]");
  const statusEl = root.querySelector("[data-status]");
  const memoryEl = root.querySelector("[data-memory-display]");
  const historyList = root.querySelector("[data-history-list]");
  const taxAmount = root.querySelector("[data-tax-amount]");
  const taxRate = root.querySelector("[data-tax-rate]");
  const taxCustomRate = root.querySelector("[data-tax-custom-rate]");
  const taxResult = root.querySelector("[data-tax-result]");
  const timeExpression = root.querySelector("[data-time-expression]");
  const timeResult = root.querySelector("[data-time-result]");

  let expression = "";
  let resultValue = 0;
  let resultText = "0";
  let hasResult = false;
  let memory = 0;
  let lastValidTimeExpression = "";

  function setStatus(message) {
    statusEl.textContent = message || "";
  }

  function updateDisplay() {
    expressionEl.textContent = expression || "0";
    resultEl.textContent = resultText || "0";
    memoryEl.textContent = window.CalcEngine.formatNumber(memory);
  }

  function readHistory() {
    try {
      return JSON.parse(localStorage.getItem(historyKey) || "[]");
    } catch (error) {
      return [];
    }
  }

  function writeHistory(items) {
    localStorage.setItem(historyKey, JSON.stringify(items.slice(0, 50)));
  }

  function addHistory(item) {
    const items = readHistory();
    items.unshift({ ...item, createdAt: new Date().toLocaleString("ja-JP") });
    writeHistory(items);
    renderHistory();
  }

  function renderHistory() {
    const items = readHistory();
    if (!items.length) {
      historyList.innerHTML = '<p class="calc-empty">履歴はありません。</p>';
      return;
    }

    historyList.innerHTML = "";
    items.forEach((item, index) => {
      const row = document.createElement("article");
      row.className = "calc-history-item";
      row.innerHTML = `
        <strong></strong>
        <span></span>
        <div class="calc-history-actions">
          <button type="button" data-history-use="${index}">再利用</button>
          <button type="button" data-history-copy="${index}">コピー</button>
          <button type="button" data-history-delete="${index}">削除</button>
        </div>
      `;
      row.querySelector("strong").textContent = item.summary || "";
      row.querySelector("span").textContent = item.createdAt || "";
      historyList.appendChild(row);
    });
  }

  async function copyText(text) {
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
    setStatus("コピーしました。");
  }

  function currentNumberValue() {
    if (hasResult) return resultValue;
    if (!expression) return 0;
    return window.CalcEngine.computeExpression(expression).value;
  }

  function appendToken(token) {
    const isNumberToken = /^\d$/.test(token) || token === ".";
    if (hasResult && isNumberToken) {
      expression = "";
      hasResult = false;
      resultText = "0";
    } else if (hasResult && "+-*/".includes(token)) {
      expression = String(resultValue);
      hasResult = false;
    }
    expression += token;
    setStatus("");
  }

  function toggleSign() {
    if (hasResult) {
      resultValue *= -1;
      resultText = window.CalcEngine.formatNumber(resultValue);
      expression = String(resultValue);
      hasResult = false;
      return;
    }
    const match = expression.match(/(-?\d+(?:\.\d+)?)$/);
    if (!match) {
      expression += "-";
      return;
    }
    const start = match.index;
    const number = match[0];
    expression = expression.slice(0, start) + (number.startsWith("-") ? number.slice(1) : `-${number}`);
  }

  function computeBasic() {
    const source = expression || String(resultValue || 0);
    const computed = window.CalcEngine.computeExpression(source);
    resultValue = computed.value;
    resultText = computed.text;
    expression = source;
    hasResult = true;
    addHistory({
      kind: "basic",
      expression: source,
      result: computed.text,
      summary: `${source} = ${computed.text}`,
      copyText: computed.text,
    });
  }

  function dispatchInput(token) {
    try {
      if (token === "clear") {
        expression = "";
        resultValue = 0;
        resultText = "0";
        hasResult = false;
        setStatus("");
      } else if (token === "backspace") {
        expression = expression.slice(0, -1);
        hasResult = false;
        setStatus("");
      } else if (token === "equals") {
        computeBasic();
        setStatus("");
      } else if (token === "sign") {
        toggleSign();
      } else if (token === "copy") {
        copyText(resultText);
      } else if (token === "memory-clear") {
        memory = 0;
      } else if (token === "memory-recall") {
        appendToken(String(memory));
      } else if (token === "memory-add") {
        memory += currentNumberValue();
      } else if (token === "memory-subtract") {
        memory -= currentNumberValue();
      } else {
        appendToken(token);
      }
    } catch (error) {
      setStatus(error.message || "計算できません。");
    }
    updateDisplay();
  }

  function setMode(mode) {
    root.querySelectorAll("[data-calc-mode]").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.calcMode === mode);
    });
    root.querySelectorAll("[data-calc-panel]").forEach((panel) => {
      panel.classList.toggle("is-active", panel.dataset.calcPanel === mode);
    });
  }

  function resolvedTaxRate() {
    return taxRate.value === "custom" ? Number(taxCustomRate.value) : Number(taxRate.value);
  }

  function renderTax(action) {
    try {
      const amount = Number(taxAmount.value);
      const rate = resolvedTaxRate();
      const result =
        action === "included"
          ? window.CalcEngine.calculateTaxIncluded(amount, rate)
          : window.CalcEngine.calculateTaxExcluded(amount, rate);
      const label = action === "included" ? "税込" : "税抜";
      taxResult.textContent = `${label}: ${window.CalcEngine.formatNumber(result.total)}円\n税額: ${window.CalcEngine.formatNumber(result.tax)}円\n本体: ${window.CalcEngine.formatNumber(result.base)}円`;
      localStorage.setItem(taxRateKey, taxRate.value === "custom" ? String(rate) : taxRate.value);
      addHistory({
        kind: "tax",
        expression: `${amount} / ${rate}% / ${label}`,
        result: String(result.total),
        summary: `${label} ${window.CalcEngine.formatNumber(result.total)}円`,
        copyText: String(result.total),
      });
    } catch (error) {
      taxResult.textContent = error.message || "税計算できません。";
    }
  }

  function restoreTaxRate() {
    const stored = localStorage.getItem(taxRateKey);
    if (!stored) return;
    if (stored === "8" || stored === "10") {
      taxRate.value = stored;
    } else {
      taxRate.value = "custom";
      taxCustomRate.value = stored;
    }
  }

  function isPartialTimeOperand(value) {
    return (
      value === "" ||
      /^\d{1,2}$/.test(value) ||
      /^\d{1,2}:$/.test(value) ||
      /^\d{1,2}:[0-5]$/.test(value) ||
      /^\d{1,2}:[0-5]\d$/.test(value)
    );
  }

  function isCompleteTimeOperand(value) {
    return /^\d{1,2}:[0-5]\d$/.test(value);
  }

  function isValidPartialTimeExpression(value) {
    const text = String(value || "");
    let operand = "";

    for (let index = 0; index < text.length; index += 1) {
      const char = text[index];
      if ("+-*/".includes(char)) {
        if (!isCompleteTimeOperand(operand)) return false;
        operand = "";
        continue;
      }
      operand += char;
      if (!isPartialTimeOperand(operand)) return false;
    }

    return operand === "" || isPartialTimeOperand(operand);
  }

  function setTimeExpressionValue(value) {
    if (!isValidPartialTimeExpression(value)) return false;
    timeExpression.value = value;
    lastValidTimeExpression = value;
    return true;
  }

  function editTimeExpression(token) {
    const start = timeExpression.selectionStart ?? timeExpression.value.length;
    const end = timeExpression.selectionEnd ?? timeExpression.value.length;
    const next = timeExpression.value.slice(0, start) + token + timeExpression.value.slice(end);
    if (!setTimeExpressionValue(next)) return;
    const caret = start + token.length;
    timeExpression.setSelectionRange(caret, caret);
  }

  function appendTimeToken(token) {
    if (token === "⌫" || token === "←") {
      const start = timeExpression.selectionStart ?? timeExpression.value.length;
      const end = timeExpression.selectionEnd ?? timeExpression.value.length;
      const deleteStart = start === end ? Math.max(0, start - 1) : start;
      const next = timeExpression.value.slice(0, deleteStart) + timeExpression.value.slice(end);
      setTimeExpressionValue(next);
      timeExpression.setSelectionRange(deleteStart, deleteStart);
      return;
    }
    editTimeExpression(token);
  }

  function computeTime() {
    try {
      const source = timeExpression.value;
      const computed = window.CalcEngine.computeTimeExpression(source);
      timeResult.textContent = `${computed.text}\n${window.CalcEngine.formatNumber(computed.minutes)}分`;
      addHistory({
        kind: "time",
        expression: source,
        result: computed.text,
        summary: `${source} = ${computed.text}`,
        copyText: computed.text,
      });
    } catch (error) {
      timeResult.textContent = error.message || "時間を計算できません。";
    }
  }

  root.addEventListener("click", (event) => {
    const modeButton = event.target.closest("[data-calc-mode]");
    if (modeButton) setMode(modeButton.dataset.calcMode);

    const key = event.target.closest("[data-calc-token]");
    if (key) dispatchInput(key.dataset.calcToken);

    const taxButton = event.target.closest("[data-tax-action]");
    if (taxButton) renderTax(taxButton.dataset.taxAction);

    const timeButton = event.target.closest("[data-time-token]");
    if (timeButton) appendTimeToken(timeButton.dataset.timeToken);
    if (event.target.closest("[data-time-clear]")) {
      setTimeExpressionValue("");
      timeResult.textContent = "0:00";
    }
    if (event.target.closest("[data-time-calc]")) computeTime();
    if (event.target.closest("[data-time-copy]")) copyText(timeResult.textContent.split("\n")[0]);

    const historyItems = readHistory();
    const useButton = event.target.closest("[data-history-use]");
    if (useButton) {
      const item = historyItems[Number(useButton.dataset.historyUse)];
      if (item?.kind === "basic") {
        setMode("basic");
        expression = item.expression || "";
        resultText = item.result || "0";
        resultValue = Number(String(resultText).replace(/,/g, "")) || 0;
        hasResult = false;
        updateDisplay();
      } else if (item?.kind === "time") {
        setMode("time");
        setTimeExpressionValue(isValidPartialTimeExpression(item.expression || "") ? item.expression || "" : "");
      } else if (item?.kind === "tax") {
        setMode("tax");
      }
    }

    const copyButton = event.target.closest("[data-history-copy]");
    if (copyButton) copyText(historyItems[Number(copyButton.dataset.historyCopy)]?.copyText || "");

    const deleteButton = event.target.closest("[data-history-delete]");
    if (deleteButton) {
      historyItems.splice(Number(deleteButton.dataset.historyDelete), 1);
      writeHistory(historyItems);
      renderHistory();
    }

    if (event.target.closest("[data-history-clear]")) {
      writeHistory([]);
      renderHistory();
    }
  });

  timeExpression.addEventListener("beforeinput", (event) => {
    if (!event.inputType || event.inputType.startsWith("delete")) return;
    const data = event.data || "";
    const start = timeExpression.selectionStart ?? timeExpression.value.length;
    const end = timeExpression.selectionEnd ?? timeExpression.value.length;
    const next = timeExpression.value.slice(0, start) + data + timeExpression.value.slice(end);
    if (!isValidPartialTimeExpression(next)) event.preventDefault();
  });

  timeExpression.addEventListener("input", () => {
    if (isValidPartialTimeExpression(timeExpression.value)) {
      lastValidTimeExpression = timeExpression.value;
      return;
    }
    timeExpression.value = lastValidTimeExpression;
  });

  document.addEventListener("keydown", (event) => {
    if (event.target.matches("input, textarea, select")) return;
    const key = event.key;
    if (/^\d$/.test(key) || "+-*/().%".includes(key)) {
      event.preventDefault();
      dispatchInput(key);
    } else if (key === "Enter") {
      event.preventDefault();
      dispatchInput("equals");
    } else if (key === "Backspace") {
      event.preventDefault();
      dispatchInput("backspace");
    } else if (key === "Escape") {
      event.preventDefault();
      dispatchInput("clear");
    }
  });

  restoreTaxRate();
  renderHistory();
  updateDisplay();
})();
