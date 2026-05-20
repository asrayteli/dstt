// 比較エンジン - データの比較・分析ロジック

class ComparisonEngine {
    constructor() {
        this.comparisonResults = [];
    }

    /**
     * フィルターに基づいて比較を実行
     */
    executeComparison(filters) {
        const {
            months,
            sites,
            subjects,
            segments,
            managerId,
            allowedContractCodes,
            siteGroupMode,
            comparisonMode,
            baseMonth,
            thresholdRate,
            thresholdAmount,
            thresholdCondition
        } = filters;

        // データ取得
        let currentData = dataManager.getData({ sites, subjects, months, segments, managerId, allowedContractCodes });

        // 5桁グループ化モードの場合はデータを合算
        if (siteGroupMode === '5digit') {
            currentData = dataManager.groupDataBy5Digit(currentData);
        }

        this.comparisonResults = [];

        currentData.forEach(item => {
            months.forEach(month => {
                const monthIndex = dataManager.getMonthIndex(month);
                if (monthIndex < 0 || monthIndex >= item.amounts.length) return;

                const currentValue = item.amounts[monthIndex];
                let comparisonValue = 0;
                let comparisonLabel = '';
                let hasComparison = true;

                // 比較モード別の処理
                if (comparisonMode === 'prev_year') {
                    // 前年同月比較
                    let prevYearItem;
                    if (siteGroupMode === '5digit') {
                        // 5桁モードの場合は5桁でグループ化した前年データを取得
                        prevYearItem = dataManager.getPrevYearData5Digit(
                            item.contract_code,
                            item.subject_name
                        );
                    } else {
                        // 8桁モードの場合は通常の前年データ取得
                        prevYearItem = dataManager.getPrevYearData(
                            item.contract_code,
                            item.subject_name
                        );
                    }
                    if (prevYearItem && prevYearItem.amounts[monthIndex] !== undefined) {
                        comparisonValue = prevYearItem.amounts[monthIndex];
                        comparisonLabel = `前年${month}月`;
                    }
                } else if (comparisonMode === 'same_year') {
                    // 同年度内指定月比較
                    // 基準月と同じ月はスキップ（差異0なので意味がない）
                    if (parseInt(month) === parseInt(baseMonth)) {
                        return;
                    }
                    const baseMonthIndex = dataManager.getMonthIndex(parseInt(baseMonth));
                    if (baseMonthIndex >= 0 && baseMonthIndex < item.amounts.length) {
                        comparisonValue = item.amounts[baseMonthIndex];
                        comparisonLabel = `${baseMonth}月`;
                    }
                } else if (comparisonMode === 'cumulative') {
                    // 累計比較モード
                    // 詳細一覧では月別の値を表示するため、前年同月比較と同じ処理
                    let prevYearItem;
                    if (siteGroupMode === '5digit') {
                        prevYearItem = dataManager.getPrevYearData5Digit(
                            item.contract_code,
                            item.subject_name
                        );
                    } else {
                        prevYearItem = dataManager.getPrevYearData(
                            item.contract_code,
                            item.subject_name
                        );
                    }
                    if (prevYearItem && prevYearItem.amounts[monthIndex] !== undefined) {
                        comparisonValue = prevYearItem.amounts[monthIndex];
                        comparisonLabel = `前年${month}月`;
                    }
                } else if (comparisonMode === 'simple_view') {
                    comparisonValue = currentValue;
                    comparisonLabel = '単純表示';
                    hasComparison = false;
                }

                // 差異計算
                const result = this.calculateDifference(currentValue, comparisonValue);
                result.month = month;
                result.item = item;
                result.comparisonLabel = comparisonLabel;
                result.hasComparison = hasComparison;
                result.isAnomaly = this.checkAnomaly(result, thresholdRate, thresholdAmount, thresholdCondition);

                this.comparisonResults.push(result);
            });
        });

        return this.comparisonResults;
    }

    /**
     * 差異を計算
     */
    calculateDifference(currentValue, comparisonValue) {
        const diff = currentValue - comparisonValue;
        const diffRate = comparisonValue !== 0 ? (diff / comparisonValue) * 100 : 0;

        return {
            currentValue: currentValue,
            comparisonValue: comparisonValue,
            diff: diff,
            diffRate: diffRate,
            diffAbs: Math.abs(diff),
            diffRateAbs: Math.abs(diffRate)
        };
    }

    /**
     * 異常値チェック
     */
    checkAnomaly(result, thresholdRate, thresholdAmount, condition) {
        const rateExceeded = result.diffRateAbs >= thresholdRate;
        const amountExceeded = result.diffAbs >= thresholdAmount;

        if (condition === 'and') {
            return rateExceeded && amountExceeded;
        } else {
            return rateExceeded || amountExceeded;
        }
    }

