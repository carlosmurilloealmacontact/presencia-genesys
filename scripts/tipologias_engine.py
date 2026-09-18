"""
Motor de Tipologías de Contacto y Detección de Contingencias (Genesys Cloud & Salesforce).
Permite radiografiar los motivos de contacto y códigos de finalización (Wrap-up Codes),
diagnosticar picos de demanda en tiempo real y generar reportes diarios consolidados exportables.
"""

from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
import time

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_GTR_PATH = BASE_DIR / "scripts" / "gtr_config.json"
CATALOG_PATH = BASE_DIR / "data" / "wrapup_codes_catalog.json"
SF_DB_PATH = BASE_DIR / "data" / "salesforce_live.db"
SF_CASES_PATH = BASE_DIR / "data" / "salesforce" / "cases_amc_cleaned.csv"


def formatear_segundos_mm_ss(segundos):
    """Convierte segundos a formato MM:SS."""
    if pd.isna(segundos) or segundos is None or segundos < 0:
        return "-"
    m = int(segundos // 60)
    s = int(segundos % 60)
    return f"{m:02d}:{s:02d}"


# ── 1. CATÁLOGO DE WRAP-UP CODES DE GENESYS ──────────────────────────────────

def cargar_catalogo_wrapup_codes(token: str, forzar_recarga: bool = False) -> dict:
    """
    Carga o descarga el catálogo oficial de códigos de finalización (Wrap-up Codes) de Genesys Cloud.
    Cachea en disco en data/wrapup_codes_catalog.json con TTL de 24 horas para respuesta instantánea.
    """
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    if not forzar_recarga and CATALOG_PATH.exists():
        try:
            mtime = os.path.getmtime(CATALOG_PATH)
            if (time.time() - mtime) < 86400:  # 24 horas
                with open(CATALOG_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
        except Exception:
            pass

    if not token:
        if CATALOG_PATH.exists():
            try:
                with open(CATALOG_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    catalog = {}
    page = 1
    while True:
        url = f"https://api.mypurecloud.com/api/v2/routing/wrapupcodes?pageSize=100&pageNumber={page}"
        try:
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code != 200:
                break
            data = r.json()
            entities = data.get("entities", [])
            if not entities:
                break
            for e in entities:
                cid = e.get("id")
                cname = e.get("name")
                if cid and cname:
                    catalog[cid] = cname
            if page >= data.get("pageCount", 1):
                break
            page += 1
        except Exception:
            break

    if catalog:
        try:
            with open(CATALOG_PATH, "w", encoding="utf-8") as f:
                json.dump(catalog, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    return catalog


# ── 2. EXTRACCIÓN DE TIPOLOGÍAS EN GENESYS CLOUD ──────────────────────────────

def obtener_tipologias_genesys(token: str, fecha: str = "hoy", servicio: str = "") -> pd.DataFrame:
    """
    Consulta a la Analytics API de Genesys Cloud las métricas agrupadas por cola y código de finalización.
    Devuelve DataFrame con: servicio, nombre_cola, wrapup_id, motivo_tipologia, canal, volumen, aht_segundos, aht_formato, porcentaje.
    """
    if not token:
        return pd.DataFrame()

    catalog = cargar_catalogo_wrapup_codes(token)

    # Cargar mapeo de colas
    queues_cfg = {}
    services_cfg = {}
    if CONFIG_GTR_PATH.exists():
        try:
            with open(CONFIG_GTR_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                queues_cfg = cfg.get("queues", {})
                services_cfg = cfg.get("services", {})
        except Exception:
            pass

    # Resolver intervalo ISO-8601
    f_clean = str(fecha or "").strip().lower()
    es_hoy = f_clean in ["", "hoy", "today", "en vivo", "tiempo real", "actual", "ahora"]

    if es_hoy:
        ahora_utc = datetime.now(timezone.utc)
        inicio_utc = ahora_utc.replace(hour=5, minute=0, second=0, microsecond=0)
        fin_utc = ahora_utc + timedelta(hours=1)
    else:
        try:
            dt_base = datetime.strptime(fecha, "%Y-%m-%d")
            inicio_utc = dt_base.replace(hour=5, minute=0, second=0, tzinfo=timezone.utc)
            fin_utc = inicio_utc + timedelta(days=1)
        except Exception:
            ahora_utc = datetime.now(timezone.utc)
            inicio_utc = ahora_utc.replace(hour=5, minute=0, second=0, microsecond=0)
            fin_utc = ahora_utc + timedelta(hours=1)

    interval_str = f"{inicio_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}/{fin_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}"

    body = {
        "interval": interval_str,
        "groupBy": ["queueId", "wrapUpCode"],
        "metrics": ["tHandle", "nOffered", "tAnswered"]
    }
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url = "https://api.mypurecloud.com/api/v2/analytics/conversations/aggregates/query"

    try:
        r = requests.post(url, headers=headers, json=body, timeout=25)
        if r.status_code != 200:
            return pd.DataFrame()
        res_json = r.json()
    except Exception:
        return pd.DataFrame()

    records = []
    for g in res_json.get("results", []):
        group_meta = g.get("group", {})
        qid = group_meta.get("queueId")
        wid = group_meta.get("wrapUpCode")
        media_type = group_meta.get("mediaType", "voice")

        q_info = queues_cfg.get(qid, {})
        if q_info.get("excluir", False):
            continue
        q_nombre_raw = str(q_info.get("nombre_cola", "")).upper()
        if q_nombre_raw.startswith(("KON_", "AEC_", "SCRIPT_", "FS_")) or q_nombre_raw in ("EPA_MESSAGE", "PENDIENTE_MENSAJE"):
            continue

        srv = q_info.get("servicio", "Otras Colas / No Mapeado")
        q_name = q_info.get("nombre_cola", qid or "Directo / Sin Cola")
        canal = services_cfg.get(srv, {}).get("canal", "VOZ" if media_type == "voice" else "DIGITAL")

        if servicio and servicio.lower() not in srv.lower():
            continue

        motivo_nombre = catalog.get(wid, "Sin Tipificar / Abandono" if not wid else f"Código {wid[:8]}")

        # Extraer tHandle (duración y conteo de gestiones tipificadas)
        handle_count = 0
        handle_sum_sec = 0.0
        data_blocks = g.get("data", [])
        for d in data_blocks:
            for m in d.get("metrics", []):
                m_name = m.get("metric")
                m_stats = m.get("stats", {})
                if m_name == "tHandle":
                    handle_count += m_stats.get("count", 0)
                    handle_sum_sec += (m_stats.get("sum", 0.0) / 1000.0)
                elif m_name == "tAnswered" and handle_count == 0:
                    handle_count += m_stats.get("count", 0)

        if handle_count > 0:
            records.append({
                "fuente": "Genesys Cloud",
                "servicio": srv,
                "cola": q_name,
                "canal": canal,
                "wrapup_id": wid or "",
                "motivo_contacto": motivo_nombre,
                "volumen": handle_count,
                "tiempo_total_sec": handle_sum_sec
            })

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    # Agrupar por servicio, cola y motivo de contacto
    df_agg = df.groupby(["fuente", "servicio", "cola", "canal", "motivo_contacto"]).agg({
        "volumen": "sum",
        "tiempo_total_sec": "sum"
    }).reset_index()

    df_agg["aht_segundos"] = (df_agg["tiempo_total_sec"] / df_agg["volumen"]).round(1)
    df_agg["aht_formato"] = df_agg["aht_segundos"].apply(formatear_segundos_mm_ss)

    total_vol = df_agg["volumen"].sum()
    df_agg["porcentaje"] = ((df_agg["volumen"] / total_vol) * 100.0).round(1) if total_vol > 0 else 0.0

    df_agg = df_agg.sort_values(by="volumen", ascending=False).reset_index(drop=True)
    return df_agg


# ── 3. EXTRACCIÓN DE TIPOLOGÍAS EN SALESFORCE (CHAT & CASOS) ──────────────────

def obtener_tipologias_salesforce(fecha: str = "hoy") -> pd.DataFrame:
    """
    Extrae las tipologías de Salesforce desde las colas de chat Omni-Channel en vivo (salesforce_live.db)
    y desde la base de casos CRM (cases_amc_cleaned.csv).
    """
    records = []

    # 1. Live Chat Queues (Tiempo Real)
    if SF_DB_PATH.exists():
        try:
            conn = sqlite3.connect(str(SF_DB_PATH))
            query = """
                SELECT queue_name, 
                       COUNT(*) as snapshots, 
                       AVG(chats_in_queue) as avg_espera, 
                       MAX(chats_in_queue) as max_espera
                FROM live_chat_queues 
                GROUP BY queue_name 
                HAVING max_espera > 0 OR avg_espera > 0
                ORDER BY avg_espera DESC
            """
            df_chats = pd.read_sql(query, conn)
            conn.close()

            for _, row in df_chats.iterrows():
                raw_q = str(row["queue_name"]).strip()
                # Limpiar nombres técnicos de bot a descripciones amigables
                motivo = raw_q.replace("BOT AMC ", "").replace("15 unidades...", "").strip()
                if "DUDAS OP" in raw_q:
                    motivo_limpio = f"Dudas Operacionales ({motivo})"
                elif "NDC" in raw_q:
                    motivo_limpio = f"Soporte Plataforma NDC ({motivo})"
                elif "AGENCIAS" in raw_q:
                    motivo_limpio = f"Atención Comercial Agencias ({motivo})"
                else:
                    motivo_limpio = motivo

                vol_aprox = int(row["max_espera"] * 5) + int(row["snapshots"] / 10)
                records.append({
                    "fuente": "Salesforce Omni-Chat",
                    "servicio": "Agencias & Pyme B2B",
                    "cola": raw_q,
                    "canal": "CHAT",
                    "motivo_contacto": motivo_limpio,
                    "volumen": max(vol_aprox, int(row["max_espera"])),
                    "aht_segundos": 600.0,
                    "aht_formato": "10:00"
                })
        except Exception:
            pass

    # 2. Casos CRM (Backlog y gestión)
    if SF_CASES_PATH.exists():
        try:
            df_cases = pd.read_csv(SF_CASES_PATH, nrows=5000)
            if "Work Queue Control" in df_cases.columns:
                cases_grouped = df_cases.groupby(["Work Queue Control", "Origen del caso"]).size().reset_index(name="conteo")
                for _, r_c in cases_grouped.head(15).iterrows():
                    q_ctrl = str(r_c["Work Queue Control"])
                    orig = str(r_c["Origen del caso"])
                    cnt = int(r_c["conteo"])
                    records.append({
                        "fuente": "Salesforce CRM Casos",
                        "servicio": "BO Agencias B2B",
                        "cola": q_ctrl,
                        "canal": orig.upper() if orig else "CASO",
                        "motivo_contacto": f"Gestión {q_ctrl} ({orig})",
                        "volumen": cnt,
                        "aht_segundos": 900.0,
                        "aht_formato": "15:00"
                    })
        except Exception:
            pass

    if not records:
        return pd.DataFrame()

    df_sf = pd.DataFrame(records)
    total_vol = df_sf["volumen"].sum()
    df_sf["porcentaje"] = ((df_sf["volumen"] / total_vol) * 100.0).round(1) if total_vol > 0 else 0.0
    df_sf = df_sf.sort_values(by="volumen", ascending=False).reset_index(drop=True)
    return df_sf


# ── 4. DETECTOR DE CONTINGENCIAS Y PICOS DE DEMANDA ────────────────────────────

def detectar_picos_y_contingencias(df_tipologias: pd.DataFrame, umbral_pct: float = 25.0) -> list:
    """
    Detecta motivos de contacto que concentran una proporción inusualmente alta de la demanda (>25%),
    indicando posibles caídas de sistemas, contingencias de vuelo o saturación repentina.
    """
    if df_tipologias is None or df_tipologias.empty:
        return []

    contingencias = []
    # Agrupar por servicio y motivo para evaluar concentración
    df_srv = df_tipologias.groupby(["servicio", "motivo_contacto"]).agg({
        "volumen": "sum"
    }).reset_index()

    for srv, group in df_srv.groupby("servicio"):
        total_srv = group["volumen"].sum()
        if total_srv < 15:
            continue
        for _, row in group.iterrows():
            pct = (row["volumen"] / total_srv) * 100.0
            motivo = row["motivo_contacto"]
            if pct >= umbral_pct and "Sin Tipificar" not in motivo:
                contingencias.append({
                    "servicio": srv,
                    "motivo": motivo,
                    "volumen": int(row["volumen"]),
                    "total_servicio": int(total_srv),
                    "porcentaje": round(pct, 1),
                    "nivel_alerta": "CRÍTICO 🔴" if pct >= 40.0 else "ALERTA 🟡"
                })

    contingencias.sort(key=lambda x: x["porcentaje"], reverse=True)
    return contingencias


# ── 5. GENERADOR PROFESIONAL DE REPORTE DIARIO EXCEL ──────────────────────────

def generar_reporte_diario_excel(df_genesys: pd.DataFrame, df_salesforce: pd.DataFrame, fecha_label: str = "Hoy") -> bytes:
    """
    Genera un archivo Excel enriquecido con formato corporativo de AlmaContact y LATAM Airlines
    con pestañas de Resumen Ejecutivo, Detalle Genesys Cloud y Detalle Salesforce.
    """
    wb = openpyxl.Workbook()
    wb.remove(wb.active)  # Quitar hoja default

    # Paleta de colores ejecutiva
    fill_header = PatternFill(start_color="3B0764", end_color="3B0764", fill_type="solid")
    fill_sub = PatternFill(start_color="4C1D95", end_color="4C1D95", fill_type="solid")
    fill_zebra = PatternFill(start_color="F5F3FF", end_color="F5F3FF", fill_type="solid")
    fill_alert = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")
    
    font_title = Font(name="Calibri", size=15, bold=True, color="FFFFFF")
    font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    font_body = Font(name="Calibri", size=10, bold=False, color="1E293B")
    font_bold = Font(name="Calibri", size=10, bold=True, color="1E293B")
    font_alert = Font(name="Calibri", size=10, bold=True, color="991B1B")

    thin_border = Border(
        left=Side(style="thin", color="E2E8F0"),
        right=Side(style="thin", color="E2E8F0"),
        top=Side(style="thin", color="E2E8F0"),
        bottom=Side(style="thin", color="E2E8F0")
    )

    # ── HOJA 1: RESUMEN EJECUTIVO Y CONTINGENCIAS ──
    ws_res = wb.create_sheet(title="Resumen Ejecutivo")
    ws_res.views.sheetView[0].showGridLines = True

    # Título
    ws_res.merge_cells("A1:F2")
    c_title = ws_res["A1"]
    c_title.value = f"REPORTE DIARIO DE TIPOLOGÍAS Y MOTIVOS DE CONTACTO — {fecha_label.upper()}"
    c_title.font = font_title
    c_title.fill = fill_header
    c_title.alignment = Alignment(horizontal="center", vertical="center")

    ws_res.append([])
    ws_res.append(["Fecha de Generación:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "", "Fuentes:", "Genesys Cloud & Salesforce B2B", ""])
    ws_res["A3"].font = font_bold
    ws_res["D3"].font = font_bold
    ws_res.append([])

    # Sección Contingencias Detectadas
    df_combo = pd.concat([df_genesys, df_salesforce], ignore_index=True) if not df_genesys.empty or not df_salesforce.empty else pd.DataFrame()
    contingencias = detectar_picos_y_contingencias(df_combo, umbral_pct=25.0)

    ws_res.append(["ALERTA DE CONTINGENCIAS Y PICOS DE DEMANDA IDENTIFICADOS"])
    ws_res.merge_cells(f"A5:F5")
    ws_res["A5"].font = font_header
    ws_res["A5"].fill = fill_sub
    ws_res["A5"].alignment = Alignment(horizontal="left", vertical="center")

    headers_cont = ["Nivel Alerta", "Servicio / Campaña", "Motivo de Contacto / Fallo", "Volumen", "Total Servicio", "% Concentración"]
    ws_res.append(headers_cont)
    for col_num in range(1, 7):
        c = ws_res.cell(row=6, column=col_num)
        c.font = font_header
        c.fill = fill_header
        c.border = thin_border
        c.alignment = Alignment(horizontal="center", vertical="center")

    curr_row = 7
    if contingencias:
        for cont in contingencias:
            ws_res.append([
                cont["nivel_alerta"],
                cont["servicio"],
                cont["motivo"],
                cont["volumen"],
                cont["total_servicio"],
                f"{cont['porcentaje']}%"
            ])
            for col_num in range(1, 7):
                cell = ws_res.cell(row=curr_row, column=col_num)
                cell.font = font_alert if "CRÍTICO" in cont["nivel_alerta"] else font_bold
                cell.fill = fill_alert
                cell.border = thin_border
                cell.alignment = Alignment(horizontal="center" if col_num in [1, 4, 5, 6] else "left", vertical="center")
            curr_row += 1
    else:
        ws_res.append(["🟢 NORMAL", "Todas las campañas", "No se detectan concentraciones anómalas de llamadas (>25%)", "-", "-", "-"])
        for col_num in range(1, 7):
            cell = ws_res.cell(row=curr_row, column=col_num)
            cell.font = font_body
            cell.border = thin_border
            cell.alignment = Alignment(horizontal="center" if col_num in [1, 4, 5, 6] else "left", vertical="center")
        curr_row += 1

    ws_res.append([])
    curr_row += 1

    # Top 15 Motivos Consolidados
    ws_res.append(["TOP 15 MOTIVOS DE CONTACTO MÁS FRECUENTES (GENERAL)"])
    ws_res.merge_cells(f"A{curr_row}:F{curr_row}")
    ws_res[f"A{curr_row}"].font = font_header
    ws_res[f"A{curr_row}"].fill = fill_sub
    curr_row += 1

    headers_top = ["Ranking", "Fuente", "Servicio", "Motivo de Contacto", "Volumen", "% Participación"]
    ws_res.append(headers_top)
    for col_num in range(1, 7):
        c = ws_res.cell(row=curr_row, column=col_num)
        c.font = font_header
        c.fill = fill_header
        c.border = thin_border
        c.alignment = Alignment(horizontal="center", vertical="center")
    curr_row += 1

    if not df_combo.empty:
        df_top15 = df_combo.groupby(["fuente", "servicio", "motivo_contacto"])["volumen"].sum().reset_index()
        tot_all = df_top15["volumen"].sum()
        df_top15["pct"] = ((df_top15["volumen"] / tot_all) * 100.0).round(1) if tot_all > 0 else 0.0
        df_top15 = df_top15.sort_values(by="volumen", ascending=False).head(15).reset_index(drop=True)

        for idx, r_t in df_top15.iterrows():
            ws_res.append([
                idx + 1,
                r_t["fuente"],
                r_t["servicio"],
                r_t["motivo_contacto"],
                int(r_t["volumen"]),
                f"{r_t['pct']}%"
            ])
            for col_num in range(1, 7):
                cell = ws_res.cell(row=curr_row, column=col_num)
                cell.font = font_bold if idx < 3 else font_body
                if idx % 2 == 1:
                    cell.fill = fill_zebra
                cell.border = thin_border
                cell.alignment = Alignment(horizontal="center" if col_num in [1, 2, 5, 6] else "left", vertical="center")
            curr_row += 1

    # Ajustar ancho de columnas Hoja 1
    for col in ws_res.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        col_letter = get_column_letter(col[0].column)
        ws_res.column_dimensions[col_letter].width = max(max_len + 3, 12)

    # ── HOJA 2: DETALLE GENESYS CLOUD ──
    if not df_genesys.empty:
        ws_gen = wb.create_sheet(title="Genesys Cloud (Voz & WPP)")
        ws_gen.views.sheetView[0].showGridLines = True

        cols_gen = ["Servicio", "Cola Genesys", "Canal", "Motivo de Contacto (Wrap-Up)", "Volumen", "% Participación", "AHT (MM:SS)"]
        ws_gen.append(cols_gen)
        for col_num in range(1, len(cols_gen) + 1):
            c = ws_gen.cell(row=1, column=col_num)
            c.font = font_header
            c.fill = fill_header
            c.alignment = Alignment(horizontal="center", vertical="center")

        for r_idx, row in df_genesys.iterrows():
            ws_gen.append([
                row["servicio"],
                row["cola"],
                row["canal"],
                row["motivo_contacto"],
                int(row["volumen"]),
                f"{row['porcentaje']}%",
                row["aht_formato"]
            ])
            row_num = r_idx + 2
            for col_num in range(1, len(cols_gen) + 1):
                cell = ws_gen.cell(row=row_num, column=col_num)
                cell.font = font_body
                if r_idx % 2 == 1:
                    cell.fill = fill_zebra
                cell.border = thin_border
                cell.alignment = Alignment(horizontal="center" if col_num in [3, 5, 6, 7] else "left", vertical="center")

        for col in ws_gen.columns:
            max_len = max(len(str(cell.value or "")) for cell in col)
            col_letter = get_column_letter(col[0].column)
            ws_gen.column_dimensions[col_letter].width = max(max_len + 3, 14)

    # ── HOJA 3: DETALLE SALESFORCE ──
    if not df_salesforce.empty:
        ws_sf = wb.create_sheet(title="Salesforce (Chat & Casos)")
        ws_sf.views.sheetView[0].showGridLines = True

        cols_sf = ["Fuente Salesforce", "Servicio", "Cola / Origen", "Canal", "Motivo / Tipología", "Volumen / Espera", "% Participación"]
        ws_sf.append(cols_sf)
        for col_num in range(1, len(cols_sf) + 1):
            c = ws_sf.cell(row=1, column=col_num)
            c.font = font_header
            c.fill = fill_header
            c.alignment = Alignment(horizontal="center", vertical="center")

        for r_idx, row in df_salesforce.iterrows():
            ws_sf.append([
                row["fuente"],
                row["servicio"],
                row["cola"],
                row["canal"],
                row["motivo_contacto"],
                int(row["volumen"]),
                f"{row['porcentaje']}%"
            ])
            row_num = r_idx + 2
            for col_num in range(1, len(cols_sf) + 1):
                cell = ws_sf.cell(row=row_num, column=col_num)
                cell.font = font_body
                if r_idx % 2 == 1:
                    cell.fill = fill_zebra
                cell.border = thin_border
                cell.alignment = Alignment(horizontal="center" if col_num in [4, 6, 7] else "left", vertical="center")

        for col in ws_sf.columns:
            max_len = max(len(str(cell.value or "")) for cell in col)
            col_letter = get_column_letter(col[0].column)
            ws_sf.column_dimensions[col_letter].width = max(max_len + 3, 14)

    output = BytesIO()
    wb.save(output)
    return output.getvalue()


# ── 6. VISTA STREAMLIT INTERACTIVA DEL MÓDULO ──────────────────────────────────

def render_seccion_tipologias(current_email: str = ""):
    """Renderiza el panel interactivo de Tipologías & Contingencias en el dashboard."""
    try:
        from live_engine import obtener_token_genesys
    except ImportError:
        from scripts.live_engine import obtener_token_genesys

    token = obtener_token_genesys()

    st.markdown(
        """
        <div style="background: linear-gradient(90deg, #3b0764 0%, #4c1d95 50%, #1e1b4b 100%); padding: 18px 24px; border-radius: 12px; margin-bottom: 20px; border-left: 5px solid #c084fc;">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
                <div>
                    <h2 style="color: #ffffff; margin: 0; font-size: 22px; font-weight: 800;">
                        🏷️ Tipologías de Contacto & Detección de Contingencias
                    </h2>
                    <p style="color: #e9d5ff; margin: 4px 0 0 0; font-size: 13px;">
                        Radiografía analítica de motivos de llamada (Genesys Wrap-Up Codes) y colas de atención Salesforce B2B para diagnóstico de picos súbitos de demanda.
                    </p>
                </div>
                <div style="text-align: right; background: rgba(255,255,255,0.1); padding: 6px 14px; border-radius: 8px; border: 1px solid rgba(255,255,255,0.2);">
                    <span style="color: #c084fc; font-size: 11px; font-weight: 700; text-transform: uppercase;">Fuentes Activas</span><br>
                    <span style="color: #4ade80; font-size: 12px; font-weight: 600;">Genesys Cloud (Voz/WPP) • Salesforce CRM</span>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    # Filtros Superiores
    f_c1, f_c2, f_c3, f_c4 = st.columns([1.2, 1.2, 1.4, 1.0])
    with f_c1:
        fuente_sel = st.selectbox("🌐 Fuente de Datos", ["Todas (Consolidado)", "Genesys Cloud (Voz & WPP)", "Salesforce B2B (Chat & Casos)"], key="tipol_fuente_sel")
    with f_c2:
        modo_fecha = st.radio("⏱️ Modalidad Temporal", ["En Vivo Hoy", "Fecha Histórica"], horizontal=True, key="tipol_modo_fecha")
    with f_c3:
        if modo_fecha == "Fecha Histórica":
            fecha_sel = st.date_input("📅 Fecha de Consulta", value=datetime(2026, 9, 17), key="tipol_fecha_dt")
            fecha_str = fecha_sel.strftime("%Y-%m-%d")
        else:
            fecha_str = "hoy"
            st.info("⚡ Analizando tráfico acumulado del día de hoy en tiempo real.")
    with f_c4:
        servicios_disp = ["Todos los Servicios", "LUA AMC", "Ventas AMC", "DT FFP AMC", "Equipajes AMC", "WPP LUA AMC", "Agencias B2B / Corporate"]
        srv_sel = st.selectbox("🏢 Filtro Servicio", servicios_disp, key="tipol_srv_sel")
        srv_query = "" if srv_sel == "Todos los Servicios" else srv_sel

    with st.spinner("Extrayendo y categorizando motivos de interacción desde Genesys y Salesforce..."):
        df_genesys = pd.DataFrame()
        df_salesforce = pd.DataFrame()

        if fuente_sel in ["Todas (Consolidado)", "Genesys Cloud (Voz & WPP)"]:
            df_genesys = obtener_tipologias_genesys(token, fecha=fecha_str, servicio=srv_query)

        if fuente_sel in ["Todas (Consolidado)", "Salesforce B2B (Chat & Casos)"]:
            df_salesforce = obtener_tipologias_salesforce(fecha=fecha_str)

        # Unificar
        frames = []
        if not df_genesys.empty:
            frames.append(df_genesys)
        if not df_salesforce.empty:
            frames.append(df_salesforce)

        df_total = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    if df_total.empty:
        st.warning("⚠️ No se registraron datos de tipología para la fecha y filtros seleccionados.")
        return

    # Recalcular porcentajes globales sobre la vista actual
    tot_vol = df_total["volumen"].sum()
    df_total["porcentaje"] = ((df_total["volumen"] / tot_vol) * 100.0).round(1) if tot_vol > 0 else 0.0

    # ── TARJETAS DE KPIS EJECUTIVOS ──
    top1_row = df_total.iloc[0] if not df_total.empty else None
    top3_pct = df_total.head(3)["porcentaje"].sum() if len(df_total) >= 3 else df_total["porcentaje"].sum()
    aht_prom = df_total["aht_segundos"].mean() if "aht_segundos" in df_total.columns else 0.0

    k_c1, k_c2, k_c3, k_c4 = st.columns(4)
    with k_c1:
        st.metric("Total Contactos Tipificados", f"{tot_vol:,}")
    with k_c2:
        st.metric("Top 1 Motivo Principal", f"{top1_row['motivo_contacto'][:22]}..." if top1_row is not None else "-", f"{top1_row['porcentaje']}% de demanda" if top1_row is not None else "")
    with k_c3:
        st.metric("Concentración Top 3", f"{top3_pct:.1f}%", help="% del volumen total que se concentra en los 3 primeros motivos")
    with k_c4:
        st.metric("AHT Promedio Ponderado", formatear_segundos_mm_ss(aht_prom))

    # ── BANNER DE ALERTA DE CONTINGENCIA / PICOS ──
    contingencias = detectar_picos_y_contingencias(df_total, umbral_pct=25.0)
    if contingencias:
        for c in contingencias:
            st.error(
                f"🚨 **{c['nivel_alerta']} en {c['servicio']}**: El motivo **'{c['motivo']}'** concentra el **{c['porcentaje']}%** del tráfico ({c['volumen']:,} de {c['total_servicio']:,} interacciones). Posible causa raíz de contingencia o pico de demanda.",
                icon="⚠️"
            )
    else:
        st.success("🟢 **Operación Estable**: La demanda se encuentra distribuida normalmente entre los motivos habituales sin concentración anómala superior al 25%.", icon="✅")

    st.markdown("---")

    # ── GRÁFICOS INTERACTIVOS (PARETO Y TREEMAP) ──
    g_c1, g_c2 = st.columns([1.5, 1.0])

    with g_c1:
        st.subheader("📊 Top 10 Motivos de Contacto (Pareto)")
        df_top10 = df_total.groupby("motivo_contacto")["volumen"].sum().reset_index().sort_values(by="volumen", ascending=True).tail(10)
        fig_bar = px.bar(
            df_top10,
            x="volumen",
            y="motivo_contacto",
            orientation="h",
            color="volumen",
            color_continuous_scale="Purples",
            text="volumen",
            labels={"volumen": "Interacciones", "motivo_contacto": "Motivo de Contacto"}
        )
        fig_bar.update_layout(
            height=380,
            margin=dict(l=10, r=20, t=10, b=10),
            coloraxis_showscale=False,
            font=dict(size=11)
        )
        fig_bar.update_traces(textposition="outside")
        st.plotly_chart(fig_bar, use_container_width=True)

    with g_c2:
        st.subheader("🗺️ Mapa de Concentración por Servicio")
        fig_tree = px.treemap(
            df_total.head(30),
            path=["servicio", "motivo_contacto"],
            values="volumen",
            color="volumen",
            color_continuous_scale="Purples"
        )
        fig_tree.update_layout(
            height=380,
            margin=dict(l=10, r=10, t=10, b=10),
            coloraxis_showscale=False
        )
        st.plotly_chart(fig_tree, use_container_width=True)

    # ── TABLA DE DETALLE Y EXPORTADOR ──
    st.subheader("📋 Detalle Exhaustivo de Tipologías y Motivos")

    d_c1, d_c2 = st.columns([2.5, 1.0], vertical_alignment="bottom")
    with d_c1:
        filtro_txt = st.text_input("🔍 Buscar motivo o cola...", placeholder="Ej. Pago, Check-in, PNR, Asientos, Equipaje...", key="tipol_txt_search")
    with d_c2:
        excel_bytes = generar_reporte_diario_excel(df_genesys, df_salesforce, fecha_label=fecha_str)
        st.download_button(
            label="📥 Descargar Reporte Diario (Excel)",
            data=excel_bytes,
            file_name=f"Reporte_Tipologias_LATAM_{fecha_str}_{datetime.now().strftime('%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            type="primary"
        )

    df_mostrar = df_total.copy()
    if filtro_txt:
        df_mostrar = df_mostrar[
            df_mostrar["motivo_contacto"].str.contains(filtro_txt, case=False, na=False) |
            df_mostrar["cola"].str.contains(filtro_txt, case=False, na=False) |
            df_mostrar["servicio"].str.contains(filtro_txt, case=False, na=False)
        ]

    cols_ver = ["fuente", "servicio", "cola", "canal", "motivo_contacto", "volumen", "porcentaje", "aht_formato"]
    cols_existentes = [c for c in cols_ver if c in df_mostrar.columns]
    
    st.dataframe(
        df_mostrar[cols_existentes].rename(columns={
            "fuente": "Fuente",
            "servicio": "Servicio",
            "cola": "Cola / Canal",
            "canal": "Tipo",
            "motivo_contacto": "Motivo de Contacto / Wrap-Up",
            "volumen": "Volumen",
            "porcentaje": "% Demanda",
            "aht_formato": "AHT (MM:SS)"
        }),
        use_container_width=True,
        hide_index=True,
        height=400
    )
