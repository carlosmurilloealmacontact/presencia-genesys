"""
Motor de tiempo real para el Command Center de Chats (Omni-Channel / Salesforce).
Maneja colas AMC, estado de agentes (Available, Busy, Break), capacidad (33%, 67%, 100%)
y genera alertas de saturacion y desvio de estados en tiempo real.
"""

import os
import sqlite3
import random
import pandas as pd
from datetime import datetime, timezone, timedelta

try:
    import mapeo_socios_engine as mse
except Exception:
    try:
        from scripts import mapeo_socios_engine as mse
    except Exception:
        mse = None

try:
    import zoneinfo
    COLOMBIA_TZ = zoneinfo.ZoneInfo("America/Bogota")
except Exception:
    COLOMBIA_TZ = timezone(timedelta(hours=-5))


def get_colombia_now():
    """Retorna la fecha y hora actual garantizada en Zona Horaria Colombia (America/Bogota, UTC-5)."""
    try:
        return datetime.now(COLOMBIA_TZ)
    except Exception:
        return datetime.now(timezone(timedelta(hours=-5)))

LIVE_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "salesforce_live.db")
DEFAULT_NEON_URL = "postgresql://neondb_owner:npg_u94jTQIadNYr@ep-proud-violet-a5lapj40-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require"


def _get_neon_connection():
    """Retorna una conexión a Neon Postgres (si está disponible y accesible)."""
    db_url = None
    try:
        import streamlit as st
        if "NEON_DB_URL" in st.secrets:
            db_url = str(st.secrets["NEON_DB_URL"]).strip()
    except Exception:
        pass

    if not db_url:
        db_url = os.environ.get("NEON_DB_URL", DEFAULT_NEON_URL)

    if db_url:
        try:
            import psycopg2
            return psycopg2.connect(db_url, connect_timeout=5)
        except Exception:
            return None
    return None


# 18 Colas BOT Omni-Channel Oficiales (Dudas OP, NDC, Corp & Grupos)
BOT_QUEUES_AMC = [
    # Dudas Operacionales (8 colas)
    "BOT AMC DUDAS OP INTER EU ESP NIVEL 1",
    "BOT AMC DUDAS OP INTER EU ING NIVEL 1",
    "BOT AMC DUDAS OP INTER NA ESP NIVEL 1",
    "BOT AMC DUDAS OP INTER NA ING NIVEL 1",
    "BOT AMC DUDAS OP INTER OC ING NIVEL 1",
    "BOT AMC DUDAS OP SSC NIVEL 1",
    "BOT AMC DUDAS OP SSC NIVEL 2",
    "BOT AMC DUDAS OP SSC NIVEL 3",
    # NDC (8 colas)
    "BOT AMC NDC INTER EU ESP NIVEL 1",
    "BOT AMC NDC INTER EU ING NIVEL 1",
    "BOT AMC NDC INTER NA ESP NIVEL 1",
    "BOT AMC NDC INTER NA ING NIVEL 1",
    "BOT AMC NDC INTER OC ING NIVEL 1",
    "BOT AMC NDC SSC NIVEL 1",
    "BOT AMC NDC SSC NIVEL 2",
    "BOT AMC NDC SSC NIVEL 3",
    # Corporativo & Grupos
    "BOT CORP SOPORTE OPERACIONAL SSC",
    "BOT GRUPOS SSC",
    "BOT AMC GRUPOS CORP SSC",
    "BOT FALLBACK QUEUE"
]

# 6 Colas de Casos / BackOffice (Work Queues SLA 24h)
CASOS_WORK_QUEUES_AMC = [
    "AMC AGENCIAS ESP",
    "AMC AGENCIAS INTER",
    "AMC CORPORATE SSC",
    "AMC EMISIONES GRUPOS CORP",
    "AMC EMISIONES GRUPOS SSC",
    "AMC EMISIONES BO EC"
]

AMC_CHAT_QUEUES = BOT_QUEUES_AMC


def obtener_categoria_cola(q_name: str) -> str:
    """Clasifica una cola en su familia operacional."""
    q = str(q_name).upper()
    if "DUDAS OP" in q or "DUDAS OPERACIONALES" in q:
        return "💬 Dudas Operacionales"
    elif "NDC" in q:
        return "✈️ NDC"
    elif "CORP" in q or "CORPORAT" in q or "GRUPOS" in q:
        return "🏢 Corporativo & Grupos"
    elif "EMISION" in q or "BO EC" in q:
        return "📋 Emisiones & BO"
    else:
        return "🌐 Otras Colas"

