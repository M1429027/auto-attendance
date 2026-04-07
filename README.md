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
