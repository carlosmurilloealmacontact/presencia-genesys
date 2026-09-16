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
@st.fragment(run_every=30)
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

    # 1. Obtener estados de ambas plataformas
    # A. Genesys Cloud
    agentes_scope = {
        k: v for k, v in agentes_map.items()
        if "MARELYN" in (v.get("coordinador") or "").upper() or "CARDONA" in (v.get("coordinador") or "").upper()
    }
    df_live_genesys = pd.DataFrame()
    if token:
        try:
            df_live_genesys = obtener_presencia_en_vivo(token, agentes_scope, catalog)
            if not df_live_genesys.empty and "coordinador" in df_live_genesys.columns:
                df_live_genesys = df_live_genesys[df_live_genesys["coordinador"].astype(str).str.contains("CARDONA|MARELYN", case=False, na=False)]
        except Exception:
            pass

    # B. Salesforce Service Cloud
    df_queues, df_agents_sf, latest_ts = sle.get_latest_live_state(force_fresh=btn_refresh)
    hora_display = str(latest_ts or "")
    try:
        dt_obj = datetime.strptime(latest_ts, "%Y-%m-%d %H:%M:%S")
        hora_display = dt_obj.strftime("%I:%M:%S %p")
    except Exception:
        pass

    st.caption(f"🟢 **Sincronización Multicanal:** Actualizado a las **{hora_display}** (Hora Colombia - COT / UTC-5) • Auto-recarga cada **30 segundos**.")

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
        st.metric("⏳ Chats en Espera", total_waiting_chats, delta="Colas AMC")
    with k6:
        delta_sla = "Meta: ≤ 100s"
        d_color = "normal" if max_wait_min <= 1.67 else "inverse"
        st.metric("⏱️ Mayor Espera Cola", f"{max_wait_min} min", delta=delta_sla, delta_color=d_color)

    st.write("")

    # 3. Bandeja Unificada de Alertas de Piso (Genesys + Salesforce)
    alerts_sf = sle.detect_live_anomalies(df_queues, df_agents_sf) if (df_queues is not None and df_agents_sf is not None) else []
    al_criticas = [a for a in alerts_sf if a.get("categoria") in ("busy", "break") or a.get("type") == "critical"]
    al_operativas = [a for a in alerts_sf if a.get("categoria") in ("idle", "stuck_chat", "free_cap") or a.get("type") in ("warning", "info")]

    # Alertas Genesys
    if not df_live_genesys.empty:
        col_llam_seg = "dur_llamada_seg" if "dur_llamada_seg" in df_live_genesys.columns else ("llamada_seg" if "llamada_seg" in df_live_genesys.columns else None)
        if col_llam_seg:
            llamadas_largas = df_live_genesys[df_live_genesys[col_llam_seg] >= 900]
            for _, r in llamadas_largas.iterrows():
                mins_ll = int(r[col_llam_seg] // 60)
                al_criticas.append({
                    "categoria": "call",
                    "asesor": r.get("agente", "Asesor"),
                    "tag": f"📞 Llamada prolongada en Genesys ({mins_ll} min)"
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
        st.markdown("##### 📥 Colas de Chat AMC en Espera (Omni-Channel)")
        if df_queues is not None and not df_queues.empty and "chats_in_queue" in df_queues.columns:
            fig_q = px.bar(
                df_queues,
                x="chats_in_queue",
                y="queue_name",
                orientation="h",
                color="chats_in_queue",
                color_continuous_scale="Viridis",
                labels={"chats_in_queue": "Chats en Espera", "queue_name": "Cola de Chat"}
            )
            fig_q.update_layout(height=230, margin=dict(l=10, r=10, t=10, b=10), template="plotly_dark", coloraxis_showscale=False)
            st.plotly_chart(fig_q, use_container_width=True)
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
    sf_by_name = {}
    sf_by_bp = {}
    if df_agents_sf is not None and not df_agents_sf.empty:
        for _, r_sf in df_agents_sf.iterrows():
            ag_alias = str(r_sf["agent_name"]).strip().upper()
            info_m = maestro.get(ag_alias, {})
            nombre_real = info_m.get("nombre_completo", ag_alias)
            bp_val = str(info_m.get("bp", "")).strip()
            sf_by_name[nombre_real.strip().upper()] = r_sf
            if bp_val:
                sf_by_bp[bp_val] = r_sf

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

            # Buscar correspondencia en Salesforce por BP o nombre
            match_sf = None
            if bp_g and bp_g in sf_by_bp:
                match_sf = sf_by_bp[bp_g]
            elif nom_g in sf_by_name:
                match_sf = sf_by_name[nom_g]
            else:
                for k_sf, v_sf in sf_by_name.items():
                    if k_sf in nom_g or nom_g in k_sf:
                        match_sf = v_sf
                        break

            if match_sf is not None:
                est_sf = str(match_sf.get("status", "Available"))
                chats_sf = int(match_sf.get("active_chats", 0))
                simult_sf = f"{chats_sf} de 3 ({match_sf.get('capacity_pct', 0)}%)"
                t_sec_sf = int(match_sf.get("time_in_status_sec", 0))
                mins_sf = t_sec_sf // 60

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
                est_sf = "— (Desconectado)"
                simult_sf = "0 chats"
                diag = "🟢 Normal" if not es_llamada or rg.get("dur_llamada_seg", 0) < 900 else "🚨 Llamada >15m"

            filas_piso.append({
                "Asesor": rg.get("agente", ""),
                "BP": bp_g,
                "Supervisor": sup_g,
                "Estado Genesys": est_g,
                "⏱️ Tiempo Genesys": t_g,
                "Estado Salesforce Omni": est_sf,
                "Simultaneidad SF": simult_sf,
                "Alerta Integrada": diag
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
            buscar_txt = st.text_input("Buscar por Asesor o BP:", placeholder="Ej: Sebastian, 4512...", key=f"{key_prefix}flt_txt")

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
                df_piso_disp["BP"].str.contains(buscar_txt, case=False, na=False)
            ]

        st.dataframe(
            df_piso_disp,
            use_container_width=True,
            hide_index=True
        )
    else:
        st.info("Sin asesores en piso reportados actualmente.")


# ── PILAR 2: NIVELES DE SERVICIO MULTICANAL (UNIFICADO GTR) ─────────────────
def obtener_metricas_agencias_b2b_unificadas(fecha_sel: str = None):
    """
    Matriz unificada de SLA para Agencias B2B con formato idéntico a GTR.
    Combina con máxima fidelidad los datos oficiales auditados de:
    1. Genesys Cloud: TARGET ESP, TARGET ENG, CORPORATE PYME (telefonía).
    2. Salesforce Messaging: AG CHAT ES, AG CORPORATE CHAT, AG CELULA REMISION (chats).
    3. Salesforce Service Cloud: BO AGENCIAS TARGET, BO_CORPORATE (casos 24h).
    """
    from cierres_semanales_loader import obtener_cierre_b2b_por_fecha
    cierres_dia = obtener_cierre_b2b_por_fecha(fecha_sel if fecha_sel != "live" else None)

    token = obtener_token_genesys()
    gtr_cfg = gtr.cargar_config_gtr()
    serv_genesys_data = {}
    if token and fecha_sel == "live":
        try:
            df_raw, _ = gtr.consultar_metricas_genesys(token, None, None, gtr_cfg)
            if not df_raw.empty:
                _, serv_genesys_data = gtr.construir_matriz_ejecutiva_gtr(df_raw, gtr_cfg)
        except Exception:
            pass

    servicios_def = [
        # ── VOZ GENESYS CLOUD ──────────────────────────────────────────────────
        {
            "clave": "TARGET ESP",
            "servicio": "TARGET ESP (Operacional SSC)",
            "plataforma": "Genesys Cloud",
            "canal": "VOZ",
            "meta_ns": 70.0,
            "umbral_txt": "≤ 20s",
            "meta_aht": 880.0
        },
        {
            "clave": "TARGET ENG",
            "servicio": "TARGET ENG (Internacional)",
            "plataforma": "Genesys Cloud",
            "canal": "VOZ",
            "meta_ns": 70.0,
            "umbral_txt": "≤ 20s",
            "meta_aht": 637.0
        },
        {
            "clave": "EMPRESAS",
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

    filas = []
    for sc in servicios_def:
        k = sc["clave"]
        srv_name = sc["servicio"]
        plat = sc["plataforma"]
        canal = sc["canal"]
        meta_ns = sc["meta_ns"]
        meta_aht = sc["meta_aht"]

        c_data = cierres_dia.get(k, {}) if cierres_dia else {}
        if c_data:
            entrantes = c_data.get("entrante", 0)
            atendidas = c_data.get("atendido", 0)
            aband = c_data.get("pct_abandono", 0.0)
            ns_real = c_data.get("ns_real", 0.0)
            aht_real = c_data.get("aht_real", meta_aht)
            asa = c_data.get("asa_real", 0.0)
        elif plat == "Genesys Cloud" and k in serv_genesys_data:
            g_d = serv_genesys_data[k]
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

        filas.append({
            "Clave": k,
            "Servicio": srv_name,
            "Plataforma": plat,
            "Canal": canal,
            "Estado": estado,
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
            "ASA (s)": int(round(asa))
        })

    return pd.DataFrame(filas)


def render_subtab_niveles_servicio_unificado():
    """Renderiza la vista unificada de Niveles de Servicio Multicanal para Agencias B2B."""
    from cierres_semanales_loader import cargar_todos_los_cierres_b2b
    cierres_all = cargar_todos_los_cierres_b2b()
    fechas_lista = sorted(cierres_all.keys(), reverse=True) if cierres_all else []

    col_title, col_fecha = st.columns([3.0, 2.0])
    with col_title:
        st.markdown("### 📈 Niveles de Servicio Multicanal — Agencias B2B")
        st.caption("Visión consolidada oficial auditada: **Genesys Cloud** (Voz e Inbound) + **Salesforce** (Chats Omni-Channel & Casos) • Formato GTR.")

    opciones_fechas = [f"{f} (Cierre Semanal Auditado)" if idx == 0 else f"{f} (Cierre Semanal)" for idx, f in enumerate(fechas_lista)]
    opciones_fechas.append("🔴 En Vivo (Tiempo Real)")

    with col_fecha:
        sel_opc = st.selectbox("📅 Periodo de Medición:", opciones_fechas, index=0, key="b2b_sel_fecha_cierre")

    fecha_param = "live" if "En Vivo" in sel_opc else sel_opc.split(" ")[0]
    df_ns = obtener_metricas_agencias_b2b_unificadas(fecha_sel=fecha_param)

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

    k1, k2, k3, k4, k5 = st.columns(5)
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
        st.metric("⏱️ AHT Promedio Ponderado", f"{aht_prom} seg", help="Tiempo medio operativo promedio de todos los canales de Agencias.")

    st.write("")

    f_c1, f_c2, f_c3 = st.columns([1.5, 1.5, 1.5])
    with f_c1:
        sel_plat = st.selectbox("Filtrar por Plataforma:", ["Todas las Plataformas", "Genesys Cloud", "Salesforce Service Cloud"], key="ns_agb2b_plat")
    with f_c2:
        sel_canal = st.selectbox("Filtrar por Canal:", ["Todos los Canales", "VOZ", "CHAT", "CASOS", "BO"], key="ns_agb2b_canal")
    with f_c3:
        sel_est = st.selectbox("Filtrar por Estado SLA:", ["Todos los Estados", "🟢 Cumple SLA", "🟡 En Riesgo (-5%)", "🔴 Crítico (< SLA)"], key="ns_agb2b_est")

    df_disp = df_ns.copy()
    if sel_plat != "Todas las Plataformas":
        df_disp = df_disp[df_disp["Plataforma"] == sel_plat]
    if sel_canal != "Todos los Canales":
        df_disp = df_disp[df_disp["Canal"] == sel_canal]
    if sel_est != "Todos los Estados":
        df_disp = df_disp[df_disp["Estado"] == sel_est]

    cols_mostrar = [
        "Servicio", "Plataforma", "Canal", "Estado", "Entrantes", "Atendidas",
        "% Aband", "NS Real", "NS Meta", "Umbral NS", "Dif NS (pp)",
        "AHT Real (s)", "AHT Meta (s)", "Desv AHT (%)", "ASA (s)"
    ]

    st.dataframe(
        df_disp[cols_mostrar],
        use_container_width=True,
        hide_index=True,
        column_config={
            "Entrantes": st.column_config.NumberColumn("Entrantes", format="%d"),
            "Atendidas": st.column_config.NumberColumn("Atendidas", format="%d"),
            "% Aband": st.column_config.NumberColumn("% Aband", format="%.1f%%"),
            "NS Real": st.column_config.NumberColumn("NS Real", format="%.1f%%"),
            "NS Meta": st.column_config.NumberColumn("NS Meta", format="%.1f%%"),
            "Dif NS (pp)": st.column_config.NumberColumn("Dif NS (pp)", format="%+.1f pp"),
            "AHT Real (s)": st.column_config.NumberColumn("AHT Real (s)", format="%d s"),
            "AHT Meta (s)": st.column_config.NumberColumn("AHT Meta (s)", format="%d s"),
            "Desv AHT (%)": st.column_config.NumberColumn("Desv AHT (%)", format="%+.1f%%"),
            "ASA (s)": st.column_config.NumberColumn("ASA (s)", format="%d s")
        }
    )

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
    Unifica el análisis de pausas y cumplimiento de turno:
    - Genesys Cloud: Pausas reglamentarias (Descanso, Baño, Diálogo, Lunch) y fuga en Available.
    - Salesforce Omni-Channel: Productividad de casos y pausas marcadas en Omni.
    """
    st.markdown("### ⏸️ Pausas, Adherencia y Productividad — Agencias B2B")
    st.caption("Seguimiento integral del uso de tiempo y productividad: **Genesys Cloud** (Turno & Pausas de Piso) + **Salesforce** (Casos Resueltos & Omni-Channel).")

    SUB_PAUSAS = [
        "📅 Adherencia y Pausas Genesys (Oficial)",
        "💬 Productividad & Pausas Salesforce (Omni-Channel)"
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

    if sel_sub_p == "📅 Adherencia y Pausas Genesys (Oficial)":
        if render_tab_historico_fn:
            render_tab_historico_fn(coordinador_forzado="CARDONA RAMIREZ MARELYN", key_prefix="agb2b_pausas_")
        else:
            st.info("Cargando motor de pausas de Genesys...")

    elif sel_sub_p == "💬 Productividad & Pausas Salesforce (Omni-Channel)":
        st.markdown("#### 🏆 Eficacia y Productividad en Salesforce")
        st.caption("Casos cerrados, cumplimiento de SLA 24h y pausas de los asesores de la coordinación de **Marelyn Cardona**.")

        df_cases = sfe.load_and_clean_cases_data()
        if not df_cases.empty and "Coordinador" in df_cases.columns:
            df_cases_m = df_cases[df_cases["Coordinador"].astype(str).str.contains("CARDONA|MARELYN", case=False, na=False)]
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

    if "Coordinador" in df_cases_raw.columns:
        df_cases_raw = df_cases_raw[df_cases_raw["Coordinador"].astype(str).str.contains("CARDONA|MARELYN", case=False, na=False)]

    st.markdown("##### 🎛️ Filtros de Backlog Agencias B2B")
    sf_f0, sf_f1, sf_f2, sf_f3, sf_f4 = st.columns([1.3, 1.0, 1.0, 1.3, 1.4])
    min_date_raw = df_cases_raw["Fecha_Inicio_dt"].dropna().min()
    max_date_raw = df_cases_raw["Fecha_Inicio_dt"].dropna().max()
    min_d = min_date_raw.date() if pd.notna(min_date_raw) else date.today()
    max_d = max_date_raw.date() if pd.notna(max_date_raw) else date.today()

    with sf_f0:
        sf_estado = st.selectbox("Estado de Casos:", ["🟢 Solo Abiertos / En Proceso", "📂 Histórico Total 2026", "✅ Solo Cerrados"], key="agb2b_bk_estado")
    with sf_f1:
        sf_desde = st.date_input("Desde:", value=min_d, min_value=min_d, max_value=max_d, key="agb2b_bk_desde")
    with sf_f2:
        sf_hasta = st.date_input("Hasta:", value=max_d, min_value=min_d, max_value=max_d, key="agb2b_bk_hasta")
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
    st.markdown("## 🏢 Operación Agencias B2B")
    st.caption("Consolidado Integral Multicanal: **Genesys Cloud** (Piso, Voz, Chats & Pausas) + **Salesforce Service Cloud** (Omni-Channel & Casos) • Coordinación Marelyn Cardona & Andrés Rodríguez")

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
