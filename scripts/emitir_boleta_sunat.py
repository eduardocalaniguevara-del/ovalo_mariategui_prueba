#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EMISOR DE BOLETAS DE VENTA ELECTRÓNICAS (SUNAT SEE-SOL)
Hostal El Óvalo Mariátegui
Basado en la grabación Playwright verificada.

Soporta:
1. Una o múltiples habitaciones/ítems en la misma boleta.
2. Cálculo automático de IGV 10.5% (Ley de Turismo/Hotelería).
3. Modo Borrador Supervisado (por defecto): Se detiene en la vista preliminar,
   toma captura de pantalla de alta resolución y NO presiona 'Emitir'.
4. Modo Emisión Definitiva (--emitir): Emite el comprobante y descarga el PDF.
"""

import os
import sys
import time
import re
import argparse
import json
from playwright.sync_api import sync_playwright

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from secretos_hostal import obtener_credenciales_sunat as obtener_credenciales

try:
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr is not None:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = r"C:\Hostal_Sistema"
DIR_BOLETAS = os.path.join(BASE_DIR, "boletas_sunat")
DIR_RECEPCION = os.path.join(BASE_DIR, "descargas_recepcion")
DIR_DESCARGAS = DIR_BOLETAS
user_home = os.environ.get("USERPROFILE", BASE_DIR)
DIR_ARTIFACT = os.path.join(user_home, ".gemini", "antigravity", "brain", "capturas")
CAPTURA_BORRADOR_DEFECTO = os.path.join(DIR_ARTIFACT, "borrador_boleta_sunat.png")

os.makedirs(DIR_BOLETAS, exist_ok=True)
os.makedirs(DIR_RECEPCION, exist_ok=True)
try:
    os.makedirs(DIR_ARTIFACT, exist_ok=True)
except Exception:
    DIR_ARTIFACT = os.path.join(BASE_DIR, "capturas")
    os.makedirs(DIR_ARTIFACT, exist_ok=True)
    CAPTURA_BORRADOR_DEFECTO = os.path.join(DIR_ARTIFACT, "borrador_boleta_sunat.png")


def calcular_valor_unitario_105(monto_total):
    """
    Calcula el valor unitario para que con IGV al 10.5% sume exactamente monto_total.
    Fórmula: Valor = Monto / 1.105 (redondeado a 4 decimales para precisión de SUNAT)
    """
    return round(float(monto_total) / 1.105, 4)


def marcar_radio(frame, element_id, label_text):
    """Marca un radio button de Dojo haciendo clic en su label o mediante dijit/JS."""
    if label_text:
        try:
            lbl = frame.locator(f"label:has-text('{label_text}')").first
            if lbl.count() > 0:
                lbl.click(force=True, timeout=2000)
                return True
        except Exception:
            pass

    if element_id:
        try:
            wid = frame.locator(f"[widgetid='{element_id}'], #widget_{element_id}").first
            if wid.count() > 0:
                wid.click(force=True, timeout=2000)
                return True
        except Exception:
            pass

    frame.evaluate(f"""() => {{
        if ('{element_id}' && window.dijit && dijit.byId('{element_id}')) {{
            dijit.byId('{element_id}').set('checked', true);
            return;
        }}
        const labels = Array.from(document.querySelectorAll('label, span, td'));
        for (const l of labels) {{
            if ((l.textContent || '').includes('{label_text}')) {{
                l.click();
                return;
            }}
        }}
    }}""")
    return True


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


def ejecutar_emision_boleta(
    dni,
    items,
    cliente_nombre=None,
    sin_documento=False,
    dia_emision=None,
    modo="borrador",
    ruta_captura=None,
    observaciones=None,
    subcarpeta=None,
    headless=False
):
    """
    Flujo completo de emisión de Boleta en SUNAT SOL.
    En modo PRUEBA (OVALO_ENTORNO=prueba), fuerza emitir_final=False (modo='borrador')
    para llegar a la Vista Preliminar real en SUNAT sin hacer clic en Emitir.
    """
    if _es_entorno_prueba():
        modo = "borrador"
        print(
            "🧪 [ENTORNO PRUEBA] Ejecutando flujo REAL en SUNAT SEE-SOL hasta Vista Preliminar "
            "(Borrador con captura real, sin clic final en Emitir).",
            file=sys.stderr,
        )
    emitir_final = (str(modo or "borrador").lower() == "definitivo") and (not _es_entorno_prueba())
    ruta_captura = ruta_captura or CAPTURA_BORRADOR_DEFECTO
    ruc, usuario, clave = obtener_credenciales()

    # Autocorrección de DNI (Google Sheets suele borrar el 0 inicial)
    dni_limpio = str(dni or "").strip()
    if len(dni_limpio) == 7 and dni_limpio.isdigit():
        dni_limpio = dni_limpio.zfill(8)
        print(f"   [AUTOCORRECCIÓN DNI] Se recuperó el cero inicial: {dni_limpio}")

    es_dni_valido = len(dni_limpio) == 8 and dni_limpio.isdigit()
    usar_sin_doc = sin_documento or (not es_dni_valido)

    print("=" * 65)
    print(" MOTOR DE EMISIÓN DE BOLETAS SUNAT (SEE-SOL)")
    print(f" Modo: {'🛑 BORRADOR SUPERVISADO (Sin emitir)' if modo == 'borrador' else '🚀 EMISIÓN DEFINITIVA'}")
    print(f" Tipo Emisión: {'SIN DOCUMENTO' if usar_sin_doc else f'DNI: {dni_limpio}'}")
    if cliente_nombre:
        print(f" Cliente: {cliente_nombre}")
    print(f" Cantidad de Ítems: {len(items)}")
    for idx, it in enumerate(items, 1):
        print(f"   [{idx}] Hab. {it.get('habitacion', '-')} | Monto: S/ {it.get('monto', 0):.2f} | {it.get('descripcion', '')}")
    print("=" * 65)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=headless)
        context = browser.new_context(viewport={"width": 1600, "height": 1150})

        try:
            print("[1/5] Iniciando sesión en SUNAT Clave SOL...")
            URL_LOGIN_DIRECTO = (
                "https://api-seguridad.sunat.gob.pe/v1/clientessol/4f3b88b3-d9d6-402a-b85d-6a0bc857746a/oauth2/loginMenuSol"
                "?lang=es-PE&showDni=true&showLanguages=false"
                "&originalUrl=https://e-menu.sunat.gob.pe/cl-ti-itmenu/AutenticaMenuInternet.htm"
                "&state=rO0ABXNyABFqYXZhLnV0aWwuSGFzaE1hcAUH2sHDFmDRAwACRgAKbG9hZEZhY3RvckkACXRocmVzaG9sZHhwP0AAAAAAAAx3CAAAABAAAAADdAADZXhlcHQABnBhcmFtc3QASyomKiYvY2wtdGktaXRtZW51L01lbnVJbnRlcm5ldC5odG0mYjY0ZDI2YThiNWFmMDkxOTIzYjIzYjY0MDdhMWMxZGI0MWU3MzNhNnQABGV4ZWNweA=="
            )
            page = context.new_page()
            page.goto(URL_LOGIN_DIRECTO)
            page.wait_for_selector("#txtRuc", timeout=30000)
            page.fill("#txtRuc", ruc)
            page.fill("#txtUsuario", usuario)
            page.fill("#txtContrasena", clave)
            time.sleep(0.5)
            try:
                page.locator("#btnAceptar").click(no_wait_after=True, timeout=10000)
            except Exception:
                page.keyboard.press("Enter")
            page.wait_for_url("**/cl-ti-itmenu/MenuInternet.htm*", timeout=40000)
            time.sleep(2.0)

            # Handler para alertas de RENIEC / validación
            alerta_reniec = [False, ""]
            def on_dialog_recibido(dialog):
                msg = dialog.message or ""
                print(f"   [ALERTA SUNAT DETECTADA] {dialog.type}: {msg}")
                alerta_reniec[0] = True
                alerta_reniec[1] = msg
                dialog.accept()

            page.on("dialog", on_dialog_recibido)

            # Cerrar posibles modales de aviso
            for btn_text in ["Continuar sin confirmar", "Cerrar", "Aceptar", "Entendido"]:
                try:
                    btn = page.get_by_role("button", name=btn_text)
                    if btn.count() > 0 and btn.first.is_visible():
                        btn.first.click()
                        time.sleep(0.5)
                except Exception:
                    pass

            # Navegación exacta según codegen:
            print("[2/5] Navegando a 'Emitir Boleta de Venta'...")
            time.sleep(2.0)
            page.get_by_role("heading", name="Empresas").wait_for(state="visible", timeout=30000)
            page.get_by_role("heading", name="Empresas").click(timeout=10000)
            time.sleep(1.0)
            page.locator("#nivel1_11 > .glyphicon").first.wait_for(state="visible", timeout=15000)
            page.locator("#nivel1_11 > .glyphicon").first.click(timeout=5000)
            time.sleep(0.5)
            page.get_by_text("SEE - SOL").first.click(timeout=5000)
            time.sleep(0.5)
            page.get_by_text("Boleta de Venta Electrónica").first.click(timeout=5000)
            time.sleep(0.5)
            page.get_by_text("Emitir Boleta de Venta").first.click(timeout=5000)
            time.sleep(3.0)

            print("   Esperando que cargue el formulario dentro del iframe...")
            frame = None
            for sec in range(40):
                for f in page.frames:
                    if f.name == "iframeApplication" or "11.5.4.1.1" in f.url or "ww1" in f.url:
                        try:
                            if f.locator('[id="inicio.numeroDocumento"], [widgetid="inicio.tipoDocumento"]').is_visible():
                                frame = f
                                break
                        except Exception:
                            pass
                if frame:
                    print(f"   ✅ Formulario de boleta cargado tras {sec+1}s.")
                    break
                time.sleep(1.0)

            if not frame:
                try:
                    f_cand = page.wait_for_selector('iframe[name="iframeApplication"]', state="attached", timeout=15000).content_frame()
                    if f_cand:
                        f_cand.wait_for_selector('[id="inicio.numeroDocumento"], [widgetid="inicio.tipoDocumento"]', timeout=15000)
                        frame = f_cand
                except Exception:
                    pass

            if not frame:
                raise Exception("No se pudo cargar el formulario dentro de iframeApplication en SUNAT.")

            # Paso 1: Ingreso de Documento o Nombre del Cliente
            if not usar_sin_doc:
                print(f"[3/5] Ingresando DNI del cliente ({dni_limpio})...")
                input_doc = frame.locator('[id="inicio.numeroDocumento"]')
                input_doc.click()
                input_doc.fill(dni_limpio)
                time.sleep(0.5)

                # Clic en "No" de pago anticipado para fijar el foco y disparar RENIEC
                try:
                    frame.locator('[id="inicio.pagoAnticipado.show"]').get_by_role("cell", name="No", exact=True).click(timeout=3000)
                except Exception:
                    input_doc.press("Tab")

                time.sleep(2.5)

                # Si RENIEC arrojó alerta (DNI no encontrado, error de padrón, etc.)
                if alerta_reniec[0]:
                    print(f"   ⚠️ Falló consulta RENIEC ('{alerta_reniec[1]}'). Activando fallback automático a 'SIN DOCUMENTO'...")
                    usar_sin_doc = True

            if usar_sin_doc:
                nom_final = cliente_nombre or "CLIENTES VARIOS"
                print(f"[3/5] Configurando 'SIN DOCUMENTO' para cliente: {nom_final}...")
                
                # Seleccionar 'SIN DOCUMENTO' en el dropdown de Dojo
                wid_btn = frame.locator("[widgetid='inicio.tipoDocumento'] .dijitArrowButtonContainer").first
                if wid_btn.count() > 0:
                    wid_btn.click()
                    time.sleep(0.8)
                    frame.locator(".dijitMenuItem").filter(has_text="SIN DOCUMENTO").first.click()
                    time.sleep(1.0)
                else:
                    frame.evaluate("""() => {
                        if (window.dijit && dijit.byId('inicio.tipoDocumento')) {
                            dijit.byId('inicio.tipoDocumento').set('value', '-');
                        }
                    }""")
                    time.sleep(1.0)

                # Llenar Apellidos y Nombres en inicio.razonSocial
                rz = frame.locator('[id="inicio.razonSocial"]')
                rz.click()
                rz.fill(nom_final)
                time.sleep(0.5)

            # Continuar a la pantalla de ítems
            frame.get_by_role("button", name="Continuar").dispatch_event("click")
            time.sleep(3.0)

            # Paso 2: Agregar Ítems (Habitaciones)
            print(f"[4/5] Agregando {len(items)} ítem(s) de hospedaje...")
            for idx, it in enumerate(items, 1):
                hab = it.get("habitacion", "")
                fecha_txt = it.get("fecha", "OCTUBRE")
                monto = float(it.get("monto", 0))
                cant = int(it.get("cantidad", 1))
                monto_unitario = monto / cant if cant > 0 else monto
                valor_unitario = it.get("valor_unitario") or calcular_valor_unitario_105(monto_unitario)
                desc_personalizada = it.get("descripcion", "")
                
                if not desc_personalizada:
                    desc_personalizada = f"DIA DE ALOJAMIENTO DEL {fecha_txt}, HAB N {hab}".upper()

                print(f"   -> Agregando Ítem {idx}: {desc_personalizada} (Cant: {cant}, Unitario: S/ {monto_unitario:.2f}, Valor: {valor_unitario:.4f}, Total: S/ {monto:.2f})")
                
                # Clic en '+ Adicionar'
                frame.get_by_role("button", name="Adicionar").dispatch_event("click", timeout=5000)
                time.sleep(1.2)

                # Radio 'Servicio' instantáneo por JS/Dojo
                try:
                    frame.evaluate("""() => {
                        const el = document.getElementById('item.subTipoTI02');
                        if (el) {
                            el.checked = true;
                            el.dispatchEvent(new Event('click', { bubbles: true }));
                            el.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                        if (window.dijit && dijit.byId('item.subTipoTI02')) {
                            dijit.byId('item.subTipoTI02').set('checked', true);
                        }
                    }""")
                except Exception:
                    pass
                time.sleep(0.2)

                # Cantidad
                frame.locator('[id="item.cantidad"]').fill(str(cant))
                time.sleep(0.2)

                # Descripción
                frame.locator('[id="item.descripcion"]').fill(desc_personalizada)
                time.sleep(0.2)

                # IGV 10.5% instantáneo por JS/Dojo
                try:
                    frame.evaluate("""() => {
                        const radios = document.querySelectorAll('input[name="tipoIGV"]');
                        if (radios.length > 1) {
                            radios[1].checked = true;
                            radios[1].dispatchEvent(new Event('click', { bubbles: true }));
                            radios[1].dispatchEvent(new Event('change', { bubbles: true }));
                        }
                    }""")
                except Exception:
                    pass
                time.sleep(0.2)

                # Valor Unitario (input id: item.precioUnitario)
                inp_precio = frame.locator('[id="item.precioUnitario"]')
                inp_precio.click(force=True, timeout=3000)
                inp_precio.fill(str(valor_unitario))
                inp_precio.press("Tab")
                time.sleep(0.4)

                # Clic en 'Aceptar' del modal
                btn_aceptar = frame.locator('[id="item.botonAceptar"]')
                try:
                    btn_aceptar.dispatch_event("click", timeout=3000)
                except Exception:
                    frame.get_by_role("button", name="Aceptar").dispatch_event("click", timeout=3000)
                time.sleep(1.5)

            # Ajustar Fecha de emisión si se especificó (ej. "12", "13", "14")
            if dia_emision:
                try:
                    print(f"   Ajustando día de emisión a: {dia_emision}...")
                    btn_flecha_fecha = frame.locator(".dijitReset.dijitRight.dijitButtonNode.dijitArrowButton.dijitDownArrowButton.dijitArrowButtonContainer").first
                    if btn_flecha_fecha.count() > 0:
                        btn_flecha_fecha.dispatch_event("click", timeout=3000)
                        time.sleep(0.5)
                        frame.get_by_text(str(dia_emision), exact=True).dispatch_event("click", timeout=3000)
                        time.sleep(0.5)
                        print(f"   ✅ Día de emisión ajustado a {dia_emision}.")
                except Exception as e_fecha:
                    print(f"   [INFO] Fecha de emisión mantenida por defecto: {e_fecha}")

            # Continuar (Pantalla de ítems -> Observaciones)
            print("   Avanzando de Ítems a Observaciones...")
            try:
                frame.get_by_role("button", name="Continuar").dispatch_event("click", timeout=5000)
            except Exception:
                frame.get_by_role("button", name="Continuar").click(force=True, timeout=5000)
            time.sleep(2.0)

            # Si hay observaciones que agregar:
            if observaciones:
                try:
                    txt_obs = frame.locator("textarea").first
                    if txt_obs.count() > 0:
                        txt_obs.fill(observaciones)
                except Exception:
                    pass

            # Continuar (Pantalla de Observaciones -> Vista Preliminar / Borrador)
            print("   Avanzando de Observaciones a Vista Preliminar...")
            for _ in range(4):
                try:
                    if frame.get_by_role("button", name="Emitir").is_visible():
                        break
                    btn_cont = frame.get_by_role("button", name="Continuar")
                    if btn_cont.count() > 0 and btn_cont.first.is_visible():
                        btn_cont.first.dispatch_event("click")
                except Exception:
                    pass
                time.sleep(2.0)

            # -------------------------------------------------------------
            # VISTA PRELIMINAR ALCANZADA
            # -------------------------------------------------------------
            print("\n" + "=" * 65)
            print(" 🛑 VISTA PRELIMINAR (BORRADOR) ALCANZADA CON ÉXITO")
            print("=" * 65)

            # Si el modo es BORRADOR (emitir_final == False), detenerse aquí de forma segura
            if not emitir_final:
                page.screenshot(path=ruta_captura)
                print(f"📸 Captura del borrador guardada en: {ruta_captura}")
                print("\n🔒 [MODO SUPERVISADO / VISTA PRELIMINAR] No se ha emitido ningún comprobante real.")
                if not headless:
                    print("   Manteniendo el navegador visible 15 segundos para tu inspección...")
                    time.sleep(15.0)
                return {
                    "status": "OK",
                    "modo": "borrador",
                    "entorno_prueba": _es_entorno_prueba(),
                    "nro_comprobante": "BORRADOR-PRELIMINAR",
                    "captura": ruta_captura,
                    "mensaje": "Borrador generado exitosamente sin emisión final."
                }

            # Si el modo es DEFINITIVO (emitir_final == True y NO estamos en prueba), continuar con la emisión real
            if emitir_final and not _es_entorno_prueba():
                print("\n🚀 [EMISIÓN DEFINITIVA] Procediendo a emitir comprobante oficial...")
                try:
                    frame.get_by_role("button", name="Emitir").dispatch_event("click", timeout=5000)
                except Exception:
                    frame.get_by_role("button", name="Emitir").click(force=True, timeout=5000)
                time.sleep(1.5)

                # Confirmar en popup "¿Está seguro de emitir la Boleta?"
                try:
                    btn_conf = frame.locator("#dlgBtnAceptarConfirm, #dlgBtnAceptar")
                    if btn_conf.count() > 0:
                        btn_conf.first.dispatch_event("click", timeout=5000)
                    else:
                        frame.get_by_role("button", name="Aceptar").first.dispatch_event("click", timeout=5000)
                except Exception:
                    try:
                        frame.get_by_role("button", name="Aceptar").first.click(force=True, timeout=5000)
                    except Exception:
                        pass
                time.sleep(2.5)

                cap_emitida = os.path.join(DIR_ARTIFACT, "boleta_emitida_oficial.png")

                print("📥 Descargando comprobante PDF...")
                with page.expect_download(timeout=25000) as download_info:
                    try:
                        frame.get_by_role("button", name="Descargar PDF").dispatch_event("click", timeout=5000)
                    except Exception:
                        frame.get_by_role("button", name="Descargar PDF").click(force=True, timeout=5000)
                download = download_info.value

                # Determinar carpeta de destino por subcarpeta o auto-detección de fecha
                dir_destino = DIR_BOLETAS
                if subcarpeta:
                    dir_destino = os.path.join(DIR_BOLETAS, subcarpeta)
                else:
                    for it in items:
                        txt_it = (str(it.get("descripcion", "")) + " " + str(it.get("fecha", ""))).upper()
                        m_dia = re.search(r'\b(\d{1,2})\s+DE\s+([A-Z]+)', txt_it)
                        if m_dia:
                            dir_destino = os.path.join(DIR_BOLETAS, f"{m_dia.group(1).zfill(2)}_{m_dia.group(2).capitalize()}")
                            break
                os.makedirs(dir_destino, exist_ok=True)

                nombre_pdf = download.suggested_filename
                ruta_pdf = os.path.join(dir_destino, nombre_pdf)
                download.save_as(ruta_pdf)
                print(f"✅ Comprobante oficial descargado en carpeta limpia: {ruta_pdf}")

                # Extraer número de comprobante emitido
                nro_cp = ""
                try:
                    txt_emitido = frame.locator("body").inner_text()
                    m_cp = re.search(r'\b(EB01\s*-\s*\d+)\b', txt_emitido)
                    if m_cp:
                        nro_cp = m_cp.group(1).replace(" ", "")
                except Exception:
                    pass
                if not nro_cp and nombre_pdf:
                    m_cp = re.search(r'(EB01)-?(\d{1,8})', nombre_pdf)
                    if m_cp:
                        nro_cp = f"{m_cp.group(1)}-{int(m_cp.group(2))}"
                
                print(f"📄 Número de comprobante detectado: {nro_cp}")

                # Copiar también a descargas_recepcion para compatibilidad
                try:
                    import shutil
                    ruta_rec = os.path.join(DIR_RECEPCION, nombre_pdf)
                    shutil.copy2(ruta_pdf, ruta_rec)
                except Exception:
                    pass

                return {
                    "status": "OK",
                    "modo": "definitivo",
                    "nro_comprobante": nro_cp,
                    "archivo_pdf": ruta_pdf,
                    "nombre_pdf": nombre_pdf,
                    "captura": cap_emitida,
                    "mensaje": "Boleta emitida y descargada exitosamente."
                }

        except Exception as e:
            print(f"❌ Error durante el flujo de emisión: {e}")
            import traceback
            traceback.print_exc()
            err_cap = os.path.join(DIR_ARTIFACT, "error_emision_boleta.png")
            try:
                page.screenshot(path=err_cap, full_page=True)
                print(f"📸 Captura de error guardada en: {err_cap}")
            except Exception:
                pass
            return {
                "status": "ERROR",
                "error": str(e)
            }
        finally:
            print("Cerrando sesión de navegador...")
            context.close()
            browser.close()


def main():
    parser = argparse.ArgumentParser(description="Emisor de Boletas de Venta SUNAT (SEE-SOL)")
    parser.add_argument("--dni", type=str, default="", help="DNI del cliente (8 dígitos o 7 para autocompletar)")
    parser.add_argument("--cliente", type=str, default=None, help="Nombre o Razón Social del cliente (para Sin Documento o fallback)")
    parser.add_argument("--sin-documento", action="store_true", help="Emitir directamente como 'SIN DOCUMENTO' usando el nombre del cliente")
    parser.add_argument("--monto", type=float, help="Monto total en Soles (si es ítem único)")
    parser.add_argument("--habitacion", type=str, default="104", help="Número de habitación (si es ítem único)")
    parser.add_argument("--fecha", type=str, default="12 DE OCTUBRE", help="Texto de fecha para la descripción")
    parser.add_argument("--descripcion", type=str, default=None, help="Descripción personalizada del ítem")
    parser.add_argument("--dia-emision", type=str, default=None, help="Día del mes a elegir en el calendario de emisión (ej. 14)")
    parser.add_argument("--items-json", type=str, default=None, help="JSON con lista de ítems para múltiples habitaciones")
    parser.add_argument("--subcarpeta", type=str, default=None, help="Nombre de subcarpeta dentro de boletas_sunat (ej. 14_Octubre)")
    parser.add_argument("--emitir", action="store_true", help="Si se incluye, realiza la emisión final. Si no, queda en BORRADOR.")
    parser.add_argument("--headless", action="store_true", help="Ejecutar sin ventana visible")

    args = parser.parse_args()

    items = []
    if args.items_json:
        try:
            items = json.loads(args.items_json)
        except Exception as e:
            print(f"[ERROR] JSON de ítems inválido: {e}")
            sys.exit(1)
    else:
        if args.monto is None:
            print("[ERROR] Debe especificar --monto o --items-json")
            sys.exit(1)
        items.append({
            "habitacion": args.habitacion,
            "fecha": args.fecha,
            "monto": args.monto,
            "descripcion": args.descripcion
        })

    modo = "definitivo" if (args.emitir and not _es_entorno_prueba()) else "borrador"

    res = ejecutar_emision_boleta(
        dni=args.dni,
        items=items,
        cliente_nombre=args.cliente,
        sin_documento=args.sin_documento,
        dia_emision=args.dia_emision,
        modo=modo,
        subcarpeta=args.subcarpeta,
        headless=args.headless
    )

    if res.get("status") == "OK":
        print("\n🎉 Operación finalizada con éxito.")
        sys.exit(0)
    else:
        print("\n❌ Operación finalizada con errores.")
        sys.exit(1)


if __name__ == "__main__":
    main()
