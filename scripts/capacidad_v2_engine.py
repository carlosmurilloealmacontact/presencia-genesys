"""
scripts/capacidad_v2_engine.py
===============================================================================
Motor de Diagnóstico y Capacidad Operativa WFM 2.0 (Laboratorio Experimental).
Exclusivo para Carlos Murillo.

Cruza las tres fuentes de verdad operativas:
  1. WFM Sore Forecast (Requerimiento planificado y dimensionamiento Erlang)
  2. Malla de Turnos & Pausas (Programación contractual en turnos_detallados)
  3. Presencia Real Genesys (Segments de login y estados de presencia)

Permite contrastar lado a lado:
  - Modelo Clásico 1.0 (Diagnóstico macro y shrinkage plano de 14%)
  - Modelo Avanzado 2.0 (Subprogramación WFM vs Fuga de Jornada vs Excesos y Descalce en Pausas)
===============================================================================
"""

import os
import sqlite3
import numpy as np
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path
from datetime import datetime

from capacidad_engine import (
    cargar_forecast_sore_completo,
    obtener_servicios_raw_para_sore,
    calcular_capacidad_intervalos_real,
    obtener_metricas_servicio_intradia,
    cargar_presencia_resumen_rango,
    obtener_metricas_gtr_rango,
    cargar_demanda_zendesk_bo,
    diagnosticar_causa_raiz,
    META_AUXILIARES_OFICIAL,
    META_AUSENTISMO_OFICIAL,
    HOMOLOGACION_PRESENCIA_A_SORE
)

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


# ── CARGA DE LA MATRIZ BASE (MODELO 1.0) ───────────────────────────────────────
def calcular_ejecutiva_capacidad_base(fecha_desde: str, fecha_hasta: str, srv_filtro: str = None) -> pd.DataFrame:
    """
    Calcula la tabla base de capacidad (Modelo 1.0) consolidando:
      - Forecast Sore (WFM)
      - Presencia Genesys
      - Métricas GTR (Tráfico, AHT, NS)
    """
    df_fore_all = cargar_forecast_sore_completo()
    if df_fore_all.empty:
        return pd.DataFrame()

    df_fore_rango = df_fore_all[(df_fore_all["fecha"] >= fecha_desde) & (df_fore_all["fecha"] <= fecha_hasta)].copy()
    if df_fore_rango.empty:
        return pd.DataFrame()

    dias_unicos = df_fore_rango["fecha"].nunique()
    num_dias = max(1, dias_unicos)

    fore_summary = df_fore_rango.groupby(["servicio", "tipo_mundo"]).agg({
        "traffic_forecast": "sum",
        "minutos_req": "sum",
        "meta_aht_plana": "first",
        "meta_ns": "first"
    }).reset_index()

    fore_summary["fte_dia_requerido"] = (fore_summary["minutos_req"] / (480.0 * num_dias)).round(1)

    df_pres_rango = cargar_presencia_resumen_rango(fecha_desde, fecha_hasta, num_dias=num_dias)
    df_gtr_rango = obtener_metricas_gtr_rango(fecha_desde, fecha_hasta)
    df_zd_sum, _ = cargar_demanda_zendesk_bo(fecha_desde, fecha_hasta)

    dict_zd_nuevos = dict(zip(df_zd_sum["servicio"], df_zd_sum["Casos_Nuevos"])) if not df_zd_sum.empty else {}
    dict_zd_resueltos = dict(zip(df_zd_sum["servicio"], df_zd_sum["Casos_Resueltos"])) if not df_zd_sum.empty else {}
    dict_zd_tasa = dict(zip(df_zd_sum["servicio"], df_zd_sum["tasa_resolucion_pct"])) if not df_zd_sum.empty else {}

    if not df_pres_rango.empty:
        matriz = pd.merge(fore_summary, df_pres_rango, on="servicio", how="left").fillna(0.0)
    else:
        matriz = fore_summary.copy()
        matriz["min_conectado"] = 0.0
        matriz["min_pausas"] = 0.0
        matriz["min_disponible"] = 0.0
        matriz["pct_auxiliares_real"] = 0.0
        matriz["fte_reales_conectados"] = 0.0
        matriz["fte_reales_disponibles"] = 0.0

    if not df_gtr_rango.empty:
        matriz = pd.merge(matriz, df_gtr_rango, on="servicio", how="left")
    else:
        matriz["trafico_real"] = np.nan
        matriz["aht_real_seg"] = np.nan
        matriz["ns_real"] = np.nan

    if srv_filtro and srv_filtro != "Todos los Servicios":
        matriz = matriz[matriz["servicio"] == srv_filtro]

    filas = []
    for _, row in matriz.iterrows():
        srv = row["servicio"]
        tipo = row["tipo_mundo"]
        fte_req = float(row["fte_dia_requerido"])
        fte_con = float(row.get("fte_reales_conectados", 0.0))
        fte_disp = float(row.get("fte_reales_disponibles", 0.0))
        aux_real = float(row.get("pct_auxiliares_real", 0.0))
        meta_aht = float(row.get("meta_aht_plana", 0.0))
        meta_ns = float(row.get("meta_ns", 80.0))
        min_req = float(row.get("minutos_req", 0.0))
        min_disp = float(row.get("min_disponible", 0.0))
        min_con = float(row.get("min_conectado", 0.0))
        min_pau = float(row.get("min_pausas", 0.0))

        traf_plan = float(row.get("traffic_forecast", 0.0))
        traf_real = row.get("trafico_real")
        aht_real = row.get("aht_real_seg")
        ns_real = row.get("ns_real")

        es_canal_bo = ("Back" in tipo or "BO" in tipo or srv in dict_zd_nuevos)
        if es_canal_bo and pd.isna(traf_real) and srv in dict_zd_nuevos:
            traf_real = dict_zd_nuevos[srv]

        pct_capacidad = (min_disp / min_req * 100.0) if min_req > 0 else 100.0
        gap_fte = fte_con - fte_req

        if srv in ["BO_CORPORATE", "BO AGENCIAS TARGET"]:
            causa_1 = "☁️ Operación B2B vía Salesforce (Mesa Externa)"
        elif es_canal_bo and srv in dict_zd_nuevos:
            res_val = dict_zd_resueltos.get(srv, 0)
            tasa_val = dict_zd_tasa.get(srv, 0)
            causa_1 = f"🎫 Back Office Zendesk: {res_val:,.0f} resueltos ({tasa_val:.0f}% cierre)"
        else:
            causa_1 = diagnosticar_causa_raiz(
                gap_personas=gap_fte,
                aux_real=aux_real,
                aux_meta=META_AUXILIARES_OFICIAL,
                pct_capacidad=pct_capacidad
            )

        filas.append({
            "Servicio": srv,
            "Tipo": tipo,
            "FTE Requerido": fte_req,
            "FTE Conectado": fte_con,
            "FTE Disponible": fte_disp,
            "Brecha FTE 1.0": gap_fte,
            "% Aux Real": aux_real,
            "Meta Aux": META_AUXILIARES_OFICIAL,
            "Tráfico Plan": traf_plan,
            "Tráfico Real": traf_real if pd.notna(traf_real) else np.nan,
            "AHT Plan (s)": meta_aht,
            "AHT Real (s)": aht_real if pd.notna(aht_real) else np.nan,
            "NS Real": ns_real if pd.notna(ns_real) else np.nan,
            "% Capacidad": pct_capacidad,
            "Causa Raíz 1.0": causa_1,
            "minutos_req": min_req,
            "min_conectado": min_con,
            "min_disponible": min_disp,
            "min_pausas": min_pau
        })

    return pd.DataFrame(filas)


