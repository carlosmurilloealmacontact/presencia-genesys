"""
Motor de Simultaneidad WhatsApp — LATAM Pasajeros (Genesys Cloud)
Autor: Antigravity / Carlos Murillo

Permite:
1. Monitoreo en tiempo real (En Vivo): Detección de chats activos simultáneos por asesor.
2. Análisis histórico (Auditoría): Algoritmo de traslape (sweep-line) para calcular:
   - Simultaneidad promedio ponderada durante la interacción.
   - Distribución de carga temporal (1x, 2x, 3x+ chats concurrentes).
   - Cruce con dotación de asesores y servicios de Pasajeros.
"""

import os
import sys
import json
import time
from datetime import datetime, timedelta, timezone, date
from concurrent.futures import ThreadPoolExecutor
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COLOMBIA_TZ = timezone(timedelta(hours=-5))
META_SIMULTANEIDAD_WHATSAPP = 3.0  # Meta estándar de 3 casos simultáneos para todos los servicios WSP

# ── 1. DESCUBRIMIENTO Y MAPEADO DE COLAS WHATSAPP ─────────────────────────────

@st.cache_data(ttl=3600*6, show_spinner=False)
def obtener_colas_whatsapp(token: str) -> list[dict]:
    """
    Descubre y cataloga las colas de Genesys Cloud destinadas a WhatsApp.
    Filtra por identificadores como 'WSP', 'WHATSAPP', etc.
    """
    headers = {"Authorization": f"Bearer {token}"}
    base_url = "https://api.mypurecloud.com"
    all_queues = []
    page = 1
    while page <= 10:
        try:
            r = requests.get(
                f"{base_url}/api/v2/routing/queues?pageSize=100&pageNumber={page}",
                headers=headers,
                timeout=12
            )
            if r.status_code != 200:
                break
            data = r.json()
            entities = data.get("entities", [])
            if not entities:
                break
            all_queues.extend(entities)
            if page >= data.get("pageCount", 1):
                break
            page += 1
        except Exception:
            break

    # Filtro específico para WhatsApp
    wsp_queues = []
    for q in all_queues:
        name = q.get("name", "")
        name_u = name.upper()
        if "_WSP_" in name_u or "WHATSAPP" in name_u or name_u.startswith("AMC_WSP") or "WSP" in name_u:
            wsp_queues.append({
                "id": q.get("id"),
                "name": name
            })

    return sorted(wsp_queues, key=lambda x: x["name"])


# ── 2. SIMULTANEIDAD EN VIVO (TIEMPO REAL) ────────────────────────────────────

