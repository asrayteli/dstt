// グローバル変数
let currentCalendarId = null;
let currentYear = new Date().getFullYear();
let currentMonth = new Date().getMonth() + 1;
let calendarData = null;
let confirmCallback = null;
let currentEditingLeave = null; // 編集中の休暇情報を保持
let userNameCache = {}; // ユーザー名キャッシュ
let employeeSearchTimeout = null; // 社員検索のデバウンス用

// 初期化
document.addEventListener('DOMContentLoaded', function() {
    console.log('Initializing leave manager...');
    
    // 全モーダルを確実に非表示に
    const modals = document.querySelectorAll('.modal-overlay');
    console.log('Found modals:', modals.length);
    modals.forEach((modal, index) => {
        const classList = modal.classList;
        if (!classList.contains('hidden')) {
            classList.add('hidden');
            console.log(`Modal ${index} (${modal.id}) hidden`);
        }
        modal.style.display = 'none';
    });
    
    // カレンダー選択の変更イベント
    const calendarSelect = document.getElementById('calendar-select');
    if (calendarSelect) {
        calendarSelect.addEventListener('change', function() {
            currentCalendarId = this.value;
            if (currentCalendarId) {
                document.getElementById('add-leave-btn').disabled = false;
                document.getElementById('bulk-register-btn').disabled = false;
                loadCalendar();
            } else {
                document.getElementById('add-leave-btn').disabled = true;
                document.getElementById('bulk-register-btn').disabled = true;
                document.getElementById('calendar-container').innerHTML = '<div class="text-center text-gray-500 py-8">カレンダーを選択してください</div>';
            }
        });
    }
    
    // 月選択の初期値設定
    updateMonthPicker();
    
    // 休暇登録フォームのサブミットイベント
    const leaveForm = document.getElementById('leave-form');
    if (leaveForm) {
        leaveForm.addEventListener('submit', function(e) {
            e.preventDefault();
            saveLeave();
        });
    }
    
    // 休暇種類選択肢を初期化
    initializeLeaveTypeOptions();

    // 入力モード切り替え
    const inputModeAuto = document.getElementById('input-mode-auto');
    const inputModeManual = document.getElementById('input-mode-manual');
    if (inputModeAuto && inputModeManual) {
        inputModeAuto.addEventListener('change', () => toggleInputMode('auto'));
        inputModeManual.addEventListener('change', () => toggleInputMode('manual'));
    }

    // 名前入力時のリアルタイム検索
    const leaveNameInput = document.getElementById('leave-name');
    if (leaveNameInput) {
        leaveNameInput.addEventListener('input', function() {
            const inputModeElement = document.querySelector('input[name="input-mode"]:checked');
            if (inputModeElement && inputModeElement.value === 'auto') {
                searchEmployees(this.value);
            }
        });
    }

    console.log('Leave manager initialized');
});

// ユーザー名をキャッシュから取得（フォールバック付き）
function getUserDisplayName(username) {
    if (!username) return '不明';
    
    // キャッシュから取得
    if (userNameCache[username]) {
        return userNameCache[username];
    }
    
    // キャッシュにない場合は非同期で取得してキャッシュに保存
    fetchUserName(username);
    
    // とりあえずusernameを返す（後でDOM更新される）
    return username;
}

// ユーザー名を非同期で取得してキャッシュに保存
function fetchUserName(username) {
    if (!username || userNameCache[username]) return;
    
    fetch(`/tools/leave_mgr/api/user/${username}/name`)
        .then(response => response.json())
        .then(data => {
            const displayName = data.name && data.name !== 'unknown' ? data.name : username;
            userNameCache[username] = displayName;
            
            // DOM内の該当する要素を更新
            updateUserNameInDOM(username, displayName);
        })
        .catch(error => {
            console.warn(`Failed to fetch name for user ${username}:`, error);
            userNameCache[username] = username; // フォールバック
        });
}

// DOM内のユーザー名表示を更新
function updateUserNameInDOM(username, displayName) {
    // data-username属性を持つ要素を更新
    const elements = document.querySelectorAll(`[data-username="${username}"]`);
    elements.forEach(element => {
        element.textContent = displayName;
    });
}

// カレンダー読み込み時にユーザー名を事前取得
function preloadUserNames(leaves) {
    const usernames = new Set();
    
    leaves.forEach(leave => {
        if (leave.created_by) usernames.add(leave.created_by);
        if (leave.confirmed_by) usernames.add(leave.confirmed_by);
    });
    
    // 各ユーザー名を非同期で取得
    usernames.forEach(username => {
        fetchUserName(username);
    });
}

// 休暇種類の選択肢を初期化する関数
function initializeLeaveTypeOptions() {
    const leaveTypeSelect = document.getElementById('leave-type');
    if (leaveTypeSelect && window.leaveColors) {
        // 既存のオプションをクリア
        leaveTypeSelect.innerHTML = '<option value="">選択してください</option>';
        
        // 休暇種類を動的に追加
        for (const [leaveType, color] of Object.entries(window.leaveColors)) {
            const option = document.createElement('option');
            option.value = leaveType;
            option.textContent = leaveType;
            leaveTypeSelect.appendChild(option);
        }
    }
}

// カレンダー読み込み
function loadCalendar() {
    if (!currentCalendarId) return;
    
    const yearMonth = `${currentYear}${String(currentMonth).padStart(2, '0')}`;
    
    fetch(`/tools/leave_mgr/api/calendar/${currentCalendarId}/${yearMonth}`)
        .then(response => response.json())
        .then(data => {
            calendarData = data;
            // ユーザー名を事前読み込み
            preloadUserNames(data.leaves);
            renderCalendar();
        })
        .catch(error => {
            console.error('Error loading calendar:', error);
            alert('カレンダーの読み込みに失敗しました');
        });
}

