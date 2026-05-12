/* PowerImager — EventBus: モジュール間通信 */
window.PIEventBus = (function () {
  const listeners = {};

  function on(event, fn) {
    if (!listeners[event]) listeners[event] = [];
    listeners[event].push(fn);
  }

  function off(event, fn) {
    if (!listeners[event]) return;
    listeners[event] = listeners[event].filter(f => f !== fn);
  }

  function emit(event, data) {
    if (!listeners[event]) return;
    listeners[event].forEach(fn => { try { fn(data); } catch (e) { console.error('EventBus error:', event, e); } });
  }

  function once(event, fn) {
    const wrapper = (data) => { off(event, wrapper); fn(data); };
    on(event, wrapper);
  }

  return { on, off, emit, once };
})();
