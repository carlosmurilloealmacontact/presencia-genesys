"""
Motor de Monitoreo en Vivo — Radar Genesys Cloud.
Maneja la autenticación, consulta en tiempo real con Genesys y renderizado de la pestaña en vivo.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import time

import pandas as pd
import requests
import streamlit as st

COLOMBIA_OFFSET = timedelta(hours=-5)
COLOMBIA_TZ = timezone(COLOMBIA_OFFSET)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH_DEFAULT = os.path.normpath(
    os.path.join(BASE_DIR, "../../Seguimiento Pausas 4DX/scripts/genesys_token.txt")
)

ESTADOS_SISTEMA = {"Offline", "Available", "Conectado", "On Queue"}


def servicio_autorizado_casos_bo(servicio: str) -> bool:
    """Casos Backoffice está autorizado para servicios BO y células de Chat/Redes autorizadas."""
    s = (servicio or "").upper()
    if s.startswith("BO ") or s.startswith("BO_") or "BACKOFFICE" in s:
        return True
    if "AGY" in s and "CHAT" in s:
        return True
    celulas = (
        "RRSS AMC",
        "CHAT AGENCIAS ESP",
        "AGY N1 ESP CHAT",
        "AGY N3 ESP CHAT",
        "AG CORPORATE CHAT",
        "SPEECH",
        "AG CHECK IN",
        "AG CELULA REMISION",
        "CARGO BOOKING",
        "RRSS AMC ING",
        "RRSS PORT AMC",
        "ANTIFRAUDE",
    )
    return any(c in s for c in celulas)


def obtener_token_genesys() -> str | None:
    """Busca el token en archivo local, st.secrets o base de datos Neon Postgres."""
    # 1. Archivo local de renovación automática (entorno local)
    if os.path.exists(TOKEN_PATH_DEFAULT):
        try:
            with open(TOKEN_PATH_DEFAULT, "r", encoding="utf-8") as f:
                t = f.read().strip()
                if t:
                    return t
        except Exception:
            pass

    # 2. Variable directa st.secrets["GENESYS_TOKEN"]
    try:
        if "GENESYS_TOKEN" in st.secrets:
            t = str(st.secrets["GENESYS_TOKEN"]).strip()
            if t:
                return t
    except Exception:
        pass

    # 3. Variable de entorno directa
    env_token = os.environ.get("GENESYS_TOKEN", "").strip()
    if env_token:
        return env_token

    # 4. Sincronización automática vía Neon Postgres (nube)
    neon_url = None
    try:
        if "NEON_DB_URL" in st.secrets:
            neon_url = str(st.secrets["NEON_DB_URL"]).strip()
    except Exception:
        pass
    if not neon_url:
        neon_url = os.environ.get("NEON_DB_URL")

    if neon_url:
        try:
            import psycopg2
            conn = psycopg2.connect(neon_url)
            cur = conn.cursor()
            cur.execute("SELECT value FROM genesys_config WHERE key = 'GENESYS_TOKEN' LIMIT 1;")
            row = cur.fetchone()
            cur.close()
            conn.close()
            if row and row[0]:
                return str(row[0]).strip()
        except Exception as err:
            print(f"Error consultando token en Neon: {err}")

    return None


def safe_genesys_get(url: str, headers: dict, params: dict = None, max_retries: int = 3):
    """Consulta GET a Genesys con reintento automático si responde 429 (Rate Limit)."""
    for attempt in range(max_retries):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=15)
            if r.status_code == 429:
                wait_sec = int(r.headers.get("Retry-After", 3))
                time.sleep(min(wait_sec, 8))
                continue
            return r
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(2)
            else:
                raise
    return r


@st.cache_data(ttl=3600)
def cargar_catalogo_presencias(token: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    try:
        r = safe_genesys_get(
            "https://api.mypurecloud.com/api/v2/presencedefinitions?pageSize=100",
            headers=headers,
        )
        if r and r.status_code == 200:
            catalog = {}
            for e in r.json().get("entities", []):
                labels = e.get("languageLabels", {})
                label = (
                    labels.get("es")
                    or labels.get("en_US")
                    or labels.get("en")
                    or e.get("systemPresence", "Desconocido")
                )
                catalog[e["id"]] = {"label": label, "systemPresence": e.get("systemPresence", "")}
            return catalog
    except Exception as err:
        st.error(f"Error cargando catálogo de presencias: {err}")
    return {}


@st.cache_data(ttl=30, show_spinner=False)
def obtener_aht_hoy(token: str) -> dict:
    """Consulta analytics conversation aggregate para obtener atendidas y AHT en segundos de hoy."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    now_utc = datetime.now(timezone.utc)
    bogota_today = now_utc.astimezone(timezone(timedelta(hours=-5))).replace(hour=0, minute=0, second=0, microsecond=0)
    start_utc = bogota_today.astimezone(timezone.utc)
    s_str = start_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    e_str = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    interval = f"{s_str}/{e_str}"

    body = {
        "interval": interval,
        "groupBy": ["userId"],
        "metrics": ["tHandle"],
        "filter": {
            "type": "and",
            "predicates": [
                {"type": "dimension", "dimension": "purpose", "operator": "matches", "value": "agent"}
            ]
        }
    }
    try:
        r = requests.post("https://api.mypurecloud.com/api/v2/analytics/conversations/aggregates/query", headers=headers, json=body, timeout=12)
        if r.status_code == 200:
            mapa_aht = {}
            for item in r.json().get("results", []):
                uid = item.get("group", {}).get("userId")
                data = item.get("data", [])
                if uid and data:
                    metrics = {m.get("metric"): m.get("stats") for m in data[0].get("metrics", [])}
                    th = metrics.get("tHandle", {})
                    count = th.get("count", 0)
                    tot_ms = th.get("sum", 0)
                    if count > 0 and tot_ms > 0:
                        aht_seg = int(round((tot_ms / count) / 1000.0))
                        mapa_aht[uid] = {
                            "atendidas_hoy": count,
                            "aht_seg": aht_seg,
                            "aht_seg_str": f"{aht_seg}s",
                        }
            return mapa_aht
    except Exception:
        pass
    return {}


