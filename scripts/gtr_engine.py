"""
Motor GTR y Niveles de Servicio — Radar Genesys Cloud.
Calcula métricas de Service Level (NS), AHT, Abandono y ASA
con la estructura exacta del reporte oficial HORA A HORA (DETALLE) y AHT GENESYS.
"""

from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import time

import numpy as np
import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

from live_engine import obtener_token_genesys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_GTR_PATH = os.path.join(BASE_DIR, "gtr_config.json")

# Orden oficial de columnas de servicios según HORA A HORA (DETALLE)
SERVICIOS_ORDEN_OFICIAL = [
    "TT_LATAM",
    "LUA AMC",
    "Soporte LUA AMC",
    "WPP LUA AMC",
    "HVC AMC",
    "Ventas AMC",
    "WPP Ventas AMC",
    "CHAT Ventas AMC",
    "TRAVEL WP AMC",
    "LUA AMC ING",
    "DREAM TEAMS VOZ",
    "DREAM TEAMS ENG",
    "CHAT DREAM TEAMS ES",
    "DREAM TEAMS WA",
    "TT_EQUIPAJES",
    "Equipajes AMC",
    "Equipajes AMC ING",
    "WPP EQUIPAJES AMC"
]

SERVICIOS_TT_LATAM = [
    "LUA AMC", "Soporte LUA AMC", "WPP LUA AMC", "HVC AMC",
    "Ventas AMC", "WPP Ventas AMC", "CHAT Ventas AMC", "TRAVEL WP AMC",
    "LUA AMC ING", "DREAM TEAMS VOZ", "DREAM TEAMS ENG", "CHAT DREAM TEAMS ES", "DREAM TEAMS WA"
]

SERVICIOS_TT_EQUIPAJES = [
    "Equipajes AMC", "Equipajes AMC ING", "WPP EQUIPAJES AMC"
]


