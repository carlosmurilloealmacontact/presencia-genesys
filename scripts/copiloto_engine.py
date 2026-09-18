# copiloto_engine.py - Motor de Inteligencia Operativa y Copiloto Conversacional 4DX
# Impulsado por Vertex AI (Google Cloud) & Gemini 2.5 Flash

import os
import sqlite3
import json
import re
from datetime import datetime
from pathlib import Path
import pandas as pd
import streamlit as st

try:
    from google import genai
    from google.genai import types
    from google.oauth2 import service_account
    VERTEX_AVAILABLE = True
except Exception:
    VERTEX_AVAILABLE = False

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "presencia.db"
SF_CASES_PATH = BASE_DIR / "data" / "salesforce" / "cases_amc_cleaned.csv"
SF_OMNI_PATH = BASE_DIR / "data" / "salesforce" / "omni_presencia_historico.csv"

PROJECT_ID = "project-094fad9d-54da-42d9-880"
LOCATION = "us-central1"
MODEL_NAME = "gemini-2.5-flash"


def _obtener_cliente_vertex():
    """Inicializa el cliente oficial de Google GenAI con Vertex AI (soporta Streamlit Cloud secrets y local)."""
    if not VERTEX_AVAILABLE:
        return None, "La librería `google-genai` no está disponible en este entorno de Python."
    
    # 1. Soporte para Streamlit Cloud vía st.secrets["gcp_service_account"]
    if hasattr(st, "secrets") and "gcp_service_account" in st.secrets:
        try:
            sa_raw = st.secrets["gcp_service_account"]
            if isinstance(sa_raw, str):
                sa_info = json.loads(sa_raw)
            else:
                sa_info = dict(sa_raw)
            creds = service_account.Credentials.from_service_account_info(
                sa_info, scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION, credentials=creds)
            return client, None
        except Exception as err_sa:
            return None, f"Error leyendo `[gcp_service_account]` en Streamlit Secrets: {err_sa}"

    # 2. Fallback local / Application Default Credentials
    try:
        client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)
        return client, None
    except Exception as e:
        return None, (
            "Faltan las credenciales de Google Cloud en Streamlit Cloud. "
            "Debes agregar el bloque `[gcp_service_account]` en los Secrets de la aplicación en share.streamlit.io (Settings > Secrets)."
        )


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
            "mensaje": f"No se encontraron datos para '{nombre_o_id}' en fecha {fecha}.",
            "sugerencia": "Verifica si el nombre está bien escrito o consulta las fechas disponibles."
        }, ensure_ascii=False)

    nombre_oficial = rows_s[0][0] if rows_s else (row_t[0] if row_t else nombre_o_id)
    servicio = rows_s[0][1] if rows_s else (row_t[1] if row_t else "Desconocido")
    jefe = rows_s[0][2] if rows_s else "No registrado en Genesys"
    coordinador = rows_s[0][3] if rows_s else "No registrado en Genesys"

    estados = {r[4]: r[5] for r in rows_s}
    minutos_disponible = estados.get("Available", 0.0)
    minutos_almuerzo = estados.get("Almuerzo", estados.get("Lunch", 0.0))
    minutos_break = estados.get("Break", estados.get("Descanso", 0.0))
    minutos_bano = estados.get("Baño", estados.get("Bathroom", 0.0))
    minutos_casos = estados.get("Casos Backoffice", 0.0)

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
        "genesys_tiempos_minutos": {
            "disponible_llamadas": minutos_disponible,
            "almuerzo_real": minutos_almuerzo,
            "breaks_real": minutos_break,
            "bano": minutos_bano,
            "casos_backoffice": minutos_casos,
            "todos_los_estados": estados
        },
        "salesforce_b2b": sf_info
    }
    return json.dumps(resultado, ensure_ascii=False)


