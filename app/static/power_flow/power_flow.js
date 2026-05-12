(function () {
  const root = document.getElementById("power-flow-app");
  if (!root) return;

  const SVG_NS = "http://www.w3.org/2000/svg";
  const STORAGE_KEY = "dstt.powerFlow.documents";
  const AUTOSAVE_KEY = "dstt.powerFlow.autosave";
  const LAUNCH_DOC_KEY = "dstt.powerFlow.launchDoc";
  const LAUNCH_MODE_KEY = "dstt.powerFlow.launchMode";
  const SCHEMA_VERSION = 1;
  const EDGE_SLOT_SPACING = 18;
  const EDGE_OBSTACLE_PADDING = 18;
  const EDGE_STUB_LENGTH = 24;

  const SHAPE_CATEGORIES = [
    {
      key: "basic",
      label: "基本",
      shapes: [
        ["startEnd", "開始/終了"],
        ["process", "処理"],
        ["decision", "分岐"],
        ["io", "入出力"],
        ["data", "データ"],
        ["note", "注記"],
        ["warning", "注意"],
        ["phone", "電話"],
        ["mail", "メール"],
      ],
    },
    {
      key: "special",
      label: "特殊",
      shapes: [
        ["car", "車"],
        ["police", "警察"],
        ["hospital", "病院"],
        ["insurance", "保険"],
      ],
    },
    {
      key: "other",
      label: "その他",
      shapes: [
        ["database", "DB"],
        ["manual", "手作業"],
        ["document", "書類"],
        ["image", "画像"],
        ["subflow", "サブフロー"],
      ],
    },
  ];

  const EDGE_PRESETS = [
    { key: "default", label: "標準", color: null, width: 2.5, dashed: false, routing: "orthogonal" },
    { key: "thick", label: "太線", color: null, width: 4, dashed: false, routing: "orthogonal" },
    { key: "thin", label: "細線", color: null, width: 1.5, dashed: false, routing: "orthogonal" },
    { key: "dashed", label: "点線", color: null, width: 2.5, dashed: true, routing: "orthogonal" },
    { key: "alert", label: "強調(赤)", color: "#dc2626", width: 3, dashed: false, routing: "orthogonal" },
    { key: "info", label: "補助(青点)", color: "#2563eb", width: 2, dashed: true, routing: "orthogonal" },
    { key: "curved", label: "曲線", color: null, width: 2.5, dashed: false, routing: "curved" },
  ];

  const GRID_SIZES = [8, 16, 24, 32, 48];

  const SHAPES = SHAPE_CATEGORIES.flatMap((cat) => cat.shapes);

  const CANVAS_PRESETS = [
    { label: "A4 縦", width: 794, height: 1123 },
    { label: "A4 横", width: 1123, height: 794 },
    { label: "A3 縦", width: 1123, height: 1587 },
    { label: "A3 横", width: 1587, height: 1123 },
    { label: "B5 縦", width: 665, height: 945 },
    { label: "B5 横", width: 945, height: 665 },
  ];

  const THEMES = {
    standard: {
      label: "標準",
      canvas: "#fbfcfe",
      lane: "#f3f7fb",
      palette: { fill: "#ffffff", stroke: "#53657e", text: "#172033", edge: "#59667b" },
    },
    simple: {
      label: "シンプル",
      canvas: "#ffffff",
      lane: "#f6f6f6",
      palette: { fill: "#ffffff", stroke: "#444444", text: "#1f1f1f", edge: "#555555" },
    },
    monochrome: {
      label: "モノクロ",
      canvas: "#ffffff",
      lane: "#f2f2f2",
      palette: { fill: "#ffffff", stroke: "#111111", text: "#111111", edge: "#111111" },
    },
    alert: {
      label: "注意喚起",
      canvas: "#fffdf8",
      lane: "#fff4e0",
      palette: { fill: "#ffffff", stroke: "#b53b32", text: "#301f1d", edge: "#b53b32" },
    },
    dark: {
      label: "ダーク",
      canvas: "#202733",
      lane: "#273142",
      palette: { fill: "#303b4f", stroke: "#8fb7e6", text: "#f6f8fb", edge: "#a6bdd7" },
    },
  };

  const TYPE_LABELS = Object.fromEntries(SHAPES);
  const ICONS = {
    phone: "TEL",
    mail: "@",
    car: "CAR",
    police: "POL",
    hospital: "H",
    insurance: "INS",
    warning: "!",
    image: "IMG",
    subflow: "SUB",
  };

  const $ = (id) => document.getElementById(id);
  const svg = $("pf-canvas");
  const wrap = $("pf-canvas-wrap");
  const scaleLayer = $("pf-canvas-scale");

  const state = {
    doc: createBlankDoc(),
    selectedNodeIds: [],
    selectedConnectorId: null,
    tool: "select",
    pendingConnectorNodeId: null,
    zoom: 1,
    dirty: false,
    history: [],
    future: [],
    drag: null,
    pan: null,
    styleClipboard: null,
    nodeClipboard: [],
    lastPointer: { x: 180, y: 160 },
    connectorPreview: null,
    marquee: null,
    edgeDrag: null,
    searchHits: [],
    searchIndex: 0,
    rotateAction: null,
    pageBreakPreview: false,
    laneDrag: null,
    nodeClipboardData: null,
    contextNodeId: null,
  };

  function uid(prefix) {
    if (window.crypto && crypto.randomUUID) return `${prefix}-${crypto.randomUUID()}`;
    return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  }

  function clone(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function theme() {
    return THEMES[state.doc.theme] || THEMES.standard;
  }

  function createBlankDoc() {
    return {
      schemaVersion: SCHEMA_VERSION,
      id: uid("flow"),
      title: "新しいフロー",
      theme: "standard",
      canvas: { width: 1123, height: 794, grid: 24, snap: true, showGrid: true },
      nodes: [],
      connectors: [],
      lanes: [],
      updatedAt: new Date().toISOString(),
    };
  }

  function defaultStyle(kind) {
    const base = theme().palette;
    const fills = {
      startEnd: "#e7f7ef",
      decision: "#fff4d7",
      warning: "#fff0ee",
      note: "#fffbe6",
      phone: "#edf7ff",
      mail: "#f0f6ff",
      car: "#f4f8fb",
      police: "#edf2ff",
      hospital: "#effaf2",
      insurance: "#f6f1ff",
      image: "#f8fafc",
      subflow: "#eef5ff",
    };
    return {
      fill: fills[kind] || base.fill,
      stroke: kind === "warning" ? "#c23b3b" : (kind === "subflow" ? "#2563eb" : base.stroke),
      text: base.text,
      strokeWidth: kind === "subflow" ? 2.5 : 2,
      dashed: false,
    };
  }

  function nodeTemplate(type, x, y, text) {
    const size = {
      startEnd: [160, 68],
      decision: [180, 120],
      document: [160, 90],
      database: [160, 90],
      note: [170, 92],
      image: [180, 130],
      subflow: [180, 90],
    }[type] || [160, 76];
    const node = {
      id: uid("node"),
      type,
      x,
      y,
      width: size[0],
      height: size[1],
      text: text || TYPE_LABELS[type] || "処理",
      style: defaultStyle(type),
      locked: false,
      rotation: 0,
    };
    if (type === "decision") {
      node.choices = ["はい", "いいえ"];
    }
    if (type === "image") {
      node.imageData = "";
    }
    if (type === "subflow") {
      node.subflowRef = "";
    }
    return node;
  }

  function connectorTemplate(from, to, label) {
    return {
      id: uid("edge"),
      from,
      to,
      label: label || "",
      style: { color: theme().palette.edge, width: 2.5, dashed: false, arrow: "end", routing: "orthogonal" },
    };
  }

  function laneTemplate(index) {
    const height = 170;
    return {
      id: uid("lane"),
      name: `レーン ${index + 1}`,
      x: 0,
      y: 80 + index * height,
      width: state.doc.canvas.width,
      height,
      color: index % 2 ? "#f8fafc" : theme().lane,
    };
  }

  const TEMPLATES = {
    blank: {
      label: "空白",
      build: () => createBlankDoc(),
    },
    basic: {
      label: "基本フローチャート",
      build: () => {
        const doc = createBlankDoc();
        doc.title = "基本フローチャート";
        const start = nodeTemplate("startEnd", 300, 120, "開始");
        const work = nodeTemplate("process", 300, 240, "処理を行う");
        const check = nodeTemplate("decision", 300, 380, "条件を満たす？");
        const end = nodeTemplate("startEnd", 300, 560, "完了");
        doc.nodes = [start, work, check, end];
        doc.connectors = [
          connectorTemplate(start.id, work.id, ""),
          connectorTemplate(work.id, check.id, ""),
          connectorTemplate(check.id, end.id, "はい"),
        ];
        return doc;
      },
    },
    accident: {
      label: "車の事故対応",
      build: () => {
        const doc = createBlankDoc();
        doc.title = "車の事故発生時の対応";
        doc.theme = "alert";
        doc.canvas.height = 1600;
        const labels = [
          ["car", "事故発生"],
          ["decision", "安全な場所へ移動できる？"],
          ["hospital", "けが人を確認"],
          ["phone", "救急へ連絡"],
          ["police", "警察へ連絡"],
          ["process", "相手方情報を確認"],
          ["process", "現場写真を撮影"],
          ["insurance", "保険会社へ連絡"],
          ["process", "レッカー・修理手配"],
          ["document", "記録と保険手続き"],
          ["startEnd", "完了"],
        ];
        doc.nodes = labels.map(([type, text], index) => nodeTemplate(type, 360, 90 + index * 130, text));
        doc.connectors = doc.nodes.slice(0, -1).map((node, index) => connectorTemplate(node.id, doc.nodes[index + 1].id, ""));
        doc.connectors[1].label = "はい";
        doc.connectors[2].label = "けがあり";
        return doc;
      },
    },
    swimlane: {
      label: "スイムレーン付き",
      build: () => {
        const doc = createBlankDoc();
        doc.title = "スイムレーン付きフロー";
        doc.lanes = [
          { id: uid("lane"), name: "担当者", x: 0, y: 80, width: doc.canvas.width, height: 180, color: "#eef7f4" },
          { id: uid("lane"), name: "管理者", x: 0, y: 260, width: doc.canvas.width, height: 180, color: "#f8fafc" },
          { id: uid("lane"), name: "システム", x: 0, y: 440, width: doc.canvas.width, height: 180, color: "#f7f2ff" },
        ];
        const a = nodeTemplate("startEnd", 220, 130, "開始");
        const b = nodeTemplate("process", 480, 130, "申請を作成");
        const c = nodeTemplate("decision", 740, 300, "承認する？");
        const d = nodeTemplate("process", 1010, 490, "結果を登録");
        const e = nodeTemplate("startEnd", 1260, 490, "完了");
        doc.nodes = [a, b, c, d, e];
        doc.connectors = [
          connectorTemplate(a.id, b.id, ""),
          connectorTemplate(b.id, c.id, ""),
          connectorTemplate(c.id, d.id, "はい"),
          connectorTemplate(d.id, e.id, ""),
        ];
        return doc;
      },
    },
    yesno: {
      label: "はい/いいえ判断",
      build: () => {
        const doc = createBlankDoc();
        doc.title = "はい / いいえ判断フロー";
        const s = nodeTemplate("startEnd", 260, 160, "開始");
        const q = nodeTemplate("decision", 260, 300, "分岐");
        const yes = nodeTemplate("process", 60, 480, "はいの場合");
        const no = nodeTemplate("process", 460, 480, "いいえの場合");
        const e = nodeTemplate("startEnd", 260, 650, "完了");
        doc.nodes = [s, q, yes, no, e];
        doc.connectors = [
          connectorTemplate(s.id, q.id, ""),
          connectorTemplate(q.id, yes.id, "はい"),
          connectorTemplate(q.id, no.id, "いいえ"),
          connectorTemplate(yes.id, e.id, ""),
          connectorTemplate(no.id, e.id, ""),
        ];
        return doc;
      },
    },
  };

  function setDirty(dirty) {
    state.dirty = dirty;
    $("pf-save-state").textContent = dirty ? "未保存" : "保存済み";
    state.doc.updatedAt = new Date().toISOString();
    try {
      localStorage.setItem(AUTOSAVE_KEY, JSON.stringify(state.doc));
    } catch (error) {
      /* best-effort */
    }
  }

  function pushHistory() {
    state.history.push(clone(state.doc));
    if (state.history.length > 80) state.history.shift();
    state.future = [];
  }

  function mutate(fn) {
    pushHistory();
    fn();
    setDirty(true);
    render();
  }

  function restoreDoc(doc) {
    state.doc = clone(doc);
    state.selectedNodeIds = [];
    state.selectedConnectorId = null;
    state.pendingConnectorNodeId = null;
    $("pf-title").value = state.doc.title || "新しいフロー";
    $("pf-grid-toggle").checked = state.doc.canvas.showGrid !== false;
    $("pf-snap-toggle").checked = state.doc.canvas.snap !== false;
    render();
  }

  function render() {
    state.doc.title = $("pf-title").value || "新しいフロー";
    svg.setAttribute("width", String(state.doc.canvas.width));
    svg.setAttribute("height", String(state.doc.canvas.height));
    svg.setAttribute("viewBox", `0 0 ${state.doc.canvas.width} ${state.doc.canvas.height}`);
    svg.classList.toggle("no-grid", state.doc.canvas.showGrid === false);
    svg.replaceChildren();
    renderDefs();
    renderCanvasBackground();
    renderLanes(svg);
    renderConnectors(svg);
    renderNodes(svg);
    renderConnectorPreview(svg);
    renderMarquee(svg);
    renderPageBreaks(svg);
    applyZoom();
    renderInspector();
    renderWarnings();
    renderMinimap();
    renderLaneList();
    populateGridSelect();
    updateSearchUI();
    updateStatus();
  }

  function renderDefs() {
    const defs = el("defs");
    const edgeColor = theme().palette.edge;
    const seen = new Set();
    state.doc.connectors.forEach((edge) => {
      const color = edge.style?.color || edgeColor;
      const markerId = arrowMarkerId(color);
      if (!seen.has(markerId)) {
        seen.add(markerId);
        defs.appendChild(createArrowMarker(markerId, color));
      }
    });
    const defaultMarkerId = arrowMarkerId(edgeColor);
    if (!seen.has(defaultMarkerId)) {
      defs.appendChild(createArrowMarker(defaultMarkerId, edgeColor));
    }
    svg.appendChild(defs);
  }

  function arrowMarkerId(color) {
    return "pf-arrow-" + color.replace(/[^a-fA-F0-9]/g, "");
  }

  function createArrowMarker(id, color) {
    const marker = el("marker", {
      id,
      markerWidth: 10,
      markerHeight: 10,
      refX: 9,
      refY: 5,
      orient: "auto-start-reverse",
      markerUnits: "userSpaceOnUse",
    });
    marker.appendChild(el("path", {
      d: "M0,0 L9,5 L0,10",
      fill: "none",
      stroke: color,
      "stroke-width": 1.6,
      "stroke-linejoin": "miter",
      "stroke-linecap": "round",
    }));
    return marker;
  }

  function renderCanvasBackground() {
    svg.appendChild(el("rect", {
      class: "pf-canvas-bg",
      x: 0, y: 0,
      width: state.doc.canvas.width,
      height: state.doc.canvas.height,
      fill: theme().canvas,
    }));
    const grid = state.doc.canvas.grid || 24;
    const group = el("g", { class: "pf-grid" });
    for (let x = 0; x <= state.doc.canvas.width; x += grid) {
      group.appendChild(el("line", { class: "pf-grid-line", x1: x, y1: 0, x2: x, y2: state.doc.canvas.height, stroke: "#e2e8f0", "stroke-width": x % (grid * 5) === 0 ? 1.2 : 0.7 }));
    }
    for (let y = 0; y <= state.doc.canvas.height; y += grid) {
      group.appendChild(el("line", { class: "pf-grid-line", x1: 0, y1: y, x2: state.doc.canvas.width, y2: y, stroke: "#e2e8f0", "stroke-width": y % (grid * 5) === 0 ? 1.2 : 0.7 }));
    }
    svg.appendChild(group);
  }

  function renderLanes(target) {
    const group = el("g", { class: "pf-lanes" });
    state.doc.lanes.forEach((lane) => {
      const laneGroup = el("g", { class: "pf-lane", "data-id": lane.id });
      laneGroup.appendChild(el("rect", {
        x: lane.x, y: lane.y,
        width: lane.width, height: lane.height,
        fill: lane.color || theme().lane,
      }));
      laneGroup.appendChild(textEl(lane.x + 18, lane.y + 30, lane.name, { "text-anchor": "start", "dominant-baseline": "middle" }));
      laneGroup.addEventListener("dblclick", () => renameLane(lane.id));
      group.appendChild(laneGroup);
    });
    target.appendChild(group);
  }

  function renderConnectors(target) {
    const group = el("g", { class: "pf-connectors" });
    const layout = buildConnectorLayout();
    state.doc.connectors.forEach((edge) => {
      const from = nodeById(edge.from);
      const to = nodeById(edge.to);
      if (!from || !to) return;
      const points = connectorPoints(edge, from, to, layout);
      const edgeColor = edge.style?.color || theme().palette.edge;
      const markerId = arrowMarkerId(edgeColor);
      const routing = edge.style?.routing || "orthogonal";
      const path = el("path", {
        class: `pf-connector ${edge.style?.dashed ? "dashed" : "solid"} ${state.selectedConnectorId === edge.id ? "selected" : ""}`,
        d: connectorPathD(points, routing),
        fill: "none",
        stroke: edgeColor,
        "stroke-width": edge.style?.width || 2.5,
        "stroke-linejoin": "round",
        "stroke-linecap": "round",
        "marker-end": `url(#${markerId})`,
      });
      if (edge.style?.dashed) path.setAttribute("stroke-dasharray", "8 5");
      path.addEventListener("contextmenu", (event) => {
        event.preventDefault();
        event.stopPropagation();
        selectEdge(edge.id);
        showContextMenu(event.clientX, event.clientY, "edge");
      });
      path.addEventListener("pointerdown", (event) => {
        startEdgePointer(event, edge, points);
      });
      group.appendChild(path);
      if (state.selectedConnectorId === edge.id && routing !== "curved") {
        const handlePoint = connectorHandlePoint(points);
        const handle = el("circle", {
          class: "pf-connector-handle",
          cx: handlePoint.x,
          cy: handlePoint.y,
          r: 7,
        });
        handle.addEventListener("pointerdown", (event) => {
          startEdgePointer(event, edge, points);
        });
        group.appendChild(handle);
      }
      if (edge.label) {
        const labelPoint = connectorLabelPoint(points);
        const mx = labelPoint.x;
        const my = labelPoint.y - 10;
        const label = textEl(mx, my, edge.label, { class: "pf-connector-label" });
        group.appendChild(label);
      }
    });
    target.appendChild(group);
  }

  function connectorPathD(points, routing) {
    if (routing === "curved") {
      const dx = points.x2 - points.x1;
      const dy = points.y2 - points.y1;
      const cx1 = points.x1 + dx * 0.5;
      const cy1 = points.y1;
      const cx2 = points.x1 + dx * 0.5;
      const cy2 = points.y2;
      return `M ${points.x1} ${points.y1} C ${cx1} ${cy1} ${cx2} ${cy2} ${points.x2} ${points.y2}`;
    }
    if (points.route?.length) {
      return pathDFromPoints(points.route);
    }
    if (!points.manual && (points.x1 === points.x2 || points.y1 === points.y2)) {
      return `M ${points.x1} ${points.y1} L ${points.x2} ${points.y2}`;
    }
    return orthogonalPathThroughPoint(points);
  }

  function renderConnectorPreview(target) {
    if (!state.pendingConnectorNodeId || !state.connectorPreview) return;
    const from = nodeById(state.pendingConnectorNodeId);
    if (!from) return;
    const fc = center(from);
    const tx = state.connectorPreview.x;
    const ty = state.connectorPreview.y;
    const line = el("line", {
      class: "pf-connector-preview",
      x1: fc.x, y1: fc.y,
      x2: tx, y2: ty,
      stroke: theme().palette.edge,
      "stroke-width": 2,
      "stroke-dasharray": "6 4",
      opacity: 0.6,
    });
    target.appendChild(line);
  }

  function renderMarquee(target) {
    if (!state.marquee) return;
    const m = state.marquee;
    const x = Math.min(m.startX, m.x);
    const y = Math.min(m.startY, m.y);
    const width = Math.abs(m.x - m.startX);
    const height = Math.abs(m.y - m.startY);
    if (width < 2 && height < 2) return;
    target.appendChild(el("rect", {
      class: "pf-marquee",
      x, y, width, height,
      fill: "rgba(59, 130, 246, 0.12)",
      stroke: "#3b82f6",
      "stroke-width": 1.2,
      "stroke-dasharray": "5 3",
    }));
  }

  function nodesIntersectingRect(rect) {
    return state.doc.nodes.filter((node) => {
      return node.x < rect.x + rect.width
        && node.x + node.width > rect.x
        && node.y < rect.y + rect.height
        && node.y + node.height > rect.y;
    });
  }

  function renderNodes(target) {
    const group = el("g", { class: "pf-nodes" });
    state.doc.nodes.forEach((node) => {
      const isSelected = state.selectedNodeIds.includes(node.id);
      const isPendingSource = state.pendingConnectorNodeId === node.id;
      const isSearchHit = state.searchHits.includes(node.id);
      const rotation = Number(node.rotation) || 0;
      const transform = rotation
        ? `translate(${node.x} ${node.y}) rotate(${rotation} ${node.width / 2} ${node.height / 2})`
        : `translate(${node.x} ${node.y})`;
      const nodeGroup = el("g", {
        class: `pf-node ${isSelected ? "selected" : ""} ${isPendingSource ? "connector-source" : ""} ${isSearchHit ? "search-hit" : ""} ${node.locked ? "locked" : ""}`,
        transform,
        "data-id": node.id,
      });
      const titleEl = document.createElementNS(SVG_NS, "title");
      titleEl.textContent = (node.text || TYPE_LABELS[node.type] || "").slice(0, 200);
      nodeGroup.appendChild(titleEl);
      nodeGroup.appendChild(shapeElement(node));
      if (node.type === "image" && node.imageData) {
        const img = el("image", {
          x: 4, y: 4,
          width: node.width - 8, height: node.height - 8,
          preserveAspectRatio: "xMidYMid meet",
        });
        img.setAttributeNS("http://www.w3.org/1999/xlink", "xlink:href", node.imageData);
        img.setAttribute("href", node.imageData);
        nodeGroup.appendChild(img);
      } else if (ICONS[node.type]) {
        nodeGroup.appendChild(textEl(16, 20, ICONS[node.type], { class: "pf-node-badge", "text-anchor": "start" }));
      }
      if (node.type === "subflow") {
        nodeGroup.appendChild(textEl(node.width - 12, 18, "↗", { class: "pf-subflow-icon", "text-anchor": "end" }));
      }
      if (node.locked) {
        nodeGroup.appendChild(textEl(node.width - 10, node.height - 8, "🔒", { class: "pf-lock-icon", "text-anchor": "end" }));
      }
      if (node.type !== "image" || !node.imageData) {
        wrappedText(nodeGroup, node);
      }
      if (isSelected && !node.locked) {
        nodeGroup.appendChild(el("rect", {
          class: "pf-resize",
          x: node.width - 7, y: node.height - 7,
          width: 14, height: 14, rx: 3,
        }));
        if (rotation !== 0 || isSelected) {
          nodeGroup.appendChild(el("circle", {
            class: "pf-rotate-handle",
            cx: node.width / 2, cy: -22, r: 7,
          }));
          nodeGroup.appendChild(el("line", {
            class: "pf-rotate-line",
            x1: node.width / 2, y1: -15,
            x2: node.width / 2, y2: 0,
            stroke: "#1e67c7", "stroke-width": 1.2, "stroke-dasharray": "3 2",
          }));
        }
      }
      if (state.tool === "connector") {
        const ports = getNodePorts(node);
        ports.forEach((port) => {
          nodeGroup.appendChild(el("circle", {
            class: "pf-port",
            cx: port.x, cy: port.y, r: 6,
          }));
        });
      }
      nodeGroup.addEventListener("pointerdown", (event) => startNodePointer(event, node.id));
      nodeGroup.addEventListener("dblclick", (event) => {
        event.stopPropagation();
        if (node.type === "subflow" && node.subflowRef) {
          navigateToSubflow(node);
        } else {
          editNodeText(node.id);
        }
      });
      nodeGroup.addEventListener("contextmenu", (event) => {
        event.preventDefault();
        event.stopPropagation();
        state.contextNodeId = node.id;
        if (!state.selectedNodeIds.includes(node.id)) {
          state.selectedNodeIds = [node.id];
          state.selectedConnectorId = null;
          render();
        }
        showContextMenu(event.clientX, event.clientY, "node");
      });
      group.appendChild(nodeGroup);
    });
    target.appendChild(group);
  }

  function getNodePorts(node) {
    return [
      { x: node.width / 2, y: 0 },
      { x: node.width, y: node.height / 2 },
      { x: node.width / 2, y: node.height },
      { x: 0, y: node.height / 2 },
    ];
  }

  function shapeElement(node) {
    const attrs = {
      class: "pf-node-shape",
      fill: node.style?.fill || defaultStyle(node.type).fill,
      stroke: node.style?.stroke || defaultStyle(node.type).stroke,
      "stroke-width": node.style?.strokeWidth || 2,
    };
    if (node.style?.dashed) attrs["stroke-dasharray"] = "8 5";
    if (node.type === "startEnd") return el("rect", { ...attrs, x: 0, y: 0, width: node.width, height: node.height, rx: node.height / 2 });
    if (node.type === "decision") return el("polygon", { ...attrs, points: `${node.width / 2},0 ${node.width},${node.height / 2} ${node.width / 2},${node.height} 0,${node.height / 2}` });
    if (node.type === "io") return el("polygon", { ...attrs, points: `18,0 ${node.width},0 ${node.width - 18},${node.height} 0,${node.height}` });
    if (node.type === "document") return el("path", { ...attrs, d: `M0 0 H${node.width} V${node.height - 18} Q${node.width * 0.75} ${node.height + 10} ${node.width * 0.5} ${node.height - 8} Q${node.width * 0.25} ${node.height - 26} 0 ${node.height - 8} Z` });
    if (node.type === "database") return el("path", { ...attrs, d: `M0 18 C0 -6 ${node.width} -6 ${node.width} 18 V${node.height - 18} C${node.width} ${node.height + 6} 0 ${node.height + 6} 0 ${node.height - 18} Z M0 18 C0 42 ${node.width} 42 ${node.width} 18` });
    if (node.type === "manual") return el("polygon", { ...attrs, points: `0,12 ${node.width * 0.82},0 ${node.width},${node.height - 12} ${node.width * 0.18},${node.height}` });
    if (node.type === "note") return el("path", { ...attrs, d: `M0 0 H${node.width - 22} L${node.width} 22 V${node.height} H0 Z M${node.width - 22} 0 V22 H${node.width}` });
    if (node.type === "subflow") return el("rect", { ...attrs, x: 0, y: 0, width: node.width, height: node.height, rx: 8, "stroke-dasharray": node.style?.dashed ? "8 5" : "0" });
    if (node.type === "image") return el("rect", { ...attrs, x: 0, y: 0, width: node.width, height: node.height, rx: 4 });
    return el("rect", { ...attrs, x: 0, y: 0, width: node.width, height: node.height, rx: 8 });
  }

  function wrappedText(group, node) {
    const fontSize = 16;
    const lineHeight = 21;
    const text = el("text", {
      class: "pf-node-text",
      x: node.width / 2,
      y: node.height / 2,
      fill: node.style?.text || theme().palette.text,
      "font-size": fontSize,
      "font-weight": 700,
      "text-anchor": "middle",
      "dominant-baseline": "middle",
    });
    const lines = wrapLines(node.text || "", Math.max(5, Math.floor(node.width / 15)));
    const start = -((lines.length - 1) * lineHeight) / 2;
    lines.slice(0, 5).forEach((line, index) => {
      const tspan = el("tspan", { x: node.width / 2, dy: index === 0 ? start : lineHeight });
      tspan.textContent = line;
      text.appendChild(tspan);
    });
    group.appendChild(text);
  }

  function wrapLines(text, maxChars) {
    const raw = String(text || "").split(/\n/);
    const lines = [];
    raw.forEach((part) => {
      let buffer = "";
      Array.from(part).forEach((char) => {
        buffer += char;
        if (buffer.length >= maxChars) {
          lines.push(buffer);
          buffer = "";
        }
      });
      if (buffer || !part) lines.push(buffer);
    });
    return lines.length ? lines : [""];
  }

  function buildConnectorLayout() {
    const incoming = new Map();
    const outgoing = new Map();
    const meta = new Map();
    state.doc.connectors.forEach((edge) => {
      const from = nodeById(edge.from);
      const to = nodeById(edge.to);
      if (!from || !to) return;
      const sides = connectorSides(edge, from, to);
      meta.set(edge.id, sides);
      addEdgeSlot(incoming, to.id, sides.targetSide, edge, from, to, "target");
      addEdgeSlot(outgoing, from.id, sides.sourceSide, edge, from, to, "source");
    });
    return {
      meta,
      incoming: assignEdgeSlots(incoming),
      outgoing: assignEdgeSlots(outgoing),
    };
  }

  function addEdgeSlot(map, nodeId, side, edge, from, to, role) {
    const key = `${nodeId}:${side}`;
    const other = role === "target" ? center(from) : center(to);
    const sortValue = side === "left" || side === "right" ? other.y : other.x;
    if (!map.has(key)) map.set(key, { node: role === "target" ? to : from, side, items: [] });
    map.get(key).items.push({ edgeId: edge.id, sortValue });
  }

  function assignEdgeSlots(map) {
    const slots = new Map();
    map.forEach(({ node, side, items }) => {
      const extent = side === "left" || side === "right" ? node.height : node.width;
      const sorted = [...items].sort((a, b) => a.sortValue - b.sortValue || String(a.edgeId).localeCompare(String(b.edgeId)));
      sorted.forEach((item, index) => {
        slots.set(item.edgeId, slotOffset(index, sorted.length, extent));
      });
    });
    return slots;
  }

  function slotOffset(index, total, extent) {
    if (total <= 1) return 0;
    const usable = Math.max(0, extent - 28);
    const spacing = Math.min(EDGE_SLOT_SPACING, usable / Math.max(1, total - 1));
    return (index - (total - 1) / 2) * spacing;
  }

  function connectorSides(edge, from, to) {
    const manualPoint = storedBendPoint(edge, from, to);
    if (manualPoint) {
      const fc = center(from);
      const tc = center(to);
      return {
        sourceSide: sideFacingPoint(fc, manualPoint),
        targetSide: sideFacingPoint(tc, manualPoint),
      };
    }
    return nearestSides(from, to);
  }

  function nearestSides(from, to) {
    const allSides = ["top", "right", "bottom", "left"];
    let bestDist = Infinity;
    let bestSource = "right";
    let bestTarget = "left";
    allSides.forEach((srcSide) => {
      const src = anchorPoint(from, srcSide, 0);
      allSides.forEach((tgtSide) => {
        const tgt = anchorPoint(to, tgtSide, 0);
        const dist = Math.hypot(tgt.x - src.x, tgt.y - src.y);
        if (dist < bestDist) {
          bestDist = dist;
          bestSource = srcSide;
          bestTarget = tgtSide;
        }
      });
    });
    return { sourceSide: bestSource, targetSide: bestTarget };
  }

  function connectorPoints(edge, from, to, layout) {
    const sides = layout?.meta.get(edge.id) || connectorSides(edge, from, to);
    const start = anchorPoint(from, sides.sourceSide, layout?.outgoing.get(edge.id) || 0);
    const end = anchorPoint(to, sides.targetSide, layout?.incoming.get(edge.id) || 0);
    const manualPoint = storedBendPoint(edge, from, to);
    const bendPoint = manualPoint || defaultBendPoint(start, end, sides);
    return {
      x1: start.x,
      y1: start.y,
      x2: end.x,
      y2: end.y,
      bx: bendPoint.x,
      by: bendPoint.y,
      sourceSide: sides.sourceSide,
      targetSide: sides.targetSide,
      manual: Boolean(manualPoint),
      route: routeConnector(edge, start, end, sides, bendPoint),
    };
  }

  function storedBendPoint(edge, from, to) {
    const point = edge?.style?.bendPoint;
    const x = Number(point?.x);
    const y = Number(point?.y);
    if (Number.isFinite(x) && Number.isFinite(y)) return { x, y };

    const legacyAxis = edge?.style?.bendAxis;
    const legacyBend = Number(edge?.style?.bend);
    if (Number.isFinite(legacyBend)) {
      const fc = center(from);
      const tc = center(to);
      if (legacyAxis === "x") return { x: legacyBend, y: (fc.y + tc.y) / 2 };
      if (legacyAxis === "y") return { x: (fc.x + tc.x) / 2, y: legacyBend };
    }
    return null;
  }

  function defaultBendPoint(start, end, sides) {
    return { x: (start.x + end.x) / 2, y: (start.y + end.y) / 2 };
  }

  function sideFacingPoint(centerPoint, point) {
    const dx = point.x - centerPoint.x;
    const dy = point.y - centerPoint.y;
    if (Math.abs(dx) >= Math.abs(dy)) return dx >= 0 ? "right" : "left";
    return dy >= 0 ? "bottom" : "top";
  }

  function anchorPoint(node, side, offset) {
    if (side === "left" || side === "right") {
      const limit = Math.max(0, node.height / 2 - 14);
      return {
        x: side === "left" ? node.x : node.x + node.width,
        y: node.y + node.height / 2 + clamp(offset, -limit, limit),
      };
    }
    const limit = Math.max(0, node.width / 2 - 14);
    return {
      x: node.x + node.width / 2 + clamp(offset, -limit, limit),
      y: side === "top" ? node.y : node.y + node.height,
    };
  }

  function connectorHandlePoint(points) {
    if (points.route?.length >= 2) return routeMidpoint(points.route);
    return { x: points.bx, y: points.by };
  }

  function routeMidpoint(route) {
    let totalLen = 0;
    for (let i = 1; i < route.length; i++) {
      totalLen += Math.hypot(route[i].x - route[i - 1].x, route[i].y - route[i - 1].y);
    }
    let half = totalLen / 2;
    for (let i = 1; i < route.length; i++) {
      const segLen = Math.hypot(route[i].x - route[i - 1].x, route[i].y - route[i - 1].y);
      if (half <= segLen || i === route.length - 1) {
        const t = segLen > 0 ? half / segLen : 0;
        return {
          x: route[i - 1].x + (route[i].x - route[i - 1].x) * t,
          y: route[i - 1].y + (route[i].y - route[i - 1].y) * t,
        };
      }
      half -= segLen;
    }
    return { x: route[0].x, y: route[0].y };
  }

  function connectorLabelPoint(points) {
    return connectorHandlePoint(points);
  }

  function routeConnector(edge, start, end, sides, bendPoint) {
    const sourceStub = shiftPoint(start, sides.sourceSide, EDGE_STUB_LENGTH);
    const targetStub = shiftPoint(end, sides.targetSide, EDGE_STUB_LENGTH);
    const obstacles = connectorObstacles(edge);
    const routed = findOrthogonalRoute(sourceStub, targetStub, bendPoint, obstacles);
    const inner = routed?.length ? routed : [
      sourceStub,
      turnPointForSide(sourceStub, sides.sourceSide, bendPoint),
      bendPoint,
      turnPointForSide(targetStub, sides.targetSide, bendPoint),
      targetStub,
    ];
    return simplifyRoute([start, ...inner, end]);
  }

  function connectorObstacles(edge) {
    return state.doc.nodes
      .filter((node) => node.id !== edge.from && node.id !== edge.to)
      .map((node) => ({
        x: node.x - EDGE_OBSTACLE_PADDING,
        y: node.y - EDGE_OBSTACLE_PADDING,
        width: node.width + EDGE_OBSTACLE_PADDING * 2,
        height: node.height + EDGE_OBSTACLE_PADDING * 2,
      }));
  }

  function findOrthogonalRoute(start, end, preferred, obstacles) {
    const canvasW = state.doc.canvas.width;
    const canvasH = state.doc.canvas.height;
    const xs = uniqueNumbers([
      start.x, end.x, preferred.x,
      0, canvasW,
      ...obstacles.flatMap((rect) => [rect.x, rect.x + rect.width]),
    ].map((value) => clamp(value, 0, canvasW)));
    const ys = uniqueNumbers([
      start.y, end.y, preferred.y,
      0, canvasH,
      ...obstacles.flatMap((rect) => [rect.y, rect.y + rect.height]),
    ].map((value) => clamp(value, 0, canvasH)));

    const points = [];
    const indexByKey = new Map();
    xs.forEach((x) => {
      ys.forEach((y) => {
        addRoutePoint(points, indexByKey, { x, y }, obstacles);
      });
    });
    const startIndex = addRoutePoint(points, indexByKey, start, obstacles, true);
    const endIndex = addRoutePoint(points, indexByKey, end, obstacles, true);
    if (startIndex < 0 || endIndex < 0) return null;

    const neighbors = buildRouteNeighbors(points, obstacles);
    return shortestRoute(points, neighbors, startIndex, endIndex, preferred);
  }

  function addRoutePoint(points, indexByKey, point, obstacles, force = false) {
    const next = { x: roundCoord(point.x), y: roundCoord(point.y) };
    const key = pointKey(next);
    if (indexByKey.has(key)) return indexByKey.get(key);
    if (!force && obstacles.some((rect) => pointInsideRect(next, rect))) return -1;
    const index = points.length;
    points.push(next);
    indexByKey.set(key, index);
    return index;
  }

  function buildRouteNeighbors(points, obstacles) {
    const neighbors = new Map(points.map((_, index) => [index, []]));
    const byY = new Map();
    const byX = new Map();
    points.forEach((point, index) => {
      const xKey = roundCoord(point.x);
      const yKey = roundCoord(point.y);
      if (!byY.has(yKey)) byY.set(yKey, []);
      if (!byX.has(xKey)) byX.set(xKey, []);
      byY.get(yKey).push(index);
      byX.get(xKey).push(index);
    });
    byY.forEach((indexes) => {
      indexes.sort((a, b) => points[a].x - points[b].x);
      connectRouteLine(points, neighbors, indexes, obstacles, "x");
    });
    byX.forEach((indexes) => {
      indexes.sort((a, b) => points[a].y - points[b].y);
      connectRouteLine(points, neighbors, indexes, obstacles, "y");
    });
    return neighbors;
  }

  function connectRouteLine(points, neighbors, indexes, obstacles, dir) {
    for (let i = 0; i < indexes.length - 1; i += 1) {
      const a = indexes[i];
      const b = indexes[i + 1];
      if (!segmentClear(points[a], points[b], obstacles)) continue;
      const distance = manhattan(points[a], points[b]);
      neighbors.get(a).push({ to: b, dir, distance });
      neighbors.get(b).push({ to: a, dir, distance });
    }
  }

  function shortestRoute(points, neighbors, startIndex, endIndex, preferred) {
    const queue = [{ index: startIndex, dir: "", cost: 0, key: `${startIndex}|` }];
    const dist = new Map([[`${startIndex}|`, 0]]);
    const prev = new Map();
    let bestKey = "";
    while (queue.length) {
      queue.sort((a, b) => a.cost - b.cost);
      const current = queue.shift();
      if (current.cost !== dist.get(current.key)) continue;
      if (current.index === endIndex) {
        bestKey = current.key;
        break;
      }
      (neighbors.get(current.index) || []).forEach((edge) => {
        const turnPenalty = current.dir && current.dir !== edge.dir ? 12 : 0;
        const bias = distancePointToSegment(preferred, points[current.index], points[edge.to]) * 0.015;
        const nextCost = current.cost + edge.distance + turnPenalty + bias;
        const nextKey = `${edge.to}|${edge.dir}`;
        if (nextCost >= (dist.get(nextKey) ?? Infinity)) return;
        dist.set(nextKey, nextCost);
        prev.set(nextKey, current.key);
        queue.push({ index: edge.to, dir: edge.dir, cost: nextCost, key: nextKey });
      });
    }
    if (!bestKey) return null;
    const result = [];
    let key = bestKey;
    while (key) {
      const index = Number(key.split("|")[0]);
      result.push(points[index]);
      key = prev.get(key);
    }
    return simplifyRoute(result.reverse());
  }

  function uniqueNumbers(values) {
    return Array.from(new Set(values.map(roundCoord))).sort((a, b) => a - b);
  }

  function roundCoord(value) {
    return Math.round(Number(value) * 100) / 100;
  }

  function pointKey(point) {
    return `${roundCoord(point.x)},${roundCoord(point.y)}`;
  }

  function pointInsideRect(point, rect) {
    return point.x > rect.x && point.x < rect.x + rect.width
      && point.y > rect.y && point.y < rect.y + rect.height;
  }

  function segmentClear(a, b, obstacles) {
    return !obstacles.some((rect) => segmentIntersectsRect(a, b, rect));
  }

  function segmentIntersectsRect(a, b, rect) {
    if (Math.abs(a.y - b.y) < 0.01) {
      const y = a.y;
      if (y <= rect.y || y >= rect.y + rect.height) return false;
      return rangesOverlap(Math.min(a.x, b.x), Math.max(a.x, b.x), rect.x, rect.x + rect.width);
    }
    if (Math.abs(a.x - b.x) < 0.01) {
      const x = a.x;
      if (x <= rect.x || x >= rect.x + rect.width) return false;
      return rangesOverlap(Math.min(a.y, b.y), Math.max(a.y, b.y), rect.y, rect.y + rect.height);
    }
    return true;
  }

  function rangesOverlap(a1, a2, b1, b2) {
    return Math.max(a1, b1) < Math.min(a2, b2);
  }

  function manhattan(a, b) {
    return Math.abs(a.x - b.x) + Math.abs(a.y - b.y);
  }

  function distancePointToSegment(point, a, b) {
    if (Math.abs(a.x - b.x) < 0.01) {
      const minY = Math.min(a.y, b.y);
      const maxY = Math.max(a.y, b.y);
      if (point.y >= minY && point.y <= maxY) return Math.abs(point.x - a.x);
      return Math.min(manhattan(point, a), manhattan(point, b));
    }
    if (Math.abs(a.y - b.y) < 0.01) {
      const minX = Math.min(a.x, b.x);
      const maxX = Math.max(a.x, b.x);
      if (point.x >= minX && point.x <= maxX) return Math.abs(point.y - a.y);
      return Math.min(manhattan(point, a), manhattan(point, b));
    }
    return Math.min(manhattan(point, a), manhattan(point, b));
  }

  function simplifyRoute(route) {
    const compact = route.filter((point, index) => {
      if (index === 0) return true;
      const prev = route[index - 1];
      return Math.abs(point.x - prev.x) > 0.01 || Math.abs(point.y - prev.y) > 0.01;
    });
    const simplified = [];
    compact.forEach((point) => {
      simplified.push(point);
      while (simplified.length >= 3) {
        const a = simplified[simplified.length - 3];
        const b = simplified[simplified.length - 2];
        const c = simplified[simplified.length - 1];
        const collinear = (Math.abs(a.x - b.x) < 0.01 && Math.abs(b.x - c.x) < 0.01)
          || (Math.abs(a.y - b.y) < 0.01 && Math.abs(b.y - c.y) < 0.01);
        if (!collinear) break;
        simplified.splice(simplified.length - 2, 1);
      }
    });
    return simplified;
  }

  function pathDFromPoints(route) {
    return route.map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`).join(" ");
  }

  function shiftPoint(point, side, amount) {
    if (side === "left") return { x: clamp(point.x - amount, 0, state.doc.canvas.width), y: point.y };
    if (side === "right") return { x: clamp(point.x + amount, 0, state.doc.canvas.width), y: point.y };
    if (side === "top") return { x: point.x, y: clamp(point.y - amount, 0, state.doc.canvas.height) };
    return { x: point.x, y: clamp(point.y + amount, 0, state.doc.canvas.height) };
  }

  function orthogonalPathThroughPoint(points) {
    const bend = { x: points.bx, y: points.by };
    const route = [
      { x: points.x1, y: points.y1 },
      turnPointForSide({ x: points.x1, y: points.y1 }, points.sourceSide, bend),
      bend,
      turnPointForSide({ x: points.x2, y: points.y2 }, points.targetSide, bend),
      { x: points.x2, y: points.y2 },
    ];
    const compact = route.filter((point, index) => {
      if (index === 0) return true;
      const prev = route[index - 1];
      return Math.abs(point.x - prev.x) > 0.01 || Math.abs(point.y - prev.y) > 0.01;
    });
    return compact.map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`).join(" ");
  }

  function turnPointForSide(anchor, side, bend) {
    if (side === "left" || side === "right") return { x: bend.x, y: anchor.y };
    return { x: anchor.x, y: bend.y };
  }

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function center(node) {
    return { x: node.x + node.width / 2, y: node.y + node.height / 2 };
  }

  function nodeById(id) {
    return state.doc.nodes.find((node) => node.id === id);
  }

  function edgeById(id) {
    return state.doc.connectors.find((edge) => edge.id === id);
  }

  function el(name, attrs = {}) {
    const node = document.createElementNS(SVG_NS, name);
    Object.entries(attrs).forEach(([key, value]) => {
      if (value !== undefined && value !== null) node.setAttribute(key, String(value));
    });
    return node;
  }

  function textEl(x, y, value, attrs = {}) {
    const text = el("text", { x, y, ...attrs });
    text.textContent = value;
    return text;
  }

  function canvasPoint(event) {
    const rect = svg.getBoundingClientRect();
    return {
      x: (event.clientX - rect.left) / state.zoom,
      y: (event.clientY - rect.top) / state.zoom,
    };
  }

  function snap(value) {
    if (state.doc.canvas.snap === false) return value;
    const grid = state.doc.canvas.grid || 24;
    return Math.round(value / grid) * grid;
  }

  function addNodeAt(type, point) {
    mutate(() => {
      const node = nodeTemplate(type, snap(point.x - 80), snap(point.y - 38));
      snapNodeToLane(node);
      state.doc.nodes.push(node);
      state.selectedNodeIds = [node.id];
      state.selectedConnectorId = null;
      state.tool = "select";
    });
  }

  function snapNodeToLane(node) {
    const lane = state.doc.lanes.find((item) => node.y >= item.y && node.y <= item.y + item.height);
    if (!lane) return;
    node.y = Math.max(lane.y + 18, Math.min(lane.y + lane.height - node.height - 18, node.y));
  }

  function selectNode(id, additive) {
    if (state.tool === "connector") {
      connectNode(id);
      return;
    }
    state.selectedConnectorId = null;
    if (additive) {
      state.selectedNodeIds = state.selectedNodeIds.includes(id)
        ? state.selectedNodeIds.filter((item) => item !== id)
        : [...state.selectedNodeIds, id];
    } else if (!state.selectedNodeIds.includes(id)) {
      state.selectedNodeIds = [id];
    }
    render();
  }

  function selectEdge(id) {
    state.selectedConnectorId = id;
    state.selectedNodeIds = [];
    render();
  }

  function clearSelection() {
    state.selectedNodeIds = [];
    state.selectedConnectorId = null;
    state.pendingConnectorNodeId = null;
    state.connectorPreview = null;
    render();
  }

  function startNodePointer(event, id) {
    if (event.button !== 0) return;
    event.stopPropagation();
    closeContextMenu();
    const point = canvasPoint(event);
    state.lastPointer = point;
    if (state.tool === "connector") {
      connectNode(id);
      return;
    }
    selectNode(id, event.shiftKey || event.ctrlKey || event.metaKey);
    const target = event.target;
    const isRotating = target.classList && target.classList.contains("pf-rotate-handle");
    const resizing = target.classList && target.classList.contains("pf-resize");
    const node = nodeById(id);
    if (node?.locked && !isRotating) {
      target.ownerSVGElement.setPointerCapture(event.pointerId);
      return;
    }
    if (isRotating) {
      const center = { x: node.x + node.width / 2, y: node.y + node.height / 2 };
      state.rotateAction = {
        nodeId: id,
        center,
        startAngle: Math.atan2(point.y - center.y, point.x - center.x),
        baseRotation: Number(node.rotation) || 0,
        committed: false,
      };
      target.ownerSVGElement.setPointerCapture(event.pointerId);
      return;
    }
    const selected = state.doc.nodes
      .filter((node) => state.selectedNodeIds.includes(node.id) && !node.locked)
      .map((node) => ({ id: node.id, x: node.x, y: node.y, width: node.width, height: node.height }));
    state.drag = { mode: resizing ? "resize" : "move", start: point, selected, committed: false };
    target.ownerSVGElement.setPointerCapture(event.pointerId);
  }

  function startEdgePointer(event, edge, points) {
    if (event.button !== 0) return;
    event.stopPropagation();
    closeContextMenu();
    const point = canvasPoint(event);
    state.selectedConnectorId = edge.id;
    state.selectedNodeIds = [];
    state.edgeDrag = {
      edgeId: edge.id,
      start: point,
      originPoint: connectorHandlePoint(points),
      committed: false,
    };
    svg.setPointerCapture(event.pointerId);
    render();
  }

  function moveDrag(event) {
    if (state.rotateAction) {
      const point = canvasPoint(event);
      const action = state.rotateAction;
      const angle = Math.atan2(point.y - action.center.y, point.x - action.center.x);
      const delta = (angle - action.startAngle) * (180 / Math.PI);
      const node = nodeById(action.nodeId);
      if (node) {
        let next = action.baseRotation + delta;
        if (event.shiftKey) next = Math.round(next / 15) * 15;
        node.rotation = ((next % 360) + 360) % 360;
        if (!action.committed) {
          pushHistory();
          action.committed = true;
        }
        setDirty(true);
        render();
      }
      return;
    }
    if (!state.drag) return;
    const point = canvasPoint(event);
    const dx = point.x - state.drag.start.x;
    const dy = point.y - state.drag.start.y;
    state.drag.selected.forEach((origin) => {
      const node = nodeById(origin.id);
      if (!node || node.locked) return;
      if (state.drag.mode === "resize") {
        node.width = Math.max(48, snap(origin.width + dx));
        node.height = Math.max(32, snap(origin.height + dy));
      } else {
        node.x = Math.max(0, snap(origin.x + dx));
        node.y = Math.max(0, snap(origin.y + dy));
        snapNodeToLane(node);
      }
    });
    if (!state.drag.committed) {
      pushHistory();
      state.drag.committed = true;
      const movedIds = new Set(state.drag.selected.map((s) => s.id));
      state.doc.connectors.forEach((edge) => {
        if (movedIds.has(edge.from) || movedIds.has(edge.to)) {
          if (edge.style) {
            delete edge.style.bendPoint;
            delete edge.style.bendAxis;
            delete edge.style.bend;
          }
        }
      });
    }
    setDirty(true);
    render();
  }

  function moveEdgeDrag(event) {
    if (!state.edgeDrag) return;
    const edge = edgeById(state.edgeDrag.edgeId);
    if (!edge) return;
    const point = canvasPoint(event);
    const dx = point.x - state.edgeDrag.start.x;
    const dy = point.y - state.edgeDrag.start.y;
    if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;
    if (!state.edgeDrag.committed) {
      pushHistory();
      state.edgeDrag.committed = true;
    }
    edge.style = edge.style || {};
    edge.style.routing = "orthogonal";
    edge.style.bendPoint = {
      x: clamp(state.edgeDrag.originPoint.x + dx, 0, state.doc.canvas.width),
      y: clamp(state.edgeDrag.originPoint.y + dy, 0, state.doc.canvas.height),
    };
    delete edge.style.bendAxis;
    delete edge.style.bend;
    setDirty(true);
    render();
  }

  function endDrag() {
    const hadEdgeDrag = state.edgeDrag;
    state.drag = null;
    state.rotateAction = null;
    state.edgeDrag = null;
    if (hadEdgeDrag) render();
  }

  function connectNode(id) {
    if (!state.pendingConnectorNodeId) {
      state.pendingConnectorNodeId = id;
      $("pf-hint").textContent = "接続先のノードをクリックしてください。Escで解除。";
      render();
      return;
    }
    if (state.pendingConnectorNodeId !== id) {
      mutate(() => {
        state.doc.connectors.push(connectorTemplate(state.pendingConnectorNodeId, id, ""));
        state.selectedConnectorId = state.doc.connectors[state.doc.connectors.length - 1].id;
        state.selectedNodeIds = [];
        state.pendingConnectorNodeId = null;
        state.connectorPreview = null;
      });
    } else {
      state.pendingConnectorNodeId = null;
      state.connectorPreview = null;
      render();
    }
  }

  function editNodeText(id) {
    const node = nodeById(id);
    if (!node) return;
    const value = window.prompt("テキストを入力", node.text || "");
    if (value === null) return;
    mutate(() => {
      node.text = value;
    });
  }

  function editEdgeLabel(id) {
    const edge = edgeById(id);
    if (!edge) return;
    const value = window.prompt("ラベルを入力", edge.label || "");
    if (value === null) return;
    mutate(() => {
      edge.label = value;
    });
  }

  function renameLane(id) {
    const lane = state.doc.lanes.find((item) => item.id === id);
    if (!lane) return;
    const value = window.prompt("レーン名", lane.name);
    if (value === null) return;
    mutate(() => {
      lane.name = value.trim() || lane.name;
    });
  }

  function deleteSelection() {
    if (state.selectedConnectorId) {
      mutate(() => {
        state.doc.connectors = state.doc.connectors.filter((edge) => edge.id !== state.selectedConnectorId);
        state.selectedConnectorId = null;
      });
      return;
    }
    if (!state.selectedNodeIds.length) return;
    mutate(() => {
      const ids = new Set(state.selectedNodeIds);
      state.doc.nodes = state.doc.nodes.filter((node) => !ids.has(node.id));
      state.doc.connectors = state.doc.connectors.filter((edge) => !ids.has(edge.from) && !ids.has(edge.to));
      state.selectedNodeIds = [];
    });
  }

  function duplicateSelected() {
    if (!state.selectedNodeIds.length) return;
    mutate(() => {
      const idMap = new Map();
      const copies = state.doc.nodes
        .filter((node) => state.selectedNodeIds.includes(node.id))
        .map((node) => {
          const copy = clone(node);
          const nextId = uid("node");
          idMap.set(node.id, nextId);
          copy.id = nextId;
          copy.x += 36;
          copy.y += 36;
          return copy;
        });
      const copiedEdges = state.doc.connectors
        .filter((edge) => idMap.has(edge.from) && idMap.has(edge.to))
        .map((edge) => ({ ...clone(edge), id: uid("edge"), from: idMap.get(edge.from), to: idMap.get(edge.to) }));
      state.doc.nodes.push(...copies);
      state.doc.connectors.push(...copiedEdges);
      state.selectedNodeIds = copies.map((node) => node.id);
    });
  }

  function autoAlign(mode) {
    const nodes = selectedOrAllNodes();
    if (!nodes.length) return;
    mutate(() => {
      const sorted = [...nodes].sort((a, b) => mode === "horizontal" ? a.x - b.x : a.y - b.y);
      const startX = Math.min(...sorted.map((node) => node.x));
      const startY = Math.min(...sorted.map((node) => node.y));
      sorted.forEach((node, index) => {
        if (mode === "horizontal") {
          node.x = snap(startX + index * 240);
          node.y = snap(startY);
        } else {
          node.x = snap(startX);
          node.y = snap(startY + index * 150);
        }
        snapNodeToLane(node);
      });
    });
  }

  function selectedOrAllNodes() {
    if (state.selectedNodeIds.length) return state.doc.nodes.filter((node) => state.selectedNodeIds.includes(node.id));
    return state.doc.nodes;
  }

  function selectedNodes() {
    return state.doc.nodes.filter((node) => state.selectedNodeIds.includes(node.id));
  }

  function alignSelected(mode) {
    const nodes = selectedNodes();
    if (nodes.length < 2) return;
    mutate(() => {
      const xs = nodes.map((n) => n.x);
      const ys = nodes.map((n) => n.y);
      const rights = nodes.map((n) => n.x + n.width);
      const bottoms = nodes.map((n) => n.y + n.height);
      const centersX = nodes.map((n) => n.x + n.width / 2);
      const centersY = nodes.map((n) => n.y + n.height / 2);
      const minX = Math.min(...xs), maxR = Math.max(...rights);
      const minY = Math.min(...ys), maxB = Math.max(...bottoms);
      const avgCX = (Math.min(...centersX) + Math.max(...centersX)) / 2;
      const avgCY = (Math.min(...centersY) + Math.max(...centersY)) / 2;
      nodes.forEach((node) => {
        if (mode === "left") node.x = minX;
        else if (mode === "right") node.x = maxR - node.width;
        else if (mode === "top") node.y = minY;
        else if (mode === "bottom") node.y = maxB - node.height;
        else if (mode === "centerH") node.x = Math.round(avgCX - node.width / 2);
        else if (mode === "centerV") node.y = Math.round(avgCY - node.height / 2);
      });
    });
  }

  function distributeSelected(mode) {
    const nodes = selectedNodes();
    if (nodes.length < 3) return;
    mutate(() => {
      if (mode === "horizontal") {
        const sorted = [...nodes].sort((a, b) => (a.x + a.width / 2) - (b.x + b.width / 2));
        const totalWidth = sorted.reduce((sum, n) => sum + n.width, 0);
        const start = sorted[0].x;
        const end = sorted[sorted.length - 1].x + sorted[sorted.length - 1].width;
        const span = end - start;
        const gap = (span - totalWidth) / (sorted.length - 1);
        let cursor = start;
        sorted.forEach((node) => {
          node.x = Math.round(cursor);
          cursor += node.width + gap;
        });
      } else if (mode === "vertical") {
        const sorted = [...nodes].sort((a, b) => (a.y + a.height / 2) - (b.y + b.height / 2));
        const totalHeight = sorted.reduce((sum, n) => sum + n.height, 0);
        const start = sorted[0].y;
        const end = sorted[sorted.length - 1].y + sorted[sorted.length - 1].height;
        const span = end - start;
        const gap = (span - totalHeight) / (sorted.length - 1);
        let cursor = start;
        sorted.forEach((node) => {
          node.y = Math.round(cursor);
          cursor += node.height + gap;
        });
      }
    });
  }

  function unifySize(mode) {
    const nodes = selectedNodes();
    if (nodes.length < 2) return;
    mutate(() => {
      const widths = nodes.map((n) => n.width);
      const heights = nodes.map((n) => n.height);
      const w = Math.max(...widths);
      const h = Math.max(...heights);
      nodes.forEach((node) => {
        if (mode === "width" || mode === "both") node.width = w;
        if (mode === "height" || mode === "both") node.height = h;
      });
    });
  }

  function bulkUpdateStyle(field, value) {
    const nodes = selectedNodes();
    if (!nodes.length) return;
    mutate(() => {
      nodes.forEach((node) => {
        node.style = node.style || defaultStyle(node.type);
        if (field === "fill") node.style.fill = value;
        else if (field === "stroke") node.style.stroke = value;
        else if (field === "text") node.style.text = value;
        else if (field === "strokeWidth") node.style.strokeWidth = Math.max(1, Number(value) || 2);
      });
    });
  }

  // ── Z-order ──
  function bringSelectionTo(position) {
    if (!state.selectedNodeIds.length) return;
    mutate(() => {
      const ids = new Set(state.selectedNodeIds);
      const selected = state.doc.nodes.filter((n) => ids.has(n.id));
      const others = state.doc.nodes.filter((n) => !ids.has(n.id));
      if (position === "front") state.doc.nodes = [...others, ...selected];
      else if (position === "back") state.doc.nodes = [...selected, ...others];
      else if (position === "forward") {
        const arr = [...state.doc.nodes];
        for (let i = arr.length - 2; i >= 0; i -= 1) {
          if (ids.has(arr[i].id) && !ids.has(arr[i + 1].id)) {
            [arr[i], arr[i + 1]] = [arr[i + 1], arr[i]];
          }
        }
        state.doc.nodes = arr;
      } else if (position === "backward") {
        const arr = [...state.doc.nodes];
        for (let i = 1; i < arr.length; i += 1) {
          if (ids.has(arr[i].id) && !ids.has(arr[i - 1].id)) {
            [arr[i], arr[i - 1]] = [arr[i - 1], arr[i]];
          }
        }
        state.doc.nodes = arr;
      }
    });
  }

  // ── Lock/unlock ──
  function toggleLockSelected() {
    if (!state.selectedNodeIds.length) return;
    mutate(() => {
      const allLocked = selectedNodes().every((n) => n.locked);
      selectedNodes().forEach((n) => { n.locked = !allLocked; });
    });
  }

  // ── Copy/Paste ──
  function copySelectedToClipboard() {
    const nodes = selectedNodes();
    if (!nodes.length) return;
    const ids = new Set(nodes.map((n) => n.id));
    const internalEdges = state.doc.connectors.filter((e) => ids.has(e.from) && ids.has(e.to));
    state.nodeClipboardData = {
      nodes: clone(nodes),
      connectors: clone(internalEdges),
    };
    $("pf-hint").textContent = `${nodes.length}個のノードをコピーしました。Ctrl+Vで貼り付け。`;
  }

  function pasteClipboard() {
    const data = state.nodeClipboardData;
    if (!data || !data.nodes?.length) return;
    mutate(() => {
      const idMap = new Map();
      const offset = 36;
      const copies = data.nodes.map((node) => {
        const next = clone(node);
        next.id = uid("node");
        next.x = (node.x || 0) + offset;
        next.y = (node.y || 0) + offset;
        idMap.set(node.id, next.id);
        return next;
      });
      const copiedEdges = (data.connectors || []).map((edge) => ({
        ...clone(edge),
        id: uid("edge"),
        from: idMap.get(edge.from),
        to: idMap.get(edge.to),
      }));
      state.doc.nodes.push(...copies);
      state.doc.connectors.push(...copiedEdges);
      state.selectedNodeIds = copies.map((n) => n.id);
      state.selectedConnectorId = null;
    });
  }

  // ── Search ──
  function searchNodes(query) {
    const q = String(query || "").trim().toLowerCase();
    if (!q) {
      state.searchHits = [];
      state.searchIndex = 0;
      render();
      return 0;
    }
    state.searchHits = state.doc.nodes
      .filter((n) => String(n.text || "").toLowerCase().includes(q))
      .map((n) => n.id);
    state.searchIndex = 0;
    if (state.searchHits.length) jumpToSearchHit(0);
    render();
    return state.searchHits.length;
  }

  function jumpToSearchHit(index) {
    if (!state.searchHits.length) return;
    const i = ((index % state.searchHits.length) + state.searchHits.length) % state.searchHits.length;
    state.searchIndex = i;
    const node = nodeById(state.searchHits[i]);
    if (!node) return;
    state.selectedNodeIds = [node.id];
    state.selectedConnectorId = null;
    const cx = (node.x + node.width / 2) * state.zoom;
    const cy = (node.y + node.height / 2) * state.zoom;
    wrap.scrollLeft = Math.max(0, cx - wrap.clientWidth / 2);
    wrap.scrollTop = Math.max(0, cy - wrap.clientHeight / 2);
    render();
  }

  function clearSearch() {
    state.searchHits = [];
    state.searchIndex = 0;
    const input = $("pf-search-input");
    if (input) input.value = "";
    $("pf-search-bar")?.classList.add("hidden");
    render();
  }

  // ── Auto-layout (hierarchical, top-to-bottom) ──
  function autoLayout() {
    if (!state.doc.nodes.length) return;
    mutate(() => {
      const nodes = state.doc.nodes;
      const adj = new Map(nodes.map((n) => [n.id, []]));
      const indegree = new Map(nodes.map((n) => [n.id, 0]));
      state.doc.connectors.forEach((edge) => {
        if (adj.has(edge.from) && adj.has(edge.to)) {
          adj.get(edge.from).push(edge.to);
          indegree.set(edge.to, (indegree.get(edge.to) || 0) + 1);
        }
      });
      const layer = new Map();
      const queue = [];
      nodes.forEach((n) => {
        if ((indegree.get(n.id) || 0) === 0) {
          layer.set(n.id, 0);
          queue.push(n.id);
        }
      });
      while (queue.length) {
        const id = queue.shift();
        const lv = layer.get(id) || 0;
        (adj.get(id) || []).forEach((next) => {
          const nextLv = Math.max(layer.get(next) || 0, lv + 1);
          layer.set(next, nextLv);
          if ((indegree.get(next) || 0) > 0) {
            indegree.set(next, indegree.get(next) - 1);
            if (indegree.get(next) === 0) queue.push(next);
          }
        });
      }
      nodes.forEach((n) => {
        if (!layer.has(n.id)) layer.set(n.id, 0);
      });
      const layers = new Map();
      nodes.forEach((n) => {
        const lv = layer.get(n.id);
        if (!layers.has(lv)) layers.set(lv, []);
        layers.get(lv).push(n);
      });
      const xGap = 60;
      const yGap = 80;
      const startX = 80;
      const startY = 60;
      [...layers.entries()].sort((a, b) => a[0] - b[0]).forEach(([lv, layerNodes]) => {
        const totalW = layerNodes.reduce((sum, n) => sum + n.width, 0) + (layerNodes.length - 1) * xGap;
        const canvasW = state.doc.canvas.width;
        let cursorX = Math.max(startX, (canvasW - totalW) / 2);
        const rowY = startY + lv * (160 + yGap);
        layerNodes.forEach((n) => {
          n.x = Math.round(cursorX);
          n.y = Math.round(rowY);
          cursorX += n.width + xGap;
        });
      });
    });
    fitZoom();
  }

  // ── Connection validation ──
  function runValidation() {
    const issues = [];
    const nodes = state.doc.nodes;
    const edges = state.doc.connectors;
    const startNodes = nodes.filter((n) => n.type === "startEnd" && !edges.some((e) => e.to === n.id));
    if (startNodes.length > 1) issues.push(`開始/終了ノードが入口側に${startNodes.length}個あります`);
    nodes.forEach((n) => {
      if (n.type === "decision") {
        const out = edges.filter((e) => e.from === n.id);
        if (out.length < 2) issues.push(`分岐ノード「${truncateLabel(n.text)}」の分岐先が${out.length}個です`);
      }
    });
    nodes.forEach((n) => {
      const connected = edges.some((e) => e.from === n.id || e.to === n.id);
      if (!connected && nodes.length > 1) issues.push(`未接続ノード: ${truncateLabel(n.text || TYPE_LABELS[n.type])}`);
    });
    edges.forEach((n) => {
      if (n.type === "decision") return;
    });
    nodes.forEach((n) => {
      if (n.type !== "startEnd" && !edges.some((e) => e.from === n.id) && nodes.length > 1) {
        issues.push(`出口の無いノード: ${truncateLabel(n.text || TYPE_LABELS[n.type])}`);
      }
    });
    const cycles = detectCycles(nodes, edges);
    if (cycles.length) issues.push(`循環参照を検出: ${cycles.length}件`);
    return issues;
  }

  function truncateLabel(text) {
    const t = String(text || "");
    return t.length > 20 ? `${t.slice(0, 20)}…` : t;
  }

  function detectCycles(nodes, edges) {
    const adj = new Map(nodes.map((n) => [n.id, []]));
    edges.forEach((e) => { if (adj.has(e.from)) adj.get(e.from).push(e.to); });
    const cycles = [];
    const WHITE = 0, GRAY = 1, BLACK = 2;
    const color = new Map(nodes.map((n) => [n.id, WHITE]));
    function dfs(id) {
      color.set(id, GRAY);
      (adj.get(id) || []).forEach((next) => {
        if (color.get(next) === GRAY) cycles.push([id, next]);
        else if (color.get(next) === WHITE) dfs(next);
      });
      color.set(id, BLACK);
    }
    nodes.forEach((n) => { if (color.get(n.id) === WHITE) dfs(n.id); });
    return cycles;
  }

  // ── Mermaid export ──
  function exportMermaid() {
    const lines = ["flowchart TD"];
    const safe = (s) => String(s || "").replace(/[\n\r]/g, " ").replace(/"/g, "'").slice(0, 80);
    state.doc.nodes.forEach((n) => {
      const id = n.id.replace(/[^a-zA-Z0-9]/g, "_");
      const text = safe(n.text || TYPE_LABELS[n.type]);
      let shape;
      if (n.type === "startEnd") shape = `(["${text}"])`;
      else if (n.type === "decision") shape = `{"${text}"}`;
      else if (n.type === "io") shape = `[/"${text}"/]`;
      else if (n.type === "database") shape = `[("${text}")]`;
      else if (n.type === "document") shape = `[\\"${text}"\\]`;
      else if (n.type === "subflow") shape = `[["${text}"]]`;
      else shape = `["${text}"]`;
      lines.push(`  ${id}${shape}`);
    });
    state.doc.connectors.forEach((e) => {
      const from = e.from.replace(/[^a-zA-Z0-9]/g, "_");
      const to = e.to.replace(/[^a-zA-Z0-9]/g, "_");
      const arrow = e.style?.dashed ? "-..->" : "-->";
      const label = safe(e.label);
      lines.push(label ? `  ${from} ${arrow}|${label}| ${to}` : `  ${from} ${arrow} ${to}`);
    });
    const mmd = lines.join("\n");
    downloadBlob(mmd, "text/plain;charset=utf-8", `${safeFilename(state.doc.title)}.mmd`);
  }

  // ── Page break preview ──
  function togglePageBreakPreview() {
    state.pageBreakPreview = !state.pageBreakPreview;
    document.querySelectorAll("[data-action='page-break']").forEach((b) => b.classList.toggle("active", state.pageBreakPreview));
    render();
  }

  function renderPageBreaks(target) {
    if (!state.pageBreakPreview) return;
    const cw = state.doc.canvas.width;
    const ch = state.doc.canvas.height;
    const pageW = 794;
    const pageH = 1123;
    const group = el("g", { class: "pf-page-breaks" });
    for (let x = pageW; x < cw; x += pageW) {
      group.appendChild(el("line", {
        x1: x, y1: 0, x2: x, y2: ch,
        stroke: "#dc2626", "stroke-width": 1.5, "stroke-dasharray": "8 6", opacity: 0.6,
      }));
    }
    for (let y = pageH; y < ch; y += pageH) {
      group.appendChild(el("line", {
        x1: 0, y1: y, x2: cw, y2: y,
        stroke: "#dc2626", "stroke-width": 1.5, "stroke-dasharray": "8 6", opacity: 0.6,
      }));
    }
    target.appendChild(group);
  }

  // ── Image embedding ──
  function attachImageToSelected(file) {
    if (!file) return;
    if (state.selectedNodeIds.length !== 1) {
      window.alert("画像ノードを1つ選択してください。");
      return;
    }
    const node = nodeById(state.selectedNodeIds[0]);
    if (!node || node.type !== "image") {
      window.alert("選択されたノードは画像ノードではありません。");
      return;
    }
    if (file.size > 4 * 1024 * 1024) {
      window.alert("画像サイズは4MB以下にしてください。");
      return;
    }
    const reader = new FileReader();
    reader.onload = () => {
      mutate(() => {
        node.imageData = String(reader.result || "");
      });
    };
    reader.readAsDataURL(file);
  }

  // ── Rotation ──
  function rotateSelected(deg) {
    if (!state.selectedNodeIds.length) return;
    mutate(() => {
      selectedNodes().forEach((n) => {
        n.rotation = ((Number(n.rotation) || 0) + deg) % 360;
        if (n.rotation < 0) n.rotation += 360;
      });
    });
  }

  function setSelectedRotation(deg) {
    if (!state.selectedNodeIds.length) return;
    mutate(() => {
      selectedNodes().forEach((n) => {
        n.rotation = ((Number(deg) || 0) % 360 + 360) % 360;
      });
    });
  }

  // ── Edge presets ──
  function applyEdgePreset(key) {
    if (!state.selectedConnectorId) return;
    const preset = EDGE_PRESETS.find((p) => p.key === key);
    if (!preset) return;
    mutate(() => {
      const edge = edgeById(state.selectedConnectorId);
      if (!edge) return;
      edge.style = edge.style || {};
      if (preset.color !== null) edge.style.color = preset.color;
      else edge.style.color = theme().palette.edge;
      edge.style.width = preset.width;
      edge.style.dashed = preset.dashed;
      edge.style.routing = preset.routing;
    });
  }

  // ── Grid size ──
  function setGridSize(value) {
    const next = Math.max(4, Math.min(96, Number(value) || 24));
    mutate(() => {
      state.doc.canvas.grid = next;
    });
  }

  // ── Lane management ──
  function moveLane(id, direction) {
    const index = state.doc.lanes.findIndex((l) => l.id === id);
    if (index < 0) return;
    const target = direction === "up" ? index - 1 : index + 1;
    if (target < 0 || target >= state.doc.lanes.length) return;
    mutate(() => {
      const arr = state.doc.lanes;
      [arr[index], arr[target]] = [arr[target], arr[index]];
      let cursor = arr[0]?.y || 80;
      arr.forEach((lane) => {
        lane.y = cursor;
        cursor += lane.height;
      });
    });
  }

  function setLaneHeight(id, height) {
    mutate(() => {
      const lane = state.doc.lanes.find((l) => l.id === id);
      if (!lane) return;
      lane.height = Math.max(60, Math.min(800, Number(height) || 170));
      let cursor = state.doc.lanes[0]?.y || 80;
      state.doc.lanes.forEach((l) => {
        l.y = cursor;
        cursor += l.height;
      });
    });
  }

  function deleteLane(id) {
    mutate(() => {
      state.doc.lanes = state.doc.lanes.filter((l) => l.id !== id);
      let cursor = 80;
      state.doc.lanes.forEach((l) => {
        l.y = cursor;
        cursor += l.height;
      });
    });
  }

  // ── Subflow navigation ──
  function populateSubflowOptions(currentRef) {
    const select = $("pf-subflow-select");
    if (!select) return;
    const docs = readLocalDocs().filter((d) => d.id !== state.doc.id);
    select.replaceChildren();
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = "— 未設定 —";
    select.appendChild(empty);
    docs.forEach((doc) => {
      const opt = document.createElement("option");
      opt.value = doc.id;
      opt.textContent = doc.title || "無題";
      if (doc.id === currentRef) opt.selected = true;
      select.appendChild(opt);
    });
  }

  function renderLaneList() {
    const list = $("pf-lane-list");
    if (!list) return;
    list.replaceChildren();
    state.doc.lanes.forEach((lane, index) => {
      const row = document.createElement("div");
      row.className = "pf-lane-row";
      const name = document.createElement("input");
      name.type = "text";
      name.value = lane.name || `レーン ${index + 1}`;
      name.className = "pf-lane-name";
      name.addEventListener("change", () => {
        mutate(() => { lane.name = name.value || lane.name; });
      });
      row.appendChild(name);
      const heightInput = document.createElement("input");
      heightInput.type = "number";
      heightInput.min = "60";
      heightInput.max = "800";
      heightInput.value = Math.round(lane.height);
      heightInput.className = "pf-lane-height";
      heightInput.title = "高さ";
      heightInput.addEventListener("change", () => setLaneHeight(lane.id, heightInput.value));
      row.appendChild(heightInput);
      const upBtn = document.createElement("button");
      upBtn.type = "button";
      upBtn.textContent = "↑";
      upBtn.title = "上へ";
      upBtn.disabled = index === 0;
      upBtn.addEventListener("click", () => moveLane(lane.id, "up"));
      row.appendChild(upBtn);
      const downBtn = document.createElement("button");
      downBtn.type = "button";
      downBtn.textContent = "↓";
      downBtn.title = "下へ";
      downBtn.disabled = index === state.doc.lanes.length - 1;
      downBtn.addEventListener("click", () => moveLane(lane.id, "down"));
      row.appendChild(downBtn);
      const delBtn = document.createElement("button");
      delBtn.type = "button";
      delBtn.textContent = "×";
      delBtn.className = "danger";
      delBtn.title = "削除";
      delBtn.addEventListener("click", () => deleteLane(lane.id));
      row.appendChild(delBtn);
      list.appendChild(row);
    });
  }

  function populateGridSelect() {
    const select = $("pf-grid-size-select");
    if (!select) return;
    select.replaceChildren();
    GRID_SIZES.forEach((size) => {
      const opt = document.createElement("option");
      opt.value = size;
      opt.textContent = `${size}px`;
      if (size === state.doc.canvas.grid) opt.selected = true;
      select.appendChild(opt);
    });
  }

  function navigateToSubflow(node) {
    if (!node || node.type !== "subflow" || !node.subflowRef) {
      window.alert("このサブフローノードにはリンクが設定されていません。インスペクタから保存済みフローを選択してください。");
      return;
    }
    const docs = readLocalDocs();
    const target = docs.find((d) => d.id === node.subflowRef);
    if (!target) {
      window.alert("リンクされたサブフローが見つかりません。");
      return;
    }
    if (state.dirty && !window.confirm("現在の編集内容を破棄してサブフローを開きますか？")) return;
    restoreDoc(target);
    setDirty(false);
  }

  function generateYesNo() {
    const base = state.doc.nodes.find((node) => state.selectedNodeIds.includes(node.id));
    if (!base || base.type !== "decision") {
      window.alert("分岐ノードを1つ選択してください。");
      return;
    }
    const choices = Array.isArray(base.choices) && base.choices.length ? base.choices : ["はい", "いいえ"];
    mutate(() => {
      const newIds = [];
      const total = choices.length;
      const spread = Math.max(220, 200 * (total - 1));
      choices.forEach((label, index) => {
        const ratio = total === 1 ? 0 : index / (total - 1) - 0.5;
        const dx = ratio * spread;
        const child = nodeTemplate("process", base.x + dx, base.y + 200, `${label}の場合`);
        state.doc.nodes.push(child);
        state.doc.connectors.push(connectorTemplate(base.id, child.id, label));
        newIds.push(child.id);
      });
      state.selectedNodeIds = newIds;
    });
  }

  function addLane() {
    mutate(() => {
      state.doc.lanes.push(laneTemplate(state.doc.lanes.length));
    });
  }

  function removeLane() {
    if (!state.doc.lanes.length) return;
    mutate(() => {
      state.doc.lanes.pop();
    });
  }

  function applyTheme(key) {
    mutate(() => {
      state.doc.theme = key;
      const base = THEMES[key].palette;
      state.doc.nodes.forEach((node) => {
        if (!node.style) node.style = defaultStyle(node.type);
        node.style.text = base.text;
      });
      state.doc.connectors.forEach((edge) => {
        edge.style = edge.style || {};
        edge.style.color = base.edge;
      });
    });
    closeThemePopup();
  }

  function toggleThemePopup() {
    const popup = $("pf-theme-popup");
    popup.classList.toggle("open");
  }

  function closeThemePopup() {
    $("pf-theme-popup").classList.remove("open");
  }

  function showContextMenu(clientX, clientY, kind, canvasPt) {
    const menu = $("pf-context-menu");
    menu.replaceChildren();
    let items = [];
    if (kind === "node") {
      const multi = state.selectedNodeIds.length >= 2;
      if (multi) {
        items = [
          { label: `${state.selectedNodeIds.length}個のノードを選択中`, disabled: true },
          { divider: true },
          { label: "ここから線を引く", action: "context-add-connector" },
          { divider: true },
          { label: "左揃え", action: "align-left" },
          { label: "右揃え", action: "align-right" },
          { label: "上揃え", action: "align-top" },
          { label: "下揃え", action: "align-bottom" },
          { label: "水平中央", action: "align-center-h" },
          { label: "垂直中央", action: "align-center-v" },
          { divider: true },
          { label: "横に等間隔配置", action: "distribute-h", disabled: state.selectedNodeIds.length < 3 },
          { label: "縦に等間隔配置", action: "distribute-v", disabled: state.selectedNodeIds.length < 3 },
          { divider: true },
          { label: "幅を揃える", action: "unify-width" },
          { label: "高さを揃える", action: "unify-height" },
          { label: "サイズを揃える", action: "unify-size" },
          { divider: true },
          { label: "複製", action: "duplicate" },
          { label: "スタイル貼り付け", action: "paste-style" },
          { divider: true },
          { label: "ノードを削除", action: "delete", danger: true },
        ];
      } else {
        items = [
          { label: "テキスト編集", action: "context-edit-text" },
          { label: "ここから線を引く", action: "context-add-connector" },
          { label: "複製", action: "duplicate" },
          { label: "スタイルコピー", action: "copy-style" },
          { label: "スタイル貼り付け", action: "paste-style" },
          { divider: true },
          { label: "ノードを削除", action: "delete", danger: true },
        ];
      }
    } else if (kind === "edge") {
      items = [
        { label: "ラベル編集", action: "context-edit-label" },
        { divider: true },
        { label: "線を削除", action: "delete-edge", danger: true },
      ];
    } else {
      items = [
        { label: "処理ノードを追加", action: () => canvasPt && addNodeAt("process", canvasPt) },
        { label: "分岐ノードを追加", action: () => canvasPt && addNodeAt("decision", canvasPt) },
        { label: "開始/終了ノードを追加", action: () => canvasPt && addNodeAt("startEnd", canvasPt) },
        { divider: true },
        { label: "全選択", action: () => {
          state.selectedNodeIds = state.doc.nodes.map((node) => node.id);
          state.selectedConnectorId = null;
          render();
        }},
      ];
    }
    items.forEach((item) => {
      if (item.divider) {
        menu.appendChild(document.createElement("div")).className = "pf-context-divider";
        return;
      }
      if (item.disabled && !item.action) {
        const heading = document.createElement("div");
        heading.className = "pf-context-heading";
        heading.textContent = item.label;
        menu.appendChild(heading);
        return;
      }
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = item.label;
      const classes = [];
      if (item.danger) classes.push("danger");
      if (item.disabled) classes.push("disabled");
      if (classes.length) button.className = classes.join(" ");
      if (item.disabled) button.disabled = true;
      else button.addEventListener("click", () => {
        closeContextMenu();
        if (typeof item.action === "function") item.action();
        else handleAction(item.action);
      });
      menu.appendChild(button);
    });
    menu.classList.remove("hidden");
    const rect = menu.getBoundingClientRect();
    const maxX = window.innerWidth - rect.width - 8;
    const maxY = window.innerHeight - rect.height - 8;
    menu.style.left = `${Math.max(4, Math.min(clientX, maxX))}px`;
    menu.style.top = `${Math.max(4, Math.min(clientY, maxY))}px`;
  }

  function closeContextMenu() {
    $("pf-context-menu").classList.add("hidden");
  }

  function showSizeDialog() {
    const dialog = $("pf-size-dialog");
    $("pf-size-width").value = state.doc.canvas.width;
    $("pf-size-height").value = state.doc.canvas.height;
    dialog.showModal();
  }

  function applySizeFromDialog() {
    const w = Math.max(200, Math.min(6000, parseInt($("pf-size-width").value) || state.doc.canvas.width));
    const h = Math.max(200, Math.min(6000, parseInt($("pf-size-height").value) || state.doc.canvas.height));
    setCanvasSize(w, h);
    $("pf-size-dialog").close();
  }

  function setCanvasSize(w, h) {
    mutate(() => {
      state.doc.canvas.width = w;
      state.doc.canvas.height = h;
      state.doc.lanes.forEach((lane) => {
        lane.width = w;
      });
    });
    fitZoom();
  }

  function showNewDialog() {
    const dialog = $("pf-new-dialog");
    $("pf-new-width").value = 1123;
    $("pf-new-height").value = 794;
    dialog.showModal();
  }

  function createNewFromDialog() {
    const w = Math.max(200, Math.min(6000, parseInt($("pf-new-width").value) || 1123));
    const h = Math.max(200, Math.min(6000, parseInt($("pf-new-height").value) || 794));
    const doc = createBlankDoc();
    doc.canvas.width = w;
    doc.canvas.height = h;
    restoreDoc(doc);
    setDirty(false);
    $("pf-new-dialog").close();
    fitZoom();
  }

  function copyStyle() {
    const node = state.doc.nodes.find((item) => state.selectedNodeIds.includes(item.id));
    if (!node) return;
    state.styleClipboard = clone(node.style || defaultStyle(node.type));
  }

  function pasteStyle() {
    if (!state.styleClipboard || !state.selectedNodeIds.length) return;
    mutate(() => {
      state.doc.nodes.forEach((node) => {
        if (state.selectedNodeIds.includes(node.id)) node.style = clone(state.styleClipboard);
      });
    });
  }

  function renderInspector() {
    const node = state.selectedNodeIds.length === 1 ? nodeById(state.selectedNodeIds[0]) : null;
    const edge = state.selectedConnectorId ? edgeById(state.selectedConnectorId) : null;
    const multi = state.selectedNodeIds.length >= 2;
    $("pf-empty-inspector").classList.toggle("hidden", Boolean(node || edge || multi));
    $("pf-node-inspector").classList.toggle("hidden", !node);
    $("pf-edge-inspector").classList.toggle("hidden", !edge);
    const multiPanel = $("pf-multi-inspector");
    if (multiPanel) multiPanel.classList.toggle("hidden", !multi);
    if (multi) {
      $("pf-multi-count").textContent = `${state.selectedNodeIds.length}個のノードを選択中`;
    }
    if (node) {
      $("pf-node-text").value = node.text || "";
      $("pf-node-width").value = Math.round(node.width);
      $("pf-node-height").value = Math.round(node.height);
      $("pf-fill-color").value = normalizeColor(node.style?.fill || "#ffffff");
      $("pf-stroke-color").value = normalizeColor(node.style?.stroke || "#53657e");
      $("pf-text-color").value = normalizeColor(node.style?.text || "#172033");
      $("pf-stroke-width").value = node.style?.strokeWidth || 2;
      const rotInput = $("pf-node-rotation");
      if (rotInput) rotInput.value = Math.round(Number(node.rotation) || 0);
      const choicesSection = $("pf-choices-section");
      if (node.type === "decision") {
        choicesSection.classList.remove("hidden");
        const choices = Array.isArray(node.choices) && node.choices.length ? node.choices : ["はい", "いいえ"];
        $("pf-node-choices").value = choices.join("\n");
      } else {
        choicesSection.classList.add("hidden");
      }
      const imageSection = $("pf-image-section");
      if (imageSection) imageSection.classList.toggle("hidden", node.type !== "image");
      const subflowSection = $("pf-subflow-section");
      if (subflowSection) {
        subflowSection.classList.toggle("hidden", node.type !== "subflow");
        if (node.type === "subflow") populateSubflowOptions(node.subflowRef || "");
      }
    }
    if (edge) {
      $("pf-edge-label").value = edge.label || "";
      $("pf-edge-color").value = normalizeColor(edge.style?.color || theme().palette.edge);
      $("pf-edge-width").value = edge.style?.width || 2.5;
      $("pf-edge-style").value = edge.style?.dashed ? "dashed" : "solid";
      const routingSelect = $("pf-edge-routing");
      if (routingSelect) routingSelect.value = edge.style?.routing || "orthogonal";
    }
  }

  function normalizeColor(value) {
    const color = String(value || "").trim();
    return /^#[0-9a-fA-F]{6}$/.test(color) ? color : "#000000";
  }

  function updateSelectedNode(field, value) {
    if (state.selectedNodeIds.length !== 1) return;
    mutate(() => {
      const node = nodeById(state.selectedNodeIds[0]);
      if (!node) return;
      if (field === "text") node.text = value;
      if (field === "width") node.width = Math.max(48, Number(value) || node.width);
      if (field === "height") node.height = Math.max(32, Number(value) || node.height);
      if (field === "fill") node.style.fill = value;
      if (field === "stroke") node.style.stroke = value;
      if (field === "textColor") node.style.text = value;
      if (field === "strokeWidth") node.style.strokeWidth = Math.max(1, Number(value) || 2);
      if (field === "choices") {
        const items = String(value || "").split(/\n/).map((line) => line.trim()).filter(Boolean);
        node.choices = items.length ? items : ["はい", "いいえ"];
      }
      if (field === "rotation") node.rotation = ((Number(value) || 0) % 360 + 360) % 360;
      if (field === "subflowRef") node.subflowRef = String(value || "");
    });
  }

  function addChoiceToSelected() {
    if (state.selectedNodeIds.length !== 1) return;
    const node = nodeById(state.selectedNodeIds[0]);
    if (!node || node.type !== "decision") return;
    mutate(() => {
      node.choices = Array.isArray(node.choices) ? [...node.choices] : ["はい", "いいえ"];
      node.choices.push(`選択肢${node.choices.length + 1}`);
    });
  }

  function updateSelectedEdge(field, value) {
    const edge = edgeById(state.selectedConnectorId);
    if (!edge) return;
    mutate(() => {
      if (field === "label") edge.label = value;
      edge.style = edge.style || {};
      if (field === "color") edge.style.color = value;
      if (field === "width") edge.style.width = Math.max(1, Number(value) || 2.5);
      if (field === "style") edge.style.dashed = value === "dashed";
      if (field === "routing") edge.style.routing = value;
    });
  }

  function renderWarnings() {
    const list = $("pf-warning-list");
    list.replaceChildren();
    const warnings = runValidation();
    state.doc.nodes.filter((node) => node.type === "decision").forEach((node) => {
      const outgoing = state.doc.connectors.filter((edge) => edge.from === node.id);
      outgoing.forEach((edge) => {
        if (!String(edge.label || "").trim()) warnings.push(`ラベル未入力: ${truncateLabel(node.text || "分岐")}`);
      });
    });
    const seen = new Set();
    const unique = warnings.filter((w) => seen.has(w) ? false : (seen.add(w), true));
    if (!unique.length) {
      list.textContent = "警告はありません。";
      return;
    }
    unique.slice(0, 12).forEach((message) => {
      const item = document.createElement("div");
      item.className = "pf-warning";
      item.textContent = message;
      list.appendChild(item);
    });
  }

  function renderMinimap() {
    const mini = $("pf-minimap");
    mini.replaceChildren();
    const width = 260;
    const height = 150;
    mini.setAttribute("viewBox", `0 0 ${width} ${height}`);
    const sx = width / state.doc.canvas.width;
    const sy = height / state.doc.canvas.height;
    mini.appendChild(el("rect", { x: 0, y: 0, width, height, fill: theme().canvas }));
    state.doc.lanes.forEach((lane) => {
      mini.appendChild(el("rect", { x: lane.x * sx, y: lane.y * sy, width: lane.width * sx, height: lane.height * sy, fill: lane.color || theme().lane, opacity: 0.75 }));
    });
    state.doc.connectors.forEach((edge) => {
      const from = nodeById(edge.from);
      const to = nodeById(edge.to);
      if (!from || !to) return;
      const a = center(from);
      const b = center(to);
      mini.appendChild(el("line", { x1: a.x * sx, y1: a.y * sy, x2: b.x * sx, y2: b.y * sy, stroke: edge.style?.color || theme().palette.edge, "stroke-width": 1 }));
    });
    state.doc.nodes.forEach((node) => {
      mini.appendChild(el("rect", { x: node.x * sx, y: node.y * sy, width: Math.max(2, node.width * sx), height: Math.max(2, node.height * sy), fill: node.style?.fill || "#fff", stroke: node.style?.stroke || "#444", "stroke-width": 0.7 }));
    });
  }

  function updateStatus() {
    const selected = state.selectedNodeIds.length;
    $("pf-selection-status").textContent = state.selectedConnectorId ? "線を選択中" : selected ? `${selected}個選択中` : "選択なし";
    $("pf-canvas-status").textContent = `${state.doc.canvas.width} × ${state.doc.canvas.height}`;
    if (state.pendingConnectorNodeId) $("pf-hint").textContent = "接続先のノードをクリックしてください。Escで解除。";
    else if (state.tool === "connector") $("pf-hint").textContent = "開始ノードをクリックして線を引きます。";
    else if (state.marquee) $("pf-hint").textContent = "ドラッグして範囲選択中…";
    else $("pf-hint").textContent = "ドラッグで範囲選択。Alt/中ボタンでパン。右クリックでメニュー。";
  }

  function setTool(tool) {
    state.tool = tool;
    state.pendingConnectorNodeId = null;
    state.connectorPreview = null;
    document.querySelectorAll("[data-action='select-tool'], [data-action='connector-tool']").forEach((button) => button.classList.remove("active"));
    const active = tool === "connector" ? "[data-action='connector-tool']" : "[data-action='select-tool']";
    document.querySelector(active)?.classList.add("active");
    render();
  }

  function applyZoom() {
    scaleLayer.style.width = `${state.doc.canvas.width * state.zoom}px`;
    scaleLayer.style.height = `${state.doc.canvas.height * state.zoom}px`;
    scaleLayer.style.transform = `scale(${state.zoom})`;
    $("pf-zoom-label").textContent = `${Math.round(state.zoom * 100)}%`;
  }

  function setZoom(value) {
    state.zoom = Math.max(0.2, Math.min(3, Math.round(value * 20) / 20));
    applyZoom();
  }

  function fitZoom() {
    const availableW = wrap.clientWidth - 36;
    const availableH = wrap.clientHeight - 36;
    setZoom(Math.min(1, availableW / state.doc.canvas.width, availableH / state.doc.canvas.height));
    wrap.scrollLeft = 0;
    wrap.scrollTop = 0;
  }

  function saveLocal() {
    const all = readLocalDocs();
    state.doc.title = $("pf-title").value || "新しいフロー";
    state.doc.updatedAt = new Date().toISOString();
    const index = all.findIndex((doc) => doc.id === state.doc.id);
    if (index >= 0) all[index] = clone(state.doc);
    else all.unshift(clone(state.doc));
    localStorage.setItem(STORAGE_KEY, JSON.stringify(all.slice(0, 40)));
    setDirty(false);
  }

  function readLocalDocs() {
    try {
      const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
      return Array.isArray(value) ? value : [];
    } catch (error) {
      return [];
    }
  }

  function showOpenDialog() {
    const dialog = $("pf-open-dialog");
    const list = $("pf-saved-list");
    list.replaceChildren();
    const docs = readLocalDocs();
    if (!docs.length) {
      list.textContent = "保存済みフローはありません。";
    }
    docs.forEach((doc) => {
      const row = document.createElement("div");
      row.className = "pf-saved-item";
      row.innerHTML = `<div><strong></strong><br><span></span></div>`;
      row.querySelector("strong").textContent = doc.title || "無題";
      row.querySelector("span").textContent = doc.updatedAt ? new Date(doc.updatedAt).toLocaleString() : "";
      const open = document.createElement("button");
      open.type = "button";
      open.textContent = "開く";
      open.addEventListener("click", () => {
        restoreDoc(doc);
        setDirty(false);
        dialog.close();
      });
      const del = document.createElement("button");
      del.type = "button";
      del.textContent = "削除";
      del.className = "danger";
      del.addEventListener("click", () => {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(readLocalDocs().filter((item) => item.id !== doc.id)));
        showOpenDialog();
      });
      row.append(open, del);
      list.appendChild(row);
    });
    dialog.showModal();
  }

  function exportJson() {
    downloadBlob(JSON.stringify(state.doc, null, 2), "application/json", `${safeFilename(state.doc.title)}.powerflow.json`);
  }

  function importJson(file) {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const doc = JSON.parse(String(reader.result || ""));
        if (!doc || !Array.isArray(doc.nodes) || !Array.isArray(doc.connectors)) throw new Error();
        restoreDoc({ ...createBlankDoc(), ...doc, schemaVersion: SCHEMA_VERSION });
        setDirty(true);
      } catch (error) {
        window.alert("Power Flow JSONとして読み込めませんでした。");
      }
    };
    reader.readAsText(file);
  }

  function exportSvg() {
    const markup = exportSvgMarkup();
    downloadBlob(markup, "image/svg+xml;charset=utf-8", `${safeFilename(state.doc.title)}.svg`);
  }

  function exportSvgMarkup() {
    const bounds = exportBounds();
    const cloneSvg = svg.cloneNode(true);
    cloneSvg.setAttribute("xmlns", SVG_NS);
    cloneSvg.setAttribute("viewBox", `${bounds.x} ${bounds.y} ${bounds.width} ${bounds.height}`);
    cloneSvg.setAttribute("width", String(bounds.width));
    cloneSvg.setAttribute("height", String(bounds.height));

    cloneSvg.querySelectorAll(".pf-connector-preview").forEach((e) => e.remove());
    cloneSvg.querySelectorAll(".pf-port").forEach((e) => e.remove());
    cloneSvg.querySelectorAll(".pf-marquee").forEach((e) => e.remove());
    cloneSvg.querySelectorAll(".pf-rotate-handle").forEach((e) => e.remove());
    cloneSvg.querySelectorAll(".pf-rotate-line").forEach((e) => e.remove());
    cloneSvg.querySelectorAll(".pf-page-breaks").forEach((e) => e.remove());

    const style = document.createElementNS(SVG_NS, "style");
    style.textContent = [
      ".pf-node-text{font-family:Arial,'Yu Gothic','Hiragino Sans',sans-serif;font-size:16px;font-weight:700;text-anchor:middle;dominant-baseline:middle}",
      ".pf-connector-label{font-family:Arial,'Yu Gothic','Hiragino Sans',sans-serif;font-size:14px;font-weight:800;text-anchor:middle;paint-order:stroke;stroke:#fff;stroke-width:5px}",
      ".pf-grid-line{stroke:#e2e8f0}.pf-resize{display:none}.pf-port{display:none}",
      ".pf-node-badge{font-family:Arial,sans-serif;font-size:18px;font-weight:900}",
    ].join("\n");
    cloneSvg.insertBefore(style, cloneSvg.firstChild);
    if ($("pf-selection-export-toggle").checked && state.selectedNodeIds.length) {
      const keep = new Set(state.selectedNodeIds);
      cloneSvg.querySelectorAll(".pf-node").forEach((node) => {
        if (!keep.has(node.getAttribute("data-id"))) node.remove();
      });
      cloneSvg.querySelectorAll(".pf-connector").forEach((edgePath) => edgePath.remove());
      cloneSvg.querySelectorAll(".pf-connector-label").forEach((label) => label.remove());
    }
    return new XMLSerializer().serializeToString(cloneSvg);
  }

  async function exportPng() {
    const blob = await renderExportCanvas("image/png");
    downloadBlob(blob, "image/png", `${safeFilename(state.doc.title)}.png`);
  }

  async function exportPdf() {
    const jpegBlob = await renderExportCanvas("image/jpeg", 0.92);
    const dataUrl = await blobToDataUrl(jpegBlob);
    const pdfBlob = jpegDataUrlToPdf(dataUrl);
    downloadBlob(pdfBlob, "application/pdf", `${safeFilename(state.doc.title)}.pdf`);
  }

  function exportBounds() {
    if (!$("pf-selection-export-toggle").checked || !state.selectedNodeIds.length) {
      return { x: 0, y: 0, width: state.doc.canvas.width, height: state.doc.canvas.height };
    }
    const nodes = state.doc.nodes.filter((node) => state.selectedNodeIds.includes(node.id));
    const minX = Math.max(0, Math.min(...nodes.map((node) => node.x)) - 36);
    const minY = Math.max(0, Math.min(...nodes.map((node) => node.y)) - 36);
    const maxX = Math.min(state.doc.canvas.width, Math.max(...nodes.map((node) => node.x + node.width)) + 36);
    const maxY = Math.min(state.doc.canvas.height, Math.max(...nodes.map((node) => node.y + node.height)) + 36);
    return { x: minX, y: minY, width: Math.max(160, maxX - minX), height: Math.max(120, maxY - minY) };
  }

  function renderExportCanvas(type, quality) {
    const markup = exportSvgMarkup();
    const bounds = exportBounds();
    const blob = new Blob([markup], { type: "image/svg+xml;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        const scale = 2;
        const canvas = document.createElement("canvas");
        canvas.width = Math.ceil(bounds.width * scale);
        canvas.height = Math.ceil(bounds.height * scale);
        const ctx = canvas.getContext("2d");
        ctx.fillStyle = theme().canvas;
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        URL.revokeObjectURL(url);
        canvas.toBlob((result) => result ? resolve(result) : reject(new Error("出力に失敗しました。")), type, quality);
      };
      img.onerror = () => {
        URL.revokeObjectURL(url);
        reject(new Error("出力に失敗しました。"));
      };
      img.src = url;
    });
  }

  function jpegDataUrlToPdf(dataUrl) {
    const binary = atob(dataUrl.split(",")[1] || "");
    const bounds = exportBounds();
    const maxW = 841.89;
    const maxH = 595.28;
    const scale = Math.min(maxW / bounds.width, maxH / bounds.height, 1);
    const pageW = Math.max(240, bounds.width * scale);
    const pageH = Math.max(180, bounds.height * scale);
    const imgW = Math.ceil(bounds.width * 2);
    const imgH = Math.ceil(bounds.height * 2);
    const imageObj = `4 0 obj\n<< /Type /XObject /Subtype /Image /Width ${imgW} /Height ${imgH} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${binary.length} >>\nstream\n${binary}\nendstream\nendobj\n`;
    const content = `q\n${pageW} 0 0 ${pageH} 0 0 cm\n/Im0 Do\nQ\n`;
    const objects = [
      "1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
      "2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
      `3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${pageW.toFixed(2)} ${pageH.toFixed(2)}] /Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>\nendobj\n`,
      imageObj,
      `5 0 obj\n<< /Length ${content.length} >>\nstream\n${content}endstream\nendobj\n`,
    ];
    let pdf = "%PDF-1.4\n";
    const offsets = [0];
    objects.forEach((object) => {
      offsets.push(pdf.length);
      pdf += object;
    });
    const xref = pdf.length;
    pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
    offsets.slice(1).forEach((offset) => {
      pdf += `${String(offset).padStart(10, "0")} 00000 n \n`;
    });
    pdf += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF`;
    const bytes = new Uint8Array(pdf.length);
    for (let i = 0; i < pdf.length; i += 1) bytes[i] = pdf.charCodeAt(i) & 0xff;
    return new Blob([bytes], { type: "application/pdf" });
  }

  function blobToDataUrl(blob) {
    return new Promise((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ""));
      reader.readAsDataURL(blob);
    });
  }

  function downloadBlob(content, type, filename) {
    const blob = content instanceof Blob ? content : new Blob([content], { type });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function safeFilename(value) {
    return String(value || "power-flow").replace(/[\\/:*?"<>|]+/g, "_").slice(0, 80) || "power-flow";
  }

  function bindUi() {
    SHAPE_CATEGORIES.forEach((cat) => {
      const section = document.createElement("div");
      section.className = "pf-palette-group";
      const heading = document.createElement("h3");
      heading.textContent = cat.label;
      section.appendChild(heading);
      cat.shapes.forEach(([type, label]) => {
        const button = document.createElement("button");
        button.type = "button";
        button.innerHTML = `<span>${label}</span><span class="pf-shape-icon">${shapeIconSvg(type)}</span>`;
        button.addEventListener("click", () => setTool(type));
        section.appendChild(button);
      });
      $("pf-shape-palette").appendChild(section);
    });

    Object.entries(TEMPLATES).forEach(([key, template]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = template.label;
      button.addEventListener("click", () => {
        if (state.dirty && !window.confirm("現在の編集内容を破棄してテンプレートを開きますか？")) return;
        restoreDoc(template.build());
        setDirty(true);
      });
      $("pf-template-list").appendChild(button);
    });

    Object.entries(THEMES).forEach(([key, item]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "pf-theme-btn";
      button.innerHTML = `<span class="pf-theme-swatch" style="background:${item.canvas};border-color:${item.palette.stroke}"><span style="background:${item.palette.stroke}"></span><span style="background:${item.palette.fill}"></span></span><span>${item.label}</span>`;
      button.addEventListener("click", () => applyTheme(key));
      $("pf-theme-popup-list").appendChild(button);
    });

    CANVAS_PRESETS.forEach((preset) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = preset.label;
      button.addEventListener("click", () => {
        $("pf-size-width").value = preset.width;
        $("pf-size-height").value = preset.height;
      });
      $("pf-size-presets").appendChild(button);
    });

    CANVAS_PRESETS.forEach((preset) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = preset.label;
      button.addEventListener("click", () => {
        $("pf-new-width").value = preset.width;
        $("pf-new-height").value = preset.height;
      });
      $("pf-new-presets").appendChild(button);
    });

    const edgePresetsHost = $("pf-edge-presets");
    if (edgePresetsHost) {
      EDGE_PRESETS.forEach((preset) => {
        const button = document.createElement("button");
        button.type = "button";
        button.textContent = preset.label;
        button.className = "pf-edge-preset-btn";
        button.addEventListener("click", () => applyEdgePreset(preset.key));
        edgePresetsHost.appendChild(button);
      });
    }

    document.querySelectorAll("[data-action]").forEach((button) => {
      button.addEventListener("click", () => handleAction(button.dataset.action));
    });
    document.querySelectorAll("[data-menu]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        const target = document.querySelector(`[data-menu-panel='${button.dataset.menu}']`);
        const wasOpen = target?.classList.contains("open");
        document.querySelectorAll(".pf-menu.open").forEach((menu) => menu.classList.remove("open"));
        closeThemePopup();
        if (target && !wasOpen) target.classList.add("open");
      });
    });
    document.addEventListener("click", (event) => {
      document.querySelectorAll(".pf-menu.open").forEach((menu) => menu.classList.remove("open"));
      if (!event.target.closest(".pf-theme-wrap")) closeThemePopup();
      if (!event.target.closest("#pf-context-menu")) closeContextMenu();
    });
    document.addEventListener("pointerdown", (event) => {
      if (event.button === 0 && !event.target.closest("#pf-context-menu")) closeContextMenu();
    });
    document.addEventListener("scroll", closeContextMenu, true);
    window.addEventListener("blur", closeContextMenu);
    svg.addEventListener("contextmenu", (event) => {
      if (event.target !== svg
        && !event.target.classList.contains("pf-grid-line")
        && !event.target.classList.contains("pf-canvas-bg")) return;
      event.preventDefault();
      const point = canvasPoint(event);
      showContextMenu(event.clientX, event.clientY, "canvas", point);
    });
    $("pf-title").addEventListener("input", () => {
      state.doc.title = $("pf-title").value || "新しいフロー";
      setDirty(true);
    });
    $("pf-grid-toggle").addEventListener("change", (event) => mutate(() => {
      state.doc.canvas.showGrid = event.target.checked;
    }));
    $("pf-snap-toggle").addEventListener("change", (event) => mutate(() => {
      state.doc.canvas.snap = event.target.checked;
    }));
    $("pf-import-file").addEventListener("change", (event) => importJson(event.target.files[0]));

    svg.addEventListener("pointerdown", startCanvasPointer);
    svg.addEventListener("pointermove", (event) => {
      moveDrag(event);
      moveEdgeDrag(event);
      if (state.pendingConnectorNodeId) {
        state.connectorPreview = canvasPoint(event);
        render();
      }
      if (state.marquee) {
        const point = canvasPoint(event);
        state.marquee.x = point.x;
        state.marquee.y = point.y;
        const rect = {
          x: Math.min(state.marquee.startX, state.marquee.x),
          y: Math.min(state.marquee.startY, state.marquee.y),
          width: Math.abs(state.marquee.x - state.marquee.startX),
          height: Math.abs(state.marquee.y - state.marquee.startY),
        };
        const hits = nodesIntersectingRect(rect).map((node) => node.id);
        state.selectedNodeIds = state.marquee.additive
          ? Array.from(new Set([...state.marquee.basis, ...hits]))
          : hits;
        state.selectedConnectorId = null;
        render();
      }
    });
    svg.addEventListener("pointerup", (event) => {
      endDrag(event);
      if (state.marquee) {
        state.marquee = null;
        render();
      }
    });
    svg.addEventListener("pointercancel", (event) => {
      endDrag(event);
      if (state.marquee) {
        state.marquee = null;
        render();
      }
    });
    wrap.addEventListener("wheel", handleWheel, { passive: false });

    svg.addEventListener("pointermove", (event) => {
      if (!state.pan) return;
      wrap.scrollLeft = state.pan.scrollLeft - (event.clientX - state.pan.x);
      wrap.scrollTop = state.pan.scrollTop - (event.clientY - state.pan.y);
    });
    svg.addEventListener("pointerup", () => {
      state.pan = null;
      wrap.classList.remove("panning");
    });
    svg.addEventListener("pointercancel", () => {
      state.pan = null;
      wrap.classList.remove("panning");
    });

    bindInspector();
    bindKeyboard();
  }

  function shapeIconSvg(type) {
    const w = 22;
    const h = 16;
    if (type === "startEnd") return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><rect x="1" y="1" width="20" height="14" rx="7" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>`;
    if (type === "decision") return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><polygon points="11,1 21,8 11,15 1,8" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>`;
    if (type === "io") return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><polygon points="5,1 21,1 17,15 1,15" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>`;
    if (type === "document") return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><path d="M1,1 H21 V12 Q16,17 11,11 Q6,6 1,11 Z" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>`;
    if (type === "database") return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><path d="M2,4 C2,1 20,1 20,4 V12 C20,15 2,15 2,12 Z M2,4 C2,7 20,7 20,4" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>`;
    if (type === "manual") return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><polygon points="1,4 18,1 21,12 4,15" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>`;
    if (type === "note") return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><path d="M1,1 H16 L21,6 V15 H1 Z M16,1 V6 H21" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>`;
    if (type === "image") return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><rect x="1" y="1" width="20" height="14" rx="2" fill="none" stroke="currentColor" stroke-width="1.5"/><circle cx="6" cy="6" r="1.5" fill="currentColor"/><polyline points="3,12 8,7 13,12 18,7 21,11" fill="none" stroke="currentColor" stroke-width="1.2"/></svg>`;
    if (type === "subflow") return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><rect x="2" y="2" width="18" height="12" rx="2" fill="none" stroke="currentColor" stroke-width="1.5"/><rect x="0" y="0" width="18" height="12" rx="2" fill="none" stroke="currentColor" stroke-width="1.5" opacity="0.5"/></svg>`;
    return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><rect x="1" y="1" width="20" height="14" rx="3" fill="none" stroke="currentColor" stroke-width="1.5"/></svg>`;
  }

  function bindInspector() {
    $("pf-node-text").addEventListener("change", (event) => updateSelectedNode("text", event.target.value));
    $("pf-node-width").addEventListener("change", (event) => updateSelectedNode("width", event.target.value));
    $("pf-node-height").addEventListener("change", (event) => updateSelectedNode("height", event.target.value));
    $("pf-fill-color").addEventListener("change", (event) => updateSelectedNode("fill", event.target.value));
    $("pf-stroke-color").addEventListener("change", (event) => updateSelectedNode("stroke", event.target.value));
    $("pf-text-color").addEventListener("change", (event) => updateSelectedNode("textColor", event.target.value));
    $("pf-stroke-width").addEventListener("change", (event) => updateSelectedNode("strokeWidth", event.target.value));
    $("pf-node-choices").addEventListener("change", (event) => updateSelectedNode("choices", event.target.value));
    $("pf-multi-fill")?.addEventListener("change", (event) => bulkUpdateStyle("fill", event.target.value));
    $("pf-multi-stroke")?.addEventListener("change", (event) => bulkUpdateStyle("stroke", event.target.value));
    $("pf-multi-text-color")?.addEventListener("change", (event) => bulkUpdateStyle("text", event.target.value));
    $("pf-multi-stroke-width")?.addEventListener("change", (event) => bulkUpdateStyle("strokeWidth", event.target.value));
    $("pf-edge-label").addEventListener("change", (event) => updateSelectedEdge("label", event.target.value));
    $("pf-edge-color").addEventListener("change", (event) => updateSelectedEdge("color", event.target.value));
    $("pf-edge-width").addEventListener("change", (event) => updateSelectedEdge("width", event.target.value));
    $("pf-edge-style").addEventListener("change", (event) => updateSelectedEdge("style", event.target.value));
    $("pf-edge-routing")?.addEventListener("change", (event) => updateSelectedEdge("routing", event.target.value));
    $("pf-node-rotation")?.addEventListener("change", (event) => updateSelectedNode("rotation", event.target.value));
    $("pf-image-file")?.addEventListener("change", (event) => attachImageToSelected(event.target.files?.[0]));
    $("pf-subflow-select")?.addEventListener("change", (event) => updateSelectedNode("subflowRef", event.target.value));
    $("pf-search-input")?.addEventListener("input", (event) => searchNodes(event.target.value));
    $("pf-search-input")?.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        jumpToSearchHit(state.searchIndex + (event.shiftKey ? -1 : 1));
      } else if (event.key === "Escape") {
        $("pf-search-bar").classList.add("hidden");
        clearSearch();
      }
    });
    $("pf-grid-size-select")?.addEventListener("change", (event) => setGridSize(event.target.value));
  }

  function showSearchDialog() {
    const bar = $("pf-search-bar");
    if (!bar) return;
    bar.classList.remove("hidden");
    const input = $("pf-search-input");
    input.focus();
    input.select();
  }

  function updateSearchUI() {
    const count = $("pf-search-count");
    if (!count) return;
    if (!state.searchHits.length) {
      count.textContent = $("pf-search-input")?.value ? "0 件" : "";
    } else {
      count.textContent = `${state.searchIndex + 1}/${state.searchHits.length}件`;
    }
  }

  function showShortcutsDialog() {
    $("pf-shortcuts-dialog")?.showModal();
  }

  function handleAction(action) {
    const actions = {
      new: () => {
        if (state.dirty && !window.confirm("現在の編集内容を破棄して新規作成しますか？")) return;
        showNewDialog();
      },
      save: saveLocal,
      open: showOpenDialog,
      undo,
      redo,
      "align-vertical": () => autoAlign("vertical"),
      "align-horizontal": () => autoAlign("horizontal"),
      "yes-no": generateYesNo,
      print: () => window.print(),
      "export-png": () => exportPng().catch((error) => window.alert(error.message)),
      "export-svg": exportSvg,
      "export-pdf": () => exportPdf().catch((error) => window.alert(error.message)),
      "export-json": exportJson,
      "add-lane": addLane,
      "remove-lane": removeLane,
      "select-tool": () => setTool("select"),
      "connector-tool": () => setTool("connector"),
      "zoom-in": () => setZoom(state.zoom + 0.1),
      "zoom-out": () => setZoom(state.zoom - 0.1),
      "zoom-fit": fitZoom,
      "copy-style": copyStyle,
      "paste-style": pasteStyle,
      duplicate: duplicateSelected,
      delete: deleteSelection,
      "delete-edge": deleteSelection,
      "toggle-theme": toggleThemePopup,
      "canvas-size": showSizeDialog,
      "apply-size": applySizeFromDialog,
      "create-new": createNewFromDialog,
      "add-choice": addChoiceToSelected,
      "context-edit-text": () => state.selectedNodeIds[0] && editNodeText(state.selectedNodeIds[0]),
      "context-edit-label": () => state.selectedConnectorId && editEdgeLabel(state.selectedConnectorId),
      "context-add-connector": () => {
        const sourceId = state.contextNodeId || state.selectedNodeIds[0];
        if (sourceId) {
          setTool("connector");
          state.pendingConnectorNodeId = sourceId;
          state.selectedNodeIds = [sourceId];
          state.selectedConnectorId = null;
          render();
        }
      },
      "align-left": () => alignSelected("left"),
      "align-right": () => alignSelected("right"),
      "align-top": () => alignSelected("top"),
      "align-bottom": () => alignSelected("bottom"),
      "align-center-h": () => alignSelected("centerH"),
      "align-center-v": () => alignSelected("centerV"),
      "distribute-h": () => distributeSelected("horizontal"),
      "distribute-v": () => distributeSelected("vertical"),
      "unify-width": () => unifySize("width"),
      "unify-height": () => unifySize("height"),
      "unify-size": () => unifySize("both"),
      copy: copySelectedToClipboard,
      paste: pasteClipboard,
      "auto-layout": autoLayout,
      "search-open": showSearchDialog,
      "search-next": () => jumpToSearchHit(state.searchIndex + 1),
      "search-prev": () => jumpToSearchHit(state.searchIndex - 1),
      "search-close": clearSearch,
      "z-front": () => bringSelectionTo("front"),
      "z-back": () => bringSelectionTo("back"),
      "z-forward": () => bringSelectionTo("forward"),
      "z-backward": () => bringSelectionTo("backward"),
      "toggle-lock": toggleLockSelected,
      "rotate-left": () => rotateSelected(-15),
      "rotate-right": () => rotateSelected(15),
      "rotate-90": () => rotateSelected(90),
      "rotate-reset": () => setSelectedRotation(0),
      "export-mermaid": exportMermaid,
      "page-break": togglePageBreakPreview,
      "open-subflow": () => state.selectedNodeIds[0] && navigateToSubflow(nodeById(state.selectedNodeIds[0])),
      "show-shortcuts": showShortcutsDialog,
    };
    actions[action]?.();
  }

  function startCanvasPointer(event) {
    if (event.button === 1 || (event.button === 0 && event.altKey)) {
      event.preventDefault();
      state.pan = { x: event.clientX, y: event.clientY, scrollLeft: wrap.scrollLeft, scrollTop: wrap.scrollTop };
      wrap.classList.add("panning");
      svg.setPointerCapture(event.pointerId);
      return;
    }
    if (event.button !== 0) return;
    if (
      event.target !== svg
      && !event.target.classList.contains("pf-grid-line")
      && !event.target.classList.contains("pf-canvas-bg")
    ) return;
    const point = canvasPoint(event);
    state.lastPointer = point;
    if (SHAPES.some(([type]) => type === state.tool)) {
      addNodeAt(state.tool, point);
      return;
    }
    if (state.tool === "connector" && state.pendingConnectorNodeId) {
      state.pendingConnectorNodeId = null;
      state.connectorPreview = null;
      render();
      return;
    }
    if (!event.shiftKey && !event.ctrlKey && !event.metaKey) {
      clearSelection();
    }
    state.marquee = {
      startX: point.x,
      startY: point.y,
      x: point.x,
      y: point.y,
      additive: event.shiftKey || event.ctrlKey || event.metaKey,
      basis: event.shiftKey || event.ctrlKey || event.metaKey ? [...state.selectedNodeIds] : [],
    };
    svg.setPointerCapture(event.pointerId);
    render();
  }

  function handleWheel(event) {
    if (!event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    setZoom(state.zoom + (event.deltaY < 0 ? 0.1 : -0.1));
  }

  function undo() {
    if (!state.history.length) return;
    state.future.push(clone(state.doc));
    const previous = state.history.pop();
    restoreDoc(previous);
    setDirty(true);
  }

  function redo() {
    if (!state.future.length) return;
    state.history.push(clone(state.doc));
    const next = state.future.pop();
    restoreDoc(next);
    setDirty(true);
  }

  function bindKeyboard() {
    document.addEventListener("keydown", (event) => {
      const tag = (event.target.tagName || "").toLowerCase();
      if (["input", "textarea", "select"].includes(tag)) return;
      const key = event.key.toLowerCase();
      if (event.key === "Escape") {
        closeContextMenu();
        if (!$("pf-search-bar").classList.contains("hidden")) {
          clearSearch();
          return;
        }
        state.pendingConnectorNodeId = null;
        state.connectorPreview = null;
        setTool("select");
        clearSelection();
      }
      if (event.key === "Delete") deleteSelection();
      if ((event.ctrlKey || event.metaKey) && key === "z") {
        event.preventDefault();
        undo();
      }
      if ((event.ctrlKey || event.metaKey) && key === "y") {
        event.preventDefault();
        redo();
      }
      if ((event.ctrlKey || event.metaKey) && key === "s") {
        event.preventDefault();
        saveLocal();
      }
      if ((event.ctrlKey || event.metaKey) && key === "p") {
        event.preventDefault();
        window.print();
      }
      if ((event.ctrlKey || event.metaKey) && key === "d") {
        event.preventDefault();
        duplicateSelected();
      }
      if ((event.ctrlKey || event.metaKey) && key === "a") {
        event.preventDefault();
        state.selectedNodeIds = state.doc.nodes.map((node) => node.id);
        state.selectedConnectorId = null;
        render();
      }
      if ((event.ctrlKey || event.metaKey) && key === "c" && !event.shiftKey) {
        event.preventDefault();
        copySelectedToClipboard();
      }
      if ((event.ctrlKey || event.metaKey) && key === "v" && !event.shiftKey) {
        event.preventDefault();
        pasteClipboard();
      }
      if ((event.ctrlKey || event.metaKey) && key === "f") {
        event.preventDefault();
        showSearchDialog();
      }
      if ((event.ctrlKey || event.metaKey) && key === "l") {
        event.preventDefault();
        toggleLockSelected();
      }
      if (key === "?" || (event.shiftKey && event.key === "?")) {
        event.preventDefault();
        showShortcutsDialog();
      }
      if ((event.ctrlKey || event.metaKey) && (event.key === "+" || event.key === "=")) {
        event.preventDefault();
        setZoom(state.zoom + 0.1);
      }
      if ((event.ctrlKey || event.metaKey) && event.key === "-") {
        event.preventDefault();
        setZoom(state.zoom - 0.1);
      }
      if ((event.ctrlKey || event.metaKey) && event.key === "0") {
        event.preventDefault();
        fitZoom();
      }
      if (event.key === "Enter" && state.selectedNodeIds.length === 1) {
        event.preventDefault();
        const base = nodeById(state.selectedNodeIds[0]);
        mutate(() => {
          const next = nodeTemplate("process", base.x, base.y + 150, "次の処理");
          state.doc.nodes.push(next);
          state.doc.connectors.push(connectorTemplate(base.id, next.id, ""));
          state.selectedNodeIds = [next.id];
        });
      }
      if (event.key === "Tab" && state.selectedNodeIds.length === 1) {
        event.preventDefault();
        const base = nodeById(state.selectedNodeIds[0]);
        mutate(() => {
          const next = nodeTemplate("decision", base.x + 230, base.y, "分岐");
          state.doc.nodes.push(next);
          state.doc.connectors.push(connectorTemplate(base.id, next.id, ""));
          state.selectedNodeIds = [next.id];
        });
      }
      if (event.key === "F10" && event.shiftKey) {
        if (state.selectedNodeIds.length === 1 || state.selectedConnectorId) {
          event.preventDefault();
          const rect = svg.getBoundingClientRect();
          showContextMenu(rect.left + 80, rect.top + 80, state.selectedConnectorId ? "edge" : "node");
        }
      }
      if (key === "l" && !event.ctrlKey && !event.metaKey) {
        setTool("connector");
      }
      if (key === "v" && !event.ctrlKey && !event.metaKey) {
        setTool("select");
      }
      if (["arrowup", "arrowdown", "arrowleft", "arrowright"].includes(key) && state.selectedNodeIds.length) {
        event.preventDefault();
        const step = event.shiftKey ? 24 : 4;
        const dx = key === "arrowleft" ? -step : key === "arrowright" ? step : 0;
        const dy = key === "arrowup" ? -step : key === "arrowdown" ? step : 0;
        mutate(() => {
          state.doc.nodes.forEach((node) => {
            if (state.selectedNodeIds.includes(node.id)) {
              node.x = Math.max(0, node.x + dx);
              node.y = Math.max(0, node.y + dy);
            }
          });
        });
      }
    });
  }

  function consumeLaunchState() {
    let doc = null;
    let mode = "";
    try {
      mode = sessionStorage.getItem(LAUNCH_MODE_KEY) || "";
      const rawDoc = sessionStorage.getItem(LAUNCH_DOC_KEY);
      sessionStorage.removeItem(LAUNCH_MODE_KEY);
      sessionStorage.removeItem(LAUNCH_DOC_KEY);
      if (rawDoc) {
        const parsed = JSON.parse(rawDoc);
        if (parsed && Array.isArray(parsed.nodes) && Array.isArray(parsed.connectors)) {
          doc = { ...createBlankDoc(), ...parsed, schemaVersion: SCHEMA_VERSION };
        }
      }
    } catch (error) {
      doc = null;
    }
    return { mode, doc };
  }

  function boot() {
    bindUi();
    const launch = consumeLaunchState();
    if (launch.doc) {
      state.doc = launch.doc;
    } else if (launch.mode === "new") {
      state.doc = createBlankDoc();
    } else {
      try {
        const saved = JSON.parse(localStorage.getItem(AUTOSAVE_KEY) || "null");
        if (saved && Array.isArray(saved.nodes) && Array.isArray(saved.connectors)) {
          state.doc = { ...createBlankDoc(), ...saved };
        }
      } catch (error) {
        state.doc = createBlankDoc();
      }
    }
    $("pf-title").value = state.doc.title || "新しいフロー";
    render();
    fitZoom();
    setDirty(false);
  }

  boot();
})();
