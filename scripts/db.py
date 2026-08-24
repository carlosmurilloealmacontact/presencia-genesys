"""
Base de datos SQLite para los tramos de presencia diarios.

SQLite es un archivo unico (presencia.db) sin servidor que instalar: Python
ya trae el modulo sqlite3 incorporado. Cada consulta abre el archivo, lee o
escribe, y lo cierra - no hay nada que "levantar" como con Postgres/MySQL.
"""

import sqlite3
from pathlib import Path

from config import MASTER_DB_PATH, RETENTION_DIAS

SCHEMA = """
CREATE TABLE IF NOT EXISTS segments (
    fecha TEXT NOT NULL,
    agente_id TEXT NOT NULL,
    agente TEXT NOT NULL,
    servicio TEXT NOT NULL DEFAULT '',
    jefe_inmediato TEXT NOT NULL DEFAULT '',
    coordinador TEXT NOT NULL DEFAULT '',
    presence_label TEXT NOT NULL,
    system_presence TEXT NOT NULL,
    inicio TEXT NOT NULL,
    fin TEXT,
    duracion_min REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_segments_fecha ON segments(fecha);
CREATE INDEX IF NOT EXISTS idx_segments_agente ON segments(agente_id, fecha);
"""


def get_connection() -> sqlite3.Connection:
    """Conecta a la base MAESTRA local (extraccion/backfill escriben aqui, no en el export)."""
    db_path = Path(__file__).parent / MASTER_DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)

    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(segments)")}
    for col in ("servicio", "jefe_inmediato", "coordinador"):
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE segments ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
    conn.commit()
    return conn


_INSERT_SQL = """
    INSERT INTO segments (
        fecha, agente_id, agente, servicio, jefe_inmediato, coordinador,
        presence_label, system_presence, inicio, fin, duracion_min
    )
    VALUES (
        :fecha, :agente_id, :agente, :servicio, :jefe_inmediato, :coordinador,
        :presence_label, :system_presence, :inicio, :fin, :duracion_min
    )
"""


def replace_day(conn: sqlite3.Connection, fecha: str, rows: list[dict]) -> None:
    """Reemplaza todos los tramos de una fecha (permite re-correr el dia sin duplicar)."""
    conn.execute("DELETE FROM segments WHERE fecha = ?", (fecha,))
    conn.executemany(_INSERT_SQL, rows)
    conn.commit()


def replace_range(conn: sqlite3.Connection, fecha_min: str, fecha_max: str, rows: list[dict]) -> None:
    """Reemplaza todos los tramos dentro de [fecha_min, fecha_max] (permite re-correr sin duplicar)."""
    conn.execute("DELETE FROM segments WHERE fecha BETWEEN ? AND ?", (fecha_min, fecha_max))
    conn.executemany(_INSERT_SQL, rows)
    conn.commit()


def purge_old(conn: sqlite3.Connection) -> int:
    """Borra tramos mas viejos que RETENTION_DIAS. Devuelve cuantas filas borro."""
    cur = conn.execute(
        "DELETE FROM segments WHERE fecha < date('now', ?)",
        (f"-{RETENTION_DIAS} days",),
    )
    conn.commit()
    return cur.rowcount
