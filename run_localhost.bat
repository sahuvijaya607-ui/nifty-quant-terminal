@echo off
title Champion Model 4 - Web Quant Desk (Localhost:5000)
color 0B
cls
echo ===============================================================================
echo      STARTING CHAMPION MODEL 4 QUANT DESK ON LOCALHOST:5000
echo ===============================================================================
echo.
echo Launching default browser at http://localhost:5000 ...
start http://localhost:5000
echo.
echo Running Flask server (Press Ctrl+C to stop)...
python app.py
pause
