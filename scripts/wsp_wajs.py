#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MOTOR WHATSAPP BUSINESS CON WA-JS (@wppconnect-team/wa-js) + PLAYWRIGHT + MICROSOFT EDGE
Hostal El Óvalo Mariátegui - C:\\Hostal_Sistema\\scripts\\wsp_wajs.py

Capacidades nativas mediante inyección de wppconnect-wa.js (window.WPP):
  1. enviar          : Envía mensajes de texto, PDFs (boletas/facturas), imágenes o videos sin recargar la web.
  2. leer            : Lee mensajes recibidos de un número o lista chats recientes / no leídos (y descarga vouchers/PDFs).
  3. contactos       : Extrae todos los contactos y personas que nos escribieron y los guarda en Excel (.xlsx) y JSON,
                       o agenda un contacto nuevo.
  4. subir-estado    : Publica estados (historias de 24h) de imagen, video o texto en WhatsApp Business.
  5. etiquetas       : Lista o asigna etiquetas de WhatsApp Business.
  6. vincular        : Abre Microsoft Edge con el perfil persistente y puerto CDP 9222 para vincular QR.
  7. estado-conexion : Verifica si WhatsApp Business y WA-JS están listos.
"""

import os
import sys
import time
import json
import base64
import mimetypes
import argparse
import subprocess
import urllib.request
from datetime import datetime

try:
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr is not None:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = r"C:\Hostal_Sistema"
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

_WAJS_LOCAL = os.path.join(SCRIPTS_DIR, "wppconnect-wa.js")
_WAJS_FALLBACK = os.path.join(BASE_DIR, "scripts", "wppconnect-wa.js")
WAJS_PATH = _WAJS_LOCAL if os.path.isfile(_WAJS_LOCAL) else _WAJS_FALLBACK
SESION_DIR = os.path.join(BASE_DIR, "sesion_wsp")
DESCARGAS_WSP_DIR = os.path.join(BASE_DIR, "descargas_wsp")
from secretos_hostal import obtener_ruta_credentials_google as _obtener_ruta_credentials_google
RUTA_CREDS = _obtener_ruta_credentials_google()
try:
    from hoja_activa import obtener_spreadsheet_id as _obtener_spreadsheet_id
    SPREADSHEET_ID = _obtener_spreadsheet_id()
except ImportError:
    if os.environ.get("OVALO_ENTORNO", "operativa").strip().lower() != "operativa":
        raise SystemExit("⛔ ERROR CRÍTICO: No se pudo importar hoja_activa en modo PRUEBA.")
    print("[WARN] hoja_activa no disponible; usando Hoja Operativa por defecto.", file=sys.stderr)
    SPREADSHEET_ID = "1KudqxM1D6MSCxT5Zb2_vAOTGxnw9Ja92JuFjFpDuxZ8"
HOJA_CONTACTOS_DEFAULT = "CONTACTOS_WSP"
CDP_PORT = 9222
CDP_URL = f"http://localhost:{CDP_PORT}"

os.makedirs(DESCARGAS_WSP_DIR, exist_ok=True)


def normalizar_telefono(telefono: str) -> str:
    """Convierte un número a formato internacional peruano (51XXXXXXXXX) sin símbolos."""
    tel = "".join(ch for ch in str(telefono) if ch.isdigit())
    if len(tel) == 9:
        tel = "51" + tel
    return tel


def a_chat_id(telefono: str) -> str:
    """Convierte un teléfono a formato WID de WhatsApp (ej. 51999888777@c.us)."""
    if "@" in str(telefono):
        return str(telefono).strip()
    return f"{normalizar_telefono(telefono)}@c.us"


def buscar_archivo_local(archivo: str) -> str:
    """Busca un archivo PDF/imagen en las carpetas habituales del sistema."""
    if not archivo:
        return None
    if os.path.isabs(archivo) and os.path.isfile(archivo):
        return os.path.abspath(archivo)

    user_profile = os.environ.get("USERPROFILE", "")
    candidatos = [
        archivo,
        os.path.join(user_profile, "Downloads", archivo),
        os.path.join(BASE_DIR, archivo),
        os.path.join(SCRIPTS_DIR, archivo),
        os.path.join(BASE_DIR, "descargas_recepcion", archivo),
    ]
    for c in candidatos:
        if os.path.isfile(c):
            return os.path.abspath(c)

    import glob
    nombre = os.path.basename(archivo)
    for patron in [
        os.path.join(user_profile, "Downloads", f"*{nombre}*"),
        os.path.join(BASE_DIR, "**", f"*{nombre}*"),
    ]:
        matches = glob.glob(patron, recursive=True)
        if matches:
            return os.path.abspath(matches[0])

    raise FileNotFoundError(f"No se encontró el archivo '{archivo}'.")


def archivo_a_data_url(ruta_archivo: str):
    """Convierte un archivo local en un string Data URL Base64 listo para WPP.chat.sendFileMessage."""
    ruta_abs = buscar_archivo_local(ruta_archivo)
    mime_type, _ = mimetypes.guess_type(ruta_abs)
    if not mime_type:
        ext = os.path.splitext(ruta_abs)[1].lower()
        mapa = {
            ".pdf": "application/pdf",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".mp4": "video/mp4",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
        mime_type = mapa.get(ext, "application/octet-stream")

    with open(ruta_abs, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")

    return f"data:{mime_type};base64,{b64}", os.path.basename(ruta_abs), mime_type, ruta_abs


def cdp_esta_activo(port: int = CDP_PORT) -> bool:
    """Verifica rápidamente si Microsoft Edge está escuchando en el puerto CDP."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=1.5) as resp:
            return resp.status == 200
    except Exception:
        return False


