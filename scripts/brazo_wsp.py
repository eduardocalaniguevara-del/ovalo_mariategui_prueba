import os
import sys
import time
import json
import argparse
import urllib.parse
import subprocess
import glob
from datetime import datetime

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


def normalizar_telefono(telefono):
    tel = "".join(ch for ch in str(telefono) if ch.isdigit())
    if len(tel) == 9:
        tel = "51" + tel
    return tel


def buscar_archivo(archivo):
    if not archivo:
        return None

    user_profile = os.environ.get("USERPROFILE", "")
    nombre = os.path.basename(archivo)

    # Esperar hasta 10s por si Syncthing está terminando de replicar el PDF desde Laptop 1
    for intento in range(20):
        if os.path.isabs(archivo) and os.path.isfile(archivo):
            return os.path.abspath(archivo)

        candidatos = [
            archivo,
            os.path.join(r"C:\Hostal_Sistema\descargas_recepcion", nombre),
            os.path.join(r"C:\Hostal_Sistema\descargas_recepcion", archivo),
            os.path.join(user_profile, "Downloads", archivo),
            os.path.join(r"C:\Hostal_Sistema", archivo),
            os.path.join(SCRIPTS_DIR, archivo),
            os.path.join(r"C:\Hostal_Sistema\scripts", archivo),
        ]
        for c in candidatos:
            if os.path.isfile(c):
                return os.path.abspath(c)

        downloads_pattern = os.path.join(user_profile, "Downloads", f"*{nombre}*")
        matches = glob.glob(downloads_pattern)
        if matches:
            matches.sort(key=os.path.getmtime, reverse=True)
            pdfs = [m for m in matches if m.lower().endswith(".pdf")]
            return os.path.abspath(pdfs[0] if pdfs else matches[0])

        hostal_pattern = os.path.join(r"C:\Hostal_Sistema", "**", f"*{nombre}*")
        matches = glob.glob(hostal_pattern, recursive=True)
        if matches:
            matches.sort(key=os.path.getmtime, reverse=True)
            pdfs = [m for m in matches if m.lower().endswith(".pdf")]
            return os.path.abspath(pdfs[0] if pdfs else matches[0])

        if intento < 19:
            time.sleep(0.5)

    raise FileNotFoundError(f"No se encontró el archivo '{archivo}' en las rutas de búsqueda.")


def poner_archivo_en_portapapeles(ruta_archivo):
    ruta_archivo = os.path.abspath(ruta_archivo)
    try:
        import win32clipboard
        import win32con
        import struct
        offset = 20
        dropfiles = struct.pack("IIIIi", offset, 0, 0, 0, 1)
        data = dropfiles + (ruta_archivo + "\0\0").encode("utf-16le")
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_HDROP, data)
            return True
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        pass

    cmd = ["powershell", "-NoProfile", "-Command", f"Set-Clipboard -LiteralPath '{ruta_archivo}'"]
    res = subprocess.run(cmd, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    return res.returncode == 0


def conectar_winsta0():
    try:
        import ctypes
        user32 = ctypes.windll.user32
        hwinsta = user32.OpenWindowStationW("winsta0", False, 0x037F)
        if hwinsta:
            user32.SetProcessWindowStation(hwinsta)
            hdesk = user32.OpenDesktopW("default", 0, False, 0x01FF)
            if hdesk:
                user32.SetThreadDesktop(hdesk)
    except Exception:
        pass


def whatsapp_proceso_activo():
    try:
        res = subprocess.run(
            ["tasklist", "/NH", "/FI", "IMAGENAME eq WhatsApp*"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
            timeout=4,
        )
        out = (res.stdout or "").lower()
        return "whatsapp.exe" in out or "whatsapp.root.exe" in out
    except Exception:
        return False


def whatsapp_protocolo_instalado():
    if whatsapp_proceso_activo():
        return True
    try:
        import winreg
        for root, subkey in (
            (winreg.HKEY_CLASSES_ROOT, "whatsapp"),
            (winreg.HKEY_CURRENT_USER, r"Software\Classes\whatsapp"),
        ):
            try:
                with winreg.OpenKey(root, subkey):
                    return True
            except OSError:
                continue
    except Exception:
        pass
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "if (Get-AppxPackage *WhatsApp*) { 'YES' }"],
            capture_output=True,
            text=True,
            creationflags=CREATE_NO_WINDOW,
            timeout=5,
        )
        return "YES" in (res.stdout or "")
    except Exception:
        return False


