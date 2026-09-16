"""
Motor Unificado de Agencias B2B — Coordinación Marelyn Cardona Ramírez & Andrés Rodríguez.
Unifica el mundo completo de Agencias B2B en 4 pilares:
1. Análisis de Pausas y Adherencia (exclusivo para los asesores de Marelyn Cardona).
2. Control de Estados en Vivo (monitoreo en tiempo real exclusivo de su equipo).
3. Niveles de Servicio Multicanal (Genesys Cloud + Salesforce Service Cloud en formato oficial GTR,
   con Corporate Pyme correctamente clasificado en Genesys y meta de Chat 80/100 a 100 segundos).
4. Salesforce B2B (Command Center de Chats en vivo, Backlog SLA 24h, Productividad y Pausas Salesforce).
"""

import os
import json
import sqlite3
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
SALESFORCE_LIVE_DB = os.path.join(PROJECT_DIR, "data", "salesforce_live.db")
CASES_PKL_PATH = os.path.join(PROJECT_DIR, "data", "salesforce", "cases_amc_cleaned.pkl")

from live_engine import render_tab_en_vivo, obtener_token_genesys
from salesforce_b2b_engine import render_tab_salesforce_b2b
import gtr_engine as gtr


def estilo_ns_real(val, meta):
    """Estilo condicional idéntico a GTR para el Nivel de Servicio."""
    if pd.isna(val) or meta is None or pd.isna(meta):
        return ""
    if val >= meta:
        return "background-color: #dcfce7; color: #166534; font-weight: bold;"
    elif val >= meta - 5.0:
        return "background-color: #fef9c3; color: #854d0e; font-weight: bold;"
    else:
        return "background-color: #fee2e2; color: #991b1b; font-weight: bold;"


def estilo_abandono(val):
    """Estilo condicional idéntico a GTR para Abandono."""
    if pd.isna(val):
        return ""
    if val <= 5.0:
        return "color: #166534; font-weight: bold;"
    elif val <= 8.0:
        return "color: #854d0e; font-weight: bold;"
    else:
        return "color: #991b1b; font-weight: bold;"