def cargar_config_gtr():
    """Carga mapeo de colas y metas de NS y AHT."""
    if not os.path.exists(CONFIG_GTR_PATH):
        return {"queues": {}, "services": {}, "aht_metas": {}}
    with open(CONFIG_GTR_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def formatear_segundos_mm_ss(segundos):
    """Convierte segundos a formato MM:SS."""
    if pd.isna(segundos) or segundos is None or segundos < 0:
        return "-"
    m = int(segundos // 60)
    s = int(segundos % 60)
    return f"{m:02d}:{s:02d}"


@st.cache_data(ttl=60, show_spinner=False)
def obtener_metricas_gtr_api(token: str):
    """
    Consulta Genesys Cloud Analytics Conversation Aggregates para el día actual
    en bloques de 30 minutos (PT30M).
    """
    gtr_cfg = cargar_config_gtr()
    queues_cfg = gtr_cfg.get("queues", {})
    services_cfg = gtr_cfg.get("services", {})

    now_utc = datetime.now(timezone.utc)
    # 00:00 hora Colombia = 05:00 UTC
    today_col_start = now_utc.replace(hour=5, minute=0, second=0, microsecond=0)
    if now_utc < today_col_start:
        today_col_start -= timedelta(days=1)

    s_start = today_col_start.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    s_end = now_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    interval = f"{s_start}/{s_end}"

    body = {
        "interval": interval,
        "granularity": "PT30M",
        "groupBy": ["queueId"],
        "metrics": ["nOffered", "tAnswered", "tAbandon", "tHandle", "oServiceLevel"]
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = "https://api.mypurecloud.com/api/v2/analytics/conversations/aggregates/query"

    try:
        r = requests.post(url, headers=headers, json=body, timeout=25)
        if r.status_code != 200:
            return pd.DataFrame(), f"Error API Genesys: {r.status_code} - {r.text[:200]}"
        res = r.json()
    except Exception as e:
        return pd.DataFrame(), f"Error de conexión con Genesys: {str(e)}"

    records = []
    for group in res.get("results", []):
        qid = group.get("group", {}).get("queueId")
        q_info = queues_cfg.get(qid, {})
        srv = q_info.get("servicio", "Otras Colas / No Mapeado")
        q_name = q_info.get("nombre_cola", qid or "Directo / Sin Cola")
        canal = services_cfg.get(srv, {}).get("canal", "VOZ" if "WPP" not in srv and "CHAT" not in srv else "DIGITAL")

        for interval_data in group.get("data", []):
            int_str = interval_data.get("interval", "")
            start_utc = int_str.split("/")[0] if "/" in int_str else int_str
            dt_col = pd.to_datetime(start_utc) - pd.Timedelta(hours=5)
            int_label = dt_col.strftime("%H:%M")

            row = {
                "queueId": qid,
                "nombre_cola": q_name,
                "servicio": srv,
                "canal": canal,
                "intervalo": int_label,
                "intervalo_dt": dt_col,
                "nOffered": 0,
                "tAnswered_count": 0,
                "tAnswered_sum": 0.0,
                "tAbandon_count": 0,
                "tAbandon_sum": 0.0,
                "tHandle_count": 0,
                "tHandle_sum": 0.0,
                "sl_numerator": 0,
                "sl_denominator": 0
            }
            for metric in interval_data.get("metrics", []):
                m_name = metric.get("metric")
                stats = metric.get("stats", {})
                if m_name == "nOffered":
                    row["nOffered"] = stats.get("count", 0)
                elif m_name == "tAnswered":
                    row["tAnswered_count"] = stats.get("count", 0)
                    row["tAnswered_sum"] = stats.get("sum", 0.0)
                elif m_name == "tAbandon":
                    row["tAbandon_count"] = stats.get("count", 0)
                    row["tAbandon_sum"] = stats.get("sum", 0.0)
                elif m_name == "tHandle":
                    row["tHandle_count"] = stats.get("count", 0)
                    row["tHandle_sum"] = stats.get("sum", 0.0)
                elif m_name == "oServiceLevel":
                    ratio = stats.get("ratio", 0.0)
                    denom = stats.get("denominator", 0)
                    row["sl_denominator"] = denom
                    row["sl_numerator"] = stats.get("numerator", int(round(ratio * denom)))
            records.append(row)

    return pd.DataFrame(records), None


@st.cache_data(ttl=60, show_spinner=False)
def obtener_aht_asesores_api(token: str):
    """
    Consulta métricas de manejo por agente (userId) para el día actual.
    """
    now_utc = datetime.now(timezone.utc)
    today_col_start = now_utc.replace(hour=5, minute=0, second=0, microsecond=0)
    if now_utc < today_col_start:
        today_col_start -= timedelta(days=1)

    s_start = today_col_start.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    s_end = now_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    interval = f"{s_start}/{s_end}"

    body = {
        "interval": interval,
        "groupBy": ["userId"],
        "filter": {
            "type": "and",
            "predicates": [
                {"dimension": "direction", "value": "inbound"}
            ]
        },
        "metrics": ["tHandle", "tTalk", "tHeld", "tAcw", "nTransferred"]
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = "https://api.mypurecloud.com/api/v2/analytics/conversations/aggregates/query"

    try:
        r = requests.post(url, headers=headers, json=body, timeout=25)
        if r.status_code != 200:
            return pd.DataFrame(), f"Error AHT Asesores: {r.status_code}"
        res = r.json()
    except Exception as e:
        return pd.DataFrame(), f"Error conexión AHT: {str(e)}"

    rows = []
    for item in res.get("results", []):
        uid = item.get("group", {}).get("userId")
        t_handle_count = 0
        t_handle_sum = 0.0
        t_talk_sum = 0.0
        t_held_sum = 0.0
        t_acw_sum = 0.0
        transf_count = 0

        for interval_data in item.get("data", []):
            for m in interval_data.get("metrics", []):
                m_name = m.get("metric")
                st_data = m.get("stats", {})
                if m_name == "tHandle":
                    t_handle_count += st_data.get("count", 0)
                    t_handle_sum += st_data.get("sum", 0.0)
                elif m_name == "tTalk":
                    t_talk_sum += st_data.get("sum", 0.0)
                elif m_name == "tHeld":
                    t_held_sum += st_data.get("sum", 0.0)
                elif m_name == "tAcw":
                    t_acw_sum += st_data.get("sum", 0.0)
                elif m_name == "nTransferred":
                    transf_count += st_data.get("count", 0)

        if t_handle_count > 0:
            aht_s = round(t_handle_sum / t_handle_count / 1000, 1)
            rows.append({
                "agente_id": uid,
                "interacciones": t_handle_count,
                "aht_seg": aht_s,
                "t_talk_seg": round(t_talk_sum / t_handle_count / 1000, 1),
                "t_held_seg": round(t_held_sum / t_handle_count / 1000, 1),
                "t_acw_seg": round(t_acw_sum / t_handle_count / 1000, 1),
                "transferidas": transf_count
            })

    return pd.DataFrame(rows), None


def construir_matriz_ejecutiva_gtr(df_raw: pd.DataFrame, gtr_cfg: dict):
    """
    Construye la matriz horizontal exacta de HORA A HORA (DETALLE filas 7 a 23).
    Métricas en filas, Macro-Servicios en columnas.
    """
    services_cfg = gtr_cfg.get("services", {})
    aht_metas = gtr_cfg.get("aht_metas", {})

    serv_data = {}
    todos_servicios = set(df_raw["servicio"].unique()).union(SERVICIOS_ORDEN_OFICIAL)

    for s in todos_servicios:
        if s in ("TT_LATAM", "TT_EQUIPAJES"):
            continue
        sub = df_raw[df_raw["servicio"] == s]
        off = sub["nOffered"].sum()
        ans = sub["tAnswered_count"].sum()
        abn = sub["tAbandon_count"].sum()
        sl_n = sub["sl_numerator"].sum()
        sl_d = sub["sl_denominator"].sum()
        h_s = sub["tHandle_sum"].sum()
        h_c = sub["tHandle_count"].sum()
        a_s = sub["tAnswered_sum"].sum()

        meta_ns = services_cfg.get(s, {}).get("ns_meta", None)
        meta_ns_val = meta_ns * 100.0 if meta_ns else None

        serv_data[s] = {
            "LL ENT": off,
            "LL ATEN": ans,
            "LL ABAN": abn,
            "LL Aten. NS": sl_n,
            "% ATEN": (ans / off * 100.0) if off > 0 else 0.0,
            "% ABAN": (abn / off * 100.0) if off > 0 else 0.0,
            "% NS META": meta_ns_val,
            "% NS": (sl_n / sl_d * 100.0) if sl_d > 0 else 0.0,
            "META AHT": aht_metas.get(s, None),
            "AHT": (h_s / h_c / 1000.0) if h_c > 0 else 0.0,
            "ASA": (a_s / ans / 1000.0) if ans > 0 else 0.0,
        }

    # Calcular Macro Consolidado TT_LATAM y TT_EQUIPAJES
    for tt_name, group_list in [("TT_LATAM", SERVICIOS_TT_LATAM), ("TT_EQUIPAJES", SERVICIOS_TT_EQUIPAJES)]:
        sub = df_raw[df_raw["servicio"].isin(group_list)]
        off = sub["nOffered"].sum()
        ans = sub["tAnswered_count"].sum()
        abn = sub["tAbandon_count"].sum()
        sl_n = sub["sl_numerator"].sum()
        sl_d = sub["sl_denominator"].sum()
        h_s = sub["tHandle_sum"].sum()
        h_c = sub["tHandle_count"].sum()
        a_s = sub["tAnswered_sum"].sum()

        serv_data[tt_name] = {
            "LL ENT": off,
            "LL ATEN": ans,
            "LL ABAN": abn,
            "LL Aten. NS": sl_n,
            "% ATEN": (ans / off * 100.0) if off > 0 else 0.0,
            "% ABAN": (abn / off * 100.0) if off > 0 else 0.0,
            "% NS META": 75.3 if tt_name == "TT_LATAM" else 78.1,
            "% NS": (sl_n / sl_d * 100.0) if sl_d > 0 else 0.0,
            "META AHT": 877.0 if tt_name == "TT_LATAM" else 993.0,
            "AHT": (h_s / h_c / 1000.0) if h_c > 0 else 0.0,
            "ASA": (a_s / ans / 1000.0) if ans > 0 else 0.0,
        }

    filas_metricas = [
        ("LL ENT", "Llamadas Entrantes (Ofrecidas)"),
        ("LL ATEN", "Llamadas Atendidas"),
        ("LL ABAN", "Llamadas Abandonadas"),
        ("LL Aten. NS", "Llamadas Atendidas en NS"),
        ("% ATEN", "% Nivel de Atención"),
        ("% ABAN", "% Nivel de Abandono"),
        ("% NS META", "% NS Meta Contractual"),
        ("% NS", "% NS Real"),
        ("META AHT", "Meta AHT (segundos)"),
        ("AHT", "AHT Real (segundos)"),
        ("% VAR AHT", "% Variación AHT vs Meta"),
        ("ASA", "ASA (Tiempo Espera Segundos)"),
    ]

    # Columnas que efectivamente tienen tráfico o están en el reporte oficial
    cols_servicios = [s for s in SERVICIOS_ORDEN_OFICIAL if s in serv_data]
    # Agregar otras si hubiera tráfico fuera del orden estándar
    for s in sorted(serv_data.keys()):
        if s not in cols_servicios and serv_data[s]["LL ENT"] > 0:
            cols_servicios.append(s)

    filas_tabla = []
    for cod_m, desc_m in filas_metricas:
        row = {"Concepto / Métrica": desc_m}
        for srv in cols_servicios:
            sd = serv_data.get(srv, {})
            if cod_m == "% VAR AHT":
                aht_r = sd.get("AHT", 0)
                aht_m = sd.get("META AHT", None)
                if aht_m and aht_m > 0 and aht_r > 0:
                    row[srv] = f"{(aht_r - aht_m) / aht_m * 100.0:+.1f}%"
                else:
                    row[srv] = "-"
            elif cod_m in ("% ATEN", "% ABAN", "% NS META", "% NS"):
                v = sd.get(cod_m)
                row[srv] = f"{v:.1f}%" if v is not None else "-"
            elif cod_m in ("AHT", "META AHT", "ASA"):
                v = sd.get(cod_m)
                if v is not None and v > 0:
                    row[srv] = f"{int(round(v))}s ({formatear_segundos_mm_ss(v)})"
                else:
                    row[srv] = "-"
            else:
                v = sd.get(cod_m, 0)
                row[srv] = f"{int(v):,}"
        filas_tabla.append(row)

    df_matriz = pd.DataFrame(filas_tabla)
    return df_matriz, serv_data


def render_tab_gtr(agentes_map: dict):
    """Renderiza la pestaña oficial de Monitor GTR y Niveles de Servicio."""
    token = obtener_token_genesys()
    if not token:
        st.warning("⚠️ No se encontró token activo de Genesys Cloud. Conéctalo en Neon Postgres o revisa las credenciales.")
        return

    col_h1, col_h2 = st.columns([4, 1])
    with col_h1:
        st.subheader("📈 Monitor GTR — Reporte Oficial Hora a Hora & AHT")
        st.caption("Replicación oficial de los reportes GTR `(CONFIDENCIAL)HORA_HORA.xlsb` y `AHT GENESYS.xlsm` directamente desde Genesys Cloud Analytics.")
    with col_h2:
        if st.button("🔄 Actualizar Datos GTR", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    gtr_cfg = cargar_config_gtr()

    with st.spinner("Consultando métricas en vivo de Genesys Cloud..."):
        df_raw, err = obtener_metricas_gtr_api(token)

    if err or df_raw.empty:
        st.error(f"No fue posible cargar las métricas de Genesys: {err}")
        return

    # Construir Matriz Ejecutiva Horizontal
    df_matriz, serv_data = construir_matriz_ejecutiva_gtr(df_raw, gtr_cfg)

    # ── KPIs Resumen Global (TT_LATAM + TT_EQUIPAJES) ────────────────────────
    tt_l = serv_data.get("TT_LATAM", {})
    tt_e = serv_data.get("TT_EQUIPAJES", {})

    k1, k2, k3, k4, k5, k6 = st.columns(6)
    with k1:
        st.metric("Total Entrantes", f"{int(tt_l.get('LL ENT', 0) + tt_e.get('LL ENT', 0)):,}")
    with k2:
        st.metric("Total Atendidas", f"{int(tt_l.get('LL ATEN', 0) + tt_e.get('LL ATEN', 0)):,}")
    with k3:
        st.metric("NS TT_LATAM", f"{tt_l.get('% NS', 0):.1f}%", delta=f"Meta {tt_l.get('% NS META', 75):.0f}%")
    with k4:
        st.metric("AHT TT_LATAM", f"{int(tt_l.get('AHT', 0))}s", delta=formatear_segundos_mm_ss(tt_l.get("AHT", 0)))
    with k5:
        st.metric("NS TT_EQUIPAJES", f"{tt_e.get('% NS', 0):.1f}%", delta=f"Meta {tt_e.get('% NS META', 78):.0f}%")
    with k6:
        st.metric("AHT TT_EQUIPAJES", f"{int(tt_e.get('AHT', 0))}s", delta=formatear_segundos_mm_ss(tt_e.get("AHT", 0)))

    st.markdown("---")

    # ── 1. MATRIZ OFICIAL HORA A HORA (DETALLE) ──────────────────────────────
    st.markdown("### 📋 Matriz Ejecutiva Consolidada por Servicio (Formato Oficial HORA A HORA)")
    st.caption("Estructura de columnas por servicio y filas por indicador (LL ENT, LL ATEN, LL ABAN, % ATEN, % NS, META AHT, AHT, ASA).")

    # Controles de vista de la matriz
    opciones_columnas = [c for c in df_matriz.columns if c != "Concepto / Métrica"]
    cols_seleccionadas = st.multiselect(
        "Filtrar columnas de servicios a visualizar:",
        opciones_columnas,
        default=[c for c in opciones_columnas if c in SERVICIOS_ORDEN_OFICIAL[:10] or c in ("TT_EQUIPAJES", "WPP EQUIPAJES AMC")]
    )

    cols_a_mostrar = ["Concepto / Métrica"] + (cols_seleccionadas if cols_seleccionadas else opciones_columnas)
    st.dataframe(
        df_matriz[cols_a_mostrar],
        use_container_width=True,
        hide_index=True
    )

    # ── 2. SELECCIÓN DE SERVICIO Y TABLA INTRADÍA (CORTES 30 MIN) ────────────
    st.markdown("---")
    st.markdown("### ⏱️ Detalle Intradía por Servicio (Intervalos de 30 Minutos y Acumulados)")
    st.caption("Visualiza el corte de cada media hora exactamente como la tabla horaria de la hoja `DETALLE` de GTR.")

    servicios_intradia = [s for s in SERVICIOS_ORDEN_OFICIAL if s in serv_data and serv_data[s]["LL ENT"] > 0]
    col_sel_srv, col_espacio = st.columns([2, 3])
    with col_sel_srv:
        servicio_activo = st.selectbox("Seleccione el Servicio a Analizar:", servicios_intradia, index=1 if len(servicios_intradia) > 1 else 0)

    # Filtrar datos del servicio seleccionado
    if servicio_activo == "TT_LATAM":
        df_srv = df_raw[df_raw["servicio"].isin(SERVICIOS_TT_LATAM)].copy()
    elif servicio_activo == "TT_EQUIPAJES":
        df_srv = df_raw[df_raw["servicio"].isin(SERVICIOS_TT_EQUIPAJES)].copy()
    else:
        df_srv = df_raw[df_raw["servicio"] == servicio_activo].copy()

    # Agrupar por intervalo cronológico
    intra_srv = df_srv.groupby("intervalo").agg({
        "nOffered": "sum",
        "tAnswered_count": "sum",
        "tAbandon_count": "sum",
        "sl_numerator": "sum",
        "sl_denominator": "sum",
        "tHandle_sum": "sum",
        "tHandle_count": "sum",
        "tAnswered_sum": "sum"
    }).reset_index().sort_values("intervalo")

    # Acumulados
    intra_srv["cum_offered"] = intra_srv["nOffered"].cumsum()
    intra_srv["cum_answered"] = intra_srv["tAnswered_count"].cumsum()
    intra_srv["cum_abandon"] = intra_srv["tAbandon_count"].cumsum()
    intra_srv["cum_sl_num"] = intra_srv["sl_numerator"].cumsum()
    intra_srv["cum_sl_den"] = intra_srv["sl_denominator"].cumsum()
    intra_srv["cum_h_sum"] = intra_srv["tHandle_sum"].cumsum()
    intra_srv["cum_h_cnt"] = intra_srv["tHandle_count"].cumsum()
    intra_srv["cum_ans_sum"] = intra_srv["tAnswered_sum"].cumsum()

    # Tasas Intradía del Intervalo
    intra_srv["% Atenc"] = (intra_srv["tAnswered_count"] / intra_srv["nOffered"] * 100.0).fillna(0).round(1)
    intra_srv["% Aband"] = (intra_srv["tAbandon_count"] / intra_srv["nOffered"] * 100.0).fillna(0).round(1)
    intra_srv["% NS"] = (intra_srv["sl_numerator"] / intra_srv["sl_denominator"] * 100.0).fillna(0).round(1)
    intra_srv["AHT (s)"] = (intra_srv["tHandle_sum"] / intra_srv["tHandle_count"] / 1000.0).fillna(0).round(0)
    intra_srv["ASA (s)"] = (intra_srv["tAnswered_sum"] / intra_srv["tAnswered_count"] / 1000.0).fillna(0).round(1)

    # Tasas Acumuladas al Corte
    intra_srv["% NS Acum"] = (intra_srv["cum_sl_num"] / intra_srv["cum_sl_den"] * 100.0).fillna(0).round(1)
    intra_srv["AHT Acum (s)"] = (intra_srv["cum_h_sum"] / intra_srv["cum_h_cnt"] / 1000.0).fillna(0).round(0)
    intra_srv["ASA Acum (s)"] = (intra_srv["cum_ans_sum"] / intra_srv["cum_answered"] / 1000.0).fillna(0).round(1)

    meta_ns_srv = serv_data.get(servicio_activo, {}).get("% NS META", 80.0) or 80.0
    meta_aht_srv = serv_data.get(servicio_activo, {}).get("META AHT", None)

    # Gráficos de Curva Horaria
    tab_g1, tab_g2 = st.tabs(["Curva Intradía: NS vs NS Acumulado", "Curva Intradía: AHT vs Meta"])
    with tab_g1:
        fig_curva_ns = go.Figure()
        fig_curva_ns.add_trace(go.Scatter(
            x=intra_srv["intervalo"], y=intra_srv["% NS"],
            mode="lines+markers", name="% NS del Intervalo", line=dict(color="#378ADD", width=2)
        ))
        fig_curva_ns.add_trace(go.Scatter(
            x=intra_srv["intervalo"], y=intra_srv["% NS Acum"],
            mode="lines+markers", name="% NS Acumulado", line=dict(color="#1baf7a", width=3)
        ))
        fig_curva_ns.add_hline(y=meta_ns_srv, line_dash="dash", line_color="orange", annotation_text=f"Meta: {meta_ns_srv:.0f}%")
        fig_curva_ns.update_layout(title=f"Evolución de Nivel de Servicio — {servicio_activo}", height=380, yaxis_range=[0, 105])
        st.plotly_chart(fig_curva_ns, use_container_width=True)

    with tab_g2:
        fig_curva_aht = go.Figure()
        fig_curva_aht.add_trace(go.Scatter(
            x=intra_srv["intervalo"], y=intra_srv["AHT (s)"],
            mode="lines+markers", name="AHT del Intervalo (s)", line=dict(color="#e24b4a", width=2)
        ))
        fig_curva_aht.add_trace(go.Scatter(
            x=intra_srv["intervalo"], y=intra_srv["AHT Acum (s)"],
            mode="lines+markers", name="AHT Acumulado (s)", line=dict(color="#9b51e0", width=3)
        ))
        if meta_aht_srv:
            fig_curva_aht.add_hline(y=meta_aht_srv, line_dash="dash", line_color="green", annotation_text=f"Meta: {int(meta_aht_srv)}s")
        fig_curva_aht.update_layout(title=f"Evolución de AHT (Segundos) — {servicio_activo}", height=380)
        st.plotly_chart(fig_curva_aht, use_container_width=True)

    # Tabla Horaria idéntica a DETALLE
    cols_tabla_intra = [
        "intervalo", "nOffered", "tAnswered_count", "tAbandon_count", "sl_numerator",
        "% Atenc", "% Aband", "% NS", "% NS Acum", "AHT (s)", "AHT Acum (s)", "ASA (s)"
    ]
    df_presentar_intra = intra_srv[cols_tabla_intra].rename(columns={
        "intervalo": "Hora (Intv)",
        "nOffered": "Llam Ent",
        "tAnswered_count": "Llam Aten",
        "tAbandon_count": "Llam Aban",
        "sl_numerator": "Atend. NS",
        "% Atenc": "% Atenc.",
        "% Aband": "% Aband.",
        "% NS": "% NS",
        "% NS Acum": "% NS Acum.",
        "AHT (s)": "AHT (s)",
        "AHT Acum (s)": "AHT Acum (s)",
        "ASA (s)": "ASA (s)"
    })

    st.dataframe(
        df_presentar_intra,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Llam Ent": st.column_config.NumberColumn("Llam Ent", format="%d"),
            "Llam Aten": st.column_config.NumberColumn("Llam Aten", format="%d"),
            "Llam Aban": st.column_config.NumberColumn("Llam Aban", format="%d"),
            "Atend. NS": st.column_config.NumberColumn("Atend. NS", format="%d"),
            "% Atenc.": st.column_config.NumberColumn("% Atenc.", format="%.1f%%"),
            "% Aband.": st.column_config.NumberColumn("% Aband.", format="%.1f%%"),
            "% NS": st.column_config.NumberColumn("% NS", format="%.1f%%"),
            "% NS Acum.": st.column_config.NumberColumn("% NS Acum.", format="%.1f%%"),
            "AHT (s)": st.column_config.NumberColumn("AHT (s)", format="%.0f s"),
            "AHT Acum (s)": st.column_config.NumberColumn("AHT Acum (s)", format="%.0f s"),
            "ASA (s)": st.column_config.NumberColumn("ASA (s)", format="%.1f s"),
        }
    )

    # ── 3. AHT POR SUPERVISOR Y ASESOR (AHT GENESYS) ─────────────────────────
    st.markdown("---")
    st.markdown("### 👤 Reporte AHT Asesores y Supervisores (AHT GENESYS Oficial)")
    st.caption("Consolidado por asesor y supervisor con metas contractuales, desvíos y tiempos de operación.")

    with st.spinner("Consultando AHT por asesor en Genesys..."):
        df_asesores_raw, err_as = obtener_aht_asesores_api(token)

    if not err_as and not df_asesores_raw.empty:
        aht_metas = gtr_cfg.get("aht_metas", {})

        def enriquecer_agente(row):
            aid = row["agente_id"]
            info = agentes_map.get(aid, {})
            nombre = info.get("agente", aid)
            cargo = info.get("cargo", "ASESOR")
            serv = info.get("servicio", "General")
            sup = info.get("jefe_inmediato", "-")
            coord = info.get("coordinador", "-")
            meta_a = aht_metas.get(serv, None)
            return pd.Series([nombre, cargo, serv, sup, coord, meta_a])

        cols_extra = ["Nombre Agente", "Cargo", "Servicio", "Supervisor", "Coordinador", "Meta AHT (s)"]
        df_asesores_raw[cols_extra] = df_asesores_raw.apply(enriquecer_agente, axis=1)

        # Filtrar solo asesores
        df_asesores = df_asesores_raw[df_asesores_raw["Cargo"].str.upper().str.contains("ASESOR", na=False)].copy()

        df_asesores["Dif vs Meta (s)"] = df_asesores["aht_seg"] - df_asesores["Meta AHT (s)"]
        df_asesores["% Desv AHT"] = (df_asesores["Dif vs Meta (s)"] / df_asesores["Meta AHT (s)"] * 100.0).round(1)
        df_asesores["AHT (mm:ss)"] = df_asesores["aht_seg"].apply(formatear_segundos_mm_ss)
        df_asesores["Meta (mm:ss)"] = df_asesores["Meta AHT (s)"].apply(formatear_segundos_mm_ss)

        # Pestañas para Asesores y Supervisores
        tab_as_ind, tab_as_sup = st.tabs(["Detalle Individual por Asesor", "Consolidado por Supervisor"])

        with tab_as_sup:
            # Resumen por supervisor
            df_sup = df_asesores.groupby("Supervisor").agg({
                "interacciones": "sum",
                "aht_seg": "mean",
                "Meta AHT (s)": "mean",
                "t_talk_seg": "mean",
                "t_held_seg": "mean",
                "t_acw_seg": "mean"
            }).reset_index().dropna(subset=["Supervisor"])
            df_sup = df_sup[df_sup["Supervisor"] != "-"]
            df_sup["Desv AHT (%)"] = ((df_sup["aht_seg"] - df_sup["Meta AHT (s)"]) / df_sup["Meta AHT (s)"] * 100.0).round(1)
            df_sup["AHT Real"] = df_sup["aht_seg"].apply(lambda s: f"{int(s)}s ({formatear_segundos_mm_ss(s)})")
            df_sup["Meta AHT"] = df_sup["Meta AHT (s)"].apply(lambda s: f"{int(s)}s ({formatear_segundos_mm_ss(s)})" if pd.notna(s) else "-")
            df_sup = df_sup.sort_values(by="interacciones", ascending=False)

            st.dataframe(
                df_sup[["Supervisor", "interacciones", "AHT Real", "Meta AHT", "Desv AHT (%)", "t_talk_seg", "t_held_seg", "t_acw_seg"]].rename(columns={
                    "interacciones": "Interacciones",
                    "t_talk_seg": "Talk Prom (s)",
                    "t_held_seg": "Hold Prom (s)",
                    "t_acw_seg": "ACW Prom (s)"
                }),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Interacciones": st.column_config.NumberColumn("Interacciones", format="%d"),
                    "Desv AHT (%)": st.column_config.NumberColumn("Desv AHT (%)", format="%+.1f%%"),
                    "Talk Prom (s)": st.column_config.NumberColumn("Talk Prom (s)", format="%.0f s"),
                    "Hold Prom (s)": st.column_config.NumberColumn("Hold Prom (s)", format="%.0f s"),
                    "ACW Prom (s)": st.column_config.NumberColumn("ACW Prom (s)", format="%.0f s"),
                }
            )

        with tab_as_ind:
            col_as1, col_as2 = st.columns([2, 2])
            with col_as1:
                busq_asesor = st.text_input("🔍 Buscar por Nombre o Supervisor:", placeholder="Ej: Perez...")
            with col_as2:
                servicios_as = ["Todos"] + sorted(df_asesores["Servicio"].unique())
                serv_as_sel = st.selectbox("Filtrar servicio del asesor:", servicios_as)

            df_as_mostrar = df_asesores.copy()
            if busq_asesor:
                m = (
                    df_as_mostrar["Nombre Agente"].str.contains(busq_asesor, case=False, na=False) |
                    df_as_mostrar["Supervisor"].str.contains(busq_asesor, case=False, na=False)
                )
                df_as_mostrar = df_as_mostrar[m]
            if serv_as_sel != "Todos":
                df_as_mostrar = df_as_mostrar[df_as_mostrar["Servicio"] == serv_as_sel]

            df_as_mostrar = df_as_mostrar.sort_values(by="interacciones", ascending=False)

            cols_tabla_as = [
                "Nombre Agente", "Servicio", "Supervisor", "interacciones",
                "aht_seg", "AHT (mm:ss)", "Meta AHT (s)", "Meta (mm:ss)",
                "% Desv AHT", "t_talk_seg", "t_held_seg", "t_acw_seg"
            ]
            df_as_presentar = df_as_mostrar[cols_tabla_as].rename(columns={
                "interacciones": "Interacciones",
                "aht_seg": "AHT Real (s)",
                "Meta AHT (s)": "Meta (s)",
                "% Desv AHT": "Desv (%)",
                "t_talk_seg": "Talk (s)",
                "t_held_seg": "Hold (s)",
                "t_acw_seg": "ACW (s)"
            })

            st.dataframe(
                df_as_presentar,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Interacciones": st.column_config.NumberColumn("Interacciones", format="%d"),
                    "AHT Real (s)": st.column_config.NumberColumn("AHT Real (s)", format="%.0f s"),
                    "Meta (s)": st.column_config.NumberColumn("Meta (s)", format="%.0f s"),
                    "Desv (%)": st.column_config.NumberColumn("Desv (%)", format="%+.1f%%"),
                    "Talk (s)": st.column_config.NumberColumn("Talk (s)", format="%.0f s"),
                    "Hold (s)": st.column_config.NumberColumn("Hold (s)", format="%.0f s"),
                    "ACW (s)": st.column_config.NumberColumn("ACW (s)", format="%.0f s"),
                }
            )

        # ── EXPORTACIÓN A EXCEL ──────────────────────────────────────────────
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df_matriz.to_excel(writer, sheet_name="Matriz HORA A HORA", index=False)
            df_presentar_intra.to_excel(writer, sheet_name="Detalle Intradia", index=False)
            df_as_presentar.to_excel(writer, sheet_name="AHT Asesores", index=False)

        st.download_button(
            label="📥 Descargar Reporte Completo en Excel (.xlsx)",
            data=output.getvalue(),
            file_name=f"Reporte_HORA_HORA_Genesys_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=False
        )
    else:
        st.info("No se registraron interacciones de asesores en el período consultado.")
