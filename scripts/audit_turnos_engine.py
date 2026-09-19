"""
Motor de Auditoría y Sincronización de Cambios de Turnos WFM (Almaverso API).
Rastrea, detecta y audita todas las modificaciones posteriores a la publicación oficial:
- Cambios de horario (hora_inicio / hora_fin)
- Cambios de novedad (TUR <-> DES, Incapacidades, Permisos, Licencias)
- Reprogramación de pausas (Lunch / Descansos)
- Nuevos turnos incorporados post-corte

Garantiza que el cálculo de Adherencia, Ausentismo y Outliers en el Radar Operacional
se mantenga 100% fiel a la programación real y no penalice a los asesores injustamente.
"""

import os
import sys
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd
from dotenv import load_dotenv, find_dotenv

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

load_dotenv(find_dotenv())
env_parent = Path(__file__).resolve().parent.parent.parent / ".env"
if env_parent.exists():
    load_dotenv(env_parent)

SCRIPTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DB_PRESENCIA = PROJECT_ROOT / "data" / "presencia.db"
DB_MASTER = PROJECT_ROOT / "data" / "presencia_master.db"
CSV_AUDITORIA_HISTORICA = PROJECT_ROOT / "data" / "auditoria_cambios_turnos.csv"

# Importar funciones de extracción y guardado con fallback
try:
    from db import get_connection, guardar_turnos, guardar_turnos_detallados, SCHEMA
    from jerarquia import load_cedula_a_bp
    from config import CLOUD_EXPORT_PATH
except ImportError:
    from scripts.db import get_connection, guardar_turnos, guardar_turnos_detallados, SCHEMA
    from scripts.jerarquia import load_cedula_a_bp
    from scripts.config import CLOUD_EXPORT_PATH

try:
    from extract_turnos_api import get_token, fetch_shifts_range, parse_api_shifts, _clean_time
except Exception:
    try:
        from scripts.extract_turnos_api import get_token, fetch_shifts_range, parse_api_shifts, _clean_time
    except Exception:
        pass


SCHEMA_AUDITORIA_TURNOS = """
CREATE TABLE IF NOT EXISTS auditoria_cambios_turnos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha_auditoria TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    bp TEXT NOT NULL,
    documento TEXT DEFAULT '',
    nombre_agente TEXT DEFAULT '',
    servicio TEXT DEFAULT '',
    fecha_turno TEXT NOT NULL,
    tipo_cambio TEXT NOT NULL, 
    campo_modificado TEXT NOT NULL,
    valor_anterior TEXT DEFAULT '',
    valor_nuevo TEXT DEFAULT '',
    impacto_adherencia TEXT DEFAULT '',
    origen TEXT DEFAULT 'API_ALMAVERSO'
);
CREATE INDEX IF NOT EXISTS idx_act_fecha_aud ON auditoria_cambios_turnos(fecha_auditoria);
CREATE INDEX IF NOT EXISTS idx_act_fecha_tur ON auditoria_cambios_turnos(fecha_turno);
CREATE INDEX IF NOT EXISTS idx_act_bp ON auditoria_cambios_turnos(bp);
CREATE INDEX IF NOT EXISTS idx_act_tipo ON auditoria_cambios_turnos(tipo_cambio);
CREATE INDEX IF NOT EXISTS idx_act_servicio ON auditoria_cambios_turnos(servicio);
"""


def obtener_ventana_ciclo_4dx(today=None, min_dias_atras: int = 3, dias_adelante: int = 7) -> tuple[str, str]:
    """
    Calcula la ventana adaptativa de auditoría y descarga de turnos:
    - Retrocede como mínimo `min_dias_atras` (por defecto 3 días para auditar D-1, D-2 y D-3).
    - Cubre desde el inicio del Ciclo 4DX activo del mes:
      * Ciclo 1: días 01 a 07
      * Ciclo 2: días 08 a 15
      * Ciclo 3: días 16 a 23
      * Ciclo 4: días 24 a fin de mes
    - Hacia adelante: cubre `dias_adelante` (por defecto 7 días).
    Garantiza que cualquier modificación hecha por WFM en el ciclo activo se audite y concilie diariamente.
    """
    if today is None:
        today = datetime.now().date()
    elif isinstance(today, str):
        today = datetime.strptime(today, "%Y-%m-%d").date()

    dia = today.day
    if dia <= 7:
        dia_ini_ciclo = 1
    elif dia <= 15:
        dia_ini_ciclo = 8
    elif dia <= 23:
        dia_ini_ciclo = 16
    else:
        dia_ini_ciclo = 24

    fecha_ini_ciclo = today.replace(day=dia_ini_ciclo)
    fecha_min_retroceso = today - timedelta(days=min_dias_atras)

    fecha_inicio = min(fecha_ini_ciclo, fecha_min_retroceso)
    fecha_fin = today + timedelta(days=dias_adelante)

    return fecha_inicio.strftime("%Y-%m-%d"), fecha_fin.strftime("%Y-%m-%d")


