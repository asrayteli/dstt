(function () {
  const root = document.getElementById("pv-flow-app");
  if (!root) return;

  const apiBase = root.dataset.apiBase;
  const formId = root.dataset.formId;
  const state = {
    form: null,
    selectedIndex: -1,
    dirty: false,
    contextMenu: null,
    connecting: null,
    zoom: 1,
    canvasSize: { width: 1200, height: 760 },
    lastCanvasPoint: { x: 420, y: 180 },
    saveDialog: null,
    panning: null,
  };
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
    info: "説明",
    partition: "パーテーション",
  };

  const ADD_TYPES = [
    ["single_choice", "単一選択"],
    ["multi_choice", "複数選択"],
    ["short_text", "短文"],
    ["long_text", "長文"],
    ["rating", "評価"],
    ["number", "数値"],
    ["date", "日付"],
    ["time", "時刻"],
    ["email", "メール"],
    ["phone", "電話"],
    ["url", "URL"],
    ["consent", "同意"],
    ["info", "説明"],
    ["partition", "パーテーション"],
  ];

  function uid() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    return `local-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function setStateLabel(text) {
    $("pv-flow-state").textContent = text;
  }

  function setDirty(dirty) {
    state.dirty = dirty;
    if (!state.connecting) setStateLabel(dirty ? "未保存" : "保存済み");
  }

  function setFlowTitle(title) {
    $("pv-flow-title").textContent = title || "PowerVote Flow";
    document.title = `PowerVote Flow - ${title || "PowerVote"}`;
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
    const question = {
      id: null,
      type,
      title: TYPES[type] || "質問",
      description: "",
      required: false,
      sort_order: state.form ? state.form.questions.length : 0,
      options: [],
      settings: { flow: { key: `flow-${uid()}` } },
    };
    if (type === "single_choice" || type === "multi_choice") {
      question.title = "選択してください";
      question.options = [
        { id: uid(), label: "はい" },
        { id: uid(), label: "いいえ" },
      ];
    }
    if (type === "consent") {
      question.title = "内容に同意します";
      question.required = true;
    }
    if (type === "info") {
      question.title = "説明";
      question.description = "回答者に伝えたい内容を書きます。";
    }
    if (type === "partition") {
      question.title = "次のページ";
      question.description = "ここから回答画面のページを分けます。";
      question.required = false;
    }
    return question;
  }

  function formSettings() {
    state.form.settings = state.form.settings || {};
    return state.form.settings;
  }

  function flowFor(question) {
    question.settings = question.settings || {};
    question.settings.flow = question.settings.flow || {};
    if (!question.settings.flow.key) question.settings.flow.key = `flow-${uid()}`;
    return question.settings.flow;
  }

  function questionRef(question) {
    return flowFor(question).key;
  }

  function refsFor(question) {
    const refs = [questionRef(question)];
    if (question.id != null) refs.push(String(question.id));
    return refs;
  }

  function questionByRef(ref) {
    return (state.form.questions || []).find((question) => refsFor(question).includes(String(ref)));
  }

  function targetLabel(ref) {
    if (!ref) return "未設定";
    if (ref === "__end__") return "送信";
    const question = questionByRef(ref);
    if (!question) return "未設定";
    const index = state.form.questions.indexOf(question);
    return `${index + 1}. ${question.title || TYPES[question.type]}`;
  }

  function nodeHeight(question) {
    return Math.max(112, 108 + (question.options || []).length * 30);
  }

  function terminalPositions() {
    const questions = state.form?.questions || [];
    let maxX = 520;
    let maxY = 360;
    questions.forEach((question) => {
      const flow = flowFor(question);
      maxX = Math.max(maxX, Number(flow.x || 0));
      maxY = Math.max(maxY, Number(flow.y || 0) + nodeHeight(question));
    });
    const centerX = Math.max(420, Math.min(620, Math.round((maxX + 250) / 2) - 90));
    return {
      start: { x: centerX, y: 40, width: 190, height: 92 },
      submit: { x: centerX, y: Math.max(360, maxY + 150), width: 190, height: 92 },
      canvasWidth: Math.max(1100, maxX + 520),
      canvasHeight: Math.max(760, maxY + 320),
    };
  }

  function layoutQuestions() {
    const questions = state.form.questions || [];
    questions.forEach((question, index) => {
      const flow = flowFor(question);
      if (flow.x == null || flow.y == null) {
        flow.x = 390;
        flow.y = 180 + index * 220;
      }
      question.sort_order = index;
    });
  }

  function applyZoomAndSize() {
    const size = state.canvasSize;
    const zoomLayer = $("pv-flow-zoom-layer");
    const world = $("pv-flow-world");
    const canvas = $("pv-flow-canvas");
    const svg = $("pv-flow-lines");
    zoomLayer.style.width = `${size.width * state.zoom}px`;
    zoomLayer.style.height = `${size.height * state.zoom}px`;
    world.style.width = `${size.width}px`;
    world.style.height = `${size.height}px`;
    world.style.transform = `scale(${state.zoom})`;
    canvas.style.width = `${size.width}px`;
    canvas.style.height = `${size.height}px`;
    svg.setAttribute("width", String(size.width));
    svg.setAttribute("height", String(size.height));
    $("pv-flow-zoom-label").textContent = `${Math.round(state.zoom * 100)}%`;
  }

  function zoomAt(event, nextZoom) {
    const wrap = $("pv-flow-canvas-wrap");
    const before = canvasPoint(event);
    setZoom(nextZoom);
    wrap.scrollLeft = before.x * state.zoom - (event.clientX - wrap.getBoundingClientRect().left);
    wrap.scrollTop = before.y * state.zoom - (event.clientY - wrap.getBoundingClientRect().top);
  }

  function render() {
    layoutQuestions();
    renderCanvas();
    renderInspector();
    drawLines();
  }

  function renderCanvas() {
    const canvas = $("pv-flow-canvas");
    const positions = terminalPositions();
    state.canvasSize = { width: positions.canvasWidth, height: positions.canvasHeight };
    applyZoomAndSize();
    canvas.replaceChildren();
    canvas.classList.toggle("pv-flow-connecting", Boolean(state.connecting));
    if (state.connecting) {
      const hint = document.createElement("div");
      hint.className = "pv-flow-connection-hint";
      hint.textContent = "次につなぐ要素をクリックしてください";
      canvas.appendChild(hint);
    }
    canvas.appendChild(terminalNode("start", "開始", "ここから回答が始まります", positions.start));

    (state.form.questions || []).forEach((question, index) => {
      const node = document.createElement("article");
      const requiredLabel = question.required ? "回答必須" : "任意回答";
      node.className = "pv-flow-node" + (index === state.selectedIndex ? " active" : "");
      if (state.connecting?.source === "question" && state.connecting.index === index) {
        node.classList.add("pv-flow-connect-source");
      }
      node.dataset.index = String(index);
      node.style.left = `${flowFor(question).x}px`;
      node.style.top = `${flowFor(question).y}px`;
      node.innerHTML = `
        <div class="pv-flow-node-head">
          <div class="pv-flow-node-type"></div>
          <span class="pv-flow-required-badge"></span>
        </div>
        <h3></h3>
        <p></p>
        <div class="pv-flow-next-label"></div>
        <div class="pv-flow-node-options"></div>
      `;
      node.querySelector(".pv-flow-node-type").textContent = `${index + 1}. ${TYPES[question.type] || question.type}`;
      node.querySelector(".pv-flow-required-badge").textContent = requiredLabel;
      node.querySelector(".pv-flow-required-badge").classList.toggle("optional", !question.required);
      node.querySelector("h3").textContent = question.title || "質問";
      node.querySelector("p").textContent = question.description || "";
      node.querySelector(".pv-flow-next-label").textContent = `次: ${targetLabel(question.settings?.default_next || "")}`;
      const options = node.querySelector(".pv-flow-node-options");
      (question.options || []).forEach((option) => {
        const pill = document.createElement("div");
        const branchTarget = question.settings?.branches?.[option.id] || "";
        pill.className = "pv-flow-node-option" + (branchTarget ? " connected" : "");
        if (state.connecting?.source === "option" && state.connecting.index === index && state.connecting.optionId === option.id) {
          pill.classList.add("pv-flow-connect-source");
        }
        pill.dataset.optionId = option.id;
        pill.textContent = branchTarget ? `${option.label} → ${targetLabel(branchTarget)}` : option.label;
        pill.addEventListener("contextmenu", (event) => openOptionMenu(event, index, option.id));
        options.appendChild(pill);
      });
      node.addEventListener("click", () => handleQuestionClick(index));
      node.addEventListener("contextmenu", (event) => openNodeMenu(event, index));
      node.addEventListener("pointerdown", (event) => startDrag(event, index, node));
      canvas.appendChild(node);
    });

    canvas.appendChild(terminalNode("submit", "送信", "ここに到達すると回答を送信できます", positions.submit));
  }

  function terminalNode(kind, title, description, position) {
    const node = document.createElement("article");
    node.className = `pv-flow-node pv-flow-terminal pv-flow-${kind}`;
    if (state.connecting?.source === "start" && kind === "start") node.classList.add("pv-flow-connect-source");
    node.dataset.terminal = kind;
    node.style.left = `${position.x}px`;
    node.style.top = `${position.y}px`;
    node.style.width = `${position.width}px`;
    node.innerHTML = `
      <div class="pv-flow-node-type">${kind === "start" ? "開始点" : "完了点"}</div>
      <h3></h3>
      <p></p>
    `;
    node.querySelector("h3").textContent = title;
    node.querySelector("p").textContent = description;
    node.addEventListener("click", () => handleTerminalClick(kind));
    node.addEventListener("contextmenu", (event) => openTerminalMenu(event, kind));
    return node;
  }

  function startDrag(event, index, node) {
    if (event.button !== 0 || state.connecting) return;
    state.selectedIndex = index;
    renderInspector();
    const question = state.form.questions[index];
    const startX = event.clientX;
    const startY = event.clientY;
    const originX = Number(flowFor(question).x || 0);
    const originY = Number(flowFor(question).y || 0);
    node.setPointerCapture(event.pointerId);

    function move(moveEvent) {
      const flow = flowFor(question);
      flow.x = Math.max(40, originX + (moveEvent.clientX - startX) / state.zoom);
      flow.y = Math.max(150, originY + (moveEvent.clientY - startY) / state.zoom);
      node.style.left = `${flow.x}px`;
      node.style.top = `${flow.y}px`;
      setDirty(true);
      drawLines();
    }

    function up() {
      node.removeEventListener("pointermove", move);
      node.removeEventListener("pointerup", up);
    }

    node.addEventListener("pointermove", move);
    node.addEventListener("pointerup", up);
  }

  function handleQuestionClick(index) {
    if (state.connecting) {
      setConnectionTarget(questionRef(state.form.questions[index]));
      return;
    }
    selectNode(index);
  }

  function handleTerminalClick(kind) {
    if (state.connecting && kind === "submit") {
      setConnectionTarget("__end__");
    }
  }

  function selectNode(index) {
    state.selectedIndex = index;
    render();
  }

  function renderInspector() {
    const selected = state.form.questions[state.selectedIndex];
    $("pv-flow-empty").classList.toggle("hidden", Boolean(selected));
    $("pv-flow-edit").classList.toggle("hidden", !selected);
    if (!selected) return;

    $("pv-flow-node-title").value = selected.title || "";
    $("pv-flow-node-description").value = selected.description || "";
    $("pv-flow-node-type").value = selected.type;
    $("pv-flow-node-required").value = selected.required ? "true" : "false";
    renderOptionsEditor(selected);
  }

  function renderOptionsEditor(question) {
    const wrap = $("pv-flow-options");
    wrap.replaceChildren();
    if (question.type !== "single_choice" && question.type !== "multi_choice") return;
    question.settings = question.settings || {};
    question.settings.branches = question.settings.branches || {};

    const caption = document.createElement("div");
    caption.className = "pv-flow-options-caption";
    caption.textContent = question.type === "single_choice"
      ? "選択肢を右クリックすると、その回答だけの次の質問を設定できます。"
      : "複数選択では質問カードの「次を設定」を使います。";
    wrap.appendChild(caption);

    (question.options || []).forEach((option, optionIndex) => {
      const box = document.createElement("div");
      box.className = "pv-flow-option-edit";
      const top = document.createElement("div");
      top.className = "pv-flow-option-edit-row";
      const labelInput = document.createElement("input");
      labelInput.value = option.label || "";
      labelInput.setAttribute("aria-label", "選択肢");
      labelInput.addEventListener("input", () => {
        option.label = labelInput.value;
        setDirty(true);
        render();
      });
      const remove = document.createElement("button");
      remove.type = "button";
      remove.textContent = "削除";
      remove.addEventListener("click", () => {
        question.options.splice(optionIndex, 1);
        delete question.settings.branches[option.id];
        setDirty(true);
        render();
      });
      top.append(labelInput, remove);
      box.appendChild(top);
      if (question.type === "single_choice") {
        const branch = document.createElement("div");
        branch.className = "pv-flow-options-caption";
        branch.textContent = `次: ${targetLabel(question.settings.branches[option.id] || "")}`;
        box.appendChild(branch);
      }
      wrap.appendChild(box);
    });
  }

  function drawLines() {
    const svg = $("pv-flow-lines");
    svg.replaceChildren();
    appendArrowMarker(svg);
    const questions = state.form.questions || [];
    const positions = terminalPositions();

    const startTarget = formSettings().flow_start || "";
    if (startTarget) {
      drawTargetLine(svg, terminalAnchor(positions.start, "bottom"), startTarget, "開始", "pv-flow-line pv-flow-line-start", { source: "start" });
    }

    questions.forEach((question) => {
      const defaultTarget = question.settings?.default_next || "";
      if (defaultTarget) {
        const index = state.form.questions.indexOf(question);
        drawTargetLine(svg, nodeAnchor(question, "bottom"), defaultTarget, "次へ", "pv-flow-line pv-flow-line-default", { source: "question", index });
      }

      if (question.type !== "single_choice") return;
      const branches = question.settings?.branches || {};
      Object.entries(branches).forEach(([optionId, targetId]) => {
        if (!targetId) return;
        const label = (question.options || []).find((option) => option.id === optionId)?.label || "分岐";
        const index = state.form.questions.indexOf(question);
        drawTargetLine(svg, nodeAnchor(question, "bottom"), targetId, label, "pv-flow-line pv-flow-line-branch", { source: "option", index, optionId });
      });
    });
  }

  function appendArrowMarker(svg) {
    const defs = document.createElementNS("http://www.w3.org/2000/svg", "defs");
    defs.innerHTML = `
      <marker id="pv-flow-arrow" markerWidth="12" markerHeight="12" refX="5" refY="10" orient="auto" markerUnits="strokeWidth">
        <path d="M 0 0 L 10 10 L 0 10 z"></path>
      </marker>
    `;
    svg.appendChild(defs);
  }

  function drawTargetLine(svg, from, targetRef, label, className, edge) {
    const positions = terminalPositions();
    const target = targetRef === "__end__"
      ? terminalAnchor(positions.submit, "top")
      : questionByRef(targetRef)
        ? nodeAnchor(questionByRef(targetRef), "top")
        : null;
    if (!target) return;
    drawLine(svg, from, target, label, className, { ...edge, targetRef, from, to: target });
  }

  function nodeAnchor(question, side) {
    const flow = flowFor(question);
    return {
      x: Number(flow.x || 0) + 125,
      y: Number(flow.y || 0) + (side === "bottom" ? nodeHeight(question) : 0),
    };
  }

  function terminalAnchor(position, side = "bottom") {
    return {
      x: Number(position.x || 0) + position.width / 2,
      y: Number(position.y || 0) + (side === "bottom" ? position.height : 0),
    };
  }

  function drawLine(svg, from, to, label, className, edge) {
    const yGap = Math.max(70, Math.abs(to.y - from.y) / 2);
    const midY1 = from.y + yGap;
    const midY2 = to.y - yGap;
    const d = `M ${from.x} ${from.y} C ${from.x} ${midY1}, ${to.x} ${midY2}, ${to.x} ${to.y}`;
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    path.setAttribute("class", className);
    path.setAttribute("marker-end", "url(#pv-flow-arrow)");
    path.setAttribute("d", d);
    svg.appendChild(path);

    const hit = document.createElementNS("http://www.w3.org/2000/svg", "path");
    hit.setAttribute("class", "pv-flow-line-hit");
    hit.setAttribute("d", d);
    hit.addEventListener("contextmenu", (event) => openEdgeMenu(event, edge));
    svg.appendChild(hit);

    if (!label) return;
    const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
    text.setAttribute("class", className.includes("branch") ? "pv-flow-line-label branch" : "pv-flow-line-label");
    text.setAttribute("x", String((from.x + to.x) / 2));
    text.setAttribute("y", String((from.y + to.y) / 2 - 8));
    text.textContent = label;
    svg.appendChild(text);
  }

  function canvasPoint(event) {
    const world = $("pv-flow-world").getBoundingClientRect();
    return {
      x: Math.max(40, Math.round((event.clientX - world.left) / state.zoom)),
      y: Math.max(150, Math.round((event.clientY - world.top) / state.zoom)),
    };
  }

  function closestTarget(event, selector) {
    return event.target instanceof Element ? event.target.closest(selector) : null;
  }

  function handleCanvasWheel(event) {
    if (closestTarget(event, ".pv-flow-node") || closestTarget(event, ".pv-flow-menu")) return;
    event.preventDefault();
    closeContextMenu();
    const direction = event.deltaY > 0 ? -0.1 : 0.1;
    zoomAt(event, state.zoom + direction);
  }

  function startCanvasPan(event) {
    if (event.button !== 0 || state.connecting) return;
    if (
      closestTarget(event, ".pv-flow-node") ||
      closestTarget(event, ".pv-flow-menu") ||
      closestTarget(event, ".pv-flow-line-hit")
    ) return;
    const wrap = $("pv-flow-canvas-wrap");
    state.panning = {
      pointerId: event.pointerId,
      x: event.clientX,
      y: event.clientY,
      scrollLeft: wrap.scrollLeft,
      scrollTop: wrap.scrollTop,
    };
    wrap.classList.add("panning");
    wrap.setPointerCapture(event.pointerId);
    event.preventDefault();
  }

  function moveCanvasPan(event) {
    if (!state.panning || state.panning.pointerId !== event.pointerId) return;
    const wrap = $("pv-flow-canvas-wrap");
    wrap.scrollLeft = state.panning.scrollLeft - (event.clientX - state.panning.x);
    wrap.scrollTop = state.panning.scrollTop - (event.clientY - state.panning.y);
  }

  function endCanvasPan(event) {
    if (!state.panning || state.panning.pointerId !== event.pointerId) return;
    $("pv-flow-canvas-wrap").classList.remove("panning");
    state.panning = null;
  }

  function openCanvasMenu(event) {
    if (
      closestTarget(event, ".pv-flow-node") ||
      closestTarget(event, ".pv-flow-menu") ||
      closestTarget(event, ".pv-flow-line-hit")
    ) return;
    event.preventDefault();
    state.selectedIndex = -1;
    state.lastCanvasPoint = canvasPoint(event);
    renderInspector();
    openContextMenu(event, [
      submenu("追加", ADD_TYPES.map(([type, label]) => item(label, () => addQuestion(type, null, state.lastCanvasPoint)))),
      submenu("整列", [
        item("上から下へ整列", () => autoLayout("vertical")),
        item("少し広げる", () => autoLayout("loose")),
      ]),
      submenu("表示", [
        item("拡大", () => setZoom(state.zoom + 0.1)),
        item("縮小", () => setZoom(state.zoom - 0.1)),
        item("等倍", () => setZoom(1)),
      ]),
      separator(),
      item("保存", () => save().catch((error) => alert(error.message))),
    ]);
  }

  function openNodeMenu(event, index) {
    event.preventDefault();
    event.stopPropagation();
    state.selectedIndex = index;
    const question = state.form.questions[index];
    render();
    openContextMenu(event, [
      item("次を設定", () => startConnection({ source: "question", index })),
      item("次を解除", () => clearQuestionNext(index)),
      submenu("この後に追加", ADD_TYPES.map(([type, label]) => item(label, () => addQuestion(type, index)))),
      submenu("回答設定", [
        item("回答必須にする", () => setRequired(index, true)),
        item("任意回答にする", () => setRequired(index, false)),
      ]),
      submenu("種類を変える", ADD_TYPES.map(([type, label]) => item(label, () => changeType(index, type)))),
      separator(),
      item("複製", () => duplicateNode(index)),
      item("削除", () => deleteNode(index), "danger"),
    ]);
  }

  function openOptionMenu(event, index, optionId) {
    event.preventDefault();
    event.stopPropagation();
    state.selectedIndex = index;
    renderInspector();
    openContextMenu(event, [
      item("この選択肢の次を設定", () => startConnection({ source: "option", index, optionId })),
      item("この選択肢の次を解除", () => clearOptionNext(index, optionId)),
      separator(),
      item("選択肢を追加", () => addOption(index)),
      item("選択肢を削除", () => removeOption(index, optionId), "danger"),
    ]);
  }

  function openEdgeMenu(event, edge) {
    event.preventDefault();
    event.stopPropagation();
    openContextMenu(event, [
      item("パーテーションを追加", () => addPartitionOnEdge(edge)),
      item("線を解除", () => clearEdge(edge), "danger"),
    ]);
  }

  function openTerminalMenu(event, kind) {
    event.preventDefault();
    event.stopPropagation();
    const entries = kind === "start"
      ? [
          item("次を設定", () => startConnection({ source: "start" })),
          item("次を解除", () => clearStartNext()),
          submenu("追加", ADD_TYPES.map(([type, label]) => item(label, () => addQuestion(type, -1)))),
        ]
      : [
          submenu("追加", ADD_TYPES.map(([type, label]) => item(label, () => addQuestion(type)))),
        ];
    openContextMenu(event, [
      ...entries,
      submenu("整列", [
        item("上から下へ整列", () => autoLayout("vertical")),
        item("少し広げる", () => autoLayout("loose")),
      ]),
      submenu("表示", [
        item("拡大", () => setZoom(state.zoom + 0.1)),
        item("縮小", () => setZoom(state.zoom - 0.1)),
        item("等倍", () => setZoom(1)),
      ]),
      separator(),
      item("保存", () => save().catch((error) => alert(error.message))),
    ]);
  }

  function item(label, action, tone) {
    return { type: "item", label, action, tone };
  }

  function submenu(label, children, disabled) {
    return { type: "submenu", label, children, disabled };
  }

  function separator() {
    return { type: "separator" };
  }

  function openContextMenu(event, entries) {
    closeContextMenu();
    const menu = document.createElement("div");
    menu.className = "pv-flow-menu";
    menu.style.left = `${event.clientX}px`;
    menu.style.top = `${event.clientY}px`;
    entries.forEach((entry) => menu.appendChild(menuEntry(entry)));
    document.body.appendChild(menu);
    state.contextMenu = menu;

    const rect = menu.getBoundingClientRect();
    const openUp = event.clientY > window.innerHeight / 2;
    menu.classList.toggle("drop-up", openUp);
    if (openUp) {
      menu.style.top = `${Math.max(8, event.clientY - rect.height - 8)}px`;
    } else {
      menu.style.top = `${Math.max(8, Math.min(event.clientY, window.innerHeight - rect.height - 8))}px`;
    }
    if (rect.right > window.innerWidth) menu.style.left = `${Math.max(8, window.innerWidth - rect.width - 8)}px`;
  }

  function menuEntry(entry) {
    if (entry.type === "separator") {
      const hr = document.createElement("div");
      hr.className = "pv-flow-menu-separator";
      return hr;
    }

    const button = document.createElement("button");
    button.type = "button";
    button.className = "pv-flow-menu-item";
    if (entry.tone) button.classList.add(entry.tone);
    if (entry.disabled) button.disabled = true;
    button.textContent = entry.label;

    if (entry.type === "submenu") {
      button.classList.add("has-submenu");
      const submenuEl = document.createElement("div");
      submenuEl.className = "pv-flow-submenu";
      entry.children.forEach((child) => submenuEl.appendChild(menuEntry(child)));
      button.appendChild(submenuEl);
    } else {
      button.addEventListener("click", () => {
        closeContextMenu();
        entry.action();
      });
    }
    return button;
  }

  function closeContextMenu() {
    if (state.contextMenu) state.contextMenu.remove();
    state.contextMenu = null;
  }

  function startConnection(connection) {
    state.connecting = connection;
    formSettings().flow_mode = "manual";
    setStateLabel("接続先をクリック");
    render();
  }

  function setConnectionTarget(targetRef) {
    if (!state.connecting) return;
    const connection = state.connecting;
    formSettings().flow_mode = "manual";
    if (connection.source === "start") {
      formSettings().flow_start = targetRef;
    } else if (connection.source === "question") {
      const question = state.form.questions[connection.index];
      question.settings = question.settings || {};
      question.settings.default_next = targetRef;
    } else if (connection.source === "option") {
      const question = state.form.questions[connection.index];
      question.settings = question.settings || {};
      question.settings.branches = question.settings.branches || {};
      question.settings.branches[connection.optionId] = targetRef;
    }
    state.connecting = null;
    setDirty(true);
    render();
  }

  function cancelConnection() {
    state.connecting = null;
    setDirty(state.dirty);
    render();
  }

  function clearStartNext() {
    delete formSettings().flow_start;
    formSettings().flow_mode = "manual";
    setDirty(true);
    render();
  }

  function clearQuestionNext(index) {
    const question = state.form.questions[index];
    if (!question) return;
    question.settings = question.settings || {};
    delete question.settings.default_next;
    formSettings().flow_mode = "manual";
    setDirty(true);
    render();
  }

  function clearOptionNext(index, optionId) {
    const question = state.form.questions[index];
    if (!question) return;
    question.settings = question.settings || {};
    question.settings.branches = question.settings.branches || {};
    delete question.settings.branches[optionId];
    formSettings().flow_mode = "manual";
    setDirty(true);
    render();
  }

  function clearEdge(edge) {
    if (!edge) return;
    if (edge.source === "start") clearStartNext();
    if (edge.source === "question") clearQuestionNext(edge.index);
    if (edge.source === "option") clearOptionNext(edge.index, edge.optionId);
  }

  function addPartitionOnEdge(edge) {
    if (!edge || !edge.targetRef) return;
    const questions = state.form.questions;
    const partition = questionTemplate("partition");
    const midpoint = {
      x: Math.max(40, Math.round(((edge.from?.x || 430) + (edge.to?.x || 430)) / 2 - 125)),
      y: Math.max(150, Math.round(((edge.from?.y || 180) + (edge.to?.y || 360)) / 2 - 55)),
    };
    partition.settings.flow = { ...partition.settings.flow, x: midpoint.x, y: midpoint.y };
    partition.settings.default_next = edge.targetRef;
    const targetQuestion = questionByRef(edge.targetRef);
    const targetIndex = targetQuestion ? questions.indexOf(targetQuestion) : questions.length;
    const sourceIndex = typeof edge.index === "number" ? edge.index : -1;
    const insertAt = sourceIndex >= 0 ? sourceIndex + 1 : Math.max(0, targetIndex);
    questions.splice(insertAt, 0, partition);
    const partitionRef = questionRef(partition);
    if (edge.source === "start") {
      formSettings().flow_start = partitionRef;
    } else if (edge.source === "question") {
      questions[edge.index].settings.default_next = partitionRef;
    } else if (edge.source === "option") {
      questions[edge.index].settings.branches = questions[edge.index].settings.branches || {};
      questions[edge.index].settings.branches[edge.optionId] = partitionRef;
    }
    state.selectedIndex = insertAt;
    formSettings().flow_mode = "manual";
    setDirty(true);
    render();
  }

  function addQuestion(type, afterIndex = null, point = null) {
    const questions = state.form.questions;
    const question = questionTemplate(type);
    if (point) {
      question.settings.flow = { ...question.settings.flow, x: point.x, y: point.y };
    }
    const insertAt = afterIndex == null ? questions.length : Math.max(0, afterIndex + 1);
    questions.splice(insertAt, 0, question);
    state.selectedIndex = insertAt;
    setDirty(true);
    render();
  }

  function ensureFlowBeforeSave() {
    state.form.questions.forEach(flowFor);
    const settings = formSettings();
    if (state.form.questions.length && settings.flow_mode === "manual" && !settings.flow_start) {
      settings.flow_start = questionRef(state.form.questions[0]);
    }
  }

  function duplicateNode(index) {
    const question = state.form.questions[index];
    const copy = JSON.parse(JSON.stringify(question));
    copy.id = null;
    copy.title = `${copy.title || TYPES[copy.type]} コピー`;
    copy.options = (copy.options || []).map((option) => ({ id: uid(), label: option.label }));
    copy.settings = copy.settings || {};
    copy.settings.default_next = "";
    copy.settings.branches = {};
    copy.settings.flow = {
      key: `flow-${uid()}`,
      x: Number(flowFor(question).x || 0) + 34,
      y: Number(flowFor(question).y || 0) + 180,
    };
    state.form.questions.splice(index + 1, 0, copy);
    state.selectedIndex = index + 1;
    setDirty(true);
    render();
  }

  function deleteNode(index) {
    if (index < 0) return;
    const removed = state.form.questions[index];
    state.form.questions.splice(index, 1);
    clearTargetReferences(removed);
    state.selectedIndex = Math.min(index, state.form.questions.length - 1);
    setDirty(true);
    render();
  }

  function clearTargetReferences(removed) {
    const removedRefs = new Set(refsFor(removed));
    if (removedRefs.has(String(formSettings().flow_start))) delete formSettings().flow_start;
    (state.form.questions || []).forEach((question) => {
      if (removedRefs.has(String(question.settings?.default_next))) delete question.settings.default_next;
      const branches = question.settings?.branches || {};
      Object.entries(branches).forEach(([key, target]) => {
        if (removedRefs.has(String(target))) delete branches[key];
      });
    });
  }

  function setRequired(index, required) {
    state.form.questions[index].required = required;
    setDirty(true);
    render();
  }

  function changeType(index, type) {
    const current = state.form.questions[index];
    const replacement = {
      ...questionTemplate(type),
      id: current.id,
      title: current.title,
      description: current.description,
      required: current.required,
      settings: {
        flow: flowFor(current),
        default_next: current.settings?.default_next || "",
      },
    };
    state.form.questions[index] = replacement;
    setDirty(true);
    render();
  }

  function addOption(index) {
    const question = state.form.questions[index];
    if (!question || (question.type !== "single_choice" && question.type !== "multi_choice")) return;
    question.options = question.options || [];
    question.options.push({ id: uid(), label: `選択肢${question.options.length + 1}` });
    setDirty(true);
    render();
  }

  function removeOption(index, optionId) {
    const question = state.form.questions[index];
    if (!question) return;
    question.options = (question.options || []).filter((option) => option.id !== optionId);
    if (question.settings?.branches) delete question.settings.branches[optionId];
    setDirty(true);
    render();
  }

  function autoLayout(mode) {
    const questions = state.form.questions || [];
    const gap = mode === "loose" ? 270 : 220;
    questions.forEach((question, index) => {
      const flow = flowFor(question);
      flow.x = 390;
      flow.y = 180 + index * gap;
    });
    setDirty(true);
    render();
  }

  function setZoom(value) {
    state.zoom = Math.min(1.6, Math.max(0.45, Math.round(value * 10) / 10));
    applyZoomAndSize();
  }

  function editTitle() {
    const title = $("pv-flow-title");
    if (title.dataset.editing === "true") return;
    title.dataset.editing = "true";
    title.contentEditable = "true";
    title.focus();
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(title);
    selection.removeAllRanges();
    selection.addRange(range);
  }

  function commitTitleEdit() {
    const title = $("pv-flow-title");
    if (title.dataset.editing !== "true") return;
    title.dataset.editing = "false";
    title.contentEditable = "false";
    const nextTitle = title.textContent.trim() || "新しいPowerVote";
    if (state.form.title !== nextTitle) {
      state.form.title = nextTitle;
      setDirty(true);
    }
    setFlowTitle(state.form.title);
  }

  function notifyOpener() {
    const message = { type: "powervote:form-saved", formId: state.form.id };
    try {
      if (window.opener && !window.opener.closed) window.opener.postMessage(message, window.location.origin);
    } catch (error) {
      // Cross-window notification is best-effort; the saved form remains authoritative.
    }
    try {
      localStorage.setItem("powervote:last-saved", JSON.stringify({ ...message, at: Date.now() }));
    } catch (error) {
      // Storage can be unavailable in some browser privacy modes.
    }
  }

  function showSaveDialog() {
    closeSaveDialog();
    const overlay = document.createElement("div");
    overlay.className = "pv-flow-save-dialog";
    overlay.innerHTML = `
      <div class="pv-flow-save-dialog-card" role="dialog" aria-modal="true" aria-labelledby="pv-flow-save-dialog-title">
        <h2 id="pv-flow-save-dialog-title">一覧に戻りますか？</h2>
        <p>フローは保存されました。このまま編集を続けることもできます。</p>
        <div>
          <button type="button" data-save-dialog="cancel">キャンセル</button>
          <button type="button" class="pv-primary" data-save-dialog="back">戻る</button>
        </div>
      </div>
    `;
    overlay.addEventListener("click", (event) => {
      const action = event.target.dataset?.saveDialog;
      if (event.target === overlay || action === "cancel") closeSaveDialog();
      if (action === "back") returnToPowerVote();
    });
    document.body.appendChild(overlay);
    state.saveDialog = overlay;
  }

  function closeSaveDialog() {
    if (state.saveDialog) state.saveDialog.remove();
    state.saveDialog = null;
  }

  function returnToPowerVote() {
    closeSaveDialog();
    const listUrl = "/tools/powervote/";
    if (window.opener && !window.opener.closed) {
      try {
        window.opener.location.href = listUrl;
        window.close();
        setTimeout(() => {
          window.location.href = listUrl;
        }, 250);
        return;
      } catch (error) {
        // Fall through to same-tab navigation.
      }
    }
    window.location.href = listUrl;
  }

  function bind() {
    document.querySelectorAll("[data-flow-add]").forEach((button) => {
      button.addEventListener("click", () => addQuestion(button.dataset.flowAdd));
    });
    $("pv-flow-canvas-wrap").addEventListener("contextmenu", openCanvasMenu);
    $("pv-flow-canvas-wrap").addEventListener("wheel", handleCanvasWheel, { passive: false });
    $("pv-flow-canvas-wrap").addEventListener("pointerdown", startCanvasPan);
    $("pv-flow-canvas-wrap").addEventListener("pointermove", moveCanvasPan);
    $("pv-flow-canvas-wrap").addEventListener("pointerup", endCanvasPan);
    $("pv-flow-canvas-wrap").addEventListener("pointercancel", endCanvasPan);
    $("pv-flow-save").addEventListener("click", () => save().catch((error) => alert(error.message)));
    $("pv-flow-title").addEventListener("click", editTitle);
    $("pv-flow-title").addEventListener("blur", commitTitleEdit);
    $("pv-flow-title").addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        $("pv-flow-title").blur();
      }
      if (event.key === "Escape") {
        event.preventDefault();
        $("pv-flow-title").textContent = state.form.title || "PowerVote Flow";
        $("pv-flow-title").blur();
      }
    });
    $("pv-flow-zoom-in").addEventListener("click", () => setZoom(state.zoom + 0.1));
    $("pv-flow-zoom-out").addEventListener("click", () => setZoom(state.zoom - 0.1));
    $("pv-flow-zoom-reset").addEventListener("click", () => setZoom(1));
    $("pv-flow-node-title").addEventListener("input", () => updateSelected("title", $("pv-flow-node-title").value));
    $("pv-flow-node-description").addEventListener("input", () => updateSelected("description", $("pv-flow-node-description").value));
    $("pv-flow-node-required").addEventListener("input", () => updateSelected("required", $("pv-flow-node-required").value === "true"));
    $("pv-flow-node-type").addEventListener("input", () => changeType(state.selectedIndex, $("pv-flow-node-type").value));
    $("pv-flow-add-option").addEventListener("click", () => addOption(state.selectedIndex));
    $("pv-flow-delete-node").addEventListener("click", () => deleteNode(state.selectedIndex));
    document.addEventListener("click", closeContextMenu);
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        closeContextMenu();
        if (state.connecting) cancelConnection();
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        save().catch((error) => alert(error.message));
      }
    });
  }

  function updateSelected(field, value) {
    const selected = state.form.questions[state.selectedIndex];
    if (!selected) return;
    selected[field] = value;
    setDirty(true);
    render();
  }

  async function load() {
    const payload = await api(`/forms/${formId}`);
    state.form = payload.form;
    state.form.questions = state.form.questions || [];
    state.form.questions.forEach(flowFor);
    setFlowTitle(state.form.title);
    state.selectedIndex = state.form.questions.length ? 0 : -1;
    setDirty(false);
    render();
  }

  async function save() {
    commitTitleEdit();
    ensureFlowBeforeSave();
    const payload = await api(`/forms/${formId}`, { method: "PUT", body: JSON.stringify(state.form) });
    state.form = payload.form;
    state.form.questions = state.form.questions || [];
    state.form.questions.forEach(flowFor);
    setFlowTitle(state.form.title);
    setDirty(false);
    render();
    notifyOpener();
    showSaveDialog();
  }

  bind();
  load().catch((error) => {
    setStateLabel("エラー");
    alert(error.message);
  });
})();
