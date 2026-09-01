/* 見積書作成ツールの純粋ロジック（app/templates/quote_maker.html の QM_CORE）を
   ブラウザ無しで検証する。tests/test_quote_maker_core.py から Node で実行される。
   使い方: node tests/quote_maker_core.test.js <抽出済みcore.js> */
'use strict';
const fs = require('fs');
const path = process.argv[2];
if (!path) { console.error('usage: node quote_maker_core.test.js <core.js>'); process.exit(2); }

const root = {};
new Function('window', fs.readFileSync(path, 'utf8') + '\n')(root);
const C = root.QM_CORE;
if (!C) { console.error('QM_CORE was not exported'); process.exit(2); }

let failures = 0, checks = 0;
function ok(cond, label) {
  checks++;
  if (!cond) { failures++; console.error('FAIL: ' + label); }
}
function eq(actual, expected, label) {
  const a = JSON.stringify(actual), b = JSON.stringify(expected);
  checks++;
  if (a !== b) { failures++; console.error('FAIL: ' + label + '\n  expected ' + b + '\n  actual   ' + a); }
}
/* 表の形を "内容(cs x rs)" のグリッド文字列にして比較しやすくする（covered は '.'）*/
function shape(c) {
  return c.rows.map(r => r.cells.map(cell =>
    cell.covered ? '.' : (cell.html || '-') + (cell.cs > 1 || cell.rs > 1 ? '[' + cell.cs + 'x' + cell.rs + ']' : '')
  ).join('|')).join(' / ');
}
function grid(rows, cols) {
  const c = { columns: [], rows: [] };
  for (let x = 0; x < cols; x++) c.columns.push({ width: 100 / cols });
  for (let r = 0; r < rows; r++) {
    const cells = [];
    for (let x = 0; x < cols; x++) cells.push(C.tblCell({ html: 'r' + r + 'c' + x }));
    c.rows.push({ cells: cells });
  }
  return C.normalizeTable(c);
}
/** 結合の整合性（covered の数 = 各アンカーの占有面積 - アンカー数）を検証する */
function invariant(c, label) {
  const R = c.rows.length, Cn = c.columns.length;
  let anchors = 0, area = 0, covered = 0;
  const seen = new Array(R * Cn).fill(0);
  for (let r = 0; r < R; r++) {
    ok(c.rows[r].cells.length === Cn, label + ': 行' + r + 'のセル数が列数と一致');
    for (let x = 0; x < Cn; x++) {
      const cell = c.rows[r].cells[x];
      if (cell.covered) { covered++; ok(cell.cs === 1 && cell.rs === 1, label + ': coveredセルのspanは1'); continue; }
      anchors++; area += cell.cs * cell.rs;
      ok(r + cell.rs <= R && x + cell.cs <= Cn, label + ': 結合が表からはみ出さない');
      for (let dr = 0; dr < cell.rs; dr++) for (let dx = 0; dx < cell.cs; dx++) seen[(r + dr) * Cn + (x + dx)]++;
    }
  }
  eq(area - anchors, covered, label + ': 覆われたセル数が結合面積と一致');
  ok(seen.every(v => v === 1), label + ': すべてのマスがちょうど1回覆われる');
}

/* ---------- ブロック探索（2段組の入れ子） ---------- */
{
  const nested = { id: 'n1', type: 'text', content: { html: 'x' } };
  const cols = { id: 'c1', type: 'columns', content: { cols: [{ blocks: [nested] }, { html: '' }] } };
  const blocks = [{ id: 't1', type: 'text', content: {} }, cols];
  eq(C.locateIn(blocks, 't1').index, 0, 'locateIn: ルートのブロック');
  eq(C.locateIn(blocks, 'n1').index, 0, 'locateIn: 入れ子のブロック');
  ok(C.locateIn(blocks, 'n1').parent === cols, 'locateIn: 親が2段組');
  ok(C.locateIn(blocks, 'missing') === null, 'locateIn: 無いIDは null');
  eq(C.depthOf(blocks, 't1'), 0, 'depthOf: ルートは0');
  eq(C.depthOf(blocks, 'n1'), 1, 'depthOf: 入れ子は1');
  eq(C.depthOf(blocks, 'zzz'), -1, 'depthOf: 無いIDは-1');
  ok(C.subtreeHas(cols, 'n1'), 'subtreeHas: 子孫を検出');
  ok(C.subtreeHas(cols, 'c1'), 'subtreeHas: 自分自身も true');
  ok(!C.subtreeHas(cols, 't1'), 'subtreeHas: 兄弟は false');
  let count = 0; C.walkBlocks(blocks, () => count++);
  eq(count, 3, 'walkBlocks: 入れ子も含めて巡回');
  // 循環しない不正データでも落ちない
  C.walkBlocks([null, 5, { id: 'x', type: 'columns', content: { cols: [null, { blocks: null }] } }], () => { });
  ok(true, 'walkBlocks: 不正データでも例外にならない');
}

