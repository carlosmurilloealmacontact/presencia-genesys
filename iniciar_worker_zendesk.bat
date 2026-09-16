@echo off
setlocal
cd /d "%~dp0"
title Worker Horario Zendesk Support (Cada 60 Minutos)

echo =======================================================================
echo          WORKER HORARIO AUTONOMO ZENDESK SUPPORT
echo =======================================================================
echo  - Frecuencia: Cada 60 minutos
echo  - Extraccion: Playwright SSO GridSure (Backlog y Productividad)
echo  - Demanda: Recalculo automatico diario por cola (UTC-5)
echo  - Dashboard: Sincronizacion Zero-Lag via sync_status.json
echo =======================================================================
echo.

set PYTHONUTF8=1
python scripts\zendesk_hourly_worker.py --loop
pause
