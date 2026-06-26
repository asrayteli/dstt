/* カメラスキャナー フロントエンド
 *
 * 流れ（スマホ）: スキャン作成 → 各面を撮影（サーバーで自動補正）→ 必要なら
 * 四隅の手動調整・90度回転 → PDF作成。PC では一覧の閲覧・PDFダウンロード・削除のみ。
 */
(function () {
  "use strict";

  var CFG = window.CS_CONFIG || { presets: [], defaultPreset: "", isMobile: false };
  var IS_MOBILE = !!CFG.isMobile;
  var PRESETS = {};
  (CFG.presets || []).forEach(function (p) { PRESETS[p.key] = p; });

  var state = { scan: null, quads: {} };  // quads[side] = 自動検出の正規化四隅（調整初期値）

  // ---- DOM ヘルパー ----
  function $(id) { return document.getElementById(id); }
  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function toast(msg, isError) {
    var t = $("cs-toast");
    if (!t) return;
    t.textContent = msg;
    t.className = "cs-toast" + (isError ? " cs-toast-error" : "");
    t.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(function () { t.hidden = true; }, isError ? 4200 : 2400);
  }

  // ---- API ----
  function apiJSON(url, opts) {
    opts = opts || {};
    opts.headers = opts.headers || {};
    if (opts.json !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(opts.json);
      delete opts.json;
    }
    return fetch(url, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) throw new Error(data.error || ("通信に失敗しました (" + r.status + ")"));
        return data;
      });
    });
  }

  function fmtDate(iso) {
    if (!iso) return "";
    var m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/);
    if (!m) return iso;
    return m[1] + "/" + m[2] + "/" + m[3] + " " + m[4] + ":" + m[5];
  }

  // ---- スキャン一覧 ----
  function loadList() {
    return apiJSON("/tools/camera_scanner/api/scans").then(function (data) {
      renderList(data.scans || [], data.can_admin);
      var c = $("cs-stat-count");
      if (c) c.textContent = String(data.count != null ? data.count : (data.scans || []).length);
    }).catch(function (e) { toast(e.message, true); });
  }

  function renderList(scans, canAdmin) {
    var box = $("cs-list");
    if (!box) return;
    box.innerHTML = "";
    if (!scans.length) {
      box.appendChild(el("p", "cs-empty", "まだスキャンはありません。"));
      return;
    }
    scans.forEach(function (s) {
      var preset = PRESETS[s.preset_key] || { label: s.preset_key };
      var item = el("div", "cs-item");
      var main = el("div", "cs-item-main");
      main.appendChild(el("div", "cs-item-title", esc(s.title || "（無題）")));
      var meta = esc(preset.label) + "・" + esc(fmtDate(s.created_at));
      if (canAdmin && s.created_by) meta += "・" + esc(s.created_by);
      main.appendChild(el("div", "cs-item-meta", meta));
      item.appendChild(main);

      var statusBadge = el("span",
        "cs-status " + (s.status === "completed" ? "cs-status-completed" : "cs-status-draft"),
        s.status === "completed" ? "完成" : "下書き");
      item.appendChild(statusBadge);

      var actions = el("div", "cs-item-actions");
      if (s.has_pdf) {
        var a = el("a", "cs-btn cs-btn-primary cs-btn-sm", "PDF");
        a.href = "/tools/camera_scanner/api/scans/" + s.id + "/pdf";
        a.target = "_blank";
        a.rel = "noopener";
        actions.appendChild(a);
      }
      if (IS_MOBILE && s.status !== "completed") {
        var resume = el("button", "cs-btn cs-btn-muted cs-btn-sm", "続きから");
        resume.dataset.act = "resume"; resume.dataset.id = s.id;
        actions.appendChild(resume);
      }
      var del = el("button", "cs-btn cs-btn-danger cs-btn-sm", "削除");
      del.dataset.act = "delete"; del.dataset.id = s.id;
      actions.appendChild(del);
      item.appendChild(actions);
      box.appendChild(item);
    });
  }

  // ---- 撮影フロー（スマホ） ----
  function showStart() {
    state.scan = null; state.quads = {};
    if ($("cs-active")) $("cs-active").hidden = true;
    if ($("cs-start")) $("cs-start").hidden = false;
  }
  function showActive() {
    if ($("cs-start")) $("cs-start").hidden = true;
    if ($("cs-active")) $("cs-active").hidden = false;
  }

  function activePreset() {
    return (state.scan && PRESETS[state.scan.preset_key]) || PRESETS[CFG.defaultPreset] || { sides: [] };
  }

  function renderSides() {
    var box = $("cs-sides");
    if (!box || !state.scan) return;
    var scan = state.scan;
    var preset = activePreset();
    var title = scan.title ? scan.title : "（無題）";
    if ($("cs-active-title")) $("cs-active-title").textContent = preset.label + "：" + title;
    box.innerHTML = "";

    (preset.sides || []).forEach(function (side) {
      var key = side.key;
      var has = !!scan["has_" + key];
      var detected = !!scan[key + "_detected"];
      var card = el("div", "cs-side");

      var head = el("div", "cs-side-head");
      var left = el("div", "");
      left.appendChild(el("div", "cs-side-title", esc(side.label)));
      if (side.hint) left.appendChild(el("div", "cs-side-hint", esc(side.hint)));
      head.appendChild(left);
      var badge;
      if (!has) badge = el("span", "cs-badge cs-badge-none", "未撮影");
      else if (detected) badge = el("span", "cs-badge cs-badge-auto", "切り取り済み");
      else badge = el("span", "cs-badge cs-badge-warn", "要調整");
      head.appendChild(badge);
      card.appendChild(head);

      var preview = el("div", "cs-preview-box");
      if (has) {
        var img = el("img");
        img.alt = side.label;
        img.src = "/tools/camera_scanner/api/scans/" + scan.id + "/sides/" + key + "/preview?t=" + Date.now();
        preview.appendChild(img);
      } else {
        preview.appendChild(el("div", "cs-preview-empty", "未撮影"));
      }
      card.appendChild(preview);

      var actions = el("div", "cs-side-actions");
      var label = el("label", "cs-btn cs-btn-primary cs-capture-label", "<span>" + (has ? "撮り直す" : "撮影する") + "</span>");
      var input = el("input");
      input.type = "file"; input.accept = "image/*"; input.setAttribute("capture", "environment");
      input.dataset.side = key;
      label.appendChild(input);
      actions.appendChild(label);

      if (has) {
        var adj = el("button", "cs-btn cs-btn-muted", "四隅を調整");
        adj.dataset.act = "adjust"; adj.dataset.side = key;
        actions.appendChild(adj);
        var rot = el("button", "cs-btn cs-btn-muted", "90°回転");
        rot.dataset.act = "rotate"; rot.dataset.side = key;
        actions.appendChild(rot);
      }
      card.appendChild(actions);
      box.appendChild(card);
    });

    updateFinalize();
  }

  function updateFinalize() {
    var btn = $("cs-finalize-btn");
    if (!btn || !state.scan) return;
    var preset = activePreset();
    var ok = (preset.sides || []).every(function (s) { return !!state.scan["has_" + s.key]; });
    btn.disabled = !ok;
  }

  function startScan() {
    var preset = ($("cs-preset") && $("cs-preset").value) || CFG.defaultPreset;
    var title = ($("cs-title") && $("cs-title").value) || "";
    apiJSON("/tools/camera_scanner/api/scans", { method: "POST", json: { preset_key: preset, title: title } })
      .then(function (data) {
        state.scan = data.scan; state.quads = {};
        showActive(); renderSides();
        loadList();
      }).catch(function (e) { toast(e.message, true); });
  }

  function uploadSide(side, file) {
    if (!file || !state.scan) return;
    var fd = new FormData();
    fd.append("file", file);
    toast("処理中…");
    apiJSON("/tools/camera_scanner/api/scans/" + state.scan.id + "/sides/" + side, { method: "POST", body: fd })
      .then(function (data) {
        state.scan = data.scan;
        state.quads[side] = data.quad || null;
        renderSides();
        loadList();
        if (data.detected) {
          toast("自動で切り取りました");
        } else {
          toast("自動検出できませんでした。四隅を合わせてください", true);
          openAdjust(side);
        }
      }).catch(function (e) { toast(e.message, true); });
  }

  function rotateSide(side) {
    if (!state.scan) return;
    apiJSON("/tools/camera_scanner/api/scans/" + state.scan.id + "/sides/" + side + "/adjust",
      { method: "POST", json: { rotate: 90 } })
      .then(function (data) { state.scan = data.scan; renderSides(); })
      .catch(function (e) { toast(e.message, true); });
  }

  function finalize() {
    if (!state.scan) return;
    var btn = $("cs-finalize-btn");
    if (btn) btn.disabled = true;
    apiJSON("/tools/camera_scanner/api/scans/" + state.scan.id + "/finalize", { method: "POST" })
      .then(function (data) {
        toast("PDFを作成しました");
        showStart();
        return loadList();
      }).catch(function (e) { toast(e.message, true); updateFinalize(); });
  }

  function resumeScan(id) {
    apiJSON("/tools/camera_scanner/api/scans/" + id).then(function (data) {
      state.scan = data.scan; state.quads = {};
      showActive(); renderSides();
    }).catch(function (e) { toast(e.message, true); });
  }

  function deleteScan(id) {
    if (!window.confirm("このスキャンを削除しますか？")) return;
    apiJSON("/tools/camera_scanner/api/scans/" + id, { method: "DELETE" }).then(function () {
      if (state.scan && String(state.scan.id) === String(id)) showStart();
      loadList();
    }).catch(function (e) { toast(e.message, true); });
  }

  // ---- 四隅の手動調整オーバーレイ ----
  var adjust = { side: null, corners: null, dragging: -1 };

  function openAdjust(side) {
    if (!state.scan) return;
    adjust.side = side;
    var quad = state.quads[side];
    // 初期四隅: 自動検出があればそれ、無ければ内側に10%インセットした既定枠。
    if (quad && quad.length === 4) {
      adjust.corners = quad.map(function (p) { return { x: clamp01(p.x), y: clamp01(p.y) }; });
    } else {
      adjust.corners = [{ x: 0.1, y: 0.1 }, { x: 0.9, y: 0.1 }, { x: 0.9, y: 0.9 }, { x: 0.1, y: 0.9 }];
    }
    var img = $("cs-adjust-img");
    img.onload = function () { layoutAdjust(); };
    img.src = "/tools/camera_scanner/api/scans/" + state.scan.id + "/sides/" + side + "/original?t=" + Date.now();
    $("cs-adjust").hidden = false;
  }
  function closeAdjust() {
    $("cs-adjust").hidden = true;
    adjust.side = null; adjust.corners = null; adjust.dragging = -1;
  }
  function clamp01(v) { return Math.max(0, Math.min(1, v)); }

  function imgBox() {
    var stage = $("cs-adjust-stage");
    var img = $("cs-adjust-img");
    var sr = stage.getBoundingClientRect();
    var ir = img.getBoundingClientRect();
    return { left: ir.left - sr.left, top: ir.top - sr.top, w: ir.width, h: ir.height };
  }

  function layoutAdjust() {
    if (!adjust.corners) return;
    var b = imgBox();
    var svg = $("cs-adjust-svg");
    var poly = $("cs-adjust-poly");
    var pts = adjust.corners.map(function (c) {
      return [b.left + c.x * b.w, b.top + c.y * b.h];
    });
    poly.setAttribute("points", pts.map(function (p) { return p[0] + "," + p[1]; }).join(" "));
    var handles = svg.querySelectorAll(".cs-handle");
    handles.forEach(function (h) {
      var i = parseInt(h.dataset.i, 10);
      h.setAttribute("cx", pts[i][0]);
      h.setAttribute("cy", pts[i][1]);
    });
  }

  function pointerPos(ev) {
    var stage = $("cs-adjust-stage");
    var sr = stage.getBoundingClientRect();
    var t = (ev.touches && ev.touches[0]) || ev;
    return { x: t.clientX - sr.left, y: t.clientY - sr.top };
  }

  function onHandleDown(ev) {
    var h = ev.target.closest(".cs-handle");
    if (!h) return;
    adjust.dragging = parseInt(h.dataset.i, 10);
    ev.preventDefault();
  }
  function onMove(ev) {
    if (adjust.dragging < 0) return;
    var b = imgBox();
    var p = pointerPos(ev);
    adjust.corners[adjust.dragging] = {
      x: clamp01((p.x - b.left) / b.w),
      y: clamp01((p.y - b.top) / b.h)
    };
    layoutAdjust();
    ev.preventDefault();
  }
  function onUp() { adjust.dragging = -1; }

  function applyAdjust() {
    if (!state.scan || !adjust.side || !adjust.corners) return;
    var side = adjust.side;
    apiJSON("/tools/camera_scanner/api/scans/" + state.scan.id + "/sides/" + side + "/adjust",
      { method: "POST", json: { points: adjust.corners } })
      .then(function (data) {
        state.scan = data.scan;
        state.quads[side] = adjust.corners.slice();
        closeAdjust();
        renderSides();
        toast("切り取りました");
      }).catch(function (e) { toast(e.message, true); });
  }

  // ---- イベント結線 ----
  function wire() {
    var presetSel = $("cs-preset");
    if (presetSel) {
      var setDesc = function () {
        var p = PRESETS[presetSel.value];
        if ($("cs-preset-desc")) $("cs-preset-desc").textContent = p ? p.description : "";
      };
      presetSel.addEventListener("change", setDesc);
      setDesc();
    }
    if ($("cs-start-btn")) $("cs-start-btn").addEventListener("click", startScan);
    if ($("cs-active-close")) $("cs-active-close").addEventListener("click", function () { showStart(); });
    if ($("cs-finalize-btn")) $("cs-finalize-btn").addEventListener("click", finalize);
    if ($("cs-refresh")) $("cs-refresh").addEventListener("click", loadList);

    var sides = $("cs-sides");
    if (sides) {
      sides.addEventListener("change", function (ev) {
        var input = ev.target;
        if (input && input.matches('input[type="file"][data-side]')) {
          var file = input.files && input.files[0];
          uploadSide(input.dataset.side, file);
          input.value = "";
        }
      });
      sides.addEventListener("click", function (ev) {
        var btn = ev.target.closest("button[data-act]");
        if (!btn) return;
        if (btn.dataset.act === "adjust") openAdjust(btn.dataset.side);
        else if (btn.dataset.act === "rotate") rotateSide(btn.dataset.side);
      });
    }

    var list = $("cs-list");
    if (list) {
      list.addEventListener("click", function (ev) {
        var btn = ev.target.closest("button[data-act]");
        if (!btn) return;
        if (btn.dataset.act === "resume") resumeScan(btn.dataset.id);
        else if (btn.dataset.act === "delete") deleteScan(btn.dataset.id);
      });
    }

    // 調整オーバーレイ
    if ($("cs-adjust-cancel")) $("cs-adjust-cancel").addEventListener("click", closeAdjust);
    if ($("cs-adjust-apply")) $("cs-adjust-apply").addEventListener("click", applyAdjust);
    var svg = $("cs-adjust-svg");
    if (svg) {
      svg.addEventListener("mousedown", onHandleDown);
      svg.addEventListener("touchstart", onHandleDown, { passive: false });
    }
    document.addEventListener("mousemove", onMove);
    document.addEventListener("touchmove", onMove, { passive: false });
    document.addEventListener("mouseup", onUp);
    document.addEventListener("touchend", onUp);
    window.addEventListener("resize", function () { if (!$("cs-adjust").hidden) layoutAdjust(); });
  }

  wire();
  loadList();
})();
