/* PowerImager — ModeSwitcher: 初級/上級モード */
window.PIModeSwitcher = (function () {
  let isAdvanced = false;

  function init() {
    isAdvanced = localStorage.getItem('pi-advanced-mode') === 'true';
    apply();

    const toggle = document.getElementById('mode-toggle-input');
    if (toggle) {
      toggle.checked = isAdvanced;
      toggle.addEventListener('change', () => {
        isAdvanced = toggle.checked;
        localStorage.setItem('pi-advanced-mode', isAdvanced);
        apply();
      });
    }
  }

  function apply() {
    document.body.classList.toggle('advanced-mode', isAdvanced);
    PIStatusbar.setMode(isAdvanced ? '上級' : '初級');
    PIEventBus.emit('mode:changed', isAdvanced);
  }

  function getMode() { return isAdvanced ? 'advanced' : 'beginner'; }

  return { init, getMode };
})();