def _obtener_hwnds_whatsapp_desktop():
    conectar_winsta0()
    hwnds = []
    try:
        import win32gui

        def _cb(hwnd, extra):
            t = (win32gui.GetWindowText(hwnd) or "").strip()
            c = (win32gui.GetClassName(hwnd) or "").strip()
            tl = t.lower()
            cl = c.lower()
            if not tl:
                return
            if cl in ("winuidesktopwin32windowclass", "applicationframewindow") and "whatsapp" in tl:
                extra.append(hwnd)
            elif (
                "whatsapp" in tl
                and cl not in ("chrome_widgetwin_1", "gdkwindowtoplevel")
                and all(b not in tl for b in ("edge", "chrome", "brave", "firefox", "gdi+"))
            ):
                extra.append(hwnd)

        win32gui.EnumWindows(_cb, hwnds)
    except Exception:
        pass
    return hwnds


def _aplicar_camuflaje_invisible(hwnd, estado_guardado):
    try:
        import ctypes
        import win32con
        import win32gui

        user32 = ctypes.windll.user32
        if hwnd not in estado_guardado:
            estado_guardado[hwnd] = {
                "orig_ex": win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE),
                "vis_ini": bool(win32gui.IsWindowVisible(hwnd)),
                "iconic_ini": bool(win32gui.IsIconic(hwnd)),
            }
        orig_ex = estado_guardado[hwnd]["orig_ex"]
        nuevo_ex = orig_ex | win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT | win32con.WS_EX_TOOLWINDOW
        if win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE) != nuevo_ex:
            win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, nuevo_ex)
        user32.SetLayeredWindowAttributes(hwnd, 0, 1, win32con.LWA_ALPHA)
    except Exception:
        pass


def _restaurar_camuflaje_invisible(estado_guardado):
    try:
        import ctypes
        import win32con
        import win32gui

        user32 = ctypes.windll.user32
        for hwnd, info in list(estado_guardado.items()):
            try:
                if not win32gui.IsWindow(hwnd):
                    continue
                # Minimizar/ocultar mientras sigue con alpha=1 (100% invisible) para que no parpadee
                user32.ShowWindow(hwnd, win32con.SW_MINIMIZE)
                if not info.get("vis_ini"):
                    user32.ShowWindow(hwnd, win32con.SW_HIDE)
                # Restaurar opacidad normal (255) y estilo original por si abren WhatsApp manualmente después
                user32.SetLayeredWindowAttributes(hwnd, 0, 255, win32con.LWA_ALPHA)
                win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, info["orig_ex"])
            except Exception:
                pass
    except Exception:
        pass