@st.cache_data(ttl=15, show_spinner=False)
def obtener_simultaneidad_en_vivo(token: str) -> dict:
    """
    Consulta en tiempo real todas las conversaciones activas de WhatsApp
    sin finalizar (conversationEnd == null) y contabiliza la simultaneidad actual
    por asesor (userId).
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    base_url = "https://api.mypurecloud.com"
    now_utc = datetime.now(timezone.utc)
    start_utc = now_utc - timedelta(hours=6)
    interval = f"{start_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}/{now_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}"

    body = {
        "interval": interval,
        "conversationFilters": [
            {
                "type": "and",
                "predicates": [
                    {"type": "dimension", "dimension": "conversationEnd", "operator": "notExists"}
                ]
            }
        ],
        "segmentFilters": [
            {
                "type": "and",
                "predicates": [
                    {"type": "dimension", "dimension": "mediaType", "operator": "matches", "value": "message"},
                    {"type": "dimension", "dimension": "messageType", "operator": "matches", "value": "whatsapp"}
                ]
            }
        ],
        "paging": {"pageSize": 100, "pageNumber": 1}
    }

    agentes_wsp = {}
    total_chats_activos = 0

    try:
        page = 1
        while page <= 3:  # Hasta 300 conversaciones activas simultáneas
            body["paging"]["pageNumber"] = page
            r = requests.post(f"{base_url}/api/v2/analytics/conversations/details/query", headers=headers, json=body, timeout=12)
            if r.status_code != 200:
                break
            data = r.json()
            convs = data.get("conversations", [])
            if not convs:
                break

            for c in convs:
                conv_id = c.get("conversationId")
                for p in c.get("participants", []):
                    if p.get("purpose") == "agent":
                        uid = p.get("userId")
                        uname = p.get("participantName") or uid
                        # Validar si el agente tiene el segmento abierto (interact o hold sin segmentEnd)
                        is_interacting = False
                        for s in p.get("sessions", []):
                            for seg in s.get("segments", []):
                                if seg.get("segmentType") in ("interact", "hold") and not seg.get("segmentEnd"):
                                    is_interacting = True
                                    break
                            if is_interacting:
                                break

                        if is_interacting and uid:
                            if uid not in agentes_wsp:
                                agentes_wsp[uid] = {
                                    "user_id": uid,
                                    "nombre": uname,
                                    "chats_activos": 0,
                                    "conversaciones": []
                                }
                            agentes_wsp[uid]["chats_activos"] += 1
                            agentes_wsp[uid]["conversaciones"].append(conv_id)
                            total_chats_activos += 1

            if len(convs) < 100:
                break
            page += 1

    except Exception as err:
        print(f"Error consultando simultaneidad en vivo WhatsApp: {err}")

    return {
        "agentes": agentes_wsp,
        "total_chats_activos": total_chats_activos,
        "total_asesores_activos": len(agentes_wsp),
        "max_simultaneidad": max([a["chats_activos"] for a in agentes_wsp.values()], default=0),
        "actualizado_a": datetime.now(COLOMBIA_TZ).strftime("%I:%M:%S %p")
    }


# ── 3. SIMULTANEIDAD HISTÓRICA & ALGORITMO SWEEP-LINE ─────────────────────────

def calcular_simultaneidad_sweep_line(intervalos: list[tuple[datetime, datetime]]) -> dict:
    """
    Algoritmo Sweep-Line (barrido temporal de eventos) para calcular con
    precisión exacta la simultaneidad promedio ponderada y la distribución
    por niveles (1x, 2x, 3x, etc.).
    """
    if not intervalos:
        return {
            "tiempo_interact_sec": 0,
            "tiempo_interact_min": 0.0,
            "simultaneidad_promedio": 0.0,
            "max_concurrencia": 0,
            "distribucion_min": {1: 0.0, 2: 0.0, 3: 0.0},
            "pct_distribucion": {1: 0.0, 2: 0.0, 3: 0.0}
        }

    events = []
    for s, e in intervalos:
        if e > s:
            events.append((s, 1))
            events.append((e, -1))

    if not events:
        return {
            "tiempo_interact_sec": 0,
            "tiempo_interact_min": 0.0,
            "simultaneidad_promedio": 0.0,
            "max_concurrencia": 0,
            "distribucion_min": {1: 0.0, 2: 0.0, 3: 0.0},
            "pct_distribucion": {1: 0.0, 2: 0.0, 3: 0.0}
        }

    # Ordenar eventos cronológicamente
    events.sort(key=lambda x: x[0])

    time_at_level = {}
    current_level = 0
    last_time = None
    max_level = 0

    for t, change in events:
        if last_time is not None and t > last_time:
            dur = (t - last_time).total_seconds()
            if current_level > 0:
                time_at_level[current_level] = time_at_level.get(current_level, 0.0) + dur
        current_level += change
        if current_level > max_level:
            max_level = current_level
        last_time = t

    total_interact_sec = sum(dur for lvl, dur in time_at_level.items() if lvl > 0)
    weighted_sum = sum(lvl * dur for lvl, dur in time_at_level.items() if lvl > 0)
    avg_concurrency = (weighted_sum / total_interact_sec) if total_interact_sec > 0 else 0.0

    # Agrupar distribución: 1x, 2x, 3x o más
    sec_1x = time_at_level.get(1, 0.0)
    sec_2x = time_at_level.get(2, 0.0)
    sec_3x_plus = sum(dur for lvl, dur in time_at_level.items() if lvl >= 3)

    pct_1x = (sec_1x / total_interact_sec * 100) if total_interact_sec > 0 else 0.0
    pct_2x = (sec_2x / total_interact_sec * 100) if total_interact_sec > 0 else 0.0
    pct_3x_plus = (sec_3x_plus / total_interact_sec * 100) if total_interact_sec > 0 else 0.0

    return {
        "tiempo_interact_sec": total_interact_sec,
        "tiempo_interact_min": round(total_interact_sec / 60.0, 1),
        "simultaneidad_promedio": round(avg_concurrency, 2),
        "max_concurrencia": max_level,
        "distribucion_min": {
            1: round(sec_1x / 60.0, 1),
            2: round(sec_2x / 60.0, 1),
            3: round(sec_3x_plus / 60.0, 1)
        },
        "pct_distribucion": {
            1: round(pct_1x, 1),
            2: round(pct_2x, 1),
            3: round(pct_3x_plus, 1)
        }
    }


@st.cache_data(ttl=600, show_spinner=False)
def obtener_simultaneidad_historica(token: str, fecha_str: str) -> dict:
    """
    Descarga y calcula la simultaneidad de WhatsApp para una fecha específica (YYYY-MM-DD).
    Cubre el horario de Colombia de 00:00:00 a 23:59:59 (en UTC de 05:00 a 05:00 del día siguiente).
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    base_url = "https://api.mypurecloud.com"

    try:
        target_dt = datetime.strptime(fecha_str, "%Y-%m-%d")
    except Exception:
        target_dt = datetime.now()

    # Horario Colombia a UTC: fecha 05:00 UTC hasta fecha+1 05:00 UTC
    s_utc = datetime(target_dt.year, target_dt.month, target_dt.day, 5, 0, 0, tzinfo=timezone.utc)
    e_utc = s_utc + timedelta(days=1)
    now_utc = datetime.now(timezone.utc)
    if e_utc > now_utc:
        e_utc = now_utc

    interval = f"{s_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}/{e_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}"

    body = {
        "interval": interval,
        "order": "asc",
        "orderBy": "conversationStart",
        "segmentFilters": [
            {
                "type": "and",
                "predicates": [
                    {"type": "dimension", "dimension": "mediaType", "operator": "matches", "value": "message"},
                    {"type": "dimension", "dimension": "messageType", "operator": "matches", "value": "whatsapp"}
                ]
            }
        ],
        "paging": {"pageSize": 100, "pageNumber": 1}
    }

    agent_intervals = {}
    agent_info = {}
    total_conversaciones = 0

    page = 1
    max_pages = 25  # Hasta 2,500 conversaciones por día

    while page <= max_pages:
        body["paging"]["pageNumber"] = page
        try:
            r = requests.post(f"{base_url}/api/v2/analytics/conversations/details/query", headers=headers, json=body, timeout=20)
            if r.status_code != 200:
                break
            data = r.json()
            convs = data.get("conversations", [])
            if not convs:
                break

            total_conversaciones += len(convs)

            for c in convs:
                for p in c.get("participants", []):
                    if p.get("purpose") == "agent":
                        uid = p.get("userId")
                        uname = p.get("participantName") or uid
                        if not uid:
                            continue

                        if uid not in agent_info:
                            agent_info[uid] = {
                                "nombre": uname,
                                "chats_count": 0
                            }
                        agent_info[uid]["chats_count"] += 1

                        if uid not in agent_intervals:
                            agent_intervals[uid] = []

                        for s in p.get("sessions", []):
                            if s.get("mediaType") == "message":
                                for seg in s.get("segments", []):
                                    if seg.get("segmentType") == "interact":
                                        st_s = seg.get("segmentStart")
                                        et_s = seg.get("segmentEnd")
                                        if st_s and et_s:
                                            try:
                                                st_dt = datetime.fromisoformat(st_s.replace("Z", "+00:00"))
                                                et_dt = datetime.fromisoformat(et_s.replace("Z", "+00:00"))
                                                agent_intervals[uid].append((st_dt, et_dt))
                                            except Exception:
                                                pass

            if len(convs) < 100:
                break
            page += 1
        except Exception as err:
            print(f"Error procesando página {page} de histórico WhatsApp: {err}")
            break

    # Ejecutar sweep-line por asesor
    resultados_asesores = {}
    for uid, intervals in agent_intervals.items():
        metricas = calcular_simultaneidad_sweep_line(intervals)
        resultados_asesores[uid] = {
            "user_id": uid,
            "nombre": agent_info.get(uid, {}).get("nombre", uid),
            "chats_atendidos": agent_info.get(uid, {}).get("chats_count", 0),
            **metricas
        }

    # Promedio simple de simultaneidad entre asesores con interacción
    asesores_con_interact = [r for r in resultados_asesores.values() if r["tiempo_interact_sec"] > 0]
    promedio_simultaneidad_asesores = (
        round(sum(r["simultaneidad_promedio"] for r in asesores_con_interact) / len(asesores_con_interact), 2)
        if asesores_con_interact else 0.0
    )

    return {
        "fecha": fecha_str,
        "total_conversaciones": total_conversaciones,
        "total_asesores": len(resultados_asesores),
        "promedio_simultaneidad": promedio_simultaneidad_asesores,
        "asesores": resultados_asesores
    }


