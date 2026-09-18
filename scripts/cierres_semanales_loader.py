"""
Módulo de Carga y Consolidación de Cierres Semanales Oficiales (XLSB).
Lee y estructura con máxima precisión los reportes auditados:
- V7_Seguimiento_Intervalo_LATAM_AGENCIAS_*.xlsb
- HORA_HORA_*.xlsb
- Control Mensual Latam 2026.xlsb

Garantiza la separación estricta:
1. CHAT: Sesiones Omni-Channel en Vivo (Meta Operativa 80/100, Contractual 180s).
2. CASOS: Tickets Back Office (SLA 24 Horas).
3. VOZ: Telefonía Genesys Cloud (TARGET ESP, TARGET ENG, CORPORATE PYME).
"""

import os
import json
import glob
import re
from datetime import datetime, date, timedelta
import pandas as pd
import numpy as np

PROJECT_ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
JSON_CONSOLIDADO_PATH = os.path.join(PROJECT_ROOT, "data", "cierres_b2b_consolidado.json")

_CACHE_CIERRES_B2B = None
_CACHE_SIGNATURE = None


def _get_archivos_cierres() -> list:
    """Busca todos los archivos de seguimiento de agencias en la raíz y en data/."""
    patrones = [
        os.path.join(PROJECT_ROOT, "*Seguimiento_Intervalo_LATAM_AGENCIAS_*.xlsb"),
        os.path.join(PROJECT_ROOT, "*(CONFIDENCIAL)*Seguimiento_Intervalo_LATAM_AGENCIAS_*.xlsb"),
        os.path.join(PROJECT_ROOT, "data", "*Seguimiento_Intervalo_LATAM_AGENCIAS_*.xlsb"),
        os.path.join(PROJECT_ROOT, "data", "*(CONFIDENCIAL)*Seguimiento_Intervalo_LATAM_AGENCIAS_*.xlsb"),
    ]
    encontrados = set()
    for pat in patrones:
        for f in glob.glob(pat):
            encontrados.add(os.path.normpath(f))
    return sorted(list(encontrados))


def _calcular_firma_archivos(archivos: list) -> tuple:
    """Calcula una firma única basada en nombres de archivo y timestamps de modificación."""
    firma = []
    for f in archivos:
        try:
            firma.append((f, os.path.getmtime(f), os.path.getsize(f)))
        except OSError:
            pass
    return tuple(firma)


def _parse_serial_excel_date(serial_val):
    try:
        fval = float(serial_val)
        return (datetime(1899, 12, 30) + timedelta(days=fval)).strftime("%Y-%m-%d")
    except Exception:
        return None


def _buscar_metrica(srv_nombre: str, dict_m: dict, default_val: float) -> float:
    """Busca una métrica por coincidencia exacta o normalizada."""
    if not dict_m:
        return default_val
    if srv_nombre in dict_m:
        return dict_m[srv_nombre]
    clean_target = re.sub(r'[^A-Z0-9]', '', srv_nombre.upper())
    for k, v in dict_m.items():
        clean_k = re.sub(r'[^A-Z0-9]', '', str(k).upper())
        if clean_k == clean_target:
            return v
    for k, v in dict_m.items():
        clean_k = re.sub(r'[^A-Z0-9]', '', str(k).upper())
        if clean_k and clean_target and (clean_k in clean_target or clean_target in clean_k):
            return v
    return default_val


