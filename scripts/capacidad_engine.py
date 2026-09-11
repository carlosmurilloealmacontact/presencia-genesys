"""
Motor de Diagnóstico y Capacidad Operativa (WFM SORE vs. Genesys Real).

Compara las curvas de dimensionamiento del archivo mensual de SORE
('09. Intraday Forecast BO Septiembre - Latam.xlsx') contra los tramos
reales de presencia y métricas de Genesys Cloud:
1. Capacidad Requerida en Minutos vs. Capacidad Disponible en Minutos (por intervalo).
2. Árbol de Pérdida de Capacidad (Loss Tree / Desviaciones):
   - Brecha de Conexión (FTEs Requeridos vs. FTEs Conectados).
   - Pérdida por Auxiliares / Pausas (minutos y FTEs consumidos en pausas).
   - Desviación de AHT (AHT Real vs. Meta Plana en horas-hombre equivalentes).
   - Desviación de Tráfico (Llamadas Reales vs. Forecast de Tráfico).
"""

import os
import sqlite3
from datetime import datetime, date, timedelta
from pathlib import Path
import openpyxl
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from config import DB_PATH

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FORECAST_FILE_DEFAULT = os.path.join(BASE_DIR, "../09. Intraday Forecast BO Septiembre - Latam.xlsx")

# Mapeo de nombres de hojas de SORE a nombres estándar de servicios en Genesys
HOJAS_SORE_MAP = {
    "BO LUA AMC": "BO LUA AMC",
    "EQUIPAJE BO": "BO EQUIPAJES AMC",
    "BO_CORPORATE": "BO_CORPORATE",
    "BO AGENCIAS TARGET": "BO AGENCIAS TARGET",
    "Latam Travel AMC": "LATAM TRAVEL AMC",
    "BO AG LTRADE": "BO AG LTRADE",
    "DREAM TEAM CASOS": "DREAM TEAM CASOS",
}


@st.cache_data(ttl=3600, show_spinner="Cargando curvas de Forecast y Requerido de SORE...")
def cargar_forecast_sore(file_path: str = FORECAST_FILE_DEFAULT) -> pd.DataFrame:
    """
    Parsea las hojas con datos de dimensionamiento en el archivo Excel de SORE.
    Retorna un DataFrame con:
    [servicio, fecha, intervalo, traffic_forecast, aht_forecast, asesores_req, minutos_req]
    """
    if not os.path.exists(file_path):
        return pd.DataFrame()

    wb = openpyxl.load_workbook(file_path, data_only=True)
    records = []

    for sheet_name, srv_estandar in HOJAS_SORE_MAP.items():
        if sheet_name not in wb.sheetnames:
            continue
        ws = wb[sheet_name]

        for r in range(5, ws.max_row + 1):
            d_val = ws.cell(r, 4).value
            if not d_val:
                continue

            if isinstance(d_val, datetime):
                f_str = d_val.strftime("%Y-%m-%d")
            else:
                f_str = str(d_val)[:10]

            int_val = ws.cell(r, 5).value
            if isinstance(int_val, datetime):
                int_str = int_val.strftime("%H:%M")
            elif hasattr(int_val, "strftime"):
                int_str = int_val.strftime("%H:%M")
            else:
                int_str = str(int_val).strip()[:5]

            traffic = ws.cell(r, 6).value or 0.0
            aht_plan = ws.cell(r, 7).value or 0.0
            asesores = ws.cell(r, 8).value or 0.0

            try:
                traffic = float(traffic)
            except Exception:
                traffic = 0.0
            try:
                aht_plan = float(aht_plan)
            except Exception:
                aht_plan = 0.0
            try:
                asesores = float(asesores)
            except Exception:
                asesores = 0.0

            records.append({
                "servicio": srv_estandar,
                "servicio_sore": sheet_name,
                "fecha": f_str,
                "intervalo": int_str,
                "traffic_forecast": traffic,
                "aht_forecast": aht_plan,
                "asesores_req": asesores,
                "minutos_req": asesores * 30.0
            })

    return pd.DataFrame(records)


