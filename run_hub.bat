@echo off
title Hub LED Tuya - Sincronizador Musical
cd /d "%~dp0"

if not exist .venv (
    echo [-] Pasta .venv nao encontrada!
    pause
    exit /b
)

echo [+] Abrindo o Hub...
.venv\Scripts\python.exe hub.py
