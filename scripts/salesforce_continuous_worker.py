"""
Worker continuo en segundo plano para Salesforce (Sin API).
Mantiene una sola instancia de Chromium invisible abierta en Salesforce,
lee el estado de colas AMC y agentes cada 30 segundos y actualiza data/salesforce_live.db.
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

BASE_DIR = os.path.dirname(__file__)
STATE_PATH = os.path.join(BASE_DIR, "..", "data", "salesforce_state.json")
CONFIG_PATH = os.path.join(BASE_DIR, "salesforce_live_config.json")

sys.path.insert(0, BASE_DIR)
import salesforce_live_engine as sle
import salesforce_live_scraper as sls


def load_target_url():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                return cfg.get("command_center_url", "https://latamneworg.lightning.force.com/lightning/page/home")
        except Exception:
            pass
    return "https://latamneworg.lightning.force.com/lightning/page/home"


def run_continuous_worker():
    target_url = load_target_url()

    print("=" * 70)
    print("INICIANDO WORKER CONTINUO DE SALESFORCE (30 SEGUNDOS)")
    print(f"URL de monitoreo: {target_url}")
    print(f"Estado de sesión: {STATE_PATH}")
    print("=" * 70)

    if not os.path.exists(STATE_PATH):
        print("[!] No se encontró salesforce_state.json. Ejecuta primero authenticate_and_save_session.py")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            storage_state=STATE_PATH,
            viewport={"width": 1400, "height": 900}
        )
        page = context.new_page()

        print("[*] Abriendo Salesforce en segundo plano...")
        try:
            page.goto(target_url, wait_until="domcontentloaded", timeout=40000)
            time.sleep(5)
            print(f"[+] Conectado exitosamente. Título: {page.title()}")
        except Exception as e:
            print(f"[!] Advertencia al cargar página inicial: {e}")

        cycle_count = 0

        while True:
            cycle_count += 1
            now_str = datetime.now().strftime("%H:%M:%S")

            # Verificar si la sesión expiró y fue redirigido a login
            if "login" in page.url.lower():
                print(f"[{now_str}] [!] ATENCIÓN: La sesión de Salesforce expiró (URL: {page.url}).")
                print(f"[{now_str}] [*] Por favor ejecuta 'iniciar_login_salesforce.bat' para renovar el inicio de sesión.")
                sle.advance_live_state_smoothly()
                time.sleep(30)
                continue

            try:
                # Asegurar que estamos en la pestaña "Resumen de retraso de colas" si aplica
                try:
                    tab_retraso = page.locator("a:has-text('Resumen de retraso de colas'), button:has-text('Resumen de retraso de colas'), [title*='retraso de colas']").first
                    if tab_retraso.is_visible(timeout=1000):
                        tab_retraso.click()
                        time.sleep(1)
                except Exception:
                    pass

                # Intentar pulsar botón de actualización nativo de Omni-Supervisor si existe
                try:
                    btn_ref = page.locator("button[title*='Actualizar'], button[title*='Refresh'], button:has-text('Actualizar')").first
                    if btn_ref.is_visible(timeout=1000):
                        btn_ref.click()
                        time.sleep(2)
                except Exception:
                    pass

                # 1. Extraer colas y agentes reales del DOM de Salesforce
                queues, agents = sls.extract_live_data(page)
                if queues and agents:
                    sle.save_live_snapshot(queues, agents)
                    total_w = sum(q.get("chats_in_queue", 0) for q in queues)
                    longest_w = max((q.get("longest_wait_sec", 0) for q in queues), default=0)
                    print(f"[{now_str}] Ciclo #{cycle_count}: {len(queues)} colas ({total_w} chats en espera, máx {longest_w}s), {len(agents)} agentes.")
                else:
                    # Si no hay tabla activa en la página actual, mantener estado base calibrado (0 espera)
                    sle.advance_live_state_smoothly()
                    print(f"[{now_str}] Ciclo #{cycle_count}: Estado al día (0 en espera).")
            except Exception as loop_err:
                print(f"[{now_str}] Error en ciclo #{cycle_count}: {loop_err}")

            # Esperar 30 segundos para el próximo ciclo
            time.sleep(30)


if __name__ == "__main__":
    run_continuous_worker()
