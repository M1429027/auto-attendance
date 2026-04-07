"""
CGU 自動點名系統 - 核心邏輯（動態排班版）
attendance.py

核心設計：
  每天啟動時登入網頁，讀取當日「預計出勤時間」，
  動態為每一班建立簽到/簽退任務，支援多班制。
"""

import time
import logging
import os
import re
import requests
from typing import Optional
from datetime import datetime, date
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# 瀏覽器初始化
# ─────────────────────────────────────────────
import shutil

# 真實 Chrome 的使用者資料夾（讀取 Cookies 用）
REAL_CHROME_DIR   = r"C:\Users\yp8700\AppData\Local\Google\Chrome\User Data\Default"
# 程式專用的隔離 Profile（安全，不占用真實 Chrome）
ISOLATED_PROFILE  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chrome_profile", "Default")

# 需要複製的檔案清單（含 session / cookies）
_COPY_FILES = ["Cookies", "Login Data", "Local State", "Preferences"]


def _prepare_profile():
    """從真實 Chrome 複製 Cookies 等必要檔案到隔離 Profile"""
    os.makedirs(ISOLATED_PROFILE, exist_ok=True)
    src_dir  = REAL_CHROME_DIR
    dst_dir  = ISOLATED_PROFILE
    copied   = []
    for fname in _COPY_FILES:
        src = os.path.join(src_dir, fname)
        dst = os.path.join(dst_dir, fname)
        if os.path.exists(src):
            try:
                shutil.copy2(src, dst)
                copied.append(fname)
            except Exception as e:
                logger.warning(f"複製 {fname} 失敗: {e}")
    logger.info(f"已從 Chrome 複製 Session 檔案: {copied}")


def init_browser(headless: bool = False) -> webdriver.Chrome:
    """
    初始化 Chrome，使用隔離 Profile（已複製真實 Cookies，不鎖定主要 Chrome）。
    """
    # 先複製 Cookies、Preferences 等檔案
    _prepare_profile()

    profile_parent = os.path.dirname(ISOLATED_PROFILE)  # chrome_profile/

    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument(f"--user-data-dir={profile_parent}")
    options.add_argument("--profile-directory=Default")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)

    logger.info(f"[BROWSER] launching Chrome headless={headless}")
    driver = webdriver.Chrome(options=options)
    logger.info("[BROWSER] Chrome launched")
    driver.execute_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    logger.info("瀏覽器初始化完成（隔離 Profile + 複製 Cookies）")
    return driver


def wait_for_element(driver, by, selector, timeout: int = 20):
    return WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((by, selector))
    )


def wait_for_clickable(driver, by, selector, timeout: int = 20):
    return WebDriverWait(driver, timeout).until(
        EC.element_to_be_clickable((by, selector))
    )


# ─────────────────────────────────────────────
# Microsoft SSO 登入
# ─────────────────────────────────────────────
def microsoft_login(driver, url: str, username: str, password: str, timeout: int = 20):
    """使用 Microsoft SSO 登入長庚大學系統"""
    logger.info(f"正在開啟頁面: {url}")
    driver.get(url)
    time.sleep(2)

    # 若已登入直接到 BPM，就不需要再登入
    if "flow.cgu.edu.tw" in driver.current_url:
        logger.info("已是登入狀態")
        return

    try:
        # 輸入帳號
        email_field = wait_for_element(driver, By.ID, "i0116", timeout)
        email_field.clear()
        email_field.send_keys(username)
        logger.info(f"已輸入帳號: {username}")
        wait_for_clickable(driver, By.ID, "idSIButton9", timeout).click()
        time.sleep(2)

        # 輸入密碼
        password_field = wait_for_element(driver, By.ID, "i0118", timeout)
        password_field.clear()
        password_field.send_keys(password)
        wait_for_clickable(driver, By.ID, "idSIButton9", timeout).click()
        time.sleep(2)

        # 詢問是否保持登入 → 是
        try:
            wait_for_clickable(driver, By.ID, "idSIButton9", 5).click()
            logger.info("已選擇保持登入狀態")
        except TimeoutException:
            pass

        # 等待跳回 BPM
        WebDriverWait(driver, timeout).until(
            EC.url_contains("flow.cgu.edu.tw")
        )
        logger.info("登入成功")
        time.sleep(3)

    except TimeoutException as e:
        if "flow.cgu.edu.tw" in driver.current_url:
            logger.info("已是登入狀態")
        else:
            logger.error(f"登入失敗: {e}")
            _save_screenshot(driver, "login_error")
            raise


