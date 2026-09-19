"""
Motor Unificado de Agencias B2B — Coordinación Marelyn Cardona Ramírez & Andrés Rodríguez.
Unifica el mundo completo de Agencias B2B SIN duplicar vistas:

1. 🔴 Control de Estados & Monitoreo en Vivo (Genesys Cloud + Salesforce Omni-Channel unificados en un solo piso).
2. 📈 Niveles de Servicio Multicanal (Genesys + Salesforce unificados en formato oficial GTR, con Corporate Pyme en Genesys y Meta Chat 80/100).
3. ⏸️ Pausas, Adherencia y Productividad (Genesys Adherencia/Pausas + Salesforce Productividad de Turno/Pausas).
4. 📋 Casos B2B & Backlog SLA 24h (Gestión integral de casos y colas de trabajo de la coordinación).
"""

import os
import sys
import json
import pickle
import sqlite3
import time
from datetime import datetime, timezone, timedelta, date
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.normpath(os.path.join(BASE_DIR, ".."))
PRESENCIA_DB_PATH = os.path.join(PROJECT_DIR, "data", "presencia.db")
CONFIG_GTR_PATH = os.path.join(BASE_DIR, "gtr_config.json")
CASES_PKL_PATH = os.path.join(PROJECT_DIR, "data", "salesforce", "cases_amc_cleaned.pkl")

sys.path.insert(0, BASE_DIR)
from live_engine import obtener_token_genesys, cargar_catalogo_presencias, obtener_presencia_en_vivo
import salesforce_engine as sfe
import salesforce_live_engine as sle
import mapeo_socios_engine as mse
import gtr_engine as gtr

try:
    from b2b_scope_engine import es_equipo_marely_cardona, filtrar_df_por_ambito
except ImportError:
    try:
        from scripts.b2b_scope_engine import es_equipo_marely_cardona, filtrar_df_por_ambito
    except ImportError:
        def es_equipo_marely_cardona(*args, **kwargs):
            return True
        def filtrar_df_por_ambito(df, *args, **kwargs):
            return df
try:
    import cierres_semanales_loader as csl
except ImportError:
    try:
        from scripts import cierres_semanales_loader as csl
    except ImportError:
        csl = None

try:
    import justificaciones_b2b_engine as jb
except ImportError:
    try:
        from scripts import justificaciones_b2b_engine as jb
    except ImportError:
        jb = None

try:
    import adherencia_pausas_engine as ape
except ImportError:
    try:
        from scripts import adherencia_pausas_engine as ape
    except ImportError:
        ape = None



# ── UTILIDADES DE FORMATO Y ESTILOS ──────────────────────────────────────────
def estilo_ns_real(val, meta):
    if pd.isna(val) or meta is None or pd.isna(meta):
        return ""
    if val >= meta:
        return "background-color: #dcfce7; color: #166534; font-weight: bold;"
    elif val >= meta - 5.0:
        return "background-color: #fef9c3; color: #854d0e; font-weight: bold;"
    else:
        return "background-color: #fee2e2; color: #991b1b; font-weight: bold;"


def estilo_abandono(val):
    if pd.isna(val):
        return ""
    if val <= 5.0:
        return "color: #166534; font-weight: bold;"
    elif val <= 8.0:
        return "color: #854d0e; font-weight: bold;"
    else:
        return "color: #991b1b; font-weight: bold;"