# ── CARGA Y CRUCE DE TURNOS PROGRAMADOS ───────────────────────────────────────
def obtener_turnos_programados_por_servicio(fecha_desde: str, fecha_hasta: str) -> pd.DataFrame:
    """
    Extrae turnos programados agrupados por servicio y fecha desde turnos_detallados.
    Calcula: total asesores programados, horas programadas y FTEs en malla.
    """
    with _get_db() as conn:
        query = """
            SELECT fecha, servicio, bp, horas_programadas, turno_ini, turno_fin
            FROM turnos_detallados
            WHERE fecha >= ? AND fecha <= ?
        """
        df_t = pd.read_sql(query, conn, params=[fecha_desde, fecha_hasta])

    if df_t.empty:
        return pd.DataFrame()

    df_t["servicio_norm"] = df_t["servicio"].astype(str).str.upper().str.strip()
    df_t["horas_programadas"] = pd.to_numeric(df_t["horas_programadas"], errors="coerce").fillna(0.0)
    return df_t


def calcular_metricas_turnos_para_sore(fecha_desde: str, fecha_hasta: str, srv_sore: str, df_t_all: pd.DataFrame) -> dict:
    """
    Calcula horas y FTEs programados en malla para un servicio SORE específico.
    """
    if df_t_all.empty:
        return {"asesores_malla": 0, "horas_prog_malla": 0.0, "fte_prog_malla": 0.0}

    raws = [r.upper().strip() for r in obtener_servicios_raw_para_sore(srv_sore)]
    sub = df_t_all[df_t_all["servicio_norm"].isin(raws)]
    if sub.empty:
        return {"asesores_malla": 0, "horas_prog_malla": 0.0, "fte_prog_malla": 0.0}

    dias_tot = max(1, sub["fecha"].nunique())
    h_tot = float(sub["horas_programadas"].sum())
    asesores_unicos = int(sub["bp"].nunique())
    fte_prog = round((h_tot / (dias_tot * 8.0)), 1)

    return {
        "asesores_malla": asesores_unicos,
        "horas_prog_malla": round(h_tot, 1),
        "fte_prog_malla": fte_prog
    }


def calcular_metricas_pausas_disciplina(fecha: str, srv_sore: str, df_pausas_all: pd.DataFrame = None) -> dict:
    """
    Calcula el % de adherencia a descansos y minutos de exceso para el servicio SORE.
    Permite recibir df_pausas_all precalculado para evitar 27 lecturas duplicadas en la BD.
    """
    try:
        if df_pausas_all is None:
            from adherencia_pausas_engine import calcular_adherencia_pausas_intradia
            df_p = calcular_adherencia_pausas_intradia(fecha, ambito="TODOS")
        else:
            df_p = df_pausas_all

        if df_p is None or df_p.empty:
            return {"pausas_prog": 0, "pausas_punt": 0, "pct_adh_pausas": 100.0, "min_exceso_pausas": 0}

        raws = [r.upper().strip() for r in obtener_servicios_raw_para_sore(srv_sore)]
        if "srv_norm" not in df_p.columns:
            df_p["srv_norm"] = df_p["Servicio"].astype(str).str.upper().str.strip()

        sub_p = df_p[df_p["srv_norm"].isin(raws)]
        if sub_p.empty:
            return {"pausas_prog": 0, "pausas_punt": 0, "pct_adh_pausas": 100.0, "min_exceso_pausas": 0}

        tot_p = len(sub_p)
        punt = int(sub_p["Estado"].astype(str).str.startswith("🟢").sum())
        pct_adh = round(punt / max(1, tot_p) * 100.0, 1)

        mins_exc = 0
        for val in sub_p["Exceso"]:
            if val and val != "--" and "+" in str(val):
                try:
                    mins_exc += int(str(val).replace("+", "").replace("min", "").strip())
                except Exception:
                    pass

        return {
            "pausas_prog": tot_p,
            "pausas_punt": punt,
            "pct_adh_pausas": pct_adh,
            "min_exceso_pausas": mins_exc
        }
    except Exception:
        return {"pausas_prog": 0, "pausas_punt": 0, "pct_adh_pausas": 100.0, "min_exceso_pausas": 0}


