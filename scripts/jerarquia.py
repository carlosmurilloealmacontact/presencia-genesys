"""
Cruce de agente_id -> servicio / supervisor (jefe_inmediato) / coordinador,
leyendo el mismo sheet "Base" que usa Seguimiento Pausas 4DX.
"""

import gspread
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import os

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
TOKEN_PATH = "google_token.json"
CREDS_PATH = "google_oauth_client.json"

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
    """Mapa numero_agente -> {servicio, jefe_inmediato, coordinador}."""
    creds = get_google_creds()
    client = gspread.authorize(creds)
    sheet = client.open_by_key(BASE_SPREADSHEET_ID).worksheet(BASE_SHEET_NAME)
    rows = sheet.get_all_records()

    lookup = {}
    for row in rows:
        key = str(row.get("usuario_gestor_1", "")).strip()
        if not key:
            continue
        lookup[key] = {
            "servicio": row.get("Servicio", ""),
            "jefe_inmediato": row.get("jefe_inmediato", ""),
            "coordinador": row.get("coordinador", ""),
        }
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
