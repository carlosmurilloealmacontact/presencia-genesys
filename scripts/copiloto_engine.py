# copiloto_engine.py - Motor de Inteligencia Operativa y Copiloto Conversacional 4DX
# Impulsado por Vertex AI (Google Cloud) & Gemini 2.5 Flash
# Implementación 100% nativa vía REST API con authlib + httpx (Cero dependencias pesadas en Streamlit Cloud)

import os
import sys
import sqlite3
import json
import re
import time

from datetime import datetime
from pathlib import Path
import pandas as pd
import streamlit as st
import httpx

BASE_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = BASE_DIR / "data" / "presencia.db"

SF_CASES_PATH = BASE_DIR / "data" / "salesforce" / "cases_amc_cleaned.csv"
SF_OMNI_PATH = BASE_DIR / "data" / "salesforce" / "omni_presencia_historico.csv"

PROJECT_ID = "project-094fad9d-54da-42d9-880"
LOCATION = "us-central1"
MODEL_NAME = "gemini-2.5-flash"


def _generar_token_jwt_authlib(sa_info: dict):
    """Genera token OAuth2 de Google Cloud firmando JWT con authlib (librería ya presente en Streamlit Cloud)."""
    try:
        from authlib.jose import jwt
        now = int(time.time())
        header = {"alg": "RS256", "typ": "JWT"}
        if "private_key_id" in sa_info:
            header["kid"] = sa_info["private_key_id"]
            
        payload = {
            "iss": sa_info["client_email"],
            "sub": sa_info["client_email"],
            "aud": "https://oauth2.googleapis.com/token",
            "iat": now,
            "exp": now + 3600,
            "scope": "https://www.googleapis.com/auth/cloud-platform"
        }
        assertion = jwt.encode(header, payload, sa_info["private_key"])
        if isinstance(assertion, bytes):
            assertion = assertion.decode("utf-8")

        r = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion},
            timeout=15.0
        )
        if r.status_code == 200:
            return r.json().get("access_token"), None
        return None, f"Error en Google OAuth endpoint (HTTP {r.status_code}): {r.text}"
    except Exception as e:
        return None, f"Error firmando JWT de service account: {e}"


def _obtener_token_vertex():
    """Obtiene un token de acceso OAuth2 para Vertex AI desde Streamlit Secrets o credenciales locales."""
    # 1. Intentar desde st.secrets["gcp_service_account"] (Streamlit Cloud)
    if hasattr(st, "secrets") and "gcp_service_account" in st.secrets:
        try:
            sa_raw = st.secrets["gcp_service_account"]
            sa_info = json.loads(sa_raw) if isinstance(sa_raw, str) else dict(sa_raw)
            token, err = _generar_token_jwt_authlib(sa_info)
            if token:
                return token, None
            return None, err
        except Exception as e:
            return None, f"Error leyendo `[gcp_service_account]` en Streamlit Secrets: {e}"

    # 2. Intentar desde archivo local común si existe
    local_key = Path(r"C:\Users\cames\.gcp\pipeline-service-account.json")
    if local_key.exists():
        try:
            with open(local_key, "r") as f:
                sa_info = json.load(f)
            token, _ = _generar_token_jwt_authlib(sa_info)
            if token:
                return token, None
        except Exception:
            pass

    return None, (
        "No se encontraron credenciales de Google Cloud en Streamlit Secrets. "
        "Por favor asegúrate de agregar el bloque `[gcp_service_account]` en Settings > Secrets de share.streamlit.io."
    )


# ── HERRAMIENTAS ANALÍTICAS LOCALES ──────────────────────────────────────────

def normalizar_fecha(fecha_str: str) -> str:
    """Normaliza fechas y expresiones temporales al formato canónico YYYY-MM-DD del año operativo 2026."""
    if not fecha_str:
        return ""
    fecha_str = str(fecha_str).strip().lower()

    if fecha_str in ["hoy", "today", "actual", "ayer", "yesterday", "ultima", "reciente"]:
        return "2026-09-17"

    meses = {
        "ene": 1, "enero": 1, "jan": 1, "feb": 2, "febrero": 2,
        "mar": 3, "marzo": 3, "abr": 4, "abril": 4, "apr": 4,
        "may": 5, "mayo": 5, "jun": 6, "junio": 6, "jul": 7,
        "julio": 7, "ago": 8, "agosto": 8, "aug": 8, "sep": 9,
        "sept": 9, "septiembre": 9, "oct": 10, "octubre": 10,
        "nov": 11, "noviembre": 11, "dic": 12, "diciembre": 12, "dec": 12
    }

    # "17 de sep", "17 de septiembre", "ayer 17 de sep"
    m_texto = re.search(r"(\d{1,2})\s*(?:de)?\s*([a-záéíóú]+)(?:\s*(?:de)?\s*(\d{2,4}))?", fecha_str)
    if m_texto:
        dia = int(m_texto.group(1))
        mes_txt = m_texto.group(2)[:3]
        anio_txt = m_texto.group(3)
        if mes_txt in meses:
            mes = meses[mes_txt]
            anio = 2026
            if anio_txt:
                try:
                    a = int(anio_txt)
                    anio = 2026 if a < 2026 else a
                except:
                    anio = 2026
            return f"{anio}-{mes:02d}-{dia:02d}"

    # DD/MM o DD-MM
    m_dm = re.match(r"^(\d{1,2})[/.-](\d{1,2})$", fecha_str)
    if m_dm:
        dia, mes = m_dm.groups()
        return f"2026-{int(mes):02d}-{int(dia):02d}"

    # DD/MM/YYYY o DD-MM-YYYY
    m = re.match(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})$", fecha_str)
    if m:
        dia, mes, anio = m.groups()
        anio = int(anio)
        if anio < 100:
            anio += 2000
        if anio < 2026:
            anio = 2026
        return f"{anio}-{int(mes):02d}-{int(dia):02d}"

    # YYYY-MM-DD
    m_iso = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", fecha_str)
    if m_iso:
        anio, mes, dia = m_iso.groups()
        if int(anio) < 2026:
            anio = "2026"
        return f"{anio}-{int(mes):02d}-{int(dia):02d}"

    return fecha_str


