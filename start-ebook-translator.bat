@echo off
chcp 65001 >nul 2>&1
title Ebook Translator 啟動器

:: ============================================
:: Ebook Translator 啟動器
:: ============================================

:: 切換到專案根目錄
cd /d "%~dp0"

:: 檢查 .venv 是否存在
if not exist ".venv\Scripts\activate.bat" (
    echo.
    echo [錯誤] 找不到 .venv 虛擬環境
    echo.
    echo 請先執行以下指令建立虛擬環境：
    echo   python -m venv .venv
    echo   .venv\Scripts\activate
    echo   pip install -e ".[dev]"
    echo.
    pause
    exit /b 1
)

:: 啟用虛擬環境
call .venv\Scripts\activate.bat

:: 檢查 ebook-translator 指令是否可用
where ebook-translator >nul 2>&1
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
cls
echo.
echo ============================================================
echo   Ebook Translator 啟動器
echo ============================================================
echo.
echo   [1] 查看說明
echo   [2] 掃描 books 並估算（不呼叫 API）
echo   [3] Spy.epub 小量翻譯（10 段）
echo   [4] Spy.epub 全量翻譯
echo   [5] 驗證 Spy 輸出
echo   [6] 開啟 Spy 翻譯結果 HTML
echo   [7] 開啟輸出資料夾
echo   [8] 離開
echo.
echo ============================================================

:: 檢查 config.yaml 是否存在
if not exist "config.yaml" (
    echo.
    echo [警告] 找不到 config.yaml
    echo 請先複製範例設定檔：
    echo   copy config.example.yaml config.yaml
    echo.
)

:: 檢查 API key 是否設定
if "%TRANSLATION_API_KEY%"=="" (
    echo.
    echo [提示] TRANSLATION_API_KEY 環境變數未設定
    echo 翻譯功能（選項 3、4）需要 API key
    echo 可透過環境變數或 .env 檔設定
    echo.
)

echo.
set /p choice=請選擇操作 [1-8]:

if "%choice%"=="1" goto HELP
if "%choice%"=="2" goto DRY_RUN
if "%choice%"=="3" goto TRANSLATE_SMALL
if "%choice%"=="4" goto TRANSLATE_FULL
if "%choice%"=="5" goto VALIDATE
if "%choice%"=="6" goto OPEN_HTML
if "%choice%"=="7" goto OPEN_OUTPUT
if "%choice%"=="8" goto EXIT

echo.
echo [錯誤] 無效的選項，請輸入 1-8
pause
goto MENU

:: ============================================
:: 功能選項
:: ============================================

:HELP
cls
echo.
ebook-translator --help
echo.
pause
goto MENU

:DRY_RUN
cls
echo.
echo [執行] 掃描 books 並估算...
echo.
ebook-translator start --dry-run
echo.
pause
goto MENU

:TRANSLATE_SMALL
cls
echo.
echo [執行] Spy.epub 小量翻譯（10 段）...
echo.
ebook-translator start --book books\Spy.epub --limit 10 --max-segments 2500 --yes
echo.
pause
goto MENU

:TRANSLATE_FULL
cls
echo.
echo [執行] Spy.epub 全量翻譯...
echo.
ebook-translator start --book books\Spy.epub --max-segments 2500 --yes
echo.
pause
goto MENU

:VALIDATE
cls
echo.
echo [執行] 驗證 Spy 輸出...
echo.
ebook-translator validate outputs\spy-the-lie-how-to-spot-deception-the-cia-way --config config.yaml
ebook-translator report-missing outputs\spy-the-lie-how-to-spot-deception-the-cia-way --config config.yaml
ebook-translator export outputs\spy-the-lie-how-to-spot-deception-the-cia-way --config config.yaml
echo.
pause
goto MENU

:OPEN_HTML
cls
echo.
echo [執行] 開啟 Spy 翻譯結果 HTML...
start "" "outputs\spy-the-lie-how-to-spot-deception-the-cia-way\bilingual.html"
goto MENU

:OPEN_OUTPUT
cls
echo.
echo [執行] 開啟輸出資料夾...
explorer outputs
goto MENU

:EXIT
echo.
echo 感謝使用 Ebook Translator！
exit /b 0
