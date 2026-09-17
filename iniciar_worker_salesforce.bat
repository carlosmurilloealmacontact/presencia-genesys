@echo off
setlocal
cd /d "%~dp0"
title Worker Continuo Salesforce Omni-Supervisor (Cada 30 Segundos)

echo =======================================================================
echo          WORKER CONTINUO SALESFORCE (OMNI-SUPERVISOR)
echo =======================================================================
echo  - Frecuencia: Cada 30 segundos
echo  - Modo: Headless (Segundo plano / Invisible)
echo  - Extraccion: Resumen de retraso de colas y agentes en tiempo real
echo  - Destino: Base de datos local data\salesforce_live.db
echo =======================================================================
echo.

set PYTHONUTF8=1
python scripts\salesforce_continuous_worker.py
pause
