"""
Backfill masivo de presencia para un rango de fechas.

Consulta cada agente en ventanas de 5 dias (no 1 dia a la vez): Genesys trunca
la respuesta de /analytics/users/details/query silenciosamente alrededor de
~90 tramos sin avisar ni dar cursor de paginacion (verificado empiricamente).
Con ~13-14 tramos/agente/dia en promedio, 5 dias se mantiene comodo debajo de
ese techo incluso para agentes con presencia mas "ruidosa" (muchos cambios de
estado). Ampliar CHUNK_DIAS por encima de eso arriesga perder datos sin error.

Uso:
    python backfill_presencia.py --inicio 2026-07-01 --fin 2026-08-23
"""

import argparse
from datetime import datetime, timedelta, timezone

from config import GENESYS_CONFIG
from db import get_connection, replace_range, purge_old
from extract_presencia import (
    get_agents_by_location,
    get_presence_catalog,
    query_details_user,
    build_rows,
)
from jerarquia import load_jerarquia

CHUNK_DIAS = 5


def date_chunks(fecha_inicio: str, fecha_fin: str, dias: int = CHUNK_DIAS):
    start = datetime.fromisoformat(fecha_inicio)
    end = datetime.fromisoformat(fecha_fin)
    current = start
    while current <= end:
        chunk_end = min(current + timedelta(days=dias - 1), end)
        yield current.strftime("%Y-%m-%d"), chunk_end.strftime("%Y-%m-%d")
        current = chunk_end + timedelta(days=1)


def run(fecha_inicio: str, fecha_fin: str):
    with open(GENESYS_CONFIG["token_file"], encoding="utf-8") as f:
        token = f.read().strip()

    print(f"=== Backfill Presencia — {fecha_inicio} a {fecha_fin} ===")
    print("Obteniendo agentes de AMC Bogotá y Medellín...")
    agents = get_agents_by_location(token)
    agents_map = {a["id"]: a for a in agents}
    print(f"  {len(agents)} agentes encontrados.")

    print("Cargando catálogo de estados de presencia...")
    catalog = get_presence_catalog(token)
    print(f"  {len(catalog)} estados definidos.")

    print("Cargando jerarquía (servicio / supervisor / coordinador) desde Sheets...")
    jerarquia = load_jerarquia()
    print(f"  {len(jerarquia)} agentes en la jerarquía.")

    conn = get_connection()
    chunks = list(date_chunks(fecha_inicio, fecha_fin))
    total_agentes = len(agents_map)
    print(f"\n{len(chunks)} ventanas de hasta {CHUNK_DIAS} días × {total_agentes} agentes.")

    total_filas = 0
    for i, (c_inicio, c_fin) in enumerate(chunks, start=1):
        print(f"\n[Ventana {i}/{len(chunks)}] {c_inicio} -> {c_fin}")
        start_dt = datetime.fromisoformat(c_inicio).replace(hour=5, minute=0, second=0, tzinfo=timezone.utc)
        end_dt = datetime.fromisoformat(c_fin).replace(hour=5, minute=0, second=0, tzinfo=timezone.utc) + timedelta(days=1)

        all_user_details = []
        for j, user_id in enumerate(agents_map.keys(), start=1):
            all_user_details.extend(query_details_user(token, user_id, start_dt, end_dt))
            if j % 200 == 0:
                print(f"    {j}/{total_agentes} agentes consultados")

        rows = build_rows(all_user_details, agents_map, catalog, jerarquia, c_inicio, c_fin)
        print(f"    {len(rows)} tramos generados para esta ventana.")
        replace_range(conn, c_inicio, c_fin, rows)
        total_filas += len(rows)

    borrados = purge_old(conn)
    conn.close()
    print(f"\nBackfill completo. {total_filas} tramos totales guardados. {borrados} tramos viejos purgados (retención).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--inicio", required=True, help="YYYY-MM-DD")
    parser.add_argument("--fin", required=True, help="YYYY-MM-DD")
    args = parser.parse_args()
    run(args.inicio, args.fin)
