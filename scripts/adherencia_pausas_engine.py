"""
Motor Analítico Unificado de Cumplimiento de Horas Laboradas y Adherencia Intradía de Pausas Programadas.
Unifica en una SOLA PANTALLA:
1. Cumplimiento de horas de turno (jornada completa contratada vs horas de conexión real).
2. Adherencia y puntualidad franja a franja de pausas programadas (Descansos 1 y 2, Lunch, Diálogo 4DX, Capacitación).
3. Jerarquía completa: Coordinador y Supervisor (Jefe Inmediato).
4. Ámbitos operativos aislados:
   - ✈️ LATAM Pasajeros (excluyendo Cargo Booking y todo el personal de Agencias B2B).
   - 🏢 Agencias B2B (Marelyn Cardona, Andrés Rodríguez y líneas B2B).
"""

import os
import sqlite3
from datetime import datetime, timedelta
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

try:
    from exclusion_list import es_persona_excluida, es_servicio_latam, es_campana_ajena
except ImportError:
    try:
        from scripts.exclusion_list import es_persona_excluida, es_servicio_latam, es_campana_ajena
    except ImportError:
        def es_persona_excluida(val):
            return False
        def es_servicio_latam(val):
            return True
        def es_campana_ajena(val):
            return False

try:
    from salesforce_omni_engine import obtener_presencia_omni_por_fecha
except ImportError:
    try:
        from scripts.salesforce_omni_engine import obtener_presencia_omni_por_fecha
    except ImportError:
        def obtener_presencia_omni_por_fecha(fecha: str) -> dict:
            return {}

try:
    from b2b_scope_engine import (
        es_equipo_marely_cardona,
        cargar_universo_bps_marely,
        obtener_supervisores_disponibles_b2b,
        obtener_coordinadores_disponibles_b2b,
        obtener_servicios_disponibles_b2b,
        COORDINADOR_MARELY_OFICIAL,
    )
except ImportError:
    try:
        from scripts.b2b_scope_engine import (
            es_equipo_marely_cardona,
            cargar_universo_bps_marely,
            obtener_supervisores_disponibles_b2b,
            obtener_coordinadores_disponibles_b2b,
            obtener_servicios_disponibles_b2b,
            COORDINADOR_MARELY_OFICIAL,
        )
    except ImportError:
        def es_equipo_marely_cardona(*args, **kwargs):
            return False
        def cargar_universo_bps_marely():
            return set(), set()
        def obtener_supervisores_disponibles_b2b():
            return []
        def obtener_coordinadores_disponibles_b2b():
            return []
        def obtener_servicios_disponibles_b2b():
            return []
        COORDINADOR_MARELY_OFICIAL = "CARDONA RAMIREZ MARELYN"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.normpath(os.path.join(BASE_DIR, ".."))
DB_PATH = os.path.join(PROJECT_ROOT, "data", "presencia.db")


def _get_db():
    return sqlite3.connect(DB_PATH)


def _time_to_minutes(t_str: str) -> float:
    if not t_str:
        return 0.0
    try:
        parts = str(t_str).strip().split(":")
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


@st.cache_data(ttl=3600)
def obtener_fechas_disponibles_turnos() -> list[str]:
    """Retorna las fechas con turnos detallados en la base de datos."""
    try:
        with _get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT fecha FROM turnos_detallados ORDER BY fecha DESC;")
            return [r[0] for r in cur.fetchall()]
    except Exception:
        return []


@st.cache_data(ttl=3600)
def obtener_mapa_bp_jerarquia() -> tuple[dict[str, str], dict[str, str]]:
    """Mapea cada BP a su coordinador y supervisor (jefe_inmediato) a partir de segments."""
    try:
        with _get_db() as conn:
            df = pd.read_sql_query(
                "SELECT distinct agente, coordinador, jefe_inmediato FROM segments WHERE (coordinador IS NOT NULL OR jefe_inmediato IS NOT NULL)",
                conn
            )
            if df.empty:
                return {}, {}
            df["bp"] = df["agente"].astype(str).str.split(" - ").str[0].str.strip()
            df = df.drop_duplicates("bp", keep="last")
            bp_to_coord = dict(zip(df["bp"], df["coordinador"].fillna("")))
            bp_to_superv = dict(zip(df["bp"], df["jefe_inmediato"].fillna("")))
            return bp_to_coord, bp_to_superv
    except Exception:
        return {}, {}


@st.cache_data(ttl=3600)
def obtener_bps_b2b_y_cargo() -> tuple[set[str], set[str]]:
    """
    Identifica de forma exhaustiva todos los BPs asignados a:
    1. Agencias B2B (Equipo exclusivo de Marely Cardona).
    2. Cargo Booking (servicios y asesores de carga excluidos para Pasajeros).
    """
    try:
        bps_b2b, _ = cargar_universo_bps_marely()
        bps_cargo = set()
        with _get_db() as conn:
            cur = conn.cursor()
            # Cargo Booking en turnos
            q_cargo_t = "SELECT distinct bp FROM turnos_detallados WHERE UPPER(servicio) LIKE '%CARGO%'"
            cur.execute(q_cargo_t)
            bps_cargo.update(str(r[0]).strip() for r in cur.fetchall())

            # Cargo Booking en segmentos
            q_cargo_s = "SELECT distinct agente FROM segments WHERE UPPER(servicio) LIKE '%CARGO%'"
            cur.execute(q_cargo_s)
            for r in cur.fetchall():
                bp = str(r[0]).split(" - ")[0].strip()
                if bp:
                    bps_cargo.add(bp)

        return bps_b2b, bps_cargo
    except Exception:
        return set(), set()


@st.cache_data(ttl=3600)
def obtener_coordinadores_disponibles(filtro_tipo: str = "TODOS") -> list[str]:
    """Retorna la lista ordenada de coordinadores por ámbito ('TODOS', 'PASAJEROS', 'B2B')."""
    if filtro_tipo == "B2B":
        return obtener_coordinadores_disponibles_b2b()

    try:
        with _get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT coordinador FROM segments WHERE coordinador IS NOT NULL AND trim(coordinador) != '' ORDER BY coordinador;")
            todos = [r[0] for r in cur.fetchall()]
    except Exception:
        todos = []

    if filtro_tipo == "PASAJEROS":
        return [c for c in todos if not es_equipo_marely_cardona(coordinador=c) and not es_persona_excluida(c)]
    return [c for c in todos if not es_persona_excluida(c)]


