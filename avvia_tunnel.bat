@echo off
title Nino Online - Cloudflare Tunnel
echo ========================================================
echo   AVVIO TUNNEL ONLINE PER NINO (SO FOOD)
echo ========================================================
echo.
echo Assicurati che app.py sia avviato nell'altro terminale!
echo.
"%~dp0cloudflared.exe" tunnel --url http://127.0.0.1:5000
pause
