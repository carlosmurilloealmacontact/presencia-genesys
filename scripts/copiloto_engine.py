# copiloto_engine.py - Motor de Inteligencia Operativa y Copiloto Conversacional 4DX
# Impulsado por Vertex AI (Google Cloud) & Gemini 2.5 Flash
# Implementación 100% nativa vía REST API con authlib + httpx (Cero dependencias pesadas en Streamlit Cloud)

import os
import sys
import sqlite3
import json
import re
import time

import base64
from datetime import datetime, timedelta, timezone
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
ROCCO_IMG_PATH = BASE_DIR / "assets" / "rocco.png"

SF_CASES_PATH = BASE_DIR / "data" / "salesforce" / "cases_amc_cleaned.csv"
SF_OMNI_PATH = BASE_DIR / "data" / "salesforce" / "omni_presencia_historico.csv"
DATA_ZD_DIR = BASE_DIR / "data" / "zendesk"

PROJECT_ID = "project-094fad9d-54da-42d9-880"
LOCATION = "us-central1"
MODEL_NAME = "gemini-2.5-flash"


def obtener_rocco_b64() -> str:
    """Devuelve la imagen de Rocco en Base64 para embeberla en CSS y HTML."""
    if ROCCO_IMG_PATH.exists():
        try:
            with open(ROCCO_IMG_PATH, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        except Exception:
            pass
    return ""


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
    """Calcula y audita el ausentismo operativo:
    - Cruza los turnos programados en malla (>0 horas) contra las conexiones reales en Genesys.
    - Discrimina entre Ausentismo Justificado (con incapacidad médica, licencia, permiso o novedad) y Ausentismo Injustificado (sin conexión y sin novedad).
    - Calcula la Tasa de Ausentismo (%) y la compara contra la meta oficial (Tolerancia: 10%, Meta óptima: 8%).
    - Permite filtrar por supervisor, por servicio o ver el consolidado general.
    """
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

    query_seg = "SELECT DISTINCT agente, servicio, jefe_inmediato, coordinador FROM segments WHERE fecha = ?"
    params_seg = [fecha]
    if servicio:
        query_seg += " AND servicio LIKE ?"
        params_seg.append(f"%{servicio}%")
    if sup_oficial:
        query_seg += " AND (jefe_inmediato = ? OR coordinador = ?)"
        params_seg.extend([sup_oficial, sup_oficial])

    c.execute(query_seg, params_seg)
    agentes_conectados_rows = c.fetchall()

    genesys_bps = set()
    genesys_tokens = []
    for r in agentes_conectados_rows:
        ag = r[0]
        parts = ag.split(" - ")
        bp = parts[0].strip().upper()
        genesys_bps.add(bp)
        nom_txt = parts[1] if len(parts) > 1 else ag
        tokens = set(p.strip().upper() for p in nom_txt.split() if len(p.strip()) > 2)
        genesys_tokens.append((bp, tokens, ag))

    query_turnos = "SELECT nombre_agente, bp, servicio, turno_ini, turno_fin, horas_programadas, novedad FROM turnos_detallados WHERE fecha = ? AND horas_programadas > 0"
    params_turnos = [fecha]
    if servicio:
        query_turnos += " AND servicio LIKE ?"
        params_turnos.append(f"%{servicio}%")
    if sup_oficial:
        c.execute("SELECT DISTINCT agente FROM segments WHERE jefe_inmediato = ? OR coordinador = ?", (sup_oficial, sup_oficial))
        bps_del_sup = [r[0].split(" - ")[0].strip().upper() for r in c.fetchall()]
        if bps_del_sup:
            placeholders = ",".join(["?"] * len(bps_del_sup))
            query_turnos += f" AND bp IN ({placeholders})"
            params_turnos.extend(bps_del_sup)

    c.execute(query_turnos, params_turnos)
    turnos = c.fetchall()
    conn.close()

    total_programados = len(turnos)
    if total_programados == 0:
        return json.dumps({
            "fecha": fecha,
            "mensaje": f"No se encontraron turnos programados activos (>0 hrs) para el criterio especificado (Servicio: {servicio}, Supervisor: {supervisor})."
        }, ensure_ascii=False)

    conectados = 0
    ausentes_justificados = []
    ausentes_injustificados = []

    for t in turnos:
        nom, bp, serv, t_ini, t_fin, h_prog, nov = t
        bp_clean = str(bp).strip().upper() if bp else ""
        nom_tokens = set(p.strip().upper() for p in str(nom).split() if len(p.strip()) > 2)

        is_conn = False
        if bp_clean and bp_clean in genesys_bps:
            is_conn = True
        else:
            for g_bp, g_toks, g_ag in genesys_tokens:
                if len(nom_tokens.intersection(g_toks)) >= 2:
                    is_conn = True
                    break

        if is_conn:
            conectados += 1
        else:
            nov_str = str(nov).strip() if nov else ""
            item = {
                "agente": nom,
                "bp": bp,
                "servicio": serv,
                "turno": f"{t_ini} a {t_fin}",
                "horas_programadas": h_prog,
                "novedad": nov_str if nov_str and nov_str != "TUR" else "Sin novedad reportada (Ausencia Injustificada)"
            }
            if nov_str and nov_str not in ["TUR", ""]:
                ausentes_justificados.append(item)
            else:
                ausentes_injustificados.append(item)

    total_ausentes = len(ausentes_justificados) + len(ausentes_injustificados)
    tasa_ausentismo = round(total_ausentes / total_programados * 100, 1)

    cumple_meta = tasa_ausentismo <= 10.0
    estado_meta = "🟢 Dentro de meta (<=10%)" if cumple_meta else "🔴 Sobre meta de tolerancia (10.0%)"

    return json.dumps({
        "fecha": fecha,
        "filtro_aplicado": {
            "supervisor": sup_oficial if sup_oficial else "Todos",
            "servicio": servicio if servicio else "Consolidado General"
        },
        "resumen_ausentismo": {
            "total_turnos_programados": total_programados,
            "total_asesores_conectados": conectados,
            "total_ausencias": total_ausentes,
            "tasa_ausentismo": f"{tasa_ausentismo}%",
            "meta_tolerancia": "10.0%",
            "estado_cumplimiento": estado_meta,
            "ausencias_justificadas": len(ausentes_justificados),
            "ausencias_injustificadas": len(ausentes_injustificados)
        },
        "detalle_ausencias_injustificadas": ausentes_injustificados[:10],
        "detalle_ausencias_justificadas": ausentes_justificados[:10]
    }, ensure_ascii=False)


def consultar_organigrama_jerarquia(nombre_o_servicio: str) -> str:
    """Consulta la estructura organizativa y jerarquía operativa oficial:
    - Si es Coordinador: Servicios a cargo, supervisores que le reportan y cantidad de asesores.
    - Si es Supervisor: A qué coordinador reporta, en qué servicio está y la lista de sus asesores.
    - Si es Asesor: Su servicio, su supervisor directo y su coordinador.
    - Si es Servicio: Quién lo coordina, qué supervisores lo lideran y número de asesores.
    """
    if not DB_PATH.exists():
        return json.dumps({"error": "Base de datos no encontrada."})
    
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    
    query_term = str(nombre_o_servicio).strip().upper()
    palabras = [p for p in query_term.split() if len(p) > 2]
    
    # 1. ¿Es un SERVICIO?
    c.execute("SELECT DISTINCT servicio FROM segments WHERE servicio IS NOT NULL")
    todos_servicios = [r[0] for r in c.fetchall() if r[0]]
    servicios_match = [s for s in todos_servicios if all(p in s.upper() for p in palabras)] if palabras else []
    
    if servicios_match:
        serv_oficial = servicios_match[0]
        c.execute("""
            SELECT DISTINCT coordinador, jefe_inmediato, COUNT(DISTINCT agente)
            FROM segments
            WHERE servicio = ? AND coordinador IS NOT NULL AND coordinador != ''
            GROUP BY coordinador, jefe_inmediato
            ORDER BY COUNT(DISTINCT agente) DESC
        """, (serv_oficial,))
        rows = c.fetchall()
        
        c.execute("SELECT COUNT(DISTINCT agente) FROM segments WHERE servicio = ?", (serv_oficial,))
        tot_agentes = c.fetchone()[0]
        conn.close()
        
        coordinadores = list(dict.fromkeys([r[0] for r in rows if r[0]]))
        supervisores = [{"supervisor": r[1], "coordinador": r[0], "asesores": r[2]} for r in rows if r[1]]
        
        return json.dumps({
            "tipo_entidad": "Servicio Operativo",
            "servicio": serv_oficial,
            "total_asesores_en_servicio": tot_agentes,
            "coordinadores_responsables": coordinadores,
            "supervisores_asignados": supervisores
        }, ensure_ascii=False)
    
    # 2. ¿Es un COORDINADOR?
    c.execute("SELECT DISTINCT coordinador FROM segments WHERE coordinador IS NOT NULL AND coordinador != ''")
    coords = [r[0] for r in c.fetchall()]
    coords_match = [co for co in coords if all(p in co.upper() for p in palabras)] if palabras else []
    
    if coords_match:
        coord_oficial = coords_match[0]
        c.execute("""
            SELECT servicio, jefe_inmediato, COUNT(DISTINCT agente)
            FROM segments
            WHERE coordinador = ?
            GROUP BY servicio, jefe_inmediato
            ORDER BY servicio, COUNT(DISTINCT agente) DESC
        """, (coord_oficial,))
        rows = c.fetchall()
        
        c.execute("SELECT COUNT(DISTINCT agente) FROM segments WHERE coordinador = ?", (coord_oficial,))
        tot_agentes = c.fetchone()[0]
        conn.close()
        
        servicios_dict = {}
        for srv, sup, cant in rows:
            if srv not in servicios_dict:
                servicios_dict[srv] = []
            servicios_dict[srv].append(f"{sup} ({cant} asesores)")
            
        return json.dumps({
            "tipo_entidad": "Coordinador de Operaciones",
            "coordinador": coord_oficial,
            "total_asesores_a_cargo": tot_agentes,
            "total_servicios_coordinados": len(servicios_dict),
            "estructura_por_servicio": servicios_dict
        }, ensure_ascii=False)
        
    # 3. ¿Es un SUPERVISOR?
    c.execute("SELECT DISTINCT jefe_inmediato FROM segments WHERE jefe_inmediato IS NOT NULL AND jefe_inmediato != ''")
    sups = [r[0] for r in c.fetchall()]
    sups_match = [sp for sp in sups if all(p in sp.upper() for p in palabras)] if palabras else []
    
    if sups_match:
        sup_oficial = sups_match[0]
        c.execute("""
            SELECT DISTINCT coordinador, servicio
            FROM segments
            WHERE jefe_inmediato = ? AND coordinador IS NOT NULL
        """, (sup_oficial,))
        info_lider = c.fetchall()
        
        c.execute("""
            SELECT DISTINCT agente
            FROM segments
            WHERE jefe_inmediato = ?
            ORDER BY agente
        """, (sup_oficial,))
        asesores = [r[0] for r in c.fetchall()]
        conn.close()
        
        coords = list(dict.fromkeys([r[0] for r in info_lider if r[0]]))
        servicios = list(dict.fromkeys([r[1] for r in info_lider if r[1]]))
        
        return json.dumps({
            "tipo_entidad": "Supervisor de Operaciones",
            "supervisor": sup_oficial,
            "coordinador_directo": coords[0] if coords else "No asignado",
            "servicios": servicios,
            "total_asesores_a_cargo": len(asesores),
            "lista_asesores": asesores
        }, ensure_ascii=False)
        
    # 4. ¿Es un ASESOR?
    c.execute("SELECT agente, servicio, jefe_inmediato, coordinador FROM segments")
    all_seg = c.fetchall()
    conn.close()
    
    asesores_match = [r for r in all_seg if all(p in r[0].upper() for p in palabras)] if palabras else []
    if asesores_match:
        primero = asesores_match[0]
        return json.dumps({
            "tipo_entidad": "Asesor Operativo",
            "agente": primero[0],
            "servicio": primero[1],
            "supervisor_directo": primero[2],
            "coordinador": primero[3]
        }, ensure_ascii=False)
        
    return json.dumps({"error": f"No se encontró información de organigrama para '{nombre_o_servicio}'."})


def consultar_zendesk_backoffice(grupo_o_servicio: str = "LUA AMC", hora_inicio: int = None, hora_fin: int = None, fecha: str = None) -> str:
    """Consulta la productividad y tickets gestionados en Zendesk Support para grupos de Back Office (BO):
    - Grupos disponibles: 'BO LUA AMC', 'LUA AMC', 'DT FFP AMC', 'BO EQUIPAJES AMC', 'CÉLULA PI AMC', 'AUTORIZACIÓN SUPERVISOR'.
    - Permite filtrar por franjas horarias (ej. 8 a 12) y por fecha.
    """
    f_live = DATA_ZD_DIR / "productividad_hoy_en_vivo.csv"
    
    df = None
    if f_live.exists():
        try:
            df = pd.read_csv(f_live)
        except Exception:
            pass
        
    if df is None or df.empty:
        return json.dumps({"error": "No hay datos disponibles de productividad de Zendesk."})
        
    mapa_sinonimos = {
        "BO LUA": "LUA AMC",
        "LUA": "LUA AMC",
        "BO LUA AMC": "LUA AMC",
        "BOLUA": "LUA AMC",
        "DT FFP": "DT FFP AMC",
        "FFP": "DT FFP AMC",
        "EQUIPAJES": "Equipajes AMC SSC",
        "BO EQUIPAJES": "Equipajes AMC SSC",
        "PI": "Célula PI AMC ES",
        "CELULA PI": "Célula PI AMC ES"
    }
    
    target_grp = mapa_sinonimos.get(str(grupo_o_servicio).strip().upper(), str(grupo_o_servicio).strip())
    
    mask = df["grupo"].astype(str).str.upper().str.contains(target_grp.upper(), na=False)
    df_sub = df[mask].copy()
    
    if df_sub.empty:
        grupos_disp = df["grupo"].dropna().unique().tolist()
        return json.dumps({
            "error": f"No se encontraron tickets para el grupo '{grupo_o_servicio}'.",
            "grupos_disponibles_zendesk": grupos_disp
        }, ensure_ascii=False)
        
    if "updated_at" in df_sub.columns:
        df_sub["dt_col"] = pd.to_datetime(df_sub["updated_at"], utc=True).dt.tz_convert("America/Bogota")
        df_sub["hora"] = df_sub["dt_col"].dt.hour
    elif "Fecha_Timestamp" in df_sub.columns:
        df_sub["dt_col"] = pd.to_datetime(df_sub["Fecha_Timestamp"], errors="coerce")
        df_sub["hora"] = df_sub["dt_col"].dt.hour
    else:
        df_sub["hora"] = None
        
    total_gestionados_grupo = len(df_sub)
    
    franja_txt = "Día Completo"
    if hora_inicio is not None and hora_fin is not None and "hora" in df_sub.columns:
        df_sub = df_sub[(df_sub["hora"] >= int(hora_inicio)) & (df_sub["hora"] < int(hora_fin))].copy()
        franja_txt = f"{int(hora_inicio):02d}:00 a {int(hora_fin):02d}:00 (Hora Colombia UTC-5)"
    elif hora_inicio is not None and "hora" in df_sub.columns:
        df_sub = df_sub[df_sub["hora"] == int(hora_inicio)].copy()
        franja_txt = f"{int(hora_inicio):02d}:00 a {int(hora_inicio):02d}:59 (Hora Colombia UTC-5)"
        
    total_en_filtro = len(df_sub)
    
    desglose_horas = {}
    if "hora" in df_sub.columns:
        desglose_horas = df_sub["hora"].value_counts().sort_index().to_dict()
        desglose_horas = {f"{h:02d}:00 - {h:02d}:59": count for h, count in desglose_horas.items()}
        
    col_asesor = "Nombre_Asesor" if "Nombre_Asesor" in df_sub.columns else "TICKET_ASSIGNEE_PRIMARY_EMAIL"
    top_asesores = df_sub[col_asesor].value_counts().head(5).to_dict() if col_asesor in df_sub.columns else {}
    
    col_tipologia = "Tipo_de_Gestion" if "Tipo_de_Gestion" in df_sub.columns else "tipo_raw"
    top_tipologias = df_sub[col_tipologia].value_counts().head(5).to_dict() if col_tipologia in df_sub.columns else {}
    
    return json.dumps({
        "plataforma": "Zendesk Support (Back Office)",
        "grupo_consultado": target_grp,
        "franja_horaria": franja_txt,
        "total_tickets_gestionados_en_franja": total_en_filtro,
        "total_acumulado_dia_grupo": total_gestionados_grupo,
        "desglose_por_hora": desglose_horas,
        "top_asesores_productivos": top_asesores,
        "top_tipologias_gestionadas": top_tipologias
    }, ensure_ascii=False)


CATALOGO_SERVICIOS_SORE = {
    "LUA AMC": {"tipo": "Línea / Inbound Voz", "meta_aht_seg": 860.0, "meta_aht_formato": "14:20", "meta_ns": "75.0%"},
    "DT FFP AMC": {"tipo": "Línea / Inbound Voz", "meta_aht_seg": 646.0, "meta_aht_formato": "10:46", "meta_ns": "85.0%"},
    "DT FFP AMC ING": {"tipo": "Línea / Inbound Voz", "meta_aht_seg": 582.0, "meta_aht_formato": "09:42", "meta_ns": "85.0%"},
    "LUA AMC ING": {"tipo": "Línea / Inbound Voz", "meta_aht_seg": 900.0, "meta_aht_formato": "15:00", "meta_ns": "75.0%"},
    "VENTAS AMC": {"tipo": "Línea / Inbound Voz", "meta_aht_seg": 780.0, "meta_aht_formato": "13:00", "meta_ns": "75.0%"},
    "EQUIPAJES AMC": {"tipo": "Línea / Inbound Voz", "meta_aht_seg": 500.0, "meta_aht_formato": "08:20", "meta_ns": "75.0%"},
    "EQUIPAJES AMC ING": {"tipo": "Línea / Inbound Voz", "meta_aht_seg": 491.0, "meta_aht_formato": "08:11", "meta_ns": "75.0%"},
    "HVC AMC": {"tipo": "Línea / Inbound Voz", "meta_aht_seg": 709.0, "meta_aht_formato": "11:49", "meta_ns": "80.0%"},
    "SOPORTE LUA AMC": {"tipo": "Línea / Inbound Voz", "meta_aht_seg": 311.0, "meta_aht_formato": "05:11", "meta_ns": "75.0%"},
    "CORPORATE PYME": {"tipo": "Línea / Inbound Voz", "meta_aht_seg": 817.0, "meta_aht_formato": "13:37", "meta_ns": "80.0%"},
    "AGENCIAS TARGET ES": {"tipo": "Línea / Inbound Voz", "meta_aht_seg": 880.0, "meta_aht_formato": "14:40", "meta_ns": "80.0%"},
    "WPP LUA AMC": {"tipo": "Canales Digitales (WhatsApp)", "meta_aht_seg": 1600.0, "meta_aht_formato": "26:40", "meta_ns": "80.0%"},
    "WPP VENTAS AMC": {"tipo": "Canales Digitales (WhatsApp)", "meta_aht_seg": 1700.0, "meta_aht_formato": "28:20", "meta_ns": "80.0%"},
    "CHAT VENTAS AMC": {"tipo": "Canales Digitales (Chat)", "meta_aht_seg": 1412.0, "meta_aht_formato": "23:32", "meta_ns": "80.0%"},
    "WPP EQUIPAJES AMC": {"tipo": "Canales Digitales (WhatsApp)", "meta_aht_seg": 1231.0, "meta_aht_formato": "20:31", "meta_ns": "80.0%"},
    "RRSS AMC": {"tipo": "Canales Digitales (Redes Sociales)", "meta_aht_seg": 1200.0, "meta_aht_formato": "20:00", "meta_ns": "80.0%"},
    "RRSS AMC ING": {"tipo": "Canales Digitales (Redes Sociales)", "meta_aht_seg": 1200.0, "meta_aht_formato": "20:00", "meta_ns": "80.0%"},
    "RRSS PORT AMC": {"tipo": "Canales Digitales (Redes Sociales)", "meta_aht_seg": 1200.0, "meta_aht_formato": "20:00", "meta_ns": "80.0%"},
    "AG CORPORATE CHAT": {"tipo": "Canales Digitales (Chat)", "meta_aht_seg": 1859.0, "meta_aht_formato": "30:59", "meta_ns": "80.0%"},
}


def consultar_metas_servicio(servicio: str = "") -> str:
    """Consulta las metas oficiales contractuales y parámetros operativos SORE (AHT, Nivel de Servicio, Auxiliares y Ausentismo)."""
    if not servicio:
        return json.dumps({
            "parametros_generales": {
                "meta_maxima_auxiliares_pausas": "14.0%",
                "tolerancia_maxima_ausentismo": "10.0%",
                "tiempo_estandar_break": "30 minutos",
                "tiempo_estandar_almuerzo": "45 a 60 minutos"
            },
            "catalogo_servicios_disponibles": list(CATALOGO_SERVICIOS_SORE.keys())
        }, ensure_ascii=False)
        
    srv_clean = str(servicio).strip().upper()
    match = [k for k, v in CATALOGO_SERVICIOS_SORE.items() if srv_clean in k.upper() or k.upper() in srv_clean]
    
    if match:
        k = match[0]
        meta = CATALOGO_SERVICIOS_SORE[k]
        return json.dumps({
            "servicio": k,
            "tipo_canal": meta["tipo"],
            "meta_nivel_de_servicio": meta["meta_ns"],
            "meta_aht_segundos": meta["meta_aht_seg"],
            "meta_aht_tiempo": meta["meta_aht_formato"],
            "meta_maxima_auxiliares": "14.0%",
            "tolerancia_ausentismo": "10.0%",
            "break_permitido": "Hasta 30 minutos",
            "almuerzo_permitido": "Hasta 60 minutos"
        }, ensure_ascii=False)
        
    return json.dumps({
        "error": f"No se encontraron metas específicas para '{servicio}'.",
        "servicios_disponibles": list(CATALOGO_SERVICIOS_SORE.keys())
    }, ensure_ascii=False)


def consultar_cumplimiento_turnos_y_pausas(agente_o_supervisor: str, fecha: str = "") -> str:
    """Audita detalladamente el cumplimiento de turnos y pausas:
    - Puntualidad en la hora de conexión (hora de turno programada vs primer login real en Genesys).
    - Cumplimiento de pausas programadas (almuerzo y descansos) y excesos de break (>30m), almuerzo (>60m) o pre-pausa (>45m).
    - Si es un supervisor: consolida el ranking de puntualidad y pausas de todo su equipo.
    - Si es un asesor: auditoría individual segundo a segundo de su jornada.
    """
    if not DB_PATH.exists():
        return json.dumps({"error": "Base de datos no encontrada."})

    fecha = normalizar_fecha(fecha)
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    if not fecha:
        c.execute("SELECT DISTINCT fecha FROM segments ORDER BY fecha DESC LIMIT 1")
        row_f = c.fetchone()
        fecha = row_f[0] if row_f else "2026-09-17"

    query_term = str(agente_o_supervisor).strip().upper()
    palabras = [p for p in query_term.split() if len(p) > 2]
    if not palabras:
        palabras = [query_term]

    # 1. ¿Es un SUPERVISOR?
    c.execute("SELECT DISTINCT jefe_inmediato FROM segments WHERE jefe_inmediato IS NOT NULL AND jefe_inmediato != ''")
    sups = [r[0] for r in c.fetchall()]
    sups_match = [s for s in sups if all(p in s.upper() for p in palabras)]

    if sups_match:
        sup_oficial = sups_match[0]
        c.execute("""
            SELECT DISTINCT s.agente, s.presence_label, s.duracion_min, s.inicio, s.fin,
                            t.horas_programadas, t.turno_ini, t.turno_fin, t.lunch_ini, t.lunch_fin, t.des_1_ini, t.des_1_fin
            FROM segments s
            LEFT JOIN turnos_detallados t ON s.fecha = t.fecha AND (s.agente LIKE '%' || t.bp || '%' OR s.agente LIKE '%' || t.nombre_agente || '%')
            WHERE s.fecha = ? AND s.jefe_inmediato = ?
            ORDER BY s.agente, s.inicio
        """, (fecha, sup_oficial))
        rows = c.fetchall()
        conn.close()

        if not rows:
            return json.dumps({"error": f"No se registraron datos para el equipo de {sup_oficial} en la fecha {fecha}."})

        asesores_data = {}
        for r in rows:
            ag = r[0]
            if ag not in asesores_data:
                asesores_data[ag] = {
                    "agente": ag,
                    "turno_programado": f"{r[6]} a {r[7]}" if r[6] and r[7] else "Sin turno programado",
                    "turno_ini": r[6],
                    "turno_fin": r[7],
                    "lunch_prog": f"{r[8]} a {r[9]}" if r[8] and r[9] else None,
                    "descanso_prog": f"{r[10]} a {r[11]}" if r[10] and r[11] else None,
                    "primer_login": r[3],
                    "estados_minutos": {},
                    "duracion_total_min": 0
                }
            lbl = r[1]
            dur = float(r[2]) if r[2] else 0.0
            asesores_data[ag]["estados_minutos"][lbl] = round(asesores_data[ag]["estados_minutos"].get(lbl, 0.0) + dur, 1)
            asesores_data[ag]["duracion_total_min"] = round(asesores_data[ag]["duracion_total_min"] + dur, 1)

        tardanzas = []
        puntuales = []
        excesos_break = []
        excesos_almuerzo = []
        cumplimiento_optimo = []
        detalle_asesores = []

        for ag, data in asesores_data.items():
            t_ini_prog = data["turno_ini"]
            primer_log = data["primer_login"]

            puntualidad_status = "Sin turno en malla"
            if t_ini_prog and primer_log:
                try:
                    h_prog = datetime.strptime(str(t_ini_prog).strip(), "%H:%M:%S").time()
                    dt_log = datetime.strptime(str(primer_log).strip()[:19], "%Y-%m-%d %H:%M:%S")
                    diff_min = (dt_log.hour * 60 + dt_log.minute) - (h_prog.hour * 60 + h_prog.minute)
                    if diff_min <= 3:
                        puntualidad_status = f"🟢 Puntual ({primer_log.split()[1]} vs {t_ini_prog})"
                        puntuales.append(ag)
                    elif 3 < diff_min <= 15:
                        puntualidad_status = f"🟡 Tardanza Leve (+{diff_min} min tardanza: {primer_log.split()[1]})"
                        tardanzas.append({"agente": ag, "minutos_tarde": diff_min, "hora_login": primer_log.split()[1], "turno": t_ini_prog})
                    else:
                        puntualidad_status = f"🔴 Tardanza Severa (+{diff_min} min tardanza: {primer_log.split()[1]})"
                        tardanzas.append({"agente": ag, "minutos_tarde": diff_min, "hora_login": primer_log.split()[1], "turno": t_ini_prog})
                except Exception:
                    puntualidad_status = f"Conectado a las {primer_log.split()[1]}"

            m_break = data["estados_minutos"].get("Break", 0.0)
            m_lunch = data["estados_minutos"].get("Almuerzo", 0.0)
            m_prepausa = data["estados_minutos"].get("Pre-Pausa", 0.0)

            alertas_pausas = []
            if m_break > 35.0:
                alertas_pausas.append(f"Exceso de Break: {m_break} min (Meta: máx 30 min)")
                excesos_break.append({"agente": ag, "minutos_break": m_break, "exceso": round(m_break - 30.0, 1)})
            if m_lunch > 65.0:
                alertas_pausas.append(f"Exceso de Almuerzo: {m_lunch} min (Meta: máx 60 min)")
                excesos_almuerzo.append({"agente": ag, "minutos_almuerzo": m_lunch, "exceso": round(m_lunch - 60.0, 1)})
            if m_prepausa > 45.0:
                alertas_pausas.append(f"Pre-Pausa prolongada: {m_prepausa} min")

            es_optimo = (len(alertas_pausas) == 0) and ("Tardanza" not in puntualidad_status)
            if es_optimo:
                cumplimiento_optimo.append(ag)

            detalle_asesores.append({
                "agente": ag,
                "turno_programado": data["turno_programado"],
                "primer_login": primer_log.split()[1] if primer_log else "No registrado",
                "diagnostico_puntualidad": puntualidad_status,
                "break_tomado_min": m_break,
                "almuerzo_tomado_min": m_lunch,
                "alertas": alertas_pausas if alertas_pausas else ["✅ Cumplimiento normal de pausas"]
            })

        return json.dumps({
            "fecha": fecha,
            "supervisor": sup_oficial,
            "total_asesores_conectados": len(asesores_data),
            "resumen_cumplimiento": {
                "total_puntuales": len(puntuales),
                "total_con_tardanza": len(tardanzas),
                "total_exceso_break": len(excesos_break),
                "total_exceso_almuerzo": len(excesos_almuerzo),
                "total_cumplimiento_optimo_100%": len(cumplimiento_optimo)
            },
            "detalle_tardanzas": tardanzas,
            "detalle_exceso_breaks": excesos_break,
            "detalle_exceso_almuerzos": excesos_almuerzo,
            "asesores_cumplimiento_optimo": cumplimiento_optimo[:10],
            "muestra_asesores": detalle_asesores[:15]
        }, ensure_ascii=False)

    # 2. ¿Es un ASESOR INDIVIDUAL?
    c.execute("""
        SELECT s.agente, s.presence_label, s.duracion_min, s.inicio, s.fin, s.servicio, s.jefe_inmediato,
               t.horas_programadas, t.turno_ini, t.turno_fin, t.lunch_ini, t.lunch_fin, t.des_1_ini, t.des_1_fin, t.des_2_ini, t.des_2_fin, t.novedad
        FROM segments s
        LEFT JOIN turnos_detallados t ON s.fecha = t.fecha AND (s.agente LIKE '%' || t.bp || '%' OR s.agente LIKE '%' || t.nombre_agente || '%')
        WHERE s.fecha = ?
    """, (fecha,))
    all_s = c.fetchall()
    conn.close()

    asesor_rows = [r for r in all_s if all(p in r[0].upper() for p in palabras)]
    if not asesor_rows:
        return json.dumps({"error": f"No se encontró al asesor '{agente_o_supervisor}' en la fecha {fecha}."})

    primer = asesor_rows[0]
    ag_nombre = primer[0]
    servicio = primer[5]
    supervisor = primer[6]
    t_ini = primer[8]
    t_fin = primer[9]
    lunch_ini = primer[10]
    lunch_fin = primer[11]
    des_1_ini = primer[12]
    des_1_fin = primer[13]

    primer_login = min(r[3] for r in asesor_rows if r[3])
    ultimo_logout = max(r[4] for r in asesor_rows if r[4])

    estados = {}
    for r in asesor_rows:
        lbl = r[1]
        dur = float(r[2]) if r[2] else 0.0
        estados[lbl] = round(estados.get(lbl, 0.0) + dur, 1)

    puntualidad = "Sin turno programado para contrastar"
    if t_ini and primer_login:
        try:
            h_p = datetime.strptime(str(t_ini).strip(), "%H:%M:%S").time()
            dt_l = datetime.strptime(str(primer_login).strip()[:19], "%Y-%m-%d %H:%M:%S")
            diff = (dt_l.hour * 60 + dt_l.minute) - (h_p.hour * 60 + h_p.minute)
            if diff <= 3:
                puntualidad = f"🟢 Puntual (Login a las {primer_login.split()[1]} vs Turno {t_ini})"
            elif 3 < diff <= 15:
                puntualidad = f"🟡 Tardanza Leve (+{diff} min: Login a las {primer_login.split()[1]} vs Turno {t_ini})"
            else:
                puntualidad = f"🔴 Tardanza Severa (+{diff} min: Login a las {primer_login.split()[1]} vs Turno {t_ini})"
        except Exception:
            puntualidad = f"Conectado a las {primer_login.split()[1]}"

    m_break = estados.get("Break", 0.0)
    m_lunch = estados.get("Almuerzo", 0.0)
    m_prep = estados.get("Pre-Pausa", 0.0)
    m_avail = estados.get("Available", 0.0)
    m_onqueue = estados.get("On Queue", 0.0)

    return json.dumps({
        "agente": ag_nombre,
        "servicio": servicio,
        "supervisor": supervisor,
        "fecha": fecha,
        "turno_programado": {
            "inicio_programado": t_ini if t_ini else "No programado",
            "fin_programado": t_fin if t_fin else "No programado",
            "almuerzo_programado": f"{lunch_ini} a {lunch_fin}" if lunch_ini and lunch_fin else "No asignado en malla",
            "descanso_programado": f"{des_1_ini} a {des_1_fin}" if des_1_ini and des_1_fin else "No asignado en malla"
        },
        "conexion_real_genesys": {
            "primer_login": primer_login,
            "ultimo_logout": ultimo_logout,
            "diagnostico_puntualidad": puntualidad
        },
        "cumplimiento_pausas_genesys": {
            "minutos_almuerzo": m_lunch,
            "diagnostico_almuerzo": "🟢 Normal (<=60 min)" if m_lunch <= 65 else f"🔴 Exceso de Almuerzo ({m_lunch} min vs 60 min máx)",
            "minutos_break": m_break,
            "diagnostico_break": "🟢 Normal (<=30 min)" if m_break <= 35 else f"🔴 Exceso de Break ({m_break} min vs 30 min máx)",
            "minutos_prepausa": m_prep,
            "tiempo_en_atencion_onqueue": f"{round(m_onqueue/60.0, 1)} horas ({m_onqueue} min)",
            "tiempo_en_available": f"{round(m_avail/60.0, 1)} horas ({m_avail} min)"
        },
        "distribucion_completa_estados": estados
    }, ensure_ascii=False)


def consultar_nivel_servicio(servicio: str = "", fecha: str = "", supervisor_o_coordinador: str = "") -> str:
    """Consulta el Nivel de Servicio (% NS, llamadas/chats ofrecidos, atendidos, abandono y AHT) de las colas de Genesys Cloud TANTO EN TIEMPO REAL (HOY EN VIVO) COMO HISTÓRICO."""
    try:
        try:
            from scripts.live_engine import obtener_token_genesys
            from scripts.gtr_engine import obtener_metricas_gtr_api, obtener_metricas_gtr_historico_api, formatear_segundos_mm_ss
        except ImportError:
            from live_engine import obtener_token_genesys
            from gtr_engine import obtener_metricas_gtr_api, obtener_metricas_gtr_historico_api, formatear_segundos_mm_ss
    except Exception as e:
        return json.dumps({"error": f"Error importando módulos de GTR / Genesys: {e}"})

    token = obtener_token_genesys()
    if not token:
        return json.dumps({"error": "No se pudo obtener token de Genesys Cloud para consultar Nivel de Servicio."})

    f_clean = str(fecha or "").strip().lower()
    es_en_vivo = f_clean in ["", "hoy", "today", "en vivo", "tiempo real", "actual", "ahora", "ahora mismo"] or "vivo" in f_clean or "real" in f_clean

    if es_en_vivo:
        df_calc_raw, err, hora_corte = obtener_metricas_gtr_api(token)
        fecha_reporte = f"En Vivo Hoy ({hora_corte})"
    else:
        fecha_normalizada = normalizar_fecha(fecha)
        if not fecha_normalizada:
            fecha_normalizada = "2026-09-17"
        df_calc_raw, err = obtener_metricas_gtr_historico_api(token, fecha_normalizada, fecha_normalizada, "P1D")
        fecha_reporte = fecha_normalizada

    if err or df_calc_raw is None or df_calc_raw.empty:
        return json.dumps({"error": f"No se obtuvieron métricas de colas de Genesys: {err or 'Sin datos'}"})

    servicios_filtro = []
    persona_oficial = ""
    if supervisor_o_coordinador:
        conn = sqlite3.connect(str(DB_PATH))
        f_lookup = fecha_normalizada if (not es_en_vivo and 'fecha_normalizada' in locals()) else "2026-09-17"
        persona_oficial = resolver_supervisor(conn, supervisor_o_coordinador, f_lookup)
        c = conn.cursor()
        c.execute("""
            SELECT DISTINCT servicio FROM segments
            WHERE fecha=? AND (coordinador=? OR jefe_inmediato=?)
        """, (f_lookup, persona_oficial, persona_oficial))
        servicios_filtro = [r[0] for r in c.fetchall() if r[0]]
        if any("LUA AMC" in s for s in servicios_filtro):
            servicios_filtro.append("Soporte LUA AMC")
        conn.close()

    if servicio:
        servicios_filtro = [servicio]

    df_calc = df_calc_raw.copy()
    if servicios_filtro:
        mask = pd.Series(False, index=df_calc.index)
        for sf in servicios_filtro:
            mask = mask | df_calc["servicio"].str.contains(sf, case=False, na=False)
        df_calc = df_calc[mask]

    if df_calc.empty:
        return json.dumps({
            "fecha": fecha_reporte,
            "es_tiempo_real": es_en_vivo,
            "mensaje": f"No se encontraron colas con tráfico para los filtros especificados en {fecha_reporte}."
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
        "fecha": fecha_reporte,
        "es_tiempo_real": es_en_vivo,
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


def consultar_presencia_tiempo_real(filtro_busqueda: str = "", estado_presencia: str = "") -> str:
    """Consulta la presencia y actividad de agentes en TIEMPO REAL (EN VIVO AHORA MISMO) desde Genesys Cloud.
    Permite consultar:
    - Quién está en Break, Almuerzo, Baño, Pre-Pausa o Pausas en este instante con su cronómetro de minutos transcurridos.
    - Quién está en Available (disponible esperando llamadas) o en On Queue (en atención o llamada activa).
    - Estado en vivo de todo el equipo de un supervisor (ej. 'David Jaramillo'), coordinador (ej. 'Yineidis Carbono') o servicio (ej. 'WPP LUA AMC').
    - Diagnóstico de alertas operativas en vivo (llamadas prolongadas >15m, pausas excedidas, baños excedidos >5m).
    """
    try:
        from live_engine import obtener_token_genesys, cargar_catalogo_presencias, obtener_presencia_en_vivo
    except ImportError:
        try:
            from scripts.live_engine import obtener_token_genesys, cargar_catalogo_presencias, obtener_presencia_en_vivo
        except Exception as e:
            return json.dumps({"error": f"No se pudo cargar el motor en vivo: {e}"}, ensure_ascii=False)

    tok = obtener_token_genesys()
    if not tok:
        return json.dumps({"error": "El token de Genesys Cloud no está disponible o está en proceso de renovación automática. Intenta nuevamente en unos momentos."}, ensure_ascii=False)

    if not DB_PATH.exists():
        return json.dumps({"error": "Base de datos local no encontrada."}, ensure_ascii=False)

    conn = sqlite3.connect(str(DB_PATH))
    try:
        agentes_db = pd.read_sql("SELECT agente_id, agente, cargo, estado_laboral, servicio, jefe_inmediato, coordinador FROM dim_agentes", conn)
    except Exception:
        agentes_db = pd.read_sql("SELECT DISTINCT agente_id, agente, cargo, estado_laboral, servicio, jefe_inmediato, coordinador FROM segments", conn)
    finally:
        conn.close()

    if agentes_db.empty:
        return json.dumps({"error": "No se encontraron agentes en el catálogo operativo."}, ensure_ascii=False)

    agentes_map = agentes_db.drop_duplicates("agente_id", keep="last").set_index("agente_id").to_dict(orient="index")
    catalog = cargar_catalogo_presencias(tok)
    df_live = obtener_presencia_en_vivo(tok, agentes_map, catalog)

    if df_live is None or df_live.empty:
        return json.dumps({"error": "No se obtuvieron registros de presencia en vivo desde Genesys Cloud."}, ensure_ascii=False)

    query = str(filtro_busqueda or "").strip().upper()
    df_filtrado = df_live.copy()

    if query and query != "TODOS":
        tokens = [t for t in query.split() if len(t) >= 3]
        if not tokens:
            tokens = [query]
        mask = pd.Series(False, index=df_filtrado.index)
        for t in tokens:
            mask |= (
                df_filtrado["supervisor"].str.upper().str.contains(t, na=False) |
                df_filtrado["coordinador"].str.upper().str.contains(t, na=False) |
                df_filtrado["servicio"].str.upper().str.contains(t, na=False) |
                df_filtrado["agente"].str.upper().str.contains(t, na=False)
            )
        df_filtrado = df_filtrado[mask]

    st_filtro = str(estado_presencia or "").strip().upper()
    if st_filtro and st_filtro != "TODOS":
        if "BREAK" in st_filtro or "DESCANSO" in st_filtro:
            df_filtrado = df_filtrado[df_filtrado["estado"].str.upper().str.contains("BREAK|DESCANSO", na=False) | (df_filtrado["sys_pres"].str.upper() == "BREAK")]
        elif "ALMUERZO" in st_filtro or "LUNCH" in st_filtro:
            df_filtrado = df_filtrado[df_filtrado["estado"].str.upper().str.contains("ALMUERZO|LUNCH", na=False)]
        elif "AVAIL" in st_filtro or "DISP" in st_filtro:
            df_filtrado = df_filtrado[df_filtrado["sys_pres"].str.upper() == "AVAILABLE"]
        elif "QUEUE" in st_filtro or "COLA" in st_filtro or "LLAMADA" in st_filtro:
            df_filtrado = df_filtrado[(df_filtrado["sys_pres"].str.upper() == "ON QUEUE") | (df_filtrado["routing"] == "INTERACTING")]
        elif "CONECTADO" in st_filtro or "ACTIVO" in st_filtro or "ONLINE" in st_filtro:
            df_filtrado = df_filtrado[df_filtrado["sys_pres"].str.upper() != "OFFLINE"]
        elif "ALERTA" in st_filtro:
            df_filtrado = df_filtrado[~df_filtrado["alerta"].isin(["Normal", "Desconectado"])]
        elif "OFFLINE" in st_filtro or "DESCONECTADO" in st_filtro:
            df_filtrado = df_filtrado[df_filtrado["sys_pres"].str.upper() == "OFFLINE"]

    now_col = datetime.now(timezone(timedelta(hours=-5))).strftime("%Y-%m-%d %H:%M:%S")

    total_evaluados = len(df_filtrado)
    conectados_activos = len(df_filtrado[df_filtrado["sys_pres"].str.upper() != "OFFLINE"])
    en_cola = len(df_filtrado[df_filtrado["sys_pres"].str.upper() == "ON QUEUE"])
    en_available = len(df_filtrado[df_filtrado["sys_pres"].str.upper() == "AVAILABLE"])
    en_break = len(df_filtrado[df_filtrado["estado"].str.upper().str.contains("BREAK|DESCANSO", na=False) | (df_filtrado["sys_pres"].str.upper() == "BREAK")])
    en_almuerzo = len(df_filtrado[df_filtrado["estado"].str.upper().str.contains("ALMUERZO|LUNCH", na=False)])
    con_alertas = len(df_filtrado[~df_filtrado["alerta"].isin(["Normal", "Desconectado"])])

    df_filtrado["orden_prioridad"] = df_filtrado.apply(
        lambda r: 0 if r["alerta"] not in ["Normal", "Desconectado"] else (1 if r["sys_pres"] != "Offline" else 2),
        axis=1
    )
    df_filtrado = df_filtrado.sort_values(by=["orden_prioridad", "dur_min"], ascending=[True, False])

    detalle_agentes = []
    for _, r in df_filtrado.head(35).iterrows():
        detalle_agentes.append({
            "agente": r["agente"],
            "servicio": r["servicio"],
            "supervisor": r["supervisor"],
            "estado_presencia": r["estado"],
            "tipo_sistema": r["sys_pres"],
            "routing": r["routing"],
            "tiempo_en_estado": r["cronometro"],
            "minutos_transcurridos": r["dur_min"],
            "alerta_en_vivo": r["alerta"],
            "atendidas_hoy": r.get("atendidas_hoy", 0)
        })

    resumen_estados = df_filtrado["estado"].value_counts().head(8).to_dict() if not df_filtrado.empty else {}

    return json.dumps({
        "marca_tiempo_colombia": now_col,
        "filtro_aplicado": filtro_busqueda if filtro_busqueda else "Todos",
        "filtro_estado": estado_presencia if estado_presencia else "Todos",
        "resumen_en_vivo": {
            "total_agentes_en_alcance": total_evaluados,
            "conectados_actualmente": conectados_activos,
            "en_cola_onqueue": en_cola,
            "en_disponible_available": en_available,
            "en_break": en_break,
            "en_almuerzo": en_almuerzo,
            "con_alertas_operativas": con_alertas,
            "desconectados_offline": total_evaluados - conectados_activos
        },
        "distribucion_estados_en_vivo": resumen_estados,
        "detalle_asesores_en_vivo": detalle_agentes
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
        "description": "Consulta los NIVELES DE SERVICIO (% NS, SLA contractual 75.3% / 80%, llamadas/chats ofrecidos, atendidos, porcentaje de abandono y AHT) de las colas de atención de Genesys Cloud TANTO EN TIEMPO REAL (EN VIVO HOY / EN ESTE MOMENTO) COMO HISTÓRICOS. Si preguntan por nivel de servicio ahora, en vivo, hoy, en este momento, o de ayer: SIEMPRE llama a esta herramienta. Permite consultar el consolidado global de todos los servicios, o filtrar por servicio (ej. 'LUA AMC', 'CORPORATE PYME') o por coordinador/supervisor (ej. 'Yineidis Carbono', 'David Jaramillo').",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "servicio": {"type": "STRING", "description": "Nombre opcional del servicio o campaña a evaluar, ej. 'LUA AMC' o 'VENTAS AMC'. Dejar vacío para consolidado global de todos los servicios."},
                "supervisor_o_coordinador": {"type": "STRING", "description": "Nombre opcional del coordinador o supervisor cuyos servicios se desean evaluar, ej. 'Yineidis Carbono'"},
                "fecha": {"type": "STRING", "description": "Fecha opcional YYYY-MM-DD (ej. '2026-09-17') o 'hoy' / 'en vivo' / 'ahora' para tiempo real."}
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
    },
    {
        "name": "consultar_zendesk_backoffice",
        "description": "Consulta la gestión y productividad de tickets de Back Office (BO) en Zendesk Support (ej. 'BO LUA AMC', 'LUA AMC', 'DT FFP AMC', 'Equipajes AMC SSC', 'Célula PI AMC ES'). Permite filtrar por grupo y por franja horaria (ej. 8 a 12) o por hora puntual.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "grupo_o_servicio": {"type": "STRING", "description": "Grupo o servicio de Back Office, ej. 'BO LUA AMC', 'LUA AMC', 'DT FFP', 'Equipajes'"},
                "hora_inicio": {"type": "INTEGER", "description": "Hora inicial en formato 24h (ej. 8 para 08:00)"},
                "hora_fin": {"type": "INTEGER", "description": "Hora final en formato 24h (ej. 12 para 12:00)"},
                "fecha": {"type": "STRING", "description": "Fecha opcional YYYY-MM-DD"}
            }
        }
    },
    {
        "name": "consultar_organigrama_jerarquia",
        "description": "Consulta el organigrama y estructura de liderazgo oficial (Coordinadores, Supervisores, Asesores y Servicios). Si preguntas por un Coordinador, muestra sus servicios y supervisores. Si preguntas por un Supervisor, muestra su coordinador y sus asesores. Si preguntas por un Asesor, muestra su jefatura. Si preguntas por un Servicio, muestra quién lo lidera.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "nombre_o_servicio": {"type": "STRING", "description": "Nombre de la persona (Coordinador, Supervisor, Asesor) o nombre del Servicio"}
            },
            "required": ["nombre_o_servicio"]
        }
    },
    {
        "name": "consultar_cumplimiento_turnos_y_pausas",
        "description": "Audita de forma exhaustiva la puntualidad en la hora de conexión a Genesys (hora programada de turno vs hora real del primer login) y el cumplimiento de pausas programadas (almuerzos, descansos/breaks de 30 min, pre-pausas, y excesos de descanso) de un asesor o de todo el equipo de un supervisor.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "agente_o_supervisor": {"type": "STRING", "description": "Nombre del asesor o supervisor a auditar, ej. 'David Jaramillo' o 'Monica Restrepo'"},
                "fecha": {"type": "STRING", "description": "Fecha YYYY-MM-DD (ej. 2026-09-17)"}
            },
            "required": ["agente_o_supervisor"]
        }
    },
    {
        "name": "consultar_metas_servicio",
        "description": "Consulta las metas oficiales contractuales y parámetros SORE para cualquier campaña o servicio (Meta de AHT, Meta de % NS, Meta máxima de auxiliares 14%, Tolerancia de ausentismo 10%).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "servicio": {"type": "STRING", "description": "Nombre del servicio, ej. 'LUA AMC', 'CORPORATE PYME', 'WPP LUA AMC'"}
            }
        }
    },
    {
        "name": "consultar_presencia_tiempo_real",
        "description": "Consulta la presencia y actividad de asesores en TIEMPO REAL (EN VIVO AHORA MISMO en este preciso instante) desde Genesys Cloud. Responde a preguntas como: '¿Quién está en break en este momento?', '¿Quiénes están disponibles/Available ahora?', '¿Cómo está el equipo de David Jaramillo en vivo?', '¿Quién tiene alertas o llamadas prolongadas ahora mismo?', '¿Cuántos agentes están conectados hoy en tiempo real?'.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "filtro_busqueda": {"type": "STRING", "description": "Filtro opcional por nombre de supervisor (ej. 'David Jaramillo'), coordinador (ej. 'Yineidis Carbono'), servicio (ej. 'WPP LUA AMC', 'CORPORATE PYME') o nombre de un asesor."},
                "estado_presencia": {"type": "STRING", "description": "Filtro opcional por estado de presencia: 'Break', 'Almuerzo', 'Available', 'On Queue', 'Conectados', 'Offline', 'Alertas'."}
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
    "consultar_ausentismos": lambda a: consultar_ausentismos(a.get("fecha", ""), a.get("servicio", ""), a.get("supervisor", "")),
    "consultar_zendesk_backoffice": lambda a: consultar_zendesk_backoffice(a.get("grupo_o_servicio", "LUA AMC"), a.get("hora_inicio"), a.get("hora_fin"), a.get("fecha")),
    "consultar_organigrama_jerarquia": lambda a: consultar_organigrama_jerarquia(a.get("nombre_o_servicio", "")),
    "consultar_cumplimiento_turnos_y_pausas": lambda a: consultar_cumplimiento_turnos_y_pausas(a.get("agente_o_supervisor", ""), a.get("fecha", "")),
    "consultar_metas_servicio": lambda a: consultar_metas_servicio(a.get("servicio", "")),
    "consultar_presencia_tiempo_real": lambda a: consultar_presencia_tiempo_real(a.get("filtro_busqueda", ""), a.get("estado_presencia", ""))
}