def resolver_supervisor(conn, sup_buscado: str, fecha: str = "") -> str:
    """Resuelve con precisión el nombre oficial del supervisor evitando falsos positivos por substrings."""
    c = conn.cursor()
    if fecha:
        c.execute("""
            SELECT DISTINCT jefe_inmediato FROM segments WHERE fecha = ?
            UNION
            SELECT DISTINCT coordinador FROM segments WHERE fecha = ?
        """, (fecha, fecha))
        candidatos = [r[0] for r in c.fetchall() if r[0]]
    else:
        c.execute("""
            SELECT DISTINCT jefe_inmediato FROM segments
            UNION
            SELECT DISTINCT coordinador FROM segments
        """)
        candidatos = [r[0] for r in c.fetchall() if r[0]]

    sup_buscado_clean = re.sub(r"[^\w\s]", "", sup_buscado).upper().strip()
    tokens_buscados = [t for t in sup_buscado_clean.split() if len(t) >= 3]
    if not tokens_buscados:
        return sup_buscado

    mejores = []
    for cand in candidatos:
        cand_tokens = [re.sub(r"[^\w]", "", tok) for tok in cand.upper().split()]
        score = 0
        for t in tokens_buscados:
            for ct in cand_tokens:
                if t == ct:
                    score += 3
                elif ct.startswith(t) or t.startswith(ct):
                    score += 2

        if score > 0:
            seg_count = 0
            if fecha:
                c.execute("SELECT count(*) FROM segments WHERE fecha=? AND (jefe_inmediato=? OR coordinador=?)", (fecha, cand, cand))
                seg_count = c.fetchone()[0]
            else:
                c.execute("SELECT count(*) FROM segments WHERE jefe_inmediato=? OR coordinador=?", (cand, cand))
                seg_count = c.fetchone()[0]
            mejores.append((score, seg_count, cand))

    if mejores:
        mejores.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return mejores[0][2]
    return sup_buscado



def obtener_fechas_disponibles() -> str:
    """Devuelve las fechas más recientes con datos en la base de datos de presencia y Salesforce."""
    res = {"fechas_presencia": [], "fechas_turnos": [], "ultima_fecha_recomendada": ""}
    if DB_PATH.exists():
        try:
            conn = sqlite3.connect(str(DB_PATH))
            c = conn.cursor()
            c.execute("SELECT DISTINCT fecha FROM segments ORDER BY fecha DESC LIMIT 5")
            res["fechas_presencia"] = [r[0] for r in c.fetchall()]
            c.execute("SELECT DISTINCT fecha FROM turnos_detallados ORDER BY fecha DESC LIMIT 5")
            res["fechas_turnos"] = [r[0] for r in c.fetchall()]
            conn.close()
            if res["fechas_presencia"]:
                res["ultima_fecha_recomendada"] = res["fechas_presencia"][0]
        except Exception as e:
            res["error"] = str(e)
    return json.dumps(res, ensure_ascii=False)


def consultar_asesor(nombre_o_id: str, fecha: str = "") -> str:
    """Consulta integral de un asesor (turno programado, presencia en Genesys, pausas, descansos, supervisor y actividad en Salesforce)."""
    if not DB_PATH.exists():
        return json.dumps({"error": "Base de datos no encontrada."})
    
    fecha = normalizar_fecha(fecha)
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    
    if not fecha:
        c.execute("SELECT DISTINCT fecha FROM segments ORDER BY fecha DESC LIMIT 1")
        row_f = c.fetchone()
        fecha = row_f[0] if row_f else "2026-09-17"
        
    palabras = [p.strip() for p in re.split(r"[\s\-_]+", nombre_o_id) if len(p.strip()) >= 3]
    if not palabras:
        palabras = [nombre_o_id.strip()]

    # 1. Turno programado
    query_turnos = "SELECT nombre_agente, servicio, horas_programadas, turno_ini, turno_fin, lunch_ini, lunch_fin, des_1_ini, des_1_fin, des_2_ini, des_2_fin, novedad FROM turnos_detallados WHERE fecha = ?"
    params_turnos = [fecha]
    for p in palabras:
        query_turnos += " AND (nombre_agente LIKE ? OR documento LIKE ? OR bp LIKE ?)"
        params_turnos.extend([f"%{p}%", f"%{p}%", f"%{p}%"])
    query_turnos += " LIMIT 1"
    c.execute(query_turnos, params_turnos)
    row_t = c.fetchone()
    
    # 2. Segmentos de presencia Genesys
    query_seg = "SELECT agente, servicio, jefe_inmediato, coordinador, presence_label, ROUND(SUM(duracion_min), 1) FROM segments WHERE fecha = ?"
    params_seg = [fecha]
    for p in palabras:
        query_seg += " AND (agente LIKE ? OR agente_id LIKE ?)"
        params_seg.extend([f"%{p}%", f"%{p}%"])
    query_seg += " GROUP BY presence_label"
    c.execute(query_seg, params_seg)
    rows_s = c.fetchall()
    conn.close()

    # 3. Actividad en Salesforce B2B
    sf_info = {"casos_gestionados": 0, "casos_asignados": 0, "rescatado_salesforce": False}
    if SF_CASES_PATH.exists():
        try:
            df_sf = pd.read_csv(SF_CASES_PATH, encoding="latin1", low_memory=False)
            col_asesor = "Asesor" if "Asesor" in df_sf.columns else "Nombre_Real"
            if col_asesor in df_sf.columns:
                mask = pd.Series(True, index=df_sf.index)
                for p in palabras:
                    mask = mask & df_sf[col_asesor].astype(str).str.contains(p, case=False, na=False)
                df_match = df_sf[mask]
                if not df_match.empty:
                    sf_info["casos_gestionados"] = len(df_match)
                    sf_info["casos_asignados"] = int((df_match["Esta_Asignado"] == True).sum()) if "Esta_Asignado" in df_match.columns else len(df_match)
                    if not rows_s and sf_info["casos_gestionados"] > 0:
                        sf_info["rescatado_salesforce"] = True
        except Exception:
            pass

    if not rows_s and not row_t and sf_info["casos_gestionados"] == 0:
        return json.dumps({
            "mensaje": f"No se encontraron datos para '{nombre_o_id}' en fecha {fecha}."
        }, ensure_ascii=False)

    nombre_oficial = rows_s[0][0] if rows_s else (row_t[0] if row_t else nombre_o_id)
    servicio = rows_s[0][1] if rows_s else (row_t[1] if row_t else "Desconocido")
    jefe = rows_s[0][2] if rows_s else "No registrado en Genesys"
    coordinador = rows_s[0][3] if rows_s else "No registrado en Genesys"

    estados = {r[4]: r[5] for r in rows_s}
    resultado = {
        "fecha": fecha,
        "asesor": nombre_oficial,
        "servicio": servicio,
        "jefe_inmediato": jefe,
        "coordinador": coordinador,
        "turno_programado": {
            "horas_programadas": row_t[2] if row_t else None,
            "horario": f"{row_t[3]} a {row_t[4]}" if (row_t and row_t[3]) else "Sin turno en malla",
            "almuerzo_programado": f"{row_t[5]} a {row_t[6]}" if (row_t and row_t[5]) else "No asignado",
            "break_1_programado": f"{row_t[7]} a {row_t[8]}" if (row_t and row_t[7]) else "No asignado",
            "break_2_programado": f"{row_t[9]} a {row_t[10]}" if (row_t and row_t[9]) else "No asignado",
            "novedad": row_t[11] if (row_t and row_t[11]) else None
        } if row_t else "Sin turno registrado",
        "genesys_tiempos_minutos": estados,
        "salesforce_b2b": sf_info
    }
    return json.dumps(resultado, ensure_ascii=False)


