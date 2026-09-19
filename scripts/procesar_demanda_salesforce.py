# -*- coding: utf-8 -*-
"""
Modulo de Procesamiento y Generacion de Demanda Diaria Salesforce B2B (AMC LATAM) 2026.
Procesa cualquier archivo de reporte (.xlsx o .csv) exportado de Salesforce,
calcula Inflow (casos creados) y Outflow (casos resueltos) por fecha y cola,
y genera data/salesforce/demanda_diaria_salesforce_bo.csv compatible con el monitor de Back Office.
"""

import os
import sys
import glob
import pandas as pd
import numpy as np
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.normpath(os.path.join(BASE_DIR, '..'))
DATA_DIR = os.path.join(PROJECT_DIR, 'data', 'salesforce')
OUTPUT_DEMANDA_CSV = os.path.join(DATA_DIR, 'demanda_diaria_salesforce_bo.csv')
OUTPUT_CASES_PKL = os.path.join(DATA_DIR, 'cases_amc_cleaned.pkl')
OUTPUT_CASES_CSV = os.path.join(DATA_DIR, 'cases_amc_cleaned.csv')

# Mapeo de Colas Salesforce a Servicios Oficiales de WFM/SORE Back Office
MAPEO_SF_A_SORE_BO = {
    'AMC CORPORATE SSC': 'BO_CORPORATE',
    'AMC EMISIONES GRUPOS CORP': 'BO_CORPORATE',
    'AMC EMISIONES GRUPOS SSC': 'BO_CORPORATE',
    'AMC AGENCIAS ESP': 'BO AGENCIAS TARGET',
    'AMC AGENCIAS INTER': 'BO AGENCIAS TARGET ENG',
    'AMC DUDAS OPERACIONALES': 'BO AGENCIAS TARGET',
}


def procesar_casos_y_demanda_salesforce(file_path: str = None) -> bool:
    if not file_path or not os.path.exists(file_path):
        candidates = []
        for p in ['*.xlsx', '*.xls', '*.csv']:
            for f in glob.glob(os.path.join(DATA_DIR, p)):
                fn = os.path.basename(f)
                if fn.startswith('cases_amc_cleaned') or fn.startswith('demanda_diaria'):
                    continue
                candidates.append(f)
        if not candidates:
            print(f'[!] No se encontro ningun archivo de reporte en: {DATA_DIR}')
            return False
        candidates.sort(key=lambda x: (os.path.getsize(x) > 50000, os.path.getmtime(x)), reverse=True)
        file_path = candidates[0]

    print('=' * 75)
    print(f'PROCESANDO ARCHIVO SALESFORCE B2B: {os.path.basename(file_path)}')
    print(f'Tamano: {os.path.getsize(file_path) / (1024 * 1024):.2f} MB')
    print('=' * 75)

    import salesforce_engine as sfe
    df_amc = sfe.load_and_clean_cases_data(file_path)

    print(f'[*] Filas pertenecientes a AMC procesadas: {len(df_amc):,}')
    if df_amc.empty:
        print('[!] No se encontraron registros AMC en el archivo.')
        return False

    if 'fecha_inflow' not in df_amc.columns and 'Fecha_Inicio_dt' in df_amc.columns:
        df_amc['fecha_inflow'] = df_amc['Fecha_Inicio_dt'].dt.strftime('%Y-%m-%d')
    if 'fecha_outflow' not in df_amc.columns and 'Fecha_Finalizacion_dt' in df_amc.columns:
        df_amc['fecha_outflow'] = df_amc['Fecha_Finalizacion_dt'].dt.strftime('%Y-%m-%d')

    os.makedirs(DATA_DIR, exist_ok=True)
    df_amc.to_pickle(OUTPUT_CASES_PKL, compression='gzip')
    df_amc.to_csv(OUTPUT_CASES_CSV, index=False)
    print(f'[OK] Base maestra de casos enriquecida guardada: {OUTPUT_CASES_PKL} ({len(df_amc)} casos, comprimida gzip)')

    df_inflow = (
        df_amc.dropna(subset=['fecha_inflow'])
        .groupby(['fecha_inflow', 'Work Queue Control'], as_index=False)
        .size()
        .rename(columns={'fecha_inflow': 'Fecha', 'Work Queue Control': 'grupo', 'size': 'Casos_Nuevos'})
    )

    df_outflow = (
        df_amc.dropna(subset=['fecha_outflow'])
        .groupby(['fecha_outflow', 'Work Queue Control'], as_index=False)
        .size()
        .rename(columns={'fecha_outflow': 'Fecha', 'Work Queue Control': 'grupo', 'size': 'Casos_Resueltos'})
    )

    df_demanda = pd.merge(df_inflow, df_outflow, on=['Fecha', 'grupo'], how='outer').fillna(0)
    df_demanda['Casos_Nuevos'] = df_demanda['Casos_Nuevos'].astype(int)
    df_demanda['Casos_Resueltos'] = df_demanda['Casos_Resueltos'].astype(int)
    df_demanda['Balance_Neto'] = df_demanda['Casos_Nuevos'] - df_demanda['Casos_Resueltos']

    df_demanda = df_demanda.sort_values(['Fecha', 'grupo']).reset_index(drop=True)

    df_demanda_2026 = df_demanda[df_demanda['Fecha'].str.startswith('2026')].copy()
    if not df_demanda_2026.empty:
        df_demanda = df_demanda_2026

    df_demanda.to_csv(OUTPUT_DEMANDA_CSV, index=False)
    print(f'[OK] Matriz de Demanda Diaria Salesforce generada: {OUTPUT_DEMANDA_CSV}')
    print(f"     Total Fechas: {df_demanda['Fecha'].nunique()} | Rango: {df_demanda['Fecha'].min()} a {df_demanda['Fecha'].max()}")
    print(f"     Total Casos Nuevos (Inflow): {df_demanda['Casos_Nuevos'].sum():,}")
    print(f"     Total Casos Resueltos (Outflow): {df_demanda['Casos_Resueltos'].sum():,}")

    # Sincronizar automáticamente con Neon PostgreSQL
    sync_demanda_to_neon(df_demanda)

    return True


