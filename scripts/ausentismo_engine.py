"""
Motor de Gestión y Control de Ausentismo Operativo — Radar Genesys Cloud.

Conecta en tiempo real la programación de turnos (Planta / Sistema de Punto)
con el estado de conexión real de Genesys Cloud:
1. Radar de Alerta Temprana (Primeros 15-30 min): Detección inmediata de No-Logins y Retrasos.
2. Auto-Servicio del Líder: Justificación en 2 clics persistida en Neon Postgres y SQLite.
3. Impacto en Capacidad: Horas perdidas justificadas vs injustificadas para la Base del Requerido.
4. Matriz Ejecutiva y Exportación: Resumen por Supervisor, Servicio y Tasa de Ausentismo.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date, timedelta, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

from config import DB_PATH
from live_engine import obtener_token_genesys, obtener_presencia_en_vivo, cargar_catalogo_presencias
from audit_engine import _obtener_db_url, registrar_evento

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SEDES_JSON_PATH = os.path.join(BASE_DIR, "../data/agentes_sede.json")


@st.cache_data(ttl=3600, show_spinner=False)
def cargar_mapa_sedes() -> dict:
    """Carga el mapa BP -> Sede (Bogotá / Medellín) cacheado."""
    if os.path.exists(SEDES_JSON_PATH):
        try:
            with open(SEDES_JSON_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def estilo_ausentismo_meta(val):
    """Estilo condicional: resalta en rojo todo ausentismo superior a la meta oficial del 8%."""
    if pd.isna(val):
        return ""
    if val <= 8.0:
        return "background-color: rgba(16, 185, 129, 0.20); color: #10b981; font-weight: 700;"
    elif val <= 10.0:
        return "background-color: rgba(245, 158, 11, 0.20); color: #f59e0b; font-weight: 700;"
    return "background-color: rgba(239, 68, 68, 0.25); color: #ef4444; font-weight: 700;"


def numero_agente(agente_nombre: str) -> str:
    """Extrae el identificador o BP desde '4853818 - Nombre Apellido' -> '4853818'."""
    if not agente_nombre:
        return ""
    s = str(agente_nombre).strip()
    return s.split(" - ")[0].strip() if " - " in s else s

# Catálogo oficial de tipos de ausencia según imagen operativa
TIPOS_AUSENCIA_OFICIALES = [
    "INCAPACIDAD MEDICA POR ENFERMEDAD MENOR A TRES DIAS",
    "INCAPACIDAD MEDICA POR ENFERMEDAD MAYOR A TRES DIAS",
    "INCAPACIDAD POR ACCIDENTE DE TRABAJO O ENFERMEDAD PROFESIONAL > 3 DIAS",
    "LICENCIA DE MATERNIDAD",
    "LICENCIA DE PATERNIDAD (LEY MARIA)",
    "LICENCIA NO REMUNERADA",
    "LICENCIA POR LUTO",
    "LICENCIA REMUNERADA",
    "SANCION POR PROCESO DISCIPLINARIO",
    "VACACIONES",
    "DIA DE LA FAMILIA",
    "CALAMIDAD DOMESTICA DEBIDAMENTE COMPROBADA",
    "FALLA TECNICA / CONECTIVIDAD / HERRAMIENTAS",
    "PERMISO COMPENSATORIO / APROBADO",
    "AUSENCIA NO JUSTIFICADA / ABANDONO / NO SHOW",
    "CAMBIO DE TURNO / NO PROGRAMADO HOY"
]

# Ausencias que se clasifican como NO justificadas o penalizables
AUSENCIAS_INJUSTIFICADAS_SET = {
    "AUSENCIA NO JUSTIFICADA / ABANDONO / NO SHOW",
    "SANCION POR PROCESO DISCIPLINARIO"
}


# ─────────────────────────────────────────────────────────────────────────────
# GESTIÓN DE BASE DE DATOS DE JUSTIFICACIONES (Neon Postgres + SQLite Fallback)
# ─────────────────────────────────────────────────────────────────────────────

def cargar_justificaciones_db(fecha_str: str) -> pd.DataFrame:
    """Carga justificaciones registradas para una fecha determinada."""
    # 1. Intentar Neon Postgres
    db_url = _obtener_db_url()
    try:
        import psycopg2
        conn = psycopg2.connect(db_url, connect_timeout=4)
        df = pd.read_sql_query(
            "SELECT fecha, bp, tipo_impuntualidad, es_justificado, observacion, registrado_por, fecha_registro "
            "FROM ausentismo_justificaciones WHERE fecha = %s",
            conn,
            params=(fecha_str,)
        )
        conn.close()
        if not df.empty:
            return df
    except Exception:
        pass

    # 2. Fallback local SQLite
    db_path = Path(BASE_DIR) / DB_PATH
    if db_path.exists():
        try:
            with sqlite3.connect(db_path) as conn:
                df = pd.read_sql_query(
                    "SELECT fecha, bp, tipo_impuntualidad, es_justificado, observacion, registrado_por, fecha_registro "
                    "FROM ausentismo_justificaciones WHERE fecha = ?",
                    conn,
                    params=(fecha_str,)
                )
                return df
        except Exception:
            pass

    return pd.DataFrame(columns=["fecha", "bp", "tipo_impuntualidad", "es_justificado", "observacion", "registrado_por", "fecha_registro"])


def guardar_justificacion_db(
    fecha_str: str,
    bp: str,
    agente: str,
    supervisor: str,
    coordinador: str,
    servicio: str,
    tipo_impuntualidad: str,
    observacion: str,
    registrado_por: str
) -> bool:
    """Guarda o actualiza una justificación en Neon Postgres y sincroniza con SQLite."""
    es_justificado = 0 if tipo_impuntualidad in AUSENCIAS_INJUSTIFICADAS_SET else 1

    # Guardar en Neon Postgres
    db_url = _obtener_db_url()
    guardado_remoto = False
    try:
        import psycopg2
        conn = psycopg2.connect(db_url, connect_timeout=5)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO ausentismo_justificaciones 
            (fecha, bp, agente, supervisor, coordinador, servicio, tipo_impuntualidad, es_justificado, observacion, registrado_por)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (fecha, bp) DO UPDATE SET
                tipo_impuntualidad = EXCLUDED.tipo_impuntualidad,
                es_justificado = EXCLUDED.es_justificado,
                observacion = EXCLUDED.observacion,
                registrado_por = EXCLUDED.registrado_por,
                fecha_registro = CURRENT_TIMESTAMP;
        """, (fecha_str, bp, agente, supervisor, coordinador, servicio, tipo_impuntualidad, bool(es_justificado), observacion, registrado_por))
        conn.commit()
        cur.close()
        conn.close()
        guardado_remoto = True
    except Exception as e:
        print(f"[Ausentismo] Error guardando en Neon: {e}")

    # Guardar en SQLite local
    db_path = Path(BASE_DIR) / DB_PATH
    if db_path.exists():
        try:
            with sqlite3.connect(db_path) as conn:
                conn.execute("""
                    INSERT INTO ausentismo_justificaciones 
                    (fecha, bp, agente, supervisor, coordinador, servicio, tipo_impuntualidad, es_justificado, observacion, registrado_por)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(fecha, bp) DO UPDATE SET
                        tipo_impuntualidad = excluded.tipo_impuntualidad,
                        es_justificado = excluded.es_justificado,
                        observacion = excluded.observacion,
                        registrado_por = excluded.registrado_por,
                        fecha_registro = CURRENT_TIMESTAMP
                """, (fecha_str, bp, agente, supervisor, coordinador, servicio, tipo_impuntualidad, es_justificado, observacion, registrado_por))
                conn.commit()
        except Exception as e:
            print(f"[Ausentismo] Error guardando en SQLite: {e}")

    return guardado_remoto