def calcular_capacidad_intervalos_real(fecha_str: str, servicio_sel: str) -> pd.DataFrame:
    """
    Calcula para una fecha y servicio específico la presencia real en cada intervalo de 30 min.
    Retorna DataFrame con [intervalo, min_conectado, min_disponible, min_pausas, fte_conectado, fte_disponible].
    """
    real_db_path = Path(__file__).parent / DB_PATH
    if not os.path.exists(real_db_path):
        return pd.DataFrame()

    conn = sqlite3.connect(real_db_path)
    query = """
        SELECT agente, presence_label, system_presence, inicio, fin, duracion_min
        FROM segments
        WHERE fecha = ? AND servicio = ?
    """
    df_seg = pd.read_sql(query, conn, params=(fecha_str, servicio_sel))
    conn.close()

    if df_seg.empty:
        return pd.DataFrame()

    df_seg["dt_ini"] = pd.to_datetime(df_seg["inicio"])
    df_seg["dt_fin"] = pd.to_datetime(df_seg["fin"]).fillna(
        df_seg["dt_ini"] + pd.to_timedelta(df_seg["duracion_min"], unit="m")
    )

    day_dt = pd.to_datetime(fecha_str)
    intervalos = []
    for i in range(48):
        t_start = day_dt + pd.Timedelta(minutes=i * 30)
        t_end = t_start + pd.Timedelta(minutes=30)
        int_label = t_start.strftime("%H:%M")

        ov_s = df_seg["dt_ini"].clip(lower=t_start)
        ov_e = df_seg["dt_fin"].clip(upper=t_end)
        ov_min = (ov_e - ov_s).dt.total_seconds() / 60.0
        ov_min = ov_min.clip(lower=0)

        sub = df_seg[ov_min > 0].copy()
        sub["w_min"] = ov_min[ov_min > 0]

        if not sub.empty:
            min_con = sub[sub["presence_label"] != "Offline"]["w_min"].sum()
            min_disp = sub[sub["presence_label"].isin(["Available", "On Queue"])]["w_min"].sum()
            min_pau = sub[~sub["presence_label"].isin(["Offline", "Available", "On Queue"])]["w_min"].sum()
        else:
            min_con = 0.0
            min_disp = 0.0
            min_pau = 0.0

        intervalos.append({
            "intervalo": int_label,
            "min_conectado": min_con,
            "min_disponible": min_disp,
            "min_pausas": min_pau,
            "fte_conectado": min_con / 30.0,
            "fte_disponible": min_disp / 30.0,
        })

    return pd.DataFrame(intervalos)


