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


def obtener_codigo_verificacion_outlook(min_received_time: datetime = None, max_wait_sec: int = 120) -> str:
    """
    Monitorea la bandeja de entrada de Outlook vía Windows MAPI
    buscando el correo reciente de 'Verificar su identidad en Salesforce'.
    Garantiza que el código pertenezca a los últimos 15 minutos (vigencia oficial de Salesforce).
    """
    now_ref = datetime.now()
    if min_received_time is None:
        min_received_time = now_ref - timedelta(minutes=15)

    print(f"[*] Buscando código 2FA en Outlook válido posterior a las {min_received_time.strftime('%H:%M:%S')}...")
    start_time = time.time()

    # Asegurar que OUTLOOK.EXE esté corriendo en el sistema
    try:
        import subprocess
        ps_chk = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Process -Name OUTLOOK -ErrorAction SilentlyContinue"], capture_output=True, text=True)
        if not ps_chk.stdout.strip():
            outlook_bin = r"C:\Program Files\Microsoft Office\root\Office16\OUTLOOK.EXE"
            if os.path.exists(outlook_bin):
                subprocess.Popen([outlook_bin])
                time.sleep(3)
    except Exception:
        pass

    try:
        import win32com.client
        import pythoncom
        pythoncom.CoInitialize()
        outlook = win32com.client.Dispatch("Outlook.Application")
        namespace = outlook.GetNamespace("MAPI")
        inbox = namespace.GetDefaultFolder(6)  # 6 = olFolderInbox

        while time.time() - start_time < max_wait_sec:
            try:
                messages = inbox.Items
                messages.Sort("[ReceivedTime]", True)

                for i in range(min(15, messages.Count)):
                    msg = messages.Item(i + 1)
                    subj = str(getattr(msg, "Subject", "") or "").lower()
                    sender = str(getattr(msg, "SenderEmailAddress", "") or getattr(msg, "SenderName", "")).lower()

                    if "verificar su identidad" in subj or "salesforce" in subj or "noreply@salesforce.com" in sender:
                        recv_time = getattr(msg, "ReceivedTime", None)
                        if recv_time:
                            try:
                                t_naive = recv_time.replace(tzinfo=None)
                            except Exception:
                                t_naive = datetime.now()

                            # Los códigos de Salesforce son válidos por 15 minutos
                            diff_min = abs((now_ref - t_naive).total_seconds()) / 60.0
                            if t_naive >= min_received_time or diff_min <= 15.0:
                                body = str(getattr(msg, "Body", "") or "")
                                codes = re.findall(r"\b\d{6}\b", body)
                                if codes:
                                    print(f"[+] ¡Código 2FA detectado (recibido hace {diff_min:.1f} min)!: {codes[0]}")
                                    return codes[0]
                            else:
                                print(f"[*] Último correo en Outlook es de las {t_naive.strftime('%H:%M:%S')} (hace {diff_min:.1f} min). Esperando llegada de código...")
                                break
            except Exception as e_mapi:
                print(f"[!] Aviso leyendo Outlook MAPI: {e_mapi}")
                time.sleep(2)

            time.sleep(3)

        print("[!] Tiempo de espera agotado buscando código nuevo en Outlook.")
        return None
    except Exception as e_outer:
        print(f"[!] Error inicializando Outlook COM: {e_outer}")
        return None
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


from urllib.parse import urlparse