/* ---------- 旧形式の表からの移行 ---------- */
{
  const c = { rows: [{ label: '契約期間', value: '7月' }, { label: '運行日時', value: '' }], labelWidth: 30, labelShade: true };
  C.normalizeTable(c);
  eq(c.columns.length, 2, '移行: 2列になる');
  eq(c.columns[0].width, 30, '移行: ラベル列幅を引き継ぐ');
  eq(c.columns[1].width, 70, '移行: 残り幅');
  eq(c.rows[0].cells[0].html, '契約期間', '移行: ラベルが1列目');
  eq(c.rows[0].cells[1].html, '7月', '移行: 値が2列目');
  ok(c.rows[0].cells[0].shade === true, '移行: ラベル列の背景色を引き継ぐ');
  ok(!('labelWidth' in c), '移行: 旧キーを削除');
  invariant(c, '移行後');
  // 2回目の正規化で壊れない（冪等）
  const before = JSON.stringify(c);
  C.normalizeTable(c);
  eq(JSON.stringify(c), before, '正規化は冪等');
}
{
  const c = C.normalizeTable({});
  ok(c.columns.length >= 1 && c.rows.length >= 1, '空の content でも表として成立');
  invariant(c, '空からの正規化');
}
{
  // 行ごとにセル数がばらばらでも列数へ揃える
  const c = C.normalizeTable({ columns: [{ width: 50 }, { width: 50 }], rows: [{ cells: [C.tblCell({ html: 'a' })] }, { cells: [C.tblCell({}), C.tblCell({}), C.tblCell({ html: 'x' })] }] });
  eq(c.columns.length, 3, '欠けた列定義を補う');
  c.rows.forEach((r, i) => eq(r.cells.length, 3, '行' + i + 'のセル数を揃える'));
  invariant(c, 'セル数の補正');
}

/* ---------- 結合 ---------- */
{
  const c = grid(3, 3);
  ok(C.mergeCells(c, { r1: 0, c1: 0, r2: 1, c2: 1 }), '2x2の結合ができる');
  eq(c.rows[0].cells[0].cs, 2, '結合後 colspan=2');
  eq(c.rows[0].cells[0].rs, 2, '結合後 rowspan=2');
  eq(c.rows[0].cells[0].html, 'r0c0<br>r0c1<br>r1c0<br>r1c1', '結合セルの中身を改行で連結');
  ok(c.rows[0].cells[1].covered && c.rows[1].cells[0].covered && c.rows[1].cells[1].covered, '飲み込まれたセルは covered');
  invariant(c, '2x2結合');

  // 結合セルに重なる選択は、結合全体を含む長方形へ広がる
  const r = C.expandRect(c, { r1: 1, c1: 1, r2: 2, c2: 2 });
  eq(r, { r1: 0, c1: 0, r2: 2, c2: 2 }, 'expandRect: 結合を含む長方形に広がる');

  ok(C.splitCells(c, { r1: 0, c1: 0, r2: 0, c2: 0 }), '結合解除ができる');
  eq(shape(c), 'r0c0<br>r0c1<br>r1c0<br>r1c1|-|r0c2 / -|-|r1c2 / r2c0|r2c1|r2c2', '解除後は元の升目に戻る（中身は結合先に残る）');
  invariant(c, '結合解除後');
  ok(!C.splitCells(c, { r1: 0, c1: 0, r2: 0, c2: 0 }), '結合が無ければ解除は false');
  ok(!C.mergeCells(c, { r1: 1, c1: 1, r2: 1, c2: 1 }), '1セルだけの結合は何もしない');
}
{
  // 空セルだらけの結合では余計な <br> を作らない
  const c = C.normalizeTable({ columns: [{ width: 50 }, { width: 50 }], rows: [{ cells: [C.tblCell({ html: '合計' }), C.tblCell({ html: '' })] }] });
  C.mergeCells(c, { r1: 0, c1: 0, r2: 0, c2: 1 });
  eq(c.rows[0].cells[0].html, '合計', '空セルとの結合で改行が増えない');
}
{
  // 結合をまたぐ結合（重なり）でも整合性が保たれる
  const c = grid(4, 4);
  C.mergeCells(c, { r1: 0, c1: 0, r2: 1, c2: 1 });
  C.mergeCells(c, { r1: 1, c1: 1, r2: 2, c2: 2 });
  invariant(c, '重なる結合');
}