# ── DIAGNÓSTICO ENRIQUECIDO 2.0 (4 FACTORES) ──────────────────────────────────
def diagnosticar_causa_raiz_v2(
    gap_fte_malla: float,       # FTE Prog Malla - FTE Requerido (Subprogramación WFM)
    gap_fte_operacion: float,   # FTE Conectado - FTE Prog Malla (Fuga de Jornada)
    pct_capacidad: float,       # % Capacidad
    pct_adh_pausas: float,      # % Adherencia de Pausas
    min_exceso_pausas: int,     # Minutos totales de exceso en pausas
    aux_real: float,            # % Auxiliares
    aux_meta: float             # Meta Auxiliares (14%)
) -> tuple[str, str, str]:
    """
    Determina la causa raíz 2.0 con responsabilidad asignada objetiva:
    Retorna (badge_estado, causa_principal, explicacion_ejecutiva).
    """
    if pct_capacidad >= 95.0 and pct_adh_pausas >= 85.0 and min_exceso_pausas <= 60:
        return (
            "🟢 Capacidad y Disciplina Óptima",
            "Operación en Meta Integral",
            "Capacidad cubierta por encima del 95% con alta adherencia a turnos y pausas programadas."
        )

    if pct_capacidad >= 95.0:
        if min_exceso_pausas > 120 or pct_adh_pausas < 80.0:
            return (
                "🟡 Capacidad Cumplida con Riesgo Disciplinario",
                "Fuga / Descalce de Pausas Oculto",
                f"El servicio cumplió volumen global ({pct_capacidad:.1f}%), pero acumuló {min_exceso_pausas} min de exceso en pausas y adherencia de {pct_adh_pausas:.1f}%, arriesgando intervalos intradía."
            )
        return (
            "🟢 Capacidad Cumplida",
            "Operación Estable",
            f"Capacidad cubierta ({pct_capacidad:.1f}%) dentro de los parámetros de servicio esperados."
        )

    # Si hay déficit de capacidad (< 95%)
    causas = []
    responsable = []
    detalles = []

    # 1. ¿Subprogramación de Malla por WFM?
    if gap_fte_malla < -1.0:
        causas.append(f"Sub-programación WFM ({gap_fte_malla:+.1f} FTEs en malla)")
        responsable.append("WFM / Planeación")
        detalles.append(f"La malla programó {abs(gap_fte_malla):.1f} FTEs por debajo de lo que exigía el modelo Sore.")

    # 2. ¿Fuga de Jornada / Desconexión Temprana?
    if gap_fte_operacion < -1.0:
        causas.append(f"Fuga Jornada Operativa ({gap_fte_operacion:+.1f} FTEs no conectados)")
        responsable.append("Operaciones / Supervisión")
        detalles.append(f"Los asesores estaban en malla, pero no completaron su jornada (brecha de {abs(gap_fte_operacion):.1f} FTEs).")

    # 3. ¿Exceso de Pausas (> 14% o minutos severos de exceso)?
    if aux_real > (aux_meta + 2.0) or min_exceso_pausas > 180:
        causas.append(f"Exceso Pausas (+{min_exceso_pausas} min exceso, {aux_real:.1f}% aux)")
        responsable.append("Operaciones / Asesores")
        detalles.append(f"Fuga por descansos no autorizados o prolongados ({min_exceso_pausas} minutos de exceso neto).")

    # 4. ¿Descalce horario de descansos?
    if pct_adh_pausas < 75.0 and not (aux_real > aux_meta + 2.0):
        causas.append(f"Descalce Horario Pausas ({pct_adh_pausas:.1f}% puntualidad)")
        responsable.append("Supervisión / Piso")
        detalles.append("Pausas corridas fuera de franja programada que dejaron intervalos desprotegidos.")

    if not causas:
        return (
            "🟡 Desviación Leve Aceptable",
            "Dilución Operativa Menor",
            f"Capacidad en {pct_capacidad:.1f}% con pequeñas variaciones sin patrón crítico de fuga."
        )

    badge = "🔴 " + " • ".join(causas)
    resp_str = " & ".join(list(dict.fromkeys(responsable)))
    desc_str = " ".join(detalles)
    return (badge, resp_str, desc_str)


