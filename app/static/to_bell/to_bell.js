(function () {
  const params0 = new URLSearchParams(window.location.search);
  const state = {
    filter: params0.get("filter") || "today",
    view: params0.get("view") || "list",
    projectFilter: Number(params0.get("project_id") || 0),
    selectedTaskId: Number(params0.get("task") || 0),
    tasks: [],
    projects: [],
    calMonth: startOfMonth(new Date()),
    isPwa: document.body.classList.contains("tobell-pwa-mode"),
    swRegistration: null,
    foregroundTimer: 0,
  };

  const KANBAN_COLUMNS = [
    { key: "todo", label: "未着手" },
    { key: "doing", label: "進行中" },
    { key: "blocked", label: "保留" },
    { key: "review", label: "確認待ち" },
    { key: "returned", label: "差戻し" },
    { key: "done", label: "完了" },
  ];

  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value || "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));

  const mobileQuery = window.matchMedia("(max-width: 760px)");
  const isMobile = () => mobileQuery.matches;

  document.addEventListener("DOMContentLoaded", () => {
    if (isStandalone() && document.body.dataset.pwaShell !== "1") {
      window.location.replace(`/tools/to_bell/pwa${window.location.search || ""}`);
      return;
    }
    document.body.classList.add("tobell-page");
    bindFilters();
    bindViews();
    initProjectModal();
    initTemplateModal();
    initViewer();
    $("tb-quick-form").addEventListener("submit", createQuickTask);
    const newButton = $("tb-new-task");
    if (newButton) newButton.addEventListener("click", () => $("tb-title").focus());
    const templatesButton = $("tb-templates");
    if (templatesButton) templatesButton.addEventListener("click", openTemplateModal);
    const newProjectButton = $("tb-new-project");
    if (newProjectButton) newProjectButton.addEventListener("click", () => openProjectModal());
    $("tb-enable-push-notify").addEventListener("click", toggleNotifications);
    const reloadButton = $("tb-reload");
    if (reloadButton && isStandalone()) {
      // PWA（ホーム画面アプリ）にはブラウザの再読込が無いため専用ボタンを出す。
      reloadButton.hidden = false;
      reloadButton.addEventListener("click", () => window.location.reload());
    }
    const notifierButton = $("tb-open-notifier");
    if (notifierButton) notifierButton.addEventListener("click", openWindowsNotifier);
    const devicesButton = $("tb-push-devices");
    if (devicesButton) devicesButton.addEventListener("click", openDevicesModal);
    const testPushButton = $("tb-test-push-notify");
    if (testPushButton) testPushButton.addEventListener("click", sendTestPush);
    $("tb-read-all").addEventListener("click", readAllNotifications);
    initShareLink();
    $("tb-search").addEventListener("input", debounce(loadTasks, 220));
    // 端末の「戻る」操作で詳細オーバーレイを閉じる（スマホアプリ的な挙動）。
    window.addEventListener("popstate", () => {
      if (document.body.classList.contains("tb-detail-active")) {
        closeDetailDom();
      }
    });
    syncViewButtons();
    loadProjects().catch(() => {});
    loadTasks().catch(() => {});
    loadNotifications().catch(() => {});
    initNotifications();
  });

  function bindFilters() {
    document.querySelectorAll(".tobell-filter").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.filter === state.filter);
      button.addEventListener("click", () => {
        state.filter = button.dataset.filter || "today";
        state.view = "list";
        state.selectedTaskId = 0;
        closeDetail();
        document.querySelectorAll(".tobell-filter").forEach((item) => item.classList.remove("is-active"));
        button.classList.add("is-active");
        syncViewButtons();
        loadTasks();
      });
    });
  }

  function bindViews() {
    document.querySelectorAll(".tobell-view-btn").forEach((button) => {
      button.addEventListener("click", () => {
        state.view = button.dataset.view || "list";
        state.selectedTaskId = 0;
        closeDetail();
        syncViewButtons();
        loadTasks();
      });
    });
  }

  function syncViewButtons() {
    document.querySelectorAll(".tobell-view-btn").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.view === state.view);
    });
  }

  function effectiveFilter() {
    return state.view === "list" ? state.filter : "board";
  }

  async function loadTasks() {
    const params = new URLSearchParams({ filter: effectiveFilter(), q: $("tb-search").value || "", view: state.view || "list" });
    if (state.projectFilter) params.set("project_id", String(state.projectFilter));
    const data = await api(`/tools/to_bell/api/tasks?${params.toString()}`);
    state.tasks = data.tasks || [];
    renderSummary(data.summary || {});
    renderMain();
    if (state.selectedTaskId) {
      const selected = state.tasks.find((task) => task.id === state.selectedTaskId);
      if (selected) renderDetail(selected);
    }
  }

  function renderMain() {
    if (state.view === "kanban") {
      renderKanban();
    } else if (state.view === "calendar") {
      renderCalendar();
    } else {
      renderTasks();
    }
  }

  function renderSummary(summary) {
    const items = [
      `<span class="tobell-chip">表示 ${state.tasks.length}件</span>`,
      `<span class="tobell-chip">要対応 ${Number(summary.action_count || 0)}件</span>`,
      `<span class="tobell-chip">未読 ${Number(summary.unread_count || 0)}件</span>`,
    ];
    if (state.projectFilter) {
      const project = state.projects.find((item) => item.id === state.projectFilter);
      if (project) {
        items.push(`<span class="tobell-chip tobell-chip-project"><span class="tobell-dot" style="background:${esc(project.color)}"></span>${esc(project.name)} <button type="button" class="tobell-chip-clear" id="tb-clear-project" aria-label="プロジェクト絞り込み解除">×</button></span>`);
      }
    }
    $("tb-summary").innerHTML = items.join("");
    const clear = $("tb-clear-project");
    if (clear) clear.addEventListener("click", () => selectProject(0));
  }

  function renderTasks() {
    if (!state.tasks.length) {
      $("tb-task-list").innerHTML = '<div class="tobell-empty">ここにはまだタスクがありません。</div>';
      return;
    }
    $("tb-task-list").innerHTML = state.tasks.map((task) => {
      const due = task.due_at ? formatDue(task.due_at) : "通知なし";
      const done = task.status === "done" ? "checked" : "";
      const badgeClass = task.priority === "urgent" || isOverdue(task) ? "danger" : (task.priority === "high" ? "warning" : "");
      return `
        <article class="tobell-task ${task.id === state.selectedTaskId ? "is-selected" : ""}" data-task-id="${task.id}">
          <input class="tobell-check" type="checkbox" ${done} data-complete-id="${task.id}" aria-label="完了">
          <div class="tobell-task-body">
            <h3>${esc(task.title)}</h3>
            <p>${esc(task.description || "メモなし")}</p>
            <p class="tobell-task-meta">${esc(statusLabel(task.status))} / ${esc(due)} / 進捗 ${Number(task.progress || 0)}%</p>
            ${projectTag(task)}
          </div>
          <span class="tobell-badge ${badgeClass}">${esc(priorityLabel(task.priority))}</span>
        </article>`;
    }).join("");
    document.querySelectorAll("[data-task-id]").forEach((card) => {
      card.setAttribute("tabindex", "0");
      card.setAttribute("role", "button");
      const openTask = (event) => {
        if (event.target.matches("[data-complete-id]")) return;
        const task = state.tasks.find((item) => item.id === Number(card.dataset.taskId));
        if (task) renderDetail(task);
      };
      card.addEventListener("click", openTask);
      card.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          openTask(event);
        }
      });
    });
    document.querySelectorAll("[data-complete-id]").forEach((checkbox) => {
      checkbox.addEventListener("change", async () => {
        const id = Number(checkbox.dataset.completeId);
        const completing = checkbox.checked;
        checkbox.disabled = true;
        try {
          await api(`/tools/to_bell/api/tasks/${id}/${completing ? "complete" : "reopen"}`, { method: "POST" });
          await loadTasks();
          await loadNotifications();
          showFlash(completing ? "完了にしました" : "未完了に戻しました", "success");
        } catch (error) {
          checkbox.checked = !completing;
        } finally {
          checkbox.disabled = false;
        }
      });
    });
  }

  function projectTag(task) {
    if (!task.project) return "";
    return `<p class="tobell-task-project"><span class="tobell-dot" style="background:${esc(task.project.color)}"></span>${esc(task.project.name)}</p>`;
  }

  // ===== カンバン =====

  function renderKanban() {
    const container = $("tb-task-list");
    const board = document.createElement("div");
    board.className = "tobell-kanban";
    KANBAN_COLUMNS.forEach((column) => {
      const tasks = state.tasks.filter((task) => task.status === column.key);
      const col = document.createElement("section");
      col.className = "tobell-kan-col";
      col.dataset.status = column.key;
      col.innerHTML = `
        <header class="tobell-kan-head">${esc(column.label)}<span class="tobell-kan-count">${tasks.length}</span></header>
        <div class="tobell-kan-cards"></div>`;
      const cards = col.querySelector(".tobell-kan-cards");
      if (!tasks.length) {
        cards.innerHTML = '<div class="tobell-kan-empty">なし</div>';
      } else {
        tasks.forEach((task) => cards.appendChild(buildKanbanCard(task)));
      }
      if (!state.isPwa) bindColumnDrop(col, column.key);
      board.appendChild(col);
    });
    container.replaceChildren(board);
  }

  function buildKanbanCard(task) {
    const card = document.createElement("article");
    card.className = "tobell-kan-card";
    card.dataset.taskId = task.id;
    const overdue = isOverdue(task) ? "danger" : (task.priority === "urgent" ? "danger" : (task.priority === "high" ? "warning" : ""));
    const due = task.due_at ? formatDueShort(task.due_at) : "";
    let moveControl = "";
    if (state.isPwa) {
      // スマホはドラッグの代わりにネイティブ select で状態を移動する。
      const options = KANBAN_COLUMNS.map((column) =>
        `<option value="${column.key}" ${column.key === task.status ? "selected" : ""}>${esc(column.label)}へ</option>`
      ).join("");
      moveControl = `<select class="tobell-kan-move" data-move-id="${task.id}" aria-label="状態を移動">${options}</select>`;
    } else {
      card.setAttribute("draggable", "true");
    }
    card.innerHTML = `
      <div class="tobell-kan-card-body" data-open-id="${task.id}">
        <strong>${esc(task.title)}</strong>
        ${due ? `<span class="tobell-kan-due ${overdue}">${esc(due)}</span>` : ""}
        ${projectTag(task)}
      </div>
      ${moveControl}`;
    card.querySelector("[data-open-id]").addEventListener("click", () => openTaskById(task.id));
    const move = card.querySelector("[data-move-id]");
    if (move) {
      move.addEventListener("change", () => changeTaskStatus(task.id, move.value));
      move.addEventListener("click", (event) => event.stopPropagation());
    }
    if (!state.isPwa) {
      card.addEventListener("dragstart", (event) => {
        event.dataTransfer.setData("text/plain", String(task.id));
        card.classList.add("is-dragging");
      });
      card.addEventListener("dragend", () => card.classList.remove("is-dragging"));
    }
    return card;
  }

  function bindColumnDrop(col, status) {
    col.addEventListener("dragover", (event) => {
      event.preventDefault();
      col.classList.add("is-drop");
    });
    col.addEventListener("dragleave", () => col.classList.remove("is-drop"));
    col.addEventListener("drop", (event) => {
      event.preventDefault();
      col.classList.remove("is-drop");
      const id = Number(event.dataTransfer.getData("text/plain"));
      if (id) changeTaskStatus(id, status);
    });
  }

  async function changeTaskStatus(id, status) {
    const task = state.tasks.find((item) => item.id === id);
    if (!task || task.status === status) return;
    try {
      await api(`/tools/to_bell/api/tasks/${id}`, { method: "PUT", body: { status } });
      await loadTasks();
      showFlash(`「${statusLabel(status)}」に移動しました`, "success");
    } catch (error) {
      /* api() がトーストを表示済み */
    }
  }

  // ===== カレンダー =====

  function renderCalendar() {
    const container = $("tb-task-list");
    const anchor = state.calMonth;
    const year = anchor.getFullYear();
    const month = anchor.getMonth();
    const monthLabel = `${year}年${month + 1}月`;
    const first = new Date(year, month, 1);
    const startWeekday = first.getDay();
    const daysInMonth = new Date(year, month + 1, 0).getDate();

    const byDay = {};
    let noDue = 0;
    state.tasks.forEach((task) => {
      if (!task.due_at) {
        noDue += 1;
        return;
      }
      const d = new Date(task.due_at);
      if (d.getFullYear() === year && d.getMonth() === month) {
        const key = d.getDate();
        (byDay[key] = byDay[key] || []).push(task);
      }
    });

    const todayStr = new Date().toDateString();
    const cells = [];
    for (let i = 0; i < startWeekday; i += 1) cells.push('<div class="tobell-cal-cell is-empty"></div>');
    for (let day = 1; day <= daysInMonth; day += 1) {
      const cellDate = new Date(year, month, day);
      const isToday = cellDate.toDateString() === todayStr ? "is-today" : "";
      const tasks = byDay[day] || [];
      const limit = state.isPwa ? 2 : 4;
      const chips = tasks.slice(0, limit).map((task) =>
        `<button type="button" class="tobell-cal-task ${isOverdue(task) ? "danger" : ""}" data-open-id="${task.id}">${esc(task.title)}</button>`
      ).join("");
      const more = tasks.length > limit ? `<span class="tobell-cal-more">+${tasks.length - limit}</span>` : "";
      const iso = `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
      cells.push(`
        <div class="tobell-cal-cell ${isToday}">
          <button type="button" class="tobell-cal-daynum" data-add-date="${iso}" title="この日に追加">${day}</button>
          <div class="tobell-cal-tasks">${chips}${more}</div>
        </div>`);
    }

    const weekdays = ["日", "月", "火", "水", "木", "金", "土"]
      .map((w) => `<div class="tobell-cal-weekday">${w}</div>`).join("");
    container.innerHTML = `
      <div class="tobell-cal-bar">
        <button type="button" class="tobell-btn" data-cal-nav="-1">‹</button>
        <strong class="tobell-cal-month">${monthLabel}</strong>
        <button type="button" class="tobell-btn" data-cal-nav="1">›</button>
        <button type="button" class="tobell-btn" data-cal-nav="0">今日</button>
        ${noDue ? `<span class="tobell-chip">期日なし ${noDue}件</span>` : ""}
      </div>
      <div class="tobell-cal-grid">${weekdays}${cells.join("")}</div>`;

    container.querySelectorAll("[data-cal-nav]").forEach((button) => {
      button.addEventListener("click", () => {
        const delta = Number(button.dataset.calNav);
        state.calMonth = delta === 0 ? startOfMonth(new Date()) : new Date(year, month + delta, 1);
        renderCalendar();
      });
    });
    container.querySelectorAll("[data-open-id]").forEach((button) => {
      button.addEventListener("click", () => openTaskById(Number(button.dataset.openId)));
    });
    container.querySelectorAll("[data-add-date]").forEach((button) => {
      button.addEventListener("click", () => {
        const input = document.querySelector('#tb-quick-form input[name="due_date"]');
        if (input) input.value = button.dataset.addDate;
        const title = $("tb-title");
        if (title) {
          title.focus();
          title.scrollIntoView({ behavior: "smooth", block: "center" });
        }
        showFlash(`${button.dataset.addDate} の期日でタスクを追加できます`, "info");
      });
    });
  }

  function openTaskById(id) {
    const task = state.tasks.find((item) => item.id === id);
    if (task) renderDetail(task);
  }

  // ===== プロジェクト =====

  async function loadProjects() {
    try {
      const data = await api("/tools/to_bell/api/projects");
      state.projects = data.projects || [];
    } catch (error) {
      state.projects = [];
    }
    renderProjects();
  }

  function renderProjects() {
    const list = $("tb-project-list");
    if (list) {
      const rows = state.projects.map((project) => `
        <div class="tobell-project-row ${project.id === state.projectFilter ? "is-active" : ""}">
          <button type="button" class="tobell-project-pick" data-project-pick="${project.id}">
            <span class="tobell-dot" style="background:${esc(project.color)}"></span>
            <span class="tobell-project-name">${esc(project.name)}</span>
            <span class="tobell-project-count">${Number(project.open_count || 0)}</span>
          </button>
          <button type="button" class="tobell-project-edit" data-project-edit="${project.id}" aria-label="編集">⚙</button>
        </div>`).join("");
      list.innerHTML = rows || '<div class="tobell-empty">プロジェクトはありません。</div>';
      list.querySelectorAll("[data-project-pick]").forEach((button) => {
        button.addEventListener("click", () => selectProject(Number(button.dataset.projectPick)));
      });
      list.querySelectorAll("[data-project-edit]").forEach((button) => {
        button.addEventListener("click", () => openProjectModal(Number(button.dataset.projectEdit)));
      });
    }
    renderProjectBar();
  }

  function renderProjectBar() {
    const bar = $("tb-project-bar");
    if (!bar) return;
    const chips = [`<button type="button" class="tobell-pchip ${state.projectFilter ? "" : "is-active"}" data-project-pick="0">すべて</button>`];
    state.projects.forEach((project) => {
      chips.push(`<button type="button" class="tobell-pchip ${project.id === state.projectFilter ? "is-active" : ""}" data-project-pick="${project.id}">
        <span class="tobell-dot" style="background:${esc(project.color)}"></span>${esc(project.name)}</button>`);
    });
    chips.push('<button type="button" class="tobell-pchip tobell-pchip-add" id="tb-pchip-add" aria-label="プロジェクトを追加">＋</button>');
    bar.innerHTML = chips.join("");
    bar.querySelectorAll("[data-project-pick]").forEach((button) => {
      button.addEventListener("click", () => selectProject(Number(button.dataset.projectPick)));
    });
    const add = $("tb-pchip-add");
    if (add) add.addEventListener("click", () => openProjectModal());
  }

  function selectProject(id) {
    state.projectFilter = id || 0;
    state.selectedTaskId = 0;
    closeDetail();
    renderProjects();
    loadTasks();
  }

  function initProjectModal() {
    const modal = $("tb-project-modal");
    if (!modal) return;
    const close = () => modal.setAttribute("hidden", "");
    modal.querySelectorAll("[data-project-close]").forEach((el) => el.addEventListener("click", close));
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !modal.hasAttribute("hidden")) close();
    });
    $("tb-project-form").addEventListener("submit", saveProject);
    $("tb-project-delete").addEventListener("click", deleteProject);
    const bulkButton = $("tb-project-bulk-assign");
    if (bulkButton) bulkButton.addEventListener("click", openBulkAssignModal);
    initBulkAssignModal();
  }

  function openProjectModal(projectId) {
    const modal = $("tb-project-modal");
    if (!modal) return;
    const form = $("tb-project-form");
    form.reset();
    const project = projectId ? state.projects.find((item) => item.id === projectId) : null;
    form.elements.id.value = project ? project.id : "";
    if (project) {
      form.elements.name.value = project.name || "";
      form.elements.description.value = project.description || "";
      form.elements.color.value = project.color || "#2563eb";
      form.elements.visibility_scope.value = project.visibility_scope || "office";
      form.elements.calendar_only.checked = !!project.calendar_only;
    } else {
      form.elements.color.value = "#2563eb";
      form.elements.calendar_only.checked = false;
    }
    $("tb-project-delete").hidden = !project;
    const bulkButton = $("tb-project-bulk-assign");
    if (bulkButton) bulkButton.hidden = !project;
    $("tb-project-title").textContent = project ? "プロジェクトを編集" : "新規プロジェクト";
    modal.removeAttribute("hidden");
    form.elements.name.focus();
  }

  async function saveProject(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const id = form.elements.id.value;
    const body = {
      name: form.elements.name.value,
      description: form.elements.description.value,
      color: form.elements.color.value,
      visibility_scope: form.elements.visibility_scope.value,
      calendar_only: form.elements.calendar_only.checked,
    };
    const btn = form.querySelector('[type="submit"]');
    if (btn) btn.disabled = true;
    try {
      if (id) {
        await api(`/tools/to_bell/api/projects/${id}`, { method: "PUT", body });
      } else {
        await api("/tools/to_bell/api/projects", { method: "POST", body });
      }
      $("tb-project-modal").setAttribute("hidden", "");
      await loadProjects();
      showFlash("プロジェクトを保存しました", "success");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function deleteProject() {
    const id = $("tb-project-form").elements.id.value;
    if (!id || !window.confirm("このプロジェクトを削除しますか？タスクは残り、プロジェクトの紐付けだけ外れます。")) return;
    await api(`/tools/to_bell/api/projects/${id}`, { method: "DELETE" });
    $("tb-project-modal").setAttribute("hidden", "");
    if (state.projectFilter === Number(id)) state.projectFilter = 0;
    await loadProjects();
    await loadTasks();
    showFlash("プロジェクトを削除しました", "info");
  }

  const bulkAssign = { projectId: 0, tasks: [], selected: new Set() };

  function initBulkAssignModal() {
    const modal = $("tb-bulk-assign-modal");
    if (!modal) return;
    const close = () => modal.setAttribute("hidden", "");
    modal.querySelectorAll("[data-bulk-close]").forEach((el) => el.addEventListener("click", close));
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !modal.hasAttribute("hidden")) close();
    });
    const search = $("tb-bulk-assign-search");
    if (search) search.addEventListener("input", debounce(loadAssignableTasks, 220));
    const toggle = $("tb-bulk-assign-toggle");
    if (toggle) toggle.addEventListener("click", toggleAllAssignable);
    const submit = $("tb-bulk-assign-submit");
    if (submit) submit.addEventListener("click", submitBulkAssign);
  }

  async function openBulkAssignModal() {
    const id = Number($("tb-project-form").elements.id.value || 0);
    if (!id) return;
    bulkAssign.projectId = id;
    bulkAssign.selected = new Set();
    const modal = $("tb-bulk-assign-modal");
    if (!modal) return;
    const searchEl = $("tb-bulk-assign-search");
    if (searchEl) searchEl.value = "";
    modal.removeAttribute("hidden");
    await loadAssignableTasks();
  }

  async function loadAssignableTasks() {
    if (!bulkAssign.projectId) return;
    const list = $("tb-bulk-assign-list");
    if (!list) return;
    list.innerHTML = '<div class="tobell-empty">読み込み中です。</div>';
    const searchValue = ($("tb-bulk-assign-search") || {}).value || "";
    const params = new URLSearchParams({ q: searchValue });
    try {
      const data = await api(`/tools/to_bell/api/projects/${bulkAssign.projectId}/assignable-tasks?${params.toString()}`);
      bulkAssign.tasks = data.tasks || [];
    } catch (error) {
      bulkAssign.tasks = [];
    }
    renderAssignableTasks();
  }

  function renderAssignableTasks() {
    const list = $("tb-bulk-assign-list");
    if (!list) return;
    if (!bulkAssign.tasks.length) {
      list.innerHTML = '<div class="tobell-empty">追加できるタスクはありません。</div>';
      return;
    }
    list.innerHTML = bulkAssign.tasks.map((task) => {
      const due = task.due_at ? formatDue(task.due_at) : "通知なし";
      const checked = bulkAssign.selected.has(task.id) ? "checked" : "";
      const projectName = task.project ? esc(task.project.name) : "未設定";
      return `
        <label class="tobell-bulk-row">
          <input type="checkbox" data-bulk-id="${task.id}" ${checked}>
          <span class="tobell-bulk-title">${esc(task.title)}</span>
          <span class="tobell-bulk-meta">${esc(statusLabel(task.status))} / ${esc(due)} / ${projectName}</span>
        </label>`;
    }).join("");
    list.querySelectorAll("[data-bulk-id]").forEach((cb) => {
      cb.addEventListener("change", () => {
        const id = Number(cb.dataset.bulkId);
        if (cb.checked) bulkAssign.selected.add(id);
        else bulkAssign.selected.delete(id);
      });
    });
  }

  function toggleAllAssignable() {
    if (!bulkAssign.tasks.length) return;
    const allSelected = bulkAssign.tasks.every((task) => bulkAssign.selected.has(task.id));
    if (allSelected) {
      bulkAssign.tasks.forEach((task) => bulkAssign.selected.delete(task.id));
    } else {
      bulkAssign.tasks.forEach((task) => bulkAssign.selected.add(task.id));
    }
    renderAssignableTasks();
  }

  async function submitBulkAssign() {
    if (!bulkAssign.projectId) return;
    const ids = Array.from(bulkAssign.selected);
    if (!ids.length) {
      showFlash("追加するタスクを選んでください。", "info");
      return;
    }
    const button = $("tb-bulk-assign-submit");
    if (button) button.disabled = true;
    try {
      const result = await api(`/tools/to_bell/api/projects/${bulkAssign.projectId}/assign-tasks`, {
        method: "POST",
        body: { task_ids: ids },
      });
      $("tb-bulk-assign-modal").setAttribute("hidden", "");
      await loadProjects();
      await loadTasks();
      showFlash(`${Number(result.updated || 0)}件のタスクを紐づけました`, "success");
    } finally {
      if (button) button.disabled = false;
    }
  }

  function fillProjectSelect(selectEl, current) {
    if (!selectEl) return;
    const options = ['<option value="">なし</option>'];
    state.projects.forEach((project) => {
      options.push(`<option value="${project.id}">${esc(project.name)}</option>`);
    });
    selectEl.innerHTML = options.join("");
    selectEl.value = current ? String(current) : "";
  }

  // ===== テンプレート =====

  function initTemplateModal() {
    const modal = $("tb-template-modal");
    if (!modal) return;
    const close = () => modal.setAttribute("hidden", "");
    modal.querySelectorAll("[data-template-close]").forEach((el) => el.addEventListener("click", close));
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !modal.hasAttribute("hidden")) close();
    });
    $("tb-template-form").addEventListener("submit", createBlankTemplate);
  }

  async function openTemplateModal() {
    const modal = $("tb-template-modal");
    if (!modal) return;
    modal.removeAttribute("hidden");
    await loadTemplates();
  }

  async function loadTemplates() {
    const list = $("tb-template-list");
    if (!list) return;
    list.innerHTML = '<div class="tobell-empty">読み込み中です。</div>';
    const data = await api("/tools/to_bell/api/templates");
    const rows = data.templates || [];
    if (!rows.length) {
      list.innerHTML = '<div class="tobell-empty">テンプレートはまだありません。</div>';
      return;
    }
    list.innerHTML = rows.map((tpl) => `
      <div class="tobell-template-row" data-template-id="${tpl.id}">
        <div class="tobell-template-main">
          <strong>${esc(tpl.name)}</strong>
          <span class="tobell-template-meta">${tpl.scope === "office" ? "所属共有" : "自分のみ"} / サブタスク${Number(tpl.subtask_count || 0)}件</span>
        </div>
        <button type="button" class="tobell-btn tobell-btn-primary" data-template-use="${tpl.id}">使う</button>
        <button type="button" class="tobell-btn tobell-danger" data-template-del="${tpl.id}" aria-label="削除">×</button>
      </div>`).join("");
    list.querySelectorAll("[data-template-use]").forEach((button) => {
      button.addEventListener("click", () => instantiateTemplate(Number(button.dataset.templateUse)));
    });
    list.querySelectorAll("[data-template-del]").forEach((button) => {
      button.addEventListener("click", () => deleteTemplate(Number(button.dataset.templateDel)));
    });
  }

  async function instantiateTemplate(id) {
    const body = {};
    if (state.projectFilter) body.project_id = state.projectFilter;
    await api(`/tools/to_bell/api/templates/${id}/instantiate`, { method: "POST", body });
    $("tb-template-modal").setAttribute("hidden", "");
    await loadTasks();
    showFlash("テンプレートからタスクを作成しました", "success");
  }

  async function createBlankTemplate(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const name = form.elements.name.value.trim();
    if (!name) return;
    const btn = form.querySelector('[type="submit"]');
    if (btn) btn.disabled = true;
    try {
      await api("/tools/to_bell/api/templates", { method: "POST", body: { name } });
      form.reset();
      await loadTemplates();
      showFlash("テンプレートを作成しました", "success");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function deleteTemplate(id) {
    if (!window.confirm("このテンプレートを削除しますか？")) return;
    await api(`/tools/to_bell/api/templates/${id}`, { method: "DELETE" });
    await loadTemplates();
    showFlash("テンプレートを削除しました", "info");
  }

  function renderDetail(task) {
    state.selectedTaskId = task.id;
    const fragment = $("tb-detail-template").content.cloneNode(true);
    $("tb-detail").replaceChildren(fragment);
    openDetailOverlay();
    const form = $("tb-detail-form");
    form.elements.id.value = task.id;
    form.elements.title.value = task.title || "";
    form.elements.description.value = task.description || "";
    form.elements.status.value = task.status || "todo";
    form.elements.priority.value = task.priority || "normal";
    form.elements.due_at.value = task.due_at ? task.due_at.slice(0, 16) : "";
    form.elements.assigned_to.value = task.assigned_to || "";
    fillProjectSelect(form.elements.project_id, task.project ? task.project.id : "");
    form.elements.tags.value = (task.tags || []).map((tag) => tag.name).join(", ");
    renderSubtasks(task);
    renderComments(task);
    renderAttachments(task);
    form.addEventListener("submit", saveDetail);
    $("tb-subtask-form").addEventListener("submit", addSubtask);
    $("tb-comment-form").addEventListener("submit", addComment);
    const attachmentForm = $("tb-attachment-form");
    if (attachmentForm) attachmentForm.addEventListener("submit", uploadAttachment);
    const backButton = $("tb-detail-back");
    if (backButton) backButton.addEventListener("click", closeDetail);
    document.querySelectorAll("[data-action]").forEach((button) => button.addEventListener("click", detailAction));
    renderMain();
  }

  // ===== 添付ビューワ =====

  const viewer = { scale: 1 };

  function initViewer() {
    const modal = $("tb-viewer");
    if (!modal) return;
    modal.querySelectorAll("[data-viewer-close]").forEach((el) => el.addEventListener("click", closeViewer));
    modal.querySelectorAll("[data-viewer-zoom]").forEach((el) => {
      el.addEventListener("click", () => setViewerScale(Number(el.dataset.viewerZoom)));
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !modal.hasAttribute("hidden")) closeViewer();
    });
  }

  function viewerKind(attachment) {
    const mime = (attachment.mime_type || "").toLowerCase();
    if (mime.startsWith("image/")) return "image";
    if (mime === "application/pdf") return "pdf";
    const name = (attachment.file_name || "").toLowerCase();
    if (/\.(png|jpe?g|gif|webp|bmp|svg)$/.test(name)) return "image";
    if (/\.pdf$/.test(name)) return "pdf";
    return null;
  }

  function openViewer(attachment) {
    const kind = viewerKind(attachment);
    if (!kind) {
      // ビューワ対象外。新規タブでブラウザに任せる。
      window.open(attachment.href, "_blank", "noopener");
      return;
    }
    viewer.scale = 1;
    $("tb-viewer-title").textContent = attachment.file_name || "プレビュー";
    const dl = $("tb-viewer-download");
    dl.href = attachment.href;
    dl.setAttribute("download", attachment.file_name || "");
    const stage = $("tb-viewer-stage");
    if (kind === "image") {
      stage.innerHTML = `<img alt="${esc(attachment.file_name || "image")}">`;
      stage.querySelector("img").src = attachment.href;
    } else {
      // PDF: ブラウザ内蔵ビューワを使う。Chrome系は #view=FitH で横幅にフィット。
      const url = `${attachment.href}#view=FitH`;
      stage.innerHTML = `<iframe title="${esc(attachment.file_name || "pdf")}"></iframe>`;
      stage.querySelector("iframe").src = url;
    }
    applyViewerScale();
    $("tb-viewer").removeAttribute("hidden");
    document.body.classList.add("tb-viewer-active");
  }

  function closeViewer() {
    const modal = $("tb-viewer");
    if (!modal) return;
    modal.setAttribute("hidden", "");
    $("tb-viewer-stage").innerHTML = "";
    document.body.classList.remove("tb-viewer-active");
  }

  function setViewerScale(delta) {
    if (delta === 0) {
      viewer.scale = 1;
    } else {
      const next = viewer.scale + delta * 0.25;
      viewer.scale = Math.max(0.5, Math.min(3, next));
    }
    applyViewerScale();
  }

  function applyViewerScale() {
    const stage = $("tb-viewer-stage");
    if (stage) stage.style.transform = `scale(${viewer.scale})`;
    const pct = $("tb-viewer-pct");
    if (pct) pct.textContent = `${Math.round(viewer.scale * 100)}%`;
  }

  function renderAttachments(task) {
    const container = $("tb-attachments");
    if (!container) return;
    const rows = task.attachments || [];
    if (!rows.length) {
      container.innerHTML = '<div class="tobell-empty">添付はありません。</div>';
      return;
    }
    container.innerHTML = rows.map((item) => {
      const previewable = viewerKind(item) ? '1' : '';
      return `
      <div class="tobell-attachment">
        <a href="${item.href}" class="tobell-attachment-link" data-attachment-id="${item.id}" data-viewable="${previewable}" target="_blank" rel="noopener">${esc(item.file_name)}</a>
        <span class="tobell-attachment-size">${formatFileSize(item.file_size)}</span>
        <button type="button" class="tobell-subtask-delete" data-attachment-delete="${item.id}" aria-label="削除">×</button>
      </div>`;
    }).join("");
    container.querySelectorAll("[data-attachment-id]").forEach((link) => {
      link.addEventListener("click", (event) => {
        const id = Number(link.dataset.attachmentId);
        const att = rows.find((row) => row.id === id);
        if (att && viewerKind(att)) {
          event.preventDefault();
          openViewer(att);
        }
      });
    });
    container.querySelectorAll("[data-attachment-delete]").forEach((button) => {
      button.addEventListener("click", async () => {
        if (!window.confirm("この添付を削除しますか？")) return;
        button.disabled = true;
        try {
          await api(`/tools/to_bell/api/attachments/${button.dataset.attachmentDelete}`, { method: "DELETE" });
          await refreshSelectedTask();
          showFlash("添付を削除しました", "info");
        } finally {
          button.disabled = false;
        }
      });
    });
  }

  async function uploadAttachment(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const input = form.elements.file;
    if (!input || !input.files || !input.files.length) {
      showFlash("ファイルを選択してください", "error");
      return;
    }
    const data = new FormData();
    data.append("file", input.files[0]);
    const btn = form.querySelector('[type="submit"]');
    if (btn) btn.disabled = true;
    try {
      await api(`/tools/to_bell/api/tasks/${state.selectedTaskId}/attachments`, { method: "POST", body: data });
      form.reset();
      await refreshSelectedTask();
      showFlash("添付しました", "success");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function openDetailOverlay() {
    const wasActive = document.body.classList.contains("tb-detail-active");
    document.body.classList.add("tb-detail-active");
    // モバイルでは戻るボタンで閉じられるよう履歴エントリを1つだけ積む。
    if (!wasActive && isMobile() && !(history.state && history.state.tbDetail)) {
      try {
        history.pushState({ tbDetail: true }, "");
      } catch (error) {
        /* 履歴操作に失敗しても致命的ではない */
      }
    }
  }

  function closeDetail() {
    if (
      document.body.classList.contains("tb-detail-active")
      && history.state
      && history.state.tbDetail
    ) {
      history.back(); // popstate 経由で closeDetailDom が呼ばれる
      return;
    }
    closeDetailDom();
  }

  function closeDetailDom() {
    state.selectedTaskId = 0;
    document.body.classList.remove("tb-detail-active");
    $("tb-detail").innerHTML = '<div class="tobell-empty">タスクを選ぶと詳細が開きます。</div>';
    renderMain();
  }

  function renderSubtasks(task) {
    const rows = task.subtasks || [];
    $("tb-subtasks").innerHTML = rows.length ? rows.map((item) => `
      <div class="tobell-subtask">
        <input type="checkbox" ${item.is_done ? "checked" : ""} data-subtask-id="${item.id}">
        <span>${esc(item.title)}</span>
        <button type="button" class="tobell-subtask-delete" data-subtask-delete="${item.id}" aria-label="削除">×</button>
      </div>`).join("") : '<div class="tobell-empty">サブタスクはありません。</div>';
    document.querySelectorAll("[data-subtask-id]").forEach((box) => {
      box.addEventListener("change", async () => {
        box.disabled = true;
        try {
          await api(`/tools/to_bell/api/subtasks/${box.dataset.subtaskId}`, {
            method: "PUT",
            body: { is_done: box.checked },
          });
          await refreshSelectedTask();
          showFlash("サブタスクを更新しました", "success");
        } catch (error) {
          box.checked = !box.checked;
        } finally {
          box.disabled = false;
        }
      });
    });
    document.querySelectorAll("[data-subtask-delete]").forEach((btn) => {
      btn.addEventListener("click", async (event) => {
        event.preventDefault();
        btn.disabled = true;
        try {
          await api(`/tools/to_bell/api/subtasks/${btn.dataset.subtaskDelete}`, { method: "DELETE" });
          await refreshSelectedTask();
          showFlash("サブタスクを削除しました", "success");
        } finally {
          btn.disabled = false;
        }
      });
    });
  }

  function renderComments(task) {
    const rows = task.comments || [];
    $("tb-comments").innerHTML = rows.length ? rows.map((item) => `
      <div class="tobell-comment">
        <strong>${esc(item.created_by)}</strong>
        <div>${esc(item.body)}</div>
      </div>`).join("") : '<div class="tobell-empty">コメントはありません。</div>';
  }

  async function createQuickTask(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const btn = form.querySelector('[type="submit"]');
    if (btn) btn.disabled = true;
    try {
      const payload = Object.fromEntries(new FormData(form).entries());
      await api("/tools/to_bell/api/tasks", { method: "POST", body: payload });
      form.reset();
      await loadTasks();
      showFlash("タスクを追加しました", "success");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function saveDetail(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const btn = form.querySelector('[type="submit"]');
    if (btn) btn.disabled = true;
    try {
      const payload = Object.fromEntries(new FormData(form).entries());
      payload.tags = payload.tags || "";
      await api(`/tools/to_bell/api/tasks/${payload.id}`, { method: "PUT", body: payload });
      await refreshSelectedTask();
      await loadTasks();
      showFlash("保存しました", "success");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function detailAction(event) {
    const btn = event.currentTarget;
    const action = btn.dataset.action;
    const id = state.selectedTaskId;
    if (!id) return;
    if (action === "template") {
      await templateFromTask(id, btn);
      return;
    }
    if (action === "archive" && !window.confirm("このタスクをアーカイブしますか？")) return;
    if (action === "delete" && !window.confirm("このタスクを完全に削除します。元に戻せません。よろしいですか？")) return;
    btn.disabled = true;
    try {
      let path = `/tools/to_bell/api/tasks/${id}/${action}`;
      let method = "POST";
      if (action === "archive") {
        path = `/tools/to_bell/api/tasks/${id}`;
        method = "DELETE";
      } else if (action === "delete") {
        path = `/tools/to_bell/api/tasks/${id}?hard=1`;
        method = "DELETE";
      }
      await api(path, { method });
      if (action === "archive" || action === "delete") {
        closeDetail();
      } else {
        await refreshSelectedTask();
      }
      await loadTasks();
      await loadNotifications();
      showFlash({
        complete: "完了にしました",
        reopen: "未完了に戻しました",
        archive: "アーカイブしました",
        delete: "削除しました",
      }[action] || "更新しました", "success");
    } finally {
      btn.disabled = false;
    }
  }

  async function templateFromTask(id, btn) {
    const name = window.prompt("テンプレート名を入力してください。", "");
    if (name === null) return;
    btn.disabled = true;
    try {
      await api(`/tools/to_bell/api/tasks/${id}/template`, { method: "POST", body: { name } });
      showFlash("このタスクをテンプレート化しました", "success");
    } finally {
      btn.disabled = false;
    }
  }

  async function addSubtask(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const title = form.elements.title.value.trim();
    if (!title) return;
    const btn = form.querySelector('[type="submit"]');
    if (btn) btn.disabled = true;
    try {
      await api(`/tools/to_bell/api/tasks/${state.selectedTaskId}/subtasks`, { method: "POST", body: { title } });
      form.reset();
      await refreshSelectedTask();
      showFlash("サブタスクを追加しました", "success");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function addComment(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const body = form.elements.body.value.trim();
    if (!body) return;
    const btn = form.querySelector('[type="submit"]');
    if (btn) btn.disabled = true;
    try {
      await api(`/tools/to_bell/api/tasks/${state.selectedTaskId}/comments`, { method: "POST", body: { body } });
      form.reset();
      await refreshSelectedTask();
      await loadNotifications();
      showFlash("コメントを送信しました", "success");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function refreshSelectedTask() {
    if (!state.selectedTaskId) return;
    const task = await api(`/tools/to_bell/api/tasks/${state.selectedTaskId}`);
    const index = state.tasks.findIndex((item) => item.id === task.id);
    if (index >= 0) state.tasks.splice(index, 1, task);
    renderDetail(task);
  }

  async function loadNotifications() {
    const container = $("tb-notifications");
    if (!container) return;
    const data = await api("/tools/to_bell/api/notifications");
    const rows = data.notifications || [];
    container.innerHTML = rows.length ? rows.slice(0, 8).map((item) => `
      <div class="tobell-notification">
        <strong>${esc(item.title)}</strong>
        <div>${esc(item.body)}</div>
      </div>`).join("") : '<div class="tobell-empty">通知はありません。</div>';
  }

  async function readAllNotifications() {
    const btn = $("tb-read-all");
    if (btn) btn.disabled = true;
    try {
      await api("/tools/to_bell/api/notifications/read-all", { method: "POST" });
      await loadNotifications();
      await loadTasks();
      showFlash("通知をすべて既読にしました", "success");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function openWindowsNotifier() {
    const win = window.open(
      "/tools/to_bell/notifier",
      "toBellNotifier",
      "popup=yes,width=390,height=360,menubar=no,toolbar=no,location=no,status=no"
    );
    if (!win) {
      window.alert("通知待受ウィンドウを開けませんでした。ポップアップ許可を確認してください。");
    }
  }

  // ----- 通知（プッシュ / 前面）まわり -----

  function isStandalone() {
    return (
      window.matchMedia && window.matchMedia("(display-mode: standalone)").matches
    ) || window.navigator.standalone === true;
  }

  function isIos() {
    return /iphone|ipad|ipod/i.test(window.navigator.userAgent);
  }

  async function registerServiceWorker() {
    if (!("serviceWorker" in navigator)) return null;
    if (state.swRegistration) return state.swRegistration;
    try {
      const registration = await navigator.serviceWorker.register("/service-worker.js", { scope: "/" });
      // iOS では active になる前に subscribe すると失敗するため、ready を待つ。
      await navigator.serviceWorker.ready;
      state.swRegistration = registration;
      return registration;
    } catch (error) {
      return null;
    }
  }

  function initNotifications() {
    registerServiceWorker().then(async () => {
      await refreshNotifyToggle();
      // プッシュ購読済みならサーバーが通知するので前面監視は不要。
      // 購読がない（権限だけ付与済み等）場合のフォールバックとして動かす。
      if ($("tb-enable-push-notify") && $("tb-enable-push-notify").dataset.state !== "on") {
        startForegroundWatch();
      }
    });
  }

  async function refreshNotifyToggle() {
    const button = $("tb-enable-push-notify");
    const label = $("tb-notify-status");
    const supported = "Notification" in window && "serviceWorker" in navigator && "PushManager" in window;
    let subscribed = false;
    if (supported && Notification.permission === "granted") {
      try {
        const registration = await registerServiceWorker();
        subscribed = Boolean(registration && (await registration.pushManager.getSubscription()));
      } catch (error) {
        subscribed = false;
      }
    }
    if (button) {
      button.dataset.state = subscribed ? "on" : "off";
      button.textContent = subscribed ? "通知を無効化" : "通知を有効化";
    }
    if (label) {
      if (!("Notification" in window)) {
        label.textContent = "通知: 非対応端末";
        label.dataset.tone = "off";
      } else if (Notification.permission === "denied") {
        label.textContent = "通知: ブロック中";
        label.dataset.tone = "off";
      } else if (subscribed) {
        label.textContent = "通知: 有効";
        label.dataset.tone = "on";
      } else if (Notification.permission === "granted") {
        label.textContent = "通知: 停止中";
        label.dataset.tone = "idle";
      } else {
        label.textContent = "通知: 未設定";
        label.dataset.tone = "idle";
      }
    }
  }

  async function toggleNotifications() {
    const button = $("tb-enable-push-notify");
    if (!button) return;
    button.disabled = true;
    try {
      if (button.dataset.state === "on") {
        await disablePushNotifications();
      } else {
        await enablePushNotifications();
      }
    } finally {
      button.disabled = false;
    }
  }

  function updateNotifyStatus() {
    const label = $("tb-notify-status");
    if (!label) return;
    if (!("Notification" in window)) {
      label.textContent = "通知: 非対応端末";
      label.dataset.tone = "off";
      return;
    }
    if (Notification.permission === "granted") {
      label.textContent = "通知: 有効";
      label.dataset.tone = "on";
    } else if (Notification.permission === "denied") {
      label.textContent = "通知: ブロック中";
      label.dataset.tone = "off";
    } else {
      label.textContent = "通知: 未設定";
      label.dataset.tone = "idle";
    }
  }

  async function enablePushNotifications() {
    try {
      if (!window.isSecureContext) {
        window.alert("通知を使うには https での接続が必要です。");
        return;
      }
      if (!("Notification" in window)) {
        window.alert("このブラウザは通知に対応していません。");
        return;
      }
      if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
        if (isIos() && !isStandalone()) {
          window.alert("iPhoneでは、共有メニューから「ホーム画面に追加」したTo Bellアプリを開いてから通知を有効化してください。");
        } else {
          window.alert("このブラウザではプッシュ通知を利用できません。");
        }
        return;
      }

      // 1) 権限要求はユーザー操作直後に呼ぶ（iOS の制約）。
      const permission = await Notification.requestPermission();
      updateNotifyStatus();
      if (permission !== "granted") {
        window.alert("通知が許可されませんでした。端末の設定から通知を許可してください。");
        return;
      }

      // 2) Service Worker を登録し、active になるまで待つ。
      const registration = await registerServiceWorker();
      if (!registration) {
        window.alert("通知用のService Workerを登録できませんでした。");
        return;
      }

      // 3) サーバの公開鍵を取得。
      const keyData = await api("/tools/to_bell/api/push/public-key");
      if (!keyData.public_key) {
        window.alert(keyData.message || "プッシュ通知の公開鍵を取得できませんでした。");
        return;
      }

      // 4) 既存購読を再利用。ただしサーバ鍵が変わっていたら作り直す。
      const appServerKey = urlBase64ToUint8Array(keyData.public_key);
      let subscription = await registration.pushManager.getSubscription();
      if (subscription && !sameAppServerKey(subscription.options && subscription.options.applicationServerKey, appServerKey)) {
        try {
          await subscription.unsubscribe();
        } catch (error) {
          /* 失敗しても下で作り直す */
        }
        subscription = null;
      }
      if (!subscription) {
        subscription = await registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: appServerKey,
        });
      }

      const result = await api("/tools/to_bell/api/push/subscribe", {
        method: "POST",
        body: { subscription: subscription.toJSON() },
      });
      stopForegroundWatch();
      showFlash(`${result.device_label || "この端末"} の通知を有効にしました`, "success");
    } catch (error) {
      const message = error && error.message ? error.message : String(error);
      window.alert(`通知の有効化に失敗しました。\n${message}`);
    } finally {
      await refreshNotifyToggle();
    }
  }

  async function disablePushNotifications() {
    try {
      const registration = await registerServiceWorker();
      const subscription = registration ? await registration.pushManager.getSubscription() : null;
      if (subscription) {
        const endpoint = subscription.endpoint;
        try {
          await subscription.unsubscribe();
        } catch (error) {
          /* ローカルの解除に失敗してもサーバ側を無効化する */
        }
        await api("/tools/to_bell/api/push/unsubscribe", { method: "POST", body: { endpoint } });
      }
      stopForegroundWatch();
      showFlash("この端末の通知を無効にしました", "info");
    } catch (error) {
      const message = error && error.message ? error.message : String(error);
      window.alert(`通知の無効化に失敗しました。\n${message}`);
    } finally {
      await refreshNotifyToggle();
    }
  }

  async function sendTestPush() {
    const btn = $("tb-test-push-notify");
    if (btn) btn.disabled = true;
    try {
      const result = await api("/tools/to_bell/api/push/test", { method: "POST" });
      if ((result.sent || 0) === 0) {
        await showLocalNotification("To Bell テスト通知", { body: "前面通知のテストです。" });
      }
      showFlash(`テスト通知を送信しました（送信 ${result.sent || 0} / 失敗 ${result.failed || 0}）`, "info");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function showLocalNotification(title, options) {
    const opts = Object.assign(
      {
        icon: "/static/img/android-chrome-192x192.png",
        badge: "/static/img/apple-touch-icon.png",
      },
      options || {}
    );
    try {
      const registration = state.swRegistration || (await registerServiceWorker());
      if (registration && registration.showNotification) {
        await registration.showNotification(title, opts);
        return true;
      }
    } catch (error) {
      // フォールバックへ
    }
    try {
      const note = new Notification(title, opts);
      if (opts.data && opts.data.url) {
        note.onclick = () => window.open(opts.data.url, "toBellMain");
      }
      return true;
    } catch (error) {
      return false;
    }
  }

  function startForegroundWatch() {
    if (state.foregroundTimer) return;
    if (!("Notification" in window)) return;
    foregroundTick();
    state.foregroundTimer = window.setInterval(foregroundTick, 60000);
  }

  function stopForegroundWatch() {
    if (state.foregroundTimer) {
      window.clearInterval(state.foregroundTimer);
      state.foregroundTimer = 0;
    }
  }

  async function foregroundTick() {
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    let data;
    try {
      const response = await fetch("/tools/to_bell/api/notifications/due-tasks");
      if (!response.ok) return;
      data = await response.json();
    } catch (error) {
      return;
    }
    for (const task of data.tasks || []) {
      if (!task.due_at) continue;
      const key = `toBellNotified:${task.id}:${task.due_at}`;
      if (localStorage.getItem(key)) continue;
      localStorage.setItem(key, new Date().toISOString());
      await showLocalNotification(`ToBell ${task.title} from DSTT`, {
        body: task.description || "",
        tag: key,
        data: { url: `/tools/to_bell?task=${task.id}` },
      });
    }
  }

  function initShareLink() {
    const trigger = $("tb-share-link");
    const modal = $("tb-share-modal");
    if (!trigger || !modal) return;

    const close = () => modal.setAttribute("hidden", "");
    trigger.addEventListener("click", () => openShareModal(modal));
    modal.querySelectorAll("[data-share-close]").forEach((el) => el.addEventListener("click", close));
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !modal.hasAttribute("hidden")) close();
    });

    const issueBtn = $("tb-share-issue");
    const reissueBtn = $("tb-share-reissue");
    const revokeBtn = $("tb-share-revoke");
    issueBtn.addEventListener("click", () => mutateShare("/tools/to_bell/api/share/issue", modal, "共有リンクを発行しました。", issueBtn));
    reissueBtn.addEventListener("click", () => mutateShare("/tools/to_bell/api/share/issue", modal, "共有リンクを再発行しました。", reissueBtn));
    revokeBtn.addEventListener("click", () => mutateShare("/tools/to_bell/api/share/revoke", modal, "共有リンクを無効化しました。", revokeBtn));
    $("tb-share-copy").addEventListener("click", copyShareUrl);
    const pwaCopy = $("tb-share-pwa-copy");
    if (pwaCopy) pwaCopy.addEventListener("click", copySharePwaUrl);
  }

  async function openShareModal(modal) {
    modal.removeAttribute("hidden");
    try {
      renderShareState(await api("/tools/to_bell/api/share"));
    } catch (err) {
      /* api() がトーストを表示済み */
    }
  }

  async function mutateShare(path, modal, message, btn) {
    if (btn) btn.disabled = true;
    try {
      const data = await api(path, { method: "POST" });
      renderShareState(data);
      showFlash(message, "success");
    } catch (err) {
      /* api() がトーストを表示済み */
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function renderShareState(data) {
    const active = Boolean(data && data.active);
    const input = $("tb-share-url");
    if (input) input.value = (data && (data.mobile_url || data.url)) || "";
    const pwaInput = $("tb-share-pwa-url");
    if (pwaInput) pwaInput.value = (data && data.pwa_url) || "";
    document.querySelectorAll('[data-share-when="active"]').forEach((el) => {
      el.style.display = active ? "" : "none";
    });
    document.querySelectorAll('[data-share-when="inactive"]').forEach((el) => {
      el.style.display = active ? "none" : "";
    });
    $("tb-share-issue").style.display = active ? "none" : "";
    $("tb-share-reissue").style.display = active ? "" : "none";
    $("tb-share-revoke").style.display = active ? "" : "none";
  }

  async function copyShareUrl() {
    const input = $("tb-share-url");
    if (!input || !input.value) return;
    try {
      await navigator.clipboard.writeText(input.value);
      showFlash("URLをコピーしました。", "success");
    } catch (err) {
      input.select();
      document.execCommand("copy");
      showFlash("URLをコピーしました。", "success");
    }
  }

  async function copySharePwaUrl() {
    const input = $("tb-share-pwa-url");
    if (!input || !input.value) return;
    try {
      await navigator.clipboard.writeText(input.value);
      showFlash("PWA専用リンクをコピーしました。", "success");
    } catch (err) {
      input.select();
      document.execCommand("copy");
      showFlash("PWA専用リンクをコピーしました。", "success");
    }
  }

  async function openDevicesModal() {
    const modal = $("tb-devices-modal");
    if (!modal) return;
    modal.removeAttribute("hidden");
    modal.querySelectorAll("[data-devices-close]").forEach((el) => {
      if (!el.dataset.bound) {
        el.dataset.bound = "1";
        el.addEventListener("click", () => modal.setAttribute("hidden", ""));
      }
    });
    if (!modal.dataset.escBound) {
      modal.dataset.escBound = "1";
      document.addEventListener("keydown", (event) => {
        if (event.key === "Escape" && !modal.hasAttribute("hidden")) modal.setAttribute("hidden", "");
      });
    }
    await loadDevices();
  }

  async function loadDevices() {
    const list = $("tb-devices-list");
    if (!list) return;
    list.innerHTML = '<div class="tobell-empty">読み込み中です。</div>';
    const data = await api("/tools/to_bell/api/push/subscriptions");
    const rows = data.subscriptions || [];
    if (!rows.length) {
      list.innerHTML = '<div class="tobell-empty">登録済みの通知先はありません。</div>';
      return;
    }
    const editable = !isMobile() && !isStandalone();
    list.innerHTML = rows.map((item) => `
      <div class="tobell-device ${item.is_active ? "" : "is-disabled"}" data-device-id="${item.id}">
        <div class="tobell-device-main">
          <input class="tobell-device-label" value="${esc(item.device_label)}" aria-label="通知先名" ${editable ? "" : "readonly"}>
          <div class="tobell-device-meta">${item.is_active ? "有効" : "無効"} / ${esc(item.endpoint_tail || "")}</div>
        </div>
        ${editable ? `<button type="button" class="tobell-btn" data-device-save="${item.id}">保存</button>
        <button type="button" class="tobell-btn tobell-danger" data-device-disable="${item.id}" ${item.is_active ? "" : "disabled"}>無効化</button>
        <button type="button" class="tobell-btn tobell-danger" data-device-delete="${item.id}">削除</button>` : ""}
      </div>
    `).join("");
    list.querySelectorAll("[data-device-save]").forEach((button) => {
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          const row = button.closest("[data-device-id]");
          const label = row ? row.querySelector(".tobell-device-label").value : "";
          await api(`/tools/to_bell/api/push/subscriptions/${button.dataset.deviceSave}`, {
            method: "PUT",
            body: { device_label: label },
          });
          await loadDevices();
          showFlash("通知先を更新しました。", "success");
        } finally {
          button.disabled = false;
        }
      });
    });
    list.querySelectorAll("[data-device-disable]").forEach((button) => {
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          await api(`/tools/to_bell/api/push/subscriptions/${button.dataset.deviceDisable}`, { method: "DELETE" });
          await loadDevices();
          showFlash("通知先を無効化しました。", "success");
        } finally {
          button.disabled = false;
        }
      });
    });
    list.querySelectorAll("[data-device-delete]").forEach((button) => {
      button.addEventListener("click", async () => {
        if (!window.confirm("この通知先を完全に削除しますか？元に戻せません。")) return;
        await api(`/tools/to_bell/api/push/subscriptions/${button.dataset.deviceDelete}?hard=1`, { method: "DELETE" });
        await loadDevices();
        showFlash("通知先を削除しました。", "success");
      });
    });
  }

  async function api(path, options) {
    const init = options || {};
    const headers = { ...(init.headers || {}) };
    if (init.body && !(init.body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(init.body);
    }
    const response = await fetch(path, { ...init, headers });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      showFlash(data.error || data.message || "処理に失敗しました。", "error");
      throw new Error(data.error || data.message || response.statusText);
    }
    return data;
  }

  function isOverdue(task) {
    return task.due_at && task.status !== "done" && new Date(task.due_at).getTime() < Date.now();
  }

  function formatDue(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return value.slice(0, 16).replace("T", " ");
    const y = date.getFullYear();
    const m = String(date.getMonth() + 1).padStart(2, "0");
    const d = String(date.getDate()).padStart(2, "0");
    const hh = String(date.getHours()).padStart(2, "0");
    const mm = String(date.getMinutes()).padStart(2, "0");
    return `${y}-${m}-${d} ${hh}:${mm}`;
  }

  function formatDueShort(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    const m = String(date.getMonth() + 1).padStart(2, "0");
    const d = String(date.getDate()).padStart(2, "0");
    const hh = String(date.getHours()).padStart(2, "0");
    const mm = String(date.getMinutes()).padStart(2, "0");
    const time = hh === "23" && mm === "59" ? "" : ` ${hh}:${mm}`;
    return `${m}/${d}${time}`;
  }

  function startOfMonth(date) {
    return new Date(date.getFullYear(), date.getMonth(), 1);
  }

  function formatFileSize(bytes) {
    const size = Number(bytes || 0);
    if (size < 1024) return `${size} B`;
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
    return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  }

  function statusLabel(status) {
    return {
      todo: "未着手",
      doing: "進行中",
      blocked: "保留",
      review: "確認待ち",
      returned: "差戻し",
      done: "完了",
      archived: "アーカイブ",
    }[status] || status;
  }

  function priorityLabel(priority) {
    return { low: "低", normal: "通常", high: "高", urgent: "緊急" }[priority] || "通常";
  }

  function showFlash(message, type) {
    const node = $("tb-flash");
    if (!node) return;
    node.textContent = message;
    node.className = `tobell-flash ${type || "info"} is-visible`;
    window.clearTimeout(showFlash._timer);
    showFlash._timer = window.setTimeout(() => {
      node.classList.remove("is-visible");
    }, 2400);
  }

  function debounce(fn, wait) {
    let timer = 0;
    return () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(fn, wait);
    };
  }

  function sameAppServerKey(existing, wanted) {
    if (!existing) return false;
    const current = new Uint8Array(existing);
    if (current.length !== wanted.length) return false;
    for (let i = 0; i < current.length; i += 1) {
      if (current[i] !== wanted[i]) return false;
    }
    return true;
  }

  function urlBase64ToUint8Array(base64String) {
    const padding = "=".repeat((4 - base64String.length % 4) % 4);
    const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
    const rawData = window.atob(base64);
    const outputArray = new Uint8Array(rawData.length);
    for (let i = 0; i < rawData.length; i += 1) {
      outputArray[i] = rawData.charCodeAt(i);
    }
    return outputArray;
  }
}());