class _EscudoInputFantasma:
    """
    Bloquea clics físicos de mouse y teclas físicas en Recepción durante los ~4s del envío
    invisible por WhatsApp, dejando pasar únicamente los eventos inyectados por Python
    (Ctrl+V y Enter con flag INJECTED) para que nunca se interrumpa el envío.
    """

    def __init__(self, max_seconds=10.0):
        import threading

        self.max_seconds = max_seconds
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread = None

    def _worker(self):
        conectar_winsta0()
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            LRESULT = ctypes.c_ssize_t
            HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

            user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
            user32.SetWindowsHookExW.restype = wintypes.HHOOK
            user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
            user32.CallNextHookEx.restype = LRESULT
            user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
            user32.UnhookWindowsHookEx.restype = wintypes.BOOL
            user32.PeekMessageW.argtypes = [
                ctypes.POINTER(wintypes.MSG),
                wintypes.HWND,
                wintypes.UINT,
                wintypes.UINT,
                wintypes.UINT,
            ]
            user32.PeekMessageW.restype = wintypes.BOOL

            class KBDLLHOOKSTRUCT(ctypes.Structure):
                _fields_ = [
                    ("vkCode", wintypes.DWORD),
                    ("scanCode", wintypes.DWORD),
                    ("flags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t),
                ]

            class MSLLHOOKSTRUCT(ctypes.Structure):
                _fields_ = [
                    ("pt", wintypes.POINT),
                    ("mouseData", wintypes.DWORD),
                    ("flags", wintypes.DWORD),
                    ("time", wintypes.DWORD),
                    ("dwExtraInfo", ctypes.c_size_t),
                ]

            def _kbd_proc(nCode, wParam, lParam):
                if nCode >= 0 and lParam:
                    kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                    if not (kb.flags & 0x00000010):  # LLKHF_INJECTED
                        return 1
                return user32.CallNextHookEx(None, nCode, wParam, lParam)

            def _mouse_proc(nCode, wParam, lParam):
                if nCode >= 0 and lParam:
                    if wParam in (0x0201, 0x0202, 0x0204, 0x0205, 0x0207, 0x0208, 0x020A):
                        ms = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                        if not (ms.flags & 0x00000001):  # LLMHF_INJECTED
                            return 1
                return user32.CallNextHookEx(None, nCode, wParam, lParam)

            kbd_cb = HOOKPROC(_kbd_proc)
            mouse_cb = HOOKPROC(_mouse_proc)
            hk_kbd = user32.SetWindowsHookExW(13, kbd_cb, None, 0)
            hk_mouse = user32.SetWindowsHookExW(14, mouse_cb, None, 0)
            self._ready.set()

            msg = wintypes.MSG()
            t0 = time.time()
            try:
                while not self._stop.is_set() and (time.time() - t0 < self.max_seconds):
                    while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                        user32.TranslateMessage(ctypes.byref(msg))
                        user32.DispatchMessageW(ctypes.byref(msg))
                    time.sleep(0.01)
            finally:
                if hk_kbd:
                    user32.UnhookWindowsHookEx(hk_kbd)
                if hk_mouse:
                    user32.UnhookWindowsHookEx(hk_mouse)
        except Exception:
            self._ready.set()

    def __enter__(self):
        import threading

        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=1.0)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1.0)


def _enfocar_ventana_por_filtro(predicado, incluir_ocultas=False):
    conectar_winsta0()
    try:
        import win32gui
        import win32con
        import ctypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        hwnds = []

        def enum_windows_callback(hwnd, extra):
            if incluir_ocultas or win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd) or ""
                cls_name = win32gui.GetClassName(hwnd) or ""
                if predicado(title.lower(), cls_name.lower()):
                    extra.append(hwnd)

        win32gui.EnumWindows(enum_windows_callback, hwnds)
        if hwnds:
            hwnd = hwnds[0]
            try:
                fore = user32.GetForegroundWindow()
                curr_tid = kernel32.GetCurrentThreadId()
                fore_tid = user32.GetWindowThreadProcessId(fore, None)
                if curr_tid != fore_tid and fore_tid:
                    user32.AttachThreadInput(curr_tid, fore_tid, True)
                if win32gui.IsIconic(hwnd):
                    user32.ShowWindow(hwnd, win32con.SW_RESTORE)
                else:
                    user32.ShowWindow(hwnd, win32con.SW_SHOW)
                user32.SetForegroundWindow(hwnd)
                if curr_tid != fore_tid and fore_tid:
                    user32.AttachThreadInput(curr_tid, fore_tid, False)
                return True
            except Exception:
                pass
    except Exception:
        pass
    return False