# ─────────────────────────────────────────────────────────────────────────────
# CARGA Y CRUCE DE TURNOS PROGRAMADOS VS GENESYS
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=120)
def obtener_turnos_programados_dia(fecha_str: str) -> pd.DataFrame:
    """Carga los turnos programados en SQLite para una fecha con cruce de detalle de novedad y servicio."""
    db_path = Path(BASE_DIR) / DB_PATH
    if not db_path.exists():
        return pd.DataFrame()
    with sqlite3.connect(db_path) as conn:
        try:
            query = """
                SELECT 
                    t.bp, 
                    t.fecha, 
                    t.hora_inicio, 
                    t.hora_fin,
                    COALESCE(td.servicio, '') as servicio_turno,
                    COALESCE(td.novedad, '') as novedad,
                    COALESCE(td.nombre_agente, '') as nombre_turno,
                    COALESCE(td.documento, '') as documento_turno
                FROM turnos t
                LEFT JOIN turnos_detallados td ON t.bp = td.bp AND t.fecha = td.fecha
                WHERE t.fecha = ?
            """
            df = pd.read_sql_query(query, conn, params=(fecha_str,))
            return df
        except Exception:
            try:
                df = pd.read_sql_query(
                    "SELECT bp, fecha, hora_inicio, hora_fin FROM turnos WHERE fecha = ?",
                    conn,
                    params=(fecha_str,)
                )
                df["servicio_turno"] = ""
                df["novedad"] = ""
                df["nombre_turno"] = ""
                df["documento_turno"] = ""
                return df
            except Exception:
                return pd.DataFrame()


@st.cache_data(ttl=30, show_spinner=False)
def obtener_presencia_usuarios_ausentismo(token: str, catalog: dict) -> dict:
    """
    Consulta a todos los usuarios de Genesys Cloud sin filtrar por locations
    para mapear con 100% de certeza el estado de presencia en tiempo real por BP.
    """
    headers = {"Authorization": f"Bearer {token}"}
    try:
        r_init = requests.get(
            "https://api.mypurecloud.com/api/v2/users",
            headers=headers,
            params={"pageSize": 100, "pageNumber": 1, "expand": "presence,routingStatus"},
            timeout=15
        )
        if not r_init or r_init.status_code != 200:
            return {}

        data_init = r_init.json()
        total_pages = data_init.get("pageCount", 1)

        def fetch_page(p):
            try:
                r = requests.get(
                    "https://api.mypurecloud.com/api/v2/users",
                    headers=headers,
                    params={"pageSize": 100, "pageNumber": p, "expand": "presence,routingStatus"},
                    timeout=15
                )
                return r.json().get("entities", []) if r.status_code == 200 else []
            except Exception:
                return []

        with ThreadPoolExecutor(max_workers=5) as executor:
            paginas = list(executor.map(fetch_page, range(1, total_pages + 1)))

        now_utc = datetime.now(timezone.utc)
        col_tz = timezone(timedelta(hours=-5))
        presencia_map = {}
        for page_entities in paginas:
            for u in page_entities:
                u_name = u.get("name", "")
                bp_val = numero_agente(u_name)
                pres = u.get("presence", {})
                p_def_id = pres.get("presenceDefinition", {}).get("id")
                p_info = catalog.get(
                    p_def_id,
                    {
                        "label": pres.get("presenceDefinition", {}).get("systemPresence", "Offline"),
                        "systemPresence": pres.get("presenceDefinition", {}).get("systemPresence", "Offline")
                    }
                )
                mod_date_str = pres.get("modifiedDate")
                dur_seg = 0
                hora_inicio_str = ""
                fecha_cambio_str = ""
                if mod_date_str:
                    try:
                        dt_mod = datetime.fromisoformat(mod_date_str.replace("Z", "+00:00"))
                        dur_seg = max(0, int((now_utc - dt_mod).total_seconds()))
                        dt_col = dt_mod.astimezone(col_tz)
                        hora_inicio_str = dt_col.strftime("%H:%M:%S")
                        fecha_cambio_str = dt_col.strftime("%Y-%m-%d")
                    except Exception:
                        pass

                routing = u.get("routingStatus", {}).get("status", "OFF_QUEUE")

                if bp_val:
                    presencia_map[bp_val] = {
                        "presence_label": p_info.get("label", "Offline"),
                        "system_presence": p_info.get("systemPresence", "Offline"),
                        "routing_status": routing,
                        "duracion_min": round(dur_seg / 60.0, 1),
                        "hora_ultimo_cambio": hora_inicio_str,
                        "fecha_ultimo_cambio": fecha_cambio_str,
                        "full_name_genesys": u_name
                    }
        return presencia_map
    except Exception as err:
        print(f"Error consultando usuarios ausentismo: {err}")
        return {}


@st.cache_data(ttl=600)
def obtener_presencia_historica_dia(fecha_str: str) -> dict:
    """Consulta segments en SQLite para reconstruir asistencia y puntualidad de días pasados con soporte de trasnocho."""
    db_path = Path(BASE_DIR) / DB_PATH
    if not db_path.exists():
        return {}
    with sqlite3.connect(db_path) as conn:
        try:
            dt_f = datetime.strptime(fecha_str, "%Y-%m-%d")
            f_next = (dt_f + timedelta(days=1)).strftime("%Y-%m-%d")
            df = pd.read_sql_query("""
                SELECT 
                    substr(agente, 1, instr(agente, ' - ') - 1) as bp,
                    inicio,
                    fin,
                    duracion_min,
                    fecha
                FROM segments
                WHERE (fecha = ? OR fecha = ?) 
                  AND UPPER(system_presence) NOT IN ('OFFLINE', 'DESCONECTADO')
                  AND UPPER(presence_label) NOT IN ('OFFLINE', 'DESCONECTADO')
            """, conn, params=(fecha_str, f_next))
            if df.empty:
                return {}

            res = {}
            for bp, grp in df.groupby("bp"):
                res[bp] = grp[["inicio", "fin", "duracion_min", "fecha"]].to_dict(orient="records")
            return res
        except Exception:
            return {}


def normalizar_nombre_clave(texto: str) -> str:
    """Normaliza un nombre eliminando tildes, mayúsculas y prefijos de BP."""
    if not texto:
        return ""
    import unicodedata
    import re
    s = str(texto).strip().upper()
    if " - " in s:
        s = s.split(" - ", 1)[1].strip()
    s = re.sub(r"^[0-9]+\s+", "", s)
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


