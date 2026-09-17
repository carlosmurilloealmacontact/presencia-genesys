@echo off
setlocal
cd /d "%~dp0"
title Inicio de Sesion Asistido Salesforce (Omni-Supervisor)

echo =======================================================================
echo        INICIO DE SESION ASISTIDO SALESFORCE (OMNI-SUPERVISOR)
echo =======================================================================
echo  - Abre el navegador Chromium para renovar la sesion de Salesforce.
echo  - Pasa el codigo de verificacion (si te lo solicita).
echo  - Navega a la pestana 'Resumen de retraso de colas' en Omni-Supervisor.
echo  - Presiona ENTER en la consola para guardar la sesion de forma permanente.
echo =======================================================================
echo.

set PYTHONUTF8=1
python scripts\salesforce_login_helper.py
pause