// カレンダー描画
function renderCalendar() {
    const container = document.getElementById('calendar-container');
    
    // カレンダーのヘッダー
    const monthNames = ['1月', '2月', '3月', '4月', '5月', '6月', '7月', '8月', '9月', '10月', '11月', '12月'];
    const headerHtml = `
        <h3 class="text-xl font-bold text-center mb-4">
            ${currentYear}年 ${monthNames[currentMonth - 1]}
        </h3>
    `;
    
    // 曜日ヘッダー
    const weekDays = ['日', '月', '火', '水', '木', '金', '土'];
    let calendarHtml = '<div class="calendar-grid">';
    
    weekDays.forEach(day => {
        calendarHtml += `<div class="calendar-header">${day}</div>`;
    });
    
    // カレンダーの日付
    const firstDay = new Date(currentYear, currentMonth - 1, 1);
    const lastDay = new Date(currentYear, currentMonth, 0);
    const prevLastDay = new Date(currentYear, currentMonth - 1, 0);
    
    const firstDayOfWeek = firstDay.getDay();
    const lastDate = lastDay.getDate();
    const prevLastDate = prevLastDay.getDate();
    
    // 前月の日付
    for (let i = firstDayOfWeek - 1; i >= 0; i--) {
        const date = prevLastDate - i;
        calendarHtml += `<div class="calendar-day other-month">${date}</div>`;
    }
    
    // 当月の日付
    const today = new Date();
    for (let date = 1; date <= lastDate; date++) {
        const dateStr = `${currentYear}-${String(currentMonth).padStart(2, '0')}-${String(date).padStart(2, '0')}`;
        const isToday = today.getFullYear() === currentYear && 
                       today.getMonth() + 1 === currentMonth && 
                       today.getDate() === date;
        
        // この日の休暇を取得
        const dayLeaves = calendarData.leaves.filter(leave => leave.date === dateStr);
        
        let dayClass = 'calendar-day';
        if (isToday) dayClass += ' today';
        
        calendarHtml += `<div class="${dayClass}" onclick="onDayClick('${dateStr}', event)">`;
        calendarHtml += `<div class="calendar-day-number">${date}</div>`;
        calendarHtml += '<div class="calendar-day-content">';
        
        // 最大3人まで表示
        const displayLeaves = dayLeaves.slice(0, 3);
        displayLeaves.forEach(leave => {
            const color = window.leaveColors[leave.leave_type] || '#6b7280';
            const confirmedIcon = leave.confirmed_by ? '✓' : '';
            calendarHtml += `
                <div class="leave-item" style="background-color: ${color}; color: white;" 
                     onclick="editLeave('${leave.id}', event)" title="${leave.name} (${leave.leave_type})">
                    ${confirmedIcon}${leave.name}
                </div>
            `;
        });
        
        // 3人以上いる場合
        if (dayLeaves.length > 3) {
            calendarHtml += `
                <div class="more-indicator" onclick="showDateDetail('${dateStr}', event)">
                    他${dayLeaves.length - 3}名
                </div>
            `;
        }
        
        calendarHtml += '</div></div>';
    }
    
    // 次月の日付
    const remainingDays = 42 - (firstDayOfWeek + lastDate); // 6週間分
    for (let date = 1; date <= remainingDays; date++) {
        calendarHtml += `<div class="calendar-day other-month">${date}</div>`;
    }
    
    calendarHtml += '</div>';
    
    container.innerHTML = headerHtml + calendarHtml;
}

// 月変更
function changeMonth(delta) {
    currentMonth += delta;
    if (currentMonth > 12) {
        currentMonth = 1;
        currentYear++;
    } else if (currentMonth < 1) {
        currentMonth = 12;
        currentYear--;
    }
    updateMonthPicker();
    loadCalendar();
}

// 指定月へ移動
function goToMonth() {
    const picker = document.getElementById('month-picker');
    if (picker.value) {
        const [year, month] = picker.value.split('-');
        currentYear = parseInt(year);
        currentMonth = parseInt(month);
        loadCalendar();
    }
}

// 月選択の更新
function updateMonthPicker() {
    const picker = document.getElementById('month-picker');
    picker.value = `${currentYear}-${String(currentMonth).padStart(2, '0')}`;
}

// 日付クリック
function onDayClick(dateStr, event) {
    // 休暇登録モーダルを開く
    showLeaveModal(dateStr);
}

// 休暇登録モーダル表示（修正版）
function showLeaveModal(date = null) {
    if (!currentCalendarId) {
        alert('カレンダーを選択してください');
        return;
    }

    const modal = document.getElementById('leave-modal');
    if (modal) {
        // 表示前に他のモーダルを閉じる
        hideAllModals();
        currentEditingLeave = null;

        modal.classList.remove('hidden');
        modal.style.display = 'flex'; // flexで中央揃え

        document.getElementById('leave-modal-title').textContent = '休暇登録';
        document.getElementById('leave-form').reset();
        document.getElementById('leave-id').value = '';

        // 記入者・確認者情報セクションを非表示
        document.getElementById('leave-info-section').style.display = 'none';
        document.getElementById('leave-confirm-section').classList.add('hidden');
        document.getElementById('delete-leave-btn').classList.add('hidden');
        document.getElementById('copy-leave-btn').classList.add('hidden'); // コピーボタンも非表示

        // 入力モードをデフォルト（自動入力）に設定
        document.getElementById('input-mode-auto').checked = true;
        toggleInputMode('auto');

        // 候補リストをリセット
        document.getElementById('employee-suggestions-list').innerHTML =
            '<div class="text-sm text-gray-400">名前を入力してください</div>';

        if (date) {
            document.getElementById('leave-date').value = date;
        }

        // 休暇種類の選択肢を再初期化
        initializeLeaveTypeOptions();
    }
}

