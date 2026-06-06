@echo off
chcp 65001 >nul 2>&1
title Ebook Translator
cd /d "%~dp0."

if not exist ".venv\Scripts\activate.bat" (
    echo.
    echo [錯誤] 找不到 .venv 虛擬環境
    echo.
    echo 請先執行：
    echo   python -m venv .venv
    echo   .venv\Scripts\activate
    echo   pip install -e ".[dev]"
    echo.
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat

python -c "import ebook_translator" >nul 2>&1
if errorlevel 1 (
    echo.
    echo [資訊] ebook-translator 未安裝，正在自動安裝...
    echo.
    python -m pip install -e ".[dev]"
    if errorlevel 1 (
        echo.
        echo [錯誤] 安裝失敗，請手動執行：pip install -e ".[dev]"
        pause
        exit /b 1
    )
)

:MENU
echo.
echo ============================================
echo   Ebook Translator
echo ============================================
echo   1  start      - 翻譯（讀取 config.yaml）
echo   2  dry-run    - 估算，不呼叫 API
echo   3  inspect    - 檢查書籍結構
echo   4  estimate   - 估算 token 用量
echo   5  validate   - 驗證翻譯結果
echo   0  exit       - 離開
echo ============================================
echo.
set "CHOICE="
set /p CHOICE=請輸入選項（數字或名稱）：

if not defined CHOICE goto MENU

if "%CHOICE%"=="0"        goto DO_EXIT
if "%CHOICE%"=="1"        goto DO_START
if "%CHOICE%"=="2"        goto DO_DRYRUN
if "%CHOICE%"=="3"        goto DO_INSPECT
if "%CHOICE%"=="4"        goto DO_ESTIMATE
if "%CHOICE%"=="5"        goto DO_VALIDATE
if /i "%CHOICE%"=="exit"     goto DO_EXIT
if /i "%CHOICE%"=="start"    goto DO_START
if /i "%CHOICE%"=="dry-run"  goto DO_DRYRUN
if /i "%CHOICE%"=="dryrun"   goto DO_DRYRUN
if /i "%CHOICE%"=="inspect"  goto DO_INSPECT
if /i "%CHOICE%"=="estimate" goto DO_ESTIMATE
if /i "%CHOICE%"=="validate" goto DO_VALIDATE

echo.
echo [未知選項] 請輸入 0-5 或指令名稱。
pause
goto MENU

:DO_START
echo.
python -m ebook_translator start
pause
goto MENU

:DO_DRYRUN
echo.
python -m ebook_translator start --dry-run
pause
goto MENU

:DO_INSPECT
echo.
python -m ebook_translator inspect --config config.yaml
pause
goto MENU

:DO_ESTIMATE
echo.
python -m ebook_translator estimate --config config.yaml
pause
goto MENU

:DO_VALIDATE
echo.
set "BOOK_DIR="
set /p BOOK_DIR=請輸入 outputs 目錄路徑（例如 outputs\my-book）：
if not defined BOOK_DIR (
    echo [取消] 未輸入路徑。
    pause
    goto MENU
)
python -m ebook_translator validate "%BOOK_DIR%" --config config.yaml
pause
goto MENU

:DO_EXIT
exit /b 0
