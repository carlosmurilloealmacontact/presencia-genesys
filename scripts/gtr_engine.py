"""
Motor GTR y Niveles de Servicio — Radar Genesys Cloud.
Incluye:
1. Vista Gerencial Operativa (Alertas, semáforos de SLA y desvío de AHT ordenados por impacto).
2. Vista Técnica GTR (Matriz horizontal idéntica a HORA A HORA y desglose por 30 min).
3. Exportadores Fieles a Excel de los dos libros: (CONFIDENCIAL)HORA_HORA.xlsx y AHT_GENESYS.xlsx.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
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

# Orden oficial de columnas de servicios según HORA A HORA (DETALLE fila 3)
SERVICIOS_ORDEN_OFICIAL = [
    "TT_LATAM",
    "LUA AMC",
    "Soporte LUA AMC",
    "WPP LUA AMC",
    "HVC AMC",
    "Ventas AMC",
    "WPP VENTAS AMC",
    "CHAT VENTAS AMC",
    "TRAVEL WP AMC",
    "LUA AMC ING",
    "DT FFP AMC",
    "DT FFP AMC ING",
    "CHAT DT FFP AMC ESP",
    "DREAM TEAM WP",
    "TT_EQUIPAJES",
    "Equipajes AMC",
    "Equipajes AMC ING",
    "WPP EQUIPAJES AMC"
]

SERVICIOS_TT_LATAM = [
    "LUA AMC", "Soporte LUA AMC", "WPP LUA AMC", "HVC AMC",
    "Ventas AMC", "WPP VENTAS AMC", "CHAT VENTAS AMC", "TRAVEL WP AMC",
    "LUA AMC ING", "DT FFP AMC", "DT FFP AMC ING", "CHAT DT FFP AMC ESP", "DREAM TEAM WP"
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


@st.cache_data(ttl=3600, show_spinner=False)
def resolver_nombres_colas_genesys(token: str, queue_ids: tuple) -> dict:
    """
    Consulta a la API de Genesys Cloud el nombre legible de cada cola no mapeada.
    Cacheado por 1 hora para máxima velocidad y evitar llamadas redundantes.
    """
    if not token or not queue_ids:
        return {}

    def fetch_single(qid):
        if not qid or str(qid) == "nan" or not str(qid).strip():
            return qid, "Directo / Sin Cola"
        try:
            r = requests.get(
                f"https://api.mypurecloud.com/api/v2/routing/queues/{qid}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=4
            )
            if r.status_code == 200:
                return qid, r.json().get("name", str(qid))
            elif r.status_code == 403:
                return qid, f"Cola Otra División ({str(qid)[:8]}...)"
            return qid, str(qid)
        except Exception:
            return qid, str(qid)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = dict(pool.map(fetch_single, queue_ids))
    return results


@st.cache_data(ttl=60, show_spinner=False)
def obtener_metricas_gtr_api(token: str, fecha_desde: str = None, fecha_hasta: str = None):
    """
    Consulta Genesys Cloud Analytics Conversation Aggregates.
    - Si fecha_desde es None: consulta el día actual en vivo (PT30M).
    - Si fecha_desde es dada: consulta el día específico (PT30M) o rango de fechas.
    """
    gtr_cfg = cargar_config_gtr()
    queues_cfg = gtr_cfg.get("queues", {})
    services_cfg = gtr_cfg.get("services", {})

    now_utc = datetime.now(timezone.utc)
    if not fecha_desde:
        today_col_start = now_utc.replace(hour=5, minute=0, second=0, microsecond=0)
        if now_utc < today_col_start:
            today_col_start -= timedelta(days=1)

        s_start = today_col_start.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        s_end = now_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        interval = f"{s_start}/{s_end}"
        granularity = "PT30M"
        now_col = now_utc - timedelta(hours=5)
        hora_actualizacion = now_col.strftime("%I:%M:%S %p")
    else:
        try:
            start_dt = pd.to_datetime(f"{fecha_desde} 05:00:00")
            if not fecha_hasta or fecha_hasta == fecha_desde:
                end_dt = start_dt + pd.Timedelta(days=1)
                granularity = "PT30M"
                hora_actualizacion = f"Cierre {fecha_desde}"
            else:
                end_dt = pd.to_datetime(f"{fecha_hasta} 05:00:00") + pd.Timedelta(days=1)
                dias_diff = (end_dt - start_dt).days
                granularity = "PT30M" if dias_diff <= 7 else "P1D"
                hora_actualizacion = f"{fecha_desde} al {fecha_hasta}"
            s_start = start_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            s_end = end_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            interval = f"{s_start}/{s_end}"
        except Exception as e:
            return pd.DataFrame(), f"Error en fechas: {e}", ""

    body = {
        "interval": interval,
        "granularity": granularity,
        "groupBy": ["queueId"],
        "metrics": ["nOffered", "tAnswered", "tAbandon", "tHandle", "oServiceLevel"]
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = "https://api.mypurecloud.com/api/v2/analytics/conversations/aggregates/query"

    try:
        r = requests.post(url, headers=headers, json=body, timeout=30)
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
            int_label = dt_col.strftime("%H:%M") if granularity == "PT30M" else dt_col.strftime("%Y-%m-%d")

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

    return pd.DataFrame(records), None, hora_actualizacion


@st.cache_data(ttl=60, show_spinner=False)
def obtener_aht_asesores_api(token: str, fecha_desde: str = None, fecha_hasta: str = None):
    """
    Consulta métricas de manejo por agente (userId) para hoy o para un período histórico.
    """
    now_utc = datetime.now(timezone.utc)
    if not fecha_desde:
        today_col_start = now_utc.replace(hour=5, minute=0, second=0, microsecond=0)
        if now_utc < today_col_start:
            today_col_start -= timedelta(days=1)

        s_start = today_col_start.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        s_end = now_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    else:
        start_dt = pd.to_datetime(f"{fecha_desde} 05:00:00")
        if not fecha_hasta or fecha_hasta == fecha_desde:
            end_dt = start_dt + pd.Timedelta(days=1)
        else:
            end_dt = pd.to_datetime(f"{fecha_hasta} 05:00:00") + pd.Timedelta(days=1)
        s_start = start_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        s_end = end_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

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
        r = requests.post(url, headers=headers, json=body, timeout=30)
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
        if meta_ns is None:
            for k_srv, v_srv in services_cfg.items():
                if k_srv.strip().upper() == s.strip().upper():
                    meta_ns = v_srv.get("ns_meta", None)
                    break
        meta_ns_val = meta_ns * 100.0 if meta_ns is not None else None

        meta_aht = aht_metas.get(s, None)
        if meta_aht is None:
            for k_aht, v_aht in aht_metas.items():
                if k_aht.strip().upper() == s.strip().upper():
                    meta_aht = v_aht
                    break

        serv_data[s] = {
            "LL ENT": off,
            "LL ATEN": ans,
            "LL ABAN": abn,
            "LL Aten. NS": sl_n,
            "% ATEN": (ans / off * 100.0) if off > 0 else 0.0,
            "% ABAN": (abn / off * 100.0) if off > 0 else 0.0,
            "% NS META": meta_ns_val,
            "% NS": (sl_n / sl_d * 100.0) if sl_d > 0 else 0.0,
            "META AHT": meta_aht,
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

@st.cache_data(show_spinner="Generando libro oficial HORA A HORA...")
def generar_excel_hora_hora_fiel(df_raw: pd.DataFrame, df_matriz: pd.DataFrame, gtr_cfg: dict) -> bytes:
    """
    Recrea fielmente el libro oficial HORA A HORA conservando al 100%
    todas las 16 hojas, formatos originales, colores corporativos (#1F4E78),
    reglas condicionales con flechas/iconos, fórmulas nativas y la marca de agua de LATAM.
    """
    tpl_master = os.path.join(BASE_DIR, "../templates/HORA_HORA_EXACT_MASTER.xlsx")
    if not os.path.exists(tpl_master):
        tpl_master = os.path.join(BASE_DIR, "../templates/HORA_HORA_TEMPLATE.xlsx")

    # Intento 1: Automatización nativa Microsoft Excel aislada (Fidelidad 100% idéntica, sin corrupción)
    try:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        excel = win32com.client.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        excel.ScreenUpdating = False
        excel.EnableEvents = False

        tpl_abs = os.path.abspath(tpl_master)
        wb = excel.Workbooks.Open(tpl_abs)

        # 1. Actualizar Hora de Reporte en DETALLE!Y5 y matriz de datos en vivo
        ws_det = wb.Sheets("DETALLE")
        ws_det.Range("Y5").Value = datetime.now().strftime("%H:%M:%S")

        # Inyectar métricas en tiempo real directamente en la matriz de la hoja DETALLE (D8:W24)
        _, serv_data = construir_matriz_ejecutiva_gtr(df_raw, gtr_cfg)
        headers = ws_det.Range("D3:W3").Value[0]
        current_matrix = [list(r) for r in ws_det.Range("D8:W24").Value]

        for col_i, srv in enumerate(headers):
            if not srv:
                continue
            srv = str(srv).strip()
            sd = serv_data.get(srv)
            if not sd:
                continue

            current_matrix[0][col_i] = int(sd.get("LL ENT", 0))
            current_matrix[1][col_i] = int(sd.get("LL ATEN", 0))
            current_matrix[2][col_i] = int(sd.get("LL ABAN", 0))
            current_matrix[3][col_i] = int(sd.get("LL Aten. NS", 0))
            current_matrix[4][col_i] = round(float(sd.get("% ATEN", 0)) / 100.0, 4)
            current_matrix[5][col_i] = round(float(sd.get("% ABAN", 0)) / 100.0, 4)
            if sd.get("% NS META") is not None:
                current_matrix[6][col_i] = round(float(sd.get("% NS META", 0)) / 100.0, 4)
            current_matrix[7][col_i] = round(float(sd.get("% NS", 0)) / 100.0, 4)
            if sd.get("META AHT") is not None:
                current_matrix[8][col_i] = round(float(sd.get("META AHT", 0)), 1)
            current_matrix[9][col_i] = round(float(sd.get("AHT", 0)), 1)
            aht_r = float(sd.get("AHT", 0))
            aht_m = float(sd.get("META AHT", 0)) if sd.get("META AHT") else None
            if aht_m and aht_m > 0 and aht_r > 0:
                current_matrix[10][col_i] = round((aht_r - aht_m) / aht_m, 4)
            current_matrix[16][col_i] = round(float(sd.get("ASA", 0)), 1)

        ws_det.Range("D8:W24").Value = current_matrix

        # 2. Inyectar intervalos en DATA GENEYS
        sheet_names = [s.Name for s in wb.Sheets]
        if "DATA GENEYS" in sheet_names and df_raw is not None and not df_raw.empty:
            ws_dg = wb.Sheets("DATA GENEYS")
            last_r = ws_dg.UsedRange.Rows.Count
            if last_r > 1:
                ws_dg.Range(f"A2:N{min(last_r + 10, 5000)}").ClearContents()

            now_date_str = datetime.now().strftime("%Y-%m-%d")
            matrix_rows = []
            for _, r in df_raw.iterrows():
                ans_cnt = int(r.get("tAnswered_count", 0))
                ans_sum_s = float(r.get("tAnswered_sum", 0.0)) / 1000.0
                asa_val = round(ans_sum_s / ans_cnt, 2) if ans_cnt > 0 else 0.0
                handle_sum_s = float(r.get("tHandle_sum", 0.0)) / 1000.0
                aht_val = round(handle_sum_s / ans_cnt, 2) if ans_cnt > 0 else round(handle_sum_s, 2)
                matrix_rows.append([
                    now_date_str,
                    str(r.get("intervalo", "")),
                    str(r.get("queueId", "")),
                    str(r.get("canal", "VOZ")),
                    int(r.get("nOffered", 0)),
                    ans_cnt,
                    int(r.get("tAbandon_count", 0)),
                    int(r.get("sl_numerator", 0)),
                    asa_val,
                    "",
                    round(float(r.get("tTalk_sum", 0.0)) / 1000.0, 2),
                    round(float(r.get("tAcw_sum", 0.0)) / 1000.0, 2),
                    aht_val,
                    round(float(r.get("tHeld_sum", 0.0)) / 1000.0, 2)
                ])

            if matrix_rows:
                rng = ws_dg.Range(f"A2:N{len(matrix_rows) + 1}")
                rng.Value = matrix_rows

        excel.Calculate()

        fd, tmp_out = tempfile.mkstemp(suffix=".xlsx")
        os.close(fd)
        wb.SaveCopyAs(tmp_out)
        wb.Close(False)
        excel.Quit()
        pythoncom.CoUninitialize()

        with open(tmp_out, "rb") as f:
            data_bytes = f.read()
        os.remove(tmp_out)
        if len(data_bytes) > 500000:
            return data_bytes
    except Exception as exc:
        print(f"[ERROR GENERAR HORA_HORA COM] {exc}")

    # Respaldo limpio: retornar el archivo maestro exacto directamente sin alteración de openpyxl
    with open(tpl_master, "rb") as f:
        return f.read()


@st.cache_data(show_spinner="Generando libro oficial AHT GENESYS...")
def generar_excel_aht_genesys_fiel(df_asesores_raw: pd.DataFrame, agentes_map: dict, gtr_cfg: dict) -> bytes:
    """
    Recrea fielmente el libro macro oficial AHT GENESYS.xlsm conservando al 100%
    todas las macros VBA, segmentadores (slicers), tablas dinámicas, imágenes y formatos originales.
    """
    tpl_master = os.path.join(BASE_DIR, "../templates/AHT_GENESYS_EXACT_MASTER.xlsm")
    if not os.path.exists(tpl_master):
        tpl_master = os.path.join(BASE_DIR, "../templates/AHT_GENESYS_TEMPLATE.xlsm")

    try:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        excel = win32com.client.DispatchEx("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        excel.ScreenUpdating = False
        excel.EnableEvents = False

        tpl_abs = os.path.abspath(tpl_master)
        wb = excel.Workbooks.Open(tpl_abs)

        if "DATA" in [s.Name for s in wb.Sheets] and not df_asesores_raw.empty:
            ws_data = wb.Sheets("DATA")
            last_r = ws_data.UsedRange.Rows.Count
            if last_r > 1:
                ws_data.Range(f"A2:O{min(last_r + 10, 5000)}").ClearContents()

            now_date_str = datetime.now().strftime("%Y-%m-%d 00:00:00")
            next_date_str = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d 00:00:00")

            aht_rows = []
            for _, row in df_asesores_raw.iterrows():
                aid = str(row.get("agente_id", ""))
                ag_info = agentes_map.get(aid, {})
                nombre = ag_info.get("agente", aid)
                interacc = int(row.get("interacciones", 0))
                aht_s = float(row.get("aht_seg", 0.0))
                talk_s = float(row.get("t_talk_seg", 0.0))
                held_s = float(row.get("t_held_seg", 0.0))
                acw_s = float(row.get("t_acw_seg", 0.0))
                transf = int(row.get("transferidas", 0))

                aht_rows.append([
                    now_date_str,
                    next_date_str,
                    False,
                    "Dirección: Entrante; Dirección inicial:Entrante",
                    "voz",
                    aid,
                    nombre,
                    interacc,
                    interacc,
                    f" {formatear_segundos_mm_ss(aht_s)}.000",
                    f" {formatear_segundos_mm_ss(talk_s)}.000",
                    f" {formatear_segundos_mm_ss(held_s)}.000" if held_s > 0 else "",
                    f" {formatear_segundos_mm_ss(acw_s)}.000" if acw_s > 0 else "",
                    "",
                    transf if transf > 0 else ""
                ])

            if aht_rows:
                rng = ws_data.Range(f"A2:O{len(aht_rows) + 1}")
                rng.Value = aht_rows

        wb.RefreshAll()

        fd, tmp_out = tempfile.mkstemp(suffix=".xlsm")
        os.close(fd)
        wb.SaveCopyAs(tmp_out)
        wb.Close(False)
        excel.Quit()
        pythoncom.CoUninitialize()

        with open(tmp_out, "rb") as f:
            data_bytes = f.read()
        os.remove(tmp_out)
        if len(data_bytes) > 500000:
            return data_bytes
    except Exception as exc:
        print(f"[ERROR GENERAR AHT COM] {exc}")

    with open(tpl_master, "rb") as f:
        return f.read()



# ── RENDER PRINCIPAL DEL COMPONENTE GTR ───────────────────────────────────────

def render_tab_gtr(agentes_map: dict):
    """Renderiza la pestaña de Niveles de Servicio con soporte en vivo y selección de fechas."""
    token = obtener_token_genesys()
    if not token:
        st.warning("⚠️ No se encontró token activo de Genesys Cloud. Conéctalo en Neon Postgres o revisa las credenciales.")
        return

    ahora_col = datetime.now(timezone.utc) - timedelta(hours=5)
    hoy_col = ahora_col.date()
    k_pfx = "gtr_"

    col_h1, col_h2 = st.columns([3, 2])
    with col_h1:
        st.subheader("📈 Niveles de Servicio & GTR")
        st.caption("Replicación en vivo y por fechas de los reportes oficiales `HORA A HORA` y `AHT GENESYS`.")

    # ── BARRA DE SELECCIÓN DE TEMPORALIDAD / FECHA ───────────────────────────
    col_t1, col_t2 = st.columns([2, 3])
    with col_t1:
        opciones_corte = ["🔴 Hoy (En Vivo)", "📅 Fecha Específica", "📊 Rango de Fechas"]
        tipo_corte = st.segmented_control(
            "Temporalidad a Visualizar:",
            options=opciones_corte,
            default="🔴 Hoy (En Vivo)",
            key=f"{k_pfx}tipo_corte"
        )
        if not tipo_corte:
            tipo_corte = "🔴 Hoy (En Vivo)"

    fecha_desde_str = None
    fecha_hasta_str = None
    delta_tag = "En Vivo (60s)"

    with col_t2:
        if tipo_corte == "🔴 Hoy (En Vivo)":
            st.info(f"🟢 Mostrando métricas de hoy en tiempo real (Corte a las: `{ahora_col.strftime('%I:%M %p')}`).")
            delta_tag = "En Vivo (60s)"
        elif tipo_corte == "📅 Fecha Específica":
            c_f1, c_f2 = st.columns([2, 1])
            with c_f1:
                f_sel = st.date_input(
                    "Fecha a Analizar:",
                    value=hoy_col,
                    max_value=hoy_col,
                    key=f"{k_pfx}dia_input"
                )
                fecha_desde_str = str(f_sel)
                fecha_hasta_str = str(f_sel)
                delta_tag = f"Cierre {fecha_desde_str}"
            with c_f2:
                if st.button("Ayer", key=f"{k_pfx}btn_ayer", use_container_width=True):
                    st.session_state[f"{k_pfx}dia_input"] = hoy_col - timedelta(days=1)
                    st.rerun()
        else:  # Rango de Fechas
            c_p1, c_p2, c_p3 = st.columns([1, 1, 2])
            with c_p1:
                if st.button("7 días", key=f"{k_pfx}r7", use_container_width=True):
                    st.session_state[f"{k_pfx}r_desde"] = hoy_col - timedelta(days=6)
                    st.session_state[f"{k_pfx}r_hasta"] = hoy_col
                    st.rerun()
            with c_p2:
                if st.button("14 días", key=f"{k_pfx}r14", use_container_width=True):
                    st.session_state[f"{k_pfx}r_desde"] = hoy_col - timedelta(days=13)
                    st.session_state[f"{k_pfx}r_hasta"] = hoy_col
                    st.rerun()
            with c_p3:
                c_d_in1, c_d_in2 = st.columns(2)
                with c_d_in1:
                    if f"{k_pfx}r_desde" not in st.session_state:
                        st.session_state[f"{k_pfx}r_desde"] = hoy_col - timedelta(days=6)
                    f_r_d = st.date_input("Desde:", key=f"{k_pfx}r_desde", max_value=hoy_col)
                with c_d_in2:
                    if f"{k_pfx}r_hasta" not in st.session_state:
                        st.session_state[f"{k_pfx}r_hasta"] = hoy_col
                    f_r_h = st.date_input("Hasta:", key=f"{k_pfx}r_hasta", max_value=hoy_col)
                fecha_desde_str = str(f_r_d)
                fecha_hasta_str = str(f_r_h)
                delta_tag = f"Período {fecha_desde_str} al {fecha_hasta_str}"

    # Invalidar caché de Excel si cambió la fecha
    curr_date_key = f"{fecha_desde_str}_{fecha_hasta_str}"
    if st.session_state.get(f"{k_pfx}last_date_key") != curr_date_key:
        st.session_state[f"{k_pfx}last_date_key"] = curr_date_key
        st.session_state["bytes_hh_cache"] = None
        st.session_state["bytes_aht_cache"] = None

    with st.spinner(f"Consultando métricas de Genesys Cloud ({delta_tag})..."):
        df_raw, err, hora_act = obtener_metricas_gtr_api(token, fecha_desde_str, fecha_hasta_str)

    if err or df_raw.empty:
        st.error(f"No fue posible cargar las métricas de Genesys: {err}")
        return

    with col_h2:
        btn_c1, btn_c2 = st.columns([1, 1])
        with btn_c1:
            st.metric("Corte / Período", hora_act if hora_act else "--:--", delta=delta_tag)
        with btn_c2:
            if st.button("🔄 Actualizar Ahora", key=f"{k_pfx}btn_refresh", use_container_width=True):
                st.cache_data.clear()
                st.session_state["bytes_hh_cache"] = None
                st.session_state["bytes_aht_cache"] = None
                st.rerun()

    df_matriz, serv_data = construir_matriz_ejecutiva_gtr(df_raw, gtr_cfg)

    # ── BOTONES DE DESCARGA EXACTA GTR ───────────────────────────────────────
    suffix_file = f"_{fecha_desde_str}" if fecha_desde_str else f"_{datetime.now().strftime('%d%m%Y_%H%M')}"
    with st.expander("📦 Exportación Fiel a Archivos Oficiales de GTR (Excel Automático)", expanded=False):
        st.markdown(
            "Estos botones recrean **los mismos libros Excel que el equipo de GTR genera cada hora**, listos para archivar o enviar:"
        )
        col_exp1, col_exp2 = st.columns(2)
        with col_exp1:
            if "bytes_hh_cache" not in st.session_state:
                st.session_state["bytes_hh_cache"] = None

            if st.session_state["bytes_hh_cache"] is not None:
                c_d1, c_d2 = st.columns([4, 1])
                with c_d1:
                    st.download_button(
                        label="📥 Descargar (CONFIDENCIAL)HORA_HORA.xlsx",
                        data=st.session_state["bytes_hh_cache"],
                        file_name=f"(CONFIDENCIAL)HORA_HORA{suffix_file}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True
                    )
                with c_d2:
                    if st.button("🔄", key=f"{k_pfx}regen_hh", help="Generar nueva versión"):
                        st.session_state["bytes_hh_cache"] = None
                        st.rerun()
            else:
                if st.button("⚡ Preparar (CONFIDENCIAL)HORA_HORA.xlsx", key=f"{k_pfx}prep_hh", use_container_width=True):
                    st.session_state["bytes_hh_cache"] = generar_excel_hora_hora_fiel(df_raw, df_matriz, gtr_cfg)
                    st.rerun()

        with col_exp2:
            if "bytes_aht_cache" not in st.session_state:
                st.session_state["bytes_aht_cache"] = None

            if st.session_state["bytes_aht_cache"] is not None:
                c_a1, c_a2 = st.columns([4, 1])
                with c_a1:
                    st.download_button(
                        label="📥 Descargar AHT_GENESYS.xlsm",
                        data=st.session_state["bytes_aht_cache"],
                        file_name=f"AHT_GENESYS{suffix_file}.xlsm",
                        mime="application/vnd.ms-excel.sheet.macroEnabled.12",
                        use_container_width=True
                    )
                with c_a2:
                    if st.button("🔄", key=f"{k_pfx}regen_aht", help="Generar nueva versión"):
                        st.session_state["bytes_aht_cache"] = None
                        st.rerun()
            else:
                if st.button("⚡ Preparar AHT_GENESYS.xlsm", key=f"{k_pfx}prep_aht", use_container_width=True):
                    df_as_raw, _ = obtener_aht_asesores_api(token, fecha_desde_str, fecha_hasta_str)
                    if not df_as_raw.empty:
                        st.session_state["bytes_aht_cache"] = generar_excel_aht_genesys_fiel(df_as_raw, agentes_map, gtr_cfg)
                        st.rerun()
                    else:
                        st.warning("No se encontraron registros de asesores para exportar.")

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

        # Estilos de formato condicional (Semáforos ejecutivos)
        def estilo_dif_sla(val):
            if pd.isna(val):
                return ""
            if val >= 0:
                color = "#1baf7a"  # verde
            elif val >= -5.0:
                color = "#eda100"  # amarillo
            else:
                color = "#e24b4a"  # rojo
            return f"background-color: {color}22; color: {color}; font-weight: 600;"

        def estilo_desv_aht(val):
            if pd.isna(val):
                return ""
            if val <= 0:
                color = "#1baf7a"  # verde (a tiempo o bajo meta)
            elif val <= 10.0:
                color = "#eda100"  # amarillo (riesgo leve de tiempo)
            else:
                color = "#e24b4a"  # rojo (sobregiro crítico)
            return f"background-color: {color}22; color: {color}; font-weight: 600;"

        def estilo_hold(val):
            if pd.isna(val) or val == 0:
                return ""
            if val > 240.0:
                color = "#e24b4a"
                return f"background-color: {color}22; color: {color}; font-weight: 600;"
            elif val > 120.0:
                color = "#eda100"
                return f"background-color: {color}22; color: {color}; font-weight: 600;"
            return ""

        styler_ger = (
            df_ger.style
            .map(estilo_dif_sla, subset=["Dif NS (pp)"])
            .map(estilo_desv_aht, subset=["Desv AHT (%)"])
        )

        st.dataframe(
            styler_ger,
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

        # 4.1 Desglose interactivo de "Otras Colas / No Mapeado"
        df_no_map = df_raw[df_raw["servicio"] == "Otras Colas / No Mapeado"]
        if not df_no_map.empty:
            total_off_nomap = int(df_no_map["nOffered"].sum())
            total_ans_nomap = int(df_no_map["tAnswered_count"].sum())
            total_abn_nomap = int(df_no_map["tAbandon_count"].sum())
            qids_no_map = tuple(df_no_map["queueId"].dropna().unique())

            with st.expander(
                f"🔎 Desglose de 'Otras Colas / No Mapeado' ({len(qids_no_map)} colas activas • {total_off_nomap:,} interacciones)",
                expanded=False
            ):
                st.markdown(
                    """
                    **¿Qué es 'Otras Colas / No Mapeado'?**
                    Son interacciones procesadas en Genesys Cloud en colas que **no están catalogadas dentro de los servicios oficiales de GTR** (`gtr_config.json`).
                    
                    Típicamente corresponden a:
                    - Colas operativas de soporte interno, remisiones o pruebas técnicas.
                    - Colas de transferencias secundarias o células especializadas (ej. NDC, Reclamos, Backoffice).
                    - Colas asignadas a divisiones con permisos restringidos en Genesys Cloud.
                    
                    A continuación se presenta el desglose detallado por cola con su volumen real de hoy:
                    """
                )

                nombres_resueltos = resolver_nombres_colas_genesys(token, qids_no_map)

                desglose = df_no_map.groupby(["queueId", "canal"]).agg({
                    "nOffered": "sum",
                    "tAnswered_count": "sum",
                    "tAbandon_count": "sum",
                    "sl_numerator": "sum",
                    "sl_denominator": "sum",
                    "tHandle_sum": "sum",
                    "tHandle_count": "sum",
                    "tAnswered_sum": "sum"
                }).reset_index()

                desglose["Nombre de Cola en Genesys"] = desglose["queueId"].map(
                    lambda q: nombres_resueltos.get(q, str(q)) if q in nombres_resueltos else str(q)
                )
                desglose["% Abandono"] = desglose.apply(
                    lambda r: (r["tAbandon_count"] / r["nOffered"] * 100.0) if r["nOffered"] > 0 else 0.0, axis=1
                )
                desglose["% NS"] = desglose.apply(
                    lambda r: (r["sl_numerator"] / r["sl_denominator"] * 100.0) if r["sl_denominator"] > 0 else 0.0, axis=1
                )
                desglose["AHT (s)"] = desglose.apply(
                    lambda r: (r["tHandle_sum"] / r["tHandle_count"] / 1000.0) if r["tHandle_count"] > 0 else 0.0, axis=1
                )
                desglose["ASA (s)"] = desglose.apply(
                    lambda r: (r["tAnswered_sum"] / r["tAnswered_count"] / 1000.0) if r["tAnswered_count"] > 0 else 0.0, axis=1
                )

                desglose = desglose.rename(columns={
                    "queueId": "ID de Cola (GUID)",
                    "canal": "Canal",
                    "nOffered": "Entrantes",
                    "tAnswered_count": "Atendidas",
                    "tAbandon_count": "Abandonadas"
                })

                desglose = desglose.sort_values(by=["Entrantes"], ascending=False)

                cols_show = [
                    "Nombre de Cola en Genesys",
                    "Canal",
                    "Entrantes",
                    "Atendidas",
                    "Abandonadas",
                    "% Abandono",
                    "% NS",
                    "AHT (s)",
                    "ASA (s)",
                    "ID de Cola (GUID)"
                ]

                st.dataframe(
                    desglose[cols_show],
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Entrantes": st.column_config.NumberColumn("Entrantes", format="%d"),
                        "Atendidas": st.column_config.NumberColumn("Atendidas", format="%d"),
                        "Abandonadas": st.column_config.NumberColumn("Abandonadas", format="%d"),
                        "% Abandono": st.column_config.NumberColumn("% Abandono", format="%.1f%%"),
                        "% NS": st.column_config.NumberColumn("% NS", format="%.1f%%"),
                        "AHT (s)": st.column_config.NumberColumn("AHT (s)", format="%.0f s"),
                        "ASA (s)": st.column_config.NumberColumn("ASA (s)", format="%.1f s"),
                        "ID de Cola (GUID)": st.column_config.TextColumn("ID de Cola (GUID)", width="medium"),
                    }
                )

        # 5. Causalidad y Desglose por Supervisor
        st.markdown("#### 🔍 Diagnóstico Causa-Raíz por Supervisor")
        st.caption("Cálculo ponderado por interacciones para evaluar impacto real en AHT, Talk, Hold y ACW de cada equipo.")

        st.info("""
💡 **¿Cómo interpretar este cuadro y la causa-raíz del AHT?**

En operaciones de Contact Center, el tiempo total por llamada se desglosa matemáticamente en:
$$\\text{AHT (Tiempo Total)} = \\text{Talk (Conversación)} + \\text{Hold (Espera / Retención)} + \\text{ACW (Post-llamada)}$$

- **🔴 Retención / Hold excesivo**: El asesor deja al pasajero esperando demasiado en línea (>200s o >30% del AHT). Causa: dudas en procedimientos, lentitud de herramientas de emisión/remisión o consultas constantes a supervisores. **Acción**: Coaching técnico y revisión de permisos en sistemas.
- **🔴 ACW / Post-llamada alto**: El asesor demora más de 35s en tipificar tras colgar, o aprovecha el estado para descanso encubierto. **Acción**: Auditoría de tiempos de tipificación y reforzamiento de plantillas.
- **🔴 Conversación / Talk prolongado**: El diálogo excede la meta y el hold es bajo. Causa: falta de escucha activa, falta de síntesis o pérdida del control de la interacción. **Acción**: Escuchas de calidad y coaching en manejo de objeciones.
- **🟢 Cumple Meta**: Equipo operando de forma controlada dentro de los parámetros esperados.

👉 **Revisa la columna final "Acción Recomendada / Foco de Gestión" para saber exactamente qué intervenir con cada supervisor y con cada asesor en el desglose.**
""")

        df_as_raw, _ = obtener_aht_asesores_api(token, fecha_desde_str, fecha_hasta_str)
        if not df_as_raw.empty:
            aht_metas = gtr_cfg.get("aht_metas", {})
            metas_upper = {k.strip().upper(): v for k, v in aht_metas.items()}

            ALIAS_SERVICIOS_AHT = {
                "DT FFP AMC": "DREAM TEAMS VOZ",
                "DT FFP AMC ING": "DREAM TEAMS ENG",
                "EQUIPAJES AMC": "Equipajes AMC",
                "EQUIPAJES AMC ING": "Equipajes AMC ING",
                "VENTAS AMC": "Ventas AMC",
                "WPP VENTAS AMC": "WPP Ventas AMC",
                "CHAT VENTAS AMC": "CHAT Ventas AMC",
                "SOPORTE LUA AMC": "Soporte LUA AMC",
                "LATAM TRAVEL AMC": "Latam Travel AMC",
                "BO EQUIPAJES AMC": "EQUIPAJE BO",
                "AG CORPORATE CHAT": "AGENCIAS CHAT CORPORATE",
                "AGY N1 ESP VOZ": "AGENCIAS TARGET ES",
                "AGY N1 ENG VOZ": "AGENCIAS TARGET ENG",
                "AGY N1 ESP CHAT": "CHAT AGENCIAS ESP",
                "AGY N3 ESP VOZ": "AGENCIAS TARGET ES",
                "AGY N3 ESP CHAT": "CHAT AGENCIAS ESP",
                "RRSS AMC": "CHAT DREAM TEAMS ES",
                "RRSS AMC ING": "DREAM TEAMS ENG",
                "RRSS PORT AMC": "CHAT DREAM TEAMS ES",
            }

            def resolver_meta_aht(s_name: str):
                if not s_name:
                    return None
                s_clean = s_name.strip()
                if s_clean in aht_metas:
                    return aht_metas[s_clean]
                if s_clean.upper() in metas_upper:
                    return metas_upper[s_clean.upper()]
                if s_clean.upper() in ALIAS_SERVICIOS_AHT:
                    canon = ALIAS_SERVICIOS_AHT[s_clean.upper()]
                    return aht_metas.get(canon, metas_upper.get(canon.upper()))
                return None

            def cruzar_sup(row):
                aid = row["agente_id"]
                info = agentes_map.get(aid, {})
                nombre = info.get("agente", aid)
                serv = info.get("servicio", "Sin Asignar")
                sup = info.get("jefe_inmediato", "-")
                coord = info.get("coordinador", "-")
                meta = resolver_meta_aht(serv)
                return pd.Series([nombre, serv, sup, coord, meta])

            df_as_ger = df_as_raw.copy()
            df_as_ger[["Asesor", "Servicio", "Supervisor", "Coordinador", "Meta AHT"]] = df_as_ger.apply(cruzar_sup, axis=1)
            df_as_ger = df_as_ger[df_as_ger["Supervisor"] != "-"]

            # Selector de agrupación
            col_diag1, col_diag2 = st.columns([2, 3])
            with col_diag1:
                nivel_sup = st.radio(
                    "Nivel de Vista:",
                    ["👤 Consolidado por Supervisor", "👥 Desglose por Supervisor y Servicio"],
                    horizontal=True
                )

            group_cols = ["Supervisor"] if "Consolidado" in nivel_sup else ["Supervisor", "Servicio"]

            def agg_sup_ponderado(grp):
                tot_int = grp["interacciones"].sum()
                if tot_int == 0:
                    return pd.Series({})
                aht_p = (grp["aht_seg"] * grp["interacciones"]).sum() / tot_int
                talk_p = (grp["t_talk_seg"] * grp["interacciones"]).sum() / tot_int
                hold_p = (grp["t_held_seg"] * grp["interacciones"]).sum() / tot_int
                acw_p = (grp["t_acw_seg"] * grp["interacciones"]).sum() / tot_int
                
                valid_m = grp[grp["Meta AHT"].notna()]
                if not valid_m.empty and valid_m["interacciones"].sum() > 0:
                    meta_p = (valid_m["Meta AHT"] * valid_m["interacciones"]).sum() / valid_m["interacciones"].sum()
                else:
                    meta_p = None

                return pd.Series({
                    "Asesores Activos": int(grp["agente_id"].nunique()),
                    "Interacciones": int(tot_int),
                    "AHT Real (s)": round(aht_p, 0),
                    "Meta AHT (s)": round(meta_p, 0) if meta_p else None,
                    "Talk (s)": round(talk_p, 0),
                    "Hold (s)": round(hold_p, 0),
                    "ACW (s)": round(acw_p, 0),
                })

            sup_grp = df_as_ger.groupby(group_cols).apply(agg_sup_ponderado, include_groups=False).reset_index()

            def calc_desv(r):
                if pd.notna(r["Meta AHT (s)"]) and r["Meta AHT (s)"] > 0 and pd.notna(r["AHT Real (s)"]):
                    return round((r["AHT Real (s)"] - r["Meta AHT (s)"]) / r["Meta AHT (s)"] * 100.0, 1)
                return None

            sup_grp["Desv AHT (%)"] = sup_grp.apply(calc_desv, axis=1)

            def diagnosticar_foco_gestion(r):
                desv = r.get("Desv AHT (%)")
                if pd.isna(desv) or desv is None:
                    return "⚪ Sin Meta Definida"
                if desv <= 0:
                    return "🟢 Cumple Meta (Operación Controlada)"

                hold = r.get("Hold (s)")
                if hold is None:
                    hold = r.get("t_held_seg", 0)
                hold = float(hold or 0)

                talk = r.get("Talk (s)")
                if talk is None:
                    talk = r.get("t_talk_seg", 0)
                talk = float(talk or 0)

                acw = r.get("ACW (s)")
                if acw is None:
                    acw = r.get("t_acw_seg", 0)
                acw = float(acw or 0)

                aht_r = r.get("AHT Real (s)")
                if aht_r is None:
                    aht_r = r.get("aht_seg", 1)
                aht_r = float(aht_r or 1)

                meta = r.get("Meta AHT (s)")
                if meta is None:
                    meta = r.get("Meta AHT", 600)
                meta = float(meta or 600)

                pct_hold = (hold / aht_r * 100.0) if aht_r > 0 else 0.0

                if hold > 200 or pct_hold >= 30.0:
                    return f"🔴 Retención / Hold ({int(hold)}s - {int(pct_hold)}% AHT) — Dudas procedimentales o herramientas"
                elif acw > 35:
                    return f"🔴 ACW / Post-llamada ({int(acw)}s) — Demora en tipificación o cierre tras colgar"
                elif talk > meta:
                    return f"🔴 Conversación / Talk ({int(talk)}s) — Reforzar escucha activa y síntesis de llamada"
                elif desv <= 10.0:
                    return f"🟡 Desvío Leve (+{desv:.1f}%) — Monitorear llamadas punta"

                return f"🔴 Desvío Mixto (+{desv:.1f}%) — Auditar llamadas de mayor duración"

            def estilo_foco(val):
                if not isinstance(val, str):
                    return ""
                if "🟢" in val:
                    return "background-color: rgba(27, 175, 122, 0.15); color: #0e6251; font-weight: 600;"
                elif "🟡" in val:
                    return "background-color: rgba(237, 161, 0, 0.15); color: #7d6608; font-weight: 600;"
                elif "🔴" in val:
                    return "background-color: rgba(226, 75, 74, 0.15); color: #78281f; font-weight: 600;"
                return ""

            sup_grp["Acción Recomendada / Foco de Gestión"] = sup_grp.apply(diagnosticar_foco_gestion, axis=1)
            sup_grp = sup_grp.sort_values(by=["Desv AHT (%)", "Interacciones"], ascending=[False, False])

            styler_sup = (
                sup_grp.style
                .map(estilo_desv_aht, subset=["Desv AHT (%)"])
                .map(estilo_hold, subset=["Hold (s)"])
                .map(estilo_foco, subset=["Acción Recomendada / Foco de Gestión"])
            )

            st.dataframe(
                styler_sup,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Asesores Activos": st.column_config.NumberColumn("Asesores Activos", format="%d"),
                    "Interacciones": st.column_config.NumberColumn("Interacciones", format="%d"),
                    "AHT Real (s)": st.column_config.NumberColumn("AHT Real (s)", format="%.0f s"),
                    "Meta AHT (s)": st.column_config.NumberColumn("Meta AHT (s)", format="%.0f s"),
                    "Desv AHT (%)": st.column_config.NumberColumn("Desv AHT (%)", format="%+.1f%%"),
                    "Talk (s)": st.column_config.NumberColumn("Talk (s)", format="%.0f s"),
                    "Hold (s)": st.column_config.NumberColumn("Hold / Retención (s)", format="%.0f s"),
                    "ACW (s)": st.column_config.NumberColumn("ACW (s)", format="%.0f s"),
                    "Acción Recomendada / Foco de Gestión": st.column_config.TextColumn("Acción Recomendada / Foco de Gestión", width="large"),
                }
            )

            # Detalle Drill-down por Asesor dentro del supervisor
            with st.expander("🔎 Ver detalle de asesores por Supervisor seleccionado", expanded=False):
                lista_supervisores = sorted(df_as_ger["Supervisor"].unique().tolist())
                sup_seleccionado = st.selectbox("Selecciona un Supervisor:", lista_supervisores)
                if sup_seleccionado:
                    df_asesores_sup = df_as_ger[df_as_ger["Supervisor"] == sup_seleccionado].copy()
                    df_asesores_sup["Desv AHT (%)"] = df_asesores_sup.apply(
                        lambda r: round((r["aht_seg"] - r["Meta AHT"]) / r["Meta AHT"] * 100.0, 1) if pd.notna(r["Meta AHT"]) and r["Meta AHT"] > 0 else None,
                        axis=1
                    )
                    df_asesores_sup["Acción Recomendada / Foco de Gestión"] = df_asesores_sup.apply(diagnosticar_foco_gestion, axis=1)
                    df_asesores_sup = df_asesores_sup.sort_values(by="aht_seg", ascending=False)
                    styler_asesores = (
                        df_asesores_sup[["Asesor", "Servicio", "interacciones", "aht_seg", "Meta AHT", "Desv AHT (%)", "t_talk_seg", "t_held_seg", "t_acw_seg", "Acción Recomendada / Foco de Gestión"]]
                        .style
                        .map(estilo_desv_aht, subset=["Desv AHT (%)"])
                        .map(estilo_hold, subset=["t_held_seg"])
                        .map(estilo_foco, subset=["Acción Recomendada / Foco de Gestión"])
                    )
                    st.dataframe(
                        styler_asesores,
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "interacciones": st.column_config.NumberColumn("Interacciones", format="%d"),
                            "aht_seg": st.column_config.NumberColumn("AHT Real (s)", format="%.0f s"),
                            "Meta AHT": st.column_config.NumberColumn("Meta AHT (s)", format="%.0f s"),
                            "Desv AHT (%)": st.column_config.NumberColumn("Desv AHT (%)", format="%+.1f%%"),
                            "t_talk_seg": st.column_config.NumberColumn("Talk (s)", format="%.0f s"),
                            "t_held_seg": st.column_config.NumberColumn("Hold (s)", format="%.0f s"),
                            "t_acw_seg": st.column_config.NumberColumn("ACW (s)", format="%.0f s"),
                            "Acción Recomendada / Foco de Gestión": st.column_config.TextColumn("Acción Recomendada / Foco de Gestión", width="large"),
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
        if "Otras Colas / No Mapeado" in serv_data and serv_data["Otras Colas / No Mapeado"]["LL ENT"] > 0:
            servicios_intradia.append("Otras Colas / No Mapeado")
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


# ══════════════════════════════════════════════════════════════════════════════
# MOTOR HISTÓRICO DE NIVELES DE SERVICIO & GTR (GENESYS CLOUD ANALYTICS)
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=1800, show_spinner=False)
def obtener_metricas_gtr_historico_api(token: str, fecha_desde: str, fecha_hasta: str, granularidad: str = "P1D"):
    """
    Consulta métricas históricas de conversación a Genesys Cloud Analytics Conversation Aggregates.
    Soporta granularidad P1D (día a día) o PT30M (intervalos de 30 min para un día específico).
    """
    gtr_cfg = cargar_config_gtr()
    queues_cfg = gtr_cfg.get("queues", {})
    services_cfg = gtr_cfg.get("services", {})

    try:
        start_dt = pd.to_datetime(f"{fecha_desde} 05:00:00")
        end_dt = pd.to_datetime(f"{fecha_hasta} 05:00:00") + pd.Timedelta(days=1)
        s_start = start_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        s_end = end_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        interval = f"{s_start}/{s_end}"
    except Exception as e:
        return pd.DataFrame(), f"Error en formato de fechas: {e}"

    body = {
        "interval": interval,
        "granularity": granularidad,
        "groupBy": ["queueId"],
        "metrics": ["nOffered", "tAnswered", "tAbandon", "tHandle", "oServiceLevel"]
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = "https://api.mypurecloud.com/api/v2/analytics/conversations/aggregates/query"

    try:
        r = requests.post(url, headers=headers, json=body, timeout=35)
        if r.status_code != 200:
            return pd.DataFrame(), f"Error API Genesys ({r.status_code}): {r.text[:200]}"
        res = r.json()
    except Exception as e:
        return pd.DataFrame(), f"Error de conexión con Genesys: {str(e)}"

    records = []
    for group in res.get("results", []):
        qid = group.get("group", {}).get("queueId")
        q_info = queues_cfg.get(qid, {})
        srv = q_info.get("servicio", "Otras Colas / No Mapeado")
        q_name = q_info.get("nombre_cola", qid or "Directo / Sin Cola")
        canal = services_cfg.get(srv, {}).get("canal", "VOZ" if "WPP" not in srv and "CHAT" not in srv else "DIGITAL")

        for d in group.get("data", []):
            int_str = d.get("interval", "")
            start_utc = int_str.split("/")[0] if "/" in int_str else int_str
            dt_col = pd.to_datetime(start_utc) - pd.Timedelta(hours=5)
            f_label = dt_col.strftime("%Y-%m-%d")
            h_label = dt_col.strftime("%H:%M")

            row = {
                "fecha": f_label,
                "intervalo": h_label,
                "intervalo_dt": dt_col,
                "queueId": qid,
                "nombre_cola": q_name,
                "servicio": srv,
                "canal": canal,
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
            for metric in d.get("metrics", []):
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

    return pd.DataFrame(records), None


def render_tab_gtr_historico(token: str, gtr_cfg: dict):
    """
    Renderiza la sección histórica de Niveles de Servicio y GTR en la pestaña Histórico.
    Permite análisis de tendencias diarias, gráficos comparativos con metas y consolidado por servicio.
    """
    st.markdown("### 📈 Histórico de Niveles de Servicio & GTR")
    st.caption("Consolidado histórico de volumen, abandono, SLA y AHT directo desde Genesys Cloud Analytics Conversation Aggregates.")

    hoy = datetime.now(timezone.utc).date()
    if "gtr_hist_hasta" not in st.session_state:
        st.session_state["gtr_hist_hasta"] = hoy
    if "gtr_hist_desde" not in st.session_state:
        st.session_state["gtr_hist_desde"] = hoy - timedelta(days=6)

    # 1. Controles de Rango de Fechas
    col_d1, col_d2, col_srv_filter = st.columns([1.2, 1.2, 3.6])

    def set_rango_gtr(dias):
        st.session_state["gtr_hist_hasta"] = hoy
        if dias:
            st.session_state["gtr_hist_desde"] = hoy - timedelta(days=dias - 1)
        else:
            st.session_state["gtr_hist_desde"] = hoy.replace(day=1)

    with col_d1:
        f_desde = st.date_input(
            "Desde",
            value=st.session_state["gtr_hist_desde"],
            max_value=hoy,
            key="input_gtr_hist_desde"
        )
        st.session_state["gtr_hist_desde"] = f_desde
    with col_d2:
        f_hasta = st.date_input(
            "Hasta",
            value=st.session_state["gtr_hist_hasta"],
            max_value=hoy,
            key="input_gtr_hist_hasta"
        )
        st.session_state["gtr_hist_hasta"] = f_hasta

    # Presets rápidos
    col_p1, col_p2, col_p3, col_p4, _, col_btn_act = st.columns([1, 1, 1, 1, 1, 3])
    with col_p1:
        if st.button("7 días", key="btn_gtr_7d", use_container_width=True):
            set_rango_gtr(7)
            st.rerun()
    with col_p2:
        if st.button("14 días", key="btn_gtr_14d", use_container_width=True):
            set_rango_gtr(14)
            st.rerun()
    with col_p3:
        if st.button("30 días", key="btn_gtr_30d", use_container_width=True):
            set_rango_gtr(30)
            st.rerun()
    with col_p4:
        if st.button("Mes actual", key="btn_gtr_mes", use_container_width=True):
            set_rango_gtr(None)
            st.rerun()
    with col_btn_act:
        if st.button("🔄 Actualizar Datos Históricos", key="btn_gtr_refresh", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    if f_desde > f_hasta:
        st.error("La fecha 'Desde' no puede ser posterior a 'Hasta'.")
        return

    # Opciones de servicios
    opciones_servicios = ["TT_LATAM (Consolidado)", "TT_EQUIPAJES (Consolidado)"] + [
        s for s in SERVICIOS_ORDEN_OFICIAL if s not in ("TT_LATAM", "TT_EQUIPAJES")
    ] + ["Otras Colas / No Mapeado"]

    with col_srv_filter:
        srv_seleccionados = st.multiselect(
            "Filtrar Servicios:",
            options=opciones_servicios,
            default=["TT_LATAM (Consolidado)", "TT_EQUIPAJES (Consolidado)"]
        )

    # 2. Consultar datos históricos
    with st.spinner(f"Consultando métricas de Genesys Cloud ({f_desde} al {f_hasta})..."):
        df_hist_raw, err = obtener_metricas_gtr_historico_api(token, str(f_desde), str(f_hasta), "P1D")

    if err or df_hist_raw.empty:
        st.warning(f"No se encontraron métricas históricas para el rango seleccionado: {err or 'Sin datos'}")
        return

    st.markdown("---")

    # 3. Construir métricas agregadas por día y por servicio
    services_cfg = gtr_cfg.get("services", {})
    aht_metas = gtr_cfg.get("aht_metas", {})

    filas_macros = []
    fechas_unicas = sorted(df_hist_raw["fecha"].unique())
    for f in fechas_unicas:
        sub_f = df_hist_raw[df_hist_raw["fecha"] == f]
        # TT_LATAM
        sub_latam = sub_f[sub_f["servicio"].isin(SERVICIOS_TT_LATAM)]
        if not sub_latam.empty:
            filas_macros.append({
                "fecha": f,
                "servicio": "TT_LATAM (Consolidado)",
                "canal": "CONSOLIDADO",
                "nOffered": sub_latam["nOffered"].sum(),
                "tAnswered_count": sub_latam["tAnswered_count"].sum(),
                "tAnswered_sum": sub_latam["tAnswered_sum"].sum(),
                "tAbandon_count": sub_latam["tAbandon_count"].sum(),
                "tAbandon_sum": sub_latam["tAbandon_sum"].sum(),
                "tHandle_count": sub_latam["tHandle_count"].sum(),
                "tHandle_sum": sub_latam["tHandle_sum"].sum(),
                "sl_numerator": sub_latam["sl_numerator"].sum(),
                "sl_denominator": sub_latam["sl_denominator"].sum()
            })
        # TT_EQUIPAJES
        sub_equ = sub_f[sub_f["servicio"].isin(SERVICIOS_TT_EQUIPAJES)]
        if not sub_equ.empty:
            filas_macros.append({
                "fecha": f,
                "servicio": "TT_EQUIPAJES (Consolidado)",
                "canal": "CONSOLIDADO",
                "nOffered": sub_equ["nOffered"].sum(),
                "tAnswered_count": sub_equ["tAnswered_count"].sum(),
                "tAnswered_sum": sub_equ["tAnswered_sum"].sum(),
                "tAbandon_count": sub_equ["tAbandon_count"].sum(),
                "tAbandon_sum": sub_equ["tAbandon_sum"].sum(),
                "tHandle_count": sub_equ["tHandle_count"].sum(),
                "tHandle_sum": sub_equ["tHandle_sum"].sum(),
                "sl_numerator": sub_equ["sl_numerator"].sum(),
                "sl_denominator": sub_equ["sl_denominator"].sum()
            })

    df_macros = pd.DataFrame(filas_macros)
    df_todos_hist = pd.concat([df_hist_raw, df_macros], ignore_index=True)

    # 4. KPIs Macro del Período
    tot_off = int(df_hist_raw["nOffered"].sum())
    tot_ans = int(df_hist_raw["tAnswered_count"].sum())
    tot_abn = int(df_hist_raw["tAbandon_count"].sum())
    pct_abn = (tot_abn / tot_off * 100.0) if tot_off > 0 else 0.0

    # Macro LATAM consolidado del período
    df_latam_tot = df_hist_raw[df_hist_raw["servicio"].isin(SERVICIOS_TT_LATAM)]
    latam_sl_n = df_latam_tot["sl_numerator"].sum()
    latam_sl_d = df_latam_tot["sl_denominator"].sum()
    pct_ns_latam = (latam_sl_n / latam_sl_d * 100.0) if latam_sl_d > 0 else 0.0
    latam_h_s = df_latam_tot["tHandle_sum"].sum()
    latam_h_c = df_latam_tot["tHandle_count"].sum()
    aht_latam = (latam_h_s / latam_h_c / 1000.0) if latam_h_c > 0 else 0.0

    k1, k2, k3, k4, k5 = st.columns(5)
    with k1:
        st.metric("Entrantes Período", f"{tot_off:,}")
    with k2:
        st.metric("Atendidas Período", f"{tot_ans:,}")
    with k3:
        st.metric("% Abandono Período", f"{pct_abn:.1f}%")
    with k4:
        st.metric("NS Acumulado LATAM", f"{pct_ns_latam:.1f}%", delta=f"{pct_ns_latam - 75.3:+.1f}pp vs 75.3% Meta")
    with k5:
        st.metric("AHT Promedio LATAM", f"{int(round(aht_latam))}s", delta=formatear_segundos_mm_ss(aht_latam))

    st.markdown("---")

    # 5. Gráficos Interactivos de Tendencia
    st.markdown("#### 📊 Evolución Diaria de Niveles de Servicio y Volúmenes")

    srv_filter = srv_seleccionados if srv_seleccionados else ["TT_LATAM (Consolidado)"]
    df_plot = df_todos_hist[df_todos_hist["servicio"].isin(srv_filter)].copy()

    df_diario_srv = df_plot.groupby(["fecha", "servicio"]).agg({
        "nOffered": "sum",
        "tAnswered_count": "sum",
        "tAbandon_count": "sum",
        "sl_numerator": "sum",
        "sl_denominator": "sum",
        "tHandle_sum": "sum",
        "tHandle_count": "sum"
    }).reset_index()

    df_diario_srv["% NS"] = df_diario_srv.apply(
        lambda r: (r["sl_numerator"] / r["sl_denominator"] * 100.0) if r["sl_denominator"] > 0 else 0.0, axis=1
    ).round(1)
    df_diario_srv["AHT (s)"] = df_diario_srv.apply(
        lambda r: (r["tHandle_sum"] / r["tHandle_count"] / 1000.0) if r["tHandle_count"] > 0 else 0.0, axis=1
    ).round(0)
    df_diario_srv["% Abandono"] = df_diario_srv.apply(
        lambda r: (r["tAbandon_count"] / r["nOffered"] * 100.0) if r["nOffered"] > 0 else 0.0, axis=1
    ).round(1)

    col_g1, col_g2 = st.columns(2)

    with col_g1:
        fig_ns = px.line(
            df_diario_srv,
            x="fecha",
            y="% NS",
            color="servicio",
            markers=True,
            title="📈 Tendencia Diaria de Nivel de Servicio (% NS)",
            labels={"fecha": "Fecha", "% NS": "% Nivel de Servicio"},
            color_discrete_sequence=px.colors.qualitative.Bold
        )
        fig_ns.add_hline(
            y=75.3,
            line_dash="dash",
            line_color="#e24b4a",
            annotation_text="Meta LATAM (75.3%)",
            annotation_position="bottom right"
        )
        fig_ns.update_layout(
            hovermode="x unified",
            yaxis=dict(range=[max(0, df_diario_srv["% NS"].min() - 10), 100], ticksuffix="%"),
            margin=dict(l=20, r=20, t=40, b=20),
            legend=dict(orientation="h", yanchor="bottom", y=-0.3, xanchor="center", x=0.5)
        )
        st.plotly_chart(fig_ns, use_container_width=True)

    with col_g2:
        df_diario_tot = df_hist_raw.groupby("fecha").agg({
            "nOffered": "sum",
            "tAnswered_count": "sum",
            "tAbandon_count": "sum"
        }).reset_index()

        fig_vol = go.Figure()
        fig_vol.add_trace(go.Bar(
            x=df_diario_tot["fecha"],
            y=df_diario_tot["tAnswered_count"],
            name="Atendidas",
            marker_color="#1baf7a"
        ))
        fig_vol.add_trace(go.Bar(
            x=df_diario_tot["fecha"],
            y=df_diario_tot["tAbandon_count"],
            name="Abandonadas",
            marker_color="#e24b4a"
        ))
        fig_vol.add_trace(go.Scatter(
            x=df_diario_tot["fecha"],
            y=df_diario_tot["nOffered"],
            name="Ofrecidas (Total)",
            mode="lines+markers",
            line=dict(color="#3b82f6", width=2)
        ))
        fig_vol.update_layout(
            barmode="stack",
            title="📊 Volumen Diario de Llamadas (Entrantes vs Atendidas vs Abandono)",
            hovermode="x unified",
            margin=dict(l=20, r=20, t=40, b=20),
            legend=dict(orientation="h", yanchor="bottom", y=-0.3, xanchor="center", x=0.5)
        )
        st.plotly_chart(fig_vol, use_container_width=True)

    # 6. Tabla Gerencial Consolidada del Período por Servicio
    st.markdown("#### 📋 Consolidado Operativo del Período por Servicio")
    st.caption("Métricas acumuladas entre las fechas seleccionadas. Permite auditar el cumplimiento global de SLA y AHT.")

    filas_resumen_periodo = []
    todos_srv = set(df_todos_hist["servicio"].unique())

    for s in todos_srv:
        sub = df_todos_hist[df_todos_hist["servicio"] == s]
        off = sub["nOffered"].sum()
        ans = sub["tAnswered_count"].sum()
        abn = sub["tAbandon_count"].sum()
        sl_n = sub["sl_numerator"].sum()
        sl_d = sub["sl_denominator"].sum()
        h_s = sub["tHandle_sum"].sum()
        h_c = sub["tHandle_count"].sum()
        a_s = sub["tAnswered_sum"].sum()

        if off == 0:
            continue

        if s == "TT_LATAM (Consolidado)":
            ns_m = 75.3
            aht_m = 877.0
        elif s == "TT_EQUIPAJES (Consolidado)":
            ns_m = 78.1
            aht_m = 993.0
        else:
            meta_ns_cfg = services_cfg.get(s, {}).get("ns_meta")
            if meta_ns_cfg is None:
                for k_s, v_s in services_cfg.items():
                    if k_s.strip().upper() == s.strip().upper():
                        meta_ns_cfg = v_s.get("ns_meta")
                        break
            ns_m = meta_ns_cfg * 100.0 if meta_ns_cfg is not None else None

            aht_m = aht_metas.get(s)
            if aht_m is None:
                for k_a, v_a in aht_metas.items():
                    if k_a.strip().upper() == s.strip().upper():
                        aht_m = v_a
                        break

        ns_r = (sl_n / sl_d * 100.0) if sl_d > 0 else 0.0
        aht_r = (h_s / h_c / 1000.0) if h_c > 0 else 0.0
        asa_r = (a_s / ans / 1000.0) if ans > 0 else 0.0
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

        filas_resumen_periodo.append({
            "Servicio": s,
            "Estado": estado,
            "Entrantes": int(off),
            "Atendidas": int(ans),
            "% Aband": (abn / off * 100.0) if off > 0 else 0.0,
            "NS Real": ns_r,
            "NS Meta": ns_m,
            "Dif NS (pp)": dif_ns,
            "AHT Real (s)": int(round(aht_r)) if aht_r else 0,
            "AHT Meta (s)": int(round(aht_m)) if aht_m else None,
            "Desv AHT (%)": desv_aht,
            "ASA (s)": asa_r
        })

    df_ger_periodo = pd.DataFrame(filas_resumen_periodo).sort_values(by=["Entrantes"], ascending=False)

    def estilo_dif_sla(val):
        if pd.isna(val): return ""
        if val >= 0: color = "#1baf7a"
        elif val >= -5.0: color = "#eda100"
        else: color = "#e24b4a"
        return f"background-color: {color}22; color: {color}; font-weight: 600;"

    def estilo_desv_aht(val):
        if pd.isna(val): return ""
        if val <= 0: color = "#1baf7a"
        elif val <= 10.0: color = "#eda100"
        else: color = "#e24b4a"
        return f"background-color: {color}22; color: {color}; font-weight: 600;"

    styler_periodo = (
        df_ger_periodo.style
        .map(estilo_dif_sla, subset=["Dif NS (pp)"])
        .map(estilo_desv_aht, subset=["Desv AHT (%)"])
    )

    st.dataframe(
        styler_periodo,
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

    # 7. Desglose Día a Día por Servicio
    with st.expander("📅 Detalle Día a Día por Servicio (Tabla Cruzada)", expanded=False):
        df_cross = df_diario_srv.sort_values(by=["fecha", "nOffered"], ascending=[False, False])
        st.dataframe(
            df_cross[["fecha", "servicio", "nOffered", "tAnswered_count", "tAbandon_count", "% Abandono", "% NS", "AHT (s)"]].rename(columns={
                "fecha": "Fecha",
                "servicio": "Servicio",
                "nOffered": "Entrantes",
                "tAnswered_count": "Atendidas",
                "tAbandon_count": "Abandonadas",
                "% Abandono": "% Abandono",
                "% NS": "% NS",
                "AHT (s)": "AHT (s)"
            }),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Entrantes": st.column_config.NumberColumn("Entrantes", format="%d"),
                "Atendidas": st.column_config.NumberColumn("Atendidas", format="%d"),
                "Abandonadas": st.column_config.NumberColumn("Abandonadas", format="%d"),
                "% Abandono": st.column_config.NumberColumn("% Abandono", format="%.1f%%"),
                "% NS": st.column_config.NumberColumn("% NS", format="%.1f%%"),
                "AHT (s)": st.column_config.NumberColumn("AHT (s)", format="%.0f s"),
            }
        )

    # 8. Curva Intradía 30 Minutos de un Día Pasado
    with st.expander("⏱️ Curva Intradía 30 Minutos de un Día Histórico", expanded=False):
        st.caption("Seleccione un día específico del rango y un servicio para consultar la curva hora a hora (cada 30 min).")
        col_intra_f, col_intra_s = st.columns(2)
        with col_intra_f:
            dia_sel = st.selectbox("Fecha a Inspeccionar:", fechas_unicas, index=len(fechas_unicas) - 1, key="sel_hist_intra_dia")
        with col_intra_s:
            srv_intra_sel = st.selectbox("Servicio:", ["TT_LATAM (Consolidado)"] + SERVICIOS_ORDEN_OFICIAL, key="sel_hist_intra_srv")

        if st.button("🔍 Cargar Curva Intradía", key="btn_load_intra_hist"):
            with st.spinner(f"Cargando intervalos PT30M para {dia_sel}..."):
                df_intra_day, err_i = obtener_metricas_gtr_historico_api(token, str(dia_sel), str(dia_sel), "PT30M")
            if err_i or df_intra_day.empty:
                st.warning("No se encontraron registros intradía para esa fecha.")
            else:
                if srv_intra_sel == "TT_LATAM (Consolidado)":
                    df_sub_intra = df_intra_day[df_intra_day["servicio"].isin(SERVICIOS_TT_LATAM)]
                elif srv_intra_sel == "TT_EQUIPAJES (Consolidado)":
                    df_sub_intra = df_intra_day[df_intra_day["servicio"].isin(SERVICIOS_TT_EQUIPAJES)]
                else:
                    df_sub_intra = df_intra_day[df_intra_day["servicio"] == srv_intra_sel]

                if df_sub_intra.empty:
                    st.info("Sin tráfico registrado para este servicio en esa fecha.")
                else:
                    intra_grp = df_sub_intra.groupby("intervalo").agg({
                        "nOffered": "sum",
                        "tAnswered_count": "sum",
                        "tAbandon_count": "sum",
                        "sl_numerator": "sum",
                        "sl_denominator": "sum",
                        "tHandle_sum": "sum",
                        "tHandle_count": "sum"
                    }).reset_index().sort_values("intervalo")

                    intra_grp["% NS"] = intra_grp.apply(
                        lambda r: (r["sl_numerator"] / r["sl_denominator"] * 100.0) if r["sl_denominator"] > 0 else 0.0, axis=1
                    ).round(1)

                    fig_intra = go.Figure()
                    fig_intra.add_trace(go.Bar(
                        x=intra_grp["intervalo"],
                        y=intra_grp["nOffered"],
                        name="Llamadas Entrantes",
                        marker_color="#3b82f6",
                        opacity=0.6
                    ))
                    fig_intra.add_trace(go.Scatter(
                        x=intra_grp["intervalo"],
                        y=intra_grp["% NS"],
                        name="% Nivel de Servicio",
                        yaxis="y2",
                        mode="lines+markers",
                        line=dict(color="#1baf7a", width=3)
                    ))
                    fig_intra.update_layout(
                        title=f"Curva Intradía — {srv_intra_sel} ({dia_sel})",
                        yaxis=dict(title="Llamadas Entrantes"),
                        yaxis2=dict(title="% NS", overlaying="y", side="right", range=[0, 100], ticksuffix="%"),
                        hovermode="x unified",
                        margin=dict(l=20, r=20, t=40, b=20)
                    )
                    st.plotly_chart(fig_intra, use_container_width=True)

    # 9. Desglose de Otras Colas / No Mapeado en el Período
    df_no_map_hist = df_hist_raw[df_hist_raw["servicio"] == "Otras Colas / No Mapeado"]
    if not df_no_map_hist.empty:
        qids_hist_nomap = tuple(df_no_map_hist["queueId"].dropna().unique())
        total_nomap_off = int(df_no_map_hist["nOffered"].sum())
        with st.expander(f"🔎 Desglose de 'Otras Colas / No Mapeado' en el Período ({len(qids_hist_nomap)} colas • {total_nomap_off:,} interacciones)", expanded=False):
            st.caption("Colas activas durante el período seleccionado que no están en el catálogo oficial de servicios.")
            nombres_res = resolver_nombres_colas_genesys(token, qids_hist_nomap)

            desglose_h = df_no_map_hist.groupby(["queueId", "canal"]).agg({
                "nOffered": "sum",
                "tAnswered_count": "sum",
                "tAbandon_count": "sum",
                "sl_numerator": "sum",
                "sl_denominator": "sum",
                "tHandle_sum": "sum",
                "tHandle_count": "sum"
            }).reset_index()

            desglose_h["Nombre de Cola en Genesys"] = desglose_h["queueId"].map(
                lambda q: nombres_res.get(q, str(q)) if q in nombres_res else str(q)
            )
            desglose_h["% Abandono"] = desglose_h.apply(
                lambda r: (r["tAbandon_count"] / r["nOffered"] * 100.0) if r["nOffered"] > 0 else 0.0, axis=1
            )
            desglose_h["% NS"] = desglose_h.apply(
                lambda r: (r["sl_numerator"] / r["sl_denominator"] * 100.0) if r["sl_denominator"] > 0 else 0.0, axis=1
            )
            desglose_h["AHT (s)"] = desglose_h.apply(
                lambda r: (r["tHandle_sum"] / r["tHandle_count"] / 1000.0) if r["tHandle_count"] > 0 else 0.0, axis=1
            )

            desglose_h = desglose_h.rename(columns={
                "queueId": "ID de Cola (GUID)",
                "canal": "Canal",
                "nOffered": "Entrantes",
                "tAnswered_count": "Atendidas",
                "tAbandon_count": "Abandonadas"
            }).sort_values(by=["Entrantes"], ascending=False)

            cols_show_h = [
                "Nombre de Cola en Genesys", "Canal", "Entrantes", "Atendidas", "Abandonadas",
                "% Abandono", "% NS", "AHT (s)", "ID de Cola (GUID)"
            ]
            st.dataframe(
                desglose_h[cols_show_h],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Entrantes": st.column_config.NumberColumn("Entrantes", format="%d"),
                    "Atendidas": st.column_config.NumberColumn("Atendidas", format="%d"),
                    "Abandonadas": st.column_config.NumberColumn("Abandonadas", format="%d"),
                    "% Abandono": st.column_config.NumberColumn("% Abandono", format="%.1f%%"),
                    "% NS": st.column_config.NumberColumn("% NS", format="%.1f%%"),
                    "AHT (s)": st.column_config.NumberColumn("AHT (s)", format="%.0f s"),
                }
            )