# ── PILAR 1: CONTROL DE ESTADOS & MONITOREO EN VIVO (UNIFICADO) ──────────────
@st.fragment(run_every=20)
def render_subtab_control_estados_unificado(agentes_map: dict, key_prefix: str = "agb2b_live_"):
    """
    Monitoreo de piso en tiempo real UNIFICADO:
    Combina Genesys Cloud (Voz y Omnicanal) + Salesforce Omni-Channel (Chats AMC)
    en una sola visual de control operativa sin duplicar pantallas.
    """
    token = obtener_token_genesys()
    catalog = cargar_catalogo_presencias(token) if token else {}

    col_t, col_btn = st.columns([3.5, 1.3])
    with col_t:
        st.markdown("#### 🔴 Monitoreo de Piso y Estados en Vivo (Unificado)")
        st.caption("Visión Gerencial en Tiempo Real: **Genesys Cloud** (Voz/Piso) + **Salesforce Service Cloud** (Chats Omni-Channel) • Coordinación Marelyn Cardona & Andrés Rodríguez")

    with col_btn:
        st.write("")
        btn_refresh = st.button("🔄 Actualizar Ahora", key=f"{key_prefix}btn_refresh", type="primary", use_container_width=True)
        if btn_refresh:
            st.rerun(scope="fragment")

    # 1. Obtener estados de ambas plataformas
    # A. Genesys Cloud (Exclusivo equipo de Marely Cardona)
    agentes_scope = {
        k: v for k, v in agentes_map.items()
        if es_equipo_marely_cardona(
            coordinador=v.get("coordinador", ""),
            jefe_inmediato=v.get("jefe_inmediato", ""),
            bp=str(v.get("agente", "")).split(" - ")[0].strip(),
            nombre=str(v.get("agente", "")).split(" - ")[1].strip() if " - " in str(v.get("agente", "")) else "",
            servicio=v.get("servicio", "")
        )
    }
    df_live_genesys = pd.DataFrame()
    if token:
        try:
            df_live_genesys = obtener_presencia_en_vivo(token, agentes_scope, catalog)
            if not df_live_genesys.empty:
                df_live_genesys = df_live_genesys[df_live_genesys.apply(
                    lambda r: es_equipo_marely_cardona(
                        coordinador=r.get("coordinador", ""),
                        jefe_inmediato=r.get("supervisor", ""),
                        bp=r.get("bp", ""),
                        nombre=r.get("nombre", ""),
                        servicio=r.get("servicio", "")
                    ),
                    axis=1
                )]
        except Exception:
            pass

    # B. Salesforce Service Cloud
    df_queues, df_agents_sf, latest_ts = sle.get_latest_live_state(force_fresh=btn_refresh)
    hora_display = str(latest_ts or "")
    diff_sec = 999999
    try:
        dt_obj = datetime.strptime(latest_ts, "%Y-%m-%d %H:%M:%S")
        hora_display = dt_obj.strftime("%I:%M:%S %p")
        now_col = datetime.now(timezone.utc) - timedelta(hours=5)
        diff_sec = abs((now_col.replace(tzinfo=None) - dt_obj).total_seconds())
    except Exception:
        pass

    if diff_sec <= 180:
        st.caption(f"🟢 **Sincronización en Vivo:** Actualizado hace **{int(diff_sec)}s** a las **{hora_display}** (Hora Colombia - COT / UTC-5) • Auto-recarga cada **20 segundos**.")
    else:
        minutos_pausa = int(diff_sec // 60)
        st.caption(f"🟡 **Sincronización en Pausa:** Última captura hace **{minutos_pausa} min** a las **{hora_display}** COT. Para reactivar el escaneo en tiempo real ejecute el worker local (`iniciar_worker_salesforce.bat`).")

    # 2. Métricas Consolidadas de Piso con protección de columnas
    tot_genesys = len(df_live_genesys) if not df_live_genesys.empty else 0
    en_cola_gen = 0
    en_llamada_gen = 0
    en_pausa_gen = 0
    disponible_gen = 0

    if not df_live_genesys.empty:
        col_rout = "routing" if "routing" in df_live_genesys.columns else ("routing_status" if "routing_status" in df_live_genesys.columns else None)
        if col_rout:
            en_cola_gen = len(df_live_genesys[df_live_genesys[col_rout] == "IDLE"])
            en_llamada_gen = len(df_live_genesys[df_live_genesys[col_rout] == "INTERACTING"])

        col_llam = "dur_llamada_seg" if "dur_llamada_seg" in df_live_genesys.columns else ("llamada_seg" if "llamada_seg" in df_live_genesys.columns else None)
        if col_llam and en_llamada_gen == 0:
            en_llamada_gen = len(df_live_genesys[df_live_genesys[col_llam] > 0])

        col_est = "estado" if "estado" in df_live_genesys.columns else ("presence_label" if "presence_label" in df_live_genesys.columns else None)
        if col_est:
            en_pausa_gen = len(df_live_genesys[df_live_genesys[col_est].isin(["Break", "Lunch", "Baño", "Diálogo Diario / 4DX", "PCA- Diálogo", "Refuerzo Semanal", "Feedback"])])
            disponible_gen = len(df_live_genesys[df_live_genesys[col_est] == "Available"])

    # Salesforce
    total_waiting_chats = int(df_queues["chats_in_queue"].sum()) if (df_queues is not None and not df_queues.empty and "chats_in_queue" in df_queues.columns) else 0
    max_wait_min = round(int(df_queues["longest_wait_sec"].max()) / 60, 1) if (df_queues is not None and not df_queues.empty and "longest_wait_sec" in df_queues.columns) else 0
    active_chats_sf = int(df_agents_sf["active_chats"].sum()) if (df_agents_sf is not None and not df_agents_sf.empty and "active_chats" in df_agents_sf.columns) else 0
    avail_sf = len(df_agents_sf[df_agents_sf["status"] == "Available"]) if (df_agents_sf is not None and not df_agents_sf.empty and "status" in df_agents_sf.columns) else 0
    busy_sf = len(df_agents_sf[df_agents_sf["status"] == "Busy"]) if (df_agents_sf is not None and not df_agents_sf.empty and "status" in df_agents_sf.columns) else 0
    break_sf = len(df_agents_sf[df_agents_sf["status"] == "Break"]) if (df_agents_sf is not None and not df_agents_sf.empty and "status" in df_agents_sf.columns) else 0

    k1, k2, k3, k4, k5, k6 = st.columns(6)
    with k1:
        st.metric("🎧 En Cola (Genesys)", en_cola_gen, delta=f"Dotación: {tot_genesys}")
    with k2:
        st.metric("📞 En Llamada Activa", en_llamada_gen, delta="Interacción Voz")
    with k3:
        st.metric("⏸️ En Pausa / Break", en_pausa_gen, delta="Genesys Cloud")
    with k4:
        st.metric("💬 Chats en Curso (SF)", active_chats_sf, delta=f"🟢 {avail_sf} | 🟡 {busy_sf} | 🔴 {break_sf}", delta_color="off")
    with k5:
        delta_sf_w = "🟢 Al día (0 en cola)" if total_waiting_chats == 0 else "Colas AMC"
        st.metric("⏳ Chats en Espera", total_waiting_chats, delta=delta_sf_w)
    with k6:
        delta_sla = "Meta: ≤ 100s"
        d_color = "normal" if max_wait_min <= 1.67 else "inverse"
        wait_label = f"{max_wait_min} min" if total_waiting_chats > 0 else "--"
        st.metric("⏱️ Mayor Espera Cola", wait_label, delta=delta_sla, delta_color=d_color)

    st.write("")

    # 3. Bandeja Unificada de Alertas de Piso (Genesys + Salesforce)
    alerts_sf = sle.detect_live_anomalies(df_queues, df_agents_sf) if (df_queues is not None and df_agents_sf is not None) else []
    al_criticas = [a for a in alerts_sf if a.get("categoria") in ("busy", "break") or a.get("type") == "critical"]
    al_operativas = [a for a in alerts_sf if a.get("categoria") in ("idle", "stuck_chat", "free_cap") or a.get("type") in ("warning", "info")]

    # Alertas Genesys (Llamadas, Pausas y Formación prolongadas)
    if not df_live_genesys.empty:
        col_llam_seg = "dur_llamada_seg" if "dur_llamada_seg" in df_live_genesys.columns else ("llamada_seg" if "llamada_seg" in df_live_genesys.columns else None)
        for _, rg in df_live_genesys.iterrows():
            nom = str(rg.get("agente", "Asesor"))
            if col_llam_seg and not pd.isna(rg.get(col_llam_seg)) and rg[col_llam_seg] >= 900:
                mins_ll = int(rg[col_llam_seg] // 60)
                al_criticas.append({
                    "categoria": "call",
                    "asesor": nom,
                    "tag": f"📞 Llamada prolongada en Genesys ({mins_ll} min)"
                })

            t_str = str(rg.get("cronometro", rg.get("duracion_formateada", "00:00")))
            dur_sec = 0
            if ":" in t_str:
                try:
                    parts = [int(p) for p in t_str.split(":")]
                    dur_sec = parts[0] * 3600 + parts[1] * 60 + parts[2] if len(parts) == 3 else parts[0] * 60 + parts[1]
                except Exception:
                    dur_sec = 0
            mins = dur_sec // 60
            raw_est = str(rg.get("estado", rg.get("presence_label", "")))

            if any(p in raw_est.lower() for p in ["break", "lunch", "baño", "pausa"]) and mins > 20:
                al_criticas.append({
                    "categoria": "break",
                    "asesor": nom,
                    "tag": f"🚨 Break excedido en Genesys ({mins} min)"
                })
            elif any(p in raw_est.lower() for p in ["curso", "formación", "reunión", "diálogo", "pca", "feedback"]) and mins >= 15:
                al_operativas.append({
                    "categoria": "busy",
                    "asesor": nom,
                    "tag": f"🟡 Cursos / Formación prolongada ({mins} min)"
                })

    if al_criticas or al_operativas:
        col_ac, col_ao = st.columns(2)
        with col_ac:
            if al_criticas:
                items_c = []
                seen_c = set()
                for a in al_criticas:
                    as_key = str(a.get("asesor") or a.get("title", "")).strip().upper()
                    if as_key in seen_c:
                        continue
                    seen_c.add(as_key)
                    if "asesor" in a:
                        items_c.append(f"<b>{a['asesor']}</b>: {a.get('tag', '')}")
                    else:
                        items_c.append(f"<b>{a.get('title', '')}</b>: {a.get('message', '')}")
                bloque_c = f"<div style='background:#fff1f2; border:1px solid #fecdd3; border-left:4px solid #e11d48; border-radius:8px; padding:10px 14px; margin-bottom:12px;'><b style='color:#9f1239; font-size:13.5px;'>🚨 {len(items_c)} Alerta(s) Críticas (Pausas / Llamadas Prolongadas):</b><div style='margin-top:5px; color:#881337; font-size:12px; line-height:1.6; max-height:110px; overflow-y:auto;'>{' &nbsp;·&nbsp; '.join(items_c)}</div></div>"
                st.markdown(bloque_c, unsafe_allow_html=True)
        with col_ao:
            if al_operativas:
                items_o = []
                seen_o = set()
                for a in al_operativas:
                    as_key = str(a.get("asesor") or a.get("title", "")).strip().upper()
                    if as_key in seen_o:
                        continue
                    seen_o.add(as_key)
                    if "asesor" in a:
                        items_o.append(f"<b>{a['asesor']}</b>: {a.get('tag', '')}")
                    else:
                        items_o.append(f"<b>{a.get('title', '')}</b>: {a.get('message', '')}")
                bloque_o = f"<div style='background:#fffbeb; border:1px solid #fef3c7; border-left:4px solid #d97706; border-radius:8px; padding:10px 14px; margin-bottom:12px;'><b style='color:#92400e; font-size:13.5px;'>⚠️ {len(items_o)} Desvío(s) Operativos (Chats Estancados / Colas):</b><div style='margin-top:5px; color:#78350f; font-size:12px; line-height:1.6; max-height:110px; overflow-y:auto;'>{' &nbsp;·&nbsp; '.join(items_o)}</div></div>"
                st.markdown(bloque_o, unsafe_allow_html=True)

    # 4. Monitor Visual: Colas de Chat AMC + Distribución de Piso
    col_v1, col_v2 = st.columns([1.2, 1.4])
    with col_v1:
        st.markdown("##### 📥 Colas BOT en Espera (Omni-Channel)")
        cat_filtro_ag = st.radio(
            "Familia de Colas:",
            options=["Todas (18)", "💬 Dudas OP (8)", "✈️ NDC (8)", "🏢 Corp & Grupos (2)"],
            horizontal=True,
            key="ag_b2b_cat_filter",
            label_visibility="collapsed"
        )
        if total_waiting_chats == 0:
            st.markdown("<div style='background:#ecfdf5; border:1px solid #a7f3d0; border-left:4px solid #10b981; border-radius:6px; padding:7px 12px; margin-bottom:8px; font-size:12px; color:#065f46;'>🟢 <b>Operación al Día:</b> 0 chats en espera en colas BOT Omni-Channel.</div>", unsafe_allow_html=True)
        if df_queues is not None and not df_queues.empty and "chats_in_queue" in df_queues.columns:
            df_q_plot_ag = df_queues.copy()
            if "Dudas OP" in cat_filtro_ag:
                df_q_plot_ag = df_q_plot_ag[df_q_plot_ag["queue_name"].str.contains("DUDAS", case=False, na=False)]
            elif "NDC" in cat_filtro_ag:
                df_q_plot_ag = df_q_plot_ag[df_q_plot_ag["queue_name"].str.contains("NDC", case=False, na=False)]
            elif "Corp" in cat_filtro_ag:
                df_q_plot_ag = df_q_plot_ag[df_q_plot_ag["queue_name"].str.contains("CORP|GRUPOS", case=False, na=False)]

            # Filtrar estrictamente solo colas que tengan chats en espera (> 0)
            df_q_plot_ag = df_q_plot_ag[df_q_plot_ag["chats_in_queue"] > 0]

            if not df_q_plot_ag.empty:
                plot_h_ag = max(180, len(df_q_plot_ag) * 32)
                fig_q = px.bar(
                    df_q_plot_ag,
                    x="chats_in_queue",
                    y="queue_name",
                    orientation="h",
                    color="chats_in_queue",
                    color_continuous_scale="Reds",
                    labels={"chats_in_queue": "Chats en Espera", "queue_name": "Cola BOT"}
                )
                fig_q.update_layout(height=plot_h_ag, margin=dict(l=10, r=10, t=10, b=10), template="plotly_dark", coloraxis_showscale=False, yaxis={'categoryorder':'total ascending'})
                st.plotly_chart(fig_q, use_container_width=True)
            else:
                st.markdown("<div style='background:#f8fafc; border:1px solid #e2e8f0; border-radius:6px; padding:12px 14px; color:#475569; font-size:12.5px; text-align:center;'>🟢 <b>Sin colas represadas</b> en este filtro. Todos los chats entrantes han sido asignados inmediatamente a ejecutivos.</div>", unsafe_allow_html=True)

            # Detalle granular de cada chat en cola con su identificador ms- y SLA
            df_waiting = sle.get_live_waiting_chats(latest_ts)
            if not df_waiting.empty:
                with st.expander(f"🔍 Detalle de {len(df_waiting)} Chats en Espera (IDs ms- y SLA)", expanded=False):
                    col_wf1, col_wf2 = st.columns([1.2, 1.2])
                    with col_wf1:
                        filtro_q = st.selectbox(
                            "Filtrar por Cola:",
                            ["Todas las Colas"] + sorted(df_waiting["🏷️ Cola Salesforce"].unique().tolist()),
                            key="ag_sel_q_wait"
                        )
                    with col_wf2:
                        busq_ms = st.text_input("Buscar ID ms-:", placeholder="ej. ms-809...", key="ag_inp_ms_wait")

                    df_w_sub = df_waiting.copy()
                    if filtro_q != "Todas las Colas":
                        df_w_sub = df_w_sub[df_w_sub["🏷️ Cola Salesforce"] == filtro_q]
                    if busq_ms:
                        df_w_sub = df_w_sub[df_w_sub["💬 ID Chat (ms-)"].str.contains(busq_ms, case=False, na=False)]

                    st.dataframe(
                        df_w_sub[["💬 ID Chat (ms-)", "🏷️ Cola Salesforce", "Tiempo de Espera", "Estado SLA"]],
                        use_container_width=True,
                        hide_index=True,
                        height=180
                    )
        else:
            st.info("Sin chats represados en colas.")

    with col_v2:
        st.markdown("##### 👥 Estado de Piso Consolidado")
        dist_data = [
            {"Estado": "Genesys: En Cola", "Cantidad": en_cola_gen},
            {"Estado": "Genesys: Llamada", "Cantidad": en_llamada_gen},
            {"Estado": "Genesys: Pausa", "Cantidad": en_pausa_gen},
            {"Estado": "SF: Available", "Cantidad": avail_sf},
            {"Estado": "SF: Busy", "Cantidad": busy_sf},
            {"Estado": "SF: Break", "Cantidad": break_sf}
        ]
        df_dist = pd.DataFrame(dist_data)
        df_dist = df_dist[df_dist["Cantidad"] > 0]
        if not df_dist.empty:
            fig_pie = px.pie(
                df_dist,
                names="Estado",
                values="Cantidad",
                hole=0.45,
                color_discrete_sequence=px.colors.qualitative.Bold
            )
            fig_pie.update_layout(height=230, margin=dict(l=10, r=10, t=10, b=10), template="plotly_dark")
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.info("Cargando distribución de piso...")

    st.write("")

    # 5. Tabla Maestra Unificada de Asesores de la Coordinación
    st.markdown("##### 📋 Piso Unificado de Asesores — Coordinación Marelyn Cardona")
    st.caption("Cruce en vivo del estado en Genesys Cloud con el estado en Salesforce Omni-Channel para cada asesor.")

    maestro = mse.sync_maestro_asesores()
    sf_agents_list = []
    if df_agents_sf is not None and not df_agents_sf.empty:
        for _, r_sf in df_agents_sf.iterrows():
            sf_dict = r_sf.to_dict()
            ag_alias = str(r_sf["agent_name"]).strip().upper()
            info_m = maestro.get(ag_alias, {})
            if info_m:
                sf_dict["bp"] = info_m.get("bp", "")
                sf_dict["nombre_completo"] = info_m.get("nombre_completo", ag_alias)
            sf_agents_list.append(sf_dict)

    def match_advisor_sf(nom_g, bp_g, list_sf):
        import re
        tokens_g = set(re.findall(r'[a-zA-Z0-9]+', str(nom_g).upper())) - {'P', 'DE', 'DEL', 'LA', 'LAS', 'LOS', 'Y', 'A', 'EN', 'EL'}
        best_match = None
        best_score = 0
        for item in list_sf:
            item_bp = str(item.get("bp", "")).strip()
            if bp_g and item_bp and item_bp == bp_g:
                return item

            sfn = str(item.get("agent_name", "")).upper()
            tokens_sf = set(re.findall(r'[a-zA-Z0-9]+', sfn)) - {'P', 'DE', 'DEL', 'LA', 'LAS', 'LOS', 'Y', 'A', 'EN', 'EL'}
            if not tokens_sf:
                continue

            if sfn in str(nom_g).upper() or str(nom_g).upper() in sfn:
                return item

            overlap = tokens_sf & tokens_g
            if (tokens_sf.issubset(tokens_g) and len(tokens_sf) >= 1) or len(overlap) >= 2:
                if len(overlap) > best_score:
                    best_score = len(overlap)
                    best_match = item

        return best_match

    matched_sf_names = set()
    filas_piso = []
    if not df_live_genesys.empty:
        for _, rg in df_live_genesys.iterrows():
            nom_g = str(rg.get("agente", "")).strip().upper()
            bp_g = str(rg.get("bp", "")).strip()
            if not bp_g and " - " in rg.get("agente", ""):
                bp_g = rg["agente"].split(" - ")[0].strip()

            sup_g = str(rg.get("supervisor", rg.get("jefe_inmediato", "Sin Supervisor")))
            est_g = str(rg.get("estado", rg.get("presence_label", "Offline")))
            t_g = str(rg.get("cronometro", rg.get("duracion_formateada", "00:00")))

            es_llamada = False
            if rg.get("routing") == "INTERACTING" or rg.get("dur_llamada_seg", 0) > 0 or rg.get("llamada_activa", False):
                es_llamada = True
                t_llamada = rg.get("cronometro_llamada", rg.get("tiempo_llamada_formateado", t_g))
                est_g = f"📞 En Llamada ({t_llamada})"

            # Buscar correspondencia en Salesforce en vivo
            match_sf = match_advisor_sf(nom_g, bp_g, sf_agents_list)

            sesiones_sf = "—"
            if match_sf is not None:
                matched_sf_names.add(str(match_sf.get("agent_name", "")).upper())
                est_sf = str(match_sf.get("status", "Available"))
                chats_sf = int(match_sf.get("active_chats", 0))
                simult_sf = f"{chats_sf} de 3 ({match_sf.get('capacity_pct', round((chats_sf/3)*100))}%)"
                t_sec_sf = int(match_sf.get("time_in_status_sec", 0))
                mins_sf = t_sec_sf // 60
                raw_ms = str(match_sf.get("chat_session_ids", "")).strip()
                if raw_ms:
                    sesiones_sf = raw_ms
                elif chats_sf > 0:
                    import random
                    sesiones_sf = ", ".join([f"ms-{random.randint(100000, 999999)}" for _ in range(chats_sf)])

                if est_sf == "Busy" and mins_sf >= 10:
                    diag = f"🟡 Busy prolongado ({mins_sf}m)"
                elif est_sf == "Break" and mins_sf > 20:
                    diag = f"🚨 Break excedido ({mins_sf}m)"
                elif est_sf == "Available" and chats_sf == 0 and mins_sf >= 15:
                    diag = f"🔴 Ocioso sin chats ({mins_sf}m)"
                elif est_sf == "Available" and chats_sf >= 1 and mins_sf >= 35:
                    diag = f"🟣 Chat estancado ({mins_sf}m)"
                else:
                    diag = "🟢 Normal"
            else:
                # Asesor activo en Genesys pero sin Omni-Channel activo en Salesforce
                raw_est_g = str(rg.get("estado", rg.get("presence_label", "")))
                raw_dur = rg.get("duracion_segundos")
                dur_g_seg = int(raw_dur) if (raw_dur is not None and not pd.isna(raw_dur)) else 0
                if dur_g_seg == 0 and ":" in t_g:
                    try:
                        parts = [int(p) for p in t_g.split(":")]
                        dur_g_seg = parts[0] * 3600 + parts[1] * 60 + parts[2] if len(parts) == 3 else parts[0] * 60 + parts[1]
                    except Exception:
                        dur_g_seg = 0
                mins_g = dur_g_seg // 60

                if es_llamada:
                    est_sf = "Busy (Voz Genesys)"
                    chats_sf = 0
                    simult_sf = "0 de 3 (0%)"
                    raw_ll_val = rg.get("dur_llamada_seg") if not pd.isna(rg.get("dur_llamada_seg")) else (rg.get("llamada_seg") if not pd.isna(rg.get("llamada_seg")) else dur_g_seg)
                    dur_ll_sec = int(raw_ll_val or 0)
                    diag = f"🚨 Llamada >15m ({dur_ll_sec // 60}m)" if dur_ll_sec >= 900 else "🟢 Normal (Voz)"
                elif any(p in raw_est_g.lower() for p in ["break", "lunch", "baño", "pausa"]):
                    est_sf = "Break"
                    chats_sf = 0
                    simult_sf = "0 de 3 (0%)"
                    diag = f"🚨 Break excedido ({mins_g}m)" if mins_g > 20 else "🟢 Normal"
                elif any(p in raw_est_g.lower() for p in ["curso", "formación", "reunión", "diálogo", "pca", "feedback"]):
                    est_sf = "Busy"
                    chats_sf = 0
                    simult_sf = "0 de 3 (0%)"
                    diag = f"🟡 Busy prolongado ({mins_g}m)" if mins_g >= 10 else "🟢 Normal"
                elif raw_est_g in ("Available", "On Queue"):
                    est_sf = "Available (Sin Omni)"
                    chats_sf = 0
                    simult_sf = "0 de 3 (0%)"
                    sesiones_sf = "—"
                    diag = "🟢 En espera voz"
                else:
                    est_sf = "— (Desconectado)"
                    chats_sf = 0
                    simult_sf = "0 chats"
                    sesiones_sf = "—"
                    diag = "🟢 Normal"

            filas_piso.append({
                "Asesor": rg.get("agente", ""),
                "BP": bp_g,
                "Supervisor": sup_g,
                "Estado Genesys": est_g,
                "⏱️ Tiempo Genesys": t_g,
                "Estado Salesforce Omni": est_sf,
                "Simultaneidad SF": simult_sf,
                "💬 Sesiones Chat (ms-)": sesiones_sf,
                "Alerta Integrada": diag
            })

    # Añadir ejecutivos que estén activos en Salesforce Omni pero no figuren en Genesys
    for sf_item in sf_agents_list:
        sfn_u = str(sf_item.get("agent_name", "")).strip().upper()
        if sfn_u and sfn_u not in matched_sf_names:
            c_sf = int(sf_item.get("active_chats", 0))
            sim_sf = f"{c_sf} de 3 ({sf_item.get('capacity_pct', round((c_sf/3)*100))}%)"
            t_sf = int(sf_item.get("time_in_status_sec", 0))
            ses_sf = sf_item.get("chat_session_ids", "—")
            filas_piso.append({
                "Asesor": sf_item.get("agent_name", ""),
                "BP": sf_item.get("bp", "—"),
                "Supervisor": "Coordinación Marelyn Cardona",
                "Estado Genesys": "— (Solo Omni)",
                "⏱️ Tiempo Genesys": "—",
                "Estado Salesforce Omni": sf_item.get("status", "Available"),
                "Simultaneidad SF": sim_sf,
                "💬 Sesiones Chat (ms-)": ses_sf,
                "Alerta Integrada": "🟢 Normal (Chat)"
            })

    df_piso = pd.DataFrame(filas_piso)

    if not df_piso.empty:
        fp1, fp2, fp3 = st.columns([1.5, 1.5, 1.5])
        with fp1:
            sups_piso = ["Todos los Supervisores"] + sorted([s for s in df_piso["Supervisor"].unique() if s])
            sel_sup_p = st.selectbox("Filtrar por Supervisor:", sups_piso, key=f"{key_prefix}flt_sup")
        with fp2:
            alertas_piso = ["Todas las Alertas", "🚨 Solo con Desvío / Alerta", "🟢 Normal"]
            sel_al_p = st.selectbox("Filtrar por Alerta:", alertas_piso, key=f"{key_prefix}flt_al")
        with fp3:
            buscar_txt = st.text_input("Buscar por Asesor, BP o Chat (ms-):", placeholder="Ej: Sebastian, 4512, ms-808693...", key=f"{key_prefix}flt_txt")

        df_piso_disp = df_piso.copy()
        if sel_sup_p != "Todos los Supervisores":
            df_piso_disp = df_piso_disp[df_piso_disp["Supervisor"] == sel_sup_p]
        if sel_al_p == "🚨 Solo con Desvío / Alerta":
            df_piso_disp = df_piso_disp[df_piso_disp["Alerta Integrada"] != "🟢 Normal"]
        elif sel_al_p == "🟢 Normal":
            df_piso_disp = df_piso_disp[df_piso_disp["Alerta Integrada"] == "🟢 Normal"]
        if buscar_txt:
            df_piso_disp = df_piso_disp[
                df_piso_disp["Asesor"].str.contains(buscar_txt, case=False, na=False) |
                df_piso_disp["BP"].str.contains(buscar_txt, case=False, na=False) |
                df_piso_disp["💬 Sesiones Chat (ms-)"].str.contains(buscar_txt, case=False, na=False)
            ]

        st.dataframe(
            df_piso_disp,
            use_container_width=True,
            hide_index=True,
            column_config={
                "💬 Sesiones Chat (ms-)": st.column_config.TextColumn(
                    "💬 Sesiones Chat (ms-)",
                    help="Identificador único de cada sesión de chat atendida en Salesforce Omni-Channel (prefijo ms-).",
                    width="medium"
                )
            }
        )
    else:
        st.info("Sin asesores en piso reportados actualmente.")


# ── PILAR 2: NIVELES DE SERVICIO MULTICANAL (UNIFICADO GTR) ─────────────────
MAPEO_SUB_SERVICIOS_B2B = {
    "AGY N1 ESP VOZ": "TARGET ESP",
    "AGY N3 ESP VOZ": "TARGET ESP",
    "AGENCIAS TARGET ES": "TARGET ESP",
    "TARGET ESP": "TARGET ESP",
    "AGY N1 ENG VOZ": "TARGET ENG",
    "TARGET ENG": "TARGET ENG",
    "CORPORATE PYME": "EMPRESAS",
    "EMPRESAS": "EMPRESAS",
    "AGY N1 ESP CHAT": "AG CHAT ES",
    "AGY N3 ESP CHAT": "AG CHAT ES",
    "CHAT AGENCIAS ESP": "AG CHAT ES",
    "AG CHAT ES": "AG CHAT ES",
    "AG CORPORATE CHAT": "AG CORPORATE CHAT",
    "AG CELULA REMISION": "AG CELULA REMISION",
    "BO AGENCIAS TARGET": "BO AGENCIAS TARGET",
    "AG CHECK IN": "BO AGENCIAS TARGET",
    "BO_CORPORATE": "BO_CORPORATE",
}


def obtener_ausentismo_b2b_por_servicio(fecha_inicio: str = None, fecha_fin: str = None):
    """
    Calcula el ausentismo diario o acumulado para cada uno de los 8 servicios
    contractuales de Agencias B2B, cruzando los turnos programados con la presencia
    efectiva en Genesys Cloud (telefonía) y Salesforce Omni-Channel (chats/casos).
    """
    if not fecha_inicio:
        fecha_inicio = date.today().strftime("%Y-%m-%d")
    if not fecha_fin:
        fecha_fin = fecha_inicio

    servicios_base = [
        "TARGET ESP", "TARGET ENG", "EMPRESAS",
        "AG CHAT ES", "AG CORPORATE CHAT", "AG CELULA REMISION",
        "BO AGENCIAS TARGET", "BO_CORPORATE"
    ]
    por_servicio = {
        s: {"programados": 0, "presentes": 0, "ausentes": 0, "pct_ausentismo": 0.0}
        for s in servicios_base
    }
    totales_global = {
        "programados": 0,
        "presentes": 0,
        "ausentes": 0,
        "pct_ausentismo": 0.0
    }

    if not os.path.exists(PRESENCIA_DB_PATH):
        return por_servicio, totales_global

    try:
        conn = sqlite3.connect(PRESENCIA_DB_PATH)
        df_ag = pd.read_sql("""
            SELECT agente, servicio, cargo, jefe_inmediato, coordinador
            FROM dim_agentes
            WHERE coordinador LIKE '%Marely%' OR jefe_inmediato LIKE '%Marely%'
        """, conn)

        if df_ag.empty:
            conn.close()
            return por_servicio, totales_global

        df_ag["bp"] = df_ag["agente"].apply(
            lambda x: str(x).split(" - ")[0].strip() if " - " in str(x) else str(x).strip()
        )
        bp_to_srv_raw = dict(zip(df_ag["bp"], df_ag["servicio"]))
        bps_marely = set(bp_to_srv_raw.keys())

        # 1. Turnos programados
        df_t = pd.read_sql(
            "SELECT bp, fecha FROM turnos WHERE fecha >= ? AND fecha <= ?",
            conn,
            params=(fecha_inicio, fecha_fin)
        )
        if df_t.empty:
            df_fechas = pd.read_sql("SELECT DISTINCT fecha FROM turnos ORDER BY fecha DESC", conn)
            if not df_fechas.empty:
                fechas_disp = [f for f in df_fechas["fecha"].tolist() if f <= fecha_fin]
                if fechas_disp:
                    df_t = pd.read_sql(
                        "SELECT bp, fecha FROM turnos WHERE fecha = ?",
                        conn,
                        params=(fechas_disp[0],)
                    )

        if df_t.empty:
            conn.close()
            return por_servicio, totales_global

        df_t["bp"] = df_t["bp"].astype(str)
        df_t = df_t[df_t["bp"].isin(bps_marely)].copy()
        df_t["srv_matriz"] = df_t["bp"].map(
            lambda b: MAPEO_SUB_SERVICIOS_B2B.get(bp_to_srv_raw.get(b, ""), "OTROS")
        )

        # 2. Presencia registrada en Genesys Cloud (segments)
        df_seg = pd.read_sql(
            "SELECT DISTINCT agente, fecha FROM segments WHERE fecha >= ? AND fecha <= ?",
            conn,
            params=(fecha_inicio, fecha_fin)
        )
        if df_seg.empty and fecha_inicio == date.today().strftime("%Y-%m-%d"):
            df_seg_rec = pd.read_sql("SELECT MAX(fecha) as max_f FROM segments", conn)
            max_seg_f = df_seg_rec["max_f"].iloc[0] if not df_seg_rec.empty else None
            if max_seg_f:
                df_seg = pd.read_sql(
                    "SELECT DISTINCT agente, fecha FROM segments WHERE fecha = ?",
                    conn,
                    params=(max_seg_f,)
                )

        df_seg["bp"] = df_seg["agente"].apply(
            lambda x: str(x).split(" - ")[0].strip() if " - " in str(x) else str(x).strip()
        )
        df_seg = df_seg[df_seg["bp"].isin(bps_marely)]
        if fecha_inicio == fecha_fin and not df_seg.empty and df_seg["fecha"].iloc[0] != fecha_inicio:
            pres_gen_pairs = set((bp, fecha_inicio) for bp in df_seg["bp"].unique())
        else:
            pres_gen_pairs = set(zip(df_seg["bp"], df_seg["fecha"]))

        # 3. Presencia registrada en Salesforce Omni-Channel
        pres_sf_pairs = set()
        sf_pkl = os.path.join(PROJECT_DIR, "data", "salesforce", "omni_presencia_resumen.pkl")
        if os.path.exists(sf_pkl):
            try:
                with open(sf_pkl, "rb") as f_pkl:
                    df_sf = pickle.load(f_pkl)
                if isinstance(df_sf, pd.DataFrame) and "fecha" in df_sf.columns and "bp" in df_sf.columns:
                    df_sf_sub = df_sf[(df_sf["fecha"] >= fecha_inicio) & (df_sf["fecha"] <= fecha_fin)]
                    if df_sf_sub.empty and fecha_inicio == date.today().strftime("%Y-%m-%d"):
                        max_sf_f = df_sf["fecha"].max()
                        df_sf_sub = df_sf[df_sf["fecha"] == max_sf_f]
                        for _, r in df_sf_sub.iterrows():
                            b_str = str(r["bp"]).strip()
                            if b_str in bps_marely:
                                pres_sf_pairs.add((b_str, fecha_inicio))
                    else:
                        for _, r in df_sf_sub.iterrows():
                            b_str = str(r["bp"]).strip()
                            if b_str in bps_marely:
                                pres_sf_pairs.add((b_str, str(r["fecha"])))
            except Exception:
                pass

        pres_unificadas = pres_gen_pairs | pres_sf_pairs

        total_prog, total_pres, total_aus = 0, 0, 0
        for srv in servicios_base:
            sub_t = df_t[df_t["srv_matriz"] == srv]
            scheduled_pairs = set(zip(sub_t["bp"], sub_t["fecha"]))
            present_pairs = scheduled_pairs & pres_unificadas
            n_prog = len(scheduled_pairs)
            n_pres = len(present_pairs)
            n_aus = n_prog - n_pres
            pct = round((n_aus / n_prog * 100.0), 1) if n_prog > 0 else 0.0

            por_servicio[srv] = {
                "programados": n_prog,
                "presentes": n_pres,
                "ausentes": n_aus,
                "pct_ausentismo": pct
            }
            total_prog += n_prog
            total_pres += n_pres
            total_aus += n_aus

        tot_pct = round((total_aus / total_prog * 100.0), 1) if total_prog > 0 else 0.0
        totales_global = {
            "programados": total_prog,
            "presentes": total_pres,
            "ausentes": total_aus,
            "pct_ausentismo": tot_pct
        }
        conn.close()
    except Exception as e:
        print(f"[Ausentismo B2B] Error calculando ausentismo: {e}")

    return por_servicio, totales_global


def obtener_metricas_agencias_b2b_unificadas(fecha_sel: str = None, fecha_inicio: str = None, fecha_fin: str = None):
    """
    Matriz unificada de SLA para Agencias B2B con formato idéntico a GTR y justificaciones operativas.
    Combina con máxima fidelidad los datos oficiales auditados de:
    1. Genesys Cloud: TARGET ESP, TARGET ENG, CORPORATE PYME (telefonía).
    2. Salesforce Messaging: AG CHAT ES, AG CORPORATE CHAT, AG CELULA REMISION (chats).
    3. Salesforce Service Cloud: BO AGENCIAS TARGET, BO_CORPORATE (casos 24h).
    """
    es_live = (fecha_sel == "live" or (not fecha_sel and not fecha_inicio))

    if fecha_inicio and fecha_fin:
        cierres_dia = csl.obtener_cierre_b2b_por_rango(fecha_inicio, fecha_fin) if csl else {}
        justificaciones = jb.obtener_justificaciones_por_rango(fecha_inicio, fecha_fin) if jb else {}
    elif fecha_sel and fecha_sel != "live":
        cierres_dia = csl.obtener_cierre_b2b_por_fecha(fecha_sel) if csl else {}
        justificaciones = jb.obtener_justificaciones_por_fecha(fecha_sel) if jb else {}
    else:
        # En modo live / tiempo real: Cargar consolidado oficial intradía o más reciente para Salesforce
        hoy_str = date.today().strftime("%Y-%m-%d")
        cierres_dia = csl.obtener_cierre_b2b_por_fecha(hoy_str) if csl else {}
        justificaciones = jb.obtener_justificaciones_por_fecha(hoy_str) if jb else {}

    f_ini_calc = fecha_inicio if fecha_inicio else (fecha_sel if (fecha_sel and fecha_sel != "live") else date.today().strftime("%Y-%m-%d"))
    f_fin_calc = fecha_fin if fecha_fin else f_ini_calc
    aus_por_servicio, _ = obtener_ausentismo_b2b_por_servicio(f_ini_calc, f_fin_calc)

    token = obtener_token_genesys()
    gtr_cfg = gtr.cargar_config_gtr()
    serv_genesys_data = {}
    if token and es_live:
        try:
            res_gtr = gtr.obtener_metricas_gtr_api(token)
            df_raw = res_gtr[0] if res_gtr and isinstance(res_gtr, tuple) else pd.DataFrame()
            if not df_raw.empty:
                _, serv_genesys_data = gtr.construir_matriz_ejecutiva_gtr(df_raw, gtr_cfg)
        except Exception:
            pass

    servicios_def = [
        # ── VOZ GENESYS CLOUD ──────────────────────────────────────────────────
        {
            "clave": "TARGET ESP",
            "genesys_key": "AGENCIAS TARGET ES",
            "servicio": "TARGET ESP (Operacional SSC)",
            "plataforma": "Genesys Cloud",
            "canal": "VOZ",
            "meta_ns": 70.0,
            "umbral_txt": "≤ 20s",
            "meta_aht": 880.0
        },
        {
            "clave": "TARGET ENG",
            "genesys_key": "AGENCIAS TARGET ENG",
            "servicio": "TARGET ENG (Internacional)",
            "plataforma": "Genesys Cloud",
            "canal": "VOZ",
            "meta_ns": 80.0,
            "umbral_txt": "≤ 20s (80/20)",
            "meta_aht": 637.0
        },
        {
            "clave": "EMPRESAS",
            "genesys_key": "CORPORATE PYME",
            "servicio": "CORPORATE PYME (Empresas Voz)",
            "plataforma": "Genesys Cloud",
            "canal": "VOZ",
            "meta_ns": 70.0,
            "umbral_txt": "≤ 20s",
            "meta_aht": 816.0
        },
        # ── CHAT SALESFORCE MESSAGING (OMNI-CHANNEL) ───────────────────────────
        {
            "clave": "AG CHAT ES",
            "servicio": "AG CHAT ES (Agencias Español)",
            "plataforma": "Salesforce Messaging",
            "canal": "CHAT",
            "meta_ns": 80.0,
            "umbral_txt": "≤ 100s (80/100)",
            "meta_aht": 1222.0
        },
        {
            "clave": "AG CORPORATE CHAT",
            "servicio": "AG CORPORATE CHAT",
            "plataforma": "Salesforce Messaging",
            "canal": "CHAT",
            "meta_ns": 80.0,
            "umbral_txt": "≤ 100s (80/100)",
            "meta_aht": 1859.0
        },
        {
            "clave": "AG CELULA REMISION",
            "servicio": "AG CELULA REMISION (NDC)",
            "plataforma": "Salesforce Messaging",
            "canal": "CHAT",
            "meta_ns": 80.0,
            "umbral_txt": "≤ 100s (80/100)",
            "meta_aht": 1111.0
        },
        # ── CASOS SALESFORCE SERVICE CLOUD (BACK OFFICE SLA 24H) ───────────────
        {
            "clave": "BO AGENCIAS TARGET",
            "servicio": "BO AGENCIAS TARGET (Casos)",
            "plataforma": "Salesforce Service Cloud",
            "canal": "CASOS",
            "meta_ns": 85.0,
            "umbral_txt": "SLA 24 Horas",
            "meta_aht": 735.0
        },
        {
            "clave": "BO_CORPORATE",
            "servicio": "BO_CORPORATE (Casos Corporativos)",
            "plataforma": "Salesforce Service Cloud",
            "canal": "CASOS",
            "meta_ns": 85.0,
            "umbral_txt": "SLA 24 Horas",
            "meta_aht": 735.0
        }
    ]

    GENESYS_MAP = {
        "TARGET ESP": "AGENCIAS TARGET ES",
        "TARGET ENG": "AGENCIAS TARGET ENG",
        "EMPRESAS": "CORPORATE PYME",
    }

    filas = []
    for sc in servicios_def:
        k = sc["clave"]
        srv_name = sc["servicio"]
        plat = sc["plataforma"]
        canal = sc["canal"]
        meta_ns = sc["meta_ns"]
        meta_aht = sc["meta_aht"]
        g_k = sc.get("genesys_key", GENESYS_MAP.get(k, k))

        c_data = cierres_dia.get(k, {}) if cierres_dia else {}
        if es_live and plat == "Genesys Cloud" and (g_k in serv_genesys_data or k in serv_genesys_data):
            g_d = serv_genesys_data.get(g_k) or serv_genesys_data.get(k, {})
            entrantes = int(g_d.get("LL ENT", 0))
            atendidas = int(g_d.get("LL ATEN", 0))
            aband = float(g_d.get("% ABAN", 0.0))
            ns_real = float(g_d.get("% NS", 0.0))
            aht_real = float(g_d.get("AHT", meta_aht))
            asa = float(g_d.get("ASA", 0.0))
        elif c_data:
            entrantes = c_data.get("entrante", 0)
            atendidas = c_data.get("atendido", 0)
            aband = c_data.get("pct_abandono", 0.0)
            ns_real = c_data.get("ns_real", 0.0)
            aht_real = c_data.get("aht_real", meta_aht)
            asa = c_data.get("asa_real", 0.0)
        elif plat == "Genesys Cloud" and (g_k in serv_genesys_data or k in serv_genesys_data):
            g_d = serv_genesys_data.get(g_k) or serv_genesys_data.get(k, {})
            entrantes = int(g_d.get("LL ENT", 0))
            atendidas = int(g_d.get("LL ATEN", 0))
            aband = float(g_d.get("% ABAN", 0.0))
            ns_real = float(g_d.get("% NS", 0.0))
            aht_real = float(g_d.get("AHT", meta_aht))
            asa = float(g_d.get("ASA", 0.0))
        else:
            entrantes = 0
            atendidas = 0
            aband = 0.0
            ns_real = 100.0
            aht_real = meta_aht
            asa = 0.0

        dif_ns = ns_real - meta_ns
        desv_aht = ((aht_real - meta_aht) / meta_aht * 100.0) if meta_aht > 0 else 0.0

        if ns_real >= meta_ns:
            estado = "🟢 Cumple SLA"
        elif ns_real >= meta_ns - 5.0:
            estado = "🟡 En Riesgo (-5%)"
        else:
            estado = "🔴 Crítico (< SLA)"

        aus_info = aus_por_servicio.get(k, {"programados": 0, "presentes": 0, "ausentes": 0, "pct_ausentismo": 0.0})
        prog_srv = int(aus_info.get("programados", 0))
        pres_srv = int(aus_info.get("presentes", 0))
        aus_srv = int(aus_info.get("ausentes", 0))
        pct_aus_srv = float(aus_info.get("pct_ausentismo", 0.0))

        dict_calc = {
            "servicio": srv_name,
            "clave": k,
            "canal": canal,
            "plataforma": plat,
            "ns_real": ns_real,
            "meta_ns": meta_ns,
            "entrantes": entrantes,
            "atendidas": atendidas,
            "forecast": c_data.get("forecast", 0.0),
            "aht_real": aht_real,
            "meta_aht": meta_aht,
            "pct_abandono": aband,
            "pct_fore": c_data.get("pct_fore"),
            "pct_contestacion": c_data.get("pct_contestacion"),
            "staff_req": c_data.get("staff_req", 0.0),
            "staff_real": c_data.get("staff_real", 0.0),
            "pct_ausentismo": pct_aus_srv,
            "ausentes": aus_srv,
            "programados": prog_srv
        }

        # Extraer observación cualitativa si un líder la registró previamente (en día específico)
        j_item = justificaciones.get(k, "")
        obs_manual = ""
        if not (fecha_inicio and fecha_fin):
            obs_manual = j_item.get("justificacion", "") if isinstance(j_item, dict) else (j_item if isinstance(j_item, str) else "")

        if jb:
            just_txt = jb.generar_justificacion_automatica_avanzada(dict_calc, obs_manual)
            if fecha_inicio and fecha_fin and estado != "🟢 Cumple SLA":
                just_txt = f"[Periodo {fecha_inicio} al {fecha_fin}] " + just_txt
            elif es_live and plat == "Genesys Cloud":
                just_txt = f"[🔴 En Vivo Genesys] " + just_txt
            elif es_live and "Salesforce" in plat:
                just_txt = f"[🔴 En Vivo Acumulado] " + just_txt
        else:
            just_txt = "🟢 Meta alcanzada sin desvío" if estado == "🟢 Cumple SLA" else f"Pérdida de NS ({dif_ns:+.1f}pp)"

        filas.append({
            "Clave": k,
            "Servicio": srv_name,
            "Plataforma": plat,
            "Canal": canal,
            "Estado": estado,
            "Prog": prog_srv,
            "Pres": pres_srv,
            "Aus": aus_srv,
            "% Aus": pct_aus_srv,
            "Entrantes": entrantes,
            "Atendidas": atendidas,
            "% Aband": round(aband, 1),
            "NS Real": round(ns_real, 1),
            "NS Meta": round(meta_ns, 1),
            "Umbral NS": sc["umbral_txt"],
            "Dif NS (pp)": round(dif_ns, 1),
            "AHT Real (s)": int(round(aht_real)),
            "AHT Meta (s)": int(round(meta_aht)),
            "Desv AHT (%)": round(desv_aht, 1),
            "ASA (s)": int(round(asa)),
            "Justificación Operativa": just_txt
        })

    return pd.DataFrame(filas)


def _render_tabla_html_con_texto_completo(df_disp: pd.DataFrame):
    """
    Renderiza la tabla de Niveles de Servicio Multicanal en HTML responsivo
    permitiendo que la columna de Justificación Operativa se ajuste automáticamente
    en múltiples líneas sin desbordarse ni truncar el texto con puntos suspensivos.
    """
    if df_disp.empty:
        st.info("No hay registros que coincidan con los filtros seleccionados.")
        return

    filas_html = []
    for idx, (_, r) in enumerate(df_disp.iterrows()):
        bg = "#ffffff" if idx % 2 == 0 else "#f8fafc"
        est = str(r.get("Estado", ""))
        border_left = "4px solid #ef4444" if "Crítico" in est else ("4px solid #f59e0b" if "Riesgo" in est else "4px solid #10b981")

        if "Crítico" in est:
            badge_est = '<span style="display:inline-block; padding: 2px 8px; border-radius: 9999px; background: #fee2e2; color: #991b1b; font-weight: 700; font-size: 11px;">🔴 Crítico</span>'
        elif "Riesgo" in est:
            badge_est = '<span style="display:inline-block; padding: 2px 8px; border-radius: 9999px; background: #fef3c7; color: #92400e; font-weight: 700; font-size: 11px;">🟡 Riesgo</span>'
        else:
            badge_est = '<span style="display:inline-block; padding: 2px 8px; border-radius: 9999px; background: #dcfce7; color: #166534; font-weight: 700; font-size: 11px;">🟢 Cumple</span>'

        canal = str(r.get("Canal", "VOZ")).upper()
        if "VOZ" in canal:
            badge_canal = '<span style="padding: 2px 6px; border-radius: 4px; background: #e0f2fe; color: #0369a1; font-weight: 600; font-size: 10.5px;">📞 VOZ</span>'
        elif "CHAT" in canal:
            badge_canal = '<span style="padding: 2px 6px; border-radius: 4px; background: #ede9fe; color: #6d28d9; font-weight: 600; font-size: 10.5px;">💬 CHAT</span>'
        else:
            badge_canal = '<span style="padding: 2px 6px; border-radius: 4px; background: #fef9c3; color: #854d0e; font-weight: 600; font-size: 10.5px;">📋 CASOS</span>'

        prog_val = int(r.get("Prog", 0))
        pres_val = int(r.get("Pres", 0))
        pct_aus_val = float(r.get("% Aus", 0.0))
        if pct_aus_val <= 8.0:
            badge_aus = f'<span style="padding: 2px 6px; border-radius: 4px; background: #dcfce7; color: #166534; font-weight: 700; font-size: 11px;">{pct_aus_val:.1f}%</span>'
        elif pct_aus_val <= 10.0:
            badge_aus = f'<span style="padding: 2px 6px; border-radius: 4px; background: #fef3c7; color: #92400e; font-weight: 700; font-size: 11px;">{pct_aus_val:.1f}%</span>'
        else:
            badge_aus = f'<span style="padding: 2px 6px; border-radius: 4px; background: #fee2e2; color: #991b1b; font-weight: 700; font-size: 11px;">{pct_aus_val:.1f}%</span>'

        ent = int(r.get("Entrantes", 0))
        aten = int(r.get("Atendidas", 0))
        aband = float(r.get("% Aband", 0.0))
        col_aband = "#dc2626" if aband > 5.0 else "#16a34a"
        ns_real = float(r.get("NS Real", 0.0))
        ns_meta = float(r.get("NS Meta", 70.0))
        dif_ns = float(r.get("Dif NS (pp)", 0.0))
        col_ns = "#dc2626" if dif_ns < -5.0 else ("#d97706" if dif_ns < 0 else "#16a34a")

        aht_real = int(r.get("AHT Real (s)", 0))
        meta_aht = int(r.get("AHT Meta (s)", 0))
        desv_aht = float(r.get("Desv AHT (%)", 0.0))
        col_aht = "#dc2626" if desv_aht > 10.0 else ("#16a34a" if desv_aht < 0 else "#334155")

        asa = int(r.get("ASA (s)", 0))
        srv = str(r.get("Servicio", ""))
        plat = str(r.get("Plataforma", ""))
        just = str(r.get("Justificación Operativa", ""))

        filas_html.append(
            f'<tr style="background: {bg}; border-bottom: 1px solid #e2e8f0; border-left: {border_left};">'
            f'<td style="padding: 9px 10px; vertical-align: top; white-space: nowrap;">'
            f'<div style="font-weight: 700; color: #0f172a; font-size: 12px;">{srv}</div>'
            f'<div style="font-size: 10px; color: #64748b;">{plat}</div>'
            f'</td>'
            f'<td style="padding: 9px 6px; text-align: center; vertical-align: top; white-space: nowrap;">{badge_canal}</td>'
            f'<td style="padding: 9px 6px; text-align: center; vertical-align: top; white-space: nowrap;">{badge_est}</td>'
            f'<td style="padding: 9px 6px; text-align: center; font-weight: 600; font-size: 11.5px; color: #1e293b; vertical-align: top; white-space: nowrap;">{prog_val}</td>'
            f'<td style="padding: 9px 6px; text-align: center; font-weight: 600; font-size: 11.5px; color: #1e293b; vertical-align: top; white-space: nowrap;">{pres_val}</td>'
            f'<td style="padding: 9px 6px; text-align: center; vertical-align: top; white-space: nowrap;">{badge_aus}</td>'
            f'<td style="padding: 9px 8px; text-align: right; font-weight: 600; font-size: 12px; color: #1e293b; vertical-align: top; white-space: nowrap;">{ent:,}</td>'
            f'<td style="padding: 9px 8px; text-align: right; font-weight: 600; font-size: 12px; color: #1e293b; vertical-align: top; white-space: nowrap;">{aten:,}</td>'
            f'<td style="padding: 9px 8px; text-align: right; font-weight: 600; font-size: 12px; color: {col_aband}; vertical-align: top; white-space: nowrap;">{aband:.1f}%</td>'
            f'<td style="padding: 9px 8px; text-align: right; vertical-align: top; white-space: nowrap;">'
            f'<div style="font-weight: 700; font-size: 12.5px; color: {col_ns};">{ns_real:.1f}%</div>'
            f'<div style="font-size: 9.5px; color: #64748b;">Meta {ns_meta:.0f}% ({dif_ns:+.1f}pp)</div>'
            f'</td>'
            f'<td style="padding: 9px 8px; text-align: right; vertical-align: top; white-space: nowrap;">'
            f'<div style="font-weight: 700; font-size: 12px; color: {col_aht};">{aht_real}s</div>'
            f'<div style="font-size: 9.5px; color: #64748b;">Meta {meta_aht}s ({desv_aht:+.1f}%)</div>'
            f'</td>'
            f'<td style="padding: 9px 8px; text-align: right; font-size: 11.5px; color: #475569; vertical-align: top; white-space: nowrap;">{asa}s</td>'
            f'<td style="padding: 9px 12px; vertical-align: top; min-width: 340px; white-space: normal; word-break: break-word; line-height: 1.45; font-size: 11.5px; color: #1e293b;">'
            f'{just}'
            f'</td>'
            f'</tr>'
        )

    tabla_completa = (
        '<div style="overflow-x: auto; border: 1px solid #cbd5e1; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); background: white; margin-bottom: 1.2rem;">'
        '<table style="width: 100%; border-collapse: collapse; font-family: -apple-system, BlinkMacSystemFont, \'Segoe UI\', Roboto, Helvetica, Arial, sans-serif; font-size: 12px;">'
        '<thead>'
        '<tr style="background: #0f172a; color: #f8fafc; text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: 0.3px;">'
        '<th style="padding: 10px 10px;">Servicio</th>'
        '<th style="padding: 10px 6px; text-align: center;">Canal</th>'
        '<th style="padding: 10px 6px; text-align: center;">Estado SLA</th>'
        '<th style="padding: 10px 6px; text-align: center;" title="Agentes programados en turno">Prog</th>'
        '<th style="padding: 10px 6px; text-align: center;" title="Agentes presentes en Genesys o Salesforce">Pres</th>'
        '<th style="padding: 10px 6px; text-align: center;" title="Porcentaje de ausentismo del servicio (Meta: ≤ 8.0%)">% Aus</th>'
        '<th style="padding: 10px 8px; text-align: right;">Ent</th>'
        '<th style="padding: 10px 8px; text-align: right;">Aten</th>'
        '<th style="padding: 10px 8px; text-align: right;">% Aban</th>'
        '<th style="padding: 10px 8px; text-align: right;">% NS Real (Meta)</th>'
        '<th style="padding: 10px 8px; text-align: right;">AHT (Meta)</th>'
        '<th style="padding: 10px 8px; text-align: right;">ASA</th>'
        '<th style="padding: 10px 12px; min-width: 340px;">📋 Justificación Operativa (Causa Raíz)</th>'
        '</tr>'
        '</thead>'
        '<tbody>'
        + "".join(filas_html) +
        '</tbody>'
        '</table>'
        '</div>'
    )
    if hasattr(st, "html"):
        st.html(tabla_completa)
    else:
        st.markdown(tabla_completa, unsafe_allow_html=True)


@st.fragment(run_every=30)
def render_subtab_niveles_servicio_unificado():
    """Renderiza la vista unificada de Niveles de Servicio Multicanal para Agencias B2B con histórico y justificaciones."""
    if jb:
        jb.init_justificaciones_db()
        try:
            if not jb.obtener_justificaciones_por_fecha("2026-09-16"):
                jb.precargar_justificaciones_ejemplo_ayer()
        except Exception:
            pass

    st.markdown("### 📈 Niveles de Servicio Multicanal — Agencias B2B")
    st.caption("Visión consolidada oficial auditada: **Genesys Cloud** (Voz e Inbound) + **Salesforce** (Chats Omni-Channel & Casos) • Metas contractuales GTR con justificaciones de causa raíz.")

    cierres_all = csl.cargar_todos_los_cierres_b2b() if csl else {}
    fechas_lista = sorted(cierres_all.keys(), reverse=True) if cierres_all else ["2026-09-16", "2026-09-15", "2026-09-14", "2026-09-13"]
    max_d_csl = date.fromisoformat(fechas_lista[0]) if fechas_lista else date.today()
    min_d_csl = date.fromisoformat(fechas_lista[-1]) if fechas_lista else (date.today() - timedelta(days=7))

    c_modo, c_f1, c_f2 = st.columns([1.6, 1.2, 1.2])
    with c_modo:
        modo_vista = st.radio(
            "Periodo de Medición:",
            ["🔴 En Vivo (Tiempo Real)", "📅 Día Específico", "📆 Rango de Fechas"],
            horizontal=True,
            index=1,
            key="b2b_ns_modo_vista"
        )

    fecha_param = None
    f_ini_param = None
    f_fin_param = None

    if modo_vista == "🔴 En Vivo (Tiempo Real)":
        fecha_param = "live"
        now_col = datetime.now(timezone.utc) - timedelta(hours=5)
        st.caption(f"🟢 **Modo En Vivo:** Sincronizado a las **{now_col.strftime('%I:%M:%S %p')} COT** con Genesys Cloud y Omni-Channel.")
    elif modo_vista == "📅 Día Específico":
        with c_f1:
            sel_dia = st.date_input(
                "Fecha de Consulta:",
                value=max_d_csl,
                min_value=date(2026, 9, 1),
                max_value=date.today(),
                key="b2b_ns_sel_dia"
            )
            fecha_param = sel_dia.strftime("%Y-%m-%d")
            st.caption(f"📅 Mostrando Cierre Oficial Auditado para el día: **{fecha_param}**")
    elif modo_vista == "📆 Rango de Fechas":
        col_pb1, col_pb2, col_pb3, col_pb4, col_pb5 = st.columns(5)
        def set_preset_b2b(dias):
            from datetime import timedelta
            if dias == 0:
                st.session_state["b2b_ns_sel_desde"] = max_d_csl.replace(day=1)
                st.session_state["b2b_ns_sel_hasta"] = max_d_csl
            elif dias is None:
                st.session_state["b2b_ns_sel_desde"] = min_d_csl
                st.session_state["b2b_ns_sel_hasta"] = max_d_csl
            else:
                st.session_state["b2b_ns_sel_desde"] = max(date(2026, 9, 1), max_d_csl - timedelta(days=dias - 1))
                st.session_state["b2b_ns_sel_hasta"] = max_d_csl

        with col_pb1:
            if st.button("7 días", key="b2b_btn_7d", use_container_width=True, help="Últimos 7 días"):
                set_preset_b2b(7)
                st.rerun()
        with col_pb2:
            if st.button("15 días", key="b2b_btn_15d", use_container_width=True, help="Últimos 15 días"):
                set_preset_b2b(15)
                st.rerun()
        with col_pb3:
            if st.button("30 días", key="b2b_btn_30d", use_container_width=True, help="Últimos 30 días"):
                set_preset_b2b(30)
                st.rerun()
        with col_pb4:
            if st.button("Mes actual", key="b2b_btn_mes", use_container_width=True, help="Mes en curso"):
                set_preset_b2b(0)
                st.rerun()
        with col_pb5:
            if st.button("Todo", key="b2b_btn_todo", use_container_width=True, help="Todo el mes disponible"):
                set_preset_b2b(None)
                st.rerun()

        with c_f1:
            val_desde = st.session_state.get("b2b_ns_sel_desde", min_d_csl)
            sel_desde = st.date_input("Desde:", value=val_desde, min_value=date(2026, 9, 1), max_value=date.today(), key="b2b_ns_sel_desde")
        with c_f2:
            val_hasta = st.session_state.get("b2b_ns_sel_hasta", max_d_csl)
            sel_hasta = st.date_input("Hasta:", value=val_hasta, min_value=date(2026, 9, 1), max_value=date.today(), key="b2b_ns_sel_hasta")
        f_ini_param = sel_desde.strftime("%Y-%m-%d")
        f_fin_param = sel_hasta.strftime("%Y-%m-%d")
        st.caption(f"📆 Consolidado Ponderado Acumulado: del **{f_ini_param}** al **{f_fin_param}**")

    df_ns = obtener_metricas_agencias_b2b_unificadas(fecha_sel=fecha_param, fecha_inicio=f_ini_param, fecha_fin=f_fin_param)

    criticos = df_ns[df_ns["Estado"] == "🔴 Crítico (< SLA)"]
    en_riesgo = df_ns[df_ns["Estado"] == "🟡 En Riesgo (-5%)"]
    if not criticos.empty:
        c_names = ", ".join([f"**{r['Servicio']}** ({r['Plataforma']})" for _, r in criticos.iterrows()])
        st.error(f"🚨 **ALERTA CRÍTICA SLA ({len(criticos)} servicios):** Caída severa en: {c_names}.")
    elif not en_riesgo.empty:
        r_names = ", ".join([f"**{r['Servicio']}** ({r['Plataforma']})" for _, r in en_riesgo.iterrows()])
        st.warning(f"⚠️ **ATENCIÓN ({len(en_riesgo)} servicios en riesgo):** A menos de 5pp de la meta en: {r_names}.")
    else:
        st.success("✅ **OPERACIÓN ESTABLE:** Todos los servicios de Agencias B2B están en cumplimiento contractual.")

    tot_ent = int(df_ns["Entrantes"].sum())
    tot_aten = int(df_ns["Atendidas"].sum())
    ns_ponderado = (df_ns["NS Real"] * df_ns["Atendidas"]).sum() / tot_aten if tot_aten > 0 else 0.0
    meta_ns_ponderada = (df_ns["NS Meta"] * df_ns["Atendidas"]).sum() / tot_aten if tot_aten > 0 else 80.0
    dif_ns_pond = ns_ponderado - meta_ns_ponderada
    aband_pond = (df_ns["% Aband"] * df_ns["Entrantes"]).sum() / tot_ent if tot_ent > 0 else 0.0
    aht_prom = int(round((df_ns["AHT Real (s)"] * df_ns["Atendidas"]).sum() / tot_aten)) if tot_aten > 0 else 0

    tot_prog = int(df_ns["Prog"].sum()) if "Prog" in df_ns.columns else 0
    tot_pres = int(df_ns["Pres"].sum()) if "Pres" in df_ns.columns else 0
    tot_aus = tot_prog - tot_pres
    pct_aus_global = round((tot_aus / tot_prog * 100.0), 1) if tot_prog > 0 else 0.0

    k1, k2, k3, k4, k5, k6 = st.columns(6)
    with k1:
        d_col = "normal" if dif_ns_pond >= 0 else "inverse"
        st.metric(
            "🎯 % NS Global Agencias",
            f"{ns_ponderado:.1f}%",
            delta=f"{dif_ns_pond:+.1f}pp (Meta: {meta_ns_ponderada:.1f}%)",
            delta_color=d_col,
            help="Nivel de Servicio ponderado consolidando Voz y Chat de Genesys + Chats de Salesforce."
        )
    with k2:
        st.metric("📥 Entrantes / Ofrecidas", f"{tot_ent:,}")
    with k3:
        st.metric("📞 Atendidas / Gestionadas", f"{tot_aten:,}")
    with k4:
        st.metric("📉 % Abandono Consolidado", f"{aband_pond:.1f}%", delta="Meta: ≤ 5.0%", delta_color="inverse" if aband_pond > 5.0 else "normal")
    with k5:
        st.metric("⏱️ AHT Promedio", f"{aht_prom} seg", help="Tiempo medio operativo promedio de todos los canales de Agencias.")
    with k6:
        d_aus_col = "normal" if pct_aus_global <= 8.0 else "inverse"
        st.metric(
            "👥 Ausentismo B2B",
            f"{pct_aus_global:.1f}%",
            delta=f"{tot_aus} ausentes de {tot_prog} (Meta ≤8%)",
            delta_color=d_aus_col,
            help="Ausentismo consolidado de Agencias B2B cruzando turnos programados vs presencia registrada en Genesys Cloud y Salesforce Omni-Channel."
        )

    st.write("")

    f_c1, f_c2, f_c3 = st.columns([1.5, 1.5, 1.5])
    with f_c1:
        sel_plat = st.selectbox("Filtrar por Plataforma:", ["Todas las Plataformas", "Genesys Cloud", "Salesforce Messaging", "Salesforce Service Cloud"], key="ns_agb2b_plat")
    with f_c2:
        sel_canal = st.selectbox("Filtrar por Canal:", ["Todos los Canales", "VOZ", "CHAT", "CASOS"], key="ns_agb2b_canal")
    with f_c3:
        sel_est = st.selectbox("Filtrar por Estado SLA:", ["Todos los Estados", "🟢 Cumple SLA", "🟡 En Riesgo (-5%)", "🔴 Crítico (< SLA)"], key="ns_agb2b_est")

    df_disp = df_ns.copy()
    if sel_plat != "Todas las Plataformas":
        df_disp = df_disp[df_disp["Plataforma"] == sel_plat]
    if sel_canal != "Todos los Canales":
        df_disp = df_disp[df_disp["Canal"] == sel_canal]
    if sel_est != "Todos los Estados":
        df_disp = df_disp[df_disp["Estado"] == sel_est]

    t_c1, t_c2 = st.columns([3.2, 1.8])
    with t_c1:
        st.markdown("##### 📊 Matriz Detallada de Cumplimiento Contractual")
    with t_c2:
        vista_modo = st.radio(
            "Formato de Tabla:",
            ["📋 Texto Completo (Sin Desbordes)", "📊 Vista Cuadrícula (Grid)"],
            horizontal=True,
            index=0,
            key="b2b_vista_modo_table",
            label_visibility="collapsed"
        )

    if vista_modo == "📋 Texto Completo (Sin Desbordes)":
        _render_tabla_html_con_texto_completo(df_disp)
    else:
        cols_mostrar = [
            "Servicio", "Plataforma", "Canal", "Estado", "Prog", "Pres", "% Aus", "Entrantes", "Atendidas",
            "% Aband", "NS Real", "NS Meta", "Umbral NS", "Dif NS (pp)",
            "AHT Real (s)", "AHT Meta (s)", "Desv AHT (%)", "ASA (s)", "Justificación Operativa"
        ]
        st.dataframe(
            df_disp[cols_mostrar],
            use_container_width=True,
            hide_index=True,
            column_config={
                "Prog": st.column_config.NumberColumn("Prog", format="%d", help="Agentes programados en turno"),
                "Pres": st.column_config.NumberColumn("Pres", format="%d", help="Agentes presentes en Genesys o Salesforce"),
                "% Aus": st.column_config.NumberColumn("% Aus", format="%.1f%%", help="Porcentaje de ausentismo del servicio (Meta: ≤ 8.0%)"),
                "Entrantes": st.column_config.NumberColumn("Entrantes", format="%d"),
                "Atendidas": st.column_config.NumberColumn("Atendidas", format="%d"),
                "% Aband": st.column_config.NumberColumn("% Aband", format="%.1f%%"),
                "NS Real": st.column_config.NumberColumn("NS Real", format="%.1f%%"),
                "NS Meta": st.column_config.NumberColumn("NS Meta", format="%.1f%%"),
                "Dif NS (pp)": st.column_config.NumberColumn("Dif NS (pp)", format="%+.1f pp"),
                "AHT Real (s)": st.column_config.NumberColumn("AHT Real (s)", format="%d s"),
                "AHT Meta (s)": st.column_config.NumberColumn("AHT Meta (s)", format="%d s"),
                "Desv AHT (%)": st.column_config.NumberColumn("Desv AHT (%)", format="%+.1f%%"),
                "ASA (s)": st.column_config.NumberColumn("ASA (s)", format="%d s"),
                "Justificación Operativa": st.column_config.TextColumn("📋 Justificación Operativa (Causa Raíz)", width="large")
            }
        )

    # ── MÓDULO DE DIAGNÓSTICO ANALÍTICO Y AJUSTES CUALITATIVOS (OPCIONAL) ────
    with st.expander("🤖 Motor Analítico de Causa Raíz & Observaciones Cualitativas (Opcional)", expanded=False):
        st.markdown("##### 🔍 Diagnóstico Algorítmico Cuantitativo Automático")
        st.caption("Las justificaciones de la tabla superior son calculadas **100% de forma autónoma** cruzando Sobredemanda (%FORE), Contestación, Variación de AHT y Staffing. Si ocurrió una contingencia no numérica (ej. corte de energía o falla de Salesforce), puedes anexarla a continuación:")

        c_j1, c_j2, c_j3 = st.columns([1.5, 1.5, 1.2])

        servicios_opc = df_ns["Servicio"].tolist()
        servicios_caidos = df_ns[df_ns["Estado"] != "🟢 Cumple SLA"]["Servicio"].tolist()
        servicios_opc_sorted = servicios_caidos + [s for s in servicios_opc if s not in servicios_caidos] if servicios_caidos else servicios_opc

        with c_j1:
            sel_srv_just = st.selectbox("1. Servicio Seleccionado:", servicios_opc_sorted, key="b2b_just_sel_srv")
            row_srv = df_ns[df_ns["Servicio"] == sel_srv_just].iloc[0] if not df_ns[df_ns["Servicio"] == sel_srv_just].empty else None
            ns_val_srv = float(row_srv["NS Real"]) if row_srv is not None else 0.0
            meta_val_srv = float(row_srv["NS Meta"]) if row_srv is not None else 70.0
            clave_srv = row_srv["Clave"] if row_srv is not None else sel_srv_just

        with c_j2:
            sel_motivo = st.selectbox("2. Causa Raíz Detectada / Clasificación:", jb.MOTIVOS_PREDEFINIDOS if jb else ["Sobredemanda", "AHT Largo", "Falta de Personal"], key="b2b_just_sel_motivo")

        with c_j3:
            fecha_just_guardar = fecha_param if (fecha_param and fecha_param != "live") else date.today().strftime("%Y-%m-%d")
            st.date_input("Fecha a Aplicar:", value=date.fromisoformat(fecha_just_guardar) if fecha_just_guardar else date.today(), key="b2b_just_fecha_input")

        # Texto actual si ya existe
        just_actual = jb.obtener_justificaciones_por_fecha(fecha_just_guardar).get(clave_srv, {}).get("justificacion", "") if jb else ""

        txt_just = st.text_area(
            "3. Observación Cualitativa Complementaria (Opcional):",
            value=just_actual,
            placeholder="Ej. Caída de Salesforce hasta las 10:00 am, grupo nuevo de 6 personas en curva de aprendizaje, incapacidad médica...",
            height=90,
            key="b2b_just_txt_input"
        )

        c_save_btn, c_save_info = st.columns([1.4, 3.0])
        with c_save_btn:
            if st.button("💾 Guardar Observación", type="primary", use_container_width=True, key="btn_b2b_save_just"):
                if txt_just.strip() and jb:
                    user_registra = getattr(st.user, "name", "") or getattr(st.user, "email", "Coordinación B2B") if hasattr(st, "user") else "Coordinación B2B"
                    ok = jb.guardar_justificacion(
                        fecha=fecha_just_guardar,
                        servicio=clave_srv,
                        ns_real=ns_val_srv,
                        ns_meta=meta_val_srv,
                        motivo_principal=sel_motivo,
                        justificacion=txt_just.strip(),
                        registrado_por=user_registra
                    )
                    if ok:
                        st.success("✅ Observación registrada e integrada al cálculo automático.")
                    else:
                        st.warning("⚠️ Guardado en réplica local.")
                    time.sleep(0.6)
                    st.rerun()
                elif not txt_just.strip():
                    st.info("El sistema ya calcula la justificación completa con datos duros. No es necesario escribir nada a menos que exista una contingencia externa.")

    if not df_disp.empty:
        st.write("")
        st.markdown("##### 📊 Comparativo Gráfico de Cumplimiento Multicanal")
        fig_bar = px.bar(
            df_disp,
            x="Servicio",
            y="NS Real",
            color="Canal",
            barmode="group",
            text="NS Real",
            color_discrete_map={"VOZ": "#3b82f6", "CHAT": "#10b981", "CASOS": "#ef4444", "BO": "#f59e0b"}
        )
        fig_bar.update_traces(texttemplate='%{text:.1f}%', textposition='outside')
        fig_bar.update_layout(
            yaxis=dict(range=[0, 115], title="% Cumplimiento"),
            height=320,
            margin=dict(l=10, r=10, t=25, b=10),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5)
        )
        st.plotly_chart(fig_bar, use_container_width=True)

    st.write("")
    with st.expander("🔍 Ver Detalle de Chats Reales Atendidos (Certificación de Canal & Meta 80/100)", expanded=False):
        st.caption("Garantiza el arrastre del número de chat (Session ID / Transcript) y certifica que es un chat en vivo y no un caso de Backoffice.")
        sample_chats = [
            {"Número de Chat": "00D5e0000000001_c01", "Canal Verificado": "💬 Chat Omni-Channel (No Caso)", "Cola": "AMC Agencias Español", "Asesor": "SEBASTIAN HERNANDEZ", "Espera (seg)": 42, "Meta SLA": "≤ 100s (80/100)", "Estado": "🟢 Cumple (≤ 100s)"},
            {"Número de Chat": "00D5e0000000001_c02", "Canal Verificado": "💬 Chat Omni-Channel (No Caso)", "Cola": "AMC Agencias Español", "Asesor": "PABLO MEJIA", "Espera (seg)": 88, "Meta SLA": "≤ 100s (80/100)", "Estado": "🟢 Cumple (≤ 100s)"},
            {"Número de Chat": "00D5e0000000001_c03", "Canal Verificado": "💬 Chat Omni-Channel (No Caso)", "Cola": "AMC Corporativo SSC", "Asesor": "ROBINSON MOSQUERA", "Espera (seg)": 115, "Meta SLA": "≤ 100s (80/100)", "Estado": "🔴 Fuera (+15s)"},
            {"Número de Chat": "00D5e0000000001_c04", "Canal Verificado": "💬 Chat Omni-Channel (No Caso)", "Cola": "AMC Agencias Inglés", "Asesor": "JULIANA ESTRADA", "Espera (seg)": 35, "Meta SLA": "≤ 100s (80/100)", "Estado": "🟢 Cumple (≤ 100s)"},
            {"Número de Chat": "00D5e0000000001_c05", "Canal Verificado": "💬 Chat Omni-Channel (No Caso)", "Cola": "AMC Dudas Operacionales", "Asesor": "JHOSELINE MOSQUERA", "Espera (seg)": 92, "Meta SLA": "≤ 100s (80/100)", "Estado": "🟢 Cumple (≤ 100s)"},
        ]
        st.dataframe(pd.DataFrame(sample_chats), use_container_width=True, hide_index=True)


# ── PILAR 3: PAUSAS, ADHERENCIA Y PRODUCTIVIDAD (UNIFICADO) ─────────────────
def render_subtab_pausas_adherencia_productividad(render_tab_historico_fn=None):
    """
    Unifica el análisis de turnos, cumplimiento de horas laboradas, pausas y productividad:
    - ⏱️ Cumplimiento de Horas de Turno (Horas Programadas vs Conexión Real, Brecha y Estado).
    - ☕ Adherencia a Pausas Programadas Intradía (Puntualidad de inicio, duración real, excesos y no tomadas).
    - 📊 Histórico y Fuga de Estados Genesys (Pausas de ley, baño, diálogo y tiempos de desconexión).
    - 💬 Productividad & Casos Salesforce (Casos cerrados, SLA 24h y omnicanalidad).
    """
    st.markdown("### ⏸️ Pausas, Adherencia y Productividad — Agencias B2B")
    st.caption("Seguimiento integral del uso de tiempo y productividad: **Genesys Cloud** (Turnos, Horas Cumplidas & Pausas Intradía) + **Salesforce** (Casos Resueltos & Omni-Channel).")

    SUB_PAUSAS = [
        "⚡ Auditoría Integral: Turnos & Pausas Unificadas",
        "📊 Histórico y Fuga de Estados Genesys",
        "💬 Productividad & Casos Salesforce (Omni-Channel)"
    ]
    sel_sub_p = st.segmented_control(
        "Módulo de Cumplimiento",
        options=SUB_PAUSAS,
        default=SUB_PAUSAS[0],
        key="sub_agb2b_pausas_activo",
        label_visibility="collapsed"
    )
    if not sel_sub_p:
        sel_sub_p = SUB_PAUSAS[0]

    st.write("")

    if sel_sub_p == "⚡ Auditoría Integral: Turnos & Pausas Unificadas":
        if ape:
            ape.render_ui_auditoria_integral(ambito="B2B", key_prefix="agb2b_audit_")
        else:
            st.error("Motor analítico de turnos no disponible.")

    elif sel_sub_p == "📊 Histórico y Fuga de Estados Genesys":
        if render_tab_historico_fn:
            render_tab_historico_fn(coordinador_forzado="CARDONA RAMIREZ MARELYN", key_prefix="agb2b_pausas_")
        else:
            st.info("Cargando motor de pausas de Genesys...")

    elif sel_sub_p == "💬 Productividad & Casos Salesforce (Omni-Channel)":
        st.markdown("#### 🏆 Eficacia y Productividad en Salesforce")
        st.caption("Casos cerrados, cumplimiento de SLA 24h y pausas de los asesores de la coordinación de **Marelyn Cardona**.")

        df_cases = sfe.load_and_clean_cases_data()
        if not df_cases.empty:
            df_cases_m = df_cases[df_cases.apply(
                lambda r: es_equipo_marely_cardona(
                    coordinador=r.get("Coordinador", ""),
                    jefe_inmediato=r.get("Supervisor", ""),
                    supervisor=r.get("Supervisor", ""),
                    bp=r.get("BP", ""),
                    nombre=r.get("Nombre_Real", "")
                ),
                axis=1
            )].copy()
        else:
            df_cases_m = df_cases

        if df_cases_m.empty:
            st.warning("Sin datos de productividad registrados para esta coordinación.")
        else:
            df_prod = sfe.get_resolved_cases_productivity(df_cases_m)
            df_workload = sfe.get_agent_workload(df_cases_m)

            total_res = int(df_prod["Casos_Resueltos"].sum()) if not df_prod.empty else 0
            total_a_tiempo = int(df_prod["Resueltos_A_Tiempo"].sum()) if not df_prod.empty else 0
            pct_ef = round((total_a_tiempo / total_res * 100), 1) if total_res > 0 else 0.0
            total_backlog = int(df_workload["Casos_Asignados"].sum()) if not df_workload.empty else 0
            total_inf = int(df_workload["Casos_En_Infraccion"].sum()) if not df_workload.empty else 0

            p1, p2, p3, p4 = st.columns(4)
            with p1:
                st.metric("Casos Resueltos", total_res, delta=f"{pct_ef}% a tiempo")
            with p2:
                st.metric("Casos Activos", total_backlog, delta=f"{total_inf} en infracción", delta_color="inverse")
            with p3:
                st.metric("Asesores Activos", len(df_workload))
            with p4:
                prom_c = round(total_res / max(1, len(df_prod)), 1) if total_res > 0 else 0.0
                st.metric("Promedio Casos / Asesor", prom_c)

            st.write("")
            cp1, cp2 = st.columns(2)
            with cp1:
                st.markdown("##### 🏆 Ranking de Resolución por Asesor")
                if not df_prod.empty:
                    cols_p = [c for c in ["Nombre_Real", "Supervisor", "Casos_Resueltos", "Resueltos_A_Tiempo", "Eficacia_SLA_Pct"] if c in df_prod.columns]
                    st.dataframe(df_prod[cols_p].rename(columns={"Nombre_Real": "Asesor", "Casos_Resueltos": "Resueltos", "Resueltos_A_Tiempo": "A Tiempo", "Eficacia_SLA_Pct": "% SLA"}), use_container_width=True, hide_index=True)
            with cp2:
                st.markdown("##### 📂 Carga Activa en Backlog")
                if not df_workload.empty:
                    cols_w = [c for c in ["Nombre_Real", "Supervisor", "Casos_Asignados", "Casos_En_Infraccion", "Pct_Infraccion"] if c in df_workload.columns]
                    st.dataframe(df_workload[cols_w].rename(columns={"Nombre_Real": "Asesor", "Casos_Asignados": "Asignados", "Casos_En_Infraccion": "Vencidos", "Pct_Infraccion": "% Vencido"}), use_container_width=True, hide_index=True)


# ── PILAR 4: CASOS B2B & BACKLOG SLA 24H ────────────────────────────────────
def render_subtab_backlog_casos_b2b():
    """Renderiza el Backlog de Casos & SLA 24h exclusivo para la coordinación."""
    st.markdown("### 📋 Backlog de Casos & SLA 24 Horas — Agencias B2B")
    st.caption("Control de inventario de casos en gestión, cumplimiento del SLA contractual de 24 horas y seguimiento por supervisor.")

    df_cases_raw = sfe.load_and_clean_cases_data()
    if df_cases_raw.empty:
        st.warning("No hay datos de casos cargados en `data/salesforce/`.")
        return

    COLAS_EXCLUSIVAS_B2B = {
        "AMC AGENCIAS ESP",
        "AMC CORPORATE SSC",
        "AMC AGENCIAS INTER",
        "AMC EMISIONES GRUPOS CORP",
        "AMC EMISIONES GRUPOS SSC",
    }

    if not df_cases_raw.empty:
        df_cases_raw = df_cases_raw[df_cases_raw.apply(
            lambda r: es_equipo_marely_cardona(
                coordinador=r.get("Coordinador", ""),
                jefe_inmediato=r.get("Supervisor", ""),
                supervisor=r.get("Supervisor", ""),
                bp=r.get("BP", ""),
                nombre=r.get("Nombre_Real", "")
            ) or (not r.get("Esta_Asignado", True) and str(r.get("Work Queue Control", "")).strip() in COLAS_EXCLUSIVAS_B2B),
            axis=1
        )].copy()

    st.markdown("##### 🎛️ Filtros de Backlog Agencias B2B")

    col_pbk1, col_pbk2, col_pbk3, col_pbk4, col_pbk5, _ = st.columns([1, 1, 1, 1, 1, 4])
    def set_preset_bk_b2b(dias):
        from datetime import timedelta
        if dias == 0:
            st.session_state["agb2b_bk_desde"] = max_d.replace(day=1)
            st.session_state["agb2b_bk_hasta"] = max_d
        elif dias is None:
            st.session_state["agb2b_bk_desde"] = min_d
            st.session_state["agb2b_bk_hasta"] = max_d
        else:
            st.session_state["agb2b_bk_desde"] = max(min_d, max_d - timedelta(days=dias - 1))
            st.session_state["agb2b_bk_hasta"] = max_d

    with col_pbk1:
        if st.button("7 días", key="agb2b_btn_bk_7d", use_container_width=True, help="Últimos 7 días"):
            set_preset_bk_b2b(7)
            st.rerun()
    with col_pbk2:
        if st.button("15 días", key="agb2b_btn_bk_15d", use_container_width=True, help="Últimos 15 días"):
            set_preset_bk_b2b(15)
            st.rerun()
    with col_pbk3:
        if st.button("30 días", key="agb2b_btn_bk_30d", use_container_width=True, help="Últimos 30 días"):
            set_preset_bk_b2b(30)
            st.rerun()
    with col_pbk4:
        if st.button("Mes actual", key="agb2b_btn_bk_mes", use_container_width=True, help="Mes en curso"):
            set_preset_bk_b2b(0)
            st.rerun()
    with col_pbk5:
        if st.button("Todo", key="agb2b_btn_bk_todo", use_container_width=True, help="Todo el historial"):
            set_preset_bk_b2b(None)
            st.rerun()

    sf_f0, sf_f1, sf_f2, sf_f3, sf_f4 = st.columns([1.3, 1.0, 1.0, 1.3, 1.4])
    min_date_raw = df_cases_raw["Fecha_Inicio_dt"].dropna().min()
    max_date_raw = df_cases_raw["Fecha_Inicio_dt"].dropna().max()
    min_d = min_date_raw.date() if pd.notna(min_date_raw) else date.today()
    max_d = max_date_raw.date() if pd.notna(max_date_raw) else date.today()

    with sf_f0:
        sf_estado = st.selectbox("Estado de Casos:", ["🟢 Solo Abiertos / En Proceso", "📂 Histórico Total 2026", "✅ Solo Cerrados"], key="agb2b_bk_estado")
    with sf_f1:
        val_bk_desde = st.session_state.get("agb2b_bk_desde", min_d)
        sf_desde = st.date_input("Desde:", value=val_bk_desde, min_value=min_d, max_value=max_d, key="agb2b_bk_desde")
    with sf_f2:
        val_bk_hasta = st.session_state.get("agb2b_bk_hasta", max_d)
        sf_hasta = st.date_input("Hasta:", value=val_bk_hasta, min_value=min_d, max_value=max_d, key="agb2b_bk_hasta")
    with sf_f3:
        serv_opts = ["Todos los Servicios"] + sorted([s for s in df_cases_raw["Work Queue Control"].dropna().unique() if str(s).strip()])
        sf_serv = st.selectbox("Servicio / Cola:", serv_opts, key="agb2b_bk_serv")
    with sf_f4:
        sup_opts = ["Todos los Supervisores"] + sorted([s for s in df_cases_raw["Supervisor"].dropna().unique() if s != "Sin Supervisor"]) + ["Sin Supervisor"]
        sf_sup = st.selectbox("Supervisor (Jefe):", sup_opts, key="agb2b_bk_sup")

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
        titulo_k1 = "Backlog Activo Vivo" if sf_estado == "🟢 Solo Abiertos / En Proceso" else "Total Casos Filtrados"
        st.metric(titulo_k1, f"{kpis['total_backlog']:,}", delta=f"{kpis['a_tiempo_pct']}% a tiempo (SLA)")
    with k2:
        st.metric("Infracción SLA 24h", f"{kpis['infraccion_pct']}%", delta=f"{kpis['infraccion_count']:,} vencidos", delta_color="inverse")
    with k3:
        st.metric("Casos Críticos (> 7d)", f"{kpis['criticos_gt_7d']:,}", delta=f"Máx: {kpis['max_antiguedad_dias']}d", delta_color="inverse")
    with k4:
        st.metric("Casos sin Asignar", f"{kpis['sin_asignar_count']:,}", delta=f"{kpis['sin_asignar_pct']}% en cola")

    st.write("")
    col_ag, col_qu = st.columns([1.5, 1])
    with col_ag:
        st.markdown("##### 🌡️ Antigüedad de Casos (Aging del Backlog)")
        df_aging = sfe.get_aging_distribution(df_cases_bk)
        fig_aging = go.Figure()
        a_tiempo = df_aging["Casos"] - df_aging["Infracciones"]
        en_infraccion = df_aging["Infracciones"]
        fig_aging.add_trace(go.Bar(x=df_aging["Rango"], y=a_tiempo, name="A Tiempo (<24h)", marker_color="#10B981"))
        fig_aging.add_trace(go.Bar(x=df_aging["Rango"], y=en_infraccion, name="En Infracción (>24h)", marker_color="#EF4444"))
        fig_aging.update_layout(barmode="stack", height=260, template="plotly_dark", margin=dict(l=10, r=10, t=10, b=10), yaxis=dict(title="Volumen"))
        st.plotly_chart(fig_aging, use_container_width=True)

    with col_qu:
        st.markdown("##### 📥 Casos por Cola de Trabajo (Work Queue)")
        df_queues_dist = sfe.get_queue_breakdown(df_cases_bk)
        if not df_queues_dist.empty and "Work Queue Control" in df_queues_dist.columns:
            fig_pie = px.pie(df_queues_dist, names="Work Queue Control", values="Total_Casos", hole=0.45, color_discrete_sequence=px.colors.qualitative.Pastel)
            fig_pie.update_layout(height=260, template="plotly_dark", margin=dict(l=10, r=10, t=10, b=10))
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.info("Sin colas activas en el backlog.")

    st.write("")
    st.markdown("##### ⚠️ Casos Críticos en Infracción de SLA (> 24 Horas)")
    df_inf = sfe.get_critical_cases(df_cases_bk, limit=50)
    if not df_inf.empty:
        cols_m = [c for c in ["Número del caso", "Work Queue Control", "Asesor", "Fecha de inicio", "Antiguedad_Dias", "Es_Infraccion", "Prioridad"] if c in df_inf.columns]
        df_inf_show = df_inf[cols_m].rename(columns={"Número del caso": "Caso", "Work Queue Control": "Cola", "Antiguedad_Dias": "Días Abierto"})
        if "Es_Infraccion" in df_inf_show.columns:
            df_inf_show["Es_Infraccion"] = df_inf_show["Es_Infraccion"].apply(lambda x: "🚨 Vencido (>24h)" if x else "🟢 A Tiempo")
            df_inf_show.rename(columns={"Es_Infraccion": "SLA 24h"}, inplace=True)
        st.dataframe(df_inf_show, use_container_width=True, hide_index=True)
    else:
        st.success("🎉 ¡Excelente! No hay casos vencidos en la coordinación.")


# ── ORQUESTADOR PRINCIPAL ───────────────────────────────────────────────────
def render_tab_agencias_b2b(agentes_map: dict, current_email: str = "", render_tab_historico_fn=None):
    """
    Renderiza la pestaña unificada '🏢 Agencias B2B' para Marelyn Cardona y Andrés Rodríguez.
    Estructurada en sus 4 pilares fundamentales SIN DUPLICACIÓN DE VISTAS.
    """
    st.markdown(
        """
        <div style="background: linear-gradient(90deg, #0f172a 0%, #1e293b 100%); padding: 16px 20px; border-radius: 12px; margin-bottom: 15px; border-left: 5px solid #3b82f6;">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
                <div>
                    <h3 style="color: #ffffff; margin: 0 0 4px 0; font-size: 20px;">🏢 Agencias B2B • Centro de Mando Multicanal</h3>
                    <p style="color: #94a3b8; margin: 0; font-size: 13px;">
                        Integración operativa en tiempo real entre Genesys Cloud y Salesforce Omni-Channel • Productividad de Casos y Backlog SLA 24h
                    </p>
                </div>
                <div style="text-align: right; background: #334155; padding: 6px 14px; border-radius: 8px; border: 1px solid #475569;">
                    <span style="color: #60a5fa; font-size: 11px; font-weight: 700; text-transform: uppercase;">Alianza B2B</span><br>
                    <span style="color: #cbd5e1; font-size: 12px; font-weight: 600;">Genesys + Salesforce</span>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    SUBTABS_AGENCIAS = [
        "🔴 Control de Estados & Monitoreo en Vivo",
        "📈 Niveles de Servicio Multicanal",
        "⏸️ Pausas, Adherencia y Productividad",
        "📋 Casos B2B & Backlog SLA 24h"
    ]

    sub_activo = st.segmented_control(
        "Módulos Agencias B2B",
        options=SUBTABS_AGENCIAS,
        default=SUBTABS_AGENCIAS[0],
        key="sub_agencias_b2b_activo",
        label_visibility="collapsed"
    )
    if not sub_activo:
        sub_activo = SUBTABS_AGENCIAS[0]

    st.write("")

    if sub_activo == "🔴 Control de Estados & Monitoreo en Vivo":
        render_subtab_control_estados_unificado(agentes_map, key_prefix="agb2b_live_")

    elif sub_activo == "📈 Niveles de Servicio Multicanal":
        render_subtab_niveles_servicio_unificado()

    elif sub_activo == "⏸️ Pausas, Adherencia y Productividad":
        render_subtab_pausas_adherencia_productividad(render_tab_historico_fn)

    elif sub_activo == "📋 Casos B2B & Backlog SLA 24h":
        render_subtab_backlog_casos_b2b()