def enfocar_whatsapp_desktop(estado_camuflaje=None):
    hwnds = _obtener_hwnds_whatsapp_desktop()
    if estado_camuflaje is not None:
        for h in hwnds:
            _aplicar_camuflaje_invisible(h, estado_camuflaje)
    return _enfocar_ventana_por_filtro(
        lambda t, c: (
            (c in ("winuidesktopwin32windowclass", "applicationframewindow") and "whatsapp" in t)
            or (
                "whatsapp" in t
                and c not in ("chrome_widgetwin_1", "gdkwindowtoplevel")
                and all(b not in t for b in ("edge", "chrome", "brave", "firefox", "gdi+"))
            )
        ),
        incluir_ocultas=True,
    )


def enfocar_edge():
    return _enfocar_ventana_por_filtro(lambda t, c: ("edge" in t) or ("whatsapp" in t))


def pulsar_enter():
    conectar_winsta0()
    try:
        import pyautogui
        pyautogui.FAILSAFE = False
        pyautogui.press("enter")
        return
    except Exception:
        pass
    try:
        import win32com.client
        shell = win32com.client.Dispatch("WScript.Shell")
        shell.SendKeys("{ENTER}")
    except Exception:
        pass


def pulsar_ctrl_v():
    conectar_winsta0()
    try:
        import pyautogui
        pyautogui.FAILSAFE = False
        pyautogui.hotkey("ctrl", "v")
        return
    except Exception:
        pass
    try:
        import win32com.client
        shell = win32com.client.Dispatch("WScript.Shell")
        shell.SendKeys("^v")
    except Exception:
        pass


def _cdp_wsp_activo(port=9222):
    """Verifica si WhatsApp Desktop (WebView2) o Edge tiene el puerto CDP activo."""
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=1.5) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
            return any("whatsapp.com" in (it.get("url") or "") for it in data)
    except Exception:
        return False


def _cdp_obtener_pagina_wsp(playwright_obj, port=9222, timeout_ms=15000):
    """Conecta por CDP al WebView2/Edge activo y devuelve (browser, page) de web.whatsapp.com."""
    browser = playwright_obj.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=timeout_ms)
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    for p_tab in context.pages:
        if "web.whatsapp.com" in (p_tab.url or ""):
            return browser, p_tab
    return browser, None


def _cdp_preview_send_visible(page):
    """Retorna True si el botón Enviar del modal de vista previa de adjunto está visible."""
    try:
        return bool(
            page.evaluate(
                """() => {
                    const sels = [
                        'button[aria-label="Enviar"]',
                        'div[role="button"][aria-label="Enviar"]',
                        'button[aria-label="Send"]',
                        'div[role="button"][aria-label="Send"]',
                        'span[data-icon="wds-ic-send-filled"]',
                        'span[data-icon="send"]'
                    ];
                    for (const sel of sels) {
                        for (const el of document.querySelectorAll(sel)) {
                            if (el.closest('#main footer')) continue;
                            const btn = el.closest('button, [role="button"]') || el;
                            const r = btn.getBoundingClientRect();
                            if (r.width > 0 && r.height > 0) return true;
                        }
                    }
                    return false;
                }"""
            )
        )
    except Exception:
        return False


def _cdp_esperar_y_enviar_preview(page, timeout_s=12.0):
    """
    Espera a que el modal de vista previa de PDF/adjunto monte su botón Enviar,
    hace clic real en él en el DOM y verifica que el modal se cierre.
    """
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        if _cdp_preview_send_visible(page):
            break
        time.sleep(0.25)
    else:
        raise RuntimeError("No apareció el botón Enviar en la vista previa del adjunto.")

    time.sleep(0.35)
    clicked = page.evaluate(
        """() => {
            const sels = [
                'button[aria-label="Enviar"]',
                'div[role="button"][aria-label="Enviar"]',
                'button[aria-label="Send"]',
                'div[role="button"][aria-label="Send"]',
                'span[data-icon="wds-ic-send-filled"]',
                'span[data-icon="send"]'
            ];
            for (const sel of sels) {
                for (const el of document.querySelectorAll(sel)) {
                    if (el.closest('#main footer')) continue;
                    const btn = el.closest('button, [role="button"]') || el;
                    const r = btn.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0) {
                        btn.click();
                        return true;
                    }
                }
            }
            return false;
        }"""
    )
    if not clicked:
        raise RuntimeError("No se pudo hacer clic en el botón Enviar de la vista previa.")

    t_close = time.time()
    while time.time() - t_close < 10.0:
        if not _cdp_preview_send_visible(page):
            time.sleep(0.5)
            return True
        time.sleep(0.25)

    raise RuntimeError("La vista previa del adjunto no se cerró tras hacer clic en Enviar.")


