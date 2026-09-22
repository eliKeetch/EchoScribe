@echo off
setlocal
set "SCRIPT_DIR=%~dp0"

if exist "%SCRIPT_DIR%.venv\Scripts\python.exe" (
    "%SCRIPT_DIR%.venv\Scripts\python.exe" "%SCRIPT_DIR%transcribe.py" %*
) else (
    py -3.12 "%SCRIPT_DIR%transcribe.py" %*
)

exit /b %ERRORLEVEL%
