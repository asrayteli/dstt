$(document).ready(function () {
  $('#checkForm').on('submit', function (e) {
    e.preventDefault();
    const formData = new FormData();
    const files = $('#csv_files')[0].files;

    if (files.length === 0 || files.length > 6) {
      alert("1～6件のCSVファイルを選択してください。");
      return;
    }
    for (let i = 0; i < files.length; i++) {
      formData.append('csv_files', files[i]);
    }

    $.ajax({
      url: '/tools/shiftersync/check',  // 必要に応じて prefix 調整
      type: 'POST',
      data: formData,
      contentType: false,
      processData: false,
      success: function (response) {
        console.log("AJAX success:", response);
        renderCheckResult(response);
      },
      error: function () {
        alert("チェック処理に失敗しました。");
      }
    });
  });

  function renderCheckResult(data) {
    const container = document.getElementById("result-table");
    container.innerHTML = ""; // リセット

    const { targets, capacities, dates, matrix, conflicts, option_mappings } = data;
    
    // 重複セットを作成（エントリー全体ベース）
    const conflictSet = new Set(conflicts.map(c => `${c.date}-${c.entry}`));

    // メインコンテナ作成
    const resultContainer = document.createElement("div");
    resultContainer.className = "shift-result-container";

    // ファイルヘッダー（現場名と台数設定）を横並びで表示
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

    resultContainer.appendChild(fileHeaders);

    // 日付ごとに行を作成
    dates.forEach(date => {
      const dateRow = document.createElement("div");
      dateRow.className = "date-row";

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

        // 日付ラベル
        const dateLabel = document.createElement("div");
        dateLabel.className = "date-label";
        dateLabel.textContent = `${date}日`;
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
          // 自己重複の検出用セット（表示名 → 件数）
          const displayNameCount = {};
          entries.forEach(entry => {
            const displayName = entry.display;
            displayNameCount[displayName] = (displayNameCount[displayName] || 0) + 1;
          });

          entries.forEach(entry => {
            const nameItem = document.createElement("div");
            nameItem.className = "name-item";
            nameItem.textContent = entry.display; // オプション付きの表示名

            // 他現場との重複チェック（エントリー全体でチェック）
            const isConflict = conflictSet.has(`${date}-${entry.original}`);
            // 同一現場内重複チェック（表示名でチェック）
            const isSelfDuplicate = displayNameCount[entry.display] > 1;

            if (isConflict) {
              nameItem.classList.add("duplicate"); // 赤系
            }
            if (isSelfDuplicate) {
              nameItem.classList.add("self-duplicate"); // 緑系
            }

            namesList.appendChild(nameItem);
          });
        }

        siteSection.appendChild(namesList);
        dateRow.appendChild(siteSection);
      });

      resultContainer.appendChild(dateRow);
    });

    // 凡例を追加
    const legend = document.createElement("div");
    legend.className = "legend";
    legend.innerHTML = `
      <h4>凡例:</h4>
      <div class="legend-item">
        <span class="legend-color" style="background-color: #fee2e2; border: 1px solid #fca5a5;"></span>
        他現場との重複
      </div>
      <div class="legend-item">
        <span class="legend-color" style="background-color: #dcfce7; border: 1px solid #86efac;"></span>
        同一現場内重複
      </div>
      <div class="legend-item">
        <span class="legend-color" style="background-color: #fed7aa; border: 1px solid #fdba74;"></span>
        両方の重複
      </div>
      <div class="legend-item">
        <span class="legend-color" style="background-color: #fef3c7; border: 1px solid #fcd34d;"></span>
        未定
      </div>
      <div class="legend-item">
        <span class="legend-color" style="background-color: #fef3c7; border: 1px solid #f59e0b;"></span>
        台数不足
      </div>
    `;
    resultContainer.appendChild(legend);

    container.appendChild(resultContainer);
  }
});