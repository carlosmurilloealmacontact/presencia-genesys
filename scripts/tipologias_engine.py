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
ZD_PROD_PATH = BASE_DIR / "data" / "zendesk" / "productividad_hoy_en_vivo.csv"
ZD_BACKLOG_PATH = BASE_DIR / "data" / "zendesk" / "backlog_en_vivo.csv"


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


def resolver_nombre_y_macro_categoria(wid: str, catalog: dict, servicio: str = "", cola_nombre: str = "") -> tuple[str, str]:
    """
    Traduce el wrapUpCode (UUID o código nativo ININ) a:
    1. Nombre 100% comprensible para la operación (eliminando 'Código ININ-WRA').
    2. Contextualiza los Timeouts con el servicio/cola de procedencia (ej. [Equipajes], [Ventas]).
    3. Macro-Categoría de Negocio para análisis ejecutivo.
    """
    if not wid:
        srv_label = f" [{servicio.replace(' AMC', '')}]" if servicio else ""
        return f"⚠️ Sin Tipificar{srv_label} / Abandono", "⚠️ Sin Tipificar / Incidencias"

    wid_clean = str(wid).strip()

    # Códigos de sistema nativos de Genesys (Legacy Interactive Intelligence)
    if wid_clean == "ININ-WRAP-UP-TIMEOUT":
        srv_clean = str(servicio or cola_nombre).replace(" AMC", "").replace(" SSC", "").replace(" WPP", "").strip()
        label_srv = f" [{srv_clean}]" if srv_clean else ""
        
        # Inferencia de macro-familia por la cola de entrada del pasajero
        srv_u = str(servicio or cola_nombre).upper()
        if any(k in srv_u for k in ("BAG", "EQUIP", "MALETA")):
            m_inf = "🧳 Equipaje (Timeout ACW)"
        elif any(k in srv_u for k in ("VENTA", "EMIS", "TARIF", "PAQUETE")):
            m_inf = "💳 Ventas y Tarifas (Timeout ACW)"
        elif any(k in srv_u for k in ("FFP", "DT", "DREAM", "PASS", "HVC")):
            m_inf = "🌟 LATAM Pass (Timeout ACW)"
        elif any(k in srv_u for k in ("LUA", "VUELO", "ALTERA")):
            m_inf = "✈️ Atención Vuelos (Timeout ACW)"
        elif any(k in srv_u for k in ("AGENCIA", "CORP", "B2B")):
            m_inf = "🏢 Agencias B2B (Timeout ACW)"
        else:
            m_inf = "⚠️ Sin Tipificar / Incidencias"

        return f"⚠️ Sin Tipificar{label_srv} (Timeout ACW)", m_inf
    elif wid_clean == "ININ-WRAP-UP-DELETED":
        return "⚠️ Código Eliminado en Genesys", "⚠️ Sin Tipificar / Incidencias"
    elif wid_clean == "ININ-WRAP-UP":
        return "⚠️ Sin Tipificar (Cierre Directo ACD)", "⚠️ Sin Tipificar / Incidencias"
    elif wid_clean.startswith("ININ-"):
        clean_code = wid_clean.replace("ININ-", "").replace("-", " ").title()
        return f"⚙️ Sistema Genesys: {clean_code}", "⚙️ Eventos de Sistema"

    # Buscar en catálogo oficial de Genesys
    nom = catalog.get(wid_clean)
    if not nom:
        nom = f"Tipificación No Catalogada ({wid_clean[:8]})"

    nu = nom.upper()
    if any(k in nu for k in ("ALTERA", "ADELANTO", "POSTERGA", "CANCELAC", "CANCEL", "DEMORA", "PROTECC", "CONTINGENCIA", "HORARIOS", "DATA")):
        m = "✈️ Alteraciones y Cambios de Vuelo"
    elif any(k in nu for k in ("BAG", "EQUIP", "BAGAGEM", "MALETA", "FALTANTE O DEMORADO", "DANIFICADA", "AVIH", "PETC", "ANCILL", "ASSENTO", "ASIENTO")):
        m = "🧳 Equipaje y Ancillaries"
    elif any(k in nu for k in ("REEMBOLS", "DEVOLUC", "VOUCHER", "TRAVEL VOUCHER")):
        m = "🔁 Devoluciones y Reembolsos"
    elif any(k in nu for k in ("EMIS", "EMISS", "REEMIS", "BOLETO", "TARIF", "TARIFA", "PAGO", "PAGAMENTO", "LINK DE PAGO", "FOP")):
        m = "💳 Emisiones, Tarifas y Pagos"
    elif any(k in nu for k in ("MILLAS", "MILHAS", "PASS", "ACREDITA", "ACUMULA", "UPG", "UPGRADE", "SOCIO", "ELITE")):
        m = "🌟 LATAM Pass y Fidelización"
    elif any(k in nu for k in ("CHECK", "EMBARQUE", "DOCUMENTA", "PASSAPORTE", "PASAPORTE", "RESERVA")):
        m = "🛫 Check-in y Documentación"
    elif any(k in nu for k in ("IATA", "PCC", "AGENCIA", "AGÊNCIAS", "ADM", "DEBIT MEMO", "GDS", "SABRE", "AMADEUS")):
        m = "🏢 Agencias B2B y Canales Indirectos"
    elif any(k in nu for k in ("ALLEGRO", "ERRO", "ERROR", "QUEDA", "CAIDA", "LOGIN", "SISTEMA", "ACESSO", "ACCESO")):
        m = "💻 Fallas de Sistemas y Plataformas"
    elif any(k in nu for k in ("AGRADEC", "RESUELTO", "DUDA", "CONSULTA", "INFORMAC", "ACOMPANHAMENTO")):
        m = "🤝 Consultas, Agradecimientos y Gestión General"
    elif "SIN TIPIFICAR" in nu or "ABANDONO" in nu:
        m = "⚠️ Sin Tipificar / Incidencias"
    else:
        m = "📋 Otros Motivos de Contacto"

    return nom, m