# ─────────────────────────────────────────────
# 導覽至出勤簽到/退頁面
# ─────────────────────────────────────────────
def navigate_to_attendance(driver, url: str, timeout: int = 20):
    """
    開啟 BPM 首頁，點選左側選單 tree 導覽至出勤簽到/退頁面。
    """
    logger.info(f"開啟 BPM: {url}")
    driver.get(url)

    # 等待頁面載入（將 sidebar 元素出現為標準）
    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".sidebar, #sidebar, ul.accordion"))
        )
    except TimeoutException:
        pass
    time.sleep(2)

    # 如果被重導向到登入頁（URL 不包含 BPM 主機名）
    if "flow.cgu.edu.tw" not in driver.current_url:
        logger.error(
            "登入失敗！請先關閉所有 Chrome 視窗再執行程式。"
            f"目前頁面: {driver.current_url}"
        )
        _save_screenshot(driver, "login_required")
        raise RuntimeError("需要登入！請關閉 Chrome 後重試")

    logger.info("已進入 BPM，開始點選左側選單...")

    # 左側選單層級序列（ID, 說明）
    # 使用 JavaScript click 避免可見性/動畫問題
    menu_steps = [
        ("__RuntimePkgClsNode__CLIENT.CGUPC00003", "0200 人事室"),
        ("CLIENT.CGUPC00022",                     "012A 工讀生工作日誌"),
        ("CLIENT.CGUPK00010",                     "A1.出勤簽到/退"),
        ("CGUPK00010.CGUV000085",                 "出勤簽到退"),
    ]

    for elem_id, label in menu_steps:
        try:
            # 用 XPath 找 ID 含點的元素（避免 CSS selector 跨脱問題）
            elem = WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((By.XPATH, f"//*[@id='{elem_id}']"))
            )
            # 用 JavaScript 點擊，繞過可見性限制
            driver.execute_script("arguments[0].click();", elem)
            logger.info(f"  [OK] 點擊: {label}")
            time.sleep(1.5)
        except TimeoutException:
            logger.warning(f"  [SKIP] 找不到選單: {label} (id={elem_id})")

    # 等待出勤表格載入（切換到 iframe）
    try:
        driver.switch_to.default_content()
        driver.switch_to.frame("main")
        wait_for_element(driver, By.ID, "ctl00_cphMain_gvwData", timeout)
        logger.info("出勤簽到/退頁面載入完成")
    except TimeoutException:
        logger.warning("出勤表格未載入，可能 iframe 載入失敗")
        _save_screenshot(driver, "nav_error")

    time.sleep(1)