// 休暇編集（ユーザー名表示対応）
function editLeave(leaveId, event) {
    event.stopPropagation();
    
    const leave = calendarData.leaves.find(l => l.id === leaveId);
    if (!leave) return;
    
    const modal = document.getElementById('leave-modal');
    if (modal) {
        // 表示前に他のモーダルを閉じる
        hideAllModals();
        currentEditingLeave = leave;
        
        modal.classList.remove('hidden');
        modal.style.display = 'flex';
        
        document.getElementById('leave-modal-title').textContent = '休暇編集';
        document.getElementById('leave-id').value = leave.id;
        document.getElementById('leave-date').value = leave.date;
        document.getElementById('leave-name').value = leave.name;
        document.getElementById('leave-employee-number').value = leave.employee_number || '';
        document.getElementById('leave-deputies').value = leave.deputies ? leave.deputies.join(', ') : '';
        document.getElementById('leave-remarks').value = leave.remarks || '';

        // 入力モードをデフォルト（自動入力）に設定
        document.getElementById('input-mode-auto').checked = true;
        toggleInputMode('auto');
        
        // 休暇種類の選択肢を初期化してから値を設定
        initializeLeaveTypeOptions();
        document.getElementById('leave-type').value = leave.leave_type;
        
        // 記入者・確認者情報を表示（ユーザー名対応）
        const infoSection = document.getElementById('leave-info-section');
        infoSection.style.display = 'block';
        
        // 記入者名を表示（data-username属性付きで後から更新可能に）
        const creatorElement = document.getElementById('leave-creator');
        const creatorDisplayName = getUserDisplayName(leave.created_by);
        creatorElement.textContent = creatorDisplayName;
        creatorElement.setAttribute('data-username', leave.created_by || '');
        
        // 確認者名を表示
        const confirmerElement = document.getElementById('leave-confirmer');
        if (leave.confirmed_by) {
            const confirmerDisplayName = getUserDisplayName(leave.confirmed_by);
            confirmerElement.textContent = confirmerDisplayName;
            confirmerElement.setAttribute('data-username', leave.confirmed_by);
        } else {
            confirmerElement.textContent = '未確認';
            confirmerElement.removeAttribute('data-username');
        }
        
        document.getElementById('leave-confirmed-at').textContent = leave.confirmed_at ? 
            new Date(leave.confirmed_at).toLocaleString('ja-JP') : '-';
        
        // 確認ボタンの表示制御（修正：記入者自身も確認可能）
        const confirmSection = document.getElementById('leave-confirm-section');
        
        if (!leave.confirmed_by) {
            confirmSection.classList.remove('hidden');
        } else {
            confirmSection.classList.add('hidden');
        }
        
        // 削除ボタンの表示制御
        const deleteBtn = document.getElementById('delete-leave-btn');
        const currentUserId = document.querySelector('.user-info-box').dataset.userId;
        const isAdmin = document.querySelector('.badge-admin') !== null;
        if (isAdmin || leave.created_by === currentUserId) {
            deleteBtn.classList.remove('hidden');
        } else {
            deleteBtn.classList.add('hidden');
        }

        // コピーボタンを常に表示（編集時）
        const copyBtn = document.getElementById('copy-leave-btn');
        copyBtn.classList.remove('hidden');
    }
}

// 休暇確認機能
function confirmLeave() {
    if (!currentEditingLeave) return;
    
    const formData = {
        calendar_id: currentCalendarId,
        year_month: `${currentYear}${String(currentMonth).padStart(2, '0')}`
    };
    
    fetch(`/tools/leave_mgr/api/leave/${currentEditingLeave.id}/confirm`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(formData)
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            alert('休暇を確認しました');
            closeLeaveModal();
            loadCalendar();
        } else {
            alert(data.error || '確認に失敗しました');
        }
    })
    .catch(error => {
        console.error('Error confirming leave:', error);
        alert('確認に失敗しました');
    });
}

// 休暇保存
function saveLeave() {
    const leaveId = document.getElementById('leave-id').value;
    const formData = {
        calendar_id: currentCalendarId,
        year_month: `${currentYear}${String(currentMonth).padStart(2, '0')}`,
        date: document.getElementById('leave-date').value,
        name: document.getElementById('leave-name').value,
        employee_number: document.getElementById('leave-employee-number').value,
        leave_type: document.getElementById('leave-type').value,
        deputies: document.getElementById('leave-deputies').value.split(',').map(d => d.trim()).filter(d => d),
        remarks: document.getElementById('leave-remarks').value
    };
    
    const url = leaveId ? `/tools/leave_mgr/api/leave/${leaveId}` : '/tools/leave_mgr/api/leave';
    const method = leaveId ? 'PUT' : 'POST';
    
    fetch(url, {
        method: method,
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(formData)
    })
    .then(response => {
        if (response.status === 409) {
            // 重複警告
            return response.json().then(data => {
                const message = `同じ名前で同日に登録されています。\n\n既存の登録:\n${data.existing.map(e => `・${e.leave_type}`).join('\n')}\n\n続行しますか？`;
                showConfirmDialog(message, () => {
                    // 強制的に保存
                    saveLeaveForce(formData);
                });
            });
        }
        return response.json();
    })
    .then(data => {
        if (data && data.success) {
            closeLeaveModal();
            loadCalendar();
        }
    })
    .catch(error => {
        console.error('Error saving leave:', error);
        alert('保存に失敗しました');
    });
}

// 強制保存
function saveLeaveForce(formData) {
    fetch('/tools/leave_mgr/api/leave', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({...formData, force: true})
    })
    .then(response => response.json())
    .then(data => {
        closeLeaveModal();
        closeConfirmDialog();
        loadCalendar();
    });
}

// モーダルから削除
function deleteLeaveFromModal() {
    if (!currentEditingLeave) return;
    
    if (!confirm('この休暇を削除しますか？')) return;
    
    const formData = {
        calendar_id: currentCalendarId,
        year_month: `${currentYear}${String(currentMonth).padStart(2, '0')}`
    };
    
    fetch(`/tools/leave_mgr/api/leave/${currentEditingLeave.id}`, {
        method: 'DELETE',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(formData)
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            closeLeaveModal();
            loadCalendar();
        } else {
            alert(data.error || '削除に失敗しました');
        }
    })
    .catch(error => {
        console.error('Error deleting leave:', error);
        alert('削除に失敗しました');
    });
}