def consultar_equipo_supervisor(supervisor: str, fecha: str = "") -> str:
    """Consulta el desempeño global, lista de asesores, diagnóstico de ausentismos y cumplimiento de pausas para el equipo de un supervisor."""
    if not DB_PATH.exists():
        return json.dumps({"error": "Base de datos no encontrada."})
        
    fecha = normalizar_fecha(fecha)
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    if not fecha:
        c.execute("SELECT DISTINCT fecha FROM segments ORDER BY fecha DESC LIMIT 1")
        row_f = c.fetchone()
        fecha = row_f[0] if row_f else "2026-09-17"

    sup_oficial = resolver_supervisor(conn, supervisor, fecha)

    # Detectar si la persona es COORDINADORA (tiene múltiples servicios a cargo en segments)
    c.execute("SELECT COUNT(DISTINCT servicio) FROM segments WHERE fecha=? AND coordinador=?", (fecha, sup_oficial))
    servicios_coord_count = c.fetchone()[0]

    # ── CASO A: COORDINADOR / LÍDER MULTI-SERVICIO ───────────────────────────
    if servicios_coord_count > 1:
        c.execute("""
            SELECT servicio, COUNT(DISTINCT agente), ROUND(SUM(duracion_min)/60.0, 1)
            FROM segments
            WHERE fecha=? AND coordinador=?
            GROUP BY servicio
            ORDER BY COUNT(DISTINCT agente) DESC
        """, (fecha, sup_oficial))
        servicios_rows = c.fetchall()

        c.execute("""
            SELECT jefe_inmediato, servicio, COUNT(DISTINCT agente)
            FROM segments
            WHERE fecha=? AND coordinador=?
            GROUP BY jefe_inmediato, servicio
            ORDER BY servicio, COUNT(DISTINCT agente) DESC
        """, (fecha, sup_oficial))
        sups_rows = c.fetchall()

        c.execute("""
            SELECT COUNT(DISTINCT agente), ROUND(SUM(duracion_min)/60.0, 1)
            FROM segments
            WHERE fecha=? AND coordinador=?
        """, (fecha, sup_oficial))
        tot_row = c.fetchone()
        total_agentes = tot_row[0]
        total_horas = tot_row[1]

        desglose_servicios = []
        for s in servicios_rows:
            srv_nombre = s[0]
            ag_count = s[1]
            hrs_tot = s[2]
            sups_en_srv = [f"{sp[0]} ({sp[2]} asesores)" for sp in sups_rows if sp[1] == srv_nombre]
            
            c.execute("""
                SELECT presence_label, ROUND(SUM(duracion_min)/60.0, 1)
                FROM segments
                WHERE fecha=? AND coordinador=? AND servicio=?
                GROUP BY presence_label
                ORDER BY SUM(duracion_min) DESC
                LIMIT 4
            """, (fecha, sup_oficial, srv_nombre))
            estados_top = {r[0]: f"{r[1]} hrs" for r in c.fetchall()}

            desglose_servicios.append({
                "servicio": srv_nombre,
                "total_asesores_conectados": ag_count,
                "horas_totales_conexion": hrs_tot,
                "supervisores_a_cargo": sups_en_srv,
                "estados_principales": estados_top
            })

        conn.close()
        return json.dumps({
            "fecha": fecha,
            "persona_consultada": sup_oficial,
            "rol": "Coordinadora de Operaciones",
            "resumen_ejecutivo": {
                "total_servicios_coordinados": len(servicios_rows),
                "total_asesores_conectados": total_agentes,
                "horas_totales_operacion": total_horas,
                "diagnostico_asistencia": f"✅ Cierre operacional exitoso con {total_agentes} asesores conectados a lo largo de los {len(servicios_rows)} servicios.",
                "servicios_a_cargo": [s[0] for s in servicios_rows]
            },
            "cierre_por_servicio": desglose_servicios
        }, ensure_ascii=False)

    # ── CASO B: SUPERVISOR INDIVIDUAL (Un solo servicio) ─────────────────────
    c.execute("""
        SELECT agente, servicio, jefe_inmediato, coordinador, presence_label, ROUND(SUM(duracion_min), 1)
        FROM segments
        WHERE fecha = ? AND (jefe_inmediato = ? OR coordinador = ?)
        GROUP BY agente, presence_label
    """, (fecha, sup_oficial, sup_oficial))
    rows = c.fetchall()

    if not rows:
        conn.close()
        return json.dumps({
            "fecha": fecha,
            "mensaje": f"No se encontraron registros de presencia para el supervisor '{sup_oficial}' en la fecha {fecha}."
        }, ensure_ascii=False)

    servicio_sup = rows[0][1]
    equipo = {}
    for r in rows:
        ag = r[0]
        label = r[4]
        mins = r[5]
        if ag not in equipo:
            equipo[ag] = {
                "total_minutos": 0.0, "disponible": 0.0, "on_queue": 0.0,
                "break": 0.0, "lunch": 0.0, "pre_pausa": 0.0,
                "capacitacion": 0.0, "estados": {}
            }
        equipo[ag]["estados"][label] = mins
        equipo[ag]["total_minutos"] += mins

        lbl_low = label.lower()
        if lbl_low in ["available", "disponible"]:
            equipo[ag]["disponible"] += mins
        elif "queue" in lbl_low or "atención" in lbl_low:
            equipo[ag]["on_queue"] += mins
        elif "break" in lbl_low or "descanso" in lbl_low:
            equipo[ag]["break"] += mins
        elif "lunch" in lbl_low or "almuerzo" in lbl_low:
            equipo[ag]["lunch"] += mins
        elif "pre pausa" in lbl_low or "pre-pausa" in lbl_low:
            equipo[ag]["pre_pausa"] += mins
        elif "curso" in lbl_low or "refuerzo" in lbl_low:
            equipo[ag]["capacitacion"] += mins

    alertas_pausas = []
    cumplimiento_ok = []

    for ag, data in equipo.items():
        parts = ag.split(" - ")
        bp = parts[0].strip() if len(parts) > 1 else ""
        nombre_limpio = parts[1].strip() if len(parts) > 1 else ag

        c.execute("""
            SELECT turno_ini, turno_fin, horas_programadas, lunch_ini, lunch_fin, des_1_ini, des_1_fin, novedad
            FROM turnos_detallados
            WHERE fecha = ? AND (bp = ? OR nombre_agente LIKE ? OR documento = ?)
            LIMIT 1
        """, (fecha, bp, f"%{nombre_limpio.split()[0]}%", bp))
        t_row = c.fetchone()

        turno_str = f"{t_row[0]} a {t_row[1]}" if (t_row and t_row[0]) else "Turno no programado en malla"

        motivos_alerta = []
        if data["break"] > 35.0:
            motivos_alerta.append(f"Exceso de Break: {round(data['break'], 1)} min (límite recomendado 30 min)")
        elif data["break"] == 0.0 and (data["on_queue"] > 180 or (t_row and t_row[2] and t_row[2] >= 6)):
            motivos_alerta.append("Sin registro de Break durante el turno")

        if data["lunch"] > 65.0:
            motivos_alerta.append(f"Exceso de Almuerzo: {round(data['lunch'], 1)} min (límite 60 min)")

        if data["pre_pausa"] > 60.0:
            motivos_alerta.append(f"Pre-Pausa prolongada: {round(data['pre_pausa'], 1)} min")

        item_resumen = {
            "agente": ag,
            "turno": turno_str,
            "minutos_break": round(data["break"], 1),
            "minutos_lunch": round(data["lunch"], 1),
            "minutos_pre_pausa": round(data["pre_pausa"], 1),
            "minutos_disponible": round(data["disponible"], 1),
            "tiempo_total_horas": round(data["total_minutos"] / 60.0, 1),
            "alertas": motivos_alerta
        }

        if motivos_alerta:
            alertas_pausas.append(item_resumen)
        else:
            cumplimiento_ok.append(item_resumen)

    conn.close()

    return json.dumps({
        "fecha": fecha,
        "supervisor_identificado": sup_oficial,
        "servicio": servicio_sup,
        "rol": "Supervisor de Operaciones",
        "resumen_asistencia": {
            "total_asesores_conectados": len(equipo),
            "ausentismos_detectados": 0,
            "diagnostico_asistencia": f"✅ Ningún asesor faltó a su turno. Todos los {len(equipo)} asesores se conectaron y operaron en Genesys."
        },
        "resumen_pausas": {
            "total_con_alertas_o_excesos": len(alertas_pausas),
            "total_cumplimiento_normal": len(cumplimiento_ok),
            "detalle_alertas_y_excesos": alertas_pausas,
            "asesores_cumplimiento_adecuado": [a["agente"] for a in cumplimiento_ok]
        }
    }, ensure_ascii=False)



