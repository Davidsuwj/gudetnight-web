@echo off
chcp 65001 >nul
setlocal

REM ============================================================
REM  股得Night 專用 CDP Chrome 啟動器
REM  - 連線埠：9222（worker.py / config.py 會連這個）
REM  - 使用「專用持久設定檔」，登入一次即永久保存
REM  - 與你日常使用的 Chrome 互不干擾，可同時開
REM ============================================================

set "CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe"
if not exist "%CHROME%" set "CHROME=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"

REM 專用設定檔資料夾（登入資訊長期保存在這裡）
set "CDP_PROFILE=%USERPROFILE%\chrome-cdp-profile"

echo.
echo === 股得Night CDP Chrome ===
echo Chrome  : %CHROME%
echo Profile : %CDP_PROFILE%
echo Port    : 9222
echo.

if not exist "%CHROME%" (
    echo [錯誤] 找不到 Chrome，請確認安裝路徑後修改本檔的 CHROME 變數。
    pause
    exit /b 1
)

REM 啟動（不殺掉你日常的 Chrome，因為用的是獨立設定檔）
start "" "%CHROME%" ^
    --remote-debugging-port=9222 ^
    --user-data-dir="%CDP_PROFILE%" ^
    --no-first-run ^
    --no-default-browser-check ^
    --restore-last-session

echo.
echo [OK] 已啟動 CDP Chrome。
echo.
echo 若是第一次使用（或還沒登入），請在剛開啟的這個 Chrome 視窗登入：
echo    1. https://chatgpt.com
echo    2. https://voai.ai
echo    3. https://studio.youtube.com   ^(使用你要發布影片的頻道帳號^)
echo.
echo 登入完成後就會永久保存，之後每次只要執行本檔，
echo 專案背後跑的就是這個「已登入」的 Chrome。
echo.
pause
