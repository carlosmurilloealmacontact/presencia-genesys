"""
Worker continuo en segundo plano para Salesforce Omni-Supervisor.
Utiliza el perfil de navegador persistente (data/salesforce_browser_profile),
autenticación automática (incluyendo 2FA vía Outlook MAPI si es requerido),
y extrae el estado real de colas y tiempos de espera cada 30 segundos en data/salesforce_live.db.
"""

import json
import os
import time
import re
import sys
from datetime import datetime
from playwright.sync_api import sync_playwright

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.normpath(os.path.join(BASE_DIR, ".."))
PROFILE_DIR = os.path.join(PROJECT_DIR, "data", "salesforce_browser_profile")
STATE_PATH = os.path.join(PROJECT_DIR, "data", "salesforce_state.json")
CONFIG_PATH = os.path.join(BASE_DIR, "salesforce_live_config.json")

sys.path.insert(0, BASE_DIR)
import salesforce_live_engine as sle
import salesforce_live_scraper as sls
import salesforce_auth_manager as sam


def load_target_url():
    default_url = "https://latamneworg.lightning.force.com/one/one.app#eyJjb21wb25lbnREZWYiOiJvbW5pOnN1cGVydmlzb3JQYW5lbCIsImF0dHJpYnV0ZXMiOnt9LCJzdGF0ZSI6e319"
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                return cfg.get("command_center_url", default_url)
        except Exception:
            pass
    return default_url
def run_continuous_worker():
    target_url = load_target_url()
    STATE_PATH = os.path.join(BASE_DIR, "..", "data", "salesforce_state.json")


    print("=" * 70)
    print("INICIANDO WORKER CONTINUO DE SALESFORCE OMNI-SUPERVISOR (30S)")
    print(f"URL de monitoreo: {target_url}")
    print(f"Estado de sesión: {STATE_PATH}")
    print("=" * 70)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context_kwargs = {
            "viewport": {"width": 1600, "height": 1000},
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }
        if os.path.exists(STATE_PATH):
            context_kwargs["storage_state"] = STATE_PATH

        context = browser.new_context(**context_kwargs)
        page = context.new_page()

        print("[*] Conectando con Salesforce...")
        try:
            page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
            time.sleep(6)
        except Exception as e:
            print(f"[*] Nota navegación inicial: {e}")

        # Asegurar sesión si no estaba previamente autenticada
        if "login" in page.url.lower() or "ec=302" in page.url.lower():
            sam.asegurar_sesion_salesforce(page, context, target_url)
            try:
                context.storage_state(path=STATE_PATH)
            except Exception:
                pass
            time.sleep(4)


        cycle_count = 0

        while True:
            cycle_count += 1
            now_str = datetime.now().strftime("%H:%M:%S")

            # 1. Verificar si la sesión expiró o redirigió a login/verificación
            curr_url = page.url.lower()
            page_title = page.title().lower()
            needs_auth = (
                "login" in curr_url
                or "ec=302" in curr_url
                or "iniciar sesión" in page_title
                or "verification" in curr_url
                or "identity" in curr_url
                or "verificar su identidad" in page_title
            )

            if needs_auth:
                print(f"[{now_str}] [!] Sesión desautenticada detectada. Reautenticando en automático...")
                sam.asegurar_sesion_salesforce(page, context, target_url)
                time.sleep(5)
                continue

            try:
                # 2. Asegurar que estamos en la pestaña 'Retraso de colas'
                try:
                    tab_retraso = page.locator("a:has-text('Retraso de colas'), button:has-text('Retraso de colas'), [role='tab']:has-text('Retraso de colas')").first
                    if tab_retraso.is_visible(timeout=2000):
                        if tab_retraso.get_attribute("aria-selected") != "true":
                            tab_retraso.click()
                            time.sleep(2)
                except Exception:
                    pass

                # 3. Intentar pulsar el botón de actualización nativo de Omni-Supervisor
                try:
                    btn_ref = page.locator("button[title*='Actualizar'], button[title*='Refresh'], button:has-text('Actualizar')").first
                    if btn_ref.is_visible(timeout=1000):
                        btn_ref.click()
                        time.sleep(2)
                except Exception:
                    pass

                # 4. Extraer colas y agentes reales del DOM de Salesforce
                queues, agents = sls.extract_live_data(page)
                if queues or agents:
                    sle.advance_live_state_smoothly(scraped_queues=queues, scraped_agents=agents)
                    total_w = sum(q.get("chats_in_queue", 0) for q in (queues or []))
                    longest_w = max((q.get("longest_wait_sec", 0) for q in (queues or [])), default=0)
                    active_c = sum(a.get("active_chats", 0) for a in (agents or []))
                    queues_with_wait = [f"{q['queue_name']}={q['chats_in_queue']} ({q['longest_wait_sec']}s)" for q in (queues or []) if q.get("chats_in_queue", 0) > 0]
                    detail_str = f" [En espera: {', '.join(queues_with_wait)}]" if queues_with_wait else " [0 en espera]"
                    print(f"[{now_str}] Ciclo #{cycle_count}: {len(queues or [])} colas ({total_w} en espera, máx {longest_w}s), {len(agents or [])} agentes ({active_c} chats activos).{detail_str}")
                else:
                    sle.advance_live_state_smoothly()
                    print(f"[{now_str}] Ciclo #{cycle_count}: Estado al día (0 en espera).")
            except Exception as loop_err:
                print(f"[{now_str}] Error en ciclo #{cycle_count}: {loop_err}")

            time.sleep(15)


if __name__ == "__main__":
    run_continuous_worker()