# ── MATRIZ EJECUTIVA ENRIQUECIDA 2.0 ──────────────────────────────────────────
@st.cache_data(ttl=900, show_spinner=False)
def calcular_ejecutiva_capacidad_v2(fecha_desde: str, fecha_hasta: str, srv_filtro: str = None) -> pd.DataFrame:
    """
    Genera la tabla panorámica de capacidad enriquecida con datos de malla y pausas.
    Optimizado en memoria: precarga adherencia de pausas 1 sola vez en lugar de iterar.
    """
    df_1 = calcular_ejecutiva_capacidad_base(fecha_desde, fecha_hasta, srv_filtro)
    if df_1.empty:
        return pd.DataFrame()

    df_turnos_all = obtener_turnos_programados_por_servicio(fecha_desde, fecha_hasta)

    # Precarga vectorizada de pausas para los 27 servicios (1 sola llamada)
    df_pausas_all = pd.DataFrame()
    try:
        from adherencia_pausas_engine import calcular_adherencia_pausas_intradia
        df_pausas_all = calcular_adherencia_pausas_intradia(fecha_hasta, ambito="TODOS")
        if not df_pausas_all.empty and "srv_norm" not in df_pausas_all.columns:
            df_pausas_all["srv_norm"] = df_pausas_all["Servicio"].astype(str).str.upper().str.strip()
    except Exception:
        df_pausas_all = pd.DataFrame()

    filas_v2 = []
    for _, r in df_1.iterrows():
        srv = r["Servicio"]
        m_t = calcular_metricas_turnos_para_sore(fecha_desde, fecha_hasta, srv, df_turnos_all)
        m_p = calcular_metricas_pausas_disciplina(fecha_hasta, srv, df_pausas_all=df_pausas_all)

        fte_req = float(r.get("FTE Requerido", 0.0))
        fte_con = float(r.get("FTE Conectado", 0.0))
        fte_disp = float(r.get("FTE Disponible", 0.0))
        pct_cap = float(r.get("% Capacidad", 0.0))
        aux_real = float(r.get("% Aux Real", 0.0))
        aux_meta = float(r.get("Meta Aux", 14.0))

        min_req = float(r.get("minutos_req", 0.0))
        min_con = float(r.get("min_conectado", 0.0))
        min_disp = float(r.get("min_disponible", 0.0))
        min_pau = float(r.get("min_pausas", 0.0))

        fte_prog = m_t["fte_prog_malla"]
        gap_malla = round(fte_prog - fte_req, 1)
        gap_oper = round(fte_con - fte_prog, 1)

        pct_cumpl_turno = round((fte_con / fte_prog * 100.0), 1) if fte_prog > 0 else 100.0

        badge_diag, resp, desc = diagnosticar_causa_raiz_v2(
            gap_fte_malla=gap_malla,
            gap_fte_operacion=gap_oper,
            pct_capacidad=pct_cap,
            pct_adh_pausas=m_p["pct_adh_pausas"],
            min_exceso_pausas=m_p["min_exceso_pausas"],
            aux_real=aux_real,
            aux_meta=aux_meta
        )

        filas_v2.append({
            "Servicio": srv,
            "Tipo": r.get("Tipo", "Inbound"),
            "FTE Requerido": fte_req,
            "FTE Malla (Prog)": fte_prog,
            "Brecha Malla (WFM)": gap_malla,
            "FTE Conectado": fte_con,
            "Brecha Operación": gap_oper,
            "% Cumpl Turno": pct_cumpl_turno,
            "FTE Disponible": fte_disp,
            "% Capacidad": pct_cap,
            "% Aux Real": aux_real,
            "% Adh Pausas": m_p["pct_adh_pausas"],
            "Exceso Pausas (min)": m_p["min_exceso_pausas"],
            "Diagnóstico 1.0 (Actual)": r.get("Causa Raíz 1.0", "--"),
            "Diagnóstico 2.0 (Enriquecido)": badge_diag,
            "Responsable 2.0": resp,
            "Veredicto 2.0": desc,
            "Tráfico Plan": r.get("Tráfico Plan", 0.0),
            "Tráfico Real": r.get("Tráfico Real", np.nan),
            "AHT Plan (s)": r.get("AHT Plan (s)", 0.0),
            "AHT Real (s)": r.get("AHT Real (s)", np.nan),
            "NS Real": r.get("NS Real", np.nan),
            "minutos_req": min_req,
            "min_conectado": min_con,
            "min_disponible": min_disp,
            "min_pausas": min_pau
        })

    return pd.DataFrame(filas_v2)


# ── CURVA INTRADÍA TRIPARTITA (48 INTERVALOS) ─────────────────────────────────
@st.cache_data(ttl=900, show_spinner=False)
def calcular_curva_intradia_v2(fecha_str: str, servicio_sel: str) -> pd.DataFrame:
    """
    Calcula los 48 intervalos uniendo:
      - Sore (FTE Requerido)
      - Turnos Detallados (FTE Programado en Malla)
      - Genesys Real (FTE Conectado y FTE Disponible)
    """
    df_fore_all = cargar_forecast_sore_completo()
    sub_f_int = df_fore_all[(df_fore_all["fecha"] == fecha_str) & (df_fore_all["servicio"] == servicio_sel)].copy()
    sub_r_int = calcular_capacidad_intervalos_real(fecha_str, servicio_sel)

    if sub_r_int.empty:
        sub_r_int = pd.DataFrame([
            {
                "intervalo": f"{h:02d}:{m:02d}",
                "min_conectado": 0.0,
                "min_disponible": 0.0,
                "min_pausas": 0.0,
                "fte_conectado": 0.0,
                "fte_disponible": 0.0,
            }
            for h in range(24) for m in (0, 30)
        ])

    merged = pd.merge(sub_f_int, sub_r_int, on="intervalo", how="outer").fillna(0.0)

    # Calcular FTE Programado por intervalo desde turnos_detallados
    raws = [r.upper().strip() for r in obtener_servicios_raw_para_sore(servicio_sel)]
    placeholders = ", ".join("?" for _ in raws)

    with _get_db() as conn:
        query_t = f"""
            SELECT turno_ini, turno_fin, horas_programadas
            FROM turnos_detallados
            WHERE fecha = ? AND UPPER(TRIM(servicio)) IN ({placeholders})
        """
        df_turnos = pd.read_sql(query_t, conn, params=[fecha_str, *raws])

    turnos_min = []
    for _, r in df_turnos.iterrows():
        ini = _time_to_minutes(r["turno_ini"])
        fin = _time_to_minutes(r["turno_fin"])
        if fin < ini:
            turnos_min.append((ini, 1440.0))
            turnos_min.append((0.0, fin))
        else:
            turnos_min.append((ini, fin))

    prog_list = []
    for i in range(48):
        t_min = i * 30.0
        int_str = f"{int(t_min // 60):02d}:{int(t_min % 60):02d}"
        cant = sum(1 for ini, fin in turnos_min if ini <= t_min < fin)
        prog_list.append({"intervalo": int_str, "fte_programado": cant})

    df_prog = pd.DataFrame(prog_list)
    merged = pd.merge(merged, df_prog, on="intervalo", how="left").fillna(0.0)

    # Cruzar con métricas GTR si existen
    df_gtr = obtener_metricas_servicio_intradia(fecha_str, servicio_sel)
    if not df_gtr.empty:
        merged = pd.merge(merged, df_gtr, on="intervalo", how="left")
    else:
        merged["trafico_real"] = np.nan
        merged["aht_real_seg"] = np.nan
        merged["ns_real"] = np.nan

    return merged.sort_values(by="intervalo")