def resolver_pais_origen(dnis: str = "", cola_nombre: str = "") -> str:
    """
    Identifica el país o mercado de origen de la llamada o interacción a partir de:
    1. El prefijo telefónico internacional del DNIS o ANI (norma ITU-T E.164).
    2. Nomenclatura oficial de colas por país/idioma/mercado en Genesys y Salesforce.
    """
    d_clean = str(dnis or "").strip().lower()
    for prefix in ("tel:", "sip:", "whatsapp:", "+"):
        if d_clean.startswith(prefix):
            d_clean = d_clean[len(prefix):]

    if d_clean.startswith("00"):
        d_clean = d_clean[2:]
    elif d_clean.startswith("0") and len(d_clean) > 8:
        d_clean = d_clean[1:]

    # 1. Reglas telefónicas de DNIS / ANI
    if d_clean.startswith("56") or d_clean.startswith("22"):
        return "🇨🇱 Chile"
    elif d_clean.startswith("57"):
        return "🇨🇴 Colombia"
    elif d_clean.startswith("51"):
        return "🇵🇪 Perú"
    elif d_clean.startswith("55"):
        return "🇧🇷 Brasil"
    elif d_clean.startswith("54"):
        return "🇦🇷 Argentina"
    elif d_clean.startswith("593"):
        return "🇪🇨 Ecuador"
    elif d_clean.startswith("1") and len(d_clean) >= 10:
        return "🇺🇸 USA / Canadá"
    elif d_clean.startswith("34"):
        return "🇪🇸 España"
    elif d_clean.startswith("52"):
        return "🇲🇽 México"
    elif d_clean.startswith("598"):
        return "🇺🇾 Uruguay"
    elif d_clean.startswith("591"):
        return "🇧🇴 Bolivia"
    elif d_clean.startswith("595"):
        return "🇵🇾 Paraguay"

    # 2. Reglas por nombre de la cola o canal
    q_u = str(cola_nombre or "").upper()
    if any(k in q_u for k in ("_BR", "_PT", "BRASIL", "BRAZIL")):
        return "🇧🇷 Brasil"
    elif any(k in q_u for k in ("_ING", "_EN", "INTER NA", "NORTH AMERICA", "USA")):
        return "🇺🇸 USA / Norteamérica"
    elif any(k in q_u for k in ("_ESP", " ESP", "ESPAÑA", "SPAIN", "EUROPA")):
        return "🇪🇸 España"
    elif any(k in q_u for k in ("_CO", "COLOMBIA")):
        return "🇨🇴 Colombia"
    elif any(k in q_u for k in ("_CL", "CHILE")):
        return "🇨🇱 Chile"
    elif any(k in q_u for k in ("_PE", "PERU")):
        return "🇵🇪 Perú"
    elif any(k in q_u for k in ("_EC", "ECUADOR")):
        return "🇪🇨 Ecuador"
    elif any(k in q_u for k in ("_AR", "ARGENTINA")):
        return "🇦🇷 Argentina"
    elif "SSC" in q_u:
        return "🌎 Sudamérica (SSC)"

    return "🌎 Multimercado LATAM"


# ── 2. EXTRACCIÓN DE TIPOLOGÍAS EN GENESYS CLOUD ──────────────────────────────

