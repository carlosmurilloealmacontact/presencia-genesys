"""
Extrae TODOS los tramos de presencia (no solo Dialogo/CDR) de cada agente de
AMC Bogota y Medellin para un dia especifico, y los guarda en SQLite.

Uso:
    python extract_presencia.py                # dia de ayer
    python extract_presencia.py --fecha 2026-08-20
"""

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

# Bajo Task Scheduler (sin consola real) la salida puede caer a un codepage
# sin soporte de tildes/ñ y abortar el proceso con UnicodeEncodeError.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from config import GENESYS_CONFIG, LOCATION_IDS
from db import get_connection, replace_day, purge_old
from jerarquia import load_jerarquia, numero_agente

COLOMBIA_OFFSET = timedelta(hours=-5)


def api_get(url, headers, params=None, retries=3):
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=headers, params=params, timeout=30)
            r.raise_for_status()
            return r
        except Exception as e:
            if attempt < retries - 1:
                print(f"  Reintento {attempt+1}/{retries-1} ({e})")
                time.sleep(5)
            else:
                raise


def api_post(url, headers, json_body, retries=3):
    for attempt in range(retries):
        try:
            r = requests.post(url, headers=headers, json=json_body, timeout=60)
            r.raise_for_status()
            return r
        except Exception as e:
            if attempt < retries - 1:
                print(f"  Reintento {attempt+1}/{retries-1} ({e})")
                time.sleep(5)
            else:
                raise


def get_agents_by_location(token: str) -> list[dict]:
    headers = {"Authorization": f"Bearer {token}"}
    location_set = set(LOCATION_IDS)
    agents = []
    page = 1
    while True:
        r = api_get(
            f"{GENESYS_CONFIG['base_url']}/api/v2/users",
            headers=headers,
            params={"pageSize": 100, "pageNumber": page, "expand": "locations"},
        )
        data = r.json()
        for user in data.get("entities", []):
            for loc in (user.get("locations") or []):
                if loc.get("locationDefinition", {}).get("id", "") in location_set:
                    agents.append(user)
                    break
        if page >= data.get("pageCount", 1):
            break
        page += 1
        if page % 10 == 0:
            print(f"  Cargando usuarios... página {page}/{data.get('pageCount', '?')}")
    return agents


def get_presence_catalog(token: str) -> dict:
    """Mapa organizationPresenceId -> {label, systemPresence}."""
    headers = {"Authorization": f"Bearer {token}"}
    r = api_get(
        f"{GENESYS_CONFIG['base_url']}/api/v2/presencedefinitions",
        headers=headers,
        params={"pageSize": 100},
    )
    data = r.json()
    catalog = {}
    for e in data.get("entities", []):
        labels = e.get("languageLabels", {})
        label = labels.get("es") or labels.get("en_US") or labels.get("en") or e.get("systemPresence", "Desconocido")
        catalog[e["id"]] = {"label": label, "systemPresence": e.get("systemPresence", "")}
    return catalog


def query_details_user(token: str, user_id: str, start: datetime, end: datetime) -> list[dict]:
    """
    Tramos de presencia de UN solo usuario. Genesys trunca la respuesta cuando
    el volumen de un lote de varios usuarios es alto (sin cursor de paginacion),
    asi que se consulta de a un agente para no perder datos silenciosamente.
    """
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {
        "interval": f"{start.strftime('%Y-%m-%dT%H:%M:%S.000Z')}/{end.strftime('%Y-%m-%dT%H:%M:%S.000Z')}",
        "order": "asc",
        "userFilters": [
            {"type": "or", "predicates": [{"type": "dimension", "dimension": "userId", "value": user_id}]}
        ],
    }
    r = api_post(f"{GENESYS_CONFIG['base_url']}/api/v2/analytics/users/details/query", headers, body)
    return r.json().get("userDetails", [])


def parse_genesys_ts(ts: str) -> datetime:
    fmt = "%Y-%m-%dT%H:%M:%S.%fZ" if "." in ts else "%Y-%m-%dT%H:%M:%SZ"
    return datetime.strptime(ts, fmt)


def build_rows(user_details: list[dict], agents_map: dict, catalog: dict, jerarquia: dict, fecha_min: str, fecha_max: str) -> list[dict]:
    """Genera filas para todos los tramos cuya fecha de inicio caiga en [fecha_min, fecha_max]."""
    rows = []
    for ud in user_details:
        user_id = ud.get("userId", "")
        agente_nombre = agents_map.get(user_id, {}).get("name", user_id)
        info_jerarquia = jerarquia.get(numero_agente(agente_nombre), {})
        for p in ud.get("primaryPresence", []):
            start_str = p.get("startTime")
            if not start_str:
                continue
            inicio_col = parse_genesys_ts(start_str) + COLOMBIA_OFFSET
            fecha_objetivo = inicio_col.strftime("%Y-%m-%d")
            if fecha_objetivo < fecha_min or fecha_objetivo > fecha_max:
                continue

            end_str = p.get("endTime")
            fin_col = (parse_genesys_ts(end_str) + COLOMBIA_OFFSET) if end_str else None
            duracion_min = round(((fin_col - inicio_col).total_seconds() / 60), 2) if fin_col else 0.0

            info = catalog.get(p.get("organizationPresenceId", ""), {})
            rows.append({
                "fecha": fecha_objetivo,
                "agente_id": user_id,
                "agente": agente_nombre,
                "cargo": info_jerarquia.get("cargo", ""),
                "servicio": info_jerarquia.get("servicio", ""),
                "jefe_inmediato": info_jerarquia.get("jefe_inmediato", ""),
                "coordinador": info_jerarquia.get("coordinador", ""),
                "presence_label": info.get("label", "Desconocido"),
                "system_presence": info.get("systemPresence", ""),
                "inicio": inicio_col.strftime("%Y-%m-%d %H:%M:%S"),
                "fin": fin_col.strftime("%Y-%m-%d %H:%M:%S") if fin_col else None,
                "duracion_min": duracion_min,
            })
    return rows


def run(fecha_objetivo: str):
    with open(GENESYS_CONFIG["token_file"], encoding="utf-8") as f:
        token = f.read().strip()

    print(f"=== Presencia diaria — {fecha_objetivo} ===")
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

    # Rango en UTC que cubre el dia completo en hora Colombia (UTC-5)
    dia = datetime.fromisoformat(fecha_objetivo)
    start = dia.replace(hour=5, minute=0, second=0, tzinfo=timezone.utc)
    end = start + timedelta(days=1)

    print(f"Consultando tramos de {len(agents)} agentes (uno por uno)...")
    all_user_details = []
    for i, user_id in enumerate(agents_map.keys(), start=1):
        all_user_details.extend(query_details_user(token, user_id, start, end))
        if i % 100 == 0:
            print(f"  {i}/{len(agents_map)} agentes consultados")

    rows = build_rows(all_user_details, agents_map, catalog, jerarquia, fecha_objetivo, fecha_objetivo)
    print(f"  {len(rows)} tramos generados.")

    conn = get_connection()
    replace_day(conn, fecha_objetivo, rows)
    borrados = purge_old(conn)
    conn.close()
    print(f"Guardado en SQLite. {borrados} tramos viejos purgados (retención).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--fecha", help="YYYY-MM-DD (default: ayer)")
    args = parser.parse_args()
    fecha = args.fecha or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    run(fecha)
