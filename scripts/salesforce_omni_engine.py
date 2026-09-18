"""
Motor Analítico de Presencia Omni-Channel Salesforce (B2B Chat & Mensajería)
Proyecto 4DX — AMC / LATAM Airlines
Autor: Antigravity / Carlos Murillo

Funcionalidades:
1. Ingesta y normalización del reporte de Presencia Omni-Channel (UserServicePresence).
2. Conversión horaria calibrada: UTC-3 (Chile Org Default) -> UTC-5 (Colombia) = -2 horas.
3. Emparejamiento de alta precisión de nombres de asesores Omni contra BPs de la malla de turnos.
4. Extracción de eventos de disponibilidad, atención y pausas reglamentarias (On_Break).
5. Cálculo de hora primer login, última desconexión, tiempo efectivo y tramos de descanso.
6. Exportación para integración unificada con adherencia_pausas_engine y adherencia_v2_engine.
"""

import os
import sys
import json
import sqlite3
import unicodedata
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

try:
    import streamlit as st
    cache_data_omni = st.cache_data(ttl=300, show_spinner=False)
except Exception:
    def cache_data_omni(fn):
        return fn


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.normpath(os.path.join(BASE_DIR, ".."))
DATA_SF_DIR = os.path.join(PROJECT_DIR, "data", "salesforce")
HISTORICO_CSV = os.path.join(DATA_SF_DIR, "omni_presencia_historico.csv")
DOWNLOADED_CSV = os.path.join(DATA_SF_DIR, "omni_presencia_downloaded.csv")
RESUMEN_PKL = os.path.join(DATA_SF_DIR, "omni_presencia_resumen.pkl")
DB_PATH = os.path.join(PROJECT_DIR, "data", "presencia.db")
LIVE_DB_PATH = os.path.join(PROJECT_DIR, "data", "salesforce_live.db")


def _normalizar_tokens(texto: str) -> set:
    """Normaliza un texto quitando tildes y caracteres especiales, retornando un conjunto de tokens."""
    if not texto or pd.isna(texto):
        return set()
    clean = ''.join(c for c in unicodedata.normalize('NFD', str(texto).upper()) if unicodedata.category(c) != 'Mn')
    for char in ['-', '.', ',', '/', '(', ')', '_']:
        clean = clean.replace(char, ' ')
    tokens = set(clean.split())
    # Excluir palabras vacías comunes
    stopwords = {"DE", "DEL", "LA", "LAS", "LOS", "Y", "EL", "SAN", "AMC"}
    return tokens - stopwords


def obtener_mapeo_omni_a_bp() -> dict:
    """
    Construye el mapa { nombre_omni: (bp, nombre_agente_turno, servicio) }
    cruzando los nombres únicos de Salesforce con la base de datos de turnos y maestro B2B.
    """
    conn = None
    turnos_tokens = {}
    try:
        conn = sqlite3.connect(DB_PATH)
        turnos = pd.read_sql_query(
            "SELECT DISTINCT bp, nombre_agente, servicio FROM turnos_detallados WHERE bp IS NOT NULL AND trim(bp) != ''",
            conn
        )
        for _, row in turnos.iterrows():
            bp = str(row["bp"]).strip()
            nom = str(row["nombre_agente"]).strip()
            srv = str(row.get("servicio", "")).strip()
            toks = _normalizar_tokens(nom)
            if len(toks) >= 2:
                turnos_tokens[bp] = (nom, srv, toks)
    except Exception as e:
        print(f"[!] Error leyendo turnos para mapeo Omni: {e}")
    finally:
        if conn:
            conn.close()

    # Cargar maestro asesores B2B adicional
    maestro_path = os.path.join(DATA_SF_DIR, "maestro_asesores_b2b.json")
    if os.path.exists(maestro_path):
        try:
            with open(maestro_path, "r", encoding="utf-8") as f:
                maestro = json.load(f)
            for k, v in maestro.items():
                bp = str(v.get("bp", "")).strip()
                nom = str(v.get("nombre_completo", "")).strip()
                srv = str(v.get("servicio", "")).strip()
                if bp and bp != "-" and bp not in turnos_tokens:
                    toks = _normalizar_tokens(nom)
                    if len(toks) >= 2:
                        turnos_tokens[bp] = (nom, srv, toks)
        except Exception:
            pass

    # Leer usuarios de Omni
    csv_file = DOWNLOADED_CSV if os.path.exists(DOWNLOADED_CSV) else HISTORICO_CSV
    if not os.path.exists(csv_file):
        return {}

    try:
        df_users = pd.read_csv(csv_file, sep=';', encoding='latin-1', usecols=[0], nrows=100000)
        c_user = df_users.columns[0]
        omni_users = df_users[c_user].dropna().unique()
    except Exception:
        return {}

    mapping = {}
    for u in omni_users:
        u_toks = _normalizar_tokens(u)
        if len(u_toks) < 2:
            continue
        best_bp = None
        best_score = 0.0
        best_nom = ""
        best_srv = ""

        for bp, (nom, srv, t_toks) in turnos_tokens.items():
            inter = u_toks.intersection(t_toks)
            if len(inter) >= 2:
                score = len(inter) / max(len(u_toks), len(t_toks))
                # Priorizar coincidencias con servicios de CHAT o B2B
                if any(k in srv.upper() for k in ["CHAT", "AGY", "B2B", "CORPORATE", "PYME", "REMISION"]):
                    score += 0.15

                if score > best_score and score >= 0.50:
                    best_score = score
                    best_bp = bp
                    best_nom = nom
                    best_srv = srv

        if best_bp:
            mapping[u] = {
                "bp": best_bp,
                "nombre_turno": best_nom,
                "servicio": best_srv,
                "confianza": round(min(best_score, 1.0), 2)
            }

    return mapping


