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

                    dur_seg = 0
                    hora_inicio_str = "—"
                    if mod_date_str:
                        dt_mod = datetime.fromisoformat(mod_date_str.replace("Z", "+00:00"))
                        dur_seg = max(0, int((now_utc - dt_mod).total_seconds()))
                        dt_col = dt_mod.astimezone(COLOMBIA_TZ)
                        hora_inicio_str = dt_col.strftime("%H:%M:%S")

                    # Cronómetro formateado
                    h = dur_seg // 3600
                    m = (dur_seg % 3600) // 60
                    s = dur_seg % 60
                    cronometro = f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"

                    label = p_info["label"]
                    sys_pres = p_info["systemPresence"]
                    min_dur = dur_seg / 60.0

                    # Alertas en tiempo real
                    alerta = "Normal"
                    nivel_alerta = "ok"

                    if sys_pres == "Offline":
                        alerta = "Desconectado"
                        nivel_alerta = "offline"
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

    conectados = df_live[df_live["sys_pres"] != "Offline"]
    en_llamada = df_live[df_live["routing"] == "INTERACTING"]
    disponibles = df_live[(df_live["estado"].isin(["Available", "On Queue"])) & (df_live["routing"] == "IDLE")]
    en_pausas_regla = df_live[df_live["estado"].isin(["Break", "Baño", "Descanso", "Pre Pausa", "Lunch", "CDR"])]
    en_gestion = df_live[
        (~df_live["estado"].isin(ESTADOS_SISTEMA))
        & (~df_live["estado"].isin(["Break", "Baño", "Descanso", "Pre Pausa", "Lunch", "CDR"]))
    ]
    alertas = df_live[df_live["nivel_alerta"].isin(["danger", "warning"])]

    k1, k2, k3, k4, k5, k6 = st.columns(6)

    def render_kpi(col, titulo, valor, subtitulo, color):
        with col:
            st.markdown(
                f"""
                <div style="background:#f8f9fa; border-radius:8px; padding:12px 14px; border-left:4px solid {color}; margin-bottom:12px;">
                    <p style="color:#666; font-size:12px; margin:0;">{titulo}</p>
                    <p style="color:{color}; font-size:24px; font-weight:700; margin:2px 0;">{valor}</p>
                    <p style="color:#888; font-size:11px; margin:0;">{subtitulo}</p>
                </div>
                """,
                unsafe_allow_html=True,
            )

    pct_con = (len(conectados) / len(df_live) * 100.0) if len(df_live) > 0 else 0.0
    render_kpi(k1, "Conectados", len(conectados), f"{pct_con:.0f}% del total", "#1baf7a")
    render_kpi(k2, "En Interacción", len(en_llamada), "Llamada o chat activo", "#185FA5")
    render_kpi(k3, "En Cola Disponibles", len(disponibles), "Esperando contacto", "#0F825C")
    render_kpi(k4, "En Pausas de Ley", len(en_pausas_regla), "Break, Baño, Pre Pausa", "#BA7517")
    render_kpi(k5, "En Gestión / BO", len(en_gestion), "Backoffice, Autogestión", "#6347A6")
    render_kpi(k6, "Alertas de Exceso", len(alertas), "Pausas excedidas ahora", "#E24B4A" if len(alertas) > 0 else "#888")

    # Banner de Alertas en Tiempo Real
    if not alertas.empty:
        items_alertas = []
        for _, r in alertas.iterrows():
            nombre_agente = str(r["agente"] or "").split(" - ")[-1]
            items_alertas.append(f"<b>{nombre_agente}</b>: {r['alerta']} (Lleva {r['cronometro']})")
        
        st.markdown(
            f"""
            <div style="background:#fee8e7; border:1px solid #f9c0bc; border-radius:8px; padding:12px 16px; margin-bottom:16px;">
                <b style="color:#b3261e; font-size:15px;">🚨 {len(alertas)} Pausa(s) Excedida(s) en este Instante:</b>
                <div style="margin-top:6px; color:#5c1d1a; font-size:13px;">
                    {" &nbsp;·&nbsp; ".join(items_alertas)}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # Filtros de Piso
    st.markdown("##### Filtros de Piso")
    f_col1, f_col2, f_col3, f_col4, f_col5 = st.columns([1.2, 1.2, 1.2, 1.2, 1.5])

    with f_col1:
        vista_rapida = st.selectbox(
            "Foco de Vista",
            options=["Solo Conectados", "Todos", "Solo Pausas con Meta", "Solo Gestión", "Solo Alertas"],
            index=0,
            key="live_vista_rapida",
        )

    coords_disp = sorted([c for c in df_live["coordinador"].unique() if c and pd.notna(c)])
    with f_col2:
        coord_sel = st.multiselect("Coordinador", options=coords_disp, placeholder="Todos", key="live_coord_sel")

    df_filtrado = df_live.copy()
    if coord_sel:
        df_filtrado = df_filtrado[df_filtrado["coordinador"].isin(coord_sel)]

    servicios_disp = sorted([s for s in df_filtrado["servicio"].unique() if s and pd.notna(s)])
    with f_col3:
        serv_sel = st.multiselect("Servicio", options=servicios_disp, placeholder="Todos", key="live_serv_sel")

    if serv_sel:
        df_filtrado = df_filtrado[df_filtrado["servicio"].isin(serv_sel)]

    supervs_disp = sorted([sp for sp in df_filtrado["supervisor"].unique() if sp and pd.notna(sp)])
    with f_col4:
        superv_sel = st.multiselect("Supervisor", options=supervs_disp, placeholder="Todos", key="live_superv_sel")

    if superv_sel:
        df_filtrado = df_filtrado[df_filtrado["supervisor"].isin(superv_sel)]

    with f_col5:
        buscar_agente = st.text_input("Buscar por Asesor o BP", placeholder="Ej: 4512348...", key="live_buscar_agente")

    if buscar_agente:
        df_filtrado = df_filtrado[df_filtrado["agente"].str.contains(buscar_agente, case=False, na=False)]

    if vista_rapida == "Solo Conectados":
        df_filtrado = df_filtrado[df_filtrado["sys_pres"] != "Offline"]
    elif vista_rapida == "Solo Pausas con Meta":
        df_filtrado = df_filtrado[df_filtrado["estado"].isin(["Break", "Baño", "Descanso", "Pre Pausa", "Lunch", "CDR"])]
    elif vista_rapida == "Solo Gestión":
        df_filtrado = df_filtrado[
            (~df_filtrado["estado"].isin(ESTADOS_SISTEMA))
            & (~df_filtrado["estado"].isin(["Break", "Baño", "Descanso", "Pre Pausa", "Lunch", "CDR"]))
        ]
    elif vista_rapida == "Solo Alertas":
        df_filtrado = df_filtrado[df_filtrado["nivel_alerta"].isin(["danger", "warning"])]

    # Ordenamiento seguro
    df_filtrado["_sort_alerta"] = df_filtrado["nivel_alerta"].map({"danger": 3, "warning": 2, "ok": 1, "offline": 0}).fillna(0)
    df_filtrado = df_filtrado.sort_values(by=["_sort_alerta", "dur_seg"], ascending=[False, False]).drop(columns=["_sort_alerta"])

    st.caption(f"Mostrando {len(df_filtrado)} asesores de {len(df_live)} totales")

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
        if str(val).startswith("🚨"):
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

    tabla_vista = df_filtrado[[
        "agente", "servicio", "supervisor", "coordinador",
        "estado", "routing", "hora_inicio", "cronometro", "alerta"
    ]].rename(columns={
        "agente": "Asesor",
        "servicio": "Servicio",
        "supervisor": "Supervisor",
        "coordinador": "Coordinador",
        "estado": "Estado Actual",
        "routing": "Estado ACD",
        "hora_inicio": "Inicio Estado",
        "cronometro": "Tiempo en Estado",
        "alerta": "Alerta en Vivo",
    })

    styler_live = (
        tabla_vista.style
        .map(estilo_estado, subset=["Estado Actual"])
        .map(estilo_routing, subset=["Estado ACD"])
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
            "Inicio Estado": st.column_config.TextColumn("Inicio Estado"),
            "Tiempo en Estado": st.column_config.TextColumn("Tiempo en Estado"),
            "Alerta en Vivo": st.column_config.TextColumn("Alerta en Vivo"),
        },
    )