def encontrar_msedge_exe() -> str:
    """Ubica el ejecutable de Microsoft Edge en Windows."""
    rutas = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for r in rutas:
        if os.path.isfile(r):
            return r
    return "msedge"


def iniciar_edge_persistente_cdp(minimizado: bool = True) -> bool:
    """
    Inicia una instancia de Microsoft Edge con el perfil C:\\Hostal_Sistema\\sesion_wsp
    y el puerto de depuración 9222 abierto para que WA-JS funcione al instante.
    """
    if cdp_esta_activo(CDP_PORT):
        return True

    os.makedirs(SESION_DIR, exist_ok=True)
    msedge_bin = encontrar_msedge_exe()

    args = [
        msedge_bin,
        f"--user-data-dir={SESION_DIR}",
        f"--remote-debugging-port={CDP_PORT}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
    ]
    if minimizado:
        args.append("--start-minimized")
    else:
        args.append("--start-maximized")
    args.append("https://web.whatsapp.com")

    subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
    )

    for _ in range(25):
        if cdp_esta_activo(CDP_PORT):
            return True
        time.sleep(0.4)
    return False


def inyectar_wajs(page, timeout_ready_s: int = 45):
    """
    Inyecta wppconnect-wa.js en la pestaña de WhatsApp Web y espera a que
    window.WPP.isFullReady o window.WPP.isReady esté activo.
    """
    if not os.path.isfile(WAJS_PATH):
        raise FileNotFoundError(f"No se encontró wppconnect-wa.js en {WAJS_PATH}")

    # Esperar a que la página esté en web.whatsapp.com
    if "web.whatsapp.com" not in page.url:
        page.goto("https://web.whatsapp.com", wait_until="domcontentloaded", timeout=60000)

    # Verificar si ya está inyectado
    ya_inyectado = False
    try:
        ya_inyectado = bool(page.evaluate("() => Boolean(window.WPP && window.WPP.isInjected)"))
    except Exception:
        ya_inyectado = False

    if not ya_inyectado:
        try:
            cdp = page.context.new_cdp_session(page)
            try:
                cdp.send("Page.setBypassCSP", {"enabled": True})
                with open(WAJS_PATH, "r", encoding="utf-8") as f_wa:
                    cdp.send("Runtime.evaluate", {"expression": f_wa.read(), "awaitPromise": False})
            finally:
                try:
                    cdp.detach()
                except Exception:
                    pass
        except Exception:
            page.add_script_tag(path=WAJS_PATH)

    # Esperar a que WhatsApp Web haya iniciado sesión y WPP esté listo
    inicio = time.time()
    while time.time() - inicio < timeout_ready_s:
        try:
            estado = page.evaluate(
                """() => {
                    const qr = document.querySelector('canvas[aria-label*="Scan"], canvas[aria-label*="Escan"], div[data-ref]');
                    const wppReady = Boolean(window.WPP && (window.WPP.isFullReady || window.WPP.isReady));
                    const chatStoreReady = Boolean(window.WPP && window.WPP.whatsapp && window.WPP.whatsapp.ChatStore);
                    return {
                        wppReady: wppReady && chatStoreReady,
                        needsQr: Boolean(qr && !wppReady)
                    };
                }"""
            )
            if estado.get("wppReady"):
                return True
            if estado.get("needsQr") and (time.time() - inicio > 8):
                raise RuntimeError(
                    "WhatsApp Web solicita escanear el código QR. "
                    f"Ejecuta: python {os.path.abspath(__file__)} vincular"
                )
        except RuntimeError:
            raise
        except Exception:
            pass
        time.sleep(1.0)

    raise TimeoutError(
        "Tiempo de espera agotado esperando que WhatsApp Web (WPP) esté listo. "
        f"Verifica que la sesión esté vinculada con: python {os.path.abspath(__file__)} vincular"
    )


