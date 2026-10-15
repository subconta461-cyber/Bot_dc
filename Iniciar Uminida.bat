@echo off
cd /d "%~dp0"
set "UMINIDA_PY="
for %%E in (.venv-1 .venv64 .venv) do (
    if not defined UMINIDA_PY if exist "%%E\Scripts\python.exe" (
        "%%E\Scripts\python.exe" -c "import struct,discord,davey,aiohttp,yt_dlp; assert struct.calcsize('P') == 8" >nul 2>&1
        if not errorlevel 1 set "UMINIDA_PY=%%E\Scripts\python.exe"
    )
)
if not defined UMINIDA_PY (
    echo Nao encontrei um ambiente Python de 64 bits com as dependencias do bot.
    echo Use o ambiente que ja funciona para executar dc.py.
    pause
    exit /b 1
)
echo Uminida - Seu cantinho de musica
echo Painel: http://127.0.0.1:8765
echo Mantenha esta janela aberta enquanto usa o bot.
"%UMINIDA_PY%" dc.py
pause
