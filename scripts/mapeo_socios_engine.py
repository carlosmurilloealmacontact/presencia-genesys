"""
Motor de Cruce con Socio Maestro (Google Sheets):
Obtiene los Alias de Salesforce (Etiquetas_Especiales_2) y los Niveles N1/N2/N3 (Etiquetas_Especiales_3),
vinculandolos con el Nombre Real, Cédula/BP y Servicio de cada asesor.
"""

import json
import os
import sys
import pandas as pd
from pathlib import Path

BASE_DIR = Path(__file__).parent
CACHE_MAPEO_PATH = BASE_DIR.parent / "data" / "salesforce" / "maestro_asesores_b2b.json"

sys.path.insert(0, str(BASE_DIR))
try:
    import jerarquia
except Exception as e:
    jerarquia = None


def sync_maestro_asesores():
    """Descarga y cachea el mapeo de alias de Salesforce a Nombres Reales y Niveles."""
    os.makedirs(CACHE_MAPEO_PATH.parent, exist_ok=True)
    if jerarquia is None:
        return load_cached_mapeo()
    try:
        creds = jerarquia.get_google_creds()
        import gspread
        client = gspread.authorize(creds)
        sheet = client.open_by_key(jerarquia.BASE_SPREADSHEET_ID).worksheet(jerarquia.BASE_SHEET_NAME)
        records = sheet.get_all_records()
        df = pd.DataFrame(records)

        mapeo = {}
        for _, row in df.iterrows():
            alias = str(row.get("Etiquetas_Especiales_2", "")).strip()
            nombre = str(row.get("nombre_completo", "")).strip()
            bp = str(row.get("usuario_gestor_1", "")).strip()
            coordinador = str(row.get("coordinador", "")).strip()
            servicio = str(row.get("Servicio", "")).strip()
            cargo = str(row.get("cargo", "")).strip()
            supervisor = str(row.get("jefe_inmediato", "")).strip()

            es_marelyn = "CARDONA" in coordinador.upper() or "MARELYN" in coordinador.upper()
            es_b2b = "B2B" in str(row.get("area", "")).upper() or "CORP" in servicio.upper() or "AGENCIA" in servicio.upper()

            if not alias or alias == "-":
                if es_marelyn or es_b2b:
                    # Asignar alias derivado de BP o nombre para no perder asesores operativos
                    alias = str(row.get("usuario_gestor_3", "")).strip() or (f"BP_{bp}" if bp else nombre.split()[0] if nombre else "")
                else:
                    continue

            if not alias:
                continue

            nivel_raw = str(row.get("Etiquetas_Especiales_3", "")).strip()
            nivel = "N/A"
            if "N1" in nivel_raw and "N2" in nivel_raw:
                nivel = "N1-N2"
            elif "N1" in nivel_raw:
                nivel = "N1"
            elif "N2" in nivel_raw:
                nivel = "N2"
            elif "N3" in nivel_raw:
                nivel = "N3"
            elif nivel_raw:
                nivel = nivel_raw

            info = {
                "alias": alias,
                "nombre_completo": nombre,
                "bp": bp,
                "nivel": nivel,
                "servicio": servicio,
                "cargo": cargo,
                "supervisor": supervisor,
                "jefe_inmediato": supervisor,
                "coordinador": coordinador
            }

            # Guardar con clave en mayúsculas para cruces case-insensitive
            mapeo[alias.upper()] = info
            if bp:
                mapeo[bp] = info
            if nombre:
                mapeo[nombre.upper()] = info

        with open(CACHE_MAPEO_PATH, "w", encoding="utf-8") as f:
            json.dump(mapeo, f, indent=4, ensure_ascii=False)

        print(f"[OK] Mapeo de {len(mapeo)} registros guardado en {CACHE_MAPEO_PATH}")
        return mapeo
    except Exception as e:
        print(f"[!] Error al sincronizar con Google Sheets: {e}")
        return load_cached_mapeo()


def load_cached_mapeo():
    """Carga el mapeo cacheado localmente."""
    if os.path.exists(CACHE_MAPEO_PATH):
        try:
            with open(CACHE_MAPEO_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def get_asesor_info(alias):
    """Obtiene la información oficial de un asesor por su Alias de Salesforce."""
    if not alias:
        return {
            "nombre_completo": "Sin Asignar",
            "nivel": "N/A",
            "alias": alias,
            "bp": "",
            "servicio": "Sin Servicio",
            "supervisor": "Sin Supervisor",
            "jefe_inmediato": "Sin Supervisor",
            "coordinador": "Sin Coordinador"
        }

    alias_clean = str(alias).strip().upper()
    mapeo = load_cached_mapeo()
    if not mapeo:
        mapeo = sync_maestro_asesores()

    info = mapeo.get(alias_clean)
    if info:
        return info

    return {
        "alias": alias,
        "nombre_completo": alias,
        "nivel": "N/A",
        "bp": "",
        "servicio": "Sin Servicio",
        "supervisor": "Sin Supervisor",
        "jefe_inmediato": "Sin Supervisor",
        "coordinador": "Sin Coordinador"
    }


if __name__ == "__main__":
    m = sync_maestro_asesores()
    print("Muestra de asesores mapeados:")
    for k in list(m.keys())[:10]:
        print(f"  {k} -> {m[k]['nombre_completo']} ({m[k]['nivel']})")
