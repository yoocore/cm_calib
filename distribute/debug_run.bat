@echo off
chcp 65001 >nul
title CameraCalibration Debug

echo ============================================
echo  CameraCalibration Debug Mode
echo ============================================
echo.

set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

echo [1] Starting CameraCalibration.exe...
echo     Path: %SCRIPT_DIR%\dist_exe\CameraCalibration\CameraCalibration.exe
echo.

cd "%SCRIPT_DIR%\dist_exe\CameraCalibration"
CameraCalibration.exe

echo.
echo [2] Exit code: %ERRORLEVEL%
echo.
pause