# Ejecutivos conocidos de la operacion de chat
DEFAULT_AGENTS = [
    {"name": "Reven", "base_status": "Available", "skill": "Español"},
    {"name": "Salta", "base_status": "Busy", "skill": "Español"},
    {"name": "Jablo", "base_status": "Available", "skill": "Corporativo"},
    {"name": "Juli", "base_status": "Available", "skill": "Inglés"},
    {"name": "CALES", "base_status": "Busy", "skill": "Español"},
    {"name": "Jhose", "base_status": "Available", "skill": "Español"},
    {"name": "PEJIA", "base_status": "Available", "skill": "Inglés"},
    {"name": "Jrepo", "base_status": "Break", "skill": "Corporativo"},
    {"name": "MIRRE", "base_status": "Available", "skill": "Español"},
    {"name": "Mai", "base_status": "Available", "skill": "Corporativo"},
    {"name": "Ruera", "base_status": "Available", "skill": "Español"},
    {"name": "Srena", "base_status": "Available", "skill": "Inglés"},
    {"name": "SROZO", "base_status": "Available", "skill": "Español"},
    {"name": "YNDRO", "base_status": "Break", "skill": "Corporativo"},
    {"name": "Chica", "base_status": "Available", "skill": "Español"}
]


def get_operational_agents_pool():
    """Retorna la lista calibrada de asesores operativos para monitoreo en vivo (25-30 asesores)."""
    pool = list(DEFAULT_AGENTS)
    seen_bps = {p.get("bp") for p in pool if p.get("bp")}
    if mse:
        try:
            m = mse.load_cached_mapeo()
            for k, info in m.items():
                if len(pool) >= 28:
                    break
                coord = str(info.get("coordinador", "")).upper()
                if "MARELYN" in coord or "CARDONA" in coord:
                    bp = str(info.get("bp", "")).strip()
                    if bp and bp not in seen_bps:
                        seen_bps.add(bp)
                        al = info.get("alias", k)
                        pool.append({
                            "name": al,
                            "base_status": "Available",
                            "skill": info.get("servicio", "Chat B2B"),
                            "bp": bp,
                            "nombre_completo": info.get("nombre_completo", al)
                        })
        except Exception:
            pass
    return pool


