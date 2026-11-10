@echo off
REM ASCII-only on purpose: mixing Chinese text with chcp can break parsing.
chcp 65001 >nul
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel%==0 (
    python mic_wake_monitor.py %*
    goto :done
)

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 mic_wake_monitor.py %*
    goto :done
)

echo.
echo [ERROR] Python not found. Please install Python 3.7+ from:
echo         https://www.python.org/downloads/
echo         Remember to check "Add Python to PATH" during setup.
echo.

:done
echo.
pause
