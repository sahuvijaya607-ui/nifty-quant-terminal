@echo off
setlocal EnableDelayedExpansion
title Nifty Quant Desk - GitHub & Render Deployer
echo ======================================================================
echo       NIFTY QUANT DESK (VIKALP 2) - 1-CLICK GITHUB & RENDER DEPLOYER
echo ======================================================================
echo.

set "GIT_EXE=C:\Users\user\.git_portable\cmd\git.exe"
if not exist "!GIT_EXE!" (
    set "GIT_EXE=git"
)

echo Repository Status:
"!GIT_EXE!" status --short
echo.

set /p "REPO_URL=Enter your GitHub Repo URL (e.g. https://github.com/username/nifty-desk.git): "

if "!REPO_URL!"=="" (
    echo [ERROR] No GitHub URL entered. Please re-run and enter your repo link.
    pause
    exit /b 1
)

echo.
echo [1/3] Configuring remote origin...
"!GIT_EXE!" remote remove origin >nul 2>&1
"!GIT_EXE!" remote add origin !REPO_URL!

echo [2/3] Setting branch to main...
"!GIT_EXE!" branch -M main

echo [3/3] Pushing all 13 verified deployment files to GitHub...
"!GIT_EXE!" push -u origin main --force

if !errorlevel! equ 0 (
    echo.
    echo ======================================================================
    echo  [SUCCESS] PUSH COMPLETED TO GITHUB!
    echo  Render.com will now automatically detect the update and deploy 24/7!
    echo ======================================================================
) else (
    echo.
    echo [NOTE] If GitHub asked for authentication, sign in via browser or personal access token.
)

pause
