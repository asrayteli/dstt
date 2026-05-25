(function () {
  const state = {
    filter: new URLSearchParams(window.location.search).get("filter") || "today",
    selectedTaskId: Number(new URLSearchParams(window.location.search).get("task") || 0),
    tasks: [],
  };

  const $ = (id) => document.getElementById(id);
  const esc = (value) => String(value || "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[ch]));

  document.addEventListener("DOMContentLoaded", () => {
    bindFilters();
    $("tb-quick-form").addEventListener("submit", createQuickTask);
    $("tb-new-task").addEventListener("click", () => $("tb-title").focus());
    $("tb-enable-windows-notify").addEventListener("click", openWindowsNotifier);
    $("tb-enable-push-notify").addEventListener("click", enablePushNotifications);
    $("tb-test-push-notify").addEventListener("click", sendTestPush);
    $("tb-read-all").addEventListener("click", readAllNotifications);
    $("tb-search").addEventListener("input", debounce(loadTasks, 220));
    loadTasks();
    loadNotifications();
  });

  function bindFilters() {
    document.querySelectorAll(".tobell-filter").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.filter === state.filter);
      button.addEventListener("click", () => {
        state.filter = button.dataset.filter || "today";
        state.selectedTaskId = 0;
        document.querySelectorAll(".tobell-filter").forEach((item) => item.classList.remove("is-active"));
        button.classList.add("is-active");
        loadTasks();
      });
    });
  }

  async function loadTasks() {
    const params = new URLSearchParams({ filter: state.filter, q: $("tb-search").value || "" });
    const data = await api(`/tools/to_bell/api/tasks?${params.toString()}`);
    state.tasks = data.tasks || [];
    renderSummary(data.summary || {});
    renderTasks();
    if (state.selectedTaskId) {
      const selected = state.tasks.find((task) => task.id === state.selectedTaskId);
      if (selected) renderDetail(selected);
    }
  }

  function renderSummary(summary) {
    const items = [
      `<span class="tobell-chip">表示 ${state.tasks.length}件</span>`,
      `<span class="tobell-chip">要対応 ${Number(summary.action_count || 0)}件</span>`,
      `<span class="tobell-chip">未読 ${Number(summary.unread_count || 0)}件</span>`,
    ];
    $("tb-summary").innerHTML = items.join("");
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
          <div>
            <h3>${esc(task.title)}</h3>
            <p>${esc(task.description || "メモなし")}</p>
            <p>${esc(statusLabel(task.status))} / ${esc(due)} / 進捗 ${Number(task.progress || 0)}%</p>
          </div>
          <span class="tobell-badge ${badgeClass}">${esc(priorityLabel(task.priority))}</span>
        </article>`;
    }).join("");
    document.querySelectorAll("[data-task-id]").forEach((card) => {
      card.addEventListener("click", (event) => {
        if (event.target.matches("[data-complete-id]")) return;
        const task = state.tasks.find((item) => item.id === Number(card.dataset.taskId));
        if (task) renderDetail(task);
      });
    });
    document.querySelectorAll("[data-complete-id]").forEach((checkbox) => {
      checkbox.addEventListener("change", async () => {
        const id = Number(checkbox.dataset.completeId);
        await api(`/tools/to_bell/api/tasks/${id}/${checkbox.checked ? "complete" : "reopen"}`, { method: "POST" });
        await loadTasks();
        await loadNotifications();
      });
    });
  }

  function renderDetail(task) {
    state.selectedTaskId = task.id;
    const fragment = $("tb-detail-template").content.cloneNode(true);
    $("tb-detail").replaceChildren(fragment);
    const form = $("tb-detail-form");
    form.elements.id.value = task.id;
    form.elements.title.value = task.title || "";
    form.elements.description.value = task.description || "";
    form.elements.status.value = task.status || "todo";
    form.elements.priority.value = task.priority || "normal";
    form.elements.due_at.value = task.due_at ? task.due_at.slice(0, 16) : "";
    form.elements.assigned_to.value = task.assigned_to || "";
    form.elements.tags.value = (task.tags || []).map((tag) => tag.name).join(", ");
    renderSubtasks(task);
    renderComments(task);
    form.addEventListener("submit", saveDetail);
    $("tb-subtask-form").addEventListener("submit", addSubtask);
    $("tb-comment-form").addEventListener("submit", addComment);
    document.querySelectorAll("[data-action]").forEach((button) => button.addEventListener("click", detailAction));
    renderTasks();
  }

  function renderSubtasks(task) {
    const rows = task.subtasks || [];
    $("tb-subtasks").innerHTML = rows.length ? rows.map((item) => `
      <label class="tobell-subtask">
        <input type="checkbox" ${item.is_done ? "checked" : ""} data-subtask-id="${item.id}">
        <span>${esc(item.title)}</span>
      </label>`).join("") : '<div class="tobell-empty">サブタスクはありません。</div>';
    document.querySelectorAll("[data-subtask-id]").forEach((box) => {
      box.addEventListener("change", async () => {
        await api(`/tools/to_bell/api/subtasks/${box.dataset.subtaskId}`, {
          method: "PUT",
          body: { is_done: box.checked },
        });
        await refreshSelectedTask();
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
    const payload = Object.fromEntries(new FormData(form).entries());
    await api("/tools/to_bell/api/tasks", { method: "POST", body: payload });
    form.reset();
    await loadTasks();
  }

  async function saveDetail(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = Object.fromEntries(new FormData(form).entries());
    payload.tags = payload.tags || "";
    await api(`/tools/to_bell/api/tasks/${payload.id}`, { method: "PUT", body: payload });
    await refreshSelectedTask();
    await loadTasks();
  }

  async function detailAction(event) {
    const action = event.currentTarget.dataset.action;
    const id = state.selectedTaskId;
    if (!id) return;
    if (action === "archive" && !window.confirm("このタスクをアーカイブしますか？")) return;
    const path = action === "archive" ? `/tools/to_bell/api/tasks/${id}` : `/tools/to_bell/api/tasks/${id}/${action}`;
    await api(path, { method: action === "archive" ? "DELETE" : "POST" });
    if (action === "archive") {
      state.selectedTaskId = 0;
      $("tb-detail").innerHTML = '<div class="tobell-empty">タスクを選ぶと詳細が開きます。</div>';
    }
    await loadTasks();
    await loadNotifications();
  }

  async function addSubtask(event) {
    event.preventDefault();
    const title = event.currentTarget.elements.title.value.trim();
    if (!title) return;
    await api(`/tools/to_bell/api/tasks/${state.selectedTaskId}/subtasks`, { method: "POST", body: { title } });
    event.currentTarget.reset();
    await refreshSelectedTask();
  }

  async function addComment(event) {
    event.preventDefault();
    const body = event.currentTarget.elements.body.value.trim();
    if (!body) return;
    await api(`/tools/to_bell/api/tasks/${state.selectedTaskId}/comments`, { method: "POST", body: { body } });
    event.currentTarget.reset();
    await refreshSelectedTask();
    await loadNotifications();
  }

  async function refreshSelectedTask() {
    if (!state.selectedTaskId) return;
    const task = await api(`/tools/to_bell/api/tasks/${state.selectedTaskId}`);
    const index = state.tasks.findIndex((item) => item.id === task.id);
    if (index >= 0) state.tasks.splice(index, 1, task);
    renderDetail(task);
  }

  async function loadNotifications() {
    const data = await api("/tools/to_bell/api/notifications");
    const rows = data.notifications || [];
    $("tb-notifications").innerHTML = rows.length ? rows.slice(0, 8).map((item) => `
      <div class="tobell-notification">
        <strong>${esc(item.title)}</strong>
        <div>${esc(item.body)}</div>
      </div>`).join("") : '<div class="tobell-empty">通知はありません。</div>';
  }

  async function readAllNotifications() {
    await api("/tools/to_bell/api/notifications/read-all", { method: "POST" });
    await loadNotifications();
    await loadTasks();
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

  async function enablePushNotifications() {
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
      window.alert("このブラウザではプッシュ通知を利用できません。iPhoneではホーム画面に追加したWebアプリから実行してください。");
      return;
    }
    const permission = await Notification.requestPermission();
    if (permission !== "granted") {
      window.alert("通知が許可されませんでした。");
      return;
    }
    const keyData = await api("/tools/to_bell/api/push/public-key");
    if (!keyData.public_key) {
      window.alert(keyData.message || "プッシュ通知の公開鍵を取得できませんでした。");
      return;
    }
    const registration = await navigator.serviceWorker.register("/service-worker.js", { scope: "/" });
    const subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(keyData.public_key),
    });
    const result = await api("/tools/to_bell/api/push/subscribe", {
      method: "POST",
      body: { subscription: subscription.toJSON() },
    });
    window.alert(`${result.device_label || "端末"} のプッシュ通知を有効にしました。`);
  }

  async function sendTestPush() {
    const result = await api("/tools/to_bell/api/push/test", { method: "POST" });
    window.alert(`テスト通知を送信しました。送信 ${result.sent || 0}件 / 失敗 ${result.failed || 0}件`);
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
      window.alert(data.error || "処理に失敗しました。");
      throw new Error(data.error || response.statusText);
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

  function debounce(fn, wait) {
    let timer = 0;
    return () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(fn, wait);
    };
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
