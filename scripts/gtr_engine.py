"""
Motor GTR y Niveles de Servicio — Radar Genesys Cloud.
Incluye:
1. Vista Gerencial Operativa (Alertas, semáforos de SLA y desvío de AHT ordenados por impacto).
2. Vista Técnica GTR (Matriz horizontal idéntica a HORA A HORA y desglose por 30 min).
3. Exportadores Fieles a Excel de los dos libros: (CONFIDENCIAL)HORA_HORA.xlsx y AHT_GENESYS.xlsx.
"""

from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import time

import numpy as np
import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

from live_engine import obtener_token_genesys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_GTR_PATH = os.path.join(BASE_DIR, "gtr_config.json")

# Orden oficial de columnas de servicios según HORA A HORA (DETALLE)
SERVICIOS_ORDEN_OFICIAL = [
    "TT_LATAM",
    "LUA AMC",
    "Soporte LUA AMC",
    "WPP LUA AMC",
    "HVC AMC",
    "Ventas AMC",
    "WPP Ventas AMC",
    "CHAT Ventas AMC",
    "TRAVEL WP AMC",
    "LUA AMC ING",
    "DREAM TEAMS VOZ",
    "DREAM TEAMS ENG",
    "CHAT DREAM TEAMS ES",
    "DREAM TEAMS WA",
    "TT_EQUIPAJES",
    "Equipajes AMC",
    "Equipajes AMC ING",
    "WPP EQUIPAJES AMC"
]

SERVICIOS_TT_LATAM = [
    "LUA AMC", "Soporte LUA AMC", "WPP LUA AMC", "HVC AMC",
    "Ventas AMC", "WPP Ventas AMC", "CHAT Ventas AMC", "TRAVEL WP AMC",
    "LUA AMC ING", "DREAM TEAMS VOZ", "DREAM TEAMS ENG", "CHAT DREAM TEAMS ES", "DREAM TEAMS WA"
]

SERVICIOS_TT_EQUIPAJES = [
    "Equipajes AMC", "Equipajes AMC ING", "WPP EQUIPAJES AMC"
]