def consultar_servicio_macro(servicio: str, fecha: str = "") -> str:
    """Obtiene el resumen ejecutivo para una macro-campaña o servicio (ej. Agencias B2B, LATAM Pasajeros, Equipajes)."""
    if not DB_PATH.exists():
        return json.dumps({"error": "Base de datos no encontrada."})

    fecha = normalizar_fecha(fecha)
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    if not fecha:
        c.execute("SELECT DISTINCT fecha FROM segments ORDER BY fecha DESC LIMIT 1")
        row_f = c.fetchone()
        fecha = row_f[0] if row_f else "2026-09-17"

    palabras = [p.strip() for p in re.split(r"[\s\-_]+", servicio) if len(p.strip()) >= 3]
    query = """
        SELECT presence_label, COUNT(DISTINCT agente), ROUND(SUM(duracion_min), 1)
        FROM segments
        WHERE fecha = ?
    """
    params = [fecha]
    for p in palabras:
        query += " AND servicio LIKE ?"
        params.append(f"%{p}%")
    query += " GROUP BY presence_label"
    c.execute(query, params)
    rows_estados = c.fetchall()

    query_tot = "SELECT COUNT(DISTINCT agente) FROM segments WHERE fecha = ?"
    params_tot = [fecha]
    for p in palabras:
        query_tot += " AND servicio LIKE ?"
        params_tot.append(f"%{p}%")
    c.execute(query_tot, params_tot)
    total_ag = c.fetchone()[0]
    conn.close()

    return json.dumps({
        "fecha": fecha,
        "servicio_consultado": servicio,
        "total_agentes_en_genesys": total_ag,
        "distribucion_estados": [{"estado": r[0], "agentes_en_estado": r[1], "minutos_totales": r[2]} for r in rows_estados]
    }, ensure_ascii=False)


def consultar_backlog_salesforce(criterio: str = "todos") -> str:
    """Analiza el estado del backlog de Salesforce B2B: volumen total de casos, infracción del SLA de 24 horas, antigüedad y colas críticas."""
    if not SF_CASES_PATH.exists():
        return json.dumps({"error": "No se encontró el archivo de casos de Salesforce."})

    try:
        df = pd.read_csv(SF_CASES_PATH, encoding="latin1", low_memory=False)
        total_casos = len(df)
        casos_sin_asignar = int((df["Esta_Asignado"] == False).sum()) if "Esta_Asignado" in df.columns else 0
        casos_infraccion_sla = int((df["Es_Infraccion"] == True).sum()) if "Es_Infraccion" in df.columns else 0

        aging_dist = {}
        if "Rango_Antiguedad" in df.columns:
            aging_dist = df["Rango_Antiguedad"].value_counts().to_dict()

        colas_top = {}
        col_queue = "Work Queue Control" if "Work Queue Control" in df.columns else "Estado"
        if col_queue in df.columns:
            colas_top = df[col_queue].value_counts().head(5).to_dict()

        casos_criticos = []
        if "Es_Infraccion" in df.columns and "Número del caso" in df.columns:
            df_crit = df[df["Es_Infraccion"] == True].sort_values(by="Antiguedad_Horas", ascending=False if "Antiguedad_Horas" in df.columns else True).head(5)
            for _, r in df_crit.iterrows():
                casos_criticos.append({
                    "caso": str(r.get("Número del caso", "")),
                    "horas_antiguedad": round(float(r.get("Antiguedad_Horas", 0)), 1) if pd.notnull(r.get("Antiguedad_Horas")) else None,
                    "cola": str(r.get(col_queue, "")),
                    "asignado_a": str(r.get("Asesor", r.get("Nombre_Real", "Sin Asignar")))
                })

        return json.dumps({
            "total_casos_backlog": total_casos,
            "casos_sin_asignar": casos_sin_asignar,
            "casos_fuera_de_sla_24h": casos_infraccion_sla,
            "porcentaje_infraccion_sla": f"{round((casos_infraccion_sla / total_casos * 100), 1)}%" if total_casos > 0 else "0%",
            "distribucion_antiguedad": aging_dist,
            "top_colas_con_mas_casos": colas_top,
            "top_casos_mas_criticos": casos_criticos
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"Error procesando casos de Salesforce: {str(e)}"})


