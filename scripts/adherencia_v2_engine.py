"""
Motor Analítico Rediseñado: Adherencia & Pausas 2.0 (Lab Experimental).
Propuesta de interfaz unificada, limpia, no saturada y orientada a decisiones operativas.

Características clave:
1. Ámbitos unificados: Alterna entre ✈️ LATAM Pasajeros y 🏢 Agencias B2B con un selector.
2. Dos modos de análisis complementarios:
   - 📅 Vista Diaria (Auditoría Intradía): 3 KPIs ejecutivos, tabla semáforo limpia e inspector franja a franja.
   - 📈 Tendencia Multidía (Consolidado): KPIs de período, Panel de Foco "Top 5 Desviaciones", evolutivo diario y tabla acumulada.
3. Cero saturación: agrupa micro-estados y métricas redundantes en indicadores de impacto real.
"""

import os
import sqlite3
from datetime import datetime, timedelta
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from adherencia_pausas_engine import (
    obtener_fechas_disponibles_turnos,
    obtener_coordinadores_disponibles,
    obtener_supervisores_disponibles,
    obtener_servicios_disponibles,
    obtener_mapa_bp_jerarquia,
    obtener_bps_b2b_y_cargo,
    calcular_auditoria_integral_unificada,
    calcular_adherencia_pausas_intradia,
    _get_db,
    _minutes_to_hhmm,
    _time_to_minutes
)

try:
    from exclusion_list import es_persona_excluida
except ImportError:
    def es_persona_excluida(val):
        return False


def _obtener_rango_fechas_segments():
    """Retorna fecha mínima y máxima con datos de presencia."""
    try:
        with _get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT MIN(fecha), MAX(fecha) FROM segments;")
            row = cur.fetchone()
            if row and row[0] and row[1]:
                return row[0], row[1]
    except Exception:
        pass
    return None, None


