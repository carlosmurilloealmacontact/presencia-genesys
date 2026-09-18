"""
Módulo Autoritativo: Definición de Ámbito Agencias B2B vs LATAM Pasajeros
========================================================================
Regla de Oro (Dirección de Operaciones):
- A menos de indicación contraria expresa de Carlos Murillo, TODA persona cuyo
  jefe directo (supervisor) o coordinador sea Marely Cardona pertenece exclusivamente
  al servicio de Agencias B2B.
- Bajo ningún motivo deben aparecer en ningún reporte, selector o vista relacionada
  con LATAM Pasajeros.
- Viceversa estricto: dentro de Agencias B2B no debe aparecer nadie que NO sea del equipo
  de Marely Cardona.
- CATALINA CARDONA (CARDONA BARRAGAN CATALINA) pertenece a LATAM Pasajeros (LUA AMC),
  por lo que queda blindada y expresamente excluida de B2B.
- Los servicios BO_CUS_COL (Andrés Rojas) y BO_WAIVERS (Oscar Roldán) pertenecen a Pasajeros.
"""

import os
import re
import json
import sqlite3
import unicodedata
from functools import lru_cache
from pathlib import Path
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR.parent / "data" / "presencia.db"
MAESTRO_JSON_PATH = BASE_DIR.parent / "data" / "salesforce" / "maestro_asesores_b2b.json"

# Supervisores directos canónicos de la coordinación de Marely Cardona
SUPERVISORES_MARELY_CANONICOS = {
    "AGUIRRE GUISAO DIEGO ALEJANDRO",
    "GUISAO BARRERA JESUS ALONSO",
    "HERNANDEZ ISAZA CRISTIAN EDUARDO",
    "MENDEZ TELLECHEA ORDALIS VERONICA",
    "MORENO HURTADO DEINER ANDRES",
    "PEREZ METAUTE MARIA ISABEL",
    "RESTREPO URIBE EMANUEL",
    "OCHOA GARCIA SANDRA JANNETH",
}

# Coordinador oficial
COORDINADOR_MARELY_OFICIAL = "CARDONA RAMIREZ MARELYN"


def normalizar_cadena(s: str) -> str:
    """Normaliza texto a mayúsculas sin tildes ni caracteres especiales."""
    if not s:
        return ""
    s_norm = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("utf-8")
    return " ".join(re.sub(r"[^A-Z0-9\s]", " ", s_norm.upper()).split())


SUPERVISORES_MARELY_NORM = {normalizar_cadena(s) for s in SUPERVISORES_MARELY_CANONICOS}


def es_nombre_marely_cardona(val: str) -> bool:
    """Valida si un string de texto corresponde a Marely Cardona, descartando a Catalina Cardona."""
    if not val:
        return False
    n = normalizar_cadena(val)
    if "CATALINA" in n or "BARRAGAN" in n:
        return False
    if "CARDONA" in n and any(k in n for k in ("RAMIREZ", "MARELY", "MARELYN", "MARELIN")):
        return True
    if any(k in n for k in ("MARELY", "MARELYN", "MARELIN")):
        return True
    return False


def es_supervisor_equipo_marely(superv: str) -> bool:
    """Valida si el supervisor pertenece al equipo directo de Marely Cardona."""
    if not superv:
        return False
    ns = normalizar_cadena(superv)
    if "CATALINA" in ns:
        return False
    if es_nombre_marely_cardona(ns):
        return True
    return ns in SUPERVISORES_MARELY_NORM


@lru_cache(maxsize=1)
def cargar_universo_bps_marely() -> tuple[set[str], set[str]]:
    """
    Carga de forma consolidada todos los BPs y Nombres de asesores
    pertenecientes al equipo de Marely Cardona desde:
    1. Base de datos operativa (data/presencia.db)
    2. Maestro de Asesores (data/salesforce/maestro_asesores_b2b.json)
    """
    bps_marely = set()
    nombres_marely = set()

    # 1. Base de datos presence.db (segments)
    if DB_PATH.exists():
        try:
            conn = sqlite3.connect(str(DB_PATH))
            cur = conn.cursor()
            cur.execute("SELECT distinct agente, jefe_inmediato, coordinador FROM segments WHERE agente IS NOT NULL;")
            for ag, jefe, coord in cur.fetchall():
                c_norm = normalizar_cadena(coord)
                j_norm = normalizar_cadena(jefe)

                pertenece = False
                if es_nombre_marely_cardona(c_norm) or es_nombre_marely_cardona(j_norm):
                    pertenece = True
                elif es_supervisor_equipo_marely(j_norm):
                    pertenece = True

                if pertenece:
                    ag_str = str(ag).strip()
                    bp = ag_str.split(" - ")[0].strip()
                    if bp:
                        bps_marely.add(bp)
                    if " - " in ag_str:
                        nom = normalizar_cadena(ag_str.split(" - ", 1)[1])
                        if nom:
                            nombres_marely.add(nom)
            conn.close()
        except Exception:
            pass

    # 2. Maestro JSON de Salesforce / Socio Maestro
    if MAESTRO_JSON_PATH.exists():
        try:
            with open(MAESTRO_JSON_PATH, "r", encoding="utf-8") as f:
                maestro = json.load(f)
            for k, v in maestro.items():
                if not isinstance(v, dict):
                    continue
                c_norm = normalizar_cadena(v.get("coordinador", ""))
                j_norm = normalizar_cadena(v.get("jefe_inmediato", "") or v.get("supervisor", ""))

                pertenece = False
                if es_nombre_marely_cardona(c_norm) or es_nombre_marely_cardona(j_norm):
                    pertenece = True
                elif es_supervisor_equipo_marely(j_norm):
                    pertenece = True

                if pertenece:
                    bp = str(v.get("bp", "")).strip()
                    nom = normalizar_cadena(v.get("nombre_completo", ""))
                    if bp and bp != "-":
                        bps_marely.add(bp)
                    if nom:
                        nombres_marely.add(nom)
        except Exception:
            pass

    return bps_marely, nombres_marely


