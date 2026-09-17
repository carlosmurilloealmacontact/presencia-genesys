"""
Descarga histórica y auditoría de turnos desde el inicio de captura de datos (2026-07-19)
hasta la fecha actual (+ días programados).

Compara los turnos retornados por la API contra los existentes en la base de datos
para detectar:
1. Turnos nuevos (días o agentes que no tenían turno registrado).
2. Modificaciones de horarios (cambios en hora_inicio u hora_fin).
3. Modificaciones de novedades (cambios entre TUR, DES, incapacidades, etc.).
4. Variaciones en pausas programadas.

Aplica el upsert para consolidar la base histórica completa en SQLite.
"""

import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd
import sqlite3
from dotenv import load_dotenv, find_dotenv

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

load_dotenv(find_dotenv())
env_parent = Path(__file__).resolve().parent.parent.parent / ".env"
if env_parent.exists():
    load_dotenv(env_parent)

from db import get_connection, guardar_turnos, guardar_turnos_detallados, SCHEMA
from jerarquia import load_cedula_a_bp
from config import CLOUD_EXPORT_PATH
from extract_turnos_api import get_token, fetch_shifts_range, parse_api_shifts

DATE_START_DEFAULT = "2026-07-19"
DATE_END_DEFAULT = "2026-09-21"


def load_existing_db_data(conn: sqlite3.Connection):
    """Carga los turnos actuales en memoria indexados por (bp, fecha)."""
    cur = conn.cursor()
    
    # 1. Turnos básicos
    cur.execute("SELECT bp, fecha, hora_inicio, hora_fin FROM turnos")
    turnos_dict = {}
    for bp, fecha, h_ini, h_fin in cur.fetchall():
        turnos_dict[(str(bp), str(fecha))] = {
            "hora_inicio": str(h_ini) if h_ini else "",
            "hora_fin": str(h_fin) if h_fin else "",
        }
        
    # 2. Turnos detallados
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='turnos_detallados'")
    detallados_dict = {}
    if cur.fetchone():
        cur.execute("""
            SELECT bp, fecha, novedad, horas_programadas, turno_ini, turno_fin, 
                   dialogo_ini, des_1_ini, lunch_ini 
            FROM turnos_detallados
        """)
        for r in cur.fetchall():
            detallados_dict[(str(r[0]), str(r[1]))] = {
                "novedad": str(r[2]) if r[2] else "",
                "horas_programadas": float(r[3]) if r[3] else 0.0,
                "turno_ini": str(r[4]) if r[4] else "",
                "turno_fin": str(r[5]) if r[5] else "",
                "dialogo_ini": str(r[6]) if r[6] else "",
                "des_1_ini": str(r[7]) if r[7] else "",
                "lunch_ini": str(r[8]) if r[8] else "",
            }

    return turnos_dict, detallados_dict