@cache_data_omni
def procesar_omni_presencia_completa(forzar: bool = False) -> pd.DataFrame:
    """
    Procesa el archivo completo de presencia Omni-Channel, convirtiendo a hora Colombia
    y calculando los tramos y resumen diario por BP.
    Retorna DataFrame con resumen por (fecha, bp).
    """
    if not forzar and os.path.exists(RESUMEN_PKL):
        try:
            return pd.read_pickle(RESUMEN_PKL)
        except Exception:
            pass

    csv_file = DOWNLOADED_CSV if os.path.exists(DOWNLOADED_CSV) else HISTORICO_CSV
    if not os.path.exists(csv_file):
        print(f"[!] Archivo de presencia Omni no encontrado en: {csv_file}")
        return pd.DataFrame()

    print(f"[*] Procesando presencia Omni-Channel desde: {csv_file}")
    df_raw = pd.read_csv(csv_file, sep=';', encoding='latin-1')
    if df_raw.empty or len(df_raw.columns) < 5:
        return pd.DataFrame()

    c_user = df_raw.columns[0]
    c_ini = df_raw.columns[1]
    c_fin = df_raw.columns[2]
    c_dur = df_raw.columns[3]
    c_status = df_raw.columns[4]

    # 1. Mapeo a BPs
    mapa_bp = obtener_mapeo_omni_a_bp()
    df_raw["bp"] = df_raw[c_user].map(lambda x: mapa_bp.get(x, {}).get("bp"))
    df_raw["nombre_turno"] = df_raw[c_user].map(lambda x: mapa_bp.get(x, {}).get("nombre_turno"))
    df_raw["servicio"] = df_raw[c_user].map(lambda x: mapa_bp.get(x, {}).get("servicio"))

    # Filtrar sólo los que pertenecen a nuestra operación
    df_valid = df_raw[df_raw["bp"].notna()].copy()
    if df_valid.empty:
        print("[!] No se encontraron registros con BP asociado en Omni-Channel.")
        return pd.DataFrame()

    # 2. Conversión horaria: UTC-3 (Chile) -> UTC-5 (Colombia) = -2 horas
    print("[*] Aplicando conversión horaria a Hora Colombia (-2h)...")
    df_valid["dt_ini_col"] = pd.to_datetime(df_valid[c_ini], format='%d/%m/%Y, %H:%M', errors='coerce') - pd.Timedelta(hours=2)
    df_valid["dt_fin_col"] = pd.to_datetime(df_valid[c_fin], format='%d/%m/%Y, %H:%M', errors='coerce') - pd.Timedelta(hours=2)
    
    # Descartar filas sin fecha válida
    df_valid = df_valid[df_valid["dt_ini_col"].notna()].copy()
    df_valid["fecha_col"] = df_valid["dt_ini_col"].dt.strftime('%Y-%m-%d')
    df_valid["duracion_seg"] = pd.to_numeric(df_valid[c_dur], errors='coerce').fillna(0)
    df_valid["estado"] = df_valid[c_status].fillna("")

    # 3. Agrupación por (bp, fecha_col)
    resumen_filas = []
    grupos = df_valid.groupby(["fecha_col", "bp"])

    for (f_col, bp), sub in grupos:
        nombre_omni = sub[c_user].iloc[0]
        nombre_turno = sub["nombre_turno"].iloc[0] or nombre_omni
        servicio = sub["servicio"].iloc[0] or "B2B Chat"

        # Ordenar cronológicamente
        sub = sub.sort_values(by="dt_ini_col")

        # Primer login y última salida
        h_inicio_omni = sub["dt_ini_col"].min().strftime("%H:%M")
        h_fin_omni = sub["dt_fin_col"].max().strftime("%H:%M") if sub["dt_fin_col"].notna().any() else sub["dt_ini_col"].max().strftime("%H:%M")

        # Calcular tiempo productivo vs pausas
        sub_productivo = sub[sub["estado"].str.contains("Available|Busy", case=False, na=False)]
        minutos_conexion = round(sub_productivo["duracion_seg"].sum() / 60.0, 1)

        # Pausas reglamentarias (On_Break)
        sub_breaks = sub[sub["estado"].str.contains("Break", case=False, na=False)]
        minutos_pausa = round(sub_breaks["duracion_seg"].sum() / 60.0, 1)

        tramos_pausa = []
        for _, b_row in sub_breaks.iterrows():
            dur_m = round(b_row["duracion_seg"] / 60.0, 1)
            if dur_m >= 1.0:  # Ignorar micro-estados menores a 1 min
                ini_str = b_row["dt_ini_col"].strftime("%H:%M")
                fin_str = b_row["dt_fin_col"].strftime("%H:%M") if pd.notna(b_row["dt_fin_col"]) else ini_str
                tramos_pausa.append({
                    "inicio": ini_str,
                    "fin": fin_str,
                    "duracion_min": dur_m
                })

        resumen_filas.append({
            "fecha": f_col,
            "bp": str(bp),
            "nombre_omni": nombre_omni,
            "nombre_agente": nombre_turno,
            "servicio": servicio,
            "h_inicio_omni": h_inicio_omni,
            "h_fin_omni": h_fin_omni,
            "minutos_conexion": minutos_conexion,
            "minutos_pausa": minutos_pausa,
            "tramos_pausas": tramos_pausa,
            "eventos_count": len(sub)
        })

    df_resumen = pd.DataFrame(resumen_filas)
    os.makedirs(DATA_SF_DIR, exist_ok=True)
    df_resumen.to_pickle(RESUMEN_PKL)
    print(f"[✓] Presencia Omni procesada: {len(df_resumen):,} registros diarios guardados en {RESUMEN_PKL}")

    # Guardar en SQLite Live DB si está disponible
    try:
        conn_live = sqlite3.connect(LIVE_DB_PATH)
        df_save = df_resumen.copy()
        df_save["tramos_pausas_json"] = df_save["tramos_pausas"].apply(json.dumps)
        df_save.drop(columns=["tramos_pausas"]).to_sql("omni_presencia_resumen", conn_live, if_exists="replace", index=False)
        conn_live.close()
    except Exception as e:
        print(f"[*] Nota: no se guardó en sqlite live: {e}")

    return df_resumen


