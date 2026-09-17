"""
Motor Analítico de Cumplimiento de Horas Laboradas y Adherencia Intradía de Pausas Programadas.
Cruza:
1. Malla de Turnos Detallada (horas programadas, turno_ini/fin, des_1, des_2, lunch, dialogo).
2. Tramos reales de presencia de Genesys Cloud (tabla segments en presencia.db).
3. Salesforce Omni-Channel / Live Agent cuando aplica.
"""

import os
import sqlite3
from datetime import datetime, timedelta
import pandas as pd
import streamlit as st

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.normpath(os.path.join(BASE_DIR, ".."))
DB_PATH = os.path.join(PROJECT_ROOT, "data", "presencia.db")


def _get_db():
    return sqlite3.connect(DB_PATH)


def _time_to_minutes(t_str: str) -> float:
    if not t_str:
        return 0.0
    try:
        parts = t_str.split(":")
        h = int(parts[0])
        m = int(parts[1])
        s = int(parts[2]) if len(parts) > 2 else 0
        return h * 60.0 + m + s / 60.0
    except Exception:
        return 0.0


def _minutes_to_hhmm(mins: float) -> str:
    if pd.isna(mins) or mins < 0:
        return "00:00"
    h = int(mins // 60)
    m = int(round(mins % 60))
    if m == 60:
        h += 1
        m = 0
    return f"{h:02d}:{m:02d}"


def obtener_fechas_disponibles_turnos() -> list[str]:
    """Retorna las fechas con turnos detallados en la base de datos."""
    try:
        with _get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT fecha FROM turnos_detallados ORDER BY fecha DESC;")
            return [r[0] for r in cur.fetchall()]
    except Exception:
        return []


def calcular_cumplimiento_horas_turno(fecha: str, coordinador: str = None, servicio: str = None) -> pd.DataFrame:
    """
    Evalúa el cumplimiento de horas de la jornada laboral:
    Horas Laboradas Programadas vs Horas Reales Conectado (productivo + pausas de ley).
    Calcula: % Cumplimiento, Horas Faltantes/Sobrantes, y clasifica en semáforo.
    """
    with _get_db() as conn:
        query_turnos = """
            SELECT bp, fecha, documento, nombre_agente, servicio, novedad,
                   horas_programadas, turno_ini, turno_fin
            FROM turnos_detallados
            WHERE fecha = ?
        """
        params_t = [fecha]
        if servicio and servicio != "Todos los Servicios":
            query_turnos += " AND servicio = ?"
            params_t.append(servicio)

        df_t = pd.read_sql_query(query_turnos, conn, params=params_t)
        if df_t.empty:
            return pd.DataFrame()

        # Obtener segmentos reales para esa fecha
        query_seg = """
            SELECT agente, presence_label, system_presence, inicio, fin, duracion_min, servicio, coordinador
            FROM segments
            WHERE fecha = ?
        """
        df_seg = pd.read_sql_query(query_seg, conn, params=[fecha])

    # Enriquecer segmentos extrayendo BP (código antes del guión)
    if not df_seg.empty:
        df_seg["bp"] = df_seg["agente"].astype(str).str.split(" - ").str[0].str.strip()
    else:
        df_seg["bp"] = []

    # Agrupar por BP
    res_list = []
    
    # Categorías de presencia
    est_offline = {"Offline", "Desconectado"}
    est_pausas = {"Break", "Lunch", "Baño", "Pre Pausa", "Descanso", "Almuerzo", "Diálogo Diario / 4DX", "PCA- Diálogo", "Refuerzo Semanal", "Cursos Adicionales"}
    
    seg_by_bp = dict(tuple(df_seg.groupby("bp"))) if not df_seg.empty else {}

    for _, row in df_t.iterrows():
        bp = str(row["bp"]).strip()
        h_prog = float(row["horas_programadas"]) if pd.notna(row["horas_programadas"]) and row["horas_programadas"] > 0 else 8.0
        nom = row["nombre_agente"] or f"Asesor BP {bp}"
        srv = row["servicio"] or "LATAM"
        nov = row["novedad"] or "TUR"
        t_ini = row["turno_ini"] or "--"
        t_fin = row["turno_fin"] or "--"

        sub_seg = seg_by_bp.get(bp)
        if sub_seg is not None and not sub_seg.empty:
            coord_val = sub_seg["coordinador"].iloc[0] if "coordinador" in sub_seg.columns and pd.notna(sub_seg["coordinador"].iloc[0]) else ""
            if coordinador and coordinador != "Todos los Coordinadores" and coord_val != coordinador:
                continue

            # Minutos conectado (excluyendo offline)
            conectados = sub_seg[~sub_seg["presence_label"].isin(est_offline)]
            min_conectado = conectados["duracion_min"].sum()
            
            # Minutos en pausas
            pausas = sub_seg[sub_seg["presence_label"].isin(est_pausas)]
            min_pausas = pausas["duracion_min"].sum()
            
            # Minutos productivos
            min_productivo = max(0.0, min_conectado - min_pausas)

            # Horas
            h_conectado = round(min_conectado / 60.0, 2)
            h_prod = round(min_productivo / 60.0, 2)
            h_pau = round(min_pausas / 60.0, 2)

            # Primera conexión y última desconexión
            pri_con = conectados["inicio"].min()
            ult_des = conectados["fin"].max()
            h_pri = pri_con.split(" ")[1][:5] if pd.notna(pri_con) and " " in str(pri_con) else "--"
            h_ult = ult_des.split(" ")[1][:5] if pd.notna(ult_des) and " " in str(ult_des) else "--"
        else:
            if coordinador and coordinador != "Todos los Coordinadores":
                continue
            h_conectado = 0.0
            h_prod = 0.0
            h_pau = 0.0
            h_pri = "--"
            h_ult = "--"

        # Cumplimiento
        pct_cumpl = round((h_conectado / h_prog * 100.0), 1) if h_prog > 0 else 0.0
        brecha_h = round(h_conectado - h_prog, 2)

        if h_conectado == 0.0:
            estado = "❌ Ausente / Sin Conexión"
        elif pct_cumpl >= 98.0 or brecha_h >= -0.15:
            estado = "🟢 Cumple Jornada Completa"
        elif pct_cumpl >= 90.0:
            estado = "🟡 Déficit Leve (< 1h)"
        else:
            estado = "🔴 Déficit Severo (> 1h faltante)"

        res_list.append({
            "BP": bp,
            "Asesor": nom,
            "Servicio": srv,
            "Turno Programado": f"{t_ini[:5]} - {t_fin[:5]}",
            "Horas Prog": round(h_prog, 2),
            "Conexión Real": f"{h_pri} - {h_ult}",
            "Horas Conectado": h_conectado,
            "Horas Productivas": h_prod,
            "Horas Pausas": h_pau,
            "% Cumplimiento": pct_cumpl,
            "Brecha Horas": brecha_h,
            "Estado": estado
        })

    df_res = pd.DataFrame(res_list)
    if not df_res.empty:
        df_res = df_res.sort_values(by=["% Cumplimiento", "Brecha Horas"], ascending=[True, True])
    return df_res


def calcular_adherencia_pausas_intradia(fecha: str, coordinador: str = None, servicio: str = None, tolerancia_min: int = 10) -> pd.DataFrame:
    """
    Audita franja a franja la puntualidad y duración de cada pausa programada:
    Descanso 1, Descanso 2, Almuerzo, Diálogo 4DX y Capacitaciones.
    Cruza el horario programado contra los eventos de presence_label en segments.
    """
    with _get_db() as conn:
        query_turnos = """
            SELECT bp, fecha, documento, nombre_agente, servicio,
                   turno_ini, turno_fin, dialogo_ini, dialogo_fin,
                   des_1_ini, des_1_fin, des_2_ini, des_2_fin, des_3_ini, des_3_fin,
                   lunch_ini, lunch_fin, training_1_ini, training_1_fin
            FROM turnos_detallados
            WHERE fecha = ?
        """
        params_t = [fecha]
        if servicio and servicio != "Todos los Servicios":
            query_turnos += " AND servicio = ?"
            params_t.append(servicio)

        df_t = pd.read_sql_query(query_turnos, conn, params=params_t)
        if df_t.empty:
            return pd.DataFrame()

        query_seg = """
            SELECT agente, presence_label, inicio, fin, duracion_min, servicio, coordinador
            FROM segments
            WHERE fecha = ?
        """
        df_seg = pd.read_sql_query(query_seg, conn, params=[fecha])

    if not df_seg.empty:
        df_seg["bp"] = df_seg["agente"].astype(str).str.split(" - ").str[0].str.strip()
        df_seg["t_ini_min"] = df_seg["inicio"].astype(str).str.split(" ").str[-1].apply(_time_to_minutes)
        df_seg["t_fin_min"] = df_seg["fin"].astype(str).str.split(" ").str[-1].apply(_time_to_minutes)
    else:
        df_seg["bp"] = []
        df_seg["t_ini_min"] = []
        df_seg["t_fin_min"] = []

    seg_by_bp = dict(tuple(df_seg.groupby("bp"))) if not df_seg.empty else {}

    filas_pausas = []

    tipos_pausas = [
        ("Descanso 1 (Break)", "des_1_ini", "des_1_fin", {"Break", "Baño", "Pre Pausa", "Descanso"}),
        ("Descanso 2 (Break)", "des_2_ini", "des_2_fin", {"Break", "Baño", "Pre Pausa", "Descanso"}),
        ("Almuerzo (Lunch)", "lunch_ini", "lunch_fin", {"Lunch", "Almuerzo"}),
        ("Diálogo Diario (4DX)", "dialogo_ini", "dialogo_fin", {"Diálogo Diario / 4DX", "PCA- Diálogo"}),
        ("Capacitación (Training)", "training_1_ini", "training_1_fin", {"Cursos Adicionales", "Refuerzo Semanal", "Training"})
    ]

    for _, row in df_t.iterrows():
        bp = str(row["bp"]).strip()
        nom = row["nombre_agente"] or f"Asesor BP {bp}"
        srv = row["servicio"] or "LATAM"

        sub_seg = seg_by_bp.get(bp)
        if sub_seg is not None and not sub_seg.empty:
            coord_val = sub_seg["coordinador"].iloc[0] if "coordinador" in sub_seg.columns and pd.notna(sub_seg["coordinador"].iloc[0]) else ""
            if coordinador and coordinador != "Todos los Coordinadores" and coord_val != coordinador:
                continue
        else:
            if coordinador and coordinador != "Todos los Coordinadores":
                continue

        for label_pausa, col_ini, col_fin, labels_presencia in tipos_pausas:
            h_ini_str = row.get(col_ini)
            h_fin_str = row.get(col_fin)
            if not h_ini_str or not h_fin_str or str(h_ini_str).strip() in ("00:00:00", "None", ""):
                continue

            prog_ini_min = _time_to_minutes(str(h_ini_str))
            prog_fin_min = _time_to_minutes(str(h_fin_str))
            prog_dur_min = max(0.0, prog_fin_min - prog_ini_min)
            if prog_dur_min == 0.0:
                continue

            # Buscar tramos de pausa que se acerquen a esta ventana
            tramo_real = None
            if sub_seg is not None and not sub_seg.empty:
                candidatos = sub_seg[sub_seg["presence_label"].isin(labels_presencia)].copy()
                if not candidatos.empty:
                    candidatos["distancia"] = (candidatos["t_ini_min"] - prog_ini_min).abs()
                    cercanos = candidatos[candidatos["distancia"] <= 90].sort_values("distancia")
                    if not cercanos.empty:
                        tramo_real = cercanos.iloc[0]

            if tramo_real is not None:
                real_ini_min = tramo_real["t_ini_min"]
                real_fin_min = tramo_real["t_fin_min"]
                real_dur_min = float(tramo_real["duracion_min"])
                
                desvio_ini_min = int(round(real_ini_min - prog_ini_min))
                exceso_dur_min = int(round(real_dur_min - prog_dur_min))

                hora_real_str = f"{_minutes_to_hhmm(real_ini_min)} - {_minutes_to_hhmm(real_fin_min)}"

                if abs(desvio_ini_min) <= tolerancia_min and exceso_dur_min <= 3:
                    estado_p = "🟢 Puntual y en tiempo"
                elif abs(desvio_ini_min) <= tolerancia_min and exceso_dur_min > 3:
                    estado_p = f"🔴 Exceso de Tiempo (+{exceso_dur_min} min)"
                elif abs(desvio_ini_min) > tolerancia_min and exceso_dur_min <= 3:
                    estado_p = f"🟡 Desfasada en horario ({desvio_ini_min:+d} min)"
                else:
                    estado_p = f"🔴 Desfasada ({desvio_ini_min:+d}m) y con Exceso (+{exceso_dur_min}m)"
            else:
                hora_real_str = "--"
                real_dur_min = 0.0
                desvio_ini_min = None
                exceso_dur_min = None
                estado_p = "❌ Pausa No Tomada en Ventana"

            filas_pausas.append({
                "BP": bp,
                "Asesor": nom,
                "Servicio": srv,
                "Tipo Pausa": label_pausa,
                "Horario Programado": f"{str(h_ini_str)[:5]} - {str(h_fin_str)[:5]}",
                "Duración Prog": f"{int(round(prog_dur_min))} min",
                "Horario Real": hora_real_str,
                "Duración Real": f"{int(round(real_dur_min))} min" if real_dur_min > 0 else "--",
                "Desvío Salida": f"{desvio_ini_min:+d} min" if desvio_ini_min is not None else "--",
                "Exceso": f"{exceso_dur_min:+d} min" if exceso_dur_min is not None else "--",
                "Estado": estado_p
            })

    df_p = pd.DataFrame(filas_pausas)
    return df_p
