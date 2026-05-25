(function () {
  const status = document.getElementById("status");
  const enableButton = document.getElementById("enable");

  document.addEventListener("DOMContentLoaded", () => {
    enableButton.addEventListener("click", enableNotifications);
    startMonitor();
  });

  async function enableNotifications() {
    if (!("Notification" in window)) {
      setStatus("このブラウザではWindows通知を利用できません。");
      return;
    }
    const permission = await Notification.requestPermission();
    if (permission === "granted") {
      setStatus("通知待受中です。このウィンドウは最小化できます。");
      await checkDueTasks();
    } else {
      setStatus("通知が許可されませんでした。Windowsまたはブラウザの通知設定を確認してください。");
    }
  }

  function startMonitor() {
    if (!("Notification" in window)) {
      setStatus("通知に対応していないブラウザです。");
      return;
    }
    if (Notification.permission === "granted") {
      setStatus("通知待受中です。このウィンドウは最小化できます。");
      checkDueTasks();
    } else {
      setStatus("通知を使うには「通知を有効化」を押してください。");
    }
    window.setInterval(checkDueTasks, 60000);
  }

  async function checkDueTasks() {
    if (!("Notification" in window) || Notification.permission !== "granted") return;
    const response = await fetch("/tools/to_bell/api/notifications/due-tasks");
    if (!response.ok) return;
    const data = await response.json();
    for (const task of data.tasks || []) {
      if (!task.due_at) continue;
      const key = `toBellNotified:${task.id}:${task.due_at}`;
      if (localStorage.getItem(key)) continue;
      localStorage.setItem(key, new Date().toISOString());
      const notification = new Notification(`To Bell: ${task.title}`, {
        body: `${formatDue(task.due_at)} / ${statusLabel(task.status)}`,
        tag: key,
      });
      notification.onclick = () => {
        window.open(`/tools/to_bell?task=${task.id}`, "toBellMain");
      };
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
