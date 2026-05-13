/* PowerImager - ModeSwitcher: Normal / PRO mode */
window.PIModeSwitcher = (function () {
  let isPro = false;

  function init() {
    isPro = localStorage.getItem('pi-advanced-mode') === 'true' || localStorage.getItem('pi-pro-mode') === 'true';
    apply();

    const toggle = document.getElementById('mode-toggle-input');
    if (toggle) {
      toggle.checked = isPro;
      toggle.addEventListener('change', () => {
        isPro = toggle.checked;
        localStorage.setItem('pi-pro-mode', isPro);
        localStorage.setItem('pi-advanced-mode', isPro);
        apply();
      });
    }
  }

  function apply() {
    document.body.classList.toggle('advanced-mode', isPro);
    document.body.classList.toggle('pro-mode', isPro);
    PIStatusbar.setMode(isPro ? 'PRO' : '通常');
    PIEventBus.emit('mode:changed', isPro);
  }

  function getMode() { return isPro ? 'pro' : 'normal'; }

  return { init, getMode };
})();
