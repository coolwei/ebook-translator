@echo off
chcp 65001 >nul 2>&1
title Ebook Translator 啟動器

cd /d "%~dp0"

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

call .venv\Scripts\activate.bat

python --version >nul 2>&1
if errorlevel 1 (
    echo [錯誤] 虛擬環境中的 Python 無法執行
    pause
    exit /b 1
)

python -c "import ebook_translator" >nul 2>&1
if errorlevel 1 (
    echo.
    echo [資訊] ebook-translator 未安裝，正在自動安裝...
    echo.
    python -m pip install -e ".[dev]"
    if errorlevel 1 (
        echo [錯誤] 安裝失敗。請手動執行：pip install -e ".[dev]"
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
echo   [5] 驗證並匯出 Spy 輸出
echo   [6] 開啟 Spy 翻譯結果 HTML
echo   [7] 開啟輸出資料夾
echo   [0] 離開
echo.
echo ============================================================

if not exist "config.yaml" (
    echo.
    echo [警告] 找不到 config.yaml。請執行：copy config.example.yaml config.yaml
    echo.
)

if "%TRANSLATION_API_KEY%"=="" (
    echo.
    echo [提示] TRANSLATION_API_KEY 未設定 - 翻譯功能（選項 3、4）需要此金鑰
    echo.
)

echo.
set choice=
set /p choice=請選擇操作 [0-7]: 

if "%choice%"=="1" goto HELP
if "%choice%"=="2" goto DRY_RUN
if "%choice%"=="3" goto TRANSLATE_SMALL
if "%choice%"=="4" goto TRANSLATE_FULL
if "%choice%"=="5" goto VALIDATE
if "%choice%"=="6" goto OPEN_HTML
if "%choice%"=="7" goto OPEN_OUTPUT
if "%choice%"=="0" goto EXIT

echo.
echo [錯誤] 無效的選項，請輸入 0-7。
pause
goto MENU

:HELP
cls
echo.
python -m ebook_translator --help
echo.
pause
goto MENU

:DRY_RUN
cls
echo.
echo [執行] 掃描 books 並估算...
echo.
python -m ebook_translator start --dry-run
echo.
pause
goto MENU

:TRANSLATE_SMALL
cls
echo.
echo [執行] Spy.epub 小量翻譯（10 段）...
echo.
python -m ebook_translator start --book "books\Spy.epub" --limit 10 --max-segments 2500 --yes
echo.
pause
goto MENU

:TRANSLATE_FULL
cls
echo.
echo [執行] Spy.epub 全量翻譯...
echo.
python -m ebook_translator start --book "books\Spy.epub" --max-segments 2500 --yes
echo.
pause
goto MENU

:VALIDATE
cls
echo.
echo [執行] 驗證並匯出 Spy 輸出...
echo.
set SPY=outputs\spy-the-lie-how-to-spot-deception-the-cia-way
python -m ebook_translator validate "%SPY%"
python -m ebook_translator report-missing "%SPY%" --config config.yaml
python -m ebook_translator export "%SPY%" --config config.yaml
echo.
pause
goto MENU

:OPEN_HTML
cls
echo.
set SPY_HTML=outputs\spy-the-lie-how-to-spot-deception-the-cia-way\bilingual.html
if exist "%SPY_HTML%" (
    start "" "%SPY_HTML%"
) else (
    echo [警告] 找不到檔案: %SPY_HTML%
    pause
)
goto MENU

:OPEN_OUTPUT
cls
echo.
if exist "outputs" (
    explorer "outputs"
) else (
    echo [警告] outputs/ 資料夾尚不存在
    pause
)
goto MENU

:EXIT
echo.
echo 感謝使用 Ebook Translator！
exit /b 0
