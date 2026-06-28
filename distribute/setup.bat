@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

:: 全量输出捕获，覆盖旧日志
if not defined TEE_ACTIVE (
    set "TEE_ACTIVE=1"
    set "TEE_LOG_FILE=%~dp0setup.log"
    set "TEE_BAT_PATH=%~f0"
    cmd /v:on /c ""!TEE_BAT_PATH!" > "!TEE_LOG_FILE!" 2>&1"
    type "!TEE_LOG_FILE!"
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

echo ========================================
echo   Camera Calibration Tool - 环境搭建
echo ========================================
echo.

:: ========================================
:: Step 1: 查找 CarMaker Python
:: ========================================
echo %INFO%*%NC% 正在检测 CarMaker 安装位置...
call :log 步骤 1/4: 查找 Python
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

:: 未找到 CarMaker Python → 用 uv 安装独立 Python 3.10
if defined _CM_ALL_VERSIONS (
    echo %WARN%~%NC% 找到 CarMaker 版本: !_CM_ALL_VERSIONS!，但均无独立 Python 解释器
    call :log 找到 CarMaker 版本: !_CM_ALL_VERSIONS!，均无 Python 解释器
)

echo %INFO%*%NC% 正在通过 uv 安装独立 Python 3.10...
echo.
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
    pip install uv -i https://pypi.tuna.tsinghua.edu.cn/simple >nul 2>&1
    if !ERRORLEVEL! equ 0 (
        echo %OK%^|%NC% 通过 pip 安装 uv 成功
        set "UV_EXE=uv"
        goto :uv_ready
    )
    echo %ERR%!%NC% uv 安装失败
    call :log uv 安装失败
    echo %WARN%~%NC% 正在尝试直接从清华镜像下载 Python 3.10...
    call :log 尝试从清华镜像下载 Python 3.10
    set "PYTHON_DIR=%TEMP%\cm_python_%RANDOM%"
    mkdir "!PYTHON_DIR!" 2>nul
    curl -sL --connect-timeout 20 "https://mirrors.tuna.tsinghua.edu.cn/python/3.10.11/python-3.10.11-amd64.exe" -o "!PYTHON_DIR!\python-installer.exe"
    if !ERRORLEVEL! equ 0 (
        echo %INFO%*%NC% 正在静默安装 Python 3.10...
        "!PYTHON_DIR!\python-installer.exe" /quiet InstallAllUsers=0 PrependPath=0 Include_pip=1 TargetDir="!PYTHON_DIR!\python" >nul 2>&1
        if exist "!PYTHON_DIR!\python\python.exe" (
            echo %OK%^|%NC% Python 3.10 已安装至临时目录
            set "CM_PYTHON=!PYTHON_DIR!\python\python.exe"
            goto :found_python
        )
    )
    :: uv 全部失败 → 最后尝试系统 Python
    echo %WARN%~%NC% 下载安装 Python 失败，尝试使用系统 Python...
    call :log 尝试使用系统 Python
    where python >nul 2>nul
    if !ERRORLEVEL! equ 0 (
        for /f "delims=" %%p in ('where python') do (
            "%%p" -c "import venv; print('ok')" >nul 2>&1
            if !ERRORLEVEL! equ 0 (
                set "CM_PYTHON=%%p"
                goto :found_python
            )
        )
    )
    call :log 所有 Python 安装方式均失败
    if defined _CM_ALL_VERSIONS (
        echo   CarMaker 版本 !_CM_ALL_VERSIONS! 已找到，但均无 Python 解释器
    )
    echo 请手动安装 Python 3.10/3.11 后重试
    echo   下载地址: https://www.python.org/downloads/
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
:: 用 uv 安装 Python 3.10
echo %INFO%*%NC% 正在安装 Python 3.10...
"!UV_EXE!" python install 3.10 >nul 2>&1
if !ERRORLEVEL! neq 0 (
    echo %ERR%!%NC% Python 安装失败
    call :log uv 安装 Python 3.10 失败
    pause
    exit /b 1
)
echo %OK%^|%NC% Python 3.10 已安装
call :log 通过 uv 安装 Python 3.10 完成

:: 用 uv 创建虚拟环境（先清理旧目录）
if exist "%VENV_DIR%" (
    echo %WARN%~%NC% 移除旧的虚拟环境...
    rmdir /s /q "%VENV_DIR%" 2>nul
)
echo %INFO%*%NC% 正在创建虚拟环境（Python 3.10）...
"!UV_EXE!" venv --python 3.10 --seed "%VENV_DIR%" >nul 2>&1
if !ERRORLEVEL! neq 0 (
    echo %ERR%!%NC% 虚拟环境创建失败
    call :log uv 虚拟环境创建失败
    pause
    exit /b 1
)
echo %OK%^|%NC% 虚拟环境已创建

:: 保存 uv 路径供后续使用
set "_UV_EXE=!UV_EXE!"
set "CM_PYTHON=%VENV_DIR%\Scripts\python.exe"
echo %OK%^|%NC% 使用 Python: !CM_PYTHON!
"%CM_PYTHON%" --version
echo.
goto :install_deps
pause
exit /b 1


:: ========================================
:: 日志记录函数
:: ========================================
:log
echo [%DATE% %TIME%] %*
exit /b 0

:find_python
set "CM_INSTALL=%~1"
if not exist "%CM_INSTALL%" exit /b 0
:: 保存第一个找到的 CarMaker 路径（最新版本）
if not defined _CM_INSTALL_FIRST set "_CM_INSTALL_FIRST=%CM_INSTALL%"
:: 收集所有找到的 CarMaker 版本
for %%v in ("%~1") do if not defined _CM_ALL_VERSIONS (set "_CM_ALL_VERSIONS=%%~nxv") else (set "_CM_ALL_VERSIONS=!_CM_ALL_VERSIONS! %%~nxv")
:: Record that CM was found, in case it has no Python
for /f "delims=" %%v in ('dir "%CM_INSTALL%" /b /ad /o-n 2^>nul') do if not defined CM_PYTHON (
    if "%%v"=="Python" (
        set "_FOUND_CM_NO_PY=%CM_INSTALL%"
        set "_CM_INSTALL_FIRST=%CM_INSTALL%"
    )
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
call :log 使用 Python: %CM_PYTHON%
echo.

:: ========================================
:: Step 2: 创建虚拟环境
:: ========================================
echo %INFO%*%NC% 步骤 2/4: 检查虚拟环境...
call :log 步骤 2/4: 检查虚拟环境
echo.

set "VENV_NEEDS_CREATE=0"
set "VENV_NEEDS_REPAIR=0"

if not exist "%VENV_DIR%" (
    echo %INFO%*%NC% 虚拟环境不存在，需要创建
    set "VENV_NEEDS_CREATE=1"
) else (
    rem 检查 venv 是否有效
    if not exist "%VENV_DIR%\Scripts\python.exe" (
        echo %WARN%~%NC% 虚拟环境损坏（python.exe 缺失），需要重建
        set "VENV_NEEDS_REPAIR=1"
    ) else (
        "%VENV_DIR%\Scripts\python.exe" -c "import venv; import pip" >nul 2>&1
        if !ERRORLEVEL! neq 0 (
            echo %WARN%~%NC% 虚拟环境损坏（核心模块缺失），需要重建
            set "VENV_NEEDS_REPAIR=1"
        ) else (
            echo %OK%^|%NC% 虚拟环境有效，跳过创建
            call :log 虚拟环境已存在且有效，跳过创建
        )
    )
)

if "!VENV_NEEDS_REPAIR!"=="1" (
    echo %INFO%*%NC% 正在删除损坏的虚拟环境...
    rmdir /s /q "%VENV_DIR%" 2>nul
    set "VENV_NEEDS_CREATE=1"
)

if "!VENV_NEEDS_CREATE!"=="1" (
    echo %INFO%*%NC% 正在创建虚拟环境...
    "%CM_PYTHON%" -m venv "%VENV_DIR%"
    if !ERRORLEVEL! neq 0 (
        echo %ERR%!%NC% 虚拟环境创建失败
        pause
        exit /b 1
    )
    echo %OK%^|%NC% 虚拟环境已创建: !VENV_DIR!
    call :log 虚拟环境已创建: !VENV_DIR!
)
echo.

:install_deps
:: ========================================
:: Step 3: 安装依赖（支持增量更新）
:: ========================================
echo %INFO%*%NC% 步骤 3/4: 检查 Python 依赖...
call :log 步骤 3/4: 检查 Python 依赖
echo.

set "PIP=%VENV_DIR%\Scripts\pip.exe"

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
    goto :install_cmapi
)

echo %INFO%*%NC% 首次安装依赖
call :log 首次安装依赖

"%PIP%" config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple >nul 2>&1
echo %OK%^|%NC% pip 镜像已配置（清华源）

echo %INFO%*%NC% 正在升级 pip...
"%PIP%" install --progress-bar on --upgrade pip
if !ERRORLEVEL! equ 0 (
    echo %OK%^|%NC% pip 已升级
) else (
    echo %WARN%~%NC% pip 升级跳过
)

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

:install_cmapi
echo.

:: ========================================
:: 安装 cmapi（从 CarMaker Python 目录）
:: ========================================
echo %INFO%*%NC% 正在安装 CarMaker cmapi 模块...
set "CMAPI_DONE="
if not "%_CM_INSTALL_FIRST%"=="" (
    for /f "delims=" %%w in ('dir "%_CM_INSTALL_FIRST%\Python\cmapi-*.whl" /b /o-n 2^>nul') do (
        if not defined CMAPI_DONE "%PIP%" install "%_CM_INSTALL_FIRST%\Python\%%w" >nul 2>&1
        if not defined CMAPI_DONE if !ERRORLEVEL! equ 0 (
            echo %OK%^|%NC% cmapi ^(whl^) 已安装 && set "CMAPI_DONE=1"
        )
    )
    for /f "delims=" %%v in ('dir "%_CM_INSTALL_FIRST%\Python\python*" /b /ad /o-n 2^>nul') do (
        if not defined CMAPI_DONE if exist "%_CM_INSTALL_FIRST%\Python\%%v\cmapi" (
            >"%VENV_DIR%\Lib\site-packages\cmapi_path.pth" echo !_CM_INSTALL_FIRST!\Python\%%v
            echo !_CM_INSTALL_FIRST!\Python >>"%VENV_DIR%\Lib\site-packages\cmapi_path.pth"
            echo %OK%^|%NC% cmapi ^(directory^) 已配置 && set "CMAPI_DONE=1"
        )
    )
    rem Always add CarMaker Python path even when cmapi was installed via whl
    if defined CMAPI_DONE (
        for /f "delims=" %%v in ('dir "!_CM_INSTALL_FIRST!\Python\python*" /b /ad /o-n 2^>nul') do (
            if not exist "%VENV_DIR%\Lib\site-packages\cmapi_path.pth" (
                >"%VENV_DIR%\Lib\site-packages\cmapi_path.pth" echo !_CM_INSTALL_FIRST!\Python\%%v
                echo !_CM_INSTALL_FIRST!\Python >>"%VENV_DIR%\Lib\site-packages\cmapi_path.pth"
            )
        )
    )
)
if not defined CMAPI_DONE echo %WARN%~%NC% 未找到 cmapi 模块（不影响核心功能，但 CarMaker 联调不可用）
call :log cmapi 模块安装状态: !CMAPI_DONE!
echo.

:: ========================================
:: Step 4: 生成 run.bat
:: ========================================
echo %INFO%*%NC% 步骤 4/4: 生成启动脚本...
call :log 步骤 4/4: 生成启动脚本

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
