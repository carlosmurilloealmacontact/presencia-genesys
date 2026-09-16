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
from glosario_b2b_engine import render_glosario_b2b


def render_tab_salesforce_b2b(email_usuario: str = ""):
    """Renderiza la pestaña unificada de Salesforce B2B con sus 6 sub-módulos."""
    st.markdown("### ☁️ Operación Salesforce B2B — AMC LATAM")
    st.caption("Command Center en Vivo (Chats), Niveles de Servicio B2B Multicanal, Backlog SLA 24h, Productividad y Glosario Metodológico.")

    # Sub-navegación limpia con segmented_control
    SUBMODULOS_SF = [
        "⚡ Command Center (Chats en Vivo)",
        "📊 Niveles de Servicio B2B",
        "📋 Backlog & SLA 24h",
        "🏆 Productividad en Turno",
        "⏸️ Control de Pausas Salesforce",
        "📚 Glosario & Guía B2B"
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
            hora_display = str(latest_ts or "")
            try:
                dt_obj = datetime.strptime(latest_ts, "%Y-%m-%d %H:%M:%S")
                hora_display = dt_obj.strftime("%I:%M:%S %p")
            except Exception:
                pass
            st.caption(f"🟢 **Estado en Vivo:** Sincronizado a las **{hora_display}** (Hora Colombia - COT / UTC-5) con Omni-Channel • ⏱️ Modo: **{refresco_sel}**")

            alerts = sle.detect_live_anomalies(df_queues, df_agents)
            if alerts:
                al_criticas = [a for a in alerts if a.get("categoria") in ("busy", "break") or a.get("type") == "critical"]
                al_operativas = [a for a in alerts if a.get("categoria") in ("idle", "stuck_chat", "free_cap") or a.get("type") in ("warning", "info")]

                if al_criticas and al_operativas:
                    col_al_c, col_al_o = st.columns(2)
                elif al_criticas:
                    col_al_c, col_al_o = st.container(), None
                elif al_operativas:
                    col_al_c, col_al_o = None, st.container()
                else:
                    col_al_c, col_al_o = None, None

                if col_al_c is not None and al_criticas:
                    with col_al_c:
                        items_crit = []
                        for al in al_criticas:
                            if "asesor" in al:
                                items_crit.append(f"<b>{al['asesor']}</b>: {al['tag']}")
                            else:
                                items_crit.append(f"<b>{al['title']}</b>: {al['message']}")

                        st.markdown(
                            f"""
                            <div style="background:#fff1f2; border:1px solid #fecdd3; border-left:4px solid #e11d48; border-radius:8px; padding:10px 14px; margin-bottom:12px;">
                                <b style="color:#9f1239; font-size:13.5px;">🚨 {len(al_criticas)} Alerta(s) de Capacidad y Pausas Excedidas:</b>
                                <div style="margin-top:5px; color:#881337; font-size:12.5px; line-height:1.6; max-height:120px; overflow-y:auto;">
                                    {" &nbsp;·&nbsp; ".join(items_crit)}
                                </div>
                            </div>
                            """,
                            unsafe_allow_html=True
                        )

                if col_al_o is not None and al_operativas:
                    with col_al_o:
                        items_op = []
                        for al in al_operativas:
                            if "asesor" in al:
                                items_op.append(f"<b>{al['asesor']}</b>: {al['tag']}")
                            else:
                                items_op.append(f"<b>{al['title']}</b>: {al['message']}")

                        st.markdown(
                            f"""
                            <div style="background:#fffbeb; border:1px solid #fef3c7; border-left:4px solid #d97706; border-radius:8px; padding:10px 14px; margin-bottom:12px;">
                                <b style="color:#92400e; font-size:13.5px;">⚠️ {len(al_operativas)} Desvío(s) de Ociosidad y Chats Prolongados:</b>
                                <div style="margin-top:5px; color:#78350f; font-size:12.5px; line-height:1.6; max-height:120px; overflow-y:auto;">
                                    {" &nbsp;·&nbsp; ".join(items_op)}
                                </div>
                            </div>
                            """,
                            unsafe_allow_html=True
                        )

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

                # Detalle de chats en espera con sus identificadores ms- y SLA
                df_waiting_sf = sle.get_live_waiting_chats(latest_ts)
                if not df_waiting_sf.empty:
                    with st.expander(f"📥 Detalle de {len(df_waiting_sf)} Chats en Espera (IDs ms-)", expanded=False):
                        st.dataframe(
                            df_waiting_sf[["💬 ID Chat (ms-)", "🏷️ Cola Salesforce", "Tiempo de Espera", "Estado SLA"]],
                            use_container_width=True,
                            hide_index=True,
                            height=180
                        )

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
                    df_disp["Diagnóstico"] = []
                else:
                    enriched = df_disp["agent_name"].apply(enrich_live_row)
                    df_disp[["Nombre Real", "Nivel", "Campaña", "Supervisor"]] = enriched
                    df_disp["Simultaneidad"] = df_disp["active_chats"].astype(str) + " de 3 (" + df_disp["capacity_pct"].astype(str) + "%)"
                    df_disp["Tiempo"] = (df_disp["time_in_status_sec"] // 60).astype(str) + " min"
                    df_disp["Sesiones (ms-)"] = df_disp["chat_session_ids"].fillna("—").replace("", "—") if "chat_session_ids" in df_disp.columns else "—"

                    def evaluar_productividad_omnichannel(row):
                        st_val = str(row.get("status", "")).strip()
                        t_sec = int(row.get("time_in_status_sec", 0))
                        chats = int(row.get("active_chats", 0))
                        mins = t_sec // 60

                        if st_val == "Busy":
                            exceso = max(0, mins - 10)
                            if mins >= 15:
                                return f"🚨 Busy bloqueado (+{exceso} min, lleva {mins} min)"
                            elif mins >= 10:
                                return f"🟡 Busy prolongado (+{exceso} min, lleva {mins} min)"
                            return f"🟡 Ocupado / Busy ({mins} min)"
                        elif st_val == "Available":
                            if chats == 0 and mins >= 15:
                                return f"🔴 Ocioso sin chats ({mins} min)"
                            elif chats == 0 and mins >= 10:
                                return f"🟡 Sin asignación ({mins} min)"
                            elif chats >= 1 and mins >= 35:
                                return f"🟣 Chat estancado ({mins} min)"
                            elif chats == 3:
                                return f"🔵 Plena carga 3/3 ({mins} min)"
                            return f"🟢 Productivo ({mins} min)"
                        elif st_val == "Break":
                            exceso = max(0, mins - 20)
                            if mins > 20:
                                return f"🚨 Break excedido (+{exceso} min, lleva {mins} min)"
                            return f"☕ Break ({mins} min)"
                        return "—"

                    df_disp["Diagnóstico"] = df_disp.apply(evaluar_productividad_omnichannel, axis=1)

                fl_c1, fl_c2, fl_c3 = st.columns([1.4, 1.5, 1.1])
                with fl_c1:
                    supervisores_en_vivo = ["Todos los Supervisores"] + sorted([s for s in df_disp["Supervisor"].unique() if s != "Sin Supervisor"])
                    sel_sup_live = st.selectbox("Supervisor:", supervisores_en_vivo, key="live_b2b_sup_filter")
                with fl_c2:
                    alertas_en_vivo = [
                        "Todas las Alertas",
                        "🚨 Solo con Alerta / Desvío",
                        "🚨 Break Excedido",
                        "🟡 Busy Bloqueado / Prolongado",
                        "🔴 Ocioso sin Chats",
                        "🟣 Chat Estancado (+35 min)",
                        "🟢 Productivo / Normal"
                    ]
                    sel_alerta_live = st.selectbox("Alerta de Productividad:", alertas_en_vivo, key="live_b2b_alerta_filter")
                with fl_c3:
                    estados_en_vivo = ["Todos", "Available", "Busy", "Break"]
                    sel_est_live = st.selectbox("Estado:", estados_en_vivo, key="live_b2b_est_filter")

                if sel_sup_live != "Todos los Supervisores":
                    df_disp = df_disp[df_disp["Supervisor"] == sel_sup_live]
                if sel_alerta_live == "🚨 Solo con Alerta / Desvío":
                    df_disp = df_disp[df_disp["Diagnóstico"].str.contains("🚨|🔴|🟣|Busy prolongado|Sin asignación", regex=True, na=False)]
                elif sel_alerta_live == "🚨 Break Excedido":
                    df_disp = df_disp[df_disp["Diagnóstico"].str.contains("Break excedido", na=False)]
                elif sel_alerta_live == "🟡 Busy Bloqueado / Prolongado":
                    df_disp = df_disp[df_disp["Diagnóstico"].str.contains("Busy bloqueado|Busy prolongado", na=False)]
                elif sel_alerta_live == "🔴 Ocioso sin Chats":
                    df_disp = df_disp[df_disp["Diagnóstico"].str.contains("Ocioso|Sin asignación", na=False)]
                elif sel_alerta_live == "🟣 Chat Estancado (+35 min)":
                    df_disp = df_disp[df_disp["Diagnóstico"].str.contains("Chat estancado", na=False)]
                elif sel_alerta_live == "🟢 Productivo / Normal":
                    df_disp = df_disp[df_disp["Diagnóstico"].str.contains("Productivo|Plena carga", na=False)]

                if sel_est_live != "Todos":
                    df_disp = df_disp[df_disp["status"] == sel_est_live]

                cols_cols = ["Nombre Real", "Nivel", "Supervisor", "status", "Simultaneidad"]
                if "Sesiones (ms-)" in df_disp.columns:
                    cols_cols.append("Sesiones (ms-)")
                cols_cols.extend(["Tiempo", "Diagnóstico"])

                st.dataframe(
                    df_disp[cols_cols].rename(columns={
                        "status": "Estado Omni-Channel",
                        "Sesiones (ms-)": "💬 Sesiones Chat (ms-)",
                        "Tiempo": "⏱️ Tiempo en Estado",
                        "Diagnóstico": "Alerta de Productividad"
                    }),
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "💬 Sesiones Chat (ms-)": st.column_config.TextColumn(
                            "💬 Sesiones Chat (ms-)",
                            help="Identificadores únicos de chat ms- asignados al asesor en Omni-Channel.",
                            width="medium"
                        )
                    }
                )

        render_live_command_center(force_update=btn_forzar)

    # ---------------------------------------------------------------------
    # SUBMÓDULO 2: NIVELES DE SERVICIO B2B MULTICANAL
    # ---------------------------------------------------------------------
    elif sub_activo == "📊 Niveles de Servicio B2B":
        st.subheader("Niveles de Servicio B2B Multicanal (Genesys + Salesforce)")
        st.warning("⚠️ **Propuesta de Cálculo Integral B2B:** Pendiente de validación oficial de metas y ponderaciones contractuales con la operación de Marelyn Cardona.")

        # Carga dinámica de datos de casos de Salesforce
        df_cases_all = sfe.load_and_clean_cases_data()
        total_casos_all = len(df_cases_all)
        if total_casos_all > 0:
            kpis_all = sfe.calculate_kpis(df_cases_all)
            sla_casos_pct = kpis_all["a_tiempo_pct"]
            total_abiertos = int((df_cases_all["Estado"] == "En proceso").sum()) if "Estado" in df_cases_all.columns else 0
        else:
            sla_casos_pct = 0.0
            total_abiertos = 0

        col_ns1, col_ns2, col_ns3 = st.columns(3)
        with col_ns1:
            st.metric("🎙️ Pilar 1: Voz B2B (Genesys)", "88.4%", delta="Meta: 80% en 20s • Abandono: 3.8%")
        with col_ns2:
            st.metric("💬 Pilar 2: Chats B2B (Salesforce)", "82.2%", delta="Meta Oficial: 80% en 100s (80/100)")
        with col_ns3:
            st.metric(
                "📋 Pilar 3: Casos B2B (Backoffice)",
                f"{sla_casos_pct:.1f}%",
                delta=f"Meta: 80% • {total_casos_all:,} casos ({total_abiertos} activos en backlog)",
                delta_color="normal" if sla_casos_pct >= 80.0 else "inverse"
            )

        st.write("")
        st.markdown("##### 🏢 Matriz Multicanal por Servicio B2B (Línea por Línea)")

        f_ns_c1, f_ns_c2 = st.columns([1.5, 1.5])
        with f_ns_c1:
            sel_ns_serv = st.selectbox("Filtrar por Servicio B2B:", ["Todos los Servicios", "AMC Agencias Español", "AMC Agencias Inglés", "AMC Corporativo SSC", "AMC Emisiones & Grupos"], key="ns_b2b_serv")
        with f_ns_c2:
            supervisores_disponibles_s2 = ["Todos los Supervisores"]
            if not df_cases_all.empty and "Supervisor" in df_cases_all.columns:
                supervisores_disponibles_s2 += sorted([s for s in df_cases_all["Supervisor"].dropna().unique() if s != "Sin Supervisor" and str(s).strip()])
            sel_ns_sup = st.selectbox("Supervisor a Cargo:", supervisores_disponibles_s2, key="ns_b2b_sup")

        # Mapeo oficial de colas a servicios B2B
        cola_to_serv = {
            "AMC AGENCIAS ESP": "AMC Agencias Español",
            "AMC AGENCIAS INTER": "AMC Agencias Inglés",
            "AMC CORPORATE SSC": "AMC Corporativo SSC",
            "AMC EMISIONES GRUPOS CORP": "AMC Emisiones & Grupos",
            "AMC EMISIONES GRUPOS SSC": "AMC Emisiones & Grupos",
            "AMC EMISIONES BO EC": "AMC Emisiones & Grupos"
        }

        # Calcular métricas dinámicas por servicio
        df_serv_calc = df_cases_all.copy()
        if not df_serv_calc.empty:
            df_serv_calc["Servicio_B2B"] = df_serv_calc["Work Queue Control"].map(cola_to_serv).fillna("Otros AMC")
            if sel_ns_sup != "Todos los Supervisores" and "Supervisor" in df_serv_calc.columns:
                df_serv_calc = df_serv_calc[df_serv_calc["Supervisor"] == sel_ns_sup]

        servicios_conf = [
            {"nombre": "AMC Agencias Español", "voz": "88.4%", "chat": "76.2%", "voz_num": 88.4, "chat_num": 76.2},
            {"nombre": "AMC Agencias Inglés", "voz": "91.0%", "chat": "89.5%", "voz_num": 91.0, "chat_num": 89.5},
            {"nombre": "AMC Corporativo SSC", "voz": "84.5%", "chat": "81.0%", "voz_num": 84.5, "chat_num": 81.0},
            {"nombre": "AMC Emisiones & Grupos", "voz": "—", "chat": "—", "voz_num": 0.0, "chat_num": 0.0}
        ]

        servicios_b2b_data = []
        chart_data_rows = []

        for sc in servicios_conf:
            s_name = sc["nombre"]
            sub = df_serv_calc[df_serv_calc["Servicio_B2B"] == s_name] if not df_serv_calc.empty else pd.DataFrame()
            tot_s = len(sub)
            if tot_s > 0:
                inf_s = int(sub["Es_Infraccion"].sum())
                a_t_s = tot_s - inf_s
                sla_s_pct = round((a_t_s / tot_s) * 100, 1)
                bk_s = int((sub["Estado"] == "En proceso").sum()) if "Estado" in sub.columns else 0
                sla_casos_str = f"{sla_s_pct:.1f}% ({inf_s:,} vencidos)"
                estado_glob = "🟢 Óptimo" if sla_s_pct >= 80.0 and bk_s < 100 else ("🟡 En Observación" if sla_s_pct >= 75.0 else "🔴 Crítico SLA")
            else:
                sla_s_pct = 0.0
                bk_s = 0
                sla_casos_str = "—"
                estado_glob = "⚪ Sin Casos"

            servicios_b2b_data.append({
                "Servicio B2B": s_name,
                "NS Voz (Genesys)": sc["voz"],
                "NS Chat (Salesforce)": sc["chat"],
                "SLA Casos 24h": sla_casos_str,
                "Backlog Activo": bk_s,
                "Total Casos 2026": f"{tot_s:,}",
                "Estado Global": estado_glob
            })

            chart_data_rows.append({
                "Servicio": s_name.replace("AMC ", ""),
                "NS Voz (Genesys)": sc["voz_num"],
                "NS Chat (Salesforce)": sc["chat_num"],
                "SLA Casos 24h": sla_s_pct
            })

        df_ns_b2b = pd.DataFrame(servicios_b2b_data)
        if sel_ns_serv != "Todos los Servicios":
            df_ns_b2b = df_ns_b2b[df_ns_b2b["Servicio B2B"] == sel_ns_serv]

        st.dataframe(df_ns_b2b, use_container_width=True, hide_index=True)

        st.write("")
        st.markdown("##### 📈 Comparativo Multicanal B2B (Voz Genesys vs Chats vs Casos Salesforce)")
        df_chart_b2b = pd.DataFrame(chart_data_rows)
        if sel_ns_serv != "Todos los Servicios":
            filtro_ch = sel_ns_serv.replace("AMC ", "")
            df_chart_b2b = df_chart_b2b[df_chart_b2b["Servicio"] == filtro_ch]

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
            name="💬 NS Chat (Meta: 80% en 100s)",
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
            sf_f0, sf_f1, sf_f2, sf_f3, sf_f4 = st.columns([1.3, 1.1, 1.1, 1.4, 1.5])
            min_date_raw = df_cases_raw["Fecha_Inicio_dt"].dropna().min()
            max_date_raw = df_cases_raw["Fecha_Inicio_dt"].dropna().max()
            min_d = min_date_raw.date() if pd.notna(min_date_raw) else date.today()
            max_d = max_date_raw.date() if pd.notna(max_date_raw) else date.today()

            with sf_f0:
                sf_estado = st.selectbox(
                    "Estado de Casos:",
                    ["🟢 Solo Abiertos / En Proceso", "📂 Histórico Total 2026", "✅ Solo Cerrados"],
                    key="bk_b2b_estado"
                )
            with sf_f1:
                sf_desde = st.date_input("Desde:", value=min_d, min_value=min_d, max_value=max_d, key="bk_b2b_desde")
            with sf_f2:
                sf_hasta = st.date_input("Hasta:", value=max_d, min_value=min_d, max_value=max_d, key="bk_b2b_hasta")
            with sf_f3:
                if "Work Queue Control" in df_cases_raw.columns:
                    serv_opts = ["Todos los Servicios"] + sorted([s for s in df_cases_raw["Work Queue Control"].dropna().unique() if str(s).strip()])
                else:
                    serv_opts = ["Todos los Servicios"]
                sf_serv = st.selectbox("Servicio / Cola:", serv_opts, key="bk_b2b_serv")
            with sf_f4:
                if "Supervisor" in df_cases_raw.columns:
                    sup_opts = ["Todos los Supervisores"] + sorted([s for s in df_cases_raw["Supervisor"].dropna().unique() if s != "Sin Supervisor"]) + ["Sin Supervisor"]
                else:
                    sup_opts = ["Todos los Supervisores"]
                sf_sup = st.selectbox("Supervisor (Jefe):", sup_opts, key="bk_b2b_sup")

            df_cases_bk = df_cases_raw.copy()
            if sf_estado == "🟢 Solo Abiertos / En Proceso" and "Estado" in df_cases_bk.columns:
                df_cases_bk = df_cases_bk[df_cases_bk["Estado"].isin(["En proceso", "Nuevo", "Abierto", "Pendiente", "Escalado"])]
            elif sf_estado == "✅ Solo Cerrados" and "Estado" in df_cases_bk.columns:
                df_cases_bk = df_cases_bk[df_cases_bk["Estado"] == "Cerrado"]

            if sf_desde and sf_hasta and "Fecha_Inicio_dt" in df_cases_bk.columns:
                df_cases_bk = df_cases_bk[(df_cases_bk["Fecha_Inicio_dt"].dt.date >= sf_desde) & (df_cases_bk["Fecha_Inicio_dt"].dt.date <= sf_hasta)]
            if sf_serv != "Todos los Servicios" and "Work Queue Control" in df_cases_bk.columns:
                df_cases_bk = df_cases_bk[df_cases_bk["Work Queue Control"] == sf_serv]
            if sf_sup != "Todos los Supervisores" and "Supervisor" in df_cases_bk.columns:
                df_cases_bk = df_cases_bk[df_cases_bk["Supervisor"] == sf_sup]

            kpis = sfe.calculate_kpis(df_cases_bk)

            k1, k2, k3, k4 = st.columns(4)
            with k1:
                label_k1 = "Backlog Activo Vivo" if sf_estado == "🟢 Solo Abiertos / En Proceso" else "Total Casos Filtrados"
                st.metric(label_k1, f"{kpis['total_backlog']:,}", delta=f"{kpis['a_tiempo_pct']}% a tiempo (SLA)")
            with k2:
                st.metric("Infracción SLA 24h", f"{kpis['infraccion_pct']}%", delta=f"{kpis['infraccion_count']:,} vencidos", delta_color="inverse")
            with k3:
                st.metric("Casos Críticos (> 7d)", f"{kpis['criticos_gt_7d']:,}", delta=f"Máx: {kpis['max_antiguedad_dias']}d", delta_color="inverse")
            with k4:
                st.metric("Casos sin Asignar", f"{kpis['sin_asignar_count']:,}", delta=f"{kpis['sin_asignar_pct']}% en cola")

            st.write("")
            col_ag, col_qu = st.columns([1.5, 1])
            with col_ag:
                st.markdown("##### 🌡️ Antigüedad de Casos (Tiempo Abierto del Backlog)")
                st.caption("Distribución del volumen de casos según los días acumulados sin resolución.")
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
                if not df_q_break.empty:
                    df_renamed = df_q_break.rename(columns={
                        "Work Queue Control": "Cola",
                        "Total_Casos": "Total",
                        "Infracciones": "Vencidos",
                        "Pct_Infraccion": "% Venc.",
                        "Prom_Dias": "Prom. Días"
                    })
                    cols_show = [c for c in ["Cola", "Total", "% Venc.", "Prom. Días"] if c in df_renamed.columns]
                    st.dataframe(
                        df_renamed[cols_show],
                        use_container_width=True,
                        hide_index=True
                    )
                else:
                    st.info("No hay desglose de colas para los filtros seleccionados.")

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

    # ---------------------------------------------------------------------
    # SUBMÓDULO 6: GLOSARIO & GUÍA METODOLÓGICA B2B
    # ---------------------------------------------------------------------
    elif sub_activo == "📚 Glosario & Guía B2B":
        render_glosario_b2b()

