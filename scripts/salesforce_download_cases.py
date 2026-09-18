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


import salesforce_auth_manager as sam
import procesar_demanda_salesforce as pds


def descargar_reporte_casos_2026(headless: bool = True):
    print("=" * 70)
    print("INICIANDO DESCARGA AUTOMATIZADA DE CASOS AMC 2026 DE SALESFORCE")
    print(f"Modo: {'Headless (Segundo Plano)' if headless else 'Visible (Interactivo)'}")
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
        browser = p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context_kwargs = {
            "viewport": {"width": 1600, "height": 1000},
            "accept_downloads": True,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }
        if os.path.exists(STATE_PATH):
            context_kwargs["storage_state"] = STATE_PATH

        context = browser.new_context(**context_kwargs)
        page = context.new_page()
        
        try:
            # 1. Navegar directamente al reporte
            print("[*] 1. Accediendo al reporte en Salesforce...")
            page.goto(report_url, wait_until="domcontentloaded", timeout=45000)
            time.sleep(5)
            
            # Verificar si pide login / verificación de identidad
            curr_url = page.url.lower()
            page_title = page.title().lower()
            if "login" in curr_url or "ec=302" in curr_url or "identity" in curr_url or "iniciar sesión" in page_title:
                print("[!] Sesión inactiva o desafío detectado. Autenticando...")
                sam.asegurar_sesion_salesforce(page, context, report_url)
                time.sleep(4)

            # 2. Esperar a que cargue el visor de informe en su iframe
            print("[*] 2. Esperando que compile y renderice el informe...")
            report_frame = page.frame_locator("iframe[name*='builder'], iframe[src*='lightningReportApp']").first
            mod_btn = report_frame.locator("button:has-text('Modificar'), button:has-text('Edit')").first
            
            try:
                mod_btn.wait_for(state="visible", timeout=60000)
                print("[+] Barra de acciones del reporte cargada con éxito.")
            except Exception as e_w:
                print(f"[*] Continuando tras espera de renderizado: {e_w}")

            # 3. Localizar el botón desplegable [ ▾ ] junto a "Modificar"
            print("[*] 3. Desplegando menú de acciones del reporte...")
            arrow_btn = mod_btn.locator("xpath=following::button[1]")
            arrow_btn.click()
            time.sleep(1.2)

            # 4. Clic en 'Exportar'
            print("[*] 4. Abriendo modal de exportación...")
            export_item = report_frame.locator("a:has-text('Exportar'), button:has-text('Exportar'), [role='menuitem']:has-text('Exportar'), lightning-menu-item:has-text('Exportar')").first
            export_item.click()
            time.sleep(2)

            # 5. Modal de exportación: seleccionar "Solo detalles"
            print("[*] 5. Configurando opciones de exportación (Solo detalles)...")
            try:
                details_radio = page.locator("input[value='details'], label:has-text('Solo detalles'), .details-radio, [data-record='details']").first
                if details_radio.is_visible(timeout=3000):
                    details_radio.click()
                    time.sleep(0.5)
                    print("[+] 'Solo detalles' seleccionado.")
            except Exception as e_det:
                print(f"[*] Nota selector de formato: {e_det}")

            # 6. Disparar exportación y descargar archivo
            download_path = os.path.join(DATA_DIR, f"casos_amc_2026_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
            export_success = False

            modal_export_btn = page.locator("button[title='Exportar'], button.uiButton--brand:has-text('Exportar')").first
            if not modal_export_btn.is_visible(timeout=4000):
                modal_export_btn = page.locator("button.slds-button_brand:has-text('Exportar'), button:has-text('Exportar')").last

            print("[*] 6. Disparando descarga del archivo CSV...")

            # Intentar descarga capturando evento download en página o popup
            try:
                with page.expect_download(timeout=90000) as download_info:
                    modal_export_btn.click(no_wait_after=True)
                    print("[+] Solicitud de exportación enviada. Esperando descarga de Salesforce...")
                download = download_info.value
                download.save_as(download_path)
                print(f"\n[✓] ¡ARCHIVO DESCARGADO EXITOSAMENTE!")
                print(f"Ruta: {download_path} ({os.path.getsize(download_path)/1024:.1f} KB)")
                export_success = True
            except Exception as e_down:
                print(f"[*] Nota captura descarga directa: {e_down}")
                # Si abrió en popup (desafío MFA de exportación)
                for p_extra in context.pages:
                    if p_extra != page and ("verification" in p_extra.url.lower() or "identity" in p_extra.url.lower()):
                        print("[*] Desafío 2FA detectado en ventana de exportación. Resolviendo con Outlook MAPI...")
                        sam.completar_desafio_mfa_si_es_necesario(p_extra)
                        time.sleep(3)
                        break

            # Fallback interactivo si se ejecutó visible y la descarga requiere asistencia
            if not export_success and not headless:
                print("\n" + "=" * 70)
                print("ASISTENCIA RÁPIDA: CLIC EN EXPORTAR")
                print("=" * 70)
                print("Haz clic en 'Exportar' en la pantalla del navegador...")
                try:
                    with page.expect_download(timeout=60000) as download_info:
                        pass
                    download = download_info.value
                    download.save_as(download_path)
                    print(f"\n[✓] ¡Archivo capturado exitosamente!: {download_path}")
                    export_success = True
                except Exception:
                    pass

            print("[*] Cerrando sesión del navegador...")
            browser.close()

            # 7. Procesar y consolidar la base de datos
            if export_success and os.path.exists(download_path):
                print("\n" + "=" * 70)
                print("CONSOLIDANDO Y LIMPIANDO CASOS AMC 2026")
                print("=" * 70)
                df_clean = sfe.load_and_clean_cases_data(file_path=download_path)
                print(f"[✓] Base cases_amc_cleaned actualizada con {len(df_clean)} casos.")

                # Actualizar matriz de demanda y sincronizar a Neon PostgreSQL
                pds.procesar_casos_y_demanda_salesforce(download_path)
                print("[✓] Matriz de demanda horaria y diaria sincronizada en Neon PostgreSQL.")

                print("\n" + "=" * 70)
                print("¡EXTRACCIÓN Y ACTUALIZACIÓN 2026 COMPLETADA CON ÉXITO!")
                print("=" * 70)
                return True
            else:
                print("[!] No se completó la descarga del archivo en este ciclo.")
                return False

        except Exception as e:
            print(f"[!] Error durante el proceso de extracción: {e}")
            try:
                browser.close()
            except Exception:
                pass
            return False


def descargar_reporte_logins(headless: bool = True):
    """
    Descarga el reporte de Historial de Logins de Salesforce (00OVK00000APn9R2AT).
    Captura: primer logueo, plataforma (PC vs Celular), navegador y estado.
    """
    print("=" * 70)
    print("INICIANDO DESCARGA AUTOMATIZADA DE LOGINS SALESFORCE (PC vs CELULAR)")
    print(f"Modo: {'Headless (Segundo Plano)' if headless else 'Visible (Interactivo)'}")
    print("=" * 70)

    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(PROFILE_DIR, exist_ok=True)

    report_url = "https://latamneworg.lightning.force.com/lightning/r/Report/00OVK00000APn9R2AT/view"
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                if cfg.get("logins_report_url"):
                    report_url = cfg["logins_report_url"]
        except Exception:
            pass

    print(f"[*] URL del reporte: {report_url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context_kwargs = {
            "viewport": {"width": 1600, "height": 1000},
            "accept_downloads": True,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }
        if os.path.exists(STATE_PATH):
            context_kwargs["storage_state"] = STATE_PATH

        context = browser.new_context(**context_kwargs)
        page = context.new_page()

        try:
            print("[*] 1. Accediendo al reporte de Logins...")
            page.goto(report_url, wait_until="domcontentloaded", timeout=45000)
            time.sleep(5)

            curr_url = page.url.lower()
            page_title = page.title().lower()
            if "login" in curr_url or "ec=302" in curr_url or "identity" in curr_url or "iniciar sesión" in page_title:
                print("[!] Sesión inactiva o desafío detectado. Autenticando...")
                sam.asegurar_sesion_salesforce(page, context, report_url)
                time.sleep(4)

            print("[*] 2. Esperando que compile y renderice el informe...")
            report_frame = page.frame_locator("iframe[name*='builder'], iframe[src*='lightningReportApp']").first
            mod_btn = report_frame.locator("button:has-text('Modificar'), button:has-text('Edit')").first

            try:
                mod_btn.wait_for(state="visible", timeout=60000)
                print("[+] Barra de acciones del reporte cargada con éxito.")
            except Exception as e_w:
                print(f"[*] Continuando tras espera de renderizado: {e_w}")

            print("[*] 3. Desplegando menú de acciones del reporte...")
            arrow_btn = mod_btn.locator("xpath=following::button[1]")
            arrow_btn.click()
            time.sleep(1.2)

            print("[*] 4. Abriendo modal de exportación...")
            export_item = report_frame.locator("a:has-text('Exportar'), button:has-text('Exportar'), [role='menuitem']:has-text('Exportar'), lightning-menu-item:has-text('Exportar')").first
            export_item.click()
            time.sleep(2)

            # 5. Modal de exportación: seleccionar "Solo detalles"
            print("[*] 5. Configurando opciones de exportación (Solo detalles)...")
            modal = page.locator(".slds-modal, section[role='dialog']").first
            time.sleep(1)
            details_card = page.locator(".slds-modal label:has-text('Solo detalles'), .slds-modal div:has-text('Solo detalles')").last
            if details_card.is_visible(timeout=4000):
                details_card.click()
                time.sleep(1)
                print("[+] 'Solo detalles' seleccionado.")

            # Formato CSV
            try:
                format_select = modal.locator("select").first
                if format_select.is_visible(timeout=2000):
                    options = format_select.evaluate("el => Array.from(el.options).map(o => o.text)")
                    for opt in options:
                        if "csv" in opt.lower() or "coma" in opt.lower():
                            format_select.select_option(label=opt)
                            print(f"[+] Formato seleccionado: {opt}")
                            break
            except Exception:
                pass

            download_path = os.path.join(DATA_DIR, "logins_amc_downloaded.csv")
            export_success = False
            downloads = []
            context.on("download", lambda d: downloads.append(d))
            page.on("download", lambda d: downloads.append(d))

            modal_export_btn = modal.locator("button.uiButton--brand, button:has-text('Exportar')").last
            print("[*] 6. Disparando descarga del archivo CSV...")
            modal_export_btn.click(no_wait_after=True)
            print("[+] Solicitud de exportación enviada. Monitoreando descarga / verificación...")

            # Monitorear por 45 segundos por descargas o popups de verificación
            for _ in range(45):
                if downloads:
                    break
                for p_extra in context.pages:
                    if p_extra != page and ("verification" in p_extra.url.lower() or "identity" in p_extra.url.lower()):
                        print("[*] Desafío 2FA detectado en popup. Resolviendo con Outlook MAPI...")
                        sam.completar_desafio_mfa_si_es_necesario(p_extra)
                        time.sleep(3)
                        break
                time.sleep(1)

            if downloads:
                download = downloads[0]
                download.save_as(download_path)
                print(f"\n[✓] ¡REPORTE DE LOGINS DESCARGADO EXITOSAMENTE!")
                print(f"Ruta: {download_path} ({os.path.getsize(download_path)/1024:.1f} KB)")
                export_success = True
            else:
                print("[!] No se detectó evento de descarga en el tiempo esperado.")
                diag_screen = os.path.join(DATA_DIR, "export_modal_after_click.png")
                page.screenshot(path=diag_screen)
                print(f"Captura guardada en: {diag_screen}")
                try:
                    txt = modal.inner_text()
                    print(f"Texto del modal: {txt[:300]}")
                except Exception:
                    pass

            browser.close()
            return download_path if export_success else None

        except Exception as e:
            print(f"[!] Error durante la descarga de logins: {e}")
            try:
                browser.close()
            except Exception:
                pass
            return None


def descargar_reporte_omni(headless: bool = True):
    """
    Descarga el reporte de Omni-Channel User Presences Status de Salesforce (00OVK00000APrzR2AT).
    Captura: estado (Available, Busy, On_Break), fecha inicio, fin, duracion segundos.
    """
    print("=" * 70)
    print("INICIANDO DESCARGA AUTOMATIZADA DE PRESENCIA OMNI-CHANNEL SALESFORCE")
    print(f"Modo: {'Headless (Segundo Plano)' if headless else 'Visible (Interactivo)'}")
    print("=" * 70)

    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(PROFILE_DIR, exist_ok=True)

    report_url = "https://latamneworg.lightning.force.com/lightning/r/Report/00OVK00000APrzR2AT/view"
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                if cfg.get("omni_report_url"):
                    report_url = cfg["omni_report_url"]
        except Exception:
            pass

    print(f"[*] URL del reporte Omni: {report_url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context_kwargs = {
            "viewport": {"width": 1600, "height": 1000},
            "accept_downloads": True,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }
        if os.path.exists(STATE_PATH):
            context_kwargs["storage_state"] = STATE_PATH

        context = browser.new_context(**context_kwargs)
        page = context.new_page()

        try:
            print("[*] 1. Accediendo al reporte de Presencia Omni-Channel...")
            page.goto(report_url, wait_until="domcontentloaded", timeout=45000)
            time.sleep(5)

            curr_url = page.url.lower()
            page_title = page.title().lower()
            if "login" in curr_url or "ec=302" in curr_url or "identity" in curr_url or "iniciar sesión" in page_title:
                print("[!] Sesión inactiva o desafío detectado. Autenticando...")
                sam.asegurar_sesion_salesforce(page, context, report_url)
                time.sleep(4)

            print("[*] 2. Esperando que compile y renderice el informe...")
            report_frame = page.frame_locator("iframe[name*='builder'], iframe[src*='lightningReportApp']").first
            mod_btn = report_frame.locator("button:has-text('Modificar'), button:has-text('Edit')").first

            try:
                mod_btn.wait_for(state="visible", timeout=60000)
                print("[+] Barra de acciones del reporte cargada con éxito.")
            except Exception as e_w:
                print(f"[*] Continuando tras espera de renderizado: {e_w}")

            print("[*] 3. Desplegando menú de acciones del reporte...")
            arrow_btn = mod_btn.locator("xpath=following::button[1]")
            arrow_btn.click()
            time.sleep(1.2)

            print("[*] 4. Abriendo modal de exportación...")
            export_item = report_frame.locator("a:has-text('Exportar'), button:has-text('Exportar'), [role='menuitem']:has-text('Exportar'), lightning-menu-item:has-text('Exportar')").first
            export_item.click()
            time.sleep(2)

            print("[*] 5. Configurando opciones de exportación (Solo detalles)...")
            modal = page.locator(".slds-modal, section[role='dialog']").first
            time.sleep(1)
            details_card = page.locator(".slds-modal label:has-text('Solo detalles'), .slds-modal div:has-text('Solo detalles')").last
            if details_card.is_visible(timeout=4000):
                details_card.click()
                time.sleep(1)
                print("[+] 'Solo detalles' seleccionado.")

            try:
                format_select = modal.locator("select").first
                if format_select.is_visible(timeout=2000):
                    options = format_select.evaluate("el => Array.from(el.options).map(o => o.text)")
                    for opt in options:
                        if "csv" in opt.lower() or "coma" in opt.lower():
                            format_select.select_option(label=opt)
                            print(f"[+] Formato seleccionado: {opt}")
                            break
            except Exception:
                pass

            download_path = os.path.join(DATA_DIR, "omni_presencia_downloaded.csv")
            export_success = False
            downloads = []
            context.on("download", lambda d: downloads.append(d))
            page.on("download", lambda d: downloads.append(d))

            modal_export_btn = modal.locator("button.uiButton--brand, button:has-text('Exportar')").last
            print("[*] 6. Disparando descarga del archivo CSV...")
            modal_export_btn.click(no_wait_after=True)
            print("[+] Solicitud de exportación enviada. Monitoreando descarga...")

            for _ in range(45):
                if downloads:
                    break
                for p_extra in context.pages:
                    if p_extra != page and ("verification" in p_extra.url.lower() or "identity" in p_extra.url.lower()):
                        print("[*] Desafío 2FA detectado en popup. Resolviendo con Outlook MAPI...")
                        sam.completar_desafio_mfa_si_es_necesario(p_extra)
                        time.sleep(3)
                        break
                time.sleep(1)

            if downloads:
                download = downloads[0]
                download.save_as(download_path)
                print(f"\n[✓] ¡REPORTE DE PRESENCIA OMNI DESCARGADO EXITOSAMENTE!")
                print(f"Ruta: {download_path} ({os.path.getsize(download_path)/1024:.1f} KB)")
                # Copiar a historico
                import shutil
                hist_path = os.path.join(DATA_DIR, "omni_presencia_historico.csv")
                shutil.copyfile(download_path, hist_path)
                export_success = True
            else:
                print("[!] No se detectó evento de descarga en el tiempo esperado.")

            browser.close()
            return download_path if export_success else None

        except Exception as e:
            print(f"[!] Error durante la descarga de omni: {e}")
            try:
                browser.close()
            except Exception:
                pass
            return None


def descargar_reporte_chats(headless: bool = True, report_url: str = None) -> str | None:
    """
    Descarga el reporte oficial de Chats de Salesforce Messaging (00OVI0000037jwL2AQ - Informe CHATS CORPORATE GTR AMC).
    Contiene las sesiones de mensajería para AG CHAT ES, AG CELULA REMISION y AG CORPORATE CHAT.
    """
    if not report_url:
        report_url = "https://latamneworg.lightning.force.com/lightning/r/Report/00OVI0000037jwL2AQ/view"

    print(f"[*] URL del reporte de Chats B2B: {report_url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"]
        )
        context_kwargs = {
            "viewport": {"width": 1600, "height": 1000},
            "accept_downloads": True,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }
        if os.path.exists(STATE_PATH):
            context_kwargs["storage_state"] = STATE_PATH

        context = browser.new_context(**context_kwargs)
        page = context.new_page()

        try:
            print("[*] 1. Accediendo al reporte de Chats Messaging Sessions...")
            page.goto(report_url, wait_until="domcontentloaded", timeout=45000)
            time.sleep(5)

            curr_url = page.url.lower()
            page_title = page.title().lower()
            if "login" in curr_url or "ec=302" in curr_url or "identity" in curr_url or "iniciar sesión" in page_title:
                print("[!] Sesión inactiva o desafío detectado. Autenticando...")
                sam.asegurar_sesion_salesforce(page, context, report_url)
                time.sleep(4)

            print("[*] 2. Esperando que compile y renderice el informe...")
            # En reportes nativos Lightning o dentro de iframe
            report_frame = page.frame_locator("iframe[name*='builder'], iframe[src*='lightningReportApp']").first
            mod_btn = page.locator("button:has-text('Modificar'), button:has-text('Edit')").first
            if not mod_btn.is_visible():
                mod_btn = report_frame.locator("button:has-text('Modificar'), button:has-text('Edit')").first

            try:
                mod_btn.wait_for(state="visible", timeout=60000)
                print("[+] Barra de acciones del reporte cargada con éxito.")
            except Exception as e_w:
                print(f"[*] Continuando tras espera de renderizado: {e_w}")

            print("[*] 3. Desplegando menú de acciones del reporte...")
            arrow_btn = mod_btn.locator("xpath=following::button[1]")
            if not arrow_btn.is_visible():
                arrow_btn = page.locator(".slds-button_last, button.slds-button_icon-border-filled").last
            arrow_btn.click()
            time.sleep(1.2)

            print("[*] 4. Abriendo modal de exportación...")
            export_item = page.locator("a:has-text('Exportar'), button:has-text('Exportar'), [role='menuitem']:has-text('Exportar'), lightning-menu-item:has-text('Exportar')").first
            if not export_item.is_visible():
                export_item = report_frame.locator("a:has-text('Exportar'), button:has-text('Exportar'), [role='menuitem']:has-text('Exportar')").first
            export_item.click()
            time.sleep(2)

            print("[*] 5. Configurando opciones de exportación (Solo detalles)...")
            modal = page.locator(".slds-modal, section[role='dialog']").first
            time.sleep(1)
            details_card = page.locator(".slds-modal label:has-text('Solo detalles'), .slds-modal div:has-text('Solo detalles')").last
            if details_card.is_visible(timeout=4000):
                details_card.click()
                time.sleep(1)
                print("[+] 'Solo detalles' seleccionado.")

            try:
                format_select = modal.locator("select").first
                if format_select.is_visible(timeout=2000):
                    options = format_select.evaluate("el => Array.from(el.options).map(o => o.text)")
                    for opt in options:
                        if "csv" in opt.lower() or "coma" in opt.lower():
                            format_select.select_option(label=opt)
                            print(f"[+] Formato seleccionado: {opt}")
                            break
            except Exception:
                pass

            modal_export_btn = modal.locator("button.uiButton--brand, button.slds-button_brand:has-text('Exportar'), button:has-text('Exportar')").last
            print("[*] 6. Disparando descarga del archivo CSV de Chats...")

            download_path = os.path.join(DATA_DIR, "chats_b2b_downloaded.csv")
            export_success = False

            try:
                with page.expect_download(timeout=40000) as download_info:
                    modal_export_btn.click()
                    print("[+] Botón de exportación presionado. Esperando descarga...")
                download = download_info.value
                download.save_as(download_path)
                print(f"\n[✓] ¡REPORTE DE CHATS B2B DESCARGADO EXITOSAMENTE!")
                print(f"Ruta: {download_path} ({os.path.getsize(download_path)/1024:.1f} KB)")
                # Copiar a historico
                import shutil
                hist_path = os.path.join(DATA_DIR, "chats_b2b_historico.csv")
                shutil.copyfile(download_path, hist_path)
                export_success = True
            except Exception as e_down:
                print(f"[*] Excepción en expect_download: {e_down}")
                # Verificar si abrió desafío 2FA
                for p_extra in context.pages:
                    if p_extra != page and ("verification" in p_extra.url.lower() or "identity" in p_extra.url.lower()):
                        print("[*] Desafío 2FA detectado en popup. Resolviendo con Outlook MAPI...")
                        sam.completar_desafio_mfa_si_es_necesario(p_extra)
                        time.sleep(3)
                        break
                page.screenshot(path=os.path.join(DATA_DIR, "modal_chats_export_debug.png"))

            browser.close()
            return download_path if export_success else None

        except Exception as e:
            print(f"[!] Error durante la descarga de chats: {e}")
            try:
                browser.close()
            except Exception:
                pass
            return None


if __name__ == "__main__":
    is_visible = "--visible" in sys.argv
    if "--chats" in sys.argv:
        descargar_reporte_chats(headless=not is_visible)
    elif "--omni" in sys.argv:
        descargar_reporte_omni(headless=not is_visible)
    elif "--logins" in sys.argv:
        descargar_reporte_logins(headless=not is_visible)
    else:
        descargar_reporte_casos_2026(headless=not is_visible)


