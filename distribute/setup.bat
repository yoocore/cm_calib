@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

:: 全量输出捕获（Tee-Object），时间戳避免文件锁
if not defined TEE_ACTIVE (
    set "TEE_ACTIVE=1"
    del /f /q "%~dp0setup_*.log" >nul 2>&1
    for /f %%t in ('powershell -NoProfile "Get-Date -Format 'yyyyMMdd_HHmmss'"') do set "TS=%%t"
    set "TEE_LOG_FILE=%~dp0setup_!TS!.log"
    set "TEE_BAT_PATH=%~f0"
    powershell -NoProfile "$env:TEE_ACTIVE='1'; cmd /c '!TEE_BAT_PATH!' 2>&1 | Tee-Object -FilePath '!TEE_LOG_FILE!'"
    echo.
    echo Log saved to: !TEE_LOG_FILE!
    pause
    exit /b
)

title CM Camera Calibration - 环境搭建

:: 脚本目录（用于定位 uv.exe 等）
set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

set "VENV_DIR=%SCRIPT_DIR%\.venv"
set "LOG_FILE=%SCRIPT_DIR%\setup.log"

:: 获取 ESC 字符用于 ANSI 颜色输出
for /f %%a in ('echo prompt $E ^| cmd') do set "ESC=%%a"
set "ERR=%ESC%[31m"
set "OK=%ESC%[32m"
set "INFO=%ESC%[36m"
set "WARN=%ESC%[33m"
set "NC=%ESC%[0m"

:: ========================================
:: 目标 Python 版本（可修改此行）
:: ========================================
set "CM_PY_VER=3.10"

echo ========================================
echo   Camera Calibration Tool - 环境搭建
echo ========================================
echo.

:: ========================================
:: Step 1: 查找 CarMaker 安装位置
:: ========================================
echo %INFO%*%NC% 正在检测 CarMaker 安装位置...
call :log 步骤 1/4: 查找 Python
echo.

set "CM_PYTHON_DIR="

:: 1.1 尝试环境变量
if not "%CARMAKER_DIR%"=="" (
    if exist "%CARMAKER_DIR%\*" (
        echo %OK%+%NC% 从环境变量 CARMAKER_DIR 找到: %CARMAKER_DIR%
        for /f "delims=" %%d in ('dir "%CARMAKER_DIR%\win64-*" /b /o-n 2^>nul') do (
            call :find_python_dir "%CARMAKER_DIR%\%%d"
        )
    )
)

:: 1.2 扫描常见安装路径
set "SEARCH_ROOTS=C:\IPG\carmaker D:\IPG\carmaker C:\IPG D:\IPG C:\CarMaker D:\CarMaker C:\Program Files\IPG\carmaker D:\Program Files\IPG\carmaker C:\Program Files\CarMaker D:\Program Files\CarMaker"

for %%r in (%SEARCH_ROOTS%) do (
    if exist "%%r\*" (
        for /f "delims=" %%d in ('dir "%%r\win64-*" /b /o-n 2^>nul') do (
            call :find_python_dir "%%r\%%d"
        )
    )
)

:: 1.3 尝试注册表
for /f "skip=2 tokens=2,*" %%a in ('reg query "HKLM\SOFTWARE\IPG\CarMaker" /s /v InstallPath 2^>nul') do (
    if exist "%%b\*" (
        for /f "delims=" %%d in ('dir "%%b\win64-*" /b /o-n 2^>nul') do (
            call :find_python_dir "%%b\%%d"
        )
    )
)

if defined CM_PYTHON_DIR (
    echo %OK%+%NC% CarMaker Python 目录: !CM_PYTHON_DIR!
) else (
    echo %WARN%~%NC% 未找到 CarMaker 安装
)

echo.
echo %INFO%*%NC% 目标 Python 版本: !CM_PY_VER!
echo.

:: ========================================
:: Step 2: 安装 Python 并创建虚拟环境
:: ========================================
set "UV_DIR=%TEMP%\uv_%RANDOM%"
set "UV_EXE=%UV_DIR%\uv.exe"
mkdir "%UV_DIR%" 2>nul

:: 优先使用本地 uv.exe
if exist "%SCRIPT_DIR%\uv.exe" (
    echo %OK%^|%NC% 使用本地 uv.exe
    copy "%SCRIPT_DIR%\uv.exe" "!UV_EXE!" >nul 2>&1
    goto :uv_found
)
if exist "%SCRIPT_DIR%\tools\uv.exe" (
    echo %OK%^|%NC% 使用 tools\uv.exe
    copy "%SCRIPT_DIR%\tools\uv.exe" "!UV_EXE!" >nul 2>&1
    goto :uv_found
)