// 休暇削除（カレンダーから直接）
function deleteLeave(leaveId) {
    if (!confirm('この休暇を削除しますか？')) return;
    
    fetch(`/tools/leave_mgr/api/leave/${leaveId}`, {
        method: 'DELETE',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            calendar_id: currentCalendarId,
            year_month: `${currentYear}${String(currentMonth).padStart(2, '0')}`
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            loadCalendar();
        } else {
            alert(data.error || '削除に失敗しました');
        }
    })
    .catch(error => {
        console.error('Error deleting leave:', error);
        alert('削除に失敗しました');
    });
}

// 日付詳細表示（ユーザー名表示対応）
function showDateDetail(dateStr, event) {
    if (event) event.stopPropagation();
    
    const dayLeaves = calendarData.leaves.filter(leave => leave.date === dateStr);
    
    document.getElementById('date-detail-title').textContent = `${dateStr} の休暇一覧`;
    
    const currentUserId = document.querySelector('.user-info-box').dataset.userId;
    const isAdmin = document.querySelector('.badge-admin') !== null;
    
    let contentHtml = '<table class="data-table">';
    contentHtml += '<thead><tr>';
    contentHtml += '<th>名前</th>';
    contentHtml += '<th>休暇種類</th>';
    contentHtml += '<th>代務者</th>';
    contentHtml += '<th>記入者</th>';
    contentHtml += '<th>確認状況</th>';
    contentHtml += '<th>操作</th>';
    contentHtml += '</tr></thead><tbody>';
    
    dayLeaves.forEach(leave => {
        const color = window.leaveColors[leave.leave_type] || '#6b7280';
        const canDelete = isAdmin || leave.created_by === currentUserId;
        
        const creatorDisplayName = getUserDisplayName(leave.created_by);
        const confirmerDisplayName = leave.confirmed_by ? getUserDisplayName(leave.confirmed_by) : null;
        
        contentHtml += '<tr>';
        contentHtml += `<td>${leave.name}</td>`;
        contentHtml += `<td><span class="badge" style="background-color: ${color}; color: white">${leave.leave_type}</span></td>`;
        contentHtml += `<td>${leave.deputies ? leave.deputies.join(', ') : '-'}</td>`;
        contentHtml += `<td><span data-username="${leave.created_by || ''}">${creatorDisplayName}</span></td>`;
        
        if (leave.confirmed_by) {
            contentHtml += `<td>✓<span data-username="${leave.confirmed_by}">${confirmerDisplayName}</span></td>`;
        } else {
            contentHtml += `<td>未確認</td>`;
        }
        
        contentHtml += `<td>`;
        contentHtml += `<button onclick="editLeave('${leave.id}', event)" class="btn btn-primary btn-sm mr-2">編集</button>`;
        
        if (canDelete) {
            contentHtml += `<button onclick="deleteLeaveFromDateDetail('${leave.id}')" class="btn btn-danger btn-sm">削除</button>`;
        }
        
        contentHtml += '</td>';
        contentHtml += '</tr>';
    });
    
    contentHtml += '</tbody></table>';
    
    const modal = document.getElementById('date-detail-modal');
    if (modal) {
        hideAllModals(); // 他のモーダルを閉じる
        document.getElementById('date-detail-content').innerHTML = contentHtml;
        modal.classList.remove('hidden');
        modal.style.display = 'flex';
    }
}

// 日付詳細から削除
function deleteLeaveFromDateDetail(leaveId) {
    if (!confirm('この休暇を削除しますか？')) return;
    
    const formData = {
        calendar_id: currentCalendarId,
        year_month: `${currentYear}${String(currentMonth).padStart(2, '0')}`
    };
    
    fetch(`/tools/leave_mgr/api/leave/${leaveId}`, {
        method: 'DELETE',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(formData)
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            closeDateDetailModal();
            loadCalendar();
        } else {
            alert(data.error || '削除に失敗しました');
        }
    })
    .catch(error => {
        console.error('Error deleting leave:', error);
        alert('削除に失敗しました');
    });
}

// 全モーダルを非表示にする
function hideAllModals() {
    const modals = document.querySelectorAll('.modal-overlay');
    modals.forEach(modal => {
        modal.classList.add('hidden');
        modal.style.display = 'none';
    });
}

// モーダルを閉じる
function closeLeaveModal() {
    const modal = document.getElementById('leave-modal');
    if (modal) {
        modal.classList.add('hidden');
        modal.style.display = 'none';
        currentEditingLeave = null;
    }
}

function closeDateDetailModal() {
    const modal = document.getElementById('date-detail-modal');
    if (modal) {
        modal.classList.add('hidden');
        modal.style.display = 'none';
    }
}

// 確認ダイアログ
function showConfirmDialog(message, callback) {
    hideAllModals(); // 他のモーダルを閉じる
    
    document.getElementById('confirm-message').textContent = message;
    const modal = document.getElementById('confirm-dialog');
    if (modal) {
        modal.classList.remove('hidden');
        modal.style.display = 'flex';
    }
    confirmCallback = callback;
}

function closeConfirmDialog() {
    const modal = document.getElementById('confirm-dialog');
    if (modal) {
        modal.classList.add('hidden');
        modal.style.display = 'none';
    }
    confirmCallback = null;
}

function confirmAction() {
    if (confirmCallback) {
        confirmCallback();
    }
}

// 管理者機能（isAdminがtrueの場合のみ使用）
function showAdminModal() {
    hideAllModals(); // 他のモーダルを閉じる
    
    const modal = document.getElementById('admin-modal');
    if (modal) {
        modal.classList.remove('hidden');
        modal.style.display = 'flex';
        loadUsersList();
    }
}

function closeAdminModal() {
    const modal = document.getElementById('admin-modal');
    if (modal) {
        modal.classList.add('hidden');
        modal.style.display = 'none';
    }
}

function switchAdminTab(tab) {
    // タブボタンのスタイル変更
    document.querySelectorAll('.admin-tab').forEach(btn => {
        btn.classList.remove('active');
    });
    document.querySelector(`.admin-tab[data-tab="${tab}"]`).classList.add('active');
    
    // タブコンテンツの表示切替
    document.querySelectorAll('.admin-tab-content').forEach(content => {
        content.classList.remove('active');
    });
    document.getElementById(`admin-${tab}-tab`).classList.add('active');
    
    if (tab === 'users') {
        loadUsersList();
    }
}