# ─────────────────────────────────────────────
# 讀取當日所有班次的預計出勤時間
# ─────────────────────────────────────────────
def read_scheduled_shifts(driver, timeout: int = 20) -> list[dict]:
    """
    掃描出勤表格，回傳當日所有班次資訊。

    實際 DOM（F12 確認）:
      - 簽到時間: select#ctl00_cphMain_gvwData_{suffix}_ddlESAttTime
        value 格式: "HHMM"  e.g. "1300" = 13:00
      - 簽退時間: select#ctl00_cphMain_gvwData_{suffix}_ddlEEAttTime
      - 兩者都有 disabled="disabled"（但只影響點擊，可讀取 value）
    """
    logger.info("讀取當日預計出勤時間...")
    shifts = []

    try:
        driver.switch_to.default_content()
        driver.switch_to.frame("main")
        wait_for_element(driver, By.ID, "ctl00_cphMain_gvwData", timeout)
    except Exception as e:
        logger.error(f"找不到出勤表格: {e}")
        return shifts

    # 用「預計簽到時間」的 select 定位所有班次列
    start_selects = driver.find_elements(
        By.CSS_SELECTOR, "select[id*='ddlESAttTime']"
    )

    if not start_selects:
        logger.warning("找不到 ddlESAttTime，請檢查頁面是否正確載入")
        _save_screenshot(driver, "no_time_selects")
        return shifts

    for sel in start_selects:
        sel_id = sel.get_attribute("id")
        # ctl00_cphMain_gvwData_ctl02_ddlESAttTime → ctl02
        match = re.search(r"gvwData_(ctl\d+)_ddlESAttTime", sel_id)
        if not match:
            continue
        suffix = match.group(1)

        # 讀取 HHMM 4位數格式
        start_val = sel.get_attribute("value")   # e.g. "1300"
        end_val   = _get_select_value(
            driver, f"ctl00_cphMain_gvwData_{suffix}_ddlEEAttTime"
        )

        if not start_val or not end_val:
            logger.warning(f"Row {suffix}: 找不到時間值，跳過")
            continue

        # HHMM → HH:MM
        start_time = f"{start_val[:2]}:{start_val[2:]}"
        end_time   = f"{end_val[:2]}:{end_val[2:]}"

        # 確認簽到/退按鈕可點擊狀態
        sign_in_btn = _find_element_safe(
            driver,
            f"input[id*='gvwData_{suffix}_btnSignIn'], "
            f"button[id*='gvwData_{suffix}_btnSignIn']"
        )
        can_sign_in = (
            sign_in_btn is not None
            and not sign_in_btn.get_attribute("disabled")
        )

        sign_out_btn = _find_element_safe(
            driver,
            f"input[id*='gvwData_{suffix}_btnSignOut'], "
            f"button[id*='gvwData_{suffix}_btnSignOut']"
        )
        can_sign_out = (
            sign_out_btn is not None
            and not sign_out_btn.get_attribute("disabled")
        )

        shift = {
            "row_suffix":   suffix,
            "start":        start_time,
            "end":          end_time,
            "can_sign_in":  can_sign_in,
            "can_sign_out": can_sign_out,
        }
        shifts.append(shift)
        logger.info(
            f"  班次 {suffix}: {start_time}~{end_time}  "
            f"[簽到: {'OK' if can_sign_in else '未開放'}] "
            f"[簽退: {'OK' if can_sign_out else '未開放'}]"
        )

    logger.info(f"共讀取到 {len(shifts)} 個班次")
    return shifts


def _get_select_value(driver, element_id: str) -> Optional[str]:
    """取得 select 元素目前選取的值（使用 JS 繞過 disabled 限制）"""
    try:
        elem = driver.find_element(By.ID, element_id)
        # 即使元素 disabled，JS 依然能直接取得屬性
        val = driver.execute_script("return arguments[0].value;", elem)
        return val if val else elem.get_attribute("value")
    except NoSuchElementException:
        return None


def _find_element_safe(driver, css_selector: str):
    """安全地尋找元素，找不到回傳 None"""
    try:
        return driver.find_element(By.CSS_SELECTOR, css_selector)
    except NoSuchElementException:
        return None


