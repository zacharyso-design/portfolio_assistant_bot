@echo off
setlocal
cd /d "%~dp0"
title CHIO Portfolio Assistant - Keep This Window Open

if not exist "%~dp0PortfolioAssistant.exe" (
    echo This application folder is incomplete: PortfolioAssistant.exe is missing.
    echo Extract the entire ZIP into one writable folder, then try again.
    pause
    exit /b 2
)

"%~dp0PortfolioAssistant.exe"
exit /b %ERRORLEVEL%
