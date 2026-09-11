"""
Módulo de auditoría y usabilidad para el panel de Genesys Cloud.
Registra inicios de sesión, cambios de pestaña y descargas en Neon Postgres.
Permite visualizar estadísticas ejecutivas de adopción y uso del tablero.
"""

import os
from datetime import datetime, timezone, timedelta
import pandas as pd
import streamlit as st

DEFAULT_NEON_URL = "postgresql://neondb_owner:npg_u94jTQIadNYr@ep-proud-violet-a5lapj40-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require"
DOMINIO_CORPORATIVO = "@outsourcing-account.com"

ADMINS_AUTORIZADOS = {
    "carlosmurilloe.almacontact@outsourcing-account.com",
}


def _obtener_db_url() -> str:
    """Obtiene la URL de Neon Postgres desde st.secrets, entorno o por defecto."""
    try:
        if "NEON_DB_URL" in st.secrets:
            return str(st.secrets["NEON_DB_URL"]).strip()
    except Exception:
        pass
    return os.environ.get("NEON_DB_URL", DEFAULT_NEON_URL)


def registrar_evento(email: str, nombre: str, seccion: str, accion: str, detalles: str = ""):
    """Registra una acción de usuario en la tabla audit_usabilidad de Neon Postgres."""
    if not email:
        return

    db_url = _obtener_db_url()
    try:
        import psycopg2
        conn = psycopg2.connect(db_url, connect_timeout=5)
        cur = conn.cursor()
        
        # Fecha hora en hora Colombia (UTC-5)
        ahora_col = datetime.now(timezone.utc) - timedelta(hours=5)
        
        cur.execute("""
            INSERT INTO audit_usabilidad (email, nombre, seccion, accion, fecha_hora, detalles)
            VALUES (%s, %s, %s, %s, %s, %s);
        """, (email.strip().lower(), (nombre or "").strip(), seccion, accion, ahora_col, detalles))
        
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[Audit] Aviso al registrar evento: {e}")


@st.cache_data(ttl=60)
def obtener_datos_auditoria(dias: int = 30) -> pd.DataFrame:
    """Consulta los eventos de auditoría de los últimos N días."""
    db_url = _obtener_db_url()
    try:
        import psycopg2
        conn = psycopg2.connect(db_url, connect_timeout=5)
        query = f"""
            SELECT id, email, nombre, seccion, accion, fecha_hora, detalles
            FROM audit_usabilidad
            WHERE fecha_hora >= CURRENT_TIMESTAMP - INTERVAL '{dias} days'
            ORDER BY fecha_hora DESC;
        """
        df = pd.read_sql_query(query, conn)
        conn.close()
        return df
    except Exception as e:
        print(f"[Audit] Error consultando auditoría: {e}")
        return pd.DataFrame()


def render_panel_auditoria():
    """Renderiza el dashboard de analítica de adopción y usabilidad."""
    st.markdown("### 📊 Métricas de Usabilidad & Adopción del Panel")
    st.caption("Seguimiento de accesos, usuarios frecuentes y secciones más consultadas (Neon Postgres).")

    df = obtener_datos_auditoria(dias=30)
    if df.empty:
        st.info("ℹ️ Aún no hay registros de auditoría almacenados.")
        return

    df["fecha_hora"] = pd.to_datetime(df["fecha_hora"])
    df["fecha"] = df["fecha_hora"].dt.date
    df["hora"] = df["fecha_hora"].dt.hour

    total_eventos = len(df)
    usuarios_unicos = df["email"].nunique()
    logins_totales = len(df[df["accion"] == "login"])
    descargas_excel = len(df[df["accion"].str.contains("descarga", case=False, na=False)])

    # KPIs Superiores
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("👥 Usuarios Únicos", usuarios_unicos)
    c2.metric("🔑 Inicios de Sesión", logins_totales)
    c3.metric("📈 Interacciones Totales", total_eventos)
    c4.metric("📥 Reportes Descargados", descargas_excel)

    st.markdown("---")

    col_g1, col_g2 = st.columns(2)

    with col_g1:
        st.markdown("#### 📅 Actividad Diaria")
        df_dia = df.groupby("fecha").agg(
            interacciones=("id", "count"),
            usuarios=("email", "nunique")
        ).reset_index()
        st.bar_chart(df_dia, x="fecha", y=["interacciones", "usuarios"], use_container_width=True)

    with col_g2:
        st.markdown("#### 📂 Secciones Más Consultadas")
        df_sec = df[df["seccion"].str.strip() != ""].groupby("seccion")["id"].count().reset_index()
        df_sec.columns = ["Sección", "Visitas"]
        df_sec = df_sec.sort_values(by="Visitas", ascending=False)
        st.dataframe(df_sec, use_container_width=True, hide_index=True)

    st.markdown("#### 🏆 Ranking de Colaboradores Más Activos")
    df_users = df.groupby(["email", "nombre"]).agg(
        total_interacciones=("id", "count"),
        ultimo_acceso=("fecha_hora", "max")
    ).reset_index().sort_values(by="total_interacciones", ascending=False)

    df_users["ultimo_acceso"] = df_users["ultimo_acceso"].dt.strftime("%Y-%m-%d %I:%M %p")
    st.dataframe(
        df_users.rename(columns={
            "email": "Correo",
            "nombre": "Nombre",
            "total_interacciones": "Total Acciones",
            "ultimo_acceso": "Última Conexión"
        }),
        use_container_width=True,
        hide_index=True
    )

    with st.expander("🔍 Ver log detallado de actividad (últimos 50 eventos)"):
        st.dataframe(
            df[["fecha_hora", "email", "nombre", "seccion", "accion", "detalles"]].head(50),
            use_container_width=True,
            hide_index=True
        )