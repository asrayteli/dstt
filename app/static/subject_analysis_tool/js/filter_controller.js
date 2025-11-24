// フィルターコントローラー - サイドバーの絞り込みUIを制御

class FilterController {
    constructor() {
        this.selectedMonths = [];
        this.selectedSites = [];
        this.selectedSubjects = [];
        this.selectedSegments = [];
    }

    /**
     * UIを初期化
     */
    initUI() {
        this.renderSiteList();
        this.renderSubjectList();
        this.setupEventListeners();
    }

    /**
     * 現場リストを描画
     */
    renderSiteList() {
        const sites = dataManager.getSites();
        const container = document.getElementById('site-list');

        if (sites.length === 0) {
            container.innerHTML = '<p class="text-sm text-gray-500">現場データがありません</p>';
            return;
        }

        // セグメント別にグループ化
        const groupedSites = {};
        sites.forEach(site => {
            if (!groupedSites[site.segment]) {
                groupedSites[site.segment] = [];
            }
            groupedSites[site.segment].push(site);
        });

        let html = '';
        Object.keys(groupedSites).sort().forEach(segment => {
            html += `
                <div class="subject-group">
                    <div class="subject-group-title" onclick="toggleGroup(this)">
                        ${segment} (${groupedSites[segment].length})
                    </div>
                    <div class="subject-group-items">
            `;

            groupedSites[segment].forEach(site => {
                const siteKey = `${site.contractCode}|${site.siteName}|${site.corpName}`;
                html += `
                    <div class="site-item">
                        <input type="checkbox"
                               class="site-checkbox"
                               value="${siteKey}"
                               data-segment="${site.segment}">
                        <span>${site.displayName}</span>
                    </div>
                `;
            });

            html += `
                    </div>
                </div>
            `;
        });

        container.innerHTML = html;

        // 検索機能
        this.setupSearchFilter('site-search', '.site-item');
    }

    /**
     * 科目リストを描画
     */
    renderSubjectList() {
        const subjectGroups = dataManager.getSubjects();
        const container = document.getElementById('subject-list');

        let html = '';
        Object.keys(subjectGroups).forEach(groupName => {
            if (subjectGroups[groupName].length === 0) return;

            html += `
                <div class="subject-group">
                    <div class="subject-group-title" onclick="toggleGroup(this)">
                        ${groupName} (${subjectGroups[groupName].length})
                    </div>
                    <div class="subject-group-items">
            `;

            subjectGroups[groupName].forEach(subject => {
                html += `
                    <div class="subject-item">
                        <input type="checkbox"
                               class="subject-checkbox"
                               value="${subject}"
                               data-group="${groupName}">
                        <span>${subject}</span>
                    </div>
                `;
            });

            html += `
                    </div>
                </div>
            `;
        });

        container.innerHTML = html;

        // 検索機能
        this.setupSearchFilter('subject-search', '.subject-item');
    }

    /**
     * 検索フィルター設定
     */
    setupSearchFilter(inputId, itemSelector) {
        const input = document.getElementById(inputId);
        if (!input) return;

        input.addEventListener('input', (e) => {
            const searchTerm = e.target.value.toLowerCase();
            const items = document.querySelectorAll(itemSelector);

            items.forEach(item => {
                const text = item.textContent.toLowerCase();
                if (text.includes(searchTerm)) {
                    item.style.display = '';
                } else {
                    item.style.display = 'none';
                }
            });
        });
    }

    /**
     * イベントリスナー設定
     */
    setupEventListeners() {
        // 月チェックボックス
        document.querySelectorAll('.month-checkbox').forEach(checkbox => {
            checkbox.addEventListener('change', () => {
                this.updateSelectedMonths();
            });
        });

        // 現場チェックボックス
        document.addEventListener('change', (e) => {
            if (e.target.classList.contains('site-checkbox')) {
                this.updateSelectedSites();
            }
        });

        // 科目チェックボックス
        document.addEventListener('change', (e) => {
            if (e.target.classList.contains('subject-checkbox')) {
                this.updateSelectedSubjects();
            }
        });

        // 比較モード変更
        const comparisonMode = document.getElementById('comparison-mode');
        if (comparisonMode) {
            comparisonMode.addEventListener('change', () => {
                this.updateComparisonModeUI();
            });
        }
    }

