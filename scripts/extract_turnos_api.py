"""
Extractor automatizado de turnos operativos y pausas programadas desde la API de Almaverso.
Endpoint: GET http://10.96.16.37:8888/API/GET-EXPORT/SHIFTS?startDate=...&endDate=...

Uso:
    python extract_turnos_api.py                           # Extrae hoy - 1 hasta hoy + 7
    python extract_turnos_api.py --start 2026-09-01 --end 2026-09-17
    python extract_turnos_api.py --hoy                     # Solo el día de hoy
"""

import os
import sys
import argparse
from datetime import datetime, timedelta
from pathlib import Path
import requests
import pandas as pd
from dotenv import load_dotenv, find_dotenv

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

load_dotenv(find_dotenv())
# También intentar cargar .env de Paneles y Dashboard 4dx si existe
env_parent = Path(__file__).resolve().parent.parent.parent / ".env"
if env_parent.exists():
    load_dotenv(env_parent)

from db import get_connection, guardar_turnos, guardar_turnos_detallados, SCHEMA
from jerarquia import load_cedula_a_bp
from config import CLOUD_EXPORT_PATH

BASE_URL = os.getenv("ALMAVERSO_API_URL", "http://10.96.16.37:8888")
USERNAME = os.getenv("AD_USER", "cescobar")
PASSWORD = os.getenv("AD_PASS", "Came#0826")


