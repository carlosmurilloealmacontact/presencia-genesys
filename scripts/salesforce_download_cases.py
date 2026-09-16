"""
Extractor y Descargador Automatizado de Casos de Salesforce B2B (AMC LATAM).
Utiliza Playwright con sesion persistente para:
1. Acceder a Salesforce con sesion persistente.
2. Navegar directamente al reporte guardado (00OVK00000A4kPt2AJ).
3. Abrir el menu desplegable de acciones (flecha junto a Modificar) y seleccionar Exportar.
4. Seleccionar "Solo detalles" y formato CSV.
5. Descargar el reporte completo y procesarlo en cases_amc_cleaned.csv y demanda horaria.
"""

import os
import sys
import time
import json
from datetime import datetime
from playwright.sync_api import sync_playwright

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.normpath(os.path.join(BASE_DIR, ".."))
PROFILE_DIR = os.path.join(PROJECT_DIR, "data", "salesforce_browser_profile")
DATA_DIR = os.path.join(PROJECT_DIR, "data", "salesforce")
CREDS_PATH = os.path.join(BASE_DIR, "salesforce_credentials.json")
CONFIG_PATH = os.path.join(PROJECT_DIR, "data", "salesforce_config.json")
STATE_PATH = os.path.join(PROJECT_DIR, "data", "salesforce_state.json")

sys.path.insert(0, BASE_DIR)
import salesforce_engine as sfe


