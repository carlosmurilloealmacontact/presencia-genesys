"""
Motor de Gestión y Control de Ausentismo Operativo — Radar Genesys Cloud.

Conecta en tiempo real la programación de turnos (Planta / Sistema de Punto)
con el estado de conexión real de Genesys Cloud:
1. Radar de Alerta Temprana (Primeros 15-30 min): Detección inmediata de No-Logins y Retrasos.
2. Auto-Servicio del Líder: Justificación en 2 clics persistida en Neon Postgres y SQLite.
3. Impacto en Capacidad: Horas perdidas justificadas vs injustificadas para la Base del Requerido.
4. Matriz Ejecutiva y Exportación: Resumen por Supervisor, Servicio y Tasa de Ausentismo.
"""

from datetime import datetime, date, timedelta, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import sqlite3
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from config import DB_PATH
from live_engine import obtener_token_genesys, obtener_presencia_en_vivo, cargar_catalogo_presencias
from audit_engine import _obtener_db_url, registrar_evento

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def numero_agente(agente_nombre: str) -> str:
    """Extrae el identificador o BP desde '4853818 - Nombre Apellido' -> '4853818'."""
    if not agente_nombre:
        return ""
    s = str(agente_nombre).strip()
    return s.split(" - ")[0].strip() if " - " in s else s

# Catálogo oficial de tipos de ausencia según imagen operativa
TIPOS_AUSENCIA_OFICIALES = [
    "INCAPACIDAD MEDICA POR ENFERMEDAD MENOR A TRES DIAS",
    "INCAPACIDAD MEDICA POR ENFERMEDAD MAYOR A TRES DIAS",
    "INCAPACIDAD POR ACCIDENTE DE TRABAJO O ENFERMEDAD PROFESIONAL > 3 DIAS",
    "LICENCIA DE MATERNIDAD",
    "LICENCIA DE PATERNIDAD (LEY MARIA)",
    "LICENCIA NO REMUNERADA",
    "LICENCIA POR LUTO",
    "LICENCIA REMUNERADA",
    "SANCION POR PROCESO DISCIPLINARIO",
    "VACACIONES",
    "DIA DE LA FAMILIA",
    "CALAMIDAD DOMESTICA DEBIDAMENTE COMPROBADA",
    "FALLA TECNICA / CONECTIVIDAD / HERRAMIENTAS",
    "PERMISO COMPENSATORIO / APROBADO",
    "AUSENCIA NO JUSTIFICADA / ABANDONO / NO SHOW",
    "CAMBIO DE TURNO / NO PROGRAMADO HOY"
]

# Ausencias que se clasifican como NO justificadas o penalizables
AUSENCIAS_INJUSTIFICADAS_SET = {
    "AUSENCIA NO JUSTIFICADA / ABANDONO / NO SHOW",
    "SANCION POR PROCESO DISCIPLINARIO"
}


# ─────────────────────────────────────────────────────────────────────────────
# GESTIÓN DE BASE DE DATOS DE JUSTIFICACIONES (Neon Postgres + SQLite Fallback)
# ─────────────────────────────────────────────────────────────────────────────

def cargar_justificaciones_db(fecha_str: str) -> pd.DataFrame:
    """Carga justificaciones registradas para una fecha determinada."""
    # 1. Intentar Neon Postgres
    db_url = _obtener_db_url()
    try:
        import psycopg2
        conn = psycopg2.connect(db_url, connect_timeout=4)
        df = pd.read_sql_query(
            "SELECT fecha, bp, tipo_impuntualidad, es_justificado, observacion, registrado_por, fecha_registro "
            "FROM ausentismo_justificaciones WHERE fecha = %s",
            conn,
            params=(fecha_str,)
        )
        conn.close()
        if not df.empty:
            return df
    except Exception:
        pass

    # 2. Fallback local SQLite
    db_path = Path(BASE_DIR) / DB_PATH
    if db_path.exists():
        try:
            with sqlite3.connect(db_path) as conn:
                df = pd.read_sql_query(
                    "SELECT fecha, bp, tipo_impuntualidad, es_justificado, observacion, registrado_por, fecha_registro "
                    "FROM ausentismo_justificaciones WHERE fecha = ?",
                    conn,
                    params=(fecha_str,)
                )
                return df
        except Exception:
            pass

    return pd.DataFrame(columns=["fecha", "bp", "tipo_impuntualidad", "es_justificado", "observacion", "registrado_por", "fecha_registro"])


