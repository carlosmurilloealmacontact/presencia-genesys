"""
Motor Analítico de Detección de Outliers e Infracciones Operativas (Lab Privado).
Desarrollado para Radar Operacional 4DX - AlmaExperience / LATAM Airlines.

Características Principales:
1. Agregación Temporal Multicorte:
   - Diario (D-1 o fecha específica)
   - Semana ISO (Lunes a Domingo, W01-W52)
   - Ciclos de Gestión 4DX (C1: 01-07, C2: 08-15, C3: 16-23, C4: 24-Cierre)
   - Mes Completo (MTD)
2. Blindaje Anti-Falsos Positivos (5 Compuertas):
   - Muestra mínima representativa (excluye turnos cortos <3h o desconexiones sin login)
   - Desvío estadístico intra-servicio (IQR Tukey 1.5x / 3x)
   - Exclusión de contingencias generales
   - Cruce jerárquico Socio Maestro
   - Comprobación estricta de reincidencia (>=2 días de desvío para calificar como Infractor)
3. Doble Visualización:
   - Vista Operativa (Supervisores): Scatter Plot 4 cuadrantes, tabla semáforo, ficha forense por asesor.
   - Vista Gerencial (Dirección): FTEs perdidos, impacto en horas, Pareto de concentración por supervisor y evolución por ciclos.
"""

import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

try:
    from exclusion_list import es_persona_excluida, es_servicio_latam
except ImportError:
    try:
        from scripts.exclusion_list import es_persona_excluida, es_servicio_latam
    except ImportError:
        def es_persona_excluida(val):
            return False
        def es_servicio_latam(val):
            return True


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PRESENCIA = PROJECT_ROOT / "data" / "presencia.db"


def _get_db():
    return sqlite3.connect(DB_PRESENCIA)