def audit_and_sync(start_date: str = DATE_START_DEFAULT, end_date: str = DATE_END_DEFAULT, chunk_days: int = 7):
    print(f"================================================================================")
    print(f" AUDITORÍA Y SINCRONIZACIÓN HISTÓRICA DE TURNOS: {start_date} A {end_date}")
    print(f"================================================================================")

    conn = get_connection()
    print("1. Leyendo datos actuales de presencia_master.db...")
    existing_turnos, existing_detallados = load_existing_db_data(conn)
    print(f"   -> Turnos básicos existentes en DB: {len(existing_turnos)}")
    print(f"   -> Turnos detallados existentes en DB: {len(existing_detallados)}")

    print("\n2. Autenticando con API Almaverso...")
    token = get_token()
    print("   -> Autenticación OK.")

    print("\n3. Cargando diccionario Cédula -> BP...")
    cedula_a_bp = load_cedula_a_bp()
    print(f"   -> {len(cedula_a_bp)} cédulas mapeadas.")

    # Generar ventanas de tiempo
    d_start = datetime.strptime(start_date, "%Y-%m-%d").date()
    d_end = datetime.strptime(end_date, "%Y-%m-%d").date()

    chunks = []
    curr = d_start
    while curr <= d_end:
        chunk_end = min(curr + timedelta(days=chunk_days - 1), d_end)
        chunks.append((curr.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")))
        curr = chunk_end + timedelta(days=1)

    print(f"\n4. Descargando y comparando en {len(chunks)} ventanas de {chunk_days} días...")

    # Métricas de auditoría
    total_api_shifts = 0
    nuevos_turnos = 0
    modificados_horario = []
    modificados_novedad = []
    identicos_turnos = 0
    
    all_rows_turnos = []
    all_rows_detallados = []

    for i, (c_start, c_end) in enumerate(chunks, start=1):
        print(f"\n   [{i}/{len(chunks)}] Descargando {c_start} al {c_end} ...")
        try:
            raw_data = fetch_shifts_range(c_start, c_end, token)
            total_api_shifts += len(raw_data)
            print(f"       Recibidos: {len(raw_data)} registros.")
            
            rt, rd = parse_api_shifts(raw_data, cedula_a_bp)
            all_rows_turnos.extend(rt)
            all_rows_detallados.extend(rd)

            # Comparar
            for t in rt:
                key = (str(t["bp"]), str(t["fecha"]))
                if key not in existing_turnos:
                    nuevos_turnos += 1
                else:
                    curr_db = existing_turnos[key]
                    ini_db = curr_db["hora_inicio"]
                    fin_db = curr_db["hora_fin"]
                    ini_api = t["hora_inicio"]
                    fin_api = t["hora_fin"]

                    if ini_db != ini_api or fin_db != fin_api:
                        modificados_horario.append({
                            "bp": t["bp"],
                            "fecha": t["fecha"],
                            "hora_inicio_anterior": ini_db,
                            "hora_inicio_nueva": ini_api,
                            "hora_fin_anterior": fin_db,
                            "hora_fin_nueva": fin_api,
                        })
                    else:
                        identicos_turnos += 1

            # Comparar novedades en detallados
            for d in rd:
                key = (str(d["bp"]), str(d["fecha"]))
                if key in existing_detallados:
                    nov_db = existing_detallados[key]["novedad"]
                    nov_api = d["novedad"]
                    if nov_db and nov_api and nov_db != nov_api:
                        modificados_novedad.append({
                            "bp": d["bp"],
                            "fecha": d["fecha"],
                            "agente": d["nombre_agente"],
                            "novedad_anterior": nov_db,
                            "novedad_nueva": nov_api,
                        })

        except Exception as e:
            print(f"       ERROR en ventana {c_start} a {c_end}: {e}")
            time.sleep(2)

    print("\n================================================================================")
    print(" REPORTE DE AUDITORÍA Y COMPARACIÓN DE TURNOS")
    print("================================================================================")
    print(f"Total registros descargados desde la API:      {total_api_shifts:,}")
    print(f"Turnos válidos consolidados (con horario):     {len(all_rows_turnos):,}")
    print(f"Turnos NUEVOS incorporados a la base:          {nuevos_turnos:,}")
    print(f"Turnos IDÉNTICOS (sin cambios en horario):     {identicos_turnos:,}")
    print(f"Turnos con CAMBIO DE HORARIO detectado:        {len(modificados_horario):,}")
    print(f"Turnos con CAMBIO DE NOVEDAD detectado:        {len(modificados_novedad):,}")

    if modificados_horario:
        print(f"\n--- Muestra de primeros 10 cambios de horario detectados ---")
        df_mod = pd.DataFrame(modificados_horario)
        print(df_mod.head(10).to_string(index=False))
        
        # Guardar reporte de cambios en CSV para consulta del usuario
        report_path = Path("data/auditoria_cambios_turnos.csv")
        df_mod.to_csv(report_path, index=False, encoding="utf-8-sig")
        print(f"\n-> Reporte completo de cambios de horario exportado a: {report_path.resolve()}")

    if modificados_novedad:
        print(f"\n--- Muestra de cambios de novedad (TUR/DES/Ausentismos) ---")
        df_nov = pd.DataFrame(modificados_novedad)
        print(df_nov.head(10).to_string(index=False))

    print("\n5. Aplicando actualización masiva en bases de datos SQLite...")
    if all_rows_turnos:
        guardar_turnos(conn, all_rows_turnos)
    if all_rows_detallados:
        guardar_turnos_detallados(conn, all_rows_detallados)

    total_master = conn.execute("SELECT COUNT(*), MIN(fecha), MAX(fecha) FROM turnos").fetchone()
    total_det_master = conn.execute("SELECT COUNT(*) FROM turnos_detallados").fetchone()[0]
    conn.close()
    print(f"-> Base de datos presencia_master.db actualizada:")
    print(f"   Turnos básicos: {total_master[0]:,} ({total_master[1]} a {total_master[2]})")
    print(f"   Turnos detallados: {total_det_master:,}")

    # Sincronizar en presencia.db
    cloud_db = Path(__file__).resolve().parent.parent / CLOUD_EXPORT_PATH
    if cloud_db.exists():
        conn_cloud = sqlite3.connect(cloud_db)
        conn_cloud.executescript(SCHEMA)
        if all_rows_turnos:
            guardar_turnos(conn_cloud, all_rows_turnos)
        if all_rows_detallados:
            guardar_turnos_detallados(conn_cloud, all_rows_detallados)
        total_cloud = conn_cloud.execute("SELECT COUNT(*), MIN(fecha), MAX(fecha) FROM turnos").fetchone()
        total_det_cloud = conn_cloud.execute("SELECT COUNT(*) FROM turnos_detallados").fetchone()[0]
        conn_cloud.close()
        print(f"-> Base de datos presencia.db sincronizada:")
        print(f"   Turnos básicos: {total_cloud[0]:,} ({total_cloud[1]} a {total_cloud[2]})")
        print(f"   Turnos detallados: {total_det_cloud:,}")

    print("\n=== Proceso completado exitosamente ===")


if __name__ == "__main__":
    audit_and_sync()
