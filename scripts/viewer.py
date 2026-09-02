"""
Radar Genesys — Pausas y Adherencia de Turno.

Uso:
    streamlit run viewer.py
"""

import sqlite3
from io import BytesIO
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from openpyxl.utils import get_column_letter

from config import DB_PATH

st.set_page_config(page_title="Radar Genesys", layout="wide")

PALETA_ESTADOS = px.colors.qualitative.Alphabet + px.colors.qualitative.Dark24

# Tarjetas de cumplimiento.
# tipo "graduada": 0 usado = 0%; 0 < usado <= meta = 100%; usado > meta baja
#   suave (piso(100*meta/usado)) - no es binario, entre mas se pase mas cae.
# tipo "conteo": sin meta, solo se cuenta cuantas veces se uso (Lunch).
# "presence" es una lista: Dialogo une PCA-Dialogo + Dialogo Diario/4DX en una
#   sola meta de 15 min/dia (un agente usa una u otra, no las dos).
# unidad: "dia" o "semana" (CDR es semanal).
CARDS = [
    {"key": "descanso",  "label": "Descanso",             "presence": ["Break"],                                    "meta": 30, "unidad": "dia",    "tipo": "graduada"},
    {"key": "pre_pausa", "label": "Ocupado: Pre Pausa",    "presence": ["Pre Pausa"],                                "meta": 60, "unidad": "dia",    "tipo": "graduada"},
    {"key": "bano",      "label": "Ausente: Baño",        "presence": ["Baño"],                                     "meta": 5,  "unidad": "dia",    "tipo": "graduada"},
    {"key": "dialogo",   "label": "Reunión: Diálogo",     "presence": ["PCA- Diálogo", "Diálogo Diario / 4DX"],     "meta": 15, "unidad": "dia",    "tipo": "graduada"},
    {"key": "lunch",     "label": "Comida: Lunch",         "presence": ["Lunch"],                                    "meta": None, "unidad": "dia",  "tipo": "conteo"},
    {"key": "cdr",       "label": "Reunión: CDR",          "presence": ["CDR"],                                      "meta": 30, "unidad": "semana", "tipo": "graduada"},
]

ESTADOS_SISTEMA = {"Offline", "Available", "Conectado", "On Queue"}


def meta_texto(card: dict) -> str:
    if card["tipo"] == "conteo":
        return "Sin meta — solo conteo"
    unidad_txt = "día" if card["unidad"] == "dia" else "semana"
    return f"Meta: {card['meta']} min/{unidad_txt}"


def color_pct(pct: float) -> str:
    if pct < 70:
        return "#e24b4a"
    if pct < 90:
        return "#eda100"
    return "#1baf7a"


def color_uso_turno(pct: float) -> str:
    """Semáforo estricto para aprovechamiento de turno: <85% rojo, 85-95% amarillo, >=95% verde."""
    if pct < 85:
        return "#e24b4a"
    if pct < 95:
        return "#eda100"
    return "#1baf7a"


def estilo_micro(val):
    """Estilo para micro-estados (<= 15 seg): alerta si es recurrente."""
    if pd.isna(val) or val == 0:
        return ""
    if val >= 20:
        color = "#e24b4a"
    elif val >= 5:
        color = "#eda100"
    else:
        color = "#378ADD"
    return f"background-color: {color}22; color: {color}; font-weight: 600;"


def servicio_tiene_prepausa(servicio: str) -> bool:
    """Pre Pausa aplica a canales WPP/Chat/RRSS, excepto CHAT AGENCIAS ESP."""
    s = (servicio or "").upper()
    if "CHAT AGENCIAS ESP" in s:
        return False
    return any(k in s for k in ("WPP", "CHAT", "RRSS"))


def sumar_presencias(pivot: pd.DataFrame, presence_list: list[str]) -> pd.Series:
    cols = [p for p in presence_list if p in pivot.columns]
    if not cols:
        return pd.Series(0.0, index=pivot.index)
    return pivot[cols].sum(axis=1)


def score_graduado(usado: pd.Series, meta: float) -> pd.Series:
    """0 usado -> 0%; 0<usado<=meta -> 100%; usado>meta -> piso(100*meta/usado)."""
    score = pd.Series(0.0, index=usado.index)
    cumple_pleno = (usado > 0) & (usado <= meta)
    score[cumple_pleno] = 100.0
    excedido = usado > meta
    score[excedido] = (100 * meta / usado[excedido]).apply(lambda x: float(int(x)))
    return score


# ── Datos ────────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def cargar_rango_fechas() -> tuple[str, str] | None:
    db_path = Path(__file__).parent / DB_PATH
    if not db_path.exists():
        return None
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT MIN(fecha), MAX(fecha) FROM segments").fetchone()
    return row if row and row[0] else None


@st.cache_data(ttl=60)
def cargar_rango(fecha_min: str, fecha_max: str) -> pd.DataFrame:
    db_path = Path(__file__).parent / DB_PATH
    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql_query(
            "SELECT * FROM segments WHERE fecha BETWEEN ? AND ?", conn, params=(fecha_min, fecha_max)
        )
    return df


@st.cache_data(ttl=60)
def cargar_turnos(fecha_min: str, fecha_max: str) -> pd.DataFrame:
    db_path = Path(__file__).parent / DB_PATH
    with sqlite3.connect(db_path) as conn:
        try:
            df = pd.read_sql_query(
                "SELECT * FROM turnos WHERE fecha BETWEEN ? AND ?", conn, params=(fecha_min, fecha_max)
            )
        except pd.errors.DatabaseError:
            df = pd.DataFrame(columns=["bp", "fecha", "hora_inicio", "hora_fin"])
    return df


