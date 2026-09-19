"""
Cruce de agente_id -> servicio / supervisor (jefe_inmediato) / coordinador,
leyendo el mismo sheet "Base" que usa Seguimiento Pausas 4DX.
"""

import json
import gspread
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import os

from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
_BASE_DIR = Path(__file__).parent
TOKEN_PATH = str(_BASE_DIR / "google_token.json")
CREDS_PATH = str(_BASE_DIR / "google_oauth_client.json")

BASE_SPREADSHEET_ID = "1veAlRJlVrJ2MRtoYNi3aJ_NX97sBFTgcww0V0jv6_Q0"
BASE_SHEET_NAME = "Base"


def get_google_creds():
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return creds


def load_jerarquia() -> dict:
    """
    Mapa identificador (BP / Cédula / Gestores) -> {servicio, jefe_inmediato, coordinador, nombre, cargo, estado_laboral}.
    Resiliente: intenta Google Sheets y guarda copia local en data/cache_jerarquia_base.json;
    si Google Sheets falla o no tiene conexión, carga la copia local garantizando 100% disponibilidad.
    """
    cache_file = _BASE_DIR.parent / "data" / "cache_jerarquia_base.json"

    rows = None
    try:
        creds = get_google_creds()
        client = gspread.authorize(creds)
        sheet = client.open_by_key(BASE_SPREADSHEET_ID).worksheet(BASE_SHEET_NAME)
        rows = sheet.get_all_records()
    except Exception:
        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    lookup = {}
    for row in rows:
        info = {
            "nombre": str(row.get("nombre_completo", "")).strip(),
            "servicio": str(row.get("Servicio", "")).strip(),
            "jefe_inmediato": str(row.get("jefe_inmediato", "")).strip(),
            "coordinador": str(row.get("coordinador", "")).strip(),
            "cargo": str(row.get("cargo", "")).strip(),
            "estado_laboral": str(row.get("estado", "Activo") or "Activo").strip(),
            "cedula": str(row.get("cedula", "")).strip()
        }
        for col in ["usuario_gestor_1", "usuario_gestor_2", "usuario_gestor_3", "usuario_gestor_4", "cedula"]:
            val = str(row.get(col, "")).strip()
            if val and val not in ("-", "nan", "None", "0", "0.0"):
                if val.endswith(".0"):
                    val = val[:-2]
                lookup[val] = info

    # Persistir en cache local para resiliencia total
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(lookup, f, ensure_ascii=False)
    except Exception:
        pass

    return lookup


def load_cedula_a_bp() -> dict:
    """
    Mapa cedula -> BP (usuario_gestor_1). El archivo de Sistema de Punto
    identifica a las personas por cedula, no por BP, asi que este puente
    permite cruzarlo con nuestros datos de Genesys (que usan BP).
    """
    creds = get_google_creds()
    client = gspread.authorize(creds)
    sheet = client.open_by_key(BASE_SPREADSHEET_ID).worksheet(BASE_SHEET_NAME)
    rows = sheet.get_all_records()

    lookup = {}
    for row in rows:
        cedula = str(row.get("cedula", "")).strip()
        bp = str(row.get("usuario_gestor_1", "")).strip()
        if cedula and bp:
            lookup[cedula] = bp
    return lookup


def numero_agente(agente_nombre: str) -> str:
    """'4853818 - Garcia Rendon Salome' -> '4853818'"""
    return agente_nombre.split(" - ")[0].strip() if " - " in agente_nombre else agente_nombre.strip()