class ConectorWAJS:
    """Context manager que conecta a Microsoft Edge por CDP (o lo inicia) e inyecta WA-JS."""

    def __init__(self, visible_si_inicia: bool = False, timeout_ready_s: int = 45):
        self.visible_si_inicia = visible_si_inicia
        self.timeout_ready_s = timeout_ready_s
        self.pw = None
        self.browser = None
        self.page = None

    def __enter__(self):
        from playwright.sync_api import sync_playwright

        if not cdp_esta_activo(CDP_PORT):
            iniciar_edge_persistente_cdp(minimizado=not self.visible_si_inicia)

        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.connect_over_cdp(CDP_URL, timeout=15000)
        context = self.browser.contexts[0] if self.browser.contexts else self.browser.new_context()

        for p_tab in context.pages:
            if "web.whatsapp.com" in p_tab.url:
                self.page = p_tab
                break

        if not self.page:
            self.page = context.pages[0] if context.pages else context.new_page()
            self.page.goto("https://web.whatsapp.com", wait_until="domcontentloaded", timeout=60000)

        inyectar_wajs(self.page, timeout_ready_s=self.timeout_ready_s)
        return self.page

    def __exit__(self, exc_type, exc_val, exc_tb):
        # Con connect_over_cdp cerramos solo el cliente Playwright sin cerrar el navegador Edge
        try:
            if self.pw:
                self.pw.stop()
        except Exception:
            pass


# ==============================================================================
# FUNCIONES DE NEGOCIO (ENVIAR, LEER, CONTACTOS, ESTADOS, ETIQUETAS)
# ==============================================================================

def enviar_mensaje_wajs(telefono: str, mensaje: str = "", ruta_archivo: str = None) -> dict:
    """
    Envía un mensaje de texto y/o un archivo (PDF, imagen, video) usando WPP.chat.
    No recarga la página y funciona incluso con números no guardados en la agenda.
    """
    try:
        from hoja_activa import es_prueba as _es_prueba_ha
        _en_prueba = bool(_es_prueba_ha())
    except ImportError:
        _en_prueba = os.environ.get("OVALO_ENTORNO", "operativa").strip().lower() != "operativa"

    if _en_prueba:
        aviso_sim = "🧪 [SIMULACIÓN MODO PRUEBA - NO EMITIDO EN SUNAT / NO ENVIADO POR WSP]"
        print(aviso_sim, file=sys.stderr)
        return {
            "status": "SIMULADO_PRUEBA",
            "simulado": True,
            "aviso": aviso_sim,
            "metodo": "WA-JS-SIMULADO",
            "telefono": normalizar_telefono(telefono),
            "archivo": ruta_archivo,
            "mensaje_id": "SIMULADO_PRUEBA",
            "timestamp": datetime.now().isoformat(),
        }

    chat_id = a_chat_id(telefono)
    telefono_norm = normalizar_telefono(telefono)

    data_url = None
    nombre_archivo = None
    mime_type = None
    ruta_abs = None

    if ruta_archivo:
        data_url, nombre_archivo, mime_type, ruta_abs = archivo_a_data_url(ruta_archivo)

    with ConectorWAJS() as page:
        if data_url:
            tipo_envio = "document"
            if mime_type.startswith("image/"):
                tipo_envio = "image"
            elif mime_type.startswith("video/"):
                tipo_envio = "video"

            res = page.evaluate(
                """async ({ chatId, dataUrl, filename, caption, type, mimetype }) => {
                    const result = await window.WPP.chat.sendFileMessage(chatId, dataUrl, {
                        type: type,
                        filename: filename,
                        caption: caption || '',
                        mimetype: mimetype,
                        createChat: true,
                        waitForAck: true
                    });
                    return {
                        id: result && result.id ? result.id.toString() : null,
                        ack: result ? result.ack : null
                    };
                }""",
                {
                    "chatId": chat_id,
                    "dataUrl": data_url,
                    "filename": nombre_archivo,
                    "caption": mensaje or "",
                    "type": tipo_envio,
                    "mimetype": mime_type,
                },
            )
        else:
            res = page.evaluate(
                """async ({ chatId, texto }) => {
                    const result = await window.WPP.chat.sendTextMessage(chatId, texto, {
                        createChat: true,
                        waitForAck: true
                    });
                    return {
                        id: result && result.id ? result.id.toString() : null,
                        ack: result ? result.ack : null
                    };
                }""",
                {"chatId": chat_id, "texto": mensaje},
            )

    if not isinstance(res, dict) or not res.get("id"):
        raise RuntimeError(f"WA-JS no confirmó creación del mensaje para {chat_id}: {res}")
    if res.get("ack") is not None and int(res["ack"]) < 0:
        raise RuntimeError(f"WA-JS devolvió ACK negativo ({res.get('ack')}) para {chat_id}")

    return {
        "status": "ENVIADO",
        "metodo": "WA-JS",
        "telefono": telefono_norm,
        "chat_id": chat_id,
        "archivo": ruta_abs,
        "mensaje_id": res.get("id") if isinstance(res, dict) else None,
        "timestamp": datetime.now().isoformat(),
    }