def _cdp_adjuntar_archivo_dom(page, ruta_archivo):
    """Adjunta un archivo vía selector DOM en WhatsApp Web / WebView2."""
    file_input = page.query_selector("input[type='file'][accept*='*']")
    if not file_input:
        attach_btn = page.query_selector(
            "[aria-label='Adjuntar'], [aria-label='Attach'], button[title*='Adjuntar'], "
            "span[data-icon='plus'], span[data-icon='attach-menu-plus']"
        )
        if attach_btn:
            attach_btn.click()
            t0 = time.time()
            while time.time() - t0 < 2.0:
                file_input = page.query_selector("input[type='file'][accept*='*']")
                if file_input:
                    break
                time.sleep(0.15)
        if not file_input:
            file_input = page.query_selector("input[type='file']")

    if not file_input:
        raise RuntimeError("No se pudo localizar el selector de adjuntos en WhatsApp.")
    file_input.set_input_files(ruta_archivo)
    return True


def _cdp_enviar_texto_footer(page, timeout_s=4.0):
    """Envía el texto prellenado en #main footer si existe y espera a que la caja quede limpia."""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        info = page.evaluate(
            """() => {
                const box = document.querySelector('#main footer div[contenteditable="true"]');
                const txt = box ? (box.innerText || '').trim() : '';
                const sendBtn = document.querySelector(
                    '#main footer button[aria-label="Enviar"], #main footer div[role="button"][aria-label="Enviar"], ' +
                    '#main footer button[aria-label="Send"], #main footer span[data-icon="wds-ic-send-filled"], #main footer span[data-icon="send"]'
                );
                return { hasBox: Boolean(box), txtLen: txt.length, hasSend: Boolean(sendBtn) };
            }"""
        )
        if info.get("hasBox") and info.get("txtLen", 0) > 0:
            break
        time.sleep(0.2)

    enviado = page.evaluate(
        """() => {
            const sels = [
                '#main footer button[aria-label="Enviar"]',
                '#main footer div[role="button"][aria-label="Enviar"]',
                '#main footer button[aria-label="Send"]',
                '#main footer span[data-icon="wds-ic-send-filled"]',
                '#main footer span[data-icon="send"]'
            ];
            for (const sel of sels) {
                const el = document.querySelector(sel);
                if (el) {
                    const btn = el.closest('button, [role="button"]') || el;
                    btn.click();
                    return true;
                }
            }
            return false;
        }"""
    )
    if not enviado:
        pulsar_enter()

    t_wait = time.time()
    while time.time() - t_wait < 4.0:
        vacio = page.evaluate(
            """() => {
                const box = document.querySelector('#main footer div[contenteditable="true"]');
                return box ? ((box.innerText || '').trim().length === 0) : false;
            }"""
        )
        if vacio:
            return True
        time.sleep(0.2)
    return False


def _es_entorno_prueba() -> bool:
    try:
        if SCRIPTS_DIR not in sys.path:
            sys.path.insert(0, SCRIPTS_DIR)
        from hoja_activa import es_prueba
        return bool(es_prueba())
    except ImportError:
        raw = os.environ.get("OVALO_ENTORNO", "").strip().lower()
        if not raw:
            if "ovalo_mariategui_prueba" in os.getcwd().lower() or os.environ.get("OVALO_MODO_PRUEBA", "").strip().lower() in ("1", "true", "yes"):
                return True
            return False
        return raw != "operativa"


