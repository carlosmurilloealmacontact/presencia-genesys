"""
Worker Autónomo en Segundo Plano: Zendesk Support Sync Service
Ejecuta la extracción horaria desatendida de Zendesk:
1. Sincroniza Backlog en Vivo y Productividad del día (Playwright SSO/GridSure).
2. Transfiere los CSVs procesados a data/zendesk/.
3. Recalcula la Demanda Diaria por Cola (Inflow vs Outflow) en hora Colombia (UTC-5).
4. Anexa de forma idempotente la productividad del día al histórico consolidado.
5. Emite un archivo de control (sync_status.json) para que el Dashboard muestre el estado del último corte sin latencia.
"""

import os
import sys
import time
import json
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_ZD_DIR = PROJECT_ROOT / "data" / "zendesk"
DATA_ZD_DIR.mkdir(parents=True, exist_ok=True)

ZENDESK_MODULE_DIR = Path(r"C:\Proyecto 3.0\Zendesk")
ZENDESK_PROCESSED_DIR = ZENDESK_MODULE_DIR / "data" / "processed"
SYNC_SCRIPT_PATH = ZENDESK_MODULE_DIR / "zendesk_sync_service.py"
STATUS_FILE = DATA_ZD_DIR / "sync_status.json"


def get_colombia_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=-5)))


def actualizar_historico_productividad(df_hoy: pd.DataFrame) -> None:
    """Anexa de forma segura la productividad del día de hoy al archivo histórico en disco."""
    file_hist = DATA_ZD_DIR / "productividad_diaria_fechas.csv"
    if df_hoy is None or df_hoy.empty:
        return

    try:
        df_diario_raw = pd.read_csv(file_hist) if file_hist.exists() else pd.DataFrame()
        grp_cols = ["Fecha", "grupo", "TICKET_ASSIGNEE_PRIMARY_EMAIL", "Tipo_de_Gestion"] if "grupo" in df_hoy.columns else ["Fecha", "TICKET_ASSIGNEE_PRIMARY_EMAIL", "Tipo_de_Gestion"]
        
        df_hoy_agg = df_hoy.groupby(grp_cols).size().reset_index(name="Recuento_Tickets")
        df_hoy_agg["Fecha_Timestamp"] = df_hoy_agg["Fecha"] + " 00:00:00"
        df_hoy_agg["Tipo_de_Gestion_RAW"] = df_hoy_agg["Tipo_de_Gestion"]

        if not df_diario_raw.empty and "Fecha" in df_diario_raw.columns:
            fechas_hoy = df_hoy_agg["Fecha"].unique()
            df_diario_raw = df_diario_raw[~df_diario_raw["Fecha"].isin(fechas_hoy)]
            df_consolidado = pd.concat([df_diario_raw, df_hoy_agg], ignore_index=True)
        else:
            df_consolidado = df_hoy_agg

        df_consolidado.to_csv(file_hist, index=False, encoding="utf-8-sig")
        print(f"  [HISTÓRICO] Actualizado {file_hist.name} con {len(df_consolidado)} registros totales.")
    except Exception as e:
        print(f"  [WARN] Error anexando al histórico: {e}")


def recalcular_demanda():
    """Ejecuta generar_demanda_diaria con el entorno configurado adecuadamente."""
    if ZENDESK_MODULE_DIR.exists():
        if str(ZENDESK_MODULE_DIR) not in sys.path:
            sys.path.insert(0, str(ZENDESK_MODULE_DIR))
        try:
            from generar_demanda_diaria import generar_demanda_diaria
            df_dem = generar_demanda_diaria()
            print(f"  [DEMANDA] Recalculada exitosamente: {len(df_dem)} registros.")
            return True
        except Exception as ex:
            print(f"  [WARN] Error calculando demanda diaria: {ex}")
    return False


