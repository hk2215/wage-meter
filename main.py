import asyncio
import tkinter as tk
from tkinter import filedialog
import flet as ft
import time
import threading
import json
import os
import calendar
from datetime import datetime, timedelta
from google import genai
import PIL.Image
from dotenv import load_dotenv

# ==========================================
# 環境変数の読み込みとGeminiの初期設定
# ==========================================
load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    print("警告: APIキーが設定されていません。.envファイルを確認してください。")
    client = None
else:
    client = genai.Client(api_key=api_key)

# データを保存するファイル名
DATA_FILE = "time_wage_data.json"

# ==========================================
# 1. データベース（JSONファイル）の読み書き機能
# ==========================================
def load_db():
    default_db = {
        "settings": {
            "base_wage": 1175.0,
            "target_amount": 5000.0,
            "pay_period_start": 11,
            "bonuses": [
                {"start": "16:00", "end": "18:00", "amt": 20.0},
                {"start": "18:00", "end": "20:00", "amt": 30.0},
                {"start": "20:00", "end": "22:00", "amt": 50.0}
            ]
        },
        "state": {
            "running": False,
            "last_time": None,
            "start_time_display": "",
            "accumulated_seconds": 0.0,
            "accumulated_earned": 0.0,
            "view_month_offset": 0
        },
        "logs": {}
    }

    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                loaded_db = json.load(f)

                loaded_settings = loaded_db.get("settings", {})
                default_db["settings"].update(loaded_settings)
                default_db["state"].update(loaded_db.get("state", {}))

                raw_logs = loaded_db.get("logs", {})
                converted_logs = {}
                for k, v in raw_logs.items():
                    if isinstance(v, (int, float)):
                        converted_logs[k] = [{"start": "不明", "end": "不明", "earned": int(v)}]
                    else:
                        converted_logs[k] = v
                default_db["logs"] = converted_logs
                return default_db
        except Exception:
            pass
    return default_db

def save_db(db):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(db, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"保存エラー: {e}")