def guardar_justificacion_db(
    fecha_str: str,
    bp: str,
    agente: str,
    supervisor: str,
    coordinador: str,
    servicio: str,
    tipo_impuntualidad: str,
    observacion: str,
    registrado_por: str
) -> bool:
    """Guarda o actualiza una justificación en Neon Postgres y sincroniza con SQLite."""
    es_justificado = 0 if tipo_impuntualidad in AUSENCIAS_INJUSTIFICADAS_SET else 1

    # Guardar en Neon Postgres
    db_url = _obtener_db_url()
    guardado_remoto = False
    try:
        import psycopg2
        conn = psycopg2.connect(db_url, connect_timeout=5)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO ausentismo_justificaciones 
            (fecha, bp, agente, supervisor, coordinador, servicio, tipo_impuntualidad, es_justificado, observacion, registrado_por)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (fecha, bp) DO UPDATE SET
                tipo_impuntualidad = EXCLUDED.tipo_impuntualidad,
                es_justificado = EXCLUDED.es_justificado,
                observacion = EXCLUDED.observacion,
                registrado_por = EXCLUDED.registrado_por,
                fecha_registro = CURRENT_TIMESTAMP;
        """, (fecha_str, bp, agente, supervisor, coordinador, servicio, tipo_impuntualidad, bool(es_justificado), observacion, registrado_por))
        conn.commit()
        cur.close()
        conn.close()
        guardado_remoto = True
    except Exception as e:
        print(f"[Ausentismo] Error guardando en Neon: {e}")

    # Guardar en SQLite local
    db_path = Path(BASE_DIR) / DB_PATH
    if db_path.exists():
        try:
            with sqlite3.connect(db_path) as conn:
                conn.execute("""
                    INSERT INTO ausentismo_justificaciones 
                    (fecha, bp, agente, supervisor, coordinador, servicio, tipo_impuntualidad, es_justificado, observacion, registrado_por)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(fecha, bp) DO UPDATE SET
                        tipo_impuntualidad = excluded.tipo_impuntualidad,
                        es_justificado = excluded.es_justificado,
                        observacion = excluded.observacion,
                        registrado_por = excluded.registrado_por,
                        fecha_registro = CURRENT_TIMESTAMP
                """, (fecha_str, bp, agente, supervisor, coordinador, servicio, tipo_impuntualidad, es_justificado, observacion, registrado_por))
                conn.commit()
        except Exception as e:
            print(f"[Ausentismo] Error guardando en SQLite: {e}")

    return guardado_remoto


# ─────────────────────────────────────────────────────────────────────────────
# CARGA Y CRUCE DE TURNOS PROGRAMADOS VS GENESYS
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=120)
def obtener_turnos_programados_dia(fecha_str: str) -> pd.DataFrame:
    """Carga los turnos programados en SQLite para una fecha."""
    db_path = Path(BASE_DIR) / DB_PATH
    if not db_path.exists():
        return pd.DataFrame()
    with sqlite3.connect(db_path) as conn:
        try:
            df = pd.read_sql_query(
                "SELECT bp, fecha, hora_inicio, hora_fin FROM turnos WHERE fecha = ?",
                conn,
                params=(fecha_str,)
            )
            return df
        except Exception:
            return pd.DataFrame()


def construir_radar_ausentismo(
    fecha_str: str,
    df_live_presencia: pd.DataFrame,
    agentes_map: dict
) -> pd.DataFrame:
    """
    Cruza los turnos programados de hoy contra la presencia de Genesys.
    Determina:
    - Estado de entrada: Conectado a tiempo, Retraso (5-15m), Crítico (>15m), Sin Login (Offline).
    - Horas de turno y pérdida de capacidad.
    """
    df_turnos = obtener_turnos_programados_dia(fecha_str)
    if df_turnos.empty:
        return pd.DataFrame()

    df_just = cargar_justificaciones_db(fecha_str)
    just_map = {}
    if not df_just.empty:
        just_map = df_just.set_index("bp").to_dict(orient="index")

    # Mapeo de presencia actual por BP
    presencia_act_map = {}
    if not df_live_presencia.empty and "agente_id" in df_live_presencia.columns:
        for _, row in df_live_presencia.iterrows():
            aid = row.get("agente_id", "")
            # bp suele ser el número de agente
            bp_val = numero_agente(row.get("agente", aid))
            presencia_act_map[bp_val] = {
                "presence_label": row.get("presence_label", "Offline"),
                "system_presence": row.get("system_presence", "Offline"),
                "duracion_min": row.get("duracion_min", 0.0),
                "hora_ultimo_cambio": row.get("hora_ultimo_cambio", "")
            }

    # Hora actual en Colombia
    now_col = datetime.now(timezone.utc) - timedelta(hours=5)
    hora_act_str = now_col.strftime("%H:%M:%S")

    filas = []
    for _, r in df_turnos.iterrows():
        bp = str(r["bp"]).strip()
        h_ini = str(r["hora_inicio"]).strip()
        h_fin = str(r["hora_fin"]).strip()

        info_ag = agentes_map.get(bp, {})
        if not info_ag:
            for k, v in agentes_map.items():
                if numero_agente(v.get("agente", "")) == bp or str(k) == bp:
                    info_ag = v
                    break

        agente_nom = info_ag.get("agente", f"Asesor {bp}")
        sup = info_ag.get("jefe_inmediato", "Sin Supervisor")
        coord = info_ag.get("coordinador", "Sin Coordinador")
        srv = info_ag.get("servicio", "General")

        # Calcular duración programada en horas
        try:
            t_ini_dt = datetime.strptime(h_ini, "%H:%M:%S")
            t_fin_dt = datetime.strptime(h_fin, "%H:%M:%S")
            if t_fin_dt <= t_ini_dt:
                t_fin_dt += timedelta(days=1)
            duracion_turno_horas = (t_fin_dt - t_ini_dt).total_seconds() / 3600.0
        except Exception:
            duracion_turno_horas = 8.0

        # Estado en Genesys
        live_info = presencia_act_map.get(bp, {})
        pres_label = live_info.get("presence_label", "Offline")
        sys_pres = live_info.get("system_presence", "Offline")

        # Evaluar estado de puntualidad / asistencia
        ya_debio_iniciar = (hora_act_str >= h_ini)
        minutos_desde_inicio = 0
        try:
            t_ini_dt_today = now_col.replace(
                hour=int(h_ini.split(":")[0]),
                minute=int(h_ini.split(":")[1]),
                second=int(h_ini.split(":")[2]),
                microsecond=0
            )
            minutos_desde_inicio = (now_col - t_ini_dt_today).total_seconds() / 60.0
        except Exception:
            pass

        # Determinar Semáforo y Clasificación
        esta_conectado = (pres_label != "Offline" and sys_pres != "Offline")

        if not ya_debio_iniciar:
            estado_asistencia = "⏰ Turno Futuro"
            semaforo = "⚪"
        elif esta_conectado:
            estado_asistencia = "🟢 Conectado"
            semaforo = "🟢"
        else:
            # No está conectado y ya pasó la hora de inicio
            if minutos_desde_inicio <= 5:
                estado_asistencia = "🟡 En Margen (<=5m)"
                semaforo = "🟡"
            elif minutos_desde_inicio <= 15:
                estado_asistencia = "🟠 Retraso Leve (5-15m)"
                semaforo = "🟠"
            elif minutos_desde_inicio <= 60:
                estado_asistencia = "🔴 Retraso Crítico (>15m)"
                semaforo = "🔴"
            else:
                estado_asistencia = "🚨 Ausencia / No Login"
                semaforo = "🚨"

        # Cruzar con justificación si existe
        just_info = just_map.get(bp, {})
        tipo_just = just_info.get("tipo_impuntualidad", None)
        obs_just = just_info.get("observacion", "")
        reg_por = just_info.get("registrado_por", "")

        estado_justificacion = "Pendiente de Justificar" if (semaforo in ("🟠", "🔴", "🚨") and not tipo_just) else (
            f"✅ {tipo_just}" if tipo_just else "No Aplica"
        )
        es_justificado = just_info.get("es_justificado", True) if tipo_just else False

        filas.append({
            "Semaforo": semaforo,
            "Estado": estado_asistencia,
            "BP": bp,
            "Agente": agente_nom,
            "Supervisor": sup,
            "Coordinador": coord,
            "Servicio": srv,
            "Hora Inicio": h_ini,
            "Hora Fin": h_fin,
            "Duracion Horas": round(duracion_turno_horas, 1),
            "Min Retraso": max(0, int(round(minutos_desde_inicio))) if not esta_conectado and ya_debio_iniciar else 0,
            "Estado Genesys": pres_label,
            "Justificación": tipo_just or "Sin Justificar",
            "Es Justificado": "Sí" if (tipo_just and es_justificado) else ("No" if (tipo_just and not es_justificado) else "Pendiente"),
            "Observación": obs_just,
            "Registrado Por": reg_por,
            "Ya Inició": ya_debio_iniciar,
            "Esta Conectado": esta_conectado
        })

    df_res = pd.DataFrame(filas)
    return df_res


# ─────────────────────────────────────────────────────────────────────────────
# RENDERIZADO VISUAL DEL MÓDULO EN STREAMLIT
# ─────────────────────────────────────────────────────────────────────────────

def render_tab_ausentismo(agentes_map: dict):
    """Interfaz completa del Módulo de Ausentismo y Conexión."""
    st.markdown(
        """
        <div style="background: linear-gradient(90deg, #0f172a 0%, #1e293b 100%); padding: 16px 20px; border-radius: 12px; margin-bottom: 15px; border-left: 5px solid #ef4444;">
            <div style="display: flex; justify-content: space-between; align-items: center;">
                <div>
                    <h3 style="color: #ffffff; margin: 0 0 4px 0; font-size: 20px;">🚨 Radar de Ausentismo y Conexión Operativa</h3>
                    <p style="color: #94a3b8; margin: 0; font-size: 13px;">
                        Control en tiempo real de turnos programados vs conexión en Genesys • Detección de No-Logins y Justificación Ágil
                    </p>
                </div>
                <div style="text-align: right; background: #334155; padding: 6px 14px; border-radius: 8px; border: 1px solid #475569;">
                    <span style="color: #38bdf8; font-size: 11px; font-weight: 700; text-transform: uppercase;">Módulo Confidencial</span><br>
                    <span style="color: #cbd5e1; font-size: 12px; font-weight: 600;">Exclusivo Gestión Operativa</span>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    now_col = datetime.now(timezone.utc) - timedelta(hours=5)
    hoy_col = now_col.date()

    # Controles de cabecera
    c_f1, c_f2, c_f3, c_f4 = st.columns([1.2, 1.5, 1.5, 1.2])
    with c_f1:
        fecha_sel = st.date_input("Fecha a Evaluar:", value=hoy_col, max_value=hoy_col + timedelta(days=7), key="dt_ausentismo")
    fecha_str = str(fecha_sel)

    # Obtener token y presencia en vivo de Genesys si es hoy
    token = obtener_token_genesys()
    df_live = pd.DataFrame()
    if fecha_sel == hoy_col and token:
        with st.spinner("Consultando presencia en tiempo real en Genesys Cloud..."):
            catalog = cargar_catalogo_presencias(token)
            df_live = obtener_presencia_en_vivo(token, agentes_map, catalog)

    with st.spinner(f"Cruzando programación de turnos con presencia ({fecha_str})..."):
        df_radar = construir_radar_ausentismo(fecha_str, df_live, agentes_map)

    if df_radar.empty:
        st.warning(f"No hay turnos programados cargados para la fecha {fecha_str}. Carga la malla semanal correspondiente.")
        return

    # Filtros dinámicos
    with c_f2:
        coords = sorted(df_radar["Coordinador"].dropna().unique())
        coord_sel = st.multiselect("Filtrar Coordinador:", options=coords, default=[], key="aus_coord_sel")
    with c_f3:
        sups_disp = df_radar["Supervisor"].dropna().unique() if not coord_sel else df_radar[df_radar["Coordinador"].isin(coord_sel)]["Supervisor"].dropna().unique()
        sup_sel = st.multiselect("Filtrar Supervisor:", options=sorted(sups_disp), default=[], key="aus_sup_sel")
    with c_f4:
        servicios_disp = df_radar["Servicio"].dropna().unique()
        srv_sel = st.multiselect("Filtrar Servicio:", options=sorted(servicios_disp), default=[], key="aus_srv_sel")

    df_view = df_radar.copy()
    if coord_sel:
        df_view = df_view[df_view["Coordinador"].isin(coord_sel)]
    if sup_sel:
        df_view = df_view[df_view["Supervisor"].isin(sup_sel)]
    if srv_sel:
        df_view = df_view[df_view["Servicio"].isin(srv_sel)]

    # ── KPIs DE RESUMEN EJECUTIVO ────────────────────────────────────────────
    total_prog = len(df_view)
    iniciados = df_view[df_view["Ya Inició"]]
    total_iniciados = len(iniciados)
    conectados_act = len(iniciados[iniciados["Esta Conectado"]])
    
    # Ausentes: debieron iniciar y están offline
    ausentes_df = iniciados[~iniciados["Esta Conectado"]]
    total_ausentes = len(ausentes_df)
    
    # Tasa de ausentismo sobre turnos ya iniciados
    tasa_ausentismo = (total_ausentes / total_iniciados * 100.0) if total_iniciados > 0 else 0.0

    # Capacidad perdida en horas
    horas_perdidas_tot = ausentes_df["Duracion Horas"].sum()
    horas_justificadas = ausentes_df[ausentes_df["Es Justificado"] == "Sí"]["Duracion Horas"].sum()
    horas_injustificadas = ausentes_df[ausentes_df["Es Justificado"] != "Sí"]["Duracion Horas"].sum()

    m1, m2, m3, m4, m5 = st.columns(5)
    with m1:
        st.metric("Programados del Día", f"{total_prog:,}", help=f"Total de turnos en malla para {fecha_str}")
    with m2:
        st.metric("Turnos ya Iniciados", f"{total_iniciados:,}", delta=f"{conectados_act} conectados")
    with m3:
        st.metric("Total Ausentes (No Login)", f"{total_ausentes:,}", delta=f"{tasa_ausentismo:.1f}% tasa", delta_color="inverse")
    with m4:
        st.metric(
            "Horas Capacidad Perdidas",
            f"{horas_perdidas_tot:.1f} h",
            delta=f"-{horas_injustificadas:.1f}h injustificadas",
            delta_color="inverse",
            help="Horas de turno restadas directamente a la Base del Requerido"
        )
    with m5:
        pendientes_just = len(ausentes_df[ausentes_df["Justificación"] == "Sin Justificar"])
        st.metric("Pendientes Justificar", f"{pendientes_just:,}", delta="Acción requerida líder" if pendientes_just > 0 else "Al día", delta_color="inverse" if pendientes_just > 0 else "normal")

    st.markdown("---")

    # ── SUBPESTAÑAS DE TRABAJO ──────────────────────────────────────────────
    tab_radar, tab_justificar, tab_analisis, tab_export = st.tabs([
        "🚨 Radar Temprano (En Vivo)",
        "✍️ Auto-Servicio del Líder (Justificar)",
        "📊 Impacto y Diagnóstico por Supervisor",
        "📥 Exportación Oficial a Excel"
    ])

    # ─────────────────────────────────────────────────────────────────────────
    # SUBPESTAÑA 1: RADAR TEMPRANO EN VIVO
    # ─────────────────────────────────────────────────────────────────────────
    with tab_radar:
        st.markdown("#### 🔍 Monitoreo de Conexión y Retrasos de Entrada")
        st.caption("Identifica en los primeros minutos de cada franja quién no ha iniciado sesión en Genesys.")

        col_filtro_est, col_espacio = st.columns([2, 3])
        with col_filtro_est:
            opc_estados = ["Todos los que debieron iniciar", "Solo Ausentes / No Login (Crítico)", "Solo Retrasos (5-15 min)", "Ver Todos (Incluye Futuros)"]
            filtro_est = st.selectbox("Vista de Radar:", opc_estados, index=1, key="aus_filtro_radar")

        if filtro_est == "Solo Ausentes / No Login (Crítico)":
            df_radar_show = df_view[df_view["Ya Inició"] & (~df_view["Esta Conectado"])]
        elif filtro_est == "Solo Retrasos (5-15 min)":
            df_radar_show = df_view[df_view["Estado"].str.contains("Retraso Leve", na=False)]
        elif filtro_est == "Todos los que debieron iniciar":
            df_radar_show = df_view[df_view["Ya Inició"]]
        else:
            df_radar_show = df_view

        cols_tabla_radar = [
            "Semaforo", "Estado", "BP", "Agente", "Supervisor", "Servicio",
            "Hora Inicio", "Min Retraso", "Estado Genesys", "Justificación", "Es Justificado", "Observación"
        ]

        st.dataframe(
            df_radar_show[cols_tabla_radar].sort_values(by=["Min Retraso", "Hora Inicio"], ascending=[False, True]),
            use_container_width=True,
            hide_index=True,
            column_config={
                "Semaforo": st.column_config.TextColumn("", width="small"),
                "Estado": st.column_config.TextColumn("Estado Entrada", width="medium"),
                "BP": st.column_config.TextColumn("BP", width="small"),
                "Agente": st.column_config.TextColumn("Nombre Asesor", width="large"),
                "Supervisor": st.column_config.TextColumn("Supervisor", width="medium"),
                "Hora Inicio": st.column_config.TextColumn("Turno Inicio", width="small"),
                "Min Retraso": st.column_config.NumberColumn("Retraso (min)", format="%d m"),
                "Estado Genesys": st.column_config.TextColumn("Genesys Actual", width="small"),
                "Justificación": st.column_config.TextColumn("Causal Registrada", width="medium"),
                "Es Justificado": st.column_config.TextColumn("Justificado", width="small"),
            }
        )

    # ─────────────────────────────────────────────────────────────────────────
    # SUBPESTAÑA 2: AUTO-SERVICIO DEL LÍDER (JUSTIFICACIÓN EN 2 CLICS)
    # ─────────────────────────────────────────────────────────────────────────
    with tab_justificar:
        st.markdown("#### ✍️ Panel Rápido de Justificación para Supervisores y GTR")
        st.markdown(
            """
            Selecciona un asesor ausente o retrasado, asigna el motivo de la lista oficial de Almacontact 
            y guarda con un clic. La información queda registrada en la base de datos central en tiempo real.
            """
        )

        c_form1, c_form2 = st.columns([1.5, 1])

        with c_form1:
            # Lista de asesores que requieren justificación
            pendientes_df = df_view[df_view["Ya Inició"] & (~df_view["Esta Conectado"])]
            
            if pendientes_df.empty:
                st.success("🎉 ¡Excelente! No hay asesores pendientes de justificar en este momento con los filtros seleccionados.")
            else:
                opciones_agentes = {
                    f"{r['BP']} - {r['Agente']} (Turno {r['Hora Inicio']} • Sup: {r['Supervisor']})": r["BP"]
                    for _, r in pendientes_df.iterrows()
                }

                agente_label_sel = st.selectbox("1. Selecciona el Asesor Ausente:", list(opciones_agentes.keys()), key="aus_sel_agente")
                bp_seleccionado = opciones_agentes[agente_label_sel]
                datos_ag = pendientes_df[pendientes_df["BP"] == bp_seleccionado].iloc[0]

                c_sub1, c_sub2 = st.columns(2)
                with c_sub1:
                    tipo_aus_sel = st.selectbox("2. Tipo de Ausencia / Impuntualidad (Oficial):", TIPOS_AUSENCIA_OFICIALES, key="aus_sel_tipo")
                with c_sub2:
                    obs_input = st.text_input("3. Observación / Nro. Radicado (Opcional):", placeholder="Ej. Incapacidad EPS Sura #12345", key="aus_obs_input")

                c_btn_save, c_msg = st.columns([1, 2])
                with c_btn_save:
                    if st.button("💾 Guardar Justificación", type="primary", use_container_width=True, key="btn_save_just"):
                        user_email = getattr(st.user, "email", "carlosmurilloe.almacontact@outsourcing-account.com") if hasattr(st, "user") else "carlosmurilloe.almacontact@outsourcing-account.com"
                        ok = guardar_justificacion_db(
                            fecha_str=fecha_str,
                            bp=bp_seleccionado,
                            agente=datos_ag["Agente"],
                            supervisor=datos_ag["Supervisor"],
                            coordinador=datos_ag["Coordinador"],
                            servicio=datos_ag["Servicio"],
                            tipo_impuntualidad=tipo_aus_sel,
                            observacion=obs_input,
                            registrado_por=user_email
                        )
                        registrar_evento(user_email, user_email, "Ausentismo", "guardar_justificacion", f"BP: {bp_seleccionado} - {tipo_aus_sel}")
                        st.success(f"✅ Justificación registrada para {datos_ag['Agente']}.")
                        st.rerun()

        with c_form2:
            st.info(
                f"""
                📌 **Resumen del Asesor Seleccionado:**
                - **Turno:** `{datos_ag['Hora Inicio']}` a `{datos_ag['Hora Fin']}` ({datos_ag['Duracion Horas']} hrs)
                - **Supervisor:** {datos_ag['Supervisor']}
                - **Servicio:** {datos_ag['Servicio']}
                - **Retraso acumulado:** {datos_ag['Min Retraso']} minutos
                - **Genesys:** `{datos_ag['Estado Genesys']}`
                """ if not pendientes_df.empty else "Sin novedades activas."
            )

        # Historial de justificaciones de la fecha
        st.markdown("---")
        st.markdown("##### 📋 Justificaciones Registradas para esta Fecha:")
        df_just_hoy = cargar_justificaciones_db(fecha_str)
        if df_just_hoy.empty:
            st.caption("Aún no se han registrado justificaciones para el día seleccionado.")
        else:
            st.dataframe(df_just_hoy, use_container_width=True, hide_index=True)

    # ─────────────────────────────────────────────────────────────────────────
    # SUBPESTAÑA 3: IMPACTO Y DIAGNÓSTICO POR SUPERVISOR
    # ─────────────────────────────────────────────────────────────────────────
    with tab_analisis:
        st.markdown("#### 📊 Diagnóstico de Ausentismo e Impacto en Capacidad")
        st.caption("Permite a la gerencia identificar qué equipos y servicios están afectando más la capacidad operativa.")

        c_g1, c_g2 = st.columns(2)

        # 1. Agrupado por Supervisor
        resumen_sup = df_view[df_view["Ya Inició"]].groupby("Supervisor").agg(
            Programados=("BP", "count"),
            Conectados=("Esta Conectado", "sum"),
            Ausentes=("Esta Conectado", lambda x: (~x).sum()),
            Horas_Perdidas=("Duracion Horas", lambda h: h[~df_view.loc[h.index, "Esta Conectado"]].sum())
        ).reset_index()

        resumen_sup["% Ausentismo"] = (resumen_sup["Ausentes"] / resumen_sup["Programados"] * 100.0).round(1)
        resumen_sup = resumen_sup.sort_values(by=["Ausentes", "Horas_Perdidas"], ascending=[False, False])

        with c_g1:
            fig_sup = px.bar(
                resumen_sup.head(10),
                x="Supervisor",
                y="Ausentes",
                color="% Ausentismo",
                title="Top 10 Supervisores con Mayor Ausentismo (Personas)",
                color_continuous_scale="Reds",
                text="Ausentes"
            )
            fig_sup.update_layout(height=380, margin=dict(l=20, r=20, t=40, b=80))
            st.plotly_chart(fig_sup, use_container_width=True)

        with c_g2:
            # 2. Distribución de Tipos de Ausencia
            aus_con_motivo = df_view[df_view["Ya Inició"] & (~df_view["Esta Conectado"])]
            conteo_motivos = aus_con_motivo["Justificación"].value_counts().reset_index()
            conteo_motivos.columns = ["Motivo", "Cantidad"]

            fig_mot = px.pie(
                conteo_motivos,
                names="Motivo",
                values="Cantidad",
                title="Distribución de Causas de Ausentismo",
                hole=0.45,
                color_discrete_sequence=px.colors.qualitative.Bold
            )
            fig_mot.update_layout(height=380, margin=dict(l=20, r=20, t=40, b=20))
            st.plotly_chart(fig_mot, use_container_width=True)

        st.markdown("##### 📑 Matriz Detallada por Supervisor:")
        st.dataframe(
            resumen_sup,
            use_container_width=True,
            hide_index=True,
            column_config={
                "% Ausentismo": st.column_config.NumberColumn("% Ausentismo", format="%.1f%%"),
                "Horas_Perdidas": st.column_config.NumberColumn("Horas Perdidas", format="%.1f h")
            }
        )

    # ─────────────────────────────────────────────────────────────────────────
    # SUBPESTAÑA 4: EXPORTACIÓN OFICIAL A EXCEL
    # ─────────────────────────────────────────────────────────────────────────
    with tab_export:
        st.markdown("#### 📥 Exportar Reporte Consolidado de Ausentismo")
        st.markdown(
            """
            Descarga un libro Excel listo para enviar a Operaciones, WFM y Gerencia, 
            con el desglose individual por asesor, causales registradas y resumen consolidado por supervisor.
            """
        )

        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df_view.to_excel(writer, sheet_name="Detalle Ausentismo", index=False)
            resumen_sup.to_excel(writer, sheet_name="Resumen Supervisores", index=False)

        excel_data = output.getvalue()
        file_name = f"Reporte_Ausentismo_Genesys_{fecha_str}.xlsx"

        st.download_button(
            label="📊 Descargar Reporte de Ausentismo (Excel)",
            data=excel_data,
            file_name=file_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
            key="btn_dl_ausentismo"
        )
