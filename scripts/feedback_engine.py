"""
Motor de Feedback y Reporte de Incidencias — Radar Operacional.
Permite a los usuarios reportar bugs, inconsistencias y sugerencias desde cualquier vista,
y a los administradores revisar, diagnosticar y resolver cada ticket con persistencia
centralizada en Neon Postgres y respaldo local en SQLite.
"""

import os
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pandas as pd
import streamlit as st

try:
    from audit_engine import _obtener_db_url, registrar_evento, ADMINS_AUTORIZADOS
except Exception:
    def _obtener_db_url():
        return None
    def registrar_evento(*args, **kwargs):
        pass
    ADMINS_AUTORIZADOS = {"carlosmurilloe.almacontact@outsourcing-account.com"}

LOCAL_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "feedback.db"


def init_feedback_db():
    """Inicializa la base de datos local SQLite y asegura la tabla en Neon Postgres."""
    LOCAL_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(str(LOCAL_DB_PATH))
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS feedback_tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_code TEXT UNIQUE NOT NULL,
                fecha_hora TEXT NOT NULL,
                email TEXT NOT NULL,
                nombre TEXT,
                rol TEXT,
                modulo TEXT,
                categoria TEXT,
                severidad TEXT,
                titulo TEXT,
                descripcion TEXT,
                estado TEXT DEFAULT '🆕 Nuevo',
                notas_resolucion TEXT,
                resuelto_por TEXT,
                fecha_resolucion TEXT
            );
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Feedback] Error inicializando SQLite: {e}")

    # Neon Postgres
    db_url = _obtener_db_url()
    try:
        import psycopg2
        conn = psycopg2.connect(db_url, connect_timeout=5)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS feedback_tickets (
                id SERIAL PRIMARY KEY,
                ticket_code VARCHAR(20) UNIQUE NOT NULL,
                fecha_hora TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                email VARCHAR(255) NOT NULL,
                nombre VARCHAR(255),
                rol VARCHAR(255),
                modulo VARCHAR(100),
                categoria VARCHAR(50),
                severidad VARCHAR(30),
                titulo VARCHAR(255),
                descripcion TEXT,
                estado VARCHAR(50) DEFAULT '🆕 Nuevo',
                notas_resolucion TEXT,
                resuelto_por VARCHAR(255),
                fecha_resolucion TIMESTAMP WITH TIME ZONE
            );
        """)
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[Feedback] Error inicializando Neon Postgres: {e}")


def _generar_nuevo_codigo() -> str:
    """Genera un código único secuencial tipo FB-1001."""
    max_num = 1000
    db_url = _obtener_db_url()
    try:
        import psycopg2
        conn = psycopg2.connect(db_url, connect_timeout=4)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM feedback_tickets;")
        row = cur.fetchone()
        if row and row[0] is not None:
            max_num = 1000 + int(row[0])
        cur.close()
        conn.close()
    except Exception:
        try:
            conn = sqlite3.connect(str(LOCAL_DB_PATH))
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM feedback_tickets;")
            row = cur.fetchone()
            if row and row[0] is not None:
                max_num = 1000 + int(row[0])
            conn.close()
        except Exception:
            max_num = int(datetime.now().strftime("%H%M%S"))
    return f"FB-{max_num + 1}"


def guardar_ticket(
    email: str,
    nombre: str,
    rol: str,
    modulo: str,
    categoria: str,
    severidad: str,
    titulo: str,
    descripcion: str
) -> str:
    """Guarda un nuevo ticket de feedback en Neon Postgres y espejo local SQLite."""
    init_feedback_db()
    ticket_code = _generar_nuevo_codigo()
    ahora_utc = datetime.now(timezone.utc)
    ahora_col = ahora_utc - timedelta(hours=5)
    ahora_str = ahora_col.strftime("%Y-%m-%d %H:%M:%S")

    # 1. Guardar en Neon Postgres
    db_url = _obtener_db_url()
    try:
        import psycopg2
        conn = psycopg2.connect(db_url, connect_timeout=6)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO feedback_tickets 
            (ticket_code, fecha_hora, email, nombre, rol, modulo, categoria, severidad, titulo, descripcion, estado)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, '🆕 Nuevo');
        """, (
            ticket_code,
            ahora_col,
            email.strip().lower(),
            (nombre or "").strip(),
            (rol or "").strip(),
            modulo,
            categoria,
            severidad,
            titulo.strip(),
            descripcion.strip()
        ))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[Feedback] Aviso guardando en Neon: {e}")

    # 2. Guardar en SQLite local
    try:
        conn = sqlite3.connect(str(LOCAL_DB_PATH))
        cur = conn.cursor()
        cur.execute("""
            INSERT OR REPLACE INTO feedback_tickets 
            (ticket_code, fecha_hora, email, nombre, rol, modulo, categoria, severidad, titulo, descripcion, estado)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '🆕 Nuevo');
        """, (
            ticket_code,
            ahora_str,
            email.strip().lower(),
            (nombre or "").strip(),
            (rol or "").strip(),
            modulo,
            categoria,
            severidad,
            titulo.strip(),
            descripcion.strip()
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Feedback] Aviso guardando en SQLite: {e}")

    registrar_evento(email, nombre, modulo, "feedback_enviado", f"Ticket {ticket_code}: {titulo}")
    return ticket_code


def obtener_todos_los_tickets() -> pd.DataFrame:
    """Obtiene los tickets registrados, priorizando Neon Postgres con fallback a SQLite."""
    db_url = _obtener_db_url()
    try:
        import psycopg2
        conn = psycopg2.connect(db_url, connect_timeout=5)
        query = """
            SELECT id, ticket_code, fecha_hora, email, nombre, rol, modulo, 
                   categoria, severidad, titulo, descripcion, estado, 
                   notas_resolucion, resuelto_por, fecha_resolucion
            FROM feedback_tickets
            ORDER BY fecha_hora DESC;
        """
        df = pd.read_sql_query(query, conn)
        conn.close()
        if not df.empty:
            df["fecha_hora"] = pd.to_datetime(df["fecha_hora"])
            return df
    except Exception as e:
        print(f"[Feedback] Fallback a SQLite: {e}")

    try:
        if os.path.exists(LOCAL_DB_PATH):
            conn = sqlite3.connect(str(LOCAL_DB_PATH))
            df = pd.read_sql_query("SELECT * FROM feedback_tickets ORDER BY id DESC;", conn)
            conn.close()
            if not df.empty:
                df["fecha_hora"] = pd.to_datetime(df["fecha_hora"])
                return df
    except Exception as e:
        print(f"[Feedback] Error leyendo SQLite: {e}")

    return pd.DataFrame()


def actualizar_estado_ticket(ticket_code: str, nuevo_estado: str, notas: str, admin_email: str) -> bool:
    """Actualiza el estado de un ticket y registra las notas del desarrollador."""
    ahora_col = datetime.now(timezone.utc) - timedelta(hours=5)
    ahora_str = ahora_col.strftime("%Y-%m-%d %H:%M:%S")

    db_url = _obtener_db_url()
    try:
        import psycopg2
        conn = psycopg2.connect(db_url, connect_timeout=5)
        cur = conn.cursor()
        cur.execute("""
            UPDATE feedback_tickets
            SET estado = %s, notas_resolucion = %s, resuelto_por = %s, fecha_resolucion = %s
            WHERE ticket_code = %s;
        """, (nuevo_estado, notas.strip(), admin_email, ahora_col, ticket_code))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[Feedback] Error actualizando en Neon: {e}")

    try:
        conn = sqlite3.connect(str(LOCAL_DB_PATH))
        cur = conn.cursor()
        cur.execute("""
            UPDATE feedback_tickets
            SET estado = ?, notas_resolucion = ?, resuelto_por = ?, fecha_resolucion = ?
            WHERE ticket_code = ?;
        """, (nuevo_estado, notas.strip(), admin_email, ahora_str, ticket_code))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Feedback] Error actualizando en SQLite: {e}")

    registrar_evento(admin_email, "Admin", "Feedback", "ticket_actualizado", f"{ticket_code} -> {nuevo_estado}")
    return True