/* ---------- 行・列の追加と削除 ---------- */
{
  const c = grid(2, 2);
  ok(C.insertCol(c, 2), '列を末尾に追加できる');
  eq(c.columns.length, 3, '列が増える');
  c.rows.forEach((r, i) => eq(r.cells.length, 3, '行' + i + 'にもセルが増える'));
  eq(Math.round(c.columns.reduce((s, k) => s + k.width, 0)), 100, '列幅の合計は100');
  invariant(c, '列追加');

  ok(C.insertRow(c, 0), '行を先頭に追加できる');
  eq(c.rows.length, 3, '行が増える');
  eq(c.rows[0].cells[0].html, '', '追加された行は空');
  invariant(c, '行追加');
}
{
  // 横結合をまたぐ列追加は結合を広げる
  const c = grid(2, 3);
  C.mergeCells(c, { r1: 0, c1: 0, r2: 0, c2: 2 });
  C.insertCol(c, 1);
  eq(c.columns.length, 4, '列追加後の列数');
  eq(c.rows[0].cells[0].cs, 4, 'またぐ横結合は広がる');
  invariant(c, '結合をまたぐ列追加');
}
{
  // 縦結合をまたぐ行追加は結合を伸ばす
  const c = grid(3, 2);
  C.mergeCells(c, { r1: 0, c1: 0, r2: 2, c2: 0 });
  C.insertRow(c, 1);
  eq(c.rows.length, 4, '行追加後の行数');
  eq(c.rows[0].cells[0].rs, 4, 'またぐ縦結合は伸びる');
  invariant(c, '結合をまたぐ行追加');
}
{
  // 縦結合の先頭行を消すと、中身は次の行へ引き継がれる
  const c = grid(3, 2);
  C.mergeCells(c, { r1: 0, c1: 0, r2: 2, c2: 0 });
  const merged = c.rows[0].cells[0].html;
  ok(C.deleteRow(c, 0), '行を削除できる');
  eq(c.rows.length, 2, '行が減る');
  eq(c.rows[0].cells[0].rs, 2, '結合の高さが1つ減る');
  eq(c.rows[0].cells[0].html, merged, '結合セルの中身は残る');
  invariant(c, '結合の先頭行を削除');
}
{
  // 縦結合をまたぐ中間行の削除
  const c = grid(4, 2);
  C.mergeCells(c, { r1: 0, c1: 0, r2: 2, c2: 0 });
  C.deleteRow(c, 1);
  eq(c.rows[0].cells[0].rs, 2, 'またぐ行の削除で結合が縮む');
  invariant(c, '結合をまたぐ行削除');
}
{
  const c = grid(2, 3);
  C.mergeCells(c, { r1: 0, c1: 0, r2: 0, c2: 1 });
  ok(C.deleteCol(c, 0), '列を削除できる');
  eq(c.columns.length, 2, '列が減る');
  eq(c.rows[0].cells[0].cs, 1, '結合の幅が1つ減る');
  eq(Math.round(c.columns.reduce((s, k) => s + k.width, 0)), 100, '削除後も列幅の合計は100');
  invariant(c, '結合の先頭列を削除');
}
{
  const c = grid(1, 1);
  ok(!C.deleteRow(c, 0), '最後の1行は削除できない');
  ok(!C.deleteCol(c, 0), '最後の1列は削除できない');
}
{
  // 上限を超える追加は拒否される
  const c = grid(1, C.MAX_COLS);
  ok(!C.insertCol(c, 0), '列数の上限を超えて追加しない');
}
{
  // 行の入れ替え
  const c = grid(3, 2);
  ok(C.moveRow(c, 0, 1), '行を下へ移動できる');
  eq(c.rows[0].cells[0].html, 'r1c0', '入れ替わっている');
  ok(!C.moveRow(c, 0, -1), '先頭より上へは移動しない');
  ok(!C.moveRow(c, 2, 1), '末尾より下へは移動しない');
  const m = grid(3, 2);
  C.mergeCells(m, { r1: 0, c1: 0, r2: 1, c2: 0 });
  ok(!C.moveRow(m, 0, 1), '縦結合に絡む行は入れ替えない');
}
{
  // 不正な span をもつデータを読み込んでも表が壊れない
  const c = C.normalizeTable({
    columns: [{ width: 50 }, { width: 50 }],
    rows: [{ cells: [C.tblCell({ html: 'a', cs: 9, rs: 9 }), C.tblCell({ html: 'b' })] },
    { cells: [C.tblCell({ html: 'c' }), C.tblCell({ html: 'd', rs: 5 })] }]
  });
  invariant(c, '過大な span の切り詰め');
  eq(c.rows[0].cells[0].cs, 2, 'はみ出す colspan を列数に収める');
  eq(c.rows[0].cells[0].rs, 2, 'はみ出す rowspan を行数に収める');
}
{
  // makeTableContent（ひな形の生成）
  const c = C.makeTableContent(['1.契約期間', '2.運行コース'], { labelWidth: 24 });
  eq(c.rows.length, 2, 'ひな形: ラベルの数だけ行ができる');
  eq(c.rows[0].cells[0].html, '1.契約期間', 'ひな形: ラベルが入る');
  eq(c.columns[0].width, 24, 'ひな形: ラベル列幅');
  invariant(c, 'ひな形');
  eq(C.makeTableContent([]).rows.length, 1, 'ひな形: ラベルが無くても1行');
  eq(C.makeTableContent(['<script>x</script>']).rows[0].cells[0].html, '&lt;script&gt;x&lt;/script&gt;', 'ひな形: ラベルはエスケープされる');
}

