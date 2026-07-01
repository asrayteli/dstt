/* PowerImager — Modal: ダイアログ/モーダル共通 */
window.PIModal = (function () {
  let overlay, container;
  let currentConfig = null;

  function init() {
    overlay = document.getElementById('modal-overlay');
    container = document.getElementById('active-modal');
  }

  function show(config) {
    currentConfig = config;
    if (!overlay || !container) init();
    container.innerHTML = '';

    const h2 = document.createElement('h2');
    h2.textContent = config.title;
    container.appendChild(h2);

    if (config.description) {
      const p = document.createElement('p');
      p.textContent = config.description;
      container.appendChild(p);
    }

    const values = {};
    const inputRefs = {};

    if (config.content) {
      config.content.forEach(field => {
        values[field.key] = field.value;
        const group = document.createElement('div');
        group.className = 'adj-slider-group';

        if (field.type === 'slider') {
          const labelRow = document.createElement('div');
          labelRow.className = 'adj-slider-label';
          const lbl = document.createElement('span');
          lbl.textContent = field.label;
          const val = document.createElement('span');
          val.textContent = field.value;
          labelRow.appendChild(lbl);
          labelRow.appendChild(val);
          group.appendChild(labelRow);

          const slider = document.createElement('input');
          slider.type = 'range';
          slider.className = 'adj-slider';
          slider.min = field.min;
          slider.max = field.max;
          slider.value = field.value;
          slider.addEventListener('input', () => {
            val.textContent = slider.value;
            values[field.key] = parseFloat(slider.value);
            if (config.onPreview) config.onPreview(values);
          });
          group.appendChild(slider);
          inputRefs[field.key] = { el: slider, valLabel: val };
        } else if (field.type === 'text') {
          const fg = document.createElement('div');
          fg.className = 'modal-form-group';
          const lbl = document.createElement('label');
          lbl.textContent = field.label;
          const input = document.createElement('input');
          input.type = 'text';
          input.value = field.value || '';
          input.addEventListener('input', () => { values[field.key] = input.value; });
          fg.appendChild(lbl);
          fg.appendChild(input);
          group.appendChild(fg);
          inputRefs[field.key] = { el: input };
        } else if (field.type === 'number') {
          const fg = document.createElement('div');
          fg.className = 'modal-form-group';
          const lbl = document.createElement('label');
          lbl.textContent = field.label;
          const input = document.createElement('input');
          input.type = 'number';
          input.value = field.value || 0;
          input.min = field.min || 0;
          input.max = field.max || 99999;
          input.addEventListener('input', () => { values[field.key] = parseInt(input.value) || 0; });
          fg.appendChild(lbl);
          fg.appendChild(input);
          group.appendChild(fg);
          inputRefs[field.key] = { el: input };
        } else if (field.type === 'select') {
          const fg = document.createElement('div');
          fg.className = 'modal-form-group';
          const lbl = document.createElement('label');
          lbl.textContent = field.label;
          const sel = document.createElement('select');
          (field.options || []).forEach(opt => {
            const o = document.createElement('option');
            o.value = opt.value;
            o.textContent = opt.label;
            if (opt.value === field.value) o.selected = true;
            sel.appendChild(o);
          });
          sel.addEventListener('change', () => { values[field.key] = sel.value; if (config.onPreview) config.onPreview(values); });
          fg.appendChild(lbl);
          fg.appendChild(sel);
          group.appendChild(fg);
          inputRefs[field.key] = { el: sel };
        } else if (field.type === 'color') {
          const fg = document.createElement('div');
          fg.className = 'modal-form-group';
          const lbl = document.createElement('label');
          lbl.textContent = field.label;
          const input = document.createElement('input');
          input.type = 'color';
          input.value = field.value || '#000000';
          input.addEventListener('input', () => { values[field.key] = input.value; if (config.onPreview) config.onPreview(values); });
          fg.appendChild(lbl);
          fg.appendChild(input);
          group.appendChild(fg);
          inputRefs[field.key] = { el: input };
        } else if (field.type === 'custom') {
          if (typeof field.render === 'function') field.render(group, values, config);
          inputRefs[field.key] = { el: group, custom: true };
        } else if (field.type === 'checkbox') {
          const row = document.createElement('label');
          row.className = 'modal-check-row';
          const cb = document.createElement('input');
          cb.type = 'checkbox';
          cb.checked = !!field.value;
          cb.addEventListener('change', () => { values[field.key] = cb.checked; if (config.onPreview) config.onPreview(values); });
          const span = document.createElement('span');
          span.textContent = field.label;
          row.appendChild(cb);
          row.appendChild(span);
          group.appendChild(row);
          inputRefs[field.key] = { el: cb, check: true };
        }

        container.appendChild(group);
      });
    }

    if (config.sizePresets) {
      const presetDiv = document.createElement('div');
      presetDiv.className = 'size-presets';
      config.sizePresets.forEach(p => {
        const btn = document.createElement('button');
        btn.className = 'size-preset-btn';
        btn.textContent = p.label;
        btn.onclick = () => {
          p.onClick(values);
          Object.keys(values).forEach(key => {
            const ref = inputRefs[key];
            if (ref && ref.el) {
              ref.el.value = values[key];
              if (ref.valLabel) ref.valLabel.textContent = values[key];
            }
          });
        };
        presetDiv.appendChild(btn);
      });
      container.appendChild(presetDiv);
    }

    const actions = document.createElement('div');
    actions.className = 'modal-actions';

    const cancelBtn = document.createElement('button');
    cancelBtn.className = 'modal-btn modal-btn-secondary';
    cancelBtn.textContent = 'キャンセル';
    cancelBtn.onclick = () => { hide(); if (config.onCancel) config.onCancel(); };

    const confirmBtn = document.createElement('button');
    confirmBtn.className = 'modal-btn modal-btn-primary';
    confirmBtn.textContent = config.confirmLabel || '適用';
    confirmBtn.onclick = () => { hide(); if (config.onConfirm) config.onConfirm(values); };

    actions.appendChild(cancelBtn);
    actions.appendChild(confirmBtn);
    container.appendChild(actions);

    overlay.classList.remove('hidden');

    overlay.onclick = (e) => { if (e.target === overlay) { hide(); if (config.onCancel) config.onCancel(); } };
  }

  function hide() {
    if (overlay) overlay.classList.add('hidden');
    currentConfig = null;
  }

  function isOpen() { return currentConfig !== null; }

  return { init, show, hide, isOpen };
})();