def consultar_equipo_supervisor(supervisor: str, fecha: str = "") -> str:
    """Consulta el desempeño global, lista de asesores, horas de conexión y alertas para el equipo de un supervisor específico."""
    if not DB_PATH.exists():
        return json.dumps({"error": "Base de datos no encontrada."})
        
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    if not fecha:
        c.execute("SELECT DISTINCT fecha FROM segments ORDER BY fecha DESC LIMIT 1")
        row_f = c.fetchone()
        fecha = row_f[0] if row_f else "2026-09-17"

    palabras = [p.strip() for p in re.split(r"[\s\-_]+", supervisor) if len(p.strip()) >= 3]
    query = """
        SELECT agente, servicio, jefe_inmediato, presence_label, ROUND(SUM(duracion_min), 1)
        FROM segments
        WHERE fecha = ?
    """
    params = [fecha]
    for p in palabras:
        query += " AND jefe_inmediato LIKE ?"
        params.append(f"%{p}%")
    query += " GROUP BY agente, presence_label"
    
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()

    if not rows:
        return json.dumps({"mensaje": f"No se encontraron asesores para el supervisor '{supervisor}' en fecha {fecha}."})

    equipo = {}
    jefe_oficial = rows[0][2]
    servicio_sup = rows[0][1]

    for r in rows:
        ag = r[0]
        label = r[3]
        mins = r[4]
        if ag not in equipo:
            equipo[ag] = {"total_minutos": 0.0, "disponible": 0.0, "pausas": 0.0, "estados": {}}
        equipo[ag]["estados"][label] = mins
        equipo[ag]["total_minutos"] += mins
        if label.lower() in ["available", "disponible"]:
            equipo[ag]["disponible"] += mins
        elif label.lower() in ["almuerzo", "break", "descanso", "lunch", "baño"]:
            equipo[ag]["pausas"] += mins

    resumen_asesores = []
    for ag, data in equipo.items():
        resumen_asesores.append({
            "agente": ag,
            "minutos_disponible": data["disponible"],
            "minutos_pausas": data["pausas"],
            "tiempo_total_registrado": round(data["total_minutos"], 1)
        })

    return json.dumps({
        "fecha": fecha,
        "supervisor": jefe_oficial,
        "servicio": servicio_sup,
        "total_asesores_conectados": len(equipo),
        "asesores": resumen_asesores
    }, ensure_ascii=False)


def consultar_servicio_macro(servicio: str, fecha: str = "") -> str:
    """Obtiene el resumen ejecutivo para una macro-campaña o servicio (ej. Agencias B2B, LATAM Pasajeros, Equipajes)."""
    if not DB_PATH.exists():
        return json.dumps({"error": "Base de datos no encontrada."})

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
        return json.dumps({"error": "No se encontró el archivo de casos de Salesforce (cases_amc_cleaned.csv)."})

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

        # Casos críticos (>24 horas)
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


def consultar_ausentismos(fecha: str = "", servicio: str = "") -> str:
    """Detecta asesores con turnos programados que no tuvieron conexión en Genesys ni actividad en Salesforce (posible ausentismo)."""
    if not DB_PATH.exists():
        return json.dumps({"error": "Base de datos no encontrada."})

    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    if not fecha:
        c.execute("SELECT DISTINCT fecha FROM turnos_detallados ORDER BY fecha DESC LIMIT 1")
        row_f = c.fetchone()
        fecha = row_f[0] if row_f else "2026-09-17"

    query_t = "SELECT nombre_agente, servicio, horas_programadas, turno_ini, turno_fin, novedad FROM turnos_detallados WHERE fecha = ?"
    params_t = [fecha]
    if servicio:
        query_t += " AND servicio LIKE ?"
        params_t.append(f"%{servicio}%")
    c.execute(query_t, params_t)
    turnos = c.fetchall()

    # Obtener agentes conectados en Genesys
    c.execute("SELECT DISTINCT agente FROM segments WHERE fecha = ?", (fecha,))
    agentes_genesys = [r[0] for r in c.fetchall()]
    conn.close()

    ausentes = []
    for t in turnos:
        nombre = t[0]
        serv = t[1]
        novedad = t[5]
        
        # Verificar si aparece en Genesys
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
        "lista_ausentes_o_con_novedad": ausentes[:15]
    }, ensure_ascii=False)