/* ---------- 差出人（基本情報） ---------- */
{
  // 旧形式は同じ見た目のまま可変フィールドへ移行する
  const legacy = { id: 'i1', name: '千葉', company: '大進道路', office: '千葉営業所', zip: '260-0001', address: '千葉市中央区1-1', tel: '043-000-0000', fax: '043-000-0001', rep: '山田', seal: true };
  const html = C.issuerHtml(legacy);
  ok(html.indexOf('大進道路') >= 0 && html.indexOf('千葉営業所') >= 0, '旧形式: 会社名と営業所が出る');
  ok(html.indexOf('〒260-0001　千葉市中央区1-1') >= 0, '旧形式: 郵便番号と住所は同じ行');
  ok(html.indexOf('TEL 043-000-0000') >= 0, '旧形式: 半角ラベルは半角スペース区切り');
  ok(html.indexOf('営業担当　山田 ㊞') >= 0, '旧形式: 全角ラベルは全角スペース区切りで㊞つき');
  const f = C.issuerFieldsOf(legacy);
  eq(f.length, 7, '旧形式: 7項目へ移行');
  eq(f[1].sameLine, true, '旧形式: 営業所は会社名と同じ行');

  const norm = C.normalizeIssuer(legacy);
  ok(Array.isArray(norm.fields) && !('company' in norm), '正規化: fields 形式になり旧キーは残らない');
  eq(C.issuerHtml(norm), html, '正規化しても表示は変わらない');
}
{
  // 空の項目は出力されない・値だけの項目も出せる
  const p = { fields: [{ label: '会社名', value: '' }, { label: '備考', value: 'テスト', showLabel: true }] };
  eq(C.issuerHtml(p), '備考　テスト', '空の項目は行にしない');
}
{
  // 自由に追加した項目もそのまま出る
  const p = {
    fields: [
      { label: '部署', value: '営業部', showLabel: true, bold: true },
      { label: '登録番号', value: 'T1234567890123', showLabel: true, size: 'sm' },
      { label: '追記', value: 'あああ', sameLine: true }
    ]
  };
  const html = C.issuerHtml(p);
  eq(html.split('<br>').length, 2, '同じ行に続ける指定で行がまとまる');
  ok(html.indexOf('font-weight:700') >= 0, '太字の指定が反映される');
  ok(html.indexOf('font-size:12px') >= 0, '小さい文字の指定が反映される');
}
{
  // HTML はエスケープされる（他人のファイル読み込みでも安全）
  const p = { fields: [{ label: '<b>', value: '<img src=x onerror=alert(1)>', showLabel: true }] };
  const html = C.issuerHtml(p);
  ok(html.indexOf('<img') < 0 && html.indexOf('&lt;img') >= 0, '差出人の値はエスケープされる');
  ok(html.indexOf('<b>') < 0, '差出人のラベルもエスケープされる');
}
{
  const empty = C.normalizeIssuer({});
  eq(empty.name, '差出人', '名前が無いときの既定名');
  eq(C.issuerHtml(empty), '', '空のプロファイルは空文字');
  eq(C.defaultIssuerFields().length, 7, '新規登録の初期項目');
  eq(C.issuerPreviewText({ fields: [{ label: 'TEL', value: '03-0000-0000', showLabel: true }] }), 'TEL 03-0000-0000', '一覧用の要約テキスト');
}
{
  const named = C.normalizeIssuer({ fields: [{ label: '会社名', value: '大進道路' }] });
  eq(named.name, '大進道路', '表示名が空なら最初の値から補う');
}