@st.cache_data(ttl=3600)
def cargar_sociodemografico_db() -> dict:
    """
    Carga el maestro integral de jerarquía y sociodemográfico:
    1. Base Maestra Google Sheets / Cache Local (14,584 personas, 47,000+ llaves BP/cédula/gestor).
    2. Mirror local CSV data/zendesk/socio_demo.csv
    3. Mirror local CSV data/zendesk/servicios_socio_maestro.csv
    4. SQLite dim_agentes y sociodemografico
    5. Índice secundario por nombre normalizado en ['__NAME_MAP__'] para 100% de cobertura.
    """
    res = {}
    name_to_info = {}

    # 1. Base Maestra Google Sheets / Cache Local
    try:
        from jerarquia import load_jerarquia
        res = load_jerarquia()
        for k, v in res.items():
            nom = normalizar_nombre_clave(v.get("nombre", ""))
            if nom and nom not in name_to_info:
                name_to_info[nom] = v
    except Exception:
        res = {}

    # Si jerarquía vino vacía (ej. Streamlit Cloud sin credenciales interactivas), cargar directamente el JSON cache
    if not res:
        json_cache = Path(BASE_DIR).parent / "data" / "cache_jerarquia_base.json"
        if json_cache.exists():
            try:
                with open(json_cache, "r", encoding="utf-8") as f_cj:
                    res = json.load(f_cj)
                    for k, v in res.items():
                        nom = normalizar_nombre_clave(v.get("nombre", ""))
                        if nom and nom not in name_to_info:
                            name_to_info[nom] = v
            except Exception:
                pass

    # 2. Local CSV: socio_demo.csv
    try:
        csv_socio = Path(BASE_DIR).parent / "data" / "zendesk" / "socio_demo.csv"
        if csv_socio.exists():
            df_sd = pd.read_csv(csv_socio)
            for _, row in df_sd.iterrows():
                bp_v = str(row.get("usuario_gestor_1", "")).strip()
                if bp_v.endswith(".0"):
                    bp_v = bp_v[:-2]
                nom_c = str(row.get("nombre_completo", "")).strip()
                info_sd = {
                    "nombre": nom_c,
                    "servicio": str(row.get("Servicio", "")).strip(),
                    "jefe_inmediato": str(row.get("jefe_inmediato", "")).strip(),
                    "coordinador": str(row.get("coordinador", "")).strip(),
                    "estado_laboral": str(row.get("estado", "Activo")).strip(),
                }
                if bp_v and bp_v not in ("-", "nan", "None", ""):
                    if bp_v not in res:
                        res[bp_v] = info_sd
                    else:
                        for k_f, v_f in info_sd.items():
                            if v_f and not res[bp_v].get(k_f):
                                res[bp_v][k_f] = v_f
                nom_norm = normalizar_nombre_clave(nom_c)
                if nom_norm and nom_norm not in name_to_info:
                    name_to_info[nom_norm] = info_sd
    except Exception:
        pass

    # 3. Local CSV: servicios_socio_maestro.csv
    try:
        csv_maestro = Path(BASE_DIR).parent / "data" / "zendesk" / "servicios_socio_maestro.csv"
        if csv_maestro.exists():
            df_sm = pd.read_csv(csv_maestro)
            for _, row in df_sm.iterrows():
                nom_c = str(row.get("name") or row.get("local_name") or "").strip()
                info_sm = {
                    "nombre": nom_c,
                    "servicio": str(row.get("servicio", "")).strip(),
                    "jefe_inmediato": str(row.get("jefe", "")).strip(),
                    "coordinador": str(row.get("coordinador", "")).strip(),
                    "cargo": str(row.get("cargo", "")).strip(),
                }
                nom_norm = normalizar_nombre_clave(nom_c)
                if nom_norm and nom_norm not in name_to_info:
                    name_to_info[nom_norm] = info_sm
                local_norm = normalizar_nombre_clave(str(row.get("local_name", "")))
                if local_norm and local_norm not in name_to_info:
                    name_to_info[local_norm] = info_sm
    except Exception:
        pass

    # 4. Complementar con SQLite local (sociodemografico o dim_agentes)
    db_path = Path(BASE_DIR) / DB_PATH
    if not db_path.exists():
        db_path = Path(BASE_DIR).parent / "data" / "presencia.db"
    if db_path.exists():
        with sqlite3.connect(db_path) as conn:
            try:
                df_socio = pd.read_sql_query(
                    "SELECT bp, documento, nombre, servicio, jefe_inmediato, coordinador, cargo, estado_laboral FROM sociodemografico",
                    conn
                )
                from exclusion_list import filtrar_df_exclusiones
                df_socio = filtrar_df_exclusiones(df_socio)
                for _, row in df_socio.iterrows():
                    d = row.to_dict()
                    bp_str = str(row.get("bp", "")).strip()
                    doc_str = str(row.get("documento", "")).strip()
                    if bp_str:
                        if bp_str not in res:
                            res[bp_str] = d
                        else:
                            for k, v in d.items():
                                if v and not res[bp_str].get(k):
                                    res[bp_str][k] = v
                    if doc_str and doc_str not in res:
                        res[doc_str] = d
                    nom_norm = normalizar_nombre_clave(str(row.get("nombre", "")))
                    if nom_norm and nom_norm not in name_to_info:
                        name_to_info[nom_norm] = d
            except Exception:
                try:
                    df_dim = pd.read_sql_query(
                        "SELECT agente, cargo, estado_laboral, servicio, jefe_inmediato, coordinador FROM dim_agentes",
                        conn
                    )
                    from exclusion_list import filtrar_df_exclusiones
                    df_dim = filtrar_df_exclusiones(df_dim)
                    for _, row in df_dim.iterrows():
                        ag_s = str(row.get("agente", ""))
                        bp_s = ag_s.split(" - ")[0].strip() if " - " in ag_s else ag_s.strip()
                        nom_s = ag_s.split(" - ")[1].strip() if " - " in ag_s else ag_s.strip()
                        info_dim = {
                            "nombre": nom_s,
                            "cargo": row.get("cargo", "ASESOR"),
                            "estado_laboral": row.get("estado_laboral", "Activo"),
                            "servicio": row.get("servicio", ""),
                            "jefe_inmediato": row.get("jefe_inmediato", ""),
                            "coordinador": row.get("coordinador", "")
                        }
                        if bp_s not in res:
                            res[bp_s] = info_dim
                        else:
                            if nom_s and not res[bp_s].get("nombre"):
                                res[bp_s]["nombre"] = nom_s
                            if row.get("jefe_inmediato") and not res[bp_s].get("jefe_inmediato"):
                                res[bp_s]["jefe_inmediato"] = row.get("jefe_inmediato")
                            if row.get("coordinador") and not res[bp_s].get("coordinador"):
                                res[bp_s]["coordinador"] = row.get("coordinador")
                        nom_norm = normalizar_nombre_clave(nom_s)
                        if nom_norm and nom_norm not in name_to_info:
                            name_to_info[nom_norm] = info_dim
                except Exception:
                    pass

    res["__NAME_MAP__"] = name_to_info
    return res