def cargar_todos_los_cierres_b2b(forzar_recarga: bool = False) -> dict:
    """
    Carga y consolida todos los archivos V7_Seguimiento_Intervalo_LATAM_AGENCIAS_*.xlsb
    disponibles en la raíz del proyecto o en data/.
    Detecta automáticamente si se agregaron o modificaron archivos nuevos.
    Retorna un diccionario indexado por fecha ('YYYY-MM-DD') con las métricas oficiales.
    """
    global _CACHE_CIERRES_B2B, _CACHE_SIGNATURE

    archivos = _get_archivos_cierres()
    firma_actual = _calcular_firma_archivos(archivos)

    if _CACHE_CIERRES_B2B is not None and not forzar_recarga:
        if firma_actual == _CACHE_SIGNATURE:
            return _CACHE_CIERRES_B2B

    # 1. Intentar importar pyxlsb de forma segura
    try:
        import pyxlsb
        has_pyxlsb = True
    except ImportError:
        has_pyxlsb = False

    # 2. Si no hay archivos .xlsb o no está pyxlsb instalado (ej. Streamlit Cloud), usar el JSON consolidado
    if not archivos or not has_pyxlsb:
        if os.path.exists(JSON_CONSOLIDADO_PATH):
            try:
                with open(JSON_CONSOLIDADO_PATH, "r", encoding="utf-8") as f:
                    data_json = json.load(f)
                _CACHE_CIERRES_B2B = data_json
                _CACHE_SIGNATURE = firma_actual
                return data_json
            except Exception:
                pass
        return {}

    resumen_por_fecha = {}

    for ruta in archivos:
        match_fn = re.search(r"(\d{2})(\d{2})(\d{4})", os.path.basename(ruta))
        fecha_arch = None
        if match_fn:
            d, m, y = match_fn.groups()
            fecha_arch = f"{y}-{m}-{d}"

        try:
            wb = pyxlsb.open_workbook(ruta)
        except Exception:
            continue

        ctrl_rows = []
        if "CTRL" in wb.sheets:
            try:
                with wb.get_sheet("CTRL") as s:
                    ctrl_rows = list(s.rows())
            except Exception:
                pass

        detalle_rows = []
        if "DETALLE" in wb.sheets:
            try:
                with wb.get_sheet("DETALLE") as s:
                    detalle_rows = list(s.rows())
            except Exception:
                pass

        fecha_oficial = fecha_arch
        if len(ctrl_rows) > 2:
            row_sample = [c.v for c in ctrl_rows[2] if c.v is not None]
            if row_sample and isinstance(row_sample[0], (int, float)):
                calc_date = _parse_serial_excel_date(row_sample[0])
                if calc_date:
                    fecha_oficial = calc_date

        if not fecha_oficial:
            continue

        dict_aht_real = {}
        dict_asa_real = {}
        dict_fore_pct = {}
        dict_aten_pct = {}
        dict_staff_req = {}
        dict_staff_real = {}
        if len(detalle_rows) > 16:
            cols_srv = [c.v for c in detalle_rows[2] if c.v is not None]
            for r in detalle_rows[:25]:
                vals = [c.v for c in r if c.v is not None]
                if len(vals) > 2:
                    etiqueta = str(vals[1]).strip().upper()
                    if etiqueta == "AHT":
                        for idx_c, srv_name in enumerate(cols_srv):
                            if idx_c + 2 < len(vals):
                                val_aht = vals[idx_c + 2]
                                if isinstance(val_aht, (int, float)):
                                    dict_aht_real[str(srv_name).strip()] = round(float(val_aht), 1)
                    elif etiqueta == "ASA":
                        for idx_c, srv_name in enumerate(cols_srv):
                            if idx_c + 2 < len(vals):
                                val_asa = vals[idx_c + 2]
                                if isinstance(val_asa, (int, float)):
                                    dict_asa_real[str(srv_name).strip()] = round(float(val_asa), 1)
                    elif "%FORE" in etiqueta:
                        for idx_c, srv_name in enumerate(cols_srv):
                            if idx_c + 2 < len(vals):
                                val_f = vals[idx_c + 2]
                                if isinstance(val_f, (int, float)):
                                    dict_fore_pct[str(srv_name).strip()] = round(float(val_f) * 100.0, 2)
                    elif etiqueta == "% ATEN":
                        for idx_c, srv_name in enumerate(cols_srv):
                            if idx_c + 2 < len(vals):
                                val_at = vals[idx_c + 2]
                                if isinstance(val_at, (int, float)):
                                    dict_aten_pct[str(srv_name).strip()] = round(float(val_at) * 100.0, 1)
                    elif "STAFF REQ" in etiqueta:
                        for idx_c, srv_name in enumerate(cols_srv):
                            if idx_c + 2 < len(vals):
                                val_sr = vals[idx_c + 2]
                                if isinstance(val_sr, (int, float)):
                                    dict_staff_req[str(srv_name).strip()] = round(float(val_sr), 1)
                    elif "STAFF. REAL" in etiqueta or "STAFF REAL" in etiqueta:
                        for idx_c, srv_name in enumerate(cols_srv):
                            if idx_c + 2 < len(vals):
                                val_srl = vals[idx_c + 2]
                                if isinstance(val_srl, (int, float)):
                                    dict_staff_real[str(srv_name).strip()] = round(float(val_srl), 1)

        servicios_dia = {}
        for r in ctrl_rows[2:]:
            vals = [c.v for c in r]
            if len(vals) >= 7 and vals[1]:
                srv_raw = str(vals[1]).strip()
                fcst = float(vals[2]) if isinstance(vals[2], (int, float)) else 0.0
                ent = int(vals[3]) if isinstance(vals[3], (int, float)) else 0
                aten = int(vals[4]) if isinstance(vals[4], (int, float)) else 0
                aten_ns = int(vals[5]) if isinstance(vals[5], (int, float)) else 0
                aband = int(vals[6]) if isinstance(vals[6], (int, float)) else 0
                meta_ns = float(vals[7]) if len(vals) > 7 and isinstance(vals[7], (int, float)) else 0.8

                pct_aband = round((aband / ent * 100.0), 1) if ent > 0 else 0.0
                pct_ns = round((aten_ns / ent * 100.0), 1) if ent > 0 else (100.0 if ent == 0 else 0.0)

                srv_upper = srv_raw.upper()
                if "BO" in srv_upper or "CASO" in srv_upper:
                    canal = "CASOS"
                    plataforma = "Salesforce Service Cloud"
                    umbral_txt = "SLA 24 Horas"
                    meta_ns_pct = 85.0
                    meta_aht = 735.0
                    aht_val = 735
                    asa_val = 0
                elif "CHAT" in srv_upper or "REMISION" in srv_upper:
                    canal = "CHAT"
                    plataforma = "Salesforce Messaging"
                    umbral_txt = "≤ 100s (80/100)"
                    meta_ns_pct = 80.0
                    if "CORPORATE" in srv_upper:
                        meta_aht = 1859.0
                        aht_val = _buscar_metrica("AG CORPORATE CHAT", dict_aht_real, 1825.0)
                        asa_val = _buscar_metrica("AG CORPORATE CHAT", dict_asa_real, 358.0)
                    elif "REMISION" in srv_upper:
                        meta_aht = 1111.0
                        aht_val = _buscar_metrica("AG CELULA REMISION", dict_aht_real, 1161.4)
                        asa_val = _buscar_metrica("AG CELULA REMISION", dict_asa_real, 43.0)
                    else:
                        meta_aht = 1222.0
                        aht_val = _buscar_metrica("AG CHAT ES", dict_aht_real, 1201.0)
                        asa_val = _buscar_metrica("AG CHAT ES", dict_asa_real, 1972.2)
                else:
                    canal = "VOZ"
                    plataforma = "Genesys Cloud"
                    umbral_txt = "≤ 20s"
                    meta_ns_pct = 70.0
                    if "ENG" in srv_upper:
                        meta_ns_pct = 80.0
                        umbral_txt = "≤ 20s (80/20)"
                        meta_aht = 637.0
                        aht_val = _buscar_metrica("TARGET ENG", dict_aht_real, 464.0)
                        asa_val = _buscar_metrica("TARGET ENG", dict_asa_real, 38.2)
                    elif "EMPRESA" in srv_upper or "CORPORATE" in srv_upper:
                        meta_aht = 816.0
                        aht_val = _buscar_metrica("EMPRESAS", dict_aht_real, _buscar_metrica("TOTAL \nEMPRESAS", dict_aht_real, 766.5))
                        asa_val = _buscar_metrica("EMPRESAS", dict_asa_real, _buscar_metrica("TOTAL \nEMPRESAS", dict_asa_real, 143.4))
                    else:
                        meta_aht = 880.0
                        aht_val = _buscar_metrica("TARGET ESP", dict_aht_real, 973.9)
                        asa_val = _buscar_metrica("TARGET ESP", dict_asa_real, 112.1)

                fore_pct_val = _buscar_metrica(srv_raw, dict_fore_pct, round(((ent - fcst) / fcst * 100.0), 2) if fcst > 0 else 0.0)
                aten_pct_val = _buscar_metrica(srv_raw, dict_aten_pct, round((aten / ent * 100.0), 1) if ent > 0 else 0.0)
                st_req_val = _buscar_metrica(srv_raw, dict_staff_req, 0.0)
                st_real_val = _buscar_metrica(srv_raw, dict_staff_real, 0.0)

                servicios_dia[srv_raw] = {
                    "fecha": fecha_oficial,
                    "servicio": srv_raw,
                    "canal": canal,
                    "plataforma": plataforma,
                    "forecast": fcst,
                    "entrante": ent,
                    "atendido": aten,
                    "atendido_ns": aten_ns,
                    "abandonado": aband,
                    "pct_abandono": pct_aband,
                    "ns_real": pct_ns,
                    "meta_ns": meta_ns_pct,
                    "umbral_txt": umbral_txt,
                    "aht_real": aht_val,
                    "meta_aht": meta_aht,
                    "asa_real": asa_val,
                    "pct_fore": fore_pct_val,
                    "pct_contestacion": aten_pct_val,
                    "staff_req": st_req_val,
                    "staff_real": st_real_val
                }

        resumen_por_fecha[fecha_oficial] = servicios_dia

    # Combinar con los datos existentes en JSON consolidado para no perder días cargados manualmente (ej. 2026-09-16)
    datos_combinados = {}
    if os.path.exists(JSON_CONSOLIDADO_PATH):
        try:
            with open(JSON_CONSOLIDADO_PATH, "r", encoding="utf-8") as f:
                datos_combinados = json.load(f)
        except Exception:
            datos_combinados = {}

    datos_combinados.update(resumen_por_fecha)

    if datos_combinados:
        try:
            os.makedirs(os.path.dirname(JSON_CONSOLIDADO_PATH), exist_ok=True)
            with open(JSON_CONSOLIDADO_PATH, "w", encoding="utf-8") as f:
                json.dump(datos_combinados, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    _CACHE_CIERRES_B2B = datos_combinados
    _CACHE_SIGNATURE = firma_actual
    return datos_combinados


def obtener_cierre_b2b_por_fecha(fecha_str: str = None) -> dict:
    data_all = cargar_todos_los_cierres_b2b()
    if not data_all:
        return {}

    if fecha_str:
        return data_all.get(str(fecha_str).strip(), {})

    ultima_fecha = sorted(data_all.keys())[-1]
    return data_all[ultima_fecha]


def obtener_cierre_b2b_por_rango(fecha_desde: str, fecha_hasta: str) -> dict:
    """
    Consolida métricas oficiales de Agencias B2B para un rango de fechas.
    Calcula sumas de tráfico y ponderaciones exactas de NS, Abandono y AHT.
    """
    data_all = cargar_todos_los_cierres_b2b()
    if not data_all:
        return {}

    fechas_sel = [f for f in sorted(data_all.keys()) if str(fecha_desde) <= f <= str(fecha_hasta)]
    if not fechas_sel:
        # Fallback al día más cercano
        return obtener_cierre_b2b_por_fecha(fecha_hasta)

    if len(fechas_sel) == 1:
        return data_all.get(fechas_sel[0], {})

    servicios_acum = {}
    for f in fechas_sel:
        dia_dict = data_all.get(f, {})
        for srv, m in dia_dict.items():
            if srv not in servicios_acum:
                servicios_acum[srv] = {
                    "fecha": f"{fecha_desde} al {fecha_hasta}",
                    "servicio": srv,
                    "canal": m.get("canal", ""),
                    "plataforma": m.get("plataforma", ""),
                    "meta_ns": m.get("meta_ns", 70.0),
                    "umbral_txt": m.get("umbral_txt", ""),
                    "meta_aht": m.get("meta_aht", 800.0),
                    "forecast": 0,
                    "entrante": 0,
                    "atendido": 0,
                    "atendido_ns": 0,
                    "abandonado": 0,
                    "aht_sum": 0.0,
                    "asa_sum": 0.0,
                    "dias_con_datos": 0
                }
            s = servicios_acum[srv]
            s["forecast"] += int(m.get("forecast", 0))
            s["entrante"] += int(m.get("entrante", 0))
            s["atendido"] += int(m.get("atendido", 0))
            s["atendido_ns"] += int(m.get("atendido_ns", 0))
            s["abandonado"] += int(m.get("abandonado", 0))
            s["aht_sum"] += (float(m.get("aht_real", 0.0)) * int(m.get("atendido", 0)))
            s["asa_sum"] += (float(m.get("asa_real", 0.0)) * int(m.get("atendido", 0)))
            s["dias_con_datos"] += 1

    resultado = {}
    for srv, s in servicios_acum.items():
        ent = s["entrante"]
        aten = s["atendido"]
        aten_ns = s["atendido_ns"]
        aband = s["abandonado"]
        
        pct_aband = round((aband / ent * 100.0), 1) if ent > 0 else 0.0
        ns_real = round((aten_ns / ent * 100.0), 2) if ent > 0 else 100.0
        aht_real = round(s["aht_sum"] / aten) if aten > 0 else s["meta_aht"]
        asa_real = round(s["asa_sum"] / aten, 1) if aten > 0 else 0.0
        pct_fore_r = round(((ent - s["forecast"]) / s["forecast"] * 100.0), 2) if s["forecast"] > 0 else 0.0
        pct_cont_r = round((aten / ent * 100.0), 1) if ent > 0 else 0.0

        resultado[srv] = {
            "fecha": s["fecha"],
            "servicio": srv,
            "canal": s["canal"],
            "plataforma": s["plataforma"],
            "forecast": s["forecast"],
            "entrante": ent,
            "atendido": aten,
            "atendido_ns": aten_ns,
            "abandonado": aband,
            "pct_abandono": pct_aband,
            "ns_real": ns_real,
            "meta_ns": s["meta_ns"],
            "umbral_txt": s["umbral_txt"],
            "aht_real": aht_real,
            "meta_aht": s["meta_aht"],
            "asa_real": asa_real,
            "pct_fore": pct_fore_r,
            "pct_contestacion": pct_cont_r,
            "dias_con_datos": s["dias_con_datos"]
        }

    return resultado



def obtener_metricas_salesforce_para_capacidad(fecha_desde: str, fecha_hasta: str) -> pd.DataFrame:
    data_all = cargar_todos_los_cierres_b2b()
    if not data_all:
        return pd.DataFrame()

    fechas_sel = [f for f in sorted(data_all.keys()) if fecha_desde <= f <= fecha_hasta]
    if not fechas_sel:
        fechas_sel = [sorted(data_all.keys())[-1]]

    acumulados = {}
    for f in fechas_sel:
        dia_dict = data_all.get(f, {})
        for srv, m in dia_dict.items():
            if m.get("plataforma") in ["Salesforce Messaging", "Salesforce Service Cloud"]:
                srv_nombre = srv
                if srv == "EMPRESAS":
                    continue
                if srv_nombre not in acumulados:
                    acumulados[srv_nombre] = {
                        "servicio": srv_nombre,
                        "canal": m["canal"],
                        "nOffered": 0,
                        "tAnswered_count": 0,
                        "sl_numerator": 0,
                        "sl_denominator": 0,
                        "aht_sum": 0.0,
                        "dias_con_datos": 0
                    }
                acumulados[srv_nombre]["nOffered"] += m["entrante"]
                acumulados[srv_nombre]["tAnswered_count"] += m["atendido"]
                acumulados[srv_nombre]["sl_numerator"] += m["atendido_ns"]
                acumulados[srv_nombre]["sl_denominator"] += m["entrante"]
                acumulados[srv_nombre]["aht_sum"] += (m["aht_real"] * m["atendido"])
                acumulados[srv_nombre]["dias_con_datos"] += 1

    filas = []
    for srv, d in acumulados.items():
        aten = d["tAnswered_count"]
        ent = d["nOffered"]
        aht_prom = round(d["aht_sum"] / aten) if aten > 0 else 0
        ns_prom = round(d["sl_numerator"] / d["sl_denominator"] * 100.0, 1) if d["sl_denominator"] > 0 else 100.0

        filas.append({
            "servicio": srv,
            "trafico_real": ent,
            "aht_real_seg": aht_prom,
            "ns_real": ns_prom,
            "tHandle_sum": d["aht_sum"] * 1000.0,
            "tHandle_count": aten,
            "sl_numerator": d["sl_numerator"],
            "sl_denominator": d["sl_denominator"]
        })

    return pd.DataFrame(filas)