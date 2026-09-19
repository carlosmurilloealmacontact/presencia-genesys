"""
telegram_notifier.py — Sistema centralizado de notificaciones por Telegram para el Radar Operacional 4DX.
Envía alertas, reportes diarios y diagnósticos de ejecución directamente al bot de Telegram del usuario.
"""

import os
import sys
import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv, find_dotenv

# Cargar variables desde el archivo .env del proyecto
env_paths = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".env"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"),
    os.path.join("C:\\Proyecto 3.0\\Paneles y Dashboard 4dx", ".env")
]

for p in env_paths:
    if os.path.exists(p):
        load_dotenv(p)
        break

TOKEN = os.getenv("TELEGRAM_TOKEN", "8631936997:AAHq1vcYllB3x6uRsLGUrIc7Nu8YtWv8nmY")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "2005116251")


def _get_cot_timestamp() -> str:
    cot = datetime.now(timezone.utc) - timedelta(hours=5)
    return cot.strftime("%Y-%m-%d %H:%M:%S COT")


def enviar_mensaje(texto_html: str) -> bool:
    """Envía un mensaje formateado en HTML al chat de Telegram configurado."""
    if not TOKEN or not CHAT_ID:
        print("[!] No se encontró TELEGRAM_TOKEN o TELEGRAM_CHAT_ID.")
        return False
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        payload = {
            "chat_id": CHAT_ID,
            "text": texto_html,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 200 and resp.json().get("ok"):
            return True
        else:
            print(f"[!] Error Telegram ({resp.status_code}): {resp.text}")
            return False
    except Exception as e:
        print(f"[!] Excepción al notificar por Telegram: {e}")
        return False


def notificar_resumen_diario(fecha_auditada: str, checklist: dict, tiempo_seg: float = 0.0, git_ok: bool = True) -> bool:
    """
    Envía el resumen oficial del checklist diario de completitud de todos los módulos.
    """
    todos_ok = all(item.get("ok", False) for item in checklist.values())
    icono_global = "✅" if todos_ok else "⚠️"
    estado_global = "100% OPERATIVO Y AL DÍA" if todos_ok else "CON NOVEDADES / PENDIENTES"

    lineas = [
        f"{icono_global} <b>Radar Operacional — Sincronización Diaria</b>",
        f"📅 <b>Fecha Auditada:</b> <code>{fecha_auditada}</code>",
        f"⏰ <b>Ejecutado:</b> <code>{_get_cot_timestamp()}</code>",
        f"⏱️ <b>Duración:</b> {tiempo_seg:.1f}s",
        ""
    ]

    lineas.append("<b>📊 Estado por Módulo Operativo:</b>")
    for modulo, info in checklist.items():
        ok = info.get("ok", False)
        det = info.get("detalle", "")
        simb = "✅" if ok else "❌"
        lineas.append(f"{simb} <b>{modulo}:</b> {det}")

    lineas.append("")
    lineas.append(f"🏁 <b>Estado Global:</b> {estado_global}")
    cloud_txt = "✅ Desplegado y sincronizado en origin/main" if git_ok else "⚠️ Pendiente de push"
    lineas.append(f"☁️ <b>Streamlit Cloud:</b> {cloud_txt}")

    return enviar_mensaje("\n".join(lineas))


def notificar_alerta(titulo: str, detalle_error: str, contexto: str = "") -> bool:
    """Envía una alerta urgente a Telegram ante un fallo en un worker o sincronización."""
    lineas = [
        f"🚨 <b>ALERTA OPERACIONAL — {titulo}</b>",
        f"⏰ <b>Hora:</b> <code>{_get_cot_timestamp()}</code>",
        ""
    ]
    if contexto:
        lineas.append(f"<b>Contexto:</b> {contexto}")
    lineas.append("<b>Detalle del Error:</b>")
    lineas.append(f"<code>{str(detalle_error)[:1000]}</code>")
    lineas.append("")
    lineas.append("<i>El sistema intentará auto-recuperarse en el siguiente ciclo.</i>")

    return enviar_mensaje("\n".join(lineas))


def notificar_inicio_worker(nombre_worker: str, frecuencia: str) -> bool:
    """Notifica el arranque exitoso de un demonio supervisor."""
    msg = (
        f"🚀 <b>Worker Iniciado:</b> {nombre_worker}\n"
        f"⏰ <b>Hora:</b> <code>{_get_cot_timestamp()}</code>\n"
        f"🔄 <b>Frecuencia:</b> {frecuencia}\n"
        f"🟢 <b>Modo:</b> 24/7 Resiliente en segundo plano."
    )
    return enviar_mensaje(msg)


if __name__ == "__main__":
    test_chk = {
        "Genesys Presencia": {"ok": True, "detalle": "10,386 tramos"},
        "Turnos y Ausentismo": {"ok": True, "detalle": "1,523 turnos detallados"},
        "Zendesk Productividad": {"ok": True, "detalle": "762 tickets resueltos"},
        "Salesforce Casos Backoffice": {"ok": True, "detalle": "436,978 casos al día"},
        "Cierre Agencias B2B": {"ok": True, "detalle": "8 servicios consolidados (79.3% NS)"}
    }
    res = notificar_resumen_diario("2026-09-18", test_chk, tiempo_seg=42.5, git_ok=True)
    print("Resultado test notificacion:", res)
