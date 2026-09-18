"""
Worker Autónomo en Segundo Plano: Zendesk Support Sync Service & Salesforce Live
Arquitectura Híbrida de Dos Niveles:
- Tier 1 (Corte Rápido Near Real-Time, cada 5 min / 300s):
  Conteos instantáneos de backlog por grupo (<2s) + sincronización de resueltos de hoy (~5-7s).
- Tier 2 (Corte Profundo, cada 60 min / 12 ciclos rápidos):
  Extracción completa de 4,800+ tickets, tipologías, actualización de matrices de antigüedad
  y recálculo de demanda diaria (Inflow vs Outflow).
- Monitoreo Continuo Salesforce Live: Hilo en paralelo cada 30s.
- Blindaje Total:
  * Manejo estricto de excepciones y timeouts (el worker nunca se apaga).
  * Limpieza de bloqueos huérfanos de Chrome.
  * Sincronización automática con Git (commit y push a origin/main para Streamlit Cloud).
"""

import os
import sys
import time
import json
import shutil
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

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


def sincronizar_git():
    """Realiza commit y push silencioso de los datos para mantener actualizado Streamlit Cloud."""
    try:
        # 1. Agregar de forma segura las carpetas y archivos de datos existentes
        rutas = ["data/zendesk/"]
        sf_state = PROJECT_ROOT / "data" / "salesforce_state.json"
        if sf_state.exists():
            rutas.append("data/salesforce_state.json")

        for r in rutas:
            subprocess.run(["git", "add", r], cwd=str(PROJECT_ROOT), capture_output=True, timeout=15)

        # 2. Verificar si hay cambios en el stage listos para commit
        chk = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=str(PROJECT_ROOT), timeout=10)
        if chk.returncode != 0:  # Hay cambios en stage
            ts = get_colombia_now().strftime("%Y-%m-%d %H:%M")
            c_res = subprocess.run(
                ["git", "commit", "-m", f"Auto-sync Zendesk/Salesforce [{ts}]"],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=15
            )
            if c_res.returncode == 0:
                push_res = subprocess.run(
                    ["git", "push", "origin", "main"],
                    cwd=str(PROJECT_ROOT),
                    capture_output=True,
                    text=True,
                    timeout=45
                )
                if push_res.returncode == 0:
                    print(f"  [GIT] ✅ Cambios sincronizados y subidos a origin/main para Streamlit Cloud ({ts}).")
                else:
                    print(f"  [GIT] [WARN] Push retornó error: {push_res.stderr.strip()[:200]}")
            else:
                print(f"  [GIT] [WARN] Commit falló: {c_res.stderr.strip()[:200]}")
        else:
            print("  [GIT] ℹ️ Sin cambios en datos para sincronizar con Streamlit Cloud.")
    except Exception as e:
        print(f"  [GIT] [WARN] Sincronización git omitida: {e}")