:: 本地没有 → 下载 uv（使用国内镜像加速）
echo %INFO%*%NC% 下载 uv（Python 包管理器）(如果网络较慢请耐心等待)...
curl -sL --connect-timeout 15 https://ghproxy.net/https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip -o "%UV_DIR%\uv.zip"
if !ERRORLEVEL! neq 0 (
    call :log GitHub 代理下载失败，尝试直连...
    curl -sL --connect-timeout 15 https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip -o "%UV_DIR%\uv.zip"
)
if !ERRORLEVEL! neq 0 (
    echo %ERR%!%NC% uv 下载失败，尝试通过 pip 安装 uv...
    call :log 尝试通过 pip 安装 uv
    pip install uv >nul 2>&1
    if !ERRORLEVEL! equ 0 (
        echo %OK%^|%NC% 通过 pip 安装 uv 成功
        set "UV_EXE=uv"
        goto :uv_ready
    )
    echo %ERR%!%NC% uv 安装失败
    pause
    exit /b 1
)
powershell -Command "Expand-Archive '%UV_DIR%\uv.zip' -DestinationPath '%UV_DIR%'" >nul 2>&1
for /r "%UV_DIR%" %%f in (uv.exe) do set "UV_EXE=%%f" & goto :uv_found
:uv_found
if not exist "!UV_EXE!" (
    echo %ERR%!%NC% uv 解压失败
    pause
    exit /b 1
)

:uv_ready
echo %INFO%*%NC% 正在安装 Python !CM_PY_VER!...
"!UV_EXE!" python install !CM_PY_VER! >nul 2>&1
if !ERRORLEVEL! neq 0 (
    echo %ERR%!%NC% Python !CM_PY_VER! 安装失败
    call :log uv 安装 Python !CM_PY_VER! 失败
    pause
    exit /b 1
)
echo %OK%^|%NC% Python !CM_PY_VER! 已安装
call :log 通过 uv 安装 Python !CM_PY_VER! 完成

:: 用 uv 创建虚拟环境（先清理旧目录）
if exist "%VENV_DIR%" (
    echo %WARN%~%NC% 移除旧的虚拟环境...
    rmdir /s /q "%VENV_DIR%" 2>nul
)
echo %INFO%*%NC% 正在创建虚拟环境（Python !CM_PY_VER!）...
"!UV_EXE!" venv --python !CM_PY_VER! --seed "%VENV_DIR%" 2>&1
if !ERRORLEVEL! neq 0 (
    echo %ERR%!%NC% 虚拟环境创建失败
    call :log uv 虚拟环境创建失败
    pause
    exit /b 1
)
echo %OK%^|%NC% 虚拟环境已创建
set "_UV_EXE=!UV_EXE!"
set "PIP=%VENV_DIR%\Scripts\pip.exe"
set "CM_PYTHON=%VENV_DIR%\Scripts\python.exe"
echo %OK%^|%NC% 使用 Python: !CM_PYTHON!
"%CM_PYTHON%" --version
echo.

:: ========================================
:: Step 3: 检查 CarMaker 兼容性
:: ========================================
echo %INFO%*%NC% 检查 CarMaker cmapi 兼容性...
call :check_cm_compat
if !ERRORLEVEL! neq 0 (
    echo %ERR%!%NC% CarMaker 不兼容 Python !CM_PY_VER!
    echo %INFO%^|%NC% 请在 setup.bat 中将 CM_PY_VER 改为兼容版本后重试
    pause
    exit /b 1
)
echo %OK%^|%NC% Python !CM_PY_VER! 兼容当前 CarMaker
echo.

:: ========================================
:: Step 4: 安装依赖（支持增量更新）
:: ========================================
echo %INFO%*%NC% 步骤 4: 检查 Python 依赖...
call :log 步骤 4: 检查 Python 依赖
echo.

:: 检查 pip 是否可用
if not exist "!PIP!" (
    echo %ERR%!%NC% 错误：pip.exe 不存在，虚拟环境可能损坏
    echo   请手动删除 .venv 目录后重新运行 setup.bat
    pause
    exit /b 1
)

