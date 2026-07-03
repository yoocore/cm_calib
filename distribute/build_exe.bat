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
    exit /b 1
)

:: Build cache in distribute/build_cache/
if not exist "%CACHE_DIR%" mkdir "%CACHE_DIR%"

:: Build directly to dist_exe (PyInstaller incremental update)
echo [1/4] Building EXE (this may take several minutes)...
"%PYTHON%" -OO -m PyInstaller ^
    --noconfirm ^
    --onedir ^
    --windowed ^
    --name "CameraCalibration" ^
    --distpath "%DIST_DIR%" ^
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
    exit /b 1
)

:: Flatten — PyInstaller --name 多建一层 CameraCalibration\
if exist "%DIST_DIR%\CameraCalibration\_internal" (
    echo [2/4] Flattening output...
    robocopy "%DIST_DIR%\CameraCalibration\_internal" "%DIST_DIR%\_internal" /e /is /it /njh /njs /ndl /nfl >nul
    if exist "%DIST_DIR%\CameraCalibration\CameraCalibration.exe" (
        move "%DIST_DIR%\CameraCalibration\CameraCalibration.exe" "%DIST_DIR%\" >nul
    )
    rmdir /s /q "%DIST_DIR%\CameraCalibration" 2>nul
)

:: Post-build cleanup — remove unnecessary native binaries
echo [3/4] Post-build cleanup...

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

:: 7z compression — find 7z from common install paths
echo [4/4] Creating distribution archive...

set "SZIP="
if exist "%ProgramFiles%\7-Zip\7z.exe"       set "SZIP=%ProgramFiles%\7-Zip\7z.exe"
if exist "%ProgramW6432%\7-Zip\7z.exe"       set "SZIP=%ProgramW6432%\7-Zip\7z.exe"
if exist "%ProgramFiles(x86)%\7-Zip\7z.exe"  set "SZIP=%ProgramFiles(x86)%\7-Zip\7z.exe"
for %%X in (7z.exe) do if not defined SZIP set "SZIP=%%~$PATH:X"

if defined SZIP (
    for /f %%t in ('powershell -NoProfile "Get-Date -Format 'yyyyMMdd'"') do set "ARCHIVE_DATE=%%t"
    set "ARCHIVE=%DIST_DIR%\..\CameraCalibration_!ARCHIVE_DATE!.7z"
    del "!ARCHIVE!" 2>nul
    "!SZIP!" a -t7z -mx=9 -mmt -bb0 "!ARCHIVE!" "%DIST_DIR%" >nul
    if !ERRORLEVEL! equ 0 (
        echo [OK] Archive created:
        call :pretty_size "!ARCHIVE!"
    ) else (
        echo [WARN] 7z compression failed, skipping.
    )
) else (
    echo [WARN] 7-Zip not found, skip compression.
    echo       Install 7-Zip (https://7-zip.org) to enable auto-archive.
)
goto :end

:pretty_size
for %%f in (%~1) do set "SIZE_KB=%%~zf"
set /a "SIZE_MB=!SIZE_KB! / 1048576"
set /a "SIZE_REM=!SIZE_KB! / 1024"
if !SIZE_MB! geq 1 ( echo       %~1 (!SIZE_MB! MB) ) else ( echo       %~1 (!SIZE_REM! KB) )
exit /b 0

:end
echo.
echo ============================================
echo  Build Complete
echo ============================================
echo.
echo EXE:     %DIST_DIR%\CameraCalibration.exe
dir "%DIST_DIR%\CameraCalibration.exe" | find "CameraCalibration.exe"
