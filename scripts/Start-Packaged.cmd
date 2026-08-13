@echo off
setlocal
cd /d "%~dp0"
title CHIO Portfolio Assistant - Keep This Window Open

if not exist "config.toml" (
    copy /Y "config.example.toml" "config.toml" >nul
    if not exist "config.toml" (
        echo Could not create config.toml in this folder.
        echo Extract the ZIP to a writable approved folder and try again.
        pause
        exit /b 2
    )
    echo First-time setup is required.
    echo.
    echo Configure the government OneDrive path and approved internal AI endpoint,
    echo save the file, then double-click this launcher again.
    echo.
    start "" notepad.exe "%~dp0config.toml"
    pause
    exit /b 2
)

"%~dp0PortfolioAssistant.exe" --config "%~dp0config.toml" launch
set "CHIO_EXIT=%ERRORLEVEL%"
if not "%CHIO_EXIT%"=="0" (
    echo.
    echo CHIO Portfolio Assistant could not start. Review the error above.
    pause
)
exit /b %CHIO_EXIT%