function createCalendar() {
    const calendarId = document.getElementById('new-calendar-id').value;
    const calendarName = document.getElementById('new-calendar-name').value;
    
    if (!calendarId) {
        alert('カレンダーIDを入力してください');
        return;
    }
    
    fetch('/tools/leave_mgr/api/admin/calendar', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            calendar_id: calendarId,
            name: calendarName || calendarId
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            alert(data.error);
        } else {
            alert('カレンダーを作成しました');
            location.reload();
        }
    });
}

function grantCalendarPermission() {
    const userId = document.getElementById('grant-user-id').value;
    const calendarId = document.getElementById('grant-calendar-id').value;
    
    if (!userId || !calendarId) {
        alert('ユーザーIDとカレンダーを選択してください');
        return;
    }
    
    fetch('/tools/leave_mgr/api/admin/grant', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            user_id: userId,
            calendar_id: calendarId,
            grant_type: 'calendar'
        })
    })
    .then(response => response.json())
    .then(data => {
        alert('権限を付与しました');
        document.getElementById('grant-user-id').value = '';
        loadUsersList();
    });
}

function grantAdminPermission() {
    const userId = document.getElementById('grant-admin-id').value;
    
    if (!userId) {
        alert('ユーザーIDを入力してください');
        return;
    }
    
    if (!confirm(`ユーザー ${userId} に管理者権限を付与しますか？`)) return;
    
    fetch('/tools/leave_mgr/api/admin/grant', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            user_id: userId,
            grant_type: 'admin'
        })
    })
    .then(response => response.json())
    .then(data => {
        alert('管理者権限を付与しました');
        document.getElementById('grant-admin-id').value = '';
        loadUsersList();
    });
}

function loadUsersList() {
    fetch('/tools/leave_mgr/api/admin/users')
        .then(response => response.json())
        .then(users => {
            let html = '<table class="data-table">';
            html += '<thead><tr>';
            html += '<th>ユーザーID</th>';
            html += '<th>権限</th>';
            html += '<th>アクセス可能カレンダー</th>';
            html += '<th>操作</th>';
            html += '</tr></thead><tbody>';
            
            users.forEach(user => {
                html += '<tr>';
                html += `<td>${user.user_id}</td>`;
                html += `<td>`;
                if (user.is_admin) {
                    html += '<span class="badge badge-admin">管理者</span>';
                } else {
                    html += '<span class="badge badge-user">一般</span>';
                }
                html += '</td>';
                html += `<td>`;
                if (user.calendars.length > 0) {
                    html += user.calendars.map(cal => `${cal.name} (${cal.id})`).join(', ');
                } else {
                    html += '-';
                }
                html += '</td>';
                html += `<td>`;
                
                // 初期管理者は保護
                if (user.is_protected) {
                    html += '<span class="text-gray-500">保護対象</span>';
                } else {
                    // 権限剥奪ボタン（管理者または一般ユーザーでカレンダーアクセス権限がある場合）
                    if (user.is_admin || user.calendars.length > 0) {
                        html += `<button onclick="showRevokePermissionModal('${user.user_id}')" class="btn btn-danger btn-sm mr-2">権限剥奪</button>`;
                    }
                }
                
                html += '</td>';
                html += '</tr>';
            });
            
            html += '</tbody></table>';
            
            // カレンダー削除セクションを追加
            html += '<div class="mt-8 p-4 bg-red-50 rounded-lg">';
            html += '<h4 class="font-semibold mb-3 text-red-600">カレンダー削除</h4>';
            html += '<div class="flex gap-2">';
            html += '<select id="delete-calendar-select" class="form-input" style="width: 300px;">';
            html += '<option value="">削除するカレンダーを選択</option>';
            
            // カレンダー一覧を取得して表示
            fetch('/tools/leave_mgr/api/admin/users')
                .then(response => response.json())
                .then(userData => {
                    // 現在表示されているカレンダー情報を収集
                    const allCalendars = new Set();
                    userData.forEach(user => {
                        user.calendars.forEach(cal => allCalendars.add(JSON.stringify(cal)));
                    });
                    
                    const select = document.getElementById('delete-calendar-select');
                    if (select) {
                        allCalendars.forEach(calStr => {
                            const cal = JSON.parse(calStr);
                            const option = document.createElement('option');
                            option.value = cal.id;
                            option.textContent = `${cal.name} (${cal.id})`;
                            select.appendChild(option);
                        });
                    }
                });
            
            html += '</select>';
            html += '<button onclick="showDeleteCalendarModal()" class="btn btn-danger">削除</button>';
            html += '</div>';
            html += '</div>';
            
            document.getElementById('users-list-content').innerHTML = html;
        });
}

// 権限剥奪モーダル表示
function showRevokePermissionModal(userId) {
    fetch('/tools/leave_mgr/api/admin/users')
        .then(response => response.json())
        .then(users => {
            const user = users.find(u => u.user_id === userId);
            if (!user) return;
            
            hideAllModals();
            
            // ユーザー情報表示
            const userInfoHtml = `
                <p><strong>ユーザーID:</strong> ${user.user_id}</p>
                <p><strong>現在の権限:</strong> ${user.is_admin ? '管理者' : '一般ユーザー'}</p>
            `;
            document.getElementById('revoke-user-info').innerHTML = userInfoHtml;
            
            // 管理者権限剥奪セクションの表示制御
            const adminSection = document.getElementById('revoke-admin-section');
            if (user.is_admin) {
                adminSection.style.display = 'block';
                adminSection.dataset.userId = userId;
            } else {
                adminSection.style.display = 'none';
            }
            
            // カレンダー権限剥奪セクション
            const calendarSection = document.getElementById('revoke-calendar-section');
            if (user.calendars.length > 0) {
                let calendarHtml = '<p class="mb-2">剥奪するカレンダーアクセス権限:</p>';
                user.calendars.forEach(cal => {
                    calendarHtml += `
                        <label class="flex items-center mb-2">
                            <input type="checkbox" name="revoke-calendar" value="${cal.id}" class="mr-2">
                            ${cal.name} (${cal.id})
                        </label>
                    `;
                });
                calendarHtml += '<button onclick="revokeCalendarPermissions()" class="btn btn-danger mt-2">選択したカレンダー権限を剥奪</button>';
                document.getElementById('revoke-calendar-list').innerHTML = calendarHtml;
                calendarSection.style.display = 'block';
                calendarSection.dataset.userId = userId;
            } else {
                calendarSection.style.display = 'none';
            }
            
            const modal = document.getElementById('revoke-permission-modal');
            modal.classList.remove('hidden');
            modal.style.display = 'flex';
        });
}