@st.cache_data(ttl=3600)
def obtener_supervisores_disponibles(coordinador: str = None, ambito: str = "TODOS") -> list[str]:
    """Retorna la lista ordenada de supervisores (jefe_inmediato), filtrada opcionalmente por coordinador y ámbito."""
    if ambito == "B2B":
        return [s for s in obtener_supervisores_disponibles_b2b() if not es_persona_excluida(s)]

    try:
        with _get_db() as conn:
            query = """
                SELECT distinct jefe_inmediato, coordinador
                FROM segments
                WHERE jefe_inmediato IS NOT NULL AND trim(jefe_inmediato) != ''
            """
            df = pd.read_sql_query(query, conn)
    except Exception:
        return []

    if df.empty:
        return []

    if ambito == "PASAJEROS":
        df = df[~df["coordinador"].astype(str).apply(lambda c: es_equipo_marely_cardona(coordinador=c))]
        df = df[~df["jefe_inmediato"].astype(str).apply(lambda j: es_equipo_marely_cardona(jefe_inmediato=j))]

    if coordinador and coordinador != "Todos los Coordinadores":
        df = df[df["coordinador"].astype(str).str.contains(coordinador, case=False, na=False)]

    supervisores = [s for s in sorted(list(df["jefe_inmediato"].dropna().unique())) if not es_persona_excluida(s)]
    return supervisores


@st.cache_data(ttl=3600)
def obtener_servicios_disponibles(filtro_tipo: str = "TODOS") -> list[str]:
    """Retorna los servicios programados por ámbito ('TODOS', 'PASAJEROS', 'B2B'), garantizando solo cuenta LATAM."""
    if filtro_tipo == "B2B":
        return obtener_servicios_disponibles_b2b()

    try:
        with _get_db() as conn:
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT servicio FROM turnos_detallados WHERE servicio IS NOT NULL AND trim(servicio) != '' ORDER BY servicio;")
            todos = [r[0] for r in cur.fetchall()]
    except Exception:
        todos = []

    # REGLA MAESTRA DE CUENTA LATAM: Excluir campañas externas
    todos = [s for s in todos if es_servicio_latam(s)]
    servicios_b2b = set(obtener_servicios_disponibles_b2b())

    if filtro_tipo == "PASAJEROS":
        return [s for s in todos if s not in servicios_b2b and "CARGO" not in s.upper()]
    return todos


@st.cache_data(ttl=1800)
def obtener_actividad_salesforce_por_fecha(fecha: str) -> dict:
    """
    Retorna mapa { bp: { 'casos_count': int, 'primer_caso': str, 'ultimo_caso': str, 'duracion_horas': float } }
    extraído de cases_amc_cleaned para la fecha indicada (YYYY-MM-DD).
    Permite validar y rescatar la presencia real de asesores B2B en Salesforce (Back Office / Casos)
    para no penalizarlos como ausentes cuando no atienden telefonía de Genesys.
    """
    res = {}
    data_dir = os.path.join(os.path.dirname(__file__), "..", "data", "salesforce")
    pkl_path = os.path.join(data_dir, "cases_amc_cleaned.pkl")
    csv_path = os.path.join(data_dir, "cases_amc_cleaned.csv")

    df_sf = None
    if os.path.exists(pkl_path):
        try:
            df_sf = pd.read_pickle(pkl_path)
        except Exception:
            pass
    if df_sf is None and os.path.exists(csv_path):
        try:
            df_sf = pd.read_csv(csv_path)
            if "Fecha_Inicio_dt" in df_sf.columns:
                df_sf["Fecha_Inicio_dt"] = pd.to_datetime(df_sf["Fecha_Inicio_dt"], errors="coerce")
            if "Fecha_Finalizacion_dt" in df_sf.columns:
                df_sf["Fecha_Finalizacion_dt"] = pd.to_datetime(df_sf["Fecha_Finalizacion_dt"], errors="coerce")
        except Exception:
            pass

    if df_sf is None or df_sf.empty:
        return res

    if "BP" not in df_sf.columns or "Fecha_Inicio_dt" not in df_sf.columns:
        return res

    try:
        df_sf["_f_ini"] = df_sf["Fecha_Inicio_dt"].dt.strftime("%Y-%m-%d")
        df_sub = df_sf[df_sf["_f_ini"] == fecha].copy()
        if df_sub.empty and "fecha_outflow" in df_sf.columns:
            df_sub = df_sf[df_sf["fecha_outflow"].astype(str) == fecha].copy()

        if df_sub.empty:
            return res

        for bp_val, grp in df_sub.groupby("BP"):
            bp_str = str(bp_val).split(".")[0].strip()
            if not bp_str or bp_str in ("nan", "None", ""):
                continue
            t_min = grp["Fecha_Inicio_dt"].min()
            t_max = grp["Fecha_Finalizacion_dt"].max() if "Fecha_Finalizacion_dt" in grp.columns else grp["Fecha_Inicio_dt"].max()
            if pd.isna(t_max):
                t_max = grp["Fecha_Inicio_dt"].max()

            dur_h = (t_max - t_min).total_seconds() / 3600.0 if (pd.notna(t_min) and pd.notna(t_max)) else 0.0
            p_str = t_min.strftime("%H:%M") if pd.notna(t_min) else "--"
            u_str = t_max.strftime("%H:%M") if pd.notna(t_max) else "--"

            res[bp_str] = {
                "casos_count": len(grp),
                "primer_caso": p_str,
                "ultimo_caso": u_str,
                "duracion_horas": round(dur_h, 2)
            }
    except Exception:
        pass
    return res


