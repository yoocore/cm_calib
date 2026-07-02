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

:: Step 1 — Unit tests
echo [1/5] Running syntax and import check...
cd /d "%PROJECT_DIR%"
python diagnostics/tmp_tools/syntax_check.py
if !ERRORLEVEL! neq 0 (
    echo [FAIL] Syntax check failed, aborting.
    set "EXIT_CODE=1"
    goto :end
)
echo [OK] Syntax check passed.
echo.

:: Step 2 — Package source distribution
echo [2/5] Packaging source distribution...
cd /d "%SCRIPT_DIR%"
call package.bat
if !ERRORLEVEL! neq 0 (
    echo [FAIL] Package failed, aborting.
    set "EXIT_CODE=1"
    goto :end
)
echo [OK] Package complete.
echo.

:: Step 3 — Setup environment
echo [3/5] Setting up Python virtual environment...
cd /d "%SCRIPT_DIR%\cm_calib_dist"
:: Inject "n" answers for any pause prompts, run in quiet tee mode
echo n | call setup.bat
if !ERRORLEVEL! neq 0 (
    echo [FAIL] Setup failed, aborting.
    set "EXIT_CODE=1"
    goto :end
)
echo [OK] Environment ready.
echo.

:: Step 4 — Build EXE
echo [4/5] Compiling EXE...
cd /d "%SCRIPT_DIR%"
call build_exe.bat
if !ERRORLEVEL! neq 0 (
    echo [FAIL] EXE build failed, aborting.
    set "EXIT_CODE=1"
    goto :end
)
echo [OK] EXE built.
echo.

:: Step 5 — Calibration smoke test
echo [5/5] Running calibration smoke test...
cd /d "%PROJECT_DIR%"
python diagnostics/tmp_tools/smoke_test.py
if !ERRORLEVEL! neq 0 (
    echo [FAIL] Smoke test failed.
    set "EXIT_CODE=1"
    goto :end
)
echo [OK] Smoke test passed.
echo.

:end
echo ============================================
if !EXIT_CODE! equ 0 (
    echo  Pipeline Complete - All steps passed!
) else (
    echo  Pipeline FAILED - see above for details.
)
echo ============================================
exit /b !EXIT_CODE!
