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

    const { targets, dates, matrix, conflicts } = data;
    const conflictSet = new Set(conflicts.map(c => `${c.date}-${c.name}`));

    // メインコンテナ作成
    const resultContainer = document.createElement("div");
    resultContainer.className = "shift-result-container";

    // ファイルヘッダー（現場名）を横並びで表示
    const fileHeaders = document.createElement("div");
    fileHeaders.className = "file-headers";

    targets.forEach(target => {
      const header = document.createElement("div");
      header.className = "file-header";
      header.textContent = target;
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

        // 日付ラベル
        const dateLabel = document.createElement("div");
        dateLabel.className = "date-label";
        dateLabel.textContent = `${date}日`;
        siteSection.appendChild(dateLabel);

        // 名前リスト
        const namesList = document.createElement("div");
        namesList.className = "names-list";

        const names = matrix[date][targetIdx] || [];

        if (names.length === 0) {
          // 空白の場合
          const emptyItem = document.createElement("div");
          emptyItem.className = "name-item empty";
          emptyItem.textContent = "（空白）";
          namesList.appendChild(emptyItem);
        } else {
          // 自己重複の検出用セット（名前 → 件数）
          const nameCount = {};
          names.forEach(name => {
            nameCount[name] = (nameCount[name] || 0) + 1;
          });

          names.forEach(name => {
            const nameItem = document.createElement("div");
            nameItem.className = "name-item";
            nameItem.textContent = name;

            const isConflict = conflictSet.has(`${date}-${name}`);
            const isSelfDuplicate = nameCount[name] > 1;

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
        シフト未登録
      </div>
    `;
    resultContainer.appendChild(legend);

    container.appendChild(resultContainer);
  }
});