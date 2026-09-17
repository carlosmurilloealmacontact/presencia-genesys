@echo off
setlocal
cd /d "%~dp0"
title Worker Unificado: Zendesk (60 Min) + Salesforce Live (30 Seg)

echo =======================================================================
echo     WORKER AUTONOMO UNIFICADO: ZENDESK + SALESFORCE OMNI-CHANNEL
echo =======================================================================
echo  - Zendesk Support: Corte horario cada 60 min (Backlog, Productividad, Demanda)
echo  - Salesforce Omni-Supervisor: Monitoreo continuo en vivo cada 30 segundos
echo  - Destino: data\salesforce_live.db y data\zendesk\sync_status.json
echo  - Dashboard: Sincronizacion Multicanal Zero-Lag en tiempo real
echo =======================================================================
echo.

set PYTHONUTF8=1
python scripts\zendesk_hourly_worker.py --loop
pause