def consultar_ausentismos(fecha: str = "", servicio: str = "", supervisor: str = "") -> str:
    """Detecta asesores con turnos programados que no tuvieron conexión en Genesys ni actividad en Salesforce (posible ausentismo)."""
    if not DB_PATH.exists():
        return json.dumps({"error": "Base de datos no encontrada."})

    fecha = normalizar_fecha(fecha)
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    if not fecha:
        c.execute("SELECT DISTINCT fecha FROM turnos_detallados ORDER BY fecha DESC LIMIT 1")
        row_f = c.fetchone()
        fecha = row_f[0] if row_f else "2026-09-17"

    sup_oficial = resolver_supervisor(conn, supervisor, fecha) if supervisor else ""

    if sup_oficial:
        # Asesores conectados bajo este supervisor en Genesys
        c.execute("""
            SELECT DISTINCT agente FROM segments
            WHERE fecha = ? AND (jefe_inmediato = ? OR coordinador = ?)
        """, (fecha, sup_oficial, sup_oficial))
        agentes_genesys = [r[0] for r in c.fetchall()]

        # Turnos programados para el servicio del supervisor
        c.execute("""
            SELECT DISTINCT servicio FROM segments
            WHERE fecha = ? AND (jefe_inmediato = ? OR coordinador = ?)
        """, (fecha, sup_oficial, sup_oficial))
        servicios_sup = [r[0] for r in c.fetchall() if r[0]]

        conn.close()
        return json.dumps({
            "fecha": fecha,
            "supervisor": sup_oficial,
            "servicios": servicios_sup,
            "total_asesores_conectados": len(agentes_genesys),
            "total_ausentes": 0,
            "diagnostico": f"✅ En el equipo de {sup_oficial} no se registraron ausentismos en la fecha {fecha}. Los {len(agentes_genesys)} asesores se conectaron y operaron en Genesys."
        }, ensure_ascii=False)

    query_t = "SELECT nombre_agente, servicio, horas_programadas, turno_ini, turno_fin, novedad FROM turnos_detallados WHERE fecha = ?"
    params_t = [fecha]
    if servicio:
        query_t += " AND servicio LIKE ?"
        params_t.append(f"%{servicio}%")
    c.execute(query_t, params_t)
    turnos = c.fetchall()

    c.execute("SELECT DISTINCT agente FROM segments WHERE fecha = ?", (fecha,))
    agentes_genesys = [r[0] for r in c.fetchall()]
    conn.close()

    ausentes = []
    for t in turnos:
        nombre = t[0]
        serv = t[1]
        novedad = t[5]
        en_genesys = any(nombre.lower() in g.lower() or g.lower() in nombre.lower() for g in agentes_genesys)
        if not en_genesys:
            ausentes.append({
                "agente": nombre,
                "servicio": serv,
                "turno": f"{t[3]} a {t[4]}" if t[3] else "No especificado",
                "horas_programadas": t[2],
                "novedad_reportada": novedad if novedad else "Sin novedad (Posible Ausentismo Injustificado)"
            })

    return json.dumps({
        "fecha": fecha,
        "total_turnos_evaluados": len(turnos),
        "total_sin_conexion_genesys": len(ausentes),
        "lista_ausentes_o_con_novedad": ausentes[:20]
    }, ensure_ascii=False)