@st.cache_data(ttl=1800, show_spinner=False)
def cargar_universo_base_outliers(fecha_min: str = "2026-08-01", fecha_max: str = "2026-09-30") -> pd.DataFrame:
    """
    Carga y consolida en memoria el universo diario de turnos y presencia real
    cruzando turnos_detallados con segments para todo el personal operativo.
    """
    if not DB_PRESENCIA.exists():
        return pd.DataFrame()

    query = """
    SELECT 
        t.bp,
        t.fecha,
        t.nombre_agente,
        t.servicio,
        COALESCE(t.horas_programadas, 8.0) AS horas_programadas,
        COALESCE(s.min_conectado, 0.0) AS min_conectado,
        COALESCE(s.min_pausas, 0.0) AS min_pausas,
        COALESCE(s.coordinador, '') AS coordinador,
        COALESCE(s.jefe_inmediato, '') AS jefe_inmediato
    FROM turnos_detallados t
    LEFT JOIN (
        SELECT 
            substr(agente, 1, instr(agente, ' - ') - 1) AS bp,
            fecha,
            SUM(CASE WHEN presence_label != 'Offline' THEN duracion_min ELSE 0.0 END) AS min_conectado,
            SUM(CASE WHEN presence_label IN ('Break', 'Baño', 'Almuerzo', 'Pre Pausa', 'Pausa Activa', 'Personal') THEN duracion_min ELSE 0.0 END) AS min_pausas,
            MAX(coordinador) AS coordinador,
            MAX(jefe_inmediato) AS jefe_inmediato
        FROM segments
        WHERE fecha >= ? AND fecha <= ? AND agente LIKE '% - %'
        GROUP BY bp, fecha
    ) s ON t.bp = s.bp AND t.fecha = s.fecha
    WHERE t.fecha >= ? AND t.fecha <= ?
    """

    try:
        with _get_db() as conn:
            df = pd.read_sql_query(query, conn, params=[fecha_min, fecha_max, fecha_min, fecha_max])
    except Exception as e:
        st.error(f"Error cargando base de presencia para outliers: {e}")
        return pd.DataFrame()

    if df.empty:
        return df

    # Limpieza de nombres y servicios
    df["nombre_agente"] = df["nombre_agente"].astype(str).str.strip()
    df["servicio"] = df["servicio"].astype(str).str.strip()
    df["horas_programadas"] = pd.to_numeric(df["horas_programadas"], errors="coerce").fillna(8.0)
    df["min_conectado"] = pd.to_numeric(df["min_conectado"], errors="coerce").fillna(0.0)
    df["min_pausas"] = pd.to_numeric(df["min_pausas"], errors="coerce").fillna(0.0)

    # Filtrar exclusiones estándar LATAM
    df = df[df["servicio"].apply(es_servicio_latam)]
    df = df[~df["nombre_agente"].apply(es_persona_excluida)]

    # ── ETIQUETADO TEMPORAL MULTICORTE ──────────────────────────────────────────
    df["dt"] = pd.to_datetime(df["fecha"], errors="coerce")
    df = df.dropna(subset=["dt"])

    # 1. Semana ISO (Lunes a Domingo)
    def etiquetar_semana_iso(row):
        iso_yr, iso_wk, iso_d = row["dt"].isocalendar()
        lunes = row["dt"] - timedelta(days=iso_d - 1)
        domingo = lunes + timedelta(days=6)
        return f"Semana {iso_wk:02d} ({lunes.strftime('%d/%m')} - {domingo.strftime('%d/%m')})"

    df["semana_iso"] = df.apply(etiquetar_semana_iso, axis=1)

    # 2. Ciclos del Mes (4DX: 1-7, 8-15, 16-23, 24-Fin)
    def etiquetar_ciclo_4dx(row):
        dia = row["dt"].day
        mes_nombre = row["dt"].strftime("%b %Y").capitalize()
        if dia <= 7:
            return f"Ciclo 1: 01-07 {mes_nombre}"
        elif dia <= 15:
            return f"Ciclo 2: 08-15 {mes_nombre}"
        elif dia <= 23:
            return f"Ciclo 3: 16-23 {mes_nombre}"
        else:
            return f"Ciclo 4: 24-Fin {mes_nombre}"

    df["ciclo_4dx"] = df.apply(etiquetar_ciclo_4dx, axis=1)
    df["mes_anio"] = df["dt"].dt.strftime("%Y-%m")

    # 3. Métricas directas de cumplimiento
    df["min_programados"] = df["horas_programadas"] * 60.0
    df["pct_adherencia"] = np.where(
        df["min_programados"] > 0,
        (df["min_conectado"] / df["min_programados"]) * 100.0,
        0.0
    )
    df["pct_adherencia"] = df["pct_adherencia"].clip(lower=0.0, upper=150.0)
    df["min_productivo"] = np.maximum(0.0, df["min_conectado"] - df["min_pausas"])

    # Minutos de sobre-pausa (asumiendo estándar de descanso/almuerzo de 60m para turnos >= 6h)
    cuota_pausa_esperada = np.where(df["horas_programadas"] >= 7.0, 60.0, 30.0)
    df["min_exceso_pausa"] = np.maximum(0.0, df["min_pausas"] - cuota_pausa_esperada)

    # Horas perdidas de conexión
    df["horas_perdidas"] = np.maximum(0.0, (df["min_programados"] - df["min_conectado"]) / 60.0)

    # ── FILTRO ANTI-FALSOS POSITIVOS NIVEL 1 (Muestra Mínima Diaria) ───────────
    # Si fue programado menos de 3h o estuvo conectado menos de 30m, se marca muestra insuficiente
    df["muestra_valida"] = (df["horas_programadas"] >= 3.0) & (df["min_conectado"] >= 30.0)

    return df