def enviar_via_whatsapp_desktop(telefono, mensaje, ruta_archivo=None):
    """
    Envía mensaje y archivo PDF usando la aplicación WhatsApp Desktop de Windows
    en modo 100% INVISIBLE (WS_EX_LAYERED alpha=1 + WS_EX_TRANSPARENT) y con escudo
    anti-clics/teclas físicas (_EscudoInputFantasma). Cuando CDP 9222 está disponible,
    espera explícitamente al DOM y hace clic real en el botón Enviar de la vista previa
    verificando que el modal se cierre.
    """
    if _es_entorno_prueba():
        print("🧪 [SIMULACIÓN MODO PRUEBA - NO EMITIDO EN SUNAT / NO ENVIADO POR WSP]", file=sys.stderr)
        return True
    import threading

    conectar_winsta0()
    ventana_previa = None
    try:
        import ctypes
        ventana_previa = ctypes.windll.user32.GetForegroundWindow()
    except Exception:
        ventana_previa = None

    ya_abierto = whatsapp_proceso_activo()
    url_encoded = urllib.parse.quote(mensaje) if mensaje else ""
    uri = f"whatsapp://send?phone={telefono}&text={url_encoded}"

    estado_camuflaje = {}
    stop_camuflaje = threading.Event()

    # 0. Aplicar camuflaje invisible ANTES de abrir el enlace whatsapp://
    for h_ini in _obtener_hwnds_whatsapp_desktop():
        _aplicar_camuflaje_invisible(h_ini, estado_camuflaje)

    def _guardian_invisibilidad():
        conectar_winsta0()
        while not stop_camuflaje.is_set():
            for h_act in _obtener_hwnds_whatsapp_desktop():
                _aplicar_camuflaje_invisible(h_act, estado_camuflaje)
            time.sleep(0.015)

    t_camuflaje = threading.Thread(target=_guardian_invisibilidad, daemon=True)
    t_camuflaje.start()

    try:
        with _EscudoInputFantasma(max_seconds=24.0):
            try:
                os.startfile(uri)
            except Exception:
                subprocess.run(["cmd", "/c", "start", "", uri], creationflags=CREATE_NO_WINDOW)

            # Esperar apertura del chat (y CDP si WhatsApp Desktop estaba cerrándose/arrancando)
            time.sleep(1.5 if ya_abierto else 5.0)
            enfocar_whatsapp_desktop(estado_camuflaje)

            cdp_disponible = _cdp_wsp_activo()
            if not cdp_disponible and not ya_abierto:
                t_cdp = time.time()
                while time.time() - t_cdp < 5.0:
                    if _cdp_wsp_activo():
                        cdp_disponible = True
                        break
                    time.sleep(0.3)

            if cdp_disponible:
                try:
                    from playwright.sync_api import sync_playwright

                    with sync_playwright() as p:
                        _, page = _cdp_obtener_pagina_wsp(p, timeout_ms=10000)
                        if page:
                            page.wait_for_selector("#main footer div[contenteditable='true']", timeout=10000)
                            time.sleep(0.4)

                            if mensaje:
                                _cdp_enviar_texto_footer(page, timeout_s=3.5)
                                time.sleep(0.5)

                            if ruta_archivo:
                                if not poner_archivo_en_portapapeles(ruta_archivo):
                                    raise RuntimeError("No se pudo copiar el archivo PDF al portapapeles de Windows.")
                                time.sleep(0.25)
                                enfocar_whatsapp_desktop(estado_camuflaje)
                                time.sleep(0.25)
                                pulsar_ctrl_v()
                                time.sleep(2.8)
                                # Un solo intento de envío del preview: clic DOM si está visible, o Enter único
                                try:
                                    _cdp_esperar_y_enviar_preview(page, timeout_s=4.5)
                                except Exception:
                                    enfocar_whatsapp_desktop(estado_camuflaje)
                                    pulsar_enter()
                                    time.sleep(1.5)
                        else:
                            cdp_disponible = False
                except Exception:
                    cdp_disponible = False

            if not cdp_disponible:
                # Envío único Win32 (sin CDP 9222)
                time.sleep(1.2 if ya_abierto else 2.2)
                enfocar_whatsapp_desktop(estado_camuflaje)

                if mensaje:
                    pulsar_enter()
                    time.sleep(0.9)

                if ruta_archivo:
                    if not poner_archivo_en_portapapeles(ruta_archivo):
                        raise RuntimeError("No se pudo copiar el archivo PDF al portapapeles de Windows.")
                    time.sleep(0.3)
                    enfocar_whatsapp_desktop(estado_camuflaje)
                    time.sleep(0.25)
                    pulsar_ctrl_v()
                    time.sleep(3.2)
                    enfocar_whatsapp_desktop(estado_camuflaje)
                    pulsar_enter()
                    time.sleep(1.5)
    finally:
        stop_camuflaje.set()
        t_camuflaje.join(timeout=0.5)
        # 3. Minimizar/ocultar WhatsApp mientras sigue invisible y restaurar opacidad 255 + foco a Recepción
        _restaurar_camuflaje_invisible(estado_camuflaje)
        if not _enfocar_ventana_por_filtro(
            lambda t, c: "panel de disponibilidad" in t or "recepción" in t or "ovalo" in t or "óvalo" in t
        ):
            if ventana_previa:
                try:
                    import ctypes
                    ctypes.windll.user32.SetForegroundWindow(ventana_previa)
                except Exception:
                    pass

    return True