def leer_mensajes_wajs(
    telefono: str = None,
    cantidad: int = 20,
    solo_no_leidos: bool = False,
    descargar_media: bool = False,
) -> dict:
    """
    Lee los mensajes de un contacto específico o lista los chats recientes / no leídos.
    Opcionalmente descarga imágenes/PDFs recibidos (ej. capturas de Yape o facturas).
    """
    with ConectorWAJS() as page:
        if telefono:
            chat_id = a_chat_id(telefono)
            mensajes = page.evaluate(
                """async ({ chatId, count, onlyUnread, downloadMedia }) => {
                    const msgs = await window.WPP.chat.getMessages(chatId, {
                        count: count,
                        onlyUnread: onlyUnread
                    });
                    const salida = [];
                    for (const m of msgs) {
                        let mediaBase64 = null;
                        const isMedia = Boolean(m.isMedia || m.mimetype || ['image', 'document', 'ptt', 'audio', 'video'].includes(m.type));
                        if (downloadMedia && isMedia) {
                            try {
                                const blob = await window.WPP.chat.downloadMedia(m.id.toString());
                                mediaBase64 = await window.WPP.util.blobToBase64(blob);
                            } catch (e) {}
                        }
                        salida.push({
                            id: m.id ? m.id.toString() : '',
                            fromMe: Boolean(m.id && m.id.fromMe),
                            timestamp: m.t || 0,
                            fecha: m.t ? new Date(m.t * 1000).toLocaleString('es-PE') : '',
                            type: m.type || 'chat',
                            body: m.body || m.caption || '',
                            filename: m.filename || '',
                            mimetype: m.mimetype || '',
                            senderName: m.notifyName || '',
                            mediaBase64: mediaBase64
                        });
                    }
                    return salida;
                }""",
                {
                    "chatId": chat_id,
                    "count": cantidad,
                    "onlyUnread": solo_no_leidos,
                    "downloadMedia": descargar_media,
                },
            )

            # Si se descargó media, guardarla en disco
            for m in mensajes:
                b64_data = m.pop("mediaBase64", None)
                if b64_data and "," in b64_data:
                    _, raw_b64 = b64_data.split(",", 1)
                    ext = mimetypes.guess_extension(m.get("mimetype") or "") or ".bin"
                    nombre_seguro = m.get("filename") or f"media_{m['timestamp']}{ext}"
                    nombre_seguro = "".join(c for c in nombre_seguro if c not in r'\/:*?"<>|')
                    ruta_guardado = os.path.join(DESCARGAS_WSP_DIR, nombre_seguro)
                    with open(ruta_guardado, "wb") as f:
                        f.write(base64.b64decode(raw_b64))
                    m["archivo_descargado"] = ruta_guardado

            return {
                "status": "OK",
                "modo": "CHAT_INDIVIDUAL",
                "chat_id": chat_id,
                "total_mensajes": len(mensajes),
                "mensajes": mensajes,
            }

        else:
            chats = page.evaluate(
                """async ({ count, onlyUnread }) => {
                    const lista = await window.WPP.chat.list({
                        onlyUsers: true
                    });
                    const filtrados = onlyUnread ? lista.filter(c => c.unreadCount > 0) : lista;
                    const top = filtrados.slice(0, count);
                    return top.map(c => {
                        const lastMsg = c.msgs && c.msgs.length > 0 ? c.msgs.last() : null;
                        const wid = c.id ? c.id._serialized || c.id.toString() : '';
                        const tel = c.id && c.id.user ? c.id.user : wid.replace('@c.us', '');
                        return {
                            telefono: tel,
                            chat_id: wid,
                            nombre: c.name || c.formattedTitle || (c.contact && (c.contact.name || c.contact.pushname)) || '',
                            pushname: (c.contact && c.contact.pushname) || '',
                            no_leidos: c.unreadCount || 0,
                            timestamp: c.t || 0,
                            fecha_ultimo: c.t ? new Date(c.t * 1000).toLocaleString('es-PE') : '',
                            ultimo_mensaje: lastMsg ? (lastMsg.body || lastMsg.caption || `[${lastMsg.type}]`) : ''
                        };
                    });
                }""",
                {"count": cantidad, "onlyUnread": solo_no_leidos},
            )
            return {
                "status": "OK",
                "modo": "LISTA_CHATS",
                "solo_no_leidos": solo_no_leidos,
                "total_chats": len(chats),
                "chats": chats,
            }


