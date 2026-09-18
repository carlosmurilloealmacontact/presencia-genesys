@echo off
setlocal
cd /d "%~dp0"
title Dashboard Radar Operacional 4DX - Streamlit Local
echo =======================================================================
echo          RADAR OPERACIONAL 4DX - SEGUIMIENTO DE PRESENCIA
echo =======================================================================
echo  - Iniciando servidor local en http://localhost:8501 ...
echo  - Presione Ctrl+C para detener el servidor.
echo =======================================================================
echo.

set PYTHONUTF8=1
python -m streamlit run scripts\viewer.py --server.port 8501
pause
