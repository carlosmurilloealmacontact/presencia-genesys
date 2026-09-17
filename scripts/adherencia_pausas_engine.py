"""
Motor Analítico Unificado de Cumplimiento de Horas Laboradas y Adherencia Intradía de Pausas Programadas.
Aplica tanto para:
- ✈️ LATAM Pasajeros (todas las campañas de voz, chat, soporte y coordinaciones de pasajeros).
- 🏢 Agencias B2B (Marelyn Cardona, Andrés Rodríguez y líneas B2B).

Cruza:
1. Malla de Turnos Detallada (horas programadas, turno_ini/fin, des_1, des_2, lunch, dialogo, training).
2. Tramos reales de presencia de Genesys Cloud (tabla segments en data/presencia.db).
3. Salesforce Omni-Channel / Live Agent cuando aplica.
"""

import os
import sqlite3
from datetime import datetime, timedelta
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.normpath(os.path.join(BASE_DIR, ".."))
DB_PATH = os.path.join(PROJECT_ROOT, "data", "presencia.db")


def _get_db():
    return sqlite3.connect(DB_PATH)


def _time_to_minutes(t_str: str) -> float:
    if not t_str:
        return 0.0
    try:
        parts = str(t_str).strip().split(":")
        h = int(parts[0])
        m = int(parts[1])
        s = int(parts[2]) if len(parts) > 2 else 0
        return h * 60.0 + m + s / 60.0
    except Exception:
        return 0.0