def sincronizar_contactos_hoja_operativa(datos: list, nombre_hoja: str = HOJA_CONTACTOS_DEFAULT) -> dict:
    """
    Cruza los contactos de WhatsApp con la hoja 'Registro_Estancias' (columna CELULAR)
    y guarda/actualiza todo directamente en la Hoja Operativa de Google Sheets
    (2026_Hostal Óvalo Mariategui).
    """
    import socket
    import urllib3.util.connection as urllib3_cn
    urllib3_cn.allowed_gai_family = lambda: socket.AF_INET
    import gspread
    from google.oauth2.service_account import Credentials

    creds = Credentials.from_service_account_file(
        RUTA_CREDS, scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)

    # 1. Leer 'Registro_Estancias' para cruzar teléfonos con huéspedes registrados
    mapa_huespedes = {}
    try:
        ws_est = sh.worksheet("Registro_Estancias")
        filas_est = ws_est.get_all_values()
        if filas_est:
            headers = [h.strip() for h in filas_est[0]]
            idx_cel = headers.index("CELULAR") if "CELULAR" in headers else -1
            idx_nom = headers.index("Nombres_Huesped") if "Nombres_Huesped" in headers else -1
            idx_dni = headers.index("DNI_Huesped") if "DNI_Huesped" in headers else -1
            idx_emp = headers.index("EMPRESA") if "EMPRESA" in headers else -1
            idx_ruc = headers.index("RUC") if "RUC" in headers else -1

            if idx_cel >= 0:
                for row in filas_est[1:]:
                    if len(row) > idx_cel and row[idx_cel].strip():
                        cel_limpio = normalizar_telefono(row[idx_cel])
                        if len(cel_limpio) >= 9:
                            cel_9 = cel_limpio[-9:]
                            mapa_huespedes[cel_9] = {
                                "huesped": row[idx_nom].strip() if idx_nom >= 0 and len(row) > idx_nom else "",
                                "dni": row[idx_dni].strip() if idx_dni >= 0 and len(row) > idx_dni else "",
                                "empresa": row[idx_emp].strip() if idx_emp >= 0 and len(row) > idx_emp else "",
                                "ruc": row[idx_ruc].strip() if idx_ruc >= 0 and len(row) > idx_ruc else "",
                            }
    except Exception:
        pass

    # 2. Preparar filas para la Hoja Operativa
    encabezados = [
        "CELULAR",
        "TELEFONO_WSP",
        "NOMBRE_MOSTRAR_WSP",
        "NOMBRE_PERFIL_WSP",
        "NOMBRE_AGENDA",
        "HUESPED_REGISTRADO (Registro_Estancias)",
        "DNI_HUESPED",
        "EMPRESA",
        "RUC",
        "ETIQUETAS_BUSINESS",
        "TIENE_CHAT",
        "SIN_LEER",
        "FECHA_ULTIMO_MENSAJE",
        "ULTIMO_MENSAJE",
    ]

    filas_salida = [encabezados]
    cruzados_estancias = 0

    for d in datos:
        tel_full = str(d.get("telefono", ""))
        tel_9 = tel_full[-9:] if len(tel_full) >= 9 else tel_full
        info_h = mapa_huespedes.get(tel_9, {})
        if info_h:
            cruzados_estancias += 1

        filas_salida.append([
            tel_9,
            tel_full,
            d.get("nombre_mostrar", ""),
            d.get("nombre_perfil_wsp", ""),
            d.get("nombre_agenda", ""),
            info_h.get("huesped", ""),
            info_h.get("dni", ""),
            info_h.get("empresa", ""),
            info_h.get("ruc", ""),
            d.get("etiquetas_business", ""),
            "SÍ" if d.get("tiene_chat") else "NO",
            d.get("mensajes_sin_leer", 0),
            d.get("fecha_ultimo_mensaje", ""),
            str(d.get("ultimo_mensaje", ""))[:300],
        ])

    # 3. Crear o limpiar la pestaña en la Hoja Operativa y subir de golpe
    try:
        ws_cont = sh.worksheet(nombre_hoja)
    except gspread.exceptions.WorksheetNotFound:
        ws_cont = sh.add_worksheet(title=nombre_hoja, rows=max(len(filas_salida) + 50, 500), cols=len(encabezados))

    ws_cont.clear()
    ws_cont.update(values=filas_salida, range_name="A1")

    return {
        "spreadsheet_titulo": sh.title,
        "hoja_actualizada": nombre_hoja,
        "filas_escritas": len(filas_salida) - 1,
        "coincidencias_registro_estancias": cruzados_estancias,
    }


