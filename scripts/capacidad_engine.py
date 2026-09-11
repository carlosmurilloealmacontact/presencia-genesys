"""
Motor de Diagnóstico y Capacidad Operativa (WFM SORE vs. Genesys Real).

Diseñado con base estricta en el debate gerencial de operaciones y planificación:
- Matriz Ejecutiva Panorámica: Evalúa todos los servicios de un vistazo según las 4 palancas
  (Tráfico, AHT Meta Plana, % Auxiliares vs Meta 14%, y Personas Requeridas vs Conectadas).
- Diagnóstico de Causa Raíz / Atribución de Incumplimiento.
- Vista Detallada por Franja Horaria (Lógica 30 min * Personas = Minutos Requeridos vs Disponibles).
"""

import os
import pickle
import sqlite3
from datetime import datetime, date, timedelta
from pathlib import Path
import openpyxl
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from config import DB_PATH

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FILE_FORECAST_IN = os.path.join(BASE_DIR, "../09. Intraday Forecast IN Septiembre - Latam.xlsx")
FILE_FORECAST_BO = os.path.join(BASE_DIR, "../09. Intraday Forecast BO Septiembre - Latam.xlsx")

FILE_FORECAST_DAILY = os.path.join(BASE_DIR, "../09. Daily Forecast Sept - Latam.xlsx")
CACHE_PKL_PATH = os.path.join(BASE_DIR, "../data/forecast_cache.pkl")

META_AUXILIARES_OFICIAL = 14.0  # Meta estándar de auxiliares acordada en la conversación (14%)

# Catálogo oficial maestro: nombres de servicio en mayúsculas idénticos a segments en Genesys DB
CATALOGO_SERVICIOS_SORE = {
    # Línea / Inbound Voz
    "LUA AMC": {"tipo": "Línea / Inbound Voz", "origen": "IN", "sheet": "LUA AMC", "meta_aht": 860.0, "meta_ns": 75.0},
    "DT FFP AMC": {"tipo": "Línea / Inbound Voz", "origen": "IN", "sheet": "DREAM TEAMS VOZ", "meta_aht": 646.0, "meta_ns": 85.0},
    "DT FFP AMC ING": {"tipo": "Línea / Inbound Voz", "origen": "IN", "sheet": "DREAM TEAMS ENG", "meta_aht": 582.0, "meta_ns": 85.0},
    "LUA AMC ING": {"tipo": "Línea / Inbound Voz", "origen": "IN", "sheet": "LUA AMC ING", "meta_aht": 900.0, "meta_ns": 75.0},
    "VENTAS AMC": {"tipo": "Línea / Inbound Voz", "origen": "IN", "sheet": "Ventas AMC", "meta_aht": 780.0, "meta_ns": 75.0},
    "EQUIPAJES AMC": {"tipo": "Línea / Inbound Voz", "origen": "IN", "sheet": "Equipajes AMC", "meta_aht": 500.0, "meta_ns": 75.0},
    "EQUIPAJES AMC ING": {"tipo": "Línea / Inbound Voz", "origen": "IN", "sheet": "Equipajes AMC ING", "meta_aht": 491.0, "meta_ns": 75.0},
    "HVC AMC": {"tipo": "Línea / Inbound Voz", "origen": "IN", "sheet": "HVC AMC", "meta_aht": 709.0, "meta_ns": 80.0},
    "SOPORTE LUA AMC": {"tipo": "Línea / Inbound Voz", "origen": "IN", "sheet": "Soporte LUA AMC", "meta_aht": 311.0, "meta_ns": 75.0},
    "CORPORATE PYME": {"tipo": "Línea / Inbound Voz", "origen": "IN", "sheet": "CORPORATE PYME", "meta_aht": 817.0, "meta_ns": 80.0},
    "AGENCIAS TARGET ES": {"tipo": "Línea / Inbound Voz", "origen": "IN", "sheet": "AGENCIAS TARGET ES", "meta_aht": 880.0, "meta_ns": 80.0},

    # Canales Digitales (Chat, WhatsApp, Redes)
    "WPP LUA AMC": {"tipo": "Canales Digitales", "origen": "IN", "sheet": "WPP LUA AMC", "meta_aht": 1600.0, "meta_ns": 80.0},
    "WPP VENTAS AMC": {"tipo": "Canales Digitales", "origen": "IN", "sheet": "WPP Ventas AMC", "meta_aht": 1700.0, "meta_ns": 80.0},
    "CHAT VENTAS AMC": {"tipo": "Canales Digitales", "origen": "IN", "sheet": "CHAT Ventas AMC", "meta_aht": 1412.0, "meta_ns": 80.0},
    "WPP EQUIPAJES AMC": {"tipo": "Canales Digitales", "origen": "IN", "sheet": "WPP EQUIPAJES AMC", "meta_aht": 1231.0, "meta_ns": 80.0},
    "RRSS AMC": {"tipo": "Canales Digitales", "origen": "IN", "sheet": "RRSS AMC", "meta_aht": 1200.0, "meta_ns": 80.0},
    "RRSS AMC ING": {"tipo": "Canales Digitales", "origen": "IN", "sheet": "RRSS AMC ING", "meta_aht": 1200.0, "meta_ns": 80.0},
    "RRSS PORT AMC": {"tipo": "Canales Digitales", "origen": "IN", "sheet": "RRSS PORT AMC", "meta_aht": 1200.0, "meta_ns": 80.0},
    "AG CORPORATE CHAT": {"tipo": "Canales Digitales", "origen": "IN", "sheet": "AGENCIAS CHAT CORPORATE", "meta_aht": 1859.0, "meta_ns": 80.0},
    "CHAT AGENCIAS ESP": {"tipo": "Canales Digitales", "origen": "IN", "sheet": "CHAT AGENCIAS ESP", "meta_aht": 1223.0, "meta_ns": 80.0},
    "CHAT DT FFP AMC ESP": {"tipo": "Canales Digitales", "origen": "IN", "sheet": "CHAT DREAM TEAMS ES", "meta_aht": 1103.0, "meta_ns": 80.0},
    "DREAM TEAM WP": {"tipo": "Canales Digitales", "origen": "IN", "sheet": "DREAM TEAMS WA", "meta_aht": 1272.0, "meta_ns": 80.0},

    # Back Office
    "BO LUA AMC": {"tipo": "Back Office", "origen": "BO", "sheet": "BO LUA AMC", "meta_aht": 1006.0, "meta_ns": 85.0},
    "BO EQUIPAJES AMC": {"tipo": "Back Office", "origen": "BO", "sheet": "EQUIPAJE BO", "meta_aht": 1715.0, "meta_ns": 85.0},
    "BO_CORPORATE": {"tipo": "Back Office", "origen": "BO", "sheet": "BO_CORPORATE", "meta_aht": 735.0, "meta_ns": 85.0},
    "BO AGENCIAS TARGET": {"tipo": "Back Office", "origen": "BO", "sheet": "BO AGENCIAS TARGET", "meta_aht": 735.0, "meta_ns": 85.0},
    "LATAM TRAVEL AMC": {"tipo": "Back Office", "origen": "BO", "sheet": "Latam Travel AMC", "meta_aht": 735.0, "meta_ns": 85.0},
    "BO RECLAMOS AMC": {"tipo": "Back Office", "origen": "BO", "sheet": "BO RECLAMOS AMC", "meta_aht": 2375.0, "meta_ns": 85.0},
    "BO AG LTRADE": {"tipo": "Back Office", "origen": "BO", "sheet": "BO AG LTRADE", "meta_aht": 1200.0, "meta_ns": 85.0},
    "DREAM TEAM CASOS": {"tipo": "Back Office", "origen": "BO", "sheet": "DREAM TEAM CASOS", "meta_aht": 1150.0, "meta_ns": 85.0},
}


