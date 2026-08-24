$(document).ready(function () {
  // ファイル選択時のカウント表示
  $('#csv_files').on('change', function() {
    const fileCount = this.files.length;
    if (fileCount > 0) {
      $('#file-count').show();
      $('#selected-count').text(fileCount);
    } else {
      $('#file-count').hide();
    }
  });

  $('#checkForm').on('submit', function (e) {
    e.preventDefault();
    const formData = new FormData();
    const files = $('#csv_files')[0].files;

    if (files.length === 0) {
      alert("CSVファイルを選択してください。");
      return;
    }

    // ファイル数制限を撤廃
    for (let i = 0; i < files.length; i++) {
      formData.append('csv_files', files[i]);
    }

    // ローディング表示
    $('#checkBtn').text('処理中...').prop('disabled', true);

    $.ajax({
      url: '/tools/shiftersync/check',
      type: 'POST',
      data: formData,
      contentType: false,
      processData: false,
      success: function (response) {
        console.log("AJAX success:", response);
        renderCheckResult(response);
        $('#checkBtn').text('チェック開始').prop('disabled', false);
      },
      error: function () {
        alert("チェック処理に失敗しました。");
        $('#checkBtn').text('チェック開始').prop('disabled', false);
      }
    });
  });

  const WEEKDAY_LABELS = ["日", "月", "火", "水", "木", "金", "土"];

  function renderCheckResult(data) {
    const container = document.getElementById("result-table");
    container.innerHTML = ""; // リセット

    const { year, month, targets, capacities, dates, matrix, conflicts, same_site_conflicts, option_mappings, total_files } = data;
    const leaveWorkConflicts = data.leave_work_conflicts || [];
    const vehicleConflicts = data.vehicle_conflicts || [];
    
    // 重複セットを作成
    const conflictSet = new Set(conflicts.map(c => `${c.date}-${c.entry}`));
    const sameSiteConflictSet = new Set(same_site_conflicts.map(c => `${c.date}-${c.entry}-${c.file_index}`));
    const leaveWorkConflictSet = new Set(leaveWorkConflicts.map(c => `${c.date}-${c.entry}-${c.file_index == null ? 'cross' : c.file_index}`));
    const vehicleConflictSet = new Set(vehicleConflicts.map(c => `${c.date}-${c.entry}-${c.file_index}`));

    // メインコンテナ作成
    const resultContainer = document.createElement("div");
    resultContainer.className = "shift-result-container";

    // スクロール可能な横並びコンテナを作成
    const scrollableContainer = document.createElement("div");
    scrollableContainer.className = "scrollable-container";

    // ファイルヘッダー（現場名と台数設定）をスクロール可能な横並びで表示
    const fileHeaders = document.createElement("div");
    fileHeaders.className = "file-headers";

    targets.forEach((target, index) => {
      const header = document.createElement("div");
      header.className = "file-header";
      
      let headerText = target;
      if (capacities[index] !== null && capacities[index] !== undefined) {
        headerText += ` (必要: ${capacities[index]}人/日)`;
      }
      
      header.textContent = headerText;
      fileHeaders.appendChild(header);
    });

    scrollableContainer.appendChild(fileHeaders);

    // 日付ごとに行を作成（スクロール対応）
    dates.forEach(date => {
      const dateRow = document.createElement("div");
      dateRow.className = "date-row";

      let weekdayIdx = null;
      if (year && month) {
        const dateObj = new Date(year, month - 1, date);
        weekdayIdx = dateObj.getDay();
      }
      const weekdayLabel = weekdayIdx !== null ? WEEKDAY_LABELS[weekdayIdx] : "";
      if (weekdayIdx === 6) {
        dateRow.classList.add("is-saturday");
      } else if (weekdayIdx === 0) {
        dateRow.classList.add("is-sunday");
      }

      targets.forEach((target, targetIdx) => {
        const siteSection = document.createElement("div");
        siteSection.className = "site-section";

        // 台数不足の場合は背景色を変更
        const entries = matrix[date][targetIdx] || [];
        const currentCount = entries.length;
        const requiredCapacity = capacities[targetIdx];

        if (requiredCapacity !== null && requiredCapacity !== undefined && currentCount < requiredCapacity) {
          siteSection.classList.add("capacity-warning");
        }

        if (weekdayIdx === 6) {
          siteSection.classList.add("is-saturday");
        } else if (weekdayIdx === 0) {
          siteSection.classList.add("is-sunday");
        }

        // 日付ラベル
        const dateLabel = document.createElement("div");
        dateLabel.className = "date-label";
        dateLabel.textContent = weekdayLabel ? `${date}日 (${weekdayLabel})` : `${date}日`;
        siteSection.appendChild(dateLabel);

        // 名前リスト
        const namesList = document.createElement("div");
        namesList.className = "names-list";

        if (entries.length === 0) {
          // データがない場合は「未定」表示
          const undefinedItem = document.createElement("div");
          undefinedItem.className = "name-item undefined";
          undefinedItem.textContent = "未定";
          namesList.appendChild(undefinedItem);
        } else {
          entries.forEach(entry => {
            const nameItem = document.createElement("div");
            nameItem.className = "name-item";
            nameItem.textContent = entry.display; // オプション付きの表示名

            // 新しい重複判定ロジック
            const isConflict = conflictSet.has(`${date}-${entry.original}`);
            const isSameSiteConflict = sameSiteConflictSet.has(`${date}-${entry.original}-${targetIdx}`);
            const isLeaveWorkConflict = leaveWorkConflictSet.has(`${date}-${entry.original}-${targetIdx}`)
              || leaveWorkConflictSet.has(`${date}-${entry.original}-cross`);
            const isVehicleConflict = vehicleConflictSet.has(`${date}-${entry.original}-${targetIdx}`);

            if (isConflict) {
              nameItem.classList.add("duplicate"); // 他現場との重複（赤系）
            }
            if (isSameSiteConflict) {
              nameItem.classList.add("self-duplicate"); // 同一現場内重複（緑系）
            }
            if (isLeaveWorkConflict) {
              nameItem.classList.add("leave-work-conflict");
              nameItem.title = "休みと勤務が同日";
            }
            if (isVehicleConflict) {
              nameItem.classList.add("vehicle-conflict");
              nameItem.title = "車両の重複";
            }
            // 両方の重複がある場合は自動的にオレンジ系になる

            namesList.appendChild(nameItem);
          });
        }

        siteSection.appendChild(namesList);
        dateRow.appendChild(siteSection);
      });

      scrollableContainer.appendChild(dateRow);
    });

    resultContainer.appendChild(scrollableContainer);

    // ファイル数情報を表示
    const fileCountInfo = document.createElement("div");
    fileCountInfo.className = "file-count-info";
    fileCountInfo.innerHTML = `
      <div class="text-center mb-4 p-3 bg-blue-50 rounded-lg">
        <span class="text-blue-700 font-semibold">比較対象: ${total_files}ファイル</span>
        ${total_files > 6 ? '<span class="text-sm text-gray-600 ml-2">(横スクロールで全体を確認できます)</span>' : ''}
      </div>
    `;
    resultContainer.insertBefore(fileCountInfo, scrollableContainer);

    // 改善された重複統計を表示
    const conflictStats = document.createElement("div");
    conflictStats.className = "conflict-statistics";
    conflictStats.innerHTML = `
      <div class="text-center mb-4 p-3 bg-yellow-50 rounded-lg border border-yellow-200">
        <div class="text-yellow-800 font-semibold mb-2">重複検出結果</div>
        <div class="flex justify-center gap-6 text-sm">
          <span class="text-red-600">人の重複（帳簿間）: ${conflicts.length}件</span>
          <span class="text-green-600">人の重複（帳簿内）: ${same_site_conflicts.length}件</span>
          <span class="text-amber-700">休みと勤務が同日: ${leaveWorkConflicts.length}件</span>
          <span class="text-purple-700">車両の重複: ${vehicleConflicts.length}件</span>
        </div>
      </div>
    `;
    resultContainer.insertBefore(conflictStats, scrollableContainer);

    // 更新された凡例を追加
    const legend = document.createElement("div");
    legend.className = "legend";
    legend.innerHTML = `
      <h4>凡例:</h4>
      <div class="legend-item">
        <span class="legend-color" style="background-color: #fee2e2; border: 1px solid #fca5a5;"></span>
        人の重複（帳簿間）
      </div>
      <div class="legend-item">
        <span class="legend-color" style="background-color: #dcfce7; border: 1px solid #86efac;"></span>
        人の重複（帳簿内）
      </div>
      <div class="legend-item">
        <span class="legend-color" style="background-color: #fed7aa; border: 1px solid #fdba74;"></span>
        両方の重複
      </div>
      <div class="legend-item">
        <span class="legend-color" style="background-color: #fffbeb; border: 1px solid #f59e0b;"></span>
        休みと勤務が同日
      </div>
      <div class="legend-item">
        <span class="legend-color" style="background-color: #f5f3ff; border: 1px solid #a78bfa;"></span>
        車両の重複
      </div>
      <div class="legend-item">
        <span class="legend-color" style="background-color: #fef3c7; border: 1px solid #fcd34d;"></span>
        未定
      </div>
      <div class="legend-item">
        <span class="legend-color" style="background-color: #fef3c7; border: 1px solid #f59e0b;"></span>
        台数不足
      </div>
      <div class="legend-help">
        <strong>重複判定ルール:</strong>
        <div class="rule-tables">
          <!-- 時間オプション表 -->
          <div class="rule-table-container">
            <h5>時間オプション（優先判定）</h5>
            <table class="rule-table">
              <thead>
                <tr><th>vs</th><th>午前</th><th>午後</th><th>早番</th><th>遅番</th></tr>
              </thead>
              <tbody>
                <tr><td><strong>午前</strong></td><td class="duplicate">●</td><td class="no-duplicate">×</td><td class="duplicate">●</td><td class="no-duplicate">×</td></tr>
                <tr><td><strong>午後</strong></td><td class="no-duplicate">×</td><td class="duplicate">●</td><td class="no-duplicate">×</td><td class="duplicate">●</td></tr>
                <tr><td><strong>早番</strong></td><td class="duplicate">●</td><td class="no-duplicate">×</td><td class="duplicate">●</td><td class="no-duplicate">×</td></tr>
                <tr><td><strong>遅番</strong></td><td class="no-duplicate">×</td><td class="duplicate">●</td><td class="no-duplicate">×</td><td class="duplicate">●</td></tr>
              </tbody>
            </table>
          </div>

          <div class="rule-table-container">
            <h5>独立した警告</h5>
            <table class="rule-table special-rules">
              <tbody>
                <tr><td><strong>休みと勤務が同日</strong></td><td class="note">人の重複とは別枠で警告</td></tr>
                <tr><td><strong>車両の重複</strong></td><td class="note">同一現場・同一号車・重なる時間帯で警告</td></tr>
              </tbody>
            </table>
          </div>

          <!-- 特別ルール -->
          <div class="rule-table-container">
            <h5>特別ルール</h5>
            <table class="rule-table special-rules">
              <tbody>
                <tr><td><strong>時間帯なし</strong></td><td class="duplicate">終日扱いで、すべての勤務時間帯と重複 ●</td></tr>
                <tr><td><strong>車種・号車</strong></td><td class="note">人の重複判定には使用しない</td></tr>
                <tr><td><strong>代務・研修</strong></td><td class="note">人の重複判定には影響しない</td></tr>
                <tr><td><strong>休暇</strong></td><td class="note">人の重複対象外。勤務との同日は別枠警告</td></tr>
              </tbody>
            </table>
          </div>
        </div>
        <div class="rule-note">
          <strong>●</strong>=重複あり　<strong>×</strong>=重複なし
        </div>
      </div>
    `;
    resultContainer.appendChild(legend);

    container.appendChild(resultContainer);
  }
});
