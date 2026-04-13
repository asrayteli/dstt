// データマネージャー - 全データの管理を担当

class DataManager {
    constructor() {
        this.rawData = {
            currentYear: [],
            prevYear: [],
            siteMapping: {},
            siteMaster: {}
        };
        this.processedData = [];
        this.metadata = {
            sites: new Set(),
            subjects: new Set(),
            segments: new Set(),
            months: [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3]
        };
    }

    /**
     * サーバーからのレスポンスデータを保存
     */
    setData(responseData) {
        this.rawData.currentYear = responseData.current_year || [];
        this.rawData.prevYear = responseData.prev_year || [];
        this.rawData.siteMapping = responseData.site_mapping || {};
        this.rawData.siteMaster = responseData.site_master || {};

        this.extractMetadata();
    }

    /**
     * メタデータを抽出（現場、科目、セグメント）
     */
    extractMetadata() {
        this.metadata.sites = new Set();
        this.metadata.subjects = new Set();
        this.metadata.segments = new Set();

        this.rawData.currentYear.forEach(item => {
            // 現場を追加（契約コード + 現場名 + 法人名の組み合わせ）
            const siteKey = `${item.contract_code}|${item.site_name}|${item.corp_name}`;
            this.metadata.sites.add(siteKey);

            // 科目を追加
            if (item.subject_name) {
                this.metadata.subjects.add(item.subject_name);
            }

            // セグメントを追加（現場表がある場合）
            if (this.rawData.siteMapping[item.contract_code]) {
                this.metadata.segments.add(this.rawData.siteMapping[item.contract_code]);
            }
        });
    }

    /**
     * 8桁コードから5桁コードを抽出
     */
    get5DigitCode(contractCode) {
        return contractCode.substring(0, 5);
    }

    normalizeManagerId(value) {
        const text = String(value || '').trim();
        if (!text) return '';
        if (/^\d+$/.test(text)) {
            return text.replace(/^0+/, '') || '0';
        }
        return text;
    }

    managerIdsMatch(left, right) {
        const leftText = String(left || '').trim();
        const rightText = String(right || '').trim();
        if (!leftText || !rightText) return false;
        return leftText === rightText || this.normalizeManagerId(leftText) === this.normalizeManagerId(rightText);
    }

    /**
     * 現場リストを取得
     */
    getSites() {
        const sites = [];
        this.metadata.sites.forEach(siteKey => {
            const [contractCode, siteName, corpName] = siteKey.split('|');
            sites.push({
                contractCode,
                siteName,
                corpName,
                segment: this.rawData.siteMapping[contractCode] || '未分類',
                displayName: `${corpName} (${siteName})`
            });
        });
        return sites.sort((a, b) => a.displayName.localeCompare(b.displayName, 'ja'));
    }

    /**
     * 5桁コードのセグメントを多数決で決定
     */
    getSegmentFor5Digit(code5) {
        const segmentCounts = {};

        // 同じ5桁コードの8桁コードのセグメントをカウント
        this.metadata.sites.forEach(siteKey => {
            const [contractCode] = siteKey.split('|');
            if (this.get5DigitCode(contractCode) === code5) {
                const segment = this.rawData.siteMapping[contractCode] || '未分類';
                segmentCounts[segment] = (segmentCounts[segment] || 0) + 1;
            }
        });

        // 最も多いセグメントを取得
        const segments = Object.keys(segmentCounts);
        if (segments.length === 0) return '未分類';
        if (segments.length === 1) return segments[0];

        // 複数あり、同数の場合は自動判定
        const sortedSegments = segments.sort((a, b) => segmentCounts[b] - segmentCounts[a]);
        const maxCount = segmentCounts[sortedSegments[0]];
        const tiedSegments = sortedSegments.filter(s => segmentCounts[s] === maxCount);

        if (tiedSegments.length > 1) {
            const classifiedSegments = tiedSegments.filter(s => s !== '未分類');
            if (classifiedSegments.length === 1) {
                return classifiedSegments[0];
            }
            return '混在';
        }

        return sortedSegments[0];
    }

