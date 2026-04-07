"""
CGU 自動點名系統 - 主程式入口

模式：
  1. `python main.py --scan`
     掃描今日班表，建立當日一次性 Windows 任務後立即退出。
  2. `python main.py --run-signin-job ...`
     供 Windows 臨時任務呼叫，到點才執行簽到。
  3. `python main.py --run-signout-job ...`
     供 Windows 臨時任務呼叫，到點才執行簽退，最後一班會送工作日誌。
  4. `python main.py --run-leave-job ...`
     請假日立即執行請假流程。
"""

import atexit
import csv
import io
import os
import shlex
import signal
import subprocess
import sys
import time as _time
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import logging
import yaml

from attendance import (
    fill_leave_work_log,
    fill_work_log,
    get_work_content,
    init_browser,
    navigate_to_attendance,
    read_scheduled_shifts,
    send_line_notify,
    sign_in,
    sign_out,
)


os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(
            f"logs/attendance_{datetime.now().strftime('%Y%m%d')}.log",
            encoding="utf-8",
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPT_PATH = os.path.join(SCRIPT_DIR, "main.py")
TEMP_TASK_PREFIX = "CGU_AA_TMP_"
PYTHON_EXE = sys.executable
RUN_JOB_PS1 = os.path.join(SCRIPT_DIR, "run_job.ps1")


def _ps_single_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _log_process_start():
    logger.info(f"[PROC] start pid={os.getpid()} cwd={os.getcwd()} argv={sys.argv}")


def _log_process_exit():
    logger.info(f"[PROC] exit pid={os.getpid()}")


def _install_signal_logging():
    def _handler(signum, _frame):
        logger.error(f"[PROC] received signal={signum}, process will exit")
        raise SystemExit(128 + signum)

    for signum in (
        getattr(signal, "SIGINT", None),
        getattr(signal, "SIGTERM", None),
        getattr(signal, "SIGBREAK", None),
    ):
        if signum is None:
            continue
        try:
            signal.signal(signum, _handler)
        except (ValueError, OSError):
            pass


atexit.register(_log_process_exit)
_install_signal_logging()
_log_process_start()


def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _today_str(tz: ZoneInfo) -> str:
    return datetime.now(tz).strftime("%Y-%m-%d")


def _append_daily_summary(message: str, when: Optional[datetime] = None):
    ts = when or datetime.now()
    path = os.path.join("logs", f"daily_summary_{ts.strftime('%Y%m%d')}.log")
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{ts.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")


def _wait_until(not_before: Optional[datetime], label: str):
    if not not_before:
        return

    tz = not_before.tzinfo
    now = datetime.now(tz) if tz else datetime.now()
    delta = (not_before - now).total_seconds()
    if delta <= 0:
        return

    logger.info(f"[GUARD] {label} 提早觸發，等待 {delta:.1f}s 直到 {not_before.isoformat()}")
    while True:
        now = datetime.now(tz) if tz else datetime.now()
        remain = (not_before - now).total_seconds()
        if remain <= 0:
            logger.info(f"[GUARD] {label} reached target time")
            return
        _time.sleep(min(0.5, remain))


def _parse_iso_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _parse_today_time(hm: str, tz: ZoneInfo) -> datetime:
    hour, minute = map(int, hm.split(":"))
    return datetime.now(tz).replace(hour=hour, minute=minute, second=0, microsecond=0)


def _round_up_to_next_minute(dt: datetime) -> datetime:
    base = dt.replace(second=0, microsecond=0)
    if dt == base:
        return dt
    return base + timedelta(minutes=1)


def _task_name(date_str: str, task_type: str, row_key: str) -> str:
    return f"{TEMP_TASK_PREFIX}{date_str.replace('-', '')}_{task_type}_{row_key}"


def _run_subprocess(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _list_temp_tasks() -> list[str]:
    result = _run_subprocess(["schtasks", "/Query", "/FO", "CSV", "/NH"])
    if result.returncode != 0:
        logger.warning(f"列出排程任務失敗: {result.stderr.strip()}")
        return []

    tasks: list[str] = []
    reader = csv.reader(io.StringIO(result.stdout))
    for row in reader:
        if not row:
            continue
        task_name = row[0].lstrip("\\")
        if task_name.startswith(TEMP_TASK_PREFIX):
            tasks.append(task_name)
    return tasks


def _delete_task(task_name: str):
    result = _run_subprocess(["schtasks", "/Delete", "/TN", task_name, "/F"])
    if result.returncode == 0:
        logger.info(f"[TASK] 已刪除: {task_name}")
    else:
        logger.warning(f"[TASK] 刪除失敗 {task_name}: {result.stderr.strip()}")


def cleanup_temp_tasks(task_date: Optional[str] = None):
    for task_name in _list_temp_tasks():
        if task_date and task_date.replace("-", "") not in task_name:
            continue
        _delete_task(task_name)


def _build_task_command(args: list[str]) -> str:
    quoted = subprocess.list2cmdline(args)
    return (
        'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass '
        f'-WindowStyle Hidden -File "{RUN_JOB_PS1}" {quoted}'
    )


def _create_once_task(task_name: str, args: list[str], run_at: datetime):
    run_at = _round_up_to_next_minute(run_at)
    command = _build_task_command(args)
    result = _run_subprocess(
        [
            "schtasks",
            "/Create",
            "/TN",
            task_name,
            "/TR",
            command,
            "/SC",
            "ONCE",
            "/SD",
            run_at.strftime("%Y/%m/%d"),
            "/ST",
            run_at.strftime("%H:%M"),
            "/RL",
            "LIMITED",
            "/F",
            "/IT",
        ]
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    logger.info(f"[TASK] 已建立: {task_name} @ {run_at.strftime('%Y-%m-%d %H:%M')}")


def _schedule_worker_job(task_name: str, args: list[str], target_time: datetime):
    lead_run = target_time - timedelta(minutes=10)
    now = datetime.now(target_time.tzinfo) if target_time.tzinfo else datetime.now()
    if lead_run <= now:
        lead_run = now + timedelta(seconds=5)
    _create_once_task(task_name, args, lead_run)


def _get_leave_shift_indexes_for_date(config: dict, date_str: str) -> list[int]:
    leave_cfg = config.get("leave", {}) or {}
    leave_dates = set(leave_cfg.get("leave_dates", []) or [])
    if date_str not in leave_dates:
        return []
    raw = (leave_cfg.get("leave_shift_indexes_by_date", {}) or {}).get(date_str)
    if not raw:
        logger.warning(f"[LEAVE] {date_str} 命中請假日，但未設定班次索引，略過")
        return []
    indexes: list[int] = []
    for value in raw:
        try:
            idx = int(value)
        except (TypeError, ValueError):
            logger.warning(f"[LEAVE] 無法解析班次索引: {value}")
            continue
        if idx <= 0:
            logger.warning(f"[LEAVE] 班次索引需從 1 開始: {value}")
            continue
        indexes.append(idx)
    return indexes


def _get_holiday_dates(config: dict) -> set[str]:
    holiday_cfg = config.get("holiday", {}) or {}
    return set(holiday_cfg.get("holiday_dates", []) or [])


def _map_leave_indexes_to_suffixes(shifts: list[dict], shift_indexes: list[int]) -> list[str]:
    suffixes: list[str] = []
    for idx in shift_indexes:
        real_index = idx - 1
        if real_index >= len(shifts):
            logger.warning(f"[LEAVE] 第 {idx} 班不存在，今日只有 {len(shifts)} 班")
            continue
        suffixes.append(shifts[real_index]["row_suffix"])
    return suffixes


def _should_skip_shift(target_dt: datetime, now: datetime, late_grace_sec: int) -> bool:
    if target_dt > now:
        return False
    return (now - target_dt).total_seconds() > late_grace_sec


def _record_job_result(kind: str, row_suffix: str, success: bool, detail: str = ""):
    state = "OK" if success else "FAIL"
    extra = f" {detail}" if detail else ""
    _append_daily_summary(f"[{kind}] {state} {row_suffix}{extra}")


def _run_with_single_retry(label: str, func, retry_sec: int) -> bool:
    if func():
        return True
    if retry_sec <= 0:
        return False
    logger.warning(f"[RETRY] {label} 失敗，{retry_sec} 秒後重試一次")
    _time.sleep(retry_sec)
    return func()


def _load_shifts_for_today(config: dict) -> list[dict]:
    driver = None
    try:
        logger.info("[LOAD-SHIFTS] init browser")
        driver = init_browser(headless=config["browser"]["headless"])
        logger.info("[LOAD-SHIFTS] navigate to attendance")
        navigate_to_attendance(driver, config["url"], config["browser"]["wait_timeout"])
        logger.info("[LOAD-SHIFTS] read shifts")
        return read_scheduled_shifts(driver, config["browser"]["wait_timeout"])
    finally:
        if driver:
            driver.quit()


def _do_sign_in(row_suffix: str, not_before: Optional[datetime] = None) -> bool:
    config = load_config()
    logger.info(f"{'=' * 50}")
    logger.info(f"[SIGN-IN] Row: {row_suffix}")
    driver = None
    try:
        _wait_until(not_before, f"SIGN-IN({row_suffix})")
        logger.info(f"[SIGN-IN] init browser row={row_suffix}")
        driver = init_browser(headless=config["browser"]["headless"])
        logger.info(f"[SIGN-IN] navigate row={row_suffix}")
        navigate_to_attendance(driver, config["url"], config["browser"]["wait_timeout"])
        logger.info(f"[SIGN-IN] click sign-in row={row_suffix}")
        success = sign_in(driver, row_suffix, config["browser"]["wait_timeout"])
        msg = "[OK] 簽到成功" if success else "[FAIL] 簽到失敗"
        logger.info(msg)
        send_line_notify(config["notification"]["line_token"], f"【自動點名】{msg} ({row_suffix})")
        return success
    except Exception as e:
        logger.error(f"簽到任務例外: {e}", exc_info=True)
        return False
    finally:
        if driver:
            driver.quit()
        logger.info(f"{'=' * 50}")


def _do_sign_out(row_suffix: str, is_last_shift: bool, not_before: Optional[datetime] = None) -> bool:
    config = load_config()
    tz = ZoneInfo(config["schedule"]["timezone"])
    today_str = _today_str(tz)
    leave_suffixes = _map_leave_indexes_to_suffixes(
        _load_shifts_for_today(config),
        _get_leave_shift_indexes_for_date(config, today_str),
    )
    logger.info(f"{'=' * 50}")
    logger.info(f"[SIGN-OUT] Row: {row_suffix} | 最後一班: {is_last_shift}")
    driver = None
    try:
        logger.info(f"[SIGN-OUT] leave preload row={row_suffix}")
        _wait_until(not_before, f"SIGN-OUT({row_suffix})")
        logger.info(f"[SIGN-OUT] init browser row={row_suffix}")
        driver = init_browser(headless=config["browser"]["headless"])
        logger.info(f"[SIGN-OUT] navigate row={row_suffix}")
        navigate_to_attendance(driver, config["url"], config["browser"]["wait_timeout"])
        logger.info(f"[SIGN-OUT] read shifts row={row_suffix}")
        shifts = read_scheduled_shifts(driver, config["browser"]["wait_timeout"])

        logger.info(f"[SIGN-OUT] click sign-out row={row_suffix}")
        success = sign_out(driver, row_suffix, "", shifts, config["browser"]["wait_timeout"])
        msg = "[OK] 簽退成功" if success else "[FAIL] 簽退失敗"
        logger.info(msg)

        if is_last_shift and success:
            logger.info(f"[SIGN-OUT] worklog start row={row_suffix}")
            active_shifts = [shift for shift in shifts if shift["row_suffix"] not in set(leave_suffixes)]
            work_content = get_work_content(config["work_log_path"])
            if active_shifts:
                wl_ok = fill_work_log(
                    driver,
                    active_shifts,
                    work_content,
                    config["browser"]["wait_timeout"],
                )
                msg += "（工作日誌: OK）" if wl_ok else "（工作日誌: FAIL）"
            logger.info(f"[SIGN-OUT] cleanup temp tasks date={today_str}")
            cleanup_temp_tasks(today_str)

        send_line_notify(config["notification"]["line_token"], f"【自動點名】{msg} ({row_suffix})")
        return success
    except Exception as e:
        logger.error(f"簽退任務例外: {e}", exc_info=True)
        return False
    finally:
        if driver:
            driver.quit()
        logger.info(f"{'=' * 50}")


def _run_leave_flow(date_str: str) -> bool:
    config = load_config()
    shift_indexes = _get_leave_shift_indexes_for_date(config, date_str)
    if not shift_indexes:
        logger.info(f"[LEAVE] {date_str} 無有效請假班次設定")
        return False

    driver = None
    retry_sec = int(config.get("leave", {}).get("click_retry_once_sec", 30))
    reason_text = config.get("leave", {}).get("reason_text", "請假(未出勤)")
    auto_submit = bool(config.get("leave", {}).get("auto_submit", True))

    try:
        driver = init_browser(headless=config["browser"]["headless"])
        navigate_to_attendance(driver, config["url"], config["browser"]["wait_timeout"])
        shifts = read_scheduled_shifts(driver, config["browser"]["wait_timeout"])
        leave_suffixes = _map_leave_indexes_to_suffixes(shifts, shift_indexes)
        if not leave_suffixes:
            logger.warning(f"[LEAVE] {date_str} 沒有對應到任何班次")
            return False

        for suffix in leave_suffixes:
            _run_with_single_retry(
                f"LEAVE-SIGNIN({suffix})",
                lambda suffix=suffix: sign_in(driver, suffix, config["browser"]["wait_timeout"]),
                retry_sec,
            )
            _run_with_single_retry(
                f"LEAVE-SIGNOUT({suffix})",
                lambda suffix=suffix: sign_out(driver, suffix, "", shifts, config["browser"]["wait_timeout"]),
                retry_sec,
            )

        success = fill_leave_work_log(
            driver,
            shifts,
            leave_suffixes,
            reason_text,
            timeout=config["browser"]["wait_timeout"],
            auto_submit=auto_submit,
            retry_sec=retry_sec,
        )
        _append_daily_summary(
            f"[LEAVE] {'OK' if success else 'FAIL'} date={date_str} shifts={leave_suffixes}"
        )
        return success
    except Exception as e:
        logger.error(f"[LEAVE] 請假流程例外: {e}", exc_info=True)
        _append_daily_summary(f"[LEAVE] FAIL date={date_str} error={e}")
        return False
    finally:
        if driver:
            driver.quit()


def scan_and_schedule():
    config = load_config()
    tz = ZoneInfo(config["schedule"]["timezone"])
    late_grace_sec = int(config.get("schedule", {}).get("late_grace_sec", 120))
    retry_once_sec = int(config.get("schedule", {}).get("task_retry_once_sec", 120))
    today = datetime.now(tz)
    today_str = today.strftime("%Y-%m-%d")
    logger.info(f"{'=' * 50}")
    logger.info(f"[SCAN] 掃描今日班次 ({today_str})")

    cleanup_temp_tasks()
    if today_str in _get_holiday_dates(config):
        logger.info(f"[HOLIDAY] {today_str} is configured as holiday, skip all actions")
        _append_daily_summary(f"[HOLIDAY] SKIP date={today_str}")
        logger.info(f"{'=' * 50}")
        return

    shifts = _load_shifts_for_today(config)
    if not shifts:
        logger.warning("今日沒有排班資料，不建立任何臨時任務")
        _append_daily_summary("[SCAN] no shifts")
        return

    leave_indexes = _get_leave_shift_indexes_for_date(config, today_str)
    leave_suffixes = set(_map_leave_indexes_to_suffixes(shifts, leave_indexes))
    if leave_suffixes:
        logger.info(f"[LEAVE] 今日請假班次: {sorted(leave_suffixes)}")
        _run_leave_flow(today_str)

    normal_shifts = [shift for shift in shifts if shift["row_suffix"] not in leave_suffixes]
    logger.info(f"今日共 {len(shifts)} 個班次，正常班次 {len(normal_shifts)} 個")

    for idx, shift in enumerate(normal_shifts):
        is_last = idx == len(normal_shifts) - 1
        suffix = shift["row_suffix"]
        sign_in_dt = _parse_today_time(shift["start"], tz)
        sign_out_dt = _parse_today_time(shift["end"], tz)
        now = datetime.now(tz)

        if shift.get("can_sign_in") is False:
            logger.info(f"  [-] 班次 {suffix} 簽到未開放，跳過")
        elif _should_skip_shift(sign_in_dt, now, late_grace_sec):
            logger.info(f"  [-] 班次 {suffix} 簽到時間已過，跳過")
        else:
            signin_name = _task_name(today_str, "signin", suffix)
            signin_args = ["--run-signin-job", suffix, sign_in_dt.isoformat()]
            _schedule_worker_job(signin_name, signin_args, sign_in_dt)

        if _should_skip_shift(sign_out_dt, now, late_grace_sec):
            logger.info(f"  [-] 班次 {suffix} 簽退時間已過，跳過")
        else:
            signout_name = _task_name(today_str, "signout", suffix)
            signout_args = [
                "--run-signout-job",
                suffix,
                sign_out_dt.isoformat(),
                "true" if is_last else "false",
            ]
            _schedule_worker_job(signout_name, signout_args, sign_out_dt)

    _append_daily_summary(
        f"[SCAN] shifts={len(shifts)} normal={len(normal_shifts)} leave={sorted(leave_suffixes)} retry={retry_once_sec}s"
    )
    logger.info("[SCAN] 今日臨時任務建立完成，主程序退出")
    logger.info(f"{'=' * 50}")


def start_scheduler():
    config = load_config()
    tz = ZoneInfo(config["schedule"]["timezone"])
    scan_h, scan_m = map(int, config["schedule"]["scan_time"].split(":"))
    logger.info(f"排程器啟動（記憶體內掃描模式）: 每日 {scan_h:02d}:{scan_m:02d}")
    while True:
        now = datetime.now(tz)
        if now.hour == scan_h and now.minute == scan_m:
            scan_and_schedule()
            _time.sleep(60)
        _time.sleep(1)


def _run_signin_worker(row_suffix: str, target_time_iso: str):
    config = load_config()
    retry_sec = int(config.get("schedule", {}).get("task_retry_once_sec", 120))
    target_time = _parse_iso_datetime(target_time_iso)
    logger.info(
        f"[WORKER] SIGN-IN start row={row_suffix} target={target_time.isoformat()} retry={retry_sec}s"
    )
    success = _run_with_single_retry(
        f"SIGN-IN({row_suffix})",
        lambda: _do_sign_in(row_suffix, target_time),
        retry_sec,
    )
    _record_job_result("SIGN-IN", row_suffix, success)
    logger.info(f"[WORKER] SIGN-IN end row={row_suffix} success={success}")


def _run_signout_worker(row_suffix: str, target_time_iso: str, is_last_shift: bool):
    config = load_config()
    retry_sec = int(config.get("schedule", {}).get("task_retry_once_sec", 120))
    target_time = _parse_iso_datetime(target_time_iso)
    logger.info(
        f"[WORKER] SIGN-OUT start row={row_suffix} target={target_time.isoformat()} last={is_last_shift} retry={retry_sec}s"
    )
    success = _run_with_single_retry(
        f"SIGN-OUT({row_suffix})",
        lambda: _do_sign_out(row_suffix, is_last_shift, target_time),
        retry_sec,
    )
    _record_job_result("SIGN-OUT", row_suffix, success, f"last={is_last_shift}")
    logger.info(f"[WORKER] SIGN-OUT end row={row_suffix} success={success}")


if __name__ == "__main__":
    args = sys.argv[1:]

    if "--scan" in args:
        logger.info("[SCAN] 手動觸發：立即掃描班次")
        scan_and_schedule()
    elif "--run-signin-job" in args:
        idx = args.index("--run-signin-job")
        _run_signin_worker(args[idx + 1], args[idx + 2])
    elif "--run-signout-job" in args:
        idx = args.index("--run-signout-job")
        _run_signout_worker(args[idx + 1], args[idx + 2], args[idx + 3].lower() == "true")
    elif "--run-leave-job" in args:
        idx = args.index("--run-leave-job")
        _run_leave_flow(args[idx + 1])
    elif "--test-signin" in args:
        try:
            suffix = args[args.index("--test-signin") + 1]
        except IndexError:
            suffix = "ctl02"
        logger.info(f"[TEST-SIGNIN] row={suffix}")
        _do_sign_in(suffix)
    elif "--test-signout" in args:
        try:
            suffix = args[args.index("--test-signout") + 1]
        except IndexError:
            suffix = "ctl02"
        logger.info(f"[TEST-SIGNOUT] row={suffix}")
        _do_sign_out(suffix, is_last_shift=True)
    elif "--test-worklog" in args:
        config_t = load_config()
        logger.info("[TEST-WORKLOG] 開始測試工作日誌填寫")
        driver_t = None
        try:
            driver_t = init_browser(headless=False)
            navigate_to_attendance(driver_t, config_t["url"], config_t["browser"]["wait_timeout"])
            shifts_t = read_scheduled_shifts(driver_t, config_t["browser"]["wait_timeout"])
            if not shifts_t:
                logger.error("測試失敗：找不到班次資料")
            else:
                work_t = get_work_content(config_t["work_log_path"])
                ok = fill_work_log(
                    driver_t,
                    shifts_t,
                    work_t,
                    config_t["browser"]["wait_timeout"],
                    dry_run=True,
                )
                logger.info("[OK] 工作日誌填寫完成" if ok else "[FAIL] 工作日誌填寫失敗")
        except Exception as e:
            logger.error(f"測試例外: {e}", exc_info=True)
        finally:
            if driver_t:
                _time.sleep(3)
                driver_t.quit()
    else:
        start_scheduler()