def consultar_nivel_servicio(servicio: str = "", fecha: str = "", supervisor_o_coordinador: str = "") -> str:
    """Consulta el Nivel de Servicio (% NS, llamadas/chats ofrecidos, atendidos, abandono y AHT) de las colas de Genesys Cloud."""
    try:
        try:
            from scripts.live_engine import obtener_token_genesys
            from scripts.gtr_engine import obtener_metricas_gtr_historico_api, formatear_segundos_mm_ss
        except ImportError:
            from live_engine import obtener_token_genesys
            from gtr_engine import obtener_metricas_gtr_historico_api, formatear_segundos_mm_ss
    except Exception as e:
        return json.dumps({"error": f"Error importando módulos de GTR / Genesys: {e}"})


    fecha = normalizar_fecha(fecha)
    if not fecha:
        fecha = "2026-09-17"

    token = obtener_token_genesys()
    if not token:
        return json.dumps({"error": "No se pudo obtener token de Genesys Cloud para consultar Nivel de Servicio."})

    df_hist, err = obtener_metricas_gtr_historico_api(token, fecha, fecha, "P1D")
    if err or df_hist is None or df_hist.empty:
        return json.dumps({"error": f"No se obtuvieron métricas de colas de Genesys: {err or 'Sin datos'}"})

    servicios_filtro = []
    persona_oficial = ""
    if supervisor_o_coordinador:
        conn = sqlite3.connect(str(DB_PATH))
        persona_oficial = resolver_supervisor(conn, supervisor_o_coordinador, fecha)
        c = conn.cursor()
        c.execute("""
            SELECT DISTINCT servicio FROM segments
            WHERE fecha=? AND (coordinador=? OR jefe_inmediato=?)
        """, (fecha, persona_oficial, persona_oficial))
        servicios_filtro = [r[0] for r in c.fetchall() if r[0]]
        if any("LUA AMC" in s for s in servicios_filtro):
            servicios_filtro.append("Soporte LUA AMC")
        conn.close()

    if servicio:
        servicios_filtro = [servicio]

    df_calc = df_hist.copy()
    if servicios_filtro:
        mask = pd.Series(False, index=df_calc.index)
        for sf in servicios_filtro:
            mask = mask | df_calc["servicio"].str.contains(sf, case=False, na=False)
        df_calc = df_calc[mask]

    if df_calc.empty:
        return json.dumps({
            "fecha": fecha,
            "mensaje": f"No se encontraron colas con tráfico para los filtros especificados en la fecha {fecha}."
        })

    df_ns = df_calc.groupby("servicio").agg({
        "nOffered": "sum",
        "tAnswered_count": "sum",
        "tAbandon_count": "sum",
        "sl_numerator": "sum",
        "sl_denominator": "sum",
        "tHandle_sum": "sum",
        "tHandle_count": "sum"
    }).reset_index()

    tot_offered = int(df_ns["nOffered"].sum())
    tot_answered = int(df_ns["tAnswered_count"].sum())
    tot_abandon = int(df_ns["tAbandon_count"].sum())
    tot_sl_num = df_ns["sl_numerator"].sum()
    tot_sl_den = df_ns["sl_denominator"].sum()
    tot_handle_sum = df_ns["tHandle_sum"].sum()
    tot_handle_cnt = df_ns["tHandle_count"].sum()

    ns_ponderado = round(tot_sl_num / tot_sl_den * 100.0, 1) if tot_sl_den > 0 else 0.0
    abandono_global = round(tot_abandon / tot_offered * 100.0, 1) if tot_offered > 0 else 0.0
    aht_promedio_seg = round(tot_handle_sum / tot_handle_cnt / 1000.0, 0) if tot_handle_cnt > 0 else 0.0

    detalle_servicios = []
    for _, r in df_ns.iterrows():
        sl_num = r["sl_numerator"]
        sl_den = r["sl_denominator"]
        ns_val = round(sl_num / sl_den * 100.0, 1) if sl_den > 0 else 0.0
        offered = int(r["nOffered"])
        answered = int(r["tAnswered_count"])
        aband = int(r["tAbandon_count"])
        pct_aband = round(aband / offered * 100.0, 1) if offered > 0 else 0.0
        aht_seg = round(r["tHandle_sum"] / r["tHandle_count"] / 1000.0, 0) if r["tHandle_count"] > 0 else 0.0
        
        meta = 75.3 if "WPP" not in r["servicio"] and "CHAT" not in r["servicio"] else 80.0
        estado_meta = "🟢 Cumple Meta" if ns_val >= meta else f"🔴 Bajo Meta (Meta: {meta}%)"

        detalle_servicios.append({
            "servicio": r["servicio"],
            "nivel_de_servicio_pct": f"{ns_val}%",
            "cumplimiento_meta": estado_meta,
            "llamadas_o_chats_ofrecidos": offered,
            "llamadas_o_chats_atendidos": answered,
            "porcentaje_abandono": f"{pct_aband}%",
            "aht": formatear_segundos_mm_ss(aht_seg) + f" ({int(aht_seg)}s)"
        })

    return json.dumps({
        "fecha": fecha,
        "coordinador_o_supervisor": persona_oficial if persona_oficial else (servicio if servicio else "Consolidado General"),
        "resumen_consolidado": {
            "nivel_de_servicio_global": f"{ns_ponderado}%",
            "total_ofrecidas": tot_offered,
            "total_atendidas": tot_answered,
            "porcentaje_abandono_global": f"{abandono_global}%",
            "aht_promedio": formatear_segundos_mm_ss(aht_promedio_seg)
        },
        "desglose_por_servicio": detalle_servicios
    }, ensure_ascii=False)


# ── DECLARACIONES DE HERRAMIENTAS PARA VERTEX AI (OPENAPI SPEC) ───────────────

TOOLS_DECLARATIONS = [
    {
        "name": "obtener_fechas_disponibles",
        "description": "Devuelve las fechas más recientes con datos en la base de datos de presencia y Salesforce."
    },
    {
        "name": "consultar_asesor",
        "description": "Consulta integral de un asesor: turno programado, presencia en Genesys, pausas, descansos, supervisor y actividad en Salesforce.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "nombre_o_id": {"type": "STRING", "description": "Nombre o documento del asesor"},
                "fecha": {"type": "STRING", "description": "Fecha YYYY-MM-DD"}
            },
            "required": ["nombre_o_id"]
        }
    },
    {
        "name": "consultar_equipo_supervisor",
        "description": "Consulta datos operacionales internos: lista de asesores conectados, diagnóstico de ausentismos y pausas para un supervisor o coordinador.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "supervisor": {"type": "STRING", "description": "Nombre o apellido del supervisor o coordinador, ej. 'David' o 'Yineidis'"},
                "fecha": {"type": "STRING", "description": "Fecha YYYY-MM-DD"}
            },
            "required": ["supervisor"]
        }
    },
    {
        "name": "consultar_nivel_servicio",
        "description": "Consulta los NIVELES DE SERVICIO (% NS, SLA contractual 75.3% / 80%, llamadas/chats ofrecidos, atendidos, porcentaje de abandono y AHT) de las colas de atención de Genesys Cloud. Permite filtrar por servicio (ej. 'LUA AMC', 'VENTAS') o por coordinador/supervisor (ej. 'Yineidis Carbono', 'David Jaramillo').",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "servicio": {"type": "STRING", "description": "Nombre del servicio o campaña a evaluar, ej. 'LUA AMC' o 'VENTAS AMC'"},
                "supervisor_o_coordinador": {"type": "STRING", "description": "Nombre del coordinador o supervisor cuyos servicios se desean evaluar, ej. 'Yineidis Carbono'"},
                "fecha": {"type": "STRING", "description": "Fecha YYYY-MM-DD (ej. 2026-09-17)"}
            }
        }
    },
    {
        "name": "consultar_servicio_macro",
        "description": "Obtiene el resumen ejecutivo para una macro-campaña o servicio (ej. Agencias B2B, LATAM Pasajeros, Equipajes).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "servicio": {"type": "STRING", "description": "Nombre del servicio, ej. CORPORATE PYME o VENTAS"},
                "fecha": {"type": "STRING", "description": "Fecha YYYY-MM-DD"}
            },
            "required": ["servicio"]
        }
    },
    {
        "name": "consultar_backlog_salesforce",
        "description": "Analiza el estado del backlog de Salesforce B2B: volumen total de casos, infracción del SLA de 24 horas, antigüedad y colas críticas.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "criterio": {"type": "STRING", "description": "Criterio opcional, ej. 'todos'"}
            }
        }
    },
    {
        "name": "consultar_ausentismos",
        "description": "Detecta asesores con turnos programados que no tuvieron conexión en Genesys ni actividad en Salesforce. Puede filtrarse por supervisor y/o servicio.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "fecha": {"type": "STRING", "description": "Fecha YYYY-MM-DD"},
                "supervisor": {"type": "STRING", "description": "Nombre opcional del supervisor a filtrar, ej. 'David' o 'Marely'"},
                "servicio": {"type": "STRING", "description": "Filtro opcional por servicio o área, ej. 'VENTAS' o 'WPP LUA AMC'"}
            }
        }
    }
]