def obtener_tipologias_genesys(token: str, fecha: str = "hoy", servicio: str = "") -> pd.DataFrame:
    """
    Consulta a la Analytics API de Genesys Cloud las métricas agrupadas por cola, código de finalización y DNIS (país).
    Devuelve DataFrame con: fuente, pais, servicio, cola, canal, wrapup_id, motivo_contacto, macro_categoria, volumen, aht_segundos, aht_formato, porcentaje.
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

    # Resolver intervalo ISO-8601 en base a la zona horaria de Colombia (UTC-5 / America/Bogota)
    f_clean = str(fecha or "").strip().lower()
    es_hoy = f_clean in ["", "hoy", "today", "en vivo", "tiempo real", "actual", "ahora"]

    tz_col = timezone(timedelta(hours=-5))
    ahora_col = datetime.now(tz_col)

    if es_hoy:
        hoy_col = ahora_col.date()
        dt_ini_col = datetime(hoy_col.year, hoy_col.month, hoy_col.day, 0, 0, 0, tzinfo=tz_col)
        inicio_utc = dt_ini_col.astimezone(timezone.utc)
        fin_utc = datetime.now(timezone.utc) + timedelta(minutes=5)
    else:
        try:
            dt_base = datetime.strptime(str(fecha)[:10], "%Y-%m-%d").date()
            dt_ini_col = datetime(dt_base.year, dt_base.month, dt_base.day, 0, 0, 0, tzinfo=tz_col)
            dt_fin_col = dt_ini_col + timedelta(days=1)
            inicio_utc = dt_ini_col.astimezone(timezone.utc)
            fin_utc = dt_fin_col.astimezone(timezone.utc)
        except Exception:
            hoy_col = ahora_col.date()
            dt_ini_col = datetime(hoy_col.year, hoy_col.month, hoy_col.day, 0, 0, 0, tzinfo=tz_col)
            inicio_utc = dt_ini_col.astimezone(timezone.utc)
            fin_utc = datetime.now(timezone.utc) + timedelta(minutes=5)

    interval_str = f"{inicio_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}/{fin_utc.strftime('%Y-%m-%dT%H:%M:%SZ')}"

    body = {
        "interval": interval_str,
        "groupBy": ["queueId", "wrapUpCode", "dnis"],
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
        dnis_raw = group_meta.get("dnis")
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

        motivo_nombre, macro_cat = resolver_nombre_y_macro_categoria(wid, catalog, srv, q_name)
        pais_nombre = resolver_pais_origen(dnis_raw, q_name)

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
                "pais": pais_nombre,
                "servicio": srv,
                "cola": q_name,
                "canal": canal,
                "wrapup_id": wid or "",
                "motivo_contacto": motivo_nombre,
                "macro_categoria": macro_cat,
                "volumen": handle_count,
                "tiempo_total_sec": handle_sum_sec
            })

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    # Agrupar por fuente, país, servicio, cola, macro_categoria y motivo de contacto
    df_agg = df.groupby(["fuente", "pais", "servicio", "cola", "canal", "macro_categoria", "motivo_contacto"]).agg({
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
                pais_sf = resolver_pais_origen("", raw_q)
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
                    "pais": pais_sf,
                    "servicio": "Agencias & Pyme B2B",
                    "cola": raw_q,
                    "canal": "CHAT",
                    "motivo_contacto": motivo_limpio,
                    "macro_categoria": "🏢 Agencias B2B y Canales Indirectos",
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
                    pais_sf = resolver_pais_origen("", q_ctrl)
                    q_u = q_ctrl.upper()
                    if "EMISION" in q_u or "PAGO" in q_u:
                        m_sf = "💳 Emisiones, Tarifas y Pagos"
                    elif "CORPORATE" in q_u or "PYME" in q_u:
                        m_sf = "🏢 Clientes Corporativos y PYME"
                    elif "REEMBOLSO" in q_u or "DEVOLUCION" in q_u:
                        m_sf = "🔁 Devoluciones y Reembolsos"
                    else:
                        m_sf = "🏢 Agencias B2B y Canales Indirectos"

                    records.append({
                        "fuente": "Salesforce CRM Casos",
                        "pais": pais_sf,
                        "servicio": "BO Agencias B2B",
                        "cola": q_ctrl,
                        "canal": orig.upper() if orig else "CASO",
                        "motivo_contacto": f"Gestión {q_ctrl} ({orig})",
                        "macro_categoria": m_sf,
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


# ── 3.1 EXTRACCIÓN DE TIPOLOGÍAS EN ZENDESK (BACK OFFICE CASOUNICO) ────────────

def obtener_tipologias_zendesk(fecha: str = "hoy") -> pd.DataFrame:
    """
    Extrae las tipologías de casos y tickets gestionados en Zendesk Back Office (CASOUNICO).
    """
    records = []
    if ZD_PROD_PATH.exists():
        try:
            df_zd = pd.read_csv(ZD_PROD_PATH, encoding="utf-8")
            if not df_zd.empty and "Tipo_de_Gestion" in df_zd.columns:
                zd_grp = df_zd.groupby(["grupo", "Tipo_de_Gestion"]).size().reset_index(name="volumen")
                for _, r in zd_grp.iterrows():
                    grupo = str(r["grupo"])
                    tipo_ges = str(r["Tipo_de_Gestion"]).strip()
                    vol = int(r["volumen"])
                    
                    pais_zd = resolver_pais_origen("", grupo)
                    
                    tipo_u = tipo_ges.upper()
                    if any(k in tipo_u for k in ("PASS", "MILLAS", "MILHAS", "SOCIO", "ELITE")):
                        macro_zd = "🌟 LATAM Pass y Fidelización"
                    elif any(k in tipo_u for k in ("EQUIP", "BAG", "MALETA")):
                        macro_zd = "🧳 Equipaje y Ancillaries"
                    elif any(k in tipo_u for k in ("ANULAC", "DEVOLUC", "REEMBOLS", "VOUCHER")):
                        macro_zd = "🔁 Devoluciones y Reembolsos"
                    elif any(k in tipo_u for k in ("COMPRA", "VENTA", "EMIS", "TARIF", "PAGO")):
                        macro_zd = "💳 Emisiones, Tarifas y Pagos"
                    elif any(k in tipo_u for k in ("CAMBIO", "VUELO", "ATRASO", "CANCELAC")):
                        macro_zd = "✈️ Alteraciones y Cambios de Vuelo"
                    elif any(k in tipo_u for k in ("CHECK", "ASIENTO")):
                        macro_zd = "🛫 Check-in y Asientos"
                    elif "SIN TIPIFICAR" in tipo_u or "NULL" in tipo_u:
                        macro_zd = "⚠️ Sin Tipificar / Incidencias"
                    else:
                        macro_zd = "📋 Otros Motivos de Contacto"

                    records.append({
                        "fuente": "Zendesk Back Office",
                        "pais": pais_zd,
                        "servicio": f"BO {grupo.replace(' AMC', '').replace(' SSC', '')}",
                        "cola": grupo,
                        "canal": "TICKET",
                        "wrapup_id": "",
                        "motivo_contacto": tipo_ges,
                        "macro_categoria": macro_zd,
                        "volumen": vol,
                        "aht_segundos": 480.0,
                        "aht_formato": "08:00"
                    })
        except Exception:
            pass

    if not records:
        return pd.DataFrame()

    df_res = pd.DataFrame(records)
    tot_vol = df_res["volumen"].sum()
    df_res["porcentaje"] = ((df_res["volumen"] / tot_vol) * 100.0).round(1) if tot_vol > 0 else 0.0
    return df_res.sort_values(by="volumen", ascending=False).reset_index(drop=True)


def calcular_semaforo_calidad_servicio(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcula para cada servicio el % de llamadas tipificadas exitosamente vs abandonadas en timeout,
    generando una tabla de diagnóstico con semáforo para supervisores y jefes de sala.
    """
    if df is None or df.empty:
        return pd.DataFrame()

    records = []
    for srv, g in df.groupby("servicio"):
        tot = g["volumen"].sum()
        mask_to = (
            g["macro_categoria"].str.contains("Timeout", case=False, na=False) |
            g["motivo_contacto"].str.contains("Timeout", case=False, na=False) |
            g["motivo_contacto"].str.contains("ININ", case=False, na=False)
        )
        timeouts = g.loc[mask_to, "volumen"].sum()
        tipificadas = tot - timeouts
        pct_calidad = (tipificadas / tot * 100.0) if tot > 0 else 0.0
        
        if pct_calidad >= 80.0:
            estado = "🟢 Cumplimiento Alto (≥80%)"
        elif pct_calidad >= 60.0:
            estado = "🟡 Cumplimiento Medio (60-79%)"
        else:
            estado = "🔴 Requiere Foco (<60%)"

        records.append({
            "Servicio / Campaña": srv,
            "Total Contactos": int(tot),
            "Tipificados con Motivo": int(tipificadas),
            "Timeouts (Sin Tipificar)": int(timeouts),
            "% Calidad": f"{pct_calidad:.1f}%",
            "Diagnóstico Operativo": estado
        })

    return pd.DataFrame(records).sort_values(by="Total Contactos", ascending=False).reset_index(drop=True)


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