// 管理者権限剥奪
function revokeAdminPermission() {
    const revokeType = document.querySelector('input[name="admin-revoke-type"]:checked');
    if (!revokeType) {
        alert('剥奪タイプを選択してください');
        return;
    }
    
    const userId = document.getElementById('revoke-admin-section').dataset.userId;
    const type = revokeType.value === 'admin_only' ? 'admin' : 'admin_and_calendars';
    const message = type === 'admin' ? 
        '管理者権限のみを剥奪しますか？' : 
        '管理者権限と全カレンダーアクセス権限を剥奪しますか？';
    
    if (!confirm(message)) return;
    
    fetch('/tools/leave_mgr/api/admin/revoke', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({
            user_id: userId,
            revoke_type: type
        })
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            alert(data.error);
        } else {
            alert('権限を剥奪しました');
            closeRevokePermissionModal();
            loadUsersList();
        }
    });
}

// カレンダー権限剥奪
function revokeCalendarPermissions() {
    const checkedCalendars = document.querySelectorAll('input[name="revoke-calendar"]:checked');
    if (checkedCalendars.length === 0) {
        alert('剥奪するカレンダーを選択してください');
        return;
    }
    
    const userId = document.getElementById('revoke-calendar-section').dataset.userId;
    const calendarNames = Array.from(checkedCalendars).map(cb => {
        const label = cb.parentElement.textContent.trim();
        return label;
    }).join(', ');
    
    if (!confirm(`以下のカレンダーアクセス権限を剥奪しますか？\n\n${calendarNames}`)) return;
    
    // 各カレンダーの権限を個別に剥奪
    const promises = Array.from(checkedCalendars).map(cb => {
        return fetch('/tools/leave_mgr/api/admin/revoke', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                user_id: userId,
                revoke_type: 'calendar',
                calendar_id: cb.value
            })
        });
    });
    
    Promise.all(promises)
        .then(() => {
            alert('カレンダーアクセス権限を剥奪しました');
            closeRevokePermissionModal();
            loadUsersList();
        })
        .catch(error => {
            console.error('Error revoking permissions:', error);
            alert('権限剥奪に失敗しました');
        });
}

// カレンダー削除モーダル表示
function showDeleteCalendarModal() {
    const select = document.getElementById('delete-calendar-select');
    const calendarId = select.value;
    
    if (!calendarId) {
        alert('削除するカレンダーを選択してください');
        return;
    }
    
    const calendarName = select.options[select.selectedIndex].text;
    
    // データ件数を取得
    fetch(`/tools/leave_mgr/api/admin/calendar/${calendarId}/data_count`)
        .then(response => response.json())
        .then(data => {
            hideAllModals();
            
            let infoHtml = `<p><strong>削除対象:</strong> ${calendarName}</p>`;
            if (data.data_count > 0) {
                infoHtml += `<p class="text-red-600 font-semibold mt-2">このカレンダーには ${data.data_count} 件の休暇データが登録されています。</p>`;
                infoHtml += `<p class="text-red-600">削除すると復元できません。続行しますか？</p>`;
            } else {
                infoHtml += `<p class="text-gray-600 mt-2">このカレンダーには登録データがありません。</p>`;
            }
            
            document.getElementById('delete-calendar-info').innerHTML = infoHtml;
            
            const modal = document.getElementById('delete-calendar-modal');
            modal.dataset.calendarId = calendarId;
            modal.classList.remove('hidden');
            modal.style.display = 'flex';
        });
}

// カレンダー削除実行
function confirmDeleteCalendar() {
    const modal = document.getElementById('delete-calendar-modal');
    const calendarId = modal.dataset.calendarId;
    
    fetch(`/tools/leave_mgr/api/admin/calendar/${calendarId}`, {
        method: 'DELETE'
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            alert(data.error);
        } else {
            alert(`カレンダーを削除しました。（${data.deleted_data_count}件のデータを削除）`);
            closeDeleteCalendarModal();
            loadUsersList();
            // カレンダー選択肢も更新
            location.reload();
        }
    });
}

// モーダル閉じる
function closeRevokePermissionModal() {
    const modal = document.getElementById('revoke-permission-modal');
    modal.classList.add('hidden');
    modal.style.display = 'none';
}

function closeDeleteCalendarModal() {
    const modal = document.getElementById('delete-calendar-modal');
    modal.classList.add('hidden');
    modal.style.display = 'none';
}

// ========== 一括登録機能 ==========

// 一括登録モーダルを表示
function showBulkRegisterModal() {
    if (!currentCalendarId) {
        alert('カレンダーを選択してください');
        return;
    }

    hideAllModals();

    const modal = document.getElementById('bulk-register-modal');
    if (modal) {
        modal.classList.remove('hidden');
        modal.style.display = 'flex';

        // フォームをリセット
        document.getElementById('bulk-register-data').value = '';
        document.getElementById('bulk-register-preview').classList.add('hidden');
    }
}

// 一括登録モーダルを閉じる
function closeBulkRegisterModal() {
    const modal = document.getElementById('bulk-register-modal');
    if (modal) {
        modal.classList.add('hidden');
        modal.style.display = 'none';
    }
}

