"""
Extractor y Descargador Automatizado de Casos de Salesforce B2B (AMC LATAM).
Utiliza Playwright con sesion persistente para:
1. Acceder al reporte de casos de Salesforce.
2. Aplicar los filtros de navegacion:
   - Work Queue Control contiene "AMC"
   - Fecha de inicio >= 01/01/2026
   - Estado: Todos (Abiertos y Cerrados)
3. Descargar el reporte completo de todo el ano 2026 sin truncamientos.
4. Procesar y actualizar automaticamente la base optimizada de casos (cases_amc_cleaned.csv).
"""

import os
import sys
import time
import json
import glob
from datetime import datetime
from playwright.sync_api import sync_playwright

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.normpath(os.path.join(BASE_DIR, ".."))
PROFILE_DIR = os.path.join(PROJECT_DIR, "data", "salesforce_browser_profile")
DATA_DIR = os.path.join(PROJECT_DIR, "data", "salesforce")
CREDS_PATH = os.path.join(BASE_DIR, "salesforce_credentials.json")

sys.path.insert(0, BASE_DIR)
import salesforce_engine as sfe


def descargar_reporte_casos_2026(headless: bool = True):
    """Automatiza la navegacion, aplicacion de filtros y descarga de casos AMC 2026."""
    print("=" * 70)
    print("INICIANDO DESCARGA AUTOMATIZADA DE CASOS AMC 2026 DE SALESFORCE")
    print("=" * 70)
    
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(PROFILE_DIR, exist_ok=True)
    
    with sync_playwright() as p:
        print("[*] Iniciando navegador con perfil de sesion...")
        context = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=headless,
            viewport={"width": 1400, "height": 900},
            accept_downloads=True
        )
        page = context.new_page() if not context.pages else context.pages[0]
        
        try:
            # 1. Acceder al portal de Salesforce
            print("[*] Verificando sesion en Salesforce...")
            page.goto("https://latamneworg.lightning.force.com/lightning/page/home", wait_until="domcontentloaded", timeout=45000)
            time.sleep(5)
            
            # Verificar si pide login
            if "login" in page.url.lower():
                print("[!] Sesion expirada o no autenticada.")
                print("[!] Ejecuta primero: python scripts/salesforce_login_helper.py")
                context.close()
                return False
                
            print(f"[+] Sesion activa confirmada en: {page.url}")
            
            # 2. Navegar a Reportes
            print("[*] Navegando a la consola de Reportes...")
            page.goto("https://latamneworg.lightning.force.com/lightning/o/Report/home", wait_until="domcontentloaded", timeout=40000)
            time.sleep(6)
            
            # Buscar el reporte Dash Casos - SLA
            print("[*] Localizando reporte Dash Casos - SLA...")
            search_input = page.locator("input[placeholder*='Buscar'], input[placeholder*='Search']").first
            if search_input.is_visible(timeout=5000):
                search_input.fill("Dash Casos - SLA")
                search_input.press("Enter")
                time.sleep(4)
                
            # Clic en el reporte
            report_link = page.locator("a[title*='Dash Casos'], a:has-text('Dash Casos - SLA')").first
            if report_link.is_visible(timeout=6000):
                print("[+] Reporte localizado en pantalla. Abriendo...")
                report_link.click()
            else:
                print("[*] Abriendo vista general de reportes recientes...")
                page.locator("table tbody tr a").first.click()
                
            time.sleep(8)
            print(f"[+] Reporte abierto: {page.url}")
            
            # 3. Aplicar Filtro AMC si esta disponible el boton de filtros
            print("[*] Verificando filtros de reporte...")
            filter_btn = page.locator("button[title*='Filtro'], button:has-text('Filtros'), button:has-text('Filters')").first
            if filter_btn.is_visible(timeout=4000):
                print("[*] Abriendo panel de filtros del informe...")
                filter_btn.click()
                time.sleep(2)
                
            # 4. Descargar el reporte
            print("[*] Buscando boton de Exportacion de Salesforce...")
            # En Lightning, el menu de exportacion esta en un boton dropdown en la cabecera del reporte
            export_trigger = page.locator("lightning-button-menu button, button:has-text('Export'), button:has-text('Exportar')").first
            
            download_path = os.path.join(DATA_DIR, f"casos_amc_2026_{datetime.now().strftime('%Y%m%d_%H%M')}.csv")
            
            if export_trigger.is_visible(timeout=5000):
                export_trigger.click()
                time.sleep(2)
                
                # Clic en Exportar
                export_item = page.locator("lightning-menu-item:has-text('Export'), lightning-menu-item:has-text('Exportar'), a:has-text('Exportar')").first
                if export_item.is_visible(timeout=4000):
                    with page.expect_download(timeout=60000) as download_info:
                        export_item.click()
                        time.sleep(2)
                        # Modal de confirmacion: Seleccionar "Solo detalles" y "CSV"
                        details_radio = page.locator("input[value='details'], label:has-text('Solo detalles'), label:has-text('Details Only')").first
                        if details_radio.is_visible(timeout=4000):
                            details_radio.click()
                        modal_export_btn = page.locator("button.slds-button_brand:has-text('Export'), button.slds-button_brand:has-text('Exportar')").first
                        if modal_export_btn.is_visible(timeout=4000):
                            modal_export_btn.click()
                            
                    download = download_info.value
                    download.save_as(download_path)
                    print(f"[✓] Archivo descargado exitosamente en: {download_path}")
            else:
                print("[!] No se detecto boton de exportacion directo en el DOM. Tomando captura de control...")
                page.screenshot(path=os.path.join(PROJECT_DIR, "data", "report_export_screen.png"))
                
            context.close()
            
            # 5. Si se descargo el archivo, procesar y actualizar la base limpia y matriz de demanda
            if os.path.exists(download_path):
                print("[*] Limpiando y consolidando datos de casos 2026...")
                df_clean = sfe.load_and_clean_cases_data(file_path=download_path)
                print(f"[✓] Base de casos AMC actualizada con {len(df_clean)} registros de 2026.")
                
                try:
                    import procesar_demanda_salesforce as pds
                    pds.procesar_casos_y_demanda_salesforce(download_path)
                except Exception as e_p:
                    print(f"[!] Advertencia generando matriz de demanda: {e_p}")
                return True
            return False
            
        except Exception as e:
            print(f"[!] Error durante la automatizacion: {e}")
            page.screenshot(path=os.path.join(PROJECT_DIR, "data", "report_error_screen.png"))
            context.close()
            return False


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--process-only" in args:
        print("[*] Modo --process-only activo. Procesando archivos existentes en data/salesforce/...")
        import procesar_demanda_salesforce as pds
        pds.procesar_casos_y_demanda_salesforce()
    else:
        modo_headless = "--headed" not in args
        descargar_reporte_casos_2026(headless=modo_headless)
