@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

title CM Camera Calibration - 环境搭建

set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

set "VENV_DIR=%SCRIPT_DIR%\.venv"

:: 获取 ESC 字符用于 ANSI 颜色输出
for /f %%a in ('echo prompt $E ^| cmd') do set "ESC=%%a"
set "ERR=%ESC%[31m"
set "OK=%ESC%[32m"
set "INFO=%ESC%[36m"
set "WARN=%ESC%[33m"
set "NC=%ESC%[0m"

echo ========================================
echo   Camera Calibration Tool - 环境搭建
echo ========================================
echo.

:: ========================================
:: Step 1: 查找 CarMaker Python
:: ========================================
echo %INFO%*%NC% 正在检测 CarMaker 安装位置...
echo.

set "CM_PYTHON="

:: 1.1 尝试环境变量
if not "%CARMAKER_DIR%"=="" (
    if exist "%CARMAKER_DIR%\*" (
        echo %OK%+%NC% 从环境变量 CARMAKER_DIR 找到: %CARMAKER_DIR%
        for /f "delims=" %%d in ('dir "%CARMAKER_DIR%\win64-*" /b /o-n 2^>nul') do (
            call :find_python "%CARMAKER_DIR%\%%d"
            if defined CM_PYTHON goto :found_python
        )
    )
)

:: 1.2 扫描常见安装路径
set "SEARCH_ROOTS=C:\IPG\carmaker D:\IPG\carmaker C:\IPG D:\IPG C:\CarMaker D:\CarMaker C:\Program Files\IPG\carmaker D:\Program Files\IPG\carmaker C:\Program Files\CarMaker D:\Program Files\CarMaker"

for %%r in (%SEARCH_ROOTS%) do (
    if exist "%%r\*" (
        for /f "delims=" %%d in ('dir "%%r\win64-*" /b /o-n 2^>nul') do (
            call :find_python "%%r\%%d"
            if defined CM_PYTHON goto :found_python
        )
    )
)

:: 1.3 尝试注册表
for /f "skip=2 tokens=2,*" %%a in ('reg query "HKLM\SOFTWARE\IPG\CarMaker" /s /v InstallPath 2^>nul') do (
    if exist "%%b\*" (
        for /f "delims=" %%d in ('dir "%%b\win64-*" /b /o-n 2^>nul') do (
            call :find_python "%%b\%%d"
            if defined CM_PYTHON goto :found_python
        )
    )
)

:: 1.4 尝试系统 Python（兜底）
set "CM_PYTHON="
where py >nul 2>nul
if !ERRORLEVEL! equ 0 (
    for %%v in (3.11 3.10 3.12 3.9 3) do (
        for /f "delims=" %%p in ('py -%%v -c "import sys; print(sys.executable)" 2^>nul') do (
            "%%p" -c "import venv; print('ok')" >nul 2>&1
            if !ERRORLEVEL! equ 0 (
                set "CM_PYTHON=%%p"
                goto :found_system_python
            )
        )
    )
)
where python >nul 2>nul
if !ERRORLEVEL! equ 0 (
    for /f "delims=" %%p in ('where python') do (
        "%%p" -c "import venv; print('ok')" >nul 2>&1
        if !ERRORLEVEL! equ 0 (
            set "CM_PYTHON=%%p"
            goto :found_system_python
        )
    )
)

:found_system_python
if not "%CM_PYTHON%"=="" (
    echo %WARN%~%NC% 未找到 CarMaker 安装，使用系统 Python（可能需要手动配置 CMAPI 路径）
    echo.
    goto :found_python
)

:: 1.5 未找到可用 Python
echo %ERR%!%NC% 未找到可用的 Python 解释器
echo.
if defined _FOUND_CM_NO_PY (
    for %%i in ("%_FOUND_CM_NO_PY%") do echo   CarMaker %%~nxi 已找到，但无 Python 解释器
    echo   ^(需系统安装 Python 3.9+^)
    echo.
) else (
    echo   CarMaker 未安装或不在标准路径中
    echo.
)
echo 安装 Python 3.10/3.11: https://www.python.org/downloads/
echo ^(勾选 "Add Python to PATH"^)
echo.
pause
exit /b 1