# ─────────────────────────────────────────────
# 簽到（指定班次）
# ─────────────────────────────────────────────
def sign_in(driver, row_suffix: str, timeout: int = 20) -> bool:
    """點擊指定班次的簽到按鈕"""
    logger.info(f"執行簽到 (row={row_suffix})...")
    btn_id = f"ctl00_cphMain_gvwData_{row_suffix}_btnSignIn"
    try:
        btn = wait_for_clickable(
            driver,
            By.CSS_SELECTOR,
            f"#{btn_id}, input[id='{btn_id}'], button[id='{btn_id}']",
            timeout,
        )
        btn.click()

        # 確認彈窗
        try:
            alert = WebDriverWait(driver, 5).until(EC.alert_is_present())
            logger.info(f"確認彈窗: {alert.text}")
            alert.accept()
        except TimeoutException:
            pass

        time.sleep(2)
        logger.info(f"[OK] 簽到成功 [{datetime.now().strftime('%H:%M:%S')}]")
        _save_screenshot(driver, f"sign_in_ok_{row_suffix}")
        return True

    except TimeoutException:
        logger.error(f"[FAIL] 找不到或無法點擊簽到按鈕 ({btn_id})")
        _save_screenshot(driver, f"sign_in_err_{row_suffix}")
        return False


def sign_out(driver, row_suffix: str, work_content: str,
             shifts: list, timeout: int = 20) -> bool:
    """
    點擊指定班次的簽退按鈕。
    is_last_shift=True 時才填寫工作日誌。
    shifts 用于將預計時間全部填入實際時間欄。
    """
    logger.info(f"執行簽退 (row={row_suffix})...")
    btn_id = f"ctl00_cphMain_gvwData_{row_suffix}_btnSignOut"
    try:
        # 確保在 iframe 中
        try:
            driver.switch_to.default_content()
            driver.switch_to.frame("main")
        except Exception:
            pass

        btn = wait_for_clickable(
            driver,
            By.CSS_SELECTOR,
            f"input[id='{btn_id}'], button[id='{btn_id}']",
            timeout,
        )
        driver.execute_script("arguments[0].click();", btn)

        try:
            alert = WebDriverWait(driver, 5).until(EC.alert_is_present())
            alert.accept()
        except TimeoutException:
            pass

        time.sleep(2)
        logger.info(f"[OK] 簽退成功 [{datetime.now().strftime('%H:%M:%S')}]")
        _save_screenshot(driver, f"sign_out_ok_{row_suffix}")
        return True

    except TimeoutException:
        logger.error(f"[FAIL] 找不到或無法點擊簽退按鈕 ({btn_id})")
        _save_screenshot(driver, f"sign_out_err_{row_suffix}")
        return False


def fill_work_log(driver, shifts: list, work_content: str,
                  timeout: int = 20, dry_run: bool = False) -> bool:
    """
    點擊「填寫工作日誌」按鈕 →
      - 每一列：實際簽到時間 = 預計簽到時間
              實際簽退時間 = 預計簽退時間
              工作摘要    = work_content
      - 點擊 傳送 送出

    實際 DOM ID 格式（已為 F12 確認）：
      Row01(ctl02): ftcFormHolder_FpgForm_F05C00000001505_ctl02_COLCTRL{ASAttTime|AEAttTime|WorkSummary}
      Row02(ctl03): ftcFormHolder_FpgForm_F05C00000001505_ctl03_...
      傳送按鈕: ucToolbar_F05B000316
    """
    logger.info("工作日誌填寫中...")
    context = _open_work_log_form(driver, timeout)
    if not context:
        return False
    prefix, worklog_rows = context

    # 對每一列填入資料
    for idx, shift in enumerate(shifts):
        target_suffix = _resolve_target_suffix(driver, prefix, worklog_rows, shift["row_suffix"], idx)
        _apply_shift_time_fields(driver, prefix, target_suffix, shift)
        if _set_work_summary(driver, prefix, target_suffix, work_content):
            logger.info(f"  Row {target_suffix}: 工作摘要已填入")
        else:
            logger.warning(f"  Row {target_suffix}: 找不到工作摘要欄")

    time.sleep(1)

    # dry_run 模式：填好後截圖並停住，不點傳送
    if dry_run:
        _save_screenshot(driver, "worklog_preview")
        logger.info("[DRY-RUN] 表單已填寫完成，請在瀏覽器視窗確認內容。")
        logger.info("[DRY-RUN] 程式將在 30 秒後自動關閉（或按 Ctrl+C 結束）。")
        import time as _t
        _t.sleep(30)
        return True

    return _submit_work_log_form(driver, timeout)


