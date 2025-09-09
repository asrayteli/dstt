// グローバル変数
let currentCalendarId = null;
let currentYear = new Date().getFullYear();
let currentMonth = new Date().getMonth() + 1;
let calendarData = null;
let confirmCallback = null;

// 初期化
document.addEventListener('DOMContentLoaded', function() {
    // カレンダー選択の変更イベント
    document.getElementById('calendar-select').addEventListener('change', function() {
        currentCalendarId = this.value;
        if (currentCalendarId) {
            document.getElementById('add-leave-btn').disabled = false;
            loadCalendar();
        } else {
            document.getElementById('add-leave-btn').disabled = true;
            document.getElementById('calendar-container').innerHTML = '<div class="text-center text-gray-500 py-8">カレンダーを選択してください</div>';
        }
    });
    
    // 月選択の初期値設定
    updateMonthPicker();
    
    // 休暇登録フォームのサブミットイベント
    document.getElementById('leave-form').addEventListener('submit', function(e) {
        e.preventDefault();
        saveLeave();
    });
});

// カレンダー読み込み
function loadCalendar() {
    if (!currentCalendarId) return;
    
    const yearMonth = `${currentYear}${String(currentMonth).padStart(2, '0')}`;
    
    fetch(`/tools/leave_mgr/api/calendar/${currentCalendarId}/${yearMonth}`)
        .then(response => response.json())
        .then(data => {
            calendarData = data;
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
            calendarHtml += `
                <div class="leave-item" style="background-color: ${color}; color: white;" 
                     onclick="editLeave('${leave.id}', event)" title="${leave.name} (${leave.leave_type})">
                    ${leave.name}
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

// 休暇登録モーダル表示
function showLeaveModal(date = null) {
    if (!currentCalendarId) {
        alert('カレンダーを選択してください');
        return;
    }
    
    document.getElementById('leave-modal').classList.remove('hidden');
    document.getElementById('leave-modal-title').textContent = '休暇登録';
    document.getElementById('leave-form').reset();
    document.getElementById('leave-id').value = '';
    
    if (date) {
        document.getElementById('leave-date').value = date;
    }
}

// 休暇編集
function editLeave(leaveId, event) {
    event.stopPropagation();
    
    const leave = calendarData.leaves.find(l => l.id === leaveId);
    if (!leave) return;
    
    document.getElementById('leave-modal').classList.remove('hidden');
    document.getElementById('leave-modal-title').textContent = '休暇編集';
    document.getElementById('leave-id').value = leave.id;
    document.getElementById('leave-date').value = leave.date;
    document.getElementById('leave-name').value = leave.name;
    document.getElementById('leave-type').value = leave.leave_type;
    document.getElementById('leave-deputies').value = leave.deputies ? leave.deputies.join(', ') : '';
    document.getElementById('leave-remarks').value = leave.remarks || '';
}

// 休暇保存
function saveLeave() {
    const leaveId = document.getElementById('leave-id').value;
    const formData = {
        calendar_id: currentCalendarId,
        year_month: `${currentYear}${String(currentMonth).padStart(2, '0')}`,
        date: document.getElementById('leave-date').value,
        name: document.getElementById('leave-name').value,
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

// 休暇削除
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
        loadCalendar();
    });
}

// 日付詳細表示
function showDateDetail(dateStr, event) {
    if (event) event.stopPropagation();
    
    const dayLeaves = calendarData.leaves.filter(leave => leave.date === dateStr);
    
    document.getElementById('date-detail-title').textContent = `${dateStr} の休暇一覧`;
    
    let contentHtml = '<table class="data-table">';
    contentHtml += '<thead><tr>';
    contentHtml += '<th>名前</th>';
    contentHtml += '<th>休暇種類</th>';
    contentHtml += '<th>代務者</th>';
    contentHtml += '<th>備考</th>';
    contentHtml += '<th>操作</th>';
    contentHtml += '</tr></thead><tbody>';
    
    dayLeaves.forEach(leave => {
        const color = window.leaveColors[leave.leave_type] || '#6b7280';
        contentHtml += '<tr>';
        contentHtml += `<td>${leave.name}</td>`;
        contentHtml += `<td><span class="badge" style="background-color: ${color}; color: white">${leave.leave_type}</span></td>`;
        contentHtml += `<td>${leave.deputies ? leave.deputies.join(', ') : '-'}</td>`;
        contentHtml += `<td>${leave.remarks || '-'}</td>`;
        contentHtml += `<td>`;
        contentHtml += `<button onclick="editLeave('${leave.id}', event)" class="btn btn-primary btn-sm mr-2">編集</button>`;
        contentHtml += `<button onclick="deleteLeave('${leave.id}')" class="btn btn-danger btn-sm">削除</button>`;
        contentHtml += '</td>';
        contentHtml += '</tr>';
    });
    
    contentHtml += '</tbody></table>';
    
    document.getElementById('date-detail-content').innerHTML = contentHtml;
    document.getElementById('date-detail-modal').classList.remove('hidden');
}

// モーダルを閉じる
function closeLeaveModal() {
    document.getElementById('leave-modal').classList.add('hidden');
}

function closeDateDetailModal() {
    document.getElementById('date-detail-modal').classList.add('hidden');
}

// 確認ダイアログ
function showConfirmDialog(message, callback) {
    document.getElementById('confirm-message').textContent = message;
    document.getElementById('confirm-dialog').classList.remove('hidden');
    confirmCallback = callback;
}

function closeConfirmDialog() {
    document.getElementById('confirm-dialog').classList.add('hidden');
    confirmCallback = null;
}

function confirmAction() {
    if (confirmCallback) {
        confirmCallback();
    }
}

// 管理者機能（isAdminがtrueの場合のみ使用）
function showAdminModal() {
    document.getElementById('admin-modal').classList.remove('hidden');
    loadUsersList();
}

function closeAdminModal() {
    document.getElementById('admin-modal').classList.add('hidden');
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
                html += '</tr>';
            });
            
            html += '</tbody></table>';
            document.getElementById('users-list-content').innerHTML = html;
        });
}