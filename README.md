# CGU Auto Attendance

## What This Project Does

This project automates CGU attendance for student work shifts.

Daily flow:
1. A fixed Windows task starts `python main.py --scan` at 08:30 on Monday, Tuesday, and Friday.
2. `--scan` reads today's shifts from BPM.
3. For normal work days, it creates one-off Windows tasks for each sign-in and sign-out job.
4. Each one-off task starts 10 minutes early, waits until the exact target time, then performs the action.
5. After the last shift signs out successfully, the program fills and submits the work log.
6. If the date is configured as a holiday, the program does nothing for that day.
7. If the date is configured as leave, the program runs the leave workflow instead of normal attendance for the selected shift indexes.

## Files

- `main.py`: scan logic, worker entry points, Windows task creation, retry, cleanup
- `attendance.py`: Selenium logic for BPM, sign-in/sign-out, work log, leave form filling
- `config.yaml`: your real local config, account, password, leave / holiday settings
- `config.example.yaml`: safe config template for GitHub
- `work_log.txt`: daily work summary content
- `install_schedule.ps1`: creates the fixed Windows master schedule
- `run_scan.bat`: local helper to run `python main.py --scan`
- `run_job.bat`: local helper for manual worker testing
- `logs/`: attendance logs, summary logs, screenshots

## First-Time Setup

1. Install dependencies

```bash
pip install -r requirements.txt
```

2. Edit `config.yaml`

Required fields:
- `login.username`
- `login.password`
- `browser.headless`
- `work_log_path`

3. Install the fixed Windows master schedule

```powershell
powershell -ExecutionPolicy Bypass -File .\install_schedule.ps1 -StartTime 08:30 -Days MON,TUE,FRI
```

4. Verify the master schedule

```powershell
schtasks /Query /TN CGU_AutoAttendance_Scan /V /FO LIST
```

## Daily Usage

What you need to do:
1. Keep the PC powered on and logged in.
2. Update `work_log.txt` before the last shift finishes.
3. If today is leave or holiday, update `config.yaml` before 08:30.

What the system does automatically:
1. At 08:30, run `python main.py --scan`.
2. Clean old temporary tasks.
3. If today is a holiday, stop immediately and do nothing else.
4. If today is a leave date, run the leave workflow for the configured shift indexes.
5. Otherwise, create one-off sign-in / sign-out tasks for today's shifts.
6. Run each task at the exact attendance time.
7. After the last successful sign-out, fill and submit the work log.
8. Remove same-day temporary tasks.

## Leave vs Holiday

### Holiday

Use `holiday` for public holidays or long weekends.

Behavior:
- read config at 08:30
- if today is in `holiday.holiday_dates`, skip everything
- no sign-in
- no sign-out
- no leave workflow
- no work log submission

Example:

```yaml
holiday:
  holiday_dates:
    - "2026-04-03"
    - "2026-04-06"
```

### Leave

Use `leave` when you need to submit the leave-style work log for selected shifts.

Behavior:
- run at 08:30
- perform leave actions only for the configured shift indexes
- clear work summary
- fill leave reason
- check absent checkbox
- submit the form automatically

Example:

```yaml
leave:
  leave_dates:
    - "2026-03-31"
  leave_shift_indexes_by_date:
    "2026-03-31": [1, 2]
  reason_text: "請假(未出勤)"
  auto_submit: true
  click_retry_once_sec: 30
```

Rules:
- shift indexes are 1-based
- `1` means first shift of the day
- `2` means second shift of the day
- if the date exists in `leave_dates` but has no shift indexes, the system logs a warning and does nothing

## Manual Commands

Scan today and create temporary tasks:

```bash
python main.py --scan
```

Manual sign-in test:

```bash
python main.py --test-signin ctl02
```

Manual sign-out test:

```bash
python main.py --test-signout ctl02
```

Manual work-log test:

```bash
python main.py --test-worklog
```

Direct worker test:

```bash
python main.py --run-signin-job ctl02 2026-03-31T09:00:00+08:00
python main.py --run-signout-job ctl02 2026-03-31T17:00:00+08:00 true
python main.py --run-leave-job 2026-03-31
```

## Logs and Debugging

Main log:
- `logs/attendance_YYYYMMDD.log`

Daily summary:
- `logs/daily_summary_YYYYMMDD.log`

Screenshots:
- `logs/*.png`

Useful checks:

Check the master schedule:

```powershell
schtasks /Query /TN CGU_AutoAttendance_Scan /V /FO LIST
```

Check temporary tasks:

```powershell
schtasks /Query /FO LIST /V | findstr CGU_AA_TMP_
```

## Stability Notes

- Temporary worker tasks now run through hidden PowerShell instead of a visible console window, which reduces the chance of accidental task interruption.
- The program now writes more detailed logs around process start, process exit, waiting, browser launch, navigation, sign-in/sign-out clicks, and work-log entry.
- If a worker still fails, check the task's `Last Result` and compare it with the final lines in `logs/attendance_YYYYMMDD.log`.

## 圖形控制台（新版建議操作方式）

### 開啟方式

直接雙擊：

```text
open_ui.bat
```

也可以執行：

```powershell
python attendance_ui.py
```

### 每學期設定流程

1. 開啟「每週班表」頁籤。
2. 勾選本學期有上班的星期。
3. 填入每一天的第 1 班、第 2 班開始與結束時間；沒有第二班就留空。
4. 設定每日掃描時間，建議比最早班次早 30 分鐘，例如 `08:30`。
5. 勾選「啟用自動點名」。
6. 按「儲存、測試並套用」。
7. UI 會先檢查時間格式、時間重疊、請假時段，再建立並查詢 Windows 主排程。

### 指定某日某時段請假

1. 先完成每週班表。
2. 開啟「指定請假」頁籤。
3. 輸入日期，例如 `2026-09-21`。
4. 從下拉選單選擇當天要請假的班次。
5. 按「加入請假」。
6. 按「儲存、測試並套用」。

請假改用「日期 + 實際時段」儲存，不再需要判斷 `ctl02` 或第幾班。

### 暫停與恢復

- 暫停：取消勾選「啟用自動點名」，再按「儲存、測試並套用」。
- 恢復：勾選「啟用自動點名」，確認班表後再儲存。

停用後有雙重保護：Windows 主排程會被停用，即使手動執行 `main.py --scan`，程式也會因總開關關閉而立即退出。

### 班表安全檢查

每日掃描網站後，程式會比較：

- UI 設定的當日班次
- 網站實際讀到的班次

只要班數、開始時間或結束時間不一致，就不建立任何簽到／簽退任務，並在 log 記錄差異，避免換學期後誤用舊班表。

### 在 UI 編輯工作內容

1. 開啟「工作內容」頁籤。
2. 輸入最後一班簽退後要送出的工作摘要。
3. 按「儲存工作內容」，或按主畫面的「儲存、測試並套用」。

內容會儲存在 `work_log.txt`。若沒有修改，就會每天持續使用相同內容；修改並儲存後，下一次工作日誌會使用新內容。一般工作日仍會在最後一班簽退成功後自動填入並傳送；請假日不會使用這段工作摘要。
