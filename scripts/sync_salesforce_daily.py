"""
Script Orquestador de Sincronización Diaria de Salesforce (Casos B2B y Presencia Omni-Channel)
Proyecto 4DX — AMC / LATAM Airlines
Autor: Antigravity / Carlos Murillo

Ejecuta el ciclo completo de actualización:
1. Descarga del reporte rodante de Presencia Omni-Channel (últimos 7 días).
2. Descarga del reporte de Casos B2B 2026 (Backlog, SLA 24h, Aging).
3. Procesamiento y calibración horaria (-2h) en salesforce_omni_engine.
4. Procesamiento de demanda y limpieza en salesforce_engine.
5. Registro de auditoría del proceso en data/salesforce/sync_history.log.

Uso:
    python scripts/sync_salesforce_daily.py               # Modo headless (automático)
    python scripts/sync_salesforce_daily.py --visible     # Modo visible (depuración)
    python scripts/sync_salesforce_daily.py --solo-omni   # Solo presencia Omni
    python scripts/sync_salesforce_daily.py --solo-casos  # Solo casos B2B
"""

import os
import sys
import time
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.normpath(os.path.join(BASE_DIR, ".."))
DATA_SF_DIR = os.path.join(PROJECT_DIR, "data", "salesforce")
LOG_PATH = os.path.join(DATA_SF_DIR, "sync_history.log")

sys.path.insert(0, BASE_DIR)
import salesforce_download_cases as sdc
import salesforce_omni_engine as soe
import salesforce_engine as sfe


def log_event(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        os.makedirs(DATA_SF_DIR, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def ejecutar_sincronizacion_completa(headless: bool = True, solo_omni: bool = False, solo_casos: bool = False):
    inicio = time.time()
    log_event("=" * 65)
    log_event("INICIANDO CICLO DE SINCRONIZACIÓN DIARIA SALESFORCE B2B")
    log_event(f"Modo: {'Headless' if headless else 'Visible'} | Solo Omni: {solo_omni} | Solo Casos: {solo_casos}")
    log_event("=" * 65)

    omni_ok = False
    casos_ok = False

    # 1. Presencia Omni-Channel
    if not solo_casos:
        log_event("[1/4] Descargando reporte rodante de Presencia Omni-Channel (7 días)...")
        try:
            ruta_omni = sdc.descargar_reporte_omni(headless=headless)
            if ruta_omni and os.path.exists(ruta_omni):
                log_event(f"[✓] Archivo Omni descargado exitosamente ({os.path.getsize(ruta_omni)/1024:.1f} KB)")
                omni_ok = True
            else:
                log_event("[!] Falló la descarga de Omni o no se detectó archivo nuevo. Usando base existente.")
        except Exception as e:
            log_event(f"[!] Error durante descarga Omni: {e}")

        log_event("[2/4] Procesando presencia Omni y calibrando zona horaria (-2h)...")
        try:
            df_omni = soe.procesar_omni_presencia_completa(forzar=True)
            log_event(f"[✓] Presencia Omni procesada: {len(df_omni)} registros diarios para {df_omni['bp'].nunique()} BPs.")
        except Exception as e:
            log_event(f"[!] Error procesando presencia Omni: {e}")

    # 2. Casos AMC B2B
    if not solo_omni:
        log_event("[3/4] Descargando reporte de Casos B2B AMC 2026...")
        try:
            ruta_casos = sdc.descargar_reporte_casos_2026(headless=headless)
            if ruta_casos and os.path.exists(ruta_casos):
                log_event(f"[✓] Reporte de Casos descargado exitosamente ({os.path.getsize(ruta_casos)/1024:.1f} KB)")
                casos_ok = True
            else:
                log_event("[!] Falló la descarga de Casos o no se detectó archivo nuevo. Usando base existente.")
        except Exception as e:
            log_event(f"[!] Error durante descarga Casos: {e}")

        log_event("[4/4] Limpiando datos de Casos y calculando SLA 24h / Backlog...")
        try:
            # Buscar el archivo más reciente en data/salesforce
            csv_path = os.path.join(DATA_SF_DIR, "casos_amc_2026_downloaded.csv")
            if os.path.exists(csv_path):
                df_casos = sfe.cargar_y_limpiar_casos(csv_path)
                sfe.guardar_casos_procesados(df_casos)
                log_event(f"[✓] Casos procesados exitosamente: {len(df_casos):,} casos en backlog y cerrados.")
        except Exception as e:
            log_event(f"[!] Error procesando casos: {e}")

    duracion = round(time.time() - inicio, 1)
    log_event("=" * 65)
    log_event(f"SINCRONIZACIÓN FINALIZADA EN {duracion} SEGUNDOS")
    log_event("=" * 65)


if __name__ == "__main__":
    is_visible = "--visible" in sys.argv
    is_solo_omni = "--solo-omni" in sys.argv
    is_solo_casos = "--solo-casos" in sys.argv
    ejecutar_sincronizacion_completa(
        headless=not is_visible,
        solo_omni=is_solo_omni,
        solo_casos=is_solo_casos
    )
