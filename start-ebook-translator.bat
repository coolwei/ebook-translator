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

cmd /k python -m ebook_translator --help