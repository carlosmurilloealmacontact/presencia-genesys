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

    is_excel = file_path.lower().endswith(('.xlsx', '.xls'))
    if is_excel:
        df_preview = pd.read_excel(file_path, header=None, nrows=30)
        header_row = 13
        for idx, row in df_preview.iterrows():
            row_str = ' '.join([str(v) for v in row.dropna()]).lower()
            if 'work queue' in row_str or 'numero del caso' in row_str or 'número del caso' in row_str:
                header_row = idx
                break
        print(f'[*] Encabezado detectado en fila: {header_row}')
        df = pd.read_excel(file_path, skiprows=header_row)
    else:
        df = pd.read_csv(file_path, skiprows=13, encoding='utf-8', encoding_errors='replace')

    df.columns = [str(c).replace('\u2191', '').replace('\u2193', '').strip() for c in df.columns]
    print(f'[*] Filas leidas en bruto: {len(df):,}')

    if 'Work Queue Control' in df.columns:
        df['Work Queue Control'] = df['Work Queue Control'].ffill()
    if 'Fecha/Hora de cierre' in df.columns:
        df['Fecha/Hora de cierre'] = df['Fecha/Hora de cierre'].ffill()

    if 'Work Queue Control' in df.columns:
        df_amc = df[df['Work Queue Control'].astype(str).str.contains('AMC', case=False, na=False)].copy()
    else:
        df_amc = df.copy()

    print(f'[*] Filas pertenecientes a AMC: {len(df_amc):,}')
    if df_amc.empty:
        print('[!] No se encontraron registros AMC en el archivo.')
        return False

    if 'Fecha de inicio' in df_amc.columns:
        df_amc['Fecha_Inicio_dt'] = pd.to_datetime(df_amc['Fecha de inicio'], errors='coerce', dayfirst=True)
    elif 'Fecha_Inicio_dt' not in df_amc.columns:
        df_amc['Fecha_Inicio_dt'] = pd.NaT

    col_fin = None
    for cand in ['Fecha de finalizacion', 'Fecha de finalización', 'Fecha/Hora de cierre', 'Fecha de cierre', 'ClosedDate']:
        if cand in df_amc.columns:
            col_fin = cand
            break

    if col_fin:
        df_amc['Fecha_Finalizacion_dt'] = pd.to_datetime(df_amc[col_fin], errors='coerce', dayfirst=True)
    elif 'Fecha_Finalizacion_dt' not in df_amc.columns:
        df_amc['Fecha_Finalizacion_dt'] = pd.NaT

    df_amc['fecha_inflow'] = df_amc['Fecha_Inicio_dt'].dt.strftime('%Y-%m-%d')
    df_amc['fecha_outflow'] = df_amc['Fecha_Finalizacion_dt'].dt.strftime('%Y-%m-%d')

    os.makedirs(DATA_DIR, exist_ok=True)
    df_amc.to_pickle(OUTPUT_CASES_PKL)
    df_amc.to_csv(OUTPUT_CASES_CSV, index=False)
    print(f'[OK] Base maestra de casos guardada: {OUTPUT_CASES_PKL} ({len(df_amc)} casos)')

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

    return True


if __name__ == '__main__':
    arch = sys.argv[1] if len(sys.argv) > 1 else None
    procesar_casos_y_demanda_salesforce(arch)