def extraer_contactos_wajs(nombre_hoja: str = HOJA_CONTACTOS_DEFAULT) -> dict:
    """
    Extrae todos los chats y contactos que han escrito al WhatsApp Business
    y los sincroniza directamente en la Hoja Operativa de Google Sheets
    (2026_Hostal Óvalo Mariategui), cruzándolos con 'Registro_Estancias'.
    """
    with ConectorWAJS() as page:
        datos = page.evaluate(
            """async () => {
                const chats = await window.WPP.chat.list({ onlyUsers: true });
                const contacts = await window.WPP.contact.list();
                const labelsMap = {};
                try {
                    const labels = await window.WPP.labels.getAllLabels();
                    for (const lb of labels) {
                        labelsMap[lb.id] = lb.name;
                    }
                } catch (e) {}

                const mapa = new Map();

                // 1. Primero registrar todos los que tienen un chat abierto (nos escribieron o les escribimos)
                for (const c of chats) {
                    const wid = c.id ? (c.id._serialized || c.id.toString()) : '';
                    if (!wid || wid.includes('@g.us') || wid.includes('status')) continue;
                    const user = (c.id && c.id.user) ? c.id.user : wid.replace(/@.*$/, '');
                    const lastMsg = c.msgs && c.msgs.length > 0 ? c.msgs.last() : null;
                    const rawLabels = c.labels || (c.contact && c.contact.labels) || [];
                    const nombresEtiquetas = rawLabels.map(id => labelsMap[id] || id).join(', ');

                    mapa.set(wid, {
                        telefono: user,
                        chat_id: wid,
                        nombre_agenda: (c.contact && c.contact.name) || c.name || '',
                        nombre_perfil_wsp: (c.contact && c.contact.pushname) || '',
                        nombre_mostrar: c.formattedTitle || c.name || (c.contact && c.contact.pushname) || user,
                        es_mi_contacto: Boolean(c.contact && c.contact.isMyContact),
                        es_empresa: Boolean(c.contact && c.contact.isBusiness),
                        tiene_chat: true,
                        mensajes_sin_leer: c.unreadCount || 0,
                        etiquetas_business: nombresEtiquetas,
                        fecha_ultimo_mensaje: c.t ? new Date(c.t * 1000).toLocaleString('es-PE') : '',
                        timestamp_ultimo: c.t || 0,
                        ultimo_mensaje: lastMsg ? (lastMsg.body || lastMsg.caption || `[${lastMsg.type}]`) : ''
                    });
                }

                // 2. Agregar contactos de la agenda que aún no estén en el mapa de chats
                for (const ct of contacts) {
                    const wid = ct.id ? (ct.id._serialized || ct.id.toString()) : '';
                    if (!wid || !wid.endsWith('@c.us')) continue;
                    if (!mapa.has(wid) && (ct.isMyContact || ct.pushname || ct.name)) {
                        const user = (ct.id && ct.id.user) ? ct.id.user : wid.replace('@c.us', '');
                        mapa.set(wid, {
                            telefono: user,
                            chat_id: wid,
                            nombre_agenda: ct.name || '',
                            nombre_perfil_wsp: ct.pushname || '',
                            nombre_mostrar: ct.name || ct.pushname || user,
                            es_mi_contacto: Boolean(ct.isMyContact),
                            es_empresa: Boolean(ct.isBusiness),
                            tiene_chat: false,
                            mensajes_sin_leer: 0,
                            etiquetas_business: '',
                            fecha_ultimo_mensaje: '',
                            timestamp_ultimo: 0,
                            ultimo_mensaje: ''
                        });
                    }
                }

                return Array.from(mapa.values()).sort((a, b) => (b.timestamp_ultimo || 0) - (a.timestamp_ultimo || 0));
            }"""
        )

    info_gsheets = sincronizar_contactos_hoja_operativa(datos, nombre_hoja=nombre_hoja or HOJA_CONTACTOS_DEFAULT)

    con_chat = sum(1 for d in datos if d.get("tiene_chat"))
    return {
        "status": "OK",
        "total_registros": len(datos),
        "total_con_chat": con_chat,
        "hoja_operativa": info_gsheets,
        "muestra_recientes": datos[:10],
    }


def guardar_contacto_agenda_wajs(telefono: str, nombre: str, apellido: str = "") -> dict:
    """Guarda o actualiza un contacto directamente en la agenda de WhatsApp."""
    chat_id = a_chat_id(telefono)
    with ConectorWAJS() as page:
        res = page.evaluate(
            """async ({ chatId, nombre, apellido }) => {
                return await window.WPP.contact.save(chatId, nombre, {
                    surname: apellido || '',
                    syncAdressBook: true
                });
            }""",
            {"chatId": chat_id, "nombre": nombre, "apellido": apellido},
        )
    return {
        "status": "OK",
        "accion": "CONTACTO_GUARDADO",
        "chat_id": chat_id,
        "nombre": f"{nombre} {apellido}".strip(),
        "resultado": res,
    }


