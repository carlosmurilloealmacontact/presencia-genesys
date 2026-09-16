"""
Módulo Autónomo e Independiente: Zendesk Support Engine
Encapsula al 100% la funcionalidad de reportería y monitoreo operativo de Zendesk para Almacontact.

Diseñado para integrarse en Radar Genesys sin alterar ninguna línea de los motores existentes
(live_engine, gtr_engine, capacidad_engine, ausentismo_engine, salesforce_b2b_engine).

Sub-módulos integrados:
1. 📅 Productividad Diaria y Fechas
2. ⏳ Antigüedad del Backlog (Imágenes 1, 2 y 3)
3. ⏱️ Cortes Intradía por Asesor (Imagen 4)
4. 🚨 Backlog en Cola (Monitoreo en Vivo)
5. 👤 Desempeño y Jerarquía de Asesores
6. 🏷️ Tipología de Gestión
7. ⏱️ Tiempos de Servicio (SLAs)
8. 📊 Volumen Histórico
"""

import os
import sys
import json
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# Rutas relativas del repositorio
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data" / "zendesk"

RANGOS_ORDEN = ["<48H", ">48H<=15DIAS", ">15Y<=30DIAS", ">30DIAS"]


def formatear_colombia_dt(val) -> str:
    """Convierte cualquier timestamp UTC a formato legible en hora de Colombia (UTC-5 / America/Bogota)."""
    try:
        if pd.isna(val) or val is None or str(val).strip() in ("", "nan", "NaT"):
            return ""
        ts = pd.to_datetime(val, utc=True)
        ts_col = ts.tz_convert("America/Bogota")
        return ts_col.strftime("%d/%m/%Y %I:%M %p")
    except Exception:
        return str(val)


# ── HELPERS DE SOCIO MAESTRO Y ANTIGÜEDAD ─────────────────────────────────────

def normalizar(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.strip().upper().split())


@st.cache_data(ttl=600, show_spinner=False)
def cargar_roster_maestro() -> pd.DataFrame:
    socio_file = DATA_DIR / "servicios_socio_maestro.csv"
    if not socio_file.exists():
        return pd.DataFrame()
    try:
        d2 = pd.read_csv(socio_file, low_memory=False)
        m = d2[["name", "jefe", "coordinador", "servicio"]].dropna(subset=["name"]).drop_duplicates(subset=["name"]).copy()
        m["norm_name"] = m["name"].apply(normalizar)
        m["servicio"] = m["servicio"].fillna("Por Definir")
        m["jefe"] = m["jefe"].fillna("Por Asignar")
        m["coordinador"] = m["coordinador"].fillna("Por Asignar")
        return m
    except Exception:
        return pd.DataFrame()


@st.cache_data(ttl=600, show_spinner=False)
def cargar_condicion_antiguedad() -> dict:
    estados_file = DATA_DIR / "estados_asesores.csv"
    if not estados_file.exists():
        return {}
    try:
        df_est = pd.read_csv(estados_file, low_memory=False).dropna(subset=["Asesor"])
        return {
            normalizar(r["Asesor"]): ("NUEVO" if pd.notna(r.get("Antiguedad_Dias")) and r.get("Antiguedad_Dias") <= 90 else "ANTIGUO")
            for _, r in df_est.iterrows()
        }
    except Exception:
        return {}


def enriquecer_con_socio(df: pd.DataFrame, solo_almacontact: bool = True) -> pd.DataFrame:
    """Filtra asesores de Almacontact y cruza con la jerarquía de Socio Maestro y condición de antigüedad."""
    if df is None or df.empty:
        return df

    df_out = df.copy()

    if solo_almacontact and "TICKET_ASSIGNEE_PRIMARY_EMAIL" in df_out.columns:
        is_alma = df_out["TICKET_ASSIGNEE_PRIMARY_EMAIL"].str.contains(
            r"almacontact|\.alma@|@almacontact", case=False, na=False
        )
        df_out = df_out[is_alma].copy()

    maestro = cargar_roster_maestro()
    cond_map = cargar_condicion_antiguedad()

    cache_matches = {}
    socio_tuples = []
    if not maestro.empty:
        socio_tuples = list(zip(
            maestro["norm_name"].str.lower().str.replace(" ", "", regex=False),
            maestro["name"],
            maestro["jefe"],
            maestro["coordinador"],
            maestro["servicio"]
        ))

    def obtener_jerarquia(email):
        if email in cache_matches:
            return cache_matches[email]

        prefix = str(email).split("@")[0].split(".")[0].lower().strip()
        if len(prefix) < 4:
            res = (str(email).split("@")[0], "Sin Supervisor", "Sin Coordinador", "Almacontact General")
            cache_matches[email] = res
            return res

        pref_len = len(prefix)
        pref_start = prefix[:5]
        pref_end = prefix[-3:]
        for clean_n, nom, jef, coo, srv in socio_tuples:
            if prefix in clean_n or (pref_len > 6 and (pref_start in clean_n and pref_end in clean_n)):
                res = (nom, jef, coo, srv)
                cache_matches[email] = res
                return res

        nombre_legible = prefix.title()
        res = (nombre_legible, "Sin Supervisor Asignado", "Sin Coordinador Asignado", "Almacontact Operación")
        cache_matches[email] = res
        return res

    if "TICKET_ASSIGNEE_PRIMARY_EMAIL" in df_out.columns:
        jerarquia = df_out["TICKET_ASSIGNEE_PRIMARY_EMAIL"].apply(obtener_jerarquia)
        df_out["Nombre_Asesor"] = [j[0] for j in jerarquia]
        df_out["Supervisor"] = [j[1] for j in jerarquia]
        df_out["Coordinador"] = [j[2] for j in jerarquia]
        df_out["Servicio"] = [j[3] for j in jerarquia]
    else:
        if "Nombre_Asesor" not in df_out.columns:
            df_out["Nombre_Asesor"] = "Sin Asignar"
        if "Supervisor" not in df_out.columns:
            df_out["Supervisor"] = "Sin Supervisor"
        if "Coordinador" not in df_out.columns:
            df_out["Coordinador"] = "Sin Coordinador"
        if "Servicio" not in df_out.columns:
            df_out["Servicio"] = "Almacontact Operación"

    df_out["Condicion"] = df_out["Nombre_Asesor"].apply(
        lambda nom: cond_map.get(normalizar(nom), "ANTIGUO")
    )

    return df_out


# ── HELPERS DE ANTIGÜEDAD Y CORTES INTRADÍA ───────────────────────────────────

def clasificar_antiguedad_ticket(horas: float, dias: float) -> str:
    if horas < 48:
        return "<48H"
    elif dias <= 15:
        return ">48H<=15DIAS"
    elif dias <= 30:
        return ">15Y<=30DIAS"
    else:
        return ">30DIAS"


