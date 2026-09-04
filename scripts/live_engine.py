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
                    elif routing == "INTERACTING":
                        alerta = f"En llamada ({cronometro_llamada})"
                        nivel_alerta = "ok"

                    filas.append({
                        "agente": meta_agente["agente"],
                        "coordinador": meta_agente["coordinador"],
                        "servicio": meta_agente["servicio"],
                        "supervisor": meta_agente["jefe_inmediato"],
                        "estado": label,
                        "sys_pres": sys_pres,
                        "routing": routing,
                        "hora_inicio": hora_inicio_str,
                        "cronometro": cronometro,
                        "dur_min": round(min_dur, 1),
                        "dur_seg": dur_seg,
                        "dur_llamada_min": round(dur_llamada_min, 1),
                        "dur_llamada_seg": dur_llamada_seg,
                        "cronometro_llamada": cronometro_llamada,
                        "alerta": alerta,
                        "nivel_alerta": nivel_alerta,
                    })

        return pd.DataFrame(filas)
    except Exception as err:
        st.error(f"Error inesperado consultando Genesys: {err}")
        return pd.DataFrame()


@st.fragment(run_every=30)
def render_tab_en_vivo(agentes_map: dict):
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

    # Encabezado
    col_t, col_btn = st.columns([4, 1.2])
    with col_t:
        st.subheader("🔴 Monitoreo de Piso y Estados en Vivo")
        st.caption("Auto-actualización cada 30 segundos con cronómetros en tiempo real")

    with col_btn:
        st.write("")
        if st.button("🔄 Actualizar Ahora", key="btn_refrescar_live", width="stretch"):
            st.rerun(scope="fragment")

    t0 = time.time()
    df_live = obtener_presencia_en_vivo(token, agentes_map, catalog)
    t_descarga = time.time() - t0

    if df_live.empty:
        st.info("Sin datos recibidos de Genesys Cloud en este momento.")
        return

    hora_actual = datetime.now(COLOMBIA_TZ).strftime("%I:%M:%S %p")
    st.markdown(
        f"<p style='color:gray; font-size:13px;'>Última sincronización: <b>{hora_actual}</b> (completada en {t_descarga:.1f}s) · {len(df_live)} asesores monitoreados</p>",
        unsafe_allow_html=True,
    )

    # ── 1. Filtros de Piso (Afectan a las tarjetas, al cuadro de alertas y a la tabla) ──
    st.markdown("##### Filtros de Piso")
    f_col1, f_col2, f_col3, f_col4, f_col5 = st.columns([1.2, 1.2, 1.2, 1.3, 1.1])

    coords_disp = sorted([c for c in df_live["coordinador"].unique() if c and pd.notna(c)])
    with f_col1:
        coord_sel = st.multiselect("Coordinador", options=coords_disp, placeholder="Todos", key="live_coord_sel")

    df_filtrado = df_live.copy()
    if coord_sel:
        df_filtrado = df_filtrado[df_filtrado["coordinador"].isin(coord_sel)]

    servicios_disp = sorted([s for s in df_filtrado["servicio"].unique() if s and pd.notna(s)])
    with f_col2:
        serv_sel = st.multiselect("Servicio", options=servicios_disp, placeholder="Todos", key="live_serv_sel")

    if serv_sel:
        df_filtrado = df_filtrado[df_filtrado["servicio"].isin(serv_sel)]

    supervs_disp = sorted([sp for sp in df_filtrado["supervisor"].unique() if sp and pd.notna(sp)])
    with f_col3:
        superv_sel = st.multiselect("Supervisor", options=supervs_disp, placeholder="Todos", key="live_superv_sel")

    if superv_sel:
        df_filtrado = df_filtrado[df_filtrado["supervisor"].isin(superv_sel)]

    with f_col4:
        buscar_agente = st.text_input("Buscar por Asesor o BP", placeholder="Ej: 4512348...", key="live_buscar_agente")

    if buscar_agente:
        df_filtrado = df_filtrado[df_filtrado["agente"].str.contains(buscar_agente, case=False, na=False)]

    with f_col5:
        umbral_llamada = st.number_input(
            "Alerta Llamada >",
            min_value=5,
            max_value=120,
            value=15,
            step=5,
            key="live_umbral_llamada",
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
    llamadas_largas = en_llamada[en_llamada["dur_llamada_min"] >= umbral_llamada]
    disponibles = df_filtrado[(df_filtrado["estado"].isin(["Available", "On Queue"])) & (df_filtrado["routing"] == "IDLE")]
    en_pausas_regla = df_filtrado[df_filtrado["estado"].isin(["Break", "Baño", "Descanso", "Pre Pausa", "Lunch", "CDR"])]
    en_gestion = df_filtrado[
        (~df_filtrado["estado"].isin(ESTADOS_SISTEMA))
        & (~df_filtrado["estado"].isin(["Break", "Baño", "Descanso", "Pre Pausa", "Lunch", "CDR"]))
    ]
    alertas = df_filtrado[df_filtrado["nivel_alerta"].isin(["danger", "warning"])]

    k1, k2, k3, k4, k5, k6 = st.columns(6)

    foco_actual = st.session_state.get("live_vista_rapida", "Solo Conectados")

    def render_kpi_interactivo(col, titulo, valor, subtitulo, color, foco_asociado):
        es_activo = (foco_actual == foco_asociado)
        borde_k = f"border: 2px solid {color}; box-shadow: 0 0 10px {color}44; background: #fff;" if es_activo else f"border-left: 4px solid {color}; background: #f8f9fa;"
        tag_k = "<span style='float:right; font-size:10px; background:#185fa5; color:#fff; padding:1px 6px; border-radius:8px;'>✓ Filtrando</span>" if es_activo else ""

        with col:
            st.markdown(
                f"""
                <div style="{borde_k} border-radius:8px; padding:10px 12px; margin-bottom:4px; transition: all 0.2s;">
                    <p style="color:#666; font-size:12px; margin:0;">{titulo} {tag_k}</p>
                    <p style="color:{color}; font-size:24px; font-weight:700; margin:2px 0;">{valor}</p>
                    <p style="color:#888; font-size:11px; margin:0;">{subtitulo}</p>
                </div>
                """,
                unsafe_allow_html=True,
            )
            btn_txt = "✖ Quitar" if es_activo else "🔍 Filtrar"
            if st.button(btn_txt, key=f"btn_live_card_{foco_asociado}", width="stretch", type="primary" if es_activo else "secondary"):
                if es_activo:
                    st.session_state["live_vista_rapida"] = "Todos"
                else:
                    st.session_state["live_vista_rapida"] = foco_asociado
                st.rerun(scope="fragment")

    pct_con = (len(conectados) / len(df_filtrado) * 100.0) if len(df_filtrado) > 0 else 0.0
    sub_llamada = f"🚨 {len(llamadas_largas)} > {umbral_llamada} min" if len(llamadas_largas) > 0 else "Duración normal"
    color_llamada = "#E24B4A" if len(llamadas_largas) > 0 else "#185FA5"

    render_kpi_interactivo(k1, "Conectados", len(conectados), f"{pct_con:.0f}% del filtro", "#1baf7a", "Solo Conectados")
    render_kpi_interactivo(k2, "En Interacción", len(en_llamada), sub_llamada, color_llamada, "Solo Llamadas Activas")
    render_kpi_interactivo(k3, "En Cola Disponibles", len(disponibles), "Esperando contacto", "#0F825C", "Solo En Cola")
    render_kpi_interactivo(k4, "En Pausas de Ley", len(en_pausas_regla), "Break, Baño, Pre Pausa", "#BA7517", "Solo Pausas con Meta")
    render_kpi_interactivo(k5, "En Gestión / BO", len(en_gestion), "Backoffice, Autogestión", "#6347A6", "Solo Gestión")
    render_kpi_interactivo(k6, "Alertas de Exceso", len(alertas), f"Pausas y llamadas > {umbral_llamada}m", "#E24B4A" if len(alertas) > 0 else "#888", "Solo Alertas")

    # ── 3. Cuadro de Alertas en Tiempo Real (RESPONDE A LOS FILTROS) ──────
    if not alertas.empty:
        items_alertas = []
        for _, r in alertas.iterrows():
            nombre_agente = str(r["agente"] or "").split(" - ")[-1]
            serv_tag = f" <span style='color:#777;'>({r['servicio']})</span>" if not serv_sel else ""
            items_alertas.append(f"<b>{nombre_agente}</b>{serv_tag}: {r['alerta']}")

        filtro_txt = " (en tu selección actual)" if (coord_sel or serv_sel or superv_sel or buscar_agente) else ""
        st.markdown(
            f"""
            <div style="background:#fee8e7; border:1px solid #f9c0bc; border-radius:8px; padding:12px 16px; margin-bottom:16px;">
                <b style="color:#b3261e; font-size:15px;">🚨 {len(alertas)} Alerta(s) Activa(s) en este Instante{filtro_txt}:</b>
                <div style="margin-top:6px; color:#5c1d1a; font-size:13px; line-height:1.7;">
                    {" &nbsp;·&nbsp; ".join(items_alertas)}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # ── 4. Controles de Visualización de Tabla ───────────────────────────
    c_foco, c_orden = st.columns([1.5, 1.5])
    with c_foco:
        vista_rapida = st.selectbox(
            "Foco de Vista",
            options=[
                "Solo Conectados",
                "Todos",
                "Solo Llamadas Activas",
                "Solo Llamadas Prolongadas",
                "Solo En Cola",
                "Solo Pausas con Meta",
                "Solo Gestión",
                "Solo Alertas",
            ],
            index=0,
            key="live_vista_rapida",
        )
    with c_orden:
        orden_piso = st.selectbox(
            "Ordenar por",
            options=[
                "Nivel de Alerta y Duración",
                "Llamada más larga primero",
                "Tiempo en Estado (mayor a menor)",
                "Nombre del Asesor",
            ],
            index=0,
            key="live_orden_piso",
        )

    if vista_rapida == "Solo Conectados":
        df_vista_final = df_filtrado[df_filtrado["sys_pres"] != "Offline"]
    elif vista_rapida == "Solo Llamadas Activas":
        df_vista_final = df_filtrado[(df_filtrado["sys_pres"] != "Offline") & (df_filtrado["routing"] == "INTERACTING")]
    elif vista_rapida == "Solo Llamadas Prolongadas":
        df_vista_final = df_filtrado[
            (df_filtrado["sys_pres"] != "Offline")
            & (df_filtrado["routing"] == "INTERACTING")
            & (df_filtrado["dur_llamada_min"] >= umbral_llamada)
        ]
    elif vista_rapida == "Solo En Cola":
        df_vista_final = df_filtrado[(df_filtrado["estado"].isin(["Available", "On Queue"])) & (df_filtrado["routing"] == "IDLE")]
    elif vista_rapida == "Solo Pausas con Meta":
        df_vista_final = df_filtrado[df_filtrado["estado"].isin(["Break", "Baño", "Descanso", "Pre Pausa", "Lunch", "CDR"])]
    elif vista_rapida == "Solo Gestión":
        df_vista_final = df_filtrado[
            (~df_filtrado["estado"].isin(ESTADOS_SISTEMA))
            & (~df_filtrado["estado"].isin(["Break", "Baño", "Descanso", "Pre Pausa", "Lunch", "CDR"]))
        ]
    elif vista_rapida == "Solo Alertas":
        df_vista_final = df_filtrado[df_filtrado["nivel_alerta"].isin(["danger", "warning"])]
    else:
        df_vista_final = df_filtrado.copy()

    # Ordenamiento de tabla
    if orden_piso == "Llamada más larga primero":
        df_vista_final = df_vista_final.sort_values(by="dur_llamada_seg", ascending=False)
    elif orden_piso == "Tiempo en Estado (mayor a menor)":
        df_vista_final = df_vista_final.sort_values(by="dur_seg", ascending=False)
    elif orden_piso == "Nombre del Asesor":
        df_vista_final = df_vista_final.sort_values(by="agente", ascending=True)
    else:
        df_vista_final["_sort_alerta"] = df_vista_final["nivel_alerta"].map({"danger": 3, "warning": 2, "ok": 1, "offline": 0}).fillna(0)
        df_vista_final = df_vista_final.sort_values(
            by=["_sort_alerta", "dur_llamada_seg", "dur_seg"], ascending=[False, False, False]
        ).drop(columns=["_sort_alerta"])

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
        if str(val).startswith("🚨") or str(val).startswith("📞"):
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

    def estilo_llamada(val):
        if str(val) == "—" or not val:
            return "color: #aaa;"
        return "color: #185fa5; font-weight: 700;"

    tabla_vista = df_vista_final[[
        "agente", "servicio", "supervisor", "coordinador",
        "estado", "routing", "cronometro_llamada", "hora_inicio", "cronometro", "alerta"
    ]].rename(columns={
        "agente": "Asesor",
        "servicio": "Servicio",
        "supervisor": "Supervisor",
        "coordinador": "Coordinador",
        "estado": "Estado Actual",
        "routing": "Estado ACD",
        "cronometro_llamada": "Tiempo Llamada",
        "hora_inicio": "Inicio Estado",
        "cronometro": "Tiempo en Estado",
        "alerta": "Alerta en Vivo",
    })

    styler_live = (
        tabla_vista.style
        .map(estilo_estado, subset=["Estado Actual"])
        .map(estilo_routing, subset=["Estado ACD"])
        .map(estilo_llamada, subset=["Tiempo Llamada"])
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
            "Inicio Estado": st.column_config.TextColumn("Inicio Estado"),
            "Tiempo en Estado": st.column_config.TextColumn("Tiempo en Estado"),
            "Alerta en Vivo": st.column_config.TextColumn("Alerta en Vivo"),
        },
    )
