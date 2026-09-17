"""
Módulo de Autenticación Autónoma para Salesforce Service Cloud (AMC LATAM).
1. Utiliza el perfil persistente en data/salesforce_browser_profile.
2. Autentica automáticamente usuario y contraseña en flujo de 2 pasos.
3. Resuelve el 2FA / MFA leyendo automáticamente el código de 6 dígitos desde Microsoft Outlook vía MAPI.
4. Marca la casilla "No volver a preguntar" para registrar el equipo de confianza.
5. Garantiza acceso desatendido a Omni-Supervisor sin intervención humana.
"""

import os
import sys
import time
import json
import re
from datetime import datetime, timezone, timedelta

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.normpath(os.path.join(BASE_DIR, ".."))
PROFILE_DIR = os.path.join(PROJECT_DIR, "data", "salesforce_browser_profile")
CREDS_PATH = os.path.join(BASE_DIR, "salesforce_credentials.json")
CONFIG_PATH = os.path.join(BASE_DIR, "salesforce_live_config.json")
STATE_PATH = os.path.join(PROJECT_DIR, "data", "salesforce_state.json")


def load_credentials():
    if not os.path.exists(CREDS_PATH):
        return {}
    with open(CREDS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_omni_url():
    default_url = "https://latamneworg.lightning.force.com/one/one.app#eyJjb21wb25lbnREZWYiOiJvbW5pOnN1cGVydmlzb3JQYW5lbCIsImF0dHJpYnV0ZXMiOnt9LCJzdGF0ZSI6e319"
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                return cfg.get("command_center_url", default_url)
        except Exception:
            pass
    return default_url


def obtener_codigo_verificacion_outlook(max_wait_sec: int = 90, max_age_seconds: int = 240) -> str:
    """
    Monitorea la bandeja de entrada de Outlook vía Windows MAPI
    buscando el correo reciente de 'Verificar su identidad en Salesforce'.
    """
    print("[*] Conectando con Microsoft Outlook (MAPI) para capturar el código 2FA...")
    start_time = time.time()

    while time.time() - start_time < max_wait_sec:
        try:
            import win32com.client
            outlook = win32com.client.Dispatch("Outlook.Application")
            namespace = outlook.GetNamespace("MAPI")
            inbox = namespace.GetDefaultFolder(6)  # 6 = olFolderInbox
            messages = inbox.Items
            messages.Sort("[ReceivedTime]", True)

            for i in range(min(10, messages.Count)):
                msg = messages.Item(i + 1)
                subj = str(getattr(msg, "Subject", "") or "").lower()
                sender = str(getattr(msg, "SenderEmailAddress", "") or getattr(msg, "SenderName", "")).lower()

                if "verificar su identidad" in subj or "salesforce" in subj or "noreply@salesforce.com" in sender:
                    recv_time = getattr(msg, "ReceivedTime", None)
                    if recv_time:
                        try:
                            t_naive = recv_time.replace(tzinfo=None)
                            diff_sec = (datetime.now() - t_naive).total_seconds()
                        except Exception:
                            diff_sec = 0

                        # Aceptar correos que llegaron en los últimos max_age_seconds (4 minutos)
                        if diff_sec <= max_age_seconds:
                            body = str(getattr(msg, "Body", "") or "")
                            codes = re.findall(r"\b\d{6}\b", body)
                            if codes:
                                print(f"[+] ¡Código 2FA detectado en Outlook!: {codes[0]} (Recibido hace {int(diff_sec)}s)")
                                return codes[0]
                            else:
                                print(f"[*] Correo encontrado pero sin código de 6 dígitos todavía...")
                        else:
                            print(f"[*] Correo anterior de Salesforce descartado (hace {int(diff_sec)}s). Esperando el nuevo...")
                            break
        except Exception as e_mapi:
            print(f"[!] Aviso leyendo Outlook MAPI: {e_mapi}")
            time.sleep(2)

        time.sleep(3)

    print("[!] Tiempo de espera agotado buscando código reciente en Outlook.")
    return None


def asegurar_sesion_salesforce(page, context, target_url: str = None) -> bool:
    """
    Verifica y asegura que la página tenga la sesión abierta en Salesforce.
    Si está en formulario de login o verificación, lo resuelve 100% en automático.
    """
    if not target_url:
        target_url = load_omni_url()

    creds = load_credentials()
    user = creds.get("username", "")
    pwd = creds.get("password", "")

    curr_url = page.url.lower()
    page_title = page.title().lower()

    # 1. Detectar si requiere login de usuario / contraseña
    is_login = False
    try:
        if (
            "login" in curr_url
            or "ec=302" in curr_url
            or "iniciar sesión" in page_title
            or "login" in page_title
            or page.locator("#username").is_visible(timeout=3000)
            or page.locator("#password").is_visible(timeout=2000)
        ):
            is_login = True
    except Exception:
        pass

    if is_login:
        print("[*] Formulario de inicio de sesión detectado en Salesforce...")
        try:
            # Paso 1: Usuario
            if page.locator("#username").is_visible(timeout=4000):
                curr_val = page.locator("#username").input_value()
                if not curr_val or curr_val != user:
                    print(f"[*] Rellenando usuario: {user}...")
                    page.fill("#username", user)
                print("[*] Enviando usuario...")
                page.click("#Login")
                time.sleep(3)

            # Paso 2: Contraseña
            if page.locator("#password").is_visible(timeout=6000):
                print("[*] Rellenando contraseña...")
                page.fill("#password", pwd)
                print("[*] Enviando credenciales de acceso...")
                page.click("#Login")
                time.sleep(6)
        except Exception as e_login:
            print(f"[!] Nota durante ingreso de credenciales: {e_login}")

    # 2. Detectar si requiere verificación de identidad (MFA / 2FA por correo)
    curr_url = page.url.lower()
    is_verification = False
    try:
        if (
            "verification" in curr_url
            or "identity" in curr_url
            or page.locator("#emc").is_visible(timeout=3000)
            or page.locator("input[name='emc']").is_visible(timeout=2000)
            or "verificar su identidad" in page.content().lower()
        ):
            is_verification = True
    except Exception:
        pass

    if is_verification:
        print("[*] Pantalla de verificación de identidad (2FA) detectada. Buscando código en Outlook...")
        code = obtener_codigo_verificacion_outlook(max_wait_sec=90, max_age_seconds=300)
        if code:
            try:
                # Escribir el código en el campo correspondiente
                input_code = page.locator("#emc, input[name='emc'], input[type='text']").first
                if input_code.is_visible(timeout=4000):
                    input_code.fill(code)
                    print(f"[+] Código {code} ingresado en el formulario de Salesforce.")

                # Marcar casilla "No volver a preguntar" para registrar este equipo
                try:
                    chk = page.locator("input[type='checkbox']").first
                    if chk.is_visible(timeout=2000):
                        if not chk.is_checked():
                            chk.check()
                        print("[+] Casilla 'No volver a preguntar' marcada.")
                except Exception:
                    pass

                # Enviar formulario de verificación
                btn_verify = page.locator("#save, input[type='submit'], button:has-text('Verificar')").first
                if btn_verify.is_visible(timeout=3000):
                    btn_verify.click()
                    print("[*] Formulario de verificación enviado...")
                    time.sleep(8)
            except Exception as e_ver:
                print(f"[!] Error al ingresar código de verificación: {e_ver}")
        else:
            print("[!] No se pudo obtener el código reciente de Outlook.")

    # 3. Confirmar que la sesión está en Lightning / Omni-Supervisor
    for _ in range(5):
        curr_url = page.url.lower()
        if ("lightning" in curr_url or "one.app" in curr_url) and "login" not in curr_url and "identity" not in curr_url:
            print("[+] ¡Sesión autenticada y activa en Salesforce Lightning!")
            try:
                context.storage_state(path=STATE_PATH)
                print(f"[+] Estado de sesión actualizado en: {STATE_PATH}")
            except Exception:
                pass

            # Si no estamos en la URL de Omni-Supervisor, navegar hacia ella
            if "supervisorpanel" not in curr_url:
                print(f"[*] Navegando a Omni-Supervisor: {target_url}...")
                page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
                time.sleep(6)
            return True
        time.sleep(2)

    return False


if __name__ == "__main__":
    from playwright.sync_api import sync_playwright
    print("=" * 70)
    print("PROBANDO AUTENTICACIÓN AUTÓNOMA SALESFORCE + OUTLOOK 2FA")
    print("=" * 70)
    os.makedirs(PROFILE_DIR, exist_ok=True)
    omni_url = load_omni_url()

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=True,
            viewport={"width": 1400, "height": 900},
            args=["--disable-blink-features=AutomationControlled"]
        )
        pg = ctx.pages[0] if ctx.pages else ctx.new_page()
        print(f"[*] Abriendo Salesforce en: {omni_url}...")
        pg.goto(omni_url, wait_until="domcontentloaded", timeout=45000)
        time.sleep(4)

        ok = asegurar_sesion_salesforce(pg, ctx, omni_url)
        print(f"Resultado de autenticación: {'EXITOSA (200)' if ok else 'FALLIDA'}")
        print(f"URL final: {pg.url}")
        print(f"Título: {pg.title()}")
        ctx.close()