def get_token(base_url: str = BASE_URL, username: str = USERNAME, password: str = PASSWORD) -> str:
    """Autentica contra Active Directory en Almaverso y obtiene token JWT."""
    url = f"{base_url}/login/ActiveDirectory/"
    resp = requests.post(url, json={"username": username, "password": password}, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    token = data.get("token")
    if not token:
        raise ValueError(f"No se recibió token en respuesta de autenticación: {data}")
    return token


def _clean_time(val):
    """Normaliza cadenas de tiempo a HH:MM:SS o retorna None si no aplica."""
    if pd.isna(val) or val is None:
        return None
    val_str = str(val).strip()
    if not val_str or val_str in ("00:00:00", "0", "nan", "None", "-"):
        return None
    try:
        parts = val_str.split(":")
        if len(parts) >= 2:
            h, m = int(parts[0]), int(parts[1])
            s = int(parts[2]) if len(parts) > 2 else 0
            return f"{h:02d}:{m:02d}:{s:02d}"
    except Exception:
        pass
    try:
        dt = pd.to_datetime(val_str, errors="coerce")
        if pd.notna(dt) and dt.strftime("%H:%M:%S") != "00:00:00":
            return dt.strftime("%H:%M:%S")
    except Exception:
        pass
    return None


def fetch_shifts_range(start_date: str, end_date: str, token: str, base_url: str = BASE_URL) -> list[dict]:
    """Consulta la API de turnos para un rango específico."""
    url = f"{base_url}/API/GET-EXPORT/SHIFTS"
    params = {"startDate": start_date, "endDate": end_date}
    headers = {"Authorization": f"Bearer {token}"}
    
    resp = requests.get(url, headers=headers, params=params, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list):
        return data
    elif isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
        return data["data"]
    return []


def parse_api_shifts(raw_shifts: list[dict], cedula_a_bp: dict) -> tuple[list[dict], list[dict]]:
    """
    Parsea los turnos recibidos de la API en:
    1. rows_turnos (bp, fecha, hora_inicio, hora_fin)
    2. rows_detallados (bp, fecha, documento, nombre_agente, servicio, novedad, pausas, etc.)
    """
    rows_turnos = []
    rows_detallados = []
    vistos = set()

    for r in raw_shifts:
        doc = str(r.get("Documento") or r.get("Documento_Agente") or "").strip()
        fecha = str(r.get("Fecha") or "")[:10]
        h_ini = _clean_time(r.get("Turno_Ini"))
        h_fin = _clean_time(r.get("Turno_Fin"))
        
        if not doc or not fecha:
            continue
            
        bp = cedula_a_bp.get(doc)
        if not bp:
            # Si no está en el mapa por cédula, usar el documento como identificador fallback
            bp = doc

        # Parsear horas programadas
        horas_prog = 0.0
        try:
            h_raw = r.get("Horas_Laboradas")
            if h_raw is not None and str(h_raw).strip() != "":
                horas_prog = float(h_raw)
        except Exception:
            horas_prog = 0.0

        clave = (bp, fecha)
        if clave not in vistos:
            vistos.add(clave)
            if h_ini and h_fin:
                rows_turnos.append({
                    "bp": bp,
                    "fecha": fecha,
                    "hora_inicio": h_ini,
                    "hora_fin": h_fin,
                })

            rows_detallados.append({
                "bp": bp,
                "fecha": fecha,
                "documento": doc,
                "nombre_agente": str(r.get("Nombre_Agente") or "").strip(),
                "servicio": str(r.get("Servicio") or r.get("Cliente_Area") or "").strip(),
                "novedad": str(r.get("Novedad") or "").strip(),
                "horas_programadas": horas_prog,
                "turno_ini": h_ini,
                "turno_fin": h_fin,
                "dialogo_ini": _clean_time(r.get("Dialogo_Ini")),
                "dialogo_fin": _clean_time(r.get("Dialogo_Fin")),
                "des_1_ini": _clean_time(r.get("Des_1_Ini")),
                "des_1_fin": _clean_time(r.get("Des_1_Fin")),
                "des_2_ini": _clean_time(r.get("Des_2_Ini")),
                "des_2_fin": _clean_time(r.get("Des_2_Fin")),
                "des_3_ini": _clean_time(r.get("Des_3_Ini")),
                "des_3_fin": _clean_time(r.get("Des_3_Fin")),
                "lunch_ini": _clean_time(r.get("Lunch_Ini")),
                "lunch_fin": _clean_time(r.get("Lunch_Fin")),
                "training_1_ini": _clean_time(r.get("Training_1_Ini")),
                "training_1_fin": _clean_time(r.get("Training_1_Fin")),
            })

    return rows_turnos, rows_detallados


def sync_turnos_databases(rows_turnos: list[dict], rows_detallados: list[dict]):
    """Guarda los turnos en presencia_master.db y presencia.db."""
    if not rows_turnos and not rows_detallados:
        print("No hay turnos para guardar.")
        return

    # 1. Guardar en presencia_master.db
    conn = get_connection()
    if rows_turnos:
        guardar_turnos(conn, rows_turnos)
    if rows_detallados:
        guardar_turnos_detallados(conn, rows_detallados)
    
    total_master = conn.execute("SELECT COUNT(*), MIN(fecha), MAX(fecha) FROM turnos").fetchone()
    total_det_master = conn.execute("SELECT COUNT(*) FROM turnos_detallados").fetchone()[0]
    conn.close()
    print(f"-> Guardado en presencia_master.db: {len(rows_turnos)} turnos básicos, {len(rows_detallados)} detallados.")
    print(f"   Total acumulado master: {total_master[0]} turnos básicos ({total_master[1]} a {total_master[2]}), {total_det_master} detallados.")

    # 2. Sincronizar en presencia.db (usado por el visor y Streamlit)
    cloud_db = Path(__file__).resolve().parent.parent / CLOUD_EXPORT_PATH
    if cloud_db.exists():
        import sqlite3
        conn_cloud = sqlite3.connect(cloud_db)
        conn_cloud.executescript(SCHEMA)
        if rows_turnos:
            guardar_turnos(conn_cloud, rows_turnos)
        if rows_detallados:
            guardar_turnos_detallados(conn_cloud, rows_detallados)
        total_cloud = conn_cloud.execute("SELECT COUNT(*), MIN(fecha), MAX(fecha) FROM turnos").fetchone()
        total_det_cloud = conn_cloud.execute("SELECT COUNT(*) FROM turnos_detallados").fetchone()[0]
        conn_cloud.close()
        print(f"-> Sincronizado en presencia.db: {total_cloud[0]} turnos básicos, {total_det_cloud} detallados.")


def run_extraction(start_date: str, end_date: str):
    print(f"=== Extrayendo turnos vía API Almaverso ({start_date} -> {end_date}) ===")
    print("1. Autenticando con credenciales AD...")
    token = get_token()
    print("   Autenticación exitosa.")

    print("2. Cargando mapeo de Cédula a BP...")
    cedula_a_bp = load_cedula_a_bp()
    print(f"   {len(cedula_a_bp)} cédulas mapeadas.")

    print(f"3. Descargando turnos desde {BASE_URL}/API/GET-EXPORT/SHIFTS ...")
    raw_shifts = fetch_shifts_range(start_date, end_date, token)
    print(f"   {len(raw_shifts)} registros descargados desde la API.")

    print("4. Parseando y normalizando turnos...")
    rows_turnos, rows_detallados = parse_api_shifts(raw_shifts, cedula_a_bp)
    print(f"   {len(rows_turnos)} turnos válidos con hora inicio/fin y {len(rows_detallados)} turnos detallados.")

    print("5. Guardando en bases de datos SQLite...")
    sync_turnos_databases(rows_turnos, rows_detallados)
    print("=== Extracción finalizada con éxito ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extrae turnos de Almaverso API y los guarda en SQLite.")
    parser.add_argument("--start", help="Fecha inicio YYYY-MM-DD")
    parser.add_argument("--end", help="Fecha fin YYYY-MM-DD")
    parser.add_argument("--hoy", action="store_true", help="Solo el día de hoy")
    args = parser.parse_args()

    today = datetime.now().date()
    if args.hoy:
        start_str = today.strftime("%Y-%m-%d")
        end_str = start_str
    elif args.start and args.end:
        start_str = args.start
        end_str = args.end
    else:
        # Por defecto: desde ayer hasta hoy + 7 días
        start_str = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        end_str = (today + timedelta(days=7)).strftime("%Y-%m-%d")

    run_extraction(start_str, end_str)
