@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
set "VENV=%SCRIPT_DIR%\cm_calib_dist\.venv"
set "PYTHON=%VENV%\Scripts\python.exe"
set "DIST_DIR=%SCRIPT_DIR%\dist_exe"
set "CACHE_DIR=%SCRIPT_DIR%\build_cache"

echo ============================================
echo  Building Camera Calibration EXE
echo ============================================
echo.

if not exist "!PYTHON!" (
    echo [ERR] venv not found, run setup.bat first
    pause
    exit /b 1
)

:: Build cache in distribute/build_cache/
if not exist "%CACHE_DIR%" mkdir "%CACHE_DIR%"

:: Use temp dist path to avoid locked-file conflicts
set "BUILD_DIR=%TEMP%\cm_calib_build_%RANDOM%"

:: Build with PyInstaller (onedir mode - more reliable)
echo [1/2] Building EXE (this may take several minutes)...
"%PYTHON%" -OO -m PyInstaller ^
    --noconfirm ^
    --onedir ^
    --windowed ^
    --strip ^
    --name "CameraCalibration" ^
    --distpath "%BUILD_DIR%" ^
    --workpath "%CACHE_DIR%\pyibuild" ^
    --specpath "%CACHE_DIR%\pyispec" ^
    --add-data "%SCRIPT_DIR%\..\src\gui_app\icon.svg;src\gui_app" ^
    --add-data "%SCRIPT_DIR%\..\src\gui_app\checkbox_checked.svg;src\gui_app" ^
    --add-data "%SCRIPT_DIR%\..\src\gui_app\dropdown_arrow.svg;src\gui_app" ^
    --add-data "%SCRIPT_DIR%\..\src\gui_app\arrow_up.svg;src\gui_app" ^
    --add-data "%SCRIPT_DIR%\..\src\gui_app\arrow_down.svg;src\gui_app" ^
    --add-data "%SCRIPT_DIR%\..\src\health\precheck_cli.py;src\health" ^
    --add-data "%SCRIPT_DIR%\..\src\calibration\cli.py;src\calibration" ^
    --add-data "%SCRIPT_DIR%\..\src\orchestration\calibration_orchestrator.py;src\orchestration" ^
    --hidden-import PySide6.QtCore ^
    --hidden-import PySide6.QtGui ^
    --hidden-import PySide6.QtWidgets ^
    --exclude-module PySide6.QtQml ^
    --exclude-module PySide6.QtQuick ^
    --exclude-module PySide6.QtQuickWidgets ^
    --exclude-module PySide6.QtWebEngineCore ^
    --exclude-module cmapi ^
    --exclude-module apoc ^
    --exclude-module infofiles ^
    --exclude-module IPG ^
    --hidden-import src.entry.launch_gui ^
    --hidden-import src.gui_app ^
    --hidden-import src.gui_app.app ^
    --hidden-import src.gui_app.main_window ^
    --hidden-import src.health.verify_runtime_chain_baseline ^
    "%SCRIPT_DIR%\..\src\entry\launch_gui.py"

if !ERRORLEVEL! neq 0 (
    echo [ERR] Build failed
    pause
    exit /b 1
)

:: Deploy to final location (flatten — no intermediate CameraCalibration dir)
echo [2/2] Deploying to %DIST_DIR%...
:: Remove old EXE + _internal (in case locked, rename first)
if exist "%DIST_DIR%\CameraCalibration.exe" (
    del "%DIST_DIR%\CameraCalibration.exe" 2>nul
    if exist "%DIST_DIR%\CameraCalibration.exe" (
        ren "%DIST_DIR%\CameraCalibration.exe" "CameraCalibration.exe.old" 2>nul
    )
)
if exist "%DIST_DIR%\_internal" (
    rmdir /s /q "%DIST_DIR%\_internal" 2>nul
    if exist "%DIST_DIR%\_internal" (
        ren "%DIST_DIR%\_internal" "_internal.old" 2>nul
    )
)
mkdir "%DIST_DIR%" 2>nul
move "%BUILD_DIR%\CameraCalibration\CameraCalibration.exe" "%DIST_DIR%\CameraCalibration.exe" >nul
move "%BUILD_DIR%\CameraCalibration\_internal" "%DIST_DIR%\_internal" >nul
:: Clean up stale + temp
if exist "%DIST_DIR%\CameraCalibration.exe.old" del "%DIST_DIR%\CameraCalibration.exe.old" 2>nul
if exist "%DIST_DIR%\_internal.old" rmdir /s /q "%DIST_DIR%\_internal.old" 2>nul
rmdir /s /q "%BUILD_DIR%" 2>nul

:: Post-build cleanup — remove unnecessary native binaries
echo [3/3] Post-build cleanup...

:: QtQml/QtQuick native DLLs (not used by app, but hook collects them)
del "%DIST_DIR%\_internal\PySide6\Qt6Quick.dll"    2>nul
del "%DIST_DIR%\_internal\PySide6\Qt6Qml.dll"      2>nul
del "%DIST_DIR%\_internal\PySide6\Qt6QmlMeta.dll"  2>nul
del "%DIST_DIR%\_internal\PySide6\Qt6QmlModels.dll" 2>nul
del "%DIST_DIR%\_internal\PySide6\Qt6QmlWorkerScript.dll" 2>nul
del "%DIST_DIR%\_internal\PySide6\Qt6Pdf.dll"             2>nul
del "%DIST_DIR%\_internal\PySide6\Qt6VirtualKeyboard.dll"  2>nul
del "%DIST_DIR%\_internal\PySide6\Qt6Test.dll"             2>nul
del "%DIST_DIR%\_internal\PySide6\QtTest.pyd"              2>nul

:: Software OpenGL fallback (20MB) — not needed on modern GPUs
del "%DIST_DIR%\_internal\PySide6\opengl32sw.dll" 2>nul

:: Trim translations — keep only zh_CN + en (6.6MB→~200KB)
if exist "%DIST_DIR%\_internal\PySide6\translations" (
    set "KEEP_TRANS=%TEMP%\cm_calib_trans_%RANDOM%"
    mkdir "!KEEP_TRANS!" 2>nul
    move "%DIST_DIR%\_internal\PySide6\translations\*_zh_CN.qm" "!KEEP_TRANS!\" >nul 2>nul
    move "%DIST_DIR%\_internal\PySide6\translations\*_en.qm"    "!KEEP_TRANS!\" >nul 2>nul
    rmdir /s /q "%DIST_DIR%\_internal\PySide6\translations"
    mkdir "%DIST_DIR%\_internal\PySide6\translations" 2>nul
    move "!KEEP_TRANS!\*.qm" "%DIST_DIR%\_internal\PySide6\translations\" >nul 2>nul
    rmdir /s /q "!KEEP_TRANS!" 2>nul
)

:: Create README
(
    echo Camera Calibration Console - v1.2.5
    echo =====================================
    echo.
    echo Build Date: %date% %time%
    echo.
    echo Usage:
    echo   Run CameraCalibration.exe
    echo.
    echo Requirements:
    echo   - CarMaker 15.1 or 14.1 installed on C: or D: drive
    echo   - Windows 10/11 64-bit
    echo.
    echo The executable automatically detects your CarMaker
    echo installation and loads cmapi/apoc modules from it.
    echo.
    echo To uninstall, simply delete this folder.
) > "%DIST_DIR%\README.txt"

echo.
echo ============================================
echo  Build Complete
echo ============================================
echo.
echo Output: %DIST_DIR%\CameraCalibration.exe
dir "%DIST_DIR%\CameraCalibration.exe" | find "CameraCalibration.exe"