def procesar_antiguedad_backlog(df_backlog: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if df_backlog is None or df_backlog.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    df = df_backlog.copy()
    now_utc = pd.Timestamp.now(tz=timezone.utc)

    df["ts_created"] = pd.to_datetime(df["created_at"], utc=True)
    df["horas_antiguedad"] = (now_utc - df["ts_created"]).dt.total_seconds() / 3600.0
    df["dias_antiguedad"] = df["horas_antiguedad"] / 24.0

    df["Rango_Antiguedad"] = [
        clasificar_antiguedad_ticket(h, d) for h, d in zip(df["horas_antiguedad"], df["dias_antiguedad"])
    ]

    map_estados = {
        "new": "Nuevo",
        "open": "Abierto",
        "pending": "Pendiente",
        "hold": "En espera",
        "solved": "Resuelto",
        "closed": "Cerrado"
    }
    df["Estado"] = df["status"].map(map_estados).fillna(df["status"].str.title())
    df["Servicio"] = df["grupo"].fillna("Sin Grupo")

    # Tabla 1: Matriz de Porcentajes
    ct_counts = pd.crosstab(df["Servicio"], df["Rango_Antiguedad"])
    for r in RANGOS_ORDEN:
        if r not in ct_counts.columns:
            ct_counts[r] = 0
    ct_counts = ct_counts[RANGOS_ORDEN]

    ct_pct = ct_counts.div(ct_counts.sum(axis=1), axis=0) * 100.0
    total_col_counts = ct_counts.sum(axis=0)
    total_fabrica_pct = (total_col_counts / total_col_counts.sum()) * 100.0 if total_col_counts.sum() > 0 else total_col_counts

    matriz_pct = ct_pct.copy()
    matriz_pct.loc["FABRICA"] = total_fabrica_pct
    matriz_pct["FABRICA"] = 100.0
    matriz_display = matriz_pct.map(lambda x: f"{x:.1f}%".replace(".", ","))
    matriz_display.index.name = "SERVICIO"
    matriz_display.reset_index(inplace=True)

    # Tabla 2: Desglose por Servicio y Estado
    filas_desglose = []
    servicios_unicos = sorted(list(df["Servicio"].unique()))
    total_general_fabrica = len(df)

    for srv in servicios_unicos:
        df_srv = df[df["Servicio"] == srv]
        total_srv = len(df_srv)

        fila_srv = {
            "SERVICIO": srv,
            "SERVICIO_PADRE": srv,
            "TIPO_FILA": "SERVICIO"
        }
        for r in RANGOS_ORDEN:
            c = (df_srv["Rango_Antiguedad"] == r).sum()
            p = (c / total_srv * 100.0) if total_srv > 0 else 0.0
            fila_srv[f"{r} CASOS"] = c
            fila_srv[f"{r} % ANT."] = f"{p:.1f}%".replace(".", ",")
        fila_srv["Total CASOS"] = total_srv
        filas_desglose.append(fila_srv)

        estados_srv = df_srv["Estado"].value_counts().index.tolist()
        for est in estados_srv:
            df_est = df_srv[df_srv["Estado"] == est]
            total_est = len(df_est)
            fila_est = {
                "SERVICIO": f"    {est}",
                "SERVICIO_PADRE": srv,
                "TIPO_FILA": "ESTADO"
            }
            for r in RANGOS_ORDEN:
                c = (df_est["Rango_Antiguedad"] == r).sum()
                p = (c / total_est * 100.0) if total_est > 0 else 0.0
                fila_est[f"{r} CASOS"] = c
                fila_est[f"{r} % ANT."] = f"{p:.1f}%".replace(".", ",")
            fila_est["Total CASOS"] = total_est
            filas_desglose.append(fila_est)

    fila_total = {
        "SERVICIO": "TOTAL FABRICA",
        "SERVICIO_PADRE": "TOTAL FABRICA",
        "TIPO_FILA": "TOTAL"
    }
    for r in RANGOS_ORDEN:
        c = (df["Rango_Antiguedad"] == r).sum()
        p = (c / total_general_fabrica * 100.0) if total_general_fabrica > 0 else 0.0
        fila_total[f"{r} CASOS"] = c
        fila_total[f"{r} % ANT."] = f"{p:.1f}%".replace(".", ",")
    fila_total["Total CASOS"] = total_general_fabrica
    filas_desglose.append(fila_total)

    df_desglose = pd.DataFrame(filas_desglose)
    return df, matriz_display, df_desglose


@st.cache_data(ttl=600, show_spinner=False)
def cargar_bundle_zendesk() -> dict:
    """Carga y pre-enriquece todos los datasets de Zendesk en memoria para filtrado ultrarrápido (<50ms)."""
    file_asesores = DATA_DIR / "asesores_tipologia_metricas.csv"
    file_diario = DATA_DIR / "productividad_diaria_fechas.csv"
    file_prod_hoy = DATA_DIR / "productividad_hoy_en_vivo.csv"
    file_b_vivo = DATA_DIR / "backlog_en_vivo.csv"

    df_raw = pd.read_csv(file_asesores) if file_asesores.exists() else None
    df_diario_raw = pd.read_csv(file_diario) if file_diario.exists() else None

    # Integrar productividad hoy en vivo si existe
    if file_prod_hoy.exists():
        try:
            df_hoy = pd.read_csv(file_prod_hoy)
            if not df_hoy.empty and "TICKET_ASSIGNEE_PRIMARY_EMAIL" in df_hoy.columns and "Tipo_de_Gestion" in df_hoy.columns:
                grp_cols = ["Fecha", "grupo", "TICKET_ASSIGNEE_PRIMARY_EMAIL", "Tipo_de_Gestion"] if "grupo" in df_hoy.columns else ["Fecha", "TICKET_ASSIGNEE_PRIMARY_EMAIL", "Tipo_de_Gestion"]
                df_hoy_agg = df_hoy.groupby(grp_cols).size().reset_index(name="Recuento_Tickets")
                df_hoy_agg["Fecha_Timestamp"] = df_hoy_agg["Fecha"] + " 00:00:00"
                df_hoy_agg["Tipo_de_Gestion_RAW"] = df_hoy_agg["Tipo_de_Gestion"]

                if df_diario_raw is not None and not df_diario_raw.empty:
                    fechas_hoy = df_hoy_agg["Fecha"].unique()
                    df_diario_raw = df_diario_raw[~df_diario_raw["Fecha"].isin(fechas_hoy)]
                    df_diario_raw = pd.concat([df_diario_raw, df_hoy_agg], ignore_index=True)
                else:
                    df_diario_raw = df_hoy_agg
        except Exception:
            pass

    # Pre-enriquecer con jerarquía Socio Maestro
    df_raw_enr_alma = enriquecer_con_socio(df_raw, solo_almacontact=True) if df_raw is not None else None
    df_raw_enr_todos = enriquecer_con_socio(df_raw, solo_almacontact=False) if df_raw is not None else None

    df_diario_enr_alma = enriquecer_con_socio(df_diario_raw, solo_almacontact=True) if df_diario_raw is not None else None
    df_diario_enr_todos = enriquecer_con_socio(df_diario_raw, solo_almacontact=False) if df_diario_raw is not None else None

    # Pre-procesar backlog y antigüedad si existe
    df_b_raw = pd.read_csv(file_b_vivo) if file_b_vivo.exists() else None
    df_b_full, m_resumen, d_desglose = procesar_antiguedad_backlog(df_b_raw) if df_b_raw is not None else (pd.DataFrame(), pd.DataFrame(), pd.DataFrame())

    # Pre-calcular rango de fechas globales disponibles
    fechas_disp = []
    if df_diario_raw is not None and "Fecha" in df_diario_raw.columns:
        fechas_disp.extend([str(f)[:10] for f in df_diario_raw["Fecha"].dropna().unique() if str(f) != "Sin Fecha" and len(str(f)) >= 10])

    fechas_sorted = sorted(list(set(fechas_disp)))
    f_min_def = datetime.strptime(fechas_sorted[0], "%Y-%m-%d").date() if fechas_sorted else datetime.now().date()
    f_max_def = datetime.strptime(fechas_sorted[-1], "%Y-%m-%d").date() if fechas_sorted else datetime.now().date()

    # Pre-enriquecer productividad hoy si existe
    df_p_raw = pd.read_csv(file_prod_hoy) if file_prod_hoy.exists() else None
    df_enr_hoy = enriquecer_con_socio(df_p_raw, solo_almacontact=False) if df_p_raw is not None and not df_p_raw.empty else pd.DataFrame()

    # Pre-cargar demanda diaria y balance de colas
    file_demanda = DATA_DIR / "demanda_diaria_colas.csv"
    df_demanda = pd.read_csv(file_demanda) if file_demanda.exists() else None

    return {
        "df_raw_enr_alma": df_raw_enr_alma,
        "df_raw_enr_todos": df_raw_enr_todos,
        "df_diario_enr_alma": df_diario_enr_alma,
        "df_diario_enr_todos": df_diario_enr_todos,
        "df_b_raw": df_b_raw,
        "df_b_full": df_b_full,
        "m_resumen": m_resumen,
        "d_desglose": d_desglose,
        "df_p_raw": df_p_raw,
        "df_enr_hoy": df_enr_hoy,
        "df_demanda": df_demanda,
        "f_min_def": f_min_def,
        "f_max_def": f_max_def,
    }


def procesar_cortes_intradia(df_prod: pd.DataFrame, cortes_hora: List[int] = None) -> pd.DataFrame:
    if df_prod is None or df_prod.empty:
        return pd.DataFrame()

    if not cortes_hora:
        cortes_hora = [8, 10, 14]

    df = df_prod.copy()
    df = enriquecer_con_socio(df, solo_almacontact=False)

    df["ts_local"] = pd.to_datetime(df["updated_at"], utc=True).dt.tz_convert("America/Bogota")
    df["hora_local"] = df["ts_local"].dt.hour + (df["ts_local"].dt.minute / 60.0)

    def formato_corte(h: int) -> str:
        if h < 12:
            return f"{h}:00:00 a. m."
        elif h == 12:
            return "12:00:00 m."
        else:
            return f"{h-12}:00:00 p. m."

    nombres_cortes = [formato_corte(h) for h in cortes_hora]

    for h, nombre_corte in zip(cortes_hora, nombres_cortes):
        df[nombre_corte] = (df["hora_local"] <= h).astype(int)

    df["TAG_GRUPO"] = df["grupo"].fillna("Sin Grupo")
    df["CONDICION"] = df.get("Condicion", "ANTIGUO").fillna("ANTIGUO")
    df["AGENTE"] = df["Nombre_Asesor"].fillna(df["TICKET_ASSIGNEE_PRIMARY_EMAIL"]).fillna("Sin Asignar")

    filas_intradia = []
    grupos_unicos = sorted(list(df["TAG_GRUPO"].unique()))

    cols_sum = nombres_cortes + ["Total general"]
    df["Total general"] = 1
    total_general_acum = {c: df[c].sum() for c in cols_sum}

    for grp in grupos_unicos:
        df_g = df[df["TAG_GRUPO"] == grp]
        subtot_g = {"TAG / AGENTE": grp, "TIPO": "GRUPO"}
        for c in cols_sum:
            subtot_g[c] = df_g[c].sum()
        filas_intradia.append(subtot_g)

        for cond in ["ANTIGUO", "NUEVO"]:
            df_gc = df_g[df_g["CONDICION"] == cond]
            if df_gc.empty:
                continue

            subtot_c = {"TAG / AGENTE": f"  {cond}", "TIPO": "CONDICION"}
            for c in cols_sum:
                subtot_c[c] = df_gc[c].sum()
            filas_intradia.append(subtot_c)

            asesores_gc = df_gc.groupby("AGENTE")[cols_sum].sum().sort_values(by="Total general", ascending=False)
            for as_name, as_row in asesores_gc.iterrows():
                fila_as = {"TAG / AGENTE": f"    {as_name}", "TIPO": "AGENTE"}
                for c in cols_sum:
                    val = as_row[c]
                    fila_as[c] = val if val > 0 else ""
                filas_intradia.append(fila_as)

    fila_total = {"TAG / AGENTE": "Total general", "TIPO": "TOTAL"}
    for c in cols_sum:
        fila_total[c] = total_general_acum[c]
    filas_intradia.append(fila_total)

    return pd.DataFrame(filas_intradia)


# ── RENDERIZADOR PRINCIPAL DEL MÓDULO ZENDESK ─────────────────────────────────

def render_tab_zendesk(email_usuario: str = ""):
    """Renderiza el módulo integral de Zendesk dentro de Radar Genesys."""
    st.markdown("### 🎫 Zendesk Support — Reportería y Control Operativo Almacontact")
    st.caption("Extracción en vivo: Antigüedad de Backlog, Cortes Intradía, Productividad Diaria, Tipologías de Gestión y SLAs.")

    # Cargar bundle optimizado y pre-enriquecido en memoria (instantáneo < 1ms tras carga inicial)
    bundle = cargar_bundle_zendesk()
    file_prod_hoy = DATA_DIR / "productividad_hoy_en_vivo.csv"
    file_b_vivo = DATA_DIR / "backlog_en_vivo.csv"
    file_diario = DATA_DIR / "productividad_diaria_fechas.csv"
    file_demanda = DATA_DIR / "demanda_diaria_colas.csv"

    # Barra superior de estado de sincronización (Hora Colombia / UTC-5)
    latest_mtime = 0
    for f_chk in [file_b_vivo, file_prod_hoy, file_diario, file_demanda]:
        if f_chk.exists():
            latest_mtime = max(latest_mtime, f_chk.stat().st_mtime)

    col_h1, col_h2 = st.columns([3, 1.2])
    with col_h1:
        if latest_mtime > 0:
            import zoneinfo
            try:
                tz_col = zoneinfo.ZoneInfo("America/Bogota")
                dt_sync = datetime.fromtimestamp(latest_mtime, tz=zoneinfo.ZoneInfo("UTC")).astimezone(tz_col)
                hora_s = dt_sync.strftime('%d/%m/%Y %I:%M:%S %p')
            except Exception:
                hora_s = datetime.fromtimestamp(latest_mtime).strftime('%d/%m/%Y %I:%M:%S %p')
            st.info(f"🕒 **Última sincronización Zendesk:** `{hora_s}` *(Hora Colombia / UTC-5)* | **Colas activas:** 15 grupos AMC | **Estado:** Operativo en Vivo")
        else:
            st.info("🕒 Estado de Zendesk: Datos históricos cargados.")

    with col_h2:
        local_sync_script = Path(r"C:\Proyecto 3.0\Zendesk\zendesk_sync_service.py")
        if local_sync_script.exists():
            if st.button("🔄 Sincronizar en Vivo", type="primary", use_container_width=True, help="Ejecuta en segundo plano una consulta a Zendesk Support para actualizar el backlog y los casos resueltos hoy."):
                with st.status("🔄 Sincronizando Zendesk...", expanded=True) as status_box:
                    st.write("Iniciando sesión segura...")
                    import subprocess
                    try:
                        proc = subprocess.Popen(
                            [sys.executable, "-u", str(local_sync_script)],
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            cwd=str(local_sync_script.parent)
                        )
                        res_data = None
                        for line in iter(proc.stdout.readline, ""):
                            lc = line.strip()
                            if "Backlog en Vivo" in lc:
                                st.write("📥 Consultando Backlog activo (15 colas)...")
                            elif "Productividad resuelta" in lc:
                                st.write("📊 Consultando Resueltos hoy...")
                            elif "RESULTADO_JSON:" in lc:
                                try:
                                    res_data = json.loads(lc.split("RESULTADO_JSON:")[-1].strip())
                                except Exception:
                                    pass
                        proc.wait(timeout=120)

                        if res_data and res_data.get("status") == "ok":
                            import shutil
                            for src_f in (local_sync_script.parent / "data" / "processed").glob("*.*"):
                                shutil.copy2(src_f, DATA_DIR / src_f.name)

                            # Regenerar demanda diaria
                            try:
                                from generar_demanda_diaria import generar_demanda_diaria
                                generar_demanda_diaria()
                            except Exception:
                                pass

                            cargar_bundle_zendesk.clear()
                            bc = res_data.get("backlog_count", 0)
                            sc = res_data.get("solved_count", 0)
                            status_box.update(label=f"✅ Listo: {bc} backlog | {sc} resueltos", state="complete", expanded=False)
                            st.toast(f"¡Sincronizado! {bc} en cola | {sc} resueltos hoy")
                            time.sleep(0.5)
                            st.rerun()
                        else:
                            err = res_data.get("error") if res_data else f"Salida: {proc.returncode}"
                            status_box.update(label=f"❌ Error: {err}", state="error")
                    except Exception as ex:
                        status_box.update(label=f"❌ Error: {ex}", state="error")
        else:
            # Modo Cloud (Streamlit Community Cloud): Botón siempre visible para refresco y limpieza de caché
            if st.button("🔄 Refrescar Datos Zendesk", type="primary", use_container_width=True, help="Limpia la memoria caché de Streamlit y recarga las métricas con los últimos datos sincronizados."):
                cargar_bundle_zendesk.clear()
                st.cache_data.clear()
                st.toast("✅ Datos de Zendesk recargados exitosamente.")
                time.sleep(0.3)
                st.rerun()
            st.caption("☁️ Modo Cloud: Limpia caché y sincroniza vista.")

    # ── FILTROS SUPERIORES DE OPERACIÓN (EN MEMORIA / SIN LATENCIA) ──────────
    st.markdown("#### 🎯 Filtros de Operación y Segmentación")

    c_f1, c_f2, c_f3, c_f4 = st.columns([1.5, 1.2, 1.2, 1.2])

    f_min_def = bundle["f_min_def"]
    f_max_def = bundle["f_max_def"]

    with c_f1:
        sel_fechas = st.date_input(
            "📅 Rango de Fechas:",
            value=(f_min_def, f_max_def),
            min_value=f_min_def,
            max_value=f_max_def,
            key="zd_sel_fechas",
            help="Filtra los casos resueltos por fecha de resolución."
        )

    # Procesar rango seleccionado
    if isinstance(sel_fechas, (tuple, list)) and len(sel_fechas) == 2:
        fecha_ini, fecha_fin = sel_fechas
    elif isinstance(sel_fechas, (tuple, list)) and len(sel_fechas) == 1:
        fecha_ini = fecha_fin = sel_fechas[0]
    else:
        fecha_ini = fecha_fin = sel_fechas

    c_sub1, c_sub2 = st.columns([1.2, 3.8])
    with c_sub1:
        solo_alma = st.checkbox("Solo asesores Almacontact", value=True, key="zd_solo_alma")

    # Obtener datasets enriquecidos del bundle en memoria
    df_enriquecido = bundle["df_raw_enr_alma"] if solo_alma else bundle["df_raw_enr_todos"]
    df_diario_enr = bundle["df_diario_enr_alma"] if solo_alma else bundle["df_diario_enr_todos"]

    sel_servicio = "Todos"
    sel_coord = "Todos"
    sel_sup = "Todos"
    sel_asesor = "Todos"

    if df_enriquecido is not None and not df_enriquecido.empty:
        with c_f2:
            servicios = ["Todos"] + sorted(list(df_enriquecido["Servicio"].unique()))
            sel_servicio = st.selectbox("🏢 Servicio:", servicios, index=0, key="zd_sel_srv")
        df_step1 = df_enriquecido if sel_servicio == "Todos" else df_enriquecido[df_enriquecido["Servicio"] == sel_servicio]

        with c_f3:
            coordinadores = ["Todos"] + sorted(list(df_step1["Coordinador"].unique()))
            sel_coord = st.selectbox("👔 Coordinador:", coordinadores, index=0, key="zd_sel_coord")
        df_step2 = df_step1 if sel_coord == "Todos" else df_step1[df_step1["Coordinador"] == sel_coord]

        with c_f4:
            supervisores = ["Todos"] + sorted(list(df_step2["Supervisor"].unique()))
            sel_sup = st.selectbox("🧑‍💼 Supervisor:", supervisores, index=0, key="zd_sel_sup")
        df_step3 = df_step2 if sel_sup == "Todos" else df_step2[df_step2["Supervisor"] == sel_sup]

        with c_sub2:
            asesores_disp = ["Todos"] + sorted(list(df_step3["Nombre_Asesor"].unique()))
            sel_asesor = st.selectbox("👤 Asesor Específico:", asesores_disp, index=0, key="zd_sel_asesor")
        df_filtrado = df_step3 if sel_asesor == "Todos" else df_step3[df_step3["Nombre_Asesor"] == sel_asesor]
    else:
        df_filtrado = None

    # Filtrar df_diario en memoria por fecha y jerarquía
    if df_diario_enr is not None and not df_diario_enr.empty:
        d_f = df_diario_enr.copy()
        if fecha_ini and fecha_fin:
            f_ini_s = fecha_ini.strftime("%Y-%m-%d")
            f_fin_s = fecha_fin.strftime("%Y-%m-%d")
            d_f = d_f[(d_f["Fecha"] >= f_ini_s) & (d_f["Fecha"] <= f_fin_s)]
        if sel_servicio != "Todos":
            d_f = d_f[d_f["Servicio"] == sel_servicio]
        if sel_coord != "Todos":
            d_f = d_f[d_f["Coordinador"] == sel_coord]
        if sel_sup != "Todos":
            d_f = d_f[d_f["Supervisor"] == sel_sup]
        if sel_asesor != "Todos":
            d_f = d_f[d_f["Nombre_Asesor"] == sel_asesor]
        df_diario_filtrado = d_f
    else:
        df_diario_filtrado = None

    # Checkbox para excluir autorizaciones de supervisor de la productividad operativa
    excluir_auth = st.checkbox(
        "🛡️ Excluir colas de Autorización de Supervisor de la productividad de asesores",
        value=True,
        help="Las colas 'Autorización Supervisor AMC' y 'Autorización Supervisor HVC AMC ES' corresponden a aprobaciones de códigos de involuntario gestionadas por supervisores. Al mantener esta opción activa, no se mezclarán con la productividad operativa de los asesores.",
        key="zd_excluir_auth"
    )

    df_solo_autorizaciones = None
    if df_diario_filtrado is not None and not df_diario_filtrado.empty and "grupo" in df_diario_filtrado.columns:
        is_auth_mask = df_diario_filtrado["grupo"].str.contains("Autorización|Supervisor|Autorizacion", case=False, na=False)
        df_solo_autorizaciones = df_diario_filtrado[is_auth_mask].copy()
        if excluir_auth:
            df_diario_filtrado = df_diario_filtrado[~is_auth_mask].copy()

    # Sub-navegación por pestañas de Zendesk
    tab_zd_diario, tab_zd_demanda, tab_zd_antiguedad, tab_zd_intradia, tab_zd_backlog, tab_zd_asesores, tab_zd_tipologia, tab_zd_tiempos, tab_zd_volumen, tab_zd_autorizaciones = st.tabs([
        "📅 Productividad Diaria",
        "📥 Demanda Diaria (Nuevos)",
        "⏳ Antigüedad del Backlog",
        "⏱️ Cortes Intradía",
        "🚨 Backlog en Cola",
        "👤 Desempeño Asesores",
        "🏷️ Tipología de Gestión",
        "⏱️ SLAs y Tiempos",
        "📊 Volumen Histórico",
        "🛡️ Autorizaciones Supervisor"
    ])

    # ---------------------------------------------------------------------
    # SUBMÓDULO 1: PRODUCTIVIDAD DIARIA
    # ---------------------------------------------------------------------
    with tab_zd_diario:
        st.subheader("📅 Productividad Diaria por Fecha de Resolución")
        st.caption("Casos resueltos por día, cruzados por Servicio, Supervisor y Asesor de Almacontact.")

        if df_diario_filtrado is not None and not df_diario_filtrado.empty:
            total_periodo = df_diario_filtrado["Recuento_Tickets"].sum()
            dias_activos = df_diario_filtrado[df_diario_filtrado["Fecha"] != "Sin Fecha"]["Fecha"].nunique()
            prom_dia = (total_periodo / dias_activos) if dias_activos > 0 else total_periodo
            asesores_activos = df_diario_filtrado["Nombre_Asesor"].nunique()

            kd1, kd2, kd3, kd4 = st.columns(4)
            kd1.metric("Tickets en Periodo", f"{total_periodo:,.0f}")
            kd2.metric("Días con Operación", f"{dias_activos}")
            kd3.metric("Promedio Casos / Día", f"{prom_dia:,.1f}")
            kd4.metric("Asesores Productivos", f"{asesores_activos:,}")

            st.markdown("---")
            c_d1, c_d2 = st.columns([3, 2])

            with c_d1:
                st.subheader("📈 Evolución Diaria de Casos Resueltos")
                df_dia_grp = df_diario_filtrado[df_diario_filtrado["Fecha"] != "Sin Fecha"].groupby(["Fecha", "Servicio"])["Recuento_Tickets"].sum().reset_index()
                fig_dia = px.bar(
                    df_dia_grp,
                    x="Fecha",
                    y="Recuento_Tickets",
                    color="Servicio",
                    barmode="stack",
                    text="Recuento_Tickets",
                    title="Casos Resueltos por Día y Servicio"
                )
                fig_dia.update_layout(height=420, margin=dict(l=10, r=10))
                st.plotly_chart(fig_dia, use_container_width=True)

            with c_d2:
                st.subheader("🏆 Top Asesores en el Periodo")
                df_as_periodo = df_diario_filtrado.groupby(["Nombre_Asesor", "Supervisor", "Servicio"])["Recuento_Tickets"].sum().reset_index().sort_values(by="Recuento_Tickets", ascending=False).head(12)
                fig_top_p = px.bar(
                    df_as_periodo.sort_values(by="Recuento_Tickets", ascending=True),
                    x="Recuento_Tickets",
                    y="Nombre_Asesor",
                    orientation="h",
                    color="Supervisor",
                    text="Recuento_Tickets",
                    title="Casos Resueltos por Asesor"
                )
                fig_top_p.update_layout(height=420, yaxis=dict(title=""), margin=dict(l=10, r=10))
                st.plotly_chart(fig_top_p, use_container_width=True)

            st.subheader("📋 Detalle de Productividad Diaria por Asesor y Tipología")
            st.dataframe(
                df_diario_filtrado[["Fecha", "Nombre_Asesor", "TICKET_ASSIGNEE_PRIMARY_EMAIL", "Supervisor", "Servicio", "Tipo_de_Gestion", "Recuento_Tickets"]]
                .rename(columns={
                    "TICKET_ASSIGNEE_PRIMARY_EMAIL": "Correo",
                    "Nombre_Asesor": "Asesor",
                    "Tipo_de_Gestion": "Tipología",
                    "Recuento_Tickets": "Casos Resueltos"
                })
                .sort_values(by="Casos Resueltos", ascending=False),
                use_container_width=True
            )
        else:
            st.info("No hay registros diarios disponibles para los filtros seleccionados.")

    # ---------------------------------------------------------------------
    # SUBMÓDULO: DEMANDA DIARIA (CASOS NUEVOS INGRESADOS POR COLA)
    # ---------------------------------------------------------------------
    with tab_zd_demanda:
        st.subheader("📥 Demanda Diaria por Cola (Casos Nuevos Ingresados en Hora Col UTC-5)")
        st.caption("Monitorea la cantidad exacta de casos nuevos que ingresan día a día a cada cola de atención y compáralos contra la capacidad de resolución (Inflow vs Outflow).")

        df_dem = bundle.get("df_demanda")
        if df_dem is not None and not df_dem.empty:
            df_dem_f = df_dem.copy()
            if fecha_ini and fecha_fin:
                f_ini_s = fecha_ini.strftime("%Y-%m-%d")
                f_fin_s = fecha_fin.strftime("%Y-%m-%d")
                df_dem_f = df_dem_f[(df_dem_f["Fecha"] >= f_ini_s) & (df_dem_f["Fecha"] <= f_fin_s)]

            col_dem1, col_dem2 = st.columns([2, 1])
            with col_dem1:
                colas_disp = ["Todas las Colas"] + sorted(list(df_dem_f["grupo"].unique()))
                sel_cola_dem = st.selectbox("Filtrar Cola:", colas_disp, key="zd_dem_cola")
            with col_dem2:
                vista_dem = st.radio("Métrica Principal:", ["Casos Nuevos (Demanda)", "Balance (Entradas vs Resueltos)"], horizontal=True, key="zd_dem_vista")

            if sel_cola_dem != "Todas las Colas":
                df_dem_f = df_dem_f[df_dem_f["grupo"] == sel_cola_dem]

            # KPIs
            tot_nuevos = df_dem_f["Casos_Nuevos"].sum()
            tot_resueltos = df_dem_f["Casos_Resueltos"].sum()
            balance_neto = df_dem_f["Balance_Neto"].sum()
            dias_dem = df_dem_f["Fecha"].nunique()
            prom_ingresos_dia = (tot_nuevos / dias_dem) if dias_dem > 0 else 0

            k1, k2, k3, k4 = st.columns(4)
            k1.metric("📥 Total Casos Nuevos", f"{tot_nuevos:,.0f}", f"En periodo seleccionado")
            k2.metric("📈 Promedio Nuevos / Día", f"{prom_ingresos_dia:,.1f}", f"{dias_dem} días activos")
            k3.metric("📤 Casos Resueltos", f"{tot_resueltos:,.0f}", f"Productividad en periodo")
            k4.metric(
                "⚖️ Balance Neto de Backlog",
                f"{balance_neto:+,.0f}",
                "Creció backlog" if balance_neto > 0 else "Desahogo de cola",
                delta_color="inverse" if balance_neto > 0 else "normal"
            )

            st.markdown("---")

            c_g1, c_g2 = st.columns([3, 2])
            with c_g1:
                st.subheader("📈 Evolución de Casos Nuevos Ingresados por Día")
                df_dem_dia = df_dem_f.groupby(["Fecha", "grupo"])["Casos_Nuevos"].sum().reset_index()
                fig_dem_dia = px.bar(
                    df_dem_dia,
                    x="Fecha",
                    y="Casos_Nuevos",
                    color="grupo",
                    barmode="stack",
                    text="Casos_Nuevos",
                    title="Nuevos Casos por Día y Cola (Demanda de Entrada)"
                )
                fig_dem_dia.update_layout(height=430, margin=dict(l=10, r=10), legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
                st.plotly_chart(fig_dem_dia, use_container_width=True)

            with c_g2:
                st.subheader("🍰 Distribución de Demanda por Cola")
                df_pie_dem = df_dem_f.groupby("grupo")["Casos_Nuevos"].sum().reset_index().sort_values(by="Casos_Nuevos", ascending=False)
                fig_pie = px.pie(
                    df_pie_dem,
                    names="grupo",
                    values="Casos_Nuevos",
                    hole=0.45,
                    title="% de Carga de Entrada por Cola"
                )
                fig_pie.update_layout(height=430, margin=dict(l=10, r=10), legend=dict(orientation="h", yanchor="bottom", y=-0.2))
                st.plotly_chart(fig_pie, use_container_width=True)

            st.subheader("⚖️ Balance Operativo Día a Día: Entrada vs Salida (Inflow vs Outflow)")
            df_comp_dia = df_dem_f.groupby("Fecha")[["Casos_Nuevos", "Casos_Resueltos", "Balance_Neto"]].sum().reset_index()
            fig_comp = go.Figure()
            fig_comp.add_trace(go.Bar(x=df_comp_dia["Fecha"], y=df_comp_dia["Casos_Nuevos"], name="📥 Casos Nuevos (Entrada)", marker_color="#1E88E5"))
            fig_comp.add_trace(go.Bar(x=df_comp_dia["Fecha"], y=df_comp_dia["Casos_Resueltos"], name="📤 Casos Resueltos (Salida)", marker_color="#43A047"))
            fig_comp.add_trace(go.Scatter(x=df_comp_dia["Fecha"], y=df_comp_dia["Balance_Neto"], name="⚖️ Balance Neto (Entradas - Resueltos)", mode="lines+markers", line=dict(color="#E53935", width=3)))
            fig_comp.update_layout(
                title="Balance Diario de Cola: Demanda de Entrada vs Capacidad Resuelta",
                barmode="group",
                height=450,
                margin=dict(l=10, r=10),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            st.plotly_chart(fig_comp, use_container_width=True)

            st.subheader("📋 Registro Diario Detallado de Demanda y Capacidad por Cola")
            st.dataframe(
                df_dem_f[["Fecha", "grupo", "Casos_Nuevos", "Casos_Resueltos", "Balance_Neto"]]
                .sort_values(by=["Fecha", "Casos_Nuevos"], ascending=[False, False])
                .rename(columns={
                    "grupo": "Cola / Servicio",
                    "Casos_Nuevos": "📥 Casos Nuevos (Inflow)",
                    "Casos_Resueltos": "📤 Casos Resueltos (Outflow)",
                    "Balance_Neto": "⚖️ Balance Neto"
                }),
                use_container_width=True
            )
        else:
            st.info("No hay datos de demanda disponibles en el periodo seleccionado.")

    # ---------------------------------------------------------------------
    # SUBMÓDULO 3: ANTIGÜEDAD DEL BACKLOG (IMÁGENES 1, 2, 3)
    # ---------------------------------------------------------------------
    with tab_zd_antiguedad:
        st.subheader("⏳ Matriz de Antigüedad del Backlog Operativo")
        st.caption("Distribución por rangos temporales según fecha de creación del caso en Zendesk (<48H, >48H<=15D, >15Y<=30D, >30D).")

        df_full = bundle["df_b_full"]
        m_resumen = bundle["m_resumen"]
        d_desglose = bundle["d_desglose"]

        if df_full is not None and not df_full.empty:
            tot_bl = len(df_full)
            c_48 = (df_full["Rango_Antiguedad"] == "<48H").sum()
            c_15 = (df_full["Rango_Antiguedad"] == ">48H<=15DIAS").sum()
            c_30 = (df_full["Rango_Antiguedad"] == ">15Y<=30DIAS").sum()
            c_mas30 = (df_full["Rango_Antiguedad"] == ">30DIAS").sum()

            ka1, ka2, ka3, ka4, ka5 = st.columns(5)
            ka1.metric("🚨 Total Fábrica", f"{tot_bl:,}")
            ka2.metric("🟢 Fresco (<48H)", f"{c_48:,}", f"{(c_48/tot_bl)*100:.1f}%")
            ka3.metric("🟡 Operativo (2 a 15 D)", f"{c_15:,}", f"{(c_15/tot_bl)*100:.1f}%")
            ka4.metric("🟠 En Riesgo (15 a 30 D)", f"{c_30:,}", f"{(c_30/tot_bl)*100:.1f}%", delta_color="inverse")
            ka5.metric("🔴 Crítico (>30 Días)", f"{c_mas30:,}", f"{(c_mas30/tot_bl)*100:.1f}%", delta_color="inverse")

            st.markdown("---")

            # Matriz 1 (Imagen 1)
            st.subheader("📊 1. Matriz Resumen de Antigüedad por Servicio (% FÁBRICA)")
            st.caption("Participación porcentual de cada rango de antigüedad sobre el total de casos del servicio.")

            def destacar_fabrica(row):
                if row["SERVICIO"] == "FABRICA":
                    return ["background-color: #1F4E79; color: white; font-weight: bold;"] * len(row)
                return [""] * len(row)

            st.dataframe(
                m_resumen.style.apply(destacar_fabrica, axis=1),
                use_container_width=True,
                hide_index=True
            )

            st.markdown("---")

            # Tabla 2 (Imágenes 2 y 3)
            st.subheader("📑 2. Desglose Operativo por Servicio y Estado del Ticket")
            st.caption("Volumen de CASOS y % ANTIGÜEDAD para cada rango temporal, detallado por Estado (Abierto, En espera, Nuevo, Pendiente).")

            lista_servicios = ["Todos los Servicios"] + sorted(list(df_full["Servicio"].unique()))
            sel_srv_desglose = st.selectbox("Filtrar Desglose por Servicio:", lista_servicios, key="zd_sel_srv_bl_desglose")

            df_desglose_mostrar = d_desglose.copy()
            if sel_srv_desglose != "Todos los Servicios":
                df_desglose_mostrar = df_desglose_mostrar[
                    (df_desglose_mostrar["SERVICIO_PADRE"] == sel_srv_desglose) |
                    (df_desglose_mostrar["TIPO_FILA"] == "TOTAL")
                ]

            cols_mostrar = [c for c in df_desglose_mostrar.columns if c not in ["SERVICIO_PADRE", "TIPO_FILA", "ESTADO"]]

            def estilo_desglose(row):
                tipo = row.get("TIPO_FILA", "")
                if tipo == "TOTAL":
                    return ["background-color: #002060; color: white; font-weight: bold;"] * len(row)
                elif tipo == "SERVICIO":
                    return ["background-color: #D9E1F2; color: #002060; font-weight: bold;"] * len(row)
                else:
                    return [""] * len(row)

            st.dataframe(
                df_desglose_mostrar[cols_mostrar].style.apply(estilo_desglose, axis=1),
                use_container_width=True,
                hide_index=True
            )

            st.markdown("---")
            st.subheader("📈 Distribución Visual de Antigüedad por Grupo")
            df_plot = df_full.groupby(["Servicio", "Rango_Antiguedad"]).size().reset_index(name="Tickets")
            fig_ant = px.bar(
                df_plot,
                x="Servicio",
                y="Tickets",
                color="Rango_Antiguedad",
                barmode="stack",
                color_discrete_map={
                    "<48H": "#2CA02C",
                    ">48H<=15DIAS": "#1F77B4",
                    ">15Y<=30DIAS": "#FF7F0E",
                    ">30DIAS": "#D62728"
                },
                title="Composición de Antigüedad por Cola de Atención"
            )
            fig_ant.update_layout(xaxis_tickangle=-30, height=440)
            st.plotly_chart(fig_ant, use_container_width=True)
        else:
            st.info("No hay datos de backlog disponibles.")

    # ---------------------------------------------------------------------
    # SUBMÓDULO 3: CORTES INTRADÍA (IMAGEN 4)
    # ---------------------------------------------------------------------
    with tab_zd_intradia:
        st.subheader("⏱️ Seguimiento Intradía por Cortes Horarios")
        st.caption("Casos resueltos hoy acumulados por asesor en cada corte de turno, categorizados por Condición (ANTIGUO / NUEVO).")

        df_p_raw = bundle["df_p_raw"]
        df_enr_hoy = bundle["df_enr_hoy"]

        if df_p_raw is not None and not df_p_raw.empty:
            total_res_hoy = len(df_p_raw)
            asesores_hoy = df_enr_hoy["Nombre_Asesor"].nunique() if not df_enr_hoy.empty else 0
            antiguos_count = (df_enr_hoy["Condicion"] == "ANTIGUO").sum() if not df_enr_hoy.empty else 0
            nuevos_count = (df_enr_hoy["Condicion"] == "NUEVO").sum() if not df_enr_hoy.empty else 0

            ki1, ki2, ki3, ki4 = st.columns(4)
            ki1.metric("🎯 Total Resueltos Hoy", f"{total_res_hoy:,}")
            ki2.metric("👥 Asesores en Gestión", f"{asesores_hoy:,}")
            ki3.metric("👔 Gestión Antiguos", f"{antiguos_count:,}", f"{(antiguos_count/total_res_hoy)*100:.1f}%")
            ki4.metric("🌱 Gestión Nuevos", f"{nuevos_count:,}", f"{(nuevos_count/total_res_hoy)*100:.1f}%")

            st.markdown("---")

            col_ci1, col_ci2 = st.columns([3, 1])
            with col_ci1:
                cortes_opciones = {
                    "8:00 AM": 8,
                    "10:00 AM": 10,
                    "12:00 PM": 12,
                    "2:00 PM": 14,
                    "4:00 PM": 16,
                    "6:00 PM": 18
                }
                sel_cortes_labels = st.multiselect(
                    "Selecciona los cortes horarios a visualizar:",
                    options=list(cortes_opciones.keys()),
                    default=["8:00 AM", "10:00 AM", "2:00 PM"],
                    key="zd_sel_cortes"
                )
                cortes_num = sorted([cortes_opciones[l] for l in sel_cortes_labels]) if sel_cortes_labels else [8, 10, 14]

            with col_ci2:
                grupos_disp = ["Todos los Grupos"] + sorted(list(df_p_raw["grupo"].unique()))
                sel_grp_intra = st.selectbox("Filtrar Grupo / TAG:", grupos_disp, key="zd_sel_grp_intra")

            df_p_filtrada = df_p_raw.copy()
            if sel_grp_intra != "Todos los Grupos":
                df_p_filtrada = df_p_filtrada[df_p_filtrada["grupo"] == sel_grp_intra]

            df_intradia = procesar_cortes_intradia(df_p_filtrada, cortes_hora=cortes_num)

            if not df_intradia.empty:
                def estilo_intradia(row):
                    tipo = row.get("TIPO", "")
                    if tipo == "GRUPO":
                        return ["background-color: #2F5597; color: white; font-weight: bold;"] * len(row)
                    elif tipo == "CONDICION":
                        return ["background-color: #D9E1F2; color: #1F4E79; font-weight: bold; font-style: italic;"] * len(row)
                    elif tipo == "TOTAL":
                        return ["background-color: #1F4E79; color: white; font-weight: bold; border-top: 2px solid black;"] * len(row)
                    return [""] * len(row)

                cols_view = [c for c in df_intradia.columns if c != "TIPO"]
                st.dataframe(
                    df_intradia[cols_view].style.apply(estilo_intradia, axis=1),
                    use_container_width=True,
                    hide_index=True,
                    height=580
                )
            else:
                st.info("No hay datos de cortes para el filtro seleccionado.")
        else:
            st.info("No hay casos resueltos registrados hoy.")

    # ---------------------------------------------------------------------
    # SUBMÓDULO 4: BACKLOG EN VIVO (DETALLE Y ESTADOS)
    # ---------------------------------------------------------------------
    with tab_zd_backlog:
        st.subheader("🚨 Monitoreo de Backlog en Vivo (Tiempo Real)")
        st.caption("Tickets actualmente activos (status < solved) en las colas de Almacontact directamente desde la API de Zendesk.")

        df_b_src = bundle["df_b_raw"]
        if df_b_src is not None and not df_b_src.empty:
            df_bv = df_b_src.copy()
            map_estados = {
                "new": "Nuevo",
                "open": "Abierto",
                "pending": "Pendiente",
                "hold": "En Espera",
                "solved": "Resuelto",
                "closed": "Cerrado"
            }
            df_bv["Estado_Legible"] = df_bv["status"].map(map_estados).fillna(df_bv["status"].str.title())

            if solo_alma and "TICKET_ASSIGNEE_PRIMARY_EMAIL" in df_bv.columns:
                df_bv_asig = df_bv[df_bv["TICKET_ASSIGNEE_PRIMARY_EMAIL"].astype(str).str.len() > 3].copy()
                if not df_bv_asig.empty:
                    df_bv_enr = enriquecer_con_socio(df_bv_asig, solo_almacontact=False)
                    if df_bv_enr is not None and not df_bv_enr.empty:
                        email_map = dict(zip(df_bv_enr["TICKET_ASSIGNEE_PRIMARY_EMAIL"], df_bv_enr["Nombre_Asesor"]))
                        sup_map = dict(zip(df_bv_enr["TICKET_ASSIGNEE_PRIMARY_EMAIL"], df_bv_enr["Supervisor"]))
                        df_bv["Nombre_Asesor"] = df_bv["TICKET_ASSIGNEE_PRIMARY_EMAIL"].map(email_map).fillna(df_bv["Nombre_Asesor"])
                        df_bv["Supervisor"] = df_bv["TICKET_ASSIGNEE_PRIMARY_EMAIL"].map(sup_map).fillna("Sin Supervisor")

            col_fb1, col_fb2 = st.columns([2, 1])
            with col_fb1:
                grupos_b = ["Todos los Grupos"] + sorted(list(df_bv["grupo"].unique()))
                sel_b_grp = st.selectbox("Filtrar Backlog por Grupo:", grupos_b, key="zd_sel_b_grp")
            with col_fb2:
                estados_b = ["Todos los Estados"] + sorted(list(df_bv["Estado_Legible"].unique()))
                sel_b_est = st.selectbox("Filtrar por Estado:", estados_b, key="zd_sel_b_est")

            df_bv_view = df_bv.copy()
            if sel_b_grp != "Todos los Grupos":
                df_bv_view = df_bv_view[df_bv_view["grupo"] == sel_b_grp]
            if sel_b_est != "Todos los Estados":
                df_bv_view = df_bv_view[df_bv_view["Estado_Legible"] == sel_b_est]

            total_bv = len(df_bv_view)
            nuevos_bv = len(df_bv_view[df_bv_view["status"] == "new"])
            abiertos_bv = len(df_bv_view[df_bv_view["status"] == "open"])
            espera_bv = len(df_bv_view[df_bv_view["status"].isin(["hold", "pending"])])

            cb1, cb2, cb3, cb4 = st.columns(4)
            cb1.metric("🚨 Total Backlog en Cola", f"{total_bv:,}")
            cb2.metric("🆕 Nuevos (Por Iniciar)", f"{nuevos_bv:,}")
            cb3.metric("📂 Abiertos (En Gestión)", f"{abiertos_bv:,}")
            cb4.metric("⏳ En Espera / Pendientes", f"{espera_bv:,}")

            st.markdown("---")

            col_g1, col_g2 = st.columns([3, 2])
            with col_g1:
                st.subheader("📊 Backlog Activo por Grupo y Estado")
                df_grp_est = df_bv_view.groupby(["grupo", "Estado_Legible"]).size().reset_index(name="Tickets")
                fig_b_grp = px.bar(
                    df_grp_est,
                    x="grupo",
                    y="Tickets",
                    color="Estado_Legible",
                    barmode="stack",
                    text="Tickets",
                    title="Casos Activos por Cola de Atención"
                )
                fig_b_grp.update_layout(xaxis_tickangle=-30, height=430, margin=dict(l=10, r=10))
                st.plotly_chart(fig_b_grp, use_container_width=True)

            with col_g2:
                st.subheader("🏷️ Top Motivos / Tipologías en Cola")
                df_tip_b = df_bv_view["tipo_gestion"].value_counts().head(10).reset_index()
                df_tip_b.columns = ["Tipología", "Tickets"]
                fig_tip_b = px.bar(
                    df_tip_b.sort_values(by="Tickets", ascending=True),
                    x="Tickets",
                    y="Tipología",
                    orientation="h",
                    text="Tickets",
                    title="Tipologías Más Acumuladas en Backlog"
                )
                fig_tip_b.update_layout(yaxis=dict(title=""), height=430, margin=dict(l=10, r=10))
                st.plotly_chart(fig_tip_b, use_container_width=True)

            st.subheader("📋 Detalle de Tickets en Cola de Espera (Hora Colombia UTC-5)")
            if "created_at" in df_bv_view.columns:
                df_bv_view["Fecha Creación (Hora Col)"] = df_bv_view["created_at"].apply(formatear_colombia_dt)
            cols_mostrar = ["id", "subject", "grupo", "Estado_Legible", "priority", "tipo_gestion", "Nombre_Asesor", "Fecha Creación (Hora Col)"]
            st.dataframe(
                df_bv_view[[c for c in cols_mostrar if c in df_bv_view.columns]]
                .rename(columns={
                    "id": "Ticket ID",
                    "subject": "Asunto",
                    "grupo": "Grupo",
                    "Estado_Legible": "Estado",
                    "priority": "Prioridad",
                    "tipo_gestion": "Tipología",
                    "Nombre_Asesor": "Asesor Asignado"
                }),
                use_container_width=True
            )
        else:
            st.info("No hay datos de backlog disponibles.")

    # ---------------------------------------------------------------------
    # SUBMÓDULO 5: DESEMPEÑO Y JERARQUÍA DE ASESORES
    # ---------------------------------------------------------------------
    with tab_zd_asesores:
        if df_filtrado is not None and not df_filtrado.empty:
            st.subheader("👤 Desempeño Operativo de Asesores Almacontact (Corte Global)")
            st.caption("Cruzado con Socio Maestro: visualiza métricas individuales por Servicio, Coordinador y Supervisor.")

            df_as_summary = df_filtrado.groupby(["Nombre_Asesor", "TICKET_ASSIGNEE_PRIMARY_EMAIL", "Supervisor", "Coordinador", "Servicio"]).agg(
                Total_Tickets=("Recuento_Tickets", "sum"),
                Mediana_FRT_min=("Mediana_FRT_min", "median"),
                Mediana_RWT_hrs=("Mediana_RWT_hrs", "median"),
                Tipologias=("Tipo_de_Gestion", "nunique")
            ).reset_index().sort_values(by="Total_Tickets", ascending=False).reset_index(drop=True)

            total_t_sel = df_as_summary["Total_Tickets"].sum()
            frt_med_sel = df_as_summary["Mediana_FRT_min"].median()
            rwt_med_sel = df_as_summary["Mediana_RWT_hrs"].median()
            asesores_count = len(df_as_summary)

            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Asesores en la Vista", f"{asesores_count:,}")
            k2.metric("Total Tickets Gestionados", f"{total_t_sel:,.0f}")
            k3.metric("Mediana 1ra Respuesta (FRT)", f"{frt_med_sel:.1f} min" if not pd.isna(frt_med_sel) else "N/A")
            k4.metric("Mediana Espera (RWT)", f"{rwt_med_sel:.1f} hrs" if not pd.isna(rwt_med_sel) else "N/A")

            st.markdown("---")

            col1, col2 = st.columns([3, 2])
            with col1:
                st.subheader("Top Asesores por Volumen de Tickets Resueltos")
                top15_as = df_as_summary.head(15).sort_values(by="Total_Tickets", ascending=True)
                fig_as = px.bar(
                    top15_as,
                    x="Total_Tickets",
                    y="Nombre_Asesor",
                    orientation="h",
                    color="Supervisor",
                    text=top15_as["Total_Tickets"].apply(lambda x: f"{x:,.0f}"),
                    title="Tickets por Asesor (Color = Supervisor)"
                )
                fig_as.update_layout(height=480, yaxis=dict(title=""), margin=dict(l=10, r=10))
                st.plotly_chart(fig_as, use_container_width=True)

            with col2:
                st.subheader("Tiempos de Atención: FRT vs RWT")
                fig_scat = px.scatter(
                    df_as_summary,
                    x="Mediana_FRT_min",
                    y="Mediana_RWT_hrs",
                    size="Total_Tickets",
                    color="Supervisor",
                    hover_name="Nombre_Asesor",
                    hover_data={"Total_Tickets": ":,d", "Mediana_FRT_min": ":.1f min", "Mediana_RWT_hrs": ":.1f hrs"},
                    title="Velocidad de Respuesta vs Tiempo de Cierre"
                )
                fig_scat.update_layout(height=480, margin=dict(l=10, r=10))
                st.plotly_chart(fig_scat, use_container_width=True)

            st.subheader("📋 Ficha y Expediente Detallado de Asesores")
            st.dataframe(
                df_as_summary.rename(columns={
                    "Nombre_Asesor": "Asesor",
                    "TICKET_ASSIGNEE_PRIMARY_EMAIL": "Correo",
                    "Total_Tickets": "Tickets",
                    "Mediana_FRT_min": "FRT (min)",
                    "Mediana_RWT_hrs": "RWT (hrs)",
                    "Tipologias": "Tipologías"
                })
                .style.format({
                    "Tickets": "{:,.0f}",
                    "FRT (min)": "{:.1f}",
                    "RWT (hrs)": "{:.1f}"
                }),
                use_container_width=True
            )
        else:
            st.info("No hay datos de asesores para los filtros seleccionados.")

    # ---------------------------------------------------------------------
    # SUBMÓDULO 6: TIPOLOGÍA DE GESTIÓN
    # ---------------------------------------------------------------------
    with tab_zd_tipologia:
        if df_filtrado is not None and not df_filtrado.empty:
            st.subheader("🏷️ Tipologías de Gestión Atendidas")

            df_tip = df_filtrado.groupby("Tipo_de_Gestion").agg(
                Tickets=("Recuento_Tickets", "sum"),
                Mediana_FRT_min=("Mediana_FRT_min", "median"),
                Mediana_RWT_hrs=("Mediana_RWT_hrs", "median"),
                Asesores=("Nombre_Asesor", "nunique")
            ).reset_index().sort_values(by="Tickets", ascending=False).reset_index(drop=True)

            col_t1, col_t2 = st.columns(2)
            with col_t1:
                st.subheader("Top Tipologías Más Frecuentes")
                fig_t = px.bar(
                    df_tip.head(12).sort_values(by="Tickets", ascending=True),
                    x="Tickets",
                    y="Tipo_de_Gestion",
                    orientation="h",
                    color="Tickets",
                    color_continuous_scale="Blues",
                    text=df_tip.head(12).sort_values(by="Tickets", ascending=True)["Tickets"].apply(lambda x: f"{x:,.0f}")
                )
                fig_t.update_layout(height=450, yaxis=dict(title=""), coloraxis_showscale=False)
                st.plotly_chart(fig_t, use_container_width=True)

            with col_t2:
                st.subheader("Mayor Tiempo de Espera (RWT en Horas)")
                fig_r = px.bar(
                    df_tip[df_tip["Tickets"] >= 10].sort_values(by="Mediana_RWT_hrs", ascending=False).head(12),
                    x="Mediana_RWT_hrs",
                    y="Tipo_de_Gestion",
                    orientation="h",
                    color="Mediana_RWT_hrs",
                    color_continuous_scale="Reds",
                    text=df_tip[df_tip["Tickets"] >= 10].sort_values(by="Mediana_RWT_hrs", ascending=False).head(12)["Mediana_RWT_hrs"].apply(lambda x: f"{x:.1f} h")
                )
                fig_r.update_layout(height=450, yaxis=dict(title="", autorange="reversed"), coloraxis_showscale=False)
                st.plotly_chart(fig_r, use_container_width=True)

            st.dataframe(
                df_tip.rename(columns={
                    "Tipo_de_Gestion": "Tipología",
                    "Mediana_FRT_min": "FRT (min)",
                    "Mediana_RWT_hrs": "RWT (hrs)"
                })
                .style.format({
                    "Tickets": "{:,.0f}",
                    "FRT (min)": "{:.1f}",
                    "RWT (hrs)": "{:.1f}"
                }),
                use_container_width=True
            )

    # ---------------------------------------------------------------------
    # SUBMÓDULO 7: TIEMPOS DE SERVICIO (SLAS)
    # ---------------------------------------------------------------------
    with tab_zd_tiempos:
        file_tiempos = DATA_DIR / "tiempos_respuesta_amc.csv"
        if file_tiempos.exists():
            df_t = pd.read_csv(file_tiempos)

            k1, k2, k3, k4 = st.columns(4)
            k1.metric("⚡ 1ra Respuesta LUA AMC", "2.0 min", "Líder en rapidez")
            k2.metric("⚡ 1ra Respuesta WhatsApp", "3.0 min", "Atención inmediata")
            k3.metric("⚠️ Cuello Botella Resolución", "Equipajes SSC", "117.2 hrs (~4.9 días)")
            k4.metric("⚠️ Cuello Botella DT FFP", "DT FFP AMC", "75.3 hrs (~3.1 días)")

            st.markdown("---")
            st.subheader("🎯 Matriz de Eficiencia Operativa (Respuesta vs Resolución por Grupo)")
            fig_quad = px.scatter(
                df_t,
                x="Mediana_FRT_min",
                y="Mediana_Resolucion_hrs",
                size="Tickets",
                color="TICKET_GROUP_NAME",
                hover_name="TICKET_GROUP_NAME",
                hover_data={"Tickets": ":,d", "Mediana_FRT_min": ":.1f min", "Mediana_Resolucion_hrs": ":.1f hrs"},
                log_x=True,
                log_y=True,
                title="Relación entre Primera Respuesta y Tiempo Total de Cierre (Escala Log)"
            )
            fig_quad.update_layout(height=480)
            st.plotly_chart(fig_quad, use_container_width=True)

            st.dataframe(
                df_t[["TICKET_GROUP_NAME", "Tickets", "Mediana_FRT_min", "Mediana_Resolucion_hrs", "Mediana_Resolucion_dias"]]
                .rename(columns={"TICKET_GROUP_NAME": "Grupo", "Mediana_FRT_min": "1ra Respuesta (min)", "Mediana_Resolucion_hrs": "Resolución (hrs)", "Mediana_Resolucion_dias": "Resolución (días)"})
                .style.format({"Tickets": "{:,.0f}", "1ra Respuesta (min)": "{:.1f}", "Resolución (hrs)": "{:.1f}", "Resolución (días)": "{:.2f}"}),
                use_container_width=True
            )

    # ---------------------------------------------------------------------
    # SUBMÓDULO 8: VOLUMEN HISTÓRICO
    # ---------------------------------------------------------------------
    with tab_zd_volumen:
        file_volumen = DATA_DIR / "volumen_grupos_amc.csv"
        if file_volumen.exists():
            df_v = pd.read_csv(file_volumen).sort_values(by="Tickets", ascending=False).reset_index(drop=True)
            total_v = df_v["Tickets"].sum()
            df_v["% Participación"] = (df_v["Tickets"] / total_v) * 100
            df_v["% Acumulado"] = df_v["% Participación"].cumsum()

            fig_v = go.Figure()
            fig_v.add_trace(go.Bar(x=df_v["TICKET_GROUP_NAME"], y=df_v["Tickets"], name="Tickets", marker_color="#2E7D32"))
            fig_v.add_trace(go.Scatter(x=df_v["TICKET_GROUP_NAME"], y=df_v["% Acumulado"], name="% Acumulado (Pareto)", yaxis="y2", mode="lines+markers", marker=dict(color="#D32F2F")))
            fig_v.update_layout(
                title="Distribución Total de Volumen por Grupo",
                xaxis=dict(tickangle=-40),
                yaxis2=dict(title="% Acumulado", overlaying="y", side="right", range=[0, 105]),
                height=480
            )
            st.plotly_chart(fig_v, use_container_width=True)
            st.dataframe(df_v.style.format({"Tickets": "{:,.0f}", "% Participación": "{:.2f}%", "% Acumulado": "{:.2f}%"}), use_container_width=True)

    # ---------------------------------------------------------------------
    # SUBMÓDULO 9: AUTORIZACIONES DE SUPERVISOR (CÓDIGOS DE INVOLUNTARIO)
    # ---------------------------------------------------------------------
    with tab_zd_autorizaciones:
        st.subheader("🛡️ Monitor de Autorizaciones de Supervisor (Códigos de Involuntario)")
        st.caption("Colas 'Autorización Supervisor AMC' y 'Autorización Supervisor HVC AMC ES'. Validaciones y liberaciones de códigos involuntarios gestionadas por supervisores (segregadas de la productividad de los asesores).")

        # Base de datos de autorizaciones históricas (aplicando filtro de fechas)
        df_auth_hist = None
        if df_diario_enr is not None and not df_diario_enr.empty and "grupo" in df_diario_enr.columns:
            m_auth = df_diario_enr["grupo"].str.contains("Autorización|Supervisor|Autorizacion", case=False, na=False)
            df_auth_base = df_diario_enr[m_auth].copy()
            if fecha_ini and fecha_fin:
                f_ini_s = fecha_ini.strftime("%Y-%m-%d")
                f_fin_s = fecha_fin.strftime("%Y-%m-%d")
                df_auth_base = df_auth_base[(df_auth_base["Fecha"] >= f_ini_s) & (df_auth_base["Fecha"] <= f_fin_s)]
            df_auth_hist = df_auth_base

        # Base de datos de backlog en vivo de autorizaciones
        df_b_vivo_raw = bundle.get("df_b_raw")
        df_b_auth = None
        if df_b_vivo_raw is not None and not df_b_vivo_raw.empty and "grupo" in df_b_vivo_raw.columns:
            m_b_auth = df_b_vivo_raw["grupo"].str.contains("Autorización|Supervisor|Autorizacion", case=False, na=False)
            df_b_auth = df_b_vivo_raw[m_b_auth].copy()

        # Métricas principales
        tot_auth_resueltas = df_auth_hist["Recuento_Tickets"].sum() if df_auth_hist is not None and not df_auth_hist.empty else 0
        tot_amc = df_auth_hist[df_auth_hist["grupo"].str.contains("AMC", case=False, na=False) & ~df_auth_hist["grupo"].str.contains("HVC", case=False, na=False)]["Recuento_Tickets"].sum() if df_auth_hist is not None and not df_auth_hist.empty else 0
        tot_hvc = df_auth_hist[df_auth_hist["grupo"].str.contains("HVC", case=False, na=False)]["Recuento_Tickets"].sum() if df_auth_hist is not None and not df_auth_hist.empty else 0
        tot_bl_auth = len(df_b_auth) if df_b_auth is not None and not df_b_auth.empty else 0

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Autorizaciones Resueltas", f"{tot_auth_resueltas:,.0f}", f"En rango {fecha_ini} a {fecha_fin}" if fecha_ini and fecha_fin else "Total histórico")
        k2.metric("Supervisor AMC (Regular)", f"{tot_amc:,.0f}", f"{(tot_amc/tot_auth_resueltas*100):.1f}% del total" if tot_auth_resueltas > 0 else "0%")
        k3.metric("Supervisor HVC AMC ES (VIP)", f"{tot_hvc:,.0f}", f"{(tot_hvc/tot_auth_resueltas*100):.1f}% del total" if tot_auth_resueltas > 0 else "0%")
        k4.metric("🚨 Backlog en Cola Ahora", f"{tot_bl_auth:,}", "Pendientes de autorización en vivo", delta_color="inverse" if tot_bl_auth > 0 else "normal")

        st.markdown("---")

        if df_auth_hist is not None and not df_auth_hist.empty:
            c1, c2 = st.columns([3, 2])
            with c1:
                st.subheader("📈 Evolución Diaria de Autorizaciones por Cola")
                df_auth_dia = df_auth_hist[df_auth_hist["Fecha"] != "Sin Fecha"].groupby(["Fecha", "grupo"])["Recuento_Tickets"].sum().reset_index()
                fig_auth_dia = px.bar(
                    df_auth_dia,
                    x="Fecha",
                    y="Recuento_Tickets",
                    color="grupo",
                    barmode="stack",
                    text="Recuento_Tickets",
                    title="Autorizaciones Diarias de Supervisores",
                    color_discrete_map={
                        "Autorización Supervisor AMC": "#1E88E5",
                        "Autorización Supervisor HVC AMC ES": "#D81B60"
                    }
                )
                fig_auth_dia.update_layout(height=420, margin=dict(l=10, r=10), legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
                st.plotly_chart(fig_auth_dia, use_container_width=True)

            with c2:
                st.subheader("🧑‍💼 Supervisores con Más Autorizaciones")
                df_sup_top = df_auth_hist.groupby(["Nombre_Asesor", "grupo"])["Recuento_Tickets"].sum().reset_index().sort_values(by="Recuento_Tickets", ascending=False).head(12)
                fig_sup_top = px.bar(
                    df_sup_top.sort_values(by="Recuento_Tickets", ascending=True),
                    x="Recuento_Tickets",
                    y="Nombre_Asesor",
                    orientation="h",
                    color="grupo",
                    text="Recuento_Tickets",
                    title="Top Gestores de Códigos de Involuntario",
                    color_discrete_map={
                        "Autorización Supervisor AMC": "#1E88E5",
                        "Autorización Supervisor HVC AMC ES": "#D81B60"
                    }
                )
                fig_sup_top.update_layout(height=420, yaxis=dict(title=""), margin=dict(l=10, r=10), legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
                st.plotly_chart(fig_sup_top, use_container_width=True)

            st.subheader("📋 Registro Histórico de Autorizaciones Procesadas")
            st.dataframe(
                df_auth_hist[["Fecha", "grupo", "Nombre_Asesor", "TICKET_ASSIGNEE_PRIMARY_EMAIL", "Recuento_Tickets"]]
                .rename(columns={
                    "grupo": "Cola de Autorización",
                    "Nombre_Asesor": "Supervisor / Gestor",
                    "TICKET_ASSIGNEE_PRIMARY_EMAIL": "Correo Corporativo",
                    "Recuento_Tickets": "Casos Autorizados"
                })
                .sort_values(by=["Fecha", "Casos Autorizados"], ascending=[False, False]),
                use_container_width=True
            )
        else:
            st.info("No hay registros de autorizaciones en el rango de fechas seleccionado.")

        # Sección de Backlog en vivo si hay casos pendientes
        st.markdown("---")
        st.subheader("🚨 Backlog en Cola de Autorizaciones en Tiempo Real")
        if df_b_auth is not None and not df_b_auth.empty:
            c_b1, c_b2 = st.columns([1, 1])
            with c_b1:
                amc_b_cnt = len(df_b_auth[df_b_auth["grupo"].str.contains("HVC", case=False, na=False) == False])
                st.metric("Pendientes Autorización AMC", f"{amc_b_cnt}")
            with c_b2:
                hvc_b_cnt = len(df_b_auth[df_b_auth["grupo"].str.contains("HVC", case=False, na=False)])
                st.metric("Pendientes Autorización HVC ES", f"{hvc_b_cnt}")

            if "created_at" in df_b_auth.columns:
                df_b_auth["Fecha Ingreso (Hora Col)"] = df_b_auth["created_at"].apply(formatear_colombia_dt)
            cols_show = [c for c in ["id", "grupo", "status", "priority", "Fecha Ingreso (Hora Col)", "Nombre_Asesor", "subject"] if c in df_b_auth.columns]
            st.dataframe(
                df_b_auth[cols_show].rename(columns={
                    "id": "ID Ticket",
                    "grupo": "Cola",
                    "status": "Estado",
                    "priority": "Prioridad",
                    "Nombre_Asesor": "Asignado",
                    "subject": "Asunto / Solicitud"
                }),
                use_container_width=True
            )
        else:
            st.success("✅ ¡Excelente! No hay tickets pendientes en las colas de autorización de supervisor.")