    /**
     * 5桁グループ化された現場リストを取得
     */
    getSites5DigitGrouped() {
        const grouped = {};

        // まず5桁コードでグループ化
        const code5Groups = {};
        this.metadata.sites.forEach(siteKey => {
            const [contractCode, siteName, corpName] = siteKey.split('|');
            const code5 = this.get5DigitCode(contractCode);

            if (!code5Groups[code5]) {
                code5Groups[code5] = [];
            }

            code5Groups[code5].push({
                contractCode,
                siteName,
                corpName,
                segment: this.rawData.siteMapping[contractCode] || '未分類',
                displayName: `${corpName} (${siteName})`
            });
        });

        // セグメント別に整理（同一5桁でもセグメント単位で分割）
        Object.keys(code5Groups).forEach(code5 => {
            const sites = code5Groups[code5];
            const segmentBuckets = {};
            sites.forEach((site) => {
                const segment = site.segment || '未分類';
                if (!segmentBuckets[segment]) {
                    segmentBuckets[segment] = [];
                }
                segmentBuckets[segment].push(site);
            });

            Object.keys(segmentBuckets).forEach((segment) => {
                if (!grouped[segment]) {
                    grouped[segment] = [];
                }

                const segmentSites = segmentBuckets[segment].sort((a, b) => a.displayName.localeCompare(b.displayName, 'ja'));
                const representativeSite = segmentSites[0];

                grouped[segment].push({
                    code5: code5,
                    siteName: representativeSite.siteName,
                    corpName: representativeSite.corpName,
                    segment: segment,
                    children: segmentSites
                });
            });
        });

        // 各セグメント内でソート（法人名→現場名の順）
        Object.keys(grouped).forEach(segment => {
            grouped[segment].sort((a, b) => {
                const corpCompare = a.corpName.localeCompare(b.corpName, 'ja');
                if (corpCompare !== 0) return corpCompare;
                return a.siteName.localeCompare(b.siteName, 'ja');
            });
        });

        return grouped;
    }

    /**
     * 科目リストを取得（グループ化）
     */
    getSubjects() {
        // 経費マッピング（月次ジェネレーターと同じ）
        const expenseMapping = {
            "外注費": "材料",
            "消耗品費": "材料",
            "保健衛生費": "材料",
            "燃料費": "材料",
            "タイヤ費": "材料",
            "被服費": "材料",
            "修繕費": "材料",
            "賃借料": "材料",
            "保険料": "材料",
            "家賃地代": "材料",
            "減価償却費": "材料",
            "減価償却費（ﾘｰｽ資産）": "材料",
            "租税公課": "材料",
            "支払手数料": "材料",
            "給料": "労務",
            "賞与": "労務",
            "賞与引当金繰入額": "労務",
            "退職給付費用": "労務",
            "法定福利費": "労務",
            "福利厚生費": "労務",
            "通勤費": "労務",
            "関係会社外注費": "労務",
            "労務費振替": "労務",
            "広告宣伝費": "経費",
            "募集費": "経費",
            "旅費交通費": "経費",
            "新聞図書費": "経費",
            "水道光熱費": "経費",
            "傭車費": "経費",
            "運賃": "経費",
            "通信費": "経費",
            "リース料": "経費",
            "交際接待費": "経費",
            "文化教育費": "経費",
            "事故負担金": "経費",
            "寄付金": "経費",
            "会費": "経費",
            "雑費": "経費",
        };

        const groups = {
            "売上": [],
            "材料": [],
            "労務": [],
            "経費": [],
            "その他": []
        };

        this.metadata.subjects.forEach(subject => {
            // 売上（基本請負料、その他請負料）を優先的に売上グループに
            if (subject === "基本請負料" || subject === "その他請負料") {
                groups["売上"].push(subject);
            } else if (subject === "間接原価") {
                groups["労務"].push(subject);
            } else {
                const category = expenseMapping[subject];
                if (category) {
                    groups[category].push(subject);
                } else if (subject.includes("売上")) {
                    groups["売上"].push(subject);
                } else {
                    groups["その他"].push(subject);
                }
            }
        });

        // 各グループをソート（売上グループは基本請負料を先頭に）
        Object.keys(groups).forEach(key => {
            if (key === "売上") {
                groups[key].sort((a, b) => {
                    if (a === "基本請負料") return -1;
                    if (b === "基本請負料") return 1;
                    if (a === "その他請負料") return -1;
                    if (b === "その他請負料") return 1;
                    return a.localeCompare(b, 'ja');
                });
            } else {
                groups[key].sort((a, b) => a.localeCompare(b, 'ja'));
            }
        });

        return groups;
    }