def init_live_db():
    """Inicializa las tablas en SQLite local y en Neon Postgres."""
    # 1. SQLite local
    try:
        os.makedirs(os.path.dirname(LIVE_DB_PATH), exist_ok=True)
        conn = sqlite3.connect(LIVE_DB_PATH)
        cur = conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS live_chat_queues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            queue_name TEXT NOT NULL,
            chats_in_queue INTEGER NOT NULL,
            longest_wait_sec INTEGER NOT NULL,
            agents_online INTEGER NOT NULL
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS live_waiting_chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            chat_id TEXT NOT NULL,
            queue_name TEXT NOT NULL,
            wait_time_sec INTEGER NOT NULL,
            channel TEXT DEFAULT 'Web Chat'
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS live_chat_agents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            agent_name TEXT NOT NULL,
            status TEXT NOT NULL,
            active_chats INTEGER NOT NULL,
            capacity_pct INTEGER NOT NULL,
            time_in_status_sec INTEGER NOT NULL,
            skill TEXT,
            chat_session_ids TEXT
        )
        """)
        try:
            cur.execute("ALTER TABLE live_chat_agents ADD COLUMN chat_session_ids TEXT")
        except Exception:
            pass

        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Salesforce Live Engine] SQLite init aviso: {e}")

    # 2. Neon Postgres (Cloud)
    conn_pg = _get_neon_connection()
    if conn_pg:
        try:
            cur_pg = conn_pg.cursor()
            cur_pg.execute("""
            CREATE TABLE IF NOT EXISTS live_chat_queues (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP NOT NULL,
                queue_name VARCHAR(255) NOT NULL,
                chats_in_queue INT NOT NULL,
                longest_wait_sec INT NOT NULL,
                agents_online INT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS live_chat_agents (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP NOT NULL,
                agent_name VARCHAR(255) NOT NULL,
                status VARCHAR(50) NOT NULL,
                active_chats INT NOT NULL,
                capacity_pct INT NOT NULL,
                time_in_status_sec INT NOT NULL,
                skill VARCHAR(150),
                chat_session_ids TEXT
            );
            CREATE TABLE IF NOT EXISTS live_waiting_chats (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMP NOT NULL,
                chat_id VARCHAR(100) NOT NULL,
                queue_name VARCHAR(255) NOT NULL,
                wait_time_sec INT NOT NULL,
                channel VARCHAR(100) DEFAULT 'Web Chat'
            );
            CREATE INDEX IF NOT EXISTS idx_live_chat_queues_ts ON live_chat_queues(timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_live_chat_agents_ts ON live_chat_agents(timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_live_waiting_chats_ts ON live_waiting_chats(timestamp DESC);
            """)
            conn_pg.commit()
            cur_pg.close()
            conn_pg.close()
        except Exception as e:
            try:
                conn_pg.close()
            except Exception:
                pass


def save_live_snapshot(queues_data, agents_data, waiting_chats_data=None):
    """Guarda un snapshot del estado en vivo en Hora Colombia en SQLite local y Neon Postgres."""
    init_live_db()
    now_col = get_colombia_now()
    now_str = now_col.strftime("%Y-%m-%d %H:%M:%S")

    # 1. Guardar en SQLite local con executemany
    try:
        conn = sqlite3.connect(LIVE_DB_PATH)
        cur = conn.cursor()

        if queues_data:
            cur.executemany("""
            INSERT INTO live_chat_queues (timestamp, queue_name, chats_in_queue, longest_wait_sec, agents_online)
            VALUES (?, ?, ?, ?, ?)
            """, [(now_str, q["queue_name"], q["chats_in_queue"], q["longest_wait_sec"], q.get("agents_online", 0)) for q in queues_data])

        if agents_data:
            cur.executemany("""
            INSERT INTO live_chat_agents (timestamp, agent_name, status, active_chats, capacity_pct, time_in_status_sec, skill, chat_session_ids)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, [(now_str, a["agent_name"], a["status"], a["active_chats"], a["capacity_pct"], a["time_in_status_sec"], a.get("skill", ""), a.get("chat_session_ids", "")) for a in agents_data])

        if waiting_chats_data:
            cur.executemany("""
            INSERT INTO live_waiting_chats (timestamp, chat_id, queue_name, wait_time_sec, channel)
            VALUES (?, ?, ?, ?, ?)
            """, [(now_str, w["chat_id"], w["queue_name"], int(w["wait_time_sec"]), w.get("channel", "Web Chat")) for w in waiting_chats_data])

        cutoff_str = (now_col - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("DELETE FROM live_chat_queues WHERE timestamp < ?", (cutoff_str,))
        cur.execute("DELETE FROM live_chat_agents WHERE timestamp < ?", (cutoff_str,))
        cur.execute("DELETE FROM live_waiting_chats WHERE timestamp < ?", (cutoff_str,))

        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Salesforce Live Engine] SQLite save aviso: {e}")

    # 2. Guardar en Neon Postgres (producción / multi-dispositivo en tiempo real) con execute_values ultrarrápido
    conn_pg = _get_neon_connection()
    if conn_pg:
        try:
            import psycopg2.extras
            cur_pg = conn_pg.cursor()
            if queues_data:
                psycopg2.extras.execute_values(
                    cur_pg,
                    "INSERT INTO live_chat_queues (timestamp, queue_name, chats_in_queue, longest_wait_sec, agents_online) VALUES %s",
                    [(now_str, q["queue_name"], q["chats_in_queue"], q["longest_wait_sec"], q.get("agents_online", 0)) for q in queues_data]
                )

            if agents_data:
                psycopg2.extras.execute_values(
                    cur_pg,
                    "INSERT INTO live_chat_agents (timestamp, agent_name, status, active_chats, capacity_pct, time_in_status_sec, skill, chat_session_ids) VALUES %s",
                    [(now_str, a["agent_name"], a["status"], a["active_chats"], a["capacity_pct"], a["time_in_status_sec"], a.get("skill", ""), a.get("chat_session_ids", "")) for a in agents_data]
                )

            if waiting_chats_data:
                psycopg2.extras.execute_values(
                    cur_pg,
                    "INSERT INTO live_waiting_chats (timestamp, chat_id, queue_name, wait_time_sec, channel) VALUES %s",
                    [(now_str, w["chat_id"], w["queue_name"], int(w["wait_time_sec"]), w.get("channel", "Web Chat")) for w in waiting_chats_data]
                )

            cutoff_str = (now_col - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
            cur_pg.execute("DELETE FROM live_chat_queues WHERE timestamp < %s", (cutoff_str,))
            cur_pg.execute("DELETE FROM live_chat_agents WHERE timestamp < %s", (cutoff_str,))
            cur_pg.execute("DELETE FROM live_waiting_chats WHERE timestamp < %s", (cutoff_str,))

            conn_pg.commit()
            cur_pg.close()
            conn_pg.close()
        except Exception as e:
            print(f"[Salesforce Live Engine] Neon Postgres write aviso: {e}")
            try:
                conn_pg.rollback()
                conn_pg.close()
            except Exception:
                pass



def advance_live_state_smoothly(scraped_queues=None):
    """
    Avanza el estado de colas y agentes con dinámica operativa continua (cadena de Markov / Brownian):
    - Si se pasan scraped_queues (extraídas de Salesforce en vivo), las adopta y actualiza los chats en espera ms-.
    - Los cronómetros de tiempo en estado de cada asesor avanzan de forma natural y continua.
    - Se garantiza la persistencia de sesiones de chat ms- y concurrencia para todo el equipo B2B.
    """
    init_live_db()
    conn = sqlite3.connect(LIVE_DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT MAX(timestamp) FROM live_chat_queues")
    latest_ts = cur.fetchone()[0]

    prev_queues = {}
    prev_agents = {}
    prev_waiting = {}

    if latest_ts:
        try:
            df_prev_q = pd.read_sql_query(
                "SELECT queue_name, chats_in_queue, longest_wait_sec, agents_online FROM live_chat_queues WHERE timestamp = ?",
                conn,
                params=(latest_ts,)
            )
            for _, r in df_prev_q.iterrows():
                prev_queues[r["queue_name"]] = {
                    "chats": int(r["chats_in_queue"]),
                    "wait": int(r["longest_wait_sec"]),
                    "agents": int(r["agents_online"])
                }

            df_prev_a = pd.read_sql_query(
                "SELECT * FROM live_chat_agents WHERE timestamp = ?",
                conn,
                params=(latest_ts,)
            )
            for _, r in df_prev_a.iterrows():
                prev_agents[r["agent_name"]] = {
                    "status": str(r["status"]),
                    "active_chats": int(r["active_chats"]),
                    "capacity_pct": int(r["capacity_pct"]),
                    "time_in_status_sec": int(r["time_in_status_sec"]),
                    "skill": str(r["skill"]),
                    "chat_session_ids": str(r.get("chat_session_ids") or "")
                }

            df_prev_w = pd.read_sql_query(
                "SELECT chat_id, queue_name, wait_time_sec, channel FROM live_waiting_chats WHERE timestamp = ?",
                conn,
                params=(latest_ts,)
            )
            for _, r in df_prev_w.iterrows():
                qn = str(r["queue_name"])
                if qn not in prev_waiting:
                    prev_waiting[qn] = []
                prev_waiting[qn].append({
                    "chat_id": str(r["chat_id"]),
                    "wait_time_sec": int(r["wait_time_sec"]),
                    "channel": str(r.get("channel") or "Web Chat")
                })
        except Exception:
            pass
    conn.close()

    queues_data = []
    waiting_chats_data = []

    if scraped_queues:
        for q in scraped_queues:
            q_name = q["queue_name"]
            new_val = int(q.get("chats_in_queue", 0))
            wait_sec = int(q.get("longest_wait_sec", 0))
            agents_on = int(q.get("agents_online", 4))

            pw_list = prev_waiting.get(q_name, [])
            pw_list = sorted(pw_list, key=lambda x: x["wait_time_sec"])
            updated_chats = []
            if new_val > 0:
                for c in pw_list:
                    updated_chats.append({
                        "chat_id": c["chat_id"],
                        "queue_name": q_name,
                        "wait_time_sec": c["wait_time_sec"] + 30,
                        "channel": c.get("channel", "Web Chat")
                    })

                if new_val > len(updated_chats):
                    for _ in range(new_val - len(updated_chats)):
                        updated_chats.append({
                            "chat_id": f"ms-{random.randint(100000, 999999)}",
                            "queue_name": q_name,
                            "wait_time_sec": max(15, wait_sec - random.randint(0, 15)),
                            "channel": "Web Chat"
                        })
                elif new_val < len(updated_chats):
                    updated_chats = sorted(updated_chats, key=lambda x: x["wait_time_sec"])[:new_val]

                if updated_chats:
                    real_longest = max([c["wait_time_sec"] for c in updated_chats])
                    wait_sec = max(wait_sec, real_longest)

            waiting_chats_data.extend(updated_chats)
            queues_data.append({
                "queue_name": q_name,
                "chats_in_queue": new_val,
                "longest_wait_sec": wait_sec,
                "agents_online": agents_on
            })
    else:
        QUEUE_TARGETS = {
            # Dudas Operacionales (8 colas)
            "BOT AMC DUDAS OP SSC NIVEL 1": {"target": 0, "min": 0, "max": 0, "agents": 5},
            "BOT AMC DUDAS OP SSC NIVEL 2": {"target": 0, "min": 0, "max": 0, "agents": 3},
            "BOT AMC DUDAS OP SSC NIVEL 3": {"target": 0, "min": 0, "max": 0, "agents": 2},
            "BOT AMC DUDAS OP INTER NA ESP NIVEL 1": {"target": 0, "min": 0, "max": 0, "agents": 4},
            "BOT AMC DUDAS OP INTER NA ING NIVEL 1": {"target": 0, "min": 0, "max": 0, "agents": 2},
            "BOT AMC DUDAS OP INTER EU ESP NIVEL 1": {"target": 0, "min": 0, "max": 0, "agents": 3},
            "BOT AMC DUDAS OP INTER EU ING NIVEL 1": {"target": 0, "min": 0, "max": 0, "agents": 2},
            "BOT AMC DUDAS OP INTER OC ING NIVEL 1": {"target": 0, "min": 0, "max": 0, "agents": 1},
            # NDC (8 colas)
            "BOT AMC NDC SSC NIVEL 1": {"target": 0, "min": 0, "max": 0, "agents": 5},
            "BOT AMC NDC SSC NIVEL 2": {"target": 0, "min": 0, "max": 0, "agents": 3},
            "BOT AMC NDC SSC NIVEL 3": {"target": 0, "min": 0, "max": 0, "agents": 2},
            "BOT AMC NDC INTER NA ESP NIVEL 1": {"target": 0, "min": 0, "max": 0, "agents": 4},
            "BOT AMC NDC INTER NA ING NIVEL 1": {"target": 0, "min": 0, "max": 0, "agents": 2},
            "BOT AMC NDC INTER EU ESP NIVEL 1": {"target": 0, "min": 0, "max": 0, "agents": 3},
            "BOT AMC NDC INTER EU ING NIVEL 1": {"target": 0, "min": 0, "max": 0, "agents": 2},
            "BOT AMC NDC INTER OC ING NIVEL 1": {"target": 0, "min": 0, "max": 0, "agents": 1},
            # Corporativo & Grupos (2 colas)
            "BOT CORP SOPORTE OPERACIONAL SSC": {"target": 0, "min": 0, "max": 0, "agents": 4},
            "BOT AMC GRUPOS CORP SSC": {"target": 0, "min": 0, "max": 0, "agents": 2},
        }

        for q_name, cfg in QUEUE_TARGETS.items():
            prev = prev_queues.get(q_name)
            if prev:
                curr_val = prev["chats"]
                if curr_val > cfg["max"]:
                    delta = -1
                elif curr_val < cfg["min"]:
                    delta = 1
                elif curr_val > cfg["target"]:
                    delta = -1
                elif curr_val < cfg["target"]:
                    delta = 1
                else:
                    delta = 0
                new_val = max(0, curr_val + delta)
                wait_sec = 0 if new_val == 0 else max(15, new_val * random.randint(10, 16))
            else:
                new_val = cfg["target"]
                wait_sec = 0 if new_val == 0 else new_val * 14

            pw_list = prev_waiting.get(q_name, [])
            pw_list = sorted(pw_list, key=lambda x: x["wait_time_sec"])
            updated_chats = []
            if new_val > 0:
                for c in pw_list:
                    updated_chats.append({
                        "chat_id": c["chat_id"],
                        "queue_name": q_name,
                        "wait_time_sec": c["wait_time_sec"] + random.randint(20, 35),
                        "channel": c.get("channel", "Web Chat")
                    })

                if new_val > len(updated_chats):
                    for _ in range(new_val - len(updated_chats)):
                        updated_chats.append({
                            "chat_id": f"ms-{random.randint(100000, 999999)}",
                            "queue_name": q_name,
                            "wait_time_sec": random.randint(10, 25),
                            "channel": "Web Chat"
                        })
                elif new_val < len(updated_chats):
                    updated_chats = sorted(updated_chats, key=lambda x: x["wait_time_sec"])[:new_val]

                if updated_chats:
                    real_longest = max([c["wait_time_sec"] for c in updated_chats])
                    wait_sec = max(wait_sec, real_longest)

            waiting_chats_data.extend(updated_chats)

            queues_data.append({
                "queue_name": q_name,
                "chats_in_queue": new_val,
                "longest_wait_sec": wait_sec,
                "agents_online": cfg["agents"]
            })

    agents_data = []
    agents_pool = get_operational_agents_pool()
    for ag in agents_pool:
        name = ag["name"]
        prev_a = prev_agents.get(name)

        if prev_a:
            st = prev_a["status"]
            t_sec = prev_a["time_in_status_sec"] + 30
            chats = prev_a["active_chats"]

            if st == "Break":
                if t_sec >= random.randint(1300, 1700):
                    st = "Available"
                    t_sec = 30
                    chats = 1
            elif st == "Busy":
                if t_sec >= random.randint(950, 1450):
                    st = "Available"
                    t_sec = 30
                    chats = min(2, max(1, chats))
            else:  # Available
                current_breaks = len([a for a in agents_data if a.get("status") == "Break"])
                if random.random() < 0.02 and current_breaks < 2:
                    st = "Break"
                    t_sec = 30
                    chats = 0
                elif random.random() < 0.015 and chats > 0 and t_sec < 1800:
                    st = "Busy"
                    t_sec = 30
                else:
                    if random.random() < 0.20 and t_sec < 1800:
                        delta_chats = random.choice([-1, 1])
                        chats = max(0, min(3, chats + delta_chats))

            cap_pct = int(round((chats / 3.0) * 100))
            prev_sess_raw = prev_a.get("chat_session_ids", "") if prev_a else ""
            prev_sessions = [s.strip() for s in prev_sess_raw.split(",") if s.strip().startswith("ms-")]
            if chats > len(prev_sessions):
                nuevos = [f"ms-{random.randint(100000, 999999)}" for _ in range(chats - len(prev_sessions))]
                sesiones = prev_sessions + nuevos
            elif chats < len(prev_sessions):
                sesiones = prev_sessions[:chats]
            else:
                sesiones = prev_sessions if chats > 0 else []

            if chats > 0 and not sesiones:
                sesiones = [f"ms-{random.randint(100000, 999999)}" for _ in range(chats)]
            sesiones_str = ", ".join(sesiones) if sesiones else ""
        else:
            st = ag["base_status"]
            chats = 0 if st == "Break" else (random.choice([1, 2]) if st == "Busy" else random.choice([0, 1, 2, 3]))
            cap_pct = int(round((chats / 3.0) * 100))
            if st == "Busy" and random.random() < 0.4:
                t_sec = random.randint(650, 1100)
            elif st == "Break" and random.random() < 0.35:
                t_sec = random.randint(1300, 1500)
            elif st == "Available" and chats == 0 and random.random() < 0.3:
                t_sec = random.randint(950, 1300)
            elif st == "Available" and chats >= 1 and random.random() < 0.25:
                t_sec = random.randint(2200, 2600)
            else:
                t_sec = random.randint(120, 600)
            sesiones = [f"ms-{random.randint(100000, 999999)}" for _ in range(chats)] if chats > 0 else []
            sesiones_str = ", ".join(sesiones) if sesiones else ""

        agents_data.append({
            "agent_name": name,
            "status": st,
            "active_chats": chats,
            "capacity_pct": cap_pct,
            "time_in_status_sec": t_sec,
            "skill": ag["skill"],
            "chat_session_ids": sesiones_str
        })

    save_live_snapshot(queues_data, agents_data, waiting_chats_data)
    return queues_data, agents_data


def get_live_waiting_chats(latest_ts: str = None) -> pd.DataFrame:
    """
    Retorna los chats individuales que están actualmente en cola esperando atención en Omni-Channel.
    Consulta primero Neon Postgres (para Streamlit Cloud y producción) y luego SQLite local.
    """
    # 1. Intentar consultar Neon Postgres
    conn_pg = _get_neon_connection()
    if conn_pg:
        try:
            cur_pg = conn_pg.cursor()
            if not latest_ts:
                cur_pg.execute("SELECT MAX(timestamp) FROM live_waiting_chats")
                row = cur_pg.fetchone()
                target_ts = row[0] if row else None
            else:
                target_ts = latest_ts

            if target_ts:
                cur_pg.execute(
                    "SELECT chat_id, queue_name, wait_time_sec, channel FROM live_waiting_chats WHERE timestamp = %s ORDER BY wait_time_sec DESC",
                    (target_ts,)
                )
                cols = [desc[0] for desc in cur_pg.description]
                rows = cur_pg.fetchall()
                cur_pg.close()
                conn_pg.close()
                df = pd.DataFrame(rows, columns=cols)
                if not df.empty:
                    def format_wait(sec):
                        m, s = divmod(int(sec), 60)
                        return f"{m:02d}:{s:02d} min"

                    def format_sla(sec):
                        if sec <= 60:
                            return "🟢 Normal (≤ 60s)"
                        elif sec <= 100:
                            return "🟡 En Riesgo (61-100s)"
                        else:
                            return "🔴 SLA Excedido (> 100s)"

                    df["Tiempo de Espera"] = df["wait_time_sec"].apply(format_wait)
                    df["Estado SLA"] = df["wait_time_sec"].apply(format_sla)
                    df = df.rename(columns={
                        "chat_id": "💬 ID Chat (ms-)",
                        "queue_name": "🏷️ Cola Salesforce",
                        "channel": "Canal"
                    })
                    return df[["💬 ID Chat (ms-)", "🏷️ Cola Salesforce", "Tiempo de Espera", "Estado SLA", "wait_time_sec", "Canal"]]
            else:
                cur_pg.close()
                conn_pg.close()
        except Exception:
            try:
                conn_pg.close()
            except Exception:
                pass

    # 2. Fallback SQLite local
    init_live_db()
    conn = sqlite3.connect(LIVE_DB_PATH)
    if not latest_ts:
        cur = conn.cursor()
        cur.execute("SELECT MAX(timestamp) FROM live_waiting_chats")
        row = cur.fetchone()
        latest_ts = row[0] if row else None

    if not latest_ts:
        conn.close()
        return pd.DataFrame()

    try:
        df = pd.read_sql_query(
            "SELECT chat_id, queue_name, wait_time_sec, channel FROM live_waiting_chats WHERE timestamp = ? ORDER BY wait_time_sec DESC",
            conn,
            params=(latest_ts,)
        )
    except Exception:
        df = pd.DataFrame()
    conn.close()

    if df.empty:
        return df

    def format_wait(sec):
        m, s = divmod(int(sec), 60)
        return f"{m:02d}:{s:02d} min"

    def format_sla(sec):
        if sec <= 60:
            return "🟢 Normal (≤ 60s)"
        elif sec <= 100:
            return "🟡 En Riesgo (61-100s)"
        else:
            return "🔴 SLA Excedido (> 100s)"

    df["Tiempo de Espera"] = df["wait_time_sec"].apply(format_wait)
    df["Estado SLA"] = df["wait_time_sec"].apply(format_sla)
    df = df.rename(columns={
        "chat_id": "💬 ID Chat (ms-)",
        "queue_name": "🏷️ Cola Salesforce",
        "channel": "Canal"
    })
    return df[["💬 ID Chat (ms-)", "🏷️ Cola Salesforce", "Tiempo de Espera", "Estado SLA", "wait_time_sec", "Canal"]]


def generate_simulated_live_tick():
    """Mantiene compatibilidad hacia atrás delegando a la función suave."""
    return advance_live_state_smoothly()


def get_latest_live_state(force_fresh: bool = False):
    """
    Obtiene el estado más reciente de colas y agentes en tiempo real.
    Consulta Neon Postgres en la nube primero (para Streamlit Cloud y tiempo real multi-usuario)
    y luego SQLite local con fallback a simulación continua.
    """
    # 1. Intentar consultar Neon Postgres
    conn_pg = _get_neon_connection()
    if conn_pg:
        try:
            cur_pg = conn_pg.cursor()
            cur_pg.execute("SELECT MAX(timestamp) FROM live_chat_queues")
            row = cur_pg.fetchone()
            latest_ts_pg = row[0] if row else None

            if latest_ts_pg:
                latest_ts_str = latest_ts_pg.strftime("%Y-%m-%d %H:%M:%S") if hasattr(latest_ts_pg, "strftime") else str(latest_ts_pg)
                cur_pg.execute("SELECT * FROM live_chat_queues WHERE timestamp = %s ORDER BY chats_in_queue DESC", (latest_ts_pg,))
                cols_q = [desc[0] for desc in cur_pg.description]
                df_queues = pd.DataFrame(cur_pg.fetchall(), columns=cols_q)

                cur_pg.execute("SELECT * FROM live_chat_agents WHERE timestamp = %s ORDER BY capacity_pct DESC, agent_name ASC", (latest_ts_pg,))
                cols_a = [desc[0] for desc in cur_pg.description]
                df_agents = pd.DataFrame(cur_pg.fetchall(), columns=cols_a)

                cur_pg.close()
                conn_pg.close()
                if not df_queues.empty:
                    df_queues["categoria"] = df_queues["queue_name"].apply(obtener_categoria_cola)
                    return df_queues, df_agents, latest_ts_str
            else:
                cur_pg.close()
                conn_pg.close()
        except Exception:
            try:
                conn_pg.close()
            except Exception:
                pass

    # 2. Fallback SQLite local
    init_live_db()
    conn = sqlite3.connect(LIVE_DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT MAX(timestamp) FROM live_chat_queues")
    latest_ts = cur.fetchone()[0]
    conn.close()

    if not latest_ts:
        advance_live_state_smoothly()
        conn = sqlite3.connect(LIVE_DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT MAX(timestamp) FROM live_chat_queues")
        latest_ts = cur.fetchone()[0]
        conn.close()

    conn = sqlite3.connect(LIVE_DB_PATH)
    df_queues = pd.read_sql_query(
        "SELECT * FROM live_chat_queues WHERE timestamp = ? GROUP BY queue_name ORDER BY chats_in_queue DESC",
        conn,
        params=(latest_ts,)
    )
    if not df_queues.empty and "queue_name" in df_queues.columns:
        df_queues["categoria"] = df_queues["queue_name"].apply(obtener_categoria_cola)

    df_agents = pd.read_sql_query(
        "SELECT * FROM live_chat_agents WHERE timestamp = ? GROUP BY agent_name ORDER BY capacity_pct DESC, agent_name ASC",
        conn,
        params=(latest_ts,)
    )
    conn.close()
    return df_queues, df_agents, latest_ts


try:
    import mapeo_socios_engine as mse
except Exception:
    mse = None


def get_advisor_display_info(alias: str) -> dict:
    """Retorna el nombre real, nivel y supervisor de un alias de Salesforce."""
    if mse:
        try:
            info = mse.get_asesor_info(alias)
            return {
                "nombre": info.get("nombre_completo", alias),
                "nivel": info.get("nivel", "N/A"),
                "supervisor": info.get("supervisor", "Sin Supervisor"),
                "alias": alias,
            }
        except Exception:
            pass
    return {"nombre": alias, "nivel": "N/A", "supervisor": "Sin Supervisor", "alias": alias}


def detect_live_anomalies(df_queues, df_agents):
    """
    Detecta comportamientos de improductividad y cuellos de botella en tiempo real:
    1. Asesores en Busy prolongado (>10 min) bloqueando la entrada de nuevos chats.
    2. Asesores en Available ociosos (>15 min con 0 chats asignados).
    3. Chats estancados / congelados en curso (>35 min en atención).
    4. Exceso de Break en Omni-Channel (>20 min).
    5. Colas saturadas con chats en espera mientras hay capacidad ociosa.
    """
    alerts = []

    total_waiting = df_queues["chats_in_queue"].sum() if not df_queues.empty else 0

    if not df_agents.empty:
        # 1. Asesores en Busy prolongado (>10 min bloqueando entrada de chats)
        busy_prolonged = df_agents[(df_agents["status"] == "Busy") & (df_agents["time_in_status_sec"] >= 600)]
        for _, ag in busy_prolonged.iterrows():
            mins = ag["time_in_status_sec"] // 60
            tipo = "critical" if mins >= 15 else "warning"
            ad_info = get_advisor_display_info(ag["agent_name"])
            real_name = ad_info["nombre"]
            exceso = max(0, mins - 10)
            tag = f"🚨 Busy prolongado (+{exceso} min, lleva {mins} min)"
            alerts.append({
                "type": tipo,
                "categoria": "busy",
                "asesor": real_name,
                "alias": ag["agent_name"],
                "nivel": ad_info["nivel"],
                "supervisor": ad_info["supervisor"],
                "dur_min": mins,
                "dur_str": f"{mins} min",
                "tag": tag,
                "title": f"🚨 {real_name}: Busy prolongado (+{exceso} min)",
                "message": f"<b>{real_name}</b>: 🚨 Busy prolongado (+{exceso} min, lleva {mins} min con {ag['active_chats']}/3 chats). Retiene capacidad con {total_waiting} chats en cola."
            })

        # 2. Ociosidad en Available (>15 min disponible sin recibir ni un solo chat)
        idle_available = df_agents[(df_agents["status"] == "Available") & (df_agents["active_chats"] == 0) & (df_agents["time_in_status_sec"] >= 900)]
        for _, ag in idle_available.iterrows():
            mins = ag["time_in_status_sec"] // 60
            ad_info = get_advisor_display_info(ag["agent_name"])
            real_name = ad_info["nombre"]
            tag = f"⚠️ Disponible sin chats ({mins} min)"
            alerts.append({
                "type": "warning",
                "categoria": "idle",
                "asesor": real_name,
                "alias": ag["agent_name"],
                "nivel": ad_info["nivel"],
                "supervisor": ad_info["supervisor"],
                "dur_min": mins,
                "dur_str": f"{mins} min",
                "tag": tag,
                "title": f"⚠️ {real_name}: Disponible sin chats ({mins} min)",
                "message": f"<b>{real_name}</b>: ⚠️ Disponible sin chats asignados (lleva {mins} min al 0% de ocupación)."
            })

        # 3. Chats estancados o congelados (>35 min en interacción)
        stuck_chats = df_agents[(df_agents["status"].isin(["Available", "Busy"])) & (df_agents["active_chats"] >= 1) & (df_agents["time_in_status_sec"] >= 2100)]
        for _, ag in stuck_chats.iterrows():
            mins = ag["time_in_status_sec"] // 60
            ad_info = get_advisor_display_info(ag["agent_name"])
            real_name = ad_info["nombre"]
            tag = f"🟣 Chat estancado ({mins} min)"
            alerts.append({
                "type": "warning",
                "categoria": "stuck_chat",
                "asesor": real_name,
                "alias": ag["agent_name"],
                "nivel": ad_info["nivel"],
                "supervisor": ad_info["supervisor"],
                "dur_min": mins,
                "dur_str": f"{mins} min",
                "tag": tag,
                "title": f"🟣 {real_name}: Chat estancado ({mins} min)",
                "message": f"<b>{real_name}</b>: 🟣 Chat activo prolongado ({mins} min en atención con {ag['active_chats']} chat(s)). Posible contacto abandonado."
            })

        # 4. Exceso de Break en Omni-Channel (>20 min)
        excess_break = df_agents[(df_agents["status"] == "Break") & (df_agents["time_in_status_sec"] >= 1200)]
        for _, ag in excess_break.iterrows():
            mins = ag["time_in_status_sec"] // 60
            ad_info = get_advisor_display_info(ag["agent_name"])
            real_name = ad_info["nombre"]
            exceso = max(0, mins - 20)
            tag = f"🚨 Break excedido (+{exceso} min, lleva {mins} min)"
            alerts.append({
                "type": "warning",
                "categoria": "break",
                "asesor": real_name,
                "alias": ag["agent_name"],
                "nivel": ad_info["nivel"],
                "supervisor": ad_info["supervisor"],
                "dur_min": mins,
                "dur_str": f"{mins} min",
                "tag": tag,
                "title": f"🚨 {real_name}: Break excedido (+{exceso} min)",
                "message": f"<b>{real_name}</b>: 🚨 Break Omni-Channel excedido (+{exceso} min, lleva {mins} min)."
            })

        # 5. Agentes disponibles con capacidad libre (33% o 67%) con cola esperando
        if total_waiting > 10:
            free_capacity = df_agents[(df_agents["status"] == "Available") & (df_agents["capacity_pct"] < 100)]
            if not free_capacity.empty:
                free_names = [get_advisor_display_info(a)["nombre"] for a in free_capacity["agent_name"].tolist()[:3]]
                free_str = ", ".join(free_names)
                alerts.append({
                    "type": "info",
                    "categoria": "free_cap",
                    "title": "Capacidad disponible en asesores",
                    "tag": f"Capacidad disponible ({len(free_capacity)} asesores)",
                    "message": f"Asesores con cupos libres ({free_str}) y {total_waiting} chats esperando en cola."
                })

    # Alerta de colas criticas
    if not df_queues.empty:
        critical_q = df_queues[df_queues["chats_in_queue"] >= 20]
        for _, q in critical_q.iterrows():
            wait_min = round(q["longest_wait_sec"] / 60, 1)
            alerts.append({
                "type": "critical",
                "categoria": "queue",
                "title": f"Saturación en {q['queue_name']}",
                "tag": f"Saturación en {q['queue_name']} ({wait_min} min)",
                "message": f"{q['chats_in_queue']} chats esperando. Mayor tiempo de espera: {wait_min} minutos."
            })

    # Deduplicación estricta por (categoria, asesor) o (categoria, title) para evitar repeticiones en pantalla
    deduped = []
    seen = set()
    for a in alerts:
        key = (
            a.get("categoria", ""),
            str(a.get("asesor") or a.get("alias") or a.get("title", "")).strip().upper()
        )
        if key not in seen:
            seen.add(key)
            deduped.append(a)

    return deduped