// 一括登録データを解析（非同期対応）
async function parseBulkRegisterData(dataText) {
    const lines = dataText.trim().split('\n');
    const results = [];

    for (let index = 0; index < lines.length; index++) {
        const line = lines[index];
        if (!line.trim()) continue; // 空行はスキップ

        const parts = line.split(',').map(p => p.trim());

        if (parts.length < 3) {
            results.push({
                success: false,
                lineNumber: index + 1,
                error: '必須項目が不足しています（日付、名前または社員番号、休暇種類は必須）',
                data: null
            });
            continue;
        }

        const [date, nameOrNumber, leaveType, deputiesStr = '', remarks = ''] = parts;

        // 日付チェック
        if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
            results.push({
                success: false,
                lineNumber: index + 1,
                error: '日付の形式が正しくありません（YYYY-MM-DD形式で入力してください）',
                data: null
            });
            continue;
        }

        // 休暇種類チェック
        if (!window.leaveColors[leaveType]) {
            results.push({
                success: false,
                lineNumber: index + 1,
                error: `休暇種類「${leaveType}」は存在しません`,
                data: null
            });
            continue;
        }

        // 代務者を分割（・、カンマ、スペースで区切り）
        const deputies = deputiesStr
            ? deputiesStr.split(/[・、,，\s]+/).map(d => d.trim()).filter(d => d)
            : [];

        // 名前または社員番号かを判定
        let employeeName = nameOrNumber;
        let employeeNumber = '';

        // 数字のみの場合は社員番号として扱う
        if (/^\d+$/.test(nameOrNumber)) {
            try {
                // 社員番号から名前を取得
                const response = await fetch(`/tools/pluslist/api/search_employee?q=${nameOrNumber}`);
                const employees = await response.json();

                if (employees.length > 0 && employees[0].employee_number === nameOrNumber) {
                    employeeName = employees[0].employee_name;
                    employeeNumber = nameOrNumber;
                } else {
                    results.push({
                        success: false,
                        lineNumber: index + 1,
                        error: `社員番号「${nameOrNumber}」が見つかりません`,
                        data: null
                    });
                    continue;
                }
            } catch (error) {
                results.push({
                    success: false,
                    lineNumber: index + 1,
                    error: `社員番号「${nameOrNumber}」の検索に失敗しました`,
                    data: null
                });
                continue;
            }
        }

        results.push({
            success: true,
            lineNumber: index + 1,
            error: null,
            data: {
                date: date,
                name: employeeName,
                employee_number: employeeNumber,
                leave_type: leaveType,
                deputies: deputies,
                remarks: remarks
            }
        });
    }

    return results;
}

// 一括登録のプレビュー
async function previewBulkRegister() {
    const dataText = document.getElementById('bulk-register-data').value;

    if (!dataText.trim()) {
        alert('データを入力してください');
        return;
    }

    // ローディング表示
    const previewSection = document.getElementById('bulk-register-preview');
    const previewContent = document.getElementById('bulk-register-preview-content');
    previewContent.innerHTML = '<p class="text-gray-500">解析中...</p>';
    previewSection.classList.remove('hidden');

    const results = await parseBulkRegisterData(dataText);

    // プレビュー表示
    const previewSection = document.getElementById('bulk-register-preview');
    const previewContent = document.getElementById('bulk-register-preview-content');

    let html = '<table class="data-table">';
    html += '<thead><tr>';
    html += '<th>行</th><th>状態</th><th>日付</th><th>名前</th><th>社員番号</th><th>種類</th><th>代務者</th><th>備考</th>';
    html += '</tr></thead><tbody>';

    results.forEach(result => {
        html += '<tr>';
        html += `<td>${result.lineNumber}</td>`;

        if (result.success) {
            html += `<td><span class="text-green-600">✓ OK</span></td>`;
            html += `<td>${result.data.date}</td>`;
            html += `<td>${result.data.name}</td>`;
            html += `<td>${result.data.employee_number || '-'}</td>`;
            const color = window.leaveColors[result.data.leave_type];
            html += `<td><span class="badge" style="background-color: ${color}; color: white">${result.data.leave_type}</span></td>`;
            html += `<td>${result.data.deputies.join(', ') || '-'}</td>`;
            html += `<td>${result.data.remarks || '-'}</td>`;
        } else {
            html += `<td colspan="7"><span class="text-red-600">✗ エラー: ${result.error}</span></td>`;
        }

        html += '</tr>';
    });

    html += '</tbody></table>';

    const successCount = results.filter(r => r.success).length;
    const errorCount = results.filter(r => !r.success).length;

    html = `<p class="mb-2"><strong>成功: ${successCount}件</strong> / <strong class="text-red-600">エラー: ${errorCount}件</strong></p>` + html;

    previewContent.innerHTML = html;
    previewSection.classList.remove('hidden');
}

// 一括登録を実行
async function executeBulkRegister() {
    const dataText = document.getElementById('bulk-register-data').value;

    if (!dataText.trim()) {
        alert('データを入力してください');
        return;
    }

    const results = await parseBulkRegisterData(dataText);
    const successData = results.filter(r => r.success).map(r => r.data);
    const errorCount = results.filter(r => !r.success).length;

    if (successData.length === 0) {
        alert('登録可能なデータがありません。エラーを修正してください。');
        return;
    }

    if (errorCount > 0) {
        if (!confirm(`${errorCount}件のエラーがあります。正常なデータのみ登録しますか？`)) {
            return;
        }
    }

    // 各データを順次登録
    let registeredCount = 0;
    const promises = [];

    successData.forEach(data => {
        const formData = {
            calendar_id: currentCalendarId,
            year_month: data.date.substring(0, 7).replace('-', ''), // "2026-01" -> "202601"
            date: data.date,
            name: data.name,
            employee_number: data.employee_number || '',
            leave_type: data.leave_type,
            deputies: data.deputies,
            remarks: data.remarks,
            force: true // 重複チェックをスキップ
        };

        const promise = fetch('/tools/leave_mgr/api/leave', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(formData)
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                registeredCount++;
            }
        });

        promises.push(promise);
    });

    Promise.all(promises)
        .then(() => {
            alert(`${registeredCount}件の休暇を登録しました`);
            closeBulkRegisterModal();
            loadCalendar();
        })
        .catch(error => {
            console.error('Error in bulk register:', error);
            alert('一括登録中にエラーが発生しました');
        });
}