def cargar_config_gtr():
    """Carga mapeo de colas y metas de NS y AHT."""
    if not os.path.exists(CONFIG_GTR_PATH):
        return {"queues": {}, "services": {}, "aht_metas": {}}
    with open(CONFIG_GTR_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def formatear_segundos_mm_ss(segundos):
    """Convierte segundos a formato MM:SS."""
    if pd.isna(segundos) or segundos is None or segundos < 0:
        return "-"
    m = int(segundos // 60)
    s = int(segundos % 60)
    return f"{m:02d}:{s:02d}"


@st.cache_data(ttl=60, show_spinner=False)
def obtener_metricas_gtr_api(token: str):
    """
    Consulta Genesys Cloud Analytics Conversation Aggregates para el día actual
    en bloques de 30 minutos (PT30M).
    """
    gtr_cfg = cargar_config_gtr()
    queues_cfg = gtr_cfg.get("queues", {})
    services_cfg = gtr_cfg.get("services", {})

    now_utc = datetime.now(timezone.utc)
    today_col_start = now_utc.replace(hour=5, minute=0, second=0, microsecond=0)
    if now_utc < today_col_start:
        today_col_start -= timedelta(days=1)

    s_start = today_col_start.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    s_end = now_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    interval = f"{s_start}/{s_end}"

    body = {
        "interval": interval,
        "granularity": "PT30M",
        "groupBy": ["queueId"],
        "metrics": ["nOffered", "tAnswered", "tAbandon", "tHandle", "oServiceLevel"]
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = "https://api.mypurecloud.com/api/v2/analytics/conversations/aggregates/query"

    try:
        r = requests.post(url, headers=headers, json=body, timeout=25)
        if r.status_code != 200:
            return pd.DataFrame(), f"Error API Genesys: {r.status_code} - {r.text[:200]}", ""
        res = r.json()
    except Exception as e:
        return pd.DataFrame(), f"Error de conexión con Genesys: {str(e)}", ""

    records = []
    for group in res.get("results", []):
        qid = group.get("group", {}).get("queueId")
        q_info = queues_cfg.get(qid, {})
        srv = q_info.get("servicio", "Otras Colas / No Mapeado")
        q_name = q_info.get("nombre_cola", qid or "Directo / Sin Cola")
        canal = services_cfg.get(srv, {}).get("canal", "VOZ" if "WPP" not in srv and "CHAT" not in srv else "DIGITAL")

        for interval_data in group.get("data", []):
            int_str = interval_data.get("interval", "")
            start_utc = int_str.split("/")[0] if "/" in int_str else int_str
            dt_col = pd.to_datetime(start_utc) - pd.Timedelta(hours=5)
            int_label = dt_col.strftime("%H:%M")

            row = {
                "queueId": qid,
                "nombre_cola": q_name,
                "servicio": srv,
                "canal": canal,
                "intervalo": int_label,
                "intervalo_dt": dt_col,
                "nOffered": 0,
                "tAnswered_count": 0,
                "tAnswered_sum": 0.0,
                "tAbandon_count": 0,
                "tAbandon_sum": 0.0,
                "tHandle_count": 0,
                "tHandle_sum": 0.0,
                "sl_numerator": 0,
                "sl_denominator": 0
            }
            for metric in interval_data.get("metrics", []):
                m_name = metric.get("metric")
                stats = metric.get("stats", {})
                if m_name == "nOffered":
                    row["nOffered"] = stats.get("count", 0)
                elif m_name == "tAnswered":
                    row["tAnswered_count"] = stats.get("count", 0)
                    row["tAnswered_sum"] = stats.get("sum", 0.0)
                elif m_name == "tAbandon":
                    row["tAbandon_count"] = stats.get("count", 0)
                    row["tAbandon_sum"] = stats.get("sum", 0.0)
                elif m_name == "tHandle":
                    row["tHandle_count"] = stats.get("count", 0)
                    row["tHandle_sum"] = stats.get("sum", 0.0)
                elif m_name == "oServiceLevel":
                    ratio = stats.get("ratio", 0.0)
                    denom = stats.get("denominator", 0)
                    row["sl_denominator"] = denom
                    row["sl_numerator"] = stats.get("numerator", int(round(ratio * denom)))
            records.append(row)

    now_col = now_utc - timedelta(hours=5)
    hora_actualizacion = now_col.strftime("%I:%M:%S %p")
    return pd.DataFrame(records), None, hora_actualizacion


@st.cache_data(ttl=60, show_spinner=False)
def obtener_aht_asesores_api(token: str):
    """
    Consulta métricas de manejo por agente (userId) para el día actual.
    """
    now_utc = datetime.now(timezone.utc)
    today_col_start = now_utc.replace(hour=5, minute=0, second=0, microsecond=0)
    if now_utc < today_col_start:
        today_col_start -= timedelta(days=1)

    s_start = today_col_start.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    s_end = now_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    interval = f"{s_start}/{s_end}"

    body = {
        "interval": interval,
        "groupBy": ["userId"],
        "filter": {
            "type": "and",
            "predicates": [
                {"dimension": "direction", "value": "inbound"}
            ]
        },
        "metrics": ["tHandle", "tTalk", "tHeld", "tAcw", "nTransferred"]
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = "https://api.mypurecloud.com/api/v2/analytics/conversations/aggregates/query"

    try:
        r = requests.post(url, headers=headers, json=body, timeout=25)
        if r.status_code != 200:
            return pd.DataFrame(), f"Error AHT Asesores: {r.status_code}"
        res = r.json()
    except Exception as e:
        return pd.DataFrame(), f"Error conexión AHT: {str(e)}"

    rows = []
    for item in res.get("results", []):
        uid = item.get("group", {}).get("userId")
        t_handle_count = 0
        t_handle_sum = 0.0
        t_talk_sum = 0.0
        t_held_sum = 0.0
        t_acw_sum = 0.0
        transf_count = 0

        for interval_data in item.get("data", []):
            for m in interval_data.get("metrics", []):
                m_name = m.get("metric")
                st_data = m.get("stats", {})
                if m_name == "tHandle":
                    t_handle_count += st_data.get("count", 0)
                    t_handle_sum += st_data.get("sum", 0.0)
                elif m_name == "tTalk":
                    t_talk_sum += st_data.get("sum", 0.0)
                elif m_name == "tHeld":
                    t_held_sum += st_data.get("sum", 0.0)
                elif m_name == "tAcw":
                    t_acw_sum += st_data.get("sum", 0.0)
                elif m_name == "nTransferred":
                    transf_count += st_data.get("count", 0)

        if t_handle_count > 0:
            aht_s = round(t_handle_sum / t_handle_count / 1000, 1)
            rows.append({
                "agente_id": uid,
                "interacciones": t_handle_count,
                "aht_seg": aht_s,
                "t_talk_seg": round(t_talk_sum / t_handle_count / 1000, 1),
                "t_held_seg": round(t_held_sum / t_handle_count / 1000, 1),
                "t_acw_seg": round(t_acw_sum / t_handle_count / 1000, 1),
                "transferidas": transf_count
            })

    return pd.DataFrame(rows), None


def construir_matriz_ejecutiva_gtr(df_raw: pd.DataFrame, gtr_cfg: dict):
    """
    Construye la matriz horizontal exacta de HORA A HORA (DETALLE filas 7 a 23).
    Métricas en filas, Macro-Servicios en columnas.
    """
    services_cfg = gtr_cfg.get("services", {})
    aht_metas = gtr_cfg.get("aht_metas", {})

    serv_data = {}
    todos_servicios = set(df_raw["servicio"].unique()).union(SERVICIOS_ORDEN_OFICIAL)

    for s in todos_servicios:
        if s in ("TT_LATAM", "TT_EQUIPAJES"):
            continue
        sub = df_raw[df_raw["servicio"] == s]
        off = sub["nOffered"].sum()
        ans = sub["tAnswered_count"].sum()
        abn = sub["tAbandon_count"].sum()
        sl_n = sub["sl_numerator"].sum()
        sl_d = sub["sl_denominator"].sum()
        h_s = sub["tHandle_sum"].sum()
        h_c = sub["tHandle_count"].sum()
        a_s = sub["tAnswered_sum"].sum()

        meta_ns = services_cfg.get(s, {}).get("ns_meta", None)
        meta_ns_val = meta_ns * 100.0 if meta_ns else None

        serv_data[s] = {
            "LL ENT": off,
            "LL ATEN": ans,
            "LL ABAN": abn,
            "LL Aten. NS": sl_n,
            "% ATEN": (ans / off * 100.0) if off > 0 else 0.0,
            "% ABAN": (abn / off * 100.0) if off > 0 else 0.0,
            "% NS META": meta_ns_val,
            "% NS": (sl_n / sl_d * 100.0) if sl_d > 0 else 0.0,
            "META AHT": aht_metas.get(s, None),
            "AHT": (h_s / h_c / 1000.0) if h_c > 0 else 0.0,
            "ASA": (a_s / ans / 1000.0) if ans > 0 else 0.0,
        }

    # Calcular Macro Consolidado TT_LATAM y TT_EQUIPAJES
    for tt_name, group_list in [("TT_LATAM", SERVICIOS_TT_LATAM), ("TT_EQUIPAJES", SERVICIOS_TT_EQUIPAJES)]:
        sub = df_raw[df_raw["servicio"].isin(group_list)]
        off = sub["nOffered"].sum()
        ans = sub["tAnswered_count"].sum()
        abn = sub["tAbandon_count"].sum()
        sl_n = sub["sl_numerator"].sum()
        sl_d = sub["sl_denominator"].sum()
        h_s = sub["tHandle_sum"].sum()
        h_c = sub["tHandle_count"].sum()
        a_s = sub["tAnswered_sum"].sum()

        serv_data[tt_name] = {
            "LL ENT": off,
            "LL ATEN": ans,
            "LL ABAN": abn,
            "LL Aten. NS": sl_n,
            "% ATEN": (ans / off * 100.0) if off > 0 else 0.0,
            "% ABAN": (abn / off * 100.0) if off > 0 else 0.0,
            "% NS META": 75.3 if tt_name == "TT_LATAM" else 78.1,
            "% NS": (sl_n / sl_d * 100.0) if sl_d > 0 else 0.0,
            "META AHT": 877.0 if tt_name == "TT_LATAM" else 993.0,
            "AHT": (h_s / h_c / 1000.0) if h_c > 0 else 0.0,
            "ASA": (a_s / ans / 1000.0) if ans > 0 else 0.0,
        }

    filas_metricas = [
        ("LL ENT", "Llamadas Entrantes (Ofrecidas)"),
        ("LL ATEN", "Llamadas Atendidas"),
        ("LL ABAN", "Llamadas Abandonadas"),
        ("LL Aten. NS", "Llamadas Atendidas en NS"),
        ("% ATEN", "% Nivel de Atención"),
        ("% ABAN", "% Nivel de Abandono"),
        ("% NS META", "% NS Meta Contractual"),
        ("% NS", "% NS Real"),
        ("META AHT", "Meta AHT (segundos)"),
        ("AHT", "AHT Real (segundos)"),
        ("% VAR AHT", "% Variación AHT vs Meta"),
        ("ASA", "ASA (Tiempo Espera Segundos)"),
    ]

    cols_servicios = [s for s in SERVICIOS_ORDEN_OFICIAL if s in serv_data]
    for s in sorted(serv_data.keys()):
        if s not in cols_servicios and serv_data[s]["LL ENT"] > 0:
            cols_servicios.append(s)

    filas_tabla = []
    for cod_m, desc_m in filas_metricas:
        row = {"Concepto / Métrica": desc_m}
        for srv in cols_servicios:
            sd = serv_data.get(srv, {})
            if cod_m == "% VAR AHT":
                aht_r = sd.get("AHT", 0)
                aht_m = sd.get("META AHT", None)
                if aht_m and aht_m > 0 and aht_r > 0:
                    row[srv] = f"{(aht_r - aht_m) / aht_m * 100.0:+.1f}%"
                else:
                    row[srv] = "-"
            elif cod_m in ("% ATEN", "% ABAN", "% NS META", "% NS"):
                v = sd.get(cod_m)
                row[srv] = f"{v:.1f}%" if v is not None else "-"
            elif cod_m in ("AHT", "META AHT", "ASA"):
                v = sd.get(cod_m)
                if v is not None and v > 0:
                    row[srv] = f"{int(round(v))}s ({formatear_segundos_mm_ss(v)})"
                else:
                    row[srv] = "-"
            else:
                v = sd.get(cod_m, 0)
                row[srv] = f"{int(v):,}"
        filas_tabla.append(row)

    df_matriz = pd.DataFrame(filas_tabla)
    return df_matriz, serv_data


# ── GENERADORES FIELES DE LIBROS EXCEL GTR CON PLANTILLAS MAESTRAS ──────────

def generar_excel_hora_hora_fiel(df_raw: pd.DataFrame, df_matriz: pd.DataFrame, gtr_cfg: dict) -> bytes:
    """
    Recrea fielmente el libro HORA A HORA usando la plantilla maestra templates/HORA_HORA_TEMPLATE.xlsx
    conservando todas las hojas, formatos originales, columnas y estilos.
    """
    tpl_path = os.path.join(BASE_DIR, "../templates/HORA_HORA_TEMPLATE.xlsx")
    if os.path.exists(tpl_path):
        wb = openpyxl.load_workbook(tpl_path)
    else:
        wb = openpyxl.Workbook()

    # 1. Inyectar Matriz en hoja DETALLE
    if "DETALLE" in wb.sheetnames:
        ws_det = wb["DETALLE"]
        # Mapear columnas de servicios
        header_srvs = {}
        for col in range(4, ws_det.max_column + 1):
            val = ws_det.cell(row=3, column=col).value
            if val:
                header_srvs[str(val).strip()] = col

        # Mapear filas por métrica
        row_metrics = {}
        for r in range(4, 30):
            lbl = ws_det.cell(row=r, column=3).value
            if lbl:
                row_metrics[str(lbl).strip()] = r

        _, serv_data = construir_matriz_ejecutiva_gtr(df_raw, gtr_cfg)
        for srv, col_idx in header_srvs.items():
            sd = serv_data.get(srv, {})
            for m_lbl, r_idx in row_metrics.items():
                if m_lbl == "LL ENT":
                    ws_det.cell(row=r_idx, column=col_idx).value = int(sd.get("LL ENT", 0))
                elif m_lbl == "LL ATEN":
                    ws_det.cell(row=r_idx, column=col_idx).value = int(sd.get("LL ATEN", 0))
                elif m_lbl == "LL ABAN":
                    ws_det.cell(row=r_idx, column=col_idx).value = int(sd.get("LL ABAN", 0))
                elif m_lbl in ("LL  Aten. NS", "LL Aten. NS"):
                    ws_det.cell(row=r_idx, column=col_idx).value = int(sd.get("LL Aten. NS", 0))
                elif m_lbl == "% ATEN":
                    ws_det.cell(row=r_idx, column=col_idx).value = round(sd.get("% ATEN", 0) / 100.0, 4)
                elif m_lbl == "%ABAN":
                    ws_det.cell(row=r_idx, column=col_idx).value = round(sd.get("% ABAN", 0) / 100.0, 4)
                elif m_lbl == "%NS META":
                    v = sd.get("% NS META")
                    ws_det.cell(row=r_idx, column=col_idx).value = round(v / 100.0, 4) if v else None
                elif m_lbl == "%NS":
                    ws_det.cell(row=r_idx, column=col_idx).value = round(sd.get("% NS", 0) / 100.0, 4)
                elif m_lbl == "META AHT":
                    ws_det.cell(row=r_idx, column=col_idx).value = sd.get("META AHT")
                elif m_lbl == "AHT":
                    ws_det.cell(row=r_idx, column=col_idx).value = round(sd.get("AHT", 0), 1)
                elif m_lbl == "% VAR AHT":
                    aht_r = sd.get("AHT", 0)
                    aht_m = sd.get("META AHT")
                    if aht_m and aht_m > 0 and aht_r > 0:
                        ws_det.cell(row=r_idx, column=col_idx).value = round((aht_r - aht_m) / aht_m, 4)
                elif m_lbl == "ASA":
                    ws_det.cell(row=r_idx, column=col_idx).value = round(sd.get("ASA", 0), 1)

    # 2. Inyectar datos crudos en DATA GENEYS
    if "DATA GENEYS" in wb.sheetnames:
        ws_dg = wb["DATA GENEYS"]
        # Limpiar filas existentes a partir de fila 2
        for r in range(2, min(ws_dg.max_row + 1, 3000)):
            for c in range(1, 13):
                ws_dg.cell(row=r, column=c).value = None

        # Escribir nuevos datos de Genesys
        for idx, row in df_raw.iterrows():
            r_idx = idx + 2
            ws_dg.cell(row=r_idx, column=1).value = datetime.now().strftime("%Y-%m-%d")
            ws_dg.cell(row=r_idx, column=2).value = str(row.get("intervalo", ""))
            ws_dg.cell(row=r_idx, column=3).value = str(row.get("queueId", ""))
            ws_dg.cell(row=r_idx, column=4).value = str(row.get("canal", "VOZ"))
            ws_dg.cell(row=r_idx, column=5).value = int(row.get("nOffered", 0))
            ws_dg.cell(row=r_idx, column=6).value = int(row.get("tAnswered_count", 0))
            ws_dg.cell(row=r_idx, column=7).value = int(row.get("tAbandon_count", 0))
            ws_dg.cell(row=r_idx, column=8).value = int(row.get("sl_numerator", 0))
            ws_dg.cell(row=r_idx, column=9).value = round(row.get("tAnswered_sum", 0) / 1000.0, 2)
            ws_dg.cell(row=r_idx, column=11).value = round(row.get("tHandle_sum", 0) / 1000.0, 2)

    output = BytesIO()
    wb.save(output)
    wb.close()
    return output.getvalue()


def generar_excel_aht_genesys_fiel(df_asesores_raw: pd.DataFrame, agentes_map: dict, gtr_cfg: dict) -> bytes:
    """
    Recrea fielmente el libro AHT GENESYS.xlsm inyectando datos directamente en
    templates/AHT_GENESYS_TEMPLATE.xlsm preservando imágenes, macros, tablas dinámicas y formatos.
    """
    tpl_path = os.path.join(BASE_DIR, "../templates/AHT_GENESYS_TEMPLATE.xlsm")
    if os.path.exists(tpl_path):
        wb = openpyxl.load_workbook(tpl_path, keep_vba=True)
    else:
        wb = openpyxl.Workbook()

    if "DATA" in wb.sheetnames:
        ws_data = wb["DATA"]
        # Limpiar filas existentes de datos crudos (columnas A a O)
        for r in range(2, min(ws_data.max_row + 1, 2000)):
            for c in range(1, 16):
                ws_data.cell(row=r, column=c).value = None

        now_date_str = datetime.now().strftime("%Y-%m-%d 00:00:00")
        next_date_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d 00:00:00")

        # Inyectar datos reales de cada asesor en columnas A-O
        for idx, row in df_asesores_raw.reset_index(drop=True).iterrows():
            r_idx = idx + 2
            aid = row["agente_id"]
            info = agentes_map.get(aid, {})
            nombre = info.get("agente", aid)
            interacc = int(row.get("interacciones", 0))
            aht_s = float(row.get("aht_seg", 0.0))
            talk_s = float(row.get("t_talk_seg", 0.0))
            held_s = float(row.get("t_held_seg", 0.0))
            acw_s = float(row.get("t_acw_seg", 0.0))
            transf = int(row.get("transferidas", 0))

            ws_data.cell(row=r_idx, column=1).value = now_date_str
            ws_data.cell(row=r_idx, column=2).value = next_date_str
            ws_data.cell(row=r_idx, column=3).value = False
            ws_data.cell(row=r_idx, column=4).value = "Dirección: Entrante; Dirección inicial:Entrante"
            ws_data.cell(row=r_idx, column=5).value = "voz"
            ws_data.cell(row=r_idx, column=6).value = aid
            ws_data.cell(row=r_idx, column=7).value = nombre
            ws_data.cell(row=r_idx, column=8).value = interacc
            ws_data.cell(row=r_idx, column=9).value = interacc
            ws_data.cell(row=r_idx, column=10).value = f" {formatear_segundos_mm_ss(aht_s)}.000"
            ws_data.cell(row=r_idx, column=11).value = f" {formatear_segundos_mm_ss(talk_s)}.000"
            ws_data.cell(row=r_idx, column=12).value = f" {formatear_segundos_mm_ss(held_s)}.000" if held_s > 0 else None
            ws_data.cell(row=r_idx, column=13).value = f" {formatear_segundos_mm_ss(acw_s)}.000" if acw_s > 0 else None
            ws_data.cell(row=r_idx, column=14).value = None
            ws_data.cell(row=r_idx, column=15).value = transf if transf > 0 else None

    output = BytesIO()
    wb.save(output)
    wb.close()
    return output.getvalue()



# ── RENDER PRINCIPAL DEL COMPONENTE GTR ───────────────────────────────────────

def render_tab_gtr(agentes_map: dict):
    """Renderiza la pestaña principal de Monitor GTR con alternador de vista."""
    token = obtener_token_genesys()
    if not token:
        st.warning("⚠️ No se encontró token activo de Genesys Cloud. Conéctalo en Neon Postgres o revisa las credenciales.")
        return

    gtr_cfg = cargar_config_gtr()

    with st.spinner("Consultando métricas en vivo de Genesys Cloud..."):
        df_raw, err, hora_act = obtener_metricas_gtr_api(token)

    if err or df_raw.empty:
        st.error(f"No fue posible cargar las métricas de Genesys: {err}")
        return

    col_h1, col_h2 = st.columns([3, 2])
    with col_h1:
        st.subheader("📈 Monitor GTR — Gestión en Tiempo Real & Niveles de Servicio")
        st.caption(f"Replicación en vivo de los reportes oficiales `HORA A HORA` y `AHT GENESYS` • **Última actualización:** `{hora_act}` (Hora Col)")
    with col_h2:
        btn_c1, btn_c2 = st.columns([1, 1])
        with btn_c1:
            st.metric("Último Corte", hora_act if hora_act else "--:--", delta="En Vivo")
        with btn_c2:
            if st.button("🔄 Actualizar Datos", use_container_width=True):
                st.cache_data.clear()
                st.rerun()

    df_matriz, serv_data = construir_matriz_ejecutiva_gtr(df_raw, gtr_cfg)

    # ── BOTONES DE DESCARGA EXACTA GTR ───────────────────────────────────────
    with st.expander("📦 Exportación Fiel a Archivos Oficiales de GTR (Excel Automático)", expanded=False):
        st.markdown(
            "Estos botones recrean **los mismos libros Excel que el equipo de GTR genera cada hora**, listos para archivar o enviar:"
        )
        col_exp1, col_exp2 = st.columns(2)
        with col_exp1:
            bytes_hh = generar_excel_hora_hora_fiel(df_raw, df_matriz, gtr_cfg)
            st.download_button(
                label="📥 Descargar (CONFIDENCIAL)HORA_HORA.xlsx",
                data=bytes_hh,
                file_name=f"(CONFIDENCIAL)HORA_HORA_{datetime.now().strftime('%d%m%Y_%H%M')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
        with col_exp2:
            # Traer asesores si está disponible
            df_as_raw, _ = obtener_aht_asesores_api(token)
            if not df_as_raw.empty:
                bytes_aht = generar_excel_aht_genesys_fiel(df_as_raw, agentes_map, gtr_cfg)
                st.download_button(
                    label="📥 Descargar AHT_GENESYS.xlsx",
                    data=bytes_aht,
                    file_name=f"AHT_GENESYS_{datetime.now().strftime('%d%m%Y_%H%M')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    use_container_width=True
                )

    st.markdown("---")

    # ── SELECTOR DE MODO DE VISTA ────────────────────────────────────────────
    modo_vista = st.radio(
        "Modalidad de Tablero:",
        ["🎯 Vista Gerencial de Operaciones (Semáforos y Diagnóstico)", "📋 Vista Técnica GTR (Matriz Horizontal Hora a Hora)"],
        horizontal=True
    )

    # ══════════════════════════════════════════════════════════════════════════
    # MODO 1: VISTA GERENCIAL DE OPERACIONES
    # ══════════════════════════════════════════════════════════════════════════
    if "Gerencial" in modo_vista:
        # 1. Transformar serv_data a DataFrame Gerencial Vertical
        filas_ger = []
        for s, d in serv_data.items():
            if s in ("TT_LATAM", "TT_EQUIPAJES") or d["LL ENT"] > 0:
                ns_r = d["% NS"]
                ns_m = d["% NS META"]
                aht_r = d["AHT"]
                aht_m = d["META AHT"]
                dif_ns = (ns_r - ns_m) if ns_m is not None else None
                desv_aht = ((aht_r - aht_m) / aht_m * 100.0) if (aht_m and aht_m > 0 and aht_r > 0) else None

                if ns_m is None:
                    estado = "⚪ Sin Meta"
                elif ns_r >= ns_m:
                    estado = "🟢 Cumple SLA"
                elif ns_r >= ns_m - 5.0:
                    estado = "🟡 En Riesgo (-5%)"
                else:
                    estado = "🔴 Crítico (< SLA)"

                filas_ger.append({
                    "Servicio": s,
                    "Estado": estado,
                    "Entrantes": int(d["LL ENT"]),
                    "Atendidas": int(d["LL ATEN"]),
                    "% Aband": d["% ABAN"],
                    "NS Real": ns_r,
                    "NS Meta": ns_m,
                    "Dif NS (pp)": dif_ns,
                    "AHT Real (s)": int(round(aht_r)) if aht_r else 0,
                    "AHT Meta (s)": int(round(aht_m)) if aht_m else None,
                    "Desv AHT (%)": desv_aht,
                    "ASA (s)": d["ASA"]
                })

        df_ger = pd.DataFrame(filas_ger).sort_values(by=["Entrantes"], ascending=False)

        # 2. Alertas por Excepción (Servicios Críticos)
        criticos = df_ger[df_ger["Estado"] == "🔴 Crítico (< SLA)"]
        en_riesgo = df_ger[df_ger["Estado"] == "🟡 En Riesgo (-5%)"]

        if not criticos.empty:
            srv_nombres = ", ".join(criticos["Servicio"].tolist())
            st.error(f"🚨 **ALERTA CRÍTICA SLA:** Hay **{len(criticos)} servicios** con caída severa en Nivel de Servicio: **{srv_nombres}**.")
        elif not en_riesgo.empty:
            st.warning(f"⚠️ **ATENCIÓN:** {len(en_riesgo)} servicios en riesgo de incumplimiento (a menos de 5pp de la meta).")
        else:
            st.success("✅ **OPERACIÓN ESTABLE:** Todos los macro-servicios principales están cumpliendo con el SLA contractual.")

        # 3. KPIs Ejecutivos Macro
        tt_l = serv_data.get("TT_LATAM", {})
        tt_e = serv_data.get("TT_EQUIPAJES", {})

        k1, k2, k3, k4, k5 = st.columns(5)
        with k1:
            st.metric("Entrantes Totales", f"{int(df_raw['nOffered'].sum()):,}")
        with k2:
            st.metric("Atendidas Totales", f"{int(df_raw['tAnswered_count'].sum()):,}")
        with k3:
            st.metric("Abandono Global", f"{df_raw['tAbandon_count'].sum() / df_raw['nOffered'].sum() * 100:.1f}%")
        with k4:
            st.metric("NS Consolidado LATAM", f"{tt_l.get('% NS', 0):.1f}%", delta=f"{tt_l.get('% NS', 0) - tt_l.get('% NS META', 75):+.1f}pp")
        with k5:
            st.metric("AHT Consolidado LATAM", f"{int(tt_l.get('AHT', 0))}s", delta=formatear_segundos_mm_ss(tt_l.get("AHT", 0)))

        # 4. Tabla Ejecutiva Vertical Ordenada por Impacto
        st.markdown("#### 📊 Desempeño Operativo por Servicio (Priorizado por Volumen e Impacto)")
        st.caption("Filas ordenadas por número de llamadas. Permite detectar en 3 segundos desviaciones de SLA y excesos de AHT.")

        st.dataframe(
            df_ger,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Entrantes": st.column_config.NumberColumn("Entrantes", format="%d"),
                "Atendidas": st.column_config.NumberColumn("Atendidas", format="%d"),
                "% Aband": st.column_config.NumberColumn("% Aband.", format="%.1f%%"),
                "NS Real": st.column_config.NumberColumn("NS Real", format="%.1f%%"),
                "NS Meta": st.column_config.NumberColumn("NS Meta", format="%.1f%%"),
                "Dif NS (pp)": st.column_config.NumberColumn("Dif SLA (pp)", format="%+.1f%%"),
                "AHT Real (s)": st.column_config.NumberColumn("AHT Real (s)", format="%.0f s"),
                "AHT Meta (s)": st.column_config.NumberColumn("Meta AHT (s)", format="%.0f s"),
                "Desv AHT (%)": st.column_config.NumberColumn("Desv AHT (%)", format="%+.1f%%"),
                "ASA (s)": st.column_config.NumberColumn("ASA (s)", format="%.1f s"),
            }
        )

        # 5. Causalidad y Desglose por Supervisor
        st.markdown("#### 🔍 Diagnóstico Causa-Raíz por Supervisor")
        st.caption("Identifica qué equipos de supervisión presentan mayor volumen o sobregiro en tiempo de conversación/retención.")

        df_as_raw, _ = obtener_aht_asesores_api(token)
        if not df_as_raw.empty:
            aht_metas = gtr_cfg.get("aht_metas", {})

            def cruzar_sup(row):
                aid = row["agente_id"]
                info = agentes_map.get(aid, {})
                serv = info.get("servicio", "General")
                sup = info.get("jefe_inmediato", "-")
                meta = aht_metas.get(serv, None)
                return pd.Series([serv, sup, meta])

            df_as_ger = df_as_raw.copy()
            df_as_ger[["Servicio", "Supervisor", "Meta AHT"]] = df_as_ger.apply(cruzar_sup, axis=1)
            df_as_ger = df_as_ger[df_as_ger["Supervisor"] != "-"]

            sup_grp = df_as_ger.groupby(["Supervisor", "Servicio"]).agg({
                "interacciones": "sum",
                "aht_seg": "mean",
                "Meta AHT": "mean",
                "t_talk_seg": "mean",
                "t_held_seg": "mean",
                "t_acw_seg": "mean"
            }).reset_index()

            sup_grp["Desvío (%)"] = ((sup_grp["aht_seg"] - sup_grp["Meta AHT"]) / sup_grp["Meta AHT"] * 100.0).round(1)
            sup_grp = sup_grp.sort_values(by="interacciones", ascending=False)

            st.dataframe(
                sup_grp.rename(columns={
                    "interacciones": "Interacciones",
                    "aht_seg": "AHT Promedio (s)",
                    "Meta AHT": "Meta (s)",
                    "t_talk_seg": "Talk (s)",
                    "t_held_seg": "Hold (s)",
                    "t_acw_seg": "ACW (s)"
                }),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Interacciones": st.column_config.NumberColumn("Interacciones", format="%d"),
                    "AHT Promedio (s)": st.column_config.NumberColumn("AHT Promedio (s)", format="%.0f s"),
                    "Meta (s)": st.column_config.NumberColumn("Meta (s)", format="%.0f s"),
                    "Desvío (%)": st.column_config.NumberColumn("Desvío (%)", format="%+.1f%%"),
                    "Talk (s)": st.column_config.NumberColumn("Talk (s)", format="%.0f s"),
                    "Hold (s)": st.column_config.NumberColumn("Hold (s)", format="%.0f s"),
                    "ACW (s)": st.column_config.NumberColumn("ACW (s)", format="%.0f s"),
                }
            )

    # ══════════════════════════════════════════════════════════════════════════
    # MODO 2: VISTA TÉCNICA GTR (HORA A HORA Y MATRIZ HORIZONTAL)
    # ══════════════════════════════════════════════════════════════════════════
    else:
        st.markdown("### 📋 Matriz Ejecutiva Consolidada (Formato Oficial HORA A HORA)")
        st.caption("Estructura exacta del archivo de GTR: Macro-Servicios en columnas e indicadores en filas.")

        opciones_columnas = [c for c in df_matriz.columns if c != "Concepto / Métrica"]
        cols_seleccionadas = st.multiselect(
            "Filtrar columnas de servicios a visualizar:",
            opciones_columnas,
            default=[c for c in opciones_columnas if c in SERVICIOS_ORDEN_OFICIAL[:10] or c in ("TT_EQUIPAJES", "WPP EQUIPAJES AMC")]
        )

        cols_a_mostrar = ["Concepto / Métrica"] + (cols_seleccionadas if cols_seleccionadas else opciones_columnas)
        st.dataframe(
            df_matriz[cols_a_mostrar],
            use_container_width=True,
            hide_index=True
        )

        # Intradía por 30 minutos
        st.markdown("---")
        st.markdown("### ⏱️ Detalle Intradía por Servicio (Intervalos 30 Minutos y Acumulados)")
        servicios_intradia = [s for s in SERVICIOS_ORDEN_OFICIAL if s in serv_data and serv_data[s]["LL ENT"] > 0]
        servicio_activo = st.selectbox("Seleccione el Servicio a Analizar:", servicios_intradia, index=1 if len(servicios_intradia) > 1 else 0)

        if servicio_activo == "TT_LATAM":
            df_srv = df_raw[df_raw["servicio"].isin(SERVICIOS_TT_LATAM)].copy()
        elif servicio_activo == "TT_EQUIPAJES":
            df_srv = df_raw[df_raw["servicio"].isin(SERVICIOS_TT_EQUIPAJES)].copy()
        else:
            df_srv = df_raw[df_raw["servicio"] == servicio_activo].copy()

        intra_srv = df_srv.groupby("intervalo").agg({
            "nOffered": "sum",
            "tAnswered_count": "sum",
            "tAbandon_count": "sum",
            "sl_numerator": "sum",
            "sl_denominator": "sum",
            "tHandle_sum": "sum",
            "tHandle_count": "sum",
            "tAnswered_sum": "sum"
        }).reset_index().sort_values("intervalo")

        intra_srv["cum_sl_num"] = intra_srv["sl_numerator"].cumsum()
        intra_srv["cum_sl_den"] = intra_srv["sl_denominator"].cumsum()
        intra_srv["cum_h_sum"] = intra_srv["tHandle_sum"].cumsum()
        intra_srv["cum_h_cnt"] = intra_srv["tHandle_count"].cumsum()
        intra_srv["cum_ans_sum"] = intra_srv["tAnswered_sum"].cumsum()
        intra_srv["cum_answered"] = intra_srv["tAnswered_count"].cumsum()

        intra_srv["% Atenc"] = (intra_srv["tAnswered_count"] / intra_srv["nOffered"] * 100.0).fillna(0).round(1)
        intra_srv["% Aband"] = (intra_srv["tAbandon_count"] / intra_srv["nOffered"] * 100.0).fillna(0).round(1)
        intra_srv["% NS"] = (intra_srv["sl_numerator"] / intra_srv["sl_denominator"] * 100.0).fillna(0).round(1)
        intra_srv["AHT (s)"] = (intra_srv["tHandle_sum"] / intra_srv["tHandle_count"] / 1000.0).fillna(0).round(0)
        intra_srv["ASA (s)"] = (intra_srv["tAnswered_sum"] / intra_srv["tAnswered_count"] / 1000.0).fillna(0).round(1)

        intra_srv["% NS Acum"] = (intra_srv["cum_sl_num"] / intra_srv["cum_sl_den"] * 100.0).fillna(0).round(1)
        intra_srv["AHT Acum (s)"] = (intra_srv["cum_h_sum"] / intra_srv["cum_h_cnt"] / 1000.0).fillna(0).round(0)
        intra_srv["ASA Acum (s)"] = (intra_srv["cum_ans_sum"] / intra_srv["cum_answered"] / 1000.0).fillna(0).round(1)

        cols_tabla_intra = [
            "intervalo", "nOffered", "tAnswered_count", "tAbandon_count", "sl_numerator",
            "% Atenc", "% Aband", "% NS", "% NS Acum", "AHT (s)", "AHT Acum (s)", "ASA (s)"
        ]
        st.dataframe(
            intra_srv[cols_tabla_intra].rename(columns={
                "intervalo": "Hora (Intv)",
                "nOffered": "Llam Ent",
                "tAnswered_count": "Llam Aten",
                "tAbandon_count": "Llam Aban",
                "sl_numerator": "Atend. NS",
                "% Atenc": "% Atenc.",
                "% Aband": "% Aband.",
                "% NS": "% NS",
                "% NS Acum": "% NS Acum.",
                "AHT (s)": "AHT (s)",
                "AHT Acum (s)": "AHT Acum (s)",
                "ASA (s)": "ASA (s)"
            }),
            use_container_width=True,
            hide_index=True
        )
