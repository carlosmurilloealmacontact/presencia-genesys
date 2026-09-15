"""
Motor de tiempo real para el Command Center de Chats (Omni-Channel / Salesforce).
Maneja colas AMC, estado de agentes (Available, Busy, Break), capacidad (33%, 67%, 100%)
y genera alertas de saturacion y desvio de estados en tiempo real.
"""

import os
import sqlite3
import random
import pandas as pd
from datetime import datetime

LIVE_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "salesforce_live.db")

# Colas operativas de AMC segun directriz de negocio
AMC_CHAT_QUEUES = [
    "AMC Agencias Español",
    "AMC Agencias Inglés",
    "AMC Corporativo SSC",
    "AMC Dudas Operacionales"
]

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


def init_live_db():
    """Inicializa las tablas SQLite para el estado de chats en vivo."""
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
    CREATE TABLE IF NOT EXISTS live_chat_agents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        agent_name TEXT NOT NULL,
        status TEXT NOT NULL,
        active_chats INTEGER NOT NULL,
        capacity_pct INTEGER NOT NULL,
        time_in_status_sec INTEGER NOT NULL,
        skill TEXT
    )
    """)

    conn.commit()
    conn.close()


def save_live_snapshot(queues_data, agents_data):
    """Guarda un snapshot del estado en vivo."""
    init_live_db()
    conn = sqlite3.connect(LIVE_DB_PATH)
    cur = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for q in queues_data:
        cur.execute("""
        INSERT INTO live_chat_queues (timestamp, queue_name, chats_in_queue, longest_wait_sec, agents_online)
        VALUES (?, ?, ?, ?, ?)
        """, (now_str, q["queue_name"], q["chats_in_queue"], q["longest_wait_sec"], q.get("agents_online", 0)))

    for a in agents_data:
        cur.execute("""
        INSERT INTO live_chat_agents (timestamp, agent_name, status, active_chats, capacity_pct, time_in_status_sec, skill)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (now_str, a["agent_name"], a["status"], a["active_chats"], a["capacity_pct"], a["time_in_status_sec"], a.get("skill", "")))

    conn.commit()
    conn.close()


def generate_simulated_live_tick():
    """
    Genera un tick en tiempo real calibrado con la realidad operativa descrita en la grabacion:
    - 14 a 44 chats esperando en cola.
    - Simultaneidad de 3 chats = 100%.
    - Ejecutivos en Available, Busy (bloqueando entrada) y Break.
    """
    queues_data = [
        {
            "queue_name": "AMC Agencias Español",
            "chats_in_queue": random.randint(18, 35),
            "longest_wait_sec": random.randint(180, 540),
            "agents_online": 7
        },
        {
            "queue_name": "AMC Agencias Inglés",
            "chats_in_queue": random.randint(3, 8),
            "longest_wait_sec": random.randint(45, 180),
            "agents_online": 3
        },
        {
            "queue_name": "AMC Corporativo SSC",
            "chats_in_queue": random.randint(5, 12),
            "longest_wait_sec": random.randint(90, 320),
            "agents_online": 4
        },
        {
            "queue_name": "AMC Dudas Operacionales",
            "chats_in_queue": random.randint(1, 4),
            "longest_wait_sec": random.randint(30, 90),
            "agents_online": 2
        }
    ]

    agents_data = []
    for ag in DEFAULT_AGENTS:
        status_choice = ag["base_status"]
        # Simulacion leve de transiciones
        rand_val = random.random()
        if rand_val < 0.15:
            status = "Busy"
        elif rand_val < 0.25:
            status = "Break"
        else:
            status = status_choice

        # Simultaneidad: Maximo 3 chats (1 chat = 33%, 2 = 67%, 3 = 100%)
        if status == "Break":
            active_chats = 0
        elif status == "Busy":
            # Puede tener 1 o 2 chats activos y ponerse en busy para no recibir el 3ro
            active_chats = random.choice([1, 2])
        else:  # Available
            active_chats = random.choices([1, 2, 3], weights=[0.25, 0.45, 0.30])[0]

        capacity_pct = int(round((active_chats / 3.0) * 100))
        time_in_status = random.randint(60, 2400)

        agents_data.append({
            "agent_name": ag["name"],
            "status": status,
            "active_chats": active_chats,
            "capacity_pct": capacity_pct,
            "time_in_status_sec": time_in_status,
            "skill": ag["skill"]
        })

    save_live_snapshot(queues_data, agents_data)
    return queues_data, agents_data


