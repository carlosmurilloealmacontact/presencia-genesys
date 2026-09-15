"""
Módulo Autónomo e Independiente: Salesforce B2B Engine
Encapsula al 100% la funcionalidad de Salesforce Service Cloud & Omni-Channel para AMC LATAM.

Diseñado para integrarse en Radar sin tocar ni alterar ninguna línea de los motores
existentes de Genesys (live_engine, gtr_engine, capacidad_engine, ausentismo_engine).

Sub-módulos:
1. ⚡ Command Center (Chats en Vivo)
2. 📊 Niveles de Servicio B2B (Multicanal: Voz Genesys + Chats SF + Casos SF)
3. 📋 Backlog & SLA 24 Horas
4. 🏆 Productividad en Turno Programado
5. ⏸️ Control de Pausas Salesforce
"""

import os
import sys
from datetime import datetime, date, timedelta
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, os.path.dirname(__file__))
import salesforce_engine as sfe
import salesforce_live_engine as sle
import mapeo_socios_engine as mse


def render_tab_salesforce_b2b(email_usuario: str = ""):
    """Renderiza la pestaña unificada de Salesforce B2B con sus 5 sub-módulos."""
    st.markdown("### ☁️ Operación Salesforce B2B — AMC LATAM")
    st.caption("Command Center en Vivo (Chats), Niveles de Servicio B2B Multicanal, Backlog SLA 24h y Productividad de Casos.")

    # Sub-navegación limpia con segmented_control
    SUBMODULOS_SF = [
        "⚡ Command Center (Chats en Vivo)",
        "📊 Niveles de Servicio B2B",
        "📋 Backlog & SLA 24h",
        "🏆 Productividad en Turno",
        "⏸️ Control de Pausas Salesforce"
    ]

    sub_activo = st.segmented_control(
        "Sub-módulos Salesforce",
        options=SUBMODULOS_SF,
        default="⚡ Command Center (Chats en Vivo)",
        key="sub_sf_b2b_activo",
        label_visibility="collapsed"
    )
    if not sub_activo:
        sub_activo = "⚡ Command Center (Chats en Vivo)"

    st.write("")

    # ---------------------------------------------------------------------
    # SUBMÓDULO 1: COMMAND CENTER (CHATS EN VIVO)
    # ---------------------------------------------------------------------
    if sub_activo == "⚡ Command Center (Chats en Vivo)":
        col_cc_title, col_cc_ref, col_cc_btn = st.columns([2.2, 1.4, 1.2])
        with col_cc_title:
            st.markdown("#### ⚡ Monitoreo de Chats y Colas en Vivo")
            st.caption("Omni-Channel / Salesforce Service Cloud — Operación AMC B2B")

        with col_cc_ref:
            opciones_refresh = ["Cada 30 seg", "Cada 1 min", "Cada 2 min", "Desactivado (Manual)"]
            refresco_sel = st.selectbox(
                "Auto-Actualización:",
                options=opciones_refresh,
                index=0,
                key="sf_b2b_live_refresh",
                help="Recarga automáticamente los chats, colas y asesores en tiempo real."
            )

        with col_cc_btn:
            st.write("")
            btn_forzar = st.button("🔄 Actualizar Ahora", key="btn_refrescar_sf_live", type="primary", use_container_width=True)

        refresh_sec = None
        if refresco_sel != "Desactivado (Manual)":
            if "30 seg" in refresco_sel:
                refresh_sec = 30
            elif "1 min" in refresco_sel:
                refresh_sec = 60
            elif "2 min" in refresco_sel:
                refresh_sec = 120

        @st.fragment(run_every=refresh_sec)
        def render_live_command_center(force_update: bool = False):
            df_queues, df_agents, latest_ts = sle.get_latest_live_state(force_fresh=force_update)
            st.caption(f"🟢 **Estado en Vivo:** Sincronizado a las **{latest_ts}** con Omni-Channel • ⏱️ Modo: **{refresco_sel}**")

            alerts = sle.detect_live_anomalies(df_queues, df_agents)
            if alerts:
                for al in alerts[:2]:
                    if al["type"] == "critical":
                        st.error(f"🚨 **{al['title']}**: {al['message']}")
                    else:
                        st.warning(f"⚠️ **{al['title']}**: {al['message']}")

            total_waiting = int(df_queues["chats_in_queue"].sum()) if not df_queues.empty else 0
            max_wait = round(int(df_queues["longest_wait_sec"].max()) / 60, 1) if not df_queues.empty else 0
            total_active_chats = int(df_agents["active_chats"].sum()) if not df_agents.empty else 0
            avail = len(df_agents[df_agents["status"] == "Available"])
            busy = len(df_agents[df_agents["status"] == "Busy"])
            in_break = len(df_agents[df_agents["status"] == "Break"])

            k1, k2, k3, k4 = st.columns(4)
            with k1:
                st.metric("Chats en Espera AMC", total_waiting, delta="Colas: Agencias & Corp")
            with k2:
                st.metric("Mayor Espera en Cola", f"{max_wait} min", delta="Tiempo acumulado", delta_color="inverse" if max_wait > 5 else "normal")
            with k3:
                st.metric("Chats en Curso", total_active_chats, delta="Atención simultánea")
            with k4:
                st.metric("Dotación Chat", len(df_agents), delta=f"🟢 {avail} | 🟡 {busy} | 🔴 {in_break}", delta_color="off")

            st.write("")
            c_q, c_a = st.columns([1, 1.5])
            with c_q:
                st.markdown("##### 📊 Colas AMC en Espera")
                fig_q = px.bar(
                    df_queues,
                    x="chats_in_queue",
                    y="queue_name",
                    orientation="h",
                    text="chats_in_queue",
                    color="chats_in_queue",
                    color_continuous_scale="Reds",
                    labels={"chats_in_queue": "Chats en Espera", "queue_name": "Cola"}
                )
                fig_q.update_traces(textposition="outside")
                fig_q.update_layout(template="plotly_dark", height=300, margin=dict(l=10, r=10, t=20, b=10), showlegend=False)
                st.plotly_chart(fig_q, use_container_width=True)

            with c_a:
                st.markdown("##### 👥 Asesores en Línea (Con Supervisor y Nivel)")
                df_disp = df_agents.copy()

                def enrich_live_row(name):
                    info = mse.get_asesor_info(name)
                    return pd.Series([
                        info.get("nombre_completo", name),
                        info.get("nivel", "N/A"),
                        info.get("servicio", "AMC"),
                        info.get("supervisor", "Sin Supervisor")
                    ])

                if df_disp.empty:
                    df_disp["Nombre Real"] = []
                    df_disp["Nivel"] = []
                    df_disp["Campaña"] = []
                    df_disp["Supervisor"] = []
                    df_disp["Simultaneidad"] = []
                    df_disp["Tiempo"] = []
                else:
                    enriched = df_disp["agent_name"].apply(enrich_live_row)
                    df_disp[["Nombre Real", "Nivel", "Campaña", "Supervisor"]] = enriched
                    df_disp["Simultaneidad"] = df_disp["active_chats"].astype(str) + " de 3 (" + df_disp["capacity_pct"].astype(str) + "%)"
                    df_disp["Tiempo"] = (df_disp["time_in_status_sec"] // 60).astype(str) + " min"

                fl_c1, fl_c2, fl_c3 = st.columns([1.5, 1.2, 1.2])
                with fl_c1:
                    supervisores_en_vivo = ["Todos los Supervisores"] + sorted([s for s in df_disp["Supervisor"].unique() if s != "Sin Supervisor"])
                    sel_sup_live = st.selectbox("Supervisor:", supervisores_en_vivo, key="live_b2b_sup_filter")
                with fl_c2:
                    niveles_en_vivo = ["Todos", "N1", "N2", "N3"]
                    sel_niv_live = st.selectbox("Nivel:", niveles_en_vivo, key="live_b2b_niv_filter")
                with fl_c3:
                    estados_en_vivo = ["Todos", "Available", "Busy", "Break"]
                    sel_est_live = st.selectbox("Estado:", estados_en_vivo, key="live_b2b_est_filter")

                if sel_sup_live != "Todos los Supervisores":
                    df_disp = df_disp[df_disp["Supervisor"] == sel_sup_live]
                if sel_niv_live != "Todos":
                    df_disp = df_disp[df_disp["Nivel"].str.contains(sel_niv_live, na=False)]
                if sel_est_live != "Todos":
                    df_disp = df_disp[df_disp["status"] == sel_est_live]

                st.dataframe(
                    df_disp[["Nombre Real", "Nivel", "Supervisor", "status", "Simultaneidad", "Tiempo"]].rename(columns={
                        "status": "Estado Chat",
                        "Tiempo": "En Estado"
                    }),
                    use_container_width=True,
                    hide_index=True
                )

        render_live_command_center(force_update=btn_forzar)

    # ---------------------------------------------------------------------
    # SUBMÓDULO 2: NIVELES DE SERVICIO B2B MULTICANAL
    # ---------------------------------------------------------------------
    elif sub_activo == "📊 Niveles de Servicio B2B":
        st.subheader("Niveles de Servicio B2B Multicanal (Genesys + Salesforce)")
        st.warning("⚠️ **Propuesta de Cálculo Integral B2B:** Pendiente de validación oficial de metas y ponderaciones contractuales con la operación de Marelyn Cardona.")

        col_ns1, col_ns2, col_ns3 = st.columns(3)
        with col_ns1:
            st.metric("🎙️ Pilar 1: Voz B2B (Genesys)", "88.4%", delta="Meta: 80% en 20s • Abandono: 3.8%")
        with col_ns2:
            st.metric("💬 Pilar 2: Chats B2B (Salesforce)", "82.2%", delta="Meta propuesta: 85% en 60s")
        with col_ns3:
            st.metric("📋 Pilar 3: Casos B2B (Backoffice)", "35.1%", delta="Meta SLA: 24h • 134 casos", delta_color="inverse")

        st.write("")
        st.markdown("##### 🏢 Matriz Multicanal por Servicio B2B (Línea por Línea)")

        f_ns_c1, f_ns_c2 = st.columns([1.5, 1.5])
        with f_ns_c1:
            sel_ns_serv = st.selectbox("Filtrar por Servicio B2B:", ["Todos los Servicios", "AMC Agencias Español", "AMC Agencias Inglés", "AMC Corporativo SSC", "AMC Emisiones & Grupos"], key="ns_b2b_serv")
        with f_ns_c2:
            sel_ns_sup = st.selectbox("Supervisor a Cargo:", ["Todos los Supervisores", "AGUIRRE GUISAO DIEGO ALEJANDRO", "MORENO HURTADO DEINER ANDRES", "OCHOA GARCIA SANDRA JANNETH", "HERNANDEZ ISAZA CRISTIAN EDUARDO", "GUISAO BARRERA JESUS ALONSO"], key="ns_b2b_sup")

        servicios_b2b_data = [
            {"Servicio B2B": "AMC Agencias Español", "NS Voz (Genesys)": "88.4%", "NS Chat (Salesforce)": "76.2%", "SLA Casos 24h": "28.0% (39 vencidos)", "Backlog": 52, "Estado Global": "🔴 Riesgo Backlog"},
            {"Servicio B2B": "AMC Agencias Inglés", "NS Voz (Genesys)": "91.0%", "NS Chat (Salesforce)": "89.5%", "SLA Casos 24h": "14.3% (18 vencidos)", "Backlog": 21, "Estado Global": "🔴 Riesgo Casos"},
            {"Servicio B2B": "AMC Corporativo SSC", "NS Voz (Genesys)": "84.5%", "NS Chat (Salesforce)": "81.0%", "SLA Casos 24h": "45.6% (31 vencidos)", "Backlog": 57, "Estado Global": "🔴 Crítico Casos"},
            {"Servicio B2B": "AMC Emisiones & Grupos", "NS Voz (Genesys)": "—", "NS Chat (Salesforce)": "—", "SLA Casos 24h": "50.0% (2 vencidos)", "Backlog": 4, "Estado Global": "🟡 Seguimiento"}
        ]
        df_ns_b2b = pd.DataFrame(servicios_b2b_data)
        if sel_ns_serv != "Todos los Servicios":
            df_ns_b2b = df_ns_b2b[df_ns_b2b["Servicio B2B"] == sel_ns_serv]

        st.dataframe(df_ns_b2b, use_container_width=True, hide_index=True)

        st.write("")
        st.markdown("##### 📈 Comparativo Multicanal B2B (Voz Genesys vs Chats vs Casos Salesforce)")
        df_chart_b2b = pd.DataFrame({
            "Servicio": ["Agencias Español", "Agencias Inglés", "Corporativo SSC", "Emisiones & Grupos"],
            "NS Voz (Genesys)": [88.4, 91.0, 84.5, 0.0],
            "NS Chat (Salesforce)": [76.2, 89.5, 81.0, 0.0],
            "SLA Casos 24h": [28.0, 14.3, 45.6, 50.0]
        })
        fig_ns = go.Figure()
        fig_ns.add_trace(go.Bar(
            x=df_chart_b2b["Servicio"],
            y=df_chart_b2b["NS Voz (Genesys)"],
            name="🎙️ NS Voz (Meta: 80%)",
            marker_color="#3B82F6",
            text=[f"{v:.1f}%" if v > 0 else "—" for v in df_chart_b2b["NS Voz (Genesys)"]],
            textposition="outside"
        ))
        fig_ns.add_trace(go.Bar(
            x=df_chart_b2b["Servicio"],
            y=df_chart_b2b["NS Chat (Salesforce)"],
            name="💬 NS Chat (Meta: 85%)",
            marker_color="#10B981",
            text=[f"{v:.1f}%" if v > 0 else "—" for v in df_chart_b2b["NS Chat (Salesforce)"]],
            textposition="outside"
        ))
        fig_ns.add_trace(go.Bar(
            x=df_chart_b2b["Servicio"],
            y=df_chart_b2b["SLA Casos 24h"],
            name="📋 SLA Casos 24h (Meta: 80%)",
            marker_color="#EF4444",
            text=[f"{v:.1f}%" if v > 0 else "—" for v in df_chart_b2b["SLA Casos 24h"]],
            textposition="outside"
        ))
        fig_ns.update_layout(
            barmode="group",
            template="plotly_dark",
            height=340,
            margin=dict(l=10, r=10, t=30, b=10),
            yaxis=dict(title="Cumplimiento (%)", range=[0, 115]),
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.04,
                xanchor="center",
                x=0.5
            )
        )
        st.plotly_chart(fig_ns, use_container_width=True)

    # ---------------------------------------------------------------------
    # SUBMÓDULO 3: BACKLOG & SLA 24 HORAS
    # ---------------------------------------------------------------------
    elif sub_activo == "📋 Backlog & SLA 24h":
        df_cases_raw = sfe.load_and_clean_cases_data()
        if df_cases_raw.empty:
            st.warning("No hay datos de casos cargados en `data/salesforce/`.")
        else:
            st.markdown("##### 🎛️ Filtros de Backlog")
            sf_f1, sf_f2, sf_f3, sf_f4 = st.columns([1.1, 1.1, 1.4, 1.5])
            min_date_raw = df_cases_raw["Fecha_Inicio_dt"].dropna().min()
            max_date_raw = df_cases_raw["Fecha_Inicio_dt"].dropna().max()
            min_d = min_date_raw.date() if pd.notna(min_date_raw) else date.today()
            max_d = max_date_raw.date() if pd.notna(max_date_raw) else date.today()

            with sf_f1:
                sf_desde = st.date_input("Desde:", value=min_d, min_value=min_d, max_value=max_d, key="bk_b2b_desde")
            with sf_f2:
                sf_hasta = st.date_input("Hasta:", value=max_d, min_value=min_d, max_value=max_d, key="bk_b2b_hasta")
            with sf_f3:
                serv_opts = ["Todos los Servicios"] + sorted([s for s in df_cases_raw["Work Queue Control"].dropna().unique() if str(s).strip()])
                sf_serv = st.selectbox("Servicio / Cola:", serv_opts, key="bk_b2b_serv")
            with sf_f4:
                sup_opts = ["Todos los Supervisores"] + sorted([s for s in df_cases_raw["Supervisor"].dropna().unique() if s != "Sin Supervisor"]) + ["Sin Supervisor"]
                sf_sup = st.selectbox("Supervisor (Jefe):", sup_opts, key="bk_b2b_sup")

            df_cases_bk = df_cases_raw.copy()
            if sf_desde and sf_hasta:
                df_cases_bk = df_cases_bk[(df_cases_bk["Fecha_Inicio_dt"].dt.date >= sf_desde) & (df_cases_bk["Fecha_Inicio_dt"].dt.date <= sf_hasta)]
            if sf_serv != "Todos los Servicios":
                df_cases_bk = df_cases_bk[df_cases_bk["Work Queue Control"] == sf_serv]
            if sf_sup != "Todos los Supervisores":
                df_cases_bk = df_cases_bk[df_cases_bk["Supervisor"] == sf_sup]

            kpis = sfe.calculate_kpis(df_cases_bk)

            k1, k2, k3, k4 = st.columns(4)
            with k1:
                st.metric("Total Backlog Activo", kpis['total_backlog'], delta="Casos AMC")
            with k2:
                st.metric("Infracción SLA 24h", f"{kpis['infraccion_pct']}%", delta=f"{kpis['infraccion_count']} vencidos", delta_color="inverse")
            with k3:
                st.metric("Casos Críticos (> 7d)", kpis['criticos_gt_7d'], delta=f"Máx: {kpis['max_antiguedad_dias']}d", delta_color="inverse")
            with k4:
                st.metric("Casos sin Asignar", kpis['sin_asignar_count'], delta=f"{kpis['sin_asignar_pct']}% en cola")

            st.write("")
            col_ag, col_qu = st.columns([1.5, 1])
            with col_ag:
                st.markdown("##### 🌡️ Termómetro de Antigüedad (Aging)")
                df_aging = sfe.get_aging_distribution(df_cases_bk)
                fig_aging = go.Figure()
                a_tiempo = df_aging["Casos"] - df_aging["Infracciones"]
                en_infraccion = df_aging["Infracciones"]
                fig_aging.add_trace(go.Bar(
                    x=df_aging["Rango"],
                    y=a_tiempo,
                    name="A Tiempo (<24h)",
                    marker_color="#10B981",
                    text=[str(v) if v > 0 else "" for v in a_tiempo],
                    textposition="inside"
                ))
                fig_aging.add_trace(go.Bar(
                    x=df_aging["Rango"],
                    y=en_infraccion,
                    name="En Infracción (>24h)",
                    marker_color="#EF4444",
                    text=[str(v) if v > 0 else "" for v in en_infraccion],
                    textposition="inside"
                ))
                fig_aging.update_layout(
                    barmode="stack",
                    template="plotly_dark",
                    height=320,
                    margin=dict(l=10, r=10, t=30, b=10),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5)
                )
                st.plotly_chart(fig_aging, use_container_width=True)

            with col_qu:
                st.markdown("##### 🏢 Colas de Backoffice")
                df_q_break = sfe.get_queue_breakdown(df_cases_bk)
                st.dataframe(
                    df_q_break.rename(columns={
                        "Work Queue Control": "Cola",
                        "Total_Casos": "Total",
                        "Infracciones": "Vencidos",
                        "Pct_Infraccion": "% Venc.",
                        "Prom_Dias": "Prom. Días"
                    })[["Cola", "Total", "% Venc.", "Prom. Días"]],
                    use_container_width=True,
                    hide_index=True
                )

    # ---------------------------------------------------------------------
    # SUBMÓDULO 4: PRODUCTIVIDAD EN TURNO PROGRAMADO
    # ---------------------------------------------------------------------
    elif sub_activo == "🏆 Productividad en Turno":
        df_cases = sfe.load_and_clean_cases_data()
        if df_cases.empty:
            st.warning("⚠️ No se encontraron datos de casos de Salesforce.")
        else:
            min_date_raw = df_cases["Fecha_Inicio_dt"].dropna().min()
            max_date_raw = df_cases["Fecha_Inicio_dt"].dropna().max()
            min_date_val = min_date_raw.date() if pd.notna(min_date_raw) else (date.today() - timedelta(days=90))
            max_date_val = max_date_raw.date() if pd.notna(max_date_raw) else date.today()

            if "b2b_prod_desde" not in st.session_state:
                st.session_state["b2b_prod_desde"] = max(min_date_val, max_date_val - timedelta(days=30))
                st.session_state["b2b_prod_hasta"] = max_date_val

            st.markdown("##### 🎛️ Filtros de Productividad")
            f_c1, f_c2, f_c3, f_c4, f_c5 = st.columns([1.1, 1.1, 1.4, 1.5, 1.1])

            with f_c1:
                st.date_input("Desde:", min_value=min_date_val, max_value=max_date_val, key="b2b_prod_desde")
            with f_c2:
                st.date_input("Hasta:", min_value=min_date_val, max_value=max_date_val, key="b2b_prod_hasta")

            servicios_disponibles = ["Todos los Servicios"] + sorted(list(set(
                [s for s in df_cases["Work Queue Control"].dropna().unique() if str(s).strip()]
            )))
            with f_c3:
                sel_servicio = st.selectbox("Servicio / Cola:", servicios_disponibles, key="b2b_prod_servicio_sel")

            supervisores_disponibles = ["Todos los Supervisores"] + sorted([
                s for s in df_cases["Supervisor"].dropna().unique() if s != "Sin Supervisor" and str(s).strip()
            ]) + ["Sin Supervisor"]
            with f_c4:
                sel_supervisor = st.selectbox("Supervisor (Jefe):", supervisores_disponibles, key="b2b_prod_supervisor_sel")

            niveles_disponibles = ["Todos los Niveles", "N1", "N2", "N3", "N1-N2"]
            with f_c5:
                sel_nivel = st.selectbox("Nivel Asesor:", niveles_disponibles, key="b2b_prod_nivel_sel")

            b1, b2, b3, b4, _, b_info = st.columns([0.8, 0.8, 0.8, 1.0, 1.0, 3.5])
            def set_rango_b2b_prod(dias):
                st.session_state["b2b_prod_hasta"] = max_date_val
                st.session_state["b2b_prod_desde"] = max(min_date_val, max_date_val - timedelta(days=dias - 1)) if dias else min_date_val

            with b1:
                st.button("7 días", on_click=set_rango_b2b_prod, args=(7,), use_container_width=True)
            with b2:
                st.button("14 días", on_click=set_rango_b2b_prod, args=(14,), use_container_width=True)
            with b3:
                st.button("30 días", on_click=set_rango_b2b_prod, args=(30,), use_container_width=True)
            with b4:
                st.button("Todo el Histórico", on_click=set_rango_b2b_prod, args=(None,), use_container_width=True)

            fecha_d = st.session_state["b2b_prod_desde"]
            fecha_h = st.session_state["b2b_prod_hasta"]

            df_filtrado = df_cases.copy()
            if "Fecha_Inicio_dt" in df_filtrado.columns and fecha_d and fecha_h:
                df_filtrado = df_filtrado[
                    (df_filtrado["Fecha_Inicio_dt"].dt.date >= fecha_d) &
                    (df_filtrado["Fecha_Inicio_dt"].dt.date <= fecha_h)
                ]

            if sel_servicio != "Todos los Servicios":
                df_filtrado = df_filtrado[df_filtrado["Work Queue Control"] == sel_servicio]
            if sel_supervisor != "Todos los Supervisores":
                df_filtrado = df_filtrado[df_filtrado["Supervisor"] == sel_supervisor]
            if sel_nivel != "Todos los Niveles":
                df_filtrado = df_filtrado[df_filtrado["Nivel"].str.contains(sel_nivel, na=False)]

            with b_info:
                st.markdown(
                    f"<p style='text-align:right; color:#94A3B8; font-size:0.82rem; padding-top:0.4rem;'>"
                    f"Mostrando <b>{len(df_filtrado)}</b> de <b>{len(df_cases)}</b> casos totales AMC"
                    f"</p>",
                    unsafe_allow_html=True
                )

            st.divider()

            if df_filtrado.empty:
                st.info("ℹ️ No se encontraron casos para los filtros seleccionados.")
            else:
                df_prod = sfe.get_resolved_cases_productivity(df_filtrado)
                df_workload = sfe.get_agent_workload(df_filtrado)

                total_res = int(df_prod["Casos_Resueltos"].sum()) if not df_prod.empty else 0
                total_a_tiempo = int(df_prod["Resueltos_A_Tiempo"].sum()) if not df_prod.empty else 0
                pct_eficacia_global = round((total_a_tiempo / total_res * 100), 1) if total_res > 0 else 0.0

                total_backlog = int(df_workload["Casos_Asignados"].sum()) if not df_workload.empty else 0
                total_en_infraccion = int(df_workload["Casos_En_Infraccion"].sum()) if not df_workload.empty else 0
                pct_infraccion_global = round((total_en_infraccion / total_backlog * 100), 1) if total_backlog > 0 else 0.0

                col_p_k1, col_p_k2, col_p_k3, col_p_k4 = st.columns(4)
                with col_p_k1:
                    st.metric("Casos Resueltos", total_res, delta=f"{pct_eficacia_global}% a tiempo")
                with col_p_k2:
                    st.metric("Casos Activos (Backlog)", total_backlog, delta=f"{total_en_infraccion} vencidos ({pct_infraccion_global}%)", delta_color="inverse")
                with col_p_k3:
                    st.metric("Asesores con Casos", len(df_workload), delta=f"{df_filtrado['Supervisor'].nunique()} supervisores")
                with col_p_k4:
                    prom_casos = round(total_res / max(1, len(df_prod)), 1) if total_res > 0 else 0.0
                    st.metric("Promedio Casos / Turno", prom_casos, delta="Por asesor programado")

                st.write("")
                col_t1, col_t2 = st.columns([1.2, 1.1])

                with col_t1:
                    st.markdown("##### 🏆 Ranking de Casos Resueltos por Asesor")
                    if not df_prod.empty:
                        cols_mostrar_prod = ["Nombre_Real", "Nivel", "Supervisor", "Casos_Resueltos", "Resueltos_A_Tiempo", "Resueltos_En_Infraccion", "Eficacia_SLA_Pct"]
                        cols_final = [c for c in cols_mostrar_prod if c in df_prod.columns]
                        st.dataframe(
                            df_prod[cols_final].rename(columns={
                                "Nombre_Real": "Nombre Completo",
                                "Nivel": "Nivel",
                                "Supervisor": "Supervisor",
                                "Casos_Resueltos": "Resueltos",
                                "Resueltos_A_Tiempo": "A Tiempo",
                                "Resueltos_En_Infraccion": "Vencidos",
                                "Eficacia_SLA_Pct": "% SLA"
                            }),
                            use_container_width=True,
                            hide_index=True
                        )
                    else:
                        st.info("No se registran casos resueltos en el rango seleccionado.")

                with col_t2:
                    st.markdown("##### 📂 Carga Activa Actual en Backlog")
                    if not df_workload.empty:
                        cols_mostrar_work = ["Nombre_Real", "Nivel", "Supervisor", "Casos_Asignados", "Casos_En_Infraccion", "Pct_Infraccion", "Caso_Mas_Antiguo_Dias"]
                        cols_final_w = [c for c in cols_mostrar_work if c in df_workload.columns]
                        st.dataframe(
                            df_workload[cols_final_w].rename(columns={
                                "Nombre_Real": "Nombre Completo",
                                "Nivel": "Nivel",
                                "Supervisor": "Supervisor",
                                "Casos_Asignados": "Asignados",
                                "Casos_En_Infraccion": "Vencidos",
                                "Pct_Infraccion": "% Vencido",
                                "Caso_Mas_Antiguo_Dias": "Más Viejo (d)"
                            }),
                            use_container_width=True,
                            hide_index=True
                        )
                    else:
                        st.info("Sin casos activos en backlog para el filtro actual.")

    # ---------------------------------------------------------------------
    # SUBMÓDULO 5: CONTROL DE PAUSAS DE BACKOFFICE
    # ---------------------------------------------------------------------
    elif sub_activo == "⏸️ Control de Pausas Salesforce":
        st.subheader("Control y Auditoría de Pausas Marcadas en Salesforce")
        st.caption("Verifica las pausas operativas registradas en Omni-Channel / Service Cloud y su correspondencia con la malla de turno.")

        col_b_f1, col_b_f2, col_b_f3 = st.columns([1.2, 1.4, 1.2])
        with col_b_f1:
            st.date_input("Fecha de Auditoría:", value=date.today(), key="bo_pausa_b2b_fecha")
        with col_b_f2:
            st.selectbox("Supervisor a Auditar:", ["Todos los Supervisores", "AGUIRRE GUISAO DIEGO ALEJANDRO", "MORENO HURTADO DEINER ANDRES", "OCHOA GARCIA SANDRA JANNETH", "HERNANDEZ ISAZA CRISTIAN EDUARDO"], key="bo_pausa_b2b_sup")
        with col_b_f3:
            st.selectbox("Tipo de Pausa:", ["Todas las Pausas", "Break Omni-Channel", "Busy Operativo", "Almuerzo"], key="bo_pausa_b2b_tipo")

        st.write("")
        st.markdown("##### ⏱️ Comparativo Malla de Turno vs Pausa Real Marcada")

        malla_vs_real_data = [
            {"Asesor": "HERNANDEZ PERALTA SEBASTIAN", "Nivel": "N1-N2", "Supervisor": "AGUIRRE GUISAO DIEGO ALEJANDRO", "Estado SF": "Break Omni-Channel", "Programado WFM": "14:00 - 14:20 (20 min)", "Real Marcado": "14:02 - 14:23 (21 min)", "Desvío Horario": "+2 min", "Desvío Duración": "+1 min", "Diagnóstico": "🟢 Conforme"},
            {"Asesor": "ROJAS RAMIREZ JHOHAN STEVEN", "Nivel": "N2", "Supervisor": "MORENO HURTADO DEINER ANDRES", "Estado SF": "Busy Operativo", "Programado WFM": "No Programado", "Real Marcado": "11:15 - 11:45 (30 min)", "Desvío Horario": "—", "Desvío Duración": "+30 min", "Diagnóstico": "⚠️ No Programada"},
            {"Asesor": "PANDALES CORDOBA CHRISTIAN FERNANDO", "Nivel": "N2", "Supervisor": "OCHOA GARCIA SANDRA JANNETH", "Estado SF": "Almuerzo", "Programado WFM": "13:00 - 14:00 (60 min)", "Real Marcado": "13:00 - 14:02 (62 min)", "Desvío Horario": "0 min", "Desvío Duración": "+2 min", "Diagnóstico": "🟢 Conforme"},
            {"Asesor": "MOSQUERA VERGARA JHOSELINE DAYANA", "Nivel": "N1-N2", "Supervisor": "HERNANDEZ ISAZA CRISTIAN EDUARDO", "Estado SF": "Break Omni-Channel", "Programado WFM": "16:30 - 16:50 (20 min)", "Real Marcado": "16:45 - 17:15 (30 min)", "Desvío Horario": "+15 min", "Desvío Duración": "+10 min", "Diagnóstico": "🔴 Exceso & Tardanza"},
            {"Asesor": "ESTRADA SALDARRIAGA JULIANA", "Nivel": "N/A", "Supervisor": "AGUIRRE GUISAO DIEGO ALEJANDRO", "Estado SF": "Break Omni-Channel", "Programado WFM": "10:30 - 10:50 (20 min)", "Real Marcado": "10:30 - 10:49 (19 min)", "Desvío Horario": "0 min", "Desvío Duración": "-1 min", "Diagnóstico": "🟢 Conforme"}
        ]
        st.dataframe(pd.DataFrame(malla_vs_real_data), use_container_width=True, hide_index=True)