@st.cache_data(ttl=1800, show_spinner=False)
def calcular_cumplimiento_horas_turno(fecha: str, coordinador: str = None, supervisor: str = None, servicio: str = None, ambito: str = "TODOS") -> pd.DataFrame:
    """
    Evalúa el cumplimiento de horas de la jornada laboral:
    Horas Programadas vs Horas Reales Conectado (productivo + pausas de ley).
    Calcula: % Cumplimiento, Horas Faltantes/Sobrantes, y clasifica en semáforo.
    Incluye rescate y validación de presencia en Salesforce para Agencias B2B.
    """
    bp_to_coord, bp_to_superv = obtener_mapa_bp_jerarquia()
    coords_pasajeros = set(obtener_coordinadores_disponibles("PASAJEROS"))
    coords_b2b = set(obtener_coordinadores_disponibles("B2B"))
    bps_b2b, bps_cargo = obtener_bps_b2b_y_cargo()
    sf_activity = obtener_actividad_salesforce_por_fecha(fecha)
    omni_activity = obtener_presencia_omni_por_fecha(fecha)

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

        query_seg = """
            SELECT agente, presence_label, system_presence, inicio, fin, duracion_min, servicio, coordinador, jefe_inmediato
            FROM segments
            WHERE fecha = ?
        """
        df_seg = pd.read_sql_query(query_seg, conn, params=[fecha])

    if not df_seg.empty:
        df_seg["bp"] = df_seg["agente"].astype(str).str.split(" - ").str[0].str.strip()
    else:
        df_seg["bp"] = []

    est_offline = {"Offline", "Desconectado"}
    est_pausas = {"Break", "Lunch", "Baño", "Pre Pausa", "Descanso", "Almuerzo", "Diálogo Diario / 4DX", "PCA- Diálogo", "Refuerzo Semanal", "Cursos Adicionales"}
    seg_by_bp = dict(tuple(df_seg.groupby("bp"))) if not df_seg.empty else {}

    res_list = []
    for _, row in df_t.iterrows():
        bp = str(row["bp"]).strip()
        h_prog = float(row["horas_programadas"]) if pd.notna(row.get("horas_programadas")) and float(row.get("horas_programadas") or 0) > 0 else 8.0
        nom = str(row["nombre_agente"]).strip() if pd.notna(row.get("nombre_agente")) and str(row["nombre_agente"]).strip() else f"Asesor BP {bp}"
        srv = str(row["servicio"]).strip() if pd.notna(row.get("servicio")) and str(row["servicio"]).strip() else "LATAM"
        t_ini_val = row.get("turno_ini")
        t_fin_val = row.get("turno_fin")
        t_ini = str(t_ini_val).strip() if pd.notna(t_ini_val) and str(t_ini_val).strip() not in ("", "None", "nan") else "--"
        t_fin = str(t_fin_val).strip() if pd.notna(t_fin_val) and str(t_fin_val).strip() not in ("", "None", "nan") else "--"

        # REGLA MAESTRA DE CUENTA LATAM:
        # Descartar inmediatamente personal o turnos de campañas externas (Claro, Chec, Colmédica, etc.)
        if not es_servicio_latam(srv):
            continue

        sub_seg = seg_by_bp.get(bp)
        coord_real = (sub_seg["coordinador"].iloc[0] if sub_seg is not None and not sub_seg.empty and pd.notna(sub_seg["coordinador"].iloc[0]) else None) or bp_to_coord.get(bp, "")
        superv_real = (sub_seg["jefe_inmediato"].iloc[0] if sub_seg is not None and not sub_seg.empty and pd.notna(sub_seg["jefe_inmediato"].iloc[0]) else None) or bp_to_superv.get(bp, "")

        # Exclusiones de ámbito (Regla de Oro Marely Cardona)
        es_de_marely = es_equipo_marely_cardona(
            coordinador=coord_real,
            jefe_inmediato=superv_real,
            bp=bp,
            nombre=nom,
            servicio=srv
        )

        if ambito == "PASAJEROS":
            if bp in bps_cargo or "CARGO" in srv.upper():
                continue
            if es_de_marely:
                continue
            if es_persona_excluida(nom) or (coord_real and es_persona_excluida(coord_real)) or (superv_real and es_persona_excluida(superv_real)):
                continue
        elif ambito == "B2B":
            if not es_de_marely:
                continue
            if es_persona_excluida(nom):
                continue

        # Filtros de jerarquía
        if coordinador and coordinador != "Todos los Coordinadores":
            if not coord_real or coordinador.upper() not in coord_real.upper():
                continue
        if supervisor and supervisor != "Todos los Supervisores":
            if not superv_real or supervisor.upper() not in superv_real.upper():
                continue

        if sub_seg is not None and not sub_seg.empty:
            conectados = sub_seg[~sub_seg["presence_label"].isin(est_offline)]
            min_conectado = conectados["duracion_min"].sum()
            pausas = sub_seg[sub_seg["presence_label"].isin(est_pausas)]
            min_pausas = pausas["duracion_min"].sum()
            min_productivo = max(0.0, min_conectado - min_pausas)

            h_conectado = round(min_conectado / 60.0, 2)
            h_prod = round(min_productivo / 60.0, 2)
            h_pau = round(min_pausas / 60.0, 2)

            pri_con = conectados["inicio"].min()
            ult_des = conectados["fin"].max()
            h_pri = pri_con.split(" ")[1][:5] if pd.notna(pri_con) and " " in str(pri_con) else "--"
            h_ult = ult_des.split(" ")[1][:5] if pd.notna(ult_des) and " " in str(ult_des) else "--"
        else:
            h_conectado = 0.0
            h_prod = 0.0
            h_pau = 0.0
            h_pri = "--"
            h_ult = "--"

        # Rescate y validación de presencia en Salesforce (Omni-Channel Chat y Casos Back Office):
        omni_act = omni_activity.get(bp)
        act_sf = sf_activity.get(bp)

        if h_conectado == 0.0 and omni_act:
            h_pri = omni_act["h_inicio_omni"]
            h_ult = f"{omni_act['h_fin_omni']} (Omni)"
            m_con = float(omni_act.get("minutos_conexion", 0.0))
            m_pau = float(omni_act.get("minutos_pausa", 0.0))
            tot_m = m_con + m_pau
            if tot_m >= 30.0:
                h_conectado = min(h_prog, round(tot_m / 60.0, 2))
            else:
                dur_span = max(0.0, _time_to_minutes(omni_act["h_fin_omni"]) - _time_to_minutes(omni_act["h_inicio_omni"]))
                h_conectado = min(h_prog, round(max(dur_span, 60.0) / 60.0, 2))
            h_pau = round(m_pau / 60.0, 2)
            h_prod = max(0.0, round(h_conectado - h_pau, 2))
            pct_cumpl = round((h_conectado / h_prog * 100.0), 1) if h_prog > 0 else 100.0
            brecha_h = round(h_conectado - h_prog, 2)
            estado = f"🔵 Conectado en Salesforce Omni ({h_conectado}h)"
        elif h_conectado == 0.0 and act_sf and act_sf.get("casos_count", 0) > 0:
            c_count = act_sf["casos_count"]
            dur_sf = act_sf["duracion_horas"]
            h_pri_sf = act_sf["primer_caso"]
            h_ult_sf = act_sf["ultimo_caso"]

            # Si gestionó casos a lo largo de su turno (ventana >= h_prog - 1.5h o >= 4 casos distribuidos),
            # se le reconoce el cumplimiento de su jornada programada.
            if dur_sf >= (h_prog - 1.5) or c_count >= 4:
                h_conectado = h_prog
            else:
                # Estimación proporcional: mínimo 1 hora por caso o duración de ventana entre primer y último caso
                h_conectado = min(h_prog, max(round(dur_sf, 2), round(c_count * 1.0, 2)))

            h_prod = h_conectado
            h_pri = h_pri_sf
            h_ult = f"{h_ult_sf} (SF)"
            pct_cumpl = round((h_conectado / h_prog * 100.0), 1) if h_prog > 0 else 100.0
            brecha_h = round(h_conectado - h_prog, 2)
            estado = f"🔵 Conectado en Salesforce ({c_count} Casos)"
        elif h_conectado == 0.0:
            pct_cumpl = 0.0
            brecha_h = round(-h_prog, 2)
            estado = "❌ Ausente / Sin Conexión"
        else:
            pct_cumpl = round((h_conectado / h_prog * 100.0), 1) if h_prog > 0 else 0.0
            brecha_h = round(h_conectado - h_prog, 2)
            if pct_cumpl >= 98.0 or brecha_h >= -0.15:
                estado = "🟢 Cumple Jornada Completa"
            elif pct_cumpl >= 90.0:
                estado = "🟡 Déficit Leve (< 1h)"
            else:
                estado = "🔴 Déficit Severo (> 1h faltante)"


        res_list.append({
            "BP": bp,
            "Asesor": nom,
            "Coordinador": coord_real or "No Asignado",
            "Supervisor": superv_real or "No Asignado",
            "Servicio": srv,
            "Turno Programado": f"{t_ini[:5]} - {t_fin[:5]}" if (t_ini != "--" and t_fin != "--") else "--",
            "Horas Prog": round(h_prog, 2),
            "Conexión Real": f"{h_pri} - {h_ult}",
            "Horas Conectado": h_conectado,
            "Horas Productivas": h_prod,
            "Horas Pausas": h_pau,
            "% Cumplimiento": pct_cumpl,
            "Brecha Horas": brecha_h,
            "Estado Turno": estado
        })

    df_res = pd.DataFrame(res_list)
    if not df_res.empty:
        df_res = df_res.sort_values(by=["% Cumplimiento", "Brecha Horas"], ascending=[True, True])
    return df_res