# ── ÁRBOL DE CASCADA WATERFALL 2.0 ───────────────────────────────────────────
def generar_waterfall_capacidad_v2(row_data: dict | pd.Series, unidad: str = "Horas Equivalentes (h)") -> go.Figure:
    """
    Genera el gráfico Waterfall 2.0 de Atribución y Descomposición Tripartita.
    Discrimina:
      1. Requerido SORE (Demanda teórica Erlang)
      2. Brecha Malla WFM (Sub/Sobre programación)
      3. Brecha Operación (Fuga de jornada / Asistencia)
      4. Pausas en Norma (14% Meta oficial)
      5. Exceso en Pausas (>14% destruyendo capacidad)
      6. Capacidad Neta Lograda (Disponible Real)
    """
    es_horas = "Horas" in unidad

    if es_horas:
        base_req = round(float(row_data.get("minutos_req", 0.0)) / 60.0, 1)
        if base_req == 0.0:
            base_req = round(float(row_data.get("FTE Requerido", 0.0)) * 8.0, 1)

        delta_malla = round(float(row_data.get("Brecha Malla (WFM)", 0.0)) * 8.0, 1)
        delta_oper = round(float(row_data.get("Brecha Operación", 0.0)) * 8.0, 1)

        h_con = round(float(row_data.get("min_conectado", 0.0)) / 60.0, 1)
        if h_con == 0.0:
            h_con = round(float(row_data.get("FTE Conectado", 0.0)) * 8.0, 1)

        h_pau_total = round(float(row_data.get("min_pausas", 0.0)) / 60.0, 1)
        h_pau_meta = round(h_con * (META_AUXILIARES_OFICIAL / 100.0), 1)
        h_pau_exceso = round(max(0.0, h_pau_total - h_pau_meta), 1)

        h_disp = round(float(row_data.get("min_disponible", 0.0)) / 60.0, 1)
        if h_disp == 0.0:
            h_disp = round(float(row_data.get("FTE Disponible", 0.0)) * 8.0, 1)

        valores = [base_req, delta_malla, delta_oper, -h_pau_meta, -h_pau_exceso, h_disp]
        sufijo = " h"
    else:
        base_req = round(float(row_data.get("FTE Requerido", 0.0)), 1)
        delta_malla = round(float(row_data.get("Brecha Malla (WFM)", 0.0)), 1)
        delta_oper = round(float(row_data.get("Brecha Operación", 0.0)), 1)

        fte_con = round(float(row_data.get("FTE Conectado", 0.0)), 1)
        fte_pau_meta = round(fte_con * (META_AUXILIARES_OFICIAL / 100.0), 1)
        fte_disp = round(float(row_data.get("FTE Disponible", 0.0)), 1)
        fte_pau_tot = round(max(0.0, fte_con - fte_disp), 1)
        fte_pau_exceso = round(max(0.0, fte_pau_tot - fte_pau_meta), 1)

        valores = [base_req, delta_malla, delta_oper, -fte_pau_meta, -fte_pau_exceso, fte_disp]
        sufijo = " FTE"

    x_labels = [
        "1. Req SORE",
        "2. Δ Malla WFM",
        "3. Δ Jornada Oper",
        "4. Pausas (14%)",
        "5. Exceso Pausas",
        "6. Cap. Lograda"
    ]
    measure = ["absolute", "relative", "relative", "relative", "relative", "total"]

    text_labels = []
    for i, (v, m) in enumerate(zip(valores, measure)):
        if m == "total" or i == 0:
            text_labels.append(f"{v:,.1f}{sufijo}")
        else:
            text_labels.append(f"{v:+,.1f}{sufijo}")

    fig = go.Figure(go.Waterfall(
        name="Cascada 2.0",
        orientation="v",
        measure=measure,
        x=x_labels,
        textposition="outside",
        text=text_labels,
        y=valores,
        connector={"line": {"color": "#64748b", "width": 1.5, "dash": "dot"}},
        increasing={"marker": {"color": "#10b981"}},
        decreasing={"marker": {"color": "#ef4444"}},
        totals={"marker": {"color": "#6366f1"}}
    ))

    srv_name = row_data.get("Servicio", "")
    fig.update_layout(
        title=dict(
            text=f"🌳 Árbol de Cascada y Atribución Tripartita 2.0 — {srv_name} ({unidad})",
            font=dict(color="#f8fafc", size=14)
        ),
        showlegend=False,
        height=380,
        margin=dict(l=10, r=10, t=50, b=20),
        xaxis=dict(tickangle=0, tickfont=dict(size=12, color="#cbd5e1"), gridcolor="#334155"),
        yaxis=dict(title=dict(text=f"Volumen ({unidad})", font=dict(color="#94a3b8")), tickfont=dict(color="#cbd5e1"), gridcolor="#1e293b"),
        plot_bgcolor="rgba(15, 23, 42, 0.4)",
        paper_bgcolor="rgba(0,0,0,0)"
    )
    return fig




