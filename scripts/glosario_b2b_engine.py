"""
Módulo de Glosario y Guía de Usuario para la Operación Salesforce B2B — AMC LATAM.
Diseñado para que supervisores, coordinadores y directivos comprendan la arquitectura
de cálculo multicanal, SLA 24h de Backoffice, capacidad Omni-Channel y métricas en vivo.
"""

import streamlit as st


CATALOGO_B2B = [
    # ── Omni-Channel & Chats en Vivo ──────────────────────────────────────────
    {
        "termino": "Simultaneidad y Capacidad de Chat (Omni-Channel)",
        "categoria": "Chats en Vivo",
        "badge": "Omni-Channel",
        "color": "#2563eb",
        "resumen": "Porcentaje de ocupación del asesor en función de sus chats activos (máximo 3 concurrentes).",
        "definicion": (
            "En Salesforce Omni-Channel, cada asesor tiene configurada una capacidad máxima de <b>3 chats simultáneos</b>. "
            "El porcentaje de capacidad asignada refleja el número de interacciones activas en tiempo real:<br>"
            "• <b>1 chat activo:</b> 33% de capacidad ocupada (holgura para recibir 2 más).<br>"
            "• <b>2 chats activos:</b> 67% de capacidad ocupada (holgura para recibir 1 más).<br>"
            "• <b>3 chats activos:</b> 100% de capacidad ocupada (saturación máxima, Omni-Channel no le enruta más chats)."
        ),
        "formula": r"\% \text{Capacidad Chat} = \left(\frac{\text{Chats Activos}}{3}\right) \times 100\%",
        "ejemplo": "Si un asesor tiene 2 chats abiertos en su consola, su simultaneidad es 2/3 y su capacidad es 67%."
    },
    {
        "termino": "Estados de Conexión Omni-Channel (Available, Busy, Break)",
        "categoria": "Chats en Vivo",
        "badge": "Disponibilidad",
        "color": "#10b981",
        "resumen": "Condición de ruteo del ejecutivo dentro de la consola de atención de Salesforce.",
        "definicion": (
            "Determina si el motor de enrutamiento puede o no asignarle chats al asesor:<br>"
            "• 🟢 <b>Available (Disponible):</b> El asesor está listo en cola para aceptar chats de su skill.<br>"
            "• 🟡 <b>Busy (Ocupado / Bloqueo):</b> El asesor cambia su estado a ocupado para detener la entrada de nuevos contactos mientras resuelve un caso.<br>"
            "• 🔴 <b>Break (Descanso):</b> El asesor se encuentra en su pausa reglamentaria; no recibe chats ni computa tiempo productivo."
        ),
        "formula": r"\text{Estado Ruteo} \in \{\text{Available}, \text{Busy}, \text{Break}\}",
        "ejemplo": "Un asesor con 1 chat activo que pasa a \"Busy\" impide que el sistema le mande el 2do y 3er chat, generando cola artificial."
    },
    {
        "termino": "Tiempo de Espera en Cola (Longest Wait Time)",
        "categoria": "Chats en Vivo",
        "badge": "Experiencia Cliente",
        "color": "#f59e0b",
        "resumen": "Tiempo acumulado en minutos del chat que lleva más tiempo aguardando atención.",
        "definicion": (
            "Mide la criticidad inmediata de las colas de AMC (Español, Inglés, Corporativo). "
            "Representa los minutos transcurridos desde que el cliente agencia o corporativo inició el contacto "
            "hasta el momento actual sin haber sido aceptado por ningún ejecutivo disponible."
        ),
        "formula": r"\text{Mayor Espera (min)} = \max_{i \in \text{Cola}} \left(\frac{\text{Ahora} - \text{Hora Solicitud}_i}{60}\right)",
        "ejemplo": "Una espera de 7.2 min en \"AMC Agencias Español\" alerta saturación o falta de asesores en estado Available."
    },

    # ── Niveles de Servicio Multicanal ─────────────────────────────────────────
    {
        "termino": "Scorecard Multicanal B2B (Los 3 Pilares)",
        "categoria": "Niveles de Servicio",
        "badge": "Estrategia B2B",
        "color": "#8b5cf6",
        "resumen": "Integración de los 3 canales contractuales de AMC: Voz Genesys, Chat Salesforce y Casos Backoffice.",
        "definicion": (
            "Visión integral de atención B2B que supera la medición aislada de telefonía:<br>"
            "• 🎙️ <b>Pilar 1 (Voz Genesys):</b> Meta 80% de llamadas atendidas en ≤ 20 segundos.<br>"
            "• 💬 <b>Pilar 2 (Chats Salesforce):</b> Meta propuesta 85% de chats atendidos en ≤ 60 segundos.<br>"
            "• 📋 <b>Pilar 3 (Casos Backoffice):</b> Meta contractual de resolución y cierre en ≤ 24 horas."
        ),
        "formula": r"\text{Scorecard B2B} = \{ \text{NS Voz}_{\le 20s},\; \text{NS Chat}_{\le 60s},\; \text{SLA Casos}_{\le 24h} \}",
        "ejemplo": "Permite ver que un servicio puede estar en 91% en Voz pero con SLA de Casos crítico al 14.3%."
    },
    {
        "termino": "Nivel de Servicio Chat (Meta Propuesta 85% en 60s)",
        "categoria": "Niveles de Servicio",
        "badge": "KPI Chat",
        "color": "#10b981",
        "resumen": "Porcentaje de chats entrantes aceptados por un asesor en menos de 60 segundos.",
        "definicion": (
            "Evalúa la rapidez de respuesta en la ventana de chat. "
            "Se calcula sobre el total de chats ofrecidos en las colas de AMC, contrastando cuántos fueron "
            "atendidos antes de cumplir el umbral de 60 segundos pactado para canales digitales sincrónicos."
        ),
        "formula": r"\% \text{NS Chat} = \left(\frac{\text{Chats Atendidos} \le 60\text{s}}{\text{Chats Ofrecidos Totales}}\right) \times 100\%",
        "ejemplo": "De 100 chats ofrecidos en Agencias Inglés, 89 fueron aceptados antes del minuto: NS Chat = 89.0%."
    },
    {
        "termino": "Cumplimiento de SLA Casos 24 Horas (Meta Contractual 80%)",
        "categoria": "Niveles de Servicio",
        "badge": "SLA Backoffice",
        "color": "#ef4444",
        "resumen": "Proporción de solicitudes de backoffice resueltas antes del vencimiento de 24 horas.",
        "definicion": (
            "Indicador contractual crítico de AMC Backoffice. "
            "Un caso está <b>en norma</b> si su tiempo transcurrido desde la apertura hasta el cierre definitivo "
            "es menor o igual a 24 horas. Si supera las 24 horas, entra en <b>Infracción de SLA</b>."
        ),
        "formula": r"\% \text{SLA Casos} = \left(\frac{\text{Casos Cerrados} \le 24\text{h}}{\text{Total Casos Gestionados}}\right) \times 100\%",
        "ejemplo": "Si de 50 casos gestionados en el turno, 14 se cerraron superando las 24h, el cumplimiento de SLA fue del 72.0% (infracción del 28%)."
    },

    # ── Backlog & Antigüedad ──────────────────────────────────────────────────
    {
        "termino": "Backlog Activo de Casos",
        "categoria": "Backlog & SLA",
        "badge": "Volumen",
        "color": "#2563eb",
        "resumen": "Inventario vivo de casos abiertos en colas o asignados a bandejas pendientes de solución.",
        "definicion": (
            "Representa la carga de trabajo pendiente de la célula de Backoffice. "
            "Incluye casos en estado <i>Nuevo</i>, <i>En Gestión</i>, <i>Escalado</i> o <i>Pendiente Socio</i> "
            "que pertenecen a colas operativas de AMC (Agencias, Corporativo, Emisiones)."
        ),
        "formula": r"\text{Backlog Activo} = \sum \text{Casos con Estado} \ne \text{'Cerrado'}",
        "ejemplo": "Si AMC tiene 134 casos activos y 88 están vencidos, el 65.7% del backlog está fuera de SLA."
    },
    {
        "termino": "Termómetro de Antigüedad (Aging de Casos)",
        "categoria": "Backlog & SLA",
        "badge": "Severidad",
        "color": "#f59e0b",
        "resumen": "Segmentación del backlog según los días acumulados desde la fecha de inicio del caso.",
        "definicion": (
            "Estratificación del inventario para priorizar la evacuación operativa:<br>"
            "• 🟢 <b>< 24 Horas:</b> Casos sanos dentro de SLA.<br>"
            "• 🟡 <b>1 a 3 Días:</b> Infracción temprana; prioridad alta de asignación.<br>"
            "• 🟠 <b>4 a 7 Días:</b> Riesgo operativo moderado.<br>"
            "• 🔴 <b>8 a 15 Días:</b> Riesgo grave de reclamo de agencia.<br>"
            "• 🟣 <b>16 a 30 Días:</b> Caso estancado; requiere intervención de supervisión.<br>"
            "• ⚫ <b>> 30 Días (Crítico):</b> Máxima severidad; posible bloqueo con el socio o aerolínea."
        ),
        "formula": r"\text{Antigüedad (días)} = \frac{\text{Fecha Actual} - \text{Fecha Inicio}}{24 \times 3600}",
        "ejemplo": "Identificar 8 casos con más de 30 días permite activar un plan de choque inmediato con supervisión."
    },
    {
        "termino": "Casos sin Asignar (En Cola de Espera)",
        "categoria": "Backlog & SLA",
        "badge": "Asignación",
        "color": "#6366f1",
        "resumen": "Casos que permanecen en la cola general sin que ningún asesor los haya tomado en su bandeja.",
        "definicion": (
            "Indica el volumen de solicitudes \"huérfanas\" en la bandeja general. "
            "Un alto porcentaje de casos sin asignar revela lentitud en el reparto operativo o falta de personal "
            "dedicado a evacuar la cola de entrada antes de que los casos envejezcan."
        ),
        "formula": r"\% \text{Sin Asignar} = \left(\frac{\text{Casos en Cola sin Propietario}}{\text{Total Backlog}}\right) \times 100\%",
        "ejemplo": "Tener 55 casos (41.0%) sin asignar significa que casi la mitad del backlog ni siquiera ha empezado a ser atendido."
    },

    # ── Productividad & Rendimiento ───────────────────────────────────────────
    {
        "termino": "Casos Resueltos en Turno por Asesor",
        "categoria": "Productividad",
        "badge": "Rendimiento",
        "color": "#10b981",
        "resumen": "Volumen neto de casos llevados a estado cerrado o resuelto por un colaborador en la jornada.",
        "definicion": (
            "Métrica individual de rendimiento para asesores de Backoffice. "
            "Mide la capacidad de evacuación de cada asesor y se complementa con la <b>Eficacia SLA</b>, "
            "diferenciando cuántos casos cerró dentro del tiempo límite vs cuántos cerró ya vencidos."
        ),
        "formula": r"\text{Productividad} = \sum \text{Casos Resueltos por Asesor en el Periodo}",
        "ejemplo": "Un asesor que resuelve 12 casos por turno con 91.7% a tiempo demuestra alto rendimiento y disciplina operativa."
    },
    {
        "termino": "Eficacia de SLA Individual (% A Tiempo)",
        "categoria": "Productividad",
        "badge": "Calidad SLA",
        "color": "#2563eb",
        "resumen": "Porcentaje de los casos cerrados por el asesor que cumplieron el SLA de 24 horas.",
        "definicion": (
            "Evita evaluar la productividad por mero volumen numérico. "
            "Premia al asesor que resuelve oportunamente antes de que el caso supere las 24 horas, "
            "e identifica si un asesor está cerrando casos viejos que ya estaban en infracción."
        ),
        "formula": r"\% \text{Eficacia} = \left(\frac{\text{Casos Resueltos a Tiempo}}{\text{Total Casos Resueltos}}\right) \times 100\%",
        "ejemplo": "Resolver 10 casos a tiempo de 10 resueltos = 100% de eficacia, frente a 10 resueltos con 4 vencidos = 60%."
    },
    {
        "termino": "Carga Activa Asignada (Workload de Bandeja)",
        "categoria": "Productividad",
        "badge": "Equilibrio",
        "color": "#f59e0b",
        "resumen": "Número de casos activos asignados simultáneamente a la bandeja personal de un asesor.",
        "definicion": (
            "Permite al supervisor auditar el balance de carga en el equipo. "
            "Detecta si hay asesores sobrecargados con 15 o 20 casos activos mientras otros tienen 2 o 3, "
            "y alerta si un asesor retiene casos vencidos en su bandeja sin reportar avances."
        ),
        "formula": r"\text{Carga Asignada} = \sum \text{Casos Activos por Propietario}",
        "ejemplo": "Un asesor con 18 casos asignados y 12 en infracción requiere redistribución urgente de tareas."
    },

    # ── Mapeo de Niveles & Clasificación ──────────────────────────────────────
    {
        "termino": "Niveles de Asesor B2B (N1, N2, N3, N1-N2)",
        "categoria": "Clasificación & Malla",
        "badge": "Especialidad",
        "color": "#8b5cf6",
        "resumen": "Categorización oficial de destreza y alcance resolutivo del asesor según Socio Maestro.",
        "definicion": (
            "Estructura técnica de resolución definida por la cuenta AMC LATAM:<br>"
            "• 🔹 <b>Nivel N1:</b> Atención de primer contacto, consultas frecuentes de agencias y trámites estándar.<br>"
            "• 🔹 <b>Nivel N2:</b> Especialista resolutivo; atiende casos con complejidad tarifaria, reemisiones y cambios complejos.<br>"
            "• 🔹 <b>Nivel N3:</b> Soporte técnico avanzado, auditoría de emisiones, grupos y casos críticos corporativos.<br>"
            "• 🔹 <b>Nivel N1-N2:</b> Asesores híbridos certificados para cubrir ambos frentes según la demanda del día."
        ),
        "formula": r"\text{Nivel} \in \{\text{N1}, \text{N2}, \text{N3}, \text{N1-N2}\}",
        "ejemplo": "Al filtrar por Nivel N2 en Productividad, se evalúa únicamente a los especialistas asignados a colas complejas."
    },
    {
        "termino": "Alias de Salesforce vs Nombre Oficial",
        "categoria": "Clasificación & Malla",
        "badge": "Identidad",
        "color": "#64748b",
        "resumen": "Homologación entre el nombre de usuario corto de Salesforce y el colaborador en nómina/WFM.",
        "definicion": (
            "Salesforce Omni-Channel registra a los asesores mediante alias cortos (ej: <code>Reven</code>, <code>CALES</code>, <code>Jrepo</code>). "
            "El motor de cruce vincula automáticamente cada alias con su <b>Nombre Completo</b>, <b>Cédula/BP</b>, "
            "<b>Supervisor Inmediato</b> y <b>Coordinador</b> registrado en la matriz oficial de Socio Maestro."
        ),
        "formula": r"\text{Alias SF} \xrightarrow{\text{Socio Maestro}} (\text{Nombre Completo},\; \text{Supervisor},\; \text{Nivel},\; \text{Servicio})",
        "ejemplo": "El alias \x27Reven\x27 se resuelve en el tablero como \x27ZAPATA ARBOLEDA EDWIN ALONSO\x27, Nivel N2, a cargo del supervisor Diego Aguirre."
    }
]


