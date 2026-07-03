@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
set "PROJECT_DIR=%SCRIPT_DIR%\.."
set "EXIT_CODE=0"

echo ============================================
echo  Camera Calibration - Release Pipeline
echo ============================================
echo.

:: Step 1 - Unit tests
echo [1/5] Running syntax and import check...
cd /d "%PROJECT_DIR%"
python diagnostics/tmp_tools/syntax_check.py
set "RC=!ERRORLEVEL!"
echo [TRACE] step1 RC=!RC!
if !RC! neq 0 (
    echo [FAIL] Syntax check failed, aborting.
    set "EXIT_CODE=1"
    goto :end
)
echo [OK] Syntax check passed.
echo.

:: Step 2 - Package source distribution
echo [2/5] Packaging source distribution...
call "%SCRIPT_DIR%\package.bat"
set "RC=!ERRORLEVEL!"
echo [TRACE] step2 RC=!RC!
if !RC! neq 0 (
    echo [FAIL] Package failed, aborting.
    set "EXIT_CODE=1"
    goto :end
)
echo [OK] Package complete.
echo.

:: Step 3 - Setup virtual environment (using built-in venv, no uv/network needed)
echo [3/5] Setting up Python virtual environment...
cd /d "%SCRIPT_DIR%\cm_calib_dist"
python --version 2>&1
python -m venv .venv --system-site-packages
set "RC=!ERRORLEVEL!"
echo [TRACE] venv RC=!RC!
if !RC! neq 0 (
    echo [FAIL] venv creation failed.
    set "EXIT_CODE=1"
    goto :end
)
echo [OK] Virtual environment created (with system packages).
echo [3/5] Installing PyInstaller from user site-packages (no network)...
set "USER_SP=%APPDATA%\Python\Python310\site-packages"
set "VENV_SP=%SCRIPT_DIR%\cm_calib_dist\.venv\Lib\site-packages"
if exist "%USER_SP%\PyInstaller" (
    xcopy /e /i /y "%USER_SP%\PyInstaller" "%VENV_SP%\PyInstaller\" >nul 2>nul
    xcopy /e /i /y "%USER_SP%\pyinstaller-*.dist-info" "%VENV_SP%\pyinstaller-*.dist-info\" >nul 2>nul
    xcopy /e /i /y "%USER_SP%\_pyinstaller_hooks_contrib" "%VENV_SP%\_pyinstaller_hooks_contrib\" >nul 2>nul
    xcopy /e /i /y "%USER_SP%\pyinstaller_hooks_contrib-*.dist-info" "%VENV_SP%\pyinstaller_hooks_contrib-*.dist-info\" >nul 2>nul
    xcopy /e /i /y "%USER_SP%\altgraph" "%VENV_SP%\altgraph\" >nul 2>nul
    xcopy /e /i /y "%USER_SP%\altgraph-*.dist-info" "%VENV_SP%\altgraph-*.dist-info\" >nul 2>nul
    copy /y "%USER_SP%\pefile.py" "%VENV_SP%\pefile.py" >nul 2>nul
    xcopy /e /i /y "%USER_SP%\pefile-*.dist-info" "%VENV_SP%\pefile-*.dist-info\" >nul 2>nul
    xcopy /e /i /y "%USER_SP%\win32ctypes" "%VENV_SP%\win32ctypes\" >nul 2>nul
    xcopy /e /i /y "%USER_SP%\pywin32_ctypes-*.dist-info" "%VENV_SP%\pywin32_ctypes-*.dist-info\" >nul 2>nul
    echo [OK] PyInstaller copied from user site-packages.
) else (
    echo [WARN] PyInstaller not found at user site-packages, trying pip install...
    call .venv\Scripts\pip install --quiet pyinstaller 2>nul || echo [WARN] pip install pyinstaller failed.
)
echo [3/5] Installing CarMaker wheels...
.venv\Scripts\python.exe -m pip install --quiet "D:\IPG\carmaker\win64-15.1\Python\cmapi-15.1.0-*-win_amd64.whl" 2>nul
echo [OK] Dependencies ready.
echo.

:: Step 4 - Build EXE
echo [4/5] Compiling EXE...
call "%SCRIPT_DIR%\build_exe.bat"
set "RC=!ERRORLEVEL!"
echo [TRACE] step4 RC=!RC!
if !RC! neq 0 (
    echo [FAIL] EXE build failed, aborting.
    set "EXIT_CODE=1"
    goto :end
)
echo [OK] EXE built.
echo.

:: Step 5 - Calibration smoke test
echo [5/5] Running calibration smoke test...
cd /d "%PROJECT_DIR%"
"%SCRIPT_DIR%\cm_calib_dist\.venv\Scripts\python.exe" diagnostics/tmp_tools/smoke_test.py
set "RC=!ERRORLEVEL!"
echo [TRACE] step5 RC=!RC!
if !RC! neq 0 (
    echo [FAIL] Smoke test failed.
    set "EXIT_CODE=1"
    goto :end
)
echo [OK] Smoke test passed.
echo.

:end
echo ============================================
echo [TRACE] EXIT_CODE=!EXIT_CODE! ERRORLEVEL=!ERRORLEVEL!
if !EXIT_CODE! equ 0 (
    echo  Pipeline Complete - All steps passed!
) else (
    echo  Pipeline FAILED - see above for details.
)
echo ============================================
exit /b !EXIT_CODE!