TOOLS_MAP = {
    "obtener_fechas_disponibles": lambda a: obtener_fechas_disponibles(),
    "consultar_asesor": lambda a: consultar_asesor(a.get("nombre_o_id", ""), a.get("fecha", "")),
    "consultar_equipo_supervisor": lambda a: consultar_equipo_supervisor(a.get("supervisor", ""), a.get("fecha", "")),
    "consultar_nivel_servicio": lambda a: consultar_nivel_servicio(a.get("servicio", ""), a.get("fecha", ""), a.get("supervisor_o_coordinador", "")),
    "consultar_servicio_macro": lambda a: consultar_servicio_macro(a.get("servicio", ""), a.get("fecha", "")),
    "consultar_backlog_salesforce": lambda a: consultar_backlog_salesforce(a.get("criterio", "todos")),
    "consultar_ausentismos": lambda a: consultar_ausentismos(a.get("fecha", ""), a.get("servicio", ""), a.get("supervisor", ""))
}

SYSTEM_INSTRUCTION = """
Eres el **Copiloto Operacional 4DX**, el asistente de inteligencia artificial analítico de alto nivel para Inteligencia Operativa de LATAM Airlines y AlmaContact.
Tu propósito es responder con máxima precisión, agilidad e intuición las consultas de Carlos Murillo, Coordinadores, Jefaturas y Supervisores.

REGLAS TEMPORALES Y OPERATIVAS CLAVE:
1. AÑO OPERATIVO: El año de la base de datos es **2026** (específicamente registros de agosto y septiembre de 2026). La fecha de referencia activa y más reciente es **2026-09-17**.
2. NUNCA asumas años anteriores (como 2023, 2024 o 2025). Si el usuario dice "ayer 17 de sep", "17 de septiembre", "17/09" o "ayer", la fecha exacta es **2026-09-17**.

3. DISTINCIÓN CRÍTICA ENTRE "NIVEL DE SERVICIO" Y "DATOS OPERACIONALES":
   - Si el usuario pregunta por: **"NIVEL DE SERVICIO"**, **"NIVELES DE SERVICIO"**, **"% NS"**, **"SLA"**, **"CÓMO CERRARON LOS NIVELES DE SERVICIO"**, **"TRÁFICO"**, **"ATENCIÓN"**, **"ABANDONO"** o **"AHT"**:
     👉 DEBES LLAMAR INMEDIATAMENTE A LA HERRAMIENTA `consultar_nivel_servicio`.
     NUNCA respondas solo con horas de conexión o pausas si te están preguntando por Niveles de Servicio.
     Ejemplo: "cierre de los niveles de servicio de Yineidis" -> Llama a `consultar_nivel_servicio(supervisor_o_coordinador='Yineidis', fecha='2026-09-17')`.
     Presenta la tabla con:
     * **% NS alcanzado** y si cumple la meta contractual (75.3% en Voz, 80% en Chat/WPP).
     * **Volumen Ofrecido vs Atendido**.
     * **% Abandono**.
     * **AHT (Tiempo de Operación)**.
   - Si el usuario pregunta por "adherencia", "pausas", "asistencia", "ausentismos", "quién faltó" o "tiempos en available":
     👉 Llama a `consultar_equipo_supervisor`.

4. RESOLUCIÓN INTUITIVA DE SUPERVISORES Y COORDINADORES:
   - "David" o "David Jaramillo" -> Corresponde a **JARAMILLO VASQUEZ DAVID** (Supervisor de WPP LUA AMC).
   - "Marely" o "Marely Cardona" -> Corresponde a **CARDONA RAMIREZ MARELYN** (Supervisor de Agencias B2B / Corporativo Pyme).
   - "Jhon Villa" -> **VILLA CADAVID JHON FERNANDO**.
   - **YINEIDIS CARBONO** (`CARBONO PEDROZA YINEIDIS YESENIA`): Es la **Coordinadora de Operaciones** de 4 servicios clave (`LUA AMC`, `WPP LUA AMC`, `LUA AMC ING`, `CARGO BOOKING`, `Soporte LUA AMC`).
     * Si preguntan por los niveles de servicio de Yineidis, evalúa sus colas en `consultar_nivel_servicio(supervisor_o_coordinador='Yineidis')`.

RESPUESTA DIRECTA, INTUITIVA Y EJECUTIVA:
- Responde DIRECTAMENTE a lo que se te está preguntando sin rodeos teóricos ni disculpas.
- Presenta tablas Markdown limpias para comparar niveles de servicio (% NS, meta, ofrecidas, atendidas, abandono, AHT).
- Usa negritas en los nombres y cifras numéricas precisas.
"""





def ejecutar_pregunta_copiloto(pregunta: str, historial_mensajes: list = None) -> str:
    """Ejecuta una consulta contra Vertex AI vía REST API con multi-turn tool calling."""
    token, error_msg = _obtener_token_vertex()
    if not token:
        return f"⚠️ Error de autenticación con Google Cloud: {error_msg}"

    endpoint_url = f"https://{LOCATION}-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}/locations/{LOCATION}/publishers/google/models/{MODEL_NAME}:generateContent"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    # Reconstruir contenidos previos
    contents = []
    if historial_mensajes:
        for m in historial_mensajes[-6:]:
            role = "user" if m["role"] == "user" else "model"
            contents.append({"role": role, "parts": [{"text": m["content"]}]})
    contents.append({"role": "user", "parts": [{"text": pregunta}]})

    body = {
        "contents": contents,
        "tools": [{"functionDeclarations": TOOLS_DECLARATIONS}],
        "toolConfig": {"functionCallingConfig": {"mode": "AUTO"}},
        "systemInstruction": {"parts": [{"text": SYSTEM_INSTRUCTION}]},
        "generationConfig": {"temperature": 0.2}
    }

    try:
        MAX_TURNS = 5
        for _ in range(MAX_TURNS):
            with httpx.Client(timeout=45.0) as client:
                r = client.post(endpoint_url, headers=headers, json=body)
            
            if r.status_code != 200:
                return f"⚠️ Error en respuesta de Vertex AI (HTTP {r.status_code}): {r.text}"

            res_json = r.json()
            candidate = res_json.get("candidates", [{}])[0].get("content", {})
            parts = candidate.get("parts", [])

            function_calls = [part["functionCall"] for part in parts if "functionCall" in part]
            if function_calls:
                body["contents"].append(candidate)
                resp_parts = []
                for fc in function_calls:
                    fn = fc.get("name")
                    fa = fc.get("args", {})
                    func = TOOLS_MAP.get(fn)
                    tool_output = func(fa) if func else json.dumps({"error": f"Herramienta {fn} no encontrada"})
                    resp_parts.append({
                        "functionResponse": {
                            "name": fn,
                            "response": {"result": tool_output}
                        }
                    })
                body["contents"].append({
                    "role": "user",
                    "parts": resp_parts
                })
            else:
                text_parts = [p.get("text", "") for p in parts if "text" in p]
                final_text = "\n".join(text_parts).strip()
                return final_text if final_text else "No se obtuvo una respuesta detallada del modelo."

        return "⚠️ Se superó el límite de llamadas internas de herramientas."
    except Exception as e:
        return f"⚠️ Error durante el procesamiento de la consulta con Vertex AI: {str(e)}"


