@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
set "VENV=%SCRIPT_DIR%\cm_calib_dist\.venv"
set "PYTHON=%VENV%\Scripts\python.exe"
set "DIST_DIR=%SCRIPT_DIR%\dist_exe"

echo ============================================
echo  Building Camera Calibration EXE
echo ============================================
echo.

if not exist "!PYTHON!" (
    echo [ERR] venv not found, run setup.bat first
    pause
    exit /b 1
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
    --workpath "%TEMP%\pyibuild" ^
    --specpath "%TEMP%\pyispec" ^
    --add-data "%SCRIPT_DIR%\..\src\gui_app\icon.svg;src\gui_app" ^
    --add-data "%SCRIPT_DIR%\..\src\gui_app\checkbox_checked.svg;src\gui_app" ^
    --add-data "%SCRIPT_DIR%\..\src\gui_app\dropdown_arrow.svg;src\gui_app" ^
    --add-data "%SCRIPT_DIR%\..\src\gui_app\arrow_up.svg;src\gui_app" ^
    --add-data "%SCRIPT_DIR%\..\src\gui_app\arrow_down.svg;src\gui_app" ^
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
    "%SCRIPT_DIR%\..\src\entry\launch_gui.py"

if !ERRORLEVEL! neq 0 (
    echo [ERR] Build failed
    pause
    exit /b 1
)

:: Move to final location
if exist "%DIST_DIR%" rmdir /s /q "%DIST_DIR%" 2>nul
mkdir "%DIST_DIR%" 2>nul
move "%BUILD_DIR%\CameraCalibration" "%DIST_DIR%\CameraCalibration" >nul
rmdir /s /q "%BUILD_DIR%" 2>nul
rmdir /s /q "%TEMP%\pyibuild" 2>nul
rmdir /s /q "%TEMP%\pyispec" 2>nul

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
