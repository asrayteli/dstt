$(document).ready(function() {
  let mode = 'scene';

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function renderEditorShell(year, month, name, capacityEnabled, requiredCapacity) {
    const safeName = escapeHtml(name);
    return `
      <section class="ss-section">
        <div class="ss-section-head">
          <div>
            <h2>編集画面</h2>
            <p class="ss-section-note">コメントは各シフトの下に短く表示され、項目をクリックすると詳細を確認できます。</p>
          </div>
          <div class="ss-actions">
            <button type="button" id="saveBtn" class="btn-primary">CSV を保存</button>
          </div>
        </div>

        <div class="ss-summary-grid" style="margin-bottom: 18px;">
          <div class="ss-summary-item">
            <div class="ss-summary-label">種別</div>
            <div class="ss-summary-value">${mode === 'scene' ? '現場シフト' : '個人シフト'}</div>
          </div>
          <div class="ss-summary-item">
            <div class="ss-summary-label">対象</div>
            <div class="ss-summary-value">${safeName}</div>
          </div>
          <div class="ss-summary-item">
            <div class="ss-summary-label">年月</div>
            <div class="ss-summary-value">${year}年${month}月</div>
          </div>
          <div class="ss-summary-item">
            <div class="ss-summary-label">必要人数</div>
            <div class="ss-summary-value">${capacityEnabled ? `${requiredCapacity}人` : '未設定'}</div>
          </div>
        </div>

        <div class="weekday-header">
          <div>月</div>
          <div>火</div>
          <div>水</div>
          <div>木</div>
          <div>金</div>
          <div>土</div>
          <div>日</div>
        </div>
        <div id="shiftGrid" class="calendar-grid"></div>
      </section>
    `;
  }

  function startEditor() {
    const year = parseInt($('#year').val(), 10);
    const month = parseInt($('#month').val(), 10);
    const name = $('#target_name').val().trim();
    const capacityEnabled = $('#enableCapacity').is(':checked');
    const requiredCapacity = parseInt($('#capacity').val(), 10) || 0;

    if (!year || !month || !name) {
      alert('年、月、名称を入力してください');
      return;
    }
    if (month < 1 || month > 12) {
      alert('月は1から12で入力してください');
      return;
    }
    if (capacityEnabled && requiredCapacity <= 0) {
      alert('必要人数を設定する場合は、1以上の値を入力してください');
      return;
    }

    ShifterSync.setState('mode', mode);
    ShifterSync.setState('name', name);
    ShifterSync.setState('capacityEnabled', capacityEnabled);
    ShifterSync.setState('requiredCapacity', requiredCapacity);

    $('#inputArea')
      .html(renderEditorShell(year, month, name, capacityEnabled, requiredCapacity))
      .stop(true, true)
      .fadeIn(180);

    ShifterSync.buildCalendar(year, month, mode);

    $('html, body').animate({
      scrollTop: $('#inputArea').offset().top - 20
    }, 280);
  }

  function downloadCsv(filename) {
    const csv = ShifterSync.buildCSV();
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const link = $('<a>')
      .attr('href', url)
      .attr('download', filename)
      .appendTo('body');

    link[0].click();
    link.remove();
    URL.revokeObjectURL(url);
  }

  $('#mode').on('change', function() {
    mode = $(this).val();
    $('#nameLabel').text(mode === 'scene' ? '現場名' : '個人名');
  });

  $('#enableCapacity').on('change', function() {
    const enabled = $(this).is(':checked');
    $('#capacityInputGroup').stop(true, true)[enabled ? 'slideDown' : 'slideUp'](120);
    $('#capacity').prop('required', enabled);
    if (!enabled) {
      $('#capacity').val('');
    }
  });

  $('#startBtn').on('click', startEditor);

  $('#shiftMetaForm').on('keydown', 'input, select', function(e) {
    if (e.key === 'Enter') {
      e.preventDefault();
      startEditor();
    }
  });

  $(document).on('click', '#saveBtn', function() {
    const modeValue = ShifterSync.getState('mode');
    const year = ShifterSync.getState('year');
    const month = ShifterSync.getState('month');
    const name = ShifterSync.getState('name');
    downloadCsv(`${modeValue},${year},${month},${name}.csv`);
  });

  const now = new Date();
  $('#year').val(now.getFullYear());
  $('#month').val(now.getMonth() + 1);
});
