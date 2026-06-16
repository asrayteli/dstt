// フィルターコントローラー - サイドバーの絞り込みUIを制御

// 現場名・科目名に引用符やタグが含まれてもHTMLが壊れないようにエスケープする
function escapeFilterHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function escapeCssAttrValue(value) {
    const text = String(value ?? '');
    if (window.CSS && typeof window.CSS.escape === 'function') {
        return window.CSS.escape(text);
    }
    return text.replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\]/g, '\\]');
}

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
        this.updateComparisonModeUI();
    }

    /**
     * 現場リストを描画
     */
    renderSiteList() {
        const sites = dataManager.getSites();
        const container = document.getElementById('site-list');
        const siteGroupMode = document.getElementById('site-group-mode')?.value || '8digit';

        if (sites.length === 0) {
            container.innerHTML = '<p class="text-sm text-gray-500">現場データがありません</p>';
            return;
        }

        let html = '';

        if (siteGroupMode === '5digit') {
            // 5桁グループ化モード：ツリー構造で表示
            const grouped5Digit = dataManager.getSites5DigitGrouped();

            Object.keys(grouped5Digit).sort().forEach((segment, segmentIndex) => {
                html += `
                    <div class="subject-group">
                        <div class="subject-group-title" onclick="toggleGroup(this)">
                            ${escapeFilterHtml(segment)} (${grouped5Digit[segment].length})
                        </div>
                        <div class="subject-group-items">
                `;

                grouped5Digit[segment].forEach((group, groupIndex) => {
                    const code5 = group.code5;
                    const segmentKey = group.segment || segment;
                    const groupKey = `5digit:${code5}:${segmentKey}`;
                    // id や inline onclick で安全に使えるよう英数字のみで構成する
                    const treeId = `children-${code5}-${segmentIndex}-${groupIndex}`;
                    const allChecked = this.selectedSites.includes(groupKey);

                    html += `
                        <div class="site-item site-group-5digit">
                            <input type="checkbox"
                                   class="site-checkbox site-checkbox-5digit"
                                   value="${escapeFilterHtml(groupKey)}"
                                   data-segment="${escapeFilterHtml(segment)}"
                                   data-group-key="${escapeFilterHtml(groupKey)}"
                                   ${allChecked ? 'checked' : ''}
                                   onchange="filterController.handle5DigitCheckboxChange(this)">
                            <span onclick="filterController.toggle8DigitChildren('${treeId}')" style="cursor: pointer;">
                                <span id="expand-icon-${treeId}">▶</span> ${escapeFilterHtml(group.corpName)}（${group.children.length}拠点）
                            </span>
                        </div>
                        <div id="${treeId}" class="site-8digit-children" style="display: none; margin-left: 20px;">
                    `;

                    group.children.forEach(site => {
                        const siteKey = `${site.contractCode}|${site.siteName}|${site.corpName}`;
                        const checked = this.selectedSites.includes(siteKey) || allChecked;
                        html += `
                            <div class="site-item">
                                <input type="checkbox"
                                       class="site-checkbox site-checkbox-8digit"
                                       value="${escapeFilterHtml(siteKey)}"
                                       data-segment="${escapeFilterHtml(segment)}"
                                       data-group-key="${escapeFilterHtml(groupKey)}"
                                       ${checked ? 'checked' : ''}
                                       onchange="filterController.handle8DigitCheckboxChange(this)">
                                <span>${escapeFilterHtml(site.displayName)}</span>
                            </div>
                        `;
                    });

                    html += `
                        </div>
                    `;
                });

                html += `
                        </div>
                    </div>
                `;
            });
        } else {
            // 8桁個別表示モード：従来の表示
            const groupedSites = {};
            sites.forEach(site => {
                if (!groupedSites[site.segment]) {
                    groupedSites[site.segment] = [];
                }
                groupedSites[site.segment].push(site);
            });

            Object.keys(groupedSites).sort().forEach(segment => {
                html += `
                    <div class="subject-group">
                        <div class="subject-group-title" onclick="toggleGroup(this)">
                            ${escapeFilterHtml(segment)} (${groupedSites[segment].length})
                        </div>
                        <div class="subject-group-items">
                `;

                groupedSites[segment].forEach(site => {
                    const siteKey = `${site.contractCode}|${site.siteName}|${site.corpName}`;
                    const checked = this.selectedSites.includes(siteKey);
                    html += `
                        <div class="site-item">
                            <input type="checkbox"
                                   class="site-checkbox"
                                   value="${escapeFilterHtml(siteKey)}"
                                   data-segment="${escapeFilterHtml(site.segment)}"
                                   ${checked ? 'checked' : ''}>
                            <span>${escapeFilterHtml(site.displayName)}</span>
                        </div>
                    `;
                });

                html += `
                        </div>
                    </div>
                `;
            });
        }

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
                        ${escapeFilterHtml(groupName)} (${subjectGroups[groupName].length})
                    </div>
                    <div class="subject-group-items">
            `;

            subjectGroups[groupName].forEach(subject => {
                html += `
                    <div class="subject-item">
                        <input type="checkbox"
                               class="subject-checkbox"
                               value="${escapeFilterHtml(subject)}"
                               data-group="${escapeFilterHtml(groupName)}">
                        <span>${escapeFilterHtml(subject)}</span>
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

        let isComposing = false;
        const applyFilter = (value) => {
            const searchTerm = String(value || '').toLowerCase();
            const items = document.querySelectorAll(itemSelector);
            items.forEach(item => {
                const text = item.textContent.toLowerCase();
                item.style.display = text.includes(searchTerm) ? '' : 'none';
            });
        };

        input.addEventListener('compositionstart', () => {
            isComposing = true;
        });

        input.addEventListener('compositionend', (e) => {
            isComposing = false;
            applyFilter(e.target.value);
        });

        input.addEventListener('input', (e) => {
            if (isComposing || e.isComposing) return;
            applyFilter(e.target.value);
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

        // 現場グループ化モード変更
        const siteGroupMode = document.getElementById('site-group-mode');
        if (siteGroupMode) {
            siteGroupMode.addEventListener('change', () => {
                this.renderSiteList();
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
        const container = document.querySelector('.sat-container');

        if (baseMonthGroup) {
            if (mode === 'same_year') {
                baseMonthGroup.style.display = 'block';
            } else {
                baseMonthGroup.style.display = 'none';
            }
        }

        if (container) {
            container.classList.toggle('sat-simple-view', mode === 'simple_view');
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
            managerId: document.getElementById('manager-id-filter')?.value.trim() || '',
            siteGroupMode: document.getElementById('site-group-mode').value,
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
        const managerIdInput = document.getElementById('manager-id-filter');
        if (managerIdInput) managerIdInput.value = '';
    }

    /**
     * 8桁の子要素を展開/折りたたみ
     */
    toggle8DigitChildren(treeId) {
        const childrenDiv = document.getElementById(treeId);
        const iconSpan = document.getElementById(`expand-icon-${treeId}`);

        if (childrenDiv.style.display === 'none') {
            childrenDiv.style.display = 'block';
            iconSpan.textContent = '▼';
        } else {
            childrenDiv.style.display = 'none';
            iconSpan.textContent = '▶';
        }
    }

    /**
     * 5桁チェックボックスの変更ハンドラー
     */
    handle5DigitCheckboxChange(checkbox) {
        const groupKey = checkbox.dataset.groupKey;
        const isChecked = checkbox.checked;

        // 配下の8桁チェックボックスを全て同じ状態にする
        const safeGroupKey = escapeCssAttrValue(groupKey);
        const children = document.querySelectorAll(`.site-checkbox-8digit[data-group-key="${safeGroupKey}"]`);
        children.forEach(child => {
            child.checked = isChecked;
        });

        this.updateSelectedSites();
    }

    /**
     * 8桁チェックボックスの変更ハンドラー
     */
    handle8DigitCheckboxChange(checkbox) {
        const groupKey = checkbox.dataset.groupKey;

        // 同じグループの8桁チェックボックスの状態を確認
        const safeGroupKey = escapeCssAttrValue(groupKey);
        const children = document.querySelectorAll(`.site-checkbox-8digit[data-group-key="${safeGroupKey}"]`);
        const allChecked = Array.from(children).every(cb => cb.checked);
        const noneChecked = Array.from(children).every(cb => !cb.checked);

        // 5桁チェックボックスの状態を更新
        const parent5Digit = document.querySelector(`.site-checkbox-5digit[data-group-key="${safeGroupKey}"]`);
        if (parent5Digit) {
            if (allChecked) {
                parent5Digit.checked = true;
                parent5Digit.indeterminate = false;
            } else if (noneChecked) {
                parent5Digit.checked = false;
                parent5Digit.indeterminate = false;
            } else {
                parent5Digit.checked = false;
                parent5Digit.indeterminate = true;
            }
        }

        this.updateSelectedSites();
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