@cache_data_omni
def obtener_presencia_omni_por_fecha(fecha: str) -> dict:
    """
    Retorna mapa indexado por BP con la presencia de Omni-Channel para la fecha (YYYY-MM-DD):
    {
        bp: {
            'bp': str,
            'nombre': str,
            'h_inicio_omni': str (HH:MM),
            'h_fin_omni': str (HH:MM),
            'minutos_conexion': float,
            'minutos_pausa': float,
            'tramos_pausas': list[dict],
            'eventos_count': int
        }
    }
    """
    df_res = procesar_omni_presencia_completa(forzar=False)
    if df_res.empty or "fecha" not in df_res.columns:
        return {}

    sub = df_res[df_res["fecha"] == fecha]
    if sub.empty:
        return {}

    out = {}
    for _, r in sub.iterrows():
        bp = str(r["bp"]).strip()
        out[bp] = {
            "bp": bp,
            "nombre": r["nombre_agente"],
            "servicio": r["servicio"],
            "h_inicio_omni": r["h_inicio_omni"],
            "h_fin_omni": r["h_fin_omni"],
            "minutos_conexion": float(r["minutos_conexion"]),
            "minutos_pausa": float(r["minutos_pausa"]),
            "tramos_pausas": r["tramos_pausas"],
            "eventos_count": int(r["eventos_count"])
        }
    return out


if __name__ == "__main__":
    print("=" * 60)
    print("PROCESADOR DE PRESENCIA OMNI-CHANNEL SALESFORCE")
    print("=" * 60)
    df = procesar_omni_presencia_completa(forzar=True)
    if not df.empty:
        print(f"Fechas cubiertas: {df['fecha'].min()} a {df['fecha'].max()}")
        print(f"Total BPs únicos procesados: {df['bp'].nunique()}")
        # Muestra de la fecha más reciente
        ult_fecha = df['fecha'].max()
        print(f"\nMuestra de presencia para {ult_fecha}:")
        sample = df[df['fecha'] == ult_fecha].head(5)
        for _, r in sample.iterrows():
            print(f"  BP {r['bp']} ({r['nombre_agente']}): {r['h_inicio_omni']} - {r['h_fin_omni']} | Conexión: {r['minutos_conexion']}m | Pausas: {len(r['tramos_pausas'])} ({r['minutos_pausa']}m)")
