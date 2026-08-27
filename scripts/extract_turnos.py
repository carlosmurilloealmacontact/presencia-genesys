"""
Extrae los turnos programados (Hora Inicio / Hora Fin) desde el archivo de
Sistema de Punto compartido en red, y los guarda en SQLite.

El archivo lo actualizan a mano y solo muestra el mes en curso (no retiene
historico) - por eso esta extraccion hace upsert sin borrar lo que ya
capturamos antes: cada corrida solo agrega/actualiza lo que el archivo trae
en ese momento.

Uso:
    python extract_turnos.py
"""

import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import openpyxl

from db import get_connection, guardar_turnos
from jerarquia import load_cedula_a_bp

RUTA_ARCHIVO = r"\\Co0000fs0001\01. Reporting\02. Sistema de Punto\Privado - ABS Sistema de Punto Ajustado.xlsx"
HOJA = "BD OPERACIÓN"

# Indices de columna (0-based) segun el encabezado real del archivo
COL_CODIGO = 1   # CodigoColaborador
COL_FECHA = 7    # Fecha
COL_HORA_INICIO = 13  # Hora Inicio
COL_HORA_FIN = 14     # Hora Fin


def run():
    print("Cargando puente cedula -> BP desde el sheet Base...")
    cedula_a_bp = load_cedula_a_bp()
    print(f"  {len(cedula_a_bp)} cedulas mapeadas.")

    print(f"Leyendo {RUTA_ARCHIVO} ...")
    wb = openpyxl.load_workbook(RUTA_ARCHIVO, read_only=True, data_only=True)
    ws = wb[HOJA]

    rows = []
    vistos = set()
    sin_match = set()
    for fila in ws.iter_rows(min_row=2, values_only=True):
        cedula = fila[COL_CODIGO]
        fecha = fila[COL_FECHA]
        hora_inicio = fila[COL_HORA_INICIO]
        hora_fin = fila[COL_HORA_FIN]
        if not cedula or not fecha or hora_inicio is None or hora_fin is None:
            continue

        cedula = str(cedula).strip()
        bp = cedula_a_bp.get(cedula)
        if not bp:
            sin_match.add(cedula)
            continue

        fecha_str = fecha.strftime("%Y-%m-%d") if hasattr(fecha, "strftime") else str(fecha)[:10]
        clave = (bp, fecha_str)
        if clave in vistos:
            continue  # el archivo no deberia tener duplicados, pero por si acaso
        vistos.add(clave)

        rows.append({
            "bp": bp,
            "fecha": fecha_str,
            "hora_inicio": hora_inicio.strftime("%H:%M:%S"),
            "hora_fin": hora_fin.strftime("%H:%M:%S"),
        })

    wb.close()
    print(f"  {len(rows)} turnos leidos y cruzados a BP ({len(sin_match)} cedulas sin match en Base).")

    conn = get_connection()
    guardar_turnos(conn, rows)
    total_en_bd = conn.execute("SELECT COUNT(*) FROM turnos").fetchone()[0]
    conn.close()
    print(f"Guardado. La tabla turnos ahora tiene {total_en_bd} registros en total (historico acumulado).")


if __name__ == "__main__":
    run()