def fill_leave_work_log(
    driver,
    shifts: list,
    leave_row_suffixes: list[str],
    reason_text: str,
    timeout: int = 20,
    auto_submit: bool = True,
    retry_sec: int = 30,
) -> bool:
    """請假流程：指定列填未出勤與原因，工作摘要清空。"""
    logger.info("請假工作日誌填寫中...")
    context = _open_work_log_form(driver, timeout)
    if not context:
        return False
    prefix, worklog_rows = context
    leave_set = set(leave_row_suffixes)

    for idx, shift in enumerate(shifts):
        if shift["row_suffix"] not in leave_set:
            continue

        target_suffix = _resolve_target_suffix(driver, prefix, worklog_rows, shift["row_suffix"], idx)
        _apply_shift_time_fields(driver, prefix, target_suffix, shift)
        _set_work_summary(driver, prefix, target_suffix, "")

        def _reason_step():
            return _set_leave_reason(driver, prefix, target_suffix, reason_text)

        if _retry_bool_action(f"LEAVE-REASON({target_suffix})", _reason_step, retry_sec):
            logger.info(f"  Row {target_suffix}: 已填請假原因")
        else:
            logger.warning(f"  Row {target_suffix}: 請假原因欄設定失敗")

        def _absent_step():
            return _set_absent_checkbox(driver, prefix, target_suffix, True)

        if _retry_bool_action(f"LEAVE-ABSENT({target_suffix})", _absent_step, retry_sec):
            logger.info(f"  Row {target_suffix}: 已勾選未出勤")
        else:
            logger.warning(f"  Row {target_suffix}: 未出勤勾選失敗")

    if not auto_submit:
        _save_screenshot(driver, "leave_worklog_preview")
        return True

    return _submit_work_log_form(driver, timeout)


_WORKLOG_ID_RE = re.compile(
    r"^(?P<prefix>.+)_(?P<suffix>ctl\d+)_COLCTRL(?P<field>[A-Za-z0-9]+)$"
)


def _open_work_log_form(driver, timeout: int = 20) -> Optional[tuple[str, list[str]]]:
    """開啟工作日誌表單並解析 prefix / row suffix。"""
    handles_before = set(driver.window_handles)
    clicked = False
    for css in ["[id*='btnOpenWorkLog']", "input[value*='工作日誌']"]:
        try:
            btn = WebDriverWait(driver, 5).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, css))
            )
            driver.execute_script("arguments[0].click();", btn)
            logger.info("點擊「填寫工作日誌」")
            clicked = True
            break
        except TimeoutException:
            continue

    if not clicked:
        btn = driver.execute_script(
            "return Array.from(document.querySelectorAll('a,input,button'))"
            ".find(el => (el.textContent || '').includes('\u5de5\u4f5c\u65e5\u8a8c') "
            "|| (el.value || '').includes('\u5de5\u4f5c\u65e5\u8a8c'));"
        )
        if btn:
            driver.execute_script("arguments[0].click();", btn)
            logger.info("點擊「填寫工作日誌」(JS fallback)")
            clicked = True

    if not clicked:
        logger.error("找不到填寫工作日誌按鈕")
        _save_screenshot(driver, "worklog_btn_not_found")
        return None

    time.sleep(2)
    new_wins = set(driver.window_handles) - handles_before
    if new_wins:
        driver.switch_to.window(new_wins.pop())
        logger.info("切換到工作日誌彈出視窗")
        try:
            WebDriverWait(driver, 5).until(
                EC.frame_to_be_available_and_switch_to_it((By.NAME, "main"))
            )
            logger.info("切換到彈出視窗內 main iframe")
        except TimeoutException:
            pass
    else:
        logger.info("未偵測到新視窗，嘗試在目前 iframe 中繼續")

    if not _ensure_worklog_context(driver, timeout):
        logger.warning("工作日誌表單元素未出現")
        _save_screenshot(driver, "worklog_form_not_found")
        return None

    prefix = _discover_worklog_prefix(driver)
    if not prefix:
        logger.error("無法解析工作日誌欄位 ID 前綴（prefix）")
        _save_screenshot(driver, "worklog_prefix_not_found")
        return None

    worklog_rows = _discover_worklog_suffixes(driver, prefix)
    logger.info(f"worklog prefix: {prefix}")
    if worklog_rows:
        logger.info(f"worklog rows: {worklog_rows}")
    return prefix, worklog_rows