def _minutes_to_hhmm(mins: float) -> str:
    if pd.isna(mins) or mins < 0:
        return "00:00"
    h = int(mins // 60)
    m = int(round(mins % 60))
    if m == 60:
        h += 1
        m = 0
    return f"{h:02d}:{m:02d}"


@st.cache_data(ttl=3600)
def obtener_fechas_disponibles_turnos() -> list[str]:
    """Retorna las fechas con turnos detallados en la base de datos."""
    try:
        with _get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT fecha FROM turnos_detallados ORDER BY fecha DESC;")
            return [r[0] for r in cur.fetchall()]
    except Exception:
        return []


@st.cache_data(ttl=3600)
def obtener_mapa_bp_coordinador() -> dict[str, str]:
    """Mapea cada BP a su coordinador histórico a partir de los segmentos de Genesys."""
    try:
        with _get_db() as conn:
            df = pd.read_sql_query(
                "SELECT distinct agente, coordinador FROM segments WHERE coordinador IS NOT NULL AND trim(coordinador) != ''",
                conn
            )
            if df.empty:
                return {}
            df["bp"] = df["agente"].astype(str).str.split(" - ").str[0].str.strip()
            df = df.drop_duplicates("bp", keep="last")
            return dict(zip(df["bp"], df["coordinador"]))
    except Exception:
        return {}


@st.cache_data(ttl=3600)
def obtener_coordinadores_disponibles(filtro_tipo: str = "TODOS") -> list[str]:
    """Retorna la lista ordenada de coordinadores por ámbito ('TODOS', 'PASAJEROS', 'B2B')."""
    try:
        with _get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT coordinador FROM segments WHERE coordinador IS NOT NULL AND trim(coordinador) != '' ORDER BY coordinador;")
            todos = [r[0] for r in cur.fetchall()]
    except Exception:
        todos = []

    b2b_keywords = ["CARDONA", "RODRIGUEZ URIBE"]
    if filtro_tipo == "B2B":
        return [c for c in todos if any(k in c.upper() for k in b2b_keywords)]
    elif filtro_tipo == "PASAJEROS":
        return [c for c in todos if not any(k in c.upper() for k in b2b_keywords)]
    return todos


@st.cache_data(ttl=3600)
def obtener_servicios_disponibles(filtro_tipo: str = "TODOS") -> list[str]:
    """Retorna los servicios programados por ámbito ('TODOS', 'PASAJEROS', 'B2B')."""
    try:
        with _get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT servicio FROM turnos_detallados WHERE servicio IS NOT NULL AND trim(servicio) != '' ORDER BY servicio;")
            todos = [r[0] for r in cur.fetchall()]
    except Exception:
        todos = []

    b2b_keywords = ["AGENCIA", "AGY", "CORPORATE", "PYME", "BO_CUS", "BO_WAIVERS", "BO_CORPORATE", "BO AGENCIAS", "AG CELULA", "AG CHECK", "AG CORPORATE"]
    if filtro_tipo == "B2B":
        return [s for s in todos if any(k in s.upper() for k in b2b_keywords)]
    elif filtro_tipo == "PASAJEROS":
        return [s for s in todos if not any(k in s.upper() for k in b2b_keywords)]
    return todos


def calcular_cumplimiento_horas_turno(fecha: str, coordinador: str = None, servicio: str = None, ambito: str = "TODOS") -> pd.DataFrame:
    """
    Evalúa el cumplimiento de horas de la jornada laboral:
    Horas Programadas vs Horas Reales Conectado (productivo + pausas de ley).
    Calcula: % Cumplimiento, Horas Faltantes/Sobrantes, y clasifica en semáforo.
    """
    bp_to_coord = obtener_mapa_bp_coordinador()
    coords_pasajeros = set(obtener_coordinadores_disponibles("PASAJEROS"))
    coords_b2b = set(obtener_coordinadores_disponibles("B2B"))

    with _get_db() as conn:
        query_turnos = """
            SELECT bp, fecha, documento, nombre_agente, servicio, novedad,
                   horas_programadas, turno_ini, turno_fin
            FROM turnos_detallados
            WHERE fecha = ?
        """
        params_t = [fecha]
        if servicio and servicio != "Todos los Servicios":
            query_turnos += " AND servicio = ?"
            params_t.append(servicio)

        df_t = pd.read_sql_query(query_turnos, conn, params=params_t)
        if df_t.empty:
            return pd.DataFrame()

        query_seg = """
            SELECT agente, presence_label, system_presence, inicio, fin, duracion_min, servicio, coordinador
            FROM segments
            WHERE fecha = ?
        """
        df_seg = pd.read_sql_query(query_seg, conn, params=[fecha])

    if not df_seg.empty:
        df_seg["bp"] = df_seg["agente"].astype(str).str.split(" - ").str[0].str.strip()
    else:
        df_seg["bp"] = []

    est_offline = {"Offline", "Desconectado"}
    est_pausas = {"Break", "Lunch", "Baño", "Pre Pausa", "Descanso", "Almuerzo", "Diálogo Diario / 4DX", "PCA- Diálogo", "Refuerzo Semanal", "Cursos Adicionales"}
    seg_by_bp = dict(tuple(df_seg.groupby("bp"))) if not df_seg.empty else {}

    res_list = []
    for _, row in df_t.iterrows():
        bp = str(row["bp"]).strip()
        h_prog = float(row["horas_programadas"]) if pd.notna(row["horas_programadas"]) and row["horas_programadas"] > 0 else 8.0
        nom = row["nombre_agente"] or f"Asesor BP {bp}"
        srv = row["servicio"] or "LATAM"
        t_ini = row["turno_ini"] or "--"
        t_fin = row["turno_fin"] or "--"

        sub_seg = seg_by_bp.get(bp)
        coord_real = (sub_seg["coordinador"].iloc[0] if sub_seg is not None and not sub_seg.empty and pd.notna(sub_seg["coordinador"].iloc[0]) else None) or bp_to_coord.get(bp, "")

        # Filtro por ámbito
        if ambito == "PASAJEROS":
            if coord_real and coord_real not in coords_pasajeros and coord_real in coords_b2b:
                continue
        elif ambito == "B2B":
            if coord_real and coord_real not in coords_b2b and coord_real in coords_pasajeros:
                continue

        # Filtro por coordinador específico
        if coordinador and coordinador != "Todos los Coordinadores":
            if not coord_real or coordinador.upper() not in coord_real.upper():
                continue

        if sub_seg is not None and not sub_seg.empty:
            conectados = sub_seg[~sub_seg["presence_label"].isin(est_offline)]
            min_conectado = conectados["duracion_min"].sum()
            pausas = sub_seg[sub_seg["presence_label"].isin(est_pausas)]
            min_pausas = pausas["duracion_min"].sum()
            min_productivo = max(0.0, min_conectado - min_pausas)

            h_conectado = round(min_conectado / 60.0, 2)
            h_prod = round(min_productivo / 60.0, 2)
            h_pau = round(min_pausas / 60.0, 2)

            pri_con = conectados["inicio"].min()
            ult_des = conectados["fin"].max()
            h_pri = pri_con.split(" ")[1][:5] if pd.notna(pri_con) and " " in str(pri_con) else "--"
            h_ult = ult_des.split(" ")[1][:5] if pd.notna(ult_des) and " " in str(ult_des) else "--"
        else:
            h_conectado = 0.0
            h_prod = 0.0
            h_pau = 0.0
            h_pri = "--"
            h_ult = "--"

        pct_cumpl = round((h_conectado / h_prog * 100.0), 1) if h_prog > 0 else 0.0
        brecha_h = round(h_conectado - h_prog, 2)

        if h_conectado == 0.0:
            estado = "❌ Ausente / Sin Conexión"
        elif pct_cumpl >= 98.0 or brecha_h >= -0.15:
            estado = "🟢 Cumple Jornada Completa"
        elif pct_cumpl >= 90.0:
            estado = "🟡 Déficit Leve (< 1h)"
        else:
            estado = "🔴 Déficit Severo (> 1h faltante)"

        res_list.append({
            "BP": bp,
            "Asesor": nom,
            "Coordinador": coord_real or "No Asignado",
            "Servicio": srv,
            "Turno Programado": f"{t_ini[:5]} - {t_fin[:5]}",
            "Horas Prog": round(h_prog, 2),
            "Conexión Real": f"{h_pri} - {h_ult}",
            "Horas Conectado": h_conectado,
            "Horas Productivas": h_prod,
            "Horas Pausas": h_pau,
            "% Cumplimiento": pct_cumpl,
            "Brecha Horas": brecha_h,
            "Estado": estado
        })

    df_res = pd.DataFrame(res_list)
    if not df_res.empty:
        df_res = df_res.sort_values(by=["% Cumplimiento", "Brecha Horas"], ascending=[True, True])
    return df_res


def calcular_adherencia_pausas_intradia(fecha: str, coordinador: str = None, servicio: str = None, tolerancia_min: int = 10, ambito: str = "TODOS") -> pd.DataFrame:
    """
    Audita franja a franja la puntualidad y duración de cada pausa programada:
    Descanso 1, Descanso 2, Almuerzo, Diálogo 4DX y Capacitaciones.
    Cruza el horario programado contra los eventos de presence_label en segments.
    """
    bp_to_coord = obtener_mapa_bp_coordinador()
    coords_pasajeros = set(obtener_coordinadores_disponibles("PASAJEROS"))
    coords_b2b = set(obtener_coordinadores_disponibles("B2B"))

    with _get_db() as conn:
        query_turnos = """
            SELECT bp, fecha, documento, nombre_agente, servicio,
                   turno_ini, turno_fin, dialogo_ini, dialogo_fin,
                   des_1_ini, des_1_fin, des_2_ini, des_2_fin, des_3_ini, des_3_fin,
                   lunch_ini, lunch_fin, training_1_ini, training_1_fin
            FROM turnos_detallados
            WHERE fecha = ?
        """
        params_t = [fecha]
        if servicio and servicio != "Todos los Servicios":
            query_turnos += " AND servicio = ?"
            params_t.append(servicio)

        df_t = pd.read_sql_query(query_turnos, conn, params=params_t)
        if df_t.empty:
            return pd.DataFrame()

        query_seg = """
            SELECT agente, presence_label, inicio, fin, duracion_min, servicio, coordinador
            FROM segments
            WHERE fecha = ?
        """
        df_seg = pd.read_sql_query(query_seg, conn, params=[fecha])

    if not df_seg.empty:
        df_seg["bp"] = df_seg["agente"].astype(str).str.split(" - ").str[0].str.strip()
        df_seg["t_ini_min"] = df_seg["inicio"].astype(str).str.split(" ").str[-1].apply(_time_to_minutes)
        df_seg["t_fin_min"] = df_seg["fin"].astype(str).str.split(" ").str[-1].apply(_time_to_minutes)
    else:
        df_seg["bp"] = []
        df_seg["t_ini_min"] = []
        df_seg["t_fin_min"] = []

    seg_by_bp = dict(tuple(df_seg.groupby("bp"))) if not df_seg.empty else {}

    filas_pausas = []
    tipos_pausas = [
        ("Descanso 1 (Break)", "des_1_ini", "des_1_fin", {"Break", "Baño", "Pre Pausa", "Descanso"}),
        ("Descanso 2 (Break)", "des_2_ini", "des_2_fin", {"Break", "Baño", "Pre Pausa", "Descanso"}),
        ("Almuerzo (Lunch)", "lunch_ini", "lunch_fin", {"Lunch", "Almuerzo"}),
        ("Diálogo Diario (4DX)", "dialogo_ini", "dialogo_fin", {"Diálogo Diario / 4DX", "PCA- Diálogo"}),
        ("Capacitación (Training)", "training_1_ini", "training_1_fin", {"Cursos Adicionales", "Refuerzo Semanal", "Training"})
    ]

    for _, row in df_t.iterrows():
        bp = str(row["bp"]).strip()
        nom = row["nombre_agente"] or f"Asesor BP {bp}"
        srv = row["servicio"] or "LATAM"

        sub_seg = seg_by_bp.get(bp)
        coord_real = (sub_seg["coordinador"].iloc[0] if sub_seg is not None and not sub_seg.empty and pd.notna(sub_seg["coordinador"].iloc[0]) else None) or bp_to_coord.get(bp, "")

        if ambito == "PASAJEROS":
            if coord_real and coord_real not in coords_pasajeros and coord_real in coords_b2b:
                continue
        elif ambito == "B2B":
            if coord_real and coord_real not in coords_b2b and coord_real in coords_pasajeros:
                continue

        if coordinador and coordinador != "Todos los Coordinadores":
            if not coord_real or coordinador.upper() not in coord_real.upper():
                continue

        for label_pausa, col_ini, col_fin, labels_presencia in tipos_pausas:
            h_ini_str = row.get(col_ini)
            h_fin_str = row.get(col_fin)
            if not h_ini_str or not h_fin_str or str(h_ini_str).strip() in ("00:00:00", "None", ""):
                continue

            prog_ini_min = _time_to_minutes(str(h_ini_str))
            prog_fin_min = _time_to_minutes(str(h_fin_str))
            prog_dur_min = max(0.0, prog_fin_min - prog_ini_min)
            if prog_dur_min == 0.0:
                continue

            tramo_real = None
            if sub_seg is not None and not sub_seg.empty:
                candidatos = sub_seg[sub_seg["presence_label"].isin(labels_presencia)].copy()
                if not candidatos.empty:
                    candidatos["distancia"] = (candidatos["t_ini_min"] - prog_ini_min).abs()
                    cercanos = candidatos[candidatos["distancia"] <= 90].sort_values("distancia")
                    if not cercanos.empty:
                        tramo_real = cercanos.iloc[0]

            if tramo_real is not None:
                real_ini_min = tramo_real["t_ini_min"]
                real_fin_min = tramo_real["t_fin_min"]
                real_dur_min = float(tramo_real["duracion_min"])
                
                desvio_ini_min = int(round(real_ini_min - prog_ini_min))
                exceso_dur_min = int(round(real_dur_min - prog_dur_min))
                hora_real_str = f"{_minutes_to_hhmm(real_ini_min)} - {_minutes_to_hhmm(real_fin_min)}"

                if abs(desvio_ini_min) <= tolerancia_min and exceso_dur_min <= 3:
                    estado_p = "🟢 Puntual y en tiempo"
                elif abs(desvio_ini_min) <= tolerancia_min and exceso_dur_min > 3:
                    estado_p = f"🔴 Exceso de Tiempo (+{exceso_dur_min} min)"
                elif abs(desvio_ini_min) > tolerancia_min and exceso_dur_min <= 3:
                    estado_p = f"🟡 Desfasada en horario ({desvio_ini_min:+d} min)"
                else:
                    estado_p = f"🔴 Desfasada ({desvio_ini_min:+d}m) y con Exceso (+{exceso_dur_min}m)"
            else:
                hora_real_str = "--"
                real_dur_min = 0.0
                desvio_ini_min = None
                exceso_dur_min = None
                estado_p = "❌ Pausa No Tomada en Ventana"

            filas_pausas.append({
                "BP": bp,
                "Asesor": nom,
                "Coordinador": coord_real or "No Asignado",
                "Servicio": srv,
                "Tipo Pausa": label_pausa,
                "Horario Programado": f"{str(h_ini_str)[:5]} - {str(h_fin_str)[:5]}",
                "Duración Prog": f"{int(round(prog_dur_min))} min",
                "Horario Real": hora_real_str,
                "Duración Real": f"{int(round(real_dur_min))} min" if real_dur_min > 0 else "--",
                "Desvío Salida": f"{desvio_ini_min:+d} min" if desvio_ini_min is not None else "--",
                "Exceso": f"{exceso_dur_min:+d} min" if exceso_dur_min is not None else "--",
                "Estado": estado_p
            })

    df_p = pd.DataFrame(filas_pausas)
    return df_p


# ── RENDERERS STREAMLIT REUTILIZABLES ─────────────────────────────────────────

def render_ui_cumplimiento_horas(ambito: str = "PASAJEROS", key_prefix: str = "pas_turno_"):
    """Renderiza la visual interactiva de cumplimiento de horas de turno."""
    fechas_disp = obtener_fechas_disponibles_turnos()
    if not fechas_disp:
        st.warning("⚠️ No se encontraron turnos detallados en la base de datos.")
        return

    coords_disp = ["Todos los Coordinadores"] + obtener_coordinadores_disponibles(ambito)
    servs_disp = ["Todos los Servicios"] + obtener_servicios_disponibles(ambito)

    c_f1, c_f2, c_f3, c_f4, c_f5 = st.columns([1.1, 1.4, 1.3, 1.4, 1.4])
    with c_f1:
        fecha_sel = st.selectbox("📅 Fecha a Auditar", options=fechas_disp, index=0, key=f"{key_prefix}fecha")
    with c_f2:
        coord_sel = st.selectbox("👤 Coordinación", options=coords_disp, index=0, key=f"{key_prefix}coord")
    with c_f3:
        serv_sel = st.selectbox("🏢 Servicio / Cola", options=servs_disp, index=0, key=f"{key_prefix}serv")
    with c_f4:
        estado_sel = st.selectbox(
            "🚦 Filtro Estado",
            options=["Todos los Estados", "🟢 Cumple Jornada Completa", "🟡 Déficit Leve (< 1h)", "🔴 Déficit Severo (> 1h faltante)", "❌ Ausente / Sin Conexión"],
            index=0,
            key=f"{key_prefix}est"
        )
    with c_f5:
        search_asesor = st.text_input("🔍 Buscar Asesor / BP", key=f"{key_prefix}search").strip().lower()

    # Ejecución
    df_horas = calcular_cumplimiento_horas_turno(fecha_sel, coordinador=coord_sel, servicio=serv_sel, ambito=ambito)
    if df_horas.empty:
        st.info(f"No hay registros de turnos o conexión para la fecha **{fecha_sel}** con los filtros aplicados.")
        return

    if estado_sel != "Todos los Estados":
        df_horas = df_horas[df_horas["Estado"] == estado_sel]
    if search_asesor:
        df_horas = df_horas[
            df_horas["Asesor"].astype(str).str.lower().str.contains(search_asesor) |
            df_horas["BP"].astype(str).str.lower().str.contains(search_asesor)
        ]

    if df_horas.empty:
        st.warning("No hay registros que coincidan con los filtros o búsqueda.")
        return

    # Métricas superiores
    total_asesores = len(df_horas)
    pct_prom_cumpl = round(df_horas["% Cumplimiento"].mean(), 1)
    cumplen_tot = int((df_horas["Estado"] == "🟢 Cumple Jornada Completa").sum())
    deficit_tot = int(df_horas["Estado"].isin(["🟡 Déficit Leve (< 1h)", "🔴 Déficit Severo (> 1h faltante)"]).sum())
    ausentes_tot = int((df_horas["Estado"] == "❌ Ausente / Sin Conexión").sum())
    horas_deficit = round(abs(df_horas[df_horas["Brecha Horas"] < 0]["Brecha Horas"].sum()), 1)

    kp1, kp2, kp3, kp4, kp5 = st.columns(5)
    with kp1:
        st.metric("Asesores Programados", total_asesores)
    with kp2:
        st.metric("% Cumplimiento Promedio", f"{pct_prom_cumpl}%", delta=f"{round(pct_prom_cumpl - 100, 1)}% vs 100%")
    with kp3:
        st.metric("🟢 Cumplen Jornada", cumplen_tot, delta=f"{round(cumplen_tot/max(1,total_asesores)*100, 1)}%")
    with kp4:
        st.metric("⚠️ En Déficit de Horas", deficit_tot, delta=f"-{horas_deficit} h faltantes", delta_color="inverse")
    with kp5:
        st.metric("❌ Sin Conexión", ausentes_tot)

    st.write("")

    col_g1, col_g2 = st.columns([1, 1.4])
    with col_g1:
        st.markdown("##### 🎯 Distribución de Cumplimiento")
        dist_estados = df_horas["Estado"].value_counts().reset_index()
        dist_estados.columns = ["Estado", "Cantidad"]
        fig_pie = px.pie(
            dist_estados,
            names="Estado",
            values="Cantidad",
            hole=0.45,
            color="Estado",
            color_discrete_map={
                "🟢 Cumple Jornada Completa": "#10b981",
                "🟡 Déficit Leve (< 1h)": "#f59e0b",
                "🔴 Déficit Severo (> 1h faltante)": "#ef4444",
                "❌ Ausente / Sin Conexión": "#64748b"
            }
        )
        fig_pie.update_layout(height=280, margin=dict(l=10, r=10, t=10, b=10))
        st.plotly_chart(fig_pie, use_container_width=True)

    with col_g2:
        st.markdown("##### 🚨 Top 10 Asesores con Mayor Déficit de Horas")
        df_deficit = df_horas[df_horas["Brecha Horas"] < 0].sort_values("Brecha Horas").head(10).copy()
        if not df_deficit.empty:
            df_deficit["Horas Faltantes"] = df_deficit["Brecha Horas"].abs()
            fig_bar_def = px.bar(
                df_deficit,
                x="Horas Faltantes",
                y="Asesor",
                orientation="h",
                text="Horas Faltantes",
                color="Horas Faltantes",
                color_continuous_scale="Reds"
            )
            fig_bar_def.update_traces(texttemplate="%{text:.2f} h", textposition="outside")
            fig_bar_def.update_layout(
                height=280,
                margin=dict(l=10, r=10, t=10, b=10),
                yaxis=dict(autorange="reversed"),
                coloraxis_showscale=False
            )
            st.plotly_chart(fig_bar_def, use_container_width=True)
        else:
            st.success("🎉 ¡Excelente! Ningún asesor presenta déficit de horas en esta selección.")

    st.write("")
    st.markdown("##### 📋 Auditoría Detallada Asesor por Asesor")
    st.caption("Muestra la jornada oficial programada contra el tiempo real de presencia segundo a segundo extraído de Genesys.")

    column_cfg_horas = {
        "% Cumplimiento": st.column_config.ProgressColumn(
            "% Cumplimiento",
            help="Porcentaje de horas de conexión logradas vs horas de turno programadas",
            format="%.1f%%",
            min_value=0,
            max_value=120
        ),
        "Horas Prog": st.column_config.NumberColumn("Horas Prog", format="%.2f h"),
        "Horas Conectado": st.column_config.NumberColumn("Horas Conectado", format="%.2f h"),
        "Horas Productivas": st.column_config.NumberColumn("Horas Prod", format="%.2f h"),
        "Horas Pausas": st.column_config.NumberColumn("Horas Pausas", format="%.2f h"),
        "Brecha Horas": st.column_config.NumberColumn("Brecha", format="%.2f h"),
    }
    st.dataframe(df_horas, column_config=column_cfg_horas, use_container_width=True, hide_index=True)

    csv_h = df_horas.to_csv(index=False).encode('utf-8-sig')
    st.download_button(
        label=f"📥 Descargar Reporte de Cumplimiento de Horas ({ambito}) (CSV)",
        data=csv_h,
        file_name=f"cumplimiento_horas_{ambito.lower()}_{fecha_sel}.csv",
        mime="text/csv",
        key=f"{key_prefix}btn_dl"
    )


def render_ui_adherencia_pausas(ambito: str = "PASAJEROS", key_prefix: str = "pas_pausa_"):
    """Renderiza la visual interactiva de adherencia intradía de pausas programadas."""
    fechas_disp = obtener_fechas_disponibles_turnos()
    if not fechas_disp:
        st.warning("⚠️ No se encontraron turnos detallados en la base de datos.")
        return

    coords_disp = ["Todos los Coordinadores"] + obtener_coordinadores_disponibles(ambito)
    servs_disp = ["Todos los Servicios"] + obtener_servicios_disponibles(ambito)

    c_p1, c_p2, c_p3, c_p4, c_p5 = st.columns([1.1, 1.4, 1.3, 1.4, 1.4])
    with c_p1:
        fecha_sel = st.selectbox("📅 Fecha a Auditar", options=fechas_disp, index=0, key=f"{key_prefix}fecha")
    with c_p2:
        coord_sel = st.selectbox("👤 Coordinación", options=coords_disp, index=0, key=f"{key_prefix}coord")
    with c_p3:
        serv_sel = st.selectbox("🏢 Servicio / Cola", options=servs_disp, index=0, key=f"{key_prefix}serv")
    with c_p4:
        tipo_p_sel = st.selectbox(
            "☕ Tipo de Pausa",
            options=["Todas las Pausas", "Descanso 1 (Break)", "Descanso 2 (Break)", "Almuerzo (Lunch)", "Diálogo Diario (4DX)", "Capacitación (Training)"],
            index=0,
            key=f"{key_prefix}tipo"
        )
    with c_p5:
        search_asesor_p = st.text_input("🔍 Buscar Asesor / BP", key=f"{key_prefix}search").strip().lower()

    df_pausas = calcular_adherencia_pausas_intradia(fecha_sel, coordinador=coord_sel, servicio=serv_sel, ambito=ambito)
    if df_pausas.empty:
        st.info(f"No hay registros de pausas programadas para la fecha **{fecha_sel}** con los filtros aplicados.")
        return

    if tipo_p_sel != "Todas las Pausas":
        df_pausas = df_pausas[df_pausas["Tipo Pausa"] == tipo_p_sel]
    if search_asesor_p:
        df_pausas = df_pausas[
            df_pausas["Asesor"].astype(str).str.lower().str.contains(search_asesor_p) |
            df_pausas["BP"].astype(str).str.lower().str.contains(search_asesor_p)
        ]

    if df_pausas.empty:
        st.warning("No hay pausas que coincidan con los filtros seleccionados.")
        return

    # Métricas de puntualidad
    tot_p = len(df_pausas)
    puntuales_p = int(df_pausas["Estado"].str.startswith("🟢").sum())
    desfasadas_p = int(df_pausas["Estado"].str.startswith("🟡").sum())
    excesos_p = int(df_pausas["Estado"].str.startswith("🔴").sum())
    no_tomadas_p = int(df_pausas["Estado"].str.startswith("❌").sum())
    pct_puntual = round(puntuales_p / max(1, tot_p) * 100, 1)

    kp1, kp2, kp3, kp4, kp5 = st.columns(5)
    with kp1:
        st.metric("Pausas Programadas", tot_p)
    with kp2:
        st.metric("% Puntualidad & Adherencia", f"{pct_puntual}%", delta=f"{round(pct_puntual - 85.0, 1)}% vs Meta 85%")
    with kp3:
        st.metric("🟢 Puntuales en Tiempo", puntuales_p, delta=f"{round(puntuales_p/max(1,tot_p)*100, 1)}%")
    with kp4:
        st.metric("🟡 Desfasadas de Horario", desfasadas_p, delta="Salida anticipada / tardía", delta_color="inverse")
    with kp5:
        st.metric("🔴 Con Exceso de Tiempo", excesos_p, delta=f"{no_tomadas_p} no tomadas", delta_color="inverse")

    st.write("")

    st.markdown("##### 📊 Adherencia por Tipo de Pausa")
    df_p_grp = df_pausas.groupby(["Tipo Pausa", "Estado"]).size().reset_index(name="Cantidad")
    fig_bar_p = px.bar(
        df_p_grp,
        x="Tipo Pausa",
        y="Cantidad",
        color="Estado",
        barmode="stack",
        color_discrete_map={
            "🟢 Puntual y en tiempo": "#10b981",
            "🟡 Desfasada en horario": "#f59e0b",
            "🔴 Exceso de Tiempo": "#ef4444",
            "❌ Pausa No Tomada en Ventana": "#64748b"
        }
    )
    fig_bar_p.update_layout(height=290, margin=dict(l=10, r=10, t=10, b=10))
    st.plotly_chart(fig_bar_p, use_container_width=True)

    st.write("")
    st.markdown("##### 📋 Detalle Intradía de Pausas Programadas vs Reales")
    st.caption("Tolerancia permitida de inicio: ±10 minutos. Evalúa si la persona salió a su franja y si excedió el tiempo reglamentario.")
    st.dataframe(df_pausas, use_container_width=True, hide_index=True)

    csv_p = df_pausas.to_csv(index=False).encode('utf-8-sig')
    st.download_button(
        label=f"📥 Descargar Reporte de Adherencia a Pausas ({ambito}) (CSV)",
        data=csv_p,
        file_name=f"adherencia_pausas_{ambito.lower()}_{fecha_sel}.csv",
        mime="text/csv",
        key=f"{key_prefix}btn_dl"
    )


def render_subtab_pausas_pasajeros(render_tab_historico_fn=None):
    """
    Submódulo integral de Pausas, Adherencia y Horas de Turno para LATAM Pasajeros.
    """
    st.markdown("### ⏸️ Pausas, Adherencia y Cumplimiento de Turno — LATAM Pasajeros")
    st.caption("Auditoría de cumplimiento de jornada laboral contratada y puntualidad de descansos intradía (Descansos 1 y 2, Lunch, Diálogo 4DX y Capacitaciones).")

    SUB_PAUSAS_PASAJEROS = [
        "⏱️ Cumplimiento de Horas de Turno",
        "☕ Adherencia a Pausas Programadas (Intradía)",
        "📊 Histórico y Fuga de Estados Genesys"
    ]
    sel_sub = st.segmented_control(
        "Módulo de Cumplimiento Pasajeros",
        options=SUB_PAUSAS_PASAJEROS,
        default=SUB_PAUSAS_PASAJEROS[0],
        key="sub_pasajeros_pausas_activo",
        label_visibility="collapsed"
    )
    if not sel_sub:
        sel_sub = SUB_PAUSAS_PASAJEROS[0]

    st.write("")

    if sel_sub == "⏱️ Cumplimiento de Horas de Turno":
        render_ui_cumplimiento_horas(ambito="PASAJEROS", key_prefix="pasajeros_turno_")
    elif sel_sub == "☕ Adherencia a Pausas Programadas (Intradía)":
        render_ui_adherencia_pausas(ambito="PASAJEROS", key_prefix="pasajeros_pausa_")
    elif sel_sub == "📊 Histórico y Fuga de Estados Genesys":
        if render_tab_historico_fn:
            render_tab_historico_fn(key_prefix="pasajeros_pausas_hist_")
        else:
            st.info("Cargando motor de pausas de Genesys...")
