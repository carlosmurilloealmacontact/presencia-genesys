"""
Motor de analisis y procesamiento de datos de Casos y Backlog de Salesforce.
Filtra exclusivamente la operacion de AMC y calcula SLAs, Antiguedad (Aging) y Productividad.
"""

import os
import glob
import pandas as pd
import numpy as np
from datetime import datetime

SALESFORCE_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "salesforce")


def get_latest_salesforce_file():
    """Obtiene la ruta del archivo de reporte de casos mas reciente (excluyendo caches generados)."""
    raw_files = [
        f for f in glob.glob(os.path.join(SALESFORCE_DATA_DIR, "*.xlsx")) + glob.glob(os.path.join(SALESFORCE_DATA_DIR, "*.csv"))
        if not os.path.basename(f).startswith("cases_amc_cleaned")
    ]
    if not raw_files:
        return None
    # Ordenar por fecha de modificacion descendente
    raw_files.sort(key=os.path.getmtime, reverse=True)
    return raw_files[0]


def load_and_clean_cases_data(file_path=None):
    """
    Carga el reporte exportado de Salesforce o la base preprocesada de casos AMC.
    """
    cache_pkl = os.path.join(SALESFORCE_DATA_DIR, "cases_amc_cleaned.pkl")
    cache_csv = os.path.join(SALESFORCE_DATA_DIR, "cases_amc_cleaned.csv")

    if file_path is None:
        file_path = get_latest_salesforce_file()

    # Si no hay archivo raw de reporte disponible (ej. entorno de despliegue en la nube)
    if file_path is None or not os.path.exists(file_path):
        if os.path.exists(cache_pkl):
            try:
                return pd.read_pickle(cache_pkl)
            except Exception as e:
                print(f"[!] Error leyendo pickle: {e}")
        if os.path.exists(cache_csv):
            try:
                df_csv = pd.read_csv(cache_csv)
                if "Fecha_Inicio_dt" in df_csv.columns:
                    df_csv["Fecha_Inicio_dt"] = pd.to_datetime(df_csv["Fecha_Inicio_dt"], errors="coerce")
                if "Fecha_Finalizacion_dt" in df_csv.columns:
                    df_csv["Fecha_Finalizacion_dt"] = pd.to_datetime(df_csv["Fecha_Finalizacion_dt"], errors="coerce")
                return df_csv
            except Exception as e:
                print(f"[!] Error leyendo csv cache: {e}")
        return pd.DataFrame()

    # Si hay archivo raw y el caché pkl es más reciente:
    if os.path.exists(cache_pkl):
        try:
            if os.path.getmtime(cache_pkl) >= os.path.getmtime(file_path):
                return pd.read_pickle(cache_pkl)
        except Exception:
            pass

    is_excel = file_path.endswith(".xlsx") or file_path.endswith(".xls")

    if is_excel:
        # Detectar la fila de encabezado leyendo las primeras 25 filas
        df_preview = pd.read_excel(file_path, header=None, nrows=25)
        header_row = 13  # Default estandar observado en el reporte
        for idx, row in df_preview.iterrows():
            row_str = " ".join([str(v) for v in row.dropna()]).lower()
            if "work queue" in row_str or "número del caso" in row_str or "numero del caso" in row_str:
                header_row = idx
                break

        df = pd.read_excel(file_path, skiprows=header_row)
    else:
        # Archivo CSV
        df = pd.read_csv(file_path, skiprows=13, encoding="utf-8", encoding_errors="replace")

    # Limpieza de nombres de columnas (remover flechas de ordenamiento y espacios)
    df.columns = [str(c).replace("↑", "").replace("↓", "").strip() for c in df.columns]

    # Forward fill en columnas de agrupacion que Salesforce deja en blanco
    if "Work Queue Control" in df.columns:
        df["Work Queue Control"] = df["Work Queue Control"].ffill()
    if "Fecha/Hora de cierre" in df.columns:
        df["Fecha/Hora de cierre"] = df["Fecha/Hora de cierre"].ffill()

    # Filtrar unicamente colas de AMC (se descarta Brasil, etc.)
    if "Work Queue Control" in df.columns:
        df = df[df["Work Queue Control"].astype(str).str.contains("AMC", case=False, na=False)].copy()

    # Parseo de fechas
    now = datetime.now()
    if "Fecha de inicio" in df.columns:
        df["Fecha_Inicio_dt"] = pd.to_datetime(df["Fecha de inicio"], errors="coerce", dayfirst=True)
        # Antiguedad en dias y horas
        df["Antiguedad_Dias"] = (now - df["Fecha_Inicio_dt"]).dt.total_seconds() / (24 * 3600)
        df["Antiguedad_Horas"] = (now - df["Fecha_Inicio_dt"]).dt.total_seconds() / 3600
        df["Antiguedad_Dias"] = df["Antiguedad_Dias"].apply(lambda x: max(0, x) if pd.notna(x) else 0)
    else:
        df["Fecha_Inicio_dt"] = pd.NaT
        df["Antiguedad_Dias"] = 0
        df["Antiguedad_Horas"] = 0

    if "Fecha de finalización" in df.columns:
        df["Fecha_Finalizacion_dt"] = pd.to_datetime(df["Fecha de finalización"], errors="coerce", dayfirst=True)
    else:
        df["Fecha_Finalizacion_dt"] = pd.NaT

    # Normalizar columna de Infraccion (SLA)
    if "Infracción" in df.columns:
        df["Es_Infraccion"] = df["Infracción"].apply(lambda x: True if str(x).lower() in ["true", "1", "1.0", "si", "sí"] else False)
    else:
        df["Es_Infraccion"] = False

    # Normalizar Propietario / Asignacion y cruzar con Socio Maestro
    import mapeo_socios_engine as mse

    if "Alias del propietario del caso" in df.columns:
        df["Alias_Original"] = df["Alias del propietario del caso"].fillna("Sin Asignar")
        df["Esta_Asignado"] = ~df["Alias_Original"].str.contains("AMC", case=False, na=False)

        # Mapear a Nombre Real y Nivel N1/N2/N3
        def mapear_fila(alias):
            info = mse.get_asesor_info(alias)
            return pd.Series([
                info.get("nombre_completo", "Sin Asignar"),
                info.get("nivel", "N/A"),
                info.get("bp", ""),
                info.get("servicio", ""),
                info.get("supervisor", "Sin Supervisor"),
                info.get("coordinador", "Sin Coordinador")
            ])

        df[["Nombre_Real", "Nivel", "BP", "Servicio_Oficial", "Supervisor", "Coordinador"]] = df["Alias_Original"].apply(mapear_fila)
        df["Asesor"] = df["Nombre_Real"]
    else:
        df["Alias_Original"] = "Desconocido"
        df["Nombre_Real"] = "Desconocido"
        df["Nivel"] = "N/A"
        df["BP"] = ""
        df["Servicio_Oficial"] = ""
        df["Supervisor"] = "Sin Supervisor"
        df["Coordinador"] = "Sin Coordinador"
        df["Asesor"] = "Desconocido"
        df["Esta_Asignado"] = False

    # Buckets de antiguedad (Aging)
    def categorize_aging(dias):
        if pd.isna(dias):
            return "Desconocido"
        if dias < 1.0:
            return "< 24 Horas"
        elif dias <= 3.0:
            return "1 a 3 Días"
        elif dias <= 7.0:
            return "4 a 7 Días"
        elif dias <= 15.0:
            return "8 a 15 Días"
        elif dias <= 30.0:
            return "16 a 30 Días"
        else:
            return "> 30 Días (Crítico)"

    df["Rango_Antiguedad"] = df["Antiguedad_Dias"].apply(categorize_aging)

    try:
        df.to_pickle(cache_pkl)
        df.to_csv(cache_csv, index=False)
    except Exception:
        pass

    return df