def _resolve_target_suffix(driver, prefix: str, worklog_rows: list[str], suffix: str, idx: int) -> str:
    target_suffix = suffix
    ws_id = f"{prefix}_{suffix}_COLCTRLWorkSummary"
    if not driver.find_elements(By.ID, ws_id) and worklog_rows and idx < len(worklog_rows):
        target_suffix = worklog_rows[idx]
        logger.warning(f"Row {suffix} 不存在於工作日誌頁，改用 {target_suffix}")
    return target_suffix


def _apply_shift_time_fields(driver, prefix: str, target_suffix: str, shift: dict):
    start_hhmm = shift["start"].replace(":", "")
    end_hhmm = shift["end"].replace(":", "")
    start_hm = shift["start"]
    end_hm = shift["end"]

    as_id = f"{prefix}_{target_suffix}_COLCTRLASAttTime"
    if _js_set_select(driver, as_id, start_hhmm, start_hm):
        logger.info(f"  Row {target_suffix}: 實際簽到 = {start_hm}")
    else:
        logger.warning(f"  Row {target_suffix}: 實際簽到下拉設定失敗 ({as_id})")

    ae_id = f"{prefix}_{target_suffix}_COLCTRLAEAttTime"
    if _js_set_select(driver, ae_id, end_hhmm, end_hm):
        logger.info(f"  Row {target_suffix}: 實際簽退 = {end_hm}")
    else:
        logger.warning(f"  Row {target_suffix}: 實際簽退下拉設定失敗 ({ae_id})")


def _set_work_summary(driver, prefix: str, target_suffix: str, content: str) -> bool:
    try:
        ta = driver.find_element(By.ID, f"{prefix}_{target_suffix}_COLCTRLWorkSummary")
        ta.clear()
        if content:
            ta.send_keys(content)
        return True
    except NoSuchElementException:
        return False


def _set_leave_reason(driver, prefix: str, target_suffix: str, reason_text: str) -> bool:
    field = _find_leave_reason_field(driver, prefix, target_suffix)
    if field is None:
        return False
    try:
        field.clear()
    except Exception:
        pass
    field.send_keys(reason_text)
    return True


def _set_absent_checkbox(driver, prefix: str, target_suffix: str, checked: bool) -> bool:
    checkbox = _find_absent_checkbox(driver, prefix, target_suffix)
    if checkbox is None:
        return False
    try:
        current = bool(checkbox.is_selected())
    except Exception:
        current = False
    if current != checked:
        driver.execute_script("arguments[0].click();", checkbox)
    return True


def _find_leave_reason_field(driver, prefix: str, target_suffix: str):
    exact = driver.find_elements(
        By.CSS_SELECTOR,
        f"textarea[id^='{prefix}_{target_suffix}_COLCTRL']:not([id$='COLCTRLWorkSummary'])",
    )
    if exact:
        return exact[0]

    row_scope = _find_worklog_row_scope(driver, prefix, target_suffix)
    if row_scope is None:
        return None

    matches = row_scope.find_elements(By.CSS_SELECTOR, "textarea")
    for match in matches:
        match_id = match.get_attribute("id") or ""
        if "WorkSummary" not in match_id:
            return match
    return matches[0] if matches else None


