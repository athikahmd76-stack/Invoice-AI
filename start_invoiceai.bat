@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul
title InvoiceAI - Local Invoice Extractor

echo ============================================
echo   InvoiceAI - AI-powered invoice extractor
echo   100%% LOCAL - NO CLOUD AI
echo ============================================
echo.

REM -------------------------------------------------- root dir of this script
cd /d "%~dp0"

REM -------------------------------------------------- 1. create required dirs
if not exist "data" mkdir "data"
if not exist "uploads" mkdir "uploads"
if not exist "exports" mkdir "exports"
if not exist "logs" mkdir "logs"
if not exist "models" mkdir "models"

REM -------------------------------------------------- 2. virtual environment
if not exist ".venv\Scripts\python.exe" (
    echo [1/5] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo.
        echo ERROR: Could not create virtual environment.
        echo Install Python 3.10-3.12 from https://www.python.org/downloads/
        echo and make sure "python" is on your PATH.
        goto :fail
    )
    echo       Installing dependencies - first run, takes a few minutes...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo ERROR: pip install failed. Check requirements.txt and your internet connection.
        goto :fail
    )
    echo       Virtual environment ready.
) else (
    echo [1/5] Virtual environment found.
)

REM -------------------------------------------------- 3. verify Ollama
echo [2/5] Checking Ollama...
where ollama >nul 2>nul
if errorlevel 1 (
    echo   ERROR: Ollama CLI not found on PATH.
    echo   Install Ollama from https://ollama.com/download and restart this script.
    goto :fail
)
echo   Ollama detected.

ollama list >nul 2>nul
if errorlevel 1 (
    echo   ERROR: Ollama service is not running.
    echo   Open the Ollama app - system tray - and start it, then run this script again.
    goto :fail
)
echo   Ollama service running.

REM -------------------------------------------------- 4. verify model
echo [3/5] Checking AI model...
set "MODEL=qwen3-vl:8b"
if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
        if /i "%%A"=="OLLAMA_MODEL" set "MODEL=%%B"
    )
)
ollama list | findstr /c:"%MODEL%" >nul
if errorlevel 1 (
    echo   Model "%MODEL%" not installed. Downloading - may take several minutes...
    ollama pull "%MODEL%"
    if errorlevel 1 (
        echo.
        echo   ERROR: model download failed. Try manually:  ollama pull %MODEL%
        goto :fail
    )
)
echo   Model "%MODEL%" available.

REM -------------------------------------------------- 5. start streamlit
echo [4/5] Starting Streamlit...
set "STREAMLIT_BROWSER_GATHER_USAGE_STATS=false"

start "" http://localhost:8501

".venv\Scripts\python.exe" -c "import streamlit" >nul 2>nul
if errorlevel 1 (
    echo   Installing missing dependencies...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

type nul > logs\startup.log 2>nul
".venv\Scripts\python.exe" -m streamlit run app.py --server.port 8501 --server.headless true >> logs\startup.log 2>&1

goto :end

:fail
echo.
echo ============================================
echo   InvoiceAI could not start. See messages above.
echo   Troubleshooting: open README.md
echo ============================================
pause
exit /b 1

:end
exit /b 0