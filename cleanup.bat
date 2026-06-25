@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

title CM Camera Calibration - 清理

set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

for /f %%a in ('echo prompt $E ^| cmd') do set "ESC=%%a"
set "ERR=%ESC%[31m"
set "OK=%ESC%[32m"
set "INFO=%ESC%[36m"
set "WARN=%ESC%[33m"
set "NC=%ESC%[0m"

echo ========================================
echo   Camera Calibration Tool - 清理
echo ========================================
echo.

:: Step 1: 删除虚拟环境
set "VENV_DIR=%SCRIPT_DIR%\.venv"
if exist "%VENV_DIR%" (
    rmdir /s /q "%VENV_DIR%" 2>nul
    if !ERRORLEVEL! equ 0 (
        echo %OK%^|%NC% 虚拟环境已删除
    ) else (
        echo %ERR%!%NC% 虚拟环境删除失败，请手动删除 .venv 目录
    )
) else (
    echo %INFO%-%NC% 虚拟环境不存在，跳过
)

:: Step 2: 删除生成的启动脚本
set "RUN_BAT=%SCRIPT_DIR%\run.bat"
if exist "%RUN_BAT%" (
    del "%RUN_BAT%" 2>nul
    if !ERRORLEVEL! equ 0 (
        echo %OK%^|%NC% 启动脚本 run.bat 已删除
    )
) else (
    echo %INFO%-%NC% 启动脚本不存在，跳过
)

:: Step 3: 删除 Python 缓存
echo %INFO%*%NC% 清理 Python 缓存...
for /d /r "%SCRIPT_DIR%" %%d in (__pycache__) do (
    if exist "%%d" rmdir /s /q "%%d" 2>nul
)
echo %OK%^|%NC% 缓存已清理

:: Step 4: 删除 .egg-info
for /d /r "%SCRIPT_DIR%" %%d in (*.egg-info) do (
    if exist "%%d" rmdir /s /q "%%d" 2>nul
)

echo.
echo ========================================
echo   %OK%清理完成%NC%
echo ========================================
echo.
echo 如需重新安装，运行 setup.bat 即可。
echo.
pause
exit /b 0
