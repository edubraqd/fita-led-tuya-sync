@echo off
title Sincronizador Spotify -> Fita LED
color 0b
echo ==================================================
echo      Iniciando Sincronizador Spotify LED...
echo ==================================================
echo.

:: Mudar para o diretorio do arquivo bat
cd /d "%~dp0"

:: Verificar se a pasta do ambiente virtual existe
if not exist .venv (
    echo [-] Erro: Pasta .venv nao encontrada!
    echo Certifique-se de que o projeto foi instalado corretamente.
    pause
    exit /b
)

echo [+] Ativando ambiente virtual (.venv)...
call .venv\Scripts\activate.bat

echo [+] Iniciando sincronizador de audio...
python spotify_sync.py

echo.
echo ==================================================
echo      Programa finalizado.
echo ==================================================
pause