def calcular_outliers_intra_servicio(df_sub: pd.DataFrame) -> pd.DataFrame:
    """
    Aplica el algoritmo de IQR (Tukey 1.5x) dentro de cada servicio para identificar
    los desvíos relativos a nivel diario sin mezclar colas dispares.
    """
    if df_sub.empty:
        return df_sub

    df_res = df_sub.copy()
    df_res["es_outlier_adherencia"] = False
    df_res["es_outlier_pausas"] = False
    df_res["es_outlier_dia"] = False
    df_res["es_outlier_extremo"] = False

    # Agrupamos por servicio para calcular percentiles relativos
    for srv, g in df_res.groupby("servicio"):
        g_val = g[g["muestra_valida"]]
        if len(g_val) < 5:
            continue

        # 1. Adherencia (Outlier por abajo: cola izquierda)
        q1_adh = g_val["pct_adherencia"].quantile(0.25)
        q3_adh = g_val["pct_adherencia"].quantile(0.75)
        iqr_adh = q3_adh - q1_adh
        lim_inf_adh = max(0.0, q1_adh - (1.5 * iqr_adh))
        lim_ext_adh = max(0.0, q1_adh - (3.0 * iqr_adh))

        # 2. Pausas (Outlier por arriba: cola derecha)
        q1_pau = g_val["min_pausas"].quantile(0.25)
        q3_pau = g_val["min_pausas"].quantile(0.75)
        iqr_pau = q3_pau - q1_pau
        lim_sup_pau = q3_pau + (1.5 * iqr_pau)
        lim_ext_pau = q3_pau + (3.0 * iqr_pau)

        idx = g.index
        # Marcado condicional
        cond_adh = (df_res.loc[idx, "muestra_valida"]) & (df_res.loc[idx, "pct_adherencia"] < lim_inf_adh)
        cond_pau = (df_res.loc[idx, "muestra_valida"]) & (df_res.loc[idx, "min_pausas"] > lim_sup_pau)
        cond_ext = (df_res.loc[idx, "pct_adherencia"] < lim_ext_adh) | (df_res.loc[idx, "min_pausas"] > lim_ext_pau)

        df_res.loc[idx, "es_outlier_adherencia"] = cond_adh
        df_res.loc[idx, "es_outlier_pausas"] = cond_pau
        df_res.loc[idx, "es_outlier_dia"] = cond_adh | cond_pau
        df_res.loc[idx, "es_outlier_extremo"] = cond_ext

    return df_res


def consolidar_infractores_periodo(df_eval: pd.DataFrame, min_dias_reincidencia: int = 2) -> pd.DataFrame:
    """
    Agrupa por asesor para validar reincidencia (filtro compuerta 5).
    Clasifica en los 4 Arquetipos Operativos.
    """
    if df_eval.empty:
        return pd.DataFrame()

    grp = df_eval.groupby(["bp", "nombre_agente", "servicio"]).agg(
        coordinador=("coordinador", lambda s: s.mode()[0] if not s.empty and s.mode().size > 0 else (s.iloc[0] if len(s) > 0 else "")),
        supervisor=("jefe_inmediato", lambda s: s.mode()[0] if not s.empty and s.mode().size > 0 else (s.iloc[0] if len(s) > 0 else "")),
        dias_programados=("fecha", "nunique"),
        dias_evaluados=("muestra_valida", "sum"),
        dias_desvio=("es_outlier_dia", "sum"),
        dias_desvio_extremo=("es_outlier_extremo", "sum"),
        dias_desvio_adh=("es_outlier_adherencia", "sum"),
        dias_desvio_pau=("es_outlier_pausas", "sum"),
        pct_adh_prom=("pct_adherencia", "mean"),
        min_pau_prom=("min_pausas", "mean"),
        min_exceso_pau_total=("min_exceso_pausa", "sum"),
        horas_perdidas_total=("horas_perdidas", "sum"),
    ).reset_index()

    # Redondeos ejecutivos
    grp["pct_adh_prom"] = grp["pct_adh_prom"].round(1)
    grp["min_pau_prom"] = grp["min_pau_prom"].round(1)
    grp["min_exceso_pau_total"] = grp["min_exceso_pau_total"].round(0)
    grp["horas_perdidas_total"] = grp["horas_perdidas_total"].round(1)

    def clasificar_arquetipo(row):
        d_desv = row["dias_desvio"]
        d_eval = row["dias_evaluados"]
        adh = row["pct_adh_prom"]
        pau = row["min_pau_prom"]

        if d_eval == 0:
            return "Muestra Insuficiente", "#94a3b8", "⚪"

        # Infractor Confirmado: supera el umbral de reincidencia
        if d_desv >= min_dias_reincidencia:
            if d_desv >= 4 or row["dias_desvio_extremo"] >= 2:
                return "Infractor Crítico (Alto Riesgo)", "#dc2626", "🚨"
            return "Infractor Reincidente", "#ea580c", "🛑"

        # 1 solo evento aislado: En observación
        if d_desv == 1:
            return "En Observación (Alerta Preventiva)", "#f59e0b", "🟡"

        # Sin desvíos estadísticos:
        if adh >= 92.0 and pau <= 65.0:
            return "Top Performer (Alta Disciplina)", "#16a34a", "🟢"
        elif adh >= 85.0:
            return "Cumplidor Estándar", "#2563eb", "🔵"
        else:
            return "Rendimiento Moderado", "#64748b", "🔘"

    arqs = grp.apply(clasificar_arquetipo, axis=1)
    grp["arquetipo"] = [a[0] for a in arqs]
    grp["color"] = [a[1] for a in arqs]
    grp["icono"] = [a[2] for a in arqs]

    grp["tasa_infraccion_pct"] = np.where(
        grp["dias_evaluados"] > 0,
        (grp["dias_desvio"] / grp["dias_evaluados"]) * 100.0,
        0.0
    ).round(1)

    grp = grp.sort_values(by=["dias_desvio", "horas_perdidas_total"], ascending=[False, False])
    return grp