/* ---------- 壊れかけたデータの立て直し ---------- */
{
  // 列の定義だけ欠けていてもセルの中身は失わない
  const c = C.normalizeTable({ columns: [], rows: [
    { cells: [C.tblCell({ html: 'a' }), C.tblCell({ html: 'b' }), C.tblCell({ html: 'c' })] },
    { cells: [C.tblCell({ html: 'd' }), C.tblCell({ html: 'e' }), C.tblCell({ html: 'f' })] }] });
  eq(c.columns.length, 3, '列定義が空でもセル数から作り直す');
  eq(c.rows[0].cells.map(x => x.html), ['a', 'b', 'c'], '中身は失わない');
  invariant(c, '列定義の復旧');
}
{
  // rows が配列ですらない
  const c = C.normalizeTable({ columns: [{ width: 50 }, { width: 50 }], rows: 'こわれた' });
  ok(c.rows.length >= 1 && c.rows[0].cells.length >= 1, '壊れた rows でも表として成立');
  invariant(c, '壊れた rows');
}
{
  // 結合したままの行を続けて削除しても整合性が保たれる（画面の「選んだ行を削除」相当）
  const c = grid(5, 3);
  C.mergeCells(c, { r1: 1, c1: 0, r2: 3, c2: 1 });
  for (let k = 0; k < 2; k++) C.deleteRow(c, 1);
  eq(c.rows.length, 3, '2行まとめて削除できる');
  invariant(c, '結合をまたぐ連続削除');
  for (let k = 0; k < 2; k++) C.deleteCol(c, 0);
  eq(c.columns.length, 1, '2列まとめて削除できる');
  invariant(c, '連続した列削除');
}
{
  // 行の移動（結合していない行）
  const c = grid(3, 2);
  ok(C.moveRow(c, 2, -1), '行を上へ移動できる');
  eq(c.rows[1].cells[0].html, 'r2c0', '入れ替わっている');
  invariant(c, '行の移動');
}
{
  // 結合 → 解除 → 再結合を繰り返しても壊れない
  const c = grid(4, 4);
  for (let i = 0; i < 6; i++) {
    C.mergeCells(c, { r1: 0, c1: 0, r2: 1 + (i % 2), c2: 1 + (i % 3) });
    invariant(c, '繰り返し結合 ' + i);
    C.splitCells(c, { r1: 0, c1: 0, r2: 3, c2: 3 });
    invariant(c, '繰り返し解除 ' + i);
  }
  eq(c.rows.length, 4, '行数は変わらない');
  eq(c.columns.length, 4, '列数は変わらない');
}

if (failures) { console.error('\n' + failures + ' / ' + checks + ' checks failed'); process.exit(1); }
console.log('quote_maker core: ' + checks + ' checks passed');
