@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
set "VENV=%SCRIPT_DIR%\cm_calib_dist\.venv"
set "PYTHON=%VENV%\Scripts\python.exe"
set "DIST_DIR=%SCRIPT_DIR%\dist_exe"
set "UPX_DIR=%SCRIPT_DIR%\..\build_cache\upx"

echo ============================================
echo  Building Camera Calibration EXE
echo ============================================
echo.

if not exist "!PYTHON!" (
    echo [ERR] venv not found, run setup.bat first
    pause
    exit /b 1
)

:: --- UPX setup (auto-download if missing) ---
set "UPX_BIN=%UPX_DIR%\upx.exe"
if not exist "!UPX_BIN!" (
    echo [SETUP] UPX not found, downloading...
    if not exist "%UPX_DIR%" mkdir "%UPX_DIR%"
    curl -sL "https://github.com/upx/upx/releases/download/v4.2.4/upx-4.2.4-win64.zip" -o "%UPX_DIR%\upx.zip"
    if exist "!UPX_DIR!\upx.zip" (
        powershell -NoProfile -Command "Expand-Archive '%UPX_DIR%\upx.zip' '%UPX_DIR%\upx_tmp' -Force" >nul
        if exist "!UPX_DIR!\upx_tmp\upx-4.2.4-win64\upx.exe" (
            move "%UPX_DIR%\upx_tmp\upx-4.2.4-win64\upx.exe" "%UPX_DIR%\upx.exe" >nul
            rmdir /s /q "%UPX_DIR%\upx_tmp" 2>nul
            del "%UPX_DIR%\upx.zip" 2>nul
            echo [SETUP] UPX ready at !UPX_BIN!
        ) else (
            echo [SETUP] UPX download failed, proceeding without compression
        )
    ) else (
        echo [SETUP] UPX download failed, proceeding without compression
    )
) else (
    echo [SETUP] UPX found at !UPX_BIN!
)

:: Use temp dist path to avoid locked-file conflicts
set "BUILD_DIR=%TEMP%\cm_calib_build_%RANDOM%"

:: Build with PyInstaller (onedir mode - more reliable)
echo [1/2] Building EXE (this may take several minutes)...
"%PYTHON%" -OO -m PyInstaller ^
    --noconfirm ^
    --onedir ^
    --windowed ^
    --name "CameraCalibration" ^
    --distpath "%BUILD_DIR%" ^
    --workpath "%SCRIPT_DIR%\..\build_cache\pyibuild" ^
    --specpath "%SCRIPT_DIR%\..\build_cache\pyispec" ^
    --add-data "%SCRIPT_DIR%\..\src\gui_app\icon.svg;src\gui_app" ^
    --add-data "%SCRIPT_DIR%\..\src\gui_app\checkbox_checked.svg;src\gui_app" ^
    --add-data "%SCRIPT_DIR%\..\src\gui_app\dropdown_arrow.svg;src\gui_app" ^
    --add-data "%SCRIPT_DIR%\..\src\gui_app\arrow_up.svg;src\gui_app" ^
    --add-data "%SCRIPT_DIR%\..\src\gui_app\arrow_down.svg;src\gui_app" ^
    --add-data "%SCRIPT_DIR%\..\src\health\precheck_cli.py;src\health" ^
    --add-data "%SCRIPT_DIR%\..\src\calibration\cli.py;src\calibration" ^
    --add-data "%SCRIPT_DIR%\..\src\orchestration\calibration_orchestrator.py;src\orchestration" ^
    --collect-all PySide6 ^
    --exclude-module cmapi ^
    --exclude-module apoc ^
    --exclude-module infofiles ^
    --exclude-module IPG ^
    --hidden-import src.entry.launch_gui ^
    --hidden-import src.gui_app ^
    --hidden-import src.gui_app.app ^
    --hidden-import src.gui_app.main_window ^
    --hidden-import src.health.verify_runtime_chain_baseline ^
    "%SCRIPT_DIR%\..\src\entry\launch_gui.py" ^
    --upx-dir "%UPX_DIR%"

if !ERRORLEVEL! neq 0 (
    echo [ERR] Build failed
    pause
    exit /b 1
)

:: Move to final location (safely handle stale nested directory)
echo [2/2] Deploying to %DIST_DIR%...
if exist "%DIST_DIR%\CameraCalibration" (
    rmdir /s /q "%DIST_DIR%\CameraCalibration" 2>nul
    if exist "%DIST_DIR%\CameraCalibration" (
        ren "%DIST_DIR%\CameraCalibration" "CameraCalibration.old" 2>nul
    )
)
mkdir "%DIST_DIR%" 2>nul
move "%BUILD_DIR%\CameraCalibration" "%DIST_DIR%\CameraCalibration" >nul
if exist "%DIST_DIR%\CameraCalibration.old" rmdir /s /q "%DIST_DIR%\CameraCalibration.old" 2>nul
rmdir /s /q "%BUILD_DIR%" 2>nul

:: Create README
(
    echo Camera Calibration Console - v1.2.5
    echo =====================================
    echo.
    echo Build Date: %date% %time%
    echo.
    echo Usage:
    echo   Run CameraCalibration\CameraCalibration.exe
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
echo Output: %DIST_DIR%\CameraCalibration\CameraCalibration.exe
dir /s "%DIST_DIR%\CameraCalibration\" | find "File(s)"
