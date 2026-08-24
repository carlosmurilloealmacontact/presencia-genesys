"""
Visor de presencia diaria por agente.

Uso:
    streamlit run viewer.py
"""

import sqlite3
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from config import DB_PATH

# Paleta amplia (50 colores) para cubrir sin colisiones los ~33 estados de presencia posibles
PALETA_ESTADOS = px.colors.qualitative.Alphabet + px.colors.qualitative.Dark24

st.set_page_config(page_title="Presencia diaria", layout="wide")

st.markdown(
    """
    <style>
    .leyenda-sticky {
        position: sticky;
        top: 2.75rem;
        z-index: 999;
        background: var(--background-color, white);
        padding: 0.5rem 0 0.75rem 0;
        border-bottom: 1px solid rgba(128,128,128,0.25);
    }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(ttl=60)
def cargar_fechas_disponibles() -> list[str]:
    db_path = Path(__file__).parent / DB_PATH
    if not db_path.exists():
        return []
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT DISTINCT fecha FROM segments ORDER BY fecha DESC").fetchall()
    return [r[0] for r in rows]


@st.cache_data(ttl=60)
def cargar_dia(fecha: str) -> pd.DataFrame:
    db_path = Path(__file__).parent / DB_PATH
    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql_query("SELECT * FROM segments WHERE fecha = ?", conn, params=(fecha,))
    if df.empty:
        return df
    df["inicio"] = pd.to_datetime(df["inicio"])
    # Tramos sin fin (todavia en curso al momento de la extraccion) se cierran visualmente en +1 min
    df["fin"] = pd.to_datetime(df["fin"]).fillna(df["inicio"] + pd.Timedelta(minutes=1))
    # Tramos que se extienden mucho mas alla de este dia (ej. offline por varios dias) se
    # recortan al cierre del dia para que no disparen la escala del grafico
    fin_del_dia = pd.Timestamp(fecha) + pd.Timedelta(days=1)
    df["fin"] = df["fin"].clip(upper=fin_del_dia)
    return df


def multiselect_opciones(serie: pd.Series) -> list[str]:
    return sorted(v for v in serie.unique() if v)


@st.cache_data(ttl=300)
def cargar_color_map() -> dict[str, str]:
    """
    Color fijo por estado, asignado sobre el universo COMPLETO de estados ya
    vistos en la base (no solo los del dia/filtro actual). Asi el color de
    'Break' nunca cambia sin importar que otros estados esten presentes.
    """
    db_path = Path(__file__).parent / DB_PATH
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT DISTINCT presence_label FROM segments ORDER BY presence_label").fetchall()
    universo = [r[0] for r in rows]
    return {estado: PALETA_ESTADOS[i % len(PALETA_ESTADOS)] for i, estado in enumerate(universo)}


st.title("Presencia diaria por agente")

fechas = cargar_fechas_disponibles()
if not fechas:
    st.warning("Todavía no hay datos extraídos. Corre `python extract_presencia.py` primero.")
    st.stop()

fecha_sel = st.selectbox("Día", fechas, index=0)
df = cargar_dia(fecha_sel)
if df.empty:
    st.info("Sin tramos para este día.")
    st.stop()

FILTROS = [
    ("coord_sel", "coordinador", "Coordinador (vacío = todos)"),
    ("superv_sel", "jefe_inmediato", "Supervisor (vacío = todos)"),
    ("servicio_sel", "servicio", "Servicio (vacío = todos)"),
    ("agentes_sel", "agente", "Agentes (vacío = todos)"),
]


def aplicar_filtros(df: pd.DataFrame, excluir_key: str | None) -> pd.DataFrame:
    """Filtra df por todas las selecciones actuales en session_state, salvo excluir_key."""
    vista = df
    for key, columna, _ in FILTROS:
        if key == excluir_key:
            continue
        seleccion = st.session_state.get(key, [])
        if seleccion:
            vista = vista[vista[columna].isin(seleccion)]
    return vista


# Cada filtro muestra solo las opciones que siguen siendo validas dado lo elegido
# en los OTROS filtros (cruzados entre si), y se podan selecciones que dejaron de
# existir para no romper el widget.
cols = st.columns(4)
for (key, columna, label), col in zip(FILTROS, cols):
    opciones = multiselect_opciones(aplicar_filtros(df, excluir_key=key)[columna])
    seleccion_actual = st.session_state.get(key, [])
    podada = [v for v in seleccion_actual if v in opciones]
    if podada != seleccion_actual:
        st.session_state[key] = podada
    with col:
        st.multiselect(label, opciones, key=key)

vista = aplicar_filtros(df, excluir_key=None)

if vista.empty:
    st.info("Sin tramos para estos filtros.")
    st.stop()

st.caption(f"{vista['agente'].nunique()} agentes · {len(vista)} tramos")

color_map_global = cargar_color_map()
estados = sorted(vista["presence_label"].unique())
color_map = {estado: color_map_global[estado] for estado in estados}

leyenda_html = '<div class="leyenda-sticky"><div style="display:flex; flex-wrap:wrap; gap:14px;">'
for estado in estados:
    color = color_map[estado]
    leyenda_html += (
        f'<span style="display:flex; align-items:center; gap:6px; font-size:13px;">'
        f'<span style="width:11px; height:11px; border-radius:2px; background:{color}; flex-shrink:0;"></span>'
        f"{estado}</span>"
    )
leyenda_html += "</div></div>"
st.markdown(leyenda_html, unsafe_allow_html=True)

fig = px.timeline(
    vista.sort_values("agente"),
    x_start="inicio",
    x_end="fin",
    y="agente",
    color="presence_label",
    color_discrete_map=color_map,
    hover_data={"duracion_min": True, "inicio": True, "fin": True, "agente": False},
)
fig.update_yaxes(autorange="reversed")
altura = max(300, 28 * vista["agente"].nunique() + 120)
fig.update_layout(height=altura, showlegend=False, xaxis_title=None, yaxis_title=None)
st.plotly_chart(fig, use_container_width=True)

st.subheader("Minutos por estado")
resumen = (
    vista.groupby(["agente", "presence_label"])["duracion_min"]
    .sum()
    .round(1)
    .unstack(fill_value=0)
    .sort_index()
)
st.dataframe(resumen, use_container_width=True)
st.download_button(
    "Descargar CSV",
    resumen.to_csv().encode("utf-8"),
    file_name=f"minutos_por_estado_{fecha_sel}.csv",
    mime="text/csv",
)
