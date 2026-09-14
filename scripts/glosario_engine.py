"""
Módulo de Glosario y Guía de Usuario para Radar Genesys / Panel 4DX.
Diseñado para capacitar a todo el público (Supervisores, Coordinadores, WFM, Gerencia)
con explicaciones claras, fórmulas, guías de uso por módulo y buscador interactivo.
"""

import streamlit as st


# Catálogo estructurado de términos del glosario
TERMINOS_GLOSARIO = [
    # ── Capacidad & WFM ────────────────────────────────────────────────────────
    {
        "termino": "FTE (Full-Time Equivalent / Equivalente de Tiempo Completo)",
        "categoria": "Capacidad & WFM",
        "badge": "Dotación",
        "color": "#2563eb",
        "resumen": "Unidad de medida estándar que representa la jornada legal completa de un asesor.",
        "definicion": (
            "1 FTE equivale exactamente a <b>8 horas netas de trabajo al día (480 minutos)</b>. "
            "En evaluaciones de un solo día, los FTEs conectados son los minutos conectados divididos entre 480. "
            "En periodos de varios días (rango de N días), se calcula como el promedio diario de personas: "
            "<code>FTE = Minutos Totales / (480 × N días)</code>."
        ),
        "formula": r"\text{FTE} = \frac{\sum \text{Minutos}}{480 \text{ min} \times N \text{ días}}",
        "ejemplo": "Si un servicio acumuló 24,000 minutos de conexión en 5 días: 24,000 / (480 × 5) = 10.0 FTEs promedio diario."
    },
    {
        "termino": "Requerido del Mes (Forecast / Dimensionamiento)",
        "categoria": "Capacidad & WFM",
        "badge": "Planificación",
        "color": "#2563eb",
        "resumen": "Base oficial de personal y minutos planificados por WFM para atender la demanda.",
        "definicion": (
            "Proyección oficial dimensionada mes a mes por el área de Workforce Management (WFM). "
            "Establece cuántos minutos-hombre y asesores equivalentes (FTEs) deben estar conectados y productivos "
            "en cada intervalo de 30 minutos para garantizar el nivel de servicio bajo la meta plana de AHT."
        ),
        "formula": r"\text{FTE Requerido} = \frac{\text{Minutos Requeridos}}{480 \text{ min}}",
        "ejemplo": "Si el modelo dimensionó 4,800 minutos para un turno, se requieren exactamente 10 FTEs en esa jornada."
    },
    {
        "termino": "% Cumplimiento de Capacidad Neta",
        "categoria": "Capacidad & WFM",
        "badge": "Kpi Clave",
        "color": "#10b981",
        "resumen": "Porcentaje de los minutos requeridos que fueron efectivamente entregados en estados productivos.",
        "definicion": (
            "Evalúa con rigor si la operación entregó el tiempo productivo que el plan exigía. "
            "Solo toma en cuenta los <b>minutos disponibles / productivos</b> (Available, On Queue, y Casos Backoffice en servicios autorizados). "
            "No premia la sola conexión si el tiempo se perdió en pausas o auxiliares excesivos."
        ),
        "formula": r"\% \text{Capacidad} = \left(\frac{\text{Minutos Disponibles Reales}}{\text{Minutos Requeridos}}\right) \times 100\%",
        "ejemplo": "Si se requerían 10,000 minutos y se lograron 9,200 minutos disponibles, el cumplimiento de capacidad fue del 92.0%."
    },
    {
        "termino": "Brecha de Personal (Brecha FTE)",
        "categoria": "Capacidad & WFM",
        "badge": "Dotación",
        "color": "#2563eb",
        "resumen": "Diferencia neta entre el personal conectado real y el personal requerido por el plan.",
        "definicion": (
            "Indica si el servicio contó con holgura o déficit de colaboradores. "
            "Un valor positivo (🟢 <b>+FTE</b>) representa superávit de personal; "
            "un valor negativo (🔴 <b>-FTE</b>) alerta un faltante de personal que pone en riesgo el servicio."
        ),
        "formula": r"\text{Brecha FTE} = \text{FTE Conectados} - \text{FTE Requeridos}",
        "ejemplo": "+2.5 FTE significa que hubo 2.5 asesores más de lo planeado; -3.0 FTE significa que faltaron 3 asesores."
    },
    {
        "termino": "Meta Oficial de Auxiliares (14%)",
        "categoria": "Capacidad & WFM",
        "badge": "Disciplina",
        "color": "#f59e0b",
        "resumen": "Límite máximo pactado de tiempo en pausas y estados no productivos sobre la conexión.",
        "definicion": (
            "Estándar oficial acordado por el comité de operaciones de la cuenta. "
            "Establece que de todo el tiempo que un asesor está conectado, como máximo el <b>14.0%</b> puede "
            "destinarse a pausas de ley, necesidades personales, reuniones y capacitaciones. "
            "El <b>86.0%</b> restante debe ser disponibilidad productiva directa."
        ),
        "formula": r"\% \text{Auxiliares} = \left(\frac{\text{Minutos en Pausas}}{\text{Minutos Conectados Totales}}\right) \times 100\% \le 14.0\%",
        "ejemplo": "En una jornada de 480 min conectados, el tiempo máximo permitido en pausas es de 67.2 minutos (14%)."
    },
    {
        "termino": "Horas de Fuga por Exceso de Auxiliares",
        "categoria": "Capacidad & WFM",
        "badge": "Pérdida",
        "color": "#ef4444",
        "resumen": "Horas-hombre netas destruidas por haber superado el límite del 14% de pausas.",
        "definicion": (
            "Cuantifica el impacto económico y operativo del sobreconsumo de pausas. "
            "Calcula cuántas horas adicionales se consumieron en estados auxiliares por encima del 14% autorizado, "
            "y las traduce en su equivalente de asesores completos que se perdieron durante el turno o periodo."
        ),
        "formula": r"\text{Minutos Fuga} = \max\Big(0,\; \text{Minutos Pausa} - (\text{Minutos Conectados} \times 0.14)\Big)",
        "ejemplo": "Si un equipo tuvo 1,200 min de pausas cuando su cupo del 14% era 900 min, se fugaron 300 min (5.0 horas ≈ 0.6 FTEs)."
    },
    {
        "termino": "Diagnóstico de Causa Raíz",
        "categoria": "Capacidad & WFM",
        "badge": "Atribución",
        "color": "#8b5cf6",
        "resumen": "Veredicto analítico que dictamina la razón exacta del descalce de capacidad.",
        "definicion": (
            "Clasificación automática gerencial de cada servicio:<br>"
            "• 🟢 <b>Capacidad Cumplida / Óptima:</b> Logró ≥ 95% de capacidad con auxiliares controlados.<br>"
            "• 🔴 <b>Déficit Personas:</b> Faltó personal conectado (Brecha < -1.5 FTE).<br>"
            "• 🔴 <b>Fuga Auxiliares:</b> Hubo personas pero el % de pausas superó el 16% (meta 14% + 2%).<br>"
            "• 🟡 <b>Dilución Operativa Intradía:</b> El total diario calza pero hubo descalce de franjas horarias."
        ),
        "formula": r"\text{Veredicto}(\text{Brecha FTE},\; \% \text{Auxiliares},\; \% \text{Capacidad})",
        "ejemplo": "Permite responder al instante al cliente o a la gerencia si la caída fue por ausentismo o por disciplina de piso."
    },

    # ── Monitoreo en Vivo & Piso ───────────────────────────────────────────────
    {
        "termino": "Estado ACD / Routing Status",
        "categoria": "Monitoreo en Vivo",
        "badge": "Genesys Cloud",
        "color": "#0ea5e9",
        "resumen": "Condición técnica en que el motor de Genesys encuentra al asesor para asignarle tráfico.",
        "definicion": (
            "• <b>INTERACTING:</b> El asesor está atendiendo una llamada activa o chat en curso.<br>"
            "• <b>IDLE:</b> El asesor está disponible en cola esperando que el ACD le transfiera un contacto.<br>"
            "• <b>NOT_RESPONDING:</b> El ACD intentó pasar una llamada al asesor y este no contestó a tiempo (alerta roja).<br>"
            "• <b>COMMUNICATING:</b> Interacción en proceso de marcación o transferencia.<br>"
            "• <b>OFF_QUEUE:</b> El asesor está conectado pero en una pausa no disponible para recibir llamadas."
        ),
        "formula": r"\text{Genesys ACD Routing Engine}",
        "ejemplo": "Un asesor en estado 'Available' pero 'IDLE' está esperando contacto; si pasa a 'INTERACTING' está en llamada."
    },
    {
        "termino": "Llamadas Prolongadas (Alerta > Umbral)",
        "categoria": "Monitoreo en Vivo",
        "badge": "Alerta de Piso",
        "color": "#ea580c",
        "resumen": "Llamadas activas en curso cuya duración supera el umbral configurado (ej: 15 min).",
        "definicion": (
            "Detección en tiempo real de interacciones telefónicas anormalmente largas. "
            "Se calcula en base a la hora de inicio del estado <code>INTERACTING</code> reportada por la API de Genesys. "
            "Permite a los supervisores intervenir en vivo para brindar soporte al asesor o agilizar la resolución."
        ),
        "formula": r"\text{Duración Llamada} \ge \text{Umbral Configurado (defecto 15 min)}",
        "ejemplo": "En la tarjeta naranja '📞 Llamadas Largas' se listan con cronómetro vivo (ej: 📞 18:42)."
    },
    {
        "termino": "Excesos de Breaks y Pausas de Ley",
        "categoria": "Monitoreo en Vivo",
        "badge": "Alerta de Piso",
        "color": "#dc2626",
        "resumen": "Asesores cuyo tiempo continuo en un estado auxiliar supera el límite de tolerancia.",
        "definicion": (
            "Límites de tolerancia en tiempo real por tipo de pausa:<br>"
            "• <b>Baño:</b> Límite 5 minutos.<br>"
            "• <b>Break / Descanso:</b> Límite 15 minutos continuos.<br>"
            "• <b>Diálogo Diario / 4DX:</b> Límite 15 minutos.<br>"
            "• <b>Feedback:</b> Límite 30 minutos.<br>"
            "• <b>Autogestión:</b> Límite 30 minutos.<br>"
            "• <b>Pre Pausa / Cursos / Refuerzo:</b> Límite 60 minutos.<br>"
            "• <b>Lunch:</b> Pausa no autorizada en jornada normal (genera alerta inmediata)."
        ),
        "formula": r"\text{Minutos en Estado} > \text{Tolerancia Máxima del Estado}",
        "ejemplo": "Un asesor con 22 min en Break genera la alerta: 🚨 Break excedido (+7.0 min)."
    },
    {
        "termino": "Selección Múltiple de Tarjetas de Piso",
        "categoria": "Monitoreo en Vivo",
        "badge": "Interacción",
        "color": "#0ea5e9",
        "resumen": "Capacidad de combinar varios filtros de estado simultáneamente haciendo clic en las tarjetas.",
        "definicion": (
            "Permite presionar <b>🔍 Filtrar</b> en más de una tarjeta (por ejemplo: <i>📞 Llamadas Largas</i> + <i>☕ Excesos Breaks</i>). "
            "El panel activa la unión de ambos estados y la tabla filtra inmediatamente a los asesores que cumplan "
            "cualquiera de las condiciones activas. Para desactivar, se presiona <b>✖ Quitar</b> o <b>🧹 Quitar Filtros</b>."
        ),
        "formula": r"\text{Filtro Tabla} = \text{Estado}_A \cup \text{Estado}_B \cup \dots",
        "ejemplo": "Activar 'En Interacción' + 'En Cola' muestra todo el frente de asesores productivos."
    },

    # ── GTR & Telefonía ───────────────────────────────────────────────────────
    {
        "termino": "AHT (Average Handle Time / TMO)",
        "categoria": "GTR & Telefonía",
        "badge": "Rendimiento",
        "color": "#0284c7",
        "resumen": "Tiempo Promedio de Operación: duración media de cada contacto atendido.",
        "definicion": (
            "Mide el tiempo total que consume un asesor en promedio para resolver una interacción. "
            "Incluye el tiempo hablado (Talk), el tiempo en espera o retención (Hold) y el tiempo posterior a la llamada (Wrap-up / ACW)."
        ),
        "formula": r"\text{AHT} = \frac{\text{Tiempo Hablado} + \text{Tiempo Hold} + \text{Tiempo ACW}}{\text{Interacciones Atendidas}}",
        "ejemplo": "Si se hablaron 60,000 seg, con 5,000 seg de hold y 5,000 seg de ACW en 100 llamadas: AHT = 70,000 / 100 = 700 segundos."
    },
    {
        "termino": "Meta Plana de AHT",
        "categoria": "GTR & Telefonía",
        "badge": "Estándar WFM",
        "color": "#0284c7",
        "resumen": "Valor objetivo contractual fijado en el dimensionamiento para evaluar cada servicio.",
        "definicion": (
            "Es el AHT fijo asumido en el modelo de Erlang C del dimensionamiento mensual. "
            "Si el AHT real de la operación supera la meta plana, se genera una 'inflación de tráfico' que destruye capacidad "
            "aunque el número de personas conectadas sea el correcto."
        ),
        "formula": r"\text{Desvío AHT} = \text{AHT Real} - \text{Meta AHT Plana}",
        "ejemplo": "LUA AMC tiene meta plana de 860s. Si opera a 980s (+120s), se requerirán más asesores para el mismo volumen."
    },
    {
        "termino": "Nivel de Servicio (NS / SLA)",
        "categoria": "GTR & Telefonía",
        "badge": "Calidad Contractual",
        "color": "#10b981",
        "resumen": "Porcentaje de llamadas atendidas antes de que venza el umbral pactado (ej: 80% en 20 segundos).",
        "definicion": (
            "Indicador reina de la gestión en tiempo real (GTR). Mide la rapidez de respuesta al cliente. "
            "Calcula la proporción de llamadas que entraron a cola y fueron contestadas por un asesor dentro del tiempo límite, "
            "descontando o penalizando los abandonos según la fórmula oficial acordada."
        ),
        "formula": r"\text{NS} = \left(\frac{\text{Llamadas Atendidas} \le T_{\text{umbral}}}{\text{Llamadas Ofrecidas} - \text{Abandonos Cortos}}\right) \times 100\%",
        "ejemplo": "Si se ofrecieron 1,000 llamadas y 850 se atendieron antes de 20 segundos: NS = 85.0% (🟢 Cumplido)."
    },
    {
        "termino": "ASA (Average Speed of Answer)",
        "categoria": "GTR & Telefonía",
        "badge": "Espera",
        "color": "#0284c7",
        "resumen": "Velocidad promedio de respuesta: segundos que un usuario espera en cola antes de ser atendido.",
        "definicion": (
            "Tiempo que transcurre desde que la llamada entra a la cola de Genesys hasta que el asesor contesta. "
            "Un ASA alto es síntoma directo de falta de asesores disponibles en cola (IDLE) o picos no previstos de llamadas."
        ),
        "formula": r"\text{ASA} = \frac{\text{Tiempo Total de Espera en Cola}}{\text{Llamadas Atendidas}}",
        "ejemplo": "Un ASA de 14 segundos indica una respuesta rápida y fluida; un ASA de 180 segundos refleja encolamiento severo."
    },
    {
        "termino": "% de Abandono",
        "categoria": "GTR & Telefonía",
        "badge": "Pérdida Tráfico",
        "color": "#ef4444",
        "resumen": "Proporción de clientes que cuelgan antes de ser atendidos por un asesor.",
        "definicion": (
            "Refleja la insatisfacción y la demanda insatisfecha. Ocurre cuando los tiempos de espera exceden la paciencia del usuario. "
            "Generalmente la meta de abandono de la cuenta es inferior al 5.0%."
        ),
        "formula": r"\% \text{Abandono} = \left(\frac{\text{Llamadas Abandonadas}}{\text{Llamadas Ofrecidas}}\right) \times 100\%",
        "ejemplo": "De 500 llamadas ofrecidas, 15 colgaron en cola: Abandono = 15 / 500 = 3.0% (🟢 Controlado)."
    },

    # ── Ausentismo & Adherencia ───────────────────────────────────────────────
    {
        "termino": "Adherencia de Turno",
        "categoria": "Ausentismo & Adherencia",
        "badge": "Turnos",
        "color": "#a855f7",
        "resumen": "Grado de coincidencia entre el horario programado en malla y la conexión real en Genesys.",
        "definicion": (
            "Compara el turno planificado (hora inicio, hora fin y pausas programadas) contra la presencia efectiva "
            "registrada segundo a segundo en Genesys Cloud. Detecta llegadas tarde, desconexiones tempranas y pausas fuera de hora."
        ),
        "formula": r"\% \text{Adherencia} = \left(\frac{\text{Tiempo Real en Actividad Programada}}{\text{Tiempo Programado Total}}\right) \times 100\%",
        "ejemplo": "Un asesor programado de 06:00 a 14:00 que se conecta a las 06:25 pierde adherencia por inicio tardío."
    },
    {
        "termino": "Ausentismo Injustificado de Piso",
        "categoria": "Ausentismo & Adherencia",
        "badge": "Auditoría",
        "color": "#dc2626",
        "resumen": "Asesores programados para laborar en el día que no registran ningún evento de conexión en Genesys.",
        "definicion": (
            "Cruce automático entre la malla oficial de turnos sociodemográfica y los segmentos de presencia en Genesys. "
            "Filtra inteligentemente a los colaboradores que tienen cargos operativos sin uso de Genesys (ej. Cargo) "
            "para no generar falsos positivos de ausentismo."
        ),
        "formula": r"\text{Ausentes} = \text{Programados en Turno} - \text{Conectados en Genesys} - \text{Novedades Justificadas}",
        "ejemplo": "Si hay 100 asesores programados y 95 registraron login: 5 asesores ausentes (5% de ausentismo)."
    },
    {
        "termino": "Casos Backoffice (Estado Excepcional)",
        "categoria": "Ausentismo & Adherencia",
        "badge": "Regla de Negocio",
        "color": "#6366f1",
        "resumen": "Estado de gestión documental que solo es considerado productivo en servicios autorizados de Back Office.",
        "definicion": (
            "En servicios de Inbound Voz, ponerse en 'Casos Backoffice' es una desviación no autorizada (se cuenta como pausa / no productivo). "
            "En cambio, en servicios oficiales de Back Office (ej: <code>BO AMC</code>, <code>BACKOFFICE</code>), este estado es plenamente "
            "productivo y suma a la capacidad neta disponible."
        ),
        "formula": r"\text{Productivo si } \text{Servicio} \in \{\text{Back Office, Células BO}\}",
        "ejemplo": "Un asesor de Voz en 'Casos Backoffice' genera alerta roja; un asesor de BO en el mismo estado está cumpliendo su labor."
    }
]