def render_glosario_b2b():
    """Renderiza el Glosario y Guía Metodológica interactiva de Salesforce B2B."""
    st.markdown("### 📚 Glosario y Guía Metodológica — Operación Salesforce B2B")
    st.caption("Guía oficial de conceptos, fórmulas de cálculo, estándares contractuales de AMC LATAM y arquitectura de datos.")

    # ── 1. Buscador y Filtro por Categoría ────────────────────────────────────
    c_b1, c_b2 = st.columns([2.5, 1.5])
    with c_b1:
        query_busqueda = st.text_input(
            "🔍 Buscar concepto, fórmula o KPI de Salesforce B2B:",
            placeholder="Ej: SLA 24h, Simultaneidad, Nivel N2, Aging, Omni-Channel...",
            key="busq_glosario_b2b"
        ).strip().lower()

    categorias_disponibles = ["Todas las Categorías"] + sorted(list(set(t["categoria"] for t in CATALOGO_B2B)))
    with c_b2:
        cat_seleccionada = st.selectbox("Filtrar por Categoría:", categorias_disponibles, key="cat_glosario_b2b")

    # Filtrado dinámico
    terminos_filtrados = CATALOGO_B2B
    if cat_seleccionada != "Todas las Categorías":
        terminos_filtrados = [t for t in terminos_filtrados if t["categoria"] == cat_seleccionada]

    if query_busqueda:
        terminos_filtrados = [
            t for t in terminos_filtrados
            if query_busqueda in t["termino"].lower()
            or query_busqueda in t["resumen"].lower()
            or query_busqueda in t["definicion"].lower()
            or query_busqueda in t.get("ejemplo", "").lower()
        ]

    st.markdown(f"<p style='color:gray; font-size:13px;'>Mostrando <b>{len(terminos_filtrados)}</b> términos y conceptos operativos.</p>", unsafe_allow_html=True)
    st.write("")

    # ── 2. Renderizado de Tarjetas de Términos ────────────────────────────────
    for t in terminos_filtrados:
        with st.container():
            st.markdown(
                f"""
                <div style="background:#ffffff; border:1px solid #e2e8f0; border-radius:10px; padding:16px 20px; margin-bottom:14px; box-shadow:0 2px 6px rgba(0,0,0,0.03);">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                        <span style="font-size:16px; font-weight:700; color:#0f172a;">{t['termino']}</span>
                        <span style="background:{t['color']}18; color:{t['color']}; font-size:11px; font-weight:600; padding:3px 10px; border-radius:12px; border:1px solid {t['color']}33;">
                            {t['badge']} • {t['categoria']}
                        </span>
                    </div>
                    <p style="color:#475569; font-size:13.5px; margin:0 0 10px 0; font-weight:500;">{t['resumen']}</p>
                    <div style="color:#334155; font-size:13px; line-height:1.55; margin-bottom:10px;">{t['definicion']}</div>
                </div>
                """,
                unsafe_allow_html=True
            )

            # Fórmula KaTeX y Ejemplo práctico
            col_f, col_e = st.columns([1.1, 1.4])
            with col_f:
                st.markdown("**📐 Fórmula de Cálculo:**")
                st.latex(t["formula"])
            with col_e:
                st.markdown("**💡 Ejemplo de Aplicación:**")
                st.info(t["ejemplo"], icon="📌")

            st.write("")

    # ── 3. Tabla Resumen de Metas Contractuales B2B ───────────────────────────
    st.markdown("---")
    st.markdown("#### 🎯 Tabla de Metas y Estándares Contractuales AMC")
    st.caption("Resumen ejecutivo de umbrales y acuerdos de nivel de servicio acordados para la operación.")

    t_c1, t_c2 = st.columns(2)
    with t_c1:
        st.markdown(
            """
            <table style="width:100%; font-size:13px; border-collapse:collapse; background:#fff; border:1px solid #e2e8f0; border-radius:8px;">
                <thead>
                    <tr style="background:#f1f5f9; border-bottom:2px solid #cbd5e1; text-align:left;">
                        <th style="padding:8px 12px;">Canal / Módulo</th>
                        <th style="padding:8px 12px;">Métrica Clave</th>
                        <th style="padding:8px 12px;">Meta Oficial</th>
                        <th style="padding:8px 12px;">Frecuencia</th>
                    </tr>
                </thead>
                <tbody>
                    <tr style="border-bottom:1px solid #f1f5f9;">
                        <td style="padding:7px 12px; font-weight:600;">🎙️ Telefonía B2B (Genesys)</td>
                        <td style="padding:7px 12px;">Nivel de Servicio (NS)</td>
                        <td style="padding:7px 12px; color:#059669; font-weight:600;">80% en ≤ 20 seg</td>
                        <td style="padding:7px 12px;">Intradía / Diario</td>
                    </tr>
                    <tr style="border-bottom:1px solid #f1f5f9;">
                        <td style="padding:7px 12px; font-weight:600;">💬 Chats B2B (Salesforce)</td>
                        <td style="padding:7px 12px;">Nivel de Servicio (NS)</td>
                        <td style="padding:7px 12px; color:#059669; font-weight:600;">85% en ≤ 60 seg</td>
                        <td style="padding:7px 12px;">En Vivo (30 seg)</td>
                    </tr>
                    <tr style="border-bottom:1px solid #f1f5f9;">
                        <td style="padding:7px 12px; font-weight:600;">📋 Casos Backoffice (SF)</td>
                        <td style="padding:7px 12px;">Cumplimiento de SLA</td>
                        <td style="padding:7px 12px; color:#059669; font-weight:600;">80% en ≤ 24 horas</td>
                        <td style="padding:7px 12px;">Corte de Turno</td>
                    </tr>
                    <tr style="border-bottom:1px solid #f1f5f9;">
                        <td style="padding:7px 12px; font-weight:600;">⚡ Simultaneidad Chat</td>
                        <td style="padding:7px 12px;">Capacidad Máxima</td>
                        <td style="padding:7px 12px; color:#2563eb; font-weight:600;">3 chats / asesor</td>
                        <td style="padding:7px 12px;">Permanente</td>
                    </tr>
                </tbody>
            </table>
            """,
            unsafe_allow_html=True
        )

    with t_c2:
        st.markdown(
            """
            <table style="width:100%; font-size:13px; border-collapse:collapse; background:#fff; border:1px solid #e2e8f0; border-radius:8px;">
                <thead>
                    <tr style="background:#f1f5f9; border-bottom:2px solid #cbd5e1; text-align:left;">
                        <th style="padding:8px 12px;">Severidad Aging</th>
                        <th style="padding:8px 12px;">Rango de Días</th>
                        <th style="padding:8px 12px;">Estado Operativo</th>
                        <th style="padding:8px 12px;">Acción Requerida</th>
                    </tr>
                </thead>
                <tbody>
                    <tr style="border-bottom:1px solid #f1f5f9;">
                        <td style="padding:7px 12px; font-weight:600;">🟢 Dentro de Norma</td>
                        <td style="padding:7px 12px;">0 a 24 Horas</td>
                        <td style="padding:7px 12px; color:#059669;">Sano</td>
                        <td style="padding:7px 12px;">Gestión regular de cola</td>
                    </tr>
                    <tr style="border-bottom:1px solid #f1f5f9;">
                        <td style="padding:7px 12px; font-weight:600;">🟡 Infracción Inicial</td>
                        <td style="padding:7px 12px;">1 a 3 Días</td>
                        <td style="padding:7px 12px; color:#d97706;">Alerta</td>
                        <td style="padding:7px 12px;">Asignación prioritaria</td>
                    </tr>
                    <tr style="border-bottom:1px solid #f1f5f9;">
                        <td style="padding:7px 12px; font-weight:600;">🟠 Infracción Media</td>
                        <td style="padding:7px 12px;">4 a 7 Días</td>
                        <td style="padding:7px 12px; color:#ea580c;">Riesgo</td>
                        <td style="padding:7px 12px;">Escalar con supervisor</td>
                    </tr>
                    <tr style="border-bottom:1px solid #f1f5f9;">
                        <td style="padding:7px 12px; font-weight:600;">🔴 Infracción Crítica</td>
                        <td style="padding:7px 12px;">> 7 Días</td>
                        <td style="padding:7px 12px; color:#dc2626; font-weight:600;">Crítico</td>
                        <td style="padding:7px 12px; color:#dc2626;">Plan de choque inmediato</td>
                    </tr>
                </tbody>
            </table>
            """,
            unsafe_allow_html=True
        )

    # ── 4. Preguntas Frecuentes de la Operación B2B ───────────────────────────
    st.markdown("---")
    st.markdown("#### ❓ Preguntas Frecuentes de la Operación Salesforce B2B")

    with st.expander("¿Por qué el Command Center de Chats se actualiza en tiempo real pero el Backlog se consulta por cortes?"):
        st.markdown(
            """
            Por diseño de arquitectura y optimización de rendimiento:
            1. **Los Chats son transacciones sincrónicas efímeras:** Una cola de chat cambia cada 15 a 30 segundos. El supervisor necesita ver en el instante si hay 40 chats esperando para reubicar personal de inmediato.
            2. **Los Casos de Backoffice son procesos asincrónicos:** Su ciclo de vida se mide en horas o días (meta 24h). El reporte crudo de casos pesa casi 8 MB; procesarlo en cada segundo saturaría el navegador del usuario. El tablero utiliza una base preprocesada de alta velocidad (49 KB) que permite aplicar filtros en apenas 11 milisegundos.
            """
        )

    with st.expander("¿Cómo afecta el estado 'Busy' de un asesor a las colas de espera en Omni-Channel?"):
        st.markdown(
            """
            Cuando un asesor atiende 1 o 2 chats y cambia manualmente su estado a **Busy** (para evitar que Omni-Channel le entregue el 3er chat), bloquea su capacidad residual. 
            Si varios asesores hacen esto simultáneamente, el sistema detecta una anomalía de **'Bloqueo con cola activa'**, ya que hay clientes esperando en cola mientras el equipo mantiene capacidad libre sin aprovechar. El Command Center genera una alerta automática cuando esto ocurre.
            """
        )

    with st.expander("¿Cómo se homologa un asesor si su nombre en Salesforce difiere de Genesys?"):
        st.markdown(
            """
            El sistema cruza el archivo oficial de **Socio Maestro** (Google Sheets). Mediante la columna `Etiquetas_Especiales_2` se mapea el **Alias corto de Salesforce** (ej: `CALES`, `Reven`) con el **Nombre Completo**, la **Cédula/BP**, el **Supervisor Inmediato** y el **Nivel N1/N2/N3** registrado en la nómina de WFM.
            """
        )

    with st.expander("¿Qué diferencia hay entre 'Casos Resueltos' y 'Carga Activa en Backlog'?"):
        st.markdown(
            """
            • **Casos Resueltos:** Es el flujo de salida (output). Indica cuántos casos cerró efectivamente el asesor durante el rango de fechas seleccionado y cuál fue su tasa de eficacia dentro de las 24 horas.<br>
            • **Carga Activa (Workload):** Es el inventario actual (stock). Indica cuántos casos no cerrados tiene asignados el asesor en su bandeja personal en este preciso momento, y cuántos de ellos ya están vencidos.
            """
        )