def inicializar_tabla_auditoria(conn: sqlite3.Connection = None) -> None:
    """Crea la tabla de auditoría en la conexión provista o en ambas bases (master y presencia.db)."""
    if conn:
        conn.executescript(SCHEMA_AUDITORIA_TURNOS)
        conn.commit()
        return

    # Master
    if DB_MASTER.exists():
        with sqlite3.connect(DB_MASTER) as c_master:
            c_master.executescript(SCHEMA_AUDITORIA_TURNOS)
            c_master.commit()

    # Presencia pública
    if DB_PRESENCIA.exists():
        with sqlite3.connect(DB_PRESENCIA) as c_pres:
            c_pres.executescript(SCHEMA_AUDITORIA_TURNOS)
            c_pres.commit()


def sembrar_historico_si_vacio(conn: sqlite3.Connection = None) -> int:
    """
    Si la tabla auditoria_cambios_turnos está vacía, importa el archivo
    histórico data/auditoria_cambios_turnos.csv para mantener la trazabilidad previa.
    """
    inicializar_tabla_auditoria(conn)
    
    target_conn = conn if conn else sqlite3.connect(DB_PRESENCIA)
    close_after = conn is None
    
    try:
        cur = target_conn.cursor()
        cur.execute("SELECT COUNT(*) FROM auditoria_cambios_turnos")
        count = cur.fetchone()[0]
        if count > 0:
            return 0  # Ya tiene datos
            
        if not CSV_AUDITORIA_HISTORICA.exists():
            return 0

        df_hist = pd.read_csv(CSV_AUDITORIA_HISTORICA)
        if df_hist.empty:
            return 0

        # Cargar nombres si están en dim_agentes
        map_nombres = {}
        try:
            for r in cur.execute("SELECT agente_id, agente, servicio FROM dim_agentes").fetchall():
                map_nombres[str(r[0])] = (str(r[1]), str(r[2]))
        except Exception:
            pass

        rows_insert = []
        ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for _, row in df_hist.iterrows():
            bp_str = str(row.get("bp", "")).strip()
            fecha_str = str(row.get("fecha", "")).strip()
            ini_ant = str(row.get("hora_inicio_anterior", "") or "").strip()
            ini_nue = str(row.get("hora_inicio_nueva", "") or "").strip()
            fin_ant = str(row.get("hora_fin_anterior", "") or "").strip()
            fin_nue = str(row.get("hora_fin_nueva", "") or "").strip()

            val_ant = f"{ini_ant} - {fin_ant}".strip(" -")
            val_nue = f"{ini_nue} - {fin_nue}".strip(" -")

            nombre, servicio = map_nombres.get(bp_str, ("", ""))

            impacto = "Horario de turno modificado post-publicación. Evita falsos desvíos de adherencia."
            if ini_ant and ini_nue and ini_ant != ini_nue:
                if ini_nue > ini_ant:
                    impacto = f"Inicio pospuesto ({ini_ant} ➔ {ini_nue}). Previene marcar falso retraso matutino."
                else:
                    impacto = f"Inicio adelantado ({ini_ant} ➔ {ini_nue}). Reconoce conexión temprana en adherencia."

            rows_insert.append((
                ahora,
                bp_str,
                bp_str,
                nombre,
                servicio,
                fecha_str,
                "CAMBIO_HORARIO",
                "horario",
                val_ant,
                val_nue,
                impacto,
                "MIGRACION_HISTORICA_CSV",
            ))

        cur.executemany("""
            INSERT INTO auditoria_cambios_turnos (
                fecha_auditoria, bp, documento, nombre_agente, servicio,
                fecha_turno, tipo_cambio, campo_modificado, valor_anterior,
                valor_nuevo, impacto_adherencia, origen
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows_insert)
        target_conn.commit()
        return len(rows_insert)
    finally:
        if close_after:
            target_conn.close()


def cargar_turnos_db_para_comparar(conn: sqlite3.Connection, fecha_min: str, fecha_max: str):
    """Carga los turnos básicos y detallados existentes en SQLite en un rango de fechas."""
    cur = conn.cursor()
    
    # 1. Turnos básicos
    cur.execute("""
        SELECT bp, fecha, hora_inicio, hora_fin 
        FROM turnos 
        WHERE fecha >= ? AND fecha <= ?
    """, (fecha_min, fecha_max))
    turnos_dict = {}
    for bp, fecha, h_ini, h_fin in cur.fetchall():
        turnos_dict[(str(bp), str(fecha))] = {
            "hora_inicio": str(h_ini) if h_ini else "",
            "hora_fin": str(h_fin) if h_fin else "",
        }

    # 2. Turnos detallados
    detallados_dict = {}
    try:
        cur.execute("""
            SELECT bp, fecha, documento, nombre_agente, servicio, novedad, 
                   horas_programadas, turno_ini, turno_fin, dialogo_ini, dialogo_fin,
                   des_1_ini, des_1_fin, lunch_ini, lunch_fin
            FROM turnos_detallados
            WHERE fecha >= ? AND fecha <= ?
        """, (fecha_min, fecha_max))
        for r in cur.fetchall():
            detallados_dict[(str(r[0]), str(r[1]))] = {
                "documento": str(r[2]) if r[2] else "",
                "nombre_agente": str(r[3]) if r[3] else "",
                "servicio": str(r[4]) if r[4] else "",
                "novedad": str(r[5]) if r[5] else "",
                "horas_programadas": float(r[6]) if r[6] else 0.0,
                "turno_ini": str(r[7]) if r[7] else "",
                "turno_fin": str(r[8]) if r[8] else "",
                "dialogo_ini": str(r[9]) if r[9] else "",
                "dialogo_fin": str(r[10]) if r[10] else "",
                "des_1_ini": str(r[11]) if r[11] else "",
                "des_1_fin": str(r[12]) if r[12] else "",
                "lunch_ini": str(r[13]) if r[13] else "",
                "lunch_fin": str(r[14]) if r[14] else "",
            }
    except Exception:
        pass

    return turnos_dict, detallados_dict


def auditar_diferencias(raw_shifts: list[dict], cedula_a_bp: dict, existing_turnos: dict, existing_detallados: dict):
    """
    Compara los turnos provenientes de la API contra la base de datos existente
    y genera:
    1. rows_turnos (listo para upsert)
    2. rows_detallados (listo para upsert)
    3. rows_auditoria (registros de cambios detectados)
    """
    rows_turnos, rows_detallados = parse_api_shifts(raw_shifts, cedula_a_bp)
    
    rows_auditoria = []
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Mapeo rápido de detallados entrantes por (bp, fecha)
    detallados_in_map = {(str(d["bp"]), str(d["fecha"])): d for d in rows_detallados}

    # 1. Comparar horarios en rows_turnos
    for t in rows_turnos:
        key = (str(t["bp"]), str(t["fecha"]))
        h_ini_new = t["hora_inicio"]
        h_fin_new = t["hora_fin"]
        
        info_det = detallados_in_map.get(key, {})
        doc = info_det.get("documento", "")
        nombre = info_det.get("nombre_agente", "")
        servicio = info_det.get("servicio", "")

        if key not in existing_turnos:
            # Turno totalmente nuevo
            rows_auditoria.append({
                "fecha_auditoria": ahora,
                "bp": t["bp"],
                "documento": doc,
                "nombre_agente": nombre,
                "servicio": servicio,
                "fecha_turno": t["fecha"],
                "tipo_cambio": "TURNO_NUEVO",
                "campo_modificado": "horario",
                "valor_anterior": "-- Sin turno previo --",
                "valor_nuevo": f"{h_ini_new} - {h_fin_new}",
                "impacto_adherencia": "Nuevo turno programado post-publicación oficial. Incorporado al cálculo.",
                "origen": "API_ALMAVERSO",
            })
        else:
            curr_db = existing_turnos[key]
            h_ini_old = curr_db["hora_inicio"]
            h_fin_old = curr_db["hora_fin"]

            if h_ini_old != h_ini_new or h_fin_old != h_fin_new:
                # Determinar impacto analítico
                impacto = f"Horario modificado de {h_ini_old}-{h_fin_old} a {h_ini_new}-{h_fin_new}."
                if h_ini_old != h_ini_new:
                    if h_ini_new > h_ini_old:
                        impacto = f"Inicio pospuesto ({h_ini_old} ➔ {h_ini_new}). Evita penalizar como tardanza las horas matutinas."
                    else:
                        impacto = f"Inicio adelantado ({h_ini_old} ➔ {h_ini_new}). Reconoce conexión temprana en la adherencia real."

                rows_auditoria.append({
                    "fecha_auditoria": ahora,
                    "bp": t["bp"],
                    "documento": doc,
                    "nombre_agente": nombre,
                    "servicio": servicio,
                    "fecha_turno": t["fecha"],
                    "tipo_cambio": "CAMBIO_HORARIO",
                    "campo_modificado": "horario",
                    "valor_anterior": f"{h_ini_old} - {h_fin_old}",
                    "valor_nuevo": f"{h_ini_new} - {h_fin_new}",
                    "impacto_adherencia": impacto,
                    "origen": "API_ALMAVERSO",
                })

    # 2. Comparar novedades y pausas en rows_detallados
    for d in rows_detallados:
        key = (str(d["bp"]), str(d["fecha"]))
        if key not in existing_detallados:
            continue

        curr_det = existing_detallados[key]
        doc = d.get("documento", "")
        nombre = d.get("nombre_agente", "")
        servicio = d.get("servicio", "")

        # Novedad (ej: TUR -> DES, TUR -> INC, DES -> TUR)
        nov_old = (curr_det.get("novedad") or "").strip().upper()
        nov_new = (d.get("novedad") or "").strip().upper()

        if nov_old and nov_new and nov_old != nov_new:
            impacto_nov = f"Novedad reclasificada de {nov_old} a {nov_new}."
            if "DES" in nov_new or "DESCANSO" in nov_new:
                impacto_nov = f"Turno transformado en Descanso ({nov_new}). Se exonera de adherencia y previene falso ausentismo."
            elif any(x in nov_new for x in ("INC", "INCAPACIDAD", "PER", "PERMISO", "LIC")):
                impacto_nov = f"Novedad médica/permiso aplicada ({nov_new}). Justifica inasistencia operacional."
            elif nov_old in ("DES", "DESCANSO") and "TUR" in nov_new:
                impacto_nov = f"Descanso reactivado como Turno Operativo ({nov_new}). Se activa seguimiento de cumplimiento."

            rows_auditoria.append({
                "fecha_auditoria": ahora,
                "bp": d["bp"],
                "documento": doc,
                "nombre_agente": nombre,
                "servicio": servicio,
                "fecha_turno": d["fecha"],
                "tipo_cambio": "CAMBIO_NOVEDAD",
                "campo_modificado": "novedad",
                "valor_anterior": nov_old,
                "valor_nuevo": nov_new,
                "impacto_adherencia": impacto_nov,
                "origen": "API_ALMAVERSO",
            })

        # Pausa Almuerzo
        lunch_old = curr_det.get("lunch_ini")
        lunch_new = d.get("lunch_ini")
        if lunch_old != lunch_new and (lunch_old or lunch_new):
            rows_auditoria.append({
                "fecha_auditoria": ahora,
                "bp": d["bp"],
                "documento": doc,
                "nombre_agente": nombre,
                "servicio": servicio,
                "fecha_turno": d["fecha"],
                "tipo_cambio": "CAMBIO_PAUSA_ALMUERZO",
                "campo_modificado": "lunch_ini",
                "valor_anterior": lunch_old or "-- Sin almuerzo --",
                "valor_nuevo": lunch_new or "-- Eliminado --",
                "impacto_adherencia": f"Almuerzo reprogramado de {lunch_old or 'N/A'} a {lunch_new or 'N/A'}. Ajusta ventana de tolerancia.",
                "origen": "API_ALMAVERSO",
            })

        # Pausa Descanso 1
        des_old = curr_det.get("des_1_ini")
        des_new = d.get("des_1_ini")
        if des_old != des_new and (des_old or des_new):
            rows_auditoria.append({
                "fecha_auditoria": ahora,
                "bp": d["bp"],
                "documento": doc,
                "nombre_agente": nombre,
                "servicio": servicio,
                "fecha_turno": d["fecha"],
                "tipo_cambio": "CAMBIO_PAUSA_DESCANSO",
                "campo_modificado": "des_1_ini",
                "valor_anterior": des_old or "-- Sin descanso --",
                "valor_nuevo": des_new or "-- Eliminado --",
                "impacto_adherencia": f"Descanso 1 reprogramado de {des_old or 'N/A'} a {des_new or 'N/A'}.",
                "origen": "API_ALMAVERSO",
            })

    return rows_turnos, rows_detallados, rows_auditoria


def ejecutar_auditoria_y_sync_turnos(
    dias_atras: int = None,
    dias_adelante: int = 7,
    fecha_ini_custom: str = None,
    fecha_fin_custom: str = None
) -> dict:
    """
    Ejecuta el ciclo completo de auditoría y sincronización:
    1. Si no se especifican días, usa la ventana adaptativa del Ciclo 4DX (mínimo 3 días atrás + ciclo activo).
    2. Descarga turnos vigentes desde API Almaverso para la ventana [fecha_ini, fecha_fin].
    3. Compara contra turnos y turnos_detallados en base de datos.
    4. Registra todas las diferencias en auditoria_cambios_turnos.
    5. Aplica el upsert en presencia_master.db y presencia.db.
    6. Limpia los caches de Streamlit para reflejo inmediato.
    """
    today = datetime.now().date()
    if fecha_ini_custom and fecha_fin_custom:
        fecha_ini = fecha_ini_custom
        fecha_fin = fecha_fin_custom
    elif dias_atras is not None:
        fecha_ini = (today - timedelta(days=dias_atras)).strftime("%Y-%m-%d")
        fecha_fin = (today + timedelta(days=dias_adelante)).strftime("%Y-%m-%d")
    else:
        # Por defecto: Ciclo 4DX activo + mínimo 3 días atrás (cubre D-1, D-2, D-3 y todo el ciclo actual)
        fecha_ini, fecha_fin = obtener_ventana_ciclo_4dx(today, min_dias_atras=3, dias_adelante=dias_adelante)

    inicializar_tabla_auditoria()
    sembrar_historico_si_vacio()

    # 1. Cargar datos existentes en SQLite
    conn_pres = sqlite3.connect(DB_PRESENCIA)
    existing_turnos, existing_detallados = cargar_turnos_db_para_comparar(conn_pres, fecha_ini, fecha_fin)
    conn_pres.close()

    # 2. Descargar de API
    token = get_token()
    cedula_a_bp = load_cedula_a_bp()
    raw_shifts = fetch_shifts_range(fecha_ini, fecha_fin, token)

    if not raw_shifts:
        return {
            "ok": True,
            "fecha_inicio": fecha_ini,
            "fecha_fin": fecha_fin,
            "total_api": 0,
            "nuevos": 0,
            "cambios_horario": 0,
            "cambios_novedad": 0,
            "cambios_pausas": 0,
            "total_cambios": 0,
            "mensaje": "La API no retornó registros para el rango consultado.",
        }

    # 3. Auditar diferencias
    rows_turnos, rows_detallados, rows_auditoria = auditar_diferencias(
        raw_shifts, cedula_a_bp, existing_turnos, existing_detallados
    )

    # 4. Guardar cambios en auditoria_cambios_turnos
    if rows_auditoria:
        for db_file in (DB_MASTER, DB_PRESENCIA):
            if db_file.exists():
                with sqlite3.connect(db_file) as c_db:
                    c_db.executemany("""
                        INSERT INTO auditoria_cambios_turnos (
                            fecha_auditoria, bp, documento, nombre_agente, servicio,
                            fecha_turno, tipo_cambio, campo_modificado, valor_anterior,
                            valor_nuevo, impacto_adherencia, origen
                        ) VALUES (
                            :fecha_auditoria, :bp, :documento, :nombre_agente, :servicio,
                            :fecha_turno, :tipo_cambio, :campo_modificado, :valor_anterior,
                            :valor_nuevo, :impacto_adherencia, :origen
                        )
                    """, rows_auditoria)
                    c_db.commit()

    # 5. Sincronizar / Upsert en bases de datos SQLite
    conn_master = get_connection()
    if rows_turnos:
        guardar_turnos(conn_master, rows_turnos)
    if rows_detallados:
        guardar_turnos_detallados(conn_master, rows_detallados)
    conn_master.close()

    if DB_PRESENCIA.exists():
        with sqlite3.connect(DB_PRESENCIA) as c_p:
            if rows_turnos:
                guardar_turnos(c_p, rows_turnos)
            if rows_detallados:
                guardar_turnos_detallados(c_p, rows_detallados)

    # Intentar limpiar caches en Streamlit si está corriendo
    try:
        import streamlit as st
        st.cache_data.clear()
    except Exception:
        pass

    # Conteo por tipo de cambio
    c_nuevos = sum(1 for r in rows_auditoria if r["tipo_cambio"] == "TURNO_NUEVO")
    c_horario = sum(1 for r in rows_auditoria if r["tipo_cambio"] == "CAMBIO_HORARIO")
    c_novedad = sum(1 for r in rows_auditoria if r["tipo_cambio"] == "CAMBIO_NOVEDAD")
    c_pausas = sum(1 for r in rows_auditoria if "PAUSA" in r["tipo_cambio"])

    return {
        "ok": True,
        "fecha_inicio": fecha_ini,
        "fecha_fin": fecha_fin,
        "total_api": len(raw_shifts),
        "turnos_validos": len(rows_turnos),
        "nuevos": c_nuevos,
        "cambios_horario": c_horario,
        "cambios_novedad": c_novedad,
        "cambios_pausas": c_pausas,
        "total_cambios": len(rows_auditoria),
        "mensaje": f"Sincronización y auditoría finalizada: {len(rows_auditoria)} modificaciones detectadas y actualizadas.",
    }


def obtener_resumen_kpis_auditoria() -> dict:
    """Obtiene métricas ejecutivas de la tabla auditoria_cambios_turnos."""
    inicializar_tabla_auditoria()
    sembrar_historico_si_vacio()
    
    if not DB_PRESENCIA.exists():
        return {}

    with sqlite3.connect(DB_PRESENCIA) as conn:
        cur = conn.cursor()
        total = cur.execute("SELECT COUNT(*) FROM auditoria_cambios_turnos").fetchone()[0]
        horarios = cur.execute("SELECT COUNT(*) FROM auditoria_cambios_turnos WHERE tipo_cambio = 'CAMBIO_HORARIO'").fetchone()[0]
        novedades = cur.execute("SELECT COUNT(*) FROM auditoria_cambios_turnos WHERE tipo_cambio = 'CAMBIO_NOVEDAD'").fetchone()[0]
        pausas = cur.execute("SELECT COUNT(*) FROM auditoria_cambios_turnos WHERE tipo_cambio LIKE '%PAUSA%'").fetchone()[0]
        nuevos = cur.execute("SELECT COUNT(*) FROM auditoria_cambios_turnos WHERE tipo_cambio = 'TURNO_NUEVO'").fetchone()[0]
        
        row_ult = cur.execute("SELECT MAX(fecha_auditoria) FROM auditoria_cambios_turnos").fetchone()
        ult_auditoria = row_ult[0] if row_ult and row_ult[0] else "Sin registros"

        row_rango = cur.execute("SELECT MIN(fecha_turno), MAX(fecha_turno) FROM auditoria_cambios_turnos").fetchone()
        f_min = row_rango[0] if row_rango and row_rango[0] else "--"
        f_max = row_rango[1] if row_rango and row_rango[1] else "--"

    return {
        "total": total,
        "horarios": horarios,
        "novedades": novedades,
        "pausas": pausas,
        "nuevos": nuevos,
        "ultima_auditoria": ult_auditoria,
        "rango_turnos": f"{f_min} a {f_max}",
    }


def consultar_historial_auditoria_turnos(
    fecha_min: str = None,
    fecha_max: str = None,
    tipo_cambio: str = None,
    busqueda: str = None,
    limit: int = 500
) -> pd.DataFrame:
    """Consulta la tabla de auditoría con filtros analíticos."""
    inicializar_tabla_auditoria()
    sembrar_historico_si_vacio()

    if not DB_PRESENCIA.exists():
        return pd.DataFrame()

    query = """
        SELECT 
            id,
            fecha_auditoria AS [Fecha Detección],
            fecha_turno AS [Fecha Turno],
            bp AS [BP],
            nombre_agente AS [Asesor],
            servicio AS [Servicio],
            tipo_cambio AS [Tipo de Modificación],
            valor_anterior AS [Valor Anterior],
            valor_nuevo AS [Valor Nuevo],
            impacto_adherencia AS [Impacto en Adherencia],
            origen AS [Origen]
        FROM auditoria_cambios_turnos
        WHERE 1=1
    """
    params = []

    if fecha_min:
        query += " AND fecha_turno >= ?"
        params.append(fecha_min)
    if fecha_max:
        query += " AND fecha_turno <= ?"
        params.append(fecha_max)
    if tipo_cambio and tipo_cambio != "Todos":
        query += " AND tipo_cambio = ?"
        params.append(tipo_cambio)
    if busqueda and busqueda.strip():
        term = f"%{busqueda.strip()}%"
        query += " AND (nombre_agente LIKE ? OR bp LIKE ? OR servicio LIKE ? OR impacto_adherencia LIKE ?)"
        params.extend([term, term, term, term])

    query += " ORDER BY fecha_auditoria DESC, fecha_turno DESC LIMIT ?"
    params.append(limit)

    with sqlite3.connect(DB_PRESENCIA) as conn:
        df = pd.read_sql_query(query, conn, params=params)

    return df


def render_panel_auditoria_turnos():
    """Renderiza el módulo interactivo de Auditoría de Cambios de Turnos en Streamlit."""
    import streamlit as st

    st.markdown(
        """
        <div style="background: linear-gradient(135deg, #1e1e38 0%, #0f172a 100%); padding: 18px 22px; border-radius: 12px; margin-bottom: 20px; border-left: 5px solid #6366f1;">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px;">
                <div>
                    <h3 style="color: #ffffff; margin: 0 0 5px 0; font-size: 21px; display: flex; align-items: center; gap: 10px;">
                        <span>🔄</span> Auditoría de Cambios de Turno WFM
                    </h3>
                    <p style="color: #94a3b8; margin: 0; font-size: 13px;">
                        Trazabilidad forense de modificaciones en la malla horaria oficial: Cambios de horario, descansos (DES), incapacidades y pausas reprogramadas.
                    </p>
                </div>
                <div style="background: rgba(99, 102, 241, 0.15); border: 1px solid rgba(99, 102, 241, 0.3); border-radius: 8px; padding: 6px 14px; text-align: right;">
                    <span style="color: #a5b4fc; font-size: 11px; font-weight: 700; text-transform: uppercase;">Garantía de Adherencia</span><br>
                    <span style="color: #e0e7ff; font-size: 12px; font-weight: 600;">Blindaje Anti-Falsos Desvíos</span>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    # 1. Métricas Principales
    kpis = obtener_resumen_kpis_auditoria()
    
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("Total Cambios Auditados", f"{kpis.get('total', 0):,}", help="Total de modificaciones de turno detectadas y sincronizadas.")
    with col2:
        st.metric("Cambios de Horario", f"{kpis.get('horarios', 0):,}", help="Turnos con ajuste en hora de inicio o fin.")
    with col3:
        st.metric("Novedades Reclasificadas", f"{kpis.get('novedades', 0):,}", help="Modificaciones entre TUR, DES (descanso), e Incapacidades.")
    with col4:
        st.metric("Pausas Reprogramadas", f"{kpis.get('pausas', 0):,}", help="Variaciones en horarios de almuerzo o descansos.")
    with col5:
        st.metric("Turnos Nuevos Post-Corte", f"{kpis.get('nuevos', 0):,}", help="Turnos incorporados después de la programación inicial.")

    st.markdown("---")

    # 2. Control de Ejecución en Vivo de Auditoría
    with st.expander("⚡ Ejecución de Auditoría y Sincronización WFM (En Tiempo Real)", expanded=False):
        st.markdown(
            """
            Esta herramienta se conecta directamente a la API de Almaverso (`/API/GET-EXPORT/SHIFTS`),
            compara la programación actual contra la almacenada en la base de datos, detecta cualquier desvío
            o modificación efectuada por supervisión/WFM, y actualiza los turnos en el sistema para que
            los cálculos de adherencia y outliers reflejen la realidad operativa inmediatamente.
            """
        )
        c_p1, c_p2, c_p3 = st.columns([1.5, 1.5, 2])
        with c_p1:
            dias_atras = st.slider("Días hacia atrás a auditar", min_value=1, max_value=30, value=7)
        with c_p2:
            dias_adelante = st.slider("Días hacia adelante a sincronizar", min_value=1, max_value=14, value=7)
        with c_p3:
            st.write("")
            st.write("")
            btn_ejecutar = st.button("🚀 Ejecutar Auditoría WFM Ahora", type="primary", use_container_width=True)

        if btn_ejecutar:
            with st.spinner(f"Auditando turnos entre hoy-{dias_atras}d y hoy+{dias_adelante}d desde API Almaverso..."):
                try:
                    res = ejecutar_auditoria_y_sync_turnos(dias_atras=dias_atras, dias_adelante=dias_adelante)
                    if res.get("ok"):
                        st.success(
                            f"✅ **Auditoría completada exitosamente:**\n\n"
                            f"- Ventana auditada: `{res.get('fecha_inicio')}` a `{res.get('fecha_fin')}`\n"
                            f"- Registros analizados en API: **{res.get('total_api'):,}**\n"
                            f"- Nuevos turnos: **{res.get('nuevos'):,}**\n"
                            f"- Cambios de horario detectados: **{res.get('cambios_horario'):,}**\n"
                            f"- Novedades reclasificadas: **{res.get('cambios_novedad'):,}**\n"
                            f"- Pausas reprogramadas: **{res.get('cambios_pausas'):,}**\n"
                            f"- Base de datos actualizada y lista para visualización."
                        )
                        st.rerun()
                    else:
                        st.error(f"Error en auditoría: {res.get('mensaje')}")
                except Exception as ex:
                    st.error(f"Ocurrió una excepción durante la auditoría: {ex}")

    # 3. Filtros Analíticos
    st.markdown("#### 📋 Bitácora Forense de Cambios de Turno")
    
    cf1, cf2, cf3, cf4 = st.columns([1.5, 1.5, 2, 1])
    with cf1:
        tipos_opciones = ["Todos", "CAMBIO_HORARIO", "CAMBIO_NOVEDAD", "CAMBIO_PAUSA_ALMUERZO", "CAMBIO_PAUSA_DESCANSO", "TURNO_NUEVO"]
        sel_tipo = st.selectbox("Filtrar por Tipo de Cambio", options=tipos_opciones, index=0)
    with cf2:
        sel_rango = st.date_input(
            "Rango de Fechas del Turno",
            value=(datetime.now().date() - timedelta(days=15), datetime.now().date() + timedelta(days=3)),
            key="filtro_fecha_audit_turnos"
        )
    with cf3:
        txt_busqueda = st.text_input("Buscar por Asesor, BP, Servicio o Motivo", placeholder="Ej: Marcela, 351426, Agencias, Inicio pospuesto...")
    with cf4:
        limite_filas = st.selectbox("Límite", options=[100, 250, 500, 1000], index=1)

    f_ini_str = None
    f_fin_str = None
    if isinstance(sel_rango, (tuple, list)) and len(sel_rango) == 2:
        f_ini_str = sel_rango[0].strftime("%Y-%m-%d")
        f_fin_str = sel_rango[1].strftime("%Y-%m-%d")
    elif isinstance(sel_rango, datetime) or hasattr(sel_rango, "strftime"):
        f_ini_str = sel_rango.strftime("%Y-%m-%d")
        f_fin_str = f_ini_str

    df_audit = consultar_historial_auditoria_turnos(
        fecha_min=f_ini_str,
        fecha_max=f_fin_str,
        tipo_cambio=sel_tipo,
        busqueda=txt_busqueda,
        limit=limite_filas
    )

    if df_audit.empty:
        st.info("ℹ️ No se encontraron registros de cambios de turno para los filtros seleccionados.")
    else:
        st.caption(f"Mostrando **{len(df_audit):,}** modificaciones de turno auditadas.")

        # Función de estilo visual para las filas
        def _formatear_tipo_cambio(val):
            val_s = str(val).upper()
            if "HORARIO" in val_s:
                return "🕒 Horario"
            elif "NOVEDAD" in val_s:
                return "🔄 Novedad / DES"
            elif "ALMUERZO" in val_s:
                return "🍽️ Almuerzo"
            elif "DESCANSO" in val_s:
                return "☕ Descanso"
            elif "NUEVO" in val_s:
                return "✨ Turno Nuevo"
            return val_s

        df_display = df_audit.copy()
        df_display["Tipo de Modificación"] = df_display["Tipo de Modificación"].apply(_formatear_tipo_cambio)

        st.dataframe(
            df_display[[
                "Fecha Detección", "Fecha Turno", "BP", "Asesor", "Servicio",
                "Tipo de Modificación", "Valor Anterior", "Valor Nuevo", "Impacto en Adherencia"
            ]],
            use_container_width=True,
            hide_index=True,
            height=450
        )

        # Botón de Descarga Excel
        from io import BytesIO
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df_audit.to_excel(writer, index=False, sheet_name="Auditoria_Turnos")
        excel_data = output.getvalue()

        st.download_button(
            label="📥 Descargar Reporte de Auditoría en Excel (.xlsx)",
            data=excel_data,
            file_name=f"Auditoria_Cambios_Turnos_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Auditoría periódica de cambios de turnos WFM.")
    parser.add_argument("--days-back", type=int, default=7, help="Días hacia atrás a auditar")
    parser.add_argument("--days-forward", type=int, default=7, help="Días hacia adelante a auditar")
    parser.add_argument("--seed-csv", action="store_true", help="Sembrar histórico desde CSV si está vacío")
    args = parser.parse_args()

    if args.seed_csv:
        n = sembrar_historico_si_vacio()
        print(f"Sembradas {n} filas históricas en auditoria_cambios_turnos.")

    res = ejecutar_auditoria_y_sync_turnos(dias_atras=args.days_back, dias_adelante=args.days_forward)
    print("Resultado auditoría:", res)