def asegurar_sesion_salesforce(page, context, target_url: str = None) -> bool:
    """
    Verifica y asegura que la página tenga la sesión abierta en Salesforce Lightning.
    Si está en formulario de login o verificación 2FA, lo resuelve 100% de manera autónoma.
    """
    if not target_url:
        target_url = load_omni_url()

    creds = load_credentials()
    user = creds.get("username", "")
    pwd = creds.get("password", "")

    parsed_initial = urlparse(page.url)
    if parsed_initial.netloc.endswith("lightning.force.com") and not any(k in page.url.lower() for k in ["ec=302", "login", "identity", "verification"]):
        print(f"[+] Sesión ya se encuentra activa en Salesforce Lightning ({page.url[:60]}...).")
        return True

    print("[*] Verificando estado de sesión / necesidad de autenticación...")

    # 1. Manejo de formulario de Login (Usuario / Tarjeta de Identidad y Contraseña)
    needs_login = (
        "login" in page.url.lower()
        or "ec=302" in page.url.lower()
        or page.locator("#Login").is_visible(timeout=2000)
        or page.locator("#username").is_visible(timeout=1500)
        or page.locator("#password").is_visible(timeout=1500)
    )

    if needs_login:
        print("[*] Formulario de inicio de sesión detectado en Salesforce...")
        try:
            # Paso 1: Si no está visible la contraseña, interactuar con usuario/idcard para revelarla
            if not page.locator("#password").is_visible():
                print("[*] Paso 1: Usuario/ID Card. Enviando para mostrar campo de contraseña...")
                if page.locator("#username").is_visible() and not page.locator("#username").input_value():
                    print(f"[*] Rellenando usuario: {user}...")
                    page.fill("#username", user)
                
                try:
                    if page.locator("#rememberUn").is_visible(timeout=1500):
                        if not page.locator("#rememberUn").is_checked():
                            page.locator("#rememberUn").check()
                except Exception:
                    pass

                page.click("#Login")
                time.sleep(3)

            # Paso 2: Rellenar contraseña
            try:
                page.wait_for_selector("#password", timeout=8000)
            except Exception:
                pass

            if page.locator("#password").is_visible(timeout=4000):
                print("[*] Paso 2: Rellenando contraseña...")
                page.fill("#password", pwd)
                time.sleep(1)
                print("[*] Enviando credenciales de acceso...")
                page.click("#Login")
                time.sleep(5)
        except Exception as e_login:
            print(f"[!] Nota durante ingreso de credenciales: {e_login}")

    # 2. Manejo de Verificación de Identidad (MFA / 2FA vía Outlook MAPI)
    for _ in range(6):
        curr_url = page.url.lower()
        if (
            "identity" in curr_url
            or "verification" in curr_url
            or page.locator("#emc").is_visible(timeout=1000)
            or "verificar" in page.title().lower()
        ):
            print("[*] Paso 3: Pantalla de verificación 2FA detectada. Buscando código en Outlook...")
            code = obtener_codigo_verificacion_outlook(max_wait_sec=90)
            if code:
                try:
                    input_code = page.locator("#emc, input[name='emc'], input[type='text']").first
                    if input_code.is_visible(timeout=3000):
                        input_code.fill(code)
                        print(f"[+] Código {code} ingresado en el formulario de Salesforce.")

                    chk = page.locator("#RememberDeviceCheckbox, #rememberUnaccDevice, input[type='checkbox']").first
                    if chk.is_visible(timeout=2000):
                        if not chk.is_checked():
                            chk.check()
                        print("[+] Casilla 'No volver a preguntar' marcada para registrar equipo de confianza.")

                    btn_verify = page.locator("#save, input[type='submit'], input[value='Verificar']").first
                    if btn_verify.is_visible(timeout=3000):
                        btn_verify.click()
                        print("[*] Formulario de verificación enviado...")
                        time.sleep(8)

                    # Si el código fue inválido o expiró, reintentar con botón 'Volver a enviar'
                    if page.locator(".errorMsg, #error").is_visible():
                        print("[!] Código expirado o rechazado. Reenviando código nuevo...")
                        btn_resend = page.locator("a:has-text('Volver a enviar el código')").first
                        if btn_resend.is_visible():
                            btn_resend.click()
                            time.sleep(12)
                            new_code = obtener_codigo_verificacion_outlook(max_wait_sec=90)
                            if new_code and new_code != code:
                                print(f"[+] Nuevo código recibido: {new_code}. Ingresando...")
                                page.fill("#emc", new_code)
                                page.click("#save")
                                time.sleep(8)
                except Exception as e_ver:
                    print(f"[!] Error resolviendo 2FA: {e_ver}")
            break
        
        parsed = urlparse(page.url)
        if parsed.netloc.endswith("lightning.force.com") and not any(k in page.url.lower() for k in ["ec=302", "login", "identity", "verification"]):
            break
        time.sleep(2)

    # 3. Confirmar llegada y redirección a Lightning / Omni-Supervisor
    print("[*] Verificando redirección a Salesforce Lightning...")
    for i in range(12):
        parsed = urlparse(page.url)
        is_authenticated = (
            parsed.netloc.endswith("lightning.force.com")
            and not any(k in page.url.lower() for k in ["ec=302", "login", "identity", "verification"])
            and "iniciar sesión" not in page.title().lower()
            and "verificar su identidad" not in page.title().lower()
        )

        if is_authenticated:
            print(f"[+] ¡Sesión autenticada y activa en Salesforce Lightning! (URL: {page.url[:60]}..., Título: {page.title()})")
            time.sleep(3)
            try:
                context.storage_state(path=STATE_PATH)
                print(f"[+] Estado de sesión guardado exitosamente en: {STATE_PATH}")
            except Exception as e_st:
                print(f"[*] Nota storage_state: {e_st}")

            if "supervisorpanel" not in page.url.lower():
                print(f"[*] Navegando directamente a Omni-Supervisor: {target_url}...")
                try:
                    page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
                    time.sleep(5)
                except Exception as e_nav:
                    print(f"[*] Nota navegación: {e_nav}")
            return True
        time.sleep(2.5)

    return False


def completar_desafio_mfa_si_es_necesario(page) -> bool:
    """Resuelve el desafío 2FA en una página o popup de verificación."""
    try:
        curr_url = page.url.lower()
        if (
            "verification" in curr_url
            or "identity" in curr_url
            or "verificar su identidad" in page.title().lower()
            or page.locator("#emc").is_visible(timeout=2000)
            or page.locator("input[name='emc']").is_visible(timeout=2000)
        ):
            print("[*] Desafío 2FA detectado. Obteniendo código desde Outlook...")
            code = obtener_codigo_verificacion_outlook(min_received_time=datetime.now() - timedelta(minutes=4), max_wait_sec=120)
            if code:
                inp = page.locator("#emc, input[name='emc'], input[type='text']").first
                if inp.is_visible(timeout=3000):
                    inp.fill(code)
                try:
                    chk = page.locator("#rememberUnaccDevice, input[type='checkbox']").first
                    if chk.is_visible(timeout=2000):
                        chk.check()
                except Exception:
                    pass
                btn = page.locator("#save, input[type='submit'], input[value='Verificar'], button:has-text('Verificar')").first
                if btn.is_visible(timeout=3000):
                    btn.click(no_wait_after=True)
                    time.sleep(6)
                return True
    except Exception as e:
        print(f"[!] Error completando MFA en página: {e}")
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