@st.cache_data(ttl=25, show_spinner=False)
def obtener_presencia_en_vivo(token: str, agentes_map: dict, catalog: dict) -> pd.DataFrame:
    headers = {"Authorization": f"Bearer {token}"}
    try:
        r_init = safe_genesys_get(
            "https://api.mypurecloud.com/api/v2/users",
            headers=headers,
            params={"pageSize": 100, "pageNumber": 1, "expand": "presence,routingStatus"},
        )
        if not r_init or r_init.status_code == 401:
            st.error("⚠️ El token de Genesys ha expirado o no es válido.")
            return pd.DataFrame()
        if r_init.status_code != 200:
            st.error(f"Error consultando usuarios en Genesys: {r_init.status_code}")
            return pd.DataFrame()

        data_init = r_init.json()
        total_pages = data_init.get("pageCount", 1)

        def fetch_page(p):
            r = safe_genesys_get(
                "https://api.mypurecloud.com/api/v2/users",
                headers=headers,
                params={"pageSize": 100, "pageNumber": p, "expand": "presence,routingStatus"},
            )
            return r.json().get("entities", []) if r and r.status_code == 200 else []

        with ThreadPoolExecutor(max_workers=5) as executor:
            paginas = list(executor.map(fetch_page, range(1, total_pages + 1)))

        now_utc = datetime.now(timezone.utc)
        mapa_aht = obtener_aht_hoy(token)
        wsp_live = {}
        try:
            from whatsapp_simultaneidad_engine import obtener_simultaneidad_en_vivo
            wsp_live = obtener_simultaneidad_en_vivo(token)
        except Exception:
            pass
        mapa_wsp_agentes = wsp_live.get("agentes", {})
        filas = []

        for page_entities in paginas:
            for u in page_entities:
                uid = u.get("id")
                if uid in agentes_map:
                    meta_agente = agentes_map[uid]
                    pres = u.get("presence", {})
                    p_def_id = pres.get("presenceDefinition", {}).get("id")
                    p_info = catalog.get(
                        p_def_id,
                        {
                            "label": pres.get("presenceDefinition", {}).get("systemPresence", "N/A"),
                            "systemPresence": pres.get("presenceDefinition", {}).get("systemPresence", "N/A"),
                        },
                    )
                    mod_date_str = pres.get("modifiedDate")
                    routing = u.get("routingStatus", {}).get("status", "OFF_QUEUE")
                    routing_start = u.get("routingStatus", {}).get("startTime")

                    wsp_ag = mapa_wsp_agentes.get(uid, {})
                    chats_wsp = wsp_ag.get("chats_activos", 0)

                    dur_seg = 0
                    hora_inicio_str = "—"
                    if mod_date_str:
                        dt_mod = datetime.fromisoformat(mod_date_str.replace("Z", "+00:00"))
                        dur_seg = max(0, int((now_utc - dt_mod).total_seconds()))
                        dt_col = dt_mod.astimezone(COLOMBIA_TZ)
                        hora_inicio_str = dt_col.strftime("%H:%M:%S")

                    # Cronómetro formateado de presencia
                    h = dur_seg // 3600
                    m = (dur_seg % 3600) // 60
                    s = dur_seg % 60
                    cronometro = f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"

                    label = p_info["label"]
                    sys_pres = p_info["systemPresence"]
                    min_dur = dur_seg / 60.0

                    # Duración y cronómetro de llamada activa en curso
                    dur_llamada_seg = 0
                    dur_llamada_min = 0.0
                    cronometro_llamada = "—"
                    if routing == "INTERACTING" and sys_pres != "Offline" and routing_start:
                        try:
                            dt_rs = datetime.fromisoformat(routing_start.replace("Z", "+00:00"))
                            dur_llamada_seg = max(0, int((now_utc - dt_rs).total_seconds()))
                            dur_llamada_min = dur_llamada_seg / 60.0
                            hl = dur_llamada_seg // 3600
                            ml = (dur_llamada_seg % 3600) // 60
                            sl = dur_llamada_seg % 60
                            cronometro_llamada = f"{hl:02d}:{ml:02d}:{sl:02d}" if hl > 0 else f"{ml:02d}:{sl:02d}"
                        except Exception:
                            pass

                    # Alertas en tiempo real (Pausas y Llamadas)
                    alerta = "Normal"
                    nivel_alerta = "ok"

                    if sys_pres == "Offline":
                        alerta = "Desconectado"
                        nivel_alerta = "offline"
                    elif routing == "INTERACTING" and dur_llamada_min >= 15.0:
                        alerta = f"📞 Llamada prolongada ({cronometro_llamada})"
                        nivel_alerta = "danger" if dur_llamada_min >= 20.0 else "warning"
                    elif label == "Baño" and min_dur > 5.0:
                        alerta = f"🚨 Baño excedido (+{min_dur - 5.0:.1f} min)"
                        nivel_alerta = "danger"
                    elif label in ("Break", "Descanso") and min_dur > 15.0:
                        alerta = f"🚨 Break excedido (+{min_dur - 15.0:.1f} min)"
                        nivel_alerta = "danger"
                    elif label in ("Diálogo Diario / 4DX", "PCA- Diálogo") and min_dur > 15.0:
                        alerta = f"⚠️ Diálogo prolongado (+{min_dur - 15.0:.1f} min)"
                        nivel_alerta = "warning"
                    elif label == "Lunch":
                        alerta = f"🚨 Lunch no autorizado ({min_dur:.1f} min)"
                        nivel_alerta = "danger"
                    elif label == "Pre Pausa" and min_dur > 60.0:
                        alerta = f"🚨 Pre Pausa excedida (+{min_dur - 60.0:.1f} min)"
                        nivel_alerta = "danger"
                    elif label in ("Feedback", "PCA - Feedback") and min_dur > 30.0:
                        alerta = f"⚠️ Feedback prolongado (+{min_dur - 30.0:.1f} min)"
                        nivel_alerta = "warning"
                    elif label == "Cursos Adicionales" and min_dur > 60.0:
                        alerta = f"⚠️ Cursos prolongados (+{min_dur - 60.0:.1f} min)"
                        nivel_alerta = "warning"
                    elif label == "Refuerzo Semanal" and min_dur > 60.0:
                        alerta = f"⚠️ Refuerzo prolongado (+{min_dur - 60.0:.1f} min)"
                        nivel_alerta = "warning"
                    elif label == "Autogestión" and min_dur > 30.0:
                        alerta = f"⚠️ Autogestión prolongada (+{min_dur - 30.0:.1f} min)"
                        nivel_alerta = "warning"
                    elif label == "Gestión sin Contacto":
                        alerta = f"🚨 Gestión sin Contacto (No aut. {min_dur:.1f} min)"
                        nivel_alerta = "danger"
                    elif label == "Casos Backoffice" and not servicio_autorizado_casos_bo(meta_agente.get("servicio", "")):
                        alerta = f"🚨 Casos BO no autorizado en {meta_agente.get('servicio', '')}"
                        nivel_alerta = "danger"
                    elif chats_wsp > 0:
                        if chats_wsp > 1:
                            alerta = f"💬 WSP ({chats_wsp}x) ({cronometro_llamada})"
                        else:
                            alerta = f"💬 WhatsApp ({cronometro_llamada})"
                        nivel_alerta = "ok"
                    elif routing == "INTERACTING":
                        alerta = f"En llamada ({cronometro_llamada})"
                        nivel_alerta = "ok"

                    info_aht = mapa_aht.get(uid, {"atendidas_hoy": 0, "aht_seg": None, "aht_seg_str": "—"})
                    atendidas_hoy = info_aht["atendidas_hoy"]
                    aht_seg = info_aht["aht_seg"] if atendidas_hoy > 0 else None
                    aht_seg_str = info_aht["aht_seg_str"]

                    filas.append({
                        "agente": meta_agente["agente"],
                        "coordinador": meta_agente["coordinador"],
                        "servicio": meta_agente["servicio"],
                        "supervisor": meta_agente["jefe_inmediato"],
                        "estado": label,
                        "sys_pres": sys_pres,
                        "routing": routing,
                        "chats_wsp": chats_wsp,
                        "hora_inicio": hora_inicio_str,
                        "cronometro": cronometro,
                        "dur_min": round(min_dur, 1),
                        "dur_seg": dur_seg,
                        "dur_llamada_min": round(dur_llamada_min, 1),
                        "dur_llamada_seg": dur_llamada_seg,
                        "cronometro_llamada": cronometro_llamada,
                        "atendidas_hoy": atendidas_hoy,
                        "aht_seg": aht_seg,
                        "aht_seg_str": aht_seg_str,
                        "alerta": alerta,
                        "nivel_alerta": nivel_alerta,
                    })

        return pd.DataFrame(filas)
    except Exception as err:
        st.error(f"Error inesperado consultando Genesys: {err}")
        return pd.DataFrame()


