# CGU Auto Attendance

長庚大學線上核簽系統的自動簽到 / 簽退工具。  
目前版本採用「固定主排程 + 當日臨時任務」：

- 固定主排程：每週一、二、五 `08:30` 啟動一次 `python main.py --scan`
- `--scan`：讀取今天班表後，建立當日的臨時簽到 / 簽退任務，然後立即退出
- 臨時任務：在各班次時間前 10 分鐘啟動，到點才真正執行，不會提早
- 最後一班簽退成功後：自動開工作日誌、填內容並送出
- 請假日：`08:30` 掃描後立即處理，不等班次時間

## 專案結構

```text
auto-attendance/
├── main.py                # 掃描器、臨時任務 worker、CLI 入口
├── attendance.py          # Selenium 操作與工作日誌填寫邏輯
├── config.yaml            # 帳號、排程、請假設定
├── work_log.txt           # 每日工作內容
├── requirements.txt       # Python 套件
├── run_scan.bat           # 固定主排程啟動用
├── run_job.bat            # 臨時簽到 / 簽退 / 請假任務啟動用
├── install_schedule.ps1   # 建立 Windows 主排程
├── 啟動.bat                # 手動啟動選單
└── logs/                  # 執行 log、摘要 log、截圖
```

## 首次安裝

1. 安裝 Python 套件

```bash
pip install -r requirements.txt
```

2. 編輯 `config.yaml`

- `login.username` / `login.password`：你的 CGU 帳號密碼
- `browser.headless`：
  - `false`：看得到瀏覽器，方便確認
  - `true`：背景執行
- `schedule.task_retry_once_sec`：臨時任務失敗後幾秒重試一次
- `work_log_path`：通常保留 `./work_log.txt`

3. 安裝 Windows 主排程

```powershell
powershell -ExecutionPolicy Bypass -File .\install_schedule.ps1 -StartTime 08:30 -Days MON,TUE,FRI
```

4. 驗證排程

```powershell
schtasks /Query /TN CGU_AutoAttendance_Scan /V /FO LIST
```

你應該看到：

- `狀態: 就緒`
- `排程工作狀態: 已啟用`
- `天: MON, TUE, FRI`
- `開始時間: 上午 08:30:00`

## 每日使用流程

### 你每天需要做的事

1. 保持電腦開機、登入 Windows、可連網
2. 在最後一班簽退前，更新 `work_log.txt`
3. 如果當天請假，先在 `config.yaml` 寫好請假日期和第幾班

### 系統每天會自動做的事

1. 週一、週二、週五 `08:30` 啟動 `run_scan.bat`
2. `run_scan.bat` 會執行 `python main.py --scan`
3. `--scan` 會：
   - 清掉舊的臨時任務
   - 登入網站讀今天班表
   - 判斷今天是否有請假設定
   - 為正常班次建立臨時簽到 / 簽退任務
   - 做完後自己退出，不會整天掛著
4. 臨時任務到各自時間前 10 分鐘會啟動
5. 真正執行前，worker 會等到精準時間才按按鈕，所以不會提早
6. 最後一班簽退成功後，會自動填工作日誌並送出
7. 當天任務完成後，會刪掉當日臨時任務，只保留固定主排程

## 請假設定

請假設定寫在 `config.yaml` 的 `leave` 區塊。

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

### 設定規則

- `leave_dates`：哪些日期要套用請假流程
- `leave_shift_indexes_by_date`：該日期請第幾班
- 班次索引從 `1` 開始
  - `[1]` = 第 1 班
  - `[1, 2]` = 第 1 班和第 2 班
- 「第幾班」的順序，以當天網站掃描到的班表順序為準
- 如果日期命中了 `leave_dates`，但沒有填 `leave_shift_indexes_by_date`，程式只會寫 warning，不會自動請假

### 請假日系統會怎麼做

在 `08:30` 掃描後，系統會立即處理請假班次：

1. 先對指定班次立刻嘗試簽到 + 簽退
2. 打開工作日誌
3. 指定列會：
   - 保留預計 / 實際出勤時間
   - `工作摘要` 清空
   - `未簽到/未簽退原因說明、出勤時間` 填 `請假(未出勤)`
   - 勾選 `未出勤`
4. 自動按 `傳送`

## 手動指令

### 立即掃描今天班表並建立臨時任務

```bash
python main.py --scan
```

### 手動執行某一列簽到

```bash
python main.py --test-signin ctl02
```

### 手動執行某一列簽退

```bash
python main.py --test-signout ctl02
```

### 只測工作日誌填寫，不送出

```bash
python main.py --test-worklog
```

### 直接執行 worker（除錯用）

```bash
python main.py --run-signin-job ctl02 2026-03-31T09:00:00+08:00
python main.py --run-signout-job ctl02 2026-03-31T17:00:00+08:00 true
python main.py --run-leave-job 2026-03-31
```

## Log 與排查

### Log 位置

- 詳細執行 log：`logs/attendance_YYYYMMDD.log`
- 每日摘要 log：`logs/daily_summary_YYYYMMDD.log`
- 失敗截圖：`logs/*.png`

### 常見檢查點

1. 排程有沒有真的存在

```powershell
schtasks /Query /TN CGU_AutoAttendance_Scan /V /FO LIST
```

2. 當日臨時任務有沒有建立

```powershell
schtasks /Query /FO LIST /V | findstr CGU_AA_TMP_
```

3. 如果網站改版

- 優先看 `attendance.py` 的選單點擊、班表解析、工作日誌欄位定位
- 對照 `logs/` 裡的截圖確認失敗位置

4. 如果瀏覽器啟不起來

- 關閉所有 Chrome 後再試
- 確認 `chrome_profile/` 沒被其他程式占用

## 補充說明

- 目前登入主要依賴本機 Chrome session / cookies
- `config.yaml` 內是明碼帳密，功能上可用，但安全性上建議之後改成環境變數或本機 secrets 檔
- 若你只想手動控制，也可以直接跑 `python main.py --scan`，不一定要等 Windows 主排程
