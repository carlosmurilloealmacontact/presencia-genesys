@echo off
setlocal
cd /d "%~dp0"
title Worker Unificado: Zendesk (5 Min Fast / 60 Min Full) + Salesforce Live (30 Seg)

echo =======================================================================
echo     WORKER AUTONOMO UNIFICADO: ZENDESK + SALESFORCE OMNI-CHANNEL
echo =======================================================================
echo  - Zendesk Fast Sync: En vivo cada 5 min (Backlog y Productividad)
echo  - Zendesk Full Sync: Corte profundo cada 60 min (Matrices y Demanda)
echo  - Salesforce Omni-Supervisor: Monitoreo continuo en vivo cada 30 segundos
echo  - Destino: data\salesforce_live.db y data\zendesk\sync_status.json
echo  - Blindaje: Auto-recuperacion y Git push automatico
echo =======================================================================
echo.

set PYTHONUTF8=1
python -u scripts\zendesk_hourly_worker.py
pause