def enviar_via_cdp(telefono, mensaje, ruta_archivo=None):
    if _es_entorno_prueba():
        print("🧪 [SIMULACIÓN MODO PRUEBA - NO EMITIDO EN SUNAT / NO ENVIADO POR WSP]", file=sys.stderr)
        return True
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser, page = _cdp_obtener_pagina_wsp(p, timeout_ms=15000)
        if not page:
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()

        url_encoded = urllib.parse.quote(mensaje) if mensaje else ""
        url = f"https://web.whatsapp.com/send?phone={telefono}&text={url_encoded}"
        if "web.whatsapp.com" in (page.url or ""):
            page.evaluate(
                """(targetUrl) => {
                    let a = document.getElementById('__agy_send_chat');
                    if (!a) {
                        a = document.createElement('a');
                        a.id = '__agy_send_chat';
                        document.body.appendChild(a);
                    }
                    a.href = targetUrl;
                    a.click();
                }""",
                url,
            )
            time.sleep(1.2)
        else:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_selector("#main footer div[contenteditable='true']", timeout=30000)
        time.sleep(1.0)

        if ruta_archivo:
            if mensaje:
                _cdp_enviar_texto_footer(page, timeout_s=4.0)
                time.sleep(0.4)
            _cdp_adjuntar_archivo_dom(page, ruta_archivo)
            _cdp_esperar_y_enviar_preview(page, timeout_s=15.0)
        else:
            _cdp_enviar_texto_footer(page, timeout_s=4.0)

        time.sleep(1.5)
        return True


def enviar_via_gui(telefono, mensaje, ruta_archivo=None):
    if _es_entorno_prueba():
        print("🧪 [SIMULACIÓN MODO PRUEBA - NO EMITIDO EN SUNAT / NO ENVIADO POR WSP]", file=sys.stderr)
        return True
    conectar_winsta0()
    url_encoded = urllib.parse.quote(mensaje) if mensaje else ""
    url = f"https://web.whatsapp.com/send?phone={telefono}&text={url_encoded}"

    if ruta_archivo:
        poner_archivo_en_portapapeles(ruta_archivo)

    subprocess.run(["cmd", "/c", "start", "", "msedge", url], creationflags=CREATE_NO_WINDOW)

    time.sleep(4.0)
    enfocar_edge()
    time.sleep(8.0)
    enfocar_edge()

    if ruta_archivo:
        pulsar_ctrl_v()
        time.sleep(3.0)
        pulsar_enter()
        time.sleep(3.0)
        if mensaje:
            try:
                import pyperclip
                pyperclip.copy(mensaje)
                pulsar_ctrl_v()
                time.sleep(1.0)
                pulsar_enter()
                time.sleep(2.0)
            except Exception:
                pass
    else:
        pulsar_enter()
        time.sleep(2.0)

    return True