    /**
     * 選択された月を更新
     */
    updateSelectedMonths() {
        this.selectedMonths = Array.from(
            document.querySelectorAll('.month-checkbox:checked')
        ).map(cb => parseInt(cb.value));
    }

    /**
     * 選択された現場を更新
     */
    updateSelectedSites() {
        this.selectedSites = Array.from(
            document.querySelectorAll('.site-checkbox:checked')
        ).map(cb => cb.value);

        // 選択されたセグメントも更新
        this.selectedSegments = Array.from(
            new Set(
                Array.from(document.querySelectorAll('.site-checkbox:checked'))
                    .map(cb => cb.dataset.segment)
            )
        );
    }

    /**
     * 選択された科目を更新
     */
    updateSelectedSubjects() {
        this.selectedSubjects = Array.from(
            document.querySelectorAll('.subject-checkbox:checked')
        ).map(cb => cb.value);
    }

    /**
     * 比較モードUIを更新
     */
    updateComparisonModeUI() {
        const mode = document.getElementById('comparison-mode').value;
        const baseMonthGroup = document.getElementById('base-month-group');

        if (mode === 'same_year') {
            baseMonthGroup.style.display = 'block';
        } else {
            baseMonthGroup.style.display = 'none';
        }
    }

    /**
     * 現在のフィルター設定を取得
     */
    getFilters() {
        this.updateSelectedMonths();
        this.updateSelectedSites();
        this.updateSelectedSubjects();

        return {
            months: this.selectedMonths,
            sites: this.selectedSites,
            subjects: this.selectedSubjects,
            segments: this.selectedSegments,
            comparisonMode: document.getElementById('comparison-mode').value,
            baseMonth: document.getElementById('base-month').value,
            displayMode: document.getElementById('display-mode').value,
            thresholdRate: parseFloat(document.getElementById('threshold-rate').value) || 5,
            thresholdAmount: parseFloat(document.getElementById('threshold-amount').value) || 10000,
            thresholdCondition: document.getElementById('threshold-condition').value,
            highlightEnabled: document.getElementById('highlight-enabled').checked
        };
    }

    /**
     * フィルターをリセット
     */
    reset() {
        this.selectedMonths = [];
        this.selectedSites = [];
        this.selectedSubjects = [];
        this.selectedSegments = [];

        // UIをリセット
        document.querySelectorAll('.month-checkbox').forEach(cb => cb.checked = false);
        document.querySelectorAll('.site-checkbox').forEach(cb => cb.checked = false);
        document.querySelectorAll('.subject-checkbox').forEach(cb => cb.checked = false);
    }
}

// グローバル関数

function selectAllMonths() {
    document.querySelectorAll('.month-checkbox').forEach(cb => cb.checked = true);
    filterController.updateSelectedMonths();
}

function deselectAllMonths() {
    document.querySelectorAll('.month-checkbox').forEach(cb => cb.checked = false);
    filterController.updateSelectedMonths();
}

function selectAllSites() {
    document.querySelectorAll('.site-checkbox').forEach(cb => cb.checked = true);
    filterController.updateSelectedSites();
}

function deselectAllSites() {
    document.querySelectorAll('.site-checkbox').forEach(cb => cb.checked = false);
    filterController.updateSelectedSites();
}

function selectAllSubjects() {
    document.querySelectorAll('.subject-checkbox').forEach(cb => cb.checked = true);
    filterController.updateSelectedSubjects();
}

function deselectAllSubjects() {
    document.querySelectorAll('.subject-checkbox').forEach(cb => cb.checked = false);
    filterController.updateSelectedSubjects();
}

function toggleGroup(element) {
    const items = element.nextElementSibling;
    if (items.style.display === 'none') {
        items.style.display = 'block';
    } else {
        items.style.display = 'none';
    }
}

// グローバルインスタンス
const filterController = new FilterController();