def parsear_archivos_sore_crudos() -> pd.DataFrame:
    """Parsea los libros de Excel de SORE (Inbound y Back Office) a un DataFrame unificado."""
    records = []
    archivos = {"IN": FILE_FORECAST_IN, "BO": FILE_FORECAST_BO}

    for origen, fpath in archivos.items():
        if not os.path.exists(fpath):
            continue

        wb = openpyxl.load_workbook(fpath, data_only=True)
        for srv_nombre, info in CATALOGO_SERVICIOS_SORE.items():
            if info["origen"] != origen:
                continue

            sheet_target = info["sheet"]
            if sheet_target not in wb.sheetnames:
                continue

            ws = wb[sheet_target]
            for r in range(5, ws.max_row + 1):
                d_val = ws.cell(r, 4).value
                if not d_val:
                    continue

                f_str = d_val.strftime("%Y-%m-%d") if isinstance(d_val, datetime) else str(d_val)[:10]

                int_val = ws.cell(r, 5).value
                if isinstance(int_val, datetime) or hasattr(int_val, "strftime"):
                    int_str = int_val.strftime("%H:%M")
                else:
                    int_str = str(int_val).strip()[:5]

                traffic = ws.cell(r, 6).value or 0.0
                aht_plan = ws.cell(r, 7).value or 0.0
                asesores = ws.cell(r, 8).value or 0.0

                try:
                    traffic = float(traffic)
                except Exception:
                    traffic = 0.0
                try:
                    aht_plan = float(aht_plan)
                except Exception:
                    aht_plan = 0.0
                try:
                    asesores = float(asesores)
                except Exception:
                    asesores = 0.0

                records.append({
                    "servicio": srv_nombre,
                    "tipo_mundo": info["tipo"],
                    "origen": origen,
                    "fecha": f_str,
                    "intervalo": int_str,
                    "traffic_forecast": traffic,
                    "aht_forecast": aht_plan,
                    "asesores_req": asesores,
                    "minutos_req": asesores * 30.0,
                    "meta_aht_plana": info["meta_aht"],
                    "meta_ns": info["meta_ns"],
                })
        wb.close()

    df_out = pd.DataFrame(records)
    return df_out