    /**
     * 利益分析データを生成
     *
     * 重要: 利益の定義について
     * - CSV上: 売上=正（収入）, 原価=負（支出）
     * - 符号反転処理: 表示用に原価を正に変換済み
     * - 利益計算: 利益 = 売上 - 原価（引き算）
     * - 原価は符号反転済みの正の値として扱う
     */
    getProfitAnalysis() {
        if (this.comparisonResults.length === 0) {
            return null;
        }

        // 売上と原価を分離
        const revenueResults = this.comparisonResults.filter(r => r.item.is_revenue);
        const costResults = this.comparisonResults.filter(r => !r.item.is_revenue);

        if (revenueResults.length === 0) {
            return { error: '売上データが選択されていません。科目選択で「売上」グループを選択してください。' };
        }

        // 月別・現場別にグループ化
        const grouped = {};

        // 売上を集計（正の値）
        revenueResults.forEach(result => {
            const key = `${result.item.contract_code}|${result.month}`;
            if (!grouped[key]) {
                grouped[key] = {
                    contractCode: result.item.contract_code,
                    siteName: result.item.site_name,
                    corpName: result.item.corp_name,
                    segment: dataManager.rawData.siteMapping[result.item.contract_code] || '未分類',
                    month: result.month,
                    revenue: 0,  // 売上（正）
                    revenueComparison: 0,  // 売上（正）
                    cost: 0,  // 原価（符号反転済みの正）
                    costComparison: 0,  // 原価（符号反転済みの正）
                    comparisonLabel: result.comparisonLabel
                };
            }
            grouped[key].revenue += result.currentValue;  // 売上（正）を加算
            grouped[key].revenueComparison += result.comparisonValue;  // 売上（正）を加算
        });

        // 原価を集計（符号反転済みの正の値として扱う）
        costResults.forEach(result => {
            const key = `${result.item.contract_code}|${result.month}`;
            if (!grouped[key]) {
                // 売上がない現場はスキップ
                return;
            }
            // 原価は既に符号反転済み（正の値）
            grouped[key].cost += result.currentValue;
            grouped[key].costComparison += result.comparisonValue;
        });

        // 利益と利益率を計算
        // 原価は負の値なので、利益 = 売上 + 原価（足し算）
        const profitData = Object.values(grouped).map(item => {
            const profit = item.revenue + item.cost;  // costが負なので足し算
            const profitComparison = item.revenueComparison + item.costComparison;
            const profitRate = item.revenue !== 0 ? (profit / item.revenue) * 100 : 0;
            const profitRateComparison = item.revenueComparison !== 0 ? (profitComparison / item.revenueComparison) * 100 : 0;

            return {
                ...item,
                profit: profit,
                profitComparison: profitComparison,
                profitDiff: profit - profitComparison,
                profitRate: profitRate,
                profitRateComparison: profitRateComparison,
                profitRateDiff: profitRate - profitRateComparison
            };
        });

        return profitData;
    }

    /**
     * 結果を取得
     */
    getResults() {
        return this.comparisonResults;
    }

    /**
     * 現場別サマリーを生成
     */
    getSiteSummary() {
        const summary = {};

        this.comparisonResults.forEach(result => {
            const siteKey = `${result.item.contract_code}|${result.item.site_name}|${result.item.corp_name}`;

            if (!summary[siteKey]) {
                summary[siteKey] = {
                    siteName: result.item.site_name,
                    corpName: result.item.corp_name,
                    contractCode: result.item.contract_code,
                    segment: dataManager.rawData.siteMapping[result.item.contract_code] || '未分類',
                    totalCurrent: 0,
                    totalComparison: 0,
                    totalDiff: 0,
                    anomalyCount: 0,
                    items: []
                };
            }

            summary[siteKey].totalCurrent += result.currentValue;
            summary[siteKey].totalComparison += result.comparisonValue;
            summary[siteKey].totalDiff += result.diff;
            if (result.isAnomaly) {
                summary[siteKey].anomalyCount++;
            }
            summary[siteKey].items.push(result);
        });

        // 差異率を計算
        Object.keys(summary).forEach(key => {
            const site = summary[key];
            site.totalDiffRate = site.totalComparison !== 0
                ? (site.totalDiff / site.totalComparison) * 100
                : 0;
        });

        return Object.values(summary);
    }