set "REQUIREMENTS=%SCRIPT_DIR%\docs\requirements.txt"
set "DEPS_MARKER=%VENV_DIR%\.deps_installed"

if exist "!DEPS_MARKER!" (
    echo %OK%^|%NC% 依赖未变化，跳过安装
    call :log 依赖未变化，跳过安装
) else (
    echo %INFO%*%NC% 首次安装依赖
    call :log 首次安装依赖
    set "PIP_PROGRESS_BAR=on"
    "%PIP%" config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple >nul 2>&1
    echo %OK%^|%NC% pip 镜像已配置（清华源）
    echo %INFO%*%NC% 正在升级 pip...
    "%PIP%" install --progress-bar on --upgrade pip
    if !ERRORLEVEL! equ 0 ( echo %OK%^|%NC% pip 已升级 ) else ( echo %WARN%~%NC% pip 升级跳过 )
    if exist "%REQUIREMENTS%" (
        echo %INFO%*%NC% 正在增量安装依赖...
        for /f "usebackq delims=" %%p in ("%REQUIREMENTS%") do (
            echo %INFO%^|%NC% 安装 %%p ...
            "%PIP%" install --progress-bar on %%p
            if !ERRORLEVEL! neq 0 (
                echo %WARN%~%NC% %%p 安装失败，跳过
            )
        )
    ) else (
        echo %WARN%~%NC% 未找到 requirements.txt，安装核心依赖
        "%PIP%" install --progress-bar on numpy opencv-python pillow pywin32 pywinauto PySide6 pyqtgraph
    )
    copy /y "%REQUIREMENTS%" "!DEPS_MARKER!" >nul
    echo %OK%^|%NC% 依赖安装完成
    call :log Python 依赖安装完成
)

echo.

:: ========================================
:: Step 5: 安装 cmapi 并配置 apoc 路径
:: ========================================
echo %INFO%*%NC% 正在安装 CarMaker cmapi 模块...
call :log 步骤 5: 安装 cmapi / 配置 apoc
echo.

set "CMAPI_DONE="

:: 计算 Python 标签（cp39、cp310 等）
set "PY_TAG="
for /f "delims=" %%t in ('"%CM_PYTHON%" -c "import sys; print(f'cp{sys.version_info.major}{sys.version_info.minor}')"') do set "PY_TAG=%%t"
if not defined PY_TAG (
    for /f "delims=. tokens=1,2" %%a in ("!CM_PY_VER!") do set "PY_TAG=cp%%a%%b"
)
echo %OK%^|%NC% Python 标签: !PY_TAG!

:: 扫描 CarMaker 安装目录，安装匹配版本的 cmapi whl
for %%r in (C:\IPG\carmaker D:\IPG\carmaker C:\IPG D:\IPG) do (
    if exist "%%r" for /f "delims=" %%d in ('dir "%%r\win64-*" /b /o-n 2^>nul') do (
        if not defined CMAPI_DONE (
            for /f "delims=" %%w in ('dir "%%r\%%d\Python\cmapi-*!PY_TAG!*.whl" /b /o-n 2^>nul') do (
                "%PIP%" install "%%r\%%d\Python\%%w" >nul 2>&1
                if !ERRORLEVEL! equ 0 (
                    echo %OK%^|%NC% cmapi ^(whl^) 已安装
                    set "CMAPI_DONE=1"
                )
            )
        )
        rem 记录 CM_PYTHON_DIR（用于 apoc .pth）
        if not defined CM_PYTHON_DIR (
            for /f "delims=" %%v in ('dir "%%r\%%d\Python\python*" /b /ad /o-n 2^>nul') do (
                set "CM_PYTHON_DIR=%%r\%%d\Python\%%v"
            )
        )
    )
)

:: 配置 apoc 路径
echo %INFO%*%NC% 配置 apoc 模块路径...
if defined CM_PYTHON_DIR (
    >"%VENV_DIR%\Lib\site-packages\zzz_apoc_path.pth" echo !CM_PYTHON_DIR!
    echo !CM_PYTHON_DIR!\.. >>"%VENV_DIR%\Lib\site-packages\zzz_apoc_path.pth"
    if exist "!CM_PYTHON_DIR!\Lib\site-packages" (
        echo !CM_PYTHON_DIR!\Lib\site-packages >>"%VENV_DIR%\Lib\site-packages\zzz_apoc_path.pth"
    )
    echo %OK%^|%NC% CarMaker Python 路径已添加到 .pth
) else (
    echo %WARN%~%NC% 未找到 CarMaker Python 目录，跳过 apoc .pth 配置
)
if not defined CMAPI_DONE echo %WARN%~%NC% 未找到 cmapi whl（不影响核心功能，但 CarMaker 联调不可用）
call :log cmapi 模块安装状态: !CMAPI_DONE!