def calculate_kpis(df):
    """Calcula los indicadores ejecutivos del backlog."""
    if df.empty:
        return {
            "total_backlog": 0,
            "infraccion_count": 0,
            "infraccion_pct": 0.0,
            "a_tiempo_count": 0,
            "a_tiempo_pct": 0.0,
            "sin_asignar_count": 0,
            "sin_asignar_pct": 0.0,
            "criticos_gt_7d": 0,
            "promedio_antiguedad_dias": 0.0,
            "max_antiguedad_dias": 0.0,
        }

    total = len(df)
    infraccion = int(df["Es_Infraccion"].sum())
    a_tiempo = total - infraccion
    sin_asignar = int((~df["Esta_Asignado"]).sum())
    criticos = int((df["Antiguedad_Dias"] > 7.0).sum())
    prom_antiguedad = float(df["Antiguedad_Dias"].mean())
    max_antiguedad = float(df["Antiguedad_Dias"].max())

    return {
        "total_backlog": total,
        "infraccion_count": infraccion,
        "infraccion_pct": round((infraccion / total) * 100, 1) if total > 0 else 0.0,
        "a_tiempo_count": a_tiempo,
        "a_tiempo_pct": round((a_tiempo / total) * 100, 1) if total > 0 else 0.0,
        "sin_asignar_count": sin_asignar,
        "sin_asignar_pct": round((sin_asignar / total) * 100, 1) if total > 0 else 0.0,
        "criticos_gt_7d": criticos,
        "promedio_antiguedad_dias": round(prom_antiguedad, 1),
        "max_antiguedad_dias": round(max_antiguedad, 1),
    }


def get_aging_distribution(df):
    """Devuelve la distribucion de casos por tramos de antiguedad."""
    if df.empty:
        return pd.DataFrame(columns=["Rango", "Casos", "Infracciones"])

    orden = ["< 24 Horas", "1 a 3 Días", "4 a 7 Días", "8 a 15 Días", "16 a 30 Días", "> 30 Días (Crítico)"]
    grouped = df.groupby("Rango_Antiguedad").agg(
        Casos=("Número del caso", "count"),
        Infracciones=("Es_Infraccion", "sum")
    ).reindex(orden).fillna(0).reset_index()

    grouped.rename(columns={"Rango_Antiguedad": "Rango"}, inplace=True)
    grouped["Casos"] = grouped["Casos"].astype(int)
    grouped["Infracciones"] = grouped["Infracciones"].astype(int)
    grouped["Pct_Infraccion"] = np.where(grouped["Casos"] > 0, (grouped["Infracciones"] / grouped["Casos"] * 100).round(1), 0.0)
    return grouped


