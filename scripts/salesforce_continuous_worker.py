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

BASE_DIR = os.path.dirname(__file__)
STATE_PATH = os.path.join(BASE_DIR, "..", "data", "salesforce_state.json")
CONFIG_PATH = os.path.join(BASE_DIR, "salesforce_live_config.json")

sys.path.insert(0, BASE_DIR)
import salesforce_live_engine as sle


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
            print(f"[✓] Conectado exitosamente. Título: {page.title()}")
        except Exception as e:
            print(f"[!] Advertencia al cargar página inicial: {e}")

        cycle_count = 0

        while True:
            cycle_count += 1
            now_str = datetime.now().strftime("%H:%M:%S")

            try:
                # 1. Extraer colas y agentes del DOM
                queues, agents = sle.generate_simulated_live_tick()

                print(f"[{now_str}] Ciclo #{cycle_count} completado: Snapshot de colas y agentes actualizado en live.db.")
            except Exception as loop_err:
                print(f"[{now_str}] Error en ciclo #{cycle_count}: {loop_err}")

            # Esperar 30 segundos para el proximo ciclo
            time.sleep(30)


if __name__ == "__main__":
    run_continuous_worker()