    /**
     * 指定した条件でデータを取得
     */
    getData(filters) {
        const {
            sites = [],
            subjects = [],
            months = [],
            segments = [],
            managerId = '',
            allowedContractCodes = []
        } = filters;
        const allowedSet = new Set((allowedContractCodes || []).map((code) => String(code || '').trim()).filter(Boolean));

        let filteredData = this.rawData.currentYear.filter(item => {
            const contractCode = String(item.contract_code || '').trim();

            if (allowedSet.size > 0 && !allowedSet.has(contractCode)) {
                return false;
            }

            // 現場フィルタ
            if (sites.length > 0) {
                const siteKey = `${item.contract_code}|${item.site_name}|${item.corp_name}`;
                if (!sites.includes(siteKey)) return false;
            }

            // 科目フィルタ
            if (subjects.length > 0 && !subjects.includes(item.subject_name)) {
                return false;
            }

            // セグメントフィルタ
            if (segments.length > 0) {
                const segment = this.rawData.siteMapping[item.contract_code];
                if (!segment || !segments.includes(segment)) return false;
            }

            if (managerId) {
                const siteMaster = this.rawData.siteMaster[item.contract_code] || {};
                if (!this.managerIdsMatch(siteMaster.site_manager_id, managerId)) return false;
            }

            return true;
        });

        return filteredData;
    }

    /**
     * データを5桁コードでグループ化（金額合算）
     */
    groupDataBy5Digit(data) {
        const grouped = {};

        data.forEach(item => {
            const code5 = this.get5DigitCode(item.contract_code);
            const key = `${code5}|${item.subject_name}`;

            if (!grouped[key]) {
                // 新しいグループを作成
                grouped[key] = {
                    contract_code: code5,
                    site_name: item.site_name, // 代表の現場名
                    corp_name: item.corp_name,
                    subject_name: item.subject_name,
                    is_revenue: item.is_revenue,
                    amounts: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0], // 12ヶ月分
                    original_codes: [] // 元の8桁コードのリスト
                };
            }

            // 金額を合算
            for (let i = 0; i < 12; i++) {
                grouped[key].amounts[i] += item.amounts[i];
            }

            // 元の8桁コードを記録
            if (!grouped[key].original_codes.includes(item.contract_code)) {
                grouped[key].original_codes.push(item.contract_code);
            }
        });

        return Object.values(grouped);
    }

    /**
     * 前年データを5桁コードでグループ化して取得
     */
    getPrevYearData5Digit(code5, subjectName) {
        if (!this.rawData.prevYear) return null;

        // 同じ5桁コードの前年データを全て取得
        const matchingData = this.rawData.prevYear.filter(item =>
            this.get5DigitCode(item.contract_code) === code5 &&
            item.subject_name === subjectName
        );

        if (matchingData.length === 0) return null;

        // 金額を合算
        const result = {
            contract_code: code5,
            site_name: matchingData[0].site_name,
            corp_name: matchingData[0].corp_name,
            subject_name: subjectName,
            is_revenue: matchingData[0].is_revenue,
            amounts: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
        };

        matchingData.forEach(item => {
            for (let i = 0; i < 12; i++) {
                result.amounts[i] += item.amounts[i];
            }
        });

        return result;
    }

    /**
     * 前年データを取得
     */
    getPrevYearData(contractCode, subjectName) {
        if (!this.rawData.prevYear) return null;

        return this.rawData.prevYear.find(item =>
            item.contract_code === contractCode &&
            item.subject_name === subjectName
        );
    }

    /**
     * 月のインデックスを取得（4月=0, 5月=1, ..., 3月=11）
     */
    getMonthIndex(month) {
        const monthOrder = [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3];
        return monthOrder.indexOf(parseInt(month));
    }

    /**
     * 月の表示名を取得
     */
    getMonthName(month) {
        return `${month}月`;
    }

    /**
     * 累計を計算（4月から指定月まで）
     */
    calculateCumulative(amounts, targetMonth) {
        const monthIndex = this.getMonthIndex(targetMonth);
        if (monthIndex < 0) return 0;

        return amounts.slice(0, monthIndex + 1).reduce((sum, val) => sum + val, 0);
    }

    /**
     * データが読み込まれているか
     */
    hasData() {
        return this.rawData.currentYear.length > 0;
    }

    /**
     * 前年データがあるか
     */
    hasPrevYearData() {
        return this.rawData.prevYear && this.rawData.prevYear.length > 0;
    }

    /**
     * 現場表データがあるか
     */
    hasSiteMapping() {
        return Object.keys(this.rawData.siteMapping).length > 0;
    }

    /**
     * データをリセット
     */
    reset() {
        this.rawData = {
            currentYear: [],
            prevYear: [],
            siteMapping: {},
            siteMaster: {}
        };
        this.processedData = [];
        this.metadata = {
            sites: new Set(),
            subjects: new Set(),
            segments: new Set(),
            months: [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3]
        };
    }
}

// グローバルインスタンス
const dataManager = new DataManager();