COPILOTO_TOOLS = [
    obtener_fechas_disponibles,
    consultar_asesor,
    consultar_equipo_supervisor,
    consultar_servicio_macro,
    consultar_backlog_salesforce,
    consultar_ausentismos
]

SYSTEM_INSTRUCTION = """
Eres el **Copiloto Operacional 4DX**, el asistente de inteligencia artificial de alto nivel para el equipo de Inteligencia Operativa de LATAM Airlines y AlmaContact.
Tu propósito es responder preguntas de Coordinadores, Jefaturas y Supervisores sobre:
1. Métricas de presencia, turnos, adherencia y pausas de Genesys Cloud CX.
2. Salud del backlog de casos de Salesforce B2B y cumplimiento del SLA de 24 horas.
3. Rescate operativo de asesores de Agencias B2B (asesores que no tienen login en Genesys pero sí gestionaron casos o chats en Salesforce).
4. Ausentismos y novedades de turno.

REGLAS CRÍTICAS:
- NUNCA inventes números ni nombres. Si no estás seguro o falta la fecha, ejecuta tus herramientas para consultar la base de datos o verificar las fechas disponibles.
- Regla de Oro de Marely Cardona: Quienes están bajo supervisión de Marely Cardona pertenecen exclusivamente a Agencias B2B (Corporativo/Pyme). Están blindados y separados de LATAM Pasajeros.
- Formato de respuesta: Responde siempre de manera ejecutiva, clara, cordial y profesional. Usa viñetas estructuradas, negritas para métricas clave y tablas Markdown cuando presentes listados o comparativos.
- Si una persona no tiene login en Genesys pero tiene casos en Salesforce, aclara explícitamente que fue RESCATADO por actividad en Salesforce.
- Manejo de fechas relativas: Si el usuario pregunta por "ayer", "hoy" o una fecha relativa sin especificar fecha exacta (YYYY-MM-DD), ejecuta primero 'obtener_fechas_disponibles' para usar la fecha más reciente de datos disponibles (por ejemplo 2026-09-17).
- Búsqueda por áreas/campañas: Si el usuario pregunta por "ventas", evalúa los servicios que coincidan como 'CHAT VENTAS AMC', 'VENTAS AMC', 'Ventas AMC' o 'WPP VENTAS AMC'.
"""


def ejecutar_pregunta_copiloto(pregunta: str, historial_mensajes: list = None) -> str:
    """Ejecuta una consulta contra Vertex AI utilizando las herramientas locales y el historial de chat."""
    client, error_msg = _obtener_cliente_vertex()
    if not client:
        return f"⚠️ {error_msg}"

    try:
        # Formatear contenidos
        contents = []
        if historial_mensajes:
            for m in historial_mensajes[-6:]:  # Mantener últimos turnos de contexto
                role = "user" if m["role"] == "user" else "model"
                contents.append(types.Content(role=role, parts=[types.Part.from_text(text=m["content"])]))
        contents.append(types.Content(role="user", parts=[types.Part.from_text(text=pregunta)]))

        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=contents,
            config=types.GenerateContentConfig(
                tools=COPILOTO_TOOLS,
                system_instruction=SYSTEM_INSTRUCTION,
                temperature=0.2
            )
        )
        return response.text if response.text else "No se obtuvo una respuesta detallada del modelo."
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
                    <span style="color: #4ade80; font-size: 12px; font-weight: 600;">🟢 Disponible ($3.6M COP)</span>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    # Validar conexión con Vertex AI
    client_test, client_err = _obtener_cliente_vertex()
    if not client_test:
        st.warning(
            f"⚙️ **Configuración requerida en Streamlit Cloud:**\n\n"
            f"{client_err}\n\n"
            "👉 Para solucionarlo: Entra a tu consola de **Streamlit Cloud** (share.streamlit.io) ➔ **Settings** ➔ **Secrets** y pega las credenciales de `[gcp_service_account]`."
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
        # Añadir al historial
        st.session_state.copiloto_chat_history.append({"role": "user", "content": prompt_a_procesar})
        with st.chat_message("user", avatar="👤"):
            st.markdown(prompt_a_procesar)

        # Respuesta con spinner
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