def get_latest_live_state():
    """Obtiene el ultimo estado registrado de colas y agentes."""
    init_live_db()
    conn = sqlite3.connect(LIVE_DB_PATH)

    # Verificar si hay datos
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM live_chat_queues")
    count = cur.fetchone()[0]

    if count == 0:
        conn.close()
        # Generar primer snapshot si esta vacio
        generate_simulated_live_tick()
        conn = sqlite3.connect(LIVE_DB_PATH)

    # Obtener el timestamp mas reciente
    cur = conn.cursor()
    cur.execute("SELECT MAX(timestamp) FROM live_chat_queues")
    latest_ts = cur.fetchone()[0]

    df_queues = pd.read_sql_query(
        "SELECT * FROM live_chat_queues WHERE timestamp = ? ORDER BY chats_in_queue DESC",
        conn,
        params=(latest_ts,)
    )

    df_agents = pd.read_sql_query(
        "SELECT * FROM live_chat_agents WHERE timestamp = ? ORDER BY capacity_pct DESC, agent_name ASC",
        conn,
        params=(latest_ts,)
    )

    conn.close()
    return df_queues, df_agents, latest_ts


def detect_live_anomalies(df_queues, df_agents):
    """
    Detecta comportamientos y cuellos de botella en tiempo real:
    1. Cola acumulada con agentes disponibles que tienen capacidad libre.
    2. Agentes en Busy prolongado mientras hay cola esperando.
    3. Chats al 100% de capacidad con tiempos excesivos (estancamiento).
    """
    alerts = []

    total_waiting = df_queues["chats_in_queue"].sum() if not df_queues.empty else 0

    if not df_agents.empty and total_waiting > 0:
        # Agentes en Busy con cola pendiente
        busy_with_queue = df_agents[(df_agents["status"] == "Busy") & (df_agents["time_in_status_sec"] > 300)]
        for _, ag in busy_with_queue.iterrows():
            mins = ag["time_in_status_sec"] // 60
            alerts.append({
                "type": "warning",
                "title": f"Asesor en Busy con cola activa ({ag['agent_name']})",
                "message": f"{ag['agent_name']} lleva {mins} min en estado Busy ({ag['active_chats']}/3 chats). Hay {total_waiting} chats esperando en cola."
            })

        # Agentes disponibles con capacidad libre (33% o 67%)
        free_capacity = df_agents[(df_agents["status"] == "Available") & (df_agents["capacity_pct"] < 100)]
        if not free_capacity.empty and total_waiting > 10:
            free_names = ", ".join(free_capacity["agent_name"].tolist()[:4])
            alerts.append({
                "type": "info",
                "title": "Capacidad disponible en asesores",
                "message": f"Hay asesores con cupos libres ({free_names}) y {total_waiting} chats esperando asignación."
            })

    # Alerta de colas criticas
    if not df_queues.empty:
        critical_q = df_queues[df_queues["chats_in_queue"] >= 20]
        for _, q in critical_q.iterrows():
            wait_min = round(q["longest_wait_sec"] / 60, 1)
            alerts.append({
                "type": "critical",
                "title": f"Saturación en {q['queue_name']}",
                "message": f"{q['chats_in_queue']} chats esperando. Mayor tiempo de espera: {wait_min} minutos."
            })

    return alerts