def render_tab_adherencia_v2(agentes_map: dict = None, current_email: str = ""):
    """Punto de entrada principal para el módulo experimental 2.0."""
    
    # ── ENCABEZADO Y BANNER EXPERIMENTAL ─────────────────────────────────────
    st.markdown(
        """
        <div style="background: linear-gradient(90deg, #0f172a 0%, #1e293b 100%); padding: 14px 20px; border-radius: 12px; margin-bottom: 15px; border-left: 5px solid #8b5cf6;">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
                <div>
                    <h3 style="color: #ffffff; margin: 0 0 3px 0; font-size: 19px;">🧪 Adherencia & Pausas 2.0 (Lab)</h3>
                    <p style="color: #cbd5e1; margin: 0; font-size: 12.5px;">
                        Laboratorio Experimental • Propuesta de interfaz consolidada, limpia y desaturada con foco en acción inmediata
                    </p>
                </div>
                <div style="text-align: right; background: #334155; padding: 5px 12px; border-radius: 8px; border: 1px solid #475569;">
                    <span style="color: #a78bfa; font-size: 10px; font-weight: 700; text-transform: uppercase;">Prototipo Exclusivo</span><br>
                    <span style="color: #f1f5f9; font-size: 11.5px; font-weight: 600;">Carlos Murillo</span>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    # ── SELECTORES SUPERIORES: ÁMBITO Y MODO DE ANÁLISIS ─────────────────────
    c_sel1, c_sel2 = st.columns([1.1, 1.9])
    
    with c_sel1:
        ambito_sel = st.segmented_control(
            "Ámbito Operativo",
            options=["✈️ Pasajeros", "🏢 Agencias B2B"],
            default="✈️ Pasajeros",
            key="v2_lab_ambito",
            label_visibility="collapsed"
        )
        if not ambito_sel:
            ambito_sel = "✈️ Pasajeros"
            
    with c_sel2:
        opciones_modo = ["📅 Vista Diaria (Auditoría Intradía)", "📈 Tendencia Multidía (Consolidado)"]
        if ambito_code == "PASAJEROS":
            opciones_modo.append("💬 Simultaneidad WhatsApp")
        modo_sel = st.segmented_control(
            "Modo de Análisis",
            options=opciones_modo,
            default="📅 Vista Diaria (Auditoría Intradía)",
            key="v2_lab_modo",
            label_visibility="collapsed"
        )
        if not modo_sel:
            modo_sel = "📅 Vista Diaria (Auditoría Intradía)"

    ambito_code = "B2B" if "B2B" in ambito_sel else "PASAJEROS"

    st.write("")

    # Enrutar según el modo seleccionado
    if modo_sel == "📅 Vista Diaria (Auditoría Intradía)":
        _render_vista_diaria(ambito_code, ambito_sel)
    elif modo_sel == "📈 Tendencia Multidía (Consolidado)":
        _render_vista_multidia(ambito_code, ambito_sel)
    elif modo_sel == "💬 Simultaneidad WhatsApp":
        from live_engine import obtener_token_genesys
        from whatsapp_simultaneidad_engine import render_panel_simultaneidad_whatsapp_historico
        token = obtener_token_genesys()
        if not token:
            st.warning("⚠️ No se encontró token activo de Genesys Cloud.")
        else:
            col_f, col_c = st.columns([1.2, 2.5])
            with col_f:
                f_sel = st.date_input("Fecha de Auditoría", value=datetime.now().date(), key="v2_wsp_fecha_sel")
            with col_c:
                mapa_ag = agentes_map if agentes_map else {}
                coords = sorted(list(set(v.get("coordinador", "") for v in mapa_ag.values() if v.get("coordinador"))))
                c_sel = st.selectbox("Filtrar por Coordinador", options=["TODOS"] + coords, key="v2_wsp_coord_sel")

            render_panel_simultaneidad_whatsapp_historico(token, f_sel, mapa_ag, coordinador_filtro=c_sel)


# ==============================================================================
# 1. VISTA DIARIA (AUDITORÍA INTRADÍA DESATURADA)
# ==============================================================================
def _render_vista_diaria(ambito_code: str, ambito_label: str):
    fechas_turnos = obtener_fechas_disponibles_turnos()
    if not fechas_turnos:
        st.warning("⚠️ No se encontraron turnos en la base de datos.")
        return

    # Fecha por defecto: ayer si existe, de lo contrario la última disponible
    hoy_str = datetime.now().strftime("%Y-%m-%d")
    ayer_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    def_idx = 0
    if ayer_str in fechas_turnos:
        def_idx = fechas_turnos.index(ayer_str)
    elif hoy_str in fechas_turnos:
        def_idx = fechas_turnos.index(hoy_str)

    # ── BARRA DE FILTROS COMPACTA (1 FILA) ───────────────────────────────────
    c_f1, c_f2, c_f3, c_f4, c_f5 = st.columns([1.2, 1.4, 1.4, 1.4, 1.1])
    
    with c_f1:
        fecha_sel = st.selectbox(
            "📅 Fecha",
            options=fechas_turnos,
            index=def_idx,
            key=f"v2_diaria_fecha_{ambito_code}"
        )
    
    with c_f2:
        coords = ["Todos los Coordinadores"] + obtener_coordinadores_disponibles(ambito_code)
        coord_sel = st.selectbox(
            "👔 Coordinador",
            options=coords,
            key=f"v2_diaria_coord_{ambito_code}"
        )

    with c_f3:
        supervisores = ["Todos los Supervisores"] + obtener_supervisores_disponibles(
            coordinador=coord_sel if coord_sel != "Todos los Coordinadores" else None,
            ambito=ambito_code
        )
        superv_sel = st.selectbox(
            "👤 Supervisor",
            options=supervisores,
            key=f"v2_diaria_superv_{ambito_code}"
        )

    with c_f4:
        servicios = ["Todos los Servicios"] + obtener_servicios_disponibles(ambito_code)
        serv_sel = st.selectbox(
            "🏷️ Servicio",
            options=servicios,
            key=f"v2_diaria_serv_{ambito_code}"
        )

    with c_f5:
        estado_turno_filtro = st.selectbox(
            "🚥 Estado",
            options=["Todos", "Cumple", "Conectado en Salesforce", "Déficit", "Exceso Pausas", "Ausente"],
            key=f"v2_diaria_est_{ambito_code}"
        )

    # Cargar datos unificados del día
    with st.spinner("Calculando cumplimiento y adherencia..."):
        df_unif, df_pausas = calcular_auditoria_integral_unificada(
            fecha=fecha_sel,
            coordinador=coord_sel if coord_sel != "Todos los Coordinadores" else None,
            supervisor=superv_sel if superv_sel != "Todos los Supervisores" else None,
            servicio=serv_sel if serv_sel != "Todos los Servicios" else None,
            ambito=ambito_code
        )

    if df_unif.empty:
        st.info(f"No hay registros de turnos para {ambito_label} en la fecha {fecha_sel} con los filtros seleccionados.")
        return

    # Filtro opcional por estado
    if estado_turno_filtro == "Cumple":
        df_unif = df_unif[df_unif["Estado Turno"].str.contains("Cumple|Salesforce", case=False, na=False)]
    elif estado_turno_filtro == "Conectado en Salesforce":
        df_unif = df_unif[df_unif["Estado Turno"].str.contains("Salesforce", case=False, na=False)]
    elif estado_turno_filtro == "Déficit":
        df_unif = df_unif[df_unif["Estado Turno"].str.contains("Déficit", case=False, na=False)]
    elif estado_turno_filtro == "Exceso Pausas":
        df_unif = df_unif[df_unif["Minutos Exceso"].apply(lambda x: int(str(x).replace(" min", "").strip() or 0) > 0)]
    elif estado_turno_filtro == "Ausente":
        df_unif = df_unif[df_unif["Estado Turno"].str.contains("Ausente|Sin Conexión", case=False, na=False)]

    if df_unif.empty:
        st.info("Sin asesores para el estado de turno seleccionado.")
        return

    # ── 3 KPIS EJECUTIVOS LIMPIOS (FOCO REAL) ────────────────────────────────
    tot_asesores = len(df_unif)
    h_prog_tot = df_unif["Horas Prog"].sum()
    h_con_tot = df_unif["Horas Conectado"].sum()
    pct_cumpl_jornada = round((h_con_tot / h_prog_tot * 100), 1) if h_prog_tot > 0 else 0.0

    p_prog_tot = df_unif["Pausas Prog"].sum()
    p_punt_tot = df_unif["Pausas Puntuales"].sum()
    pct_adh_pausas = round((p_punt_tot / p_prog_tot * 100), 1) if p_prog_tot > 0 else 0.0

    minutos_exceso_tot = df_unif["Minutos Exceso"].apply(lambda x: int(str(x).replace(" min", "").strip() or 0)).sum()
    horas_exceso_tot = round(minutos_exceso_tot / 60.0, 1)

    k1, k2, k3 = st.columns(3)
    with k1:
        st.markdown(
            f"""
            <div style="background:#ffffff; border-radius:10px; padding:14px 18px; border:1px solid #e2e8f0; border-left:5px solid #10b981; box-shadow:0 1px 3px rgba(0,0,0,0.04);">
                <div style="font-size:12px; color:#64748b; font-weight:700; text-transform:uppercase;">⏱️ Cumplimiento de Turno</div>
                <div style="font-size:28px; font-weight:800; color:#0f172a; margin:4px 0;">{pct_cumpl_jornada}%</div>
                <div style="font-size:12px; color:#64748b;">
                    <b>{h_con_tot:.1f}h</b> reales de <b>{h_prog_tot:.1f}h</b> programadas • {tot_asesores} asesores
                </div>
            </div>
            """,
            unsafe_allow_html=True
        )

    with k2:
        color_adh = "#10b981" if pct_adh_pausas >= 85.0 else ("#f59e0b" if pct_adh_pausas >= 75.0 else "#ef4444")
        st.markdown(
            f"""
            <div style="background:#ffffff; border-radius:10px; padding:14px 18px; border:1px solid #e2e8f0; border-left:5px solid {color_adh}; box-shadow:0 1px 3px rgba(0,0,0,0.04);">
                <div style="font-size:12px; color:#64748b; font-weight:700; text-transform:uppercase;">🎯 Puntualidad en Descansos</div>
                <div style="font-size:28px; font-weight:800; color:{color_adh}; margin:4px 0;">{pct_adh_pausas}%</div>
                <div style="font-size:12px; color:#64748b;">
                    <b>{p_punt_tot}</b> de <b>{p_prog_tot}</b> pausas en su franja reglamentaria (Meta: 85%)
                </div>
            </div>
            """,
            unsafe_allow_html=True
        )

    with k3:
        color_fuga = "#ef4444" if horas_exceso_tot > 5 else ("#f59e0b" if horas_exceso_tot > 1 else "#10b981")
        st.markdown(
            f"""
            <div style="background:#ffffff; border-radius:10px; padding:14px 18px; border:1px solid #e2e8f0; border-left:5px solid {color_fuga}; box-shadow:0 1px 3px rgba(0,0,0,0.04);">
                <div style="font-size:12px; color:#64748b; font-weight:700; text-transform:uppercase;">🚨 Tiempo de Fuga por Excesos</div>
                <div style="font-size:28px; font-weight:800; color:{color_fuga}; margin:4px 0;">{horas_exceso_tot} h</div>
                <div style="font-size:12px; color:#64748b;">
                    <b>{minutos_exceso_tot:,} min</b> acumulados fuera de pauta en descansos
                </div>
            </div>
            """,
            unsafe_allow_html=True
        )

    st.write("")

    # ── TABLA MAESTRA SEMÁFORO (UNA FILA POR ASESOR) ─────────────────────────
    st.markdown("##### 📋 Cumplimiento de Jornada y Pausas por Asesor")
    st.caption("Selecciona cualquier asesor en la tabla para inspeccionar abajo sus pausas franja a franja (Descanso 1, 2, Lunch, Diálogo y Capacitaciones).")

    cols_ver = [
        "BP", "Asesor", "Supervisor", "Servicio", "Turno Programado",
        "Conexión Real", "% Cumplimiento", "Brecha Horas", "Estado Turno",
        "% Adh Pausas", "Minutos Exceso", "Resumen Pausas"
    ]
    df_ver = df_unif[[c for c in cols_ver if c in df_unif.columns]].copy()

    cfg_unif = {
        "% Cumplimiento": st.column_config.ProgressColumn(
            "% Turno",
            format="%.1f%%",
            min_value=0,
            max_value=120
        ),
        "% Adh Pausas": st.column_config.ProgressColumn(
            "% Adh Pausas",
            format="%.1f%%",
            min_value=0,
            max_value=100
        ),
        "Brecha Horas": st.column_config.NumberColumn("Brecha", format="%.2f h"),
    }

    event = st.dataframe(
        df_ver,
        column_config=cfg_unif,
        use_container_width=True,
        hide_index=True,
        selection_mode="single-row",
        on_select="rerun",
        key=f"v2_tbl_diaria_{ambito_code}"
    )

    # ── INSPECTOR INTERACTIVO FRANJA A FRANJA ────────────────────────────────
    bp_seleccionado = None
    nom_seleccionado = None
    row_sel = None

    if event and hasattr(event, "selection") and event.selection and event.selection.get("rows"):
        idx_sel = event.selection["rows"][0]
        if idx_sel < len(df_ver):
            row_sel = df_ver.iloc[idx_sel]
            bp_seleccionado = str(row_sel["BP"]).strip()
            nom_seleccionado = str(row_sel["Asesor"]).strip()

    st.write("")
    c_insp1, c_insp2 = st.columns([2.2, 1.8], vertical_alignment="center")
    with c_insp1:
        st.markdown("##### 🔍 Detalle Franja a Franja del Asesor")
    with c_insp2:
        opciones_dropdown = ["-- Seleccionar asesor de la lista --"] + [f"{r['Asesor']} (BP {r['BP']})" for _, r in df_ver.iterrows()]
        default_drop = 0
        if bp_seleccionado:
            for i_opc, t_opc in enumerate(opciones_dropdown):
                if f"(BP {bp_seleccionado})" in t_opc:
                    default_drop = i_opc
                    break
        dd_sel = st.selectbox(
            "Buscar asesor",
            options=opciones_dropdown,
            index=default_drop,
            key=f"v2_dd_asesor_{ambito_code}",
            label_visibility="collapsed"
        )
        if dd_sel != "-- Seleccionar asesor de la lista --":
            bp_extraido = dd_sel.split("(BP ")[-1].replace(")", "").strip()
            bp_seleccionado = bp_extraido
            sub = df_ver[df_ver["BP"] == bp_seleccionado]
            if not sub.empty:
                row_sel = sub.iloc[0]
                nom_seleccionado = str(row_sel["Asesor"]).strip()

    if bp_seleccionado and row_sel is not None:
        st.markdown(
            f"""
            <div style="background: #1e293b; color: white; padding: 12px 18px; border-radius: 8px; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap;">
                <div>
                    <span style="font-size: 15px; font-weight: 700;">👤 {nom_seleccionado}</span>
                    <span style="color: #94a3b8; font-size: 12px; margin-left: 8px;">BP: {bp_seleccionado}</span><br>
                    <span style="color: #cbd5e1; font-size: 12px;">Turno: <b>{row_sel.get('Turno Programado', '--')}</b> • Conexión: <b>{row_sel.get('Conexión Real', '--')}</b> ({row_sel.get('Estado Turno', '')})</span>
                </div>
                <div style="text-align: right;">
                    <span style="background: #334155; padding: 4px 10px; border-radius: 6px; font-size: 12px; font-weight: 600; color: #38bdf8;">
                        Exceso: {row_sel.get('Minutos Exceso', '0 min')}
                    </span>
                </div>
            </div>
            """,
            unsafe_allow_html=True
        )

        df_p_indiv = df_pausas[df_pausas["BP"] == bp_seleccionado].copy()
        if not df_p_indiv.empty:
            cols_indiv = [
                "Tipo Pausa", "Horario Programado", "Duración Prog",
                "Horario Real", "Duración Real", "Desvío Salida", "Exceso", "Estado"
            ]
            df_p_indiv = df_p_indiv[[c for c in cols_indiv if c in df_p_indiv.columns]]
            st.dataframe(df_p_indiv, use_container_width=True, hide_index=True)
        else:
            st.info(f"El colaborador {nom_seleccionado} no registra pausas programadas para esta fecha.")
    else:
        st.caption("💡 Haz clic en una fila de la tabla superior o usa el buscador de la derecha para inspeccionar el detalle intradía de pausas.")


# ==============================================================================
# 2. VISTA TENDENCIA MULTIDÍA (CONSOLIDADO HISTÓRICO Y FOCO)
# ==============================================================================
def _render_vista_multidia(ambito_code: str, ambito_label: str):
    f_min, f_max = _obtener_rango_fechas_segments()
    if not f_min or not f_max:
        st.warning("⚠️ No hay tramos de presencia cargados en la base de datos.")
        return

    d_max = pd.Timestamp(f_max).date()
    d_min = pd.Timestamp(f_min).date()

    # ── SELECTOR DE RANGO Y BOTONES RÁPIDOS ──────────────────────────────────
    k_d_desde = f"v2_multi_desde_{ambito_code}"
    k_d_hasta = f"v2_multi_hasta_{ambito_code}"

    if k_d_desde not in st.session_state:
        st.session_state[k_d_desde] = max(d_min, d_max - timedelta(days=6))
        st.session_state[k_d_hasta] = d_max

    c_b1, c_b2, c_b3, c_b4, c_date1, c_date2 = st.columns([0.8, 0.8, 0.8, 0.8, 1.2, 1.2])

    def _set_rango_dias(num_dias):
        st.session_state[k_d_hasta] = d_max
        st.session_state[k_d_desde] = max(d_min, d_max - timedelta(days=num_dias - 1)) if num_dias else d_min

    with c_b1:
        st.button("7 Días", key=f"v2_btn7_{ambito_code}", on_click=_set_rango_dias, args=(7,), use_container_width=True)
    with c_b2:
        st.button("14 Días", key=f"v2_btn14_{ambito_code}", on_click=_set_rango_dias, args=(14,), use_container_width=True)
    with c_b3:
        st.button("30 Días", key=f"v2_btn30_{ambito_code}", on_click=_set_rango_dias, args=(30,), use_container_width=True)
    with c_b4:
        st.button("Todo", key=f"v2_btntodo_{ambito_code}", on_click=_set_rango_dias, args=(None,), use_container_width=True)

    with c_date1:
        st.date_input("Desde", key=k_d_desde, min_value=d_min, max_value=d_max)
    with c_date2:
        st.date_input("Hasta", key=k_d_hasta, min_value=d_min, max_value=d_max)

    fecha_ini = str(st.session_state[k_d_desde])
    fecha_fin = str(st.session_state[k_d_hasta])

    if fecha_ini > fecha_fin:
        st.error("La fecha 'Desde' no puede ser posterior a 'Hasta'.")
        return

    # Filtros jerárquicos
    c_f1, c_f2, c_f3 = st.columns(3)
    with c_f1:
        coords = ["Todos los Coordinadores"] + obtener_coordinadores_disponibles(ambito_code)
        coord_sel = st.selectbox("👔 Coordinador", options=coords, key=f"v2_multi_coord_{ambito_code}")
    with c_f2:
        supervisores = ["Todos los Supervisores"] + obtener_supervisores_disponibles(
            coordinador=coord_sel if coord_sel != "Todos los Coordinadores" else None,
            ambito=ambito_code
        )
        superv_sel = st.selectbox("👤 Supervisor", options=supervisores, key=f"v2_multi_superv_{ambito_code}")
    with c_f3:
        servicios = ["Todos los Servicios"] + obtener_servicios_disponibles(ambito_code)
        serv_sel = st.selectbox("🏷️ Servicio", options=servicios, key=f"v2_multi_serv_{ambito_code}")

    # ── CONSULTA DE TENDENCIA CONSOLIDADA (SQL OPTIMIZADO) ────────────────────
    with st.spinner("Consolidando tendencia multidía..."):
        with _get_db() as conn:
            # 1. Turnos en el rango
            q_turnos = """
                SELECT bp, fecha, novedad, horas_programadas, turno_ini, turno_fin, servicio
                FROM turnos_detallados
                WHERE fecha BETWEEN ? AND ?
            """
            p_t = [fecha_ini, fecha_fin]
            if serv_sel != "Todos los Servicios":
                q_turnos += " AND servicio = ?"
                p_t.append(serv_sel)
            df_t_multi = pd.read_sql_query(q_turnos, conn, params=p_t)

            # 2. Segmentos de presencia en el rango
            q_seg = """
                SELECT fecha, agente, presence_label, duracion_min, coordinador, jefe_inmediato, servicio
                FROM segments
                WHERE fecha BETWEEN ? AND ?
            """
            p_s = [fecha_ini, fecha_fin]
            df_seg_multi = pd.read_sql_query(q_seg, conn, params=p_s)

    if df_seg_multi.empty:
        st.info("Sin registros de presencia en el período seleccionado.")
        return

    # Extraer BP, nombre, supervisor y coordinador
    df_seg_multi["bp"] = df_seg_multi["agente"].astype(str).str.split(" - ").str[0].str.strip()
    df_seg_multi["nombre"] = df_seg_multi["agente"].astype(str).apply(lambda a: str(a).split(" - ", 1)[1].strip() if " - " in str(a) else str(a).strip())
    df_seg_multi["supervisor"] = df_seg_multi["jefe_inmediato"].fillna("No Asignado").astype(str).str.strip()
    df_seg_multi["coordinador"] = df_seg_multi["coordinador"].fillna("No Asignado").astype(str).str.strip()
    if not df_t_multi.empty:
        df_t_multi["horas_programadas"] = pd.to_numeric(df_t_multi["horas_programadas"], errors="coerce").fillna(8.0)
    
    # Filtro ámbito
    b2b_keywords = ["CARDONA", "RODRIGUEZ URIBE"]
    if ambito_code == "B2B":
        df_seg_multi = df_seg_multi[df_seg_multi["coordinador"].astype(str).apply(lambda c: any(k in c.upper() for k in b2b_keywords))]
    else:
        df_seg_multi = df_seg_multi[~df_seg_multi["coordinador"].astype(str).apply(lambda c: any(k in c.upper() for k in b2b_keywords))]
        df_seg_multi = df_seg_multi[~df_seg_multi["servicio"].astype(str).str.upper().str.contains("CARGO", na=False)]

    if coord_sel != "Todos los Coordinadores":
        df_seg_multi = df_seg_multi[df_seg_multi["coordinador"].astype(str).str.contains(coord_sel, case=False, na=False)]
    if superv_sel != "Todos los Supervisores":
        df_seg_multi = df_seg_multi[df_seg_multi["jefe_inmediato"].astype(str).str.contains(superv_sel, case=False, na=False)]
    if serv_sel != "Todos los Servicios":
        df_seg_multi = df_seg_multi[df_seg_multi["servicio"].astype(str) == serv_sel]

    if df_seg_multi.empty:
        st.info("Sin registros tras aplicar los filtros jerárquicos.")
        return

    # Clasificar estados
    ESTADOS_SISTEMA = {"OFFLINE", "DESCONECTADO", "OFF-LINE", "OFF LINE", "LOGOUT"}
    PAUSAS_KEYWORDS = ["PAUSA", "DESCANSO", "LUNCH", "ALMUERZO", "BAÑO", "BANO", "BREAK"]

    df_seg_multi["es_conectado"] = ~df_seg_multi["presence_label"].str.upper().isin(ESTADOS_SISTEMA)
    df_seg_multi["es_pausa"] = df_seg_multi["presence_label"].str.upper().apply(lambda p: any(k in p for k in PAUSAS_KEYWORDS))

    # Agrupar por fecha para evolutivo
    evolutivo = df_seg_multi.groupby("fecha").agg(
        horas_con=("duracion_min", lambda m: m[df_seg_multi.loc[m.index, "es_conectado"]].sum() / 60.0),
        horas_pausa=("duracion_min", lambda m: m[df_seg_multi.loc[m.index, "es_pausa"]].sum() / 60.0),
        agentes_unicos=("bp", "nunique")
    ).reset_index()

    # Horas programadas por fecha
    if not df_t_multi.empty:
        t_prog_dia = df_t_multi.groupby("fecha")["horas_programadas"].sum().reset_index()
        evolutivo = pd.merge(evolutivo, t_prog_dia, on="fecha", how="left").fillna(0.0)
        evolutivo["% Cumplimiento"] = (evolutivo["horas_con"] / evolutivo["horas_programadas"] * 100).fillna(0.0).round(1)
    else:
        evolutivo["horas_programadas"] = 0.0
        evolutivo["% Cumplimiento"] = 0.0

    # ── 3 KPIS DE PERÍODO MULTIDÍA ───────────────────────────────────────────
    tot_h_con = evolutivo["horas_con"].sum()
    tot_h_prog = evolutivo["horas_programadas"].sum()
    pct_global_cumpl = round((tot_h_con / tot_h_prog * 100), 1) if tot_h_prog > 0 else 0.0
    prom_agentes = int(round(evolutivo["agentes_unicos"].mean())) if not evolutivo.empty else 0
    tot_h_pausa = evolutivo["horas_pausa"].sum()

    m1, m2, m3 = st.columns(3)
    with m1:
        st.markdown(
            f"""
            <div style="background:#ffffff; border-radius:10px; padding:14px 18px; border:1px solid #e2e8f0; border-left:5px solid #3b82f6; box-shadow:0 1px 3px rgba(0,0,0,0.04);">
                <div style="font-size:12px; color:#64748b; font-weight:700; text-transform:uppercase;">📈 Cumplimiento Promedio de Turno</div>
                <div style="font-size:28px; font-weight:800; color:#0f172a; margin:4px 0;">{pct_global_cumpl}%</div>
                <div style="font-size:12px; color:#64748b;">
                    <b>{tot_h_con:.0f}h</b> conectadas de <b>{tot_h_prog:.0f}h</b> programadas en el período
                </div>
            </div>
            """,
            unsafe_allow_html=True
        )

    with m2:
        st.markdown(
            f"""
            <div style="background:#ffffff; border-radius:10px; padding:14px 18px; border:1px solid #e2e8f0; border-left:5px solid #8b5cf6; box-shadow:0 1px 3px rgba(0,0,0,0.04);">
                <div style="font-size:12px; color:#64748b; font-weight:700; text-transform:uppercase;">👥 Población Promedio Activa</div>
                <div style="font-size:28px; font-weight:800; color:#0f172a; margin:4px 0;">{prom_agentes} asesores/día</div>
                <div style="font-size:12px; color:#64748b;">
                    Total de <b>{evolutivo['fecha'].nunique()} días</b> auditados en este rango
                </div>
            </div>
            """,
            unsafe_allow_html=True
        )

    with m3:
        pct_tiempo_pausas = round((tot_h_pausa / tot_h_con * 100), 1) if tot_h_con > 0 else 0.0
        st.markdown(
            f"""
            <div style="background:#ffffff; border-radius:10px; padding:14px 18px; border:1px solid #e2e8f0; border-left:5px solid #f59e0b; box-shadow:0 1px 3px rgba(0,0,0,0.04);">
                <div style="font-size:12px; color:#64748b; font-weight:700; text-transform:uppercase;">☕ Tiempo en Descansos / Pausas</div>
                <div style="font-size:28px; font-weight:800; color:#0f172a; margin:4px 0;">{tot_h_pausa:.0f} h</div>
                <div style="font-size:12px; color:#64748b;">
                    Representa el <b>{pct_tiempo_pausas}%</b> del tiempo total de conexión del equipo
                </div>
            </div>
            """,
            unsafe_allow_html=True
        )

    st.write("")

    # ── PANEL DE FOCO DE ATENCIÓN: TOP 5 DESVIACIONES ────────────────────────
    st.markdown("##### 🎯 Panel de Foco: Asesores con Mayor Desviación en el Período")
    st.caption("Identifica de inmediato a los colaboradores que requieren retroalimentación o ajuste de jornada sin revisar uno por uno.")

    # Agrupar por asesor
    resumen_asesor = df_seg_multi.groupby(["bp", "nombre", "supervisor", "coordinador"]).agg(
        dias_asistidos=("fecha", "nunique"),
        horas_conectado=("duracion_min", lambda m: m[df_seg_multi.loc[m.index, "es_conectado"]].sum() / 60.0),
        horas_pausas=("duracion_min", lambda m: m[df_seg_multi.loc[m.index, "es_pausa"]].sum() / 60.0)
    ).reset_index()

    # Cruzar con programado
    if not df_t_multi.empty:
        t_prog_as = df_t_multi.groupby("bp")["horas_programadas"].sum().reset_index()
        resumen_asesor = pd.merge(resumen_asesor, t_prog_as, on="bp", how="left").fillna(0.0)
        resumen_asesor["brecha_h"] = (resumen_asesor["horas_conectado"] - resumen_asesor["horas_programadas"]).round(2)
        resumen_asesor["% Cumplimiento"] = (resumen_asesor["horas_conectado"] / resumen_asesor["horas_programadas"] * 100).fillna(0.0).round(1)
    else:
        resumen_asesor["horas_programadas"] = 0.0
        resumen_asesor["brecha_h"] = 0.0
        resumen_asesor["% Cumplimiento"] = 0.0

    c_top1, c_top2 = st.columns(2)
    with c_top1:
        st.markdown(
            """
            <div style="background:#fef2f2; border:1px solid #fecaca; border-radius:8px; padding:10px 14px; margin-bottom:8px;">
                <span style="color:#991b1b; font-weight:700; font-size:13px;">🚨 Top 5 Asesores con Mayor Déficit de Horas</span>
            </div>
            """,
            unsafe_allow_html=True
        )
        top_deficit = resumen_asesor.sort_values("brecha_h", ascending=True).head(5)
        top_def_show = top_deficit[["nombre", "supervisor", "horas_programadas", "horas_conectado", "brecha_h"]].rename(
            columns={
                "nombre": "Asesor", "supervisor": "Supervisor",
                "horas_programadas": "H. Prog", "horas_conectado": "H. Conectado",
                "brecha_h": "Faltante (h)"
            }
        )
        st.dataframe(top_def_show, use_container_width=True, hide_index=True)

    with c_top2:
        st.markdown(
            """
            <div style="background:#fffbeb; border:1px solid #fde68a; border-radius:8px; padding:10px 14px; margin-bottom:8px;">
                <span style="color:#92400e; font-weight:700; font-size:13px;">☕ Top 5 Asesores con Mayor Tiempo en Pausas</span>
            </div>
            """,
            unsafe_allow_html=True
        )
        top_pausas = resumen_asesor.sort_values("horas_pausas", ascending=False).head(5)
        top_pau_show = top_pausas[["nombre", "supervisor", "horas_pausas", "horas_conectado"]].rename(
            columns={
                "nombre": "Asesor", "supervisor": "Supervisor",
                "horas_pausas": "Total Pausas (h)", "horas_conectado": "H. Conectado"
            }
        )
        st.dataframe(top_pau_show, use_container_width=True, hide_index=True)

    st.write("")

    # ── GRÁFICO EVOLUTIVO DIARIO (PLOTLY) ────────────────────────────────────
    st.markdown("##### 📊 Evolución Diaria del Cumplimiento de Turno")
    if not evolutivo.empty:
        fig_evo = px.bar(
            evolutivo,
            x="fecha",
            y="% Cumplimiento",
            text="% Cumplimiento",
            color="% Cumplimiento",
            color_continuous_scale=["#ef4444", "#f59e0b", "#10b981"],
            range_color=[70, 100]
        )
        fig_evo.update_traces(texttemplate='%{text:.1f}%', textposition='outside')
        fig_evo.add_hline(y=100.0, line_dash="dash", line_color="#10b981", annotation_text="Meta 100%")
        fig_evo.update_layout(
            height=300,
            margin=dict(l=10, r=10, t=25, b=10),
            coloraxis_showscale=False,
            xaxis_title="",
            yaxis_title="% Cumplimiento",
            yaxis_range=[0, max(115, evolutivo["% Cumplimiento"].max() + 10)]
        )
        st.plotly_chart(fig_evo, use_container_width=True, key=f"v2_chart_evo_{ambito_code}")

    # ── TABLA CONSOLIDADA ACUMULADA ──────────────────────────────────────────
    with st.expander("📂 Ver Tabla Consolidada Acumulada de Todos los Asesores", expanded=False):
        cols_resumen = [
            "bp", "nombre", "coordinador", "supervisor",
            "dias_asistidos", "horas_programadas", "horas_conectado", "brecha_h", "% Cumplimiento", "horas_pausas"
        ]
        df_exp = resumen_asesor[[c for c in cols_resumen if c in resumen_asesor.columns]].rename(
            columns={
                "bp": "BP", "nombre": "Asesor", "coordinador": "Coordinador", "supervisor": "Supervisor",
                "dias_asistidos": "Días Asistidos", "horas_programadas": "H. Prog",
                "horas_conectado": "H. Conectado", "brecha_h": "Brecha (h)",
                "% Cumplimiento": "% Cumplimiento", "horas_pausas": "H. Pausas"
            }
        )
        st.dataframe(df_exp, use_container_width=True, hide_index=True)
        csv_exp = df_exp.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "📥 Descargar Consolidado del Período (CSV)",
            data=csv_exp,
            file_name=f"consolidado_adherencia_{ambito_code}_{fecha_ini}_{fecha_fin}.csv",
            mime="text/csv",
            key=f"v2_dl_multi_{ambito_code}"
        )