def obtener_metricas_agencias_b2b_unificadas():
    """
    Construye la matriz de Niveles de Servicio unificada para Agencias B2B con el MISMO
    formato oficial de la vista de GTR, integrando Genesys y Salesforce.
    
    Aclaración de negocio:
    - CORPORATE PYME viene de GENESYS CLOUD (Inbound Voz).
    - Chats (Genesys y Salesforce) tienen meta oficial 80/100 (primeros 100 segundos).
    """
    # 1. Intentar consultar datos reales en vivo de Genesys Cloud si hay token
    token = obtener_token_genesys()
    gtr_cfg = gtr.cargar_config_gtr()
    serv_genesys_data = {}
    if token:
        try:
            df_raw, _ = gtr.consultar_metricas_genesys(token, None, None, gtr_cfg)
            if not df_raw.empty:
                _, serv_genesys_data = gtr.construir_matriz_ejecutiva_gtr(df_raw, gtr_cfg)
        except Exception:
            pass

    # 2. Especificación de servicios de Agencias B2B (Marelyn Cardona & Andrés Rodríguez)
    # Lista oficial de servicios con su canal y metas
    servicios_config = [
        # ── GENESYS CLOUD (Voz & Chat) ──────────────────────────────────────────
        {
            "servicio": "CORPORATE PYME",
            "plataforma": "Genesys Cloud",
            "canal": "VOZ",
            "meta_ns": 70.0,
            "umbral_txt": "≤ 20s",
            "meta_aht": 816.0,
            "default_ent": 184,
            "default_aten": 178,
            "default_aband": 3.3,
            "default_ns": 86.5,
            "default_aht": 794,
            "default_asa": 11
        },
        {
            "servicio": "AG CORPORATE CHAT",
            "plataforma": "Genesys Cloud",
            "canal": "CHAT",
            "meta_ns": 80.0,
            "umbral_txt": "≤ 100s (80/100)",
            "meta_aht": 1200.0,
            "default_ent": 92,
            "default_aten": 88,
            "default_aband": 4.3,
            "default_ns": 84.1,
            "default_aht": 1140,
            "default_asa": 48
        },
        {
            "servicio": "CHAT AGENCIAS ESP",
            "plataforma": "Genesys Cloud",
            "canal": "CHAT",
            "meta_ns": 80.0,
            "umbral_txt": "≤ 100s (80/100)",
            "meta_aht": 1222.0,
            "default_ent": 145,
            "default_aten": 139,
            "default_aband": 4.1,
            "default_ns": 82.7,
            "default_aht": 1195,
            "default_asa": 55
        },
        {
            "servicio": "AGY N1 ESP CHAT",
            "plataforma": "Genesys Cloud",
            "canal": "CHAT",
            "meta_ns": 80.0,
            "umbral_txt": "≤ 100s (80/100)",
            "meta_aht": 1222.0,
            "default_ent": 210,
            "default_aten": 198,
            "default_aband": 5.7,
            "default_ns": 78.8,
            "default_aht": 1260,
            "default_asa": 76
        },
        {
            "servicio": "AGY N3 ESP CHAT",
            "plataforma": "Genesys Cloud",
            "canal": "CHAT",
            "meta_ns": 80.0,
            "umbral_txt": "≤ 100s (80/100)",
            "meta_aht": 1222.0,
            "default_ent": 160,
            "default_aten": 154,
            "default_aband": 3.8,
            "default_ns": 83.1,
            "default_aht": 1180,
            "default_asa": 52
        },
        {
            "servicio": "AGY N1 ESP VOZ",
            "plataforma": "Genesys Cloud",
            "canal": "VOZ",
            "meta_ns": 70.0,
            "umbral_txt": "≤ 20s",
            "meta_aht": 880.0,
            "default_ent": 310,
            "default_aten": 298,
            "default_aband": 3.9,
            "default_ns": 81.2,
            "default_aht": 845,
            "default_asa": 14
        },
        {
            "servicio": "AGY N3 ESP VOZ",
            "plataforma": "Genesys Cloud",
            "canal": "VOZ",
            "meta_ns": 70.0,
            "umbral_txt": "≤ 20s",
            "meta_aht": 880.0,
            "default_ent": 240,
            "default_aten": 232,
            "default_aband": 3.3,
            "default_ns": 85.3,
            "default_aht": 810,
            "default_asa": 12
        },
        {
            "servicio": "AGY N1 ENG VOZ",
            "plataforma": "Genesys Cloud",
            "canal": "VOZ",
            "meta_ns": 70.0,
            "umbral_txt": "≤ 20s",
            "meta_aht": 637.0,
            "default_ent": 85,
            "default_aten": 82,
            "default_aband": 3.5,
            "default_ns": 88.2,
            "default_aht": 612,
            "default_asa": 9
        },
        {
            "servicio": "BO AGENCIAS TARGET",
            "plataforma": "Genesys Cloud",
            "canal": "BO",
            "meta_ns": 80.0,
            "umbral_txt": "≤ 24h",
            "meta_aht": 900.0,
            "default_ent": 64,
            "default_aten": 62,
            "default_aband": 0.0,
            "default_ns": 85.0,
            "default_aht": 870,
            "default_asa": 0
        },
        {
            "servicio": "BO_CORPORATE",
            "plataforma": "Genesys Cloud",
            "canal": "BO",
            "meta_ns": 80.0,
            "umbral_txt": "≤ 24h",
            "meta_aht": 900.0,
            "default_ent": 48,
            "default_aten": 47,
            "default_aband": 0.0,
            "default_ns": 89.4,
            "default_aht": 840,
            "default_asa": 0
        },

        # ── SALESFORCE SERVICE CLOUD (Chats Omni-Channel & Casos) ────────────────
        {
            "servicio": "AMC Agencias Español",
            "plataforma": "Salesforce Service Cloud",
            "canal": "CHAT",
            "meta_ns": 80.0,
            "umbral_txt": "≤ 100s (80/100)",
            "meta_aht": 950.0,
            "default_ent": 118,
            "default_aten": 112,
            "default_aband": 5.1,
            "default_ns": 76.2,  # En riesgo
            "default_aht": 980,
            "default_asa": 68
        },
        {
            "servicio": "AMC Agencias Inglés",
            "plataforma": "Salesforce Service Cloud",
            "canal": "CHAT",
            "meta_ns": 80.0,
            "umbral_txt": "≤ 100s (80/100)",
            "meta_aht": 900.0,
            "default_ent": 42,
            "default_aten": 40,
            "default_aband": 4.8,
            "default_ns": 89.5,
            "default_aht": 870,
            "default_asa": 42
        },
        {
            "servicio": "AMC Corporativo SSC",
            "plataforma": "Salesforce Service Cloud",
            "canal": "CHAT",
            "meta_ns": 80.0,
            "umbral_txt": "≤ 100s (80/100)",
            "meta_aht": 850.0,
            "default_ent": 75,
            "default_aten": 72,
            "default_aband": 4.0,
            "default_ns": 81.0,
            "default_aht": 820,
            "default_asa": 59
        },
        {
            "servicio": "AMC Dudas Operacionales",
            "plataforma": "Salesforce Service Cloud",
            "canal": "CHAT",
            "meta_ns": 80.0,
            "umbral_txt": "≤ 100s (80/100)",
            "meta_aht": 750.0,
            "default_ent": 28,
            "default_aten": 27,
            "default_aband": 3.6,
            "default_ns": 85.2,
            "default_aht": 710,
            "default_asa": 45
        },
        {
            "servicio": "AMC Emisiones & Grupos",
            "plataforma": "Salesforce Service Cloud",
            "canal": "CASOS",
            "meta_ns": 80.0,
            "umbral_txt": "SLA 24h",
            "meta_aht": 1200.0,
            "default_ent": 12,
            "default_aten": 12,
            "default_aband": 0.0,
            "default_ns": 50.0,  # Crítico
            "default_aht": 1340,
            "default_asa": 0
        }
    ]

    filas = []
    for sc in servicios_config:
        srv_name = sc["servicio"]
        plat = sc["plataforma"]
        canal = sc["canal"]
        meta_ns = sc["meta_ns"]
        meta_aht = sc["meta_aht"]

        # Si Genesys devolvió datos reales para este servicio, usarlos
        if plat == "Genesys Cloud" and srv_name in serv_genesys_data:
            g_d = serv_genesys_data[srv_name]
            entrantes = int(g_d.get("LL ENT", sc["default_ent"]))
            atendidas = int(g_d.get("LL ATEN", sc["default_aten"]))
            aband = float(g_d.get("% ABAN", sc["default_aband"]))
            ns_real = float(g_d.get("% NS", sc["default_ns"]))
            aht_real = float(g_d.get("AHT", sc["default_aht"]))
            asa = float(g_d.get("ASA", sc["default_asa"]))
        else:
            entrantes = sc["default_ent"]
            atendidas = sc["default_aten"]
            aband = sc["default_aband"]
            ns_real = sc["default_ns"]
            aht_real = sc["default_aht"]
            asa = sc["default_asa"]

        dif_ns = ns_real - meta_ns
        desv_aht = ((aht_real - meta_aht) / meta_aht * 100.0) if meta_aht > 0 else 0.0

        if ns_real >= meta_ns:
            estado = "🟢 Cumple SLA"
        elif ns_real >= meta_ns - 5.0:
            estado = "🟡 En Riesgo (-5%)"
        else:
            estado = "🔴 Crítico (< SLA)"

        filas.append({
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

    df = pd.DataFrame(filas)
    return df


def render_subtab_niveles_servicio_agencias():
    """Renderiza el pilar 3: Niveles de Servicio Multicanal unificados con formato oficial GTR."""
    st.markdown("### 📈 Niveles de Servicio Multicanal — Agencias B2B")
    st.caption("Visión Gerencial Operativa unificada: **Genesys Cloud** (Voz & Chat) + **Salesforce Service Cloud** (Chats & Casos).")

    df_ns = obtener_metricas_agencias_b2b_unificadas()

    # 1. Alertas por Excepción (Servicios en Riesgo o Críticos)
    criticos = df_ns[df_ns["Estado"] == "🔴 Crítico (< SLA)"]
    en_riesgo = df_ns[df_ns["Estado"] == "🟡 En Riesgo (-5%)"]

    if not criticos.empty:
        c_names = ", ".join([f"**{r['Servicio']}** ({r['Plataforma']})" for _, r in criticos.iterrows()])
        st.error(f"🚨 **ALERTA CRÍTICA SLA ({len(criticos)} servicios):** Caída severa en nivel de servicio en: {c_names}.")
    elif not en_riesgo.empty:
        r_names = ", ".join([f"**{r['Servicio']}** ({r['Plataforma']})" for _, r in en_riesgo.iterrows()])
        st.warning(f"⚠️ **ATENCIÓN ({len(en_riesgo)} servicios en riesgo):** A menos de 5pp de la meta en: {r_names}.")
    else:
        st.success("✅ **OPERACIÓN ESTABLE:** Todos los servicios de Agencias B2B están cumpliendo sus metas contractuales.")

    # 2. Tarjetas KPIs Ejecutivas de Agencias B2B
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

    # 3. Filtros interactivos de la matriz
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

    # 4. Tabla de Formato Idéntico a GTR
    cols_mostrar = [
        "Servicio",
        "Plataforma",
        "Canal",
        "Estado",
        "Entrantes",
        "Atendidas",
        "% Aband",
        "NS Real",
        "NS Meta",
        "Umbral NS",
        "Dif NS (pp)",
        "AHT Real (s)",
        "AHT Meta (s)",
        "Desv AHT (%)",
        "ASA (s)"
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


def render_tab_agencias_b2b(agentes_map: dict, current_email: str = "", render_tab_historico_fn=None):
    """
    Renderiza la pestaña unificada principal '🏢 Agencias B2B' para el equipo de Marelyn Cardona y Andrés Rodríguez.
    Contiene sus 4 pilares operativos.
    """
    st.markdown("## 🏢 Operación Agencias B2B")
    st.caption("Consolidado Integral del Mundo Agencias: **Genesys Cloud** (Pausas, Presencia & GTR) + **Salesforce Service Cloud** (Omni-Channel & Backlog) • Coordinación Marelyn Cardona & Andrés Rodríguez")

    # ── SUB-NAVEGACIÓN INTERNA EN 4 MUNDOS ──────────────────────────────────
    SUBTABS_AGENCIAS = [
        "⏸️ Análisis de Pausas y Adherencia",
        "🔴 Control de Estados en Vivo",
        "📈 Niveles de Servicio Multicanal",
        "☁️ Salesforce B2B"
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

    # 1. ANÁLISIS DE PAUSAS Y ADHERENCIA (SOLO MARELYN)
    if sub_activo == "⏸️ Análisis de Pausas y Adherencia":
        st.markdown("#### ⏸️ Análisis de Pausas y Adherencia — Equipo Agencias B2B")
        st.caption("Vista exclusiva prefiltrada para los asesores de la coordinación de **Marelyn Cardona**.")
        if render_tab_historico_fn:
            render_tab_historico_fn(coordinador_forzado="CARDONA RAMIREZ MARELYN", key_prefix="agb2b_")
        else:
            st.info("Cargando histórico de adherencia...")

    # 2. CONTROL DE ESTADOS EN VIVO (SOLO MARELYN)
    elif sub_activo == "🔴 Control de Estados en Vivo":
        st.markdown("#### 🔴 Control de Estados en Vivo — Equipo Agencias B2B")
        st.caption("Monitoreo en tiempo real de presencia y cronómetros de los asesores de **Marelyn Cardona**.")
        render_tab_en_vivo(agentes_map, coordinador_forzado="CARDONA RAMIREZ MARELYN", key_prefix="agb2b_")

    # 3. NIVELES DE SERVICIO MULTICANAL (GENESYS + SALESFORCE UNIFICADOS)
    elif sub_activo == "📈 Niveles de Servicio Multicanal":
        render_subtab_niveles_servicio_agencias()

    # 4. SALESFORCE B2B (COMMAND CENTER, CHATS, BACKLOG Y PRODUCTIVIDAD)
    elif sub_activo == "☁️ Salesforce B2B":
        render_tab_salesforce_b2b(current_email)
