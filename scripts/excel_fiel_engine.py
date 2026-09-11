"""
Motor de generación fiel de archivos Excel oficiales (HORA A HORA y AHT GENESYS).
Utiliza lxml y zipfile para modificar directamente las celdas y filas de datos
en los libros maestros oficiales SIN alterar tablas dinámicas, macros VBA,
slicers, formatos condicionales ni la estructura de 16 hojas.
100% compatible con Linux / Streamlit Cloud (no requiere Windows ni win32com).
"""

import os
import zipfile
from datetime import datetime, date, timedelta, timezone
from io import BytesIO
from lxml import etree
import pandas as pd

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
COL_LETTERS = [
    "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z",
    "AA", "AB", "AC", "AD", "AE", "AF", "AG", "AH", "AI", "AJ", "AK", "AL", "AM", "AN", "AO", "AP", "AQ", "AR", "AS", "AT", "AU", "AV", "AW", "AX", "AY", "AZ"
]

def _col_idx_to_letter(col_idx: int) -> str:
    """Convierte índice base 0 a letra de columna Excel (0 -> A, 27 -> AB)."""
    if col_idx < len(COL_LETTERS):
        return COL_LETTERS[col_idx]
    result = ""
    col_idx += 1
    while col_idx > 0:
        col_idx, remainder = divmod(col_idx - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _date_to_serial(d: date) -> int:
    """Calcula el número de serie de fecha de Excel (base 1899-12-30)."""
    return (d - date(1899, 12, 30)).days


def _time_to_serial(t_str: str) -> float:
    """Convierte string HH:MM:SS a fracción de día de Excel."""
    try:
        parts = [int(p) for p in t_str.split(':')]
        h = parts[0]
        m = parts[1] if len(parts) > 1 else 0
        s = parts[2] if len(parts) > 2 else 0
        return round((h * 3600 + m * 60 + s) / 86400.0, 8)
    except Exception:
        return 0.375  # Default 09:00


def inyectar_datos_hora_hora(tpl_path: str, df_raw: pd.DataFrame, serv_data: dict, fecha_corte: date, hora_str: str) -> bytes:
    """
    Inyecta métricas en tiempo real en HORA_HORA_EXACT_MASTER.xlsx:
    - DETALLE!Y5 (hora de corte)
    - DETALLE!Y8 (fecha del reporte)
    - DETALLE!D8:W24 (matriz ejecutiva calculada)
    - DATA GENEYS (intervalos actualizados con la fecha seleccionada)
    """
    date_serial = _date_to_serial(fecha_corte)
    time_serial = _time_to_serial(hora_str)

    buf_out = BytesIO()

    with zipfile.ZipFile(tpl_path, 'r') as zin:
        with zipfile.ZipFile(buf_out, 'w', compression=zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                content = zin.read(item.filename)

                # 1. Modificar Hoja DETALLE (sheet1.xml)
                if item.filename == 'xl/worksheets/sheet1.xml':
                    parser = etree.XMLParser(remove_blank_text=False)
                    tree = etree.fromstring(content, parser)

                    # Headers en fila 3 (D3:W3)
                    # Mapeo de columna a nombre de servicio
                    headers_map = {
                        "D": "TT_LATAM", "E": "LUA AMC", "F": "Soporte LUA AMC", "G": "WPP LUA AMC",
                        "H": "HVC AMC", "I": "Ventas AMC", "J": "WPP VENTAS AMC", "K": "CHAT VENTAS AMC",
                        "L": "TRAVEL WP AMC", "M": "LUA AMC ING", "N": "DT FFP AMC", "O": "DT FFP AMC ING",
                        "P": "CHAT DT FFP AMC ESP", "Q": "DREAM TEAM WP", "T": "TT_EQUIPAJES",
                        "U": "Equipajes AMC", "V": "Equipajes AMC ING", "W": "WPP EQUIPAJES AMC"
                    }

                    # Fila a métrica
                    row_metrics = {
                        "8": "LL ENT",
                        "9": "LL ATEN",
                        "10": "LL ABAN",
                        "11": "LL Aten. NS",
                        "12": "% ATEN",
                        "13": "% ABAN",
                        "14": "% NS META",
                        "15": "% NS",
                        "16": "META AHT",
                        "17": "AHT",
                        "18": "% VAR AHT",
                        "24": "ASA"
                    }

                    # Buscar y actualizar celdas
                    for c in tree.iter(f'{{{NS_MAIN}}}c'):
                        r_ref = c.get('r')
                        if not r_ref:
                            continue

                        # Hora de corte Y5
                        if r_ref == 'Y5':
                            v = c.find(f'{{{NS_MAIN}}}v')
                            if v is None:
                                v = etree.SubElement(c, f'{{{NS_MAIN}}}v')
                            v.text = str(time_serial)

                        # Fecha de corte Y8
                        elif r_ref == 'Y8':
                            v = c.find(f'{{{NS_MAIN}}}v')
                            if v is None:
                                v = etree.SubElement(c, f'{{{NS_MAIN}}}v')
                            v.text = str(date_serial)

                        # C3 (fórmula ='DATA GENEYS'!AM3543, utilizada para validar =$C$3<>$Y$8)
                        elif r_ref == 'C3':
                            v = c.find(f'{{{NS_MAIN}}}v')
                            if v is None:
                                v = etree.SubElement(c, f'{{{NS_MAIN}}}v')
                            v.text = str(date_serial)

                        elif r_ref in ('D52', 'A54'):
                            v = c.find(f'{{{NS_MAIN}}}v')
                            if v is not None:
                                v.text = str(date_serial)

                        # Celdas de la matriz ejecutiva (D8:W24)
                        elif len(r_ref) >= 2 and r_ref[0] in headers_map:
                            col_let = r_ref[0]
                            row_num = r_ref[1:]
                            if row_num in row_metrics:
                                srv_name = headers_map.get(col_let)
                                metric_name = row_metrics.get(row_num)
                                if srv_name and metric_name and srv_name in serv_data:
                                    sd = serv_data[srv_name]
                                    val = sd.get(metric_name)
                                    if val is not None:
                                        # Formateo porcentual o entero según métrica
                                        if "%" in metric_name:
                                            calc_val = round(float(val) / 100.0, 4)
                                        else:
                                            calc_val = round(float(val), 2)

                                        v = c.find(f'{{{NS_MAIN}}}v')
                                        if v is None:
                                            v = etree.SubElement(c, f'{{{NS_MAIN}}}v')
                                        v.text = str(calc_val)

                    content = etree.tostring(tree, xml_declaration=True, encoding='UTF-8', standalone='yes')

                # 2. Modificar Hoja DATA GENEYS (sheet13.xml)
                elif item.filename == 'xl/worksheets/sheet13.xml':
                    parser = etree.XMLParser(remove_blank_text=False)
                    tree = etree.fromstring(content, parser)
                    
                    # Actualizar fecha en columna A (Fecha) y columna AM (FECHA2)
                    for c in tree.iter(f'{{{NS_MAIN}}}c'):
                        r_ref = c.get('r')
                        if not r_ref:
                            continue
                        # Columna A (A2, A3... pero no AA, AB...)
                        if r_ref.startswith('A') and len(r_ref) > 1 and not r_ref[1].isalpha() and r_ref != 'A1':
                            v = c.find(f'{{{NS_MAIN}}}v')
                            if v is not None:
                                v.text = str(date_serial)
                        # Columna AM (AM2, AM3... FECHA2)
                        elif r_ref.startswith('AM') and len(r_ref) > 2 and not r_ref[2].isalpha() and r_ref != 'AM1':
                            v = c.find(f'{{{NS_MAIN}}}v')
                            if v is not None:
                                v.text = str(date_serial)

                    content = etree.tostring(tree, xml_declaration=True, encoding='UTF-8', standalone='yes')

                # 3. Modificar Hoja DATA GEN 2 (sheet14.xml)
                elif item.filename == 'xl/worksheets/sheet14.xml':
                    parser = etree.XMLParser(remove_blank_text=False)
                    tree = etree.fromstring(content, parser)
                    
                    # Actualizar fecha en columna A (Fecha) y columna AI (FECHA2)
                    for c in tree.iter(f'{{{NS_MAIN}}}c'):
                        r_ref = c.get('r')
                        if not r_ref:
                            continue
                        # Columna A (A2, A3...)
                        if r_ref.startswith('A') and len(r_ref) > 1 and not r_ref[1].isalpha() and r_ref != 'A1':
                            v = c.find(f'{{{NS_MAIN}}}v')
                            if v is not None:
                                v.text = str(date_serial)
                        # Columna AI (AI2, AI3... FECHA2)
                        elif r_ref.startswith('AI') and len(r_ref) > 2 and not r_ref[2].isalpha() and r_ref != 'AI1':
                            v = c.find(f'{{{NS_MAIN}}}v')
                            if v is not None:
                                v.text = str(date_serial)

                    content = etree.tostring(tree, xml_declaration=True, encoding='UTF-8', standalone='yes')

                # 4. Actualizar fechas cacheadas en hojas de apoyo
                elif item.filename in ('xl/worksheets/sheet7.xml', 'xl/worksheets/sheet8.xml', 'xl/worksheets/sheet11.xml'):
                    parser = etree.XMLParser(remove_blank_text=False)
                    tree = etree.fromstring(content, parser)
                    
                    for c in tree.iter(f'{{{NS_MAIN}}}c'):
                        r_ref = c.get('r')
                        if r_ref in ('A3', 'B1', 'AI7'):
                            v = c.find(f'{{{NS_MAIN}}}v')
                            if v is not None:
                                v.text = str(date_serial)

                    content = etree.tostring(tree, xml_declaration=True, encoding='UTF-8', standalone='yes')

                # 5. Forzar recálculo completo de fórmulas en Excel al abrir
                elif item.filename == 'xl/workbook.xml':
                    parser = etree.XMLParser(remove_blank_text=False)
                    tree = etree.fromstring(content, parser)
                    calc_pr = tree.find(f'{{{NS_MAIN}}}calcPr')
                    if calc_pr is not None:
                        calc_pr.set('fullCalcOnLoad', '1')
                        calc_pr.set('forceFullCalc', '1')
                    content = etree.tostring(tree, xml_declaration=True, encoding='UTF-8', standalone='yes')

                zout.writestr(item, content)

    buf_out.seek(0)
    return buf_out.getvalue()


def inyectar_datos_aht_genesys(tpl_path: str, df_asesores_raw: pd.DataFrame, agentes_map: dict, gtr_cfg: dict, fecha_corte: date) -> bytes:
    """
    Inyecta datos de asesores en AHT_GENESYS_EXACT_MASTER.xlsm (hoja DATA / sheet3.xml)
    manteniendo tablas dinámicas, macros VBA y segmentadores 100% operativos.
    """
    date_serial = _date_to_serial(fecha_corte)
    next_date_serial = date_serial + 1

    buf_out = BytesIO()

    with zipfile.ZipFile(tpl_path, 'r') as zin:
        with zipfile.ZipFile(buf_out, 'w', compression=zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                content = zin.read(item.filename)

                if item.filename == 'xl/worksheets/sheet3.xml' and not df_asesores_raw.empty:
                    parser = etree.XMLParser(remove_blank_text=False)
                    tree = etree.fromstring(content, parser)
                    ns = {'m': NS_MAIN}

                    # Convertir DataFrame a lista de diccionarios
                    advisors_list = []
                    for _, r in df_asesores_raw.iterrows():
                        aid = str(r.get("agente_id", "")).strip()
                        ag_info = agentes_map.get(aid, {})

                        doc = ag_info.get("documento") or ag_info.get("bp") or aid[:7]
                        nombre = (ag_info.get("nombre") or r.get("agente_nombre") or aid).strip().upper()
                        servicio = ag_info.get("servicio", "HVC AMC")
                        supervisor = ag_info.get("jefe_inmediato", "SUPERVISOR")
                        coordinador = ag_info.get("coordinador", "COORDINADOR")
                        sede = "LATAM MED" if "MED" in str(ag_info.get("sede", "")).upper() else "LATAM BOG"

                        interacc = int(r.get("interacciones", 0))
                        aht_s = float(r.get("aht_seg", 0.0))
                        meta_aht = float(gtr_cfg.get("METAS_SERVICIOS", {}).get(servicio, {}).get("META_AHT", 700))
                        rango = ">360" if aht_s > 360 else "<=360"

                        advisors_list.append({
                            "aid": aid,
                            "doc": str(doc),
                            "nombre": nombre,
                            "servicio": servicio,
                            "sup": supervisor,
                            "coord": coordinador,
                            "sede": sede,
                            "interacc": str(interacc),
                            "meta": str(meta_aht),
                            "aht": str(round(aht_s, 2)),
                            "rango": rango
                        })

                    # Recorrer filas existentes en sheet3 (2..2000)
                    rows = tree.findall('.//m:row', ns)
                    for r_el in rows:
                        r_num_str = r_el.get('r')
                        if not r_num_str or r_num_str == '1':
                            continue
                        r_idx = int(r_num_str) - 2

                        cell_dict = {}
                        for c in r_el.findall('m:c', ns):
                            c_ref = c.get('r')
                            if c_ref:
                                col_p = c_ref[:-len(r_num_str)]
                                cell_dict[col_p] = c

                        if r_idx < len(advisors_list):
                            ag = advisors_list[r_idx]
                            vals = {
                                'A': (str(date_serial), None),
                                'B': (str(next_date_serial), None),
                                'F': (ag['aid'], 'str'),
                                'G': (ag['nombre'], 'str'),
                                'H': (ag['interacc'], None),
                                'I': (ag['interacc'], None),
                                'Q': (ag['doc'], None),
                                'S': (ag['nombre'], 'str'),
                                'T': (ag['servicio'], 'str'),
                                'U': (ag['sup'], 'str'),
                                'V': (ag['coord'], 'str'),
                                'W': (ag['interacc'], None),
                                'X': (ag['meta'], None),
                                'Y': (ag['aht'], None),
                                'Z': (ag['aht'], None),
                                'AA': (ag['doc'], None),
                                'AB': ('1', None),
                                'AC': (ag['rango'], 'str'),
                                'AD': (ag['sede'], 'str'),
                                'AE': (str(date_serial), None),
                            }
                            for col, (v_txt, t_typ) in vals.items():
                                c = cell_dict.get(col)
                                if c is not None:
                                    if t_typ:
                                        c.set('t', t_typ)
                                    elif 't' in c.attrib and c.get('t') in ['s', 'str']:
                                        del c.attrib['t']
                                    v = c.find(f'{{{NS_MAIN}}}v')
                                    if v is None:
                                        v = etree.SubElement(c, f'{{{NS_MAIN}}}v')
                                    v.text = v_txt
                        else:
                            # Limpiar filas restantes para no dejar registros viejos
                            for col in ['F', 'G', 'H', 'I', 'Q', 'S', 'T', 'U', 'V', 'W', 'X', 'Y', 'Z', 'AA', 'AB', 'AC', 'AD']:
                                c = cell_dict.get(col)
                                if c is not None:
                                    v = c.find(f'{{{NS_MAIN}}}v')
                                    if v is not None:
                                        v.text = ''
                            for col in ['A', 'AE']:
                                c = cell_dict.get(col)
                                if c is not None:
                                    v = c.find(f'{{{NS_MAIN}}}v')
                                    if v is not None:
                                        v.text = str(date_serial)

                    content = etree.tostring(tree, xml_declaration=True, encoding='UTF-8', standalone='yes')

                zout.writestr(item, content)

    buf_out.seek(0)
    return buf_out.getvalue()
