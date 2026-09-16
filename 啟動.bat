@echo off
chcp 65001 >nul
echo.
echo ========================================
echo   CGU 自動點名系統
echo ========================================
echo.
echo [1] 開啟圖形控制台（建議）
echo [2] 立即掃描今日班次
echo [3] 測試簽到（立即執行，預設 Row: ctl02）
echo [4] 測試簽退（立即執行，預設 Row: ctl02，含工作日誌）
echo [5] 離開
echo.
set /p choice="請輸入選項 (1-5): "

if "%choice%"=="1" (
    start "" pythonw attendance_ui.py
    exit
)
if "%choice%"=="2" (
    echo 立即掃描今日班次並排程...
    python main.py --scan
)
if "%choice%"=="3" (
    echo 執行測試簽到 ^(ctl02^)...
    python main.py --test-signin ctl02
)
if "%choice%"=="4" (
    echo 執行測試簽退 ^(ctl02^)...
    python main.py --test-signout ctl02
)
if "%choice%"=="5" exit

pause