def subir_estado_wajs(
    texto: str = "",
    ruta_imagen: str = None,
    ruta_video: str = None,
    color_fondo: str = "#0b3d91",
    fuente: int = 1,
) -> dict:
    """
    Publica un Estado (Historia de 24h) en WhatsApp Business:
      - Imagen con pie de foto (WPP.status.sendImageStatus)
      - Video con pie de foto (WPP.status.sendVideoStatus)
      - Texto con fondo de color (WPP.status.sendTextStatus)
    """
    with ConectorWAJS() as page:
        if ruta_imagen:
            data_url, nombre, _, ruta_abs = archivo_a_data_url(ruta_imagen)
            res = page.evaluate(
                """async ({ dataUrl, caption }) => {
                    const r = await window.WPP.status.sendImageStatus(dataUrl, {
                        caption: caption || '',
                        waitForAck: true
                    });
                    return { id: r && r.id ? r.id.toString() : 'ok' };
                }""",
                {"dataUrl": data_url, "caption": texto or ""},
            )
            return {
                "status": "ESTADO_PUBLICADO",
                "tipo": "IMAGEN",
                "archivo": ruta_abs,
                "texto": texto,
                "resultado": res,
                "timestamp": datetime.now().isoformat(),
            }

        elif ruta_video:
            data_url, nombre, _, ruta_abs = archivo_a_data_url(ruta_video)
            res = page.evaluate(
                """async ({ dataUrl, caption }) => {
                    const r = await window.WPP.status.sendVideoStatus(dataUrl, {
                        caption: caption || '',
                        waitForAck: true
                    });
                    return { id: r && r.id ? r.id.toString() : 'ok' };
                }""",
                {"dataUrl": data_url, "caption": texto or ""},
            )
            return {
                "status": "ESTADO_PUBLICADO",
                "tipo": "VIDEO",
                "archivo": ruta_abs,
                "texto": texto,
                "resultado": res,
                "timestamp": datetime.now().isoformat(),
            }

        elif texto:
            res = page.evaluate(
                """async ({ texto, backgroundColor, font }) => {
                    const r = await window.WPP.status.sendTextStatus(texto, {
                        backgroundColor: backgroundColor || '#0b3d91',
                        font: font || 1,
                        waitForAck: true
                    });
                    return { id: r && r.id ? r.id.toString() : 'ok' };
                }""",
                {"texto": texto, "backgroundColor": color_fondo, "font": fuente},
            )
            return {
                "status": "ESTADO_PUBLICADO",
                "tipo": "TEXTO",
                "texto": texto,
                "color_fondo": color_fondo,
                "resultado": res,
                "timestamp": datetime.now().isoformat(),
            }
        else:
            raise ValueError("Debes indicar --texto, --imagen o --video para subir un estado.")


def gestionar_etiquetas_wajs(telefono: str = None, etiqueta_id: str = None, accion: str = "add") -> dict:
    """Lista las etiquetas de WhatsApp Business o asigna/quita una etiqueta a un chat."""
    with ConectorWAJS() as page:
        if not telefono:
            etiquetas = page.evaluate("async () => await window.WPP.labels.getAllLabels()")
            return {"status": "OK", "etiquetas": etiquetas}
        else:
            chat_id = a_chat_id(telefono)
            res = page.evaluate(
                """async ({ chatId, labelId, type }) => {
                    return await window.WPP.labels.addOrRemoveLabels(
                        [chatId],
                        [{ labelId: String(labelId), type: type }]
                    );
                }""",
                {"chatId": chat_id, "labelId": etiqueta_id, "type": accion},
            )
            return {"status": "OK", "chat_id": chat_id, "etiqueta_id": etiqueta_id, "accion": accion, "resultado": res}


def verificar_estado_conexion() -> dict:
    """Verifica el estado de la conexión de WhatsApp Business y la versión de WA-JS."""
    with ConectorWAJS(visible_si_inicia=True, timeout_ready_s=60) as page:
        info = page.evaluate(
            """async () => {
                const wid = window.WPP.conn && window.WPP.conn.getMyUserWid ? window.WPP.conn.getMyUserWid() : null;
                const chats = await window.WPP.chat.list({ onlyUsers: true });
                const unread = chats.filter(c => c.unreadCount > 0).length;
                return {
                    wppVersion: window.WPP.version || '',
                    isFullReady: Boolean(window.WPP.isFullReady),
                    miNumero: wid ? (wid.user || wid.toString()) : '',
                    totalChatsUsuarios: chats.length,
                    chatsConMensajesSinLeer: unread
                };
            }"""
        )
    return {"status": "CONECTADO", **info}