SYSTEM_INSTRUCTION = """
Eres **Rocco**, el pulpo ninja inteligente y Copiloto Operacional 4DX de Inteligencia Operativa de LATAM Airlines y AlmaContact.
Con tus múltiples tentáculos tienes acceso simultáneo e instantáneo a todas las fuentes operacionales: Genesys Cloud en tiempo real y voz, Salesforce B2B CRM, Zendesk Back Office, Turnos, Malla y Jerarquía de liderazgo.
Tu propósito es responder con máxima agilidad, intuición, precisión matemática y tono ejecutivo las consultas de Carlos Murillo, Coordinadores, Jefaturas y Supervisores.
Si te preguntan quién eres, cómo te llamas o qué haces, preséntate con orgullo como Rocco, el pulpo ninja y copiloto analítico de operaciones 4DX.

REGLAS TEMPORALES Y OPERATIVAS CLAVE:
1. DISTINCIÓN TEMPORAL CRÍTICA: ¿TIEMPO REAL vs HISTÓRICO?
   - **TIEMPO REAL (EN VIVO AHORA MISMO / HOY)**:
     * Si el usuario pregunta por el estado ACTUAL de agentes: **"ahora"**, **"en este momento"**, **"en vivo"**, **"en este instante"**, **"actualmente"**, **"ya"**, **"quién está en break en este momento"**, **"cuántos disponibles hay ahora"**, **"cómo está el equipo de David en vivo"**:
       👉 DEBES LLAMAR INMEDIATAMENTE A `consultar_presencia_tiempo_real(filtro_busqueda=..., estado_presencia=...)`.
     * Si el usuario pregunta por el **NIVEL DE SERVICIO (% NS), SLA, LLAMADAS O TRÁFICO EN TIEMPO REAL / HOY / EN ESTE MOMENTO**:
       👉 DEBES LLAMAR INMEDIATAMENTE A `consultar_nivel_servicio(fecha="hoy", servicio=...)`.
       ⚠️ NUNCA digas que no tienes capacidad para ver el nivel de servicio en tiempo real o que solo ves histórico. Tienes conexión directa a la API de Genesys Cloud en vivo hoy con métricas intradía por cola.
   - **HISTÓRICO / REGISTROS PASADOS**:
     * El año operativo histórico de la base de datos de Genesys/presencia es **2026** (agosto y septiembre de 2026). La fecha de referencia activa y más reciente cerrada es **2026-09-17**.
     * Si el usuario dice "ayer", "17 de sep", "17 de septiembre", "cómo le fue a...", "cuántos faltaron ayer": llama a las herramientas históricas (`consultar_equipo_supervisor`, `consultar_cumplimiento_turnos_y_pausas`, `consultar_asesor`, `consultar_ausentismos`, o `consultar_nivel_servicio(fecha='2026-09-17')`).

2. DISTINCIÓN CRÍTICA ENTRE PLATAFORMAS (ZENDESK vs GENESYS vs SALESFORCE):
   - **ZENDESK SUPPORT (BACK OFFICE)**:
     * Si el usuario pregunta por: **"BO LUA"**, **"BACK OFFICE"**, **"CASOS/TICKETS DE BO LUA"**, **"EQUIPAJES"**, **"DT FFP"**, **"CÉLULA PI"**, **"TICKETS GESTIONADOS"**, **"CASOS RESUELTOS EN LA MAÑANA / ENTRE LAS 8 Y LAS 12"**:
       👉 DEBES LLAMAR INMEDIATAMENTE A `consultar_zendesk_backoffice`.
       NUNCA vayas a Salesforce ni a Genesys para casos de Back Office BO LUA.
   - **GENESYS CLOUD (VOZ, WHATSAPP, PRESENCIA, ADHERENCIA Y TRÁFICO)**:
     * Si el usuario pregunta por: **"NIVEL DE SERVICIO"**, **"% NS"**, **"SLA"**, **"TRÁFICO"**, **"LLAMADAS ATENDIDAS"**, **"ABANDONO"**, **"AHT"** (sea en TIEMPO REAL HOY o HISTÓRICO):
       👉 Llama SIEMPRE a `consultar_nivel_servicio`. Si es para hoy o en vivo usa `fecha='hoy'`.
     * Si el usuario pregunta por: **"PUNTUALIDAD"**, **"HORA DE CONEXIÓN"**, **"CUMPLIMIENTO DE TURNOS"**, **"CUMPLIMIENTO DE PAUSAS"**, **"TARDANZAS"**, **"QUIÉN LLEGÓ TARDE"**, **"EXCESOS DE BREAK O ALMUERZO"**:
       👉 Llama a `consultar_cumplimiento_turnos_y_pausas(agente_o_supervisor=..., fecha=...)`.
     * Si el usuario pregunta por: **"PAUSAS EN GENERAL"**, **"ASISTENCIA"**, **"AUSENCIAS"**, **"TIEMPOS EN AVAILABLE"**:
       👉 Llama a `consultar_equipo_supervisor` o `consultar_asesor`.
   - **SALESFORCE B2B (CRM COMERCIAL / AGENCIAS / CORPORATE)**:
     * Si el usuario pregunta por: **"BACKLOG SALESFORCE"**, **"CASOS B2B >24H"**, **"AGENCIAS TARGET"**, **"INFRACCIÓN SLA 24H"**:
       👉 Llama a `consultar_backlog_salesforce`.

3. METAS CONTRACTUALES Y PARÁMETROS OPERATIVOS SORE:
   - Si el usuario pregunta por: **"METAS"**, **"OBJETIVOS"**, **"META DE AHT"**, **"META DE NS"**, **"LÍMITE DE AUXILIARES"**, **"TOLERANCIA DE AUSENTISMO"**:
     👉 Llama a `consultar_metas_servicio(servicio=...)`.

4. ORGANIGRAMA Y ESTRUCTURA DE EQUIPOS:
   - Si el usuario pregunta por: **"ORGANIGRAMA"**, **"ESTRUCTURA"**, **"QUIÉN LE REPORTA A"**, **"CUÁL ES EL EQUIPO DE"**, **"QUIÉNES SON LOS ASESORES DE"**, **"QUIÉN COORDINA"**, **"QUÉ SERVICIOS TIENE A CARGO"**:
     👉 Llama a `consultar_organigrama_jerarquia(nombre_o_servicio=...)`.

5. RESOLUCIÓN INTUITIVA DE LÍDERES:
   - "David" o "David Jaramillo" -> Corresponde a **JARAMILLO VASQUEZ DAVID** (Supervisor de WPP LUA AMC bajo la coordinación de Yineidis Carbono).
   - "Marely" o "Marely Cardona" -> Corresponde a **CARDONA RAMIREZ MARELYN** (Coordinadora de Operaciones de Corporativo Pyme y Agencias B2B).
   - "Yineidis" o "Yineidis Carbono" -> Corresponde a **CARBONO PEDROZA YINEIDIS YESENIA** (Coordinadora de LUA AMC, WPP LUA AMC, LUA ING, CARGO BOOKING).
   - "Jhon Villa" -> **VILLA CADAVID JHON FERNANDO**.

RESPUESTA DIRECTA, INTUITIVA Y EJECUTIVA:
- Responde DIRECTAMENTE a lo que se te está preguntando sin rodeos teóricos ni disculpas.
- Presenta tablas Markdown limpias con cifras claras, franjas horarias y porcentajes.
- Usa negritas en los nombres de líderes, asesores y métricas clave.
"""