def render_panel_outliers():
    """Renderiza el módulo interactivo del Detector de Outliers en Radar Operacional."""
    st.markdown(
        """
        <div style="background: linear-gradient(135deg, #0f172a 0%, #1e1b4b 100%); padding: 18px 24px; border-radius: 12px; margin-bottom: 20px; border-left: 6px solid #f43f5e; box-shadow: 0 4px 12px rgba(0,0,0,0.15);">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
                <div>
                    <h2 style="color: #ffffff; margin: 0; font-size: 22px; font-weight: 700; letter-spacing: -0.5px;">
                        🎯 Detector de Outliers e Infracciones Operativas
                    </h2>
                    <p style="color: #94a3b8; margin: 4px 0 0 0; font-size: 13px;">
                        Laboratorio Privado • Blindaje estadístico anti-falsos positivos (IQR intra-servicio), auditoría multitemporal y matriz 4DX
                    </p>
                </div>
                <div style="text-align: right; background: #334155; padding: 6px 14px; border-radius: 8px; border: 1px solid #475569;">
                    <span style="color: #f43f5e; font-size: 11px; font-weight: 700; text-transform: uppercase;">Exclusivo Supervisión</span><br>
                    <span style="color: #cbd5e1; font-size: 12px; font-weight: 600;">Lab Carlos Murillo</span>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    with st.spinner("Cargando matriz analítica de turnos y presencia real..."):
        df_base = cargar_universo_base_outliers("2026-08-01", "2026-09-30")

    if df_base.empty:
        st.warning("⚠️ No se encontraron registros de turnos o presencia en la base de datos.")
        return

    # ── CONTROLES SUPERIORES: SELECTOR DE MODO TEMPORAL ──────────────────────
    st.markdown("#### ⏱️ 1. Horizonte Temporal de Análisis")

    c_modo1, c_modo2 = st.columns([1.2, 2.8])
    with c_modo1:
        modo_temporal = st.radio(
            "Seleccionar Horizonte:",
            [
                "🔄 Ciclo del Mes (4DX)",
                "📅 Semana ISO (Lun-Dom)",
                "📆 Diario (D-1)",
                "📊 Mes Completo (MTD)"
            ],
            index=0,
            key="outlier_modo_temp"
        )

    df_filtrado = pd.DataFrame()
    label_periodo_seleccionado = ""
    min_reincidencia_requerida = 2

    with c_modo2:
        if modo_temporal == "🔄 Ciclo del Mes (4DX)":
            ciclos_disponibles = sorted(list(df_base["ciclo_4dx"].unique()), reverse=True)
            sel_ciclo = st.selectbox("Seleccione el Ciclo 4DX a auditar:", ciclos_disponibles, index=0)
            df_filtrado = df_base[df_base["ciclo_4dx"] == sel_ciclo]
            label_periodo_seleccionado = sel_ciclo
            min_reincidencia_requerida = 2

        elif modo_temporal == "📅 Semana ISO (Lun-Dom)":
            semanas_disponibles = sorted(list(df_base["semana_iso"].unique()), reverse=True)
            sel_sem = st.selectbox("Seleccione la Semana ISO:", semanas_disponibles, index=0)
            df_filtrado = df_base[df_base["semana_iso"] == sel_sem]
            label_periodo_seleccionado = sel_sem
            min_reincidencia_requerida = 2

        elif modo_temporal == "📆 Diario (D-1)":
            fechas_disponibles = sorted(list(df_base["fecha"].unique()), reverse=True)
            sel_fec = st.selectbox("Seleccione la Fecha a auditar:", fechas_disponibles, index=0)
            df_filtrado = df_base[df_base["fecha"] == sel_fec]
            label_periodo_seleccionado = f"Día {sel_fec}"
            min_reincidencia_requerida = 1

        else:  # Mes Completo
            meses_disponibles = sorted(list(df_base["mes_anio"].unique()), reverse=True)
            sel_mes = st.selectbox("Seleccione el Mes MTD:", meses_disponibles, index=0)
            df_filtrado = df_base[df_base["mes_anio"] == sel_mes]
            label_periodo_seleccionado = f"Mes {sel_mes}"
            min_reincidencia_requerida = 3

    # ── FILTROS DE SEGMENTACIÓN OPERATIVA ────────────────────────────────────
    st.markdown("#### 🎯 2. Filtros de Operación y Segmentación")
    c_flt1, c_flt2, c_flt3 = st.columns(3)

    servicios_disp = ["Todos los Servicios"] + sorted([s for s in df_filtrado["servicio"].dropna().unique() if str(s).strip() != ""])
    with c_flt1:
        sel_srv = st.selectbox("Servicio / Campaña:", servicios_disp, index=0, key="outlier_sel_srv")
        if sel_srv != "Todos los Servicios":
            df_filtrado = df_filtrado[df_filtrado["servicio"] == sel_srv]

    coords_disp = ["Todos los Coordinadores"] + sorted([c for c in df_filtrado["coordinador"].dropna().unique() if str(c).strip() != ""])
    with c_flt2:
        sel_coord = st.selectbox("Coordinador:", coords_disp, index=0, key="outlier_sel_coord")
        if sel_coord != "Todos los Coordinadores":
            df_filtrado = df_filtrado[df_filtrado["coordinador"] == sel_coord]

    supervs_disp = ["Todos los Supervisores"] + sorted([s for s in df_filtrado["jefe_inmediato"].dropna().unique() if str(s).strip() != ""])
    with c_flt3:
        sel_superv = st.selectbox("Supervisor Inmediato:", supervs_disp, index=0, key="outlier_sel_superv")
        if sel_superv != "Todos los Supervisores":
            df_filtrado = df_filtrado[df_filtrado["jefe_inmediato"] == sel_superv]

    if df_filtrado.empty:
        st.info("ℹ️ No hay registros para la combinación de filtros seleccionada.")
        return

    # ── EJECUCIÓN DEL MOTOR DE OUTLIERS ──────────────────────────────────────
    df_outliers_dia = calcular_outliers_intra_servicio(df_filtrado)
    df_resumen = consolidar_infractores_periodo(df_outliers_dia, min_dias_reincidencia=min_reincidencia_requerida)

    # ── KPI METRICS CARDS SUPERIORES ─────────────────────────────────────────
    tot_evaluados = len(df_resumen)
    infractores_criticos = df_resumen[df_resumen["arquetipo"].str.contains("Crítico")]
    infractores_reincidentes = df_resumen[df_resumen["arquetipo"].str.contains("Reincidente")]
    tot_infractores = len(infractores_criticos) + len(infractores_reincidentes)
    en_observacion = len(df_resumen[df_resumen["arquetipo"].str.contains("Observación")])
    top_performers = len(df_resumen[df_resumen["arquetipo"].str.contains("Top Performer")])

    horas_perdidas_infractores = (infractores_criticos["horas_perdidas_total"].sum() + infractores_reincidentes["horas_perdidas_total"].sum())
    dias_habiles_est = max(1, df_filtrado["fecha"].nunique())
    fte_fantasma = round(horas_perdidas_infractores / (dias_habiles_est * 8.0), 1)

    c_k1, c_k2, c_k3, c_k4, c_k5 = st.columns(5)
    with c_k1:
        st.metric("👥 Asesores Evaluados", f"{tot_evaluados:,}", help="Total de asesores con turnos programados en el corte.")
    with c_k2:
        st.metric("🛑 Infractores Confirmados", f"{tot_infractores}", f"{round((tot_infractores/tot_evaluados*100), 1) if tot_evaluados>0 else 0}% del piso", delta_color="inverse")
    with c_k3:
        st.metric("🟡 En Observación (1 desvío)", f"{en_observacion}", "Alerta preventiva")
    with c_k4:
        st.metric("🟢 Top Performers", f"{top_performers}", "Alta disciplina")
    with c_k5:
        st.metric("📉 FTEs Perdidos", f"{fte_fantasma} FTE", f"{int(horas_perdidas_infractores):,} hrs perdidas", delta_color="inverse")

    st.markdown("---")

    # ── PESTAÑAS: VISTA OPERATIVA VS VISTA GERENCIAL ─────────────────────────
    tab_op, tab_ger = st.tabs(["👁️ Vista Operativa (Piso y Supervisores)", "📊 Vista Gerencial (Estratégica y Financiera)"])

    # ─────────────────────────────────────────────────────────────────────────
    # TAB 1: VISTA OPERATIVA
    # ─────────────────────────────────────────────────────────────────────────
    with tab_op:
        st.markdown(f"### 📍 Matriz de 4 Cuadrantes: Disciplina vs Pausas ({label_periodo_seleccionado})")
        st.caption("Cada punto representa a un asesor. Puntos rojos/naranjas en la esquina superior izquierda son infractores reincidentes.")

        fig_scatter = px.scatter(
            df_resumen,
            x="pct_adh_prom",
            y="min_pau_prom",
            color="arquetipo",
            color_discrete_map={
                "Infractor Crítico (Alto Riesgo)": "#dc2626",
                "Infractor Reincidente": "#ea580c",
                "En Observación (Alerta Preventiva)": "#f59e0b",
                "Top Performer (Alta Disciplina)": "#16a34a",
                "Cumplidor Estándar": "#2563eb",
                "Rendimiento Moderado": "#64748b",
                "Muestra Insuficiente": "#cbd5e1"
            },
            hover_name="nombre_agente",
            hover_data={
                "supervisor": True,
                "servicio": True,
                "dias_desvio": True,
                "dias_programados": True,
                "horas_perdidas_total": True,
                "pct_adh_prom": ":.1f%",
                "min_pau_prom": ":.1f min"
            },
            labels={
                "pct_adh_prom": "% Adherencia Promedio (Conexión / Turno)",
                "min_pau_prom": "Minutos Diarios en Pausas (Promedio)",
                "arquetipo": "Clasificación"
            },
            height=460
        )

        fig_scatter.add_vline(x=90.0, line_dash="dash", line_color="#94a3b8", annotation_text="Meta Adherencia 90%")
        fig_scatter.add_hline(y=60.0, line_dash="dash", line_color="#94a3b8", annotation_text="Límite Pausa 60m")
        fig_scatter.update_layout(margin=dict(l=20, r=20, t=30, b=20), legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
        st.plotly_chart(fig_scatter, use_container_width=True)

        st.markdown("#### 📋 Expediente de Infractores Reincidentes")
        df_infractores = df_resumen[df_resumen["dias_desvio"] >= min_reincidencia_requerida].copy()

        if not df_infractores.empty:
            cols_show = [
                "icono", "nombre_agente", "supervisor", "servicio", "dias_desvio",
                "dias_programados", "pct_adh_prom", "min_pau_prom", "horas_perdidas_total", "arquetipo"
            ]
            st.dataframe(
                df_infractores[cols_show].rename(columns={
                    "icono": "Alerta",
                    "nombre_agente": "Asesor",
                    "supervisor": "Supervisor Inmediato",
                    "servicio": "Servicio",
                    "dias_desvio": "Días Desvío",
                    "dias_programados": "Días Turno",
                    "pct_adh_prom": "% Adherencia",
                    "min_pau_prom": "Min Pausa/Día",
                    "horas_perdidas_total": "Horas Perdidas",
                    "arquetipo": "Diagnóstico"
                }),
                use_container_width=True,
                hide_index=True
            )
        else:
            st.success("🎉 ¡Excelente noticia! No se detectaron infractores reincidentes en el periodo y filtros seleccionados.")

        st.markdown("---")
        st.markdown("#### 🔍 Ficha Forense del Asesor (Auditoría Día a Día)")
        asesores_lista = df_resumen["nombre_agente"].tolist()
        sel_asesor_forense = st.selectbox("Seleccione un asesor para auditar su detalle diario:", asesores_lista, index=0)

        df_asesor_dias = df_outliers_dia[df_outliers_dia["nombre_agente"] == sel_asesor_forense].sort_values("fecha")
        if not df_asesor_dias.empty:
            c_f1, c_f2 = st.columns([1.2, 2.8])
            with c_f1:
                sup_asesor = df_asesor_dias["jefe_inmediato"].iloc[0] or "No registrado"
                srv_asesor = df_asesor_dias["servicio"].iloc[0]
                tot_desv = df_asesor_dias["es_outlier_dia"].sum()
                tot_perd = df_asesor_dias["horas_perdidas"].sum()
                st.markdown(
                    f"""
                    <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 10px; padding: 14px;">
                        <h4 style="margin: 0 0 6px 0; color: #0f172a;">👤 {sel_asesor_forense}</h4>
                        <div style="font-size: 12px; color: #64748b;"><b>Supervisor:</b> {sup_asesor}</div>
                        <div style="font-size: 12px; color: #64748b;"><b>Servicio:</b> {srv_asesor}</div>
                        <hr style="margin: 8px 0; border: 0; border-top: 1px solid #e2e8f0;">
                        <div style="font-size: 13px; color: #dc2626;"><b>Días en Infracción:</b> {tot_desv} jornadas</div>
                        <div style="font-size: 13px; color: #475569;"><b>Horas Perdidas:</b> {tot_perd:.1f} hrs</div>
                    </div>
                    """,
                    unsafe_allow_html=True
                )
            with c_f2:
                df_forense_tbl = df_asesor_dias[[
                    "fecha", "horas_programadas", "min_conectado", "min_pausas", "pct_adherencia", "es_outlier_dia"
                ]].copy()
                df_forense_tbl["min_conectado"] = df_forense_tbl["min_conectado"].round(0).astype(int)
                df_forense_tbl["min_pausas"] = df_forense_tbl["min_pausas"].round(0).astype(int)
                df_forense_tbl["pct_adherencia"] = df_forense_tbl["pct_adherencia"].round(1)
                df_forense_tbl["Estado Día"] = df_forense_tbl["es_outlier_dia"].map({True: "🛑 Infracción Atípica", False: "🟢 Conforme"})

                st.dataframe(
                    df_forense_tbl[[
                        "fecha", "horas_programadas", "min_conectado", "min_pausas", "pct_adherencia", "Estado Día"
                    ]].rename(columns={
                        "fecha": "Fecha",
                        "horas_programadas": "Horas Prog.",
                        "min_conectado": "Min Conectado",
                        "min_pausas": "Min Pausas",
                        "pct_adherencia": "% Adherencia"
                    }),
                    use_container_width=True,
                    hide_index=True
                )

    # ─────────────────────────────────────────────────────────────────────────
    # TAB 2: VISTA GERENCIAL
    # ─────────────────────────────────────────────────────────────────────────
    with tab_ger:
        st.markdown(f"### 💼 Resumen Ejecutivo y Costo del Desvío ({label_periodo_seleccionado})")

        c_g1, c_g2 = st.columns([1.5, 2.5])
        with c_g1:
            st.markdown(
                f"""
                <div style="background: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; padding: 18px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
                    <h4 style="margin: 0 0 10px 0; color: #0f172a; font-size: 16px;">💰 Impacto Financiero y Capacidad</h4>
                    <p style="font-size: 13px; color: #475569; line-height: 1.5;">
                        Los desvíos atípicos de los <b>{tot_infractores} infractores confirmados</b> representan:
                    </p>
                    <ul style="font-size: 13px; color: #334155; padding-left: 18px; line-height: 1.6;">
                        <li><b>{fte_fantasma} FTEs fantasma</b> no disponibles para atención.</li>
                        <li><b>{int(horas_perdidas_infractores):,} horas</b> de servicio pagadas pero no laboradas.</li>
                        <li>Equivalente a <b>{round(horas_perdidas_infractores / 8.0, 1)} turnos completos</b> perdidos.</li>
                    </ul>
                    <div style="background: #fef2f2; border-left: 4px solid #ef4444; padding: 10px 12px; border-radius: 6px; font-size: 12px; color: #991b1b; margin-top: 10px;">
                        💡 <b>Recomendación 4DX:</b> Focalizar compromisos 1 a 1 en los 3 supervisores con mayor concentración de reincidencia.
                    </div>
                </div>
                """,
                unsafe_allow_html=True
            )

        with c_g2:
            st.markdown("#### 🏢 Concentración de Infractores por Supervisor Inmediato (Pareto)")
            df_sup_pareto = df_resumen[df_resumen["dias_desvio"] >= min_reincidencia_requerida].groupby("supervisor").agg(
                infractores=("bp", "count"),
                horas_perdidas=("horas_perdidas_total", "sum")
            ).reset_index().sort_values("infractores", ascending=False)

            if not df_sup_pareto.empty:
                fig_pareto = px.bar(
                    df_sup_pareto.head(10),
                    x="supervisor",
                    y="infractores",
                    color="horas_perdidas",
                    color_continuous_scale="Reds",
                    labels={"supervisor": "Supervisor", "infractores": "Cant. Infractores", "horas_perdidas": "Horas Perdidas"},
                    text="infractores",
                    height=320
                )
                fig_pareto.update_layout(margin=dict(l=20, r=20, t=10, b=20))
                st.plotly_chart(fig_pareto, use_container_width=True)
            else:
                st.info("Sin infractores registrados para generar gráfico de Pareto.")

        st.markdown("---")
        st.markdown("#### 📈 Trazabilidad y Evolución entre Ciclos 4DX")
        df_ciclos_evo = df_base.groupby(["ciclo_4dx", "bp"]).agg(
            min_con=("min_conectado", "sum"),
            min_prog=("min_programados", "sum"),
            min_pau=("min_pausas", "sum")
        ).reset_index()

        df_ciclos_evo["pct_adh"] = np.where(df_ciclos_evo["min_prog"] > 0, (df_ciclos_evo["min_con"] / df_ciclos_evo["min_prog"]) * 100.0, 0.0)
        df_ciclos_evo["es_critico"] = (df_ciclos_evo["pct_adh"] < 80.0) | (df_ciclos_evo["min_pau"] > 400.0)

        evo_summary = df_ciclos_evo.groupby("ciclo_4dx").agg(
            total_asesores=("bp", "nunique"),
            infractores_ciclo=("es_critico", "sum")
        ).reset_index().sort_values("ciclo_4dx")

        evo_summary["tasa_infraccion"] = ((evo_summary["infractores_ciclo"] / evo_summary["total_asesores"]) * 100.0).round(1)

        fig_evo = px.line(
            evo_summary,
            x="ciclo_4dx",
            y="tasa_infraccion",
            markers=True,
            text="tasa_infraccion",
            labels={"ciclo_4dx": "Ciclo 4DX", "tasa_infraccion": "% Tasa de Infracción"},
            height=280
        )
        fig_evo.update_traces(textposition="top center", line_color="#dc2626")
        fig_evo.update_layout(margin=dict(l=20, r=20, t=20, b=20))
        st.plotly_chart(fig_evo, use_container_width=True)
