(function () {
  const status = document.getElementById("status");
  const enableButton = document.getElementById("enable");
  let swRegistration = null;
  let monitorTimer = 0;

  document.addEventListener("DOMContentLoaded", () => {
    enableButton.addEventListener("click", enableNotifications);
    startMonitor();
  });

  async function enableNotifications() {
    if (!("Notification" in window)) {
      setStatus("このブラウザでは通知を利用できません。");
      return;
    }
    try {
      const permission = await Notification.requestPermission();
      if (permission === "granted") {
        await registerServiceWorker();
        setStatus("通知待受中です。このウィンドウは最小化できます。");
        startMonitor();
        await checkDueTasks();
      } else {
        setStatus("通知が許可されませんでした。Windowsまたはブラウザの通知設定を確認してください。");
      }
    } catch (error) {
      setStatus(`通知を有効化できませんでした: ${error && error.message ? error.message : error}`);
    }
  }

  function startMonitor() {
    if (!("Notification" in window)) {
      setStatus("通知に対応していないブラウザです。");
      return;
    }
    if (Notification.permission === "granted") {
      setStatus("通知待受中です。このウィンドウは最小化できます。");
      registerServiceWorker();
      checkDueTasks();
    } else {
      setStatus("通知を使うには「通知を有効化」を押してください。");
    }
    if (!monitorTimer) {
      monitorTimer = window.setInterval(checkDueTasks, 60000);
    }
  }

  async function registerServiceWorker() {
    if (!("serviceWorker" in navigator)) return null;
    if (swRegistration) return swRegistration;
    try {
      const registration = await navigator.serviceWorker.register("/service-worker.js", { scope: "/" });
      await navigator.serviceWorker.ready;
      swRegistration = registration;
      return registration;
    } catch (error) {
      return null;
    }
  }

  async function checkDueTasks() {
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
      await showNotification(`To Bell: ${task.title}`, {
        body: `${formatDue(task.due_at)} / ${statusLabel(task.status)}`,
        tag: key,
        data: { url: `/tools/to_bell?task=${task.id}` },
      });
    }
  }

  async function showNotification(title, options) {
    const opts = Object.assign(
      {
        icon: "/static/img/android-chrome-192x192.png",
        badge: "/static/img/apple-touch-icon.png",
      },
      options || {}
    );
    try {
      const registration = swRegistration || (await registerServiceWorker());
      if (registration && registration.showNotification) {
        await registration.showNotification(title, opts);
        return;
      }
    } catch (error) {
      // フォールバックへ
    }
    try {
      const note = new Notification(title, opts);
      note.onclick = () => {
        const url = opts.data && opts.data.url ? opts.data.url : "/tools/to_bell";
        window.open(url, "toBellMain");
      };
    } catch (error) {
      setStatus(`通知の表示に失敗しました: ${error && error.message ? error.message : error}`);
    }
  }

  function setStatus(message) {
    status.textContent = message;
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

  function statusLabel(taskStatus) {
    return {
      todo: "未着手",
      doing: "進行中",
      blocked: "保留",
      review: "確認待ち",
      returned: "差戻し",
      done: "完了",
      archived: "アーカイブ",
    }[taskStatus] || taskStatus;
  }
}());
