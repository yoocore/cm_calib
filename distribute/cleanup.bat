@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

:: 全量输出捕获，覆盖旧日志
if not defined TEE_ACTIVE (
    set "TEE_ACTIVE=1"
    set "TEE_LOG_FILE=%~dp0cleanup.log"
    set "TEE_BAT_PATH=%~f0"
    powershell -NoProfile "& '!TEE_BAT_PATH!' 2>&1 | ForEach-Object { $_; $_ | Out-File '!TEE_LOG_FILE!' -Encoding UTF8 }"
    type "!TEE_LOG_FILE!"
    echo.
    echo Log saved to: !TEE_LOG_FILE!
    pause
    exit /b
)

title CM Camera Calibration - Cleanup

set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
set "LOG_FILE=%SCRIPT_DIR%\cleanup.log"

for /f %%a in ('echo prompt $E ^| cmd') do set "ESC=%%a"
set "ERR=%ESC%[31m"
set "OK=%ESC%[32m"
set "INFO=%ESC%[36m"
set "WARN=%ESC%[33m"
set "NC=%ESC%[0m"

echo ========================================
echo   Camera Calibration Tool - Cleanup
echo ========================================
echo.

:: Step 1: Delete virtual environment
set "VENV_DIR=%SCRIPT_DIR%\.venv"
if exist "%VENV_DIR%" (
    rmdir /s /q "%VENV_DIR%" 2>nul
    if !ERRORLEVEL! equ 0 (
        echo %OK%^|%NC% Virtual environment deleted
    ) else (
        echo %ERR%!%NC% Failed to delete virtual environment, please manually delete .venv directory
    )
) else (
    echo %INFO%-%NC% Virtual environment does not exist, skipping
)

:: Step 2: Delete generated launch script
set "RUN_BAT=%SCRIPT_DIR%\run.bat"
if exist "%RUN_BAT%" (
    del "%RUN_BAT%" 2>nul
    if !ERRORLEVEL! equ 0 (
        echo %OK%^|%NC% Launch script run.bat deleted
    )
) else (
    echo %INFO%-%NC% Launch script does not exist, skipping
)

:: Step 3: Delete Python cache
echo %INFO%*%NC% Cleaning Python cache...
for /d /r "%SCRIPT_DIR%" %%d in (__pycache__) do (
    if exist "%%d" rmdir /s /q "%%d" 2>nul
)
echo %OK%^|%NC% Cache cleaned

:: Step 4: Delete .egg-info
for /d /r "%SCRIPT_DIR%" %%d in (*.egg-info) do (
    if exist "%%d" rmdir /s /q "%%d" 2>nul
)

echo.
echo ========================================
echo   %OK%Cleanup Complete%NC%
echo ========================================
echo.
echo Log saved to: %LOG_FILE%
echo To reinstall, run setup.bat.
echo.
pause
exit /b 0
