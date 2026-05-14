(function (root) {
  "use strict";

  const OPERATORS = {
    "+": { precedence: 1, args: 2, run: (a, b) => a + b },
    "-": { precedence: 1, args: 2, run: (a, b) => a - b },
    "*": { precedence: 2, args: 2, run: (a, b) => a * b },
    "/": {
      precedence: 2,
      args: 2,
      run: (a, b) => {
        if (b === 0) throw new Error("0で割ることはできません。");
        return a / b;
      },
    },
    "%": { precedence: 3, args: 1, run: (a) => a / 100 },
  };

  function roundNumber(value) {
    if (!Number.isFinite(value)) throw new Error("計算できない値です。");
    return Number(value.toFixed(12));
  }

  function formatNumber(value) {
    const rounded = roundNumber(value);
    return rounded.toLocaleString("ja-JP", { maximumFractionDigits: 10 });
  }

  function isDigit(char) {
    return char >= "0" && char <= "9";
  }

  function normalizeExpression(expression) {
    return String(expression || "")
      .replace(/,/g, "")
      .replace(/×/g, "*")
      .replace(/÷/g, "/")
      .replace(/\s+/g, "");
  }

  function previousAllowsSign(tokens) {
    if (!tokens.length) return true;
    const previous = tokens[tokens.length - 1];
    return previous.type === "operator" || previous.type === "leftParen";
  }

  function tokenize(expression) {
    const source = normalizeExpression(expression);
    const tokens = [];
    let index = 0;

    while (index < source.length) {
      const char = source[index];
      const next = source[index + 1];

      if (
        isDigit(char) ||
        char === "." ||
        (char === "-" && previousAllowsSign(tokens) && (isDigit(next) || next === "."))
      ) {
        let numberText = char;
        index += 1;
        while (index < source.length && (isDigit(source[index]) || source[index] === ".")) {
          numberText += source[index];
          index += 1;
        }
        if (!/^-?(?:\d+|\d*\.\d+)$/.test(numberText)) {
          throw new Error("数値の形式が正しくありません。");
        }
        tokens.push({ type: "number", value: Number(numberText) });
        continue;
      }

      if (char === "-" && previousAllowsSign(tokens) && next === "(") {
        tokens.push({ type: "number", value: 0 });
        tokens.push({ type: "operator", value: "-" });
        index += 1;
        continue;
      }

      if ("+-*/".includes(char)) {
        tokens.push({ type: "operator", value: char });
        index += 1;
        continue;
      }

      if (char === "%") {
        tokens.push({ type: "operator", value: "%" });
        index += 1;
        continue;
      }

      if (char === "(") {
        tokens.push({ type: "leftParen", value: char });
        index += 1;
        continue;
      }

      if (char === ")") {
        tokens.push({ type: "rightParen", value: char });
        index += 1;
        continue;
      }

      throw new Error("時間は 01:30 のように入力してください。");
    }

    if (!tokens.length) throw new Error("式を入力してください。");
    return tokens;
  }

  function toReversePolish(tokens) {
    const output = [];
    const operators = [];

    tokens.forEach((token) => {
      if (token.type === "number") {
        output.push(token);
        return;
      }

      if (token.type === "operator") {
        const op = OPERATORS[token.value];
        while (operators.length) {
          const top = operators[operators.length - 1];
          if (top.type !== "operator") break;
          const topOp = OPERATORS[top.value];
          if (topOp.precedence < op.precedence) break;
          output.push(operators.pop());
        }
        operators.push(token);
        return;
      }

      if (token.type === "leftParen") {
        operators.push(token);
        return;
      }

      if (token.type === "rightParen") {
        while (operators.length && operators[operators.length - 1].type !== "leftParen") {
          output.push(operators.pop());
        }
        if (!operators.length) throw new Error("かっこの対応が正しくありません。");
        operators.pop();
      }
    });

    while (operators.length) {
      const token = operators.pop();
      if (token.type === "leftParen") throw new Error("かっこの対応が正しくありません。");
      output.push(token);
    }

    return output;
  }

  function computeRpn(tokens) {
    const stack = [];
    tokens.forEach((token) => {
      if (token.type === "number") {
        stack.push(token.value);
        return;
      }

      const op = OPERATORS[token.value];
      if (stack.length < op.args) throw new Error("式の並びが正しくありません。");
      if (op.args === 1) {
        stack.push(roundNumber(op.run(stack.pop())));
        return;
      }
      const right = stack.pop();
      const left = stack.pop();
      stack.push(roundNumber(op.run(left, right)));
    });

    if (stack.length !== 1) throw new Error("式の並びが正しくありません。");
    return roundNumber(stack[0]);
  }

  function computeExpression(expression) {
    const value = computeRpn(toReversePolish(tokenize(expression)));
    return { value, text: formatNumber(value) };
  }

  function calculateTaxIncluded(amount, rate) {
    const base = Number(amount);
    const taxRate = Number(rate);
    if (!Number.isFinite(base) || !Number.isFinite(taxRate)) throw new Error("金額と税率を入力してください。");
    const tax = Math.round(base * (taxRate / 100));
    return { base, tax, total: base + tax, rate: taxRate };
  }

  function calculateTaxExcluded(amount, rate) {
    const total = Number(amount);
    const taxRate = Number(rate);
    if (!Number.isFinite(total) || !Number.isFinite(taxRate)) throw new Error("金額と税率を入力してください。");
    const base = Math.round(total / (1 + taxRate / 100));
    return { base, tax: total - base, total, rate: taxRate };
  }

  function parseTimeToMinutes(value) {
    const text = String(value || "").trim();
    const match = text.match(/^(\d{1,2}):([0-5]\d)$/);
    if (!match) throw new Error("時間は 1:30 のように入力してください。");
    const hours = Number(match[1]);
    const minutes = Number(match[2]);
    return hours * 60 + minutes;
  }

  function formatMinutes(totalMinutes) {
    if (!Number.isFinite(totalMinutes)) throw new Error("時間を計算できません。");
    const sign = totalMinutes < 0 ? "-" : "";
    const absolute = Math.abs(Math.round(totalMinutes));
    const hours = Math.floor(absolute / 60);
    const minutes = String(absolute % 60).padStart(2, "0");
    return `${sign}${hours}:${minutes}`;
  }

  function tokenizeTimeExpression(expression) {
    const source = String(expression || "").replace(/\s+/g, "");
    const tokens = [];
    let index = 0;

    while (index < source.length) {
      const char = source[index];
      const timeText = source.slice(index, index + 5);
      const timeMatch = source.slice(index).match(/^\d{1,2}:[0-5]\d/);
      if (timeMatch) {
        tokens.push({ type: "number", value: parseTimeToMinutes(timeMatch[0]) });
        index += timeMatch[0].length;
        continue;
      }
      if ("+-*/".includes(char)) {
        tokens.push({ type: "operator", value: char });
        index += 1;
        continue;
      }
      if (char === "(") {
        tokens.push({ type: "leftParen", value: char });
        index += 1;
        continue;
      }
      if (char === ")") {
        tokens.push({ type: "rightParen", value: char });
        index += 1;
        continue;
      }
      throw new Error("時間は 1:30 のように入力してください。");
    }

    if (!tokens.length) throw new Error("時間式を入力してください。");
    return tokens;
  }

  function computeTimeExpression(expression) {
    const minutes = computeRpn(toReversePolish(tokenizeTimeExpression(expression)));
    return { minutes, text: formatMinutes(minutes) };
  }

  const api = {
    calculateTaxExcluded,
    calculateTaxIncluded,
    computeExpression,
    computeTimeExpression,
    formatMinutes,
    formatNumber,
    parseTimeToMinutes,
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  root.CalcEngine = api;
})(typeof globalThis !== "undefined" ? globalThis : window);
