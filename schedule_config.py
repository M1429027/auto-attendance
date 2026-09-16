"""Configuration and Windows Task Scheduler helpers for the local UI."""

from __future__ import annotations

import copy
import locale
import os
import re
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Union

import yaml


TASK_NAME = "CGU_AutoAttendance_Scan"
WEEKDAY_KEYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
WEEKDAY_LABELS = {
    "monday": "星期一",
    "tuesday": "星期二",
    "wednesday": "星期三",
    "thursday": "星期四",
    "friday": "星期五",
    "saturday": "星期六",
    "sunday": "星期日",
}
SCHTASKS_DAYS = {
    "monday": "MON",
    "tuesday": "TUE",
    "wednesday": "WED",
    "thursday": "THU",
    "friday": "FRI",
    "saturday": "SAT",
    "sunday": "SUN",
}
TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


def load_yaml(path: Union[str, os.PathLike]) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8-sig") as handle:
        return yaml.safe_load(handle) or {}


def save_yaml(path: Union[str, os.PathLike], config: dict[str, Any]) -> None:
    target = Path(path)
    backup = target.with_suffix(target.suffix + ".bak")
    if target.exists():
        backup.write_bytes(target.read_bytes())
    target.write_text(
        yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def ensure_ui_defaults(config: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(config)
    result.setdefault("automation", {})
    result["automation"].setdefault("enabled", False)
    result["automation"].setdefault("task_name", TASK_NAME)

    schedule = result.setdefault("schedule", {})
    schedule.setdefault("scan_time", "08:30")

    weekly = result.setdefault("weekly_schedule", {})
    for key in WEEKDAY_KEYS:
        weekly.setdefault(key, [])

    leave = result.setdefault("leave", {})
    leave.setdefault("requests", [])
    leave.setdefault("reason_text", "請假(未出勤)")
    leave.setdefault("auto_submit", True)
    leave.setdefault("click_retry_once_sec", 30)
    return result


def validate_config(config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    scan_time = str(config.get("schedule", {}).get("scan_time", ""))
    if not TIME_RE.fullmatch(scan_time):
        errors.append("掃描時間必須是 HH:MM，例如 08:30。")

    weekly = config.get("weekly_schedule", {}) or {}
    for day in WEEKDAY_KEYS:
        previous_end = None
        shifts = weekly.get(day, []) or []
        for index, shift in enumerate(shifts, start=1):
            start = str(shift.get("start", ""))
            end = str(shift.get("end", ""))
            if not TIME_RE.fullmatch(start) or not TIME_RE.fullmatch(end):
                errors.append(f"{WEEKDAY_LABELS[day]}第 {index} 班時間格式錯誤。")
                continue
            if start >= end:
                errors.append(f"{WEEKDAY_LABELS[day]}第 {index} 班結束時間必須晚於開始時間。")
            if previous_end and start < previous_end:
                errors.append(f"{WEEKDAY_LABELS[day]}班次時間有重疊。")
            previous_end = end

    for index, request in enumerate(config.get("leave", {}).get("requests", []) or [], start=1):
        try:
            request_date = date.fromisoformat(str(request.get("date", "")))
        except ValueError:
            errors.append(f"第 {index} 筆請假日期格式錯誤。")
            continue
        start = str(request.get("start", ""))
        end = str(request.get("end", ""))
        if not TIME_RE.fullmatch(start) or not TIME_RE.fullmatch(end) or start >= end:
            errors.append(f"第 {index} 筆請假時段格式錯誤。")
            continue
        day_key = WEEKDAY_KEYS[request_date.weekday()]
        available = weekly.get(day_key, []) or []
        if not any(s.get("start") == start and s.get("end") == end for s in available):
            errors.append(
                f"第 {index} 筆請假 {request_date} {start}-{end} 不在該星期的班表中。"
            )
    return errors


def configured_weekdays(config: dict[str, Any]) -> list[str]:
    weekly = config.get("weekly_schedule", {}) or {}
    return [key for key in WEEKDAY_KEYS if weekly.get(key)]


def is_configured_workday(config: dict[str, Any], target_date: date) -> bool:
    key = WEEKDAY_KEYS[target_date.weekday()]
    return bool((config.get("weekly_schedule", {}) or {}).get(key, []))


def expected_shifts_for_date(
    config: dict[str, Any], target_date: date
) -> list[dict[str, str]]:
    key = WEEKDAY_KEYS[target_date.weekday()]
    return list((config.get("weekly_schedule", {}) or {}).get(key, []) or [])


def shifts_match_expected(
    expected: list[dict[str, Any]], actual: list[dict[str, Any]]
) -> bool:
    expected_times = [
        (str(item.get("start")), str(item.get("end"))) for item in expected
    ]
    actual_times = [
        (str(item.get("start")), str(item.get("end"))) for item in actual
    ]
    return expected_times == actual_times


def leave_indexes_for_shifts(
    config: dict[str, Any], date_str: str, shifts: list[dict[str, Any]]
) -> list[int]:
    requests = config.get("leave", {}).get("requests", []) or []
    requested_times = {
        (str(item.get("start")), str(item.get("end")))
        for item in requests
        if str(item.get("date")) == date_str
    }
    indexes = [
        index
        for index, shift in enumerate(shifts, start=1)
        if (str(shift.get("start")), str(shift.get("end"))) in requested_times
    ]
    if indexes:
        return indexes

    # Backward compatibility for existing configs.
    leave = config.get("leave", {}) or {}
    if date_str not in set(leave.get("leave_dates", []) or []):
        return []
    return [
        int(value)
        for value in (leave.get("leave_shift_indexes_by_date", {}) or {}).get(date_str, [])
        if str(value).isdigit() and int(value) > 0
    ]


def preview_schedule(config: dict[str, Any], days: int = 21) -> list[str]:
    lines: list[str] = []
    weekly = config.get("weekly_schedule", {}) or {}
    requests = config.get("leave", {}).get("requests", []) or []
    holidays = set(config.get("holiday", {}).get("holiday_dates", []) or [])
    today = date.today()
    for offset in range(days):
        current = today + timedelta(days=offset)
        day_key = WEEKDAY_KEYS[current.weekday()]
        shifts = weekly.get(day_key, []) or []
        if not shifts:
            continue
        date_str = current.isoformat()
        if date_str in holidays:
            lines.append(f"{date_str} {WEEKDAY_LABELS[day_key]}：放假，完全不執行")
            continue
        leave_times = {
            (str(item.get("start")), str(item.get("end")))
            for item in requests
            if str(item.get("date")) == date_str
        }
        descriptions = []
        for shift in shifts:
            span = (str(shift.get("start")), str(shift.get("end")))
            state = "請假" if span in leave_times else "正常"
            descriptions.append(f"{span[0]}-{span[1]}（{state}）")
        lines.append(f"{date_str} {WEEKDAY_LABELS[day_key]}：" + "、".join(descriptions))
    return lines


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        encoding=locale.getpreferredencoding(False),
        errors="replace",
    )


def query_master_task(task_name: str = TASK_NAME) -> tuple[bool, str]:
    result = _run(["schtasks", "/Query", "/TN", task_name, "/V", "/FO", "LIST"])
    return result.returncode == 0, (result.stdout or result.stderr).strip()


def apply_master_task(config: dict[str, Any], repo_dir: str) -> tuple[bool, str]:
    task_name = str(config.get("automation", {}).get("task_name", TASK_NAME))
    enabled = bool(config.get("automation", {}).get("enabled", False))
    days = configured_weekdays(config)

    if not enabled:
        exists, _ = query_master_task(task_name)
        if not exists:
            return True, "自動化目前停用，Windows 主排程尚未建立。"
        result = _run(["schtasks", "/Change", "/TN", task_name, "/Disable"])
        return result.returncode == 0, (result.stdout or result.stderr).strip()

    if not days:
        return False, "已啟用自動化，但每週班表沒有任何上班日。"

    script = os.path.join(repo_dir, "install_schedule.ps1")
    result = _run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            script,
            "-TaskName",
            task_name,
            "-StartTime",
            str(config["schedule"]["scan_time"]),
            "-Days",
            ",".join(SCHTASKS_DAYS[day] for day in days),
        ]
    )
    if result.returncode != 0:
        return False, (result.stdout or result.stderr).strip()

    verify_ok, details = query_master_task(task_name)
    if not verify_ok:
        return False, "排程建立後查詢失敗：\n" + details
    return True, "Windows 主排程已建立並驗證。\n" + details