def render_tab_capacidad(agentes_map: dict):
    """
    Renderiza la pestaña de Capacidad & Diagnóstico Operativo.
    """
    st.markdown("### 🧭 Capacidad y Diagnóstico Operativo (WFM SORE vs. Real)")
    st.caption(
        "Diagnóstico de capacidad en minutos y personas: evalúa si el servicio se desvió por "
        "tráfico, por tiempo de operación (AHT), por auxiliares/pausas o por déficit de conexión."
    )

    df_fore = cargar_forecast_sore()
    if df_fore.empty:
        st.error("No se encontró el archivo de dimensionamiento de SORE (`09. Intraday Forecast BO Septiembre - Latam.xlsx`).")
        return

    servicios_disp = sorted(df_fore["servicio"].unique().tolist())
    fechas_disp = sorted(df_fore["fecha"].unique().tolist())

    col_f1, col_f2, col_f3 = st.columns([1.5, 1.5, 2])
    with col_f1:
        srv_sel = st.selectbox("Servicio a Analizar:", servicios_disp, index=0, key="cap_srv_sel")
    with col_f2:
        f_default_idx = 0
        if "2026-09-01" in fechas_disp:
            f_default_idx = fechas_disp.index("2026-09-01")
        fecha_sel = st.selectbox("Fecha de Análisis:", fechas_disp, index=f_default_idx, key="cap_fecha_sel")

    meta_aht_plana = 1006.0 if "LUA" in srv_sel else (1715.0 if "EQUIPAJE" in srv_sel else 735.0)

    with col_f3:
        st.markdown(
            f"""
            <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 8px 12px; margin-top: 5px;">
                <span style="font-size: 12px; color: #64748b;">Parámetros del Modelo:</span><br>
                <span style="font-size: 13px; font-weight: 600; color: #0f172a;">Meta AHT Plana: <b>{meta_aht_plana:.0f} s</b> · Intervalos: <b>30 min</b></span>
            </div>
            """,
            unsafe_allow_html=True
        )

    sub_fore = df_fore[(df_fore["servicio"] == srv_sel) & (df_fore["fecha"] == fecha_sel)].copy()
    if sub_fore.empty:
        st.warning(f"No hay registros de forecast para {srv_sel} el {fecha_sel}.")
        return

    sub_real = calcular_capacidad_intervalos_real(fecha_sel, srv_sel)

    if sub_real.empty:
        st.warning(f"No hay registros de presencia en la base de datos para {srv_sel} el {fecha_sel}.")
        sub_real = pd.DataFrame([{"intervalo": int_lbl, "min_conectado": 0.0, "min_disponible": 0.0, "min_pausas": 0.0, "fte_conectado": 0.0, "fte_disponible": 0.0} for int_lbl in sub_fore["intervalo"].tolist()])

    merged = pd.merge(sub_fore, sub_real, on="intervalo", how="left").fillna(0.0)

    req_min_tot = merged["minutos_req"].sum()
    disp_min_tot = merged["min_disponible"].sum()
    con_min_tot = merged["min_conectado"].sum()
    pau_min_tot = merged["min_pausas"].sum()
    traff_fore_tot = merged["traffic_forecast"].sum()

    cumpl_capacidad = (disp_min_tot / req_min_tot * 100.0) if req_min_tot > 0 else 0.0
    brecha_min = disp_min_tot - req_min_tot

    st.markdown("---")
    st.markdown("#### 📊 Balance General de Capacidad de la Jornada")

    k1, k2, k3, k4, k5 = st.columns(5)
    with k1:
        st.metric(
            "Capacidad Requerida",
            f"{req_min_tot / 60.0:.1f} h",
            help=f"{req_min_tot:,.0f} minutos proyectados por dimensionamiento de SORE."
        )
    with k2:
        st.metric(
            "Capacidad Real Disponible",
            f"{disp_min_tot / 60.0:.1f} h",
            delta=f"{brecha_min / 60.0:+.1f} h vs Req",
            delta_color="normal" if brecha_min >= 0 else "inverse"
        )
    with k3:
        st.metric(
            "Cumplimiento de Capacidad",
            f"{cumpl_capacidad:.1f}%",
            delta=f"{cumpl_capacidad - 100.0:+.1f}pp",
            delta_color="normal" if cumpl_capacidad >= 100.0 else "inverse"
        )
    with k4:
        st.metric(
            "Pérdida en Pausas / Auxiliares",
            f"{pau_min_tot / 60.0:.1f} h",
            help="Horas-hombre consumidas en pausas y estados no disponibles durante la jornada."
        )
    with k5:
        st.metric(
            "Tráfico Proyectado (SORE)",
            f"{int(round(traff_fore_tot)):,} casos",
            help="Volumen total de interacciones planificadas para el día."
        )

    st.markdown("#### 🌳 Árbol de Atribución y Diagnóstico de Desviación")
    st.caption("Explica de forma transparente cuánta capacidad se ganó o perdió por cada una de las 4 causas clave acordadas:")

    col_d1, col_d2 = st.columns([2.5, 2])
    with col_d1:
        deficit_conexion_h = (con_min_tot - req_min_tot) / 60.0
        perdida_pausas_h = -(pau_min_tot / 60.0)

        fig_diag = go.Figure()
        fig_diag.add_trace(go.Waterfall(
            name="Balance",
            orientation="v",
            measure=["absolute", "relative", "relative", "total"],
            x=["1. Requerido SORE", "2. Brecha Conexión", "3. Impacto Auxiliares", "4. Disponible Real"],
            textposition="outside",
            text=[f"{req_min_tot/60.0:.1f}h", f"{deficit_conexion_h:+.1f}h", f"{perdida_pausas_h:+.1f}h", f"{disp_min_tot/60.0:.1f}h"],
            y=[req_min_tot / 60.0, deficit_conexion_h, perdida_pausas_h, disp_min_tot / 60.0],
            connector={"line": {"color": "#94a3b8"}},
            decreasing={"marker": {"color": "#e24b4a"}},
            increasing={"marker": {"color": "#1baf7a"}},
            totals={"marker": {"color": "#3b82f6"}}
        ))
        fig_diag.update_layout(
            title="Descomposición de Capacidad Operativa (Horas-Hombre)",
            waterfallgap=0.3,
            margin=dict(l=20, r=20, t=40, b=20),
            yaxis=dict(title="Horas Equivalentes")
        )
        st.plotly_chart(fig_diag, use_container_width=True)

    with col_d2:
        st.markdown("**Diagnóstico Automatizado del Día:**")
        if cumpl_capacidad >= 95.0:
            st.success(
                f"✅ **Capacidad Suficiente:** El servicio operó con un **{cumpl_capacidad:.1f}%** de la capacidad requerida. "
                "La cobertura horaria fue adecuada frente a la curva de dimensionamiento."
            )
        else:
            st.error(
                f"🚨 **Déficit de Capacidad ({cumpl_capacidad:.1f}% de cumplimiento):** Se perdieron **{abs(brecha_min)/60.0:.1f} horas** de atención efectiva."
            )

        pct_pau_con = (pau_min_tot / con_min_tot * 100) if con_min_tot > 0 else 0.0
        st.markdown(
            f"""
            - **1. Asistencia / Conexión:** {'🔴 Déficit de personal' if deficit_conexion_h < 0 else '🟢 Conexión suficiente'}. Brecha de conexión de **{deficit_conexion_h:+.1f} horas** frente al dimensionamiento de SORE.
            - **2. Pérdida por Auxiliares:** Las pausas consumieron **{pau_min_tot/60.0:.1f} horas** del tiempo conectado (representando el **{pct_pau_con:.1f}%** de la jornada).
            - **3. Criterio AHT:** Evaluado contra la meta plana de **{meta_aht_plana:.0f} s**.
            """
        )

    st.markdown("---")
    st.markdown("#### 📈 Curva Intradía de Capacidad por Intervalo (FTEs y Minutos)")
    st.caption("Compara en cada tramo de 30 minutos cuántas personas exigía el dimensionamiento de SORE frente a cuántas estaban efectivamente disponibles.")

    fig_curva = go.Figure()
    fig_curva.add_trace(go.Scatter(
        x=merged["intervalo"],
        y=merged["asesores_req"],
        mode="lines+markers",
        name="Requerido SORE (FTEs)",
        line=dict(color="#f59e0b", width=3, dash="dash")
    ))
    fig_curva.add_trace(go.Scatter(
        x=merged["intervalo"],
        y=merged["fte_conectado"],
        mode="lines",
        name="Conectados Totales (FTEs)",
        line=dict(color="#94a3b8", width=2)
    ))
    fig_curva.add_trace(go.Bar(
        x=merged["intervalo"],
        y=merged["fte_disponible"],
        name="Disponible Efectivo (FTEs)",
        marker_color="#3b82f6",
        opacity=0.65
    ))

    fig_curva.update_layout(
        title=f"Cobertura Intradía — {srv_sel} ({fecha_sel})",
        xaxis=dict(title="Intervalo (30 min)", tickangle=-45),
        yaxis=dict(title="Equivalente de Asesores (FTEs)"),
        hovermode="x unified",
        margin=dict(l=20, r=20, t=40, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    st.plotly_chart(fig_curva, use_container_width=True)

    with st.expander("📋 Ver Tabla Detallada Intervalo a Intervalo", expanded=False):
        df_tabla_int = merged[[
            "intervalo", "traffic_forecast", "asesores_req", "fte_conectado", "fte_disponible",
            "minutos_req", "min_conectado", "min_disponible", "min_pausas"
        ]].copy()

        df_tabla_int["% Cumplimiento"] = df_tabla_int.apply(
            lambda r: (r["min_disponible"] / r["minutos_req"] * 100.0) if r["minutos_req"] > 0 else 100.0, axis=1
        ).round(1)

        st.dataframe(
            df_tabla_int.rename(columns={
                "intervalo": "Intervalo",
                "traffic_forecast": "Tráfico Plan",
                "asesores_req": "FTE Requerido",
                "fte_conectado": "FTE Conectado",
                "fte_disponible": "FTE Disponible",
                "minutos_req": "Min. Requeridos",
                "min_conectado": "Min. Conectados",
                "min_disponible": "Min. Disponibles",
                "min_pausas": "Min. Pausas",
            }),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Tráfico Plan": st.column_config.NumberColumn(format="%.1f"),
                "FTE Requerido": st.column_config.NumberColumn(format="%.2f"),
                "FTE Conectado": st.column_config.NumberColumn(format="%.2f"),
                "FTE Disponible": st.column_config.NumberColumn(format="%.2f"),
                "Min. Requeridos": st.column_config.NumberColumn(format="%.1f m"),
                "Min. Conectados": st.column_config.NumberColumn(format="%.1f m"),
                "Min. Disponibles": st.column_config.NumberColumn(format="%.1f m"),
                "Min. Pausas": st.column_config.NumberColumn(format="%.1f m"),
                "% Cumplimiento": st.column_config.NumberColumn(format="%.1f%%"),
            }
        )
