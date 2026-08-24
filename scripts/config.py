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

# Base maestra local: historial completo (60 dias). NUNCA se sube a GitHub
# (ver .gitignore) - solo la usan extract_presencia.py y backfill_presencia.py.
MASTER_DB_PATH = "../data/presencia_master.db"
RETENTION_DIAS = 60

# Copia recortada (ultimos CLOUD_RETENTION_DIAS dias) que SI viaja en el repo
# de GitHub. El visor (viewer.py) siempre lee este archivo - tanto en tu
# maquina como en Streamlit Cloud - asi ambos se comportan igual sin
# necesitar configurar nada aparte en la nube.
DB_PATH = "../data/presencia.db"
CLOUD_EXPORT_PATH = DB_PATH
CLOUD_RETENTION_DIAS = 35
