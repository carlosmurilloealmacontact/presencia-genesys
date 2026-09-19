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
    """Anexa de forma segura la productividad del día de hoy tanto al acumulado diario como al parquet histórico."""
    file_hist = DATA_ZD_DIR / "productividad_diaria_fechas.csv"
    file_hist_zd = ZENDESK_PROCESSED_DIR / "productividad_diaria_fechas.csv"
    file_pq = DATA_ZD_DIR / "productividad_historica_2026.parquet"
    file_pq_zd = ZENDESK_PROCESSED_DIR / "productividad_historica_2026.parquet"

    if df_hoy is None or df_hoy.empty:
        return

    # 1. Actualizar acumulado diario CSV
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
        if ZENDESK_PROCESSED_DIR.exists():
            df_consolidado.to_csv(file_hist_zd, index=False, encoding="utf-8-sig")
        print(f"  [HISTÓRICO] Actualizado {file_hist.name} con {len(df_consolidado)} registros totales.")
    except Exception as e:
        print(f"  [WARN] Error anexando al histórico diario: {e}")

    # 2. Actualizar Parquet histórico detallado
    try:
        target_pq = file_pq if file_pq.exists() else file_pq_zd
        if target_pq.exists() and "id" in df_hoy.columns:
            df_pq = pd.read_parquet(target_pq)
            cols_pq = [c for c in df_pq.columns if c in df_hoy.columns]
            df_pq_upd = pd.concat([df_pq, df_hoy[cols_pq]], ignore_index=True)
            df_pq_upd.drop_duplicates(subset=["id"], keep="last", inplace=True)
            df_pq_upd.sort_values(by=["Fecha", "updated_at"], inplace=True)
            df_pq_upd.to_parquet(file_pq, index=False)
            if ZENDESK_PROCESSED_DIR.exists():
                df_pq_upd.to_parquet(file_pq_zd, index=False)
            print(f"  [HISTÓRICO PARQUET] Actualizado {file_pq.name} con {len(df_pq_upd)} tickets totales.")
    except Exception as e_pq:
        print(f"  [WARN] Error actualizando histórico parquet: {e_pq}")


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
        sf_live = PROJECT_ROOT / "data" / "salesforce_live.db"
        if sf_live.exists():
            rutas.append("data/salesforce_live.db")

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

            # 3. Recalcular Demanda Diaria (Inflow vs Outflow)
            recalcular_demanda()
            if tipo == "full":
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


def obtener_procesos_wmi(filtro_cmd: str, ignorar_pid: int = None) -> list:
    """Consulta procesos de Windows usando WMI COM nativo en 20ms sin llamar a PowerShell."""
    pids = []
    try:
        import win32com.client
        import pythoncom
        pythoncom.CoInitialize()
        wmi = win32com.client.GetObject("winmgmts:")
        procs = wmi.ExecQuery("Select ProcessId, CommandLine from Win32_Process")
        for p in procs:
            cmd = str(getattr(p, "CommandLine", "") or "")
            pid = getattr(p, "ProcessId", 0)
            if filtro_cmd.lower() in cmd.lower() and "python.exe" in cmd.lower():
                if ignorar_pid is not None and pid == ignorar_pid:
                    continue
                pids.append(pid)
    except Exception:
        pass
    return pids


_sf_process = None


def asegurar_salesforce_worker_activo():
    """
    Verifica si salesforce_continuous_worker.py está corriendo en el sistema.
    Si no está activo, lo inicia automáticamente como subproceso desatendido e independiente.
    Garantiza auto-recuperación y monitoreo continuo 24/7 sin intervención humana.
    """
    global _sf_process
    try:
        if _sf_process is not None and _sf_process.poll() is None:
            return _sf_process

        pids_activos = obtener_procesos_wmi("salesforce_continuous_worker", ignorar_pid=os.getpid())
        if pids_activos:
            return None

        sf_script = str(PROJECT_ROOT / "scripts" / "salesforce_continuous_worker.py")
        if os.path.exists(sf_script):
            creation_flag = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            _sf_process = subprocess.Popen(
                [sys.executable, "-u", sf_script],
                cwd=str(PROJECT_ROOT),
                creationflags=creation_flag
            )
            print(f"  [SF LIVE] ✅ Proceso autónomo de Salesforce Omni-Supervisor iniciado (PID {_sf_process.pid}, cada 30s).")
            return _sf_process
    except Exception as ex:
        print(f"  [SF LIVE] [WARN] Error supervisando proceso de Salesforce: {ex}")
    return None


