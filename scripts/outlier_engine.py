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
3. Causa Raíz Explícita y Forense de Pausas:
   - Motivo sintético e inmediato en la tabla principal (ej: Sobre-pausa en Baño/Break vs Baja conexión).
   - Desglose forense de pausas (Baño, Break, Almuerzo, Coaching/4DX) día por día con semáforos de tolerancia.
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
    Carga y consolida en memoria el universo diario de turnos y presencia real.
    BLINDAJE DE TURNOS TRASNOCHO (Cruzan la medianoche):
    - Si un turno tiene turno_fin < turno_ini (ej: 22:00 a 05:00), la ventana de presencia
      se extiende desde fecha turno_ini - 30m hasta fecha+1 turno_fin + 30m.
    - Se acreditan todas las horas y pausas de la madrugada a la fecha de inicio del turno,
      eliminando falsos positivos en asesores de turno nocturno.
    """
    if not DB_PRESENCIA.exists():
        return pd.DataFrame()

    # 1. Cargar turnos detallados en el rango solicitado
    q_turnos = """
    SELECT 
        bp, fecha, nombre_agente, servicio,
        COALESCE(horas_programadas, 8.0) AS horas_programadas,
        COALESCE(turno_ini, '--') AS turno_ini,
        COALESCE(turno_fin, '--') AS turno_fin
    FROM turnos_detallados
    WHERE fecha >= ? AND fecha <= ?
    """

    # 2. Cargar segmentos de presencia extendiendo 1 día antes y 1 día después para trasnochos
    try:
        dt_min_f = pd.to_datetime(fecha_min) - timedelta(days=1)
        dt_max_f = pd.to_datetime(fecha_max) + timedelta(days=1)
        f_min_ext = dt_min_f.strftime("%Y-%m-%d")
        f_max_ext = dt_max_f.strftime("%Y-%m-%d")

        with _get_db() as conn:
            df_turnos = pd.read_sql_query(q_turnos, conn, params=[fecha_min, fecha_max])
            
            q_segments = """
            SELECT 
                substr(agente, 1, instr(agente, ' - ') - 1) AS bp,
                fecha,
                presence_label,
                inicio,
                fin,
                duracion_min,
                coordinador,
                jefe_inmediato
            FROM segments
            WHERE fecha >= ? AND fecha <= ? AND agente LIKE '% - %'
            """
            df_seg = pd.read_sql_query(q_segments, conn, params=[f_min_ext, f_max_ext])
    except Exception as e:
        st.error(f"Error cargando base de presencia para outliers: {e}")
        return pd.DataFrame()

    if df_turnos.empty:
        return pd.DataFrame()

    # Limpieza
    df_turnos["nombre_agente"] = df_turnos["nombre_agente"].astype(str).str.strip()
    df_turnos["servicio"] = df_turnos["servicio"].astype(str).str.strip()
    df_turnos["horas_programadas"] = pd.to_numeric(df_turnos["horas_programadas"], errors="coerce").fillna(8.0)

    # Filtrar exclusiones estándar LATAM
    df_turnos = df_turnos[df_turnos["servicio"].apply(es_servicio_latam)]
    df_turnos = df_turnos[~df_turnos["nombre_agente"].apply(es_persona_excluida)]

    if df_seg.empty:
        df_turnos["min_conectado"] = 0.0
        df_turnos["min_pausas"] = 0.0
        df_turnos["min_break"] = 0.0
        df_turnos["min_bano"] = 0.0
        df_turnos["min_lunch"] = 0.0
        df_turnos["min_coaching"] = 0.0
        df_turnos["coordinador"] = ""
        df_turnos["jefe_inmediato"] = ""
        return df_turnos

    # Pre-procesar fechas y tipos de pausa en segmentos
    df_seg["inicio_dt"] = pd.to_datetime(df_seg["inicio"], errors="coerce")
    df_seg = df_seg.dropna(subset=["inicio_dt"])

    est_offline = {'Offline'}
    pausas_break = {'Break', 'Pre Pausa', 'Pausa Activa'}
    pausas_bano = {'Baño'}
    pausas_lunch = {'Lunch', 'Almuerzo', 'Refeição (sólo BR)'}
    pausas_coaching = {'Feedback', 'Reunión Equipo', 'PCA - Feedback', 'PCA- Diálogo', 'Diálogo Diario / 4DX', 'Refuerzo Semanal', 'Cursos Adicionales'}
    todas_pausas = {'Break', 'Baño', 'Almuerzo', 'Pre Pausa', 'Pausa Activa', 'Personal', 'Lunch', 'Refeição (sólo BR)'}

    df_seg["is_con"] = ~df_seg["presence_label"].isin(est_offline)
    df_seg["is_pau"] = df_seg["presence_label"].isin(todas_pausas)
    df_seg["is_break"] = df_seg["presence_label"].isin(pausas_break)
    df_seg["is_bano"] = df_seg["presence_label"].isin(pausas_bano)
    df_seg["is_lunch"] = df_seg["presence_label"].isin(pausas_lunch)
    df_seg["is_coaching"] = df_seg["presence_label"].isin(pausas_coaching)

    # Segmentos agrupados por BP
    seg_by_bp = {k: v for k, v in df_seg.groupby("bp")}

    # Mapeo de jerarquía por BP
    bp_coord_map = df_seg.dropna(subset=["coordinador"]).drop_duplicates("bp", keep="last").set_index("bp")["coordinador"].to_dict()
    bp_superv_map = df_seg.dropna(subset=["jefe_inmediato"]).drop_duplicates("bp", keep="last").set_index("bp")["jefe_inmediato"].to_dict()

    # Procesar cada turno con ventana de jornada adaptativa
    filas_procesadas = []
    for _, trn in df_turnos.iterrows():
        bp = trn["bp"]
        fec = trn["fecha"]
        t_ini = str(trn["turno_ini"]).strip()
        t_fin = str(trn["turno_fin"]).strip()
        h_prog = float(trn["horas_programadas"])

        sub_s = seg_by_bp.get(bp)
        if sub_s is None or sub_s.empty:
            filas_procesadas.append({
                "min_conectado": 0.0, "min_pausas": 0.0, "min_break": 0.0,
                "min_bano": 0.0, "min_lunch": 0.0, "min_coaching": 0.0,
                "coordinador": bp_coord_map.get(bp, ""), "jefe_inmediato": bp_superv_map.get(bp, "")
            })
            continue

        # Validar si es turno trasnocho (cruza la medianoche)
        es_trasnocho = (t_fin < t_ini) and (t_fin not in ("--", "", "None")) and (t_ini not in ("--", "", "None"))

        if es_trasnocho:
            dt_ini = pd.to_datetime(f"{fec} {t_ini}")
            dt_fin = pd.to_datetime(f"{fec} {t_fin}") + timedelta(days=1)
            w_start = dt_ini - timedelta(minutes=30)
            w_end = dt_fin + timedelta(minutes=30)

            # Ventana extendida a la madrugada del día siguiente
            sub_w = sub_s[(sub_s["inicio_dt"] >= w_start) & (sub_s["inicio_dt"] <= w_end)]
        else:
            # Turno normal de calendario
            sub_w = sub_s[sub_s["fecha"] == fec]

        m_con = sub_w.loc[sub_w["is_con"], "duracion_min"].sum()
        m_pau = sub_w.loc[sub_w["is_pau"], "duracion_min"].sum()
        m_brk = sub_w.loc[sub_w["is_break"], "duracion_min"].sum()
        m_ban = sub_w.loc[sub_w["is_bano"], "duracion_min"].sum()
        m_lun = sub_w.loc[sub_w["is_lunch"], "duracion_min"].sum()
        m_coa = sub_w.loc[sub_w["is_coaching"], "duracion_min"].sum()

        filas_procesadas.append({
            "min_conectado": m_con,
            "min_pausas": m_pau,
            "min_break": m_brk,
            "min_bano": m_ban,
            "min_lunch": m_lun,
            "min_coaching": m_coa,
            "coordinador": bp_coord_map.get(bp, ""),
            "jefe_inmediato": bp_superv_map.get(bp, "")
        })

    df_metrics = pd.DataFrame(filas_procesadas, index=df_turnos.index)
    for col in df_metrics.columns:
        df_turnos[col] = df_metrics[col]

    df = df_turnos

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

    # 3. Métricas de adherencia y sobrepausa
    df["min_programados"] = df["horas_programadas"] * 60.0
    df["pct_adherencia"] = np.where(
        df["min_programados"] > 0,
        (df["min_conectado"] / df["min_programados"]) * 100.0,
        0.0
    ).clip(0.0, 150.0)

    # Excesos de pausas específicas
    # Tolerancia estándar: Baño 20m, Break 35m, Lunch 45m
    df["exceso_bano"] = np.maximum(0.0, df["min_bano"] - 20.0)
    df["exceso_break"] = np.maximum(0.0, df["min_break"] - 35.0)
    df["cuota_pausa_esperada"] = np.where(df["horas_programadas"] >= 7.0, 60.0, 30.0)
    df["min_exceso_pausa"] = np.maximum(0.0, df["min_pausas"] - df["cuota_pausa_esperada"])

    # Horas perdidas de conexión
    df["horas_perdidas"] = np.maximum(0.0, (df["min_programados"] - df["min_conectado"]) / 60.0)

    # Filtro de muestra válida diaria
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
    df_res["es_outlier_bano"] = False
    df_res["es_outlier_dia"] = False
    df_res["es_outlier_extremo"] = False

    for srv, g in df_res.groupby("servicio"):
        g_val = g[g["muestra_valida"]]
        if len(g_val) < 5:
            continue

        # 1. Adherencia (Outlier por abajo)
        q1_adh = g_val["pct_adherencia"].quantile(0.25)
        q3_adh = g_val["pct_adherencia"].quantile(0.75)
        iqr_adh = q3_adh - q1_adh
        lim_inf_adh = max(0.0, q1_adh - (1.5 * iqr_adh))
        lim_ext_adh = max(0.0, q1_adh - (3.0 * iqr_adh))

        # 2. Pausas Totales (Outlier por arriba)
        q1_pau = g_val["min_pausas"].quantile(0.25)
        q3_pau = g_val["min_pausas"].quantile(0.75)
        iqr_pau = q3_pau - q1_pau
        lim_sup_pau = q3_pau + (1.5 * iqr_pau)
        lim_ext_pau = q3_pau + (3.0 * iqr_pau)

        idx = g.index
        cond_adh = (df_res.loc[idx, "muestra_valida"]) & (df_res.loc[idx, "pct_adherencia"] < lim_inf_adh)
        cond_pau = (df_res.loc[idx, "muestra_valida"]) & (df_res.loc[idx, "min_pausas"] > lim_sup_pau)
        cond_bano = (df_res.loc[idx, "muestra_valida"]) & (df_res.loc[idx, "min_bano"] > 25.0)
        cond_ext = (df_res.loc[idx, "pct_adherencia"] < lim_ext_adh) | (df_res.loc[idx, "min_pausas"] > lim_ext_pau)

        df_res.loc[idx, "es_outlier_adherencia"] = cond_adh
        df_res.loc[idx, "es_outlier_pausas"] = cond_pau
        df_res.loc[idx, "es_outlier_bano"] = cond_bano
        df_res.loc[idx, "es_outlier_dia"] = cond_adh | cond_pau | cond_bano
        df_res.loc[idx, "es_outlier_extremo"] = cond_ext

    return df_res


def consolidar_infractores_periodo(df_eval: pd.DataFrame, min_dias_reincidencia: int = 2) -> pd.DataFrame:
    """
    Agrupa por asesor para validar reincidencia y genera el motivo de alerta explícito.
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
        dias_desvio_bano=("es_outlier_bano", "sum"),
        pct_adh_prom=("pct_adherencia", "mean"),
        min_pau_prom=("min_pausas", "mean"),
        min_break_prom=("min_break", "mean"),
        min_bano_prom=("min_bano", "mean"),
        min_lunch_prom=("min_lunch", "mean"),
        min_exceso_pau_total=("min_exceso_pausa", "sum"),
        horas_perdidas_total=("horas_perdidas", "sum"),
    ).reset_index()

    grp["pct_adh_prom"] = grp["pct_adh_prom"].round(1)
    grp["min_pau_prom"] = grp["min_pau_prom"].round(1)
    grp["min_break_prom"] = grp["min_break_prom"].round(1)
    grp["min_bano_prom"] = grp["min_bano_prom"].round(1)
    grp["min_lunch_prom"] = grp["min_lunch_prom"].round(1)
    grp["min_exceso_pau_total"] = grp["min_exceso_pau_total"].round(0)
    grp["horas_perdidas_total"] = grp["horas_perdidas_total"].round(1)

    def clasificar_arquetipo_y_motivo(row):
        d_desv = row["dias_desvio"]
        d_eval = row["dias_evaluados"]
        adh = row["pct_adh_prom"]
        pau = row["min_pau_prom"]
        bano = row["min_bano_prom"]
        brk = row["min_break_prom"]
        perd = row["horas_perdidas_total"]

        if d_eval == 0:
            return "Muestra Insuficiente", "#94a3b8", "⚪", "Sin conexión representativa en el periodo"

        # Determinación de Causas Raíz específicas
        causas = []
        if adh < 75.0:
            causas.append(f"Baja conexión ({adh:.1f}%, -{perd:.1f}h)")
        elif adh < 85.0:
            causas.append(f"Conexión deficiente ({adh:.1f}%)")

        if bano >= 25.0 and brk >= 40.0:
            causas.append(f"Sobre-pausa severa en Baño ({bano:.0f}m) y Break ({brk:.0f}m)")
        elif bano >= 25.0:
            causas.append(f"Exceso en Baño (prom. {bano:.0f}m/día)")
        elif brk >= 45.0:
            causas.append(f"Exceso en Break (prom. {brk:.0f}m/día)")
        elif pau >= 75.0:
            causas.append(f"Exceso general en pausas (prom. {pau:.0f}m/día)")

        motivo_texto = " + ".join(causas) if causas else "Desvío atípico intra-servicio"

        # 1. Infractor Confirmado: supera el umbral de reincidencia
        if d_desv >= min_dias_reincidencia:
            if d_desv >= 4 or row["dias_desvio_extremo"] >= 2 or adh < 65.0:
                return "Infractor Crítico (Alto Riesgo)", "#dc2626", "🚨", f"🛑 Reincidente {d_desv} días: {motivo_texto}"
            return "Infractor Reincidente", "#ea580c", "🛑", f"⚠️ Reincidente {d_desv} días: {motivo_texto}"

        # 2. Un solo evento aislado
        if d_desv == 1:
            return "En Observación (Alerta Preventiva)", "#f59e0b", "🟡", f"Incidencia aislada (1 día): {motivo_texto}"

        # 3. Sin desvíos
        if adh >= 92.0 and pau <= 65.0:
            return "Top Performer (Alta Disciplina)", "#16a34a", "🟢", "Operación ejemplar (adherencia y pausas en norma)"
        elif adh >= 85.0:
            return "Cumplidor Estándar", "#2563eb", "🔵", "Desempeño dentro de parámetros esperados"
        else:
            return "Rendimiento Moderado", "#64748b", "🔘", "Sin infracciones estadísticas en el periodo"

    res_diag = grp.apply(clasificar_arquetipo_y_motivo, axis=1)
    grp["arquetipo"] = [r[0] for r in res_diag]
    grp["color"] = [r[1] for r in res_diag]
    grp["icono"] = [r[2] for r in res_diag]
    grp["motivo_alerta"] = [r[3] for r in res_diag]

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
                        Laboratorio Privado • Blindaje estadístico anti-falsos positivos (IQR intra-servicio), causas raíz explícitas y auditoría multitemporal
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
                "motivo_alerta": True,
                "pct_adh_prom": ":.1f%",
                "min_bano_prom": ":.1f min",
                "min_break_prom": ":.1f min"
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

        # ── TABLA PRINCIPAL: EXPEDIENTE DE INFRACTORES CON MOTIVO EXPLÍCITO ───
        st.markdown("#### 📋 Expediente de Infractores Reincidentes (Con Motivo Explícito)")
        st.caption("Esta tabla detalla la causa raíz de la infracción sin necesidad de abrir la ficha individual.")

        df_infractores = df_resumen[df_resumen["dias_desvio"] >= min_reincidencia_requerida].copy()

        if not df_infractores.empty:
            cols_show = [
                "icono", "nombre_agente", "supervisor", "servicio", "motivo_alerta",
                "dias_desvio", "pct_adh_prom", "min_bano_prom", "min_break_prom", "horas_perdidas_total"
            ]
            st.dataframe(
                df_infractores[cols_show].rename(columns={
                    "icono": "Alerta",
                    "nombre_agente": "Asesor",
                    "supervisor": "Supervisor Inmediato",
                    "servicio": "Servicio",
                    "motivo_alerta": "🎯 Causa Raíz / Motivo de Alerta",
                    "dias_desvio": "Días Desvío",
                    "pct_adh_prom": "% Adh.",
                    "min_bano_prom": "Baño Prom.",
                    "min_break_prom": "Break Prom.",
                    "horas_perdidas_total": "Hrs Perdidas"
                }),
                use_container_width=True,
                hide_index=True
            )
        else:
            st.success("🎉 ¡Excelente noticia! No se detectaron infractores reincidentes en el periodo y filtros seleccionados.")

        # ── FICHA FORENSE INDIVIDUAL CON DETALLE DE PAUSAS DÍA POR DÍA ────────
        st.markdown("---")
        st.markdown("#### 🔍 Ficha Forense del Asesor (Auditoría Día a Día & Desglose de Pausas)")
        asesores_lista = df_resumen["nombre_agente"].tolist()
        sel_asesor_forense = st.selectbox("Seleccione un asesor para auditar su detalle de pausas y conexión:", asesores_lista, index=0)

        df_asesor_dias = df_outliers_dia[df_outliers_dia["nombre_agente"] == sel_asesor_forense].sort_values("fecha")
        if not df_asesor_dias.empty:
            sup_asesor = df_asesor_dias["jefe_inmediato"].iloc[0] or "No registrado"
            srv_asesor = df_asesor_dias["servicio"].iloc[0]
            tot_desv = df_asesor_dias["es_outlier_dia"].sum()
            tot_perd = df_asesor_dias["horas_perdidas"].sum()
            prom_adh = df_asesor_dias["pct_adherencia"].mean()
            prom_bano = df_asesor_dias["min_bano"].mean()
            prom_break = df_asesor_dias["min_break"].mean()
            prom_lunch = df_asesor_dias["min_lunch"].mean()

            # Tarjetas de auditoría de pausas del asesor seleccionado
            c_af1, c_af2, c_af3, c_af4 = st.columns(4)
            with c_af1:
                st.metric(
                    "🚽 Baño / Necesidades",
                    f"{prom_bano:.0f} min/día",
                    f"{prom_bano - 20.0:+.0f} min vs límite (20m)" if prom_bano > 20 else "Dentro de norma",
                    delta_color="inverse" if prom_bano > 20 else "normal"
                )
            with c_af2:
                st.metric(
                    "☕ Break / Descanso",
                    f"{prom_break:.0f} min/día",
                    f"{prom_break - 35.0:+.0f} min vs límite (35m)" if prom_break > 35 else "Dentro de norma",
                    delta_color="inverse" if prom_break > 35 else "normal"
                )
            with c_af3:
                st.metric(
                    "🍽️ Almuerzo / Lunch",
                    f"{prom_lunch:.0f} min/día",
                    "Programado" if prom_lunch > 0 else "Sin registro",
                )
            with c_af4:
                st.metric(
                    "📉 Conexión Neta",
                    f"{prom_adh:.1f}%",
                    f"-{tot_perd:.1f} hrs no prestadas",
                    delta_color="inverse" if prom_adh < 85 else "normal"
                )

            st.markdown(f"**Expediente Día a Día de `{sel_asesor_forense}` (Supervisor: {sup_asesor} | {srv_asesor}):**")

            # Diagnóstico explícito por día
            def diagnosticar_dia(row):
                if not row["muestra_valida"]:
                    return "⚪ Turno no representativo (<3h)"
                if not row["es_outlier_dia"]:
                    return "🟢 Conforme"

                fallas = []
                if row["min_bano"] > 25.0:
                    fallas.append(f"Exceso Baño ({int(row['min_bano'])}m)")
                if row["min_break"] > 40.0:
                    fallas.append(f"Exceso Break ({int(row['min_break'])}m)")
                if row["pct_adherencia"] < 75.0:
                    fallas.append(f"Baja conexión ({row['pct_adherencia']:.1f}%)")
                elif row["es_outlier_adherencia"]:
                    fallas.append(f"Adherencia bajo norma ({row['pct_adherencia']:.1f}%)")

                return "🛑 " + (" + ".join(fallas) if fallas else "Desvío estadístico del servicio")

            df_asesor_dias["Diagnóstico del Día"] = df_asesor_dias.apply(diagnosticar_dia, axis=1)

            cols_tbl = [
                "fecha", "horas_programadas", "min_conectado", "min_bano", "min_break", "min_lunch", "min_coaching",
                "pct_adherencia", "Diagnóstico del Día"
            ]

            df_show_forense = df_asesor_dias[cols_tbl].copy()
            df_show_forense["min_conectado"] = df_show_forense["min_conectado"].round(0).astype(int)
            df_show_forense["min_bano"] = df_show_forense["min_bano"].round(0).astype(int)
            df_show_forense["min_break"] = df_show_forense["min_break"].round(0).astype(int)
            df_show_forense["min_lunch"] = df_show_forense["min_lunch"].round(0).astype(int)
            df_show_forense["min_coaching"] = df_show_forense["min_coaching"].round(0).astype(int)
            df_show_forense["pct_adherencia"] = df_show_forense["pct_adherencia"].round(1)

            st.dataframe(
                df_show_forense.rename(columns={
                    "fecha": "Fecha",
                    "horas_programadas": "Horas Prog.",
                    "min_conectado": "Min Conexión",
                    "min_bano": "Baño (m)",
                    "min_break": "Break (m)",
                    "min_lunch": "Almuerzo (m)",
                    "min_coaching": "Coaching/4DX (m)",
                    "pct_adherencia": "% Adh.",
                    "Diagnóstico del Día": "📋 Veredicto de la Jornada"
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
                        💡 <b>Recomendación 4DX:</b> Focalizar compromisos 1 a 1 en los supervisores con mayor concentración de reincidencia en la gráfica contigua.
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