@st.cache_data(ttl=300)
def cargar_color_map() -> dict[str, str]:
    """Color fijo por estado sobre el universo completo ya visto en la base."""
    db_path = Path(__file__).parent / DB_PATH
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT DISTINCT presence_label FROM segments ORDER BY presence_label").fetchall()
    universo = [r[0] for r in rows]
    return {estado: PALETA_ESTADOS[i % len(PALETA_ESTADOS)] for i, estado in enumerate(universo)}


def opciones_validas(serie: pd.Series) -> list[str]:
    return sorted(v for v in serie.unique() if v)


# ── Calculo de cumplimiento ─────────────────────────────────────────────

def calcular_cumplimiento(df: pd.DataFrame) -> pd.DataFrame:
    """
    Una fila por agente. Para tarjetas "graduada": promedio del score diario
    (o semanal) de cada dia/semana trabajado — no es un % de dias que pasaron
    un corte binario, cada dia tiene su propio score 0-100 (ver score_graduado).
    Para "conteo" (Lunch): total de veces usado en el rango, sin promediar.
    Denominador de dias/semanas = cuando el agente tuvo CUALQUIER tramo de
    presencia, no dias calendario, para no penalizar dias libres que no vemos.
    """
    info_agente = df.drop_duplicates("agente_id").set_index("agente_id")[
        ["agente", "servicio", "jefe_inmediato", "coordinador"]
    ]

    df = df.copy()
    dt = pd.to_datetime(df["fecha"])
    iso = dt.dt.isocalendar()
    df["semana"] = iso.year.astype(str) + "-W" + iso.week.astype(str).str.zfill(2)

    resultado = pd.DataFrame(index=info_agente.index)

    diario = df.groupby(["agente_id", "fecha", "presence_label"])["duracion_min"].sum().reset_index()
    pivot_diario = diario.pivot_table(index=["agente_id", "fecha"], columns="presence_label", values="duracion_min", fill_value=0)
    dias_trabajados = pivot_diario.groupby("agente_id").size()
    resultado["dias_trabajados"] = dias_trabajados

    semanal = df.groupby(["agente_id", "semana", "presence_label"])["duracion_min"].sum().reset_index()
    pivot_semanal = semanal.pivot_table(index=["agente_id", "semana"], columns="presence_label", values="duracion_min", fill_value=0)
    semanas_trabajadas = pivot_semanal.groupby("agente_id").size()
    resultado["semanas_trabajadas"] = semanas_trabajadas

    for c in CARDS:
        if c["tipo"] == "conteo":
            conteo = df[df["presence_label"].isin(c["presence"])].groupby("agente_id").size()
            resultado[c["key"]] = conteo.reindex(resultado.index, fill_value=0).astype(int)
            continue
        pivot = pivot_diario if c["unidad"] == "dia" else pivot_semanal
        usado = sumar_presencias(pivot, c["presence"])
        score = score_graduado(usado, c["meta"])
        resultado[c["key"]] = score.groupby("agente_id").mean().round(1)

    return resultado.join(info_agente)


def cumplimiento_global(df: pd.DataFrame) -> dict[str, float]:
    """Promedio pooled (no promedio de promedios) por tarjeta, para las tarjetas KPI de arriba."""
    df = df.copy()
    dt = pd.to_datetime(df["fecha"])
    iso = dt.dt.isocalendar()
    df["semana"] = iso.year.astype(str) + "-W" + iso.week.astype(str).str.zfill(2)

    diario = df.groupby(["agente_id", "fecha", "presence_label"])["duracion_min"].sum().reset_index()
    pivot_diario = diario.pivot_table(index=["agente_id", "fecha"], columns="presence_label", values="duracion_min", fill_value=0)

    semanal = df.groupby(["agente_id", "semana", "presence_label"])["duracion_min"].sum().reset_index()
    pivot_semanal = semanal.pivot_table(index=["agente_id", "semana"], columns="presence_label", values="duracion_min", fill_value=0)

    globales = {}
    for c in CARDS:
        if c["tipo"] == "conteo":
            globales[c["key"]] = int(df[df["presence_label"].isin(c["presence"])].shape[0])
            continue
        pivot = pivot_diario if c["unidad"] == "dia" else pivot_semanal
        if pivot.empty:
            globales[c["key"]] = 0.0
            continue
        usado = sumar_presencias(pivot, c["presence"])
        score = score_graduado(usado, c["meta"])
        globales[c["key"]] = round(score.mean(), 1)
    return globales