def _find_absent_checkbox(driver, prefix: str, target_suffix: str):
    exact = driver.find_elements(
        By.CSS_SELECTOR,
        f"input[type='checkbox'][id^='{prefix}_{target_suffix}_COLCTRL']",
    )
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return exact[-1]

    row_scope = _find_worklog_row_scope(driver, prefix, target_suffix)
    if row_scope is None:
        return None

    checkbox = driver.execute_script(
        """
        const row = arguments[0];
        const boxes = Array.from(row.querySelectorAll('input[type="checkbox"]'));
        const labelled = boxes.find(box => {
          const parentText = (box.parentElement?.innerText || '') + ' ' + (box.nextSibling?.textContent || '');
          return parentText.includes('未出勤');
        });
        return labelled || (boxes.length > 1 ? boxes[1] : boxes[0]) || null;
        """,
        row_scope,
    )
    return checkbox


def _find_worklog_row_scope(driver, prefix: str, target_suffix: str):
    anchor = None
    for field_name in ["WorkSummary", "ASAttTime", "AEAttTime"]:
        field_id = f"{prefix}_{target_suffix}_COLCTRL{field_name}"
        found = driver.find_elements(By.ID, field_id)
        if found:
            anchor = found[0]
            break
    if anchor is None:
        return None

    return driver.execute_script(
        """
        let node = arguments[0];
        while (node) {
          if (node.tagName === 'TR') return node;
          node = node.parentElement;
        }
        return arguments[0].parentElement;
        """,
        anchor,
    )


def _submit_work_log_form(driver, timeout: int = 20) -> bool:
    try:
        send_btn = wait_for_clickable(driver, By.ID, "ucToolbar_F05B000316", timeout)
        driver.execute_script("arguments[0].click();", send_btn)
        logger.info("點擊「傳送」")
        time.sleep(3)
        try:
            alert = WebDriverWait(driver, 8).until(EC.alert_is_present())
            logger.info(f"彈窗: {alert.text}")
            alert.accept()
        except TimeoutException:
            pass
        logger.info("[OK] 工作日誌傳送成功")
        _save_screenshot(driver, "work_log_submitted")
        return True
    except TimeoutException as e:
        logger.error(f"[FAIL] 傳送按鈕找不到: {e}")
        _save_screenshot(driver, "work_log_send_error")
        return False


def _retry_bool_action(label: str, func, retry_sec: int) -> bool:
    if func():
        return True
    if retry_sec <= 0:
        return False
    logger.warning(f"{label} 失敗，{retry_sec} 秒後重試一次")
    time.sleep(retry_sec)
    return func()


def _ensure_worklog_context(driver, timeout: int = 20) -> bool:
    """
    確保 driver 的目前 frame context 內找得到工作日誌欄位。
    若找不到，會嘗試在同一個 window 的各個 iframe（含一層 nested）中尋找。
    """
    def _has_worklog_fields() -> bool:
        return bool(driver.find_elements(By.CSS_SELECTOR, "[id$='COLCTRLWorkSummary']"))

    # 先在目前 frame 試
    try:
        WebDriverWait(driver, 3).until(lambda d: _has_worklog_fields())
        logger.info("工作日誌表單已載入（目前 frame）")
        return True
    except Exception:
        pass

    # 回到 default_content 後掃 iframe
    try:
        driver.switch_to.default_content()
    except Exception:
        pass

    iframes = driver.find_elements(By.TAG_NAME, "iframe")
    for idx, frame in enumerate(iframes):
        try:
            driver.switch_to.default_content()
            driver.switch_to.frame(frame)
            if _has_worklog_fields():
                logger.info(f"工作日誌表單已載入（iframe[{idx}]）")
                return True

            # nested 一層
            nested = driver.find_elements(By.TAG_NAME, "iframe")
            for nidx, nframe in enumerate(nested):
                try:
                    driver.switch_to.frame(nframe)
                    if _has_worklog_fields():
                        logger.info(f"工作日誌表單已載入（iframe[{idx}].nested[{nidx}]）")
                        return True
                    driver.switch_to.parent_frame()
                except Exception:
                    try:
                        driver.switch_to.parent_frame()
                    except Exception:
                        pass
        except Exception:
            continue

    # 最後等一次（有時候是慢載入）
    try:
        driver.switch_to.default_content()
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "[id$='COLCTRLWorkSummary']"))
        )
        logger.info("工作日誌表單已載入（default_content）")
        return True
    except TimeoutException:
        return False