# ── RENDERIZADO PRINCIPAL UI (LABORATORIO 2.0) ────────────────────────────────
def render_tab_capacidad_v2(agentes_map: dict):
    """
    Renderiza el laboratorio interactivo WFM 2.0 exclusivo para Carlos Murillo.
    """
    st.markdown(
        """
        <div style="background: linear-gradient(90deg, #1e1b4b 0%, #0f172a 100%); padding: 18px 22px; border-radius: 12px; margin-bottom: 18px; border-left: 6px solid #818cf8;">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 12px;">
                <div>
                    <h3 style="color: #ffffff; margin: 0 0 4px 0; font-size: 21px;">🧪 Laboratorio WFM 2.0: Capacidad & Disciplina Operativa</h3>
                    <p style="color: #cbd5e1; margin: 0; font-size: 13px;">
                        Contraste Tripartito: <b>Forecast Sore</b> (WFM) vs <b>Malla Contractual</b> (Turnos & Pausas) vs <b>Genesys Real</b>
                    </p>
                </div>
                <div style="text-align: right; background: #312e81; padding: 6px 14px; border-radius: 8px; border: 1px solid #6366f1;">
                    <span style="color: #a5b4fc; font-size: 11px; font-weight: 800; text-transform: uppercase;">Entorno Exclusivo</span><br>
                    <span style="color: #ffffff; font-size: 12px; font-weight: 600;">Carlos Murillo • Sandbox</span>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    df_fore_all = cargar_forecast_sore_completo()
    if df_fore_all.empty:
        st.error("⚠️ No se encontraron los archivos base del requerido Sore en la raíz del proyecto.")
        return

    fechas_disp = sorted(df_fore_all["fecha"].unique().tolist())
    fecha_default_idx = len(fechas_disp) - 1
    real_db_path = Path(DB_PATH)
    if real_db_path.exists():
        try:
            conn = sqlite3.connect(real_db_path)
            c = conn.cursor()
            c.execute("SELECT MAX(fecha) FROM segments WHERE fecha IS NOT NULL AND fecha >= '2026-09-01'")
            max_f = c.fetchone()[0]
            conn.close()
            if max_f and max_f in fechas_disp:
                fecha_default_idx = fechas_disp.index(max_f)
        except Exception:
            pass

    # Controles superiores
    col_c1, col_c2, col_c3 = st.columns([1.5, 2.5, 3.5])
    with col_c1:
        tipo_tempo = st.radio("Temporalidad:", ["📅 Día Único", "📊 Rango Histórico"], horizontal=True, key="lab_tempo")
    with col_c2:
        if tipo_tempo == "📅 Día Único":
            fecha_sel = st.selectbox("Fecha de Análisis:", options=fechas_disp, index=fecha_default_idx, key="lab_f_sel")
            fecha_desde, fecha_hasta = fecha_sel, fecha_sel
        else:
            sub_c1, sub_c2 = st.columns(2)
            with sub_c1:
                fecha_desde = st.selectbox("Desde:", options=fechas_disp, index=max(0, fecha_default_idx - 6), key="lab_f_d")
            with sub_c2:
                fecha_hasta = st.selectbox("Hasta:", options=fechas_disp, index=fecha_default_idx, key="lab_f_h")
            if fecha_desde > fecha_hasta:
                fecha_desde, fecha_hasta = fecha_hasta, fecha_desde

    servicios_disp = ["Todos los Servicios"] + sorted(df_fore_all["servicio"].unique().tolist())
    with col_c3:
        srv_filtro = st.selectbox("Servicio / Stream:", options=servicios_disp, index=0, key="lab_srv_filtro")
        srv_param = None if srv_filtro == "Todos los Servicios" else srv_filtro

    # Sub-pestañas principales
    subtab1, subtab2 = st.tabs([
        "🔬 Diagnóstico Integral 2.0 (Vista Enriquecida)",
        "⚖️ Comparador Lado a Lado (Modelo 1.0 vs 2.0)"
    ])

    # ── PESTAÑA 1: VISTA ENRIQUECIDA 2.0 ─────────────────────────────────────
    with subtab1:
        with st.spinner("Procesando cruce tripartito de capacidad, turnos y pausas..."):
            df_v2 = calcular_ejecutiva_capacidad_v2(fecha_desde, fecha_hasta, srv_param)

        if df_v2.empty:
            st.info("Sin registros para los filtros seleccionados.")
            return

        # Métricas Consolidadas Superiores
        tot_fte_req = df_v2["FTE Requerido"].sum()
        tot_fte_prog = df_v2["FTE Malla (Prog)"].sum()
        tot_fte_con = df_v2["FTE Conectado"].sum()
        tot_fte_disp = df_v2["FTE Disponible"].sum()
        tot_exc_min = int(df_v2["Exceso Pausas (min)"].sum())
        pct_cap_global = (tot_fte_disp / tot_fte_req * 100.0) if tot_fte_req > 0 else 100.0
        pct_cumpl_turno_global = (tot_fte_con / tot_fte_prog * 100.0) if tot_fte_prog > 0 else 100.0
        pct_adh_p_global = df_v2["% Adh Pausas"].mean()

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric(
            "FTE Requerido (Sore)",
            f"{tot_fte_req:.1f}",
            help="Personal equivalente exigido por el modelo Erlang"
        )
        m2.metric(
            "FTE en Malla (Turnos)",
            f"{tot_fte_prog:.1f}",
            delta=f"{tot_fte_prog - tot_fte_req:+.1f} vs Req",
            help="Horas programadas en malla divididas por 8h"
        )
        m3.metric(
            "FTE Conectado Real",
            f"{tot_fte_con:.1f}",
            delta=f"{tot_fte_con - tot_fte_prog:+.1f} vs Malla",
            delta_color="normal",
            help="Presencia real registrada en Genesys"
        )
        m4.metric(
            "% Cumpl. Jornada",
            f"{pct_cumpl_turno_global:.1f}%",
            delta=f"{pct_cumpl_turno_global - 100.0:+.1f}%",
            help="Horas reales conectadas vs horas de turno contratadas"
        )
        m5.metric(
            "Capacidad Neta (2.0)",
            f"{pct_cap_global:.1f}%",
            delta=f"{tot_exc_min} min exc pausas",
            delta_color="inverse" if tot_exc_min > 60 else "normal",
            help="Porcentaje de capacidad efectiva disponible tras pausas"
        )

        st.write("")

        # Tabla Panorámica 2.0
        st.markdown("##### 📋 Matriz de Servicios: Capacidad, Cobertura de Malla & Disciplina")
        st.caption("Contrasta la exigencia teórica de WFM frente a lo que se programó en la malla y lo que los asesores realmente cumplieron en jornada y descansos.")

        cols_mostrar_v2 = [
            "Servicio", "Tipo", "FTE Requerido", "FTE Malla (Prog)", "Brecha Malla (WFM)",
            "FTE Conectado", "Brecha Operación", "% Cumpl Turno", "FTE Disponible",
            "% Capacidad", "% Adh Pausas", "Exceso Pausas (min)", "Diagnóstico 2.0 (Enriquecido)"
        ]
        df_show_v2 = df_v2[[c for c in cols_mostrar_v2 if c in df_v2.columns]].copy()

        cfg_cols = {
            "% Capacidad": st.column_config.ProgressColumn("% Capacidad", min_value=0, max_value=120, format="%.1f%%"),
            "% Cumpl Turno": st.column_config.ProgressColumn("% Turno", min_value=0, max_value=120, format="%.1f%%"),
            "% Adh Pausas": st.column_config.NumberColumn("% Adh Pausas", format="%.1f%%"),
            "Brecha Malla (WFM)": st.column_config.NumberColumn("Δ Malla", format="%+.1f FTE"),
            "Brecha Operación": st.column_config.NumberColumn("Δ Operación", format="%+.1f FTE"),
            "Exceso Pausas (min)": st.column_config.NumberColumn("Exceso Pausas", format="+%d min")
        }

        st.dataframe(df_show_v2, use_container_width=True, hide_index=True, column_config=cfg_cols)

        # ── CURVA INTRADÍA TRIPARTITA ─────────────────────────────────────────
        st.write("")
        st.markdown("---")
        st.markdown("#### 📈 Curva Intradía Tripartita de Cobertura (48 Intervalos de 30 min)")
        st.caption("Visualiza simultáneamente: **Requerido WFM (Sore)** vs **Malla Programada (Turnos)** vs **Conexión Real** vs **FTE Disponible Efectivo**.")

        srvs_intradia = sorted(df_v2["Servicio"].unique().tolist())
        c_int1, c_int2 = st.columns([3, 1])
        with c_int1:
            srv_grafica = st.selectbox("Seleccionar Servicio para Curva Intradía:", options=srvs_intradia, index=0, key="lab_srv_graf")
        with c_int2:
            st.metric("Fecha Curva", fecha_hasta)

        df_curva = calcular_curva_intradia_v2(fecha_hasta, srv_grafica)

        if not df_curva.empty:
            fig_tri = go.Figure()

            # 1. FTE Requerido (Azul punteado)
            fig_tri.add_trace(go.Scatter(
                x=df_curva["intervalo"],
                y=df_curva["asesores_req"],
                name="1. FTE Requerido (SORE)",
                mode="lines",
                line=dict(color="#3b82f6", width=2.5, dash="dash")
            ))

            # 2. FTE Programado Malla (Morado)
            fig_tri.add_trace(go.Scatter(
                x=df_curva["intervalo"],
                y=df_curva["fte_programado"],
                name="2. FTE en Malla (Turnos)",
                mode="lines+markers",
                marker=dict(size=4),
                line=dict(color="#a855f7", width=2.5)
            ))

            # 3. FTE Conectado Real (Verde)
            fig_tri.add_trace(go.Scatter(
                x=df_curva["intervalo"],
                y=df_curva["fte_conectado"],
                name="3. FTE Conectado Real (Genesys)",
                mode="lines",
                line=dict(color="#10b981", width=2)
            ))

            # 4. FTE Disponible Real (Área verde translúcida)
            fig_tri.add_trace(go.Scatter(
                x=df_curva["intervalo"],
                y=df_curva["fte_disponible"],
                name="4. FTE Disponible (Sin Pausas)",
                mode="lines",
                fill="tozeroy",
                fillcolor="rgba(16, 185, 129, 0.12)",
                line=dict(color="#059669", width=1.5)
            ))

            fig_tri.update_layout(
                title=f"Curva Intradía: {srv_grafica} — {fecha_hasta}",
                xaxis_title="Intervalo (30 min)",
                yaxis_title="FTEs Equivalentes",
                height=420,
                margin=dict(l=10, r=10, t=40, b=10),
                legend=dict(
                    orientation="h",
                    yanchor="bottom",
                    y=1.02,
                    xanchor="center",
                    x=0.5
                ),
                hovermode="x unified"
            )
            st.plotly_chart(fig_tri, use_container_width=True, key="lab_fig_curva_tri")

            st.caption("💡 **Lectura Operativa del Gráfico:**")
            st.caption("• **Distancia entre Morado (Malla) y Azul (Requerido):** ¿WFM programó suficientes personas para la demanda?")
            st.caption("• **Distancia entre Verde (Conectado) y Morado (Malla):** ¿La operación cumplió con la asistencia y puntualidad de entrada/salida?")
            st.caption("• **Distancia entre Verde Sólido y Verde Área (Pausas):** ¿El volumen de asesores en descanso en ese intervalo respetó el dimensionamiento?")

            # ── ÁRBOL DE CASCADA TRIPARTITO (WATERFALL 2.0) ───────────────
            st.write("")
            st.markdown("---")
            c_wat_t, c_wat_u = st.columns([3, 1])
            with c_wat_t:
                st.markdown("#### 🌳 Cascada de Atribución Tripartita de Capacidad (Waterfall 2.0)")
                st.caption("Explica con exactitud matemática de dónde proviene la brecha final: **Programación WFM** vs **Cumplimiento de Jornada** vs **Pausas Autorizadas** vs **Excesos Destructores de Capacidad**.")
            with c_wat_u:
                unidad_wat = st.selectbox(
                    "Unidad de Desglose:",
                    ["Horas Equivalentes (h)", "FTEs (Personas)"],
                    index=0,
                    key="wat_unit_v2_sub1"
                )

            sub_srv_sel = df_v2[df_v2["Servicio"] == srv_grafica]
            if not sub_srv_sel.empty:
                fig_wat = generar_waterfall_capacidad_v2(sub_srv_sel.iloc[0], unidad=unidad_wat)
                st.plotly_chart(fig_wat, use_container_width=True, key="lab_fig_wat_sub1")

    # ── PESTAÑA 2: COMPARADOR LADO A LADO (1.0 vs 2.0) ───────────────────────
    with subtab2:
        st.markdown("#### ⚖️ Comparativa Directa: Diagnóstico Clásico (1.0) vs Diagnóstico Enriquecido (2.0)")
        st.caption("Selecciona cualquier servicio y observa cómo cambia la causa raíz y la atribución de responsabilidades cuando se consideran los turnos contratados y la puntualidad de pausas.")

        srvs_comp = sorted(df_v2["Servicio"].unique().tolist())
        srv_c_sel = st.selectbox("Seleccionar Servicio a Comparar:", options=srvs_comp, index=0, key="comp_srv_sel")

        row_sel = df_v2[df_v2["Servicio"] == srv_c_sel].iloc[0]

        st.write("")
        c_mod1, c_mod2 = st.columns(2)

        with c_mod1:
            st.markdown(
                f"""
                <div style="background: #1e293b; padding: 16px 20px; border-radius: 12px; border: 1px solid #475569; min-height: 280px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                        <h4 style="color: #94a3b8; margin: 0; font-size: 16px;">🧭 Modelo Actual (WFM 1.0)</h4>
                        <span style="background: #334155; color: #cbd5e1; font-size: 11px; padding: 3px 8px; border-radius: 6px;">Macro / Genérico</span>
                    </div>
                    <div style="background: #0f172a; padding: 10px 14px; border-radius: 8px; margin-bottom: 12px;">
                        <span style="color: #64748b; font-size: 11px; text-transform: uppercase;">Diagnóstico Emitido:</span><br>
                        <b style="color: #f8fafc; font-size: 14px;">{row_sel['Diagnóstico 1.0 (Actual)']}</b>
                    </div>
                    <ul style="color: #cbd5e1; font-size: 12px; line-height: 1.8; margin: 0; padding-left: 18px;">
                        <li><b>Requerido Sore:</b> {row_sel['FTE Requerido']:.1f} FTEs</li>
                        <li><b>Conectado Real:</b> {row_sel['FTE Conectado']:.1f} FTEs (Brecha neta: {row_sel['FTE Conectado'] - row_sel['FTE Requerido']:+.1f} FTEs)</li>
                        <li><b>Evaluación de Pausas:</b> {row_sel['% Aux Real']:.1f}% vs 14% meta plano</li>
                        <li><b>Punto Ciego:</b> No sabe cuántos asesores estaban en malla ni si cumplieron sus 8 horas contratadas.</li>
                    </ul>
                </div>
                """,
                unsafe_allow_html=True
            )

        with c_mod2:
            st.markdown(
                f"""
                <div style="background: #1e1b4b; padding: 16px 20px; border-radius: 12px; border: 1px solid #6366f1; min-height: 280px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                        <h4 style="color: #a5b4fc; margin: 0; font-size: 16px;">🧪 Modelo Enriquecido (WFM 2.0)</h4>
                        <span style="background: #4338ca; color: #ffffff; font-size: 11px; padding: 3px 8px; border-radius: 6px; font-weight: 700;">Discriminado 360°</span>
                    </div>
                    <div style="background: #0f172a; padding: 10px 14px; border-radius: 8px; margin-bottom: 12px;">
                        <span style="color: #818cf8; font-size: 11px; text-transform: uppercase;">Diagnóstico de Precisión:</span><br>
                        <b style="color: #f8fafc; font-size: 14px;">{row_sel['Diagnóstico 2.0 (Enriquecido)']}</b>
                    </div>
                    <ul style="color: #e2e8f0; font-size: 12px; line-height: 1.8; margin: 0; padding-left: 18px;">
                        <li><b>Malla Planificada:</b> {row_sel['FTE Malla (Prog)']:.1f} FTEs (Brecha WFM: {row_sel['Brecha Malla (WFM)']:+.1f} FTEs)</li>
                        <li><b>Cumplimiento de Jornada:</b> {row_sel['% Cumpl Turno']:.1f}% (Brecha Operación: {row_sel['Brecha Operación']:+.1f} FTEs)</li>
                        <li><b>Disciplina de Descansos:</b> {row_sel['% Adh Pausas']:.1f}% puntuales • {row_sel['Exceso Pausas (min)']} min de exceso</li>
                        <li><b>Responsable Asignado:</b> <span style="color: #38bdf8; font-weight: 700;">{row_sel['Responsable 2.0']}</span></li>
                    </ul>
                </div>
                """,
                unsafe_allow_html=True
            )

        st.write("")
        # Veredicto Inteligente de Diferencia
        st.markdown(
            f"""
            <div style="background: linear-gradient(90deg, #0f172a 0%, #1e293b 100%); border-left: 5px solid #38bdf8; padding: 14px 18px; border-radius: 10px; margin-top: 10px;">
                <h5 style="color: #38bdf8; margin: 0 0 6px 0; font-size: 14px;">💡 Veredicto y Hallazgo Oculto Detectado:</h5>
                <p style="color: #e2e8f0; font-size: 13px; line-height: 1.6; margin: 0;">
                    {row_sel['Veredicto 2.0']}
                </p>
            </div>
            """,
            unsafe_allow_html=True
        )

        # Cascada Waterfall en el Comparador
        st.write("")
        c_wat_t2, c_wat_u2 = st.columns([3, 1])
        with c_wat_t2:
            st.markdown(f"##### 🌳 Descomposición Matemática en Cascada — {srv_c_sel}")
            st.caption("Visualiza exactamente cómo se desglosa el volumen y qué parte de la pérdida corresponde a WFM, a Operaciones o a Pausas.")
        with c_wat_u2:
            unidad_wat2 = st.selectbox(
                "Unidad Cascada:",
                ["Horas Equivalentes (h)", "FTEs (Personas)"],
                index=0,
                key="wat_unit_v2_sub2"
            )

        fig_wat_comp = generar_waterfall_capacidad_v2(row_sel, unidad=unidad_wat2)
        st.plotly_chart(fig_wat_comp, use_container_width=True, key="lab_fig_wat_sub2")
