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
            comparisonMode,
            baseMonth,
            thresholdRate,
            thresholdAmount,
            thresholdCondition
        } = filters;

        // データ取得
        const currentData = dataManager.getData({ sites, subjects, months, segments });

        this.comparisonResults = [];

        currentData.forEach(item => {
            months.forEach(month => {
                const monthIndex = dataManager.getMonthIndex(month);
                if (monthIndex < 0 || monthIndex >= item.amounts.length) return;

                const currentValue = item.amounts[monthIndex];
                let comparisonValue = 0;
                let comparisonLabel = '';

                // 比較モード別の処理
                if (comparisonMode === 'prev_year') {
                    // 前年同月比較
                    const prevYearItem = dataManager.getPrevYearData(
                        item.contract_code,
                        item.subject_name
                    );
                    if (prevYearItem && prevYearItem.amounts[monthIndex] !== undefined) {
                        comparisonValue = prevYearItem.amounts[monthIndex];
                        comparisonLabel = `前年${month}月`;
                    }
                } else if (comparisonMode === 'same_year') {
                    // 同年度内指定月比較
                    const baseMonthIndex = dataManager.getMonthIndex(parseInt(baseMonth));
                    if (baseMonthIndex >= 0 && baseMonthIndex < item.amounts.length) {
                        comparisonValue = item.amounts[baseMonthIndex];
                        comparisonLabel = `${baseMonth}月`;
                    }
                } else if (comparisonMode === 'cumulative') {
                    // 累計比較
                    const currentCumulative = dataManager.calculateCumulative(item.amounts, month);
                    const prevYearItem = dataManager.getPrevYearData(
                        item.contract_code,
                        item.subject_name
                    );
                    if (prevYearItem) {
                        comparisonValue = dataManager.calculateCumulative(prevYearItem.amounts, month);
                        comparisonLabel = `前年累計（4月～${month}月）`;
                    }
                    // 累計モードでは現在値も累計に変更
                    const result = this.calculateDifference(currentCumulative, comparisonValue);
                    result.month = month;
                    result.item = item;
                    result.comparisonLabel = comparisonLabel;
                    result.isAnomaly = this.checkAnomaly(result, thresholdRate, thresholdAmount, thresholdCondition);
                    this.comparisonResults.push(result);
                    return;
                }

                // 差異計算
                const result = this.calculateDifference(currentValue, comparisonValue);
                result.month = month;
                result.item = item;
                result.comparisonLabel = comparisonLabel;
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
    getStatistics() {
        if (this.comparisonResults.length === 0) {
            return null;
        }

        const currentValues = this.comparisonResults.map(r => r.currentValue);
        const diffs = this.comparisonResults.map(r => r.diff);
        const diffRates = this.comparisonResults.map(r => r.diffRate);

        return {
            count: this.comparisonResults.length,
            anomalyCount: this.comparisonResults.filter(r => r.isAnomaly).length,
            currentValue: {
                sum: this.sum(currentValues),
                avg: this.average(currentValues),
                median: this.median(currentValues),
                max: Math.max(...currentValues),
                min: Math.min(...currentValues),
                stdDev: this.standardDeviation(currentValues)
            },
            diff: {
                sum: this.sum(diffs),
                avg: this.average(diffs),
                median: this.median(diffs),
                max: Math.max(...diffs),
                min: Math.min(...diffs),
                stdDev: this.standardDeviation(diffs)
            },
            diffRate: {
                avg: this.average(diffRates),
                median: this.median(diffRates),
                max: Math.max(...diffRates),
                min: Math.min(...diffRates),
                stdDev: this.standardDeviation(diffRates)
            }
        };
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
}

// グローバルインスタンス
const comparisonEngine = new ComparisonEngine();