def ejecutar_corte_horario_zendesk() -> dict:
    """Ejecuta un ciclo completo de sincronización de Zendesk de forma limpia y desatendida."""
    now_col = get_colombia_now()
    ts_str = now_col.strftime("%Y-%m-%d %H:%M:%S")
    ts_label = now_col.strftime("%d/%m/%Y %I:%M:%S %p")
    print(f"\n=======================================================")
    print(f"[{ts_str}] Iniciando corte horario Zendesk...")
    print(f"=======================================================")

    if not SYNC_SCRIPT_PATH.exists():
        msg = f"Script de sincronización no encontrado en {SYNC_SCRIPT_PATH}"
        print(f"[ERROR] {msg}")
        return {"status": "error", "message": msg}

    import subprocess
    cmd = [sys.executable, "-u", str(SYNC_SCRIPT_PATH)]
    inicio = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ZENDESK_MODULE_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180
        )
        duracion = round(time.time() - inicio, 1)

        # Buscar resultado JSON en stdout
        res_data = None
        for line in proc.stdout.splitlines():
            if "RESULTADO_JSON:" in line:
                try:
                    res_data = json.loads(line.split("RESULTADO_JSON:")[-1].strip())
                except Exception:
                    pass

        if proc.returncode == 0 and res_data and res_data.get("status") == "ok":
            # 1. Copiar todos los archivos procesados de Zendesk a data/zendesk/
            if ZENDESK_PROCESSED_DIR.exists():
                for f in ZENDESK_PROCESSED_DIR.glob("*.*"):
                    shutil.copy2(f, DATA_ZD_DIR / f.name)
                print(f"  [SYNC] Archivos transferidos a {DATA_ZD_DIR}")

            # 2. Anexar al histórico consolidado
            f_hoy = DATA_ZD_DIR / "productividad_hoy_en_vivo.csv"
            if f_hoy.exists():
                try:
                    df_hoy = pd.read_csv(f_hoy)
                    actualizar_historico_productividad(df_hoy)
                except Exception as ex:
                    print(f"  [WARN] Error procesando hoy: {ex}")

            # 3. Recalcular Demanda Diaria (Inflow vs Outflow)
            recalcular_demanda()

            # 4. Guardar archivo de estado para el Dashboard
            backlog_c = res_data.get("backlog_count", 0)
            solved_c = res_data.get("solved_count", 0)
            status_payload = {
                "status": "ok",
                "timestamp": ts_str,
                "timestamp_label": ts_label,
                "duracion_seg": duracion,
                "backlog_count": backlog_c,
                "solved_today_count": solved_c,
                "next_sync_est": (now_col + timedelta(hours=1)).strftime("%I:%M %p")
            }
            with open(STATUS_FILE, "w", encoding="utf-8") as f:
                json.dump(status_payload, f, ensure_ascii=False, indent=2)

            print(f"[{ts_str}] ✅ Corte horario finalizado con éxito en {duracion}s: {backlog_c} backlog, {solved_c} resueltos hoy.")
            return status_payload
        else:
            err_msg = res_data.get("error") if res_data else f"Exit code {proc.returncode}: {proc.stderr[:200]}"
            print(f"[ERROR] Sincronización falló: {err_msg}")
            return {"status": "error", "error": err_msg}

    except subprocess.TimeoutExpired:
        print("[ERROR] Tiempo límite de 180s excedido al consultar Zendesk.")
        return {"status": "error", "error": "TimeoutExpired (180s)"}
    except Exception as e:
        print(f"[ERROR] Excepción inesperada: {e}")
        return {"status": "error", "error": str(e)}


def iniciar_demonio_horario(intervalo_segundos: int = 3600):
    """Bucle infinito para ejecución autónoma desatendida como servicio de fondo."""
    print(f"🚀 Iniciando Demonio Horario Zendesk (Intervalo: {intervalo_segundos}s / {intervalo_segundos//60} min)...")
    while True:
        try:
            ejecutar_corte_horario_zendesk()
        except Exception as ex:
            print(f"[CRITICAL] Error en ciclo del demonio: {ex}")
        print(f"💤 Esperando {intervalo_segundos//60} minutos para el próximo corte...")
        time.sleep(intervalo_segundos)


if __name__ == "__main__":
    if "--loop" in sys.argv or "-d" in sys.argv:
        iniciar_demonio_horario(3600)
    else:
        ejecutar_corte_horario_zendesk()
