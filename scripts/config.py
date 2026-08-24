GENESYS_CONFIG = {
    "base_url": "https://api.mypurecloud.com",
    # Comparte el token con Seguimiento Pausas 4DX en vez de mantener una copia
    # propia: ese pipeline ya lo refresca (login SSO+GridSure) antes de que
    # corra esta extraccion, asi evitamos duplicar ese login.
    "token_file": "../../Seguimiento Pausas 4DX/scripts/genesys_token.txt",
}

# Ubicaciones a filtrar (mismas que Seguimiento Pausas 4DX)
LOCATION_IDS = [
    "8cc0066f-4791-45b2-9c78-d0d9f084393e",  # AMC - Sede Bogotá - Colombia
    "4d1a1f46-b39a-4b22-ab9b-ac8f0bb2f9e5",  # AMC - Sede Medellín - Colombia
]

import os

# Base maestra local: historial completo (60 dias), nunca se sube a GitHub.
MASTER_DB_PATH = "../data/presencia_master.db"

# La extraccion y el backfill siempre escriben en la maestra. El visor lee
# DB_PATH, que por defecto ES la maestra (uso local) pero en Streamlit Cloud
# se sobreescribe con la variable de entorno PRESENCIA_DB_PATH para apuntar a
# la copia recortada (ver export_cloud.py) que si viaja en el repo publico.
DB_PATH = os.environ.get("PRESENCIA_DB_PATH", MASTER_DB_PATH)
RETENTION_DIAS = 60

# Copia recortada que se sube al repo de GitHub / Streamlit Cloud.
CLOUD_EXPORT_PATH = "../data/presencia.db"
CLOUD_RETENTION_DIAS = 35