def descargar_reporte_casos_2026(headless: bool = False):
    print("=" * 70)
    print("INICIANDO DESCARGA AUTOMATIZADA DE CASOS AMC 2026 DE SALESFORCE")
    print("=" * 70)
    
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(PROFILE_DIR, exist_ok=True)
    
    # Cargar URL de reporte guardada
    report_url = "https://latamneworg.lightning.force.com/lightning/r/Report/00OVK00000A4kPt2AJ/view?queryScope=userFolders"
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                if cfg.get("report_url"):
                    report_url = cfg["report_url"]
        except Exception:
            pass

    print(f"[*] URL del reporte: {report_url}")

    with sync_playwright() as p:
        print("[*] Iniciando navegador Chrome...")
        context = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=headless,
            viewport={"width": 1400, "height": 900},
            accept_downloads=True,
            args=["--start-maximized"]
        )
        page = context.new_page() if not context.pages else context.pages[0]
        
        try:
            # 1. Navegar directamente al reporte
            print("[*] Abriendo reporte en Salesforce...")
            page.goto(report_url, wait_until="domcontentloaded", timeout=45000)
            time.sleep(5)
            
            # Verificar si pide login
            curr_url = page.url.lower()
            if "ec=302" in curr_url or "login" in curr_url or "identity" in curr_url:
                print("[!] Formulario de inicio de sesión detectado.")
                try:
                    if os.path.exists(CREDS_PATH):
                        with open(CREDS_PATH, "r", encoding="utf-8") as f:
                            creds = json.load(f)
                        if page.locator("#username").is_visible(timeout=4000):
                            val = page.locator("#username").input_value()
                            if not val:
                                page.fill("#username", creds["username"])
                            page.click("#Login")
                            time.sleep(3)
                        if page.locator("#password").is_visible(timeout=4000):
                            page.fill("#password", creds["password"])
                            page.click("#Login")
                            time.sleep(3)
                except Exception as e_c:
                    print(f"[*] Nota credenciales: {e_c}")

                print("[*] Esperando autenticación en pantalla...")
                start_login_wait = time.time()
                while time.time() - start_login_wait < 180:
                    curr = page.url.lower()
                    if ("/lightning/" in curr or "/one/one.app" in curr) and "ec=302" not in curr and "login" not in curr:
                        print("[+] ¡Sesión autenticada con éxito!")
                        break
                    time.sleep(3)
            
            # Guardar estado de sesión
            try:
                context.storage_state(path=STATE_PATH)
            except Exception:
                pass

            print(f"[+] Vista de reporte cargando en: {page.url}")

            # Esperar a que cargue la barra de herramientas del reporte (botón Modificar)
            print("[*] Esperando que cargue la barra de acciones del reporte...")
            try:
                page.wait_for_selector("button:has-text('Modificar'), button:has-text('Edit')", timeout=30000)
                print("[+] Barra de acciones del reporte cargada con éxito.")
            except Exception:
                print("[*] Continuando espera...")
                time.sleep(5)

            time.sleep(3)
            page.screenshot(path=os.path.join(PROJECT_DIR, "data", "report_toolbar_ready.png"))

            # 2. Localizar el botón dropdown de acciones [ v ] junto a "Modificar"
            print("\n[*] Localizando menú de acciones [ ▾ ] junto a 'Modificar'...")
            menu_clicked = False

            # Selector prioritario 1: El botón hermano inmediato a la derecha de "Modificar"
            try:
                modificar_btn = page.locator("button:has-text('Modificar'), button:has-text('Edit')").first
                if modificar_btn.is_visible(timeout=4000):
                    # Buscar el botón de flecha junto a él
                    arrow_btn = modificar_btn.locator("xpath=following::button[1]")
                    if arrow_btn.is_visible(timeout=2000):
                        print("[+] Botón desplegable [ ▾ ] localizado junto a 'Modificar'. Haciendo clic...")
                        arrow_btn.click()
                        menu_clicked = True
            except Exception as e_btn1:
                print(f"[*] Nota selector 1: {e_btn1}")

            # Selector 2: Botón de flecha en el grupo de botones
            if not menu_clicked:
                try:
                    candidates = [
                        ".slds-button-group button:last-child",
                        "button.slds-button_icon-border-filled",
                        "button[title*='acciones'], button[title*='actions']",
                        "button:has-text('Mostrar más acciones')"
                    ]
                    for cand in candidates:
                        b = page.locator(cand).first
                        if b.is_visible(timeout=2000):
                            print(f"[+] Haciendo clic en selector alternativo: {cand}")
                            b.click()
                            menu_clicked = True
                            break
                except Exception as e_btn2:
                    print(f"[*] Nota selector 2: {e_btn2}")

            time.sleep(2)

            # 3. Exportar
            download_path = os.path.join(DATA_DIR, f"casos_amc_2026_{datetime.now().strftime('%Y%m%d_%H%M')}.csv")
            export_success = False

            # Buscar la opción Exportar en el menú desplegado
            export_item = page.locator("lightning-menu-item:has-text('Export'), lightning-menu-item:has-text('Exportar'), a:has-text('Exportar'), span:has-text('Exportar')").first
            
            if export_item.is_visible(timeout=4000):
                print("[+] Opción 'Exportar' localizada en el menú. Abriendo modal...")
                export_item.click()
                time.sleep(3)

                # Modal de exportación: seleccionar "Solo detalles"
                try:
                    details_radio = page.locator("input[value='details'], label:has-text('Solo detalles'), label:has-text('Details Only')").first
                    if details_radio.is_visible(timeout=4000):
                        print("[*] Seleccionando 'Solo detalles' (Details Only)...")
                        details_radio.click()
                        time.sleep(1)
                except Exception as e_det:
                    print(f"[*] Nota radio: {e_det}")

                print("[*] Iniciando descarga del archivo CSV...")
                with page.expect_download(timeout=120000) as download_info:
                    modal_export_btn = page.locator("button.slds-button_brand:has-text('Export'), button.slds-button_brand:has-text('Exportar')").first
                    if modal_export_btn.is_visible(timeout=4000):
                        modal_export_btn.click()
                    else:
                        page.locator("button:has-text('Exportar'), button:has-text('Export')").last.click()

                download = download_info.value
                download.save_as(download_path)
                print(f"\n[✓] ¡ARCHIVO DESCARGADO EXITOSAMENTE!")
                print(f"Ruta: {download_path}")
                export_success = True
            else:
                print("\n" + "=" * 70)
                print("ASISTENCIA RÁPIDA: CLIC EN EXPORTAR")
                print("=" * 70)
                print("En la barra superior del reporte, haz clic en la flechita [ ▾ ] junto a 'Modificar'.")
                print("Luego selecciona 'Exportar' ➔ 'Solo detalles' ➔ 'Exportar'.")
                print("El script detectará automáticamente la descarga (esperando hasta 120s)...")
                print("=" * 70)
                try:
                    with page.expect_download(timeout=120000) as download_info:
                        pass
                    download = download_info.value
                    download.save_as(download_path)
                    print(f"\n[✓] ¡Archivo capturado exitosamente!: {download_path}")
                    export_success = True
                except Exception as e_wait:
                    print(f"[!] Captura de control tomada en data/report_export_screen.png: {e_wait}")
                    page.screenshot(path=os.path.join(PROJECT_DIR, "data", "report_export_screen.png"))

            print("[*] Cerrando navegador...")
            time.sleep(2)
            context.close()

            # 4. Procesar y consolidar la base de datos
            if export_success and os.path.exists(download_path):
                print("\n" + "=" * 70)
                print("CONSOLIDANDO Y LIMPIANDO CASOS AMC 2026")
                print("=" * 70)
                df_clean = sfe.load_and_clean_cases_data(file_path=download_path)
                print(f"[✓] Base cases_amc_cleaned actualizada con {len(df_clean)} casos.")

                try:
                    import procesar_demanda_salesforce as pds
                    pds.procesar_casos_y_demanda_salesforce(download_path)
                    print("[✓] Matriz de demanda horaria 2026 actualizada con éxito.")
                except Exception as e_p:
                    print(f"[*] Nota demanda: {e_p}")

                print("\n" + "=" * 70)
                print("¡EXTRACCIÓN Y ACTUALIZACIÓN 2026 COMPLETADA CON ÉXITO!")
                print("=" * 70)
                return True
            else:
                print("[!] No se completó la descarga del archivo.")
                return False

        except Exception as e:
            print(f"[!] Error: {e}")
            try:
                page.screenshot(path=os.path.join(PROJECT_DIR, "data", "report_error_screen.png"))
                context.close()
            except Exception:
                pass
            return False


if __name__ == "__main__":
    descargar_reporte_casos_2026(headless=False)
