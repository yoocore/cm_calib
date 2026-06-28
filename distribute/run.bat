@echo off
chcp 65001 >nul
title Camera Calibration Tool

set "ROOT_DIR=%~dp0"
set "ROOT_DIR=%ROOT_DIR:~0,-1%"

call "%ROOT_DIR%\.venv\Scripts\activate.bat"
if %ERRORLEVEL% neq 0 (
    echo [错误] 无法激活虚拟环境，请重新运行 setup.bat
    pause
    exit /b 1
)

rem Add CarMaker Python path for cmapi/apoc
set "PYTHONPATH=D:\IPG\carmaker\win64-14.1\Python\python3.9;%PYTHONPATH%"

python "%ROOT_DIR%\src\entry\launch_gui.py" %*
if %ERRORLEVEL% neq 0 (
    echo [错误] 程序异常退出，代码: %ERRORLEVEL%
    pause
)