def comando_vincular():
    """
    Abre Microsoft Edge en modo visible con el perfil persistente y puerto CDP 9222,
    espera a que el usuario escanee el QR (si aún no está vinculado) e inyecta WA-JS.
    """
    print("=" * 68)
    print("VINCULACIÓN Y ACTIVACIÓN DE WHATSAPP BUSINESS (WA-JS + MICROSOFT EDGE)")
    print("=" * 68)
    iniciar_edge_persistente_cdp(minimizado=False)
    print(f"[1/2] Microsoft Edge iniciado con sesión en: {SESION_DIR} (Puerto CDP: {CDP_PORT})")
    print("[2/2] Si es la primera vez, escanea el código QR en la ventana de Microsoft Edge...")

    with ConectorWAJS(visible_si_inicia=True, timeout_ready_s=180) as page:
        info = page.evaluate(
            """async () => {
                const wid = window.WPP.conn && window.WPP.conn.getMyUserWid ? window.WPP.conn.getMyUserWid() : null;
                const chats = await window.WPP.chat.list({ onlyUsers: true });
                return {
                    wppVersion: window.WPP.version || '',
                    miNumero: wid ? (wid.user || wid.toString()) : '',
                    totalChats: chats.length
                };
            }"""
        )
    print("\n[OK] ¡WhatsApp Business y WA-JS están listos y conectados!")
    print(json.dumps(info, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description="Gestor integral de WhatsApp Business con WA-JS + Playwright + Microsoft Edge"
    )
    sub = parser.add_subparsers(dest="comando", required=True)

    # 1. vincular
    sub.add_parser("vincular", help="Abre Microsoft Edge y vincula/verifica la sesión con WA-JS")

    # 2. estado-conexion
    sub.add_parser("estado-conexion", help="Muestra el estado actual de la conexión y versión de WA-JS")

    # 3. enviar
    p_env = sub.add_parser("enviar", help="Envía texto o archivos PDF/imagen por WhatsApp Business")
    p_env.add_argument("--telefono", required=True, help="Número de teléfono del destinatario")
    p_env.add_argument("--mensaje", default="", help="Texto o pie de foto del mensaje")
    p_env.add_argument("--archivo", default=None, help="Ruta o nombre del archivo PDF/imagen a adjuntar")

    # 4. leer
    p_leer = sub.add_parser("leer", help="Lee mensajes de un contacto o lista los últimos chats/no leídos")
    p_leer.add_argument("--telefono", default=None, help="Teléfono del chat a consultar (opcional)")
    p_leer.add_argument("--cantidad", type=int, default=20, help="Cantidad de mensajes o chats a mostrar")
    p_leer.add_argument("--solo-no-leidos", action="store_true", help="Mostrar únicamente mensajes/chats no leídos")
    p_leer.add_argument("--descargar-media", action="store_true", help="Descargar imágenes/PDFs recibidos en el chat")

    # 5. contactos
    p_cont = sub.add_parser("contactos", help="Sincroniza todos los contactos que escribieron directo a la Hoja Operativa")
    p_cont.add_argument("--hoja", default=HOJA_CONTACTOS_DEFAULT, help="Nombre de la pestaña en la Hoja Operativa (default: CONTACTOS_WSP)")
    p_cont.add_argument("--guardar-numero", default=None, help="Número a guardar en la agenda")
    p_cont.add_argument("--guardar-nombre", default=None, help="Nombre para agendar el contacto")
    p_cont.add_argument("--guardar-apellido", default="", help="Apellido opcional")

    # 6. subir-estado
    p_est = sub.add_parser("subir-estado", help="Publica un estado (historia) de imagen, video o texto")
    p_est.add_argument("--texto", default="", help="Texto del estado o pie de foto")
    p_est.add_argument("--imagen", default=None, help="Ruta de la imagen para el estado")
    p_est.add_argument("--video", default=None, help="Ruta del video para el estado")
    p_est.add_argument("--color", default="#0b3d91", help="Color de fondo si es estado de solo texto")

    # 7. etiquetas
    p_eti = sub.add_parser("etiquetas", help="Lista o asigna etiquetas de WhatsApp Business")
    p_eti.add_argument("--telefono", default=None, help="Teléfono al cual asignar/quitar etiqueta")
    p_eti.add_argument("--etiqueta-id", default=None, help="ID de la etiqueta")
    p_eti.add_argument("--accion", choices=["add", "remove"], default="add", help="Agregar o quitar etiqueta")

    args = parser.parse_args()

    try:
        if args.comando == "vincular":
            comando_vincular()
            return
        elif args.comando == "estado-conexion":
            res = verificar_estado_conexion()
        elif args.comando == "enviar":
            res = enviar_mensaje_wajs(args.telefono, args.mensaje, args.archivo)
        elif args.comando == "leer":
            res = leer_mensajes_wajs(
                telefono=args.telefono,
                cantidad=args.cantidad,
                solo_no_leidos=args.solo_no_leidos,
                descargar_media=args.descargar_media,
            )
        elif args.comando == "contactos":
            if args.guardar_numero and args.guardar_nombre:
                res = guardar_contacto_agenda_wajs(args.guardar_numero, args.guardar_nombre, args.guardar_apellido)
            else:
                res = extraer_contactos_wajs(args.hoja)
        elif args.comando == "subir-estado":
            res = subir_estado_wajs(
                texto=args.texto,
                ruta_imagen=args.imagen,
                ruta_video=args.video,
                color_fondo=args.color,
            )
        elif args.comando == "etiquetas":
            res = gestionar_etiquetas_wajs(args.telefono, args.etiqueta_id, args.accion)
        else:
            raise ValueError(f"Comando no soportado: {args.comando}")

        print(json.dumps(res, ensure_ascii=False, indent=2))
        sys.exit(0)

    except Exception as e:
        err = {
            "status": "ERROR",
            "comando": args.comando,
            "error": str(e),
            "timestamp": datetime.now().isoformat(),
        }
        print(json.dumps(err, ensure_ascii=False, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
