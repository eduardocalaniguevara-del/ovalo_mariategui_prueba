# -*- coding: utf-8 -*-
"""
Lanzador Autónomo de Escritorio — Recepción Hotel Óvalo Mariátegui
Se ejecuta con doble clic desde el ícono del Escritorio ("Recepción Hotel Óvalo (Local)"):
- No usa ni consume nada de Antigravity / IA (corre 100% en tu Windows local).
- Si el motor local aún no está prendido, lo enciende en silencio y abre Chrome.
- Si ya estaba prendido, abre la ventana de Recepción inmediatamente.
"""

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser

APP_DIR = Path(__file__).resolve().parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


def _autoproteger_codigo_prueba() -> None:
    """
    Candado autónomo del entorno de prueba (Punto 1c):
    Si algún script externo antiguo (ej. instalar_arnes_laptop2.py tras un reset de Operativa)
    llegara a sobrescribir config.py / supabase_sync.py / main.py en esta carpeta de prueba
    o degradar spreadsheet_id a SIN_HOJA_MODO_PRUEBA, restaura de inmediato desde HEAD local
    antes de importar config.py para evitar caída por ImportError.
    """
    ruta_low = str(APP_DIR).lower()
    if "prueba" not in ruta_low or "recepcion_ovalo_local" in ruta_low or "terrazas" in ruta_low:
        return

    cfg_file = APP_DIR / "config.py"
    if cfg_file.is_file() and (APP_DIR / ".git").exists():
        try:
            txt_cfg = cfg_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            txt_cfg = ""
        if "HOJA_PRUEBA_HABILITADA" not in txt_cfg:
            for git_cand in (
                shutil.which("git"),
                r"C:\Hostal_Sistema\tools\mingit\cmd\git.exe",
                r"C:\Program Files\Git\cmd\git.exe",
            ):
                if git_cand and os.path.exists(git_cand):
                    try:
                        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0
                        subprocess.run(
                            [
                                git_cand,
                                "checkout",
                                "HEAD",
                                "--",
                                "config.py",
                                "supabase_sync.py",
                                "main.py",
                                "sync_engine.py",
                                "promover_a_operativa.py",
                                "instalar_arnes_laptop2.py",
                            ],
                            cwd=str(APP_DIR),
                            capture_output=True,
                            timeout=10,
                            creationflags=flags,
                        )
                    except Exception:
                        pass
                    break

    perfil_file = APP_DIR / "data" / "hotel_perfil.json"
    if perfil_file.is_file():
        try:
            pf = json.loads(perfil_file.read_text(encoding="utf-8-sig"))
            if isinstance(pf, dict) and str(pf.get("spreadsheet_id") or "").strip() in ("", "SIN_HOJA_MODO_PRUEBA"):
                pf["spreadsheet_id"] = "1VXg1d8QQgS2te1NWlSFbK6Y4fqLV5B-kNwJ-mezRoqE"
                pf["modo_prueba"] = True
                pf["conectar_pruebas_supabase"] = False
                perfil_file.write_text(json.dumps(pf, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except Exception:
            pass


_autoproteger_codigo_prueba()

from config import APP_HOST, APP_PORT, NOMBRE_HOTEL

BIND_HOST = APP_HOST
LOCAL_HOST = "127.0.0.1"
PORT = APP_PORT
URL = f"http://{LOCAL_HOST}:{PORT}"

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
]


def servidor_activo() -> bool:
    try:
        with socket.create_connection((LOCAL_HOST, PORT), timeout=0.5):
            return True
    except OSError:
        return False


def abrir_navegador_app() -> None:
    if "--no-browser" in sys.argv:
        return
    for chrome_exe in CHROME_PATHS:
        if os.path.exists(chrome_exe):
            try:
                subprocess.Popen(
                    [chrome_exe, f"--app={URL}"],
                    cwd=str(APP_DIR),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    close_fds=True,
                )
                return
            except Exception:
                pass
    try:
        os.startfile(URL)
    except Exception:
        webbrowser.open(URL)


def _esperar_y_abrir_navegador() -> None:
    for _ in range(40):
        if servidor_activo():
            break
        time.sleep(0.2)
    abrir_navegador_app()


def main() -> None:
    if servidor_activo():
        abrir_navegador_app()
        return

    log_path = APP_DIR / "server_local.log"
    log_file = open(log_path, "a", encoding="utf-8", buffering=1)
    sys.stdout = log_file
    sys.stderr = log_file

    # Abrir el navegador apenas el puerto 8080 responda
    threading.Thread(target=_esperar_y_abrir_navegador, daemon=True).start()

    # Sincronizar última versión de GitHub antes de importar módulos por si hubo un corte previo
    try:
        from auto_updater import verificar_y_aplicar_actualizacion
        verificar_y_aplicar_actualizacion()
    except Exception:
        pass

    # Iniciar el servidor FastAPI directamente en este proceso silencioso
    import uvicorn
    from main import app

    uvicorn.run(app, host=BIND_HOST, port=PORT, log_config=None)


if __name__ == "__main__":
    main()
