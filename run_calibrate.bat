@echo off
title Calibracao por Webcam - Fita LED
cd /d "%~dp0"
if not exist .venv ( echo [-] .venv nao encontrada! & pause & exit /b )
call .venv\Scripts\activate.bat
python calibrate.py
pause