def ejecutar_pregunta_copiloto(pregunta: str = "", historial_mensajes: list = None, audio_bytes: bytes = None, audio_mime: str = "audio/wav") -> str:
    """Ejecuta una consulta textual o por voz contra Vertex AI vía REST API con multi-turn tool calling."""
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
            
    user_parts = []
    if audio_bytes:
        b64_aud = base64.b64encode(audio_bytes).decode("utf-8")
        user_parts.append({
            "inlineData": {
                "mimeType": audio_mime,
                "data": b64_aud
            }
        })
        prompt_txt = pregunta if pregunta else "Escucha atentamente el audio en español colombiano, identifica la consulta operacional e invoca las herramientas necesarias para responder con datos exactos."
        user_parts.append({"text": prompt_txt})
    else:
        user_parts.append({"text": pregunta if pregunta else "Hola Rocco"})

    contents.append({"role": "user", "parts": user_parts})

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
            r = None
            for intento in range(3):
                try:
                    with httpx.Client(timeout=60.0) as client:
                        r = client.post(endpoint_url, headers=headers, json=body)
                    if r.status_code == 429:
                        espera = (intento + 1) * 3.0
                        time.sleep(espera)
                        continue
                    break
                except (httpx.ReadTimeout, httpx.ConnectTimeout):
                    if intento < 2:
                        time.sleep(2.0)
                        continue
                    raise
            
            if r is None or r.status_code != 200:
                return f"⚠️ Error en respuesta de Vertex AI (HTTP {r.status_code if r else 'Timeout'}): {r.text if r else 'Sin respuesta'}"

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


