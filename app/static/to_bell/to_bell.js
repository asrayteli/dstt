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
    currentUser: (document.querySelector("[data-tb-user]") || {}).dataset?.tbUser || "",
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

  // 各種マジック値の定数化（調整はここを起点に行う）
  const DEFAULT_ATTACHMENT_MAX_BYTES = 25 * 1024 * 1024;
  const FOREGROUND_TICK_MS = 60000;       // 前面での期限監視間隔
  const FLASH_DURATION_MS = 2400;         // トースト表示時間
  const SEARCH_DEBOUNCE_MS = 220;         // 検索入力のデバウンス
  const NOTIFICATION_DISPLAY_LIMIT = 8;   // 通知パネルの表示件数
  // FILEPOST 閾値設定はほぼ変化しないため、セッション中はキャッシュする。
  let filepostThresholdCache = null;

  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value || "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));

  // メモ・コメント内の http(s) URL を、クリックできるリンクに変換する。
  // すべての文字列は esc() を通すので XSS にはならない（href も esc 済み・http/https 限定）。
  const URL_RE = /(https?:\/\/[^\s<>"']+)/gi;
  function linkify(value) {
    const text = String(value || "");
    let html = "";
    let last = 0;
    let match;
    URL_RE.lastIndex = 0;
    while ((match = URL_RE.exec(text)) !== null) {
      html += esc(text.slice(last, match.index));
      let url = match[0];
      let trail = "";
      // 末尾の句読点・閉じ括弧はURLから除外する（「(http://...)」「http://...。」対策）。
      const tm = url.match(/[)\]\}.,!?;:、。）」』]+$/);
      if (tm) {
        trail = url.slice(url.length - tm[0].length);
        url = url.slice(0, url.length - tm[0].length);
      }
      const safe = esc(url);
      html += `<a href="${safe}" target="_blank" rel="noopener noreferrer" class="tobell-link">${safe}</a>`;
      html += esc(trail);
      last = match.index + match[0].length;
    }
    html += esc(text.slice(last));
    return html;
  }

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
    initNewTaskModal();
    initContextMenu();
    initBulkDelete();
    const newButton = $("tb-new-task");
    if (newButton) newButton.addEventListener("click", () => openNewTaskModal());
    const templatesButton = $("tb-templates");
    if (templatesButton) templatesButton.addEventListener("click", openTemplateModal);
    const newProjectButton = $("tb-new-project");
    if (newProjectButton) newProjectButton.addEventListener("click", () => openProjectModal());
    const projectsManageButton = $("tb-projects-manage");
    if (projectsManageButton) projectsManageButton.addEventListener("click", openProjectsListModal);
    initProjectsListModal();
    const enablePushButton = $("tb-enable-push-notify");
    if (enablePushButton) enablePushButton.addEventListener("click", toggleNotifications);
    const reloadButton = $("tb-reload");
    if (reloadButton && isStandalone()) {
      // PWA（ホーム画面アプリ）にはブラウザの再読込が無いため専用ボタンを出す。
      // 押すと Service Worker キャッシュを全消去してから再読込（スーパーリロード相当）する。
      reloadButton.hidden = false;
      reloadButton.addEventListener("click", () => hardReload(reloadButton));
    }
    const notifierButton = $("tb-open-notifier");
    if (notifierButton) notifierButton.addEventListener("click", openWindowsNotifier);
    const devicesButton = $("tb-push-devices");
    if (devicesButton) devicesButton.addEventListener("click", openDevicesModal);
    const testPushButton = $("tb-test-push-notify");
    if (testPushButton) testPushButton.addEventListener("click", sendTestPush);
    $("tb-read-all").addEventListener("click", readAllNotifications);
    initShareLink();
    initSettingsModal();
    initLinksModal();
    $("tb-search").addEventListener("input", debounce(loadTasks, SEARCH_DEBOUNCE_MS));
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
    loadGoogleStatus().catch(() => {});
    handleGoogleReturn();
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
      const active = button.dataset.view === state.view;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-selected", String(active));
    });
  }

  function effectiveFilter() {
    return state.view === "list" ? state.filter : "board";
  }

  async function loadTasks() {
    const params = new URLSearchParams({ filter: effectiveFilter(), q: $("tb-search").value || "", view: state.view || "list" });
    if (state.projectFilter) params.set("project_id", String(state.projectFilter));
    if (!state.tasks.length) {
      $("tb-task-list").innerHTML = '<div class="tobell-empty tobell-loading">読み込み中…</div>';
    }
    const data = await api(`/tools/to_bell/api/tasks?${params.toString()}`);
    state.tasks = data.tasks || [];
    renderSummary(data.summary || {});
    renderMain();
    if (state.selectedTaskId) {
      const selected = state.tasks.find((task) => task.id === state.selectedTaskId);
      if (selected) renderDetail(selected);
      else await openTaskFromLink(state.selectedTaskId);
    }
  }

  // 通知（プッシュ／アプリ内）のリンクは /tools/to_bell?task=<id> で開く。
  // 対象が現在のフィルタに含まれない（連携タスク・完了済み・別プロジェクトなど）
  // ことは普通にあるので、一覧に無ければ単体で取得して詳細を開く。
  async function openTaskFromLink(taskId) {
    if (!taskId) return;
    try {
      const task = await api(`/tools/to_bell/api/tasks/${taskId}`);
      renderDetail(task);
    } catch (_) {
      state.selectedTaskId = 0;
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

  // 日付（due_at）を持つプロジェクトを、タスク一覧に出す対象として返す。
  // 特定プロジェクトに絞り込み中・完了/連携フィルタでは出さない。
  function datedProjectsForList() {
    if (state.projectFilter) return [];
    if (["done", "integrations", "archived"].includes(state.filter)) return [];
    // 検索中はタスクと同じ条件で絞る（以前は検索しても無関係なプロジェクトが残った）。
    const q = (($("tb-search") || {}).value || "").trim().toLowerCase();
    return state.projects
      .filter((project) => project.due_at && project.status !== "archived")
      .filter((project) => !q || `${project.name || ""} ${project.description || ""}`.toLowerCase().includes(q))
      .slice()
      .sort((a, b) => new Date(a.due_at).getTime() - new Date(b.due_at).getTime());
  }

  function projectIsOverdue(project) {
    return project.due_at && new Date(project.due_at).getTime() < Date.now();
  }

  function projectTaskCardHtml(project) {
    const due = formatDue(project.due_at);
    const badgeClass = projectIsOverdue(project) ? "danger" : "";
    return `
      <article class="tobell-task tobell-task-as-project" data-project-card="${project.id}" tabindex="0" role="button">
        <span class="tobell-project-card-icon" aria-hidden="true">📁</span>
        <div class="tobell-task-body">
          <h3><span class="tobell-dot" style="background:${esc(project.color)}"></span>${esc(project.name)}</h3>
          <p>${esc(project.description || "プロジェクト")}</p>
          <p class="tobell-task-meta">プロジェクト / ${esc(due)} / 未完了 ${Number(project.open_count || 0)}件</p>
        </div>
        <span class="tobell-badge ${badgeClass}">${badgeClass ? "期限超過" : "予定"}</span>
      </article>`;
  }

  function bindProjectCards(list) {
    list.querySelectorAll("[data-project-card]").forEach((card) => {
      const open = () => selectProject(Number(card.dataset.projectCard));
      card.addEventListener("click", open);
      card.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); }
      });
      card.addEventListener("contextmenu", (event) => {
        event.preventDefault();
        openContextMenu(event, projectMenuItems(Number(card.dataset.projectCard)));
      });
    });
  }

  function renderTasks() {
    const list = $("tb-task-list");
    const projectCards = datedProjectsForList();
    if (!state.tasks.length && !projectCards.length) {
      list.innerHTML =
        '<div class="tobell-empty">' +
        '<p>ここにはまだタスクがありません。</p>' +
        '<button type="button" class="tobell-btn tobell-btn-primary" id="tb-empty-add">＋ 最初のタスクを追加</button>' +
        '</div>';
      const addBtn = $("tb-empty-add");
      if (addBtn) addBtn.addEventListener("click", () => openNewTaskModal());
      return;
    }
    const projectHtml = projectCards.map(projectTaskCardHtml).join("");
    list.innerHTML = projectHtml + state.tasks.map((task) => {
      const due = taskDueText(task);
      const done = task.status === "done" ? "checked" : "";
      const badgeClass = task.priority === "urgent" || isOverdue(task) ? "danger" : (task.priority === "high" ? "warning" : "");
      const pinned = task.pinned ? "is-pinned" : "";
      return `
        <article class="tobell-task ${task.id === state.selectedTaskId ? "is-selected" : ""} ${pinned}" data-task-id="${task.id}">
          <input class="tobell-check" type="checkbox" ${done} data-complete-id="${task.id}" aria-label="完了">
          <div class="tobell-task-body">
            <h3>${task.pinned ? '<span class="tobell-task-pin" aria-hidden="true">📌</span> ' : ""}${esc(task.title)}</h3>
            <p>${linkify(task.description || "メモなし")}</p>
            <p class="tobell-task-meta">${esc(statusLabel(task.status))} / ${esc(due)} / 進捗 ${Number(task.progress || 0)}%</p>
            ${projectTag(task)}
          </div>
          <button type="button" class="tobell-task-pintoggle ${task.pinned ? "is-on" : ""}" data-pin-id="${task.id}" aria-label="${task.pinned ? "ピン留め解除" : "ピン留め"}" title="${task.pinned ? "ピン留め解除" : "ピン留め"}">📌</button>
          <span class="tobell-badge ${badgeClass}">${esc(priorityLabel(task.priority))}</span>
        </article>`;
    }).join("");
    list.querySelectorAll("[data-task-id]").forEach((card) => {
      card.setAttribute("tabindex", "0");
      card.setAttribute("role", "button");
      const openTask = (event) => {
        if (event.target.matches("[data-complete-id]")) return;
        if (event.target.matches("[data-pin-id]")) return;
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
    list.querySelectorAll("[data-pin-id]").forEach((button) => {
      button.addEventListener("click", async (event) => {
        event.stopPropagation();
        const id = Number(button.dataset.pinId);
        button.disabled = true;
        try {
          await api(`/tools/to_bell/api/tasks/${id}/pin`, { method: "POST" });
          await loadTasks();
        } finally {
          button.disabled = false;
        }
      });
    });
    list.querySelectorAll("[data-complete-id]").forEach((checkbox) => {
      checkbox.addEventListener("change", async () => {
        const id = Number(checkbox.dataset.completeId);
        const completing = checkbox.checked;
        checkbox.disabled = true;
        try {
          await api(`/tools/to_bell/api/tasks/${id}/${completing ? "complete" : "reopen"}`, { method: "POST" });
          await Promise.all([loadTasks(), loadNotifications()]);
          showFlash(completing ? "完了にしました" : "未完了に戻しました", "success");
        } catch (error) {
          checkbox.checked = !completing;
        } finally {
          checkbox.disabled = false;
        }
      });
    });
    bindProjectCards(list);
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
      if (!state.isPwa && !isMobile()) bindColumnDrop(col, column.key);
      board.appendChild(col);
    });
    container.replaceChildren(board);
  }

  function buildKanbanCard(task) {
    const card = document.createElement("article");
    card.className = "tobell-kan-card";
    card.dataset.taskId = task.id;
    const overdue = isOverdue(task) ? "danger" : (task.priority === "urgent" ? "danger" : (task.priority === "high" ? "warning" : ""));
    const due = task.due_at ? formatDueShort(task.due_at, task.due_all_day) : "";
    let moveControl = "";
    // タッチ端末では HTML5 のドラッグ＆ドロップが効かない。PWA だけでなく、
    // 通常のモバイルブラウザでも select で状態を移動できるようにする。
    const useSelectMove = state.isPwa || isMobile();
    if (useSelectMove) {
      // スマホはドラッグの代わりにネイティブ select で状態を移動する。
      const options = KANBAN_COLUMNS.map((column) =>
        `<option value="${column.key}" ${column.key === task.status ? "selected" : ""}>${esc(column.label)}へ</option>`
      ).join("");
      moveControl = `<select class="tobell-kan-move" data-move-id="${task.id}" aria-label="状態を移動">${options}</select>`;
    } else {
      card.setAttribute("draggable", "true");
    }
    if (task.pinned) card.classList.add("is-pinned");
    card.innerHTML = `
      <div class="tobell-kan-card-body" data-open-id="${task.id}">
        <strong>${task.pinned ? '<span class="tobell-task-pin" aria-label="ピン留め">📌</span> ' : ""}${esc(task.title)}</strong>
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
    if (!useSelectMove) {
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

    // 日付付きプロジェクトもカレンダーに「タスクのように」表示する。
    const projByDay = {};
    if (!state.projectFilter) {
      state.projects.forEach((project) => {
        if (!project.due_at || project.status === "archived") return;
        const d = new Date(project.due_at);
        if (d.getFullYear() === year && d.getMonth() === month) {
          const key = d.getDate();
          (projByDay[key] = projByDay[key] || []).push(project);
        }
      });
    }

    const todayStr = new Date().toDateString();
    const cells = [];
    for (let i = 0; i < startWeekday; i += 1) cells.push('<div class="tobell-cal-cell is-empty"></div>');
    for (let day = 1; day <= daysInMonth; day += 1) {
      const cellDate = new Date(year, month, day);
      const isToday = cellDate.toDateString() === todayStr ? "is-today" : "";
      const tasks = byDay[day] || [];
      const projects = projByDay[day] || [];
      const limit = state.isPwa ? 2 : 4;
      const projChips = projects.map((project) =>
        `<button type="button" class="tobell-cal-task tobell-cal-project ${projectIsOverdue(project) ? "danger" : ""}" data-project-open="${project.id}" title="プロジェクト: ${esc(project.name)}">📁 ${esc(project.name)}</button>`
      ).join("");
      const chips = tasks.slice(0, limit).map((task) =>
        `<button type="button" class="tobell-cal-task ${isOverdue(task) ? "danger" : ""}" data-open-id="${task.id}">${esc(task.title)}</button>`
      ).join("");
      const more = tasks.length > limit ? `<span class="tobell-cal-more">+${tasks.length - limit}</span>` : "";
      const iso = `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
      cells.push(`
        <div class="tobell-cal-cell ${isToday}" data-cell-date="${iso}" title="右クリック（長押し）で追加・操作">
          <span class="tobell-cal-daynum">${day}</span>
          <div class="tobell-cal-tasks">${projChips}${chips}${more}</div>
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
        <span class="tobell-cal-hint tobell-pc-only">セルを右クリックで追加・操作</span>
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
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        openTaskById(Number(button.dataset.openId));
      });
      button.addEventListener("contextmenu", (event) => {
        event.preventDefault();
        event.stopPropagation();
        openContextMenu(event, taskMenuItems(Number(button.dataset.openId)));
      });
    });
    container.querySelectorAll("[data-project-open]").forEach((button) => {
      button.addEventListener("click", (event) => {
        event.stopPropagation();
        selectProject(Number(button.dataset.projectOpen));
      });
      button.addEventListener("contextmenu", (event) => {
        event.preventDefault();
        event.stopPropagation();
        openContextMenu(event, projectMenuItems(Number(button.dataset.projectOpen)));
      });
    });
    // 日付セル全体を対象に、右クリック（長押し）で操作メニューを開く。
    container.querySelectorAll("[data-cell-date]").forEach((cell) => {
      const iso = cell.dataset.cellDate;
      cell.addEventListener("contextmenu", (event) => {
        event.preventDefault();
        openContextMenu(event, cellMenuItems(iso));
      });
      // タッチ端末は右クリックが無いため、セルのタップでもメニューを開く。
      if (isMobile()) {
        cell.addEventListener("click", (event) => {
          if (event.target.closest("[data-open-id], [data-project-open]")) return;
          // 直後の document クリックで即閉じしないよう伝播を止める。
          event.stopPropagation();
          openContextMenu(event, cellMenuItems(iso));
        });
      }
    });
  }

  // ===== カレンダーの操作メニュー（右クリック / 長押し） =====

  function cellMenuItems(iso) {
    return [
      { label: "📝 タスクを追加", action: () => openNewTaskModal({ due_date: iso }) },
      { label: "📁 プロジェクトを追加", action: () => openProjectModal(null, { due_date: iso }) },
    ];
  }

  function taskMenuItems(taskId) {
    const task = state.tasks.find((item) => item.id === taskId);
    const done = task && task.status === "done";
    return [
      { label: "開く", action: () => openTaskById(taskId) },
      {
        label: done ? "↩ 未完了に戻す" : "✓ 完了にする",
        action: () => toggleTaskComplete(taskId, !done),
      },
      { label: "🗑 タスクを削除", danger: true, action: () => deleteTaskById(taskId) },
    ];
  }

  function projectMenuItems(projectId) {
    return [
      { label: "開く", action: () => selectProject(projectId) },
      { label: "⚙ 編集", action: () => openProjectModal(projectId) },
      { label: "🗑 プロジェクトを削除", danger: true, action: () => deleteProjectById(projectId) },
    ];
  }

  async function toggleTaskComplete(id, completing) {
    try {
      await api(`/tools/to_bell/api/tasks/${id}/${completing ? "complete" : "reopen"}`, { method: "POST" });
      await Promise.all([loadTasks(), loadNotifications()]);
      showFlash(completing ? "完了にしました" : "未完了に戻しました", "success");
    } catch (_) {
      /* api() がトーストを表示済み */
    }
  }

  async function deleteTaskById(id) {
    if (!window.confirm("このタスクを完全に削除します。元に戻せません。よろしいですか？")) return;
    try {
      await api(`/tools/to_bell/api/tasks/${id}?hard=1`, { method: "DELETE" });
      if (state.selectedTaskId === id) closeDetail();
      await Promise.all([loadTasks(), loadNotifications()]);
      showFlash("タスクを削除しました", "info");
    } catch (_) {
      /* api() がトーストを表示済み */
    }
  }

  async function deleteProjectById(id) {
    if (!window.confirm("このプロジェクトを削除しますか？タスクは残り、プロジェクトの紐付けだけ外れます。")) return;
    try {
      await api(`/tools/to_bell/api/projects/${id}`, { method: "DELETE" });
      if (state.projectFilter === id) state.projectFilter = 0;
      await Promise.all([loadProjects(), loadTasks()]);
      showFlash("プロジェクトを削除しました", "info");
    } catch (_) {
      /* api() がトーストを表示済み */
    }
  }

  // ===== コンテキストメニュー基盤 =====

  function initContextMenu() {
    const menu = $("tb-context-menu");
    if (!menu) return;
    document.addEventListener("click", (event) => {
      if (!menu.hasAttribute("hidden") && !menu.contains(event.target)) closeContextMenu();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !menu.hasAttribute("hidden")) closeContextMenu();
    });
    window.addEventListener("scroll", closeContextMenu, true);
  }

  function closeContextMenu() {
    const menu = $("tb-context-menu");
    if (menu) menu.setAttribute("hidden", "");
  }

  function openContextMenu(event, items) {
    const menu = $("tb-context-menu");
    if (!menu || !items || !items.length) return;
    menu.innerHTML = "";
    items.forEach((item) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "tobell-context-item" + (item.danger ? " tobell-danger" : "");
      button.textContent = item.label;
      button.addEventListener("click", (ev) => {
        ev.stopPropagation();
        closeContextMenu();
        try { item.action(); } catch (_) { /* 個々の操作失敗は握りつぶす */ }
      });
      menu.appendChild(button);
    });
    // いったん表示してサイズを測り、画面内に収まるよう配置する。
    menu.removeAttribute("hidden");
    const px = (event.touches && event.touches[0] ? event.touches[0].clientX : event.clientX) || 0;
    const py = (event.touches && event.touches[0] ? event.touches[0].clientY : event.clientY) || 0;
    const rect = menu.getBoundingClientRect();
    const x = Math.min(px, window.innerWidth - rect.width - 8);
    const y = Math.min(py, window.innerHeight - rect.height - 8);
    menu.style.left = `${Math.max(8, x)}px`;
    menu.style.top = `${Math.max(8, y)}px`;
  }

  function openTaskById(id) {
    const task = state.tasks.find((item) => item.id === id);
    if (task) renderDetail(task);
    else openTaskFromLink(id);
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
        <div class="tobell-project-row ${project.id === state.projectFilter ? "is-active" : ""} ${project.pinned ? "is-pinned" : ""}" data-project-row="${project.id}" draggable="true">
          <span class="tobell-project-drag" aria-hidden="true">⋮⋮</span>
          <button type="button" class="tobell-project-pick" data-project-pick="${project.id}">
            <span class="tobell-dot" style="background:${esc(project.color)}"></span>
            <span class="tobell-project-name">${project.pinned ? "📌 " : ""}${esc(project.name)}</span>
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
      bindProjectReorder(list);
    }
    renderProjectBar();
    syncQuickAddProjectHint();
    const modal = $("tb-projects-modal");
    if (modal && !modal.hasAttribute("hidden")) renderProjectsListModal();
  }

  let dragOrder = { id: 0, list: null };

  function bindProjectReorder(list) {
    list.querySelectorAll("[data-project-row]").forEach((row) => {
      row.addEventListener("dragstart", (event) => {
        dragOrder.id = Number(row.dataset.projectRow);
        dragOrder.list = list;
        row.classList.add("is-dragging");
        if (event.dataTransfer) event.dataTransfer.effectAllowed = "move";
      });
      row.addEventListener("dragend", () => {
        row.classList.remove("is-dragging");
        list.querySelectorAll(".is-drop-target").forEach((el) => el.classList.remove("is-drop-target"));
      });
      row.addEventListener("dragover", (event) => {
        event.preventDefault();
        if (!dragOrder.id || dragOrder.id === Number(row.dataset.projectRow)) return;
        row.classList.add("is-drop-target");
      });
      row.addEventListener("dragleave", () => row.classList.remove("is-drop-target"));
      row.addEventListener("drop", async (event) => {
        event.preventDefault();
        row.classList.remove("is-drop-target");
        const targetId = Number(row.dataset.projectRow);
        const sourceId = dragOrder.id;
        dragOrder.id = 0;
        if (!sourceId || !targetId || sourceId === targetId) return;
        await reorderProjects(sourceId, targetId);
      });
    });
  }

  async function reorderProjects(sourceId, targetId) {
    const ids = state.projects.map((project) => project.id);
    const fromIndex = ids.indexOf(sourceId);
    const toIndex = ids.indexOf(targetId);
    if (fromIndex < 0 || toIndex < 0) return;
    ids.splice(fromIndex, 1);
    ids.splice(toIndex, 0, sourceId);
    try {
      await api("/tools/to_bell/api/projects/reorder", { method: "POST", body: { ordered_ids: ids } });
      await loadProjects();
    } catch (error) {
      /* api() がトーストを表示済み */
    }
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
    syncQuickAddProjectHint();
    loadTasks();
  }

  function currentProject() {
    if (!state.projectFilter) return null;
    return state.projects.find((item) => item.id === state.projectFilter) || null;
  }

  // プロジェクトを開いている間は、追加したタスクがそのプロジェクトに入ることを明示する。
  function syncQuickAddProjectHint() {
    const form = $("tb-quick-form");
    if (!form) return;
    const project = currentProject();
    const title = form.elements.title;
    if (title) {
      title.placeholder = project
        ? `「${project.name}」にタスクを追加`
        : "タスク名だけで追加できます";
    }
    form.classList.toggle("has-project", !!project);
  }

  function initProjectsListModal() {
    const modal = $("tb-projects-modal");
    if (!modal) return;
    const close = () => modal.setAttribute("hidden", "");
    modal.querySelectorAll("[data-projects-close]").forEach((el) => el.addEventListener("click", close));
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !modal.hasAttribute("hidden")) close();
    });
    const createBtn = $("tb-projects-modal-create");
    if (createBtn) createBtn.addEventListener("click", () => {
      modal.setAttribute("hidden", "");
      openProjectModal();
    });
  }

  async function openProjectsListModal() {
    const modal = $("tb-projects-modal");
    if (!modal) return;
    modal.removeAttribute("hidden");
    await loadProjects();
    renderProjectsListModal();
  }

  function renderProjectsListModal() {
    const list = $("tb-projects-modal-list");
    if (!list) return;
    if (!state.projects.length) {
      list.innerHTML = '<div class="tobell-empty">プロジェクトはありません。</div>';
      return;
    }
    list.innerHTML = state.projects.map((project) => `
      <div class="tobell-project-row ${project.id === state.projectFilter ? "is-active" : ""} ${project.pinned ? "is-pinned" : ""}" data-project-row="${project.id}">
        <button type="button" class="tobell-project-pick" data-project-pick="${project.id}">
          <span class="tobell-dot" style="background:${esc(project.color)}"></span>
          <span class="tobell-project-name">${project.pinned ? "📌 " : ""}${esc(project.name)}</span>
          <span class="tobell-project-count">${Number(project.open_count || 0)}</span>
        </button>
        <button type="button" class="tobell-project-edit" data-project-edit="${project.id}" aria-label="編集">⚙</button>
      </div>`).join("");
    list.querySelectorAll("[data-project-pick]").forEach((button) => {
      button.addEventListener("click", () => {
        selectProject(Number(button.dataset.projectPick));
        $("tb-projects-modal").setAttribute("hidden", "");
      });
    });
    list.querySelectorAll("[data-project-edit]").forEach((button) => {
      button.addEventListener("click", () => {
        $("tb-projects-modal").setAttribute("hidden", "");
        openProjectModal(Number(button.dataset.projectEdit));
      });
    });
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
    const notifyButton = $("tb-project-notify-open");
    if (notifyButton) notifyButton.addEventListener("click", openProjectNotifyModal);
    const projectLinksButton = $("tb-project-links");
    if (projectLinksButton) projectLinksButton.addEventListener("click", () => {
      const id = Number($("tb-project-form").elements.id.value || 0);
      if (!id) return;
      const project = state.projects.find((p) => p.id === id);
      openLinksModal("project", id, project ? project.name : `プロジェクト #${id}`);
    });
    const scopeSelect = $("tb-project-form").elements.visibility_scope;
    if (scopeSelect) scopeSelect.addEventListener("change", () => syncMembersVisibility(scopeSelect.value));
    const memberPick = $("tb-project-members-pick");
    if (memberPick) memberPick.addEventListener("change", () => {
      const value = memberPick.value;
      if (value) addProjectMember(value);
      memberPick.value = "";
    });
    initBulkAssignModal();
    initProjectNotifyModal();
  }

  const projectEdit = { members: new Set() };

  function syncMembersVisibility(scope) {
    const row = $("tb-project-members-row");
    if (row) row.hidden = scope !== "members";
  }

  function renderProjectMembersChips() {
    const wrap = $("tb-project-members-chips");
    if (!wrap) return;
    const names = Array.from(projectEdit.members);
    if (!names.length) {
      wrap.innerHTML = '<span class="tobell-empty tobell-empty-inline">メンバー未指定</span>';
    } else {
      wrap.innerHTML = names.map((username) => {
        const label = lookupUserLabel(username);
        return `<span class="tobell-member-chip"><span>${esc(label)}</span><button type="button" data-member-remove="${esc(username)}" aria-label="削除">×</button></span>`;
      }).join("");
      wrap.querySelectorAll("[data-member-remove]").forEach((btn) => {
        btn.addEventListener("click", () => {
          projectEdit.members.delete(btn.dataset.memberRemove);
          renderProjectMembersChips();
        });
      });
    }
  }

  function addProjectMember(username) {
    if (!username) return;
    projectEdit.members.add(username);
    renderProjectMembersChips();
  }

  function lookupUserLabel(username) {
    const select = $("tb-project-members-pick");
    if (!select) return username;
    const opt = select.querySelector(`option[value="${cssEscape(username)}"]`);
    return opt ? (opt.textContent || username) : username;
  }

  function cssEscape(value) {
    return String(value).replace(/[^a-zA-Z0-9_\-]/g, (ch) => `\\${ch}`);
  }

  function openProjectModal(projectId, prefill) {
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
      form.elements.status.value = project.status || "active";
      form.elements.calendar_only.checked = !!project.calendar_only;
      if (form.elements.due_date) form.elements.due_date.value = project.due_at ? project.due_at.slice(0, 10) : "";
      form.elements.pinned.checked = !!project.pinned;
      projectEdit.members = new Set(Array.isArray(project.members) ? project.members : []);
    } else {
      form.elements.color.value = "#2563eb";
      form.elements.status.value = "active";
      form.elements.calendar_only.checked = false;
      if (form.elements.due_date) form.elements.due_date.value = (prefill && prefill.due_date) || "";
      form.elements.pinned.checked = false;
      projectEdit.members = new Set();
    }
    syncMembersVisibility(form.elements.visibility_scope.value);
    renderProjectMembersChips();
    $("tb-project-delete").hidden = !project;
    const bulkButton = $("tb-project-bulk-assign");
    if (bulkButton) bulkButton.hidden = !project;
    const notifyButton = $("tb-project-notify-open");
    if (notifyButton) notifyButton.hidden = !project;
    const projectLinksButton = $("tb-project-links");
    if (projectLinksButton) projectLinksButton.hidden = !project;
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
      status: form.elements.status.value,
      calendar_only: form.elements.calendar_only.checked,
      due_date: form.elements.due_date ? form.elements.due_date.value : "",
      pinned: form.elements.pinned.checked,
      members: Array.from(projectEdit.members),
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

  function initProjectNotifyModal() {
    const modal = $("tb-project-notify-modal");
    if (!modal) return;
    const close = () => modal.setAttribute("hidden", "");
    modal.querySelectorAll("[data-notify-close]").forEach((el) => el.addEventListener("click", close));
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !modal.hasAttribute("hidden")) close();
    });
    const form = $("tb-project-notify-form");
    if (form) form.addEventListener("submit", submitProjectNotify);
  }

  function openProjectNotifyModal() {
    const id = Number($("tb-project-form").elements.id.value || 0);
    if (!id) return;
    const modal = $("tb-project-notify-modal");
    if (!modal) return;
    const form = $("tb-project-notify-form");
    if (form) {
      form.reset();
      form.elements.severity.value = "info";
      form.dataset.projectId = String(id);
    }
    modal.removeAttribute("hidden");
    if (form) form.elements.title.focus();
  }

  async function submitProjectNotify(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const id = Number(form.dataset.projectId || 0);
    if (!id) return;
    const body = {
      title: form.elements.title.value,
      body: form.elements.body.value,
      severity: form.elements.severity.value,
    };
    const submit = form.querySelector('[type="submit"]');
    if (submit) submit.disabled = true;
    try {
      const result = await api(`/tools/to_bell/api/projects/${id}/notify`, { method: "POST", body });
      $("tb-project-notify-modal").setAttribute("hidden", "");
      showFlash(`${Number(result.sent || 0)}人に通知を送信しました`, "success");
    } finally {
      if (submit) submit.disabled = false;
    }
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
    if (search) search.addEventListener("input", debounce(loadAssignableTasks, SEARCH_DEBOUNCE_MS));
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
      const due = taskDueText(task);
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
    // 公開範囲の変更・削除は作成者のみ。他人の所属共有テンプレートには出さない
    // （出しても 400 になるだけで、押した人には理由が分からない）。
    list.innerHTML = rows.map((tpl) => {
      const owned = tpl.owner_id === state.currentUser;
      return `
      <div class="tobell-template-row" data-template-id="${tpl.id}">
        <div class="tobell-template-main">
          <strong>${esc(tpl.name)}</strong>
          <span class="tobell-template-meta">${tpl.scope === "office" ? "所属共有" : "自分のみ"} / サブタスク${Number(tpl.subtask_count || 0)}件${owned ? "" : ` / ${esc(tpl.owner_id || "")}さん作成`}</span>
        </div>
        ${owned ? `<button type="button" class="tobell-btn" data-template-scope="${tpl.id}" data-scope="${esc(tpl.scope)}" title="公開範囲を切り替え">${tpl.scope === "office" ? "所属共有→自分のみ" : "自分のみ→所属共有"}</button>` : ""}
        <button type="button" class="tobell-btn tobell-btn-primary" data-template-use="${tpl.id}">使う</button>
        ${owned ? `<button type="button" class="tobell-btn tobell-danger" data-template-del="${tpl.id}" aria-label="削除">×</button>` : ""}
      </div>`;
    }).join("");
    list.querySelectorAll("[data-template-scope]").forEach((button) => {
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          const next = button.dataset.scope === "office" ? "private" : "office";
          await api(`/tools/to_bell/api/templates/${button.dataset.templateScope}`, { method: "PUT", body: { scope: next } });
          await loadTemplates();
          showFlash(next === "office" ? "所属内で共有しました" : "自分のみに変更しました", "success");
        } finally {
          button.disabled = false;
        }
      });
    });
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
      const scope = form.elements.scope ? form.elements.scope.value : "private";
      await api("/tools/to_bell/api/templates", { method: "POST", body: { name, scope } });
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
    if (form.elements.reviewer_id) form.elements.reviewer_id.value = task.reviewer_id || "";
    setupAllDayToggle(form, task);
    fillProjectSelect(form.elements.project_id, task.project ? task.project.id : "");
    form.elements.tags.value = (task.tags || []).map((tag) => tag.name).join(", ");
    if (form.elements.pinned) form.elements.pinned.checked = !!task.pinned;
    setupTaskCalendar(task, form);
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
    $("tb-detail").querySelectorAll("[data-action]").forEach((button) => button.addEventListener("click", detailAction));
    renderMain();
  }

  // 終日タスクは 23:59:59 として保存される。datetime-local は秒を持てないため、
  // 開いてそのまま保存すると 23:59:00 になり「時刻を指定した＝通知する」タスクに
  // 化けてしまう。終日チェックを一緒に送ることで、無変更保存でも終日を維持する。
  function setupAllDayToggle(form, task) {
    const toggle = form.querySelector("[data-due-all-day]");
    const dueInput = form.elements.due_at;
    if (!toggle || !dueInput) return;
    toggle.checked = !!task.due_all_day;
    const syncDisabled = () => {
      // 終日のときは時刻部分に意味がないことを見た目でも示す。
      dueInput.classList.toggle("is-all-day", toggle.checked);
    };
    syncDisabled();
    toggle.addEventListener("change", () => {
      if (toggle.checked && dueInput.value) {
        dueInput.value = `${dueInput.value.slice(0, 10)}T23:59`;
      }
      syncDisabled();
    });
    // 利用者が時刻を触ったら、終日ではなくなったとみなす。
    dueInput.addEventListener("input", () => {
      if (!toggle.checked) return;
      if (dueInput.value.slice(11, 16) !== "23:59") {
        toggle.checked = false;
        syncDisabled();
      }
    });
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
    } else {
      renderAttachmentRows(container, rows);
    }
    // 25MB超で FILEPOST に退避したファイルも、同じ「添付」欄に並べる。
    // 以前は「FILEPOSTへ保存しました」と出るのに詳細のどこにも現れず、
    // 「紐付け」モーダルを開かないと存在に気づけなかった。
    loadTaskExternalFiles(task.id, container).catch(() => {});
  }

  async function loadTaskExternalFiles(taskId, container) {
    if (!taskId) return;
    let files = [];
    try {
      const data = await api(`/tools/to_bell/api/integrations/filepost/files?target_type=task&target_id=${taskId}`);
      files = data.files || [];
    } catch (_) {
      return; // 連携OFF・権限なし・共有セッションでは 403/400。何も足さない。
    }
    if (!files.length || state.selectedTaskId !== taskId) return;
    const wrap = document.createElement("div");
    wrap.className = "tobell-attachment-external";
    wrap.innerHTML = files.map((row) => `
      <div class="tobell-attachment">
        <a href="${esc(row.private_url)}" target="_blank" rel="noopener">📎 ${esc(row.file_name)}</a>
        <span class="tobell-attachment-size">${formatFileSize(row.file_size)} ・ FILEPOST</span>
      </div>`).join("");
    container.appendChild(wrap);
  }

  function renderAttachmentRows(container, rows) {
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
    const file = input.files[0];
    const btn = form.querySelector('[type="submit"]');
    if (btn) btn.disabled = true;
    try {
      let settings = filepostThresholdCache;
      if (settings === null) {
        try {
          settings = await api("/tools/to_bell/api/integrations/filepost/threshold");
          filepostThresholdCache = settings;
        } catch (_) {
          settings = null;
        }
      }
      const threshold = (settings && settings.threshold_bytes) || DEFAULT_ATTACHMENT_MAX_BYTES;
      const useFilepost = settings && settings.overflow_enabled && file.size > threshold;
      const data = new FormData();
      data.append("file", file);
      if (useFilepost) {
        data.append("target_type", "task");
        data.append("target_id", String(state.selectedTaskId));
        await api("/tools/to_bell/api/integrations/filepost/upload", { method: "POST", body: data });
        form.reset();
        await refreshSelectedTask();
        showFlash(`大容量のためFILEPOSTへ保存しました（${formatFileSize(file.size)}）`, "success");
      } else {
        await api(`/tools/to_bell/api/tasks/${state.selectedTaskId}/attachments`, { method: "POST", body: data });
        form.reset();
        await refreshSelectedTask();
        showFlash("添付しました", "success");
      }
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
    const container = $("tb-subtasks");
    const rows = task.subtasks || [];
    container.innerHTML = rows.length ? rows.map((item) => `
      <div class="tobell-subtask">
        <input type="checkbox" ${item.is_done ? "checked" : ""} data-subtask-id="${item.id}">
        <span>${esc(item.title)}</span>
        <button type="button" class="tobell-subtask-delete" data-subtask-delete="${item.id}" aria-label="削除">×</button>
      </div>`).join("") : '<div class="tobell-empty">サブタスクはありません。</div>';
    container.querySelectorAll("[data-subtask-id]").forEach((box) => {
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
    container.querySelectorAll("[data-subtask-delete]").forEach((btn) => {
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
        <div>${linkify(item.body)}</div>
      </div>`).join("") : '<div class="tobell-empty">コメントはありません。</div>';
  }

  async function createQuickTask(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const btn = form.querySelector('[type="submit"]');
    if (btn) btn.disabled = true;
    try {
      const payload = Object.fromEntries(new FormData(form).entries());
      // プロジェクトを開いている状態で追加したら、そのプロジェクトのタスクにする。
      if (state.projectFilter) payload.project_id = state.projectFilter;
      await api("/tools/to_bell/api/tasks", { method: "POST", body: payload });
      form.reset();
      syncQuickAddProjectHint();
      await loadTasks();
      const project = currentProject();
      showFlash(project ? `「${project.name}」にタスクを追加しました` : "タスクを追加しました", "success");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  // ===== 新規タスク モーダル（詳細を設定して追加） =====

  function initNewTaskModal() {
    const modal = $("tb-newtask-modal");
    if (!modal) return;
    const close = () => modal.setAttribute("hidden", "");
    modal.querySelectorAll("[data-newtask-close]").forEach((el) => el.addEventListener("click", close));
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !modal.hasAttribute("hidden")) close();
    });
    const form = $("tb-newtask-form");
    if (form) form.addEventListener("submit", saveNewTask);
  }

  function openNewTaskModal(prefill) {
    const modal = $("tb-newtask-modal");
    const form = $("tb-newtask-form");
    if (!modal || !form) {
      // モーダルが無い構成では従来どおりクイック追加にフォーカスする。
      const title = $("tb-title");
      if (title) title.focus();
      return;
    }
    form.reset();
    const data = prefill || {};
    if (form.elements.title) form.elements.title.value = data.title || "";
    if (form.elements.due_date) form.elements.due_date.value = data.due_date || "";
    if (form.elements.due_time) form.elements.due_time.value = data.due_time || "";
    // プロジェクトを開いている状態で追加したら、そのプロジェクトのタスクにする。
    fillProjectSelect(form.elements.project_id, state.projectFilter || (data.project_id || ""));
    const hint = form.querySelector("[data-newtask-project-hint]");
    if (hint) {
      const project = currentProject();
      hint.hidden = !project;
      if (project) hint.textContent = `「${project.name}」のタスクとして追加します（プロジェクト欄で変更できます）。`;
    }
    modal.removeAttribute("hidden");
    if (form.elements.title) form.elements.title.focus();
  }

  async function saveNewTask(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = Object.fromEntries(new FormData(form).entries());
    payload.pinned = form.elements.pinned ? form.elements.pinned.checked : false;
    if (!String(payload.title || "").trim()) {
      showFlash("タスク名を入力してください", "error");
      return;
    }
    const btn = form.querySelector('[type="submit"]');
    if (btn) btn.disabled = true;
    try {
      await api("/tools/to_bell/api/tasks", { method: "POST", body: payload });
      $("tb-newtask-modal").setAttribute("hidden", "");
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
      payload.pinned = form.elements.pinned ? form.elements.pinned.checked : false;
      const allDay = form.querySelector("[data-due-all-day]");
      payload.due_all_day = allDay ? allDay.checked : false;
      if (form.elements.reviewer_id) payload.reviewer_id = form.elements.reviewer_id.value || "";
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
    if (action === "links") {
      const task = state.tasks.find((t) => t.id === id);
      openLinksModal("task", id, task ? task.title : `タスク #${id}`);
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
        reopen: state.filter === "archived" ? "アーカイブから戻しました" : "未完了に戻しました",
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
    if (!rows.length) {
      container.innerHTML = '<div class="tobell-empty">通知はありません。</div>';
      return;
    }
    container.innerHTML = rows.slice(0, NOTIFICATION_DISPLAY_LIMIT).map((item) => `
      <div class="tobell-notification ${item.is_read ? "is-read" : "is-unread"} ${item.is_resolved ? "is-resolved" : ""}" data-notification-id="${item.id}">
        <div class="tobell-notification-main" data-notification-open="${item.id}" role="button" tabindex="0">
          <strong>${esc(item.title)}</strong>
          <div>${linkify(item.body)}</div>
        </div>
        ${item.is_resolved ? "" : `<button type="button" class="tobell-notification-resolve" data-notification-resolve="${item.id}" aria-label="対応済みにする" title="対応済みにする">✓</button>`}
      </div>`).join("");
    // 通知をクリックしたら対象を開く（href は /tools/to_bell?task=… / ?project_id=…）。
    container.querySelectorAll("[data-notification-open]").forEach((el) => {
      const open = () => openNotification(rows.find((row) => row.id === Number(el.dataset.notificationOpen)));
      el.addEventListener("click", open);
      el.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") { event.preventDefault(); open(); }
      });
    });
    container.querySelectorAll("[data-notification-resolve]").forEach((btn) => {
      btn.addEventListener("click", async (event) => {
        event.stopPropagation();
        btn.disabled = true;
        try {
          await api(`/tools/to_bell/api/notifications/${btn.dataset.notificationResolve}/resolve`, { method: "POST" });
          await Promise.all([loadNotifications(), loadTasks()]);
        } finally {
          btn.disabled = false;
        }
      });
    });
  }

  async function openNotification(item) {
    if (!item) return;
    if (!item.is_read) {
      try {
        await api(`/tools/to_bell/api/notifications/${item.id}/read`, { method: "POST" });
      } catch (_) { /* 既読化に失敗しても遷移は続ける */ }
    }
    const href = String(item.href || "");
    const taskMatch = href.match(/[?&]task=(\d+)/);
    const projectMatch = href.match(/[?&]project_id=(\d+)/);
    if (taskMatch) {
      await openTaskFromLink(Number(taskMatch[1]));
    } else if (projectMatch) {
      selectProject(Number(projectMatch[1]));
    } else if (href && !href.startsWith("/tools/to_bell")) {
      window.open(href, "_blank", "noopener");
    }
    await Promise.all([loadNotifications(), loadTasks()]);
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

  async function hardReload(button) {
    if (button) button.disabled = true;
    try {
      // 1) ブラウザの Cache Storage を全部消す。
      if ("caches" in window) {
        try {
          const names = await caches.keys();
          await Promise.all(names.map((name) => caches.delete(name)));
        } catch (e) { /* 失敗してもリロードは試みる */ }
      }
      // 2) Service Worker にもキャッシュ消去を依頼（古い SW が動いている場合の保険）。
      if ("serviceWorker" in navigator) {
        try {
          const regs = await navigator.serviceWorker.getRegistrations();
          await Promise.all(regs.map((reg) => {
            if (reg.active) {
              try { reg.active.postMessage({ type: "tobell-clear-caches" }); } catch (e) { /* noop */ }
            }
            return reg.update().catch(() => {});
          }));
        } catch (e) { /* noop */ }
      }
    } finally {
      // 3) URL にキャッシュバスター付きで再読込。ブラウザの HTTP キャッシュも極力回避。
      const sep = window.location.search ? "&" : "?";
      const next = `${window.location.pathname}${window.location.search}${sep}_=${Date.now()}${window.location.hash}`;
      window.location.replace(next);
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
    state.foregroundTimer = window.setInterval(foregroundTick, FOREGROUND_TICK_MS);
  }

  function stopForegroundWatch() {
    if (state.foregroundTimer) {
      window.clearInterval(state.foregroundTimer);
      state.foregroundTimer = 0;
    }
  }

  // 通知済みフラグ（toBellNotified:*）は放置すると localStorage に溜まり続けるため、
  // 一定期間より古いエントリを掃除する。
  const NOTIFIED_KEY_TTL_MS = 65 * 24 * 60 * 60 * 1000;

  function pruneNotifiedKeys() {
    // サーバー側の期限監視窓（60日）より短くすると、同じタスクに対して
    // 前面通知が繰り返し出てしまう。窓より長い期間だけ保持する。
    const cutoff = Date.now() - NOTIFIED_KEY_TTL_MS;
    const stale = [];
    for (let i = 0; i < localStorage.length; i += 1) {
      const key = localStorage.key(i);
      if (!key || !key.startsWith("toBellNotified:")) continue;
      const stored = Date.parse(localStorage.getItem(key) || "");
      if (Number.isNaN(stored) || stored < cutoff) stale.push(key);
    }
    stale.forEach((key) => localStorage.removeItem(key));
  }

  async function foregroundTick() {
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    pruneNotifiedKeys();
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
    list.innerHTML = rows.map((item) => `
      <div class="tobell-device ${item.is_active ? "" : "is-disabled"}" data-device-id="${item.id}">
        <div class="tobell-device-main">
          <input class="tobell-device-label" value="${esc(item.device_label)}" aria-label="通知先名">
          <div class="tobell-device-meta">${item.is_active ? "有効" : "無効"} / ${esc(item.endpoint_tail || "")}</div>
        </div>
        <button type="button" class="tobell-btn" data-device-save="${item.id}">保存</button>
        <button type="button" class="tobell-btn tobell-danger" data-device-disable="${item.id}" ${item.is_active ? "" : "disabled"}>無効化</button>
        <button type="button" class="tobell-btn tobell-danger" data-device-delete="${item.id}">削除</button>
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

  // ===== 設定モーダル =====

  const settingsState = { catalog: [], integrations: {} };

  function initSettingsModal() {
    const button = $("tb-settings-open");
    if (!button) return;
    const modal = $("tb-settings-modal");
    if (!modal) return;
    button.addEventListener("click", openSettingsModal);
    modal.querySelectorAll("[data-settings-close]").forEach((el) => {
      el.addEventListener("click", () => modal.setAttribute("hidden", ""));
    });
    modal.querySelectorAll("[data-settings-tab]").forEach((tab) => {
      tab.addEventListener("click", () => {
        modal.querySelectorAll("[data-settings-tab]").forEach((t) => {
          const active = t === tab;
          t.classList.toggle("is-active", active);
          t.setAttribute("aria-selected", String(active));
        });
        modal.querySelectorAll("[data-settings-panel]").forEach((p) => {
          p.style.display = p.dataset.settingsPanel === tab.dataset.settingsTab ? "" : "none";
        });
        if (tab.dataset.settingsTab === "google") loadGoogleStatus(true);
      });
    });
  }

  async function openSettingsModal() {
    const modal = $("tb-settings-modal");
    if (!modal) return;
    modal.removeAttribute("hidden");
    await loadSettings();
  }

  async function loadSettings() {
    const container = $("tb-settings-integrations-list");
    if (!container) return;
    container.innerHTML = '<div class="tobell-empty">読み込み中…</div>';
    try {
      const data = await api("/tools/to_bell/api/settings");
      settingsState.catalog = data.catalog || [];
      settingsState.integrations = data.integrations || {};
      renderIntegrationList();
    } catch (_) {
      container.innerHTML = '<div class="tobell-empty">読み込みに失敗しました</div>';
    }
  }

  function renderIntegrationList() {
    const container = $("tb-settings-integrations-list");
    if (!container) return;
    if (!settingsState.catalog.length) {
      container.innerHTML = '<div class="tobell-empty">連携機能はまだありません</div>';
      return;
    }
    container.innerHTML = settingsState.catalog.map((item) => {
      const on = !!settingsState.integrations[item.key];
      // 連携先ツール（社員名簿PLUS / 現場リストPLUS / ShifterSync）のアクセス権が
      // 無い場合は ON にできない。理由が分かるように明示する。
      const available = item.available !== false;
      return `
        <div class="tobell-integration-row ${available ? "" : "is-locked"}">
          <div class="tobell-integration-meta">
            <span class="tobell-integration-label">${esc(item.label)}</span>
            <span class="tobell-integration-tool">${esc(item.tool)}</span>
            <div class="tobell-integration-desc">${esc(item.description)}</div>
            ${available ? "" : `<div class="tobell-integration-locked">「${esc(item.requires_tool || item.tool)}」を利用する権限がないため使えません。管理者に付与を依頼してください。</div>`}
          </div>
          <button type="button" class="tobell-switch ${on ? "is-on" : ""}" data-integration-key="${esc(item.key)}" aria-pressed="${on}" ${available ? "" : "disabled"}></button>
        </div>`;
    }).join("");
    container.querySelectorAll("[data-integration-key]").forEach((btn) => {
      btn.addEventListener("click", () => toggleIntegration(btn));
    });
  }

  async function toggleIntegration(btn) {
    const key = btn.dataset.integrationKey;
    const desired = !btn.classList.contains("is-on");
    btn.disabled = true;
    try {
      const next = { ...settingsState.integrations, [key]: desired };
      const data = await api("/tools/to_bell/api/settings/integrations", {
        method: "PUT",
        body: { integrations: next },
      });
      settingsState.integrations = data.integrations || {};
      btn.classList.toggle("is-on", !!settingsState.integrations[key]);
      btn.setAttribute("aria-pressed", String(!!settingsState.integrations[key]));
      showFlash(desired ? "連携をONにしました" : "連携をOFFにしました", "success");
    } catch (_) {
      // api() が flash 表示する
    } finally {
      btn.disabled = false;
    }
  }

  // ===== タスク一括削除（設定 → 一括削除） =====

  const bulkDelete = { previewed: null };

  function initBulkDelete() {
    const nameInput = $("tb-bulk-delete-name");
    const matchSel = $("tb-bulk-delete-match");
    const previewBtn = $("tb-bulk-delete-preview");
    const runBtn = $("tb-bulk-delete-run");
    if (!nameInput || !previewBtn || !runBtn) return;
    const invalidate = () => {
      bulkDelete.previewed = null;
      runBtn.disabled = true;
      const result = $("tb-bulk-delete-result");
      if (result) result.innerHTML = "";
    };
    nameInput.addEventListener("input", invalidate);
    if (matchSel) matchSel.addEventListener("change", invalidate);
    previewBtn.addEventListener("click", previewBulkDelete);
    runBtn.addEventListener("click", runBulkDelete);
    const form = $("tb-bulk-delete-form");
    if (form) form.addEventListener("submit", (event) => { event.preventDefault(); previewBulkDelete(); });
  }

  async function previewBulkDelete() {
    const name = ($("tb-bulk-delete-name").value || "").trim();
    const match = ($("tb-bulk-delete-match") || {}).value || "exact";
    const result = $("tb-bulk-delete-result");
    const runBtn = $("tb-bulk-delete-run");
    if (!name) {
      showFlash("タスク名を入力してください", "error");
      return;
    }
    const btn = $("tb-bulk-delete-preview");
    if (btn) btn.disabled = true;
    try {
      const params = new URLSearchParams({ name, match });
      const data = await api(`/tools/to_bell/api/tasks/bulk-delete?${params.toString()}`);
      const count = Number(data.count || 0);
      bulkDelete.previewed = { name, match, count };
      if (runBtn) runBtn.disabled = count === 0;
      if (result) {
        if (!count) {
          result.innerHTML = '<div class="tobell-empty">一致するタスクはありません。</div>';
        } else {
          const samples = (data.samples || []).map((title) => `<li>${esc(title)}</li>`).join("");
          result.innerHTML = `
            <p class="tobell-bulk-delete-count"><strong>${count}件</strong>のタスクが一致しました。「削除する」を押すと完全に削除します。</p>
            <ul class="tobell-bulk-delete-samples">${samples}</ul>
            ${count > (data.samples || []).length ? `<p class="tobell-share-note">ほか ${count - (data.samples || []).length} 件…</p>` : ""}`;
        }
      }
    } catch (_) {
      /* api() がトーストを表示済み */
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function runBulkDelete() {
    const previewed = bulkDelete.previewed;
    if (!previewed || !previewed.count) return;
    if (!window.confirm(`「${previewed.name}」に一致する ${previewed.count} 件のタスクを完全に削除します。元に戻せません。よろしいですか？`)) return;
    const runBtn = $("tb-bulk-delete-run");
    if (runBtn) runBtn.disabled = true;
    try {
      const data = await api("/tools/to_bell/api/tasks/bulk-delete", {
        method: "POST",
        body: { name: previewed.name, match: previewed.match },
      });
      const result = $("tb-bulk-delete-result");
      if (result) result.innerHTML = `<div class="tobell-empty">${Number(data.deleted || 0)}件のタスクを削除しました。</div>`;
      bulkDelete.previewed = null;
      const nameInput = $("tb-bulk-delete-name");
      if (nameInput) nameInput.value = "";
      await Promise.all([loadTasks(), loadProjects(), loadNotifications()]);
      showFlash(`${Number(data.deleted || 0)}件のタスクを削除しました`, "success");
    } catch (_) {
      if (runBtn) runBtn.disabled = false;
    }
  }

  // ===== Googleカレンダー連携 =====

  const googleState = { status: null };

  const REMINDER_PRESETS = [
    ["none", "通知なし"],
    ["10", "10分前"],
    ["30", "30分前"],
    ["60", "1時間前"],
    ["1440", "1日前"],
    ["10080", "1週間前"],
  ];

  function reminderOptionsHtml(selected, includeDefault) {
    const items = includeDefault ? [["default", "共通設定に従う"], ...REMINDER_PRESETS] : REMINDER_PRESETS;
    return items
      .map(([value, label]) => `<option value="${value}"${value === selected ? " selected" : ""}>${label}</option>`)
      .join("");
  }

  function reminderSelectToPayload(value) {
    if (value === "default") return "default"; // サーバー側で「個別上書きを解除」を意味する
    if (value === "none") return [];
    const minutes = Number(value);
    if (!Number.isFinite(minutes)) return "default";
    return [{ method: "popup", minutes }];
  }

  function reminderOverrideToSelect(override) {
    if (override == null) return "default";
    if (Array.isArray(override)) {
      if (override.length === 0) return "none";
      if (override.length === 1 && override[0]) {
        const value = String(override[0].minutes);
        if (REMINDER_PRESETS.some(([preset]) => preset === value)) return value;
      }
    }
    return "default";
  }

  async function loadGoogleStatus(render) {
    try {
      googleState.status = await api("/tools/to_bell/api/google/status");
    } catch (_) {
      googleState.status = null;
    }
    if (render) renderGooglePanel();
    return googleState.status;
  }

  const IMPORT_MODE_OPTIONS = [
    ["tb_suffix", "末尾が「TB」の予定のみ"],
    ["organizer", "自分が主催の予定のみ"],
    ["all", "すべての予定"],
  ];
  const IMPORT_INTERVAL_OPTIONS = [
    ["manual", "手動のみ"],
    ["15m", "15分ごと"],
    ["1h", "1時間ごと"],
    ["3h", "3時間ごと"],
    ["6h", "6時間ごと"],
    ["12h", "12時間ごと"],
    ["1d", "1日ごと"],
  ];

  function gcalSelectHtml(options, selected, attr) {
    return `<select ${attr}>${options
      .map(([value, label]) => `<option value="${value}"${value === selected ? " selected" : ""}>${esc(label)}</option>`)
      .join("")}</select>`;
  }

  function formatImportTime(iso) {
    if (!iso) return "未実行";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "未実行";
    return d.toLocaleString();
  }

  function renderGooglePanel() {
    const panel = $("tb-google-panel");
    if (!panel) return;
    const s = googleState.status;
    if (!s) {
      panel.innerHTML = '<div class="tobell-empty">読み込みに失敗しました</div>';
      return;
    }
    if (!s.configured) {
      panel.innerHTML = '<div class="tobell-empty">サーバー側でGoogle連携が未設定です。管理者に環境変数（DSTT_GOOGLE_CLIENT_ID / DSTT_GOOGLE_CLIENT_SECRET）の設定を依頼してください。</div>';
      return;
    }
    const sendOn = !!s.integration_enabled;
    const importOn = !!s.import_enabled;
    if (!sendOn && !importOn) {
      panel.innerHTML = '<div class="tobell-empty">「DSTT連携」タブで「重要タスクをGoogleカレンダーに送る」または「Googleカレンダーの予定をタスクに取り込む」をONにすると、ここで接続できます。</div>';
      return;
    }
    if (!s.connected) {
      panel.innerHTML = `
        <div class="tobell-google-account">
          <span class="tobell-google-status">未接続</span>
          <a class="tobell-btn tobell-btn-primary" href="/tools/to_bell/api/google/connect">Googleアカウントを接続</a>
        </div>
        <p class="tobell-share-note">接続するとGoogleの同意画面が開きます。許可するとカレンダーへの送信・取り込みが有効になります。</p>`;
      return;
    }
    let html = `
      <div class="tobell-google-account">
        <span class="tobell-google-status is-on">接続済み</span>
        <span class="tobell-google-email">${esc(s.google_email || "")}</span>
        <button type="button" class="tobell-btn tobell-danger" data-gcal-disconnect>接続を解除</button>
      </div>`;
    if (sendOn) {
      const reminderValue = reminderOverrideToSelect(s.default_reminders);
      html += `
        <label class="tobell-gcal-reminder">既定のリマインダー（送信）
          <select data-gcal-default-reminder>${reminderOptionsHtml(reminderValue, false)}</select>
        </label>
        <p class="tobell-share-note">各タスクの詳細で「📅 Googleカレンダーに送る」をONにすると、ここで選んだ既定リマインダーが使われます（タスクごとに上書きも可能）。</p>`;
    }
    if (importOn) {
      html += `
        <div class="tobell-panel-title" style="margin-top:0.8rem;">カレンダーから取り込み</div>
        <label class="tobell-gcal-reminder">取り込む予定
          ${gcalSelectHtml(IMPORT_MODE_OPTIONS, s.import_mode || "tb_suffix", "data-gcal-import-mode")}
        </label>
        <label class="tobell-gcal-reminder">取り込み間隔
          ${gcalSelectHtml(IMPORT_INTERVAL_OPTIONS, s.import_interval || "15m", "data-gcal-import-interval")}
        </label>
        <div class="tobell-google-account" style="margin-top:0.4rem;">
          <button type="button" class="tobell-btn tobell-btn-primary" data-gcal-import-now>今すぐ取り込み</button>
          <span class="tobell-share-note" style="margin:0;">最終取り込み: ${esc(formatImportTime(s.last_import_at))}</span>
        </div>
        <p class="tobell-share-note">「末尾が「TB」の予定のみ」を選ぶと、予定名の最後に TB が付いた予定だけを取り込みます。予定の変更・キャンセルにも追従します。</p>`;
    }
    panel.innerHTML = html;
    const disconnectBtn = panel.querySelector("[data-gcal-disconnect]");
    if (disconnectBtn) disconnectBtn.addEventListener("click", disconnectGoogle);
    const defaultSel = panel.querySelector("[data-gcal-default-reminder]");
    if (defaultSel) defaultSel.addEventListener("change", () => saveDefaultReminder(defaultSel));
    const modeSel = panel.querySelector("[data-gcal-import-mode]");
    if (modeSel) modeSel.addEventListener("change", () => saveImportSetting({ mode: modeSel.value }, modeSel));
    const intervalSel = panel.querySelector("[data-gcal-import-interval]");
    if (intervalSel) intervalSel.addEventListener("change", () => saveImportSetting({ interval: intervalSel.value }, intervalSel));
    const importBtn = panel.querySelector("[data-gcal-import-now]");
    if (importBtn) importBtn.addEventListener("click", () => runImportNow(importBtn));
  }

  async function saveImportSetting(body, el) {
    if (el) el.disabled = true;
    try {
      const data = await api("/tools/to_bell/api/google/import-settings", { method: "PUT", body });
      if (googleState.status) Object.assign(googleState.status, data);
      showFlash("取り込み設定を保存しました", "success");
    } catch (_) {
      // api() が flash 表示する。保存に失敗したらセレクト表示を元の値へ戻す。
      if (el && googleState.status) {
        if ("mode" in body) el.value = googleState.status.import_mode || "tb_suffix";
        if ("interval" in body) el.value = googleState.status.import_interval || "15m";
      }
    } finally {
      if (el) el.disabled = false;
    }
  }

  async function runImportNow(btn) {
    if (btn) {
      btn.disabled = true;
      btn.textContent = "取り込み中…";
    }
    try {
      const data = await api("/tools/to_bell/api/google/import", { method: "POST" });
      if (data && data.status) googleState.status = data.status;
      const r = (data && data.result) || {};
      const created = Number(r.created || 0);
      const updated = Number(r.updated || 0);
      const skipped = Number(r.skipped || 0);
      let message = `取り込み完了: 新規${created} / 更新${updated} / 完了${Number(r.completed || 0)}`;
      if (skipped) message += ` / 対象外${skipped}`;
      if (!created && !updated && skipped) {
        message += "（取り込む範囲の条件に合う予定がありませんでした）";
      }
      showFlash(message, created || updated ? "success" : "info");
      renderGooglePanel();
      // 取り込んだ予定は「🔗 連携」フィルタに入る。取り込み直後に一覧を切り替えて、
      // 「実行したのに何も出てこない」状態にならないようにする。
      if (created || updated) {
        state.filter = "integrations";
        state.view = "list";
        document.querySelectorAll(".tobell-filter").forEach((item) => {
          item.classList.toggle("is-active", item.dataset.filter === "integrations");
        });
        syncViewButtons();
        const settings = $("tb-settings-modal");
        if (settings) settings.setAttribute("hidden", "");
      }
      await loadTasks();
    } catch (_) {
      // api() が flash 表示する
      if (btn) {
        btn.disabled = false;
        btn.textContent = "今すぐ取り込み";
      }
    }
  }

  async function saveDefaultReminder(sel) {
    sel.disabled = true;
    try {
      const reminders = sel.value === "none" ? [] : [{ method: "popup", minutes: Number(sel.value) }];
      const data = await api("/tools/to_bell/api/google/reminders", { method: "PUT", body: { reminders } });
      if (googleState.status) googleState.status.default_reminders = data.default_reminders;
      showFlash("既定のリマインダーを保存しました", "success");
    } catch (_) {
      // api() が flash 表示する
    } finally {
      sel.disabled = false;
    }
  }

  async function disconnectGoogle() {
    if (!window.confirm("Googleカレンダー連携を解除しますか？同期済みタスクのイベント参照も外れます。")) return;
    try {
      googleState.status = await api("/tools/to_bell/api/google/disconnect", { method: "POST" });
      renderGooglePanel();
      showFlash("接続を解除しました", "info");
    } catch (_) {
      // api() が flash 表示する
    }
  }

  function setupTaskCalendar(task, form) {
    const wrap = form.querySelector("[data-gcal-detail]");
    if (!wrap) return;
    const status = googleState.status;
    if (!status || !status.integration_enabled) {
      wrap.hidden = true;
      return;
    }
    wrap.hidden = false;
    const toggle = form.querySelector("[data-gcal-toggle]");
    const reminderWrap = form.querySelector("[data-gcal-reminder-wrap]");
    const reminderSel = form.querySelector("[data-gcal-reminder]");
    const hint = form.querySelector("[data-gcal-hint]");
    // Googleカレンダーから取り込んだタスクは送り返せない（二重イベント・編集分岐を防ぐ）。
    const imported = task.source_tool === "google" && task.source_ref_type === "calendar_event";
    if (imported) {
      toggle.checked = false;
      toggle.disabled = true;
      reminderWrap.hidden = true;
      hint.textContent = "このタスクはGoogleカレンダーから取り込んだ予定のため、カレンダーへの送信はできません。";
      return;
    }
    toggle.disabled = false;
    const synced = !!task.gcal_synced;
    toggle.checked = synced;
    reminderWrap.hidden = !synced;
    reminderSel.value = reminderOverrideToSelect(task.gcal_reminder_override);
    hint.textContent = "";
    if (!status.connected) {
      hint.textContent = "Googleアカウント未接続です。「設定 → Googleカレンダー」で接続してください。";
    } else if (!task.due_at) {
      hint.textContent = "通知日時を設定すると、カレンダーに送れます。";
    }
    toggle.addEventListener("change", () => toggleTaskCalendar(task, toggle, reminderSel, reminderWrap));
    reminderSel.addEventListener("change", () => changeTaskReminder(task, toggle, reminderSel));
  }

  function applyTaskUpdate(task, updated) {
    if (!updated) return;
    Object.assign(task, updated);
    const index = state.tasks.findIndex((item) => item.id === task.id);
    if (index >= 0) state.tasks.splice(index, 1, task);
    renderMain();
  }

  async function toggleTaskCalendar(task, toggle, reminderSel, reminderWrap) {
    const status = googleState.status;
    if (toggle.checked) {
      if (!status || !status.connected) {
        toggle.checked = false;
        showFlash("先に「設定 → Googleカレンダー」で接続してください。", "error");
        return;
      }
      if (!task.due_at) {
        toggle.checked = false;
        showFlash("通知日時が未設定のタスクはカレンダーに送れません。", "error");
        return;
      }
    }
    toggle.disabled = true;
    try {
      if (toggle.checked) {
        const reminders = reminderSelectToPayload(reminderSel.value);
        const data = await api(`/tools/to_bell/api/tasks/${task.id}/calendar`, { method: "POST", body: { reminders } });
        applyTaskUpdate(task, data.task);
        reminderWrap.hidden = false;
        showFlash("Googleカレンダーに送りました", "success");
      } else {
        const data = await api(`/tools/to_bell/api/tasks/${task.id}/calendar`, { method: "DELETE" });
        applyTaskUpdate(task, data.task);
        reminderWrap.hidden = true;
        showFlash("カレンダーから外しました", "info");
      }
    } catch (_) {
      toggle.checked = !toggle.checked; // 失敗したら元に戻す
    } finally {
      toggle.disabled = false;
    }
  }

  async function changeTaskReminder(task, toggle, reminderSel) {
    if (!toggle.checked) return; // 未同期ならON時に反映する
    reminderSel.disabled = true;
    try {
      const reminders = reminderSelectToPayload(reminderSel.value);
      const data = await api(`/tools/to_bell/api/tasks/${task.id}/calendar`, { method: "POST", body: { reminders } });
      applyTaskUpdate(task, data.task);
      showFlash("リマインダーを更新しました", "success");
    } catch (_) {
      // api() が flash 表示する
    } finally {
      reminderSel.disabled = false;
    }
  }

  function handleGoogleReturn() {
    const params = new URLSearchParams(window.location.search);
    const ok = params.get("google");
    const err = params.get("google_error");
    if (!ok && !err) return;
    params.delete("google");
    params.delete("google_error");
    const qs = params.toString();
    window.history.replaceState({}, "", window.location.pathname + (qs ? `?${qs}` : ""));
    if (ok === "connected") {
      showFlash("Googleカレンダーに接続しました", "success");
      loadGoogleStatus().catch(() => {});
    } else if (err) {
      const msg = {
        state_mismatch: "接続に失敗しました（セキュリティ検証エラー）。もう一度お試しください。",
        no_code: "接続に失敗しました（認可コード未取得）。",
        token_exchange: "接続に失敗しました（トークン交換エラー）。",
        access_denied: "接続がキャンセルされました。",
      }[err] || `接続に失敗しました（${err}）。`;
      showFlash(msg, "error");
    }
  }

  // ===== 紐付けモーダル =====

  const linksState = { targetType: "", targetId: 0, label: "" };

  function initLinksModal() {
    const modal = $("tb-links-modal");
    if (!modal) return;
    modal.querySelectorAll("[data-links-close]").forEach((el) => {
      el.addEventListener("click", () => modal.setAttribute("hidden", ""));
    });
    const empSearch = $("tb-links-emp-search");
    if (empSearch) empSearch.addEventListener("click", runEmployeeSearch);
    const empQ = $("tb-links-emp-q");
    if (empQ) empQ.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); runEmployeeSearch(); } });
    const siteSearch = $("tb-links-site-search");
    if (siteSearch) siteSearch.addEventListener("click", runSiteSearch);
    const siteQ = $("tb-links-site-q");
    if (siteQ) siteQ.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); runSiteSearch(); } });
    const fpForm = $("tb-links-fp-form");
    if (fpForm) fpForm.addEventListener("submit", uploadFilepostLink);
  }

  async function openLinksModal(targetType, targetId, label) {
    const modal = $("tb-links-modal");
    if (!modal) return;
    linksState.targetType = targetType;
    linksState.targetId = Number(targetId) || 0;
    linksState.label = label || "";
    const context = $("tb-links-context");
    if (context) context.textContent = `紐付け対象: ${label || (targetType + ":" + targetId)}`;
    // 連携許可状況により section の表示/非表示
    let settings = null;
    try { settings = await api("/tools/to_bell/api/settings"); } catch (_) { settings = null; }
    const integrations = (settings && settings.integrations) || {};
    const fpEnabled = (targetType === "task" && !!integrations["filepost.attachment_overflow"]) ||
                     (targetType === "project" && !!integrations["filepost.project_files"]);
    const anyEnabled = !!integrations["pluslist.linkage"] || !!integrations["siteplus.linkage"] || fpEnabled;
    const emptyMsg = $("tb-links-empty");
    if (emptyMsg) emptyMsg.hidden = anyEnabled;
    modal.querySelectorAll("[data-links-kind]").forEach((sec) => {
      const kind = sec.dataset.linksKind;
      let enabled = false;
      if (kind === "employees") enabled = !!integrations["pluslist.linkage"];
      else if (kind === "sites") enabled = !!integrations["siteplus.linkage"];
      else if (kind === "filepost") enabled = fpEnabled;
      sec.style.display = enabled ? "" : "none";
    });
    $("tb-links-emp-results").innerHTML = "";
    $("tb-links-site-results").innerHTML = "";
    if (integrations["pluslist.linkage"]) await loadEmployeeLinks();
    if (integrations["siteplus.linkage"]) await loadSiteLinks();
    if (fpEnabled) await loadFilepostLinks();
    modal.removeAttribute("hidden");
  }

  // 詳細パネルや プロジェクト編集モーダル から呼び出すための公開フック
  window.openToBellLinks = openLinksModal;

  async function loadEmployeeLinks() {
    const container = $("tb-links-emp-current");
    container.innerHTML = '<div class="tobell-link-empty">読み込み中…</div>';
    try {
      const data = await api(`/tools/to_bell/api/links/employees?target_type=${encodeURIComponent(linksState.targetType)}&target_id=${linksState.targetId}`);
      const items = data.links || [];
      if (!items.length) { container.innerHTML = '<div class="tobell-link-empty">紐付けはまだありません</div>'; return; }
      container.innerHTML = items.map((row) => `
        <div class="tobell-link-row">
          <div>
            <div>${esc(row.employee_name || "(無名)")} <span class="tobell-link-sub">${esc(row.employee_number || "")}</span></div>
            <div class="tobell-link-sub">${esc(row.office_name || "")}</div>
          </div>
          <button type="button" class="tobell-btn tobell-danger" data-emp-unlink="${row.link_id}">解除</button>
        </div>`).join("");
      container.querySelectorAll("[data-emp-unlink]").forEach((b) => {
        b.addEventListener("click", async () => {
          b.disabled = true;
          try { await api(`/tools/to_bell/api/links/employees/${b.dataset.empUnlink}`, { method: "DELETE" }); await loadEmployeeLinks(); }
          finally { b.disabled = false; }
        });
      });
    } catch (_) {
      container.innerHTML = '<div class="tobell-link-empty">読み込みに失敗しました</div>';
    }
  }

  async function runEmployeeSearch() {
    const q = ($("tb-links-emp-q").value || "").trim();
    const results = $("tb-links-emp-results");
    results.innerHTML = '<div class="tobell-link-empty">検索中…</div>';
    try {
      const data = await api(`/tools/to_bell/api/links/employees/search?q=${encodeURIComponent(q)}`);
      const items = data.results || [];
      if (!items.length) { results.innerHTML = '<div class="tobell-link-empty">該当なし</div>'; return; }
      results.innerHTML = items.map((row) => `
        <div class="tobell-link-row">
          <div>
            <div>${esc(row.employee_name || "(無名)")} <span class="tobell-link-sub">${esc(row.employee_number || "")}</span></div>
            <div class="tobell-link-sub">${esc(row.office_name || "")}</div>
          </div>
          <button type="button" class="tobell-btn tobell-btn-primary" data-emp-link="${row.employee_id}">紐付け</button>
        </div>`).join("");
      results.querySelectorAll("[data-emp-link]").forEach((b) => {
        b.addEventListener("click", async () => {
          b.disabled = true;
          try {
            await api("/tools/to_bell/api/links/employees", { method: "POST", body: {
              target_type: linksState.targetType, target_id: linksState.targetId, employee_id: Number(b.dataset.empLink),
            }});
            await loadEmployeeLinks();
            showFlash("紐付けました", "success");
          } finally { b.disabled = false; }
        });
      });
    } catch (_) {
      results.innerHTML = '<div class="tobell-link-empty">検索に失敗しました</div>';
    }
  }

  async function loadSiteLinks() {
    const container = $("tb-links-site-current");
    container.innerHTML = '<div class="tobell-link-empty">読み込み中…</div>';
    try {
      const data = await api(`/tools/to_bell/api/links/sites?target_type=${encodeURIComponent(linksState.targetType)}&target_id=${linksState.targetId}`);
      const items = data.links || [];
      if (!items.length) { container.innerHTML = '<div class="tobell-link-empty">紐付けはまだありません</div>'; return; }
      container.innerHTML = items.map((row) => `
        <div class="tobell-link-row">
          <div>
            <div>${esc(row.site_name || "(無名)")} <span class="tobell-link-sub">${esc(row.site_id || "")}</span></div>
          </div>
          <button type="button" class="tobell-btn tobell-danger" data-site-unlink="${row.link_id}">解除</button>
        </div>`).join("");
      container.querySelectorAll("[data-site-unlink]").forEach((b) => {
        b.addEventListener("click", async () => {
          b.disabled = true;
          try { await api(`/tools/to_bell/api/links/sites/${b.dataset.siteUnlink}`, { method: "DELETE" }); await loadSiteLinks(); }
          finally { b.disabled = false; }
        });
      });
    } catch (_) {
      container.innerHTML = '<div class="tobell-link-empty">読み込みに失敗しました</div>';
    }
  }

  async function runSiteSearch() {
    const q = ($("tb-links-site-q").value || "").trim();
    const results = $("tb-links-site-results");
    results.innerHTML = '<div class="tobell-link-empty">検索中…</div>';
    try {
      const data = await api(`/tools/to_bell/api/links/sites/search?q=${encodeURIComponent(q)}`);
      const items = data.results || [];
      if (!items.length) { results.innerHTML = '<div class="tobell-link-empty">該当なし</div>'; return; }
      results.innerHTML = items.map((row) => `
        <div class="tobell-link-row">
          <div>
            <div>${esc(row.site_name || "(無名)")} <span class="tobell-link-sub">${esc(row.site_id || "")}</span></div>
          </div>
          <button type="button" class="tobell-btn tobell-btn-primary" data-site-link="${row.site_row_id}">紐付け</button>
        </div>`).join("");
      results.querySelectorAll("[data-site-link]").forEach((b) => {
        b.addEventListener("click", async () => {
          b.disabled = true;
          try {
            await api("/tools/to_bell/api/links/sites", { method: "POST", body: {
              target_type: linksState.targetType, target_id: linksState.targetId, site_row_id: Number(b.dataset.siteLink),
            }});
            await loadSiteLinks();
            showFlash("紐付けました", "success");
          } finally { b.disabled = false; }
        });
      });
    } catch (_) {
      results.innerHTML = '<div class="tobell-link-empty">検索に失敗しました</div>';
    }
  }

  async function loadFilepostLinks() {
    const container = $("tb-links-fp-current");
    if (!container) return;
    container.innerHTML = '<div class="tobell-link-empty">読み込み中…</div>';
    try {
      const data = await api(`/tools/to_bell/api/integrations/filepost/files?target_type=${encodeURIComponent(linksState.targetType)}&target_id=${linksState.targetId}`);
      const items = data.files || [];
      if (!items.length) { container.innerHTML = '<div class="tobell-link-empty">FILEPOST添付はまだありません</div>'; return; }
      container.innerHTML = items.map((row) => `
        <div class="tobell-link-row">
          <div>
            <div><a href="${esc(row.private_url)}" target="_blank" rel="noopener">${esc(row.file_name)}</a></div>
            <div class="tobell-link-sub">${formatFileSize(row.file_size)} ・ ${esc(row.uploaded_by || "")}</div>
          </div>
          <button type="button" class="tobell-btn tobell-danger" data-fp-unlink="${row.id}">削除</button>
        </div>`).join("");
      container.querySelectorAll("[data-fp-unlink]").forEach((b) => {
        b.addEventListener("click", async () => {
          if (!window.confirm("このFILEPOST紐付けを解除しますか？")) return;
          b.disabled = true;
          try { await api(`/tools/to_bell/api/integrations/filepost/files/${b.dataset.fpUnlink}`, { method: "DELETE" }); await loadFilepostLinks(); }
          finally { b.disabled = false; }
        });
      });
    } catch (_) {
      container.innerHTML = '<div class="tobell-link-empty">読み込みに失敗しました</div>';
    }
  }

  async function uploadFilepostLink(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const file = $("tb-links-fp-file").files[0];
    if (!file) { showFlash("ファイルを選択してください", "error"); return; }
    const data = new FormData();
    data.append("file", file);
    data.append("target_type", linksState.targetType);
    data.append("target_id", String(linksState.targetId));
    const btn = form.querySelector('[type="submit"]');
    if (btn) btn.disabled = true;
    try {
      await api("/tools/to_bell/api/integrations/filepost/upload", { method: "POST", body: data });
      form.reset();
      await loadFilepostLinks();
      showFlash("FILEPOSTへアップロードしました", "success");
    } finally {
      if (btn) btn.disabled = false;
    }
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

  // 終日タスク（時刻未指定）は 23:59:59 として保存される。表示では時刻を出さない。
  function isAllDayValue(value) {
    return typeof value === "string" && value.slice(11, 19) === "23:59:59";
  }

  function formatDue(value, allDay) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value || "").slice(0, 16).replace("T", " ");
    const y = date.getFullYear();
    const m = String(date.getMonth() + 1).padStart(2, "0");
    const d = String(date.getDate()).padStart(2, "0");
    const dateText = `${y}-${m}-${d}`;
    if (allDay === undefined ? isAllDayValue(value) : allDay) return `${dateText} 終日`;
    const hh = String(date.getHours()).padStart(2, "0");
    const mm = String(date.getMinutes()).padStart(2, "0");
    return `${dateText} ${hh}:${mm}`;
  }

  function taskDueText(task) {
    if (!task.due_at) return "期日なし";
    return formatDue(task.due_at, task.due_all_day);
  }

  function formatDueShort(value, allDay) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    const m = String(date.getMonth() + 1).padStart(2, "0");
    const d = String(date.getDate()).padStart(2, "0");
    if (allDay === undefined ? isAllDayValue(value) : allDay) return `${m}/${d}`;
    const hh = String(date.getHours()).padStart(2, "0");
    const mm = String(date.getMinutes()).padStart(2, "0");
    return `${m}/${d} ${hh}:${mm}`;
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
    }, FLASH_DURATION_MS);
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