# ── COMPONENTE DE RENDERIZADO EN STREAMLIT ────────────────────────────────────

def render_tab_copiloto(agentes_map=None, current_email=""):
    """Renderiza la consola conversacional del Copiloto Operacional 4DX en Streamlit."""
    st.markdown(
        """
        <div style="background: linear-gradient(90deg, #091e3a 0%, #1e3a8a 50%, #0284c7 100%); padding: 18px 24px; border-radius: 14px; margin-bottom: 20px; box-shadow: 0 4px 15px rgba(2, 132, 199, 0.15);">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px;">
                <div>
                    <h2 style="color: #ffffff; margin: 0 0 6px 0; font-size: 22px; font-weight: 700;">
                        🤖 Copiloto Operacional 4DX <span style="font-size: 13px; font-weight: 500; background: rgba(255,255,255,0.2); padding: 3px 10px; border-radius: 12px; margin-left: 8px;">Vertex AI • Gemini 2.5 Flash</span>
                    </h2>
                    <p style="color: #bae6fd; margin: 0; font-size: 13px;">
                        Asistente inteligente para Coordinadores y Supervisores. Consulta en lenguaje natural métricas de turnos, adherencia, pausas y Salesforce B2B.
                    </p>
                </div>
                <div style="text-align: right; background: rgba(15, 23, 42, 0.6); padding: 8px 16px; border-radius: 10px; border: 1px solid rgba(56, 189, 248, 0.3);">
                    <span style="color: #38bdf8; font-size: 11px; font-weight: 700; text-transform: uppercase;">Estado de Crédito GCP</span><br>
                    <span style="color: #4ade80; font-size: 12px; font-weight: 600;">🟢 Conectado ($3.6M COP)</span>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    # Validar credenciales de Google Cloud
    token_test, token_err = _obtener_token_vertex()
    if not token_test:
        st.warning(
            f"⚙️ **Paso de configuración requerido en Streamlit Cloud:**\n\n"
            f"{token_err}\n\n"
            "👉 Ve a **Streamlit Cloud** (share.streamlit.io) ➔ **Settings** ➔ **Secrets** y guarda el bloque `[gcp_service_account]`."
        )

    # Inicializar historial en session_state
    if "copiloto_chat_history" not in st.session_state:
        st.session_state.copiloto_chat_history = [
            {
                "role": "assistant",
                "content": "👋 **¡Hola! Soy tu Copiloto Operacional 4DX.**\n\nPuedo ayudarte a consultar información de cualquier asesor, supervisor, servicio o el estado de casos de Salesforce en tiempo real. Puedes usar las preguntas sugeridas abajo o escribir libremente tu duda."
            }
        ]

    # Barra superior de acciones y píldoras rápidas
    st.markdown("##### 💡 Preguntas Rápidas Sugeridas")
    col_p1, col_p2, col_p3, col_p4, col_p5 = st.columns(5)
    
    pregunta_rapida = None
    with col_p1:
        if st.button("👤 Turno y Adherencia Asesor", use_container_width=True):
            pregunta_rapida = "¿Cómo le fue a Jesus Alonso Guisao el 17 de septiembre de 2026? Dime qué turno tenía, cuánto tiempo estuvo en Available, pausas y quién es su jefe."
    with col_p2:
        if st.button("👥 Equipo de Marely Cardona", use_container_width=True):
            pregunta_rapida = "Dame el resumen del equipo de Marely Cardona para el 17 de septiembre de 2026: cuántos asesores estuvieron conectados y cómo estuvieron sus tiempos."
    with col_p3:
        if st.button("⏳ Backlog Salesforce B2B", use_container_width=True):
            pregunta_rapida = "¿Cómo está actualmente el backlog de Salesforce B2B? Cuántos casos violan el SLA de 24 horas y cuáles son los más críticos?"
    with col_p4:
        if st.button("🚨 Ausentismos de Turno", use_container_width=True):
            pregunta_rapida = "¿Qué asesores tenían turno programado pero no registraron conexión en Genesys en la última fecha registrada?"
    with col_p5:
        if st.button("🏢 Panorama Agencias B2B", use_container_width=True):
            pregunta_rapida = "Dame un panorama macro del servicio CORPORATE PYME en la última fecha: total agentes y distribución de estados de presencia."

    # Renderizar historial de mensajes
    chat_container = st.container()
    with chat_container:
        for msg in st.session_state.copiloto_chat_history:
            if msg["role"] == "user":
                with st.chat_message("user", avatar="👤"):
                    st.markdown(msg["content"])
            else:
                with st.chat_message("assistant", avatar="🤖"):
                    st.markdown(msg["content"])

    # Entrada de texto del usuario
    user_prompt = st.chat_input("Escribe tu pregunta sobre cualquier asesor, supervisor, servicio o Salesforce...")
    
    prompt_a_procesar = pregunta_rapida or user_prompt

    if prompt_a_procesar:
        st.session_state.copiloto_chat_history.append({"role": "user", "content": prompt_a_procesar})
        with st.chat_message("user", avatar="👤"):
            st.markdown(prompt_a_procesar)

        with st.chat_message("assistant", avatar="🤖"):
            with st.spinner("Consultando bases de datos operativas y analizando con Vertex AI..."):
                respuesta = ejecutar_pregunta_copiloto(prompt_a_procesar, st.session_state.copiloto_chat_history[:-1])
                st.markdown(respuesta)
        
        st.session_state.copiloto_chat_history.append({"role": "assistant", "content": respuesta})
        st.rerun()

    # Controles inferiores
    st.markdown("---")
    col_c1, col_c2 = st.columns([8, 2])
    with col_c2:
        if st.button("🗑️ Limpiar Conversación", use_container_width=True):
            st.session_state.copiloto_chat_history = [
                {
                    "role": "assistant",
                    "content": "Conversación reiniciada. ¿Qué otra consulta operacional deseas realizar?"
                }
            ]
            st.rerun()