:find_python
set "CM_INSTALL=%~1"
if not exist "%CM_INSTALL%" exit /b 0
:: Record that CM was found, in case it has no Python
for /f "delims=" %%v in ('dir "%CM_INSTALL%" /b /ad /o-n 2^>nul') do if not defined CM_PYTHON (
    if "%%v"=="Python" set "_FOUND_CM_NO_PY=%CM_INSTALL%"
)
:check_next_python

:: 查找版本化目录（python3.10、python310 ...）
if exist "%CM_INSTALL%\Python\" (
    for /f "delims=" %%p in ('dir "%CM_INSTALL%\Python\python*" /b /ad 2^>nul') do (
        if exist "%CM_INSTALL%\Python\%%p\python.exe" (
            set "CM_PY=%CM_INSTALL%\Python\%%p\python.exe"
            call :verify_cmapi
            if defined CM_PYTHON exit /b 0
        )
    )
    :: 尝试 Python 根目录
    if not defined CM_PYTHON (
        if exist "%CM_INSTALL%\Python\python.exe" (
            set "CM_PY=%CM_INSTALL%\Python\python.exe"
            call :verify_cmapi
        )
    )
)
exit /b 0

:verify_cmapi
set "PY_EXE=!CM_PY!"
if "!PY_EXE!"=="" exit /b 0
"!PY_EXE!" -c "import cmapi; print('ok')" >nul 2>&1
if !ERRORLEVEL!==0 (
    set "CM_PYTHON=!PY_EXE!"
    echo %OK%+%NC% CarMaker Python: !PY_EXE! (cmapi 可用)
) else (
    set "CM_PYTHON=!PY_EXE!"
    echo %OK%+%NC% CarMaker Python: !PY_EXE! (cmapi 不可用，后续将自动安装)
)
exit /b 0

:found_python
if "%CM_PYTHON%"=="" (
    echo %ERR%!%NC% 未找到可用 Python
    pause
    exit /b 1
)

echo.
echo %OK%^|%NC% 使用 Python: %CM_PYTHON%
"%CM_PYTHON%" --version
echo.

:: ========================================
:: Step 2: 创建虚拟环境
:: ========================================
echo %INFO%*%NC% 步骤 2/4: 创建虚拟环境...
echo.

if exist "%VENV_DIR%" (
    echo %WARN%~%NC% 虚拟环境已存在，跳过创建
    echo   如需重建请删除 .venv 目录后重试
) else (
    "%CM_PYTHON%" -m venv "%VENV_DIR%"
    if !ERRORLEVEL! neq 0 (
        echo %ERR%!%NC% 虚拟环境创建失败
        pause
        exit /b 1
    )
    echo %OK%^|%NC% 虚拟环境已创建: !VENV_DIR!
)
echo.

:: ========================================
:: Step 3: 安装依赖
:: ========================================
echo %INFO%*%NC% 步骤 3/4: 安装 Python 依赖...
echo.

set "PIP=%VENV_DIR%\Scripts\pip.exe"
set "REQUIREMENTS=%SCRIPT_DIR%\docs\requirements.txt"

:: 配置国内 pip 镜像（加速下载）
"%PIP%" config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple >nul 2>&1
echo %OK%^|%NC% pip 镜像已配置（清华源）

:: 先升级 pip
echo %INFO%*%NC% 正在升级 pip...
"%PIP%" install --upgrade pip
if !ERRORLEVEL! equ 0 (
    echo %OK%^|%NC% pip 已升级
) else (
    echo %WARN%~%NC% pip 升级跳过
)

:: 安装 requirements
if exist "%REQUIREMENTS%" (
    echo 正在从 requirements.txt 安装依赖...
    "%PIP%" install -r "%REQUIREMENTS%"
    if !ERRORLEVEL! neq 0 (
        echo %WARN%~%NC% 部分依赖安装失败，尝试逐个安装...
        "%PIP%" install -r "%REQUIREMENTS%" --no-deps
    )
) else (
    echo %WARN%~%NC% 未找到 requirements.txt，安装核心依赖
    "%PIP%" install numpy opencv-python pillow pywin32 pywinauto PySide6 pyqtgraph
)

:: 额外确保 PySide6 已安装
"%PIP%" show PySide6 >nul 2>&1
if !ERRORLEVEL! neq 0 (
    echo 正在安装 PySide6 (GUI 框架)...
    "%PIP%" install PySide6
)