@st.cache_data(ttl=3600, show_spinner=False)
def cargar_forecast_sore_completo(forzar_recarga: bool = False) -> pd.DataFrame:
    """
    Carga el forecast unificado desde caché ultrarrápido (pickle ~0.01s).
    Si el caché no existe o los archivos de Excel fueron modificados, lo reconstruye.
    """
    if not forzar_recarga and os.path.exists(CACHE_PKL_PATH) and os.path.getsize(CACHE_PKL_PATH) > 1000:
        cache_mtime = os.path.getmtime(CACHE_PKL_PATH)
        archivos_modificados = any(
            os.path.exists(fp) and os.path.getmtime(fp) > cache_mtime
            for fp in [FILE_FORECAST_IN, FILE_FORECAST_BO]
        )
        if not archivos_modificados:
            try:
                with open(CACHE_PKL_PATH, "rb") as f:
                    data = pickle.load(f)
                    return data
            except Exception:
                pass

    df_nuevo = parsear_archivos_sore_crudos()
    if not df_nuevo.empty:
        try:
            os.makedirs(os.path.dirname(CACHE_PKL_PATH), exist_ok=True)
            tmp_cache = CACHE_PKL_PATH + ".tmp"
            with open(tmp_cache, "wb") as f:
                pickle.dump(df_nuevo, f, protocol=pickle.HIGHEST_PROTOCOL)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_cache, CACHE_PKL_PATH)
        except Exception:
            pass
    return df_nuevo


def cargar_presencia_resumen_dia(fecha_str: str) -> pd.DataFrame:
    """
    Agrupa los minutos de presencia real de Genesys para una fecha específica.
    Reconoce de forma inteligente los estados productivos según el tipo de servicio:
    - Inbound / Voz: Available y On Queue son productivos.
    - Back Office: Casos Backoffice, Available y On Queue son productivos.
    """
    real_db_path = Path(__file__).parent / DB_PATH
    if not os.path.exists(real_db_path):
        return pd.DataFrame()

    conn = sqlite3.connect(real_db_path)
    query = """
        SELECT 
            UPPER(TRIM(servicio)) as servicio,
            COUNT(DISTINCT agente_id) as asesores_conectados,
            SUM(duracion_min) as min_total,
            SUM(CASE WHEN presence_label NOT IN ('Offline') THEN duracion_min ELSE 0 END) as min_conectado,
            SUM(CASE 
                WHEN UPPER(TRIM(servicio)) LIKE '%BO%' OR UPPER(TRIM(servicio)) LIKE '%BACKOFFICE%' THEN
                    CASE WHEN presence_label IN ('Available', 'On Queue', 'Casos Backoffice') THEN duracion_min ELSE 0 END
                ELSE
                    CASE WHEN presence_label IN ('Available', 'On Queue') THEN duracion_min ELSE 0 END
            END) as min_disponible,
            SUM(CASE 
                WHEN UPPER(TRIM(servicio)) LIKE '%BO%' OR UPPER(TRIM(servicio)) LIKE '%BACKOFFICE%' THEN
                    CASE WHEN presence_label NOT IN ('Offline', 'Available', 'On Queue', 'Casos Backoffice') THEN duracion_min ELSE 0 END
                ELSE
                    CASE WHEN presence_label NOT IN ('Offline', 'Available', 'On Queue') THEN duracion_min ELSE 0 END
            END) as min_pausas
        FROM segments
        WHERE fecha = ? AND servicio IS NOT NULL AND servicio != ''
        GROUP BY UPPER(TRIM(servicio))
    """
    df_pres = pd.read_sql(query, conn, params=(fecha_str,))
    conn.close()

    if not df_pres.empty:
        df_pres["pct_auxiliares_real"] = df_pres.apply(
            lambda r: (r["min_pausas"] / r["min_conectado"] * 100.0) if r["min_conectado"] > 0 else 0.0, axis=1
        ).round(1)
        # FTEs equivalentes en base a jornada estándar de 8 horas (480 minutos)
        df_pres["fte_reales_conectados"] = (df_pres["min_conectado"] / 480.0).round(1)
        df_pres["fte_reales_disponibles"] = (df_pres["min_disponible"] / 480.0).round(1)

    return df_pres


