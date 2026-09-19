@echo off
title Calibracao Bluetooth (mic) - Fita LED
cd /d "%~dp0"
if not exist .venv ( echo [-] .venv nao encontrada! & pause & exit /b )
call .venv\Scripts\activate.bat
python bt_calibrate.py
pause