def calcular_adherencia_horario(vista: pd.DataFrame, turnos: pd.DataFrame) -> pd.DataFrame:
    """
    % del turno programado (Hora Inicio/Fin, archivo de Sistema de Punto) en
    que el agente estuvo en un estado distinto de Offline en Genesys. Cruza
    por BP -> agente_id (el archivo de turnos identifica por BP).
    """
    columnas_vacias = ["horario", "dias_con_turno"]
    if turnos.empty or vista.empty:
        return pd.DataFrame(columns=["agente_id"] + columnas_vacias).set_index("agente_id")

    vista = vista.copy()
    vista["bp"] = vista["agente"].str.split(" - ").str[0].str.strip()
    bp_a_agente_id = vista.groupby("bp")["agente_id"].agg(lambda s: s.mode().iat[0])

    productivos = vista[vista["presence_label"] != "Offline"].copy()
    productivos["inicio"] = pd.to_datetime(productivos["inicio"])
    productivos["fin"] = pd.to_datetime(productivos["fin"]).fillna(productivos["inicio"] + pd.Timedelta(minutes=1))
    segmentos_por_dia = {
        clave: list(zip(grupo["inicio"], grupo["fin"]))
        for clave, grupo in productivos.groupby(["agente_id", "fecha"])
    }

    acumulado = {}
    for row in turnos.itertuples(index=False):
        agente_id = bp_a_agente_id.get(row.bp)
        if agente_id is None:
            continue

        fecha_dt = pd.Timestamp(row.fecha)
        inicio_turno = fecha_dt + pd.to_timedelta(row.hora_inicio)
        fin_turno = fecha_dt + pd.to_timedelta(row.hora_fin)
        if fin_turno <= inicio_turno:
            fin_turno += pd.Timedelta(days=1)  # turno nocturno que cruza medianoche
        duracion_turno = (fin_turno - inicio_turno).total_seconds() / 60
        if duracion_turno <= 0:
            continue

        overlap_min = 0.0
        fecha_siguiente = (fecha_dt + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        for clave in ((agente_id, row.fecha), (agente_id, fecha_siguiente)):
            for seg_inicio, seg_fin in segmentos_por_dia.get(clave, []):
                ini = max(seg_inicio, inicio_turno)
                fin = min(seg_fin, fin_turno)
                if fin > ini:
                    overlap_min += (fin - ini).total_seconds() / 60

        acc = acumulado.setdefault(agente_id, {"overlap": 0.0, "programado": 0.0, "dias": 0})
        acc["overlap"] += min(overlap_min, duracion_turno)
        acc["programado"] += duracion_turno
        acc["dias"] += 1

    filas = [
        {
            "agente_id": agente_id,
            "horario": round(100 * acc["overlap"] / acc["programado"], 1) if acc["programado"] > 0 else float("nan"),
            "dias_con_turno": acc["dias"],
        }
        for agente_id, acc in acumulado.items()
    ]
    if not filas:
        return pd.DataFrame(columns=["agente_id"] + columnas_vacias).set_index("agente_id")
    return pd.DataFrame(filas).set_index("agente_id")


def calcular_uso_turno(
    vista: pd.DataFrame, turnos: pd.DataFrame
) -> tuple[pd.DataFrame, dict[str, float], dict[tuple[str, str], float]]:
    """
    Calcula el % de Uso de Turno (aprovechamiento descontando excesos de pausas).
    Devuelve:
      - DataFrame indexado por agente_id con columnas ['uso_turno', 'exceso_prom_min']
      - dict con metricas globales: {'uso_turno': float, 'exceso_prom_min': float}
      - dict (agente_id, fecha) -> uso_dia para el detalle diario
    """
    columnas_vacias = ["uso_turno", "exceso_prom_min"]
    if vista.empty:
        return (
            pd.DataFrame(columns=["agente_id"] + columnas_vacias).set_index("agente_id"),
            {"uso_turno": 0.0, "exceso_prom_min": 0.0},
            {},
        )

    # 1. Mapa de turnos por (bp, fecha) -> duracion en minutos
    mapa_turnos = {}
    if not turnos.empty:
        for row in turnos.itertuples(index=False):
            fecha_dt = pd.Timestamp(row.fecha)
            ini = fecha_dt + pd.to_timedelta(row.hora_inicio)
            fin = fecha_dt + pd.to_timedelta(row.hora_fin)
            if fin <= ini:
                fin += pd.Timedelta(days=1)
            dur = (fin - ini).total_seconds() / 60.0
            if dur > 0:
                mapa_turnos[(str(row.bp).strip(), str(row.fecha))] = dur

    # 2. Agrupacion diaria de pausas y tiempo conectado
    vista_temp = vista.copy()
    vista_temp["bp"] = vista_temp["agente"].str.split(" - ").str[0].str.strip()

    conectados = (
        vista_temp[vista_temp["presence_label"] != "Offline"]
        .groupby(["agente_id", "fecha"])["duracion_min"]
        .sum()
        .to_dict()
    )

    diario = (
        vista_temp.groupby(["agente_id", "bp", "servicio", "fecha", "presence_label"])["duracion_min"]
        .sum()
        .unstack(fill_value=0.0)
    )

    filas_dias = []
    mapa_diario = {}

    for (agente_id, bp, servicio, fecha), row in diario.iterrows():
        duracion_turno = mapa_turnos.get((bp, fecha))
        if not duracion_turno or duracion_turno <= 0:
            duracion_turno = conectados.get((agente_id, fecha), 0.0)
        if duracion_turno <= 0:
            duracion_turno = 480.0

        bano = row.get("Baño", 0.0)
        exceso_bano = max(0.0, bano - 5.0)

        descanso = row.get("Break", 0.0)
        exceso_break = max(0.0, descanso - 30.0)

        dialogo = row.get("Diálogo Diario / 4DX", 0.0) + row.get("PCA- Diálogo", 0.0)
        exceso_dialogo = max(0.0, dialogo - 15.0)

        lunch = row.get("Lunch", 0.0)
        exceso_lunch = lunch  # No autorizada

        pre_pausa = row.get("Pre Pausa", 0.0)
        meta_pre = 60.0 if servicio_tiene_prepausa(servicio) else 0.0
        exceso_pre = max(0.0, pre_pausa - meta_pre)

        total_exceso = exceso_bano + exceso_break + exceso_dialogo + exceso_lunch + exceso_pre
        uso_dia = max(0.0, min(100.0, 100.0 - (100.0 * total_exceso / duracion_turno)))

        mapa_diario[(agente_id, fecha)] = round(float(uso_dia), 1)
        filas_dias.append({
            "agente_id": agente_id,
            "fecha": fecha,
            "uso_dia": uso_dia,
            "total_exceso": total_exceso,
        })

    df_dias = pd.DataFrame(filas_dias)
    if df_dias.empty:
        return (
            pd.DataFrame(columns=["agente_id"] + columnas_vacias).set_index("agente_id"),
            {"uso_turno": 0.0, "exceso_prom_min": 0.0},
            {},
        )

    resumen_agente = df_dias.groupby("agente_id").agg(
        uso_turno=("uso_dia", "mean"),
        exceso_prom_min=("total_exceso", "mean")
    )
    resumen_agente["uso_turno"] = resumen_agente["uso_turno"].round(1)
    resumen_agente["exceso_prom_min"] = resumen_agente["exceso_prom_min"].round(1)

    global_kpi = {
        "uso_turno": round(float(df_dias["uso_dia"].mean()), 1),
        "exceso_prom_min": round(float(df_dias["total_exceso"].mean()), 1),
    }

    return resumen_agente, global_kpi, mapa_diario


# ── Vista de detalle (linea de tiempo de un agente) ─────────────────────

def render_timeline(df_agente: pd.DataFrame, color_map: dict[str, str]):
    df_agente = df_agente.copy()
    df_agente["inicio"] = pd.to_datetime(df_agente["inicio"])
    df_agente["fin"] = pd.to_datetime(df_agente["fin"]).fillna(df_agente["inicio"] + pd.Timedelta(minutes=1))

    fig = px.timeline(
        df_agente.sort_values("fecha"),
        x_start="inicio",
        x_end="fin",
        y="fecha",
        color="presence_label",
        color_discrete_map=color_map,
        hover_data={"duracion_min": True, "inicio": True, "fin": True, "fecha": False},
    )
    fig.update_yaxes(autorange="reversed", type="category")
    altura = max(220, 32 * df_agente["fecha"].nunique() + 100)
    fig.update_layout(height=altura, showlegend=True, xaxis_title=None, yaxis_title=None, legend_title_text="Estado")
    st.plotly_chart(fig, use_container_width=True)


# ── App ──────────────────────────────────────────────────────────────────

st.markdown(
    "<div style='text-align:center;'>"
    "<h2 style='margin-bottom:0;'>Radar Genesys</h2>"
    "<p style='color:gray; margin-top:0;'>Pausas y Adherencia de Turno</p>"
    "</div>",
    unsafe_allow_html=True,
)

rango_disponible = cargar_rango_fechas()
if not rango_disponible:
    st.warning("Todavía no hay datos extraídos. Corre `python extract_presencia.py` primero.")
    st.stop()

fecha_min_disp, fecha_max_disp = rango_disponible
fecha_max_disp_d = pd.Timestamp(fecha_max_disp).date()
fecha_min_disp_d = pd.Timestamp(fecha_min_disp).date()

if "desde" not in st.session_state:
    st.session_state["desde"] = max(fecha_min_disp_d, fecha_max_disp_d - pd.Timedelta(days=13))
    st.session_state["hasta"] = fecha_max_disp_d

col_desde, col_hasta, col_coord, col_servicio, col_superv, col_agente = st.columns(6)
with col_desde:
    st.date_input("Desde", key="desde", min_value=fecha_min_disp_d, max_value=fecha_max_disp_d)
with col_hasta:
    st.date_input("Hasta", key="hasta", min_value=fecha_min_disp_d, max_value=fecha_max_disp_d)

col_rango1, col_rango2, col_rango3, col_rango4, _, col_caption = st.columns([1, 1, 1, 1, 1, 4])
def set_rango(dias):
    st.session_state["hasta"] = fecha_max_disp_d
    st.session_state["desde"] = fecha_max_disp_d - pd.Timedelta(days=dias - 1) if dias else fecha_min_disp_d
    if st.session_state["desde"] < fecha_min_disp_d:
        st.session_state["desde"] = fecha_min_disp_d

with col_rango1:
    st.button("7 días", on_click=set_rango, args=(7,), use_container_width=True)
with col_rango2:
    st.button("14 días", on_click=set_rango, args=(14,), use_container_width=True)
with col_rango3:
    st.button("30 días", on_click=set_rango, args=(30,), use_container_width=True)
with col_rango4:
    st.button("Todo", on_click=set_rango, args=(None,), use_container_width=True)
with col_caption:
    st.markdown(
        f"<p style='text-align:right; color:gray; padding-top:0.5rem;'>Datos disponibles: {fecha_min_disp} → {fecha_max_disp}</p>",
        unsafe_allow_html=True,
    )

fecha_desde = str(st.session_state["desde"])
fecha_hasta = str(st.session_state["hasta"])
if fecha_desde > fecha_hasta:
    st.error("La fecha 'Desde' es posterior a 'Hasta'.")
    st.stop()

df = cargar_rango(fecha_desde, fecha_hasta)
if df.empty:
    st.info("Sin tramos para este rango.")
    st.stop()

FILTROS = [
    ("coord_sel", "coordinador", "Coordinador", col_coord),
    ("servicio_sel", "servicio", "Servicio", col_servicio),
    ("superv_sel", "jefe_inmediato", "Supervisor", col_superv),
    ("agentes_sel", "agente", "Agente", col_agente),
]


def aplicar_filtros(df: pd.DataFrame, excluir_key: str | None) -> pd.DataFrame:
    vista = df
    for key, columna, _, _ in FILTROS:
        if key == excluir_key:
            continue
        seleccion = st.session_state.get(key, [])
        if seleccion:
            vista = vista[vista[columna].isin(seleccion)]
    return vista


for key, columna, label, col in FILTROS:
    opciones = opciones_validas(aplicar_filtros(df, excluir_key=key)[columna])
    seleccion_actual = st.session_state.get(key, [])
    podada = [v for v in seleccion_actual if v in opciones]
    if podada != seleccion_actual:
        st.session_state[key] = podada
    with col:
        st.multiselect(label, opciones, key=key, placeholder="Todos")

vista = aplicar_filtros(df, excluir_key=None)
if vista.empty:
    st.info("Sin tramos para estos filtros.")
    st.stop()

turnos_rango = cargar_turnos(fecha_desde, fecha_hasta)
adherencia_horario = calcular_adherencia_horario(vista, turnos_rango)
resumen_uso, global_uso, mapa_uso_diario = calcular_uso_turno(vista, turnos_rango)

# ── Tarjetas KPI ─────────────────────────────────────────────────────────

globales = cumplimiento_global(vista)
kpi_cols = st.columns(4)

with kpi_cols[0]:
    horario_valido = adherencia_horario["horario"].dropna()
    if horario_valido.empty:
        pct_horario_txt, color_horario, subtitulo_horario = "—", "gray", "Sin turnos cargados en el rango"
    else:
        pct_horario = round(horario_valido.mean(), 1)
        pct_horario_txt = f"{pct_horario}%"
        color_horario = color_pct(pct_horario)
        subtitulo_horario = f"{len(horario_valido)} agentes con turno"
    st.markdown(
        f"""
        <div style="background:#f7f7f7; border-radius:10px; padding:14px 16px; margin-bottom:12px;">
            <p style="color:gray; font-size:13px; margin:0;">Adherencia Horario</p>
            <p style="color:{color_horario}; font-size:28px; font-weight:700; margin:2px 0;">{pct_horario_txt}</p>
            <p style="color:gray; font-size:12px; margin:0;">{subtitulo_horario}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

with kpi_cols[1]:
    pct_uso = global_uso["uso_turno"]
    min_exceso = global_uso["exceso_prom_min"]
    color_uso = color_uso_turno(pct_uso)
    st.markdown(
        f"""
        <div style="background:#f7f7f7; border-radius:10px; padding:14px 16px; margin-bottom:12px;">
            <p style="color:gray; font-size:13px; margin:0;">Uso de Turno</p>
            <p style="color:{color_uso}; font-size:28px; font-weight:700; margin:2px 0;">{pct_uso:.1f}%</p>
            <p style="color:gray; font-size:12px; margin:0;">Fuga prom: {min_exceso:.1f} min/día</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

for i, card in enumerate(CARDS):
    valor = globales[card["key"]]
    if card["tipo"] == "conteo":
        valor_txt, color_valor = f"{valor}", "#378ADD"
    else:
        valor_txt, color_valor = f"{valor}%", color_pct(valor)
    with kpi_cols[(i + 2) % 4]:
        st.markdown(
            f"""
            <div style="background:#f7f7f7; border-radius:10px; padding:14px 16px; margin-bottom:12px;">
                <p style="color:gray; font-size:13px; margin:0;">{card['label']}</p>
                <p style="color:{color_valor}; font-size:28px; font-weight:700; margin:2px 0;">{valor_txt}</p>
                <p style="color:gray; font-size:12px; margin:0;">{meta_texto(card)}</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

# ── Tabla por agente ───────────────────────────────────────────────────

micro_por_agente = (
    vista[
        (~vista["presence_label"].isin(ESTADOS_SISTEMA)) &
        (vista["duracion_min"] <= 0.25)
    ]
    .groupby("agente_id")
    .size()
    .rename("micro_estados")
)

tabla = (
    calcular_cumplimiento(vista)
    .join(adherencia_horario[["horario", "dias_con_turno"]])
    .join(resumen_uso[["uso_turno", "exceso_prom_min"]])
    .join(micro_por_agente)
)
tabla["micro_estados"] = tabla["micro_estados"].fillna(0).astype(int)
n_agentes = len(tabla)
st.caption(f"{n_agentes} agentes · clic en una fila para ver el detalle y la comparación contra su servicio")

columnas_mostrar = ["agente", "coordinador", "servicio", "jefe_inmediato", "uso_turno", "micro_estados", "horario"] + [c["key"] for c in CARDS]
tabla_mostrar = (
    tabla.reset_index()[["agente_id"] + columnas_mostrar]
    .rename(
        columns={
            "agente": "Agente", "coordinador": "Coordinador", "servicio": "Servicio",
            "jefe_inmediato": "Supervisor", "uso_turno": "Uso Turno",
            "micro_estados": "Micro (≤15s)", "horario": "Horario",
            **{c["key"]: c["label"] for c in CARDS},
        }
    )
    .sort_values(by="Uso Turno", ascending=True)
)

pct_cols = ["Uso Turno", "Horario"] + [c["label"] for c in CARDS if c["tipo"] != "conteo"]
conteo_cols = ["Micro (≤15s)"] + [c["label"] for c in CARDS if c["tipo"] == "conteo"]


def estilo_pct(val):
    if pd.isna(val):
        return ""
    return f"background-color: {color_pct(val)}22; color: {color_pct(val)}; font-weight: 600;"


def estilo_uso(val):
    if pd.isna(val):
        return ""
    return f"background-color: {color_uso_turno(val)}22; color: {color_uso_turno(val)}; font-weight: 600;"


styler = (
    tabla_mostrar.drop(columns=["agente_id"])
    .style.map(estilo_pct, subset=["Horario"] + [c["label"] for c in CARDS if c["tipo"] != "conteo"])
    .map(estilo_uso, subset=["Uso Turno"])
    .map(estilo_micro, subset=["Micro (≤15s)"])
)

# st.dataframe en modo interactivo (on_select) ignora Styler.format() para el
# valor mostrado - solo respeta el color via Styler.map(). El formato real de
# los numeros se controla aparte con column_config.
column_config = {c: st.column_config.NumberColumn(c, format="%.1f%%") for c in pct_cols}
column_config.update({c: st.column_config.NumberColumn(c, format="%d") for c in conteo_cols})
column_config["Micro (≤15s)"] = st.column_config.NumberColumn(
    "Micro (≤15s)",
    format="%d",
    help="Tramos en pausas o gestiones con duración ≤ 15 seg. Ítem a revisar por posibles refrescos de cola o alternancia operativa.",
)

evento = st.dataframe(
    styler,
    column_config=column_config,
    use_container_width=True,
    hide_index=True,
    on_select="rerun",
    selection_mode="single-row",
    key="tabla_agentes",
)

# ── Exportar Excel ───────────────────────────────────────────────────────

presencias_de_interes = [p for c in CARDS for p in c["presence"]]
conteo_general = (
    vista[vista["presence_label"].isin(presencias_de_interes)]
    .groupby(["agente", "presence_label"])
    .size()
    .unstack(fill_value=0)
    .rename(columns=lambda p: f"{p} (veces)")
)

buffer = BytesIO()
with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
    hoja_radar = tabla_mostrar.drop(columns=["agente_id"])
    hoja_radar.to_excel(writer, index=False, sheet_name="Radar Genesys")
    conteo_general.reset_index().to_excel(writer, index=False, sheet_name="Cantidad de pausas")

    # Excel muestra los floats con toda su precision binaria (ej. 71.79999999999999)
    # si no se fija un formato de celda explicito - el .round(1) de pandas no alcanza.
    ws = writer.sheets["Radar Genesys"]
    for nombre_col in pct_cols + conteo_cols:
        idx = hoja_radar.columns.get_loc(nombre_col) + 1
        letra = get_column_letter(idx)
        formato = '0.0"%"' if nombre_col in pct_cols else "0"
        for celda in ws[f"{letra}2:{letra}{ws.max_row}"]:
            celda[0].number_format = formato
st.download_button(
    "📥 Exportar Excel",
    buffer.getvalue(),
    file_name=f"radar_genesys_{fecha_desde}_a_{fecha_hasta}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

# ── Detalle del agente seleccionado ─────────────────────────────────────

filas_sel = evento.selection.rows if evento and evento.selection else []
if filas_sel:
    agente_id_sel = tabla_mostrar.iloc[filas_sel[0]]["agente_id"]
    fila_sel = tabla.loc[agente_id_sel]

    st.divider()
    st.subheader(f"Detalle — {fila_sel['agente']}")
    st.caption(f"{fila_sel['servicio']} · Supervisor: {fila_sel['jefe_inmediato']}")

    vista_servicio = vista[vista["servicio"] == fila_sel["servicio"]]
    n_agentes_servicio = vista_servicio["agente_id"].nunique()
    df_agente = vista[vista["agente_id"] == agente_id_sel]
    bp_agente = fila_sel["agente"].split(" - ")[0].strip()
    conteo_por_estado = df_agente.groupby("presence_label").size()

    # Micro-estados del asesor (tramos auxiliares <= 15 seg)
    df_agente_valid = df_agente[~df_agente["presence_label"].isin(ESTADOS_SISTEMA)]
    micro_agente = df_agente_valid[df_agente_valid["duracion_min"] <= 0.25]
    conteo_micro = micro_agente.groupby("presence_label").size()
    total_micro = len(micro_agente)

    diario_agente = df_agente.groupby(["fecha", "presence_label"])["duracion_min"].sum().reset_index()
    pivot_agente_dia = diario_agente.pivot_table(index="fecha", columns="presence_label", values="duracion_min", fill_value=0)

    # Alerta amistosa de auditoría / ítem a revisar
    if total_micro > 0:
        st.info(
            f"🔍 **Ítem a revisar:** Se identificaron **{total_micro} micro-estados (≤ 15 seg)** en el período. "
            f"En servicios mixtos (voz y no-voz, como DT FFP / Trim Team) puede deberse a alternancia operativa entre canales, "
            f"pero se sugiere validar con el asesor para descartar posibles refrescos de cola o evasión de turnos.",
            icon="🔍",
        )

    # ── Tabla Maestra Unificada de Pausas y Gestiones ─────────────────
    st.markdown(f"**Uso de Pausas y Gestiones — vs. Metas y Mediana de «{fila_sel['servicio']}»** ({n_agentes_servicio} agentes en el servicio)")

    # Formatear duración: si promedio >= 0.1 min -> X.X min, si > 0 pero < 0.1 -> X seg, si 0 -> 0.0 min
    def format_duracion(prom_min, dur_total_min):
        if prom_min >= 0.1:
            return f"{prom_min:.1f} min"
        elif dur_total_min > 0:
            sec = int(round(dur_total_min * 60))
            return f"{sec} seg" if sec > 0 else "< 1 seg"
        else:
            return "0.0 min"

    # Duración promedio de turno del agente
    fechas_agente = sorted(pivot_agente_dia.index)
    duraciones_turno = []
    for f in fechas_agente:
        turnos_dia = turnos_rango[(turnos_rango["bp"] == bp_agente) & (turnos_rango["fecha"] == f)]
        if not turnos_dia.empty:
            r = turnos_dia.iloc[0]
            f_dt = pd.Timestamp(r["fecha"])
            ini = f_dt + pd.to_timedelta(r["hora_inicio"])
            fin = f_dt + pd.to_timedelta(r["hora_fin"])
            if fin <= ini:
                fin += pd.Timedelta(days=1)
            dur = (fin - ini).total_seconds() / 60.0
            if dur > 0:
                duraciones_turno.append(dur)
    if not duraciones_turno:
        conectado_dia = df_agente[df_agente["presence_label"] != "Offline"].groupby("fecha")["duracion_min"].sum()
        promedio_turno_agente = conectado_dia.mean() if not conectado_dia.empty and conectado_dia.mean() > 0 else 480.0
    else:
        promedio_turno_agente = sum(duraciones_turno) / len(duraciones_turno)

    filas_unificadas = []
    tiene_prepausa_serv = servicio_tiene_prepausa(fila_sel["servicio"])

    # 1. Pausas reglamentarias con meta
    for c in CARDS:
        veces = int(sum(conteo_por_estado.get(p, 0) for p in c["presence"]))
        micro_c = int(sum(conteo_micro.get(p, 0) for p in c["presence"]))
        dur_total = float(df_agente[df_agente["presence_label"].isin(c["presence"])]["duracion_min"].sum())
        usado_serie = sumar_presencias(pivot_agente_dia, c["presence"])
        prom_min = float(usado_serie.mean()) if len(usado_serie) else 0.0
        pct_turno = (prom_min / promedio_turno_agente) * 100.0 if promedio_turno_agente > 0 else 0.0

        if c["unidad"] == "dia":
            if c["key"] == "lunch":
                meta_txt = "0 min (No aut.)"
                dif_min = prom_min
                adh = 100.0 if prom_min == 0 else max(0.0, 100.0 - (prom_min / promedio_turno_agente * 100.0))
            elif c["key"] == "pre_pausa":
                meta_val = 60 if tiene_prepausa_serv else 0
                meta_txt = f"{meta_val} min" if tiene_prepausa_serv else "0 min (No aut.)"
                dif_min = prom_min - meta_val
                adh = 100.0 if prom_min <= meta_val else (round(100.0 * meta_val / prom_min, 1) if meta_val > 0 else 0.0)
            elif c["key"] == "bano":
                meta_txt = f"{c['meta']} min"
                dif_min = prom_min - c["meta"]
                adh = 100.0 if prom_min <= c["meta"] else round(100.0 * c["meta"] / prom_min, 1)
            else:
                meta_txt = f"{c['meta']} min"
                dif_min = prom_min - c["meta"]
                adh = fila_sel[c["key"]]
        else:
            # CDR semanal
            meta_txt = f"{c['meta']} min/sem"
            dif_min = prom_min - (c["meta"] / 5.0)
            adh = 100.0 if prom_min == 0 else fila_sel[c["key"]]

        filas_unificadas.append({
            "Categoría": "Pausa Reglamentaria",
            "Pausa / Estado": c["label"],
            "Veces": veces,
            "Micro (≤15s)": micro_c,
            "Min. promedio/día": format_duracion(prom_min, dur_total),
            "% del Turno": round(pct_turno, 1),
            "Meta / Referencia": meta_txt,
            "Dif. vs Referencia": round(dif_min, 1),
            "Adherencia": f"{adh:.1f}%" if pd.notna(adh) else "—",
            "_adh_num": float(adh) if pd.notna(adh) else 999.0,
        })

    # 2. Gestiones operativas sin meta (comparadas contra la mediana del servicio)
    presencias_con_meta = {p for c in CARDS for p in c["presence"]}
    otras_presencias = sorted(
        set(vista_servicio["presence_label"].unique()) - presencias_con_meta - ESTADOS_SISTEMA
    )

    if otras_presencias and not vista_servicio.empty:
        diario_servicio = vista_servicio.groupby(["agente_id", "fecha", "presence_label"])["duracion_min"].sum().reset_index()
        pivot_servicio = diario_servicio.pivot_table(index=["agente_id", "fecha"], columns="presence_label", values="duracion_min", fill_value=0)
        promedio_por_agente = pivot_servicio.groupby("agente_id").mean()

        for label in otras_presencias:
            if label not in promedio_por_agente.columns:
                continue
            prom_agente = float(promedio_por_agente.loc[agente_id_sel, label]) if agente_id_sel in promedio_por_agente.index else 0.0
            dur_total = float(df_agente[df_agente["presence_label"] == label]["duracion_min"].sum())
            mediana_servicio = float(promedio_por_agente[label].median())
            pct_turno = (prom_agente / promedio_turno_agente) * 100.0 if promedio_turno_agente > 0 else 0.0
            dif_equipo = prom_agente - mediana_servicio
            micro_l = int(conteo_micro.get(label, 0))

            filas_unificadas.append({
                "Categoría": "Gestión Operativa",
                "Pausa / Estado": label,
                "Veces": int(conteo_por_estado.get(label, 0)),
                "Micro (≤15s)": micro_l,
                "Min. promedio/día": format_duracion(prom_agente, dur_total),
                "% del Turno": round(pct_turno, 1),
                "Meta / Referencia": f"Mediana: {mediana_servicio:.1f} min",
                "Dif. vs Referencia": round(dif_equipo, 1),
                "Adherencia": "—",
                "_adh_num": 999.0,
            })

    tabla_unificada = pd.DataFrame(filas_unificadas)
    if not tabla_unificada.empty:
        df_meta = tabla_unificada[tabla_unificada["Categoría"] == "Pausa Reglamentaria"].sort_values(
            by="_adh_num", ascending=True
        )
        df_gestion = tabla_unificada[tabla_unificada["Categoría"] == "Gestión Operativa"].sort_values(
            by="Dif. vs Referencia", ascending=False
        )
        tabla_unificada = pd.concat([df_meta, df_gestion], ignore_index=True).drop(columns=["_adh_num"])

    def estilo_dif(val):
        if pd.isna(val):
            return ""
        if val > 15:
            color = "#e24b4a"
        elif val > 5:
            color = "#eda100"
        else:
            color = "#1baf7a"
        return f"background-color: {color}22; color: {color}; font-weight: 600;"

    def estilo_adh(val):
        if not val or val == "—":
            return ""
        try:
            num = float(str(val).replace("%", "").strip())
            return f"background-color: {color_pct(num)}22; color: {color_pct(num)}; font-weight: 600;"
        except Exception:
            return ""

    styler_unificado = (
        tabla_unificada.style
        .map(estilo_dif, subset=["Dif. vs Referencia"])
        .map(estilo_micro, subset=["Micro (≤15s)"])
        .map(estilo_adh, subset=["Adherencia"])
    )

    st.dataframe(
        styler_unificado,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Categoría": st.column_config.TextColumn("Categoría"),
            "Pausa / Estado": st.column_config.TextColumn("Pausa / Estado"),
            "Veces": st.column_config.NumberColumn("Veces", format="%d"),
            "Micro (≤15s)": st.column_config.NumberColumn(
                "Micro (≤15s)",
                format="%d",
                help="Tramos con duración ≤ 15 seg. Ítem a revisar por posibles refrescos de cola o alternancia operativa.",
            ),
            "Min. promedio/día": st.column_config.TextColumn("Min. promedio/día"),
            "% del Turno": st.column_config.NumberColumn("% del Turno", format="%.1f%%"),
            "Meta / Referencia": st.column_config.TextColumn("Meta / Referencia"),
            "Dif. vs Referencia": st.column_config.NumberColumn("Dif. vs Referencia", format="%+.1f min"),
            "Adherencia": st.column_config.TextColumn("Adherencia"),
        },
    )

    color_map = cargar_color_map()
    render_timeline(df_agente, color_map)

    # ── Detalle diario (score por dia) ──────────────────────────────
    fechas_agente = sorted(pivot_agente_dia.index)
    st.subheader(f"Detalle diario ({len(fechas_agente)} días)")

    filas_diarias = []
    for fecha in fechas_agente:
        df_dia = df_agente[df_agente["fecha"] == fecha]
        turnos_dia = turnos_rango[(turnos_rango["bp"] == bp_agente) & (turnos_rango["fecha"] == fecha)]
        adh_dia = calcular_adherencia_horario(df_dia, turnos_dia)
        horario_dia = adh_dia["horario"].iloc[0] if not adh_dia.empty else float("nan")
        uso_dia = mapa_uso_diario.get((agente_id_sel, fecha), float("nan"))

        fila = {"Fecha": fecha, "Novedad": "—", "Uso Turno": uso_dia, "Horario": horario_dia}
        fila_valores = pivot_agente_dia.loc[[fecha]]
        for c in CARDS:
            usado_dia = sumar_presencias(fila_valores, c["presence"]).iloc[0]
            if c["tipo"] == "conteo":
                fila[c["label"]] = int(df_dia["presence_label"].isin(c["presence"]).sum())
            else:
                fila[c["label"]] = score_graduado(pd.Series([usado_dia]), c["meta"]).iloc[0]
        filas_diarias.append(fila)

    tabla_diaria = pd.DataFrame(filas_diarias).sort_values(by="Uso Turno", ascending=True)
    pct_cols_diario = ["Uso Turno", "Horario"] + [c["label"] for c in CARDS if c["tipo"] != "conteo"]
    conteo_cols_diario = [c["label"] for c in CARDS if c["tipo"] == "conteo"]
    styler_diario = (
        tabla_diaria.style.map(estilo_pct, subset=["Horario"] + [c["label"] for c in CARDS if c["tipo"] != "conteo"])
        .map(estilo_uso, subset=["Uso Turno"])
    )
    col_config_diario = {c: st.column_config.NumberColumn(c, format="%.0f%%") for c in pct_cols_diario + ["Uso Turno"]}
    col_config_diario.update({c: st.column_config.NumberColumn(c, format="%d") for c in conteo_cols_diario})
    st.dataframe(
        styler_diario,
        use_container_width=True,
        hide_index=True,
        column_config=col_config_diario,
    )

    st.subheader("Detalle de cada ítem")
    st.caption("Cada tramo registrado en Genesys, para todos los días del rango filtrado.")
    detalle_diario = df_agente[["fecha", "presence_label", "inicio", "fin", "duracion_min"]].copy()
    detalle_diario["inicio"] = pd.to_datetime(detalle_diario["inicio"]).dt.strftime("%H:%M:%S")
    detalle_diario["fin"] = pd.to_datetime(detalle_diario["fin"]).dt.strftime("%H:%M:%S")
    detalle_diario["duracion_min"] = detalle_diario["duracion_min"].round(1)
    detalle_diario = detalle_diario.sort_values(["fecha", "inicio"]).rename(
        columns={
            "fecha": "Fecha", "presence_label": "Estado", "inicio": "Hora inicio",
            "fin": "Hora fin", "duracion_min": "Duración (min)",
        }
    )
    st.dataframe(
        detalle_diario,
        use_container_width=True,
        hide_index=True,
        column_config={"Duración (min)": st.column_config.NumberColumn("Duración (min)", format="%.1f")},
    )
