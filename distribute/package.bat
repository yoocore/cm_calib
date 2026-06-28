@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

set DIST_DIR=dist
set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
set "LOG_FILE=%SCRIPT_DIR%\package.log"
set "DIST_SRC=%SCRIPT_DIR%"
set "PROJECT_DIR=%SCRIPT_DIR%\.."

echo ============================================
echo  Camera Calibration Console - Package Script
echo ============================================
echo.

:: Clean previous dist
if exist "%DIST_DIR%" (
    echo [1/5] Cleaning previous dist...
    rmdir /s /q "%DIST_DIR%"
)

echo [2/5] Creating dist structure...
mkdir "%DIST_DIR%"
mkdir "%DIST_DIR%\docs"
mkdir "%DIST_DIR%\src"

echo [3/5] Copying files...
copy /y "%DIST_SRC%\setup.bat" "%DIST_DIR%\" >nul
copy /y "%DIST_SRC%\cleanup.bat" "%DIST_DIR%\" >nul
copy /y "%DIST_SRC%\uv.exe" "%DIST_DIR%\" >nul
copy /y "%PROJECT_DIR%\docs\requirements.txt" "%DIST_DIR%\docs\" >nul

echo [4/5] Copying src (excluding tests and cache)...
robocopy "%PROJECT_DIR%\src" "%DIST_DIR%\src" /E /XD __pycache__ .pytest_cache tests *.pyc /XF *.pyc /NFL /NDL /NJH /NJS

echo [5/5] Creating dist info file...
(
    echo Camera Calibration Console - v1.2.4
    echo =====================================
    echo.
    echo Build Date: %date% %time%
    echo Git Tag: v1.2.4
    echo.
    echo Contents:
    echo   - setup.bat      : Environment setup script
    echo   - cleanup.bat    : Environment cleanup script
    echo   - uv.exe         : Package manager
    echo   - src/           : Source code
    echo   - docs/          : Documentation and requirements
    echo.
    echo Usage:
    echo   1. Run setup.bat to configure environment
    echo   2. Run run.bat to launch GUI
    echo   3. Run cleanup.bat when finished
) > "%DIST_DIR%\README.txt"

echo.
echo ============================================
echo  Package Complete: %DIST_DIR%\
echo ============================================
echo.
echo Contents:
dir "%DIST_DIR%"

:: Save log
echo %date% %time% - Package complete > "%LOG_FILE%"
dir "%DIST_DIR%" >> "%LOG_FILE%"

echo.
echo Log saved to: %LOG_FILE%
echo Next steps:
echo   - Test the package in a clean environment
echo   - Zip the dist folder for distribution
echo.
pause

endlocal
