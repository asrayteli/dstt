from flask import Blueprint, render_template, request, send_file
from datetime import datetime, timedelta
import calendar
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Border, Side, Alignment
import io
from flask_login import login_required

workday_bp = Blueprint("workday", __name__, url_prefix="/tools/workday")

# 日本の祝日（例：2025年の祝日）
JAPAN_HOLIDAYS = [
    # 2025年の祝日
    "2025-01-01", "2025-01-13", "2025-02-11", "2025-03-20", "2025-04-29", "2025-05-03", 
    "2025-05-04", "2025-05-05", "2025-07-20", "2025-08-11", "2025-09-15", "2025-09-23", 
    "2025-10-10", "2025-11-03", "2025-11-23", "2025-12-23",

    # 2026年の祝日
    "2026-01-01", "2026-01-12", "2026-02-11", "2026-03-20", "2026-04-29", "2026-05-03", 
    "2026-05-04", "2026-05-05", "2026-07-20", "2026-08-11", "2026-09-15", "2026-09-23", 
    "2026-10-10", "2026-11-03", "2026-11-23", "2026-12-23",

    # 2027年の祝日
    "2027-01-01", "2027-01-11", "2027-02-11", "2027-03-20", "2027-04-29", "2027-05-03", 
    "2027-05-04", "2027-05-05", "2027-07-20", "2027-08-11", "2027-09-15", "2027-09-23", 
    "2027-10-10", "2027-11-03", "2027-11-23", "2027-12-23",

    # 2028年の祝日
    "2028-01-01", "2028-01-10", "2028-02-11", "2028-03-20", "2028-04-29", "2028-05-03", 
    "2028-05-04", "2028-05-05", "2028-07-20", "2028-08-11", "2028-09-15", "2028-09-23", 
    "2028-10-09", "2028-11-03", "2028-11-23", "2028-12-23",

    # 2029年の祝日
    "2029-01-01", "2029-01-15", "2029-02-11", "2029-03-20", "2029-04-29", "2029-05-03", 
    "2029-05-04", "2029-05-05", "2029-07-20", "2029-08-11", "2029-09-15", "2029-09-23", 
    "2029-10-10", "2029-11-03", "2029-11-23", "2029-12-23",

    # 2030年の祝日
    "2030-01-01", "2030-01-14", "2030-02-11", "2030-03-20", "2030-04-29", "2030-05-03", 
    "2030-05-04", "2030-05-05", "2030-07-20", "2030-08-11", "2030-09-15", "2030-09-23", 
    "2030-10-14", "2030-11-03", "2030-11-23", "2030-12-23"
]


def is_workday(date, weekdays, holidays, count_holidays_as_workdays):
    """指定された日付が営業日かどうかを判定"""
    # weekdays は日曜=0, 月曜=1, ..., 土曜=6 の形式で受け取る
    # date.weekday() は月曜=0, ..., 日曜=6 なので変換が必要
    weekday_iso_to_sunday = (date.weekday() + 1) % 7  # 日曜=0, 月曜=1, ..., 土曜=6 に変換
    
    is_selected_weekday = weekday_iso_to_sunday in weekdays
    is_holiday = str(date.date()) in holidays

    if count_holidays_as_workdays:
        # 祝日も営業日としてカウント
        return is_selected_weekday or is_holiday
    else:
        # 祝日を除外した営業日判定
        return is_selected_weekday and not is_holiday