// ========== 休暇コピー機能 ==========

// 休暇コピーモーダルを表示
function showCopyLeaveModal() {
    if (!currentEditingLeave) {
        alert('コピー元の休暇が選択されていません');
        return;
    }

    hideAllModals();

    const modal = document.getElementById('copy-leave-modal');
    if (modal) {
        modal.classList.remove('hidden');
        modal.style.display = 'flex';

        // コピー元情報を表示
        const sourceInfo = document.getElementById('copy-source-info');
        const color = window.leaveColors[currentEditingLeave.leave_type] || '#6b7280';

        let html = `<p><strong>日付:</strong> ${currentEditingLeave.date}</p>`;
        html += `<p><strong>名前:</strong> ${currentEditingLeave.name}</p>`;
        html += `<p><strong>休暇種類:</strong> <span class="badge" style="background-color: ${color}; color: white">${currentEditingLeave.leave_type}</span></p>`;
        html += `<p><strong>代務者:</strong> ${currentEditingLeave.deputies ? currentEditingLeave.deputies.join(', ') : '-'}</p>`;
        html += `<p><strong>備考:</strong> ${currentEditingLeave.remarks || '-'}</p>`;

        sourceInfo.innerHTML = html;

        // 日付をクリア
        document.getElementById('copy-target-date').value = '';
    }
}

// 休暇コピーモーダルを閉じる
function closeCopyLeaveModal() {
    const modal = document.getElementById('copy-leave-modal');
    if (modal) {
        modal.classList.add('hidden');
        modal.style.display = 'none';
    }
}

// 休暇コピーを実行
function executeCopyLeave() {
    if (!currentEditingLeave) {
        alert('コピー元の休暇が選択されていません');
        return;
    }

    const targetDate = document.getElementById('copy-target-date').value;

    if (!targetDate) {
        alert('コピー先の日付を選択してください');
        return;
    }

    // コピー元のデータをコピー
    const formData = {
        calendar_id: currentCalendarId,
        year_month: targetDate.substring(0, 7).replace('-', ''), // "2026-01" -> "202601"
        date: targetDate,
        name: currentEditingLeave.name,
        employee_number: currentEditingLeave.employee_number || '',
        leave_type: currentEditingLeave.leave_type,
        deputies: currentEditingLeave.deputies || [],
        remarks: currentEditingLeave.remarks || ''
    };

    fetch('/tools/leave_mgr/api/leave', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(formData)
    })
    .then(response => {
        if (response.status === 409) {
            // 重複警告
            return response.json().then(data => {
                const message = `同じ名前で同日に登録されています。\n\n既存の登録:\n${data.existing.map(e => `・${e.leave_type}`).join('\n')}\n\n続行しますか？`;
                if (confirm(message)) {
                    // 強制的に保存
                    return fetch('/tools/leave_mgr/api/leave', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                        },
                        body: JSON.stringify({...formData, force: true})
                    }).then(r => r.json());
                }
                throw new Error('User cancelled');
            });
        }
        return response.json();
    })
    .then(data => {
        if (data && data.success) {
            alert('休暇をコピーしました');
            closeCopyLeaveModal();
            closeLeaveModal();
            loadCalendar();
        }
    })
    .catch(error => {
        if (error.message !== 'User cancelled') {
            console.error('Error copying leave:', error);
            alert('コピーに失敗しました');
        }
    });
}

// ========== 社員名簿+連携機能 ==========

// 入力モード切り替え
function toggleInputMode(mode) {
    const suggestionPanel = document.getElementById('employee-suggestions');

    if (!suggestionPanel) {
        console.warn('Employee suggestions panel not found');
        return;
    }

    if (mode === 'auto') {
        // 自動入力モード：候補パネルを表示
        suggestionPanel.style.display = 'block';
        // 現在の名前で検索を実行
        const nameInput = document.getElementById('leave-name');
        if (nameInput && nameInput.value) {
            searchEmployees(nameInput.value);
        }
    } else {
        // 手動入力モード：候補パネルを非表示
        suggestionPanel.style.display = 'none';
    }
}

// 社員検索（デバウンス付き）
function searchEmployees(query) {
    // デバウンス処理（300ms待機）
    clearTimeout(employeeSearchTimeout);

    if (!query || query.trim().length === 0) {
        // 入力が空の場合
        document.getElementById('employee-suggestions-list').innerHTML =
            '<div class="text-sm text-gray-400">名前を入力してください</div>';
        return;
    }

    employeeSearchTimeout = setTimeout(() => {
        // 社員名簿+のAPIを呼び出し
        fetch(`/tools/pluslist/api/search_employee?q=${encodeURIComponent(query)}`)
            .then(response => response.json())
            .then(employees => {
                displayEmployeeSuggestions(employees);
            })
            .catch(error => {
                console.error('Employee search error:', error);
                document.getElementById('employee-suggestions-list').innerHTML =
                    '<div class="text-sm text-red-500">検索エラー</div>';
            });
    }, 300);
}

// 候補を表示
function displayEmployeeSuggestions(employees) {
    const listContainer = document.getElementById('employee-suggestions-list');

    if (employees.length === 0) {
        listContainer.innerHTML = '<div class="text-sm text-gray-400">該当する社員が見つかりません</div>';
        return;
    }

    let html = '';
    employees.forEach(emp => {
        html += `
            <div class="employee-suggestion-item" onclick="selectEmployee('${emp.employee_number}', '${emp.employee_name.replace(/'/g, "\\'")}')">
                <span class="employee-suggestion-name">${emp.employee_name}</span>
                <span class="employee-suggestion-number">(${emp.employee_number})</span>
            </div>
        `;
    });

    listContainer.innerHTML = html;
}

// 候補を選択
function selectEmployee(employeeNumber, employeeName) {
    // 名前と社員番号を自動入力
    document.getElementById('leave-name').value = employeeName;
    document.getElementById('leave-employee-number').value = employeeNumber;

    // 候補リストをクリア
    document.getElementById('employee-suggestions-list').innerHTML =
        '<div class="text-sm text-gray-400">選択されました</div>';
}