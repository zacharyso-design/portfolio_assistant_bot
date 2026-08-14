@echo off
setlocal
cd /d "%~dp0"
title CHIO Portfolio Assistant - Keep This Window Open

where py >nul 2>nul
if errorlevel 1 goto try_python
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 goto try_python
py -3 "%~dp0portfolio_assistant_launcher.py"
exit /b %errorlevel%

:try_python
where python >nul 2>nul
if errorlevel 1 goto no_python
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>nul
if errorlevel 1 goto no_python
python "%~dp0portfolio_assistant_launcher.py"
exit /b %errorlevel%

:no_python
echo Python 3.11 or later is required but was not found.
echo Ask your administrator to install Python 3.11+, then double-click this file again.
pause
exit /b 2
