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


def get_latest_live_state(force_fresh: bool = False):
    """Obtiene el ultimo estado registrado de colas y agentes."""
    init_live_db()
    conn = sqlite3.connect(LIVE_DB_PATH)

    # Verificar si hay datos
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM live_chat_queues")
    count = cur.fetchone()[0]

    if count == 0 or force_fresh:
        conn.close()
        # Generar snapshot fresco
        generate_simulated_live_tick()
        conn = sqlite3.connect(LIVE_DB_PATH)
        cur = conn.cursor()

    # Obtener el timestamp mas reciente
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
            alerts.append({
                "type": tipo,
                "title": f"Capacidad bloqueada: Busy prolongado ({ag['agent_name']})",
                "message": f"{ag['agent_name']} lleva {mins} min en Busy ({ag['active_chats']}/3 chats). Impide el ingreso de nuevos chats mientras hay {total_waiting} en espera."
            })

        # 2. Ociosidad en Available (>15 min disponible sin recibir ni un solo chat)
        idle_available = df_agents[(df_agents["status"] == "Available") & (df_agents["active_chats"] == 0) & (df_agents["time_in_status_sec"] >= 900)]
        for _, ag in idle_available.iterrows():
            mins = ag["time_in_status_sec"] // 60
            alerts.append({
                "type": "warning",
                "title": f"Ociosidad en Available ({ag['agent_name']})",
                "message": f"{ag['agent_name']} lleva {mins} min en estado Disponible sin ningún chat asignado (0% ocupación)."
            })

        # 3. Chats estancados o congelados (>35 min en interacción)
        stuck_chats = df_agents[(df_agents["status"].isin(["Available", "Busy"])) & (df_agents["active_chats"] >= 1) & (df_agents["time_in_status_sec"] >= 2100)]
        for _, ag in stuck_chats.iterrows():
            mins = ag["time_in_status_sec"] // 60
            alerts.append({
                "type": "warning",
                "title": f"Chat prolongado / posible congelamiento ({ag['agent_name']})",
                "message": f"{ag['agent_name']} acumula {mins} min en el mismo tramo de atención ({ag['active_chats']} chats activos). Revisar si el contacto fue abandonado."
            })

        # 4. Exceso de Break en Omni-Channel (>20 min)
        excess_break = df_agents[(df_agents["status"] == "Break") & (df_agents["time_in_status_sec"] >= 1200)]
        for _, ag in excess_break.iterrows():
            mins = ag["time_in_status_sec"] // 60
            alerts.append({
                "type": "warning",
                "title": f"Exceso de Break Omni-Channel ({ag['agent_name']})",
                "message": f"{ag['agent_name']} lleva {mins} min en pausa de Break (supera los 20 min autorizados)."
            })

        # 5. Agentes disponibles con capacidad libre (33% o 67%) con cola esperando
        if total_waiting > 10:
            free_capacity = df_agents[(df_agents["status"] == "Available") & (df_agents["capacity_pct"] < 100)]
            if not free_capacity.empty:
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
