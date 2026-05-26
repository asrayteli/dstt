(function () {
  const state = {
    filter: new URLSearchParams(window.location.search).get("filter") || "today",
    selectedTaskId: Number(new URLSearchParams(window.location.search).get("task") || 0),
    tasks: [],
    swRegistration: null,
    foregroundTimer: 0,
  };

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
    $("tb-quick-form").addEventListener("submit", createQuickTask);
    const newButton = $("tb-new-task");
    if (newButton) newButton.addEventListener("click", () => $("tb-title").focus());
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
    loadTasks();
    loadNotifications();
    initNotifications();
  });

  function bindFilters() {
    document.querySelectorAll(".tobell-filter").forEach((button) => {
      button.classList.toggle("is-active", button.dataset.filter === state.filter);
      button.addEventListener("click", () => {
        state.filter = button.dataset.filter || "today";
        state.selectedTaskId = 0;
        closeDetail();
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
          <div class="tobell-task-body">
            <h3>${esc(task.title)}</h3>
            <p>${esc(task.description || "メモなし")}</p>
            <p class="tobell-task-meta">${esc(statusLabel(task.status))} / ${esc(due)} / 進捗 ${Number(task.progress || 0)}%</p>
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
        const completing = checkbox.checked;
        await api(`/tools/to_bell/api/tasks/${id}/${completing ? "complete" : "reopen"}`, { method: "POST" });
        await loadTasks();
        await loadNotifications();
        showFlash(completing ? "完了にしました" : "未完了に戻しました", "success");
      });
    });
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
    form.elements.tags.value = (task.tags || []).map((tag) => tag.name).join(", ");
    renderSubtasks(task);
    renderComments(task);
    form.addEventListener("submit", saveDetail);
    $("tb-subtask-form").addEventListener("submit", addSubtask);
    $("tb-comment-form").addEventListener("submit", addComment);
    const backButton = $("tb-detail-back");
    if (backButton) backButton.addEventListener("click", closeDetail);
    document.querySelectorAll("[data-action]").forEach((button) => button.addEventListener("click", detailAction));
    renderTasks();
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
        showFlash("サブタスクを更新しました", "success");
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
    showFlash("タスクを追加しました", "success");
  }

  async function saveDetail(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = Object.fromEntries(new FormData(form).entries());
    payload.tags = payload.tags || "";
    await api(`/tools/to_bell/api/tasks/${payload.id}`, { method: "PUT", body: payload });
    await refreshSelectedTask();
    await loadTasks();
    showFlash("保存しました", "success");
  }

  async function detailAction(event) {
    const action = event.currentTarget.dataset.action;
    const id = state.selectedTaskId;
    if (!id) return;
    if (action === "archive" && !window.confirm("このタスクをアーカイブしますか？")) return;
    if (action === "delete" && !window.confirm("このタスクを完全に削除します。元に戻せません。よろしいですか？")) return;
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
    }
    await loadTasks();
    await loadNotifications();
    showFlash({
      complete: "完了にしました",
      reopen: "未完了に戻しました",
      archive: "アーカイブしました",
      delete: "削除しました",
    }[action] || "更新しました", "success");
  }

  async function addSubtask(event) {
    event.preventDefault();
    const title = event.currentTarget.elements.title.value.trim();
    if (!title) return;
    await api(`/tools/to_bell/api/tasks/${state.selectedTaskId}/subtasks`, { method: "POST", body: { title } });
    event.currentTarget.reset();
    await refreshSelectedTask();
    showFlash("サブタスクを追加しました", "success");
  }

  async function addComment(event) {
    event.preventDefault();
    const body = event.currentTarget.elements.body.value.trim();
    if (!body) return;
    await api(`/tools/to_bell/api/tasks/${state.selectedTaskId}/comments`, { method: "POST", body: { body } });
    event.currentTarget.reset();
    await refreshSelectedTask();
    await loadNotifications();
    showFlash("コメントを送信しました", "success");
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
    await api("/tools/to_bell/api/notifications/read-all", { method: "POST" });
    await loadNotifications();
    await loadTasks();
    showFlash("通知をすべて既読にしました", "success");
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
    updateNotifyStatus();
    // 登録は権限不要。前面通知に showNotification を使えるよう先に登録しておく。
    registerServiceWorker().then(async () => {
      await refreshNotifyToggle();
      // この端末で通知が有効（購読済み）のときだけ前面監視を動かす。
      if ($("tb-enable-push-notify") && $("tb-enable-push-notify").dataset.state === "on") {
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
    if (button && button.dataset.state === "on") {
      await disablePushNotifications();
    } else {
      await enablePushNotifications();
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
      startForegroundWatch();
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
    const result = await api("/tools/to_bell/api/push/test", { method: "POST" });
    if ((result.sent || 0) === 0) {
      // プッシュ購読がまだ無い端末向けに、前面通知でも確認できるようにする。
      await showLocalNotification("To Bell テスト通知", { body: "前面通知のテストです。" });
    }
    showFlash(`テスト通知を送信しました（送信 ${result.sent || 0} / 失敗 ${result.failed || 0}）`, "info");
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

    $("tb-share-issue").addEventListener("click", () => mutateShare("/tools/to_bell/api/share/issue", modal, "共有リンクを発行しました。"));
    $("tb-share-reissue").addEventListener("click", () => mutateShare("/tools/to_bell/api/share/issue", modal, "共有リンクを再発行しました。"));
    $("tb-share-revoke").addEventListener("click", () => mutateShare("/tools/to_bell/api/share/revoke", modal, "共有リンクを無効化しました。"));
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

  async function mutateShare(path, modal, message) {
    try {
      const data = await api(path, { method: "POST" });
      renderShareState(data);
      showFlash(message, "success");
    } catch (err) {
      /* api() がトーストを表示済み */
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
        const row = button.closest("[data-device-id]");
        const label = row ? row.querySelector(".tobell-device-label").value : "";
        await api(`/tools/to_bell/api/push/subscriptions/${button.dataset.deviceSave}`, {
          method: "PUT",
          body: { device_label: label },
        });
        await loadDevices();
        showFlash("通知先を更新しました。", "success");
      });
    });
    list.querySelectorAll("[data-device-disable]").forEach((button) => {
      button.addEventListener("click", async () => {
        await api(`/tools/to_bell/api/push/subscriptions/${button.dataset.deviceDisable}`, { method: "DELETE" });
        await loadDevices();
        showFlash("通知先を無効化しました。", "success");
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

  function todayIso() {
    const now = new Date();
    const y = now.getFullYear();
    const m = String(now.getMonth() + 1).padStart(2, "0");
    const d = String(now.getDate()).padStart(2, "0");
    return `${y}-${m}-${d}`;
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