@st.dialog("💬 Reportar Problema o Sugerencia", width="large")
def render_dialog_feedback(current_email: str, current_name: str, current_role: str, seccion_actual: str):
    """Renderiza el modal emergente para que los colaboradores reporten incidencias o feedback."""
    st.markdown(
        """
        <div style="background:#f0f9ff; border:1px solid #bae6fd; border-radius:10px; padding:12px 16px; margin-bottom:15px; font-size:13px; color:#0369a1; line-height:1.4;">
            ✨ <b>¡Tu opinión ayuda a mejorar el Radar Operacional!</b><br>
            Reporta cualquier fallo técnico, diferencia que encuentres con Genesys o Salesforce, o comparte ideas para hacer el tablero más útil para tu equipo.
        </div>
        """,
        unsafe_allow_html=True
    )

    col1, col2 = st.columns(2)
    with col1:
        categoria = st.selectbox(
            "Tipo de Reporte:",
            [
                "🐛 Bug / Error de Pantalla o Visualización",
                "⚠️ Inconsistencia de Datos (vs Genesys o Salesforce)",
                "💡 Sugerencia / Nueva Funcionalidad",
                "❓ Duda o Pregunta sobre una Métrica",
                "Otro Asunto"
            ],
            index=0,
            key="fb_input_categoria"
        )
        modulos_disp = [
            "🏢 Agencias B2B",
            "✈️ LATAM Pasajeros",
            "🎫 Zendesk",
            "🧭 Capacidad y Diagnóstico",
            "🚨 Control de Ausentismo",
            "📚 Glosario & Guía",
            "General / Todo el Dashboard"
        ]
        def_idx = modulos_disp.index(seccion_actual) if seccion_actual in modulos_disp else 0
        modulo_sel = st.selectbox("Módulo / Vista Afectada:", modulos_disp, index=def_idx, key="fb_input_modulo")

    with col2:
        severidad = st.selectbox(
            "Nivel de Urgencia / Impacto:",
            [
                "🟢 Leve (Detalle cosmético o duda menor)",
                "🟡 Media (Dificulta el seguimiento normal)",
                "🔴 Alta (Cálculo incorrecto o bloqueo de operación)"
            ],
            index=1,
            key="fb_input_severidad"
        )
        st.markdown(
            f"""
            <div style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px; padding:8px 12px; margin-top:24px; font-size:12px; color:#475569;">
                <b>👤 Reportante:</b> {current_name}<br>
                <b>✉️ Correo:</b> {current_email}<br>
                <b>🏷️ Rol:</b> {current_role or 'Colaborador'}
            </div>
            """,
            unsafe_allow_html=True
        )

    titulo = st.text_input(
        "Título breve del reporte:",
        placeholder="Ej: Las llamadas entrantes de Target ESP difieren con Genesys a las 10:30",
        key="fb_input_titulo"
    )

    descripcion = st.text_area(
        "Descripción detallada del problema o sugerencia:",
        placeholder="Cuéntanos exactamente qué estabas haciendo, qué valor o comportamiento observaste, y qué valor o resultado esperabas ver...",
        height=130,
        key="fb_input_desc"
    )

    c_b1, c_b2 = st.columns([1, 1])
    with c_b2:
        enviar_btn = st.button("🚀 Enviar Reporte", type="primary", use_container_width=True, key="fb_btn_submit")

    if enviar_btn:
        if not titulo.strip():
            st.error("⚠️ Por favor escribe un título breve para identificar el reporte.")
        elif not descripcion.strip() or len(descripcion.strip()) < 10:
            st.error("⚠️ Por favor ingresa una descripción más detallada (mínimo 10 caracteres).")
        else:
            with st.spinner("Registrando ticket..."):
                t_code = guardar_ticket(
                    email=current_email,
                    nombre=current_name,
                    rol=current_role,
                    modulo=modulo_sel,
                    categoria=categoria.split()[1] if len(categoria.split()) > 1 else categoria,
                    severidad=severidad.split()[0],
                    titulo=titulo,
                    descripcion=descripcion
                )
            st.success(f"🎉 ¡Gracias! Tu reporte ha sido registrado con el código **{t_code}**. Nuestro equipo lo revisará y ajustará lo que sea necesario.")
            st.toast(f"Ticket {t_code} generado exitosamente", icon="✅")
            st.rerun()


