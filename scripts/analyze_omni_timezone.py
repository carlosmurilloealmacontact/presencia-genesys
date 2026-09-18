import sys
sys.stdout.reconfigure(encoding='utf-8')
import pandas as pd, sqlite3, unicodedata

df_omni = pd.read_csv('report1789733621567.csv', sep=';', encoding='latin-1')
df_omni['dt_ini'] = pd.to_datetime(df_omni['Fecha de inicio de estado'], format='%d/%m/%Y, %H:%M', errors='coerce')
df_omni['fecha_str'] = df_omni['dt_ini'].dt.strftime('%Y-%m-%d')

sub_omni = df_omni[df_omni['fecha_str'] == '2026-09-15'].copy()
print('Registros Omni en 2026-09-15:', len(sub_omni))

conn = sqlite3.connect('data/presencia.db')
turnos = pd.read_sql_query("SELECT bp, nombre_agente, turno_ini, turno_fin, horas_programadas FROM turnos_detallados WHERE fecha = '2026-09-15'", conn)

def tokenize(s):
    clean = ''.join(c for c in unicodedata.normalize('NFD', str(s).upper()) if unicodedata.category(c) != 'Mn')
    return set(clean.replace('-', ' ').split())

turnos_map = {}
for _, r in turnos.iterrows():
    turnos_map[r['bp']] = (r['nombre_agente'], tokenize(r['nombre_agente']), r['turno_ini'], r['turno_fin'])

omni_by_user = sub_omni.groupby('Usuario: Nombre completo')

print('\nComparación Horario Turno (Colombia) vs Conexión Omni en el CSV:')
print('-' * 80)
matches_found = 0
for u_name, grp in omni_by_user:
    u_toks = tokenize(u_name)
    matched = None
    for bp, (t_nom, t_toks, t_ini, t_fin) in turnos_map.items():
        inter = u_toks.intersection(t_toks)
        if len(inter) >= 2 and len(inter)/max(len(u_toks), 1) >= 0.65:
            matched = (bp, t_nom, t_ini, t_fin)
            break
    
    if matched:
        matches_found += 1
        t_min = grp['dt_ini'].min()
        t_max = grp['dt_ini'].max()
        t_str = f"{t_min.strftime('%H:%M')} - {t_max.strftime('%H:%M')}" if pd.notna(t_min) else '--'
        print(f"Asesor: {u_name} (BP: {matched[0]})")
        print(f"  Turno Programado (Colombia): {matched[2][:5]} a {matched[3][:5]}")
        print(f"  Conexión en CSV:             {t_str}")
        print(f"  Total tramos en el día:      {len(grp)}")
        print('-' * 80)
        if matches_found >= 6:
            break