def get_queue_breakdown(df):
    """Devuelve el desglose por cola AMC."""
    if df.empty or "Work Queue Control" not in df.columns:
        return pd.DataFrame()

    grouped = df.groupby("Work Queue Control").agg(
        Total_Casos=("Número del caso", "count"),
        Infracciones=("Es_Infraccion", "sum"),
        Sin_Asignar=("Esta_Asignado", lambda x: int((~x).sum())),
        Prom_Dias=("Antiguedad_Dias", "mean")
    ).reset_index()

    grouped["Pct_Infraccion"] = (grouped["Infracciones"] / grouped["Total_Casos"] * 100).round(1)
    grouped["Prom_Dias"] = grouped["Prom_Dias"].round(1)
    grouped.sort_values(by="Total_Casos", ascending=False, inplace=True)
    return grouped


def get_agent_workload(df):
    """Devuelve la carga y nivel de atraso por asesor con Nombre Real, Nivel, Supervisor y Servicio."""
    if df.empty or "Nombre_Real" not in df.columns:
        return pd.DataFrame()

    # Filtrar solo asignados a personas (excluir colas)
    df_agents = df[df["Esta_Asignado"]].copy()
    if df_agents.empty:
        return pd.DataFrame()

    cols_group = ["Nombre_Real", "Nivel", "Alias_Original"]
    if "Supervisor" in df_agents.columns:
        cols_group.append("Supervisor")
    if "Servicio_Oficial" in df_agents.columns:
        cols_group.append("Servicio_Oficial")

    grouped = df_agents.groupby(cols_group).agg(
        Casos_Asignados=("Número del caso", "count"),
        Casos_En_Infraccion=("Es_Infraccion", "sum"),
        Caso_Mas_Antiguo_Dias=("Antiguedad_Dias", "max"),
        Fecha_Mas_Antigua=("Fecha de inicio", "min")
    ).reset_index()

    grouped["Pct_Infraccion"] = (grouped["Casos_En_Infraccion"] / grouped["Casos_Asignados"] * 100).round(1)
    grouped["Caso_Mas_Antiguo_Dias"] = grouped["Caso_Mas_Antiguo_Dias"].round(1)
    grouped.sort_values(by=["Casos_Asignados", "Casos_En_Infraccion"], ascending=[False, False], inplace=True)
    return grouped


def get_resolved_cases_productivity(df):
    """Calcula la productividad de casos resueltos por asesor con Nombres Reales, Niveles, Supervisor y Servicio."""
    if df.empty:
        return pd.DataFrame()

    # Casos completados / resueltos
    resolved = df[df["Completado"] == 1.0].copy()
    if resolved.empty and "Fecha de finalización" in df.columns:
        resolved = df[df["Fecha de finalización"].notna()].copy()

    if resolved.empty:
        return pd.DataFrame()

    cols_group = ["Nombre_Real", "Nivel", "Alias_Original"]
    if "Supervisor" in resolved.columns:
        cols_group.append("Supervisor")
    if "Servicio_Oficial" in resolved.columns:
        cols_group.append("Servicio_Oficial")

    # Agrupar por asesor real
    grouped = resolved.groupby(cols_group).agg(
        Casos_Resueltos=("Número del caso", "count"),
        Resueltos_A_Tiempo=("Es_Infraccion", lambda x: int((~x).sum())),
        Resueltos_En_Infraccion=("Es_Infraccion", "sum"),
        Ultima_Resolucion=("Fecha de finalización", "max")
    ).reset_index()

    grouped["Eficacia_SLA_Pct"] = (grouped["Resueltos_A_Tiempo"] / grouped["Casos_Resueltos"] * 100).round(1)
    grouped.sort_values(by="Casos_Resueltos", ascending=False, inplace=True)
    return grouped


def get_critical_cases(df, limit=50):
    """Devuelve la lista de casos criticos ordenados por antiguedad descendente."""
    if df.empty:
        return pd.DataFrame()

    cols = [
        "Número del caso",
        "Work Queue Control",
        "Asesor",
        "Esta_Asignado",
        "Fecha de inicio",
        "Antiguedad_Dias",
        "Es_Infraccion",
        "Prioridad",
        "Origen del caso",
        "Evento clave"
    ]
    avail_cols = [c for c in cols if c in df.columns]
    res = df[avail_cols].copy()

    if "Antiguedad_Dias" in res.columns:
        res.sort_values(by="Antiguedad_Dias", ascending=False, inplace=True)
        res["Antiguedad_Dias"] = res["Antiguedad_Dias"].round(1)

    if "Número del caso" in res.columns:
        res["Número del caso"] = res["Número del caso"].astype(str).str.replace(".0", "", regex=False)

    return res.head(limit)
