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

    // POST送信
    $.ajax({
      url: '/tools/shiftersync/check',
      type: 'POST',
      data: formData,
      contentType: false,
      processData: false,
      success: function (response) {
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

    const fileList = document.createElement("div");
    fileList.className = "file-list";

    const { targets, dates, matrix, conflicts } = data;
    const conflictSet = new Set(conflicts.map(c => `${c.date}-${c.name}`));

    targets.forEach((target, idx) => {
      const fileBox = document.createElement("div");
      fileBox.className = "file-box";

      const title = document.createElement("h3");
      title.textContent = target;
      fileBox.appendChild(title);

      dates.forEach(date => {
        const dayRow = document.createElement("div");
        dayRow.className = "day-row";

        const cell = document.createElement("div");
        cell.className = "name-entry";

        const names = matrix[date][idx] || [];

        if (names.length === 0) {
          const empty = document.createElement("div");
          empty.textContent = `${date}日: （情報なし）`;
          cell.appendChild(empty);
        } else {
          const dayLabel = document.createElement("div");
          dayLabel.textContent = `${date}日:`;
          cell.appendChild(dayLabel);

          // 自己重複の検出用セット（名前 → 件数）
          const nameCount = {};
          names.forEach(name => {
            nameCount[name] = (nameCount[name] || 0) + 1;
          });

          names.forEach(name => {
            const nameEl = document.createElement("div");
            nameEl.textContent = name;

            const isConflict = conflictSet.has(`${date}-${name}`);
            const isSelfDuplicate = nameCount[name] > 1;

            if (isConflict) {
              nameEl.classList.add("duplicate"); // 赤
            }
            if (isSelfDuplicate) {
              nameEl.classList.add("self-duplicate"); // 緑
            }

            cell.appendChild(nameEl);
          });
        }

        dayRow.appendChild(cell);
        fileBox.appendChild(dayRow);
      });

      fileList.appendChild(fileBox);
    });

    container.appendChild(fileList);
  }




});
