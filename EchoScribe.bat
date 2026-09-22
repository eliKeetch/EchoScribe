@echo off
setlocal
set "SCRIPT_DIR=%~dp0"

if exist "%SCRIPT_DIR%.venv\Scripts\pythonw.exe" (
    "%SCRIPT_DIR%.venv\Scripts\python.exe" -c "import PySide6; import faster_whisper" >nul 2>&1
    if errorlevel 1 (
        echo EchoScribe dependencies are not installed.
        echo.
        echo Run this command from the EchoScribe folder:
        echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
        echo.
        pause
        exit /b 1
    )
    start "EchoScribe" "%SCRIPT_DIR%.venv\Scripts\pythonw.exe" "%SCRIPT_DIR%gui.py"
) else (
    echo EchoScribe is not installed yet.
    echo.
    echo Run these commands from the EchoScribe folder first:
    echo   py -3.12 -m venv .venv
    echo   .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

exit /b 0