    /**
     * 科目別サマリーを生成
     */
    getSubjectSummary() {
        const summary = {};

        this.comparisonResults.forEach(result => {
            const subjectKey = result.item.subject_name;

            if (!summary[subjectKey]) {
                summary[subjectKey] = {
                    subjectName: subjectKey,
                    totalCurrent: 0,
                    totalComparison: 0,
                    totalDiff: 0,
                    anomalyCount: 0,
                    siteCount: new Set(),
                    items: []
                };
            }

            summary[subjectKey].totalCurrent += result.currentValue;
            summary[subjectKey].totalComparison += result.comparisonValue;
            summary[subjectKey].totalDiff += result.diff;
            if (result.isAnomaly) {
                summary[subjectKey].anomalyCount++;
            }
            summary[subjectKey].siteCount.add(result.item.contract_code);
            summary[subjectKey].items.push(result);
        });

        // 差異率とサイト数を計算
        Object.keys(summary).forEach(key => {
            const subject = summary[key];
            subject.totalDiffRate = subject.totalComparison !== 0
                ? (subject.totalDiff / subject.totalComparison) * 100
                : 0;
            subject.siteCount = subject.siteCount.size;
        });

        return Object.values(summary);
    }

    /**
     * 統計情報を生成
     */
    getStatistics(filters) {
        if (this.comparisonResults.length === 0) {
            return null;
        }
        const isSimpleMode = !!(filters && filters.comparisonMode === 'simple_view');

        let currentValues, comparisonValues, diffs, diffRates;
        let revenueValues, costValues;
        let revenueCount = 0;
        let costCount = 0;

        // 累計比較モードの場合、累計値で統計を計算
        if (filters && filters.comparisonMode === 'cumulative') {
            // 現場・科目ごとに累計を計算
            const cumulativeResults = this.calculateCumulativeStatistics(filters);
            currentValues = cumulativeResults.map(r => r.currentValue);
            comparisonValues = cumulativeResults.map(r => r.comparisonValue);
            diffs = cumulativeResults.map(r => r.diff);
            diffRates = cumulativeResults.map(r => r.diffRate);

            const revenueResults = cumulativeResults.filter(r => r.item.is_revenue);
            const costResults = cumulativeResults.filter(r => !r.item.is_revenue);
            revenueValues = revenueResults.map(r => r.currentValue);
            costValues = costResults.map(r => r.currentValue);
            revenueCount = revenueResults.length;
            costCount = costResults.length;
        } else {
            // 通常モード：月別の値で統計を計算
            currentValues = this.comparisonResults.map(r => r.currentValue);
            comparisonValues = this.comparisonResults.map(r => r.comparisonValue);
            diffs = this.comparisonResults.map(r => r.diff);
            diffRates = this.comparisonResults.map(r => r.diffRate);

            // 売上と原価を分離
            const revenueResults = this.comparisonResults.filter(r => r.item.is_revenue);
            const costResults = this.comparisonResults.filter(r => !r.item.is_revenue);
            revenueValues = revenueResults.map(r => r.currentValue);
            costValues = costResults.map(r => r.currentValue);
            revenueCount = revenueResults.length;
            costCount = costResults.length;
        }

        // 四分位数を計算
        const q1 = this.percentile(currentValues, 25);
        const q3 = this.percentile(currentValues, 75);
        const iqr = q3 - q1;

        return {
            isSimpleMode: isSimpleMode,
            count: this.comparisonResults.length,
            anomalyCount: this.comparisonResults.filter(r => r.isAnomaly).length,
            revenueCount: revenueCount,
            costCount: costCount,
            currentValue: {
                sum: this.sum(currentValues),
                avg: this.average(currentValues),
                median: this.median(currentValues),
                max: Math.max(...currentValues),
                min: Math.min(...currentValues),
                stdDev: this.standardDeviation(currentValues),
                q1: q1,
                q3: q3,
                iqr: iqr,
                range: Math.max(...currentValues) - Math.min(...currentValues),
                variance: this.variance(currentValues)
            },
            comparisonValue: {
                sum: this.sum(comparisonValues),
                avg: this.average(comparisonValues),
                median: this.median(comparisonValues),
                max: Math.max(...comparisonValues),
                min: Math.min(...comparisonValues),
                stdDev: this.standardDeviation(comparisonValues)
            },
            diff: {
                sum: this.sum(diffs),
                avg: this.average(diffs),
                median: this.median(diffs),
                max: Math.max(...diffs),
                min: Math.min(...diffs),
                stdDev: this.standardDeviation(diffs),
                positiveCount: diffs.filter(d => d > 0).length,
                negativeCount: diffs.filter(d => d < 0).length,
                zeroCount: diffs.filter(d => d === 0).length
            },
            diffRate: {
                avg: this.average(diffRates),
                median: this.median(diffRates),
                max: Math.max(...diffRates),
                min: Math.min(...diffRates),
                stdDev: this.standardDeviation(diffRates)
            },
            revenue: revenueValues.length > 0 ? {
                sum: this.sum(revenueValues),
                avg: this.average(revenueValues),
                count: revenueValues.length
            } : null,
            cost: costValues.length > 0 ? {
                sum: this.sum(costValues),
                avg: this.average(costValues),
                count: costValues.length
            } : null
        };
    }

