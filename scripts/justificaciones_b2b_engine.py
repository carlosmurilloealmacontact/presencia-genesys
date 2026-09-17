"""
Motor de Gestión y Persistencia de Justificaciones Operativas B2B (Causa Raíz de NNSS).
Almacena y consulta las justificaciones de pérdida de niveles de servicio en:
1. Neon Postgres (producción / multi-usuario en la nube)
2. SQLite local / JSON de respaldo (offline / fallback resiliente)
"""

import os
import json
import sqlite3
from datetime import datetime, date, timezone, timedelta
import pandas as pd
import streamlit as st

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.normpath(os.path.join(BASE_DIR, ".."))
JUSTIFICACIONES_JSON = os.path.join(PROJECT_ROOT, "data", "justificaciones_b2b.json")
SQLITE_DB_PATH = os.path.join(PROJECT_ROOT, "data", "presencia.db")

DEFAULT_NEON_URL = "postgresql://neondb_owner:npg_u94jTQIadNYr@ep-proud-violet-a5lapj40-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require"

# Mapeo oficial entre claves internas y nombres comunes usados por los líderes
MAPEO_SERVICIOS_B2B = {
    "VOZ ESP": "TARGET ESP",
    "TARGET ESP": "TARGET ESP",
    "VOZ ENG": "TARGET ENG",
    "TARGET ENG": "TARGET ENG",
    "CORPORATE VOZ": "EMPRESAS",
    "CORPORATE PYME": "EMPRESAS",
    "EMPRESAS": "EMPRESAS",
    "CHAT AGY": "AG CHAT ES",
    "AG CHAT ES": "AG CHAT ES",
    "CORPORATE CHAT": "AG CORPORATE CHAT",
    "AG CORPORATE CHAT": "AG CORPORATE CHAT",
    "REMISIONES": "AG CELULA REMISION",
    "AG CELULA REMISION": "AG CELULA REMISION",
    "BO AGENCIAS TARGET": "BO AGENCIAS TARGET",
    "BO_CORPORATE": "BO_CORPORATE"
}

NOMBRES_LEGIBLES = {
    "TARGET ESP": "TARGET ESP (Operacional SSC - Voz)",
    "TARGET ENG": "TARGET ENG (Internacional - Voz)",
    "EMPRESAS": "CORPORATE PYME (Empresas - Voz)",
    "AG CHAT ES": "AG CHAT ES (Agencias Español - Chat)",
    "AG CORPORATE CHAT": "AG CORPORATE CHAT (Corporativo - Chat)",
    "AG CELULA REMISION": "AG CELULA REMISION (NDC - Remisiones)",
    "BO AGENCIAS TARGET": "BO AGENCIAS TARGET (Casos Backoffice)",
    "BO_CORPORATE": "BO_CORPORATE (Casos Corporativos)"
}

MOTIVOS_PREDEFINIDOS = [
    "📈 Sobredemanda de Tráfico (> Forecast)",
    "⏱️ Desvío de AHT por Procesos Largos / Complejos",
    "👥 Falta de Personal / Requerido / Incapacidades",
    "💻 Incidencia Técnica / Caída de Plataforma (SF/Genesys)",
    "🎓 Personal Nuevo / Curva de Aprendizaje",
    "🔄 Rezago de Intervalos Anteriores",
    "⚠️ Múltiples Factores Combinados"
]


def _obtener_db_url() -> str:
    try:
        if "NEON_DB_URL" in st.secrets:
            return str(st.secrets["NEON_DB_URL"]).strip()
    except Exception:
        pass
    return os.environ.get("NEON_DB_URL", DEFAULT_NEON_URL)


def init_justificaciones_db():
    """Inicializa la tabla b2b_justificaciones_ns en Neon Postgres y SQLite local."""
    # 1. Neon Postgres
    db_url = _obtener_db_url()
    try:
        import psycopg2
        conn = psycopg2.connect(db_url, connect_timeout=5)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS b2b_justificaciones_ns (
                fecha VARCHAR(10) NOT NULL,
                servicio_clave VARCHAR(50) NOT NULL,
                ns_real NUMERIC(5,2),
                ns_meta NUMERIC(5,2),
                motivo_principal VARCHAR(100),
                justificacion TEXT NOT NULL,
                registrado_por VARCHAR(150),
                fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (fecha, servicio_clave)
            );
        """)
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[Justificaciones] Aviso Neon Postgres init: {e}")

    # 2. SQLite local
    try:
        os.makedirs(os.path.dirname(SQLITE_DB_PATH), exist_ok=True)
        with sqlite3.connect(SQLITE_DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS b2b_justificaciones_ns (
                    fecha TEXT NOT NULL,
                    servicio_clave TEXT NOT NULL,
                    ns_real REAL,
                    ns_meta REAL,
                    motivo_principal TEXT,
                    justificacion TEXT NOT NULL,
                    registrado_por TEXT,
                    fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (fecha, servicio_clave)
                );
            """)
            conn.commit()
    except Exception as e:
        print(f"[Justificaciones] Aviso SQLite local init: {e}")