# ==========================================
# 2. メインアプリ
# ==========================================
def main(page: ft.Page):
    page.title = "時給メーター Pro"
    page.theme_mode = ft.ThemeMode.LIGHT

    db = load_db()
    settings = db["settings"]
    state = db["state"]
    logs = db["logs"]

    if "view_month_offset" not in state:
        state["view_month_offset"] = 0

    def show_msg(msg):
        snack = ft.SnackBar(content=ft.Text(msg))
        page.overlay.append(snack)
        snack.open = True
        page.update()

    # --- AIシフト読み込み機能 ---
    def process_image_to_logs(e):
        if not e.files:
            return
        file_path = e.files[0].path
        show_msg("AIがシフト表を解析中です...⏳")
        page.update()

        try:
            img = PIL.Image.open(file_path)
            img.thumbnail((1024, 1024))
            prompt = (
                "この画像から勤務日(date)、開始時間(start)、終了時間(end)を抜き出し、"
                "以下のJSON形式の配列のみで出力してください。Markdownの```や解説は不要です。\n"
                '[{"date": "2026-05-10", "start": "16:00", "end": "22:00"}]'
            )

            if client is None:
                show_msg("APIキーが設定されていません")
                return

            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=[prompt, img]
            )
            raw_json = response.text.replace('```json', '').replace('```', '').strip()
            shift_data = json.loads(raw_json)

            base = settings["base_wage"]
            bonuses = get_parsed_bonuses()
            added_count = 0

            for s in shift_data:
                date_str, start_str, end_str = s["date"], s["start"], s["end"]
                start_dt = datetime.strptime(f"{date_str} {start_str}", "%Y-%m-%d %H:%M")
                end_dt = datetime.strptime(f"{date_str} {end_str}", "%Y-%m-%d %H:%M")
                if end_dt <= start_dt:
                    end_dt += timedelta(days=1)

                total_seconds = int((end_dt - start_dt).total_seconds())
                total_earned = sum(
                    (get_wage_at_time(datetime.fromtimestamp(start_dt.timestamp() + i).time(), base, bonuses) / 3600)
                    for i in range(total_seconds)
                )

                if date_str not in logs or not isinstance(logs[date_str], list):
                    logs[date_str] = []
                logs[date_str].append({
                    "start": start_str,
                    "end": end_str,
                    "earned": int(total_earned),
                    "seconds": total_seconds
                })
                added_count += 1

            save_db(db)
            show_msg(f"✅ {added_count}件のシフトを自動登録しました！")
            route_change()
        except Exception as ex:
            show_msg("解析に失敗しました。")
            print(f"解析エラー: {ex}")

    # --- ファイル選択: Flet 0.80+ の FilePicker は page.views.clear() と相性が悪く
    # TimeoutException が発生するため、tkinter のネイティブダイアログで代替する ---
    async def open_ai_picker(e):
        def pick_file_sync():
            root = tk.Tk()
            root.withdraw()
            root.attributes('-topmost', True)
            path = filedialog.askopenfilename(
                title="シフト表の画像を選択",
                filetypes=[("画像ファイル", "*.png *.jpg *.jpeg"), ("すべてのファイル", "*.*")]
            )
            root.destroy()
            return path

        file_path = await asyncio.to_thread(pick_file_sync)

        if file_path:
            class _MockFile:
                def __init__(self, p): self.path = p
            class _MockEvent:
                def __init__(self, p): self.files = [_MockFile(p)]
            process_image_to_logs(_MockEvent(file_path))

    amount_text = ft.Text("¥ 0", size=65, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_800)
    timer_text = ft.Text("00:00:00", size=20, color=ft.Colors.GREY_700)
    start_time_label = ft.Text(
        f"開始時刻: {state['start_time_display']}" if state['start_time_display'] else "",
        size=14, color=ft.Colors.GREY_500
    )

    current_wage_text = ft.Text("現在の時給: ¥ ---", size=14, color=ft.Colors.ORANGE_600, weight=ft.FontWeight.BOLD)
    current_wage_min_text = ft.Text("分給: ¥ ---", size=12, color=ft.Colors.GREY_500)
    current_wage_sec_text = ft.Text("秒給: ¥ ---", size=12, color=ft.Colors.GREY_500)

    progress_ring = ft.ProgressRing(value=0, width=250, height=250, stroke_width=10, color=ft.Colors.BLUE)

    btn_start = ft.Button(
        "開始", icon=ft.Icons.PLAY_ARROW, disabled=state["running"],
        style=ft.ButtonStyle(bgcolor=ft.Colors.GREEN_600, color=ft.Colors.WHITE)
    )
    btn_pause = ft.Button(
        "一時停止", icon=ft.Icons.PAUSE, disabled=not state["running"],
        style=ft.ButtonStyle(bgcolor=ft.Colors.ORANGE_500, color=ft.Colors.WHITE)
    )
    btn_finish = ft.Button("勤務終了（記録）", icon=ft.Icons.SAVE, width=220, height=50)

    # --- 時給計算ロジック ---
    def get_parsed_bonuses():
        parsed = []
        for b in settings.get("bonuses", []):
            if b.get("start") and b.get("end") and b.get("amt", 0) > 0:
                try:
                    st = datetime.strptime(b["start"], "%H:%M").time()
                    et = datetime.strptime(b["end"], "%H:%M").time()
                    parsed.append((st, et, float(b["amt"])))
                except ValueError:
                    pass
        return parsed

    def get_wage_at_time(check_time, base, parsed_bonuses):
        wage = base
        for st, et, amt in parsed_bonuses:
            if st <= et:
                if st <= check_time <= et:
                    wage += amt
            else:
                if check_time >= st or check_time <= et:
                    wage += amt
        return wage

    def add_earned_time(now_t):
        last = state.get("last_time")
        if last is None:
            return

        delta = now_t - last
        if delta <= 0:
            return

        base = settings["base_wage"]
        bonuses = get_parsed_bonuses()

        total_add = 0.0
        if delta > 86400:
            total_add = (base / 3600) * delta
        else:
            start_ts = last
            for i in range(int(delta)):
                try:
                    current_time = datetime.fromtimestamp(start_ts + i).time()
                    wage = get_wage_at_time(current_time, base, bonuses)
                    total_add += (wage / 3600)
                except Exception:
                    pass
            rem = delta - int(delta)
            total_add += (base / 3600) * rem

        state["accumulated_earned"] += total_add
        state["accumulated_seconds"] += delta

        if state.get("last_time") is not None:
            state["last_time"] = now_t

    def get_period_dates(offset=0):
        start_day = int(settings.get("pay_period_start", 11))
        now = datetime.now()
        base_month = now.month if now.day >= start_day else now.month - 1
        base_year = now.year
        if base_month == 0:
            base_month = 12
            base_year -= 1

        total_months = base_year * 12 + (base_month - 1) + offset
        target_year = total_months // 12
        target_month = (total_months % 12) + 1

        next_total_months = total_months + 1
        next_year = next_total_months // 12
        next_month = (next_total_months % 12) + 1

        def safe_date(y, m, d):
            _, max_d = calendar.monthrange(y, m)
            return datetime(y, m, min(d, max_d))

        s_date = safe_date(target_year, target_month, start_day)
        n_date = safe_date(next_year, next_month, start_day)
        e_date = n_date - timedelta(days=1)

        return s_date, e_date

    def get_fallback_seconds(start_str, end_str):
        if not start_str or not end_str or start_str == "不明" or end_str == "不明":
            return 0.0
        try:
            s_dt = datetime.strptime(start_str, "%H:%M")
            e_dt = datetime.strptime(end_str, "%H:%M")
            if e_dt <= s_dt:
                e_dt += timedelta(days=1)
            return (e_dt - s_dt).total_seconds()
        except Exception:
            return 0.0

    def format_time_str(total_sec):
        total_m = int(round(total_sec / 60))
        h = total_m // 60
        m = total_m % 60
        return f"{h}時間{m}分"

    def toggle_timer(is_start):
        now_t = time.time()
        if is_start:
            state["last_time"] = now_t
            if not state["start_time_display"]:
                state["start_time_display"] = datetime.now().strftime("%H:%M")
                start_time_label.value = f"開始時刻: {state['start_time_display']}"
        else:
            add_earned_time(now_t)
            state["last_time"] = None

        state["running"] = is_start
        save_db(db)
        btn_start.disabled = is_start
        btn_pause.disabled = not is_start
        page.update()

    def finish_session(e):
        if state["running"]:
            add_earned_time(time.time())
        earned = state["accumulated_earned"]
        if earned > 0:
            today = datetime.now().strftime("%Y-%m-%d")
            if today not in logs or not isinstance(logs[today], list):
                logs[today] = []
            logs[today].append({
                "start": state["start_time_display"] or "不明",
                "end": datetime.now().strftime("%H:%M"),
                "earned": int(earned),
                "seconds": state["accumulated_seconds"]
            })
        state.update({
            "running": False, "last_time": None, "start_time_display": "",
            "accumulated_seconds": 0.0, "accumulated_earned": 0.0
        })
        start_time_label.value = ""
        save_db(db)
        btn_start.disabled = False
        btn_pause.disabled = True
        show_msg("勤務を記録しました！")
        page.update()

    btn_start.on_click = lambda e: toggle_timer(True)
    btn_pause.on_click = lambda e: toggle_timer(False)
    btn_finish.on_click = finish_session

    def clear_logs(e):
        db["logs"] = {}
        logs.clear()
        save_db(db)
        show_msg("履歴をすべて削除しました")
        page.run_task(page.push_route, "/")

    def update_timer():
        while True:
            try:
                if state["running"]:
                    add_earned_time(time.time())

                if page.route == "/" or page.route == "":
                    earned = state["accumulated_earned"]
                    amount_text.value = f"¥ {int(earned)}"

                    total_sec = state["accumulated_seconds"]
                    hrs, rem = divmod(int(total_sec), 3600)
                    mins, secs = divmod(rem, 60)
                    timer_text.value = f"{hrs:02d}:{mins:02d}:{secs:02d}"

                    current_time = datetime.now().time()
                    current_wage = get_wage_at_time(current_time, settings["base_wage"], get_parsed_bonuses())

                    current_wage_text.value = f"現在の時給: ¥ {int(current_wage):,}"
                    current_wage_min_text.value = f"分給: ¥ {current_wage / 60:.1f}"
                    current_wage_sec_text.value = f"秒給: ¥ {current_wage / 3600:.2f}"

                    target = settings["target_amount"]
                    progress_ring.value = min(earned / target, 1.0) if target > 0 else 0

                    page.update()
            except Exception:
                pass
            time.sleep(1)

    # --- 設定メニュー ---
    wage_input = ft.TextField(
        label="基本時給 (円)", value=str(int(settings["base_wage"])),
        keyboard_type=ft.KeyboardType.NUMBER
    )
    target_input = ft.TextField(
        label="1日の目標金額 (円)", value=str(int(settings["target_amount"])),
        keyboard_type=ft.KeyboardType.NUMBER
    )
    pay_period_input = ft.TextField(
        label="給料の開始日 (毎月◯日)", value=str(int(settings.get("pay_period_start", 11))),
        keyboard_type=ft.KeyboardType.NUMBER
    )

    bonus_ui_items = []
    bonus_list_column = ft.Column(spacing=15)

    # ✅ 修正③: create_time_btn 内で overlay への重複追加を防ぐ
    def create_time_btn(default_time):
        t_text = ft.Text(default_time, size=16, weight=ft.FontWeight.BOLD)

        def on_change_time(e):
            if e.control.value:
                t_text.value = e.control.value.strftime("%H:%M")
                page.update()

        picker = ft.TimePicker(
            confirm_text="決定",
            cancel_text="キャンセル",
            entry_mode=ft.TimePickerEntryMode.INPUT,
            on_change=on_change_time
        )
        if picker not in page.overlay:
            page.overlay.append(picker)

        btn = ft.TextButton(
            content=ft.Row(
                [ft.Icon(ft.Icons.ACCESS_TIME, size=18, color=ft.Colors.BLUE_600), t_text],
                spacing=5
            ),
            on_click=lambda _: setattr(picker, 'open', True) or page.update(),
            style=ft.ButtonStyle(padding=5)
        )
        return btn, t_text

    def add_bonus_row(start_val="18:00", end_val="20:00", amt_val=0.0):
        btn_s, txt_s = create_time_btn(start_val)
        btn_e, txt_e = create_time_btn(end_val)
        tf_amt = ft.TextField(
            label="加算額(円)", value=str(int(amt_val)),
            width=100, keyboard_type=ft.KeyboardType.NUMBER
        )
        item = {
            "btn_start": btn_s, "txt_start": txt_s,
            "btn_end": btn_e,   "txt_end":   txt_e,
            "tf_amt": tf_amt
        }
        bonus_ui_items.append(item)
        refresh_bonus_ui()

    def refresh_bonus_ui():
        bonus_list_column.controls.clear()
        for i, item in enumerate(bonus_ui_items):
            header = ft.Row(
                [
                    ft.Text(f"時間帯加算 {i+1}", size=16, weight=ft.FontWeight.BOLD),
                    ft.IconButton(
                        icon=ft.Icons.DELETE_OUTLINE, icon_color=ft.Colors.RED_400,
                        on_click=lambda e, it=item: (bonus_ui_items.remove(it), refresh_bonus_ui())
                    )
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN
            )
            row_times = ft.Row([item["btn_start"], ft.Text("〜"), item["btn_end"]])
            row_amt = ft.Row([item["tf_amt"], ft.Text("円アップ")])
            card = ft.Container(
                content=ft.Column([header, row_times, row_amt], spacing=5),
                padding=10,
                border=ft.Border.all(1, ft.Colors.GREY_300),
                border_radius=8
            )
            bonus_list_column.controls.append(card)
        if page.route:
            page.update()

    for b in settings.get("bonuses", []):
        add_bonus_row(b.get("start", "18:00"), b.get("end", "20:00"), b.get("amt", 0.0))

    btn_add_bonus = ft.TextButton(
        "＋ 新しい加算枠を追加", icon=ft.Icons.ADD,
        on_click=lambda e: add_bonus_row()
    )

    def save_settings(e):
        try:
            settings["base_wage"] = float(wage_input.value)
            settings["target_amount"] = float(target_input.value)
            settings["pay_period_start"] = int(pay_period_input.value)
            settings["bonuses"] = [
                {
                    "start": item["txt_start"].value,
                    "end":   item["txt_end"].value,
                    "amt":   float(item["tf_amt"].value) if item["tf_amt"].value else 0.0
                }
                for item in bonus_ui_items
            ]
            save_db(db)
            page.run_task(page.close_drawer)
            show_msg("設定を保存しました")
            page.update()
        except ValueError:
            show_msg("正しい数値を入力してください")

    my_drawer = ft.NavigationDrawer(
        controls=[
            ft.Container(
                content=ft.Column(
                    [
                        ft.Text("各種設定", size=22, weight=ft.FontWeight.BOLD),
                        wage_input, target_input, pay_period_input,
                        ft.Divider(),
                        bonus_list_column,
                        btn_add_bonus,
                        ft.Divider(),
                        ft.Button(
                            "設定を保存", on_click=save_settings,
                            style=ft.ButtonStyle(bgcolor=ft.Colors.BLUE, color=ft.Colors.WHITE)
                        )
                    ],
                    spacing=10, scroll=ft.ScrollMode.AUTO
                ),
                padding=20, expand=True
            )
        ]
    )

    # --- 過去のシフトを手動で追加 ---
    txt_m_date  = ft.Text(datetime.now().strftime("%Y-%m-%d"), size=16, weight=ft.FontWeight.BOLD)
    txt_m_start = ft.Text("16:00", size=16, weight=ft.FontWeight.BOLD)
    txt_m_end   = ft.Text("22:00", size=16, weight=ft.FontWeight.BOLD)

    def on_date_change(e):
        if e.control.value:
            adjusted_date = e.control.value + timedelta(hours=9)
            txt_m_date.value = adjusted_date.strftime("%Y-%m-%d")
            page.update()

    dp_manual  = ft.DatePicker(on_change=on_date_change)
    tp_m_start = ft.TimePicker(
        on_change=lambda e: (
            setattr(txt_m_start, 'value', e.control.value.strftime("%H:%M")),
            page.update()
        ) if e.control.value else None,
        entry_mode=ft.TimePickerEntryMode.INPUT
    )
    tp_m_end = ft.TimePicker(
        on_change=lambda e: (
            setattr(txt_m_end, 'value', e.control.value.strftime("%H:%M")),
            page.update()
        ) if e.control.value else None,
        entry_mode=ft.TimePickerEntryMode.INPUT
    )

    def calc_and_add_manual(e):
        date_str  = txt_m_date.value
        start_str = txt_m_start.value
        end_str   = txt_m_end.value
        try:
            start_dt = datetime.strptime(f"{date_str} {start_str}", "%Y-%m-%d %H:%M")
            end_dt   = datetime.strptime(f"{date_str} {end_str}",   "%Y-%m-%d %H:%M")
            if end_dt <= start_dt:
                end_dt += timedelta(days=1)
            total_seconds = int((end_dt - start_dt).total_seconds())
            base, bonuses = settings["base_wage"], get_parsed_bonuses()
            total_earned = sum(
                (get_wage_at_time(datetime.fromtimestamp(start_dt.timestamp() + i).time(), base, bonuses) / 3600)
                for i in range(total_seconds)
            )

            if date_str not in logs or not isinstance(logs[date_str], list):
                logs[date_str] = []

            logs[date_str].append({
                "start":   start_str,
                "end":     end_str,
                "earned":  int(total_earned),
                "seconds": total_seconds
            })
            save_db(db)
            dlg_manual.open = False
            show_msg(f"✅ {date_str} の記録を追加しました！")
            route_change()
        except Exception:
            show_msg("エラーが発生しました。時間の形式を確認してください。")

    dlg_manual = ft.AlertDialog(
        title=ft.Text("過去のシフトを追加"),
        content=ft.Column(
            [
                ft.Text("日付", color=ft.Colors.GREY_600),
                ft.TextButton(
                    content=ft.Row([ft.Icon(ft.Icons.CALENDAR_MONTH), txt_m_date]),
                    on_click=lambda _: setattr(dp_manual, 'open', True) or page.update()
                ),
                ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                ft.Text("勤務時間", color=ft.Colors.GREY_600),
                ft.Row(
                    [
                        ft.TextButton(
                            content=ft.Row([ft.Icon(ft.Icons.ACCESS_TIME, size=16), txt_m_start]),
                            on_click=lambda _: setattr(tp_m_start, 'open', True) or page.update()
                        ),
                        ft.Text("〜"),
                        ft.TextButton(
                            content=ft.Row([ft.Icon(ft.Icons.ACCESS_TIME, size=16), txt_m_end]),
                            on_click=lambda _: setattr(tp_m_end, 'open', True) or page.update()
                        )
                    ],
                    alignment=ft.MainAxisAlignment.CENTER
                )
            ],
            tight=True
        ),
        actions=[
            ft.TextButton(
                "キャンセル",
                on_click=lambda e: setattr(dlg_manual, 'open', False) or page.update()
            ),
            ft.Button(
                "計算して記録", on_click=calc_and_add_manual,
                style=ft.ButtonStyle(bgcolor=ft.Colors.BLUE, color=ft.Colors.WHITE)
            )
        ],
        actions_alignment=ft.MainAxisAlignment.END
    )

    page.overlay.extend([dp_manual, tp_m_start, tp_m_end, dlg_manual])
    page.update()

    # --- スワイプとルーティング ---
    def swipe_to_calendar(e):
        dx = e.local_delta.x if hasattr(e, "local_delta") else getattr(e, "delta_x", 0)
        if dx > 15:
            page.run_task(page.push_route, "/calendar")

    def swipe_to_home(e):
        dx = e.local_delta.x if hasattr(e, "local_delta") else getattr(e, "delta_x", 0)
        if dx < -15:
            page.run_task(page.push_route, "/")

    def route_change(route_event=None):
        page.views.clear()

        offset = state.get("view_month_offset", 0)
        s_date, e_date = get_period_dates(offset)

        period_total   = 0
        period_seconds = 0.0
        annual_total   = 0
        annual_seconds = 0.0
        history_items  = []
        target_year    = e_date.year

        for date_str, shift_list in sorted(logs.items(), reverse=True):
            if not isinstance(shift_list, list):
                continue
            log_date = datetime.strptime(date_str, "%Y-%m-%d")

            if log_date.year == target_year:
                for s in shift_list:
                    annual_total   += s.get("earned", 0)
                    annual_seconds += s.get("seconds", get_fallback_seconds(s.get("start"), s.get("end")))

            if s_date.date() <= log_date.date() <= e_date.date():
                day_total = 0
                for s in shift_list:
                    day_total      += s.get("earned", 0)
                    period_seconds += s.get("seconds", get_fallback_seconds(s.get("start"), s.get("end")))

                period_total += day_total

                shift_tiles = [
                    ft.ListTile(
                        leading=ft.Icon(ft.Icons.SUBDIRECTORY_ARROW_RIGHT, size=16, color=ft.Colors.GREY_400),
                        title=ft.Text(f"{s.get('start', '不明')} 〜 {s.get('end', '不明')}", size=14),
                        subtitle=ft.Text(f"¥{s.get('earned', 0):,}", color=ft.Colors.BLUE, size=14),
                        dense=True,
                        content_padding=ft.Padding.only(left=20)
                    )
                    for s in shift_list
                ]
                history_items.append(
                    ft.Container(
                        content=ft.Column(
                            [ft.Text(f"{date_str} (計: ¥{day_total:,})", weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_GREY_700), *shift_tiles],
                            spacing=0
                        ),
                        padding=10,
                        margin=ft.Margin.only(bottom=10),
                        border=ft.Border.all(1, ft.Colors.GREY_200),
                        border_radius=8,
                        bgcolor=ft.Colors.WHITE
                    )
                )

        if not history_items:
            history_items.append(ft.Text("この期間の記録はありません", color=ft.Colors.GREY_500))

        def change_month(delta):
            state["view_month_offset"] = state.get("view_month_offset", 0) + delta
            save_db(db)
            route_change()

        period_str = f"{s_date.month}/{s_date.day} 〜 {e_date.month}/{e_date.day}"
        card_title  = "今月振り込まれる予定の給料" if offset == 0 else f"{abs(offset)}ヶ月前の給料"

        monthly_card = ft.Container(
            content=ft.Row(
                [
                    ft.IconButton(ft.Icons.CHEVRON_LEFT, icon_color=ft.Colors.WHITE, on_click=lambda e: change_month(-1)),
                    ft.Column(
                        [
                            ft.Text(card_title, color=ft.Colors.WHITE_70, size=12),
                            ft.Text(f"¥ {int(period_total):,}", size=36, weight=ft.FontWeight.BOLD, color=ft.Colors.WHITE),
                            ft.Row(
                                [
                                    ft.Text(f"対象期間: {period_str}", color=ft.Colors.WHITE_70, size=12),
                                    ft.Text("|", color=ft.Colors.WHITE_70, size=12),
                                    ft.Text(f"稼働: {format_time_str(period_seconds)}", color=ft.Colors.WHITE, size=12, weight=ft.FontWeight.BOLD),
                                ],
                                alignment=ft.MainAxisAlignment.CENTER
                            ),
                            ft.Container(height=5),
                            ft.Container(
                                content=ft.Text(
                                    f"【{target_year}年】 ¥ {int(annual_total):,} ({format_time_str(annual_seconds)})",
                                    color=ft.Colors.WHITE, size=14, weight=ft.FontWeight.BOLD
                                ),
                                bgcolor=ft.Colors.WHITE_24,
                                padding=ft.Padding.symmetric(horizontal=10, vertical=5),
                                border_radius=15
                            )
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        expand=True
                    ),
                    ft.IconButton(
                        ft.Icons.CHEVRON_RIGHT, icon_color=ft.Colors.WHITE,
                        disabled=(offset >= 0),
                        on_click=lambda e: change_month(1)
                    )
                ],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN
            ),
            bgcolor=ft.Colors.BLUE_600,
            padding=10,
            border_radius=10,
            width=float('inf')
        )

        page.views.append(
            ft.View(
                route="/calendar",
                controls=[
                    ft.AppBar(title=ft.Text("収入履歴"), bgcolor=ft.Colors.BLUE_50),
                    ft.GestureDetector(
                        on_pan_update=swipe_to_home,
                        expand=True,
                        content=ft.Container(
                            bgcolor=ft.Colors.TRANSPARENT,
                            expand=True,
                            content=ft.Column(
                                [
                                    monthly_card,
                                    ft.Divider(color=ft.Colors.TRANSPARENT, height=10),
                                    ft.Button(
                                        "📸 写真からシフトを自動入力",
                                        icon=ft.Icons.AUTO_AWESOME,
                                        width=250,
                                        style=ft.ButtonStyle(bgcolor=ft.Colors.GREEN_600, color=ft.Colors.WHITE),
                                        on_click=open_ai_picker
                                    ),
                                    ft.Button(
                                        "過去のシフトを手動入力",
                                        icon=ft.Icons.EDIT_CALENDAR,
                                        width=250,
                                        on_click=lambda _: setattr(dlg_manual, 'open', True) or page.update(),
                                        style=ft.ButtonStyle(bgcolor=ft.Colors.BLUE_50, color=ft.Colors.BLUE_800)
                                    ),
                                    ft.Divider(),
                                    ft.Column(history_items, scroll=ft.ScrollMode.AUTO, expand=True),
                                    ft.Divider(),
                                    ft.TextButton(
                                        "履歴をすべて削除", icon=ft.Icons.DELETE,
                                        on_click=clear_logs, icon_color=ft.Colors.RED,
                                        style=ft.ButtonStyle(color=ft.Colors.RED)
                                    ),
                                    ft.Text("← 左スワイプでホームへ戻る", color=ft.Colors.GREY_400, size=12)
                                ],
                                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                                expand=True
                            ),
                            padding=20
                        )
                    )
                ]
            )
        )

        if page.route == "/" or page.route == "":
            page.views.append(
                ft.View(
                    route="/",
                    controls=[
                        ft.AppBar(
                            title=ft.Text("ホーム"),
                            leading=ft.IconButton(ft.Icons.MENU, on_click=lambda _: page.run_task(page.show_drawer))
                        ),
                        ft.GestureDetector(
                            on_pan_update=swipe_to_calendar,
                            expand=True,
                            content=ft.Container(
                                bgcolor=ft.Colors.TRANSPARENT,
                                expand=True,
                                content=ft.Column(
                                    [
                                        ft.Text("本日の稼ぎ", size=18, color=ft.Colors.GREY_600),
                                        ft.Stack([
                                            progress_ring,
                                            ft.Container(
                                                content=ft.Column(
                                                    [
                                                        amount_text, current_wage_text,
                                                        ft.Row(
                                                            [current_wage_min_text, current_wage_sec_text],
                                                            alignment=ft.MainAxisAlignment.CENTER, spacing=15
                                                        ),
                                                        timer_text, start_time_label
                                                    ],
                                                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                                                    alignment=ft.MainAxisAlignment.CENTER,
                                                    spacing=2
                                                ),
                                                width=250, height=250,
                                                alignment=ft.Alignment(0, 0)
                                            )
                                        ]),
                                        ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
                                        ft.Row([btn_start, btn_pause], alignment=ft.MainAxisAlignment.CENTER, spacing=20),
                                        ft.Divider(height=20, color=ft.Colors.TRANSPARENT),
                                        btn_finish,
                                        ft.Text("右スワイプで履歴を確認 →", color=ft.Colors.GREY_400, size=12)
                                    ],
                                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                                    alignment=ft.MainAxisAlignment.CENTER,
                                    expand=True
                                ),
                                alignment=ft.Alignment(0, 0)
                            )
                        )
                    ],
                    drawer=my_drawer
                )
            )
        page.update()

    page.on_route_change = route_change
    page.route = page.route if page.route else "/"
    route_change()

    threading.Thread(target=update_timer, daemon=True).start()


if __name__ == "__main__":
    app_port = int(os.getenv("PORT", 8550))
    ft.run(main, host="0.0.0.0", port=app_port)