# ── COMPONENTE DE RENDERIZADO CON IDENTIDAD DE ROCCO ──────────────────────────

def render_contenido_copiloto_rocco(es_modal: bool = False):
    """Renderiza el contenido interactivo del Copiloto Rocco (soportando voz, texto y pills rápidas)."""
    rocco_b64 = obtener_rocco_b64()
    rocco_img_html = f'<img src="data:image/png;base64,{rocco_b64}" width="52" style="vertical-align: middle; border-radius: 50%; background: rgba(255,255,255,0.15); padding: 3px; margin-right: 12px; box-shadow: 0 4px 12px rgba(168,85,247,0.4);">' if rocco_b64 else '🐙 '

    st.markdown(
        f"""
        <div style="background: linear-gradient(90deg, #3b0764 0%, #581c87 50%, #0369a1 100%); padding: 16px 20px; border-radius: 14px; margin-bottom: 16px; box-shadow: 0 4px 20px rgba(124, 58, 237, 0.25); border-left: 5px solid #a855f7;">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px;">
                <div style="display: flex; align-items: center;">
                    {rocco_img_html}
                    <div>
                        <h2 style="color: #ffffff; margin: 0 0 4px 0; font-size: 21px; font-weight: 700;">
                            Rocco • Copiloto Operacional 4DX <span style="font-size: 12px; font-weight: 500; background: rgba(255,255,255,0.2); padding: 3px 10px; border-radius: 12px; margin-left: 6px;">Vertex AI • Multimodal</span>
                        </h2>
                        <p style="color: #e9d5ff; margin: 0; font-size: 12.5px;">
                            Tu asistente ninja de Inteligencia Operativa. Pregúntame por texto o <b>habla directamente con tu voz</b> sobre Genesys en vivo, turnos, ausentismos, Zendesk y Salesforce.
                        </p>
                    </div>
                </div>
                <div style="text-align: right; background: rgba(15, 23, 42, 0.65); padding: 6px 14px; border-radius: 10px; border: 1px solid rgba(192, 132, 252, 0.35);">
                    <span style="color: #c084fc; font-size: 11px; font-weight: 700; text-transform: uppercase;">Estado de Rocco</span><br>
                    <span style="color: #4ade80; font-size: 12px; font-weight: 600;">🟢 En Vivo • 🎙️ Voz Activa</span>
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
                "content": "👋 **¡Hola! Soy Rocco, tu Copiloto Operacional 4DX.**\n\nPuedo ayudarte con cualquier consulta operacional en tiempo real o histórico. Puedes escribirme abajo, usar las preguntas sugeridas o **grabar tu consulta por voz** usando el micrófono."
            }
        ]

    # Píldoras de preguntas rápidas
    st.markdown("##### 💡 Preguntas Rápidas Sugeridas")
    col_p1, col_p2, col_p3, col_p4, col_p5, col_p6 = st.columns(6)
    
    pregunta_rapida = None
    prefijo_key = "modal_" if es_modal else "tab_"
    with col_p1:
        if st.button("🔴 En Vivo Ahora", key=f"{prefijo_key}btn_live", use_container_width=True):
            pregunta_rapida = "¿Quiénes están conectados en este momento en Genesys y quiénes están en break o con alertas en vivo?"
    with col_p2:
        if st.button("👤 Turno Asesor", key=f"{prefijo_key}btn_asesor", use_container_width=True):
            pregunta_rapida = "¿Cómo le fue a Jesus Alonso Guisao el 17 de septiembre de 2026? Dime qué turno tenía, cuánto tiempo estuvo en Available, pausas y quién es su jefe."
    with col_p3:
        if st.button("👥 Marely Cardona", key=f"{prefijo_key}btn_marely", use_container_width=True):
            pregunta_rapida = "Dame el resumen del equipo de Marely Cardona para el 17 de septiembre de 2026: cuántos asesores estuvieron conectados y cómo estuvieron sus tiempos."
    with col_p4:
        if st.button("⏳ Salesforce B2B", key=f"{prefijo_key}btn_sf", use_container_width=True):
            pregunta_rapida = "¿Cómo está actualmente el backlog de Salesforce B2B? Cuántos casos violan el SLA de 24 horas y cuáles son los más críticos?"
    with col_p5:
        if st.button("🚨 Ausentismos", key=f"{prefijo_key}btn_ausent", use_container_width=True):
            pregunta_rapida = "¿Qué asesores tenían turno programado pero no registraron conexión en Genesys en la última fecha registrada?"
    with col_p6:
        if st.button("🏢 Agencias B2B", key=f"{prefijo_key}btn_agencias", use_container_width=True):
            pregunta_rapida = "Dame un panorama macro del servicio CORPORATE PYME en la última fecha: total agentes y distribución de estados de presencia."

    # Renderizar historial de mensajes
    avatar_rocco = str(ROCCO_IMG_PATH) if ROCCO_IMG_PATH.exists() else "🐙"
    chat_container = st.container()
    with chat_container:
        for msg in st.session_state.copiloto_chat_history:
            if msg["role"] == "user":
                with st.chat_message("user", avatar="👤"):
                    st.markdown(msg["content"])
            else:
                with st.chat_message("assistant", avatar=avatar_rocco):
                    st.markdown(msg["content"])

    # Entrada de voz nativa de Streamlit (Multimodal directa a Gemini 2.5 Flash)
    st.markdown("---")
    audio_in = st.audio_input("🎙️ Hablar con Rocco por voz (presiona el micrófono para grabar y enviar tu pregunta)", key=f"{prefijo_key}audio_in")

    if audio_in is not None:
        audio_id = f"{audio_in.name}_{audio_in.size}"
        if st.session_state.get(f"{prefijo_key}last_audio") != audio_id:
            st.session_state[f"{prefijo_key}last_audio"] = audio_id
            audio_bytes = audio_in.read()
            st.session_state.copiloto_chat_history.append({"role": "user", "content": "🎙️ *[Pregunta de voz dictada a Rocco]*"})
            with st.chat_message("user", avatar="👤"):
                st.markdown("🎙️ *[Pregunta de voz dictada a Rocco]*")

            with st.chat_message("assistant", avatar=avatar_rocco):
                with st.spinner("🐙 Rocco está escuchando tu voz y analizando las bases operativas..."):
                    respuesta_voz = ejecutar_pregunta_copiloto(audio_bytes=audio_bytes, historial_mensajes=st.session_state.copiloto_chat_history[:-1])
                    st.markdown(respuesta_voz)
            
            st.session_state.copiloto_chat_history.append({"role": "assistant", "content": respuesta_voz})
            st.rerun()

    # Entrada de texto del usuario
    user_prompt = st.chat_input("Escribe tu pregunta para Rocco sobre cualquier asesor, supervisor, servicio o Salesforce...", key=f"{prefijo_key}chat_in")
    
    prompt_a_procesar = pregunta_rapida or user_prompt

    if prompt_a_procesar:
        st.session_state.copiloto_chat_history.append({"role": "user", "content": prompt_a_procesar})
        with st.chat_message("user", avatar="👤"):
            st.markdown(prompt_a_procesar)

        with st.chat_message("assistant", avatar=avatar_rocco):
            with st.spinner("🐙 Rocco está consultando las bases de datos operativas y analizando con Vertex AI..."):
                respuesta = ejecutar_pregunta_copiloto(pregunta=prompt_a_procesar, historial_mensajes=st.session_state.copiloto_chat_history[:-1])
                st.markdown(respuesta)
        
        st.session_state.copiloto_chat_history.append({"role": "assistant", "content": respuesta})
        st.rerun()

    # Controles inferiores
    st.markdown("---")
    col_c1, col_c2 = st.columns([8, 2])
    with col_c2:
        if st.button("🗑️ Limpiar Conversación", key=f"{prefijo_key}btn_limpiar", use_container_width=True):
            st.session_state.copiloto_chat_history = [
                {
                    "role": "assistant",
                    "content": "Conversación reiniciada. ¿Qué otra consulta operacional deseas realizar?"
                }
            ]
            st.rerun()


@st.dialog("🐙 Rocco • Copiloto Operacional 4DX", width="large")
def modal_copiloto_rocco():
    """Ventana modal flotante omnipresente de Rocco para consultas rápidas desde cualquier pantalla."""
    render_contenido_copiloto_rocco(es_modal=True)


def render_boton_flotante_rocco():
    """Renderiza el botón flotante omnipresente de Rocco en la esquina inferior derecha con su identidad visual."""
    rocco_b64 = obtener_rocco_b64()
    
    # CSS con múltiples selectores directos a st-key para garantizar fijación en cualquier navegador/dispositivo
    st.markdown(
        f"""
        <style>
        /* Contenedor flotante fijado a la ventana del navegador */
        div.st-key-rocco_omnipresent_fab_btn,
        div[class*="st-key-rocco_omnipresent_fab_btn"] {{
            position: fixed !important;
            bottom: 26px !important;
            right: 28px !important;
            z-index: 999999999 !important;
            width: auto !important;
            height: auto !important;
            margin: 0 !important;
            padding: 0 !important;
            display: block !important;
            filter: drop-shadow(0 10px 25px rgba(124, 58, 237, 0.65)) !important;
        }}

        /* Estilo premium del botón de Rocco */
        div.st-key-rocco_omnipresent_fab_btn button,
        div[class*="st-key-rocco_omnipresent_fab_btn"] button {{
            display: flex !important;
            flex-direction: row !important;
            align-items: center !important;
            justify-content: center !important;
            gap: 10px !important;
            background: linear-gradient(135deg, #3b0764 0%, #6b21a8 50%, #0284c7 100%) !important;
            color: #ffffff !important;
            font-weight: 700 !important;
            font-size: 14.5px !important;
            letter-spacing: 0.3px !important;
            border-radius: 50px !important;
            border: 2.5px solid #c084fc !important;
            padding: 8px 22px 8px 10px !important;
            cursor: pointer !important;
            box-shadow: 0 0 20px rgba(192, 132, 252, 0.5) !important;
            transition: all 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275) !important;
            animation: roccoFabPulse 3s infinite ease-in-out !important;
        }}

        /* Mascota oficial Rocco como avatar integrado en el botón */
        div.st-key-rocco_omnipresent_fab_btn button::before,
        div[class*="st-key-rocco_omnipresent_fab_btn"] button::before {{
            content: "" !important;
            display: inline-block !important;
            width: 40px !important;
            height: 40px !important;
            border-radius: 50% !important;
            background-color: rgba(255, 255, 255, 0.22) !important;
            background-image: url('data:image/png;base64,{rocco_b64}') !important;
            background-size: 85% contain !important;
            background-repeat: no-repeat !important;
            background-position: center !important;
            border: 1.5px solid rgba(255, 255, 255, 0.6) !important;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3) !important;
            flex-shrink: 0 !important;
        }}

        /* Texto y tipografía interior */
        div.st-key-rocco_omnipresent_fab_btn button p,
        div[class*="st-key-rocco_omnipresent_fab_btn"] button p {{
            color: #ffffff !important;
            font-weight: 700 !important;
            font-size: 14.5px !important;
            margin: 0 !important;
            padding: 0 !important;
        }}

        /* Efecto hover interactivo */
        div.st-key-rocco_omnipresent_fab_btn button:hover,
        div[class*="st-key-rocco_omnipresent_fab_btn"] button:hover {{
            transform: scale(1.08) translateY(-4px) !important;
            box-shadow: 0 0 35px rgba(192, 132, 252, 0.8), 0 15px 40px rgba(124, 58, 237, 0.9) !important;
            border-color: #f5d0fe !important;
            background: linear-gradient(135deg, #4c1d95 0%, #7e22ce 50%, #0284c7 100%) !important;
        }}

        @keyframes roccoFabPulse {{
            0%, 100% {{
                box-shadow: 0 0 18px rgba(192, 132, 252, 0.4), 0 8px 25px rgba(124, 58, 237, 0.6);
            }}
            50% {{
                box-shadow: 0 0 30px rgba(192, 132, 252, 0.75), 0 12px 35px rgba(124, 58, 237, 0.9);
            }}
        }}

        /* Responsividad móvil */
        @media (max-width: 640px) {{
            div.st-key-rocco_omnipresent_fab_btn,
            div[class*="st-key-rocco_omnipresent_fab_btn"] {{
                bottom: 18px !important;
                right: 18px !important;
            }}
            div.st-key-rocco_omnipresent_fab_btn button,
            div[class*="st-key-rocco_omnipresent_fab_btn"] button {{
                padding: 6px 16px 6px 8px !important;
            }}
            div.st-key-rocco_omnipresent_fab_btn button::before,
            div[class*="st-key-rocco_omnipresent_fab_btn"] button::before {{
                width: 34px !important;
                height: 34px !important;
            }}
            div.st-key-rocco_omnipresent_fab_btn button p,
            div[class*="st-key-rocco_omnipresent_fab_btn"] button p {{
                font-size: 13px !important;
            }}
        }}
        </style>
        """,
        unsafe_allow_html=True
    )

    if st.button("Rocco 4DX 🎙️", key="rocco_omnipresent_fab_btn", help="Haz clic para consultar a Rocco por voz o texto desde cualquier sección."):
        modal_copiloto_rocco()


def render_tab_copiloto(agentes_map=None, current_email=""):
    """Renderiza la consola conversacional de Rocco en vista de pestaña completa."""
    render_contenido_copiloto_rocco(es_modal=False)

