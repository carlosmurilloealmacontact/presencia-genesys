@echo off
chcp 65001 > nul
title Radar Operacional - Sincronizacion y Auditoria Diaria
cd /d "%~dp0"
echo ======================================================================
echo INICIANDO CHEQUEO Y ACTUALIZACION AUTOMATICA DIARIA
echo ======================================================================
python scripts\daily_master_sync.py
echo.
echo Presione cualquier tecla para salir...
pause > nul