:: ========================================
:: Step 6: 生成 run.bat
:: ========================================
echo %INFO%*%NC% 步骤 6: 生成启动脚本...
call :log 步骤 6: 生成启动脚本

set "RUN_BAT=%CD%\run.bat"
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
call :log 启动脚本已生成: run.bat
if not exist "!RUN_BAT!" (
    echo %WARN%~%NC% 警告：run.bat 生成后未检测到文件，请检查目录权限
    call :log 警告：run.bat 未检测到，路径=!RUN_BAT!
)
echo.

:: 清理临时文件
if defined _UV_EXE (
    rmdir /s /q "%UV_DIR%" 2>nul
    echo %OK%^|%NC% 临时文件已清理
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
echo 详细日志已保存至: %LOG_FILE%
echo.
if exist "!RUN_BAT!" (
    call :log === 环境搭建完成 === ^(run.bat 已生成^)
) else (
    call :log === 环境搭建完成，但 run.bat 未生成 ===
)
pause
exit /b 0

:: ========================================
:: 辅助函数
:: ========================================

:log
echo [%DATE% %TIME%] %*
exit /b 0

:find_python_dir
set "CM_INSTALL=%~1"
if not exist "%CM_INSTALL%" exit /b 0
if not defined CM_PYTHON_DIR (
    if exist "%CM_INSTALL%\Python" (
        for /f "delims=" %%p in ('dir "%CM_INSTALL%\Python\python*" /b /ad 2^>nul') do (
            set "CM_PYTHON_DIR=%CM_INSTALL%\Python\%%p"
            exit /b 0
        )
        rem Python 根目录（无版本子目录）
        if exist "%CM_INSTALL%\Python" (
            set "CM_PYTHON_DIR=%CM_INSTALL%\Python"
        )
    )
)
exit /b 0

:check_cm_compat
setlocal
set "TAG=cp%CM_PY_VER:.=%"
set "COMPATIBLE=0"
set "VERSIONS="

for %%r in (C:\IPG\carmaker D:\IPG\carmaker C:\IPG D:\IPG) do (
    if exist "%%r" for /f "delims=" %%d in ('dir "%%r\win64-*" /b /o-n 2^>nul') do (
        if exist "%%r\%%d\Python" (
            rem 从目录名提取版本
            for /f "delims=" %%v in ('dir "%%r\%%d\Python\python*" /b /ad 2^>nul') do (
                set "DIRVER=%%v"
                set "DIRVER=!DIRVER:python=!"
                set "DIRVER=!DIRVER:.=!"
                if not defined VERSIONS (set "VERSIONS=%%v") else (set "VERSIONS=!VERSIONS! %%v")
                if "!TAG!"=="cp!DIRVER!" set "COMPATIBLE=1"
            )
            rem 从 whl 文件提取版本
            for /f "delims=" %%w in ('dir "%%r\%%d\Python\cmapi-*.whl" /b /o-n 2^>nul') do (
                for /f "delims=- tokens=3-5" %%a in ("%%~nw") do (
                    if not defined VERSIONS (set "VERSIONS=%%a") else (set "VERSIONS=!VERSIONS! %%a")
                    if "%%a"=="!TAG!" set "COMPATIBLE=1"
                    if "%%b"=="abi3" if "!COMPATIBLE!"=="0" (
                        "%VENV_DIR%\Scripts\python.exe" -c "import sys; t='!CM_PY_VER!'; mj,mn=map(int,t.split('.')); target=mj*100+mn; raw=int('%%a'[2:]); cv=(raw//10)*100+(raw%10) if raw<100 else raw; sys.exit(0 if target>=cv else 1)"
                        if !ERRORLEVEL! equ 0 set "COMPATIBLE=1"
                    )
                )
            )
        )
    )
)

if "!COMPATIBLE!"=="0" (
    echo %ERR%!%NC% CarMaker 不支持 Python !CM_PY_VER!
    if defined VERSIONS (
        echo %INFO%^|%NC% 支持的版本: !VERSIONS!
    )
    endlocal
    exit /b 1
)
endlocal
exit /b 0
