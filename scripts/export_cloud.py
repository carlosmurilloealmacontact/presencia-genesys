"""
Genera la copia recortada (ultimos CLOUD_RETENTION_DIAS dias) de la base
maestra que se sube al repo de GitHub para Streamlit Community Cloud.

La maestra local no tiene limite de tamaño para git; esta copia si, porque
GitHub rechaza archivos de mas de 100MB.

Uso:
    python export_cloud.py
"""

import sys
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from config import MASTER_DB_PATH, CLOUD_EXPORT_PATH, CLOUD_RETENTION_DIAS
from db import SCHEMA


def run():
    base_dir = Path(__file__).parent
    master_path = base_dir / MASTER_DB_PATH
    export_path = base_dir / CLOUD_EXPORT_PATH

    if not master_path.exists():
        print(f"No existe la base maestra en {master_path}. Corre extract_presencia.py primero.")
        return

    corte = (datetime.now() - timedelta(days=CLOUD_RETENTION_DIAS)).strftime("%Y-%m-%d")
    print(f"Exportando tramos desde {corte} en adelante...")

    export_path.unlink(missing_ok=True)
    export_conn = sqlite3.connect(export_path)
    export_conn.executescript(SCHEMA)

    master_conn = sqlite3.connect(master_path)
    filas = master_conn.execute("SELECT * FROM segments WHERE fecha >= ?", (corte,)).fetchall()
    columnas = [d[0] for d in master_conn.execute("SELECT * FROM segments LIMIT 0").description]

    placeholders = ", ".join("?" for _ in columnas)
    export_conn.executemany(f"INSERT INTO segments ({', '.join(columnas)}) VALUES ({placeholders})", filas)

    # Turnos (para Adherencia Horario) - mismo recorte de fecha
    turnos = master_conn.execute("SELECT * FROM turnos WHERE fecha >= ?", (corte,)).fetchall()
    export_conn.executemany("INSERT INTO turnos (bp, fecha, hora_inicio, hora_fin) VALUES (?, ?, ?, ?)", turnos)

    export_conn.commit()
    export_conn.execute("VACUUM")
    export_conn.close()
    master_conn.close()

    tamano_mb = export_path.stat().st_size / (1024 * 1024)
    print(f"{len(filas)} tramos y {len(turnos)} turnos exportados. Archivo: {tamano_mb:.1f} MB")
    if tamano_mb > 95:
        print("AVISO: se está acercando al límite de 100MB de GitHub — considera bajar CLOUD_RETENTION_DIAS.")


if __name__ == "__main__":
    run()