def enviar_via_headless(telefono, mensaje, ruta_archivo=None):
    if _es_entorno_prueba():
        print("🧪 [SIMULACIÓN MODO PRUEBA - NO EMITIDO EN SUNAT / NO ENVIADO POR WSP]", file=sys.stderr)
        return True
    sesion_dir = r"C:\Hostal_Sistema\sesion_wsp"
    if not os.path.exists(sesion_dir) or not os.listdir(sesion_dir):
        raise RuntimeError(f"No hay sesión headless vinculada en {sesion_dir}")

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=sesion_dir,
            channel="msedge",
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--mute-audio"],
        )
        page = context.pages[0] if context.pages else context.new_page()

        url_encoded = urllib.parse.quote(mensaje) if mensaje else ""
        url = f"https://web.whatsapp.com/send?phone={telefono}&text={url_encoded}"
        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        page.wait_for_selector("#main footer div[contenteditable='true']", timeout=35000)
        time.sleep(1.5)

        if ruta_archivo:
            if mensaje:
                _cdp_enviar_texto_footer(page, timeout_s=4.0)
                time.sleep(0.4)
            _cdp_adjuntar_archivo_dom(page, ruta_archivo)
            _cdp_esperar_y_enviar_preview(page, timeout_s=15.0)
        else:
            _cdp_enviar_texto_footer(page, timeout_s=4.0)

        context.close()
        return True


def main():
    parser = argparse.ArgumentParser(description="Brazo WhatsApp Desktop / Edge (Hostal Sistema)")
    parser.add_argument("--telefono", required=True, help="Número de teléfono de destino")
    parser.add_argument("--mensaje", required=True, help="Texto del mensaje a enviar")
    parser.add_argument("--archivo", required=False, default=None, help="Ruta o nombre del archivo PDF a adjuntar")

    args = parser.parse_args()

    telefono_norm = normalizar_telefono(args.telefono)
    if _es_entorno_prueba():
        aviso_sim = "🧪 [SIMULACIÓN MODO PRUEBA - NO EMITIDO EN SUNAT / NO ENVIADO POR WSP]"
        print(aviso_sim, file=sys.stderr)
        res_sim = {
            "status": "SIMULADO_PRUEBA",
            "simulado": True,
            "aviso": aviso_sim,
            "telefono": telefono_norm,
            "archivo": args.archivo,
            "metodo": "SIMULACION_MODO_PRUEBA",
            "timestamp": datetime.now().isoformat(),
        }
        print(json.dumps(res_sim, ensure_ascii=False))
        sys.exit(0)

    ruta_archivo_final = None

    try:
        if args.archivo:
            ruta_archivo_final = buscar_archivo(args.archivo)
    except Exception as e:
        res_error = {
            "status": "ERROR",
            "telefono": telefono_norm,
            "archivo": args.archivo,
            "error": str(e),
            "timestamp": datetime.now().isoformat(),
        }
        print(json.dumps(res_error, ensure_ascii=False))
        sys.exit(1)

    # UN SOLO INTENTO DETERMINÍSTICO EN WHATSAPP DESKTOP (SIN CASCADA DE REENVÍOS)
    try:
        enviar_via_whatsapp_desktop(telefono_norm, args.mensaje, ruta_archivo_final)
        metodo_usado = "WHATSAPP_DESKTOP_UNICO"
    except Exception as e:
        res_error = {
            "status": "ERROR",
            "telefono": telefono_norm,
            "archivo": ruta_archivo_final,
            "error": str(e),
            "timestamp": datetime.now().isoformat(),
        }
        print(json.dumps(res_error, ensure_ascii=False))
        sys.exit(1)

    res_ok = {
        "status": "ENVIADO",
        "telefono": telefono_norm,
        "archivo": ruta_archivo_final,
        "metodo": metodo_usado,
        "timestamp": datetime.now().isoformat(),
    }
    print(json.dumps(res_ok, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    main()