def generate_calendar(start_date, end_date, weekdays, holidays, count_holidays_as_workdays):
    current_date = start_date
    days = []
    while current_date <= end_date:
        days.append(current_date)
        current_date += timedelta(days=1)

    # 月ごとにカレンダーを分ける
    calendar_by_month = {}
    for day in days:
        year_month = (day.year, day.month)
        if year_month not in calendar_by_month:
            calendar_by_month[year_month] = []
        calendar_by_month[year_month].append(day)

    # 月ごとに週単位に分割してカレンダーを作成
    calendar_data = {}
    for (year, month), days_in_month in calendar_by_month.items():
        weeks = []
        
        # 月の最初の日
        first_day_of_month = datetime(year, month, 1)
        
        # 月の最初の日の曜日を取得（月曜=0, 日曜=6）
        first_weekday = first_day_of_month.weekday()
        # 日曜始まりのカレンダー用に調整（日曜=0, 月曜=1, ..., 土曜=6）
        adjusted_weekday = (first_weekday + 1) % 7
        
        # カレンダーを日曜から始める
        week = ['-'] * adjusted_weekday  # 最初の空白部分
        
        for day in days_in_month:
            week.append(day)
            if len(week) == 7:
                weeks.append(week)
                week = []

        # 最後の週を追加
        if week:
            week.extend(['-'] * (7 - len(week)))  # 1週間を埋めるため空白部分
            weeks.append(week)

        calendar_data[(year, month)] = []
        for week in weeks:
            calendar_data[(year, month)].append([{
                'date': day if day != '-' else None,
                'is_workday': is_workday(day, weekdays, holidays, count_holidays_as_workdays) if day != '-' else False
            } for day in week])

    return calendar_data


def generate_excel_calendar(start_date, end_date, weekdays, holidays, count_holidays_as_workdays):
    """エクセル形式のカレンダーを生成"""
    wb = Workbook()
    ws = wb.active
    ws.title = "営業日カレンダー"
    
    # スタイル定義
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    workday_fill = PatternFill(start_color="E7F3FF", end_color="E7F3FF", fill_type="solid")
    holiday_fill = PatternFill(start_color="FFE7E7", end_color="FFE7E7", fill_type="solid")
    border = Border(left=Side(style='thin'), right=Side(style='thin'), 
                   top=Side(style='thin'), bottom=Side(style='thin'))
    center_alignment = Alignment(horizontal='center', vertical='center')
    
    # 曜日ヘッダー
    weekday_names = ["日", "月", "火", "水", "木", "金", "土"]
    
    current_row = 1
    
    # 月ごとにカレンダーを生成
    current_date = start_date
    while current_date <= end_date:
        year = current_date.year
        month = current_date.month
        
        # 月の最初と最後の日を取得
        first_day = datetime(year, month, 1)
        if month == 12:
            last_day = datetime(year + 1, 1, 1) - timedelta(days=1)
        else:
            last_day = datetime(year, month + 1, 1) - timedelta(days=1)
        
        # 月タイトル
        ws.merge_cells(f'A{current_row}:G{current_row}')
        ws[f'A{current_row}'] = f"{year}年{month}月"
        ws[f'A{current_row}'].font = Font(bold=True, size=14)
        ws[f'A{current_row}'].alignment = center_alignment
        ws[f'A{current_row}'].fill = PatternFill(start_color="D9E2F3", end_color="D9E2F3", fill_type="solid")
        current_row += 1
        
        # 曜日ヘッダー
        for col, day_name in enumerate(weekday_names, 1):
            cell = ws.cell(row=current_row, column=col, value=day_name)
            cell.font = header_font
            cell.fill = header_fill
            cell.border = border
            cell.alignment = center_alignment
        current_row += 1
        
        # カレンダー生成
        first_weekday = first_day.weekday()
        adjusted_weekday = (first_weekday + 1) % 7  # 日曜始まり
        
        # 1週目の空白部分
        week_row = current_row
        col = 1
        for _ in range(adjusted_weekday):
            cell = ws.cell(row=week_row, column=col, value="")
            cell.border = border
            col += 1
        
        # 日付を配置
        day = first_day
        while day <= last_day:
            if day >= start_date and day <= end_date:
                # 営業日判定
                is_work = is_workday(day, weekdays, holidays, count_holidays_as_workdays)
                
                cell = ws.cell(row=week_row, column=col, value=day.day)
                cell.border = border
                cell.alignment = center_alignment
                
                if is_work:
                    cell.fill = workday_fill
                else:
                    cell.fill = holiday_fill
            else:
                cell = ws.cell(row=week_row, column=col, value="")
                cell.border = border
            
            col += 1
            if col > 7:
                col = 1
                week_row += 1
                # 新しい週の空白セルを準備
                for empty_col in range(1, 8):
                    if week_row <= ws.max_row + 1:
                        empty_cell = ws.cell(row=week_row, column=empty_col, value="")
                        empty_cell.border = border
            
            day += timedelta(days=1)
        
        # 最後の週の残りを埋める
        if col <= 7:
            for remaining_col in range(col, 8):
                cell = ws.cell(row=week_row, column=remaining_col, value="")
                cell.border = border
        
        current_row = week_row + 2
        
        # 次の月へ
        if month == 12:
            current_date = datetime(year + 1, 1, 1)
        else:
            current_date = datetime(year, month + 1, 1)
    
    # 凡例追加
    current_row += 1
    ws[f'A{current_row}'] = "凡例:"
    ws[f'A{current_row}'].font = Font(bold=True)
    current_row += 1
    
    # 営業日の凡例
    ws[f'A{current_row}'] = "営業日"
    ws[f'A{current_row}'].fill = workday_fill
    ws[f'A{current_row}'].border = border
    current_row += 1
    
    # 休日の凡例
    ws[f'A{current_row}'] = "休日"
    ws[f'A{current_row}'].fill = holiday_fill
    ws[f'A{current_row}'].border = border
    current_row += 2
    
    # 営業日数の表示
    total_workdays = sum(
        1 for day in (start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1))
        if is_workday(day, weekdays, holidays, count_holidays_as_workdays)
    )
    ws[f'A{current_row}'] = f"総営業日数: {total_workdays}日"
    ws[f'A{current_row}'].font = Font(bold=True, size=12)
    
    # 列幅調整
    for col in range(1, 8):
        ws.column_dimensions[chr(64 + col)].width = 4
    
    return wb
    """指定した範囲内の営業日数をカウント"""
    workday_count = 0
    current_date = start_date
    while current_date <= end_date:
        if is_workday(current_date, weekdays, holidays, count_holidays_as_workdays):
            workday_count += 1
        current_date += timedelta(days=1)
    return workday_count