def calcular_capacidad_intervalos_real(fecha_str: str, servicio_sel: str) -> pd.DataFrame:
    """
    Calcula la presencia real de Genesys para cada uno de los 48 intervalos de 30 min.
    Aplica la distinción de estados productivos de Back Office vs Inbound.
    """
    real_db_path = Path(__file__).parent / DB_PATH
    if not os.path.exists(real_db_path):
        return pd.DataFrame()

    conn = sqlite3.connect(real_db_path)
    query = """
        SELECT agente, presence_label, system_presence, inicio, fin, duracion_min
        FROM segments
        WHERE fecha = ? AND UPPER(TRIM(servicio)) = UPPER(TRIM(?))
    """
    df_seg = pd.read_sql(query, conn, params=(fecha_str, servicio_sel))
    conn.close()

    if df_seg.empty:
        return pd.DataFrame()

    df_seg["dt_ini"] = pd.to_datetime(df_seg["inicio"])
    df_seg["dt_fin"] = pd.to_datetime(df_seg["fin"]).fillna(
        df_seg["dt_ini"] + pd.to_timedelta(df_seg["duracion_min"], unit="m")
    )

    es_bo = ("BO" in servicio_sel.upper()) or ("BACKOFFICE" in servicio_sel.upper())
    estados_productivos = ["Available", "On Queue", "Casos Backoffice"] if es_bo else ["Available", "On Queue"]

    day_dt = pd.to_datetime(fecha_str)
    intervalos = []
    for i in range(48):
        t_start = day_dt + pd.Timedelta(minutes=i * 30)
        t_end = t_start + pd.Timedelta(minutes=30)
        int_label = t_start.strftime("%H:%M")

        ov_s = df_seg["dt_ini"].clip(lower=t_start)
        ov_e = df_seg["dt_fin"].clip(upper=t_end)
        ov_min = (ov_e - ov_s).dt.total_seconds() / 60.0
        ov_min = ov_min.clip(lower=0)

        sub = df_seg[ov_min > 0].copy()
        sub["w_min"] = ov_min[ov_min > 0]

        if not sub.empty:
            min_con = sub[sub["presence_label"] != "Offline"]["w_min"].sum()
            min_disp = sub[sub["presence_label"].isin(estados_productivos)]["w_min"].sum()
            min_pau = sub[~sub["presence_label"].isin(estados_productivos + ["Offline"])]["w_min"].sum()
        else:
            min_con = 0.0
            min_disp = 0.0
            min_pau = 0.0

        intervalos.append({
            "intervalo": int_label,
            "min_conectado": min_con,
            "min_disponible": min_disp,
            "min_pausas": min_pau,
            "fte_conectado": min_con / 30.0,
            "fte_disponible": min_disp / 30.0,
        })

    return pd.DataFrame(intervalos)


def diagnosticar_causa_raiz(gap_personas: float, aux_real: float, aux_meta: float, pct_capacidad: float) -> str:
    """
    Atribuye con objetividad operativa la causa raíz por la cual un servicio no cumplió su capacidad.
    """
    if pct_capacidad >= 95.0:
        if gap_personas >= 0 and aux_real <= aux_meta:
            return "🟢 Capacidad Óptima"
        return "🟢 Capacidad Cumplida"

    causas = []
    if gap_personas < -1.5:
        causas.append(f"Déficit Personas ({gap_personas:+.1f} FTE)")
    if aux_real > (aux_meta + 2.0):
        causas.append(f"Fuga Auxiliares ({aux_real:.1f}% > {aux_meta:.0f}%)")

    if not causas:
        if pct_capacidad >= 85.0:
            return "🟡 Desviación Leve (Aceptable)"
        return "🟡 Dilución Operativa Intradía"

    return "🔴 " + " • ".join(causas)