LAST_DAILY_SYNC_FILE = PROJECT_ROOT / "data" / "last_daily_sync.txt"


def verificar_y_ejecutar_sincronizacion_diaria():
    """
    Verifica si ya se ejecutó la sincronización y auditoría diaria (Genesys + Ausentismo + Casos).
    Si no se ha ejecutado hoy, la dispara de forma desatendida en segundo plano.
    """
    try:
        now_col = get_colombia_now()
        hoy_str = now_col.strftime("%Y-%m-%d")

        ultima_fecha = ""
        if LAST_DAILY_SYNC_FILE.exists():
            try:
                ultima_fecha = LAST_DAILY_SYNC_FILE.read_text(encoding="utf-8").strip()
            except Exception:
                pass

        if ultima_fecha != hoy_str:
            print(f"\n[DAILY MASTER] 🌅 Disparando chequeo y actualización automática diaria para {hoy_str}...")
            from threading import Thread

            def _run_bg():
                try:
                    daily_script = PROJECT_ROOT / "scripts" / "daily_master_sync.py"
                    if daily_script.exists():
                        res = subprocess.run([sys.executable, str(daily_script)], cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=900)
                        if res.returncode == 0:
                            LAST_DAILY_SYNC_FILE.write_text(hoy_str, encoding="utf-8")
                            print(f"[DAILY MASTER] ✅ Sincronización y auditoría diaria completada exitosamente ({hoy_str}).")
                        else:
                            print(f"[DAILY MASTER] [WARN] Sincronización diaria retornó código {res.returncode}.")
                            try:
                                from telegram_notifier import notificar_alerta
                                notificar_alerta("Sincronización Diaria Fallida", res.stderr or res.stdout, contexto="daily_master_sync.py vía zendesk_hourly_worker")
                            except Exception:
                                pass
                except Exception as e_d:
                    print(f"[DAILY MASTER] [WARN] Excepción en sincronización diaria: {e_d}")
                    try:
                        from telegram_notifier import notificar_alerta
                        notificar_alerta("Excepción en Sincronización Diaria", str(e_d), contexto="daily_master_sync.py vía zendesk_hourly_worker")
                    except Exception:
                        pass

            Thread(target=_run_bg, daemon=True).start()
    except Exception as e:
        print(f"[DAILY MASTER] [WARN] Error evaluando sincronización diaria: {e}")


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
    zd_pids = obtener_procesos_wmi("zendesk_hourly_worker", ignorar_pid=pid_actual)
    if zd_pids:
        print(f"  [AVISO] Ya existe otra instancia activa de zendesk_hourly_worker (PID {zd_pids[0]}). Saliendo para evitar colisiones.")
        return

    print(f"🚀 Iniciando Demonio Híbrido Autónomo:")
    print(f"   • Zendesk Fast Sync: cada {intervalo_fast_segundos // 60} min (Backlog en tiempo real + Productividad hoy)")
    print(f"   • Zendesk Full Sync: cada {ciclos_para_full * (intervalo_fast_segundos // 60)} min (Dump profundo, Tipologías, Demanda Inflow/Outflow)")
    print(f"   • Salesforce Omni-Supervisor: Monitoreo continuo 30s")
    print(f"   • Sincronizador Maestro Diario: Genesys + Ausentismo + Casos B2B (Auto-check 06:00 COT)")
    print(f"   • Blindaje: Reintento automático, limpieza de bloqueos y persistencia en Windows Task Scheduler.")

    asegurar_salesforce_worker_activo()
    verificar_y_ejecutar_sincronizacion_diaria()

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
            # Supervisar que el scraper de Salesforce esté vivo y saludable
            asegurar_salesforce_worker_activo()
            verificar_y_ejecutar_sincronizacion_diaria()

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
