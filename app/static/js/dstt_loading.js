(function() {
  'use strict';

  let activeCount = 0;
  let overlay = null;

  function ensureOverlay() {
    if (overlay) return overlay;
    overlay = document.createElement('div');
    overlay.className = 'dstt-loading-overlay';
    overlay.setAttribute('hidden', '');
    overlay.setAttribute('role', 'status');
    overlay.setAttribute('aria-live', 'polite');
    overlay.innerHTML = [
      '<div class="dstt-loading-panel">',
      '<div class="dstt-loading-spinner" aria-hidden="true"></div>',
      '<p class="dstt-loading-message" data-dstt-loading-message>Loading...</p>',
      '<p class="dstt-loading-detail" data-dstt-loading-detail></p>',
      '<div class="dstt-loading-bar" aria-hidden="true"></div>',
      '</div>'
    ].join('');
    document.body.appendChild(overlay);
    return overlay;
  }

  function setText(message, detail) {
    const node = ensureOverlay();
    const messageNode = node.querySelector('[data-dstt-loading-message]');
    const detailNode = node.querySelector('[data-dstt-loading-detail]');
    if (messageNode) messageNode.textContent = message || 'Loading...';
    if (detailNode) {
      detailNode.textContent = detail || '';
      detailNode.hidden = !detail;
    }
  }

  function show(options) {
    const settings = typeof options === 'string' ? { message: options } : (options || {});
    activeCount += 1;
    const node = ensureOverlay();
    setText(settings.message, settings.detail);
    node.removeAttribute('hidden');
    document.body.classList.add('dstt-loading-active');
    return function close() {
      hide();
    };
  }

  function hide() {
    activeCount = Math.max(0, activeCount - 1);
    if (activeCount > 0 || !overlay) return;
    overlay.setAttribute('hidden', '');
    document.body.classList.remove('dstt-loading-active');
  }

  async function during(options, task) {
    const close = show(options);
    try {
      return await task();
    } finally {
      close();
    }
  }

  window.DSTTLoading = {
    show,
    hide,
    during,
    setText
  };
})();
