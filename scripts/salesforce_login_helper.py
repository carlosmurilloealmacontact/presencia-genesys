"""
Script asistido de inicio de sesion con Playwright para Salesforce.
Abre una ventana de navegador real, inicia sesion con tus credenciales guardadas,
te permite pasar la verificacion de seguridad (si aplica) y guarda la sesion
persistente en data/salesforce_browser_profile para que los scripts de fondo
puedan consultar el Command Center en vivo sin pedir contrasenas de nuevo.
"""

import json
import os
import time
from playwright.sync_api import sync_playwright

BASE_DIR = os.path.dirname(__file__)
CREDS_PATH = os.path.join(BASE_DIR, "salesforce_credentials.json")
PROFILE_DIR = os.path.join(BASE_DIR, "..", "data", "salesforce_browser_profile")
CONFIG_PATH = os.path.join(BASE_DIR, "salesforce_live_config.json")


def load_credentials():
    if not os.path.exists(CREDS_PATH):
        print(f"[!] Archivo {CREDS_PATH} no encontrado.")
        return None
    with open(CREDS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def run_login_helper():
    creds = load_credentials()
    if not creds:
        return

    username = creds.get("username", "")
    password = creds.get("password", "")
    login_url = "https://latamneworg.my.salesforce.com"

    print("=" * 70)
    print("INICIANDO NAVEGADOR ASISTIDO DE SALESFORCE (PLAYWRIGHT)")
    print("=" * 70)
    print(f"Usuario: {username}")
    print(f"Perfil de sesion persistente: {PROFILE_DIR}")
    print("Se abrira una ventana de navegador. Por favor no la cierres.")
    print("=" * 70)

    os.makedirs(PROFILE_DIR, exist_ok=True)

    with sync_playwright() as p:
        # Lanzar contexto persistente para guardar cookies y sesion
        context = p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=False,
            viewport={"width": 1400, "height": 900},
            args=["--start-maximized"]
        )

        page = context.new_page() if not context.pages else context.pages[0]

        print(f"[*] Navegando a {login_url}...")
        page.goto(login_url, wait_until="networkidle", timeout=60000)

        # Verificar si pide login o si ya esta logueado por sesion previa
        time.sleep(3)
        if "login" in page.url.lower():
            print("[*] Formulario de login detectado. Rellenando credenciales...")
            try:
                if page.locator("#username").is_visible(timeout=5000):
                    page.fill("#username", username)
                    print("[+] Usuario ingresado. Avanzando a contraseña...")
                    page.click("#Login")
                    time.sleep(3)

                if page.locator("#password").is_visible(timeout=5000):
                    page.fill("#password", password)
                    print("[+] Contraseña ingresada. Enviando formulario...")
                    page.click("#Login")
            except Exception as e:
                print(f"[!] Nota sobre el formulario de login: {e}")

        print("\n" + "=" * 70)
        print("ESPERANDO INICIO DE SESIÓN COMPLETO...")
        print("Si Salesforce te solicita un código de verificación (correo/SMS/autenticador),")
        print("ingrésalo directamente en la ventana abierta del navegador.")
        print("=" * 70)

        # Esperar hasta que la URL sea de la consola Lightning
        max_wait = 300  # 5 minutos maximo para que complete el login
        start_time = time.time()
        logged_in = False

        while time.time() - start_time < max_wait:
            curr_url = page.url.lower()
            if ("lightning" in curr_url or "my.salesforce.com" in curr_url) and "login" not in curr_url:
                logged_in = True
                print("\n[✓] ¡SESIÓN INICIADA CON ÉXITO EN SALESFORCE LIGHTNING!")
                print(f"URL actual: {page.url}")
                try:
                    state_path = os.path.join(BASE_DIR, "..", "data", "salesforce_state.json")
                    context.storage_state(path=state_path)
                    print(f"[+] Estado de sesión guardado en: {state_path}")
                except Exception:
                    pass
                break
            time.sleep(3)

        if not logged_in:
            print("[!] Tiempo de espera agotado para el inicio de sesion.")
            context.close()
            return

        print("\n" + "=" * 70)
        print("PASO FINAL: NAVEGAR AL COMMAND CENTER / SUPERVISOR OMNI-CHANNEL")
        print("=" * 70)
        print("En la ventana del navegador que tienes abierta:")
        print("1. Abre la pestaña o aplicación del 'Command Center' o 'Supervisor de Omni-Channel'")
        print("   (donde ves los chats esperando y la lista de agentes).")
        print("2. Una vez estés viendo esa pantalla, presiona ENTER en esta consola.")
        print("=" * 70)

        input("\n>>> Presiona ENTER cuando estés viendo la pantalla del Command Center... <<<")

        command_center_url = page.url
        print(f"\n[+] URL del Command Center capturada: {command_center_url}")

        # Guardar configuracion
        config = {
            "command_center_url": command_center_url,
            "last_login": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)

        print(f"[+] Configuracion guardada en: {CONFIG_PATH}")
        print("[+] La sesion ha quedado guardada de forma persistente.")
        print("Cerrando navegador asistido...")
        time.sleep(2)
        context.close()
        print("[✓] Proceso completado con exito. Ya podemos ejecutar el extractor de fondo.")


if __name__ == "__main__":
    run_login_helper()