echo %OK%^|%NC% 依赖安装完成
echo.

:: ========================================
:: 安装 cmapi（从 CarMaker Python 目录）
:: ========================================
echo %INFO%*%NC% 正在安装 CarMaker cmapi 模块...
set "CMAPI_DONE="
if not "%CM_PYTHON%"=="" (
    for %%i in ("%CM_PYTHON%") do set "CM_DIR=%%~dpi"
    for /f "delims=" %%w in ('dir "%CM_DIR%..\Python\cmapi-*.whl" /b /o-n 2^>nul') do (
        if not defined CMAPI_DONE "%PIP%" install "%CM_DIR%..\Python\%%w" >nul 2>&1
        if not defined CMAPI_DONE if !ERRORLEVEL! equ 0 (
            echo %OK%^|%NC% cmapi (whl) 已安装 && set "CMAPI_DONE=1"
        )
    )
    for /f "delims=" %%v in ('dir "%CM_DIR%..\Python\python*" /b /ad /o-n 2^>nul') do (
        if not defined CMAPI_DONE if exist "%CM_DIR%..\Python\%%v\cmapi" (
            >"%VENV_DIR%\Lib\site-packages\cmapi_path.pth" echo %CM_DIR%..\Python\%%v
            echo %OK%^|%NC% cmapi (directory) 已配置 && set "CMAPI_DONE=1"
        )
    )
)
if not defined CMAPI_DONE echo %WARN%~%NC% 未找到 cmapi 模块（不影响核心功能，但 CarMaker 联调不可用）
echo.

:: ========================================
:: Step 4: 生成 run.bat
:: ========================================
echo %INFO%*%NC% 步骤 4/4: 生成启动脚本...

set "RUN_BAT=%SCRIPT_DIR%\run.bat"
(
echo @echo off
echo chcp 65001 ^>nul
echo title Camera Calibration Tool
echo.
echo set "ROOT_DIR=%%~dp0"
echo set "ROOT_DIR=%%ROOT_DIR:~0,-1%%"
echo.
echo call "%%ROOT_DIR%%\.venv\Scripts\activate.bat"
echo if %%ERRORLEVEL%% neq 0 ^(
echo     echo [错误] 无法激活虚拟环境，请重新运行 setup.bat
echo     pause
echo     exit /b 1
echo ^)
echo.
echo python "%%ROOT_DIR%%\src\entry\launch_gui.py" %%*
echo if %%ERRORLEVEL%% neq 0 ^(
echo     echo [错误] 程序异常退出，代码: %%ERRORLEVEL%%
echo     pause
echo ^)
) > "%RUN_BAT%"

echo %OK%^|%NC% 启动脚本已生成: run.bat
echo.

:: ========================================
:: Step 5: 可选 - 创建桌面快捷方式
:: ========================================
:shortcut
echo %INFO%*%NC% 是否创建桌面快捷方式？(Y/N)
set /p CREATE_SHORTCUT=

if /i "!CREATE_SHORTCUT!"=="Y" (
    set "DESKTOP=%USERPROFILE%\Desktop"
    set "SHORTCUT=%DESKTOP%\Camera Calibration.lnk"
    set "PS_COMMAND=$s=(New-Object -COM WScript.Shell).CreateShortcut('%SHORTCUT%'); $s.TargetPath='%RUN_BAT%'; $s.WorkingDirectory='%SCRIPT_DIR%'; $s.Description='Camera Calibration Tool'; $s.Save()"
    powershell -Command "!PS_COMMAND!" >nul 2>&1
    if !ERRORLEVEL! equ 0 (
        echo %OK%^|%NC% 桌面快捷方式已创建
    ) else (
        echo %WARN%~%NC% 快捷方式创建失败，可手动发送 run.bat 到桌面
    )
) else (
    echo %INFO%-%NC% 跳过桌面快捷方式
)

echo.
echo ========================================
echo   %OK%环境搭建完成%NC%
echo ========================================
echo.
echo 启动方式:
echo   1. 双击 run.bat 启动 GUI
echo   2. 或直接发送 run.bat 快捷方式到桌面
echo.
echo 如需更新依赖，重新运行 setup.bat 即可。
echo.
pause
exit /b 0