CATEGORIAS = [
    "Todas las Categorías",
    "Capacidad & WFM",
    "Monitoreo en Vivo",
    "GTR & Telefonía",
    "Ausentismo & Adherencia",
]


def render_tab_glosario():
    """Renderiza el módulo interactivo de Glosario y Guía de Usuario."""
    st.markdown("### 📚 Glosario Operativo & Guía de Usuario")
    st.caption(
        "Manual de referencia y consulta gerencial para toda la operación: Supervisores, Coordinadores, WFM y Gerencia. "
        "Encuentra aquí el significado, las fórmulas y la interpretación de cada indicador, estado y módulo del tablero."
    )

    # ── 1. Guía Rápida: Estructura del Dashboard ──────────────────────────────
    with st.expander("🗺️ ¿Cómo usar y navegar este Dashboard? (Guía por Vistas)", expanded=True):
        col_g1, col_g2 = st.columns(2)
        with col_g1:
            st.markdown(
                """
                <div style="background:#f8fafc; border:1px solid #e2e8f0; border-left:4px solid #2563eb; border-radius:8px; padding:12px 14px; margin-bottom:12px;">
                    <b style="color:#1e40af; font-size:14px;">1. 🔴 Control de Estados (en Vivo)</b><br>
                    <span style="font-size:12.5px; color:#334155; line-height:1.6;">
                        • <b>Objetivo:</b> Control en tiempo real del piso operativo (se refresca cada 30 segundos).<br>
                        • <b>Cómo usarlo:</b> Utiliza los filtros superiores de Coordinador, Servicio o Supervisor. Haz clic en las tarjetas KPI superiores (puedes activar varias al mismo tiempo) para aislar asesores en <i>Llamadas Largas</i> o <i>Excesos de Breaks</i>.<br>
                        • <b>Cajas de alerta:</b> Monitorea las alertas vivas de piso con cronómetro en tiempo real.
                    </span>
                </div>
                <div style="background:#f8fafc; border:1px solid #e2e8f0; border-left:4px solid #059669; border-radius:8px; padding:12px 14px; margin-bottom:12px;">
                    <b style="color:#065f46; font-size:14px;">2. 📊 Niveles de Servicio & GTR</b><br>
                    <span style="font-size:12.5px; color:#334155; line-height:1.6;">
                        • <b>Objetivo:</b> Desempeño ACD de llamadas entrantes por servicio y cola.<br>
                        • <b>Cómo usarlo:</b> Revisa las llamadas ofrecidas, atendidas, abandonadas, % NS (meta 80/20) y AHT. Usa los filtros de Coordinador y Servicio para desglosar tu operación.
                    </span>
                </div>
                """,
                unsafe_allow_html=True
            )
        with col_g2:
            st.markdown(
                """
                <div style="background:#f8fafc; border:1px solid #e2e8f0; border-left:4px solid #8b5cf6; border-radius:8px; padding:12px 14px; margin-bottom:12px;">
                    <b style="color:#5b21b6; font-size:14px;">3. 🧭 Capacidad y Diagnóstico Operativo</b><br>
                    <span style="font-size:12.5px; color:#334155; line-height:1.6;">
                        • <b>Objetivo:</b> Contraste gerencial del Requerido mensual (Forecast) vs presencia real ejecutada en Genesys.<br>
                        • <b>Cómo usarlo:</b> Elige <i>Día Específico</i> o <i>Rango de Fechas</i> (última semana, quincena, MTD). Consulta el <b>Árbol Waterfall</b> para saber cuánto sumó la dotación y cuánto restaron las pausas.<br>
                        • <b>Detalle:</b> Examina la curva intradía (48 intervalos de 30 min) o la evolución día a día.
                    </span>
                </div>
                <div style="background:#f8fafc; border:1px solid #e2e8f0; border-left:4px solid #dc2626; border-radius:8px; padding:12px 14px; margin-bottom:12px;">
                    <b style="color:#991b1b; font-size:14px;">4. 🚨 Control de Ausentismo & Adherencia</b><br>
                    <span style="font-size:12.5px; color:#334155; line-height:1.6;">
                        • <b>Objetivo:</b> Identificar ausencias cruzando la malla de turnos con los logins reales de Genesys.<br>
                        • <b>Cómo usarlo:</b> Detecta asesores programados sin conexión, filtra por supervisor o coordinador y exporta reportes para seguimiento de novedades.
                    </span>
                </div>
                """,
                unsafe_allow_html=True
            )

    st.markdown("---")

    # ── 2. Buscador y Filtros del Glosario ─────────────────────────────────────
    st.markdown("#### 🔍 Explorador de Métricas, Conceptos y Fórmulas")

    col_busq, col_cat = st.columns([2.5, 1.5])
    with col_busq:
        busqueda = st.text_input(
            "Buscar término, fórmula o palabra clave:",
            placeholder="Ej: AHT, FTE, Fuga, Auxiliares, NS, Break, Backoffice...",
            key="glosario_busqueda"
        ).strip().lower()

    with col_cat:
        cat_sel = st.selectbox(
            "Filtrar por Categoría:",
            options=CATEGORIAS,
            index=0,
            key="glosario_cat_sel"
        )

    # Filtrar catálogo
    resultados = []
    for item in TERMINOS_GLOSARIO:
        # Filtro de categoría
        if cat_sel != "Todas las Categorías" and item["categoria"] != cat_sel:
            continue
        # Filtro de búsqueda de texto
        if busqueda:
            texto_total = (
                item["termino"].lower()
                + " " + item["resumen"].lower()
                + " " + item["definicion"].lower()
                + " " + item["categoria"].lower()
                + " " + item["badge"].lower()
            )
            if busqueda not in texto_total:
                continue
        resultados.append(item)

    st.caption(f"Mostrando **{len(resultados)}** términos de {len(TERMINOS_GLOSARIO)} disponibles en el catálogo.")

    if not resultados:
        st.info("No se encontraron términos que coincidan con tu búsqueda. Intenta con otra palabra clave o selecciona 'Todas las Categorías'.")
        return

    # ── 3. Renderizar Tarjetas de Términos ─────────────────────────────────────
    for item in resultados:
        with st.container():
            st.markdown(
                f"""
                <div style="background:#ffffff; border:1px solid #e2e8f0; border-left:5px solid {item['color']}; border-radius:10px; padding:14px 18px; margin-bottom:14px; box-shadow:0 1px 3px rgba(0,0,0,0.04);">
                    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
                        <span style="font-size:16px; font-weight:700; color:#0f172a;">{item['termino']}</span>
                        <div>
                            <span style="background:{item['color']}18; color:{item['color']}; padding:2px 8px; border-radius:12px; font-size:11px; font-weight:600; margin-right:6px;">{item['badge']}</span>
                            <span style="background:#f1f5f9; color:#475569; padding:2px 8px; border-radius:12px; font-size:11px; font-weight:500;">{item['categoria']}</span>
                        </div>
                    </div>
                    <div style="font-size:13.5px; color:#334155; margin-bottom:8px; line-height:1.5;">
                        <b>Resumen:</b> {item['resumen']}
                    </div>
                    <div style="font-size:13px; color:#475569; margin-bottom:10px; line-height:1.6;">
                        {item['definicion']}
                    </div>
                    <div style="background:#f8fafc; border:1px dashed #cbd5e1; border-radius:6px; padding:8px 12px; margin-top:8px; font-size:12.5px; color:#1e293b;">
                        <b>💡 Ejemplo Operativo:</b> {item['ejemplo']}
                    </div>
                </div>
                """,
                unsafe_allow_html=True
            )
            # Renderizar fórmula matemática limpia con LaTeX si existe
            if item.get("formula"):
                with st.expander(f"📐 Ver Expresión Matemática / Fórmula ({item['termino'].split('(')[0].strip()})", expanded=False):
                    st.latex(item["formula"])

    # ── 4. Matriz Resumen de Estados de Presencia y Límites Oficiales ──────────
    st.markdown("---")
    st.markdown("#### ⏱️ Matriz Oficial de Tolerancias de Estados de Presencia")
    st.caption("Reglas oficiales de tiempos máximos autorizados en Genesys Cloud para el equipo de asesores.")

    col_t1, col_t2 = st.columns(2)
    with col_t1:
        st.markdown(
            """
            <table style="width:100%; font-size:13px; border-collapse:collapse; background:#fff; border:1px solid #e2e8f0; border-radius:8px;">
                <thead>
                    <tr style="background:#f1f5f9; border-bottom:2px solid #cbd5e1; text-align:left;">
                        <th style="padding:8px 12px;">Estado en Genesys</th>
                        <th style="padding:8px 12px;">Límite / Meta</th>
                        <th style="padding:8px 12px;">Tipo</th>
                        <th style="padding:8px 12px;">Impacto</th>
                    </tr>
                </thead>
                <tbody>
                    <tr style="border-bottom:1px solid #f1f5f9;">
                        <td style="padding:7px 12px; font-weight:600;">Break / Descanso</td>
                        <td style="padding:7px 12px; color:#059669; font-weight:600;">15 min / turno</td>
                        <td style="padding:7px 12px;">Ley</td>
                        <td style="padding:7px 12px; color:#059669;">Autorizado</td>
                    </tr>
                    <tr style="border-bottom:1px solid #f1f5f9;">
                        <td style="padding:7px 12px; font-weight:600;">Baño</td>
                        <td style="padding:7px 12px; color:#059669; font-weight:600;">5 min / evento</td>
                        <td style="padding:7px 12px;">Necesidad</td>
                        <td style="padding:7px 12px; color:#059669;">Autorizado</td>
                    </tr>
                    <tr style="border-bottom:1px solid #f1f5f9;">
                        <td style="padding:7px 12px; font-weight:600;">Pre Pausa</td>
                        <td style="padding:7px 12px; color:#059669; font-weight:600;">60 min / turno</td>
                        <td style="padding:7px 12px;">Turno</td>
                        <td style="padding:7px 12px; color:#059669;">Autorizado</td>
                    </tr>
                    <tr style="border-bottom:1px solid #f1f5f9;">
                        <td style="padding:7px 12px; font-weight:600;">Diálogo Diario / 4DX</td>
                        <td style="padding:7px 12px; color:#059669; font-weight:600;">15 min / día</td>
                        <td style="padding:7px 12px;">Reunión</td>
                        <td style="padding:7px 12px; color:#059669;">Autorizado</td>
                    </tr>
                    <tr style="border-bottom:1px solid #f1f5f9;">
                        <td style="padding:7px 12px; font-weight:600;">Lunch</td>
                        <td style="padding:7px 12px; color:#dc2626; font-weight:600;">0 min (No autor.)</td>
                        <td style="padding:7px 12px;">Excepción</td>
                        <td style="padding:7px 12px; color:#dc2626; font-weight:600;">🚨 Alerta Roja</td>
                    </tr>
                </tbody>
            </table>
            """,
            unsafe_allow_html=True
        )

    with col_t2:
        st.markdown(
            """
            <table style="width:100%; font-size:13px; border-collapse:collapse; background:#fff; border:1px solid #e2e8f0; border-radius:8px;">
                <thead>
                    <tr style="background:#f1f5f9; border-bottom:2px solid #cbd5e1; text-align:left;">
                        <th style="padding:8px 12px;">Estado en Genesys</th>
                        <th style="padding:8px 12px;">Límite / Meta</th>
                        <th style="padding:8px 12px;">Frecuencia</th>
                        <th style="padding:8px 12px;">Impacto</th>
                    </tr>
                </thead>
                <tbody>
                    <tr style="border-bottom:1px solid #f1f5f9;">
                        <td style="padding:7px 12px; font-weight:600;">Feedback / Coaching</td>
                        <td style="padding:7px 12px; color:#059669; font-weight:600;">30 min</td>
                        <td style="padding:7px 12px;">Semanal</td>
                        <td style="padding:7px 12px; color:#059669;">Autorizado</td>
                    </tr>
                    <tr style="border-bottom:1px solid #f1f5f9;">
                        <td style="padding:7px 12px; font-weight:600;">Autogestión</td>
                        <td style="padding:7px 12px; color:#059669; font-weight:600;">30 min</td>
                        <td style="padding:7px 12px;">Semanal</td>
                        <td style="padding:7px 12px; color:#059669;">Autorizado</td>
                    </tr>
                    <tr style="border-bottom:1px solid #f1f5f9;">
                        <td style="padding:7px 12px; font-weight:600;">CDR (Comité)</td>
                        <td style="padding:7px 12px; color:#059669; font-weight:600;">30 min</td>
                        <td style="padding:7px 12px;">Semanal</td>
                        <td style="padding:7px 12px; color:#059669;">Autorizado</td>
                    </tr>
                    <tr style="border-bottom:1px solid #f1f5f9;">
                        <td style="padding:7px 12px; font-weight:600;">Refuerzo / Cursos</td>
                        <td style="padding:7px 12px; color:#059669; font-weight:600;">60 min</td>
                        <td style="padding:7px 12px;">Semanal</td>
                        <td style="padding:7px 12px; color:#059669;">Autorizado</td>
                    </tr>
                    <tr style="border-bottom:1px solid #f1f5f9;">
                        <td style="padding:7px 12px; font-weight:600;">Casos Backoffice</td>
                        <td style="padding:7px 12px; color:#4338ca; font-weight:600;">Según Servicio</td>
                        <td style="padding:7px 12px;">Turno</td>
                        <td style="padding:7px 12px; color:#4338ca;">Solo BO Autorizado</td>
                    </tr>
                </tbody>
            </table>
            """,
            unsafe_allow_html=True
        )

    # ── 5. Preguntas Frecuentes (FAQ Operativo) ───────────────────────────────
    st.markdown("---")
    st.markdown("#### ❓ Preguntas Frecuentes de la Operación")

    with st.expander("¿Por qué un servicio no cumple su capacidad si tiene más personas conectadas que las requeridas?"):
        st.markdown(
            """
            Esto ocurre principalmente por dos razones operativas:
            1. **Fuga en Auxiliares (% Pausas > 14%):** Aunque haya muchos asesores conectados, si consumen más tiempo del autorizado en pausas, descansos prolongados o reuniones, los minutos disponibles efectivos caen por debajo de la base requerida.
            2. **Descalce o Dilución Intradía:** El servicio puede tener sobre-dotación en horas valle (ej: tarde/noche) y déficit crítico en las horas pico de llamadas. El tablero permite identificar esto con la curva de 48 intervalos en la vista de *Capacidad y Diagnóstico*.
            """
        )

    with st.expander("¿Cómo se calcula el AHT y por qué puede diferir del AHT de una cola individual?"):
        st.markdown(
            """
            El AHT mostrado a nivel de asesor o servicio en el monitoreo en vivo consolida **todas las interacciones atendidas** por ese grupo de asesores en lo que va del día (Tiempo Hablado + Tiempo Hold + Tiempo ACW dividido entre el total de contactos terminados).
            Si un asesor atiende múltiples colas (skills combinados), su AHT personal refleja la mezcla de todas las colas que gestionó.
            """
        )

    with st.expander("¿Qué significa 'IDLE' y por qué un asesor disponible puede estar en IDLE?"):
        st.markdown(
            """
            `IDLE` significa que el asesor está listo en cola (Available / On Queue) pero en ese preciso segundo no tiene ninguna llamada asignada porque no hay tráfico en espera en sus colas.
            Tener asesores en `IDLE` es normal y deseable para absorber llamadas de forma inmediata sin que el cliente espere en cola (garantizando ASA bajo y Nivel de Servicio alto).
            """
        )

    with st.expander("¿Cómo descargo los datos o reportes a Excel?"):
        st.markdown(
            """
            Todas las tablas del sistema (Matriz de Capacidad, Monitoreo de Piso, Colas GTR y Ausentismo) cuentan con el botón de descarga nativo de Streamlit en la esquina superior derecha de la tabla (ícono de descarga), o mediante los botones de exportación a Excel dedicados en cada vista.
            """
        )
