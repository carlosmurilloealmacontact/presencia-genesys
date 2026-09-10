"""
Motor GTR y Niveles de Servicio — Radar Genesys Cloud.
Calcula métricas de Service Level (NS), AHT, Abandono y ASA
agrupadas por servicio y cortes de 30 minutos, además de AHT individual por asesor.
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
    aht_metas = gtr_cfg.get("aht_metas", {})

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
        r = requests.post(url, headers=headers, json=body, timeout=20)
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

    df = pd.DataFrame(records)
    return df, None


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
        r = requests.post(url, headers=headers, json=body, timeout=20)
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


def exportar_resumen_gtr_excel(df_servicios: pd.DataFrame, df_asesores: pd.DataFrame) -> bytes:
    """Genera archivo Excel consolidado de GTR con formato ejecutivo."""
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df_servicios.to_excel(writer, sheet_name="Resumen Servicios", index=False)
        if not df_asesores.empty:
            df_asesores.to_excel(writer, sheet_name="AHT Asesores", index=False)
    return output.getvalue()


def render_tab_gtr(agentes_map: dict):
    """Renderiza la pestaña principal de Monitor GTR y Niveles de Servicio."""
    token = obtener_token_genesys()
    if not token:
        st.warning("⚠️ No se encontró token activo de Genesys Cloud. Conéctalo en Neon Postgres o revisa las credenciales.")
        return

    col_h1, col_h2 = st.columns([4, 1])
    with col_h1:
        st.subheader("📈 Monitor GTR — Niveles de Servicio y TMO Intradía")
        st.caption("Métricas oficiales en tiempo real extraídas de Genesys Cloud Analytics agrupadas por Macro-Servicio.")
    with col_h2:
        if st.button("🔄 Actualizar Datos GTR", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    gtr_cfg = cargar_config_gtr()
    services_cfg = gtr_cfg.get("services", {})
    aht_metas = gtr_cfg.get("aht_metas", {})

    with st.spinner("Consultando métricas en vivo de Genesys Cloud..."):
        df_raw, err = obtener_metricas_gtr_api(token)

    if err or df_raw.empty:
        st.error(f"No fue posible cargar las métricas de Genesys: {err}")
        return

    # 1. Agrupación Acumulada por Macro-Servicio
    srv_agg = df_raw.groupby(["servicio", "canal"]).agg({
        "nOffered": "sum",
        "tAnswered_count": "sum",
        "tAbandon_count": "sum",
        "tHandle_count": "sum",
        "tHandle_sum": "sum",
        "tAnswered_sum": "sum",
        "sl_numerator": "sum",
        "sl_denominator": "sum"
    }).reset_index()

    srv_agg["% Atencion"] = (srv_agg["tAnswered_count"] / srv_agg["nOffered"] * 100).fillna(0).round(1)
    srv_agg["% Abandono"] = (srv_agg["tAbandon_count"] / srv_agg["nOffered"] * 100).fillna(0).round(1)
    srv_agg["AHT Real (s)"] = (srv_agg["tHandle_sum"] / srv_agg["tHandle_count"] / 1000).fillna(0).round(0)
    srv_agg["ASA (s)"] = (srv_agg["tAnswered_sum"] / srv_agg["tAnswered_count"] / 1000).fillna(0).round(1)
    srv_agg["NS Real (%)"] = (srv_agg["sl_numerator"] / srv_agg["sl_denominator"] * 100).fillna(0).round(1)

    # Añadir metas
    srv_agg["NS Meta (%)"] = srv_agg["servicio"].map(
        lambda s: round(services_cfg.get(s, {}).get("ns_meta", 0.0) * 100, 1) if services_cfg.get(s, {}).get("ns_meta") else None
    )
    srv_agg["AHT Meta (s)"] = srv_agg["servicio"].map(lambda s: aht_metas.get(s, None))

    srv_agg["Dif NS"] = srv_agg["NS Real (%)"] - srv_agg["NS Meta (%)"]
    srv_agg["Dif AHT (s)"] = srv_agg["AHT Real (s)"] - srv_agg["AHT Meta (s)"]

    # Totales Globales
    tot_ofrecidas = int(srv_agg["nOffered"].sum())
    tot_atendidas = int(srv_agg["tAnswered_count"].sum())
    tot_abandonadas = int(srv_agg["tAbandon_count"].sum())
    pct_abandono_global = round(tot_abandonadas / tot_ofrecidas * 100, 1) if tot_ofrecidas > 0 else 0.0

    tot_sl_num = srv_agg["sl_numerator"].sum()
    tot_sl_den = srv_agg["sl_denominator"].sum()
    ns_global = round(tot_sl_num / tot_sl_den * 100, 1) if tot_sl_den > 0 else 0.0

    tot_handle_sum = srv_agg["tHandle_sum"].sum()
    tot_handle_cnt = srv_agg["tHandle_count"].sum()
    aht_global_s = round(tot_handle_sum / tot_handle_cnt / 1000, 0) if tot_handle_cnt > 0 else 0.0

    tot_ans_sum = srv_agg["tAnswered_sum"].sum()
    asa_global_s = round(tot_ans_sum / tot_atendidas / 1000, 1) if tot_atendidas > 0 else 0.0

    # ── KPIs Superiores ──────────────────────────────────────────────────────
    k1, k2, k3, k4, k5 = st.columns(5)
    with k1:
        st.metric("Total Ofrecidas", f"{tot_ofrecidas:,}")
    with k2:
        st.metric("Total Atendidas", f"{tot_atendidas:,}", delta=f"{100 - pct_abandono_global:.1f}% Atenc.")
    with k3:
        st.metric("% Abandono Global", f"{pct_abandono_global:.1f}%", delta=f"{tot_abandonadas:,} aban", delta_color="inverse")
    with k4:
        st.metric("Nivel Servicio Global", f"{ns_global:.1f}%", delta="Ponderado SLA")
    with k5:
        st.metric("AHT Global Promedio", f"{int(aht_global_s)} s", delta=formatear_segundos_mm_ss(aht_global_s))

    st.markdown("---")

    # ── Filtros y Opciones ───────────────────────────────────────────────────
    fc1, fc2, fc3 = st.columns([2, 1, 1])
    with fc1:
        servicios_disponibles = sorted(srv_agg["servicio"].unique())
        servicios_sel = st.multiselect("Filtrar por Servicio:", servicios_disponibles, default=[])
    with fc2:
        canal_sel = st.selectbox("Filtrar por Canal:", ["Todos", "VOZ", "CHAT / WPP"])
    with fc3:
        ocultar_sin_trafico = st.checkbox("Ocultar sin tráfico", value=True)

    df_filtrado = srv_agg.copy()
    if servicios_sel:
        df_filtrado = df_filtrado[df_filtrado["servicio"].isin(servicios_sel)]
    if canal_sel == "VOZ":
        df_filtrado = df_filtrado[~df_filtrado["servicio"].str.contains("WPP|CHAT", case=False, na=False)]
    elif canal_sel == "CHAT / WPP":
        df_filtrado = df_filtrado[df_filtrado["servicio"].str.contains("WPP|CHAT", case=False, na=False)]
    if ocultar_sin_trafico:
        df_filtrado = df_filtrado[df_filtrado["nOffered"] > 0]

    df_filtrado = df_filtrado.sort_values(by="nOffered", ascending=False)

    # ── Tabla Ejecutiva por Servicio ─────────────────────────────────────────
    st.markdown("### 📊 Cumplimiento de Niveles de Servicio por Macro-Servicio (Acumulado Hoy)")

    df_tabla = df_filtrado.copy()
    df_tabla["AHT Real"] = df_tabla["AHT Real (s)"].apply(lambda s: f"{int(s)}s ({formatear_segundos_mm_ss(s)})" if s > 0 else "-")
    df_tabla["AHT Meta"] = df_tabla["AHT Meta (s)"].apply(lambda s: f"{int(s)}s ({formatear_segundos_mm_ss(s)})" if pd.notna(s) and s > 0 else "-")

    columnas_mostrar = [
        "servicio", "nOffered", "tAnswered_count", "tAbandon_count",
        "% Atencion", "% Abandono", "NS Real (%)", "NS Meta (%)", "Dif NS",
        "AHT Real", "AHT Meta", "ASA (s)"
    ]
    df_presentar = df_tabla[columnas_mostrar].rename(columns={
        "servicio": "Macro-Servicio",
        "nOffered": "Ofrecidas",
        "tAnswered_count": "Atendidas",
        "tAbandon_count": "Abandono",
        "% Atencion": "% Atenc.",
        "% Abandono": "% Aband.",
        "NS Real (%)": "NS Real",
        "NS Meta (%)": "NS Meta",
        "Dif NS": "Dif NS (pp)",
        "ASA (s)": "ASA (s)"
    })

    st.dataframe(
        df_presentar,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Ofrecidas": st.column_config.NumberColumn("Ofrecidas", format="%d"),
            "Atendidas": st.column_config.NumberColumn("Atendidas", format="%d"),
            "Abandono": st.column_config.NumberColumn("Abandono", format="%d"),
            "% Atenc.": st.column_config.NumberColumn("% Atenc.", format="%.1f%%"),
            "% Aband.": st.column_config.NumberColumn("% Aband.", format="%.1f%%"),
            "NS Real": st.column_config.NumberColumn("NS Real", format="%.1f%%"),
            "NS Meta": st.column_config.NumberColumn("NS Meta", format="%.1f%%"),
            "Dif NS (pp)": st.column_config.NumberColumn("Dif NS (pp)", format="%+.1f%%"),
            "ASA (s)": st.column_config.NumberColumn("ASA (s)", format="%.1f s"),
        }
    )

    # ── Evolución Intradía por Bloques de 30 minutos ─────────────────────────
    st.markdown("### ⏱️ Evolución Intradía (Intervalos de 30 minutos)")
    
    df_intra = df_raw.copy()
    if servicios_sel:
        df_intra = df_intra[df_intra["servicio"].isin(servicios_sel)]
    elif ocultar_sin_trafico:
        top5_servicios = srv_agg.sort_values("nOffered", ascending=False).head(5)["servicio"].tolist()
        df_intra = df_intra[df_intra["servicio"].isin(top5_servicios)]

    intra_grp = df_intra.groupby(["intervalo", "servicio"]).agg({
        "nOffered": "sum",
        "tAnswered_count": "sum",
        "sl_numerator": "sum",
        "sl_denominator": "sum",
        "tHandle_sum": "sum",
        "tHandle_count": "sum"
    }).reset_index()

    intra_grp["NS (%)"] = (intra_grp["sl_numerator"] / intra_grp["sl_denominator"] * 100).fillna(0).round(1)
    intra_grp["AHT (s)"] = (intra_grp["tHandle_sum"] / intra_grp["tHandle_count"] / 1000).fillna(0).round(0)

    tab_graf1, tab_graf2, tab_matriz = st.tabs(["Curva de Nivel de Servicio (NS %)", "Curva de AHT (Segundos)", "Matriz Detallada 30 min"])

    with tab_graf1:
        fig_ns = px.line(
            intra_grp,
            x="intervalo",
            y="NS (%)",
            color="servicio",
            markers=True,
            title="Evolución del Nivel de Servicio (%) por Intervalo de 30 Minutos",
            labels={"intervalo": "Hora (Colombia UTC-5)", "NS (%)": "Nivel de Servicio (%)", "servicio": "Servicio"}
        )
        fig_ns.add_hline(y=80, line_dash="dash", line_color="green", annotation_text="Meta Estándar 80%")
        fig_ns.add_hline(y=70, line_dash="dot", line_color="orange", annotation_text="Meta 70%")
        fig_ns.update_layout(yaxis_range=[0, 105], height=400)
        st.plotly_chart(fig_ns, use_container_width=True)

    with tab_graf2:
        fig_aht = px.line(
            intra_grp,
            x="intervalo",
            y="AHT (s)",
            color="servicio",
            markers=True,
            title="Evolución del Tiempo Medio de Operación (AHT en Segundos)",
            labels={"intervalo": "Hora (Colombia UTC-5)", "AHT (s)": "AHT (segundos)", "servicio": "Servicio"}
        )
        fig_aht.update_layout(height=400)
        st.plotly_chart(fig_aht, use_container_width=True)

    with tab_matriz:
        pivot_ns = intra_grp.pivot(index="intervalo", columns="servicio", values="NS (%)").fillna("-")
        st.dataframe(pivot_ns, use_container_width=True)

    # ── Sección de AHT por Asesor ────────────────────────────────────────────
    st.markdown("---")
    st.markdown("### 👤 AHT Individual por Asesor y Metas Contractuales")
    st.caption("Cruce de datos de Genesys Analytics con la base maestra de asesores, coordinadores y supervisores.")

    with st.spinner("Consultando AHT por asesor en Genesys..."):
        df_asesores_raw, err_as = obtener_aht_asesores_api(token)

    if not err_as and not df_asesores_raw.empty:
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

        df_asesores = df_asesores_raw[df_asesores_raw["Cargo"].str.upper().str.contains("ASESOR", na=False)].copy()

        df_asesores["Dif vs Meta (s)"] = df_asesores["aht_seg"] - df_asesores["Meta AHT (s)"]
        df_asesores["AHT (mm:ss)"] = df_asesores["aht_seg"].apply(formatear_segundos_mm_ss)
        df_asesores["Meta (mm:ss)"] = df_asesores["Meta AHT (s)"].apply(formatear_segundos_mm_ss)

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
            "Dif vs Meta (s)", "t_talk_seg", "t_held_seg", "t_acw_seg"
        ]
        df_as_presentar = df_as_mostrar[cols_tabla_as].rename(columns={
            "interacciones": "Interacciones",
            "aht_seg": "AHT Real (s)",
            "Meta AHT (s)": "Meta (s)",
            "Dif vs Meta (s)": "Dif (s)",
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
                "Dif (s)": st.column_config.NumberColumn("Dif (s)", format="%+.0f s"),
                "Talk (s)": st.column_config.NumberColumn("Talk (s)", format="%.0f s"),
                "Hold (s)": st.column_config.NumberColumn("Hold (s)", format="%.0f s"),
                "ACW (s)": st.column_config.NumberColumn("ACW (s)", format="%.0f s"),
            }
        )

        excel_bytes = exportar_resumen_gtr_excel(df_presentar, df_as_presentar)
        st.download_button(
            label="📥 Descargar Reporte GTR en Excel (.xlsx)",
            data=excel_bytes,
            file_name=f"Reporte_GTR_Genesys_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=False
        )
    else:
        st.info("No se registraron interacciones de asesores en el período consultado.")