def render_panel_gestion_feedback(current_email: str):
    """Panel administrativo para consultar, interpretar y gestionar tickets de feedback."""
    st.markdown("### 📬 Bandeja de Feedback & Reporte de Incidencias")
    st.caption("Centralización de reportes enviados por los usuarios del dashboard. Permite diagnosticar, actualizar estados y dejar notas técnicas de solución.")

    init_feedback_db()
    df = obtener_todos_los_tickets()

    if df.empty:
        st.info("ℹ️ Aún no hay tickets de feedback registrados.")
        return

    # Contadores Superiores
    total_t = len(df)
    nuevos = len(df[df["estado"] == "🆕 Nuevo"])
    en_progreso = len(df[df["estado"].isin(["🔍 En Revisión", "🛠️ En Progreso", "🛠️ En Corrección"])])
    resueltos = len(df[df["estado"] == "✅ Resuelto"])

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("📥 Total Reportes", total_t)
    m2.metric("🆕 Nuevos / Por Revisar", nuevos, delta=f"{nuevos} pendientes", delta_color="inverse" if nuevos > 0 else "normal")
    m3.metric("🛠️ En Análisis / Progreso", en_progreso)
    m4.metric("✅ Resueltos", resueltos)

    st.markdown("---")

    # Filtros de búsqueda
    f_c1, f_c2, f_c3 = st.columns([1.5, 1.5, 1.5])
    with f_c1:
        filtro_est = st.selectbox(
            "Filtrar por Estado:",
            ["Todos los Estados", "🆕 Nuevo", "🔍 En Revisión", "🛠️ En Progreso", "✅ Resuelto", "❌ Descartado"],
            key="fb_mgr_filtro_est"
        )
    with f_c2:
        modulos_existentes = ["Todos los Módulos"] + sorted(list(df["modulo"].dropna().unique()))
        filtro_mod = st.selectbox("Filtrar por Módulo:", modulos_existentes, key="fb_mgr_filtro_mod")
    with f_c3:
        cats_existentes = ["Todas las Categorías"] + sorted(list(df["categoria"].dropna().unique()))
        filtro_cat = st.selectbox("Filtrar por Categoría:", cats_existentes, key="fb_mgr_filtro_cat")

    df_filtrado = df.copy()
    if filtro_est != "Todos los Estados":
        df_filtrado = df_filtrado[df_filtrado["estado"] == filtro_est]
    if filtro_mod != "Todos los Módulos":
        df_filtrado = df_filtrado[df_filtrado["modulo"] == filtro_mod]
    if filtro_cat != "Todas las Categorías":
        df_filtrado = df_filtrado[df_filtrado["categoria"] == filtro_cat]

    st.write(f"Mostrando **{len(df_filtrado)}** tickets:")

    # Renderizar cada ticket como tarjeta expandible interactiva
    for _, row in df_filtrado.iterrows():
        t_id = row["ticket_code"]
        f_dt = row["fecha_hora"].strftime("%Y-%m-%d %H:%M") if pd.notna(row["fecha_hora"]) else "-"
        sev = row.get("severidad", "🟢")
        estado_badge = row.get("estado", "🆕 Nuevo")
        cat = row.get("categoria", "Reporte")
        mod = row.get("modulo", "General")
        tit = row.get("titulo", "Sin título")

        expander_title = f"{estado_badge} | {sev} | **{t_id}** • {tit} — ({mod} • {f_dt})"
        with st.expander(expander_title, expanded=(estado_badge == "🆕 Nuevo")):
            col_det1, col_det2 = st.columns([2, 1])
            with col_det1:
                st.markdown(f"**📝 Título:** {tit}")
                st.markdown(f"**💬 Detalle reportado por el usuario:**")
                st.info(row.get("descripcion", "Sin descripción"))

                if row.get("notas_resolucion"):
                    st.markdown(f"**🛠️ Notas de Resolución previas:**")
                    st.success(f"{row['notas_resolucion']}\n\n*(Resuelto por: {row.get('resuelto_por', 'Admin')})*")

            with col_det2:
                st.markdown(
                    f"""
                    <div style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px; padding:10px 12px; font-size:12px; line-height:1.6;">
                        <b>👤 Usuario:</b> {row.get('nombre', 'Desconocido')}<br>
                        <b>✉️ Correo:</b> {row.get('email', '')}<br>
                        <b>🏷️ Rol:</b> {row.get('rol', 'Colaborador')}<br>
                        <b>📂 Módulo:</b> {mod}<br>
                        <b>🏷️ Categoría:</b> {cat}<br>
                        <b>📅 Fecha:</b> {f_dt}
                    </div>
                    """,
                    unsafe_allow_html=True
                )

                st.write("")
                # Formulario para actualizar estado
                estados_opciones = ["🆕 Nuevo", "🔍 En Revisión", "🛠️ En Progreso", "✅ Resuelto", "❌ Descartado"]
                curr_idx = estados_opciones.index(estado_badge) if estado_badge in estados_opciones else 0
                nuevo_est = st.selectbox(
                    "Cambiar Estado:",
                    estados_opciones,
                    index=curr_idx,
                    key=f"fb_sel_est_{t_id}"
                )
                nuevas_notas = st.text_input(
                    "Notas de solución / Respuesta técnica:",
                    value=row.get("notas_resolucion") or "",
                    placeholder="Ej: Corregido en commit xyz...",
                    key=f"fb_notas_{t_id}"
                )
                if st.button("💾 Guardar Actualización", key=f"fb_btn_save_{t_id}", use_container_width=True, type="secondary"):
                    actualizar_estado_ticket(t_id, nuevo_est, nuevas_notas, current_email)
                    st.toast(f"Ticket {t_id} actualizado a {nuevo_est}", icon="✅")
                    st.rerun()
