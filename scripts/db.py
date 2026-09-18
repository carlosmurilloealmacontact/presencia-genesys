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
    cargo TEXT NOT NULL DEFAULT '',
    estado_laboral TEXT NOT NULL DEFAULT 'Activo',
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
CREATE INDEX IF NOT EXISTS idx_segments_agente_id ON segments(agente_id);
CREATE INDEX IF NOT EXISTS idx_segments_coord ON segments(coordinador);
CREATE INDEX IF NOT EXISTS idx_segments_jefe ON segments(jefe_inmediato);
CREATE INDEX IF NOT EXISTS idx_segments_servicio ON segments(servicio);
CREATE INDEX IF NOT EXISTS idx_segments_fecha_srv ON segments(fecha, servicio);
CREATE INDEX IF NOT EXISTS idx_segments_cargo ON segments(cargo);

CREATE TABLE IF NOT EXISTS dim_agentes (
    agente_id TEXT PRIMARY KEY,
    agente TEXT NOT NULL,
    cargo TEXT NOT NULL DEFAULT '',
    estado_laboral TEXT NOT NULL DEFAULT 'Activo',
    servicio TEXT NOT NULL DEFAULT '',
    jefe_inmediato TEXT NOT NULL DEFAULT '',
    coordinador TEXT NOT NULL DEFAULT '',
    ultima_actualizacion TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dim_agentes_cargo ON dim_agentes(cargo);
CREATE INDEX IF NOT EXISTS idx_dim_agentes_estado ON dim_agentes(estado_laboral);

CREATE TABLE IF NOT EXISTS turnos (
    bp TEXT NOT NULL,
    fecha TEXT NOT NULL,
    hora_inicio TEXT NOT NULL,
    hora_fin TEXT NOT NULL,
    PRIMARY KEY (bp, fecha)
);

CREATE TABLE IF NOT EXISTS turnos_detallados (
    bp TEXT NOT NULL,
    fecha TEXT NOT NULL,
    documento TEXT,
    nombre_agente TEXT,
    servicio TEXT,
    novedad TEXT,
    horas_programadas REAL DEFAULT 8.0,
    turno_ini TEXT,
    turno_fin TEXT,
    dialogo_ini TEXT,
    dialogo_fin TEXT,
    des_1_ini TEXT,
    des_1_fin TEXT,
    des_2_ini TEXT,
    des_2_fin TEXT,
    des_3_ini TEXT,
    des_3_fin TEXT,
    lunch_ini TEXT,
    lunch_fin TEXT,
    training_1_ini TEXT,
    training_1_fin TEXT,
    PRIMARY KEY (bp, fecha)
);
CREATE INDEX IF NOT EXISTS idx_td_fecha ON turnos_detallados(fecha);
CREATE INDEX IF NOT EXISTS idx_td_bp ON turnos_detallados(bp);
CREATE INDEX IF NOT EXISTS idx_td_servicio ON turnos_detallados(servicio);
"""


def get_connection() -> sqlite3.Connection:
    """Conecta a la base MAESTRA local (extraccion/backfill escriben aqui, no en el export)."""
    db_path = Path(__file__).parent / MASTER_DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.executescript(SCHEMA)

    existing_cols = {row[1] for row in conn.execute("PRAGMA table_info(segments)")}
    for col in ("cargo", "estado_laboral", "servicio", "jefe_inmediato", "coordinador"):
        if col not in existing_cols:
            conn.execute(f"ALTER TABLE segments ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
    conn.commit()
    return conn


def refrescar_dim_agentes(conn: sqlite3.Connection) -> None:
    """Actualiza la tabla dimensional de agentes para consultas ultra-rápidas (<2ms) en el visor."""
    conn.execute("""
        INSERT INTO dim_agentes (agente_id, agente, cargo, estado_laboral, servicio, jefe_inmediato, coordinador, ultima_actualizacion)
        SELECT agente_id, agente, cargo, estado_laboral, servicio, jefe_inmediato, coordinador, CURRENT_TIMESTAMP
        FROM segments
        GROUP BY agente_id
        ON CONFLICT(agente_id) DO UPDATE SET
            agente = excluded.agente,
            cargo = excluded.cargo,
            estado_laboral = excluded.estado_laboral,
            servicio = excluded.servicio,
            jefe_inmediato = excluded.jefe_inmediato,
            coordinador = excluded.coordinador,
            ultima_actualizacion = CURRENT_TIMESTAMP;
    """)
    conn.commit()



_INSERT_SQL = """
    INSERT INTO segments (
        fecha, agente_id, agente, cargo, estado_laboral, servicio, jefe_inmediato, coordinador,
        presence_label, system_presence, inicio, fin, duracion_min
    )
    VALUES (
        :fecha, :agente_id, :agente, :cargo, :estado_laboral, :servicio, :jefe_inmediato, :coordinador,
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


def guardar_turnos(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """
    Upsert de turnos por (bp, fecha). El archivo fuente solo muestra el mes en
    curso (se actualiza a mano y no retiene historico), asi que NUNCA se borra
    lo que ya tenemos - cada corrida solo agrega/reemplaza lo que el archivo
    trae en ese momento, preservando turnos de meses anteriores ya capturados.
    """
    conn.executemany(
        """
        INSERT INTO turnos (bp, fecha, hora_inicio, hora_fin)
        VALUES (:bp, :fecha, :hora_inicio, :hora_fin)
        ON CONFLICT(bp, fecha) DO UPDATE SET
            hora_inicio = excluded.hora_inicio,
            hora_fin = excluded.hora_fin
        """,
        rows,
    )
    conn.commit()


def guardar_turnos_detallados(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """
    Upsert de turnos detallados con pausas programadas y horas laboradas.
    """
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO turnos_detallados (
            bp, fecha, documento, nombre_agente, servicio, novedad,
            horas_programadas, turno_ini, turno_fin, dialogo_ini, dialogo_fin,
            des_1_ini, des_1_fin, des_2_ini, des_2_fin, des_3_ini, des_3_fin,
            lunch_ini, lunch_fin, training_1_ini, training_1_fin
        )
        VALUES (
            :bp, :fecha, :documento, :nombre_agente, :servicio, :novedad,
            :horas_programadas, :turno_ini, :turno_fin, :dialogo_ini, :dialogo_fin,
            :des_1_ini, :des_1_fin, :des_2_ini, :des_2_fin, :des_3_ini, :des_3_fin,
            :lunch_ini, :lunch_fin, :training_1_ini, :training_1_fin
        )
        ON CONFLICT(bp, fecha) DO UPDATE SET
            documento = excluded.documento,
            nombre_agente = excluded.nombre_agente,
            servicio = excluded.servicio,
            novedad = excluded.novedad,
            horas_programadas = excluded.horas_programadas,
            turno_ini = excluded.turno_ini,
            turno_fin = excluded.turno_fin,
            dialogo_ini = excluded.dialogo_ini,
            dialogo_fin = excluded.dialogo_fin,
            des_1_ini = excluded.des_1_ini,
            des_1_fin = excluded.des_1_fin,
            des_2_ini = excluded.des_2_ini,
            des_2_fin = excluded.des_2_fin,
            des_3_ini = excluded.des_3_ini,
            des_3_fin = excluded.des_3_fin,
            lunch_ini = excluded.lunch_ini,
            lunch_fin = excluded.lunch_fin,
            training_1_ini = excluded.training_1_ini,
            training_1_fin = excluded.training_1_fin
        """,
        rows,
    )
    conn.commit()


def purge_old(conn: sqlite3.Connection) -> int:
    """Borra tramos mas viejos que RETENTION_DIAS. Devuelve cuantas filas borro."""
    cur = conn.execute(
        "DELETE FROM segments WHERE fecha < date('now', ?)",
        (f"-{RETENTION_DIAS} days",),
    )
    conn.commit()
    return cur.rowcount