def construir_radar_ausentismo(
    fecha_str: str,
    df_live_presencia,
    agentes_map: dict
) -> pd.DataFrame:
    """
    Cruza los turnos programados del día contra la presencia de Genesys (en vivo o histórica).
    Determina:
    - Identificación plena de Asesor, Supervisor, Coordinador y Servicio.
    - Estado de entrada: Conectado a tiempo, Retraso (5-15m), Crítico (>15m), Sin Login (Offline).
    - Horas de turno y pérdida de capacidad.
    """
    df_turnos = obtener_turnos_programados_dia(fecha_str)
    if df_turnos.empty:
        return pd.DataFrame()

    socio_map = cargar_sociodemografico_db()
    name_to_info = socio_map.get("__NAME_MAP__", {})
    sede_map = cargar_mapa_sedes()

    df_just = cargar_justificaciones_db(fecha_str)
    just_map = {}
    if not df_just.empty:
        just_map = df_just.set_index("bp").to_dict(orient="index")

    # Determinar si es fecha pasada, hoy o futura
    now_col = datetime.now(timezone.utc) - timedelta(hours=5)
    hoy_str = now_col.strftime("%Y-%m-%d")
    hora_act_str = now_col.strftime("%H:%M:%S")

    es_pasado = (fecha_str < hoy_str)
    es_futuro = (fecha_str > hoy_str)
    es_hoy = (fecha_str == hoy_str)

    hist_presencia_map = {}
    if es_pasado:
        hist_presencia_map = obtener_presencia_historica_dia(fecha_str)

    # Mapeo de presencia actual por BP (solo aplica si es hoy)
    presencia_act_map = {}
    if es_hoy:
        if isinstance(df_live_presencia, dict):
            presencia_act_map = df_live_presencia
        elif isinstance(df_live_presencia, pd.DataFrame) and not df_live_presencia.empty:
            for _, row in df_live_presencia.iterrows():
                bp_val = numero_agente(row.get("agente", ""))
                if bp_val:
                    presencia_act_map[bp_val] = {
                        "presence_label": row.get("estado", "Offline"),
                        "system_presence": row.get("sys_pres", "Offline"),
                        "duracion_min": row.get("dur_min", 0.0),
                        "hora_ultimo_cambio": row.get("hora_inicio", "")
                    }

    from exclusion_list import es_servicio_latam, es_campana_ajena

    # Indexar agentes_map por BP para búsqueda O(1)
    agentes_bp_map = {}
    for k, v in agentes_map.items():
        bp_k = numero_agente(v.get("agente", "")) or str(k)
        if bp_k:
            agentes_bp_map[bp_k] = v

    filas = []
    for _, r in df_turnos.iterrows():
        bp = str(r["bp"]).strip()
        doc = str(r.get("documento_turno", "")).strip()
        h_ini = str(r["hora_inicio"]).strip()
        h_fin = str(r["hora_fin"]).strip()
        nom_turno = str(r.get("nombre_turno", "")).strip()

        # 1. Buscar en jerarquía y socios (BP -> Cédula -> Nombre normalizado)
        socio_ag = (
            socio_map.get(bp) 
            or (socio_map.get(doc) if doc else None) 
            or (name_to_info.get(normalizar_nombre_clave(nom_turno)) if nom_turno else None)
            or {}
        )
        info_ag = agentes_bp_map.get(bp) or agentes_map.get(bp, {})
        live_info = presencia_act_map.get(bp, {})

        # Nombre del asesor con cascada completa de resolución
        nom_socio = str(socio_ag.get("nombre", "")).strip()
        nom_info = str(info_ag.get("agente", "")).strip()
        nom_genesys = str(live_info.get("full_name_genesys", "")).strip()

        nombre_candidato = ""
        for n in [nom_turno, nom_socio, nom_info, nom_genesys]:
            if n and not n.startswith("Asesor ") and n != f"{bp} - Colaborador" and n != f"{bp} - Asesor":
                nombre_candidato = n
                break

        if nombre_candidato:
            if " - " in nombre_candidato:
                agente_nom = nombre_candidato
            else:
                agente_nom = f"{bp} - {nombre_candidato}"
        else:
            agente_nom = f"{bp} - {socio_ag.get('cargo', 'Asesor')}" if socio_ag else f"Asesor {bp}"

        sup = socio_ag.get("jefe_inmediato") or info_ag.get("jefe_inmediato")
        coord = socio_ag.get("coordinador") or info_ag.get("coordinador")

        # Respaldo final por nombre candidato si supervisor aún no se encuentra
        if (not sup or sup in ("Sin Supervisor", "-", "None", "nan")) and nombre_candidato:
            info_por_nom = name_to_info.get(normalizar_nombre_clave(nombre_candidato))
            if info_por_nom:
                if not sup or sup in ("Sin Supervisor", "-", "None", "nan"):
                    sup = info_por_nom.get("jefe_inmediato")
                if not coord or coord in ("Sin Coordinador", "-", "None", "nan"):
                    coord = info_por_nom.get("coordinador")

        sup = sup or "Sin Supervisor"
        coord = coord or "Sin Coordinador"

        srv_turno = str(r.get("servicio_turno", "")).strip()
        srv = srv_turno or socio_ag.get("servicio") or info_ag.get("servicio") or "General"

        # Excluir categóricamente campañas ajenas a LATAM (Claro, Chec, Colmédica, etc.)
        if srv and srv != "General" and (not es_servicio_latam(srv) or es_campana_ajena(srv)):
            continue

        # Calcular duración programada en horas
        try:
            t_ini_dt = datetime.strptime(h_ini, "%H:%M:%S")
            t_fin_dt = datetime.strptime(h_fin, "%H:%M:%S")
            if t_fin_dt <= t_ini_dt:
                t_fin_dt += timedelta(days=1)
            duracion_turno_horas = (t_fin_dt - t_ini_dt).total_seconds() / 3600.0
        except Exception:
            duracion_turno_horas = 8.0

        # Novedad programada en malla
        nov_malla = str(r.get("novedad", "")).strip().upper()
        es_novedad_aprobada = nov_malla in ("VAC", "LMA", "ICCP", "LNR", "PAB", "DES", "FOR", "SST", "SC")

        # Verificar si pertenece a Cargo (no operan con Genesys)
        es_cargo = ("CARGO" in str(srv).upper()) or ("CARGO" in str(socio_ag.get("cargo", "")).upper())
        es_retraso = False

        # Evaluación según temporalidad (Pasado, Futuro o En Vivo)
        if es_novedad_aprobada:
            aplica_genesys = False
            es_ausente = False
            estado_asistencia = f"📑 Novedad ({nov_malla})"
            semaforo = "⚪"
            esta_conectado = False
            ya_debio_iniciar = es_pasado or (hora_act_str >= h_ini)
            pres_label = f"Novedad ({nov_malla})"
            minutos_desde_inicio = 0
        elif es_cargo:
            aplica_genesys = False
            es_ausente = False
            estado_asistencia = "📦 Cargo (Sin Genesys)"
            semaforo = "⚪"
            esta_conectado = False
            ya_debio_iniciar = es_pasado or (hora_act_str >= h_ini)
            pres_label = "No Aplica (Cargo)"
            minutos_desde_inicio = 0
        elif es_futuro:
            aplica_genesys = True
            es_ausente = False
            estado_asistencia = "⏰ Turno Futuro"
            semaforo = "⚪"
            esta_conectado = False
            ya_debio_iniciar = False
            pres_label = "Turno Futuro"
            minutos_desde_inicio = 0
        elif es_pasado:
            aplica_genesys = True
            ya_debio_iniciar = True
            segs_agente = hist_presencia_map.get(bp, [])
            if segs_agente:
                es_trasnocho = False
                try:
                    es_trasnocho = (datetime.strptime(h_fin, "%H:%M:%S") < datetime.strptime(h_ini, "%H:%M:%S"))
                except Exception:
                    pass

                # Filtrar segmentos pertenecientes a la jornada (shift-date window)
                if es_trasnocho:
                    dt_shift_ini = datetime.strptime(f"{fecha_str} {h_ini}", "%Y-%m-%d %H:%M:%S")
                    dt_f_next = datetime.strptime(fecha_str, "%Y-%m-%d") + timedelta(days=1)
                    dt_shift_fin = datetime.strptime(f"{dt_f_next.strftime('%Y-%m-%d')} {h_fin}", "%Y-%m-%d %H:%M:%S")
                    w_start = dt_shift_ini - timedelta(minutes=60)
                    w_end = dt_shift_fin + timedelta(minutes=30)
                else:
                    dt_shift_ini = datetime.strptime(f"{fecha_str} {h_ini}", "%Y-%m-%d %H:%M:%S")
                    dt_shift_fin = datetime.strptime(f"{fecha_str} {h_fin}", "%Y-%m-%d %H:%M:%S")
                    w_start = dt_shift_ini - timedelta(minutes=60)
                    w_end = dt_shift_fin + timedelta(minutes=30)

                segs_validos = []
                for s in segs_agente:
                    try:
                        s_dt = datetime.strptime(s["inicio"][:19], "%Y-%m-%d %H:%M:%S")
                        if w_start <= s_dt <= w_end:
                            segs_validos.append((s_dt, s))
                    except Exception:
                        if s.get("fecha") == fecha_str:
                            segs_validos.append((None, s))

                if segs_validos:
                    esta_conectado = True
                    segs_validos.sort(key=lambda x: x[0] if x[0] else datetime.min)
                    primer_dt = segs_validos[0][0]
                    primer_login = primer_dt.strftime("%H:%M:%S") if primer_dt else segs_validos[0][1]["inicio"]

                    diff_min = 0.0
                    if primer_dt:
                        diff_min = (primer_dt - dt_shift_ini).total_seconds() / 60.0
                    else:
                        try:
                            t_login_str = primer_login.split(" ")[-1][:8]
                            t_login_dt = datetime.strptime(t_login_str, "%H:%M:%S")
                            t_ini_dt_ref = datetime.strptime(h_ini, "%H:%M:%S")
                            diff_min = (t_login_dt - t_ini_dt_ref).total_seconds() / 60.0
                        except Exception:
                            pass

                    minutos_desde_inicio = max(0, int(round(diff_min)))
                    if diff_min <= 5:
                        estado_asistencia = "🟢 Conectó a Tiempo"
                        semaforo = "🟢"
                        es_ausente = False
                        es_retraso = False
                    elif diff_min <= 15:
                        estado_asistencia = "🟠 Retraso Leve (5-15m)"
                        semaforo = "🟠"
                        es_ausente = False
                        es_retraso = True
                    else:
                        estado_asistencia = "🔴 Retraso Crítico (>15m)"
                        semaforo = "🔴"
                        es_ausente = False
                        es_retraso = True

                    tot_min_int = int(round(sum(float(s[1].get("duracion_min", 0)) for s in segs_validos)))
                    h_ini_disp = primer_login.split(" ")[-1][:5]
                    pres_label = f"Conectó ({tot_min_int}m • Inició {h_ini_disp})"
                else:
                    esta_conectado = False
                    es_ausente = True
                    es_retraso = False
                    estado_asistencia = "🚨 Ausencia / No Login"
                    semaforo = "🚨"
                    minutos_desde_inicio = int(round(duracion_turno_horas * 60))
                    pres_label = "Sin Conexión"
            else:
                esta_conectado = False
                es_ausente = True
                es_retraso = False
                estado_asistencia = "🚨 Ausencia / No Login"
                semaforo = "🚨"
                minutos_desde_inicio = int(round(duracion_turno_horas * 60))
                pres_label = "Sin Conexión"
        else:
            # es_hoy: Evaluar estado de puntualidad y conexión con discriminación de turnos finalizados vs en curso
            aplica_genesys = True
            live_info = presencia_act_map.get(bp, {})
            pres_label = live_info.get("presence_label", "Offline")
            sys_pres = live_info.get("system_presence", "Offline")
            esta_conectado = (pres_label != "Offline" and sys_pres != "Offline")

            try:
                t_ini_dt_today = now_col.replace(
                    hour=int(h_ini.split(":")[0]),
                    minute=int(h_ini.split(":")[1]),
                    second=int(h_ini.split(":")[2]),
                    microsecond=0
                )
                t_fin_dt_today = now_col.replace(
                    hour=int(h_fin.split(":")[0]),
                    minute=int(h_fin.split(":")[1]),
                    second=int(h_fin.split(":")[2]),
                    microsecond=0
                )
                if t_fin_dt_today <= t_ini_dt_today:
                    if now_col.time() < t_fin_dt_today.time():
                        t_ini_dt_today -= timedelta(days=1)
                    else:
                        t_fin_dt_today += timedelta(days=1)
            except Exception:
                t_ini_dt_today = now_col
                t_fin_dt_today = now_col

            minutos_desde_inicio = (now_col - t_ini_dt_today).total_seconds() / 60.0
            turno_futuro = (now_col < t_ini_dt_today)
            turno_en_curso = (t_ini_dt_today <= now_col <= t_fin_dt_today)

            if turno_futuro:
                ya_debio_iniciar = False
                estado_asistencia = "⏰ Turno Futuro"
                semaforo = "⚪"
                es_ausente = False
                minutos_desde_inicio = 0
            elif turno_en_curso:
                ya_debio_iniciar = True
                if esta_conectado:
                    estado_asistencia = "🟢 Conectado"
                    semaforo = "🟢"
                    es_ausente = False
                    es_retraso = False
                    minutos_desde_inicio = 0
                else:
                    if minutos_desde_inicio <= 5:
                        estado_asistencia = "🟡 En Margen (<=5m)"
                        semaforo = "🟡"
                        es_ausente = False
                        es_retraso = False
                    elif minutos_desde_inicio <= 15:
                        estado_asistencia = "🟠 Retraso Leve (5-15m)"
                        semaforo = "🟠"
                        es_ausente = False
                        es_retraso = True
                    elif minutos_desde_inicio <= 60:
                        estado_asistencia = "🔴 Retraso Crítico (>15m)"
                        semaforo = "🔴"
                        es_ausente = True
                        es_retraso = True
                    else:
                        estado_asistencia = "🚨 Ausencia / No Login"
                        semaforo = "🚨"
                        es_ausente = True
                        es_retraso = False
            else:
                # Turno ya finalizado hoy (la hora de fin ya pasó)
                ya_debio_iniciar = True
                dur_min = live_info.get("duracion_min", 999999)
                fecha_cambio = live_info.get("fecha_ultimo_cambio", "")
                minutos_desde_medianoche = (now_col - now_col.replace(hour=0, minute=0, second=0, microsecond=0)).total_seconds() / 60.0
                h_ult = live_info.get("hora_ultimo_cambio", "")
                actividad_hoy = (fecha_cambio == hoy_str) or (dur_min <= minutos_desde_medianoche + 60)

                if esta_conectado:
                    estado_asistencia = "🟢 Conectado (Horas Extra)"
                    semaforo = "🟢"
                    es_ausente = False
                    es_retraso = False
                    minutos_desde_inicio = 0
                elif actividad_hoy:
                    h_disp = f" • Salió {h_ult[:5]}" if h_ult else ""
                    estado_asistencia = f"🟢 Cumplió Turno{h_disp}"
                    semaforo = "🟢"
                    es_ausente = False
                    es_retraso = False
                    minutos_desde_inicio = 0
                    pres_label = f"Finalizado{h_disp}"
                else:
                    estado_asistencia = "🚨 Ausencia / No Login"
                    semaforo = "🚨"
                    es_ausente = True
                    es_retraso = False
                    minutos_desde_inicio = int(round(duracion_turno_horas * 60))
                    pres_label = "Sin Conexión Hoy"

        # Cruzar con justificación si existe
        just_info = just_map.get(bp, {})
        tipo_just = just_info.get("tipo_impuntualidad", None)
        obs_just = just_info.get("observacion", "")
        reg_por = just_info.get("registrado_por", "")

        if es_cargo:
            justificacion_val = "No Aplica (Cargo)"
            es_justificado_str = "No Aplica"
        elif es_novedad_aprobada:
            justificacion_val = f"Malla: {nov_malla}"
            es_justificado_str = "Sí"
        elif not es_ausente:
            justificacion_val = tipo_just or "No Aplica (Asistió)"
            es_justificado_str = "No Aplica"
        else:
            justificacion_val = tipo_just or "Sin Justificar"
            es_justificado = (tipo_just not in AUSENCIAS_INJUSTIFICADAS_SET) if tipo_just else False
            es_justificado_str = "Sí" if (tipo_just and es_justificado) else ("No" if (tipo_just and not es_justificado) else "Pendiente")

        sede_val = sede_map.get(bp, "Medellín")

        filas.append({
            "Semaforo": semaforo,
            "Estado": estado_asistencia,
            "BP": bp,
            "Agente": agente_nom,
            "Sede": sede_val,
            "Supervisor": sup,
            "Coordinador": coord,
            "Servicio": srv,
            "Hora Inicio": h_ini,
            "Hora Fin": h_fin,
            "Duracion Horas": round(duracion_turno_horas, 1),
            "Min Retraso": max(0, int(round(minutos_desde_inicio))) if (es_retraso or (es_ausente and ya_debio_iniciar)) else 0,
            "Estado Genesys": ("No Aplica (Cargo)" if es_cargo else pres_label),
            "Justificación": justificacion_val,
            "Es Justificado": es_justificado_str,
            "Observación": obs_just,
            "Registrado Por": reg_por,
            "Ya Inició": ya_debio_iniciar,
            "Esta Conectado": esta_conectado,
            "Con Retraso": es_retraso,
            "Es Ausente": es_ausente,
            "Es Cargo": es_cargo,
            "Aplica Genesys": aplica_genesys
        })

    df_res = pd.DataFrame(filas)
    from exclusion_list import filtrar_df_exclusiones
    return filtrar_df_exclusiones(df_res)