def generar_reporte_diario_excel(df_genesys: pd.DataFrame, df_salesforce: pd.DataFrame, df_zendesk: pd.DataFrame = None, fecha_label: str = "Hoy") -> bytes:
    """
    Genera un archivo Excel enriquecido con formato corporativo de AlmaContact y LATAM Airlines
    con pestañas de Resumen Ejecutivo, Detalle Genesys Cloud, Detalle Salesforce y Detalle Zendesk.
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
    ws_res.append(["Fecha de Generación:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "", "Fuentes:", "Genesys Cloud • Salesforce B2B • Zendesk", ""])
    ws_res["A3"].font = font_bold
    ws_res["D3"].font = font_bold
    ws_res.append([])

    # Sección Contingencias Detectadas
    frames_combo = [d for d in [df_genesys, df_salesforce, df_zendesk] if d is not None and not d.empty]
    df_combo = pd.concat(frames_combo, ignore_index=True) if frames_combo else pd.DataFrame()
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

    headers_top = ["Ranking", "País / Mercado", "Fuente", "Servicio", "Motivo de Contacto", "Volumen", "% Participación"]
    ws_res.append(headers_top)
    for col_num in range(1, len(headers_top) + 1):
        c = ws_res.cell(row=curr_row, column=col_num)
        c.font = font_header
        c.fill = fill_header
        c.border = thin_border
        c.alignment = Alignment(horizontal="center", vertical="center")
    curr_row += 1

    if not df_combo.empty:
        cols_grp = ["fuente", "servicio", "motivo_contacto"]
        if "pais" in df_combo.columns:
            cols_grp.insert(0, "pais")
        df_top15 = df_combo.groupby(cols_grp)["volumen"].sum().reset_index()
        tot_all = df_top15["volumen"].sum()
        df_top15["pct"] = ((df_top15["volumen"] / tot_all) * 100.0).round(1) if tot_all > 0 else 0.0
        df_top15 = df_top15.sort_values(by="volumen", ascending=False).head(15).reset_index(drop=True)

        for idx, r_t in df_top15.iterrows():
            ws_res.append([
                idx + 1,
                r_t.get("pais", "Multimercado"),
                r_t["fuente"],
                r_t["servicio"],
                r_t["motivo_contacto"],
                int(r_t["volumen"]),
                f"{r_t['pct']}%"
            ])
            for col_num in range(1, len(headers_top) + 1):
                cell = ws_res.cell(row=curr_row, column=col_num)
                cell.font = font_bold if idx < 3 else font_body
                if idx % 2 == 1:
                    cell.fill = fill_zebra
                cell.border = thin_border
                cell.alignment = Alignment(horizontal="center" if col_num in [1, 2, 3, 6, 7] else "left", vertical="center")
            curr_row += 1

    # Ajustar ancho de columnas Hoja 1
    for col in ws_res.columns:
        max_len = max(len(str(cell.value or "")) for cell in col)
        col_letter = get_column_letter(col[0].column)
        ws_res.column_dimensions[col_letter].width = max(max_len + 3, 12)

    # ── HOJA 2: DETALLE GENESYS CLOUD ──
    if df_genesys is not None and not df_genesys.empty:
        ws_gen = wb.create_sheet(title="Genesys Cloud (Voz & WPP)")
        ws_gen.views.sheetView[0].showGridLines = True

        cols_gen = ["País / Mercado", "Servicio", "Cola Genesys", "Canal", "Macro-Categoría", "Motivo de Contacto (Wrap-Up)", "Volumen", "% Participación", "AHT (MM:SS)"]
        ws_gen.append(cols_gen)
        for col_num in range(1, len(cols_gen) + 1):
            c = ws_gen.cell(row=1, column=col_num)
            c.font = font_header
            c.fill = fill_header
            c.alignment = Alignment(horizontal="center", vertical="center")

        for r_idx, row in df_genesys.iterrows():
            ws_gen.append([
                row.get("pais", "Multimercado"),
                row["servicio"],
                row["cola"],
                row["canal"],
                row.get("macro_categoria", "General"),
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
                cell.alignment = Alignment(horizontal="center" if col_num in [1, 4, 5, 7, 8, 9] else "left", vertical="center")

        for col in ws_gen.columns:
            max_len = max(len(str(cell.value or "")) for cell in col)
            col_letter = get_column_letter(col[0].column)
            ws_gen.column_dimensions[col_letter].width = max(max_len + 3, 14)

    # ── HOJA 3: DETALLE SALESFORCE ──
    if df_salesforce is not None and not df_salesforce.empty:
        ws_sf = wb.create_sheet(title="Salesforce (Chat & Casos)")
        ws_sf.views.sheetView[0].showGridLines = True

        cols_sf = ["País / Mercado", "Fuente Salesforce", "Servicio", "Cola / Origen", "Canal", "Macro-Categoría", "Motivo / Tipología", "Volumen / Espera", "% Participación"]
        ws_sf.append(cols_sf)
        for col_num in range(1, len(cols_sf) + 1):
            c = ws_sf.cell(row=1, column=col_num)
            c.font = font_header
            c.fill = fill_header
            c.alignment = Alignment(horizontal="center", vertical="center")

        for r_idx, row in df_salesforce.iterrows():
            ws_sf.append([
                row.get("pais", "Multimercado"),
                row["fuente"],
                row["servicio"],
                row["cola"],
                row["canal"],
                row.get("macro_categoria", "General"),
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
                cell.alignment = Alignment(horizontal="center" if col_num in [1, 5, 6, 8, 9] else "left", vertical="center")

        for col in ws_sf.columns:
            max_len = max(len(str(cell.value or "")) for cell in col)
            col_letter = get_column_letter(col[0].column)
            ws_sf.column_dimensions[col_letter].width = max(max_len + 3, 14)

    # ── HOJA 4: DETALLE ZENDESK CASOUNICO ──
    if df_zendesk is not None and not df_zendesk.empty:
        ws_zd = wb.create_sheet(title="Zendesk Back Office")
        ws_zd.views.sheetView[0].showGridLines = True

        cols_zd = ["País / Mercado", "Fuente Zendesk", "Servicio", "Grupo / Cola", "Canal", "Macro-Categoría", "Motivo / Tipología", "Volumen Casos", "% Participación"]
        ws_zd.append(cols_zd)
        for col_num in range(1, len(cols_zd) + 1):
            c = ws_zd.cell(row=1, column=col_num)
            c.font = font_header
            c.fill = fill_header
            c.alignment = Alignment(horizontal="center", vertical="center")

        for r_idx, row in df_zendesk.iterrows():
            ws_zd.append([
                row.get("pais", "Multimercado"),
                row["fuente"],
                row["servicio"],
                row["cola"],
                row["canal"],
                row.get("macro_categoria", "General"),
                row["motivo_contacto"],
                int(row["volumen"]),
                f"{row['porcentaje']}%"
            ])
            row_num = r_idx + 2
            for col_num in range(1, len(cols_zd) + 1):
                cell = ws_zd.cell(row=row_num, column=col_num)
                cell.font = font_body
                if r_idx % 2 == 1:
                    cell.fill = fill_zebra
                cell.border = thin_border
                cell.alignment = Alignment(horizontal="center" if col_num in [1, 5, 6, 8, 9] else "left", vertical="center")

        for col in ws_zd.columns:
            max_len = max(len(str(cell.value or "")) for cell in col)
            col_letter = get_column_letter(col[0].column)
            ws_zd.column_dimensions[col_letter].width = max(max_len + 3, 14)

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
                    <span style="color: #4ade80; font-size: 12px; font-weight: 600;">Genesys Cloud (Voz/WPP) • Salesforce • Zendesk BO</span>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    # Filtros Superiores
    f_c1, f_c2, f_c3, f_c4 = st.columns([1.3, 1.1, 1.4, 1.0])
    with f_c1:
        fuente_sel = st.selectbox(
            "🌐 Fuente de Datos",
            [
                "Todas las Fuentes (Consolidado Tri-Plataforma)",
                "Genesys Cloud (Voz & WPP)",
                "Salesforce B2B (Chat & Casos)",
                "Zendesk Back Office (Tickets CASOUNICO)"
            ],
            key="tipol_fuente_sel"
        )
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

    with st.spinner("Extrayendo y categorizando motivos de interacción desde Genesys, Salesforce y Zendesk..."):
        df_genesys = pd.DataFrame()
        df_salesforce = pd.DataFrame()
        df_zendesk = pd.DataFrame()

        if fuente_sel in ["Todas las Fuentes (Consolidado Tri-Plataforma)", "Genesys Cloud (Voz & WPP)"]:
            df_genesys = obtener_tipologias_genesys(token, fecha=fecha_str, servicio=srv_query)

        if fuente_sel in ["Todas las Fuentes (Consolidado Tri-Plataforma)", "Salesforce B2B (Chat & Casos)"]:
            df_salesforce = obtener_tipologias_salesforce(fecha=fecha_str)

        if fuente_sel in ["Todas las Fuentes (Consolidado Tri-Plataforma)", "Zendesk Back Office (Tickets CASOUNICO)"]:
            df_zendesk = obtener_tipologias_zendesk(fecha=fecha_str)
            if srv_query and not df_zendesk.empty:
                df_zendesk = df_zendesk[
                    df_zendesk["servicio"].str.contains(srv_query, case=False, na=False) |
                    df_zendesk["cola"].str.contains(srv_query, case=False, na=False)
                ].copy()

        # Unificar
        frames = []
        if not df_genesys.empty:
            frames.append(df_genesys)
        if not df_salesforce.empty:
            frames.append(df_salesforce)
        if not df_zendesk.empty:
            frames.append(df_zendesk)

        df_total = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    if df_total.empty:
        st.warning("⚠️ No se registraron datos de tipología para la fecha y filtros seleccionados.")
        return

    # ── BARRA DE FILTROS DINÁMICOS SUPERIORES ──
    paises_disp = ["Todos los Países / Mercados"] + sorted([p for p in df_total["pais"].dropna().unique() if p])
    macros_disp = ["Todas las Familias / Macro-Categorías"] + sorted([m for m in df_total["macro_categoria"].dropna().unique() if m])

    fc_1, fc_2, fc_3 = st.columns([1.2, 1.3, 1.5], vertical_alignment="bottom")
    with fc_1:
        pais_sel = st.selectbox("🌎 País / Mercado de Origen", paises_disp, key="tipol_pais_sel")
    with fc_2:
        macro_sel = st.selectbox("🏷️ Familia / Macro-Categoría de Negocio", macros_disp, key="tipol_macro_sel")
    with fc_3:
        modo_demanda = st.selectbox(
            "🎯 Lente Analítica de Demanda",
            [
                "🔍 Auditoría Operativa (Timeouts por Servicio)",
                "🔮 Demanda Total Estimada (Reclasificación por Cola)",
                "🚫 Demanda Real Pura (Ocultar Timeouts)"
            ],
            index=0,
            help="🔍 Auditoría: muestra motivos reales + llamadas en timeout catalogadas por servicio.\n🔮 Estimada: reclasifica el 100% del tráfico mapeando timeouts a la especialidad de la cola.\n🚫 Pura: aísla y analiza únicamente interacciones donde el asesor tipificó manualmente.",
            key="tipol_modo_demanda"
        )

    # Filtrar por país si se seleccionó uno específico
    df_base = df_total.copy()
    if pais_sel != "Todos los Países / Mercados":
        df_base = df_base[df_base["pais"] == pais_sel].copy()

    if df_base.empty:
        st.info(f"ℹ️ No se registraron interacciones para {pais_sel} con los filtros actuales.")
        return

    # Recalcular porcentajes globales sobre la vista actual
    tot_vol = df_base["volumen"].sum()
    df_base["porcentaje"] = ((df_base["volumen"] / tot_vol) * 100.0).round(1) if tot_vol > 0 else 0.0

    # ── IDENTIFICAR LLAMADAS SIN TIPIFICAR (TIMEOUTS ACW) VS DEMANDA REAL ──
    mask_sin_tipificar = (
        df_base["macro_categoria"].str.contains("Sin Tipificar", case=False, na=False) |
        df_base["motivo_contacto"].str.contains("Timeout", case=False, na=False) |
        df_base["motivo_contacto"].str.contains("ININ", case=False, na=False)
    )
    vol_sin_tipificar = int(df_base.loc[mask_sin_tipificar, "volumen"].sum())
    vol_tipificadas = tot_vol - vol_sin_tipificar
    pct_calidad_tipif = ((vol_tipificadas / tot_vol) * 100.0).round(1) if tot_vol > 0 else 0.0
    pct_sin_tipificar = ((vol_sin_tipificar / tot_vol) * 100.0).round(1) if tot_vol > 0 else 0.0

    # Top 1 motivo real de cliente (excluyendo llamadas sin tipificar)
    df_clientes = df_base[~mask_sin_tipificar]
    if not df_clientes.empty:
        df_clientes_agg = df_clientes.groupby("motivo_contacto")["volumen"].sum().reset_index().sort_values(by="volumen", ascending=False)
        top1_cliente_row = df_clientes_agg.iloc[0]
        top1_nom = str(top1_cliente_row["motivo_contacto"])
        top1_vol = int(top1_cliente_row["volumen"])
        top1_pct = ((top1_vol / vol_tipificadas) * 100.0).round(1) if vol_tipificadas > 0 else 0.0
    else:
        top1_nom = "-"
        top1_vol = 0
        top1_pct = 0.0

    aht_prom = df_base["aht_segundos"].mean() if "aht_segundos" in df_base.columns else 0.0

    # ── TARJETAS DE KPIS EJECUTIVOS ──
    k_c1, k_c2, k_c3, k_c4 = st.columns(4)
    with k_c1:
        etiqueta_tot = f"Total Interacciones ({pais_sel.split(' ')[-1]})" if pais_sel != "Todos los Países / Mercados" else "Total Interacciones"
        st.metric(etiqueta_tot, f"{tot_vol:,}")
    with k_c2:
        st.metric(
            "Calidad de Tipificación",
            f"{pct_calidad_tipif:.1f}%",
            delta=f"{vol_tipificadas:,} motivos reales",
            help="Porcentaje de interacciones donde el asesor seleccionó efectivamente un motivo de contacto de negocio antes de agotar el tiempo de ACW."
        )
    with k_c3:
        st.metric(
            "Top 1 Motivo Real Cliente",
            f"{top1_nom[:20]}..." if len(top1_nom) > 20 else top1_nom,
            delta=f"{top1_pct}% demanda cliente ({top1_vol:,})" if top1_vol > 0 else None,
            help="Motivo de contacto de negocio más frecuente de los pasajeros (excluyendo llamadas sin tipificar)."
        )
    with k_c4:
        st.metric(
            "⚠️ Sin Tipificar (Timeout ACW)",
            f"{vol_sin_tipificar:,}",
            delta=f"{pct_sin_tipificar:.1f}% sin clasificar",
            delta_color="inverse",
            help="Llamadas donde el asesor dejó expirar el temporizador de ACW (After Call Work) en Genesys sin seleccionar ningún wrap-up code."
        )

    # ── BANNER DE ALERTA DE CONTINGENCIA / PICOS ──
    contingencias = detectar_picos_y_contingencias(df_clientes if not df_clientes.empty else df_base, umbral_pct=25.0)
    if vol_sin_tipificar > 0 and pct_sin_tipificar >= 20.0:
        st.warning(
            f"⏱️ **Oportunidad de Calidad Operativa**: El **{pct_sin_tipificar:.1f}%** de las llamadas ({vol_sin_tipificar:,}) cerraron en **Timeout de ACW** sin que el asesor seleccionara tipología. Esto oculta motivos reales y distorsiona el análisis de demanda.",
            icon="⚠️"
        )
    if contingencias:
        for c in contingencias:
            st.error(
                f"🚨 **{c['nivel_alerta']} en {c['servicio']}**: El motivo de cliente **'{c['motivo']}'** concentra el **{c['porcentaje']}%** del tráfico tipificado ({c['volumen']:,} de {c['total_servicio']:,} interacciones). Posible causa raíz de contingencia o pico de demanda.",
                icon="⚠️"
            )
    elif not (vol_sin_tipificar > 0 and pct_sin_tipificar >= 20.0):
        st.success("🟢 **Operación Estable**: La demanda se encuentra distribuida normalmente entre los motivos habituales sin concentración anómala superior al 25%.", icon="✅")

    # ── SEMÁFORO DE CUMPLIMIENTO OPERATIVO POR SERVICIO ──
    with st.expander("📊 Semáforo de Cumplimiento de Tipificación por Servicio (Auditoría de Jefatura y Coaching)", expanded=False):
        st.markdown(
            "Este semáforo audita la disciplina de tipificación por campaña, contrastando llamadas cerradas con wrap-up code vs aquellas abandonadas en timeout de ACW. "
            "Permite a supervisores y monitores de calidad focalizar refuerzos y coaching en los equipos con mayor fuga de tipificación."
        )
        df_semaforo = calcular_semaforo_calidad_servicio(df_base)
        if not df_semaforo.empty:
            st.dataframe(df_semaforo, use_container_width=True, hide_index=True)
        else:
            st.info("No hay datos disponibles para calcular el semáforo.")

    st.markdown("---")

    # ── APLICAR LENTE DE DEMANDA SELECCIONADA PARA GRÁFICOS ──
    df_graficos = df_base.copy()
    if modo_demanda == "🚫 Demanda Real Pura (Ocultar Timeouts)":
        df_graficos = df_graficos[~mask_sin_tipificar].copy()
    elif modo_demanda == "🔮 Demanda Total Estimada (Reclasificación por Cola)":
        def _reclasificar_fila(row):
            is_to = (
                "Sin Tipificar" in str(row.get("macro_categoria", "")) or
                "Timeout" in str(row.get("motivo_contacto", "")) or
                "ININ" in str(row.get("motivo_contacto", ""))
            )
            if is_to:
                srv_txt = str(row.get("servicio", "")).upper()
                cola_txt = str(row.get("cola", "")).upper()
                if "EQUIP" in srv_txt or "EQUIP" in cola_txt:
                    row["macro_categoria"] = "🧳 Equipaje y Ancillaries"
                    row["motivo_contacto"] = "Demanda Estimada: Equipajes (Vía Cola)"
                elif "VENTA" in srv_txt or "VENTA" in cola_txt:
                    row["macro_categoria"] = "💳 Emisiones, Tarifas y Pagos"
                    row["motivo_contacto"] = "Demanda Estimada: Ventas y Tarifas (Vía Cola)"
                elif any(k in srv_txt or k in cola_txt for k in ("FFP", "DT", "PASS", "ELITE")):
                    row["macro_categoria"] = "🌟 LATAM Pass y Fidelización"
                    row["motivo_contacto"] = "Demanda Estimada: LATAM Pass (Vía Cola)"
                elif "WPP" in srv_txt or "WHATSAPP" in cola_txt:
                    row["macro_categoria"] = "📱 Canales Digitales y WhatsApp"
                    row["motivo_contacto"] = "Demanda Estimada: Atención WhatsApp (Vía Cola)"
                elif any(k in srv_txt or k in cola_txt for k in ("B2B", "AGENC", "CORP")):
                    row["macro_categoria"] = "🏢 Agencias B2B / Canales Indirectos"
                    row["motivo_contacto"] = "Demanda Estimada: Soporte B2B (Vía Cola)"
                elif "LUA" in srv_txt or "LUA" in cola_txt:
                    row["macro_categoria"] = "✈️ Cambios y Servicio al Pasajero"
                    row["motivo_contacto"] = "Demanda Estimada: Línea Única LUA (Vía Cola)"
                else:
                    row["macro_categoria"] = "📋 Tráfico General Atribuido"
                    row["motivo_contacto"] = f"Demanda Estimada: {row.get('servicio', 'General')} (Vía Cola)"
            return row

        df_graficos = df_graficos.apply(_reclasificar_fila, axis=1)

    if macro_sel != "Todas las Familias / Macro-Categorías":
        df_graficos = df_graficos[df_graficos["macro_categoria"] == macro_sel]

    # ── GRÁFICOS INTERACTIVOS (PARETO Y MACRO-CATEGORÍAS) ──
    g_c1, g_c2 = st.columns([1.4, 1.1])

    with g_c1:
        if modo_demanda == "🚫 Demanda Real Pura (Ocultar Timeouts)":
            subtit_bar = "📊 Top 10 Motivos Reales de Clientes (Solo Tipificados)"
        elif modo_demanda == "🔮 Demanda Total Estimada (Reclasificación por Cola)":
            subtit_bar = "📊 Top 10 Motivos de Demanda Estimada (100% Tráfico)"
        else:
            subtit_bar = "📊 Top 10 Motivos de Contacto (Auditoría con Timeouts)"
        st.subheader(subtit_bar)
        if not df_graficos.empty:
            df_top10 = df_graficos.groupby("motivo_contacto")["volumen"].sum().reset_index().sort_values(by="volumen", ascending=True).tail(10)
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
                height=390,
                margin=dict(l=10, r=20, t=10, b=10),
                coloraxis_showscale=False,
                font=dict(size=11)
            )
            fig_bar.update_traces(textposition="outside")
            st.plotly_chart(fig_bar, use_container_width=True)
        else:
            st.info("No hay datos para mostrar con los filtros aplicados.")

    with g_c2:
        st.subheader("🍩 Macro-Familias de Demanda")
        if not df_graficos.empty:
            df_macro_pie = df_graficos.groupby("macro_categoria")["volumen"].sum().reset_index().sort_values(by="volumen", ascending=False)
            fig_pie = px.pie(
                df_macro_pie,
                names="macro_categoria",
                values="volumen",
                hole=0.45,
                color_discrete_sequence=px.colors.qualitative.Prism
            )
            fig_pie.update_layout(
                height=390,
                margin=dict(l=10, r=10, t=10, b=10),
                legend=dict(orientation="h", yanchor="bottom", y=-0.35, xanchor="center", x=0.5, font=dict(size=10))
            )
            fig_pie.update_traces(textinfo="percent", hoverinfo="label+value+percent")
            st.plotly_chart(fig_pie, use_container_width=True)
        else:
            st.info("No hay datos para mostrar.")

    # ── VISTA CRUZADA: PAÍS DE ORIGEN VS CASUÍSTICA DE DEMANDA ──
    st.markdown("---")
    st.subheader("🗺️ Radiografía Cruzada: País de Origen vs. Casuística de Demanda")
    st.caption("Permite identificar en un solo panel de qué país provienen las llamadas y cuáles son las mayores casuísticas y afectaciones por mercado.")

    rx_c1, rx_c2 = st.columns([1.2, 1.8])
    with rx_c1:
        st.markdown("##### 🏆 Ranking de Demanda por País / Mercado")
        df_pais_agg = df_graficos.groupby("pais")["volumen"].sum().reset_index().sort_values(by="volumen", ascending=True)
        if not df_pais_agg.empty:
            fig_pais = px.bar(
                df_pais_agg,
                x="volumen",
                y="pais",
                orientation="h",
                color="volumen",
                color_continuous_scale="Blues",
                text="volumen",
                labels={"volumen": "Interacciones", "pais": "País / Mercado"}
            )
            fig_pais.update_layout(
                height=350,
                margin=dict(l=10, r=20, t=10, b=10),
                coloraxis_showscale=False,
                font=dict(size=11)
            )
            fig_pais.update_traces(textposition="outside")
            st.plotly_chart(fig_pais, use_container_width=True)
        else:
            st.info("Sin datos de país disponibles.")

    with rx_c2:
        st.markdown("##### 🧭 Concentración Cruzada (País ➔ Familia ➔ Motivo)")
        if not df_graficos.empty:
            fig_tree_geo = px.treemap(
                df_graficos.head(50),
                path=["pais", "macro_categoria", "motivo_contacto"],
                values="volumen",
                color="volumen",
                color_continuous_scale="Purples"
            )
            fig_tree_geo.update_layout(
                height=350,
                margin=dict(l=10, r=10, t=10, b=10),
                coloraxis_showscale=False
            )
            st.plotly_chart(fig_tree_geo, use_container_width=True)
        else:
            st.info("Sin datos de concentración.")

    # ── TABLA DE DETALLE Y EXPORTADOR ──
    st.markdown("---")
    st.subheader("📋 Detalle Exhaustivo de Tipologías y Motivos")

    d_c1, d_c2 = st.columns([2.5, 1.0], vertical_alignment="bottom")
    with d_c1:
        filtro_txt = st.text_input("🔍 Buscar motivo, país, macro-categoría o cola...", placeholder="Ej. Chile, Colombia, Pago, Check-in, Equipaje, Vuelo...", key="tipol_txt_search")
    with d_c2:
        excel_bytes = generar_reporte_diario_excel(df_genesys, df_salesforce, df_zendesk=df_zendesk, fecha_label=fecha_str)
        st.download_button(
            label="📥 Descargar Reporte Diario (Excel)",
            data=excel_bytes,
            file_name=f"Reporte_Tipologias_LATAM_{fecha_str}_{datetime.now().strftime('%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            type="primary"
        )

    df_mostrar = df_graficos.copy()
    if filtro_txt:
        df_mostrar = df_mostrar[
            df_mostrar["motivo_contacto"].str.contains(filtro_txt, case=False, na=False) |
            df_mostrar["pais"].str.contains(filtro_txt, case=False, na=False) |
            df_mostrar["macro_categoria"].str.contains(filtro_txt, case=False, na=False) |
            df_mostrar["cola"].str.contains(filtro_txt, case=False, na=False) |
            df_mostrar["servicio"].str.contains(filtro_txt, case=False, na=False)
        ]

    cols_ver = ["fuente", "pais", "servicio", "cola", "canal", "macro_categoria", "motivo_contacto", "volumen", "porcentaje", "aht_formato"]
    cols_existentes = [c for c in cols_ver if c in df_mostrar.columns]
    
    st.dataframe(
        df_mostrar[cols_existentes].rename(columns={
            "fuente": "Fuente",
            "pais": "País / Mercado",
            "servicio": "Servicio",
            "cola": "Cola / Canal",
            "canal": "Tipo",
            "macro_categoria": "Macro-Categoría",
            "motivo_contacto": "Motivo de Contacto / Wrap-Up",
            "volumen": "Volumen",
            "porcentaje": "% Demanda",
            "aht_formato": "AHT (MM:SS)"
        }),
        use_container_width=True,
        hide_index=True,
        height=420
    )
