@echo off
title NIFTY Quant Desk - Live Mobile & Localhost Server
color 0A
cls
echo ===============================================================================
echo      STARTING NIFTY CHAMPION MODEL 4 QUANT DESK (MOBILE + LOCALHOST)
echo ===============================================================================
echo.
echo 1. Local URL:  http://localhost:5000
echo 2. Mobile URL: Check results\live_public_url.txt
echo.
echo Launching Web Server and Cloudflare Secure Tunnel...
start /B python app.py
timeout /t 3 /nobreak >nul
start /B cloudflared.exe tunnel --url http://localhost:5000
echo.
echo Server is running live in background!
pause