@workday_bp.route("/", methods=["GET", "POST"])
@login_required
def workday():
    workday_count = None
    calendar_data = None

    if request.method == "POST":
        start_date_str = request.form.get("start_date")
        end_date_str = request.form.get("end_date")
        weekdays = list(map(int, request.form.getlist("weekdays[]")))
        holidays = request.form.get("holidays", "").split(',')
        holidays = [h.strip() for h in holidays if h.strip()]  # 空文字列を除去
        holidays.extend(JAPAN_HOLIDAYS)  # 日本の祝日を追加
        
        # 祝日を営業日としてカウントするかどうかの設定
        count_holidays_as_workdays = request.form.get('count_holidays') == 'on'
        print(f"count_holidays_as_workdays: {count_holidays_as_workdays}")

        start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d")

        # 営業日カレンダー生成
        calendar_data = generate_calendar(start_date, end_date, weekdays, holidays, count_holidays_as_workdays)

        # 営業日数をカウント
        workday_count = sum(
            1 for day in (start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1))
            if is_workday(day, weekdays, holidays, count_holidays_as_workdays)
        )

    return render_template("workday.html", calendar=calendar_data, workday_count=workday_count)


@workday_bp.route("/download", methods=["POST"])
@login_required
def download_excel():
    """エクセルファイルをダウンロード"""
    try:
        start_date_str = request.form.get("start_date")
        end_date_str = request.form.get("end_date")
        weekdays = list(map(int, request.form.getlist("weekdays[]")))
        holidays = request.form.get("holidays", "").split(',')
        holidays = [h.strip() for h in holidays if h.strip()]
        holidays.extend(JAPAN_HOLIDAYS)
        
        count_holidays_as_workdays = request.form.get('count_holidays') == 'on'

        start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        end_date = datetime.strptime(end_date_str, "%Y-%m-%d")

        # エクセルファイル生成
        wb = generate_excel_calendar(start_date, end_date, weekdays, holidays, count_holidays_as_workdays)
        
        # メモリ内でファイルを作成
        output = io.BytesIO()
        wb.save(output)
        output.seek(0)
        
        # ファイル名生成
        filename = f"営業日カレンダー_{start_date_str}_{end_date_str}.xlsx"
        
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )
        
    except Exception as e:
        print(f"エラー: {e}")
        return "エラーが発生しました", 500