def _discover_worklog_prefix(driver) -> Optional[str]:
    """
    從頁面上的欄位 id 解析出 prefix：
      {prefix}_{ctlXX}_COLCTRLWorkSummary
    prefix 可能因表單版本而變動，所以不要硬編。
    """
    els = driver.find_elements(
        By.CSS_SELECTOR,
        "[id$='COLCTRLWorkSummary'], [id$='COLCTRLASAttTime'], [id$='COLCTRLAEAttTime']",
    )
    for el in els:
        el_id = el.get_attribute("id") or ""
        m = _WORKLOG_ID_RE.match(el_id)
        if m:
            return m.group("prefix")
    return None


def _discover_worklog_suffixes(driver, prefix: str) -> list[str]:
    """從工作摘要欄位的 id 蒐集畫面上實際存在的列 suffix（依 DOM 順序）。"""
    suffixes: list[str] = []
    els = driver.find_elements(By.CSS_SELECTOR, "[id$='COLCTRLWorkSummary']")
    for el in els:
        el_id = el.get_attribute("id") or ""
        m = _WORKLOG_ID_RE.match(el_id)
        if not m:
            continue
        if m.group("prefix") != prefix:
            continue
        suf = m.group("suffix")
        if suf not in suffixes:
            suffixes.append(suf)
    return suffixes


def _js_set_select(driver, element_id: str, value: str, text_fallback: Optional[str] = None) -> bool:
    """用 JS 設定 select 的選項值，優先用 value，不行就用文字比對。"""
    try:
        result = driver.execute_script(
            """
            const id = arguments[0];
            const value = arguments[1];
            const text = arguments[2];

            const el = document.getElementById(id);
            if (!el) return { ok: false, reason: 'not_found' };

            const opts = Array.from(el.options || []);
            let target = value;

            if (opts.length) {
              const byValue = opts.find(o => o.value === value);
              if (!byValue && text) {
                const byText = opts.find(o => (o.textContent || '').trim() === text)
                           || opts.find(o => (o.textContent || '').includes(text));
                if (byText) target = byText.value;
              }
            }

            el.value = target;
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));

            return { ok: (el.value === target), value: el.value, target };
            """,
            element_id, value, text_fallback
        )
        return bool(result and result.get("ok"))
    except Exception as e:
        logger.warning(f"JS set select {element_id}={value} 失敗: {e}")
        return False


# ─────────────────────────────────────────────
# 工具函式
# ─────────────────────────────────────────────
def get_work_content(work_log_path: str) -> str:
    """讀取工作日誌檔案，若不存在或為空則用預設值"""
    default = "今日工作內容請補充"
    if not os.path.exists(work_log_path):
        logger.warning(f"找不到工作日誌: {work_log_path}，使用預設值")
        return default
    with open(work_log_path, "r", encoding="utf-8") as f:
        content = f.read().strip()
    if not content:
        logger.warning("工作日誌為空，使用預設值")
        return default
    logger.info(f"讀取工作日誌: {content[:50]}...")
    return content


def send_line_notify(token: str, message: str):
    """發送 Line Notify 通知（選用）"""
    if not token:
        return
    try:
        headers = {"Authorization": f"Bearer {token}"}
        requests.post(
            "https://notify-api.line.me/api/notify",
            headers=headers,
            data={"message": f"\n{message}"},
            timeout=10,
        )
        logger.info("Line Notify 已發送")
    except Exception as e:
        logger.warning(f"Line Notify 失敗: {e}")


def _save_screenshot(driver, name: str):
    """截圖存至 logs/"""
    os.makedirs("logs", exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join("logs", f"{ts}_{name}.png")
    try:
        driver.save_screenshot(path)
        logger.info(f"截圖: {path}")
    except Exception:
        pass