@st.cache_data(ttl=1800, show_spinner=False)
def calcular_adherencia_pausas_intradia(fecha: str, coordinador: str = None, supervisor: str = None, servicio: str = None, tolerancia_min: int = 20, ambito: str = "TODOS") -> pd.DataFrame:
    """
    Audita franja a franja la puntualidad y duración de cada pausa programada:
    Descanso 1, Descanso 2, Almuerzo, Diálogo 4DX y Capacitaciones.
    Cruza el horario programado contra los eventos de presence_label en segments.
    Tolerancia por defecto de inicio: 20 min (absorbe llamadas en curso de Inbound).
    Reconoce Offline intradía para Almuerzo.
    """
    bp_to_coord, bp_to_superv = obtener_mapa_bp_jerarquia()
    coords_pasajeros = set(obtener_coordinadores_disponibles("PASAJEROS"))
    coords_b2b = set(obtener_coordinadores_disponibles("B2B"))
    bps_b2b, bps_cargo = obtener_bps_b2b_y_cargo()
    omni_activity = obtener_presencia_omni_por_fecha(fecha)

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
            SELECT agente, presence_label, inicio, fin, duracion_min, servicio, coordinador, jefe_inmediato
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
        ("Almuerzo (Lunch)", "lunch_ini", "lunch_fin", {"Lunch", "Almuerzo", "Refeição (sólo BR)"}),
        ("Diálogo Diario (4DX)", "dialogo_ini", "dialogo_fin", {"Diálogo Diario / 4DX", "PCA- Diálogo"}),
        ("Capacitación (Training)", "training_1_ini", "training_1_fin", {"Cursos Adicionales", "Refuerzo Semanal", "Training"})
    ]

    for _, row in df_t.iterrows():
        bp = str(row["bp"]).strip()
        nom = row["nombre_agente"] or f"Asesor BP {bp}"
        srv = str(row["servicio"]).strip() if pd.notna(row.get("servicio")) and str(row["servicio"]).strip() else "LATAM"

        # REGLA MAESTRA DE CUENTA LATAM:
        # Descartar inmediatamente personal o turnos de campañas externas (Claro, Chec, Colmédica, etc.)
        if not es_servicio_latam(srv):
            continue

        sub_seg = seg_by_bp.get(bp)
        coord_real = (sub_seg["coordinador"].iloc[0] if sub_seg is not None and not sub_seg.empty and pd.notna(sub_seg["coordinador"].iloc[0]) else None) or bp_to_coord.get(bp, "")
        superv_real = (sub_seg["jefe_inmediato"].iloc[0] if sub_seg is not None and not sub_seg.empty and pd.notna(sub_seg["jefe_inmediato"].iloc[0]) else None) or bp_to_superv.get(bp, "")

        # Exclusiones de ámbito (Regla de Oro Marely Cardona)
        es_de_marely = es_equipo_marely_cardona(
            coordinador=coord_real,
            jefe_inmediato=superv_real,
            bp=bp,
            nombre=nom,
            servicio=srv
        )

        if ambito == "PASAJEROS":
            if bp in bps_cargo or "CARGO" in srv.upper():
                continue
            if es_de_marely:
                continue
            if es_persona_excluida(nom) or (coord_real and es_persona_excluida(coord_real)) or (superv_real and es_persona_excluida(superv_real)):
                continue
        elif ambito == "B2B":
            if not es_de_marely:
                continue
            if es_persona_excluida(nom):
                continue
        else:
            # En ámbito TODOS, descartar registros de cuentas ajenas que no operan en LATAM
            if sub_seg is None and not es_servicio_latam(srv):
                continue

        if coordinador and coordinador != "Todos los Coordinadores":
            if not coord_real or coordinador.upper() not in coord_real.upper():
                continue
        if supervisor and supervisor != "Todos los Supervisores":
            if not superv_real or supervisor.upper() not in superv_real.upper():
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

            tramo_real = None
            if sub_seg is not None and not sub_seg.empty:
                if "Almuerzo" in label_pausa:
                    # Incluye Lunch, Almuerzo y desconexiones Offline intradía (entre 15 y 90 min)
                    candidatos = sub_seg[
                        (sub_seg["presence_label"].isin(labels_presencia)) |
                        ((sub_seg["presence_label"] == "Offline") & (sub_seg["duracion_min"] <= 90.0) & (sub_seg["duracion_min"] >= 15.0))
                    ].copy()
                else:
                    candidatos = sub_seg[sub_seg["presence_label"].isin(labels_presencia)].copy()

                if not candidatos.empty:
                    candidatos["distancia"] = (candidatos["t_ini_min"] - prog_ini_min).abs()
                    cercanos = candidatos[candidatos["distancia"] <= 120].sort_values("distancia")
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
                elif exceso_dur_min > 3:
                    estado_p = "🔴 Exceso de Tiempo"
                elif abs(desvio_ini_min) > tolerancia_min:
                    estado_p = "🟡 Desfasada en horario"
                else:
                    estado_p = "🟢 Puntual y en tiempo"
            elif bp in omni_activity and omni_activity[bp].get("tramos_pausas"):
                omni_act = omni_activity[bp]
                candidatos_omni = []
                for p in omni_act.get("tramos_pausas", []):
                    p_ini_m = _time_to_minutes(p["inicio"])
                    p_fin_m = _time_to_minutes(p["fin"])
                    p_dur_m = float(p.get("duracion_min", 0.0))
                    dist = abs(p_ini_m - prog_ini_min)
                    if dist <= 120:
                        candidatos_omni.append((dist, p_ini_m, p_fin_m, p_dur_m, p["inicio"], p["fin"]))
                if candidatos_omni:
                    candidatos_omni.sort(key=lambda x: x[0])
                    best_c = candidatos_omni[0]
                    real_ini_min = best_c[1]
                    real_fin_min = best_c[2]
                    real_dur_min = float(best_c[3])
                    desvio_ini_min = int(round(real_ini_min - prog_ini_min))
                    exceso_dur_min = int(round(real_dur_min - prog_dur_min))
                    hora_real_str = f"{best_c[4]} - {best_c[5]} (Omni)"
                    if abs(desvio_ini_min) <= tolerancia_min and exceso_dur_min <= 3:
                        estado_p = "🟢 Puntual y en tiempo"
                    elif exceso_dur_min > 3:
                        estado_p = "🔴 Exceso de Tiempo"
                    elif abs(desvio_ini_min) > tolerancia_min:
                        estado_p = "🟡 Desfasada en horario"
                    else:
                        estado_p = "🟢 Puntual y en tiempo"
                else:
                    hora_real_str = "--"
                    real_dur_min = 0.0
                    desvio_ini_min = None
                    exceso_dur_min = None
                    estado_p = "❌ Pausa No Tomada en Ventana"
            else:
                hora_real_str = "--"
                real_dur_min = 0.0
                desvio_ini_min = None
                exceso_dur_min = None
                estado_p = "❌ Pausa No Tomada en Ventana"

            filas_pausas.append({
                "BP": bp,
                "Asesor": nom,
                "Coordinador": coord_real or "No Asignado",
                "Supervisor": superv_real or "No Asignado",
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


@st.cache_data(ttl=1800, show_spinner=False)
def calcular_auditoria_integral_unificada(fecha: str, coordinador: str = None, supervisor: str = None, servicio: str = None, ambito: str = "TODOS") -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Combina en un solo DataFrame por asesor:
    - Cumplimiento de horas de turno (jornada programada, conexión real, horas productivas, horas pausas, brecha y estado).
    - Resumen consolidado de adherencia y puntualidad a pausas (total pausas, puntuales, excesos, % adherencia, minutos de exceso y detalle).
    Retorna (df_unificado, df_pausas_detalle).
    """
    df_horas = calcular_cumplimiento_horas_turno(fecha, coordinador=coordinador, supervisor=supervisor, servicio=servicio, ambito=ambito)
    df_pausas = calcular_adherencia_pausas_intradia(fecha, coordinador=coordinador, supervisor=supervisor, servicio=servicio, tolerancia_min=20, ambito=ambito)

    if df_horas.empty:
        return pd.DataFrame(), df_pausas

    # Agrupar métricas de pausas por BP
    pausas_resumen = []
    if not df_pausas.empty:
        for bp, grp in df_pausas.groupby("BP"):
            tot_p = len(grp)
            puntuales = int(grp["Estado"].str.startswith("🟢").sum())
            desfasadas = int(grp["Estado"].str.startswith("🟡").sum())
            excesos = int(grp["Estado"].str.startswith("🔴").sum())
            no_tomadas = int(grp["Estado"].str.startswith("❌").sum())

            excesos_mins = 0
            for exc_str in grp["Exceso"]:
                if exc_str and exc_str != "--" and "+" in str(exc_str):
                    try:
                        excesos_mins += int(str(exc_str).replace("+", "").replace("min", "").strip())
                    except Exception:
                        pass

            pct_adh = round(puntuales / max(1, tot_p) * 100, 1)

            partes = []
            if puntuales > 0:
                partes.append(f"🟢 {puntuales} Puntual{'es' if puntuales > 1 else ''}")
            if desfasadas > 0:
                partes.append(f"🟡 {desfasadas} Desfasada{'s' if desfasadas > 1 else ''}")
            if excesos > 0:
                partes.append(f"🔴 {excesos} Exceso (+{excesos_mins}m)")
            if no_tomadas > 0:
                partes.append(f"❌ {no_tomadas} No tomada{'s' if no_tomadas > 1 else ''}")

            res_str = " | ".join(partes) if partes else "Sin Pausas"

            pausas_resumen.append({
                "BP": bp,
                "Pausas Prog": tot_p,
                "Pausas Puntuales": puntuales,
                "Pausas con Exceso": excesos,
                "Pausas Desfasadas": desfasadas,
                "Pausas No Tomadas": no_tomadas,
                "% Adh Pausas": pct_adh,
                "Minutos Exceso": f"+{excesos_mins} min" if excesos_mins > 0 else "0 min",
                "Exceso Mins Int": excesos_mins,
                "Resumen Pausas": res_str
            })

    df_resumen_p = pd.DataFrame(pausas_resumen)

    if not df_resumen_p.empty:
        df_unif = pd.merge(df_horas, df_resumen_p, on="BP", how="left")
    else:
        df_unif = df_horas.copy()
        df_unif["Pausas Prog"] = 0
        df_unif["Pausas Puntuales"] = 0
        df_unif["Pausas con Exceso"] = 0
        df_unif["Pausas Desfasadas"] = 0
        df_unif["Pausas No Tomadas"] = 0
        df_unif["% Adh Pausas"] = 0.0
        df_unif["Minutos Exceso"] = "0 min"
        df_unif["Exceso Mins Int"] = 0
        df_unif["Resumen Pausas"] = "Sin Pausas Programadas"

    df_unif["Pausas Prog"] = df_unif["Pausas Prog"].fillna(0).astype(int)
    df_unif["Pausas Puntuales"] = df_unif["Pausas Puntuales"].fillna(0).astype(int)
    df_unif["Pausas con Exceso"] = df_unif["Pausas con Exceso"].fillna(0).astype(int)
    df_unif["Pausas Desfasadas"] = df_unif["Pausas Desfasadas"].fillna(0).astype(int)
    df_unif["Pausas No Tomadas"] = df_unif["Pausas No Tomadas"].fillna(0).astype(int)
    df_unif["% Adh Pausas"] = df_unif["% Adh Pausas"].fillna(0.0)
    df_unif["Minutos Exceso"] = df_unif["Minutos Exceso"].fillna("0 min")
    df_unif["Exceso Mins Int"] = df_unif["Exceso Mins Int"].fillna(0).astype(int)
    df_unif["Resumen Pausas"] = df_unif["Resumen Pausas"].fillna("Sin Pausas Programadas")

    return df_unif, df_pausas


# ── RENDERER UNIFICADO: TURNOS Y PAUSAS EN UNA SOLA PANTALLA ────────────────

def render_ui_auditoria_integral(ambito: str = "PASAJEROS", key_prefix: str = "pas_audit_"):
    """
    Renderiza la vista unificada de Cumplimiento de Horas de Turno y Adherencia a Pausas
    en una sola pantalla integral con filtro por Coordinador y Supervisor.
    """
    fechas_disp = obtener_fechas_disponibles_turnos()
    if not fechas_disp:
        st.warning("⚠️ No se encontraron turnos detallados en la base de datos.")
        return

    # Selección de fecha por defecto: Día anterior (ayer) para evitar días futuros sin conexión
    ayer_str = (datetime.now().date() - timedelta(days=1)).strftime("%Y-%m-%d")
    idx_def = 0
    if ayer_str in fechas_disp:
        idx_def = fechas_disp.index(ayer_str)
    else:
        fechas_pasadas = [f for f in fechas_disp if f <= ayer_str]
        if fechas_pasadas:
            idx_def = fechas_disp.index(fechas_pasadas[0])

    # Fila de Filtros
    coords_disp = ["Todos los Coordinadores"] + obtener_coordinadores_disponibles(ambito)
    servs_disp = ["Todos los Servicios"] + obtener_servicios_disponibles(ambito)

    c_f1, c_f2, c_f3, c_f4, c_f5, c_f6 = st.columns([1.1, 1.4, 1.4, 1.3, 1.4, 1.4])
    with c_f1:
        fecha_sel = st.selectbox("📅 Fecha", options=fechas_disp, index=idx_def, key=f"{key_prefix}fecha")
    with c_f2:
        coord_sel = st.selectbox("👤 Coordinación", options=coords_disp, index=0, key=f"{key_prefix}coord")

    # Supervisores filtrados dinámicamente según la coordinación
    sups_disp = ["Todos los Supervisores"] + obtener_supervisores_disponibles(coordinador=coord_sel, ambito=ambito)
    with c_f3:
        superv_sel = st.selectbox("🎖️ Supervisor", options=sups_disp, index=0, key=f"{key_prefix}superv")
    with c_f4:
        serv_sel = st.selectbox("🏢 Servicio / Cola", options=servs_disp, index=0, key=f"{key_prefix}serv")
    with c_f5:
        estado_sel = st.selectbox(
            "🚦 Filtro Estado",
            options=[
                "Todos los Estados",
                "🟢 Cumple Jornada Completa",
                "🔵 Conectado en Salesforce",
                "🟡 Déficit Leve (< 1h)",
                "🔴 Déficit Severo (> 1h faltante)",
                "❌ Ausente / Sin Conexión",
                "🚨 Con Exceso en Pausas",
                "⚠️ Con Pausas Desfasadas",
            ],
            index=0,
            key=f"{key_prefix}est"
        )
    with c_f6:
        search_asesor = st.text_input("🔍 Buscar Asesor / BP", key=f"{key_prefix}search").strip().lower()

    # Ejecución unificada
    df_unif, df_pausas = calcular_auditoria_integral_unificada(
        fecha_sel,
        coordinador=coord_sel,
        supervisor=superv_sel,
        servicio=serv_sel,
        ambito=ambito
    )

    if df_unif.empty:
        st.info(f"No hay registros de turnos o conexión para la fecha **{fecha_sel}** con los filtros aplicados.")
        return

    # Aplicar filtros de estado y búsqueda
    if estado_sel == "🟢 Cumple Jornada Completa":
        df_unif = df_unif[df_unif["Estado Turno"] == "🟢 Cumple Jornada Completa"]
    elif estado_sel == "🔵 Conectado en Salesforce":
        df_unif = df_unif[df_unif["Estado Turno"].str.contains("Salesforce", case=False, na=False)]
    elif estado_sel == "🟡 Déficit Leve (< 1h)":
        df_unif = df_unif[df_unif["Estado Turno"] == "🟡 Déficit Leve (< 1h)"]
    elif estado_sel == "🔴 Déficit Severo (> 1h faltante)":
        df_unif = df_unif[df_unif["Estado Turno"] == "🔴 Déficit Severo (> 1h faltante)"]
    elif estado_sel == "❌ Ausente / Sin Conexión":
        df_unif = df_unif[df_unif["Estado Turno"] == "❌ Ausente / Sin Conexión"]
    elif estado_sel == "🚨 Con Exceso en Pausas":
        df_unif = df_unif[df_unif["Pausas con Exceso"] > 0]
    elif estado_sel == "⚠️ Con Pausas Desfasadas":
        df_unif = df_unif[df_unif["Pausas Desfasadas"] > 0]

    if search_asesor:
        df_unif = df_unif[
            df_unif["Asesor"].astype(str).str.lower().str.contains(search_asesor) |
            df_unif["BP"].astype(str).str.lower().str.contains(search_asesor) |
            df_unif["Supervisor"].astype(str).str.lower().str.contains(search_asesor)
        ]

    if df_unif.empty:
        st.warning("No hay registros que coincidan con los filtros o búsqueda.")
        return

    # ── TARJETAS DE KPIS UNIFICADAS ──────────────────────────────────────────
    tot_asesores = len(df_unif)
    h_prog_tot = float(df_unif["Horas Prog"].sum())
    h_con_tot = float(df_unif["Horas Conectado"].sum())
    pct_cumpl_jornada = round((h_con_tot / h_prog_tot * 100), 1) if h_prog_tot > 0 else 0.0
    tot_pausas_prog = int(df_unif["Pausas Prog"].sum())
    tot_pausas_punt = int(df_unif["Pausas Puntuales"].sum())
    pct_adh_pausas = round(tot_pausas_punt / max(1, tot_pausas_prog) * 100, 1)

    asesores_deficit = int(df_unif["Estado Turno"].isin(["🟡 Déficit Leve (< 1h)", "🔴 Déficit Severo (> 1h faltante)"]).sum())
    horas_deficit_tot = round(abs(df_unif[df_unif["Brecha Horas"] < 0]["Brecha Horas"].sum()), 1)
    asesores_exceso_pausa = int((df_unif["Pausas con Exceso"] > 0).sum())
    minutos_exceso_tot = int(df_unif["Exceso Mins Int"].sum())
    ausentes_tot = int((df_unif["Estado Turno"] == "❌ Ausente / Sin Conexión").sum())

    k1, k2, k3, k4, k5, k6 = st.columns(6)
    with k1:
        st.metric("Asesores Programados", tot_asesores)
    with k2:
        st.metric("% Cumplimiento Turno", f"{pct_cumpl_jornada}%", delta=f"{round(pct_cumpl_jornada - 100, 1)}% vs 100%")
    with k3:
        st.metric("% Adherencia Pausas", f"{pct_adh_pausas}%", delta=f"{round(pct_adh_pausas - 85.0, 1)}% vs Meta 85%")
    with k4:
        st.metric("⚠️ En Déficit de Turno", asesores_deficit, delta=f"-{horas_deficit_tot} h faltantes", delta_color="inverse")
    with k5:
        st.metric("🔴 Con Exceso en Pausas", asesores_exceso_pausa, delta=f"+{minutos_exceso_tot} min exceso", delta_color="inverse")
    with k6:
        st.metric("❌ Sin Conexión", ausentes_tot)

    st.write("")

    # ── GRÁFICOS INTEGRADOS (2 COLUMNAS) ─────────────────────────────────────
    col_g1, col_g2 = st.columns([1, 1.4])
    with col_g1:
        st.markdown("##### 🎯 Distribución Cumplimiento de Jornada")
        dist_turnos = df_unif["Estado Turno"].value_counts().reset_index()
        dist_turnos.columns = ["Estado", "Cantidad"]
        pie_colors = {
            "🟢 Cumple Jornada Completa": "#10b981",
            "🟡 Déficit Leve (< 1h)": "#f59e0b",
            "🔴 Déficit Severo (> 1h faltante)": "#ef4444",
            "❌ Ausente / Sin Conexión": "#64748b"
        }
        for est_val in dist_turnos["Estado"].unique():
            if "Salesforce" in str(est_val):
                pie_colors[est_val] = "#3b82f6"

        fig_pie = px.pie(
            dist_turnos,
            names="Estado",
            values="Cantidad",
            hole=0.45,
            color="Estado",
            color_discrete_map=pie_colors
        )
        fig_pie.update_layout(
            height=280,
            margin=dict(l=10, r=10, t=25, b=10),
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="center",
                x=0.5,
                title_text=""
            )
        )
        st.plotly_chart(fig_pie, use_container_width=True)

    with col_g2:
        st.markdown("##### ☕ Disciplina de Pausas por Tipo de Descanso")
        if not df_pausas.empty:
            df_p_filt = df_pausas[df_pausas["BP"].isin(df_unif["BP"])].copy()
            if not df_p_filt.empty:
                df_p_grp = df_p_filt.groupby(["Tipo Pausa", "Estado"]).size().reset_index(name="Cantidad")
                fig_bar_p = px.bar(
                    df_p_grp,
                    x="Tipo Pausa",
                    y="Cantidad",
                    color="Estado",
                    barmode="stack",
                    category_orders={
                        "Estado": [
                            "🟢 Puntual y en tiempo",
                            "🟡 Desfasada en horario",
                            "🔴 Exceso de Tiempo",
                            "❌ Pausa No Tomada en Ventana"
                        ]
                    },
                    color_discrete_map={
                        "🟢 Puntual y en tiempo": "#10b981",
                        "🟡 Desfasada en horario": "#f59e0b",
                        "🔴 Exceso de Tiempo": "#ef4444",
                        "❌ Pausa No Tomada en Ventana": "#64748b"
                    }
                )
                fig_bar_p.update_layout(
                    height=280,
                    margin=dict(l=10, r=10, t=25, b=10),
                    legend=dict(
                        orientation="h",
                        yanchor="bottom",
                        y=1.02,
                        xanchor="center",
                        x=0.5,
                        title_text=""
                    )
                )
                st.plotly_chart(fig_bar_p, use_container_width=True)
            else:
                st.info("Sin descansos registrados para los asesores seleccionados.")
        else:
            st.info("Sin pausas programadas para auditar.")

    st.write("")

    # ── TABLA MAESTRA UNIFICADA (TURNO + PAUSAS EN UNA SOLA VISTA) ───────────
    st.markdown("##### 📋 Matriz Unificada de Asesores: Jornada Laboral & Control de Pausas")
    st.caption("Consolida el cumplimiento del turno completo contratado y la puntualidad y excesos en pausas programadas (Descanso 1 y 2, Lunch, Diálogo 4DX y Training).")

    cols_unif_show = [
        "BP", "Asesor", "Coordinador", "Supervisor", "Servicio",
        "Turno Programado", "Horas Prog", "Conexión Real", "Horas Conectado",
        "Horas Productivas", "Horas Pausas", "% Cumplimiento", "Brecha Horas",
        "Estado Turno", "Pausas Prog", "Pausas Puntuales", "% Adh Pausas",
        "Minutos Exceso", "Resumen Pausas"
    ]
    df_show = df_unif[[c for c in cols_unif_show if c in df_unif.columns]].copy()

    column_cfg_unif = {
        "% Cumplimiento": st.column_config.ProgressColumn(
            "% Turno",
            help="Horas reales conectado vs horas programadas de turno",
            format="%.1f%%",
            min_value=0,
            max_value=120
        ),
        "% Adh Pausas": st.column_config.ProgressColumn(
            "% Adh Pausas",
            help="% de pausas tomadas en horario y duración reglamentaria",
            format="%.1f%%",
            min_value=0,
            max_value=100
        ),
        "Horas Prog": st.column_config.NumberColumn("H. Prog", format="%.2f h"),
        "Horas Conectado": st.column_config.NumberColumn("H. Conectado", format="%.2f h"),
        "Horas Productivas": st.column_config.NumberColumn("H. Prod", format="%.2f h"),
        "Horas Pausas": st.column_config.NumberColumn("H. Pausas", format="%.2f h"),
        "Brecha Horas": st.column_config.NumberColumn("Brecha", format="%.2f h"),
    }
    event_tabla = st.dataframe(
        df_show,
        column_config=column_cfg_unif,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key=f"{key_prefix}tbl_unif"
    )

    # Identificar si se seleccionó una fila por clic
    bp_seleccionado = None
    nom_seleccionado = None
    row_seleccionada = None

    if event_tabla and hasattr(event_tabla, "selection") and event_tabla.selection:
        filas_sel = event_tabla.selection.get("rows", [])
        if filas_sel and len(filas_sel) > 0 and filas_sel[0] < len(df_show):
            row_seleccionada = df_show.iloc[filas_sel[0]]
            bp_seleccionado = str(row_seleccionada["BP"]).strip()
            nom_seleccionado = str(row_seleccionada["Asesor"]).strip()

    # Botón de descarga de la matriz unificada
    csv_u = df_show.to_csv(index=False).encode('utf-8-sig')
    st.download_button(
        label=f"📥 Descargar Matriz Unificada de Asesores ({ambito}) (CSV)",
        data=csv_u,
        file_name=f"auditoria_turnos_pausas_{ambito.lower()}_{fecha_sel}.csv",
        mime="text/csv",
        key=f"{key_prefix}btn_dl_unif"
    )

    # ── SECCIÓN DINÁMICA DE AUDITORÍA INTRADÍA DE DESCANSOS ───────────────────
    st.write("")
    
    # Encabezado dinámico con selector complementario
    c_sub_t1, c_sub_t2 = st.columns([2.3, 1.7])
    with c_sub_t1:
        st.markdown("##### 🔍 Auditoría Intradía Franja a Franja de Descansos")
    with c_sub_t2:
        lista_opciones_as = ["-- Todos los Asesores --"] + [f"{r['Asesor']} (BP {r['BP']})" for _, r in df_show.iterrows()]
        idx_default_dd = 0
        if bp_seleccionado:
            for i_opc, opc_text in enumerate(lista_opciones_as):
                if f"(BP {bp_seleccionado})" in opc_text:
                    idx_default_dd = i_opc
                    break
        asesor_dropdown = st.selectbox(
            "Filtrar Asesor para Inspección",
            options=lista_opciones_as,
            index=idx_default_dd,
            key=f"{key_prefix}dropdown_asesor_p",
            label_visibility="collapsed"
        )
        if asesor_dropdown != "-- Todos los Asesores --":
            bp_extraido = asesor_dropdown.split("(BP ")[-1].replace(")", "").strip()
            bp_seleccionado = bp_extraido
            sub_matches = df_show[df_show["BP"] == bp_seleccionado]
            if not sub_matches.empty:
                row_seleccionada = sub_matches.iloc[0]
                nom_seleccionado = str(row_seleccionada["Asesor"]).strip()
        elif not (event_tabla and hasattr(event_tabla, "selection") and event_tabla.selection and event_tabla.selection.get("rows")):
            bp_seleccionado = None

    if bp_seleccionado and row_seleccionada is not None:
        t_prog = row_seleccionada.get("Turno Programado", "--")
        h_con = row_seleccionada.get("Conexión Real", "--")
        brecha_h = row_seleccionada.get("Brecha Horas", 0.0)
        est_t = row_seleccionada.get("Estado Turno", "--")
        p_prog = row_seleccionada.get("Pausas Prog", 0)
        p_punt = row_seleccionada.get("Pausas Puntuales", 0)
        pct_adh_p = row_seleccionada.get("% Adh Pausas", 0.0)
        mins_exc = row_seleccionada.get("Minutos Exceso", "0 min")

        st.markdown(
            f"""
            <div style="background: linear-gradient(90deg, #1e293b 0%, #0f172a 100%); border-left: 5px solid #3b82f6; padding: 12px 18px; border-radius: 10px; margin: 5px 0 12px 0;">
                <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
                    <div>
                        <h4 style="color: #ffffff; margin: 0 0 4px 0; font-size: 16px;">👤 {nom_seleccionado} <span style="color: #94a3b8; font-size: 12px;">(BP {bp_seleccionado})</span></h4>
                        <span style="color: #cbd5e1; font-size: 12px;">
                            Turno: <b>{t_prog}</b> • Conexión Real: <b>{h_con}</b> • Brecha: <b>{brecha_h:+.2f} h</b> ({est_t})
                        </span>
                    </div>
                    <div style="text-align: right; background: #334155; padding: 5px 12px; border-radius: 6px;">
                        <span style="color: #38bdf8; font-size: 11px; font-weight: 700; text-transform: uppercase;">Adherencia Pausas: {pct_adh_p:.1f}%</span><br>
                        <span style="color: #e2e8f0; font-size: 12px;">{p_punt}/{p_prog} puntuales • Exceso: <b>{mins_exc}</b></span>
                    </div>
                </div>
            </div>
            """,
            unsafe_allow_html=True
        )

        df_p_view = df_pausas[df_pausas["BP"] == bp_seleccionado].copy()
        if not df_p_view.empty:
            cols_p_individual = [
                "Tipo Pausa", "Horario Programado", "Duración Prog",
                "Horario Real", "Duración Real", "Desvío Salida", "Exceso", "Estado"
            ]
            df_p_view = df_p_view[[c for c in cols_p_individual if c in df_p_view.columns]]
            st.dataframe(df_p_view, use_container_width=True, hide_index=True)

            csv_indiv = df_p_view.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label=f"📥 Descargar Pausas de {nom_seleccionado} (CSV)",
                data=csv_indiv,
                file_name=f"pausas_{bp_seleccionado}_{fecha_sel}.csv",
                mime="text/csv",
                key=f"{key_prefix}btn_dl_indiv"
            )
        else:
            st.info(f"El asesor {nom_seleccionado} no registra pausas programadas para la fecha {fecha_sel}.")

    else:
        st.caption("💡 **Tip Operativo**: Haz clic en cualquier fila de la tabla superior (o selecciona en el buscador) para inspeccionar de forma individualizada y exclusiva los descansos de ese asesor.")
        with st.expander("📂 Ver Listado Completo de Pausas de Todos los Asesores", expanded=False):
            if not df_pausas.empty:
                df_p_view = df_pausas[df_pausas["BP"].isin(df_unif["BP"])].copy()
                if not df_p_view.empty:
                    cols_p_order = [
                        "BP", "Asesor", "Coordinador", "Supervisor", "Servicio",
                        "Tipo Pausa", "Horario Programado", "Duración Prog",
                        "Horario Real", "Duración Real", "Desvío Salida", "Exceso", "Estado"
                    ]
                    df_p_view = df_p_view[[c for c in cols_p_order if c in df_p_view.columns]]
                    st.dataframe(df_p_view, use_container_width=True, hide_index=True)

                    csv_p = df_p_view.to_csv(index=False).encode('utf-8-sig')
                    st.download_button(
                        label=f"📥 Descargar Detalle Intradía de Pausas ({ambito}) (CSV)",
                        data=csv_p,
                        file_name=f"detalle_pausas_intradia_{ambito.lower()}_{fecha_sel}.csv",
                        mime="text/csv",
                        key=f"{key_prefix}btn_dl_p_det"
                    )
                else:
                    st.info("Sin registros de pausas para los asesores filtrados.")
            else:
                st.info("Sin datos de pausas disponibles.")


def render_subtab_pausas_pasajeros(render_tab_historico_fn=None, agentes_map: dict = None):
    """
    Submódulo integral de Pausas, Adherencia y Horas de Turno para LATAM Pasajeros.
    Unifica en una sola vista el cumplimiento de turno y la disciplina de pausas.
    """
    st.markdown("### ⏸️ Pausas, Adherencia y Cumplimiento de Turno — LATAM Pasajeros")
    st.caption("Auditoría unificada de jornada laboral y disciplina de descansos intradía (Descansos 1 y 2, Lunch, Diálogo 4DX y Capacitaciones) con filtro por Coordinador y Supervisor.")

    SUB_PAUSAS_PASAJEROS = [
        "⚡ Auditoría Integral: Turnos & Pausas Unificadas",
        "📊 Histórico y Fuga de Estados Genesys",
        "💬 Simultaneidad WhatsApp (Pasajeros)"
    ]
    sel_sub = st.segmented_control(
        "Módulo de Cumplimiento Pasajeros",
        options=SUB_PAUSAS_PASAJEROS,
        default=SUB_PAUSAS_PASAJEROS[0],
        key="sub_pasajeros_pausas_activo",
        label_visibility="collapsed"
    )
    if not sel_sub:
        sel_sub = SUB_PAUSAS_PASAJEROS[0]

    st.write("")

    if sel_sub == "⚡ Auditoría Integral: Turnos & Pausas Unificadas":
        render_ui_auditoria_integral(ambito="PASAJEROS", key_prefix="pasajeros_audit_")
    elif sel_sub == "📊 Histórico y Fuga de Estados Genesys":
        if render_tab_historico_fn:
            render_tab_historico_fn(key_prefix="pasajeros_pausas_hist_", excluir_b2b_y_cargo=True)
        else:
            st.info("Cargando motor de pausas de Genesys...")
    elif sel_sub == "💬 Simultaneidad WhatsApp (Pasajeros)":
        from live_engine import obtener_token_genesys
        from whatsapp_simultaneidad_engine import render_panel_simultaneidad_whatsapp_historico
        token = obtener_token_genesys()
        if not token:
            st.warning("⚠️ No se encontró token activo de Genesys Cloud.")
        else:
            col_f, col_c = st.columns([1.2, 2.5])
            with col_f:
                f_sel = st.date_input("Fecha de Auditoría", value=datetime.now().date(), key="wsp_hist_fecha_sel")
            with col_c:
                mapa_ag = agentes_map if agentes_map else {}
                coords = sorted(list(set(v.get("coordinador", "") for v in mapa_ag.values() if v.get("coordinador"))))
                c_sel = st.selectbox("Filtrar por Coordinador", options=["TODOS"] + coords, key="wsp_hist_coord_sel")

            render_panel_simultaneidad_whatsapp_historico(token, f_sel, mapa_ag, coordinador_filtro=c_sel)