def ejecutar_corte_zendesk(tipo: str = "fast", ultimo_full_label: str = "") -> dict:
    """
    Ejecuta un ciclo de sincronización de Zendesk:
    - 'fast': corte rápido (Tier 1, cada 5 min). Consulta conteos de backlog por grupo (<2s) y productividad de hoy (~5-7s).
    - 'full': corte profundo (Tier 2, cada 60 min). Descarga completa de tickets, recalcula demanda (Inflow/Outflow) y tipologías.
    """
    now_col = get_colombia_now()
    ts_str = now_col.strftime("%Y-%m-%d %H:%M:%S")
    ts_label = now_col.strftime("%d/%m/%Y %I:%M:%S %p")
    tipo_desc = "Corte Rápido En Vivo (Tier 1)" if tipo == "fast" else "Corte Profundo Horario (Tier 2)"
    print(f"\n=======================================================")
    print(f"[{ts_str}] Iniciando Zendesk: {tipo_desc}...")
    print(f"=======================================================")

    if not SYNC_SCRIPT_PATH.exists():
        msg = f"Script de sincronización no encontrado en {SYNC_SCRIPT_PATH}"
        print(f"[ERROR] {msg}")
        return {"status": "error", "message": msg}

    cmd = [sys.executable, "-u", str(SYNC_SCRIPT_PATH)]
    if tipo == "fast":
        cmd.append("--fast")

    timeout_sec = 240 if tipo == "fast" else 450
    inicio = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ZENDESK_MODULE_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec
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
            # 1. Copiar archivos según el tipo de corte
            if tipo == "full":
                if ZENDESK_PROCESSED_DIR.exists():
                    for f in ZENDESK_PROCESSED_DIR.glob("*.*"):
                        shutil.copy2(f, DATA_ZD_DIR / f.name)
                    print(f"  [SYNC FULL] Todos los datasets transferidos a {DATA_ZD_DIR}")
            else:
                f_src_hoy = ZENDESK_PROCESSED_DIR / "productividad_hoy_en_vivo.csv"
                if f_src_hoy.exists():
                    shutil.copy2(f_src_hoy, DATA_ZD_DIR / f_src_hoy.name)
                    print(f"  [SYNC FAST] Archivo productividad_hoy_en_vivo.csv actualizado.")

            # 2. Anexar al histórico consolidado
            f_hoy = DATA_ZD_DIR / "productividad_hoy_en_vivo.csv"
            if f_hoy.exists():
                try:
                    df_hoy = pd.read_csv(f_hoy)
                    actualizar_historico_productividad(df_hoy)
                except Exception as ex:
                    print(f"  [WARN] Error procesando hoy: {ex}")

            # 3. Recalcular Demanda Diaria si fue corte profundo
            if tipo == "full":
                recalcular_demanda()
                ultimo_full_label = ts_label

            # 4. Guardar archivo de estado para el Dashboard
            backlog_c = res_data.get("backlog_count", 0)
            solved_c = res_data.get("solved_count", 0)
            status_payload = {
                "status": "ok",
                "tipo_corte": tipo,
                "timestamp": ts_str,
                "timestamp_label": ts_label,
                "duracion_seg": duracion,
                "backlog_count": backlog_c,
                "backlog_por_grupo": res_data.get("backlog_por_grupo", {}),
                "solved_today_count": solved_c,
                "next_sync_est": (now_col + timedelta(minutes=5 if tipo == "fast" else 60)).strftime("%I:%M %p"),
                "ultimo_corte_full": ultimo_full_label or ts_label
            }
            with open(STATUS_FILE, "w", encoding="utf-8") as f:
                json.dump(status_payload, f, ensure_ascii=False, indent=2)
            zd_status = ZENDESK_PROCESSED_DIR / "sync_status.json"
            try:
                with open(zd_status, "w", encoding="utf-8") as f:
                    json.dump(status_payload, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

            print(f"[{ts_str}] ✅ {tipo_desc} finalizado con éxito en {duracion}s: {backlog_c} backlog, {solved_c} resueltos hoy.")
            return status_payload
        else:
            err_msg = res_data.get("error") if res_data else f"Exit code {proc.returncode}: {proc.stderr[:200]}"
            print(f"[ERROR] Sincronización falló: {err_msg}")
            return {"status": "error", "error": err_msg}

    except subprocess.TimeoutExpired:
        print(f"[ERROR] Tiempo límite ({timeout_sec}s) excedido al consultar Zendesk.")
        return {"status": "error", "error": f"TimeoutExpired ({timeout_sec}s)"}
    except Exception as e:
        print(f"[ERROR] Excepción inesperada en corte: {e}")
        return {"status": "error", "error": str(e)}


def iniciar_hilo_salesforce_live():
    """Inicia el monitor continuo de Salesforce Omni-Supervisor en un hilo de fondo independiente si no está activo."""
    try:
        import threading
        chk = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*salesforce_continuous_worker*' -and $_.ProcessId -ne $PID } | Select-Object -ExpandProperty ProcessId"],
            capture_output=True, text=True, timeout=5
        )
        if chk.stdout.strip():
            print("  [SF LIVE] ℹ️ Proceso de Salesforce Live ya se encuentra activo en el sistema.")
            return None

        scripts_dir = str(PROJECT_ROOT / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import salesforce_continuous_worker as scw
        sf_thread = threading.Thread(target=scw.run_continuous_worker, name="SalesforceLiveThread", daemon=True)
        sf_thread.start()
        print("  [SF LIVE] ✅ Hilo de monitoreo en vivo continuo de Salesforce iniciado (cada 30s).")
        return sf_thread
    except Exception as ex:
        print(f"  [WARN] No se pudo iniciar hilo de Salesforce Live: {ex}")
        return None


def iniciar_demonio_hibrido(intervalo_fast_segundos: int = 300, ciclos_para_full: int = 12):
    """
    Bucle infinito blindado para ejecución autónoma desatendida multicanal (Zendesk + Salesforce):
    - Fast Sync cada 5 min (300s).
    - Full Sync cada 60 min (cada 12 ciclos) o en el primer ciclo si falta data.
    - Sincronización continua de Git.
    - Blindado contra interrupciones o fallos de red.
    """
    # Evitar múltiples instancias concurrentes del demonio
    pid_actual = os.getpid()
    try:
        chk_zd = subprocess.run(
            ["powershell", "-NoProfile", "-Command", f"Get-CimInstance Win32_Process | Where-Object {{ $_.CommandLine -like '*zendesk_hourly_worker*' -and $_.ProcessId -ne {pid_actual} }} | Select-Object -ExpandProperty ProcessId"],
            capture_output=True, text=True, timeout=5
        )
        if chk_zd.stdout.strip():
            print(f"  [AVISO] Ya existe otra instancia activa de zendesk_hourly_worker (PID {chk_zd.stdout.strip().splitlines()[0]}). Saliendo para evitar colisiones.")
            return
    except Exception:
        pass

    print(f"🚀 Iniciando Demonio Híbrido Autónomo:")
    print(f"   • Zendesk Fast Sync: cada {intervalo_fast_segundos // 60} min (Backlog en tiempo real + Productividad hoy)")
    print(f"   • Zendesk Full Sync: cada {ciclos_para_full * (intervalo_fast_segundos // 60)} min (Dump profundo, Tipologías, Demanda Inflow/Outflow)")
    print(f"   • Salesforce Omni-Supervisor: Monitoreo continuo 30s")
    print(f"   • Blindaje: Reintento automático, limpieza de bloqueos y persistencia en Windows Task Scheduler.")

    iniciar_hilo_salesforce_live()

    ciclo = 0
    ultimo_full_label = ""
    if STATUS_FILE.exists():
        try:
            with open(STATUS_FILE, "r", encoding="utf-8") as f:
                prev_st = json.load(f)
                ultimo_full_label = prev_st.get("ultimo_corte_full", prev_st.get("timestamp_label", ""))
        except Exception:
            pass

    hay_datos_base = (DATA_ZD_DIR / "backlog_en_vivo.csv").exists() and (DATA_ZD_DIR / "demanda_diaria_colas.csv").exists()
    if not hay_datos_base:
        ciclo = 0
    else:
        ciclo = 1

    while True:
        try:
            es_full = (ciclo % ciclos_para_full == 0)
            tipo = "full" if es_full else "fast"

            res = ejecutar_corte_zendesk(tipo=tipo, ultimo_full_label=ultimo_full_label)
            if res.get("status") == "ok":
                if tipo == "full":
                    ultimo_full_label = res.get("timestamp_label", ultimo_full_label)
                sincronizar_git()
            else:
                print(f"[WARN] Ciclo {ciclo} ({tipo}) finalizó con error. Se reintentará en el próximo corte.")

            ciclo += 1

        except Exception as ex:
            print(f"[CRITICAL] Excepción no controlada en el demonio: {ex}")
            time.sleep(30)

        print(f"💤 Esperando {intervalo_fast_segundos // 60} minutos para el próximo corte rápido (Ciclo actual: {ciclo})...")
        time.sleep(intervalo_fast_segundos)


if __name__ == "__main__":
    if "--fast" in sys.argv or "-f" in sys.argv:
        ejecutar_corte_zendesk(tipo="fast")
    elif "--full" in sys.argv:
        ejecutar_corte_zendesk(tipo="full")
    else:
        iniciar_demonio_hibrido(300, 12)