def sync_demanda_to_neon(df_demanda: pd.DataFrame = None) -> bool:
    """Sincroniza la matriz de demanda diaria en la tabla salesforce_bo_demanda de Neon PostgreSQL."""
    if df_demanda is None or df_demanda.empty:
        if os.path.exists(OUTPUT_DEMANDA_CSV):
            df_demanda = pd.read_csv(OUTPUT_DEMANDA_CSV)
        else:
            return False

    try:
        import salesforce_live_engine as sle
        from psycopg2.extras import execute_batch

        conn = sle._get_neon_connection()
        if not conn:
            print("[*] Neon no disponible para sync de demanda (modo local activo).")
            return False

        cur = conn.cursor()
        records = [
            (str(r['Fecha']).strip(), str(r['grupo']).strip(), int(r.get('Casos_Nuevos', 0)), int(r.get('Casos_Resueltos', 0)), int(r.get('Balance_Neto', 0)))
            for _, r in df_demanda.iterrows()
        ]
        query = """
            INSERT INTO salesforce_bo_demanda (fecha, grupo, casos_nuevos, casos_resueltos, balance_neto, updated_at)
            VALUES (%s, %s, %s, %s, %s, NOW())
            ON CONFLICT (fecha, grupo) DO UPDATE SET
                casos_nuevos = EXCLUDED.casos_nuevos,
                casos_resueltos = EXCLUDED.casos_resueltos,
                balance_neto = EXCLUDED.balance_neto,
                updated_at = NOW();
        """
        execute_batch(cur, query, records, page_size=200)
        conn.commit()
        cur.close()
        conn.close()
        print(f"[✓] {len(records)} registros de demanda diaria sincronizados en Neon PostgreSQL.")
        return True
    except Exception as e:
        print(f"[!] Error sincronizando demanda con Neon: {e}")
        return False


def load_demanda_from_neon() -> pd.DataFrame:
    """Carga la demanda diaria desde Neon PostgreSQL con fallback local al CSV."""
    try:
        import salesforce_live_engine as sle
        conn = sle._get_neon_connection()
        if conn:
            query = """
                SELECT fecha as "Fecha", grupo, casos_nuevos as "Casos_Nuevos", 
                       casos_resueltos as "Casos_Resueltos", balance_neto as "Balance_Neto"
                FROM salesforce_bo_demanda
                ORDER BY fecha, grupo;
            """
            df = pd.read_sql(query, conn)
            conn.close()
            if not df.empty:
                df["Fecha"] = df["Fecha"].astype(str)
                return df
    except Exception:
        pass
    if os.path.exists(OUTPUT_DEMANDA_CSV):
        return pd.read_csv(OUTPUT_DEMANDA_CSV)
    return pd.DataFrame()


if __name__ == '__main__':
    arch = sys.argv[1] if len(sys.argv) > 1 else None
    procesar_casos_y_demanda_salesforce(arch)
