"""Tkinter control panel for CGU Auto Attendance."""

from __future__ import annotations

import os
import subprocess
import sys
import tkinter as tk
from datetime import date, datetime
from pathlib import Path
from tkinter import messagebox, ttk

from schedule_config import (
    TASK_NAME,
    WEEKDAY_KEYS,
    WEEKDAY_LABELS,
    apply_master_task,
    ensure_ui_defaults,
    load_yaml,
    preview_schedule,
    query_master_task,
    save_yaml,
    validate_config,
)


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.yaml"
TIME_OPTIONS = [""] + [
    f"{hour:02d}:{minute:02d}"
    for hour in range(24)
    for minute in (0, 15, 30, 45)
]


class AttendanceUI(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("CGU 自動點名控制台")
        self.geometry("980x760")
        self.minsize(900, 680)
        self.configure(bg="#f2eee5")

        self.config_data = ensure_ui_defaults(load_yaml(CONFIG_PATH))
        self.enabled_var = tk.BooleanVar(
            value=bool(self.config_data["automation"]["enabled"])
        )
        self.scan_time_var = tk.StringVar(
            value=str(self.config_data["schedule"]["scan_time"])
        )
        self.status_var = tk.StringVar(value="讀取狀態中...")
        self.date_var = tk.StringVar(value=date.today().isoformat())
        self.leave_shift_var = tk.StringVar()
        self.day_vars: dict[str, dict[str, tk.Variable]] = {}
        self.leave_rows: list[dict[str, str]] = list(
            self.config_data["leave"].get("requests", []) or []
        )

        self._configure_style()
        self._build_header()
        self._build_body()
        self._load_work_content()
        self._load_weekly_values()
        self._refresh_leave_choices()
        self._refresh_leave_tree()
        self.refresh_status()

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#f2eee5")
        style.configure("Card.TFrame", background="#fffdf7")
        style.configure("Title.TLabel", background="#173b36", foreground="#fffaf0", font=("Microsoft JhengHei UI", 20, "bold"))
        style.configure("Subtitle.TLabel", background="#173b36", foreground="#c9ded8", font=("Microsoft JhengHei UI", 10))
        style.configure("Heading.TLabel", background="#fffdf7", foreground="#173b36", font=("Microsoft JhengHei UI", 13, "bold"))
        style.configure("TLabel", background="#fffdf7", font=("Microsoft JhengHei UI", 10))
        style.configure("Accent.TButton", font=("Microsoft JhengHei UI", 10, "bold"), foreground="white", background="#cf5b36")
        style.map("Accent.TButton", background=[("active", "#ac4729")])

    def _build_header(self) -> None:
        header = ttk.Frame(self, padding=(24, 18), style="TFrame")
        header.configure(style="TFrame")
        header.pack(fill="x")
        banner = tk.Frame(header, bg="#173b36", padx=24, pady=18)
        banner.pack(fill="x")
        ttk.Label(banner, text="AUTO ATTENDANCE", style="Title.TLabel").pack(anchor="w")
        ttk.Label(banner, text="每週班表、請假與 Windows 排程集中管理", style="Subtitle.TLabel").pack(anchor="w", pady=(4, 0))

    def _build_body(self) -> None:
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=24, pady=(0, 16))
        self.schedule_tab = ttk.Frame(notebook, padding=20, style="Card.TFrame")
        self.leave_tab = ttk.Frame(notebook, padding=20, style="Card.TFrame")
        self.work_tab = ttk.Frame(notebook, padding=20, style="Card.TFrame")
        self.status_tab = ttk.Frame(notebook, padding=20, style="Card.TFrame")
        notebook.add(self.schedule_tab, text="  每週班表  ")
        notebook.add(self.leave_tab, text="  指定請假  ")
        notebook.add(self.work_tab, text="  工作內容  ")
        notebook.add(self.status_tab, text="  狀態與測試  ")
        self._build_schedule_tab()
        self._build_leave_tab()
        self._build_work_tab()
        self._build_status_tab()

    def _build_schedule_tab(self) -> None:
        top = ttk.Frame(self.schedule_tab, style="Card.TFrame")
        top.pack(fill="x", pady=(0, 16))
        self._make_check_button(
            top, self.enabled_var, checked_text="☑ 自動點名已啟用", unchecked_text="☐ 自動點名已停用", width=18
        ).pack(side="left")
        ttk.Label(top, text="每日掃描時間：").pack(side="left", padx=(30, 6))
        scan_combo = ttk.Combobox(
            top,
            textvariable=self.scan_time_var,
            values=TIME_OPTIONS[1:],
            state="readonly",
            width=8,
        )
        scan_combo.pack(side="left")
        ttk.Label(top, text="建議比最早班次早 30 分鐘").pack(side="left", padx=8)

        grid = ttk.Frame(self.schedule_tab, style="Card.TFrame")
        grid.pack(fill="x")
        headers = ["星期", "上班", "第 1 班開始", "第 1 班結束", "第 2 班開始", "第 2 班結束"]
        for column, text in enumerate(headers):
            ttk.Label(grid, text=text, style="Heading.TLabel").grid(row=0, column=column, padx=8, pady=8, sticky="w")

        for row, day in enumerate(WEEKDAY_KEYS, start=1):
            values = {
                "enabled": tk.BooleanVar(value=False),
                "start1": tk.StringVar(),
                "end1": tk.StringVar(),
                "start2": tk.StringVar(),
                "end2": tk.StringVar(),
            }
            self.day_vars[day] = values
            ttk.Label(grid, text=WEEKDAY_LABELS[day]).grid(row=row, column=0, padx=8, pady=6, sticky="w")
            self._make_check_button(
                grid, values["enabled"], checked_text="☑", unchecked_text="☐", width=3
            ).grid(row=row, column=1, padx=8)
            for column, key in enumerate(("start1", "end1", "start2", "end2"), start=2):
                combo = ttk.Combobox(
                    grid,
                    textvariable=values[key],
                    values=TIME_OPTIONS,
                    state="readonly",
                    width=9,
                )
                combo.grid(row=row, column=column, padx=8, pady=4)
                combo.bind("<<ComboboxSelected>>", lambda _event: self._refresh_leave_choices())

        ttk.Label(
            self.schedule_tab,
            text="沒有第二班時請留空。UI 設定是安全閘門，網站讀到的實際班表仍是最終執行依據。",
        ).pack(anchor="w", pady=(18, 0))

    def _make_check_button(
        self,
        parent,
        variable: tk.BooleanVar,
        checked_text: str,
        unchecked_text: str,
        width: int,
    ) -> tk.Button:
        button = tk.Button(
            parent,
            width=width,
            font=("Microsoft JhengHei UI", 10, "bold"),
            relief="flat",
            cursor="hand2",
            padx=8,
            pady=5,
        )

        def refresh(*_args) -> None:
            checked = bool(variable.get())
            button.configure(
                text=checked_text if checked else unchecked_text,
                bg="#d9eee5" if checked else "#eee9df",
                fg="#145c46" if checked else "#756f66",
                activebackground="#c7e4d7" if checked else "#e1dbd0",
                activeforeground="#145c46" if checked else "#756f66",
            )

        def toggle() -> None:
            variable.set(not variable.get())
            refresh()

        button.configure(command=toggle)
        variable.trace_add("write", refresh)
        refresh()
        return button

    def _build_leave_tab(self) -> None:
        form = ttk.Frame(self.leave_tab, style="Card.TFrame")
        form.pack(fill="x", pady=(0, 12))
        ttk.Label(form, text="請假日期（YYYY-MM-DD）：").grid(row=0, column=0, padx=6, pady=8, sticky="w")
        date_entry = ttk.Entry(form, textvariable=self.date_var, width=16)
        date_entry.grid(row=0, column=1, padx=6)
        date_entry.bind("<FocusOut>", lambda _event: self._refresh_leave_choices())
        ttk.Label(form, text="請假時段：").grid(row=0, column=2, padx=(24, 6), pady=8)
        self.leave_combo = ttk.Combobox(form, textvariable=self.leave_shift_var, state="readonly", width=24)
        self.leave_combo.grid(row=0, column=3, padx=6)
        ttk.Button(form, text="加入請假", command=self.add_leave, style="Accent.TButton").grid(row=0, column=4, padx=12)

        self.leave_tree = ttk.Treeview(self.leave_tab, columns=("date", "time"), show="headings", height=14)
        self.leave_tree.heading("date", text="日期")
        self.leave_tree.heading("time", text="時段")
        self.leave_tree.column("date", width=180, anchor="center")
        self.leave_tree.column("time", width=300, anchor="center")
        self.leave_tree.pack(fill="both", expand=True, pady=8)
        ttk.Button(self.leave_tab, text="刪除選取請假", command=self.remove_leave).pack(anchor="e", pady=8)

    def _build_work_tab(self) -> None:
        ttk.Label(
            self.work_tab,
            text="每日工作摘要",
            style="Heading.TLabel",
        ).pack(anchor="w")
        ttk.Label(
            self.work_tab,
            text="內容會一直保留並重複使用，直到你在這裡修改後重新儲存。最後一班簽退成功後會自動填入並傳送。",
            wraplength=820,
            justify="left",
        ).pack(anchor="w", pady=(6, 14))
        self.work_text = tk.Text(
            self.work_tab,
            height=18,
            wrap="word",
            font=("Microsoft JhengHei UI", 11),
            bg="#fffef9",
            fg="#173b36",
            insertbackground="#173b36",
            relief="solid",
            borderwidth=1,
            padx=14,
            pady=14,
        )
        self.work_text.pack(fill="both", expand=True)
        actions = ttk.Frame(self.work_tab, style="Card.TFrame")
        actions.pack(fill="x", pady=(12, 0))
        ttk.Button(
            actions,
            text="儲存工作內容",
            command=self.save_work_content,
            style="Accent.TButton",
        ).pack(side="right")

    def _work_log_path(self) -> Path:
        configured = Path(str(self.config_data.get("work_log_path", "./work_log.txt")))
        return configured if configured.is_absolute() else BASE_DIR / configured

    def _load_work_content(self) -> None:
        path = self._work_log_path()
        content = path.read_text(encoding="utf-8-sig").strip() if path.exists() else ""
        self.work_text.delete("1.0", "end")
        self.work_text.insert("1.0", content)

    def _save_work_content(self, show_message: bool = True) -> bool:
        content = self.work_text.get("1.0", "end-1c").strip()
        if not content:
            messagebox.showerror("工作內容不可空白", "請填寫工作內容後再儲存。")
            return False
        path = self._work_log_path()
        path.write_text(content + "\n", encoding="utf-8")
        if show_message:
            messagebox.showinfo("已儲存", f"工作內容已儲存至：\n{path}")
        return True

    def save_work_content(self) -> None:
        self._save_work_content(show_message=True)

    def _build_status_tab(self) -> None:
        ttk.Label(self.status_tab, textvariable=self.status_var, style="Heading.TLabel").pack(anchor="w", pady=(0, 12))
        buttons = ttk.Frame(self.status_tab, style="Card.TFrame")
        buttons.pack(fill="x", pady=(0, 12))
        ttk.Button(buttons, text="重新整理狀態", command=self.refresh_status).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="開啟 logs 資料夾", command=self.open_logs).pack(side="left", padx=8)
        ttk.Button(buttons, text="立即掃描（實際執行）", command=self.run_scan).pack(side="left", padx=8)
        self.output = tk.Text(self.status_tab, height=22, wrap="word", font=("Consolas", 10), bg="#122a27", fg="#e8f1ed", insertbackground="white", padx=12, pady=12)
        self.output.pack(fill="both", expand=True)

        footer = ttk.Frame(self, padding=(24, 0, 24, 20), style="TFrame")
        footer.pack(fill="x")
        ttk.Button(footer, text="只驗證，不儲存", command=self.validate_only).pack(side="right", padx=8)
        ttk.Button(footer, text="儲存、測試並套用", command=self.save_and_apply, style="Accent.TButton").pack(side="right")

    def _load_weekly_values(self) -> None:
        weekly = self.config_data.get("weekly_schedule", {}) or {}
        for day, variables in self.day_vars.items():
            shifts = weekly.get(day, []) or []
            variables["enabled"].set(bool(shifts))
            for index in range(2):
                variables[f"start{index + 1}"].set(shifts[index].get("start", "") if index < len(shifts) else "")
                variables[f"end{index + 1}"].set(shifts[index].get("end", "") if index < len(shifts) else "")

    def _collect_config(self) -> dict:
        config = ensure_ui_defaults(self.config_data)
        config["automation"]["enabled"] = bool(self.enabled_var.get())
        config["schedule"]["scan_time"] = self.scan_time_var.get().strip()
        weekly = {}
        for day, variables in self.day_vars.items():
            shifts = []
            if variables["enabled"].get():
                for index in (1, 2):
                    start = variables[f"start{index}"].get().strip()
                    end = variables[f"end{index}"].get().strip()
                    if start or end:
                        shifts.append({"start": start, "end": end})
            weekly[day] = shifts
        config["weekly_schedule"] = weekly
        config["leave"]["requests"] = sorted(self.leave_rows, key=lambda item: (item["date"], item["start"]))
        return config

    def _shifts_for_date(self, date_text: str) -> list[dict[str, str]]:
        try:
            target = date.fromisoformat(date_text)
        except ValueError:
            return []
        day = WEEKDAY_KEYS[target.weekday()]
        config = self._collect_config()
        return config["weekly_schedule"].get(day, []) or []

    def _refresh_leave_choices(self) -> None:
        shifts = self._shifts_for_date(self.date_var.get().strip())
        choices = [f"{item['start']}-{item['end']}" for item in shifts if item.get("start") and item.get("end")]
        self.leave_combo["values"] = choices
        self.leave_shift_var.set(choices[0] if choices else "")

    def add_leave(self) -> None:
        date_text = self.date_var.get().strip()
        try:
            date.fromisoformat(date_text)
        except ValueError:
            messagebox.showerror("日期錯誤", "請使用 YYYY-MM-DD，例如 2026-09-21。")
            return
        selection = self.leave_shift_var.get()
        if "-" not in selection:
            messagebox.showerror("沒有可選班次", "請先在每週班表設定該星期的班次。")
            return
        start, end = selection.split("-", 1)
        item = {"date": date_text, "start": start, "end": end}
        if item not in self.leave_rows:
            self.leave_rows.append(item)
        self._refresh_leave_tree()

    def remove_leave(self) -> None:
        selected = self.leave_tree.selection()
        indexes = sorted((int(self.leave_tree.item(item, "tags")[0]) for item in selected), reverse=True)
        for index in indexes:
            self.leave_rows.pop(index)
        self._refresh_leave_tree()

    def _refresh_leave_tree(self) -> None:
        self.leave_rows = sorted(
            self.leave_rows, key=lambda item: (item["date"], item["start"])
        )
        for item in self.leave_tree.get_children():
            self.leave_tree.delete(item)
        for index, request in enumerate(self.leave_rows):
            self.leave_tree.insert("", "end", values=(request["date"], f"{request['start']} - {request['end']}"), tags=(str(index),))

    def _validation_report(self, config: dict) -> tuple[list[str], str]:
        errors = validate_config(config)
        lines = ["=== 內部排程預覽（未操作網站） ==="]
        lines.extend(preview_schedule(config) or ["未來 21 天沒有設定上班日。"])
        return errors, "\n".join(lines)

    def validate_only(self) -> None:
        config = self._collect_config()
        errors, report = self._validation_report(config)
        self._show_output(report + ("\n\n驗證失敗：\n- " + "\n- ".join(errors) if errors else "\n\n設定驗證通過。"))
        if errors:
            messagebox.showerror("驗證失敗", "\n".join(errors))
        else:
            messagebox.showinfo("驗證完成", "設定格式與未來排程預覽均通過。")

    def save_and_apply(self) -> None:
        config = self._collect_config()
        errors, report = self._validation_report(config)
        if errors:
            self._show_output(report + "\n\n驗證失敗：\n- " + "\n- ".join(errors))
            messagebox.showerror("無法儲存", "\n".join(errors))
            return
        if not self._save_work_content(show_message=False):
            return
        save_yaml(CONFIG_PATH, config)
        ok, scheduler_result = apply_master_task(config, str(BASE_DIR))
        self.config_data = config
        self._show_output(report + "\n\n=== Windows 排程檢查 ===\n" + scheduler_result)
        self.refresh_status(keep_output=True)
        if ok:
            messagebox.showinfo("完成", "設定已儲存，內部驗證與 Windows 排程檢查均完成。")
        else:
            messagebox.showerror("排程套用失敗", scheduler_result)

    def refresh_status(self, keep_output: bool = False) -> None:
        task_name = str(self.config_data.get("automation", {}).get("task_name", TASK_NAME))
        exists, details = query_master_task(task_name)
        enabled = bool(self.enabled_var.get())
        state = "啟用" if enabled else "停用"
        task_state = "存在" if exists else "不存在"
        self.status_var.set(f"UI 設定：{state}　｜　Windows 主排程：{task_state}　｜　{datetime.now():%Y-%m-%d %H:%M:%S}")
        if not keep_output:
            self._show_output(details if details else "尚未建立 Windows 主排程。")

    def _show_output(self, text: str) -> None:
        self.output.delete("1.0", "end")
        self.output.insert("1.0", text)

    def open_logs(self) -> None:
        logs = BASE_DIR / "logs"
        logs.mkdir(exist_ok=True)
        os.startfile(logs)

    def run_scan(self) -> None:
        if not messagebox.askyesno("確認實際掃描", "這會登入網站並依目前設定建立今天的臨時任務，確定執行嗎？"):
            return
        subprocess.Popen([sys.executable, str(BASE_DIR / "main.py"), "--scan"], cwd=BASE_DIR)
        messagebox.showinfo("已啟動", "已在背景啟動今天的實際掃描，請稍後查看 logs。")


if __name__ == "__main__":
    AttendanceUI().mainloop()