# ── 4. RENDERIZADO VISUAL EN STREAMLIT (HISTÓRICO) ────────────────────────────

def render_panel_simultaneidad_whatsapp_historico(token: str, fecha_sel: date, agentes_map: dict, coordinador_filtro: str = None):
    """
    Renderiza el panel de análisis y auditoría de simultaneidad WhatsApp en Streamlit.
    """
    st.markdown("### 💬 Auditoría de Simultaneidad WhatsApp")
    st.caption(
        "Monitoreo analítico de concurrencia e interacciones en colas WhatsApp de LATAM Pasajeros • "
        "Medición precisa basada en traslapes de segmentos de atención (*sweep-line*)."
    )

    fecha_str = fecha_sel.strftime("%Y-%m-%d")

    col_btn, col_info = st.columns([1, 4])
    with col_btn:
        btn_calc = st.button("🔍 Calcular Simultaneidad", type="primary", use_container_width=True, key=f"btn_wsp_calc_{fecha_str}")

    if not btn_calc and f"wsp_data_{fecha_str}" not in st.session_state:
        st.info(f"💡 Haz clic en **'Calcular Simultaneidad'** para auditar las conversaciones de WhatsApp del día **{fecha_str}**.")
        return

    with st.spinner(f"Analizando interacciones de WhatsApp en Genesys Cloud para el {fecha_str}..."):
        data_hist = obtener_simultaneidad_historica(token, fecha_str)
        st.session_state[f"wsp_data_{fecha_str}"] = True

    asesores_dict = data_hist.get("asesores", {})
    if not asesores_dict:
        st.warning(f"No se registraron interacciones en colas de WhatsApp en la fecha {fecha_str}.")
        return

    # Mapear metadatos de asesor (Servicio, Coordinador, Supervisor)
    filas = []
    for uid, d in asesores_dict.items():
        meta = agentes_map.get(uid, {})
        coord = meta.get("coordinador", "Sin Asignar")
        sup = meta.get("supervisor", "Sin Asignar")
        servicio = meta.get("servicio", "WhatsApp Pasajeros")

        # Filtro de coordinador si aplica
        if coordinador_filtro and coordinador_filtro != "TODOS":
            if coordinador_filtro.upper() not in coord.upper():
                continue

        filas.append({
            "Asesor": d["nombre"],
            "Servicio": servicio,
            "Coordinador": coord,
            "Supervisor": sup,
            "Chats Atendidos": d["chats_atendidos"],
            "Tpo. Interacción (min)": d["tiempo_interact_min"],
            "Simultaneidad Promedio": d["simultaneidad_promedio"],
            "Max Concurrencia": f"{d['max_concurrencia']}x",
            "% al 1x": d["pct_distribucion"].get(1, 0.0),
            "% al 2x": d["pct_distribucion"].get(2, 0.0),
            "% al 3x+": d["pct_distribucion"].get(3, 0.0),
            "Min 1x": d["distribucion_min"].get(1, 0.0),
            "Min 2x": d["distribucion_min"].get(2, 0.0),
            "Min 3x+": d["distribucion_min"].get(3, 0.0),
        })

    if not filas:
        st.info("No hay datos para los filtros seleccionados.")
        return

    df_wsp = pd.DataFrame(filas)

    # ── Tarjetas KPI Consolidadas ───────────────────────────────────────────────
    tot_chats = df_wsp["Chats Atendidos"].sum()
    tot_asesores = len(df_wsp)
    simult_global = round(df_wsp["Simultaneidad Promedio"].mean(), 2)
    pct_alta_simult = round(df_wsp["% al 2x"].mean() + df_wsp["% al 3x+"].mean(), 1)
    max_pico = df_wsp["Max Concurrencia"].str.replace("x", "").astype(int).max()

    cumplimiento_meta = round((simult_global / META_SIMULTANEIDAD_WHATSAPP) * 100, 1) if META_SIMULTANEIDAD_WHATSAPP > 0 else 0.0
    delta_color = "normal" if simult_global >= META_SIMULTANEIDAD_WHATSAPP else "off"

    k1, k2, k3, k4, k5 = st.columns(5)
    with k1:
        st.metric("💬 Chats WhatsApp", f"{tot_chats:,}", delta="Total Atendidos")
    with k2:
        st.metric("👥 Asesores Activos", tot_asesores, delta="Operación WSP")
    with k3:
        st.metric("⚡ Simultaneidad Promedio", f"{simult_global}x", delta=f"Meta: {META_SIMULTANEIDAD_WHATSAPP:.1f}x ({cumplimiento_meta}%)", delta_color=delta_color)
    with k4:
        st.metric("🔥 Carga Simultánea (≥2x)", f"{pct_alta_simult}%", delta="% tiempo en 2 o más chats")
    with k5:
        st.metric("🚀 Pico Concurrencia", f"{max_pico}x", delta="Máxima alcanzada")

    st.write("")

    # ── Gráfica de Distribución de Carga ────────────────────────────────────────
    c_g1, c_g2 = st.columns([1.5, 1])
    with c_g1:
        st.markdown("##### 📊 Distribución de Carga por Asesor (1x vs 2x vs 3x+)")
        df_top = df_wsp.sort_values(by="Chats Atendidos", ascending=False).head(15)
        fig_bar = go.Figure()
        fig_bar.add_trace(go.Bar(name="1 Chat (1x)", x=df_top["Asesor"], y=df_top["% al 1x"], marker_color="#60a5fa"))
        fig_bar.add_trace(go.Bar(name="2 Chats (2x)", x=df_top["Asesor"], y=df_top["% al 2x"], marker_color="#3b82f6"))
        fig_bar.add_trace(go.Bar(name="3+ Chats (3x+)", x=df_top["Asesor"], y=df_top["% al 3x+"], marker_color="#1d4ed8"))
        fig_bar.update_layout(
            barmode="stack",
            height=320,
            margin=dict(l=10, r=10, t=25, b=60),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            xaxis=dict(tickangle=-35),
            yaxis=dict(title="% Tiempo de Interacción", range=[0, 100])
        )
        st.plotly_chart(fig_bar, use_container_width=True)

    with c_g2:
        st.markdown("##### 🥧 Proporción Global de Simultaneidad")
        min_tot_1x = df_wsp["Min 1x"].sum()
        min_tot_2x = df_wsp["Min 2x"].sum()
        min_tot_3x = df_wsp["Min 3x+"].sum()
        fig_pie = px.pie(
            values=[min_tot_1x, min_tot_2x, min_tot_3x],
            names=["1x (1 chat)", "2x (2 chats)", "3x+ (3 o más chats)"],
            color_discrete_sequence=["#93c5fd", "#3b82f6", "#1e40af"],
            hole=0.45
        )
        fig_pie.update_layout(
            height=320,
            margin=dict(l=10, r=10, t=25, b=10),
            legend=dict(orientation="h", yanchor="bottom", y=-0.15, xanchor="center", x=0.5)
        )
        st.plotly_chart(fig_pie, use_container_width=True)

    # ── Tabla Detallada ────────────────────────────────────────────────────────
    st.markdown("##### 📋 Detalle de Simultaneidad por Asesor")

    # Formatear columnas
    cols_display = [
        "Asesor", "Servicio", "Coordinador", "Chats Atendidos",
        "Tpo. Interacción (min)", "Simultaneidad Promedio", "Max Concurrencia",
        "% al 1x", "% al 2x", "% al 3x+"
    ]
    df_sorted = df_wsp[cols_display].sort_values(by="Simultaneidad Promedio", ascending=False)

    st.dataframe(
        df_sorted.style.format({
            "Chats Atendidos": "{:d}",
            "Tpo. Interacción (min)": "{:.1f}",
            "Simultaneidad Promedio": "{:.2f}x",
            "% al 1x": "{:.1f}%",
            "% al 2x": "{:.1f}%",
            "% al 3x+": "{:.1f}%",
        }).background_gradient(subset=["Simultaneidad Promedio"], cmap="Blues"),
        use_container_width=True,
        hide_index=True
    )