# ─────────────────────────────────────────────────────────────────────────────
# RENDERIZADO VISUAL DEL MÓDULO EN STREAMLIT
# ─────────────────────────────────────────────────────────────────────────────

def render_tab_ausentismo(agentes_map: dict):
    """Interfaz completa del Módulo de Ausentismo y Conexión."""
    st.markdown(
        """
        <div style="background: linear-gradient(90deg, #0f172a 0%, #1e293b 100%); padding: 16px 20px; border-radius: 12px; margin-bottom: 15px; border-left: 5px solid #ef4444;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div>
                    <h3 style="color: #ffffff; margin: 0 0 4px 0; font-size: 20px;">🚨 Radar de Ausentismo y Conexión Operativa</h3>
                    <p style="color: #94a3b8; margin: 0; font-size: 13px;">
                        Control en tiempo real de turnos programados vs conexión en Genesys • Detección de No-Logins y Justificación Ágil
                    </p>
                </div>
                <div style="text-align: right; background: #334155; padding: 6px 14px; border-radius: 8px; border: 1px solid #475569;">
                    <span style="color: #38bdf8; font-size: 11px; font-weight: 700; text-transform: uppercase;">Módulo Confidencial</span><br>
                    <span style="color: #cbd5e1; font-size: 12px; font-weight: 600;">Exclusivo Gestión Operativa</span>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    now_col = datetime.now(timezone.utc) - timedelta(hours=5)
    hoy_col = now_col.date()
    hora_corte_str = now_col.strftime("%I:%M:%S %p")

    # Controles de cabecera: Fila 1 (Fecha, Auto-refresco y Botón de actualización)
    c_f1, c_f_ref, c_f_btn = st.columns([2.2, 1.4, 1.2])
    with c_f1:
        fecha_sel = st.date_input("Fecha a Evaluar:", value=hoy_col, max_value=hoy_col + timedelta(days=7), key="dt_ausentismo")
    fecha_str = str(fecha_sel)

    with c_f_ref:
        opciones_refresh = ["Desactivado (Manual)", "Cada 30 seg", "Cada 1 min", "Cada 2 min", "Cada 5 min"]
        refresco_sel = st.selectbox(
            "Auto-Actualización:",
            options=opciones_refresh,
            index=2 if fecha_sel == hoy_col else 0,
            key="aus_auto_refresh",
            help="Recarga automáticamente los estados de presencia de Genesys si estás consultando el día de hoy."
        )

    with c_f_btn:
        st.write("")
        if st.button("🔄 Actualizar Ahora", key="btn_refrescar_ausentismo", type="primary", use_container_width=True):
            obtener_presencia_usuarios_ausentismo.clear()
            obtener_presencia_historica_dia.clear()
            cargar_sociodemografico_db.clear()
            obtener_turnos_programados_dia.clear()
            cargar_mapa_sedes.clear()
            st.cache_data.clear()
            st.toast("Datos y sociodemográfico actualizados con éxito", icon="✅")
            st.rerun()

    # Cálculo de segundos para refresco automático
    refresh_sec = None
    if fecha_sel == hoy_col and refresco_sel != "Desactivado (Manual)":
        if "30 seg" in refresco_sel:
            refresh_sec = 30
        elif "1 min" in refresco_sel:
            refresh_sec = 60
        elif "2 min" in refresco_sel:
            refresh_sec = 120
        elif "5 min" in refresco_sel:
            refresh_sec = 300

    if fecha_sel == hoy_col:
        st.caption(f"🟢 **Modo Radar En Vivo:** Sincronizado a las **{hora_corte_str}** con Genesys Cloud • ⏱️ {refresco_sel}")
    elif fecha_sel < hoy_col:
        st.caption(f"📑 **Modo Auditoría Histórica:** Cruzando programación con registros de conexión de Genesys del **{fecha_str}**")
    else:
        st.caption(f"⏰ **Modo Proyección:** Visualizando turnos programados en malla para el **{fecha_str}**")

    @st.fragment(run_every=refresh_sec)
    def _render_cuerpo_ausentismo():
        # Obtener token y presencia en vivo de Genesys si es hoy
        token = obtener_token_genesys()
        presencia_map = {}
        if fecha_sel == hoy_col and token:
            catalog = cargar_catalogo_presencias(token)
            presencia_map = obtener_presencia_usuarios_ausentismo(token, catalog)

        df_radar = construir_radar_ausentismo(fecha_str, presencia_map, agentes_map)

        if df_radar.empty:
            st.warning(f"No hay turnos programados cargados para la fecha {fecha_str}. Carga la malla semanal correspondiente.")
            return

        # Filtros dinámicos
        c_f2, c_f_sede, c_f3, c_f4, c_f5 = st.columns([1.2, 1.0, 1.2, 1.1, 0.9])
        with c_f2:
            coords = sorted(df_radar["Coordinador"].dropna().unique())
            coord_sel = st.multiselect("Filtrar Coordinador:", options=coords, placeholder="Todos", key="aus_coord_sel")
        with c_f_sede:
            sedes_disp = sorted(df_radar["Sede"].dropna().unique())
            sede_sel = st.multiselect("Ciudad / Sede:", options=sedes_disp, placeholder="Todos", key="aus_sede_sel")
        with c_f3:
            df_sup_filter = df_radar
            if coord_sel:
                df_sup_filter = df_sup_filter[df_sup_filter["Coordinador"].isin(coord_sel)]
            if sede_sel:
                df_sup_filter = df_sup_filter[df_sup_filter["Sede"].isin(sede_sel)]
            sups_disp = df_sup_filter["Supervisor"].dropna().unique()
            sup_sel = st.multiselect("Filtrar Supervisor:", options=sorted(sups_disp), placeholder="Todos", key="aus_sup_sel")
        with c_f4:
            servicios_disp = df_radar["Servicio"].dropna().unique()
            srv_sel = st.multiselect("Filtrar Servicio:", options=sorted(servicios_disp), placeholder="Todos", key="aus_srv_sel")
        with c_f5:
            st.write("")
            excluir_cargo = st.checkbox("Excluir Cargo", value=True, help="Oculta colaboradores de Cargo Booking / CC (no operan con Genesys)", key="aus_excluir_cargo")

        df_view = df_radar.copy()
        if excluir_cargo:
            df_view = df_view[~df_view["Es Cargo"]]
        if sede_sel:
            df_view = df_view[df_view["Sede"].isin(sede_sel)]
        if coord_sel:
            df_view = df_view[df_view["Coordinador"].isin(coord_sel)]
        if sup_sel:
            df_view = df_view[df_view["Supervisor"].isin(sup_sel)]
        if srv_sel:
            df_view = df_view[df_view["Servicio"].isin(srv_sel)]

        # ── KPIs DE RESUMEN EJECUTIVO ────────────────────────────────────────────
        total_prog = len(df_view)
        iniciados = df_view[df_view["Ya Inició"] & df_view["Aplica Genesys"]]
        total_iniciados = len(iniciados)
        conectados_act = len(iniciados[iniciados["Esta Conectado"]])
    
        # Ausentes: debieron iniciar, aplican a Genesys y están clasificados como ausentes
        ausentes_df = iniciados[iniciados["Es Ausente"]]
        total_ausentes = len(ausentes_df)
    
        # Tasa de ausentismo sobre turnos ya iniciados
        tasa_ausentismo = (total_ausentes / total_iniciados * 100.0) if total_iniciados > 0 else 0.0

        # Capacidad perdida en horas
        horas_perdidas_tot = ausentes_df["Duracion Horas"].sum()
        horas_justificadas = ausentes_df[ausentes_df["Es Justificado"] == "Sí"]["Duracion Horas"].sum()
        horas_injustificadas = ausentes_df[ausentes_df["Es Justificado"] != "Sí"]["Duracion Horas"].sum()

        m1, m2, m3, m4, m5 = st.columns(5)
        with m1:
            st.metric("Programados del Día", f"{total_prog:,}", help=f"Total de turnos en malla para {fecha_str}")
        with m2:
            st.metric("Turnos ya Iniciados", f"{total_iniciados:,}", delta=f"{conectados_act} conectados")
        with m3:
            st.metric("Total Ausentes (No Login)", f"{total_ausentes:,}", delta=f"{tasa_ausentismo:.1f}% tasa", delta_color="inverse")
        with m4:
            st.metric(
                "Horas Capacidad Perdidas",
                f"{horas_perdidas_tot:.1f} h",
                delta=f"-{horas_injustificadas:.1f}h injustificadas",
                delta_color="inverse",
                help="Horas de turno restadas directamente a la Base del Requerido"
            )
        with m5:
            pendientes_just = len(ausentes_df[ausentes_df["Es Justificado"] == "Pendiente"])
            st.metric("Pendientes Justificar", f"{pendientes_just:,}", delta="Acción requerida líder" if pendientes_just > 0 else "Al día", delta_color="inverse" if pendientes_just > 0 else "normal")

        st.markdown("---")

        # ── SUBPESTAÑAS DE TRABAJO ──────────────────────────────────────────────
        tab_radar, tab_justificar, tab_analisis, tab_export = st.tabs([
            "🚨 Radar Temprano (En Vivo)",
            "✍️ Auto-Servicio del Líder (Justificar)",
            "📊 Impacto y Diagnóstico por Supervisor",
            "📥 Exportación Oficial a Excel"
        ])

        # ─────────────────────────────────────────────────────────────────────────
        # SUBPESTAÑA 1: RADAR TEMPRANO EN VIVO
        # ─────────────────────────────────────────────────────────────────────────
        with tab_radar:
            st.markdown("#### 🔍 Monitoreo de Conexión y Retrasos de Entrada")
            st.caption("Identifica en los primeros minutos de cada franja quién no ha iniciado sesión en Genesys.")

            col_filtro_est, col_espacio = st.columns([2, 3])
            with col_filtro_est:
                opc_estados = [
                    "Solo Ausentes / No Login (Crítico)",
                    "Solo Retrasos (5-15 min)",
                    "Turnos En Curso (Activos Ahora)",
                    "Turnos Finalizados (Cumplieron)",
                    "Todos los que debieron iniciar",
                    "Ver Todos (Malla Completa)"
                ]
                filtro_est = st.selectbox("Vista de Radar:", opc_estados, index=0, key="aus_filtro_radar")

            if filtro_est == "Solo Ausentes / No Login (Crítico)":
                df_radar_show = df_view[df_view["Ya Inició"] & df_view["Es Ausente"]]
            elif filtro_est == "Solo Retrasos (5-15 min)":
                df_radar_show = df_view[df_view["Estado"].str.contains("Retraso", na=False)]
            elif filtro_est == "Turnos En Curso (Activos Ahora)":
                df_radar_show = df_view[df_view["Estado"].isin(["🟢 Conectado", "🟡 En Margen (<=5m)", "🟠 Retraso Leve (5-15m)", "🔴 Retraso Crítico (>15m)"])]
            elif filtro_est == "Turnos Finalizados (Cumplieron)":
                df_radar_show = df_view[df_view["Estado"].str.contains("Cumplió Turno", na=False)]
            elif filtro_est == "Todos los que debieron iniciar":
                df_radar_show = df_view[df_view["Ya Inició"]]
            else:
                df_radar_show = df_view

            cols_tabla_radar = [
                "Semaforo", "Estado", "BP", "Agente", "Sede", "Supervisor", "Servicio",
                "Hora Inicio", "Min Retraso", "Estado Genesys", "Justificación", "Es Justificado", "Observación"
            ]

            st.dataframe(
                df_radar_show[cols_tabla_radar].sort_values(by=["Min Retraso", "Hora Inicio"], ascending=[False, True]),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Semaforo": st.column_config.TextColumn("", width="small"),
                    "Estado": st.column_config.TextColumn("Estado Entrada", width="medium"),
                    "BP": st.column_config.TextColumn("BP", width="small"),
                    "Agente": st.column_config.TextColumn("Nombre Asesor", width="large"),
                    "Sede": st.column_config.TextColumn("Sede", width="small"),
                    "Supervisor": st.column_config.TextColumn("Supervisor", width="medium"),
                    "Hora Inicio": st.column_config.TextColumn("Turno Inicio", width="small"),
                    "Min Retraso": st.column_config.NumberColumn("Retraso (min)", format="%d m"),
                    "Estado Genesys": st.column_config.TextColumn("Genesys Actual", width="small"),
                    "Justificación": st.column_config.TextColumn("Causal Registrada", width="medium"),
                    "Es Justificado": st.column_config.TextColumn("Justificado", width="small"),
                }
            )

        # ─────────────────────────────────────────────────────────────────────────
        # SUBPESTAÑA 2: AUTO-SERVICIO DEL LÍDER (JUSTIFICACIÓN EN 2 CLICS)
        # ─────────────────────────────────────────────────────────────────────────
        with tab_justificar:
            st.markdown("#### ✍️ Panel Rápido de Justificación para Supervisores y GTR")
            st.markdown(
                """
                Selecciona un asesor ausente o retrasado, asigna el motivo de la lista oficial de Almacontact 
                y guarda con un clic. La información queda registrada en la base de datos central en tiempo real.
                """
            )

            c_form1, c_form2 = st.columns([1.5, 1])

            with c_form1:
                # Lista de asesores que requieren justificación (solo ausencias evaluables en Genesys)
                pendientes_df = df_view[df_view["Ya Inició"] & df_view["Es Ausente"]]
            
                if pendientes_df.empty:
                    st.success("🎉 ¡Excelente! No hay asesores pendientes de justificar en este momento con los filtros seleccionados.")
                else:
                    opciones_agentes = {
                        f"{r['BP']} - {r['Agente']} (Turno {r['Hora Inicio']} • Sup: {r['Supervisor']})": r["BP"]
                        for _, r in pendientes_df.iterrows()
                    }

                    agente_label_sel = st.selectbox("1. Selecciona el Asesor Ausente:", list(opciones_agentes.keys()), key="aus_sel_agente")
                    bp_seleccionado = opciones_agentes[agente_label_sel]
                    datos_ag = pendientes_df[pendientes_df["BP"] == bp_seleccionado].iloc[0]

                    c_sub1, c_sub2 = st.columns(2)
                    with c_sub1:
                        tipo_aus_sel = st.selectbox("2. Tipo de Ausencia / Impuntualidad (Oficial):", TIPOS_AUSENCIA_OFICIALES, key="aus_sel_tipo")
                    with c_sub2:
                        obs_input = st.text_input("3. Observación / Nro. Radicado (Opcional):", placeholder="Ej. Incapacidad EPS Sura #12345", key="aus_obs_input")

                    c_btn_save, c_msg = st.columns([1, 2])
                    with c_btn_save:
                        if st.button("💾 Guardar Justificación", type="primary", use_container_width=True, key="btn_save_just"):
                            user_email = getattr(st.user, "email", "carlosmurilloe.almacontact@outsourcing-account.com") if hasattr(st, "user") else "carlosmurilloe.almacontact@outsourcing-account.com"
                            ok = guardar_justificacion_db(
                                fecha_str=fecha_str,
                                bp=bp_seleccionado,
                                agente=datos_ag["Agente"],
                                supervisor=datos_ag["Supervisor"],
                                coordinador=datos_ag["Coordinador"],
                                servicio=datos_ag["Servicio"],
                                tipo_impuntualidad=tipo_aus_sel,
                                observacion=obs_input,
                                registrado_por=user_email
                            )
                            registrar_evento(user_email, user_email, "Ausentismo", "guardar_justificacion", f"BP: {bp_seleccionado} - {tipo_aus_sel}")
                            st.success(f"✅ Justificación registrada para {datos_ag['Agente']}.")
                            st.rerun()

            with c_form2:
                st.info(
                    f"""
                    📌 **Resumen del Asesor Seleccionado:**
                    - **Turno:** `{datos_ag['Hora Inicio']}` a `{datos_ag['Hora Fin']}` ({datos_ag['Duracion Horas']} hrs)
                    - **Supervisor:** {datos_ag['Supervisor']}
                    - **Servicio:** {datos_ag['Servicio']}
                    - **Retraso acumulado:** {datos_ag['Min Retraso']} minutos
                    - **Genesys:** `{datos_ag['Estado Genesys']}`
                    """ if not pendientes_df.empty else "Sin novedades activas."
                )

            # Historial de justificaciones de la fecha
            st.markdown("---")
            st.markdown("##### 📋 Justificaciones Registradas para esta Fecha:")
            df_just_hoy = cargar_justificaciones_db(fecha_str)
            if df_just_hoy.empty:
                st.caption("Aún no se han registrado justificaciones para el día seleccionado.")
            else:
                st.dataframe(df_just_hoy, use_container_width=True, hide_index=True)

        # ─────────────────────────────────────────────────────────────────────────
        # SUBPESTAÑA 3: IMPACTO Y DIAGNÓSTICO POR SUPERVISOR
        # ─────────────────────────────────────────────────────────────────────────
        # ─────────────────────────────────────────────────────────────────────────
        # SUBPESTAÑA 3: IMPACTO Y DIAGNÓSTICO POR SUPERVISOR, SEDE Y SERVICIO
        # ─────────────────────────────────────────────────────────────────────────
        with tab_analisis:
            st.markdown("#### 📊 Diagnóstico de Ausentismo e Impacto en Capacidad")
            st.caption("Identifica qué sedes, servicios y supervisores superan la meta corporativa del **8.0%** de ausentismo.")

            c_g1, c_g2 = st.columns(2)

            # 1. Agrupado por Supervisor (solo evaluables en Genesys)
            evaluables_sup = df_view[df_view["Ya Inició"] & df_view["Aplica Genesys"]]
            resumen_sup = evaluables_sup.groupby("Supervisor").agg(
                Programados=("BP", "count"),
                Conectados=("Esta Conectado", "sum"),
                Con_Retraso=("Con Retraso", "sum"),
                Ausentes=("Es Ausente", "sum"),
                Horas_Perdidas=("Duracion Horas", lambda h: h[evaluables_sup.loc[h.index, "Es Ausente"]].sum())
            ).reset_index()

            resumen_sup["% Ausentismo"] = (resumen_sup["Ausentes"] / resumen_sup["Programados"] * 100.0).round(1)
            resumen_sup = resumen_sup.sort_values(by=["Ausentes", "Horas_Perdidas"], ascending=[False, False])

            with c_g1:
                fig_sup = px.bar(
                    resumen_sup.head(10),
                    x="Supervisor",
                    y="Ausentes",
                    color="% Ausentismo",
                    title="Top 10 Supervisores con Mayor Ausentismo (Personas)",
                    color_continuous_scale="Reds",
                    text="Ausentes"
                )
                fig_sup.update_layout(height=380, margin=dict(l=20, r=20, t=40, b=80))
                st.plotly_chart(fig_sup, use_container_width=True)

            with c_g2:
                # 2. Distribución de Tipos de Ausencia (solo ausencias evaluables)
                aus_con_motivo = df_view[df_view["Ya Inició"] & df_view["Es Ausente"]]
                conteo_motivos = aus_con_motivo["Justificación"].value_counts().reset_index()
                conteo_motivos.columns = ["Motivo", "Cantidad"]

                fig_mot = px.pie(
                    conteo_motivos,
                    names="Motivo",
                    values="Cantidad",
                    title="Distribución de Causas de Ausentismo",
                    hole=0.45,
                    color_discrete_sequence=px.colors.qualitative.Bold
                )
                fig_mot.update_layout(height=380, margin=dict(l=20, r=20, t=40, b=20))
                st.plotly_chart(fig_mot, use_container_width=True)

            # ── DESGLOSE POR SEDE Y POR SERVICIO ─────────────────────────────
            c_sede_diag, c_srv_diag = st.columns(2)

            resumen_sede = evaluables_sup.groupby("Sede").agg(
                Programados=("BP", "count"),
                Conectados=("Esta Conectado", "sum"),
                Con_Retraso=("Con Retraso", "sum"),
                Ausentes=("Es Ausente", "sum"),
                Horas_Perdidas=("Duracion Horas", lambda h: h[evaluables_sup.loc[h.index, "Es Ausente"]].sum())
            ).reset_index()
            resumen_sede["% Ausentismo"] = (resumen_sede["Ausentes"] / resumen_sede["Programados"] * 100.0).round(1)
            resumen_sede = resumen_sede.sort_values(by="Ausentes", ascending=False)

            resumen_srv = evaluables_sup.groupby("Servicio").agg(
                Programados=("BP", "count"),
                Conectados=("Esta Conectado", "sum"),
                Con_Retraso=("Con Retraso", "sum"),
                Ausentes=("Es Ausente", "sum"),
                Horas_Perdidas=("Duracion Horas", lambda h: h[evaluables_sup.loc[h.index, "Es Ausente"]].sum())
            ).reset_index()
            resumen_srv["% Ausentismo"] = (resumen_srv["Ausentes"] / resumen_srv["Programados"] * 100.0).round(1)
            resumen_srv = resumen_srv.sort_values(by=["Ausentes", "% Ausentismo"], ascending=[False, False])

            with c_sede_diag:
                st.markdown("##### 🏢 Ausentismo por Ciudad / Sede:")
                styler_sede = resumen_sede.style.map(estilo_ausentismo_meta, subset=["% Ausentismo"])
                st.dataframe(
                    styler_sede,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Con_Retraso": st.column_config.NumberColumn("Con Retraso", help="Asesores que asistieron pero iniciaron sesión tarde (>5m)"),
                        "Ausentes": st.column_config.NumberColumn("Ausentes (No Login)", help="Asesores sin registro de conexión en su turno"),
                        "% Ausentismo": st.column_config.NumberColumn("% Ausentismo", format="%.1f%%"),
                        "Horas_Perdidas": st.column_config.NumberColumn("Horas Perdidas", format="%.1f h")
                    }
                )

            with c_srv_diag:
                st.markdown("##### 🎧 Top Servicios con Mayor Ausentismo:")
                styler_srv = resumen_srv.head(10).style.map(estilo_ausentismo_meta, subset=["% Ausentismo"])
                st.dataframe(
                    styler_srv,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Con_Retraso": st.column_config.NumberColumn("Con Retraso", help="Asesores que asistieron pero iniciaron sesión tarde (>5m)"),
                        "Ausentes": st.column_config.NumberColumn("Ausentes (No Login)", help="Asesores sin registro de conexión en su turno"),
                        "% Ausentismo": st.column_config.NumberColumn("% Ausentismo", format="%.1f%%"),
                        "Horas_Perdidas": st.column_config.NumberColumn("Horas Perdidas", format="%.1f h")
                    }
                )

            st.markdown("##### 📑 Matriz Detallada por Supervisor (Meta corporativa: 8.0%):")
            styler_sup = resumen_sup.style.map(estilo_ausentismo_meta, subset=["% Ausentismo"])
            st.dataframe(
                styler_sup,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Con_Retraso": st.column_config.NumberColumn("Con Retraso", help="Asesores que asistieron pero iniciaron sesión tarde (>5m)"),
                    "Ausentes": st.column_config.NumberColumn("Ausentes (No Login)", help="Asesores sin registro de conexión en su turno"),
                    "% Ausentismo": st.column_config.NumberColumn("% Ausentismo", format="%.1f%%"),
                    "Horas_Perdidas": st.column_config.NumberColumn("Horas Perdidas", format="%.1f h")
                }
            )

        # ─────────────────────────────────────────────────────────────────────────
        # SUBPESTAÑA 4: EXPORTACIÓN OFICIAL Y ENCUADRE GTR
        # ─────────────────────────────────────────────────────────────────────────
        with tab_export:
            st.markdown("#### 📋 Encuadre GTR & Exportación Oficial a Excel")
            st.markdown(
                """
                Estructura consolidada por **Servicio** y **Sede** lista para cuadrar capacidad con GTR y WFM.
                Incluye desglose individual por asesor, causales registradas y resumen ejecutivo.
                """
            )

            # Construir tabla de encuadre GTR: Servicio x Sede
            df_encuadre = evaluables_sup.groupby(["Servicio", "Sede"]).agg(
                Programados=("BP", "count"),
                Conectados=("Esta Conectado", "sum"),
                Con_Retraso=("Con Retraso", "sum"),
                Ausentes=("Es Ausente", "sum"),
                Horas_Perdidas=("Duracion Horas", lambda h: h[evaluables_sup.loc[h.index, "Es Ausente"]].sum())
            ).reset_index()
            df_encuadre["% Ausentismo"] = (df_encuadre["Ausentes"] / df_encuadre["Programados"] * 100.0).round(1)
            df_encuadre["Capacidad Efectiva %"] = (100.0 - df_encuadre["% Ausentismo"]).round(1)
            df_encuadre = df_encuadre.sort_values(by=["Servicio", "Sede"])

            st.markdown("##### 📌 Matriz de Encuadre Operativo (Servicio • Sede):")
            styler_encuadre = df_encuadre.style.map(estilo_ausentismo_meta, subset=["% Ausentismo"])
            st.dataframe(
                styler_encuadre,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Con_Retraso": st.column_config.NumberColumn("Con Retraso", help="Asesores con login > 5m"),
                    "Ausentes": st.column_config.NumberColumn("Ausentes", help="Asesores sin conexión"),
                    "% Ausentismo": st.column_config.NumberColumn("% Ausentismo", format="%.1f%%"),
                    "Capacidad Efectiva %": st.column_config.NumberColumn("% Efectiva", format="%.1f%%"),
                    "Horas_Perdidas": st.column_config.NumberColumn("Horas Perdidas", format="%.1f h")
                }
            )

            output = BytesIO()
            with pd.ExcelWriter(output, engine="openpyxl") as writer:
                df_encuadre.to_excel(writer, sheet_name="Encuadre GTR", index=False)
                resumen_sede.to_excel(writer, sheet_name="Resumen Sedes", index=False)
                resumen_srv.to_excel(writer, sheet_name="Resumen Servicios", index=False)
                resumen_sup.to_excel(writer, sheet_name="Resumen Supervisores", index=False)
                df_view.to_excel(writer, sheet_name="Detalle Asesores", index=False)

            excel_data = output.getvalue()
            file_name = f"Reporte_Ausentismo_Encuadre_GTR_{fecha_str}.xlsx"

            st.download_button(
                label="📊 Descargar Libro Completo de Encuadre GTR (Excel)",
                data=excel_data,
                file_name=file_name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                type="primary",
                key="btn_dl_ausentismo"
            )

    _render_cuerpo_ausentismo()