def _guardar_en_json(fecha: str, clave: str, datos: dict):
    """Respaldo en JSON local garantizado."""
    try:
        os.makedirs(os.path.dirname(JUSTIFICACIONES_JSON), exist_ok=True)
        data = {}
        if os.path.exists(JUSTIFICACIONES_JSON):
            with open(JUSTIFICACIONES_JSON, "r", encoding="utf-8") as f:
                data = json.load(f)
        if fecha not in data:
            data[fecha] = {}
        data[fecha][clave] = datos
        with open(JUSTIFICACIONES_JSON, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Justificaciones] Error guardando JSON: {e}")


def guardar_justificacion(
    fecha: str,
    servicio: str,
    ns_real: float,
    ns_meta: float,
    motivo_principal: str,
    justificacion: str,
    registrado_por: str = "Coordinación B2B"
) -> bool:
    """Guarda o actualiza una justificación en Neon Postgres, SQLite y JSON."""
    clave = MAPEO_SERVICIOS_B2B.get(servicio.upper().strip(), servicio.strip())
    fecha_str = str(fecha).strip()
    guardado_ok = False

    # 1. Neon Postgres
    db_url = _obtener_db_url()
    try:
        import psycopg2
        conn = psycopg2.connect(db_url, connect_timeout=5)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO b2b_justificaciones_ns
            (fecha, servicio_clave, ns_real, ns_meta, motivo_principal, justificacion, registrado_por, fecha_registro)
            VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (fecha, servicio_clave) DO UPDATE SET
                ns_real = EXCLUDED.ns_real,
                ns_meta = EXCLUDED.ns_meta,
                motivo_principal = EXCLUDED.motivo_principal,
                justificacion = EXCLUDED.justificacion,
                registrado_por = EXCLUDED.registrado_por,
                fecha_registro = CURRENT_TIMESTAMP;
        """, (fecha_str, clave, float(ns_real or 0.0), float(ns_meta or 0.0), motivo_principal, justificacion, registrado_por))
        conn.commit()
        cur.close()
        conn.close()
        guardado_ok = True
    except Exception as e:
        print(f"[Justificaciones] Aviso guardado Neon: {e}")

    # 2. SQLite local
    try:
        with sqlite3.connect(SQLITE_DB_PATH) as conn:
            conn.execute("""
                INSERT INTO b2b_justificaciones_ns
                (fecha, servicio_clave, ns_real, ns_meta, motivo_principal, justificacion, registrado_por, fecha_registro)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT (fecha, servicio_clave) DO UPDATE SET
                    ns_real = excluded.ns_real,
                    ns_meta = excluded.ns_meta,
                    motivo_principal = excluded.motivo_principal,
                    justificacion = excluded.justificacion,
                    registrado_por = excluded.registrado_por,
                    fecha_registro = CURRENT_TIMESTAMP
            """, (fecha_str, clave, float(ns_real or 0.0), float(ns_meta or 0.0), motivo_principal, justificacion, registrado_por))
            conn.commit()
            guardado_ok = True
    except Exception as e:
        print(f"[Justificaciones] Aviso guardado SQLite: {e}")

    # 3. JSON local
    _guardar_en_json(fecha_str, clave, {
        "ns_real": float(ns_real or 0.0),
        "ns_meta": float(ns_meta or 0.0),
        "motivo_principal": motivo_principal,
        "justificacion": justificacion,
        "registrado_por": registrado_por,
        "fecha_registro": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })

    return guardado_ok


def obtener_justificaciones_por_fecha(fecha: str) -> dict:
    """
    Retorna un diccionario {servicio_clave: {motivo, justificacion, registrado_por, ...}}
    para la fecha dada ('YYYY-MM-DD').
    """
    fecha_str = str(fecha).strip()
    resultado = {}

    # 1. Intentar consultar Neon Postgres
    db_url = _obtener_db_url()
    try:
        import psycopg2
        conn = psycopg2.connect(db_url, connect_timeout=5)
        cur = conn.cursor()
        cur.execute("""
            SELECT servicio_clave, ns_real, ns_meta, motivo_principal, justificacion, registrado_por, fecha_registro
            FROM b2b_justificaciones_ns
            WHERE fecha = %s;
        """, (fecha_str,))
        rows = cur.fetchall()
        for r in rows:
            resultado[r[0]] = {
                "ns_real": float(r[1]) if r[1] is not None else 0.0,
                "ns_meta": float(r[2]) if r[2] is not None else 0.0,
                "motivo_principal": r[3] or "",
                "justificacion": r[4] or "",
                "registrado_por": r[5] or "",
                "fecha_registro": str(r[6]) if r[6] else ""
            }
        cur.close()
        conn.close()
        if resultado:
            return resultado
    except Exception:
        pass

    # 2. Intentar SQLite local
    if os.path.exists(SQLITE_DB_PATH):
        try:
            with sqlite3.connect(SQLITE_DB_PATH) as conn:
                cur = conn.cursor()
                cur.execute("""
                    SELECT servicio_clave, ns_real, ns_meta, motivo_principal, justificacion, registrado_por, fecha_registro
                    FROM b2b_justificaciones_ns
                    WHERE fecha = ?;
                """, (fecha_str,))
                for r in cur.fetchall():
                    resultado[r[0]] = {
                        "ns_real": float(r[1]) if r[1] is not None else 0.0,
                        "ns_meta": float(r[2]) if r[2] is not None else 0.0,
                        "motivo_principal": r[3] or "",
                        "justificacion": r[4] or "",
                        "registrado_por": r[5] or "",
                        "fecha_registro": str(r[6]) if r[6] else ""
                    }
                if resultado:
                    return resultado
        except Exception:
            pass

    # 3. Fallback en JSON local
    if os.path.exists(JUSTIFICACIONES_JSON):
        try:
            with open(JUSTIFICACIONES_JSON, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get(fecha_str, {})
        except Exception:
            pass

    return resultado


def obtener_justificaciones_por_rango(fecha_inicio: str, fecha_fin: str) -> dict:
    """
    Retorna un diccionario de justificaciones consolidadas agrupadas por servicio_clave
    para un rango de fechas.
    """
    resultado_acum = {}
    db_url = _obtener_db_url()
    try:
        import psycopg2
        conn = psycopg2.connect(db_url, connect_timeout=5)
        cur = conn.cursor()
        cur.execute("""
            SELECT fecha, servicio_clave, motivo_principal, justificacion
            FROM b2b_justificaciones_ns
            WHERE fecha >= %s AND fecha <= %s
            ORDER BY fecha DESC;
        """, (str(fecha_inicio), str(fecha_fin)))
        for r in cur.fetchall():
            fec, srv, mot, jus = r[0], r[1], r[2], r[3]
            if srv not in resultado_acum:
                resultado_acum[srv] = []
            resultado_acum[srv].append(f"[{fec}] {jus}")
        cur.close()
        conn.close()
        if resultado_acum:
            return {s: " • ".join(items[:2]) for s, items in resultado_acum.items()}
    except Exception:
        pass

    # Fallback JSON
    if os.path.exists(JUSTIFICACIONES_JSON):
        try:
            with open(JUSTIFICACIONES_JSON, "r", encoding="utf-8") as f:
                data = json.load(f)
            for fec, srvs in data.items():
                if str(fecha_inicio) <= fec <= str(fecha_fin):
                    for srv, item in srvs.items():
                        if srv not in resultado_acum:
                            resultado_acum[srv] = []
                        resultado_acum[srv].append(f"[{fec}] {item.get('justificacion', '')}")
            return {s: " • ".join(items[:2]) for s, items in resultado_acum.items()}
        except Exception:
            pass

    return {}


def precargar_justificaciones_ejemplo_ayer():
    """
    Precarga las 6 justificaciones oficiales reportadas por la coordinación
    para el día de ayer (2026-09-16).
    """
    fecha_ayer = "2026-09-16"
    casos = [
        {
            "servicio": "VOZ ESP",
            "ns_real": 63.98,
            "ns_meta": 70.0,
            "motivo": "📈 Sobredemanda de Tráfico (> Forecast)",
            "justificacion": "Cierre NNSS 63,98% ⚠️ Pérdida de NNSS por sobredemanda del 24,10% con una contestación del 13,0%."
        },
        {
            "servicio": "VOZ ENG",
            "ns_real": 64.80,
            "ns_meta": 70.0,
            "motivo": "📈 Sobredemanda de Tráfico (> Forecast)",
            "justificacion": "Cierre NNSS 64,80% 🛑 Pérdida de NNSS por sobredemanda, aunque en el ponderado del día no tenemos una sobredemanda mayor al 10% en los intervalos perdidos (por debajo de 80%). De los 20 intervalos perdidos, 9 tienen sobredemanda por arriba del 10%, 5 tuvimos el AHT fuera de lo programado por procesos largos como cotizaciones y cambios, los restantes (6 intervalos) no logramos pasarlos por el rezago de las llamadas del intervalo anterior, adicional tuvimos un retiro con turno 12:00 - 19:00. Nos ayudó cerrar el AHT en 594s, es decir, -43s."
        },
        {
            "servicio": "CHAT AGY",
            "ns_real": 49.89,
            "ns_meta": 80.0,
            "motivo": "💻 Incidencia Técnica / Caída de Plataforma (SF/Genesys)",
            "justificacion": "Cierre NNSS 49,89% ⚠️ Pérdida de NNSS por caída de Salesforce hasta las 10:00 am y adicional tuvimos un AHT de +295s sobre la meta programada relacionada a procesos largos y 6 personas nuevas en el servicio que ingresaron en el último grupo."
        },
        {
            "servicio": "REMISIONES",
            "ns_real": 66.67,
            "ns_meta": 80.0,
            "motivo": "💻 Incidencia Técnica / Caída de Plataforma (SF/Genesys)",
            "justificacion": "Cierre NNSS 66,67% ⚠️ Pérdida de NNSS por caída de Salesforce hasta las 10:00 am y adicional tuvimos una incapacidad médica (el servicio cuenta con 8 agentes en total)."
        },
        {
            "servicio": "CORPORATE VOZ",
            "ns_real": 40.87,
            "ns_meta": 70.0,
            "motivo": "👥 Falta de Personal / Requerido / Incapacidades",
            "justificacion": "Cierre NNSS 40,87% 🛑 Pérdida de NNSS por falta del requerido: estamos -7 agentes. Para contestar el máximo posible controlamos llamadas largas (-62s AHT). Equipo nuevo ingresa el próximo lunes 21 de septiembre."
        },
        {
            "servicio": "CORPORATE CHAT",
            "ns_real": 78.03,
            "ns_meta": 80.0,
            "motivo": "📈 Sobredemanda de Tráfico (> Forecast)",
            "justificacion": "Cierre NNSS 78,03% ⚠️ Pérdida de NNSS por sobredemanda del 20,80% con una contestación del 18,0% y un AHT favorable de -280s."
        }
    ]

    for c in casos:
        guardar_justificacion(
            fecha=fecha_ayer,
            servicio=c["servicio"],
            ns_real=c["ns_real"],
            ns_meta=c["ns_meta"],
            motivo_principal=c["motivo"],
            justificacion=c["justificacion"],
            registrado_por="Operación Agencias B2B (Oficial)"
        )


def diagnosticar_justificacion_automatica(ns_real: float, ns_meta: float, aht_real: float, aht_meta: float, entrantes: int = 0) -> str:
    """Genera una explicación diagnóstica preliminar basada en las palancas operativas."""
    if ns_real >= ns_meta:
        return "🟢 Meta alcanzada sin desvío"

    dif_ns = ns_meta - ns_real
    desv_aht = aht_real - aht_meta

    if desv_aht > 60:
        return f"⚠️ Cierre NNSS {ns_real:.1f}%: Pérdida de NS asociada a desvío de AHT (+{int(desv_aht)}s sobre meta) por procesos largos."
    elif dif_ns > 15:
        return f"🛑 Cierre NNSS {ns_real:.1f}%: Impacto severo en NS (-{dif_ns:.1f}pp). Causa probable: Sobredemanda en franjas pico o falta de dotación en cola."
    else:
        return f"⚠️ Cierre NNSS {ns_real:.1f}%: A menos de 5pp de meta. Oportunidad en rezago entre intervalos y adherencia."
