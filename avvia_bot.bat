@echo off
title Nino - Bot So Food
echo ========================================================
echo   AVVIO SERVER NINO (SO FOOD)
echo ========================================================
echo.
cd /d "%~dp0"
call "%~dp0.venv\Scripts\activate.bat"
python "%~dp0app.py"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERRORE] Il server si e' interrotto con un errore.
    pause
)
