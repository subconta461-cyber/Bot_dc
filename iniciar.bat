@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Ambiente nao encontrado. Siga as instrucoes do README.md.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" dc.py
pause