def render_tab_capacidad(agentes_map: dict):
    """
    Renderiza la Matriz Ejecutiva Panorámica de Capacidad y el Desglose Intradía.
    """
    st.markdown("### 🧭 Matriz Ejecutiva de Capacidad y Diagnóstico Operativo")
    st.caption(
        "Herramienta gerencial de contraste: Compara el dimensionamiento planificado por WFM (SORE) "
        "frente a la ejecución real de presencia en Genesys, identificando la causa raíz de las brechas de servicio."
    )

    df_fore_all = cargar_forecast_sore_completo()
    if df_fore_all.empty:
        st.error("⚠️ No se encontraron los archivos de dimensionamiento de SORE en la raíz del proyecto.")
        return

    # Selector de fecha disponible (default al día más reciente con registros)
    fechas_disp = sorted(df_fore_all["fecha"].unique().tolist())
    real_db_path = Path(__file__).parent / DB_PATH
    fecha_default_idx = len(fechas_disp) - 1
    if os.path.exists(real_db_path):
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

    # Barra superior de controles
    col_f1, col_f2, col_f3, col_btn = st.columns([1.6, 2.0, 3.6, 1.2])
    with col_f1:
        fecha_sel = st.selectbox("📅 Fecha de Evaluación:", fechas_disp, index=fecha_default_idx, key="cap_fecha_sel")
    with col_f2:
        filtro_mundo = st.selectbox(
            "🌐 Operación / Canal:",
            ["Todos los Servicios", "📞 Línea / Inbound Voz", "💬 Canales Digitales", "📂 Back Office"],
            index=0
        )
    with col_f3:
        st.markdown(
            f"""
            <div style="background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px; padding: 7px 12px; margin-top: 5px;">
                <span style="font-size: 11px; color: #64748b;">Parámetros Oficiales del Comité:</span><br>
                <span style="font-size: 12px; font-weight: 600; color: #0f172a;">
                    Meta Auxiliares: <b>{META_AUXILIARES_OFICIAL:.0f}%</b> (86% Disp.) · Meta AHT: <b>Fija SORE</b> · Base FTE: <b>8h (480m)</b>
                </span>
            </div>
            """,
            unsafe_allow_html=True
        )
    with col_btn:
        st.markdown("<div style='margin-top: 24px;'></div>", unsafe_allow_html=True)
        if st.button("🔄 Recargar", help="Fuerza la lectura fresca de los archivos Excel de SORE"):
            cargar_forecast_sore_completo(forzar_recarga=True)
            st.rerun()

    # 1. Consolidar dimensionamiento del día
    df_fore_dia = df_fore_all[df_fore_all["fecha"] == fecha_sel].copy()
    if df_fore_dia.empty:
        st.warning(f"No hay registros de dimensionamiento para la fecha {fecha_sel}.")
        return

    # 2. Cargar presencia real de Genesys
    df_pres_dia = cargar_presencia_resumen_dia(fecha_sel)

    # Resumen agrupado del forecast por servicio
    fore_summary = df_fore_dia.groupby(["servicio", "tipo_mundo"]).agg({
        "traffic_forecast": "sum",
        "minutos_req": "sum",
        "meta_aht_plana": "first",
        "meta_ns": "first"
    }).reset_index()

    # FTEs requeridos en base a jornada estándar de 8 horas (480 minutos)
    fore_summary["fte_dia_requerido"] = (fore_summary["minutos_req"] / 480.0).round(1)

    # 3. Unir Forecast y Real
    if not df_pres_dia.empty:
        matriz = pd.merge(fore_summary, df_pres_dia, on="servicio", how="left").fillna(0.0)
    else:
        matriz = fore_summary.copy()
        matriz["min_conectado"] = 0.0
        matriz["min_pausas"] = 0.0
        matriz["min_disponible"] = 0.0
        matriz["pct_auxiliares_real"] = 0.0
        matriz["fte_reales_conectados"] = 0.0
        matriz["fte_reales_disponibles"] = 0.0

    # Aplicar filtro por mundo
    if filtro_mundo == "📞 Línea / Inbound Voz":
        matriz = matriz[matriz["tipo_mundo"] == "Línea / Inbound Voz"]
    elif filtro_mundo == "💬 Canales Digitales":
        matriz = matriz[matriz["tipo_mundo"] == "Canales Digitales"]
    elif filtro_mundo == "📂 Back Office":
        matriz = matriz[matriz["tipo_mundo"] == "Back Office"]

    if matriz.empty:
        st.info("Sin registros para los filtros seleccionados.")
        return

    # Construir filas ejecutivas con cálculos
    filas_tabla = []
    for _, row in matriz.iterrows():
        srv = row["servicio"]
        tipo = row["tipo_mundo"]
        fte_req = row["fte_dia_requerido"]
        fte_con = row["fte_reales_conectados"]
        fte_disp = row["fte_reales_disponibles"]
        aux_real = row["pct_auxiliares_real"]
        meta_aht = row["meta_aht_plana"]
        min_req = row["minutos_req"]
        min_disp = row["min_disponible"]
        min_con = row["min_conectado"]
        min_pau = row["min_pausas"]

        gap_fte = fte_con - fte_req
        pct_capacidad = (min_disp / min_req * 100.0) if min_req > 0 else 100.0

        # Pérdida de tiempo por exceder el 14% de auxiliares
        min_aux_permitidos = min_con * (META_AUXILIARES_OFICIAL / 100.0)
        min_exceso_aux = max(0.0, min_pau - min_aux_permitidos)
        horas_exceso_aux = min_exceso_aux / 60.0

        causa = diagnosticar_causa_raiz(
            gap_personas=gap_fte,
            aux_real=aux_real,
            aux_meta=META_AUXILIARES_OFICIAL,
            pct_capacidad=pct_capacidad
        )

        filas_tabla.append({
            "Servicio": srv,
            "Canal": tipo,
            "FTE Req": fte_req,
            "FTE Con": fte_con,
            "Brecha FTE": gap_fte,
            "% Aux Real": aux_real,
            "Meta Aux": META_AUXILIARES_OFICIAL,
            "Horas Fuga Aux": horas_exceso_aux,
            "Meta AHT (s)": int(meta_aht),
            "% Capacidad": pct_capacidad,
            "Min. Requeridos": min_req,
            "Min. Disponibles": min_disp,
            "Min. Pausas": min_pau,
            "Diagnóstico Operativo": causa
        })

    df_ejecutiva = pd.DataFrame(filas_tabla).sort_values(by=["FTE Req"], ascending=False)

    # 4. Scorecard Macro de la Operación
    tot_fte_req = df_ejecutiva["FTE Req"].sum()
    tot_fte_con = df_ejecutiva["FTE Con"].sum()
    tot_min_req = df_ejecutiva["Min. Requeridos"].sum()
    tot_min_disp = df_ejecutiva["Min. Disponibles"].sum()
    tot_min_pau = df_ejecutiva["Min. Pausas"].sum()
    tot_min_con = tot_min_disp + tot_min_pau

    cumpl_global = (tot_min_disp / tot_min_req * 100.0) if tot_min_req > 0 else 0.0
    pct_aux_global = (tot_min_pau / tot_min_con * 100.0) if tot_min_con > 0 else 0.0
    gap_fte_global = tot_fte_con - tot_fte_req
    tot_horas_fuga = df_ejecutiva["Horas Fuga Aux"].sum()
    fte_fuga_equivalentes = tot_horas_fuga / 8.0

    st.markdown("---")
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        color_cumpl = "normal" if cumpl_global >= 90 else "inverse"
        st.metric(
            "Capacidad Neta Global",
            f"{cumpl_global:.1f}%",
            delta=f"{cumpl_global - 100.0:+.1f}% vs Requerido",
            delta_color=color_cumpl,
            help="% de minutos productivos reales frente al total exigido por SORE."
        )
    with m2:
        color_gap = "normal" if gap_fte_global >= 0 else "inverse"
        st.metric(
            "Balance de Personal (FTE)",
            f"{tot_fte_con:.1f} / {tot_fte_req:.1f}",
            delta=f"{gap_fte_global:+.1f} FTEs",
            delta_color=color_gap,
            help="Asesores equivalentes conectados vs asesores requeridos."
        )
    with m3:
        color_aux = "normal" if pct_aux_global <= META_AUXILIARES_OFICIAL else "inverse"
        st.metric(
            "% Auxiliares Global",
            f"{pct_aux_global:.1f}%",
            delta=f"{pct_aux_global - META_AUXILIARES_OFICIAL:+.1f}% vs Meta (14%)",
            delta_color=color_aux,
            help="% del tiempo conectado consumido en pausas en toda la operación."
        )
    with m4:
        st.metric(
            "Horas Perdidas por Exceso",
            f"{tot_horas_fuga:.1f} h",
            delta=f"≈ {fte_fuga_equivalentes:.1f} Asesores",
            delta_color="off",
            help="Horas hombre netas destruidas por haber superado el límite del 14% de auxiliares."
        )

    st.markdown("---")

    # 5. Tabla Matriz Ejecutiva Panorámica
    st.markdown("#### 📊 Matriz Panorámica de Cumplimiento y Causa Raíz")
    st.caption("Haz clic en cualquier servicio para desglosar su comportamiento intradía en las 48 franjas horarias.")

    def estilo_gap(val):
        if pd.isna(val): return ""
        if val >= 0: color = "#10b981"
        elif val >= -2.0: color = "#f59e0b"
        else: color = "#ef4444"
        return f"background-color: {color}20; color: {color}; font-weight: 700;"

    def estilo_cumpl(val):
        if pd.isna(val): return ""
        if val >= 95.0: color = "#10b981"
        elif val >= 85.0: color = "#f59e0b"
        else: color = "#ef4444"
        return f"background-color: {color}20; color: {color}; font-weight: 700;"

    def estilo_aux(val):
        if pd.isna(val) or val == 0: return ""
        if val <= META_AUXILIARES_OFICIAL: color = "#10b981"
        elif val <= (META_AUXILIARES_OFICIAL + 4.0): color = "#f59e0b"
        else: color = "#ef4444"
        return f"background-color: {color}20; color: {color}; font-weight: 700;"

    styler_matriz = (
        df_ejecutiva[[
            "Servicio", "Canal", "FTE Req", "FTE Con", "Brecha FTE",
            "% Aux Real", "Meta Aux", "Meta AHT (s)", "% Capacidad", "Diagnóstico Operativo"
        ]].style
        .map(estilo_gap, subset=["Brecha FTE"])
        .map(estilo_cumpl, subset=["% Capacidad"])
        .map(estilo_aux, subset=["% Aux Real"])
    )

    evento = st.dataframe(
        styler_matriz,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key="tabla_matriz_capacidad_v2",
        column_config={
            "Servicio": st.column_config.TextColumn("Servicio", help="Nombre oficial en Genesys y SORE"),
            "Canal": st.column_config.TextColumn("Mundo / Canal"),
            "FTE Req": st.column_config.NumberColumn("FTE Req", format="%.1f", help="Asesores requeridos por SORE"),
            "FTE Con": st.column_config.NumberColumn("FTE Con", format="%.1f", help="Asesores conectados en Genesys"),
            "Brecha FTE": st.column_config.NumberColumn("Brecha FTE", format="%+.1f", help="Diferencia de personal: Conectados - Requeridos"),
            "% Aux Real": st.column_config.NumberColumn("% Aux Real", format="%.1f%%", help="% de tiempo en pausas y estados no productivos"),
            "Meta Aux": st.column_config.NumberColumn("Meta Aux", format="%.0f%%"),
            "Meta AHT (s)": st.column_config.NumberColumn("Meta AHT", format="%d s", help="Meta plana fija oficial de dimensionamiento"),
            "% Capacidad": st.column_config.NumberColumn("% Capacidad Neta", format="%.1f%%", help="Minutos disponibles ÷ Minutos requeridos"),
            "Diagnóstico Operativo": st.column_config.TextColumn("Causa Raíz / Veredicto"),
        }
    )

    # 6. Servicio Seleccionado para el Detalle Intradía
    filas_sel = evento.selection.get("rows", [])
    if filas_sel:
        idx_sel = filas_sel[0]
        srv_detalle = df_ejecutiva.iloc[idx_sel]["Servicio"]
        fila_detalle = df_ejecutiva.iloc[idx_sel]
    else:
        srv_detalle = df_ejecutiva.iloc[0]["Servicio"]
        fila_detalle = df_ejecutiva.iloc[0]

    # 7. Diagnóstico Narrativo Ejecutivo del Servicio Seleccionado
    st.markdown("---")
    st.subheader(f"🔎 Diagnóstico Detallado — {srv_detalle}")

    d_req = fila_detalle["FTE Req"]
    d_con = fila_detalle["FTE Con"]
    d_gap = fila_detalle["Brecha FTE"]
    d_aux = fila_detalle["% Aux Real"]
    d_cap = fila_detalle["% Capacidad"]
    d_aht = fila_detalle["Meta AHT (s)"]
    d_mreq = fila_detalle["Min. Requeridos"]
    d_mdisp = fila_detalle["Min. Disponibles"]
    d_mpau = fila_detalle["Min. Pausas"]
    d_hfuga = fila_detalle["Horas Fuga Aux"]

    texto_personas = (
        f"🟢 Se contó con dotación suficiente (**{d_con:.1f} FTEs** vs **{d_req:.1f} FTEs** requeridos, **{d_gap:+.1f}** de holgura)"
        if d_gap >= 0
        else f"🔴 Se presentó un déficit de personal de **{d_gap:+.1f} FTEs** (**{d_con:.1f}** conectados frente a **{d_req:.1f}** solicitados)"
    )

    texto_aux = (
        f"🟢 La disciplina de auxiliares estuvo controlada en un **{d_aux:.1f}%** (dentro de la meta oficial del 14%)"
        if d_aux <= META_AUXILIARES_OFICIAL
        else f"🟠 Se registró sobreconsumo de auxiliares con un **{d_aux:.1f}%** frente al 14% meta, destruyendo **{d_hfuga:.1f} horas de capacidad**"
    )

    st.markdown(
        f"""
        <div style="background: #ffffff; border-left: 5px solid {'#10b981' if d_cap >= 90 else '#ef4444'}; border-radius: 8px; padding: 14px 18px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); margin-bottom: 16px;">
            <span style="font-size: 15px; font-weight: 700; color: #0f172a;">Resumen Ejecutivo de Capacidad:</span><br>
            <p style="font-size: 13.5px; color: #334155; margin-top: 6px; line-height: 1.6;">
                Para el servicio <b>{srv_detalle}</b> el <b>{fecha_sel}</b>, WFM SORE dimensionó una exigencia de <b>{d_mreq:,.0f} minutos hombre</b> ({d_req:.1f} FTEs).<br>
                • <b>1. Conexión / Asistencia:</b> {texto_personas}.<br>
                • <b>2. Auxiliares y Pausas:</b> {texto_aux}. De los minutos conectados, <b>{d_mpau:,.0f} minutos</b> se consumieron en pausas.<br>
                • <b>3. Criterio de AHT:</b> Evaluado contra la meta plana de dimensionamiento de <b>{d_aht} segundos</b>.<br>
                • <b>4. Capacidad Efectiva Lograda:</b> Quedaron <b>{d_mdisp:,.0f} minutos productivos disponibles</b>, alcanzando un <b>{d_cap:.1f}% de cumplimiento de capacidad</b> frente al plan.
            </p>
        </div>
        """,
        unsafe_allow_html=True
    )

    # 8. Gráfica Intradía de 48 Franjas de 30 min (Lógica Hombre 3)
    st.markdown("##### 📈 Curva Intradía de Cobertura (30 min × FTEs = Minutos)")
    st.caption("Compara en cada intervalo cuántas personas exigía SORE vs cuántas estaban conectadas y cuántas efectivamente disponibles.")

    sub_f_int = df_fore_dia[df_fore_dia["servicio"] == srv_detalle].copy()
    sub_r_int = calcular_capacidad_intervalos_real(fecha_sel, srv_detalle)

    if sub_r_int.empty:
        sub_r_int = pd.DataFrame([
            {"intervalo": int_lbl, "min_conectado": 0.0, "min_disponible": 0.0, "min_pausas": 0.0, "fte_conectado": 0.0, "fte_disponible": 0.0}
            for int_lbl in sub_f_int["intervalo"].tolist()
        ])

    merged_int = pd.merge(sub_f_int, sub_r_int, on="intervalo", how="left").fillna(0.0)

    fig_int = go.Figure()
    fig_int.add_trace(go.Scatter(
        x=merged_int["intervalo"],
        y=merged_int["asesores_req"],
        mode="lines+markers",
        name="1. Requerido SORE (FTEs)",
        line=dict(color="#f59e0b", width=3, dash="dash")
    ))
    fig_int.add_trace(go.Scatter(
        x=merged_int["intervalo"],
        y=merged_int["fte_conectado"],
        mode="lines",
        name="2. Conectados Totales (FTEs)",
        line=dict(color="#94a3b8", width=2)
    ))
    fig_int.add_trace(go.Bar(
        x=merged_int["intervalo"],
        y=merged_int["fte_disponible"],
        name="3. Disponible Efectivo (FTEs)",
        marker_color="#2563eb",
        opacity=0.75
    ))

    fig_int.update_layout(
        title=f"Curva Intradía de Cobertura y Capacidad — {srv_detalle} ({fecha_sel})",
        xaxis=dict(title="Intervalo de 30 min", tickangle=-45),
        yaxis=dict(title="Equivalente de Asesores (FTEs)"),
        hovermode="x unified",
        margin=dict(l=20, r=20, t=40, b=20),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    st.plotly_chart(fig_int, use_container_width=True)

    # 9. Tabla de Intervalos de 30 minutos (Lógica Hombre 3)
    with st.expander(f"📋 Ver Tabla Detallada Intervalo a Intervalo ({srv_detalle})", expanded=False):
        df_mostrar_int = merged_int[[
            "intervalo", "traffic_forecast", "asesores_req", "fte_conectado", "fte_disponible",
            "minutos_req", "min_conectado", "min_pausas", "min_disponible"
        ]].copy()

        df_mostrar_int["% Capacidad"] = df_mostrar_int.apply(
            lambda r: (r["min_disponible"] / r["minutos_req"] * 100.0) if r["minutos_req"] > 0 else 100.0, axis=1
        ).round(1)

        st.dataframe(
            df_mostrar_int.rename(columns={
                "intervalo": "Intervalo",
                "traffic_forecast": "Tráfico Plan",
                "asesores_req": "FTE Requerido",
                "fte_conectado": "FTE Conectado",
                "fte_disponible": "FTE Disponible",
                "minutos_req": "Min. Requeridos",
                "min_conectado": "Min. Conectados",
                "min_pausas": "Min. Pausas",
                "min_disponible": "Min. Disponibles",
            }),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Intervalo": st.column_config.TextColumn(width="small"),
                "Tráfico Plan": st.column_config.NumberColumn(format="%.1f"),
                "FTE Requerido": st.column_config.NumberColumn(format="%.2f"),
                "FTE Conectado": st.column_config.NumberColumn(format="%.2f"),
                "FTE Disponible": st.column_config.NumberColumn(format="%.2f"),
                "Min. Requeridos": st.column_config.NumberColumn(format="%.1f m"),
                "Min. Conectados": st.column_config.NumberColumn(format="%.1f m"),
                "Min. Pausas": st.column_config.NumberColumn(format="%.1f m"),
                "Min. Disponibles": st.column_config.NumberColumn(format="%.1f m"),
                "% Capacidad": st.column_config.NumberColumn(format="%.1f%%"),
            }
        )