def es_equipo_marely_cardona(
    coordinador: str = "",
    jefe_inmediato: str = "",
    supervisor: str = "",
    bp: str = "",
    nombre: str = "",
    servicio: str = ""
) -> bool:
    """
    Función autoritativa única:
    Determina si un registro/asesor pertenece al equipo de Marely Cardona (Agencias B2B).
    """
    # 0. Verificación previa de exclusión expresa de Catalina Cardona
    j_all = f"{jefe_inmediato} {supervisor}"
    if "CATALINA" in normalizar_cadena(j_all) or "BARRAGAN" in normalizar_cadena(j_all):
        return False

    # 0.1 Exclusión de servicios que NO pertenecen a B2B (ej. BO_CUS, BO_WAIVERS, LUA AMC)
    if servicio:
        s_norm = normalizar_cadena(servicio)
        if any(pasaj in s_norm for pasaj in ("BO CUS", "BO WAIVERS", "LUA AMC", "SWAT", "CLARO", "DT FFP")):
            if not es_nombre_marely_cardona(coordinador):
                return False

    # 1. Coordinador es Marely Cardona
    if coordinador and es_nombre_marely_cardona(coordinador):
        return True

    # 2. Jefe directo o Supervisor es Marely Cardona o alguno de sus supervisores
    if jefe_inmediato and es_supervisor_equipo_marely(jefe_inmediato):
        return True
    if supervisor and es_supervisor_equipo_marely(supervisor):
        return True

    # 3. Validación por BP o Nombre en el universo de Marely
    bps_marely, nombres_marely = cargar_universo_bps_marely()
    if bp:
        bp_clean = str(bp).strip().split(" - ")[0].strip()
        if bp_clean in bps_marely:
            return True

    if nombre:
        nom_norm = normalizar_cadena(nombre)
        if nom_norm in nombres_marely:
            return True

    return False


def obtener_supervisores_disponibles_b2b() -> list[str]:
    """Retorna la lista ordenada de supervisores que pertenecen al equipo de Marely Cardona."""
    return sorted(list(SUPERVISORES_MARELY_CANONICOS))


def obtener_coordinadores_disponibles_b2b() -> list[str]:
    """Retorna la lista de coordinadores de Agencias B2B."""
    return [COORDINADOR_MARELY_OFICIAL]


def obtener_servicios_disponibles_b2b() -> list[str]:
    """Retorna los servicios programados exclusivos de Agencias B2B."""
    return [
        "AG CELULA REMISION",
        "AG CHECK IN",
        "AG CORPORATE CHAT",
        "AGENCIAS TARGET ENG",
        "AGENCIAS TARGET ES",
        "AGY N1 ENG VOZ",
        "AGY N1 ESP CHAT",
        "AGY N1 ESP VOZ",
        "AGY N3 ESP CHAT",
        "AGY N3 ESP VOZ",
        "BO AGENCIAS TARGET",
        "BO_CORPORATE",
        "CHAT AGENCIAS ESP",
        "CORPORATE PYME",
    ]


def filtrar_df_por_ambito(
    df: pd.DataFrame,
    ambito: str = "PASAJEROS",
    col_coord: str = "coordinador",
    col_jefe: str = "jefe_inmediato",
    col_superv: str = "supervisor",
    col_bp: str = "bp",
    col_nombre: str = "nombre_agente",
    col_servicio: str = "servicio"
) -> pd.DataFrame:
    """
    Filtra cualquier DataFrame garantizando la partición matemática estricta:
    - Si ambito == 'B2B': solo registros de Marely Cardona.
    - Si ambito == 'PASAJEROS': exclusión categórica del equipo de Marely Cardona.
    """
    if df is None or df.empty:
        return df

    def fila_es_marely(row):
        c = row.get(col_coord, "") if col_coord in row else ""
        j = row.get(col_jefe, "") if col_jefe in row else ""
        s = row.get(col_superv, "") if col_superv in row else ""
        b = row.get(col_bp, "") if col_bp in row else ""
        n = row.get(col_nombre, "") if col_nombre in row else ""
        srv = row.get(col_servicio, "") if col_servicio in row else ""
        return es_equipo_marely_cardona(
            coordinador=c,
            jefe_inmediato=j,
            supervisor=s,
            bp=b,
            nombre=n,
            servicio=srv
        )

    mascara_marely = df.apply(fila_es_marely, axis=1)

    if str(ambito).upper() == "B2B":
        return df[mascara_marely].copy()
    else:
        return df[~mascara_marely].copy()