    /**
     * 累計比較モード用の統計データを計算
     */
    calculateCumulativeStatistics(filters) {
        const { months, sites, subjects, segments } = filters;
        const currentData = dataManager.getData({ sites, subjects, months, segments });
        const cumulativeResults = [];

        // 選択された最大月を特定（累計の対象月）
        const selectedMonths = months.map(m => parseInt(m)).sort((a, b) => {
            // 4月始まりでソート
            const aIdx = dataManager.getMonthIndex(a);
            const bIdx = dataManager.getMonthIndex(b);
            return aIdx - bIdx;
        });
        const maxMonth = selectedMonths[selectedMonths.length - 1];

        // 現場・科目ごとに累計を計算
        currentData.forEach(item => {
            // 当期の累計値を計算
            const currentCumulative = dataManager.calculateCumulative(item.amounts, maxMonth);

            // 前年の累計値を計算
            const prevYearItem = dataManager.getPrevYearData(item.contract_code, item.subject_name);
            let comparisonCumulative = 0;
            if (prevYearItem) {
                comparisonCumulative = dataManager.calculateCumulative(prevYearItem.amounts, maxMonth);
            }

            // 差異を計算
            const diff = currentCumulative - comparisonCumulative;
            const diffRate = comparisonCumulative !== 0 ? (diff / comparisonCumulative) * 100 : 0;

            cumulativeResults.push({
                item: item,
                currentValue: currentCumulative,
                comparisonValue: comparisonCumulative,
                diff: diff,
                diffRate: diffRate
            });
        });

        return cumulativeResults;
    }

    /**
     * 月次推移データを生成（グラフ用）
     */
    getMonthlyTrend(filters) {
        const { sites, subjects } = filters;
        const currentData = dataManager.getData({ sites, subjects });

        const monthOrder = [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3];
        const trend = {
            months: monthOrder,
            datasets: []
        };

        // 科目ごとにデータセットを作成
        const subjectMap = {};
        currentData.forEach(item => {
            if (!subjectMap[item.subject_name]) {
                subjectMap[item.subject_name] = Array(12).fill(0);
            }

            item.amounts.forEach((amount, index) => {
                subjectMap[item.subject_name][index] += amount;
            });
        });

        Object.keys(subjectMap).forEach(subjectName => {
            trend.datasets.push({
                label: subjectName,
                data: subjectMap[subjectName]
            });
        });

        return trend;
    }

    // 統計関数

    sum(arr) {
        return arr.reduce((sum, val) => sum + val, 0);
    }

    average(arr) {
        return arr.length > 0 ? this.sum(arr) / arr.length : 0;
    }

    median(arr) {
        if (arr.length === 0) return 0;
        const sorted = [...arr].sort((a, b) => a - b);
        const mid = Math.floor(sorted.length / 2);
        return sorted.length % 2 === 0
            ? (sorted[mid - 1] + sorted[mid]) / 2
            : sorted[mid];
    }

    standardDeviation(arr) {
        if (arr.length === 0) return 0;
        const avg = this.average(arr);
        const squareDiffs = arr.map(val => Math.pow(val - avg, 2));
        const avgSquareDiff = this.average(squareDiffs);
        return Math.sqrt(avgSquareDiff);
    }

    variance(arr) {
        if (arr.length === 0) return 0;
        const avg = this.average(arr);
        const squareDiffs = arr.map(val => Math.pow(val - avg, 2));
        return this.average(squareDiffs);
    }

    percentile(arr, p) {
        if (arr.length === 0) return 0;
        const sorted = [...arr].sort((a, b) => a - b);
        const index = (p / 100) * (sorted.length - 1);
        const lower = Math.floor(index);
        const upper = Math.ceil(index);
        const weight = index % 1;

        if (lower === upper) {
            return sorted[lower];
        }

        return sorted[lower] * (1 - weight) + sorted[upper] * weight;
    }
}

// グローバルインスタンス
const comparisonEngine = new ComparisonEngine();
