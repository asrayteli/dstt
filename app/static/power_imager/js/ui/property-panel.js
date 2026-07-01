/* PowerImager — PropertyPanel: プロパティパネル */
window.PIPropertyPanel = (function () {
  let container;

  function init() {
    container = document.getElementById('property-panel-body');
    PIEventBus.on('tool:activated', () => render());
    PIEventBus.on('tool:properties-changed', () => render());
    // アクティブレイヤーの切替に追従（選択した図形/テキストのプロパティを表示）
    PIEventBus.on('layers:changed', () => render());
  }

  function render() {
    if (!container) return;
    container.innerHTML = '';
    const tool = PIToolbar.getCurrentTool();
    if (!tool) return;
    const props = tool.getProperties();
    if (!props) return;

    const title = document.createElement('div');
    title.style.cssText = 'font-weight:700;font-size:13px;margin-bottom:8px;color:var(--pi-text);';
    title.textContent = props.title;
    container.appendChild(title);

    props.fields.forEach(field => {
      const div = document.createElement('div');
      div.className = 'prop-field';
      if (field.advancedOnly) div.classList.add('advanced-only');

      const label = document.createElement('label');
      label.textContent = field.label;

      if (field.type === 'slider') {
        div.appendChild(label);
        const row = document.createElement('div');
        row.className = 'slider-row';
        const slider = document.createElement('input');
        slider.type = 'range';
        slider.min = field.min; slider.max = field.max;
        slider.value = field.value;
        const valSpan = document.createElement('span');
        valSpan.className = 'slider-value';
        valSpan.textContent = field.value + (field.unit || '');
        slider.addEventListener('input', () => {
          valSpan.textContent = slider.value + (field.unit || '');
          if (field.onChange) field.onChange(slider.value);
        });
        row.appendChild(slider);
        row.appendChild(valSpan);
        div.appendChild(row);
      } else if (field.type === 'select') {
        div.appendChild(label);
        const sel = document.createElement('select');
        (field.options || []).forEach(opt => {
          const o = document.createElement('option');
          o.value = opt.value; o.textContent = opt.label;
          if (opt.value === field.value) o.selected = true;
          sel.appendChild(o);
        });
        sel.addEventListener('change', () => { if (field.onChange) field.onChange(sel.value); });
        div.appendChild(sel);
      } else if (field.type === 'color') {
        div.appendChild(label);
        const input = document.createElement('input');
        input.type = 'color'; input.value = field.value;
        input.addEventListener('input', () => { if (field.onChange) field.onChange(input.value); });
        div.appendChild(input);
      } else if (field.type === 'number') {
        div.appendChild(label);
        const input = document.createElement('input');
        input.type = 'number'; input.value = field.value;
        if (field.min !== undefined) input.min = field.min;
        if (field.max !== undefined) input.max = field.max;
        input.addEventListener('input', () => { if (field.onChange) field.onChange(input.value); });
        div.appendChild(input);
      } else if (field.type === 'checkbox') {
        const row = document.createElement('div');
        row.className = 'checkbox-row';
        const cb = document.createElement('input');
        cb.type = 'checkbox'; cb.checked = field.value;
        cb.addEventListener('change', () => { if (field.onChange) field.onChange(cb.checked); });
        const lbl = document.createElement('span');
        lbl.textContent = field.label;
        row.appendChild(cb); row.appendChild(lbl);
        div.appendChild(row);
      } else if (field.type === 'button') {
        const btn = document.createElement('button');
        btn.className = 'modal-btn modal-btn-primary';
        btn.style.cssText = 'width:100%;margin-top:4px;';
        btn.textContent = field.label;
        btn.addEventListener('click', () => { if (field.onClick) field.onClick(); });
        div.appendChild(btn);
      } else if (field.type === 'toggle-group') {
        div.appendChild(label);
        const group = document.createElement('div');
        group.className = 'toggle-group';
        field.toggles.forEach(t => {
          const btn = document.createElement('button');
          btn.className = 'toggle-btn' + (t.active ? ' active' : '');
          btn.textContent = t.label;
          btn.addEventListener('click', () => {
            btn.classList.toggle('active');
            if (t.onChange) t.onChange(btn.classList.contains('active'));
          });
          group.appendChild(btn);
        });
        div.appendChild(group);
      } else if (field.type === 'button-group') {
        div.appendChild(label);
        const group = document.createElement('div');
        group.className = 'button-group';
        field.buttons.forEach(b => {
          const btn = document.createElement('button');
          btn.className = b.active ? 'active' : '';
          btn.textContent = b.label;
          btn.addEventListener('click', () => {
            if (b.onClick) b.onClick();
            PIEventBus.emit('tool:properties-changed');
          });
          group.appendChild(btn);
        });
        div.appendChild(group);
      } else if (field.type === 'preset-list') {
        div.appendChild(label);
        const list = document.createElement('div');
        list.className = 'preset-list';
        field.presets.forEach(p => {
          const btn = document.createElement('button');
          btn.className = 'preset-btn';
          btn.textContent = p.label;
          btn.addEventListener('click', () => {
            if (p.onClick) p.onClick();
            PIEventBus.emit('tool:properties-changed');
          });
          list.appendChild(btn);
        });
        div.appendChild(list);
      }

      container.appendChild(div);
    });
  }

  return { init, render };
})();
