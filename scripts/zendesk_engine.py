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
import sqlite3
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# Rutas relativas del repositorio y resolución en sys.path
SCRIPTS_DIR = Path(__file__).resolve().parent
BASE_DIR = SCRIPTS_DIR.parent
DATA_DIR = BASE_DIR / "data" / "zendesk"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from exclusion_list import es_persona_excluida, filtrar_df_exclusiones
except Exception:
    try:
        from scripts.exclusion_list import es_persona_excluida, filtrar_df_exclusiones
    except Exception:
        def es_persona_excluida(val: str) -> bool:
            return False
        def filtrar_df_exclusiones(df: pd.DataFrame) -> pd.DataFrame:
            return df

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
def cargar_catalogo_usuarios_zd() -> dict:
    f_cat = DATA_DIR / "catalogo_usuarios.json"
    if not f_cat.exists():
        return {}
    try:
        with open(f_cat, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return {
            u.get("email", "").strip().lower(): u.get("name", "").strip().title()
            for u in raw.values() if u.get("email") and u.get("name")
        }
    except Exception:
        return {}


@st.cache_data(ttl=600, show_spinner=False)
def cargar_roster_maestro() -> pd.DataFrame:
    """Carga y unifica las fuentes sociodemográficas disponibles con prioridad para Back Office AMC."""
    m_list = []

    # Fuente 1 (Prioritaria para Zendesk): servicios_socio_maestro.csv
    socio_file = DATA_DIR / "servicios_socio_maestro.csv"
    if socio_file.exists():
        try:
            d1 = pd.read_csv(socio_file, low_memory=False)
            m1 = d1[["name", "jefe", "coordinador", "servicio"]].dropna(subset=["name"]).rename(columns={"name": "nombre"})
            m_list.append(m1)
        except Exception:
            pass

    # Fuente 2: socio_demo.csv
    socio_demo_file = DATA_DIR / "socio_demo.csv"
    if socio_demo_file.exists():
        try:
            d2 = pd.read_csv(socio_demo_file, low_memory=False)
            m2 = d2[["nombre_completo", "jefe_inmediato", "coordinador", "Servicio"]].dropna(subset=["nombre_completo"]).rename(
                columns={"nombre_completo": "nombre", "jefe_inmediato": "jefe", "Servicio": "servicio"}
            )
            m_list.append(m2)
        except Exception:
            pass

    # Fuente 3: Base de datos sociodemográfico consolidado
    db_master = BASE_DIR / "data" / "presencia_master.db"
    if db_master.exists():
        try:
            conn = sqlite3.connect(db_master)
            df_socio_db = pd.read_sql(
                "SELECT nombre, jefe_inmediato as jefe, coordinador, servicio FROM sociodemografico "
                "WHERE servicio LIKE '%AMC%' OR servicio LIKE '%BO%' OR servicio LIKE '%LATAM%' OR servicio LIKE '%BACK%'",
                conn
            )
            conn.close()
            if not df_socio_db.empty:
                m_list.append(df_socio_db)
        except Exception:
            pass

    # Fuente 4: maestro_asesores_b2b.json (si existe)
    b2b_file = DATA_DIR.parent / "salesforce" / "maestro_asesores_b2b.json"
    if b2b_file.exists():
        try:
            with open(b2b_file, "r", encoding="utf-8") as f:
                b2b = json.load(f)
            rows_b2b = [
                {"nombre": v.get("nombre_completo"), "jefe": v.get("supervisor"), "coordinador": v.get("coordinador"), "servicio": v.get("servicio")}
                for v in b2b.values() if v.get("nombre_completo")
            ]
            if rows_b2b:
                m_list.append(pd.DataFrame(rows_b2b))
        except Exception:
            pass

    if not m_list:
        return pd.DataFrame()

    try:
        m = pd.concat(m_list, ignore_index=True).drop_duplicates(subset=["nombre"]).copy()
        m["norm_name"] = m["nombre"].apply(normalizar)
        m["servicio"] = m["servicio"].fillna("Por Definir")
        m["jefe"] = m["jefe"].fillna("Por Asignar")
        m["coordinador"] = m["coordinador"].fillna("Por Asignar")
        m = filtrar_df_exclusiones(m)
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
        df_est = df_est[~df_est["Asesor"].apply(es_persona_excluida)]
        return {
            normalizar(r["Asesor"]): ("NUEVO" if pd.notna(r.get("Antiguedad_Dias")) and r.get("Antiguedad_Dias") <= 90 else "ANTIGUO")
            for _, r in df_est.iterrows()
        }
    except Exception:
        return {}


MAPA_GRUPO_A_SERVICIO = {
    "DT FFP AMC": "DT FFP AMC",
    "LUA AMC": "BO LUA AMC",
    "Equipajes AMC SSC": "BO EQUIPAJES AMC",
    "Célula PI AMC ES": "CÉLULA PI AMC ES",
    "Clula PI AMC ES": "CÉLULA PI AMC ES",
    "Celula PI AMC ES": "CÉLULA PI AMC ES",
    "Latam Travel": "LATAM TRAVEL AMC",
    "Autorización Supervisor HVC AMC ES": "AUTORIZACIÓN SUPERVISOR",
    "Autorización Supervisor AMC": "AUTORIZACIÓN SUPERVISOR",
    "Autorizacion Supervisor AMC": "AUTORIZACIÓN SUPERVISOR",
    "Back Office Reclamos": "BO_CUS_COL",
    "BO_WAIVERS": "BO_WAIVERS",
    "BO ANTIFRAUDE AMC": "BO ANTIFRAUDE AMC",
    "BO_CORPORATE": "BO_CORPORATE",
    "BO AGENCIAS TARGET": "BO AGENCIAS TARGET"
}


@st.cache_data(ttl=600, show_spinner=False)
def cargar_mapa_asesor_a_servicio_operativo() -> dict:
    """Pre-calcula el servicio operativo predominante de cada correo a partir de la productividad diaria."""
    file_d = DATA_DIR / "productividad_diaria_fechas.csv"
    if not file_d.exists():
        return {}
    try:
        df_d = pd.read_csv(file_d)
        if df_d.empty or "TICKET_ASSIGNEE_PRIMARY_EMAIL" not in df_d.columns or "grupo" not in df_d.columns:
            return {}
        df_d["srv_norm"] = df_d["grupo"].map(MAPA_GRUPO_A_SERVICIO).fillna(df_d["grupo"])
        grp_agg = df_d.groupby(["TICKET_ASSIGNEE_PRIMARY_EMAIL", "srv_norm"])["Recuento_Tickets"].sum().reset_index()
        top_srv = grp_agg.sort_values(by=["TICKET_ASSIGNEE_PRIMARY_EMAIL", "Recuento_Tickets"], ascending=[True, False]).drop_duplicates(subset=["TICKET_ASSIGNEE_PRIMARY_EMAIL"])
        return dict(zip(top_srv["TICKET_ASSIGNEE_PRIMARY_EMAIL"].astype(str).str.lower().str.strip(), top_srv["srv_norm"]))
    except Exception:
        return {}


def enriquecer_con_socio(df: pd.DataFrame, solo_almacontact: bool = True) -> pd.DataFrame:
    """Filtra asesores de Almacontact y cruza con la jerarquía de Socio Maestro y condición de antigüedad."""
    if df is None or df.empty:
        return df

    df_out = df.copy()

    # Filtro Almacontact por correo si corresponde
    col_email = None
    for cand in ["TICKET_ASSIGNEE_PRIMARY_EMAIL", "assignee_email", "email"]:
        if cand in df_out.columns:
            col_email = cand
            break

    if solo_almacontact and col_email:
        is_alma = df_out[col_email].astype(str).str.contains(
            r"almacontact|\.alma@|@almacontact|@outsourcing-account\.com", case=False, na=False
        )
        df_out = df_out[is_alma].copy()

    zd_catalog = cargar_catalogo_usuarios_zd()
    maestro = cargar_roster_maestro()
    cond_map = cargar_condicion_antiguedad()
    mapa_asesor_srv = cargar_mapa_asesor_a_servicio_operativo()

    roster_dict = {}
    token_tuples = []
    if not maestro.empty:
        for _, r in maestro.iterrows():
            norm_k = r.get("norm_name")
            if norm_k and norm_k not in roster_dict:
                roster_dict[norm_k] = {
                    "nombre": r.get("nombre", ""),
                    "jefe": r.get("jefe", "Por Asignar"),
                    "coordinador": r.get("coordinador", "Por Asignar"),
                    "servicio": r.get("servicio", "Back Office AMC")
                }
        token_tuples = [
            (norm_k, set(norm_k.split()), data)
            for norm_k, data in roster_dict.items()
            if len(norm_k.split()) >= 2
        ]

    cache_matches = {}

    def obtener_jerarquia(email, grupo=""):
        if not email or pd.isna(email) or str(email).strip().lower() in ("", "nan", "none"):
            srv_def = MAPA_GRUPO_A_SERVICIO.get(str(grupo).strip(), "Back Office AMC")
            return ("Sin Asignar", "Sin Supervisor", "Sin Coordinador", srv_def)

        em_str = str(email).strip().lower()
        cache_key = f"{em_str}___{grupo}"
        if cache_key in cache_matches:
            return cache_matches[cache_key]

        zd_name = zd_catalog.get(em_str, "")
        if not zd_name:
            parts = em_str.split("@")[0].split(".")[0].replace("_", " ").split()
            zd_name = " ".join(parts).title()

        norm_zd = normalizar(zd_name)
        matched_info = None

        # 1. Match Exacto por nombre normalizado
        if norm_zd in roster_dict:
            matched_info = roster_dict[norm_zd]

        # 2. Match por Tokens de Nombre
        if not matched_info:
            toks_zd = set(norm_zd.split())
            if len(toks_zd) >= 2:
                for norm_k, toks_db, data in token_tuples:
                    inter = toks_zd.intersection(toks_db)
                    if len(inter) >= 2 and len(inter) / max(len(toks_zd), len(toks_db)) >= 0.5:
                        matched_info = data
                        break

        # 3. Match por prefijo de correo en nombres de asesores
        if not matched_info:
            prefix = em_str.split("@")[0].split(".")[0].strip()
            if len(prefix) >= 5:
                for norm_k, toks_db, data in token_tuples:
                    clean_k = norm_k.replace(" ", "").lower()
                    if prefix in clean_k:
                        matched_info = data
                        break

        if matched_info:
            nom_res = str(matched_info.get("nombre", zd_name)).title()
            jef_res = str(matched_info.get("jefe", "Por Asignar"))
            coo_res = str(matched_info.get("coordinador", "Por Asignar"))
            srv_res = str(matched_info.get("servicio", "Back Office AMC"))

            # Refinar servicio genérico con la cola del ticket o con el servicio operativo del asesor
            if srv_res in ("Back Office AMC", "Almacontact Operación", "Por Definir", "OPERACION MEDELLIN", "Sin Servicio"):
                if grupo:
                    srv_res = MAPA_GRUPO_A_SERVICIO.get(str(grupo).strip(), srv_res)
                elif em_str in mapa_asesor_srv:
                    srv_res = mapa_asesor_srv[em_str]

            if srv_res in MAPA_GRUPO_A_SERVICIO:
                srv_res = MAPA_GRUPO_A_SERVICIO[srv_res]

            res = (nom_res, jef_res, coo_res, srv_res)
        else:
            srv_fallback = MAPA_GRUPO_A_SERVICIO.get(str(grupo).strip()) if grupo else mapa_asesor_srv.get(em_str, "Back Office AMC")
            if srv_fallback in MAPA_GRUPO_A_SERVICIO:
                srv_fallback = MAPA_GRUPO_A_SERVICIO[srv_fallback]
            res = (zd_name.title(), "Por Asignar", "Por Asignar", srv_fallback)

        cache_matches[cache_key] = res
        return res

    col_grp = "grupo" if "grupo" in df_out.columns else None

    if col_email:
        if col_grp:
            jerarquia = [
                obtener_jerarquia(em, grp)
                for em, grp in zip(df_out[col_email], df_out[col_grp])
            ]
        else:
            jerarquia = [obtener_jerarquia(em) for em in df_out[col_email]]

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
    if "Coordinador" not in df.columns or "Supervisor" not in df.columns:
        df = enriquecer_con_socio(df, solo_almacontact=False)

    file_b_vivo = DATA_DIR / "backlog_en_vivo.csv"
    if file_b_vivo.exists():
        ref_dt = pd.to_datetime(file_b_vivo.stat().st_mtime, unit="s", utc=True)
    else:
        ref_dt = pd.Timestamp.now(tz=timezone.utc)

    df["ts_created"] = pd.to_datetime(df["created_at"], utc=True)
    df["horas_antiguedad"] = (ref_dt - df["ts_created"]).dt.total_seconds() / 3600.0
    df["horas_antiguedad"] = df["horas_antiguedad"].clip(lower=0.0)
    df["dias_antiguedad"] = df["horas_antiguedad"] / 24.0

    df["Rango_Antiguedad"] = [
        clasificar_antiguedad_ticket(h, d) for h, d in zip(df["horas_antiguedad"], df["dias_antiguedad"])
    ]

    map_estados = {
        "new": "Nuevo",
        "open": "Abierto",
        "pending": "Pendiente",
        "hold": "En Espera",
        "solved": "Resuelto",
        "closed": "Cerrado"
    }
    df["Estado"] = df["status"].map(map_estados).fillna(df["status"].str.title())
    df["Servicio"] = df["grupo"].map(MAPA_GRUPO_A_SERVICIO).fillna(df["grupo"]).fillna("Sin Grupo")

    # REGLA OPERATIVA DE BACKLOG:
    # Casos pendientes (esperando al pasajero) NO suman al backlog operativo activo.
    # El backlog operativo SOLO incluye: 'new' (Nuevo), 'open' (Abierto) y 'hold' (En Espera).
    df_activo = df[df["status"].isin(["new", "open", "hold"])].copy()

    # Tabla 1: Matriz de Porcentajes (% FÁBRICA) calculada EXCLUSIVAMENTE sobre backlog operativo
    ct_counts = pd.crosstab(df_activo["Servicio"], df_activo["Rango_Antiguedad"])
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
    total_general_activo = len(df_activo)

    for srv in servicios_unicos:
        df_srv = df[df["Servicio"] == srv]
        df_srv_act = df_srv[df_srv["status"].isin(["new", "open", "hold"])]
        total_srv_act = len(df_srv_act)

        # Fila Resumen de Servicio (SOLO suma casos operativos activos del servicio)
        fila_srv = {
            "SERVICIO": srv,
            "SERVICIO_PADRE": srv,
            "TIPO_FILA": "SERVICIO"
        }
        for r in RANGOS_ORDEN:
            c = (df_srv_act["Rango_Antiguedad"] == r).sum()
            p = (c / total_srv_act * 100.0) if total_srv_act > 0 else 0.0
            fila_srv[f"{r} CASOS"] = c
            fila_srv[f"{r} % ANT."] = f"{p:.1f}%".replace(".", ",")
        fila_srv["Total CASOS"] = total_srv_act
        filas_desglose.append(fila_srv)

        # Subfilas operativas activas (Nuevo, Abierto, En Espera)
        for est, est_key in [("Nuevo", "new"), ("Abierto", "open"), ("En Espera", "hold")]:
            df_est = df_srv[df_srv["status"] == est_key]
            if not df_est.empty:
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

        # Subfila Informativa de Pendientes (Para visibilidad y auditoría sin sumar al total operativo)
        df_pend = df_srv[df_srv["status"] == "pending"]
        if not df_pend.empty:
            total_pend = len(df_pend)
            fila_pend = {
                "SERVICIO": "    🟡 Pendiente (Esperando Pasajero - Informativo)",
                "SERVICIO_PADRE": srv,
                "TIPO_FILA": "PENDIENTE_INFO"
            }
            for r in RANGOS_ORDEN:
                c = (df_pend["Rango_Antiguedad"] == r).sum()
                p = (c / total_pend * 100.0) if total_pend > 0 else 0.0
                fila_pend[f"{r} CASOS"] = c
                fila_pend[f"{r} % ANT."] = f"{p:.1f}%".replace(".", ",")
            fila_pend["Total CASOS"] = total_pend
            filas_desglose.append(fila_pend)

    # Fila TOTAL FABRICA OPERATIVO (Solo casos operativos activos)
    fila_total = {
        "SERVICIO": "TOTAL FÁBRICA OPERATIVO",
        "SERVICIO_PADRE": "TOTAL FÁBRICA OPERATIVO",
        "TIPO_FILA": "TOTAL"
    }
    for r in RANGOS_ORDEN:
        c = (df_activo["Rango_Antiguedad"] == r).sum()
        p = (c / total_general_activo * 100.0) if total_general_activo > 0 else 0.0
        fila_total[f"{r} CASOS"] = c
        fila_total[f"{r} % ANT."] = f"{p:.1f}%".replace(".", ",")
    fila_total["Total CASOS"] = total_general_activo
    filas_desglose.append(fila_total)

    # Fila Informativa TOTAL PENDIENTES FABRICA
    df_all_pend = df[df["status"] == "pending"]
    if not df_all_pend.empty:
        tot_all_pend = len(df_all_pend)
        fila_tot_pend = {
            "SERVICIO": "🟡 TOTAL PENDIENTES (Esperando Pasajero)",
            "SERVICIO_PADRE": "TOTAL PENDIENTES",
            "TIPO_FILA": "TOTAL_PENDIENTE"
        }
        for r in RANGOS_ORDEN:
            c = (df_all_pend["Rango_Antiguedad"] == r).sum()
            p = (c / tot_all_pend * 100.0) if tot_all_pend > 0 else 0.0
            fila_tot_pend[f"{r} CASOS"] = c
            fila_tot_pend[f"{r} % ANT."] = f"{p:.1f}%".replace(".", ",")
        fila_tot_pend["Total CASOS"] = tot_all_pend
        filas_desglose.append(fila_tot_pend)

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

    # Pre-calcular banderas de SLA en df_raw antes de enriquecer
    if df_raw is not None and not df_raw.empty:
        if "Mediana_RWT_hrs" in df_raw.columns:
            df_raw["Cumple_RWT_48h"] = df_raw["Mediana_RWT_hrs"] <= 48.0
        if "Mediana_FRT_hrs" in df_raw.columns:
            df_raw["Cumple_FRT_24h"] = df_raw["Mediana_FRT_hrs"] <= 24.0

    # Pre-enriquecer con jerarquía Socio Maestro
    df_raw_enr_alma = enriquecer_con_socio(df_raw, solo_almacontact=True) if df_raw is not None else None
    df_raw_enr_todos = enriquecer_con_socio(df_raw, solo_almacontact=False) if df_raw is not None else None

    # Segregar autorizaciones de supervisor en histórico diario
    df_diario_operativo = None
    df_diario_auth = None
    if df_diario_raw is not None and not df_diario_raw.empty:
        is_auth_d = df_diario_raw["grupo"].astype(str).str.contains("Autorización|Supervisor|Autorizacion", case=False, na=False)
        df_diario_operativo = df_diario_raw[~is_auth_d].copy()
        df_diario_auth = df_diario_raw[is_auth_d].copy()

    df_diario_enr_alma = enriquecer_con_socio(df_diario_operativo, solo_almacontact=True) if df_diario_operativo is not None else None
    df_diario_enr_todos = enriquecer_con_socio(df_diario_operativo, solo_almacontact=False) if df_diario_operativo is not None else None
    df_auth_hist = enriquecer_con_socio(df_diario_auth, solo_almacontact=False) if df_diario_auth is not None else None

    # Pre-procesar backlog y antigüedad separando autorizaciones
    df_b_raw = pd.read_csv(file_b_vivo) if file_b_vivo.exists() else None
    df_b_operativo = None
    df_b_auth = None
    if df_b_raw is not None and not df_b_raw.empty:
        is_auth_b = df_b_raw["grupo"].astype(str).str.contains("Autorización|Supervisor|Autorizacion", case=False, na=False)
        df_b_operativo = df_b_raw[~is_auth_b].copy()
        df_b_auth = df_b_raw[is_auth_b].copy()

    # La matriz y el backlog operativo SOLO reciben df_b_operativo (sin autorizaciones para no inflar la fábrica)
    df_b_full, m_resumen, d_desglose = procesar_antiguedad_backlog(df_b_operativo) if df_b_operativo is not None else (pd.DataFrame(), pd.DataFrame(), pd.DataFrame())

    # Detectar casos Reopen en df_b_full (casos actualmente open que registran fecha o registro previo de resuelto)
    file_parquet = DATA_DIR / "productividad_historica_2026.parquet"
    ids_resueltos = set()
    if file_parquet.exists():
        try:
            df_pq = pd.read_parquet(file_parquet, columns=["id"])
            ids_resueltos.update(df_pq["id"].dropna().astype(str).tolist())
        except Exception:
            pass
    if file_prod_hoy.exists():
        try:
            df_h = pd.read_csv(file_prod_hoy, usecols=["id"])
            ids_resueltos.update(df_h["id"].dropna().astype(str).tolist())
        except Exception:
            pass

    if df_b_full is not None and not df_b_full.empty:
        df_b_full["Es_Reopen"] = (df_b_full["status"] == "open") & (df_b_full["id"].astype(str).isin(ids_resueltos))
    elif df_b_full is not None:
        df_b_full["Es_Reopen"] = False

    # Pre-calcular rango de fechas globales disponibles
    fechas_disp = []
    if df_diario_raw is not None and "Fecha" in df_diario_raw.columns:
        fechas_disp.extend([str(f)[:10] for f in df_diario_raw["Fecha"].dropna().unique() if str(f) != "Sin Fecha" and len(str(f)) >= 10])

    fechas_sorted = sorted(list(set(fechas_disp)))
    f_min_def = datetime.strptime(fechas_sorted[0], "%Y-%m-%d").date() if fechas_sorted else datetime.now().date()
    f_max_def = datetime.strptime(fechas_sorted[-1], "%Y-%m-%d").date() if fechas_sorted else datetime.now().date()

    # Pre-enriquecer productividad hoy separando autorizaciones
    df_p_raw = pd.read_csv(file_prod_hoy) if file_prod_hoy.exists() else None
    df_p_operativo = None
    df_p_auth = None
    if df_p_raw is not None and not df_p_raw.empty:
        is_auth_p = df_p_raw["grupo"].astype(str).str.contains("Autorización|Supervisor|Autorizacion", case=False, na=False)
        df_p_operativo = df_p_raw[~is_auth_p].copy()
        df_p_auth = df_p_raw[is_auth_p].copy()

    df_enr_hoy = enriquecer_con_socio(df_p_operativo, solo_almacontact=False) if df_p_operativo is not None and not df_p_operativo.empty else pd.DataFrame()

    # Pre-cargar demanda diaria y balance de colas separando autorizaciones
    file_demanda = DATA_DIR / "demanda_diaria_colas.csv"
    df_demanda = pd.read_csv(file_demanda) if file_demanda.exists() else None
    df_demanda_operativo = None
    df_demanda_auth = None
    if df_demanda is not None and not df_demanda.empty:
        is_auth_dem = df_demanda["grupo"].astype(str).str.contains("Autorización|Supervisor|Autorizacion", case=False, na=False)
        df_demanda_operativo = df_demanda[~is_auth_dem].copy()
        df_demanda_auth = df_demanda[is_auth_dem].copy()

    # Pre-calcular dataset transaccional unificado para Calidad & SLAs (RWT <= 48h y FRT <= 24h)
    df_slas_alma = pd.DataFrame()
    df_slas_todos = pd.DataFrame()
    try:
        dfs_to_concat = []
        if file_parquet.exists():
            pq_cols = ["id", "status", "created_at", "updated_at", "grupo", "Fecha", "Tipo_de_Gestion", "TICKET_ASSIGNEE_PRIMARY_EMAIL", "Nombre_Asesor"]
            df_pq_sla = pd.read_parquet(file_parquet, columns=pq_cols)
            dfs_to_concat.append(df_pq_sla)
        if file_prod_hoy.exists():
            h_cols = ["id", "status", "created_at", "updated_at", "grupo", "Fecha", "Tipo_de_Gestion", "TICKET_ASSIGNEE_PRIMARY_EMAIL", "Nombre_Asesor"]
            df_h_sla = pd.read_csv(file_prod_hoy)
            exist_h_cols = [c for c in h_cols if c in df_h_sla.columns]
            dfs_to_concat.append(df_h_sla[exist_h_cols])

        if dfs_to_concat:
            df_all_sla = pd.concat(dfs_to_concat, ignore_index=True).drop_duplicates(subset=["id"])
            df_all_sla["c_dt"] = pd.to_datetime(df_all_sla["created_at"], utc=True)
            df_all_sla["u_dt"] = pd.to_datetime(df_all_sla["updated_at"], utc=True)
            dur = (df_all_sla["u_dt"] - df_all_sla["c_dt"]).dt.total_seconds() / 3600.0
            is_closed_long = (df_all_sla["status"] == "closed") & (dur >= 168.0)
            df_all_sla["res_hrs"] = dur.where(~is_closed_long, dur - 168.0).clip(lower=0.05)
            df_all_sla["cumple_rwt_48h"] = df_all_sla["res_hrs"] <= 48.0

            # Tiempo real de primera respuesta humana (FRT):
            # En tickets de Back Office, es el tiempo transcurrido desde que el pasajero crea el caso (created_at)
            # hasta que el asesor de Almacontact lo gestiona y emite su primera respuesta/resolución.
            df_all_sla["frt_hrs"] = df_all_sla["res_hrs"]
            df_all_sla["frt_min"] = df_all_sla["frt_hrs"] * 60.0
            df_all_sla["cumple_frt_24h"] = df_all_sla["frt_hrs"] <= 24.0

            # Excluir autorizaciones de supervisor del dataset operativo de SLA
            is_auth_sla = df_all_sla["grupo"].astype(str).str.contains("Autorización|Supervisor|Autorizacion", case=False, na=False)
            df_all_sla_op = df_all_sla[~is_auth_sla].copy()

            df_slas_todos = enriquecer_con_socio(df_all_sla_op, solo_almacontact=False)
            df_slas_alma = enriquecer_con_socio(df_all_sla_op, solo_almacontact=True)
    except Exception:
        pass

    return {
        "df_raw_enr_alma": df_raw_enr_alma,
        "df_raw_enr_todos": df_raw_enr_todos,
        "df_diario_enr_alma": df_diario_enr_alma,
        "df_diario_enr_todos": df_diario_enr_todos,
        "df_b_raw": df_b_operativo,
        "df_b_operativo": df_b_operativo,
        "df_b_auth": df_b_auth,
        "df_b_full": df_b_full,
        "m_resumen": m_resumen,
        "d_desglose": d_desglose,
        "df_p_raw": df_p_operativo,
        "df_p_operativo": df_p_operativo,
        "df_p_auth": df_p_auth,
        "df_enr_hoy": df_enr_hoy,
        "df_demanda": df_demanda_operativo,
        "df_demanda_operativo": df_demanda_operativo,
        "df_demanda_auth": df_demanda_auth,
        "df_auth_hist": df_auth_hist,
        "df_slas_alma": df_slas_alma,
        "df_slas_todos": df_slas_todos,
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
    st.markdown(
        """
        <div style="background: linear-gradient(90deg, #0f172a 0%, #1e293b 100%); padding: 16px 20px; border-radius: 12px; margin-bottom: 15px; border-left: 5px solid #10b981;">
            <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px;">
                <div>
                    <h3 style="color: #ffffff; margin: 0 0 4px 0; font-size: 20px;">🎫 Mesa Digital Zendesk • Backlog y Casos Especiales</h3>
                    <p style="color: #94a3b8; margin: 0; font-size: 13px;">
                        Monitoreo del canal escrito, segregación de Autorizaciones Supervisor, productividad diaria y cumplimiento de SLA
                    </p>
                </div>
                <div style="text-align: right; background: #334155; padding: 6px 14px; border-radius: 8px; border: 1px solid #475569;">
                    <span style="color: #34d399; font-size: 11px; font-weight: 700; text-transform: uppercase;">Soporte Digital</span><br>
                    <span style="color: #cbd5e1; font-size: 12px; font-weight: 600;">Zendesk Support</span>
                </div>
            </div>
        </div>
        """,
        unsafe_allow_html=True
    )

    # Cargar bundle optimizado y pre-enriquecido en memoria (instantáneo < 1ms tras carga inicial)
    bundle = cargar_bundle_zendesk()
    file_prod_hoy = DATA_DIR / "productividad_hoy_en_vivo.csv"
    file_b_vivo = DATA_DIR / "backlog_en_vivo.csv"
    file_diario = DATA_DIR / "productividad_diaria_fechas.csv"
    file_demanda = DATA_DIR / "demanda_diaria_colas.csv"

    # Barra superior de estado de sincronización y cortes horarios
    f_status = DATA_DIR / "sync_status.json"
    status_info = {}
    if f_status.exists():
        try:
            with open(f_status, "r", encoding="utf-8") as f:
                status_info = json.load(f)
        except Exception:
            pass

    latest_mtime = 0
    for f_chk in [file_b_vivo, file_prod_hoy, file_diario, file_demanda]:
        if f_chk.exists():
            latest_mtime = max(latest_mtime, f_chk.stat().st_mtime)

    hora_s = "Datos cargados"
    if latest_mtime > 0:
        try:
            import zoneinfo
            tz_col = zoneinfo.ZoneInfo("America/Bogota")
            dt_sync = datetime.fromtimestamp(latest_mtime, tz=zoneinfo.ZoneInfo("UTC")).astimezone(tz_col)
            hora_s = dt_sync.strftime('%d/%m/%Y %I:%M:%S %p')
        except Exception:
            hora_s = datetime.fromtimestamp(latest_mtime).strftime('%d/%m/%Y %I:%M:%S %p')

    ts_corte = status_info.get("timestamp_label", hora_s)
    next_c = status_info.get("next_sync_est", "Próxima hora")
    df_b_op_raw = bundle.get("df_b_operativo")
    if df_b_op_raw is not None and not df_b_op_raw.empty and "status" in df_b_op_raw.columns:
        bl_op_c = len(df_b_op_raw[df_b_op_raw["status"].isin(["new", "open", "hold"])])
        bl_pend_c = len(df_b_op_raw[df_b_op_raw["status"] == "pending"])
    else:
        bl_op_c = len(df_b_op_raw) if df_b_op_raw is not None else 0
        bl_pend_c = 0

    sol_op_c = len(bundle.get("df_p_operativo", [])) if bundle.get("df_p_operativo") is not None else 0
    bl_auth_c = len(bundle.get("df_b_auth", [])) if bundle.get("df_b_auth") is not None else 0
    sol_auth_c = len(bundle.get("df_p_auth", [])) if bundle.get("df_p_auth") is not None else 0

    col_h1, col_h2 = st.columns([3.5, 0.9])
    with col_h1:
        st.info(f"🕒 **Corte Horario Zendesk:** `{ts_corte}` *(Hora Col / UTC-5)* | 🚨 **Backlog Operativo:** `{bl_op_c:,}` | 🟡 **Pendientes (Cliente):** `{bl_pend_c:,}` | ✅ **Resueltos Hoy:** `{sol_op_c:,}` | 🛡️ **Autorizaciones:** `{bl_auth_c} cola / {sol_auth_c} hoy`")

    with col_h2:
        if st.button("🔄 Refrescar Vista", use_container_width=True, help="Limpia la memoria caché y recarga las métricas con el último corte disponible."):
            cargar_bundle_zendesk.clear()
            st.cache_data.clear()
            st.toast("✅ Vista de Zendesk recargada.")
            time.sleep(0.3)
            st.rerun()
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

    # Usar datos operativos reales filtrados por fecha para alimentar los desplegables
    if df_diario_enr is not None and not df_diario_enr.empty:
        d_base = df_diario_enr.copy()
        if fecha_ini and fecha_fin:
            f_ini_s = fecha_ini.strftime("%Y-%m-%d")
            f_fin_s = fecha_fin.strftime("%Y-%m-%d")
            d_base = d_base[(d_base["Fecha"] >= f_ini_s) & (d_base["Fecha"] <= f_fin_s)]
    elif df_enriquecido is not None and not df_enriquecido.empty:
        d_base = df_enriquecido.copy()
    else:
        d_base = pd.DataFrame()

    sel_servicio = "Todos"
    sel_coord = "Todos"
    sel_sup = "Todos"
    sel_asesor = "Todos"

    if d_base is not None and not d_base.empty:
        with c_f2:
            servicios_unicos = sorted([s for s in d_base["Servicio"].dropna().unique() if str(s).strip() and str(s).strip() not in ("nan", "None")])
            servicios = ["Todos"] + servicios_unicos
            sel_servicio = st.selectbox("🏢 Servicio:", servicios, index=0, key="zd_sel_srv")
        df_step1 = d_base if sel_servicio == "Todos" else d_base[d_base["Servicio"] == sel_servicio]

        with c_f3:
            coords_unicos = sorted([c for c in df_step1["Coordinador"].dropna().unique() if str(c).strip() and str(c).strip() not in ("nan", "None")])
            coordinadores = ["Todos"] + coords_unicos
            sel_coord = st.selectbox("👔 Coordinador:", coordinadores, index=0, key="zd_sel_coord")
        df_step2 = df_step1 if sel_coord == "Todos" else df_step1[df_step1["Coordinador"] == sel_coord]

        with c_f4:
            sups_unicos = sorted([s for s in df_step2["Supervisor"].dropna().unique() if str(s).strip() and str(s).strip() not in ("nan", "None")])
            supervisores = ["Todos"] + sups_unicos
            sel_sup = st.selectbox("🧑‍💼 Supervisor:", supervisores, index=0, key="zd_sel_sup")
        df_step3 = df_step2 if sel_sup == "Todos" else df_step2[df_step2["Supervisor"] == sel_sup]

        with c_sub2:
            asesores_disp = ["Todos"] + sorted([a for a in df_step3["Nombre_Asesor"].dropna().unique() if str(a).strip() and str(a).strip() not in ("nan", "None")])
            sel_asesor = st.selectbox("👤 Asesor Específico:", asesores_disp, index=0, key="zd_sel_asesor")
        df_filtrado = df_step3 if sel_asesor == "Todos" else df_step3[df_step3["Nombre_Asesor"] == sel_asesor]
    else:
        df_filtrado = None

    # Asignar df_diario_filtrado de acuerdo a la selección jerárquica
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

    # Asignar df_slas_filtrado con reactividad jerárquica y temporal completa
    df_slas_base = bundle.get("df_slas_alma" if solo_alma else "df_slas_todos")
    if df_slas_base is not None and not df_slas_base.empty:
        d_s = df_slas_base.copy()
        if fecha_ini and fecha_fin and "Fecha" in d_s.columns:
            f_ini_s = fecha_ini.strftime("%Y-%m-%d")
            f_fin_s = fecha_fin.strftime("%Y-%m-%d")
            d_s = d_s[(d_s["Fecha"] >= f_ini_s) & (d_s["Fecha"] <= f_fin_s)]
        if sel_servicio != "Todos":
            d_s = d_s[
                (d_s["Servicio"] == sel_servicio)
                | (d_s["grupo"] == sel_servicio)
                | (d_s["grupo"].map(MAPA_GRUPO_A_SERVICIO) == sel_servicio)
            ]
        if sel_coord != "Todos":
            d_s = d_s[d_s["Coordinador"] == sel_coord]
        if sel_sup != "Todos":
            d_s = d_s[d_s["Supervisor"] == sel_sup]
        if sel_asesor != "Todos":
            d_s = d_s[d_s["Nombre_Asesor"] == sel_asesor]
        df_slas_filtrado = d_s
    else:
        df_slas_filtrado = None

    # =========================================================================
    # MACRO-MÓDULOS EJECUTIVOS CONSOLIDADOS (5 PESTAÑAS ESTRATÉGICAS)
    # =========================================================================
    tab_zd_backlog, tab_zd_prod, tab_zd_slas, tab_zd_tipologia, tab_zd_demanda = st.tabs([
        "🚨 Backlog en Cola & Antigüedad",
        "📈 Productividad & Desempeño Operativo",
        "⏱️ Calidad & SLAs (Reopen, RWT, FRT)",
        "🏷️ Tipología & Distribución",
        "⚖️ Demanda & Balance (Inflow vs Outflow)"
    ])

    # -------------------------------------------------------------------------
    # MÓDULO 1: BACKLOG EN COLA & ANTIGÜEDAD
    # -------------------------------------------------------------------------
    with tab_zd_backlog:
        st.subheader("🚨 Monitoreo de Backlog en Cola y Matriz de Antigüedad")
        st.caption("Monitoreo en tiempo real del volumen activo en cola, distribución por estados operativos y matriz oficial de antigüedad.")

        df_b_src = bundle.get("df_b_raw")
        df_full = bundle.get("df_b_full")
        m_resumen = bundle.get("m_resumen")
        d_desglose = bundle.get("d_desglose")

        if df_full is not None and not df_full.empty:
            df_full_f = df_full.copy()

            # 1. Aplicar filtros generales superiores
            if solo_alma:
                is_alma = (
                    df_full_f["TICKET_ASSIGNEE_PRIMARY_EMAIL"].astype(str).str.contains(
                        r"almacontact|\.alma@|@almacontact|@outsourcing-account\.com", case=False, na=False
                    )
                    | (df_full_f["Nombre_Asesor"] == "Sin Asignar")
                    | (df_full_f["TICKET_ASSIGNEE_PRIMARY_EMAIL"].isna())
                )
                df_full_f = df_full_f[is_alma]

            if sel_servicio != "Todos":
                df_full_f = df_full_f[
                    (df_full_f["Servicio"] == sel_servicio)
                    | (df_full_f["grupo"] == sel_servicio)
                    | (df_full_f["grupo"].map(MAPA_GRUPO_A_SERVICIO) == sel_servicio)
                ]

            if sel_coord != "Todos":
                df_full_f = df_full_f[df_full_f["Coordinador"] == sel_coord]

            if sel_sup != "Todos":
                df_full_f = df_full_f[df_full_f["Supervisor"] == sel_sup]

            if sel_asesor != "Todos":
                norm_sel = normalizar(sel_asesor)
                toks_sel = set(norm_sel.split())
                def match_asesor(val):
                    if not val or pd.isna(val):
                        return False
                    n_val = normalizar(str(val))
                    if n_val == norm_sel:
                        return True
                    t_val = set(n_val.split())
                    return len(toks_sel.intersection(t_val)) >= 2
                df_full_f = df_full_f[df_full_f["Nombre_Asesor"].apply(match_asesor)]

            # 2. Filtro interactivo específico del módulo: Estado Operativo
            col_fb1, col_fb2 = st.columns([2.5, 2.5])
            with col_fb1:
                map_estados = {
                    "new": "Nuevo",
                    "open": "Abierto",
                    "pending": "Pendiente",
                    "hold": "En Espera",
                    "solved": "Resuelto",
                    "closed": "Cerrado"
                }
                if "Estado_Legible" not in df_full_f.columns:
                    df_full_f["Estado_Legible"] = df_full_f["status"].map(map_estados).fillna(df_full_f["status"].astype(str).str.title())

                opciones_estado = [
                    "🚨 Backlog Operativo (Nuevo, Abierto, En Espera)",
                    "Todos los Estados (Incluye Pendientes)",
                    "Abierto",
                    "Nuevo",
                    "En Espera",
                    "🟡 Pendiente (Esperando Cliente / Pasajero)"
                ]
                sel_b_est = st.selectbox("Filtrar por Estado Operativo:", opciones_estado, index=0, key="zd_sel_b_est_v4")

            # Conteo de pendientes antes de filtrar por estado operativo
            tot_pendientes_base = (df_full_f["status"] == "pending").sum()

            if sel_b_est == "🚨 Backlog Operativo (Nuevo, Abierto, En Espera)":
                df_full_f = df_full_f[df_full_f["status"].isin(["new", "open", "hold"])]
            elif sel_b_est == "🟡 Pendiente (Esperando Cliente / Pasajero)":
                df_full_f = df_full_f[df_full_f["status"] == "pending"]
            elif sel_b_est == "Abierto":
                df_full_f = df_full_f[df_full_f["status"] == "open"]
            elif sel_b_est == "Nuevo":
                df_full_f = df_full_f[df_full_f["status"] == "new"]
            elif sel_b_est == "En Espera":
                df_full_f = df_full_f[df_full_f["status"] == "hold"]

            # 3. Métricas Ejecutivas Reactivas (calculadas sobre df_full_f filtrado)
            tot_bl = len(df_full_f)
            c_48 = (df_full_f["Rango_Antiguedad"] == "<48H").sum() if tot_bl > 0 else 0
            c_15 = (df_full_f["Rango_Antiguedad"] == ">48H<=15DIAS").sum() if tot_bl > 0 else 0
            c_30 = (df_full_f["Rango_Antiguedad"] == ">15Y<=30DIAS").sum() if tot_bl > 0 else 0
            c_mas30 = (df_full_f["Rango_Antiguedad"] == ">30DIAS").sum() if tot_bl > 0 else 0
            c_reopen = (df_full_f["Es_Reopen"] == True).sum() if ("Es_Reopen" in df_full_f.columns and tot_bl > 0) else 0

            nuevos_bv = len(df_full_f[df_full_f["status"] == "new"]) if "status" in df_full_f.columns else 0
            abiertos_bv = len(df_full_f[df_full_f["status"] == "open"]) if "status" in df_full_f.columns else 0
            hold_bv = len(df_full_f[df_full_f["status"] == "hold"]) if "status" in df_full_f.columns else 0

            hay_filtro_b = (sel_servicio != "Todos" or sel_coord != "Todos" or sel_sup != "Todos" or sel_asesor != "Todos" or sel_b_est != "🚨 Backlog Operativo (Nuevo, Abierto, En Espera)")
            titulo_total = "🎯 Backlog Filtrado" if hay_filtro_b else "🚨 Backlog Operativo"

            k1, k2, k3, k4, k5, k6, k7 = st.columns(7)
            k1.metric(titulo_total, f"{tot_bl:,}", f"{nuevos_bv} N / {abiertos_bv} A / {hold_bv} H")
            k2.metric("🟢 Fresco (<48H)", f"{c_48:,}", f"{(c_48/tot_bl)*100:.1f}%" if tot_bl > 0 else "0.0%")
            k3.metric("🟡 Operativo (2 a 15 D)", f"{c_15:,}", f"{(c_15/tot_bl)*100:.1f}%" if tot_bl > 0 else "0.0%")
            k4.metric("🟠 En Riesgo (15 a 30 D)", f"{c_30:,}", f"{(c_30/tot_bl)*100:.1f}%" if tot_bl > 0 else "0.0%", delta_color="inverse")
            k5.metric("🔴 Crítico (>30 Días)", f"{c_mas30:,}", f"{(c_mas30/tot_bl)*100:.1f}%" if tot_bl > 0 else "0.0%", delta_color="inverse")
            k6.metric("🟡 Pendientes (Cliente)", f"{tot_pendientes_base:,}", "Fuera de Backlog")
            k7.metric("🔄 Reabiertos", f"{c_reopen:,}", f"{(c_reopen/tot_bl)*100:.1f}% de cola" if tot_bl > 0 else "0.0%", delta_color="inverse" if c_reopen > 0 else "normal")

            st.markdown("---")

            if tot_bl == 0:
                st.info("ℹ️ No se encontraron tickets en cola para la combinación de filtros seleccionada. Prueba seleccionando 'Todos los Estados' o ajustando el servicio superior.")

            # Gráficos ejecutivos
            col_g1, col_g2 = st.columns([3, 2])
            with col_g1:
                st.subheader("📊 Composición de Antigüedad por Cola")
                df_plot = df_full_f.groupby(["Servicio", "Rango_Antiguedad"]).size().reset_index(name="Tickets")
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
                    title="Distribución de Antigüedad por Cola"
                )
                fig_ant.update_layout(xaxis_tickangle=-30, height=430, margin=dict(l=10, r=10))
                st.plotly_chart(fig_ant, use_container_width=True)

            with col_g2:
                st.subheader("🏷️ Top Motivos en Backlog")
                campo_tip = "tipo_gestion" if "tipo_gestion" in df_full_f.columns else "Tipo_de_Gestion"
                if campo_tip in df_full_f.columns:
                    df_tip_b = df_full_f[campo_tip].value_counts().head(10).reset_index()
                    df_tip_b.columns = ["Tipología", "Tickets"]
                    fig_tip_b = px.bar(
                        df_tip_b.sort_values(by="Tickets", ascending=True),
                        x="Tickets",
                        y="Tipología",
                        orientation="h",
                        text="Tickets",
                        title="Tipologías Más Frecuentes en Espera"
                    )
                    fig_tip_b.update_layout(yaxis=dict(title=""), height=430, margin=dict(l=10, r=10))
                    st.plotly_chart(fig_tip_b, use_container_width=True)
                else:
                    st.info("Sin tipologías registradas.")

            # Matrices Oficiales de Antigüedad
            with st.expander("📑 Ver Matriz Oficial de Antigüedad (% FÁBRICA) y Desglose por Estado", expanded=False):
                if m_resumen is not None and not m_resumen.empty:
                    st.subheader("1. Matriz Resumen de Antigüedad (% FÁBRICA)")
                    ren_m = {
                        "<48H": "<48h",
                        ">48H<=15DIAS": "2-15d",
                        ">15Y<=30DIAS": "15-30d",
                        ">30DIAS": ">30d",
                        "FABRICA": "% Fábrica"
                    }
                    m_res_display = m_resumen.rename(columns=ren_m)

                    def destacar_fabrica(row):
                        if row.get("SERVICIO") == "FABRICA":
                            return ["background-color: #1F4E79; color: white; font-weight: bold;"] * len(row)
                        return [""] * len(row)
                    st.dataframe(m_res_display.style.apply(destacar_fabrica, axis=1), use_container_width=True, hide_index=True)

                if d_desglose is not None and not d_desglose.empty:
                    st.subheader("2. Desglose Operativo por Servicio y Estado")

                    c_mode1, c_mode2 = st.columns([2.5, 2.5])
                    with c_mode1:
                        modo_desglose = st.radio(
                            "Formato de visualización del Desglose:",
                            ["📱 Compacto (Casos y % en una celda)", "📊 Columnas Separadas"],
                            horizontal=True,
                            key="zd_modo_desglose"
                        )

                    rangos_map = [
                        ("<48H", "<48h"),
                        (">48H<=15DIAS", "2-15d"),
                        (">15Y<=30DIAS", "15-30d"),
                        (">30DIAS", ">30d")
                    ]

                    def estilo_desglose(row):
                        tipo = row.get("TIPO_FILA", "")
                        if tipo == "TOTAL":
                            return ["background-color: #002060; color: white; font-weight: bold;"] * len(row)
                        elif tipo == "TOTAL_PENDIENTE":
                            return ["background-color: #FFF2CC; color: #7F6000; font-weight: bold;"] * len(row)
                        elif tipo == "SERVICIO":
                            return ["background-color: #D9E1F2; color: #002060; font-weight: bold;"] * len(row)
                        elif tipo == "PENDIENTE_INFO":
                            return ["background-color: #FAFAFA; color: #8A6D3B; font-style: italic;"] * len(row)
                        return [""] * len(row)

                    if modo_desglose == "📱 Compacto (Casos y % en una celda)":
                        filas_c = []
                        for _, row in d_desglose.iterrows():
                            f = {
                                "SERVICIO": row["SERVICIO"],
                                "TIPO_FILA": row.get("TIPO_FILA", ""),
                                "SERVICIO_PADRE": row.get("SERVICIO_PADRE", "")
                            }
                            for r_orig, r_nuevo in rangos_map:
                                c = row.get(f"{r_orig} CASOS", 0)
                                p = row.get(f"{r_orig} % ANT.", "0,0%")
                                f[r_nuevo] = f"{c:,} ({p})" if c > 0 else "-"
                            f["Total"] = f"{row.get('Total CASOS', 0):,}"
                            filas_c.append(f)
                        df_d_show = pd.DataFrame(filas_c)
                        cols_c = ["SERVICIO", "<48h", "2-15d", "15-30d", ">30d", "Total"]

                        col_cfg = {
                            "SERVICIO": st.column_config.TextColumn("Servicio / Estado", width="medium"),
                            "<48h": st.column_config.TextColumn("<48h", width="small"),
                            "2-15d": st.column_config.TextColumn("2-15d", width="small"),
                            "15-30d": st.column_config.TextColumn("15-30d", width="small"),
                            ">30d": st.column_config.TextColumn(">30d", width="small"),
                            "Total": st.column_config.TextColumn("Total", width="small"),
                        }
                        st.dataframe(
                            df_d_show[["TIPO_FILA"] + cols_c].style.apply(estilo_desglose, axis=1),
                            use_container_width=True,
                            hide_index=True,
                            column_order=cols_c,
                            column_config=col_cfg
                        )
                    else:
                        ren_sep = {
                            "<48H CASOS": "<48h",
                            "<48H % ANT.": "% <48h",
                            ">48H<=15DIAS CASOS": "2-15d",
                            ">48H<=15DIAS % ANT.": "% 2-15d",
                            ">15Y<=30DIAS CASOS": "15-30d",
                            ">15Y<=30DIAS % ANT.": "% 15-30d",
                            ">30DIAS CASOS": ">30d",
                            ">30DIAS % ANT.": "% >30d",
                            "Total CASOS": "Total"
                        }
                        df_d_sep = d_desglose.rename(columns=ren_sep)
                        cols_sep = [c for c in df_d_sep.columns if c not in ["SERVICIO_PADRE", "TIPO_FILA", "ESTADO"]]
                        st.dataframe(
                            df_d_sep[["TIPO_FILA"] + cols_sep].style.apply(estilo_desglose, axis=1),
                            use_container_width=True,
                            hide_index=True,
                            column_order=cols_sep
                        )

            # Detalle individual de tickets
            st.subheader("📋 Detalle de Tickets en Cola de Espera (Hora Colombia UTC-5)")
            if "created_at" in df_full_f.columns:
                df_full_f["Fecha Creación (Hora Col)"] = df_full_f["created_at"].apply(formatear_colombia_dt)
            if "Es_Reopen" in df_full_f.columns:
                df_full_f["Indicador_Reopen"] = df_full_f["Es_Reopen"].apply(lambda x: "🔄 Reabierto" if x else "Normal")
            else:
                df_full_f["Indicador_Reopen"] = "Normal"

            cols_t = ["id", "subject", "Servicio", "Estado_Legible", "priority", "Indicador_Reopen", campo_tip, "Nombre_Asesor", "Fecha Creación (Hora Col)", "Rango_Antiguedad"]
            cols_exist = [c for c in cols_t if c in df_full_f.columns]
            st.dataframe(
                df_full_f[cols_exist].rename(columns={
                    "id": "Ticket ID",
                    "subject": "Asunto",
                    "Servicio": "Servicio / Cola",
                    "Estado_Legible": "Estado",
                    "priority": "Prioridad",
                    "Indicador_Reopen": "Reapertura",
                    campo_tip: "Tipología",
                    "Nombre_Asesor": "Asignado",
                    "Rango_Antiguedad": "Rango Antigüedad"
                }),
                use_container_width=True
            )
        else:
            st.info("No hay datos de backlog disponibles.")

    # -------------------------------------------------------------------------
    # MÓDULO 2: PRODUCTIVIDAD & DESEMPEÑO
    # -------------------------------------------------------------------------
    with tab_zd_prod:
        st.subheader("📈 Productividad Diaria, Cortes Intradía y Desempeño de Asesores")
        st.caption("Seguimiento de resolución histórica, cortes de turno de la jornada actual y desempeño individual enriquecido con Socio Maestro.")

        # Sección 1: Productividad Diaria del Periodo
        if df_diario_filtrado is not None and not df_diario_filtrado.empty:
            total_periodo = df_diario_filtrado["Recuento_Tickets"].sum()
            dias_activos = df_diario_filtrado[df_diario_filtrado["Fecha"] != "Sin Fecha"]["Fecha"].nunique()
            prom_dia = (total_periodo / dias_activos) if dias_activos > 0 else total_periodo
            asesores_activos = df_diario_filtrado["Nombre_Asesor"].nunique()

            # Métricas de hoy si están en el bundle
            df_p_raw = bundle.get("df_p_raw")
            total_hoy = len(df_p_raw) if df_p_raw is not None and not df_p_raw.empty else 0

            kd1, kd2, kd3, kd4 = st.columns(4)
            kd1.metric("Tickets en Periodo", f"{total_periodo:,.0f}")
            kd2.metric("Promedio Casos / Día", f"{prom_dia:,.1f}", f"{dias_activos} días activos")
            kd3.metric("Asesores Productivos", f"{asesores_activos:,}")
            kd4.metric("🎯 Resueltos Hoy", f"{total_hoy:,}", "Corte jornada en vivo")

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

            # Sección 2: Cortes Intradía de la Jornada
            st.markdown("---")
            st.subheader("⏱️ Seguimiento Intradía por Cortes Horarios (Jornada Actual)")
            st.caption("Casos resueltos hoy acumulados por asesor en cada corte horario, categorizados por Condición (ANTIGUO / NUEVO).")

            df_enr_hoy = bundle.get("df_enr_hoy")
            if df_p_raw is not None and not df_p_raw.empty:
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
                        "Cortes horarios a visualizar:",
                        options=list(cortes_opciones.keys()),
                        default=["8:00 AM", "10:00 AM", "2:00 PM"],
                        key="zd_sel_cortes_v2"
                    )
                    cortes_num = sorted([cortes_opciones[l] for l in sel_cortes_labels]) if sel_cortes_labels else [8, 10, 14]

                with col_ci2:
                    grupos_disp = ["Todos los Grupos"] + sorted(list(df_p_raw["grupo"].dropna().unique()))
                    sel_grp_intra = st.selectbox("Filtrar Grupo / TAG:", grupos_disp, key="zd_sel_grp_intra_v2")

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
                        height=420
                    )
                else:
                    st.info("No hay datos de cortes para el filtro seleccionado.")
            else:
                st.info("No hay casos resueltos registrados hoy.")

            # Sección 3: Desempeño y Expediente de Asesores
            st.markdown("---")
            st.subheader("👤 Ficha y Expediente de Asesores (Cruce Socio Maestro)")
            st.caption("Detalle individualizado por Coordinador, Supervisor y Servicio con métricas de tiempos y volumen.")

            df_as_summary = df_diario_filtrado.groupby(["Nombre_Asesor", "TICKET_ASSIGNEE_PRIMARY_EMAIL", "Supervisor", "Coordinador", "Servicio"]).agg(
                Total_Tickets=("Recuento_Tickets", "sum"),
                Tipologias=("Tipo_de_Gestion", "nunique") if "Tipo_de_Gestion" in df_diario_filtrado.columns else ("Nombre_Asesor", "count")
            ).reset_index().sort_values(by="Total_Tickets", ascending=False).reset_index(drop=True)

            st.dataframe(
                df_as_summary.rename(columns={
                    "Nombre_Asesor": "Asesor",
                    "TICKET_ASSIGNEE_PRIMARY_EMAIL": "Correo",
                    "Total_Tickets": "Casos Resueltos",
                    "Tipologias": "Tipologías Distintas"
                }).style.format({
                    "Casos Resueltos": "{:,.0f}",
                    "Tipologías Distintas": "{:,.0f}"
                }),
                use_container_width=True
            )
        else:
            st.info("No hay registros diarios disponibles para los filtros seleccionados.")

    # -------------------------------------------------------------------------
    # MÓDULO 3: CALIDAD & SLAS (REOPEN, RWT <= 48H, FRT <= 24H)
    # -------------------------------------------------------------------------
    with tab_zd_slas:
        st.subheader("⏱️ Acuerdos de Nivel de Servicio (SLAs) y Calidad Operativa")
        st.caption("Monitoreo ejecutivo de tiempos de primera respuesta (FRT <= 24h), resolución de casos (RWT <= 48h) y tasa de reapertura (Reopen).")

        # Base de datos de SLAs enriquecida
        # Base de datos de SLAs enriquecida
        df_slas = df_slas_filtrado if df_slas_filtrado is not None else pd.DataFrame()
        tot_t_slas = len(df_slas)

        # RWT <= 48h (Resolution Wait Time)
        if tot_t_slas > 0 and "cumple_rwt_48h" in df_slas.columns:
            pct_rwt = (df_slas["cumple_rwt_48h"].mean() * 100.0)
            med_rwt_val = df_slas["res_hrs"].median()
        else:
            pct_rwt = 0.0
            med_rwt_val = 0.0

        # FRT <= 24h (First Response Time)
        if tot_t_slas > 0 and "cumple_frt_24h" in df_slas.columns:
            pct_frt = (df_slas["cumple_frt_24h"].mean() * 100.0)
            med_frt_val = df_slas["frt_hrs"].median()
        else:
            pct_frt = 0.0
            med_frt_val = 0.0

        # Casos Reopen: tickets actualmente open que registran haber sido resueltos previamente
        reopen_df = df_full_f[df_full_f["Es_Reopen"] == True] if (df_full_f is not None and not df_full_f.empty and "Es_Reopen" in df_full_f.columns) else pd.DataFrame()
        tot_reopen = len(reopen_df)

        # Base para tasa % Reopen: Total resueltos en periodo / tickets auditados
        total_resueltos_base = tot_t_slas if tot_t_slas > 0 else (total_periodo if (df_diario_filtrado is not None and not df_diario_filtrado.empty and total_periodo > 0) else 1)
        tasa_reopen = (tot_reopen / total_resueltos_base * 100.0) if total_resueltos_base > 0 else 0.0

        # Tarjetas Ejecutivas de Calidad & SLA
        c_sla1, c_sla2, c_sla3, c_sla4 = st.columns(4)
        c_sla1.metric(
            "⏱️ % RWT <= 48h (Resolución)",
            f"{pct_rwt:.1f}%",
            f"{pct_rwt - 85.0:+.1f}% vs Meta 85%",
            delta_color="normal" if pct_rwt >= 85.0 else "inverse"
        )
        c_sla2.metric(
            "⚡ % FRT <= 24h (1ra Respuesta)",
            f"{pct_frt:.1f}%",
            f"{pct_frt - 95.0:+.1f}% vs Meta 95%",
            delta_color="normal" if pct_frt >= 95.0 else "inverse"
        )
        c_sla3.metric(
            "🔄 % Reopen (Tasa Reapertura)",
            f"{tasa_reopen:.2f}%",
            f"{tot_reopen} reabiertos / {total_resueltos_base:,.0f} resueltos",
            delta_color="normal" if tasa_reopen <= 5.0 else "inverse"
        )
        c_sla4.metric(
            "📋 Total Tickets Auditados",
            f"{tot_t_slas:,.0f}",
            f"Mediana Res: {med_rwt_val:.1f}h | FRT: {med_frt_val:.1f}h"
        )

        st.markdown("---")

        # ---------------------------------------------------------------------
        # SECCIÓN 1: AUDITORÍA DETALLADA DE REOPEN (CASOS REABIERTOS)
        # ---------------------------------------------------------------------
        st.subheader("🔄 Auditoría de Casos Reabiertos (Reopen Drilldown)")
        st.caption(
            "Casos que registraron fecha de resolución previa y cuyo estado actual regresó a **Open**. "
            "Representa retrabajo operativo e insatisfacción potencial del pasajero."
        )

        if tot_reopen > 0:
            c_r1, c_r2 = st.columns([1, 2])
            with c_r1:
                st.metric("Total Reabiertos en Cola", f"{tot_reopen:,}", f"{tasa_reopen:.2f}% de tasa")
                if "Servicio" in reopen_df.columns:
                    reopen_por_srv = reopen_df["Servicio"].value_counts().reset_index()
                    reopen_por_srv.columns = ["Servicio", "Reabiertos"]
                    st.dataframe(reopen_por_srv, use_container_width=True, hide_index=True)
            with c_r2:
                cols_reopen = ["id", "subject", "Servicio", "grupo", "Nombre_Asesor", "Fecha Creación (Hora Col)", "priority"]
                cols_reopen_exist = [c for c in cols_reopen if c in reopen_df.columns]
                st.dataframe(
                    reopen_df[cols_reopen_exist].rename(columns={
                        "id": "Ticket ID",
                        "subject": "Asunto",
                        "Servicio": "Servicio / Cola",
                        "grupo": "Cola Zendesk",
                        "Nombre_Asesor": "Asesor Asignado",
                        "Fecha Creación (Hora Col)": "Fecha Creación",
                        "priority": "Prioridad"
                    }),
                    use_container_width=True,
                    height=260
                )
        else:
            st.success("✅ ¡Excelente! No se detectan tickets reabiertos en estado Open para los filtros actuales.")

        st.markdown("---")

        # ---------------------------------------------------------------------
        # SECCIÓN 2: MATRIZ DE DESEMPEÑO SLA POR ASESOR Y LIDERAZGO
        # ---------------------------------------------------------------------
        st.subheader("📊 Matriz de Cumplimiento de SLAs por Asesor y Liderazgo")
        st.caption("Cumplimiento individualizado de RWT <= 48h y FRT <= 24h calculado con fechas exactas de resolución y primera respuesta.")

        if not df_slas.empty:
            df_as_sla = df_slas.groupby(["Nombre_Asesor", "Supervisor", "Coordinador", "Servicio"]).agg(
                Tickets=("id", "count"),
                Cumple_RWT=("cumple_rwt_48h", "sum"),
                Cumple_FRT=("cumple_frt_24h", "sum"),
                Mediana_RWT_hrs=("res_hrs", "median"),
                Mediana_FRT_hrs=("frt_hrs", "median")
            ).reset_index()

            df_as_sla["% RWT <= 48h"] = (df_as_sla["Cumple_RWT"] / df_as_sla["Tickets"] * 100.0).round(1)
            df_as_sla["% FRT <= 24h"] = (df_as_sla["Cumple_FRT"] / df_as_sla["Tickets"] * 100.0).round(1)
            df_as_sla = df_as_sla.sort_values(by="Tickets", ascending=False).reset_index(drop=True)

            def estilo_sla_val(val):
                if isinstance(val, (int, float)):
                    if val >= 90.0:
                        return "background-color: #C6EFCE; color: #006100; font-weight: bold;"
                    elif val >= 80.0:
                        return "background-color: #FFEB9C; color: #9C6500; font-weight: bold;"
                    else:
                        return "background-color: #FFC7CE; color: #9C0006; font-weight: bold;"
                return ""

            cols_as_sla = ["Nombre_Asesor", "Supervisor", "Coordinador", "Servicio", "Tickets", "% RWT <= 48h", "% FRT <= 24h", "Mediana_RWT_hrs", "Mediana_FRT_hrs"]
            styler_sla = df_as_sla[cols_as_sla].rename(columns={
                "Nombre_Asesor": "Asesor",
                "Tickets": "Tickets Resueltos",
                "Mediana_RWT_hrs": "Mediana Res. (hrs)",
                "Mediana_FRT_hrs": "Mediana FRT (hrs)"
            }).style

            # Compatibilidad universal: map (pandas >= 2.1) y applymap (pandas < 2.1)
            map_fn = getattr(styler_sla, "map", getattr(styler_sla, "applymap", None))
            if map_fn:
                styler_sla = map_fn(estilo_sla_val, subset=["% RWT <= 48h", "% FRT <= 24h"])

            styler_sla = styler_sla.format({
                "Tickets Resueltos": "{:,.0f}",
                "% RWT <= 48h": "{:.1f}%",
                "% FRT <= 24h": "{:.1f}%",
                "Mediana Res. (hrs)": "{:.1f}h",
                "Mediana FRT (hrs)": "{:.1f}h"
            })

            st.dataframe(
                styler_sla,
                use_container_width=True,
                height=350
            )

        # ---------------------------------------------------------------------
        # SECCIÓN 3: TIEMPOS DE RESPUESTA Y RESOLUCIÓN POR COLA OFICIAL
        # ---------------------------------------------------------------------
        st.markdown("---")
        st.subheader("⏱️ Tiempos de Respuesta y Resolución por Grupo Oficial (tiempos_respuesta_amc.csv)")
        file_tiempos = DATA_DIR / "tiempos_respuesta_amc.csv"
        if file_tiempos.exists():
            df_t = pd.read_csv(file_tiempos)
            col_t1, col_t2 = st.columns([3, 2])
            with col_t1:
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
                    title="Relación FRT (min) vs Tiempo de Resolución (hrs) - Escala Log"
                )
                fig_quad.update_layout(height=380, margin=dict(l=10, r=10))
                st.plotly_chart(fig_quad, use_container_width=True)
            with col_t2:
                st.dataframe(
                    df_t[["TICKET_GROUP_NAME", "Tickets", "Mediana_FRT_min", "Mediana_Resolucion_hrs"]]
                    .rename(columns={
                        "TICKET_GROUP_NAME": "Grupo Oficial",
                        "Tickets": "Tickets",
                        "Mediana_FRT_min": "FRT (min)",
                        "Mediana_Resolucion_hrs": "Resolución (hrs)"
                    })
                    .style.format({"Tickets": "{:,.0f}", "FRT (min)": "{:.1f}m", "Resolución (hrs)": "{:.1f}h"}),
                    use_container_width=True,
                    height=380
                )

    # -------------------------------------------------------------------------
    # MÓDULO 4: TIPOLOGÍA & DISTRIBUCIÓN
    # -------------------------------------------------------------------------
    with tab_zd_tipologia:
        st.subheader("🏷️ Tipología de Gestión y Motivos de Contacto")
        st.caption("Análisis de distribución de tipologías atendidas, diagrama de Pareto (80/20) y detección de casos sin tipificar.")

        df_tip_src = df_diario_filtrado if (df_diario_filtrado is not None and not df_diario_filtrado.empty) else df_filtrado
        if df_tip_src is not None and not df_tip_src.empty and "Tipo_de_Gestion" in df_tip_src.columns:
            df_tip = df_tip_src.groupby("Tipo_de_Gestion").agg(
                Tickets=("Recuento_Tickets", "sum"),
                Asesores=("Nombre_Asesor", "nunique")
            ).reset_index().sort_values(by="Tickets", ascending=False).reset_index(drop=True)

            total_t_tip = df_tip["Tickets"].sum()
            df_tip["Pct_Participacion"] = (df_tip["Tickets"] / total_t_tip * 100.0) if total_t_tip > 0 else 0.0
            df_tip["Pct_Acumulado"] = df_tip["Pct_Participacion"].cumsum()

            # Detección de casos sin tipificar
            mask_sin_tip = df_tip["Tipo_de_Gestion"].astype(str).str.strip().str.lower().isin(["sin tipificar", "pendiente", "sin asignar", "", "none", "nan"])
            casos_sin_tip = df_tip[mask_sin_tip]["Tickets"].sum() if mask_sin_tip.any() else 0
            casos_tipificados = total_t_tip - casos_sin_tip
            pct_apego = (casos_tipificados / total_t_tip * 100.0) if total_t_tip > 0 else 100.0

            c_tp1, c_tp2, c_tp3, c_tp4 = st.columns(4)
            c_tp1.metric("🏷️ Tipologías Únicas", f"{len(df_tip):,}")
            c_tp2.metric("✅ Casos Tipificados", f"{casos_tipificados:,.0f}", f"{pct_apego:.1f}% de apego")
            c_tp3.metric(
                "⚠️ Sin Tipificar / Pendiente",
                f"{casos_sin_tip:,.0f}",
                f"{100.0 - pct_apego:.1f}% del total",
                delta_color="inverse" if casos_sin_tip > 0 else "normal"
            )
            top1_nombre = df_tip.iloc[0]["Tipo_de_Gestion"] if not df_tip.empty else "-"
            top1_pct = df_tip.iloc[0]["Pct_Participacion"] if not df_tip.empty else 0.0
            c_tp4.metric(f"Top 1: {str(top1_nombre)[:18]}...", f"{top1_pct:.1f}%", "Mayor volumen")

            st.markdown("---")

            # Pareto (Curva 80/20) y Gráfico Donut
            c_par1, c_par2 = st.columns([3, 2])
            with c_par1:
                st.subheader("📊 Diagrama de Pareto de Tipologías (Top 15)")
                top_15_tip = df_tip.head(15).copy()
                fig_pareto = go.Figure()
                fig_pareto.add_trace(go.Bar(
                    x=top_15_tip["Tipo_de_Gestion"],
                    y=top_15_tip["Tickets"],
                    name="Tickets Resueltos",
                    marker_color="#1F4E79",
                    text=top_15_tip["Tickets"],
                    textposition="auto"
                ))
                fig_pareto.add_trace(go.Scatter(
                    x=top_15_tip["Tipo_de_Gestion"],
                    y=top_15_tip["Pct_Acumulado"],
                    name="% Acumulado (Pareto)",
                    yaxis="y2",
                    mode="lines+markers",
                    line=dict(color="#E53935", width=3)
                ))
                fig_pareto.add_hline(y=80, line_dash="dash", line_color="orange", yref="y2", annotation_text="Línea 80% Pareto")
                fig_pareto.update_layout(
                    height=430,
                    margin=dict(l=10, r=10),
                    yaxis=dict(title="Volumen Tickets"),
                    yaxis2=dict(title="% Acumulado", overlaying="y", side="right", range=[0, 105]),
                    xaxis=dict(tickangle=-45),
                    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
                )
                st.plotly_chart(fig_pareto, use_container_width=True)

            with c_par2:
                st.subheader("🍰 Distribución Top 8 Tipologías")
                fig_pie_tip = px.pie(
                    df_tip.head(8),
                    values="Tickets",
                    names="Tipo_de_Gestion",
                    hole=0.45,
                    title="Participación de Top 8 Motivos"
                )
                fig_pie_tip.update_layout(height=430, margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h", yanchor="bottom", y=-0.2))
                st.plotly_chart(fig_pie_tip, use_container_width=True)

            # Matriz Cruzada: Tipología vs Servicio
            st.markdown("---")
            st.subheader("📋 Matriz Cruzada: Tipología vs Servicio")
            if "Servicio" in df_tip_src.columns:
                pivot_tip = df_tip_src.pivot_table(
                    index="Tipo_de_Gestion",
                    columns="Servicio",
                    values="Recuento_Tickets",
                    aggfunc="sum",
                    fill_value=0
                )
                pivot_tip["Total"] = pivot_tip.sum(axis=1)
                pivot_tip = pivot_tip.sort_values(by="Total", ascending=False).head(20)
                st.dataframe(pivot_tip.style.format("{:,.0f}"), use_container_width=True)

            # Tabla completa
            st.subheader("📑 Catálogo Completo de Tipologías Atendidas")
            st.dataframe(
                df_tip.rename(columns={
                    "Tipo_de_Gestion": "Tipología",
                    "Tickets": "Tickets Resueltos",
                    "Asesores": "Asesores Involucrados",
                    "Pct_Participacion": "% Participación",
                    "Pct_Acumulado": "% Acumulado"
                }).style.format({
                    "Tickets Resueltos": "{:,.0f}",
                    "Asesores Involucrados": "{:,.0f}",
                    "% Participación": "{:.2f}%",
                    "% Acumulado": "{:.2f}%"
                }),
                use_container_width=True,
                height=350
            )
        else:
            st.info("No hay datos de tipología disponibles para los filtros seleccionados.")

    # -------------------------------------------------------------------------
    # MÓDULO 5: DEMANDA & BALANCE (INFLOW VS OUTFLOW) Y AUTORIZACIONES
    # -------------------------------------------------------------------------
    with tab_zd_demanda:
        st.subheader("⚖️ Demanda Diaria, Capacidad Resuelta y Control de Autorizaciones")
        st.caption("Monitorea la cantidad de casos nuevos que ingresan día a día (Inflow) contra la capacidad de resolución (Outflow), el balance neto del backlog y el control de códigos de involuntario.")

        df_dem = bundle.get("df_demanda")
        if df_dem is not None and not df_dem.empty:
            df_dem_f = df_dem.copy()
            if fecha_ini and fecha_fin:
                f_ini_s = fecha_ini.strftime("%Y-%m-%d")
                f_fin_s = fecha_fin.strftime("%Y-%m-%d")
                df_dem_f = df_dem_f[(df_dem_f["Fecha"] >= f_ini_s) & (df_dem_f["Fecha"] <= f_fin_s)]

            col_dem1, col_dem2 = st.columns([2, 1])
            with col_dem1:
                colas_disp = ["Todas las Colas"] + sorted(list(df_dem_f["grupo"].dropna().unique()))
                sel_cola_dem = st.selectbox("Filtrar Cola / Servicio:", colas_disp, key="zd_dem_cola_v2")
            with col_dem2:
                vista_dem = st.radio("Métrica Principal:", ["Casos Nuevos (Demanda)", "Balance (Entradas vs Resueltos)"], horizontal=True, key="zd_dem_vista_v2")

            if sel_cola_dem != "Todas las Colas":
                df_dem_f = df_dem_f[df_dem_f["grupo"] == sel_cola_dem]

            # KPIs de Demanda y Balance
            tot_nuevos = df_dem_f["Casos_Nuevos"].sum()
            tot_resueltos = df_dem_f["Casos_Resueltos"].sum()
            balance_neto = df_dem_f["Balance_Neto"].sum()
            dias_dem = df_dem_f["Fecha"].nunique()
            prom_ingresos_dia = (tot_nuevos / dias_dem) if dias_dem > 0 else 0

            k1, k2, k3, k4 = st.columns(4)
            k1.metric("📥 Total Casos Nuevos (Inflow)", f"{tot_nuevos:,.0f}", "Demanda en periodo")
            k2.metric("📈 Promedio Nuevos / Día", f"{prom_ingresos_dia:,.1f}", f"{dias_dem} días activos")
            k3.metric("📤 Casos Resueltos (Outflow)", f"{tot_resueltos:,.0f}", "Productividad en periodo")
            k4.metric(
                "⚖️ Balance Neto de Backlog",
                f"{balance_neto:+,.0f}",
                "Creció backlog" if balance_neto > 0 else "Desahogo de cola",
                delta_color="inverse" if balance_neto > 0 else "normal"
            )

            st.markdown("---")

            # Gráfico de Balance Inflow vs Outflow
            st.subheader("⚖️ Balance Operativo Día a Día: Entrada vs Salida (Inflow vs Outflow)")
            df_comp_dia = df_dem_f.groupby("Fecha")[["Casos_Nuevos", "Casos_Resueltos", "Balance_Neto"]].sum().reset_index()
            fig_comp = go.Figure()
            fig_comp.add_trace(go.Bar(x=df_comp_dia["Fecha"], y=df_comp_dia["Casos_Nuevos"], name="📥 Casos Nuevos (Entrada)", marker_color="#1E88E5"))
            fig_comp.add_trace(go.Bar(x=df_comp_dia["Fecha"], y=df_comp_dia["Casos_Resueltos"], name="📤 Casos Resueltos (Salida)", marker_color="#43A047"))
            fig_comp.add_trace(go.Scatter(x=df_comp_dia["Fecha"], y=df_comp_dia["Balance_Neto"], name="⚖️ Balance Neto (Inflow - Outflow)", mode="lines+markers", line=dict(color="#E53935", width=3)))
            fig_comp.update_layout(
                title="Balance Diario de Cola: Demanda de Entrada vs Capacidad Resuelta",
                barmode="group",
                height=420,
                margin=dict(l=10, r=10),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
            )
            st.plotly_chart(fig_comp, use_container_width=True)

            c_g1, c_g2 = st.columns([3, 2])
            with c_g1:
                st.subheader("📈 Evolución de Casos Nuevos por Día")
                df_dem_dia = df_dem_f.groupby(["Fecha", "grupo"])["Casos_Nuevos"].sum().reset_index()
                fig_dem_dia = px.bar(
                    df_dem_dia,
                    x="Fecha",
                    y="Casos_Nuevos",
                    color="grupo",
                    barmode="stack",
                    text="Casos_Nuevos",
                    title="Nuevos Casos por Día y Cola"
                )
                fig_dem_dia.update_layout(height=420, margin=dict(l=10, r=10), legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
                st.plotly_chart(fig_dem_dia, use_container_width=True)

            with c_g2:
                st.subheader("🍰 Carga de Demanda por Cola")
                df_pie_dem = df_dem_f.groupby("grupo")["Casos_Nuevos"].sum().reset_index().sort_values(by="Casos_Nuevos", ascending=False)
                fig_pie = px.pie(
                    df_pie_dem,
                    names="grupo",
                    values="Casos_Nuevos",
                    hole=0.45,
                    title="% Distribución de Entrada"
                )
                fig_pie.update_layout(height=420, margin=dict(l=10, r=10), legend=dict(orientation="h", yanchor="bottom", y=-0.2))
                st.plotly_chart(fig_pie, use_container_width=True)

            # Registro detallado de demanda por cola
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
        # SECCIÓN INTEGRADA: MONITOR DE AUTORIZACIONES DE SUPERVISOR
        # ---------------------------------------------------------------------
        st.markdown("---")
        with st.expander("🛡️ Monitor de Autorizaciones de Supervisor (Códigos de Involuntario)", expanded=True):
            st.caption("Segregación y control de autorizaciones de códigos de involuntario gestionadas por supervisores.")

            df_auth_hist = bundle.get("df_auth_hist")
            if df_auth_hist is not None and not df_auth_hist.empty:
                if fecha_ini and fecha_fin:
                    f_ini_s = fecha_ini.strftime("%Y-%m-%d")
                    f_fin_s = fecha_fin.strftime("%Y-%m-%d")
                    df_auth_hist = df_auth_hist[(df_auth_hist["Fecha"] >= f_ini_s) & (df_auth_hist["Fecha"] <= f_fin_s)]

            df_b_auth = bundle.get("df_b_auth")

            tot_auth_resueltas = df_auth_hist["Recuento_Tickets"].sum() if df_auth_hist is not None and not df_auth_hist.empty else 0
            tot_amc = df_auth_hist[df_auth_hist["grupo"].str.contains("AMC", case=False, na=False) & ~df_auth_hist["grupo"].str.contains("HVC", case=False, na=False)]["Recuento_Tickets"].sum() if df_auth_hist is not None and not df_auth_hist.empty else 0
            tot_hvc = df_auth_hist[df_auth_hist["grupo"].str.contains("HVC", case=False, na=False)]["Recuento_Tickets"].sum() if df_auth_hist is not None and not df_auth_hist.empty else 0
            tot_bl_auth = len(df_b_auth) if df_b_auth is not None and not df_b_auth.empty else 0

            # KPIs Autorizaciones
            ka1, ka2, ka3, ka4 = st.columns(4)
            ka1.metric("Autorizaciones Resueltas", f"{tot_auth_resueltas:,.0f}", "Periodo seleccionado")
            ka2.metric("Supervisor AMC (Regular)", f"{tot_amc:,.0f}", f"{(tot_amc/tot_auth_resueltas*100):.1f}%" if tot_auth_resueltas > 0 else "0%")
            ka3.metric("Supervisor HVC AMC ES (VIP)", f"{tot_hvc:,.0f}", f"{(tot_hvc/tot_auth_resueltas*100):.1f}%" if tot_auth_resueltas > 0 else "0%")
            ka4.metric("🚨 Pendientes en Cola Ahora", f"{tot_bl_auth:,}", "En vivo", delta_color="inverse" if tot_bl_auth > 0 else "normal")

            if df_auth_hist is not None and not df_auth_hist.empty:
                ca1, ca2 = st.columns([3, 2])
                with ca1:
                    st.subheader("📈 Evolución Diaria de Autorizaciones")
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
                    fig_auth_dia.update_layout(height=380, margin=dict(l=10, r=10), legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
                    st.plotly_chart(fig_auth_dia, use_container_width=True)

                with ca2:
                    st.subheader("🧑‍💼 Supervisores con Más Aprobaciones")
                    df_sup_top = df_auth_hist.groupby(["Nombre_Asesor", "grupo"])["Recuento_Tickets"].sum().reset_index().sort_values(by="Recuento_Tickets", ascending=False).head(10)
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
                    fig_sup_top.update_layout(height=380, yaxis=dict(title=""), margin=dict(l=10, r=10), legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1))
                    st.plotly_chart(fig_sup_top, use_container_width=True)

            # Backlog de autorizaciones en vivo
            if df_b_auth is not None and not df_b_auth.empty:
                st.subheader("🚨 Backlog en Cola de Autorizaciones en Vivo")
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

    # -------------------------------------------------------------------------
    # PIE DE PÁGINA: GLOSARIO Y GUÍA OPERATIVA (EXPANDIBLE E INFORMATIVO)
    # -------------------------------------------------------------------------
    st.markdown("---")
    with st.expander("📚 Glosario de Términos y Guía Operativa — Zendesk Support", expanded=False):
        st.caption("Diccionario técnico y operativo de todas las métricas, dimensiones, estados y reglas de cálculo del módulo.")

        with st.expander("🎫 1. Conceptos Básicos y Ciclo de Vida del Ticket", expanded=True):
            st.markdown("""
- **Ticket / Caso**: Unidad transaccional individual de atención en Zendesk que agrupa toda la interacción entre un usuario/pasajero y la aerolínea/operación de Almacontact.
- **Ciclo de Vida de los Estados (`Status`)**:
  - `New (Nuevo)`: El ticket acaba de ingresar a Zendesk y aún no ha sido respondido ni tomado por ningún asesor.
  - `Open (Abierto)`: El ticket está asignado a un asesor o cola y requiere atención o gestión operativa activa.
  - `Pending (Pendiente)`: El asesor respondió al usuario y se encuentra a la espera de información adicional o documentos del cliente.
  - `Hold (En Espera)`: El ticket está pausado internamente a la espera de una respuesta de un tercero o área externa (ej: Mantenimiento, Fraude, Equipajes Central).
  - `Solved (Resuelto)`: El asesor completó la gestión y envió la solución al cliente. En este estado se computa la **Productividad Operativa**.
  - `Closed (Cerrado)`: Tras permanecer entre 3 y 5 días en estado *Solved* sin que el usuario reabra el caso, Zendesk lo sella de forma inmutable y definitiva.
- **Diferencia Clave entre Solved y Closed**: Para efectos de productividad histórica y reportería acumulada, la consulta considera `status >= solved` para capturar tanto los casos recientemente resueltos como los que ya archivó el sistema.
            """)

        with st.expander("📥 2. Demanda de Entrada vs Capacidad de Salida (Inflow vs Outflow)", expanded=True):
            st.markdown("""
- **Demanda Diaria (Inflow / Casos Nuevos)**:
  - Se calcula a partir del timestamp exacto de creación del ticket (`created_at`) convertido a zona horaria **Colombia (UTC-5 / America/Bogota)**.
  - Representa el volumen real de solicitudes que la operación recibe cada día en cada cola.
- **Capacidad de Salida (Outflow / Casos Resueltos)**:
  - Se calcula según la fecha en que el caso fue marcado como `Solved`.
  - Representa el volumen de tickets resueltos por la fuerza operativa.
- **Balance Neto de Backlog (`Inflow - Outflow`)**:
  - **Balance Positivo (+)**: Entraron más casos de los que se resolvieron. El backlog de la cola **aumenta**.
  - **Balance Negativo (-)**: La operación resolvió más casos de los que ingresaron. Se genera **desahogo de cola**.
  - **Balance Neutro (0)**: Operación en equilibrio perfecto.
            """)

        with st.expander("⏱️ 3. Métricas de Tiempo, Calidad y Acuerdos de Nivel de Servicio (SLAs)", expanded=True):
            st.markdown(r"""
- **% FRT <= 24h (First Response Time SLA)**:
  - Porcentaje de casos en los cuales la operación brindó la **primera respuesta pública del asesor** al usuario dentro de las **primeras 24 horas** calendario desde la creación del ticket.
  - Meta estándar Almacontact: **>= 95.0%**.
  - Excluye respuestas automáticas del sistema o mensajes iniciales del pasajero.
- **% RWT <= 48h (Resolution Wait Time SLA)**:
  - Porcentaje de casos resueltos de forma definitiva dentro de las **primeras 48 horas** calendario.
  - Meta estándar Almacontact: **>= 85.0%**.
  - Mide la eficiencia y velocidad de cierre del servicio en primera instancia.
- **% Reopen (Tasa de Reapertura de Casos)**:
  - Casos que habiendo sido marcados previamente con fecha de resolución (`Solved`), regresan a estado activo `Open` debido a que el cliente envió una nueva comunicación o reapertura.
  - Fórmula:
    $$\% \\text{Reopen} = \\frac{\\text{Casos Reabiertos en Estado Open}}{\\text{Total Casos Resueltos}} \\times 100$$
  - Meta estándar Almacontact: **<= 5.0%**.
  - Mide el retrabajo operativo, la efectividad de la primera resolución y la calidad de la respuesta entregada al pasajero.
- **FRT (First Response Time / Tiempo de Primera Respuesta)**:
  - Tiempo transcurrido (en minutos u horas) desde que el ticket se crea hasta que el primer asesor envía una respuesta pública al cliente.
- **RWT (Requester Wait Time / Tiempo de Espera del Usuario)**:
  - Tiempo acumulado (en horas) que el pasajero pasa esperando una respuesta de Almacontact mientras el caso está en estados activos (`New` u `Open`).
- **Mediana vs Promedio**: En este panel se reporta la **Mediana** de tiempos en lugar del promedio simple, garantizando inmunidad ante valores atípicos y distorsiones estadísticas.
            """)

        with st.expander("⏳ 4. Matriz de Antigüedad del Backlog Operativo", expanded=True):
            st.markdown("""
Distribución de los tickets que actualmente permanecen sin resolver en Zendesk, según su fecha de creación:
- **`< 48H`**: Casos dentro de la ventana de atención primaria o SLA estándar.
- **`> 48H <= 15 DÍAS`**: Backlog en etapa intermedia que requiere seguimiento preventivo.
- **`> 15 Y <= 30 DÍAS`**: Casos demorados o con escalamientos complejos que requieren aceleración.
- **`> 30 DÍAS`**: Backlog crítico / deuda operacional acumulada con alto riesgo de insatisfacción o penalidad.
            """)

        with st.expander("⏱️ 5. Cortes Intradía y Segmentación de Asesores", expanded=True):
            st.markdown("""
- **Cortes Intradía**: Mediciones horarias acumuladas (ej: 8:00 a. m., 10:00 a. m., 2:00 p. m. Hora Col) que permiten al equipo de GTR y Supervisión verificar la cadencia horaria de resolución de cada asesor en tiempo real.
- **Condición del Asesor (Curva de Aprendizaje)**:
  - **`NUEVO`**: Asesor con **90 días o menos** de antigüedad en la operación. Se evalúa con metas de productividad adaptadas a su curva de maduración.
  - **`ANTIGUO`**: Asesor con **más de 90 días** de antigüedad, con dominio pleno de procesos y tipologías.
            """)

        with st.expander("🏢 6. Colas de Atención y Segregación de Autorizaciones", expanded=True):
            st.markdown("""
- **Colas Operativas de Asesores (Atención Directa)**:
  - `LUA AMC`: Soporte integral de reservas, emisiones y cambios para pasajeros.
  - `DT FFP AMC`: Programa de lealtad y viajero frecuente (canjes, millas, cuentas).
  - `Equipajes AMC SSC`: Indemnizaciones, rastreo y reclamos de equipaje demorado o dañado.
  - `Célula PI AMC ES`: Protección involuntaria y gestiones especiales de reprogramación.
  - `Autorización SOP LUA AMC`: Cola técnica de segundo nivel de soporte.
- **🛡️ Colas de Autorización de Supervisor (Códigos de Involuntario)**:
  - `Autorización Supervisor AMC` y `Autorización Supervisor HVC AMC ES`: Colas exclusivas donde los supervisores aprueban códigos de exoneración e involuntarios solicitados por los agentes.
  - **Regla de Negocio**: Estas autorizaciones se excluyen automáticamente de la productividad operativa de los asesores mediante el filtro superior, evitando que se mezclen gestiones administrativas de liderazgo con la productividad de línea. Cuentan con su propia pestaña de control dedicado.
            """)

        with st.expander("🏷️ 7. Tipologías de Gestión y Enriquecimiento Socio Maestro", expanded=False):
            st.markdown("""
- **Tipología de Gestión (`custom_fields`)**: Campo estructurado de Zendesk (`15124153673235`) donde el asesor categoriza el motivo raíz del contacto (ej: *Compras en LATAM*, *Reclamo Equipaje*, *Endoso Ley Chile*).
- **Cruce con Socio Maestro**: Cruce automático del correo del asesor con la base de datos de Gestión Humana de Almacontact para asignar en memoria y sin latencia su **Servicio**, **Coordinador**, **Supervisor** y **Condición de Antigüedad**.
            """)