@st.fragment(run_every=30)
def render_tab_en_vivo(agentes_map: dict, coordinador_forzado: str = None, key_prefix: str = ""):
    token = obtener_token_genesys()
    if not token:
        st.warning(
            "🔒 **Monitoreo en Vivo no disponible en este entorno.**\n\n"
            "Para activar el monitoreo en vivo en la nube pública, configure el token en Streamlit Secrets (`GENESYS_TOKEN`). "
            "En red local se activa automáticamente al encontrar el archivo de renovación de token de Genesys Cloud.",
            icon="ℹ️",
        )
        return

    catalog = cargar_catalogo_presencias(token)

    # Filtrar mapa de agentes si hay coordinador forzado
    agentes_scope = agentes_map
    if coordinador_forzado:
        agentes_scope = {
            k: v for k, v in agentes_map.items()
            if "MARELYN" in (v.get("coordinador") or "").upper() or "CARDONA" in (v.get("coordinador") or "").upper()
        }

    # Encabezado
    col_t, col_btn = st.columns([4, 1.2])
    with col_t:
        sub_titulo = "🔴 Monitoreo de Piso y Estados en Vivo"
        if coordinador_forzado:
            sub_titulo += f" — Coordinación {coordinador_forzado}"
        st.subheader(sub_titulo)
        st.caption("Auto-actualización cada 30 segundos con cronómetros en tiempo real")

    with col_btn:
        st.write("")
        if st.button("🔄 Actualizar Ahora", key=f"{key_prefix}btn_refrescar_live", width="stretch"):
            st.rerun(scope="fragment")

    t0 = time.time()
    df_live = obtener_presencia_en_vivo(token, agentes_scope, catalog)
    t_descarga = time.time() - t0

    if coordinador_forzado and not df_live.empty and "coordinador" in df_live.columns:
        df_live = df_live[df_live["coordinador"].astype(str).str.contains("CARDONA|MARELYN", case=False, na=False)]

    if df_live.empty:
        st.info("Sin datos recibidos de Genesys Cloud en este momento.")
        return

    hora_actual = datetime.now(COLOMBIA_TZ).strftime("%I:%M:%S %p")
    df_con_aht_tot = df_live[df_live["atendidas_hoy"] > 0]
    aht_prom_global = int(df_con_aht_tot["aht_seg"].mean()) if not df_con_aht_tot.empty else 0
    tot_atendidas_global = int(df_live["atendidas_hoy"].sum())

    st.markdown(
        f"<p style='color:gray; font-size:13px;'>Última sincronización: <b>{hora_actual}</b> (en {t_descarga:.1f}s) · <b>{len(df_live)}</b> asesores · 🎯 <b>{tot_atendidas_global}</b> interacciones hoy · ⏱️ AHT Promedio: <b>{aht_prom_global}s</b></p>",
        unsafe_allow_html=True,
    )

    # ── 1. Filtros de Piso (Afectan a las tarjetas, al cuadro de alertas y a la tabla) ──
    st.markdown("##### Filtros de Piso")
    f_col1, f_col2, f_col3, f_col4, f_col5 = st.columns([1.2, 1.2, 1.2, 1.3, 1.1])

    df_filtrado = df_live.copy()

    if coordinador_forzado:
        with f_col1:
            st.text_input("Coordinador", value=coordinador_forzado, disabled=True, key=f"{key_prefix}live_coord_fixed")
    else:
        coords_disp = sorted([c for c in df_live["coordinador"].unique() if c and pd.notna(c)])
        with f_col1:
            coord_sel = st.multiselect("Coordinador", options=coords_disp, placeholder="Todos", key=f"{key_prefix}live_coord_sel")
        if coord_sel:
            df_filtrado = df_filtrado[df_filtrado["coordinador"].isin(coord_sel)]

    servicios_disp = sorted([s for s in df_filtrado["servicio"].unique() if s and pd.notna(s)])
    with f_col2:
        serv_sel = st.multiselect("Servicio", options=servicios_disp, placeholder="Todos", key=f"{key_prefix}live_serv_sel")

    if serv_sel:
        df_filtrado = df_filtrado[df_filtrado["servicio"].isin(serv_sel)]

    supervs_disp = sorted([sp for sp in df_filtrado["supervisor"].unique() if sp and pd.notna(sp)])
    with f_col3:
        superv_sel = st.multiselect("Supervisor", options=supervs_disp, placeholder="Todos", key=f"{key_prefix}live_superv_sel")

    if superv_sel:
        df_filtrado = df_filtrado[df_filtrado["supervisor"].isin(superv_sel)]

    with f_col4:
        buscar_agente = st.text_input("Buscar por Asesor o BP", placeholder="Ej: 4512348...", key=f"{key_prefix}live_buscar_agente")

    if buscar_agente:
        df_filtrado = df_filtrado[df_filtrado["agente"].str.contains(buscar_agente, case=False, na=False)]

    with f_col5:
        umbral_llamada = st.number_input(
            "Alerta Llamada >",
            min_value=5,
            max_value=120,
            value=15,
            step=5,
            key=f"{key_prefix}live_umbral_llamada",
            help="Marca como llamada prolongada las llamadas en curso que superen estos minutos.",
        )

    # Actualizar alertas de llamada según el umbral configurado por el usuario
    for idx, r in df_filtrado.iterrows():
        if r["routing"] == "INTERACTING" and r["sys_pres"] != "Offline":
            if r["dur_llamada_min"] >= umbral_llamada:
                df_filtrado.at[idx, "alerta"] = f"📞 Llamada prolongada ({r['cronometro_llamada']})"
                df_filtrado.at[idx, "nivel_alerta"] = "danger" if r["dur_llamada_min"] >= (umbral_llamada + 5) else "warning"
            elif r["nivel_alerta"] in ("danger", "warning") and "Llamada" in str(r["alerta"]):
                df_filtrado.at[idx, "alerta"] = f"En llamada ({r['cronometro_llamada']})"
                df_filtrado.at[idx, "nivel_alerta"] = "ok"

    # ── 2. Métricas y KPIs de Piso (Filtrados) ───────────────────────────
    conectados = df_filtrado[df_filtrado["sys_pres"] != "Offline"]
    en_llamada = df_filtrado[(df_filtrado["sys_pres"] != "Offline") & (df_filtrado["routing"] == "INTERACTING")]
    en_wsp = df_filtrado[df_filtrado["chats_wsp"] > 0]
    tot_chats_wsp = int(df_filtrado["chats_wsp"].sum())
    llamadas_largas = en_llamada[en_llamada["dur_llamada_min"] >= umbral_llamada]
    disponibles = df_filtrado[(df_filtrado["estado"].isin(["Available", "On Queue"])) & (df_filtrado["routing"] == "IDLE")]
    en_pausas_regla = df_filtrado[df_filtrado["estado"].isin(["Break", "Baño", "Descanso", "Pre Pausa", "Lunch", "CDR"])]
    en_gestion = df_filtrado[
        (~df_filtrado["estado"].isin(ESTADOS_SISTEMA))
        & (~df_filtrado["estado"].isin(["Break", "Baño", "Descanso", "Pre Pausa", "Lunch", "CDR"]))
    ]
    alertas_llamadas = llamadas_largas
    alertas_breaks = df_filtrado[
        df_filtrado["nivel_alerta"].isin(["danger", "warning"])
        & (df_filtrado["routing"] != "INTERACTING")
    ]
    todas_alertas = df_filtrado[df_filtrado["nivel_alerta"].isin(["danger", "warning"])]

    k1, k2, k3, k4, k5, k6, k7, k8 = st.columns(8)

    if "live_focos_activos" not in st.session_state:
        prev_foco = st.session_state.get("live_vista_rapida", "Solo Conectados")
        if isinstance(prev_foco, list):
            st.session_state["live_focos_activos"] = prev_foco
        elif prev_foco in (
            "Todos", "Solo Conectados", "Solo Llamadas Activas", "Solo WhatsApp Activo",
            "Solo Llamadas Prolongadas", "Solo Excesos de Breaks", "Solo En Cola",
            "Solo Pausas con Meta", "Solo Gestión"
        ):
            st.session_state["live_focos_activos"] = [prev_foco]
        else:
            st.session_state["live_focos_activos"] = ["Solo Conectados"]

    focos_activos = list(st.session_state.get("live_focos_activos", ["Solo Conectados"]))
    if not focos_activos:
        focos_activos = ["Solo Conectados"]
        st.session_state["live_focos_activos"] = focos_activos

    def render_kpi_interactivo(col, titulo, valor, subtitulo, color, foco_asociado):
        es_activo = (foco_asociado in focos_activos)
        borde_k = f"border: 2px solid {color}; box-shadow: 0 0 10px {color}44; background: #fff;" if es_activo else f"border-left: 4px solid {color}; background: #f8f9fa;"
        tag_k = "<span style='float:right; font-size:9px; background:#185fa5; color:#fff; padding:1px 5px; border-radius:6px;'>✓ Activo</span>" if es_activo else ""

        with col:
            st.markdown(
                f"""
                <div style="{borde_k} border-radius:8px; padding:8px 10px; margin-bottom:4px; transition: all 0.2s; min-height: 85px;">
                    <p style="color:#666; font-size:11.5px; margin:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">{titulo} {tag_k}</p>
                    <p style="color:{color}; font-size:22px; font-weight:700; margin:2px 0;">{valor}</p>
                    <p style="color:#888; font-size:10.5px; margin:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;">{subtitulo}</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
            btn_txt = "✖ Quitar" if es_activo else "🔍 Filtrar"
            if st.button(btn_txt, key=f"btn_live_card_{foco_asociado}", width="stretch", type="primary" if es_activo else "secondary"):
                actuales = list(st.session_state.get("live_focos_activos", ["Solo Conectados"]))

                if foco_asociado in ("Solo Conectados", "Todos"):
                    if es_activo and foco_asociado == "Solo Conectados":
                        st.session_state["live_focos_activos"] = ["Todos"]
                    else:
                        st.session_state["live_focos_activos"] = [foco_asociado]
                else:
                    if "Solo Conectados" in actuales:
                        actuales.remove("Solo Conectados")
                    if "Todos" in actuales:
                        actuales.remove("Todos")

                    if foco_asociado in actuales:
                        actuales.remove(foco_asociado)
                    else:
                        actuales.append(foco_asociado)

                    if not actuales:
                        actuales = ["Solo Conectados"]
                    st.session_state["live_focos_activos"] = actuales

                st.session_state["ms_live_focos_select"] = st.session_state["live_focos_activos"]
                st.rerun(scope="fragment")

    pct_con = (len(conectados) / len(df_filtrado) * 100.0) if len(df_filtrado) > 0 else 0.0
    llamadas_puras_voz = len(en_llamada[en_llamada["chats_wsp"] == 0])

    render_kpi_interactivo(k1, "Conectados", len(conectados), f"{pct_con:.0f}% del filtro", "#1baf7a", "Solo Conectados")
    render_kpi_interactivo(k2, "📞 En Voz", llamadas_puras_voz, "Llamadas en curso", "#185FA5", "Solo Llamadas Activas")
    render_kpi_interactivo(k3, "💬 WhatsApp", f"{tot_chats_wsp} chats", f"{len(en_wsp)} asesores activos", "#047857", "Solo WhatsApp Activo")
    render_kpi_interactivo(k4, "En Cola", len(disponibles), "Esperando contacto", "#0F825C", "Solo En Cola")
    render_kpi_interactivo(k5, "En Pausas de Ley", len(en_pausas_regla), "Break, Baño, Pre Pausa", "#BA7517", "Solo Pausas con Meta")
    render_kpi_interactivo(k6, "En Gestión / BO", len(en_gestion), "Backoffice, Autogestión", "#6347A6", "Solo Gestión")
    render_kpi_interactivo(k7, "📞 Llamadas Largas", len(alertas_llamadas), f"> {umbral_llamada} min en curso", "#EA580C" if len(alertas_llamadas) > 0 else "#888", "Solo Llamadas Prolongadas")
    render_kpi_interactivo(k8, "☕ Excesos Breaks", len(alertas_breaks), "Breaks y pausas excedidos", "#DC2626" if len(alertas_breaks) > 0 else "#888", "Solo Excesos de Breaks")

    # Barra informativa cuando hay múltiples filtros de tarjeta activos
    TITULOS_FOCOS = {
        "Solo Conectados": "Conectados",
        "Solo Llamadas Activas": "En Voz",
        "Solo WhatsApp Activo": "💬 En WhatsApp",
        "Solo En Cola": "En Cola Disponibles",
        "Solo Pausas with Meta": "En Pausas de Ley",
        "Solo Pausas con Meta": "En Pausas de Ley",
        "Solo Gestión": "En Gestión / BO",
        "Solo Llamadas Prolongadas": "📞 Llamadas Largas",
        "Solo Excesos de Breaks": "☕ Excesos de Breaks",
        "Todos": "Todos los Registros",
    }
    if len(focos_activos) > 1 or (len(focos_activos) == 1 and focos_activos[0] != "Solo Conectados"):
        tags_activos = " ".join([
            f"<span style='background:#185fa5; color:#fff; padding:2px 8px; border-radius:10px; font-size:11px; margin-right:4px; font-weight:600;'>{TITULOS_FOCOS.get(f, f)}</span>"
            for f in focos_activos
        ])
        st.markdown(
            f"""
            <div style="background:#e8f0fe; border-left:4px solid #185fa5; border-radius:6px; padding:6px 12px; margin:6px 0 10px 0; display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:6px;">
                <div style="font-size:12px; color:#185fa5;">
                    <b>Filtro de visualización activo (Combinado):</b> {tags_activos}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # ── 3. Bandeja Unificada de Alertas en Vivo (Llamadas Prolongadas y Excesos de Breaks) ──
    filtro_txt = ""
    if coord_sel:
        filtro_txt += f" · Coord: {', '.join(coord_sel)}"
    if serv_sel:
        filtro_txt += f" · Servicio: {', '.join(serv_sel)}"
    if superv_sel:
        filtro_txt += f" · Sup: {', '.join(superv_sel)}"

    if not alertas_llamadas.empty:
        items_ll = []
        for _, r in alertas_llamadas.sort_values(by="dur_llamada_seg", ascending=False).iterrows():
            nom = r["agente"]
            stag = f" <span style='color:#777;'>({r['servicio']})</span>" if not serv_sel else ""
            items_ll.append(f"<b>{nom}</b>{stag}: {r['cronometro_llamada']}")
        st.markdown(
            f"""
            <div style="background:#fff4eb; border:1px solid #fed7aa; border-left:4px solid #ea580c; border-radius:8px; padding:10px 14px; margin-bottom:10px;">
                <b style="color:#c2410c; font-size:14px;">📞 {len(alertas_llamadas)} Llamada(s) Prolongada(s) > {umbral_llamada} min{filtro_txt}:</b>
                <div style="margin-top:5px; color:#9a3412; font-size:12.5px; line-height:1.6; max-height:120px; overflow-y:auto;">
                    {" &nbsp;·&nbsp; ".join(items_ll)}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    if not alertas_breaks.empty:
        items_br = []
        for _, r in alertas_breaks.sort_values(by="dur_seg", ascending=False).iterrows():
            nom = r["agente"]
            stag = f" <span style='color:#777;'>({r['servicio']})</span>" if not serv_sel else ""
            items_br.append(f"<b>{nom}</b>{stag}: {r['alerta']}")
        st.markdown(
            f"""
            <div style="background:#fee8e7; border:1px solid #f9c0bc; border-left:4px solid #dc2626; border-radius:8px; padding:10px 14px; margin-bottom:14px;">
                <b style="color:#991b1b; font-size:14px;">☕ {len(alertas_breaks)} Exceso(s) de Breaks y Pausas{filtro_txt}:</b>
                <div style="margin-top:5px; color:#7f1d1d; font-size:12.5px; line-height:1.6; max-height:120px; overflow-y:auto;">
                    {" &nbsp;·&nbsp; ".join(items_br)}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # ── 4. Controles de Visualización de Tabla ───────────────────────────
    c_foco, c_orden = st.columns([1.8, 1.2])
    with c_foco:
        OPCIONES_FOCO = {
            "Solo Conectados": "🟢 Conectados (Línea Base)",
            "Solo Llamadas Activas": "📞 En Voz (Interacción)",
            "Solo WhatsApp Activo": "💬 En WhatsApp (Simultaneidad)",
            "Solo En Cola": "🟢 En Cola Disponibles",
            "Solo Pausas with Meta": "☕ En Pausas de Ley",
            "Solo Pausas con Meta": "☕ En Pausas de Ley",
            "Solo Gestión": "📂 En Gestión / BO",
            "Solo Llamadas Prolongadas": "📞 Llamadas Largas (> umbral)",
            "Solo Excesos de Breaks": "☕ Excesos de Breaks",
            "Todos": "👥 Todos (Incluye Desconectados)",
        }
        if "ms_live_focos_select" not in st.session_state:
            st.session_state["ms_live_focos_select"] = focos_activos
        elif not st.session_state["ms_live_focos_select"]:
            st.session_state["ms_live_focos_select"] = ["Solo Conectados"]

        focos_ms = st.multiselect(
            "Foco de Vista (puedes combinar múltiples estados):",
            options=list(OPCIONES_FOCO.keys()),
            key="ms_live_focos_select",
            format_func=lambda k: OPCIONES_FOCO.get(k, k),
            help="Elige uno o varios estados para combinar. Se sincroniza con las tarjetas interactivas superiores."
        )
        if focos_ms and set(focos_ms) != set(st.session_state.get("live_focos_activos", [])):
            st.session_state["live_focos_activos"] = focos_ms
            st.rerun(scope="fragment")

    with c_orden:
        orden_piso = st.selectbox(
            "Ordenar por",
            options=[
                "Nivel de Alerta y Duración",
                "Más Chats WhatsApp Activos",
                "Mayor AHT Hoy (seg)",
                "Más Interacciones Hoy",
                "Llamada más larga primero",
                "Tiempo en Estado (mayor a menor)",
                "Nombre del Asesor",
            ],
            index=0,
            key="live_orden_piso",
        )

    focos_a_filtrar = focos_ms if focos_ms else focos_activos
    if not focos_a_filtrar:
        focos_a_filtrar = ["Solo Conectados"]

    mascaras = []
    for f in focos_a_filtrar:
        if f == "Solo Conectados":
            mascaras.append(df_filtrado["sys_pres"] != "Offline")
        elif f == "Solo Llamadas Activas":
            mascaras.append((df_filtrado["sys_pres"] != "Offline") & (df_filtrado["routing"] == "INTERACTING") & (df_filtrado["chats_wsp"] == 0))
        elif f == "Solo WhatsApp Activo":
            mascaras.append(df_filtrado["chats_wsp"] > 0)
        elif f == "Solo Llamadas Prolongadas":
            mascaras.append(
                (df_filtrado["sys_pres"] != "Offline")
                & (df_filtrado["routing"] == "INTERACTING")
                & (df_filtrado["dur_llamada_min"] >= umbral_llamada)
            )
        elif f == "Solo En Cola":
            mascaras.append((df_filtrado["estado"].isin(["Available", "On Queue"])) & (df_filtrado["routing"] == "IDLE"))
        elif f in ("Solo Pausas con Meta", "Solo Pausas with Meta"):
            mascaras.append(df_filtrado["estado"].isin(["Break", "Baño", "Descanso", "Pre Pausa", "Lunch", "CDR"]))
        elif f == "Solo Gestión":
            mascaras.append(
                (~df_filtrado["estado"].isin(ESTADOS_SISTEMA))
                & (~df_filtrado["estado"].isin(["Break", "Baño", "Descanso", "Pre Pausa", "Lunch", "CDR"]))
            )
        elif f == "Solo Excesos de Breaks":
            mascaras.append(
                df_filtrado["nivel_alerta"].isin(["danger", "warning"])
                & (df_filtrado["routing"] != "INTERACTING")
            )
        elif f in ("Solo Alertas", "Todas las Alertas"):
            mascaras.append(df_filtrado["nivel_alerta"].isin(["danger", "warning"]))
        elif f == "Todos":
            mascaras.append(pd.Series(True, index=df_filtrado.index))

    if mascaras:
        mask_final = mascaras[0]
        for m in mascaras[1:]:
            mask_final = mask_final | m
        df_vista_final = df_filtrado[mask_final]
    else:
        df_vista_final = df_filtrado[df_filtrado["sys_pres"] != "Offline"]

    # Ordenamiento de tabla
    if orden_piso == "Más Chats WhatsApp Activos":
        df_vista_final = df_vista_final.sort_values(by="chats_wsp", ascending=False)
    elif orden_piso == "Mayor AHT Hoy (seg)":
        df_vista_final = df_vista_final.sort_values(by="aht_seg", ascending=False, na_position="last")
    elif orden_piso == "Más Interacciones Hoy":
        df_vista_final = df_vista_final.sort_values(by="atendidas_hoy", ascending=False)
    elif orden_piso == "Llamada más larga primero":
        df_vista_final = df_vista_final.sort_values(by="dur_llamada_seg", ascending=False)
    elif orden_piso == "Tiempo en Estado (mayor a menor)":
        df_vista_final = df_vista_final.sort_values(by="dur_seg", ascending=False)
    elif orden_piso == "Nombre del Asesor":
        df_vista_final = df_vista_final.sort_values(by="agente")
    else:
        peso_alerta = {"danger": 3, "warning": 2, "offline": 1, "ok": 0}
        df_vista_final["_peso"] = df_vista_final["nivel_alerta"].map(peso_alerta).fillna(0)
        df_vista_final = df_vista_final.sort_values(by=["_peso", "dur_seg"], ascending=[False, False]).drop(columns=["_peso"])

    st.caption(f"Mostrando {len(df_vista_final)} asesores de {len(df_live)} totales")

    def estilo_estado(val):
        if val in ("Available", "On Queue"):
            return "background-color: #1baf7a22; color: #1baf7a; font-weight: 700;"
        elif val in ("Break", "Baño", "Descanso"):
            return "background-color: #eda10022; color: #b87b00; font-weight: 700;"
        elif val == "Lunch":
            return "background-color: #e24b4a22; color: #e24b4a; font-weight: 700;"
        elif val == "Offline":
            return "color: gray;"
        else:
            return "background-color: #378add22; color: #185fa5; font-weight: 600;"

    def estilo_alerta_col(val):
        if str(val).startswith("💬"):
            return "background-color: #ecfdf5; color: #047857; font-weight: 700;"
        elif str(val).startswith("📞"):
            return "background-color: #fff4eb; color: #c2410c; font-weight: 700;"
        elif str(val).startswith("🚨"):
            return "background-color: #fee8e7; color: #b3261e; font-weight: 700;"
        elif str(val).startswith("⚠️"):
            return "background-color: #fef7e0; color: #b07000; font-weight: 700;"
        elif val == "Desconectado":
            return "color: #999;"
        return "color: #1baf7a; font-weight: 600;"

    def estilo_routing(val):
        if val == "INTERACTING":
            return "background-color: #e8f0fe; color: #185fa5; font-weight: 700;"
        elif val == "IDLE":
            return "background-color: #e6f4ea; color: #137333; font-weight: 600;"
        elif val == "NOT_RESPONDING":
            return "background-color: #fce8e6; color: #c5221f; font-weight: 700;"
        return "color: gray;"

    def estilo_chats_wsp(val):
        if str(val) == "—" or not val:
            return "color: #bbb;"
        return "background-color: #ecfdf5; color: #047857; font-weight: 700;"

    def estilo_llamada(val):
        if str(val) == "—" or not val:
            return "color: #aaa;"
        return "color: #185fa5; font-weight: 700;"

    def estilo_aht(val):
        if pd.isna(val) or val is None or val == 0 or str(val) == "—" or not val:
            return "color: #aaa;"
        try:
            s_val = float(val)
            if s_val >= 1200:
                return "background-color: #fee8e7; color: #b3261e; font-weight: 700;"
            elif s_val >= 900:
                return "background-color: #fef7e0; color: #b07000; font-weight: 700;"
        except Exception:
            pass
        return "color: #185fa5; font-weight: 700;"

    df_vista_final = df_vista_final.copy()
    df_vista_final["chats_wsp_disp"] = df_vista_final["chats_wsp"].apply(lambda c: f"{c} 💬" if c > 0 else "—")

    tabla_vista = df_vista_final[[
        "agente", "servicio", "supervisor", "coordinador",
        "estado", "routing", "chats_wsp_disp", "cronometro_llamada", "atendidas_hoy", "aht_seg",
        "hora_inicio", "cronometro", "alerta"
    ]].rename(columns={
        "agente": "Asesor",
        "servicio": "Servicio",
        "supervisor": "Supervisor",
        "coordinador": "Coordinador",
        "estado": "Estado Actual",
        "routing": "Estado ACD",
        "chats_wsp_disp": "Chats WSP",
        "cronometro_llamada": "Tiempo Llamada",
        "atendidas_hoy": "Interacciones Hoy",
        "aht_seg": "AHT Hoy (seg)",
        "hora_inicio": "Inicio Estado",
        "cronometro": "Tiempo en Estado",
        "alerta": "Alerta en Vivo",
    })

    styler_live = (
        tabla_vista.style
        .map(estilo_estado, subset=["Estado Actual"])
        .map(estilo_routing, subset=["Estado ACD"])
        .map(estilo_chats_wsp, subset=["Chats WSP"])
        .map(estilo_llamada, subset=["Tiempo Llamada"])
        .map(estilo_aht, subset=["AHT Hoy (seg)"])
        .map(estilo_alerta_col, subset=["Alerta en Vivo"])
    )

    st.dataframe(
        styler_live,
        width="stretch",
        hide_index=True,
        column_config={
            "Asesor": st.column_config.TextColumn("Asesor"),
            "Servicio": st.column_config.TextColumn("Servicio"),
            "Supervisor": st.column_config.TextColumn("Supervisor"),
            "Coordinador": st.column_config.TextColumn("Coordinador"),
            "Estado Actual": st.column_config.TextColumn("Estado Actual"),
            "Estado ACD": st.column_config.TextColumn("Estado ACD"),
            "Tiempo Llamada": st.column_config.TextColumn("Tiempo Llamada"),
            "Interacciones Hoy": st.column_config.NumberColumn("Interacciones Hoy", format="%d", help="Total de interacciones atendidas y finalizadas hoy"),
            "AHT Hoy (seg)": st.column_config.NumberColumn("AHT Hoy (seg)", format="%d", help="Tiempo Promedio de Operación (Handle Time) en segundos en lo que va del día"),
            "Inicio Estado": st.column_config.TextColumn("Inicio Estado"),
            "Tiempo en Estado": st.column_config.TextColumn("Tiempo en Estado"),
            "Alerta en Vivo": st.column_config.TextColumn("Alerta en Vivo"),
        },
    )
