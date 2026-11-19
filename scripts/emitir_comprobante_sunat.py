#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EMISOR UNIFICADO DE COMPROBANTES SUNAT (BOLETA EB01 / FACTURA E001) + ENVÍO WSP
Hostal El Óvalo Mariátegui - C:\\Hostal_Sistema\\scripts\\emitir_comprobante_sunat.py

Capacidades:
1. Emisión de BOLETA (EB01) con DNI o 'SIN DOCUMENTO' (Clientes Varios).
2. Emisión de FACTURA (E001) con RUC (11 dígitos) y condición al 'Contado'.
3. Soporte multi-ítem con cálculo automático de IGV 10.5% (Ley MYPE Hotelería/Turismo):
   Valor Unitario = Precio Unitario con IGV / 1.105 (4 decimales).
4. Descarga automática del PDF en boletas_sunat/ o facturas_sunat/ y copia en descargas_recepcion/.
5. Actualización en Google Sheets de 'Registro_Estancias' (Col AG: Tipo, AH: Emitido, AI: Nro CP, AJ: Monto)
   y opcionalmente 'CALZADO_OCT' mediante registrar_emision_sistema.py.
6. Despacho opcional inmediato por WhatsApp de Recepción mediante brazo_wsp.py.
"""

import os
import sys
import time
import re
import json
import base64
import shutil
import argparse
import subprocess
from datetime import datetime
from playwright.sync_api import sync_playwright

try:
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr is not None:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = r"C:\Hostal_Sistema"
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from secretos_hostal import obtener_credenciales_sunat as obtener_credenciales
from registrar_emision_sistema import registrar_emision

DIR_BOLETAS = os.path.join(BASE_DIR, "boletas_sunat")
DIR_FACTURAS = os.path.join(BASE_DIR, "facturas_sunat")
DIR_RECEPCION = os.path.join(BASE_DIR, "descargas_recepcion")
DIR_CAPTURAS = os.path.join(BASE_DIR, "capturas")

for d in (DIR_BOLETAS, DIR_FACTURAS, DIR_RECEPCION, DIR_CAPTURAS):
    os.makedirs(d, exist_ok=True)


MESES_CARPETA = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
    5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
    9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}


def calcular_valor_unitario_105(precio_con_igv):
    """Fórmula Ley Hotelería 10.5%: Valor Unitario = Precio Unitario / 1.105 (4 decimales)"""
    return round(float(precio_con_igv) / 1.105, 4)


def normalizar_y_validar_dni(doc_raw, telefono="", enviar_wsp=False):
    """
    Normaliza DNI peruano (7 dígitos -> 8 dígitos con cero a la izquierda) y aplica la regla estricta:
    - Si tiene celular / envío WSP, es OBLIGATORIO tener DNI válido de 7 u 8 dígitos (nunca enviar con DNI erróneo o vacío).
    - Si se ingresó algún documento (no vacío) con longitud inválida (ej. 4 dígitos), bloquea de inmediato.
    """
    raw_str = str(doc_raw or "").strip()
    primer_token = re.split(r"[/,;]+", raw_str)[0].strip() if raw_str else ""
    es_vacio = primer_token in ("", "-", "SIN DOCUMENTO", "S/D")
    solo_dig = "" if es_vacio else re.sub(r"\D", "", primer_token)
    if len(solo_dig) == 7:
        solo_dig = solo_dig.zfill(8)

    tel_dig = re.sub(r"\D", "", str(telefono or "").strip())

    if not es_vacio and len(solo_dig) != 8:
        raise ValueError(
            f"DNI incorrecto ('{primer_token}' tiene {len(solo_dig)} dígitos). "
            f"El DNI debe tener 7 u 8 dígitos numéricos válidos."
        )

    if (tel_dig or enviar_wsp) and len(solo_dig) != 8:
        raise ValueError(
            f"El huésped tiene número de celular registrado ({tel_dig or 'WhatsApp'}), "
            f"por lo que es OBLIGATORIO un DNI válido de 7 u 8 dígitos antes de emitir o enviar."
        )

    return solo_dig


def agregar_items_sunat(frame, items):
    """Itera sobre la lista de ítems del constructor y los registra en el modal Dojo de SUNAT (blindado contra S/ undefined)."""
    for idx, it in enumerate(items, 1):
        cant = max(1, int(it.get("cantidad", 1)))
        precio_unit = float(it.get("precio_unitario", it.get("monto", 0)))
        valor_unit = float(it.get("valor_unitario") or calcular_valor_unitario_105(precio_unit))
        valor_str = f"{valor_unit:.4f}"
        desc = str(it.get("descripcion", "")).strip().upper()

        print(f"   -> Agregando Ítem {idx}: Cant={cant} | P.Unit=S/ {precio_unit:.2f} | V.Unit={valor_str} | {desc}")

        # Clic en '+ Adicionar' y esperar apertura real del modal de ítem
        frame.get_by_role("button", name="Adicionar").dispatch_event("click", timeout=5000)
        inp_desc = frame.locator('[id="item.descripcion"]')
        inp_desc.wait_for(state="visible", timeout=7000)
        time.sleep(0.2)

        # 1. Marcar Radio 'Servicio' (subTipoTI02) y dar pausa a Dojo para re-renderizar campos
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
        time.sleep(0.35)

        # 2. Seleccionar IGV 10.5% ANTES del Valor Unitario para que Dojo no borre el precio ni calcule S/ undefined
        def _marcar_igv_105():
            frame.evaluate("""() => {
                const radios = document.querySelectorAll('input[name="tipoIGV"]');
                if (radios.length > 1) {
                    const r105 = radios[1];
                    r105.checked = true;
                    r105.dispatchEvent(new Event('click', { bubbles: true }));
                    r105.dispatchEvent(new Event('change', { bubbles: true }));
                    if (window.dijit && r105.id && dijit.byId(r105.id)) {
                        dijit.byId(r105.id).set('checked', true);
                    }
                }
            }""")

        _marcar_igv_105()
        time.sleep(0.35)

        # 3. Cantidad y Descripción
        inp_cant = frame.locator('[id="item.cantidad"]')
        if inp_cant.count() > 0:
            inp_cant.fill(str(cant))

        inp_desc.fill(desc)
        time.sleep(0.15)

        # 4. Valor Unitario sin IGV con verificación anti-Dojo (evita IGV: S/ undefined)
        inp_precio = frame.locator('[id="item.precioUnitario"]')
        for intento_p in range(1, 4):
            _marcar_igv_105()
            time.sleep(0.15)
            inp_precio.click(force=True, timeout=3000)
            inp_precio.fill("")
            inp_precio.fill(valor_str)
            frame.evaluate(
                """(valStr) => {
                    const el = document.getElementById('item.precioUnitario');
                    if (el) {
                        el.value = valStr;
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                    if (window.dijit && dijit.byId('item.precioUnitario')) {
                        try { dijit.byId('item.precioUnitario').set('value', valStr); } catch (e) {}
                    }
                }""",
                valor_str,
            )
            inp_precio.press("Tab")
            time.sleep(0.45)

            estado_item = frame.evaluate("""() => {
                const el = document.getElementById('item.precioUnitario');
                const v = el ? String(el.value || '').trim() : '';
                const igvEl = document.getElementById('item.igv') || document.getElementById('item.mtoIgv');
                const totEl = document.getElementById('item.importeTotal') || document.getElementById('item.total');
                const txtModal = (
                    (igvEl ? igvEl.value : '') + ' ' +
                    (totEl ? totEl.value : '') + ' ' +
                    (el && el.closest('form, .dijitDialog') ? el.closest('form, .dijitDialog').innerText : '')
                ).toLowerCase();
                return {
                    val: v,
                    num: parseFloat(v.replace(',', '')) || 0,
                    hasUndefined: txtModal.includes('undefined') || txtModal.includes('nan')
                };
            }""")
            if estado_item.get("num", 0) > 0 and not estado_item.get("hasUndefined"):
                break
            print(f"   ⚠️ Reintentando fijar Valor Unitario en Dojo ({intento_p}/3): {estado_item}")
            time.sleep(0.3)

        # 5. Aceptar Ítem en el modal y verificar que realmente se cierre y agregue a la grilla
        def _clic_aceptar_item():
            btn_aceptar = frame.locator('[id="item.botonAceptar"]')
            if btn_aceptar.count() > 0 and btn_aceptar.first.is_visible():
                btn_aceptar.first.click(force=True, timeout=3000)
            else:
                frame.get_by_role("button", name="Aceptar").first.click(force=True, timeout=3000)

        _clic_aceptar_item()
        try:
            inp_desc.wait_for(state="hidden", timeout=5500)
        except Exception:
            time.sleep(0.4)
            if inp_desc.is_visible():
                _clic_aceptar_item()
                try:
                    inp_desc.wait_for(state="hidden", timeout=4500)
                except Exception:
                    raise RuntimeError(
                        f"SUNAT no aceptó el ítem #{idx} ('{desc[:45]}') en el formulario Dojo. Verifique monto y descripción."
                    )


AUTENTICA_SOL_URL = "https://e-menu.sunat.gob.pe/cl-ti-itmenu/AutenticaMenuInternet.htm"
STATE_SOL_DEFAULT = (
    "rO0ABXNyABFqYXZhLnV0aWwuSGFzaE1hcAUH2sHDFmDRAwACRgAKbG9hZEZhY3RvckkACXRocmVzaG9sZHhwP0AAAAAAAAx3CAAAABAAAAADdAADZXhlcHQABnBhcmFtc3QA"
    "SyomKiYvY2wtdGktaXRtZW51L01lbnVJbnRlcm5ldC5odG0mYjY0ZDI2YThiNWFmMDkxOTIzYjIzYjY0MDdhMWMxZGI0MWU3MzNhNnQABGV4ZWNweA=="
)
URL_MENU_SOL = "https://e-menu.sunat.gob.pe/cl-ti-itmenu/MenuInternet.htm"
ERRORES_RED = ("net::ERR_", "ERR_CONNECTION", "ERR_TIMED_OUT", "ERR_NAME_NOT_RESOLVED", "Timeout", "timeout")


def _es_error_red(msg):
    msg = str(msg or "")
    return any(t in msg for t in ERRORES_RED)


def _en_menu_internet_final(url_str: str) -> bool:
    u = str(url_str or "")
    return "/MenuInternet.htm" in u and "AutenticaMenuInternet.htm" not in u


def iniciar_sesion_sol_blindado(page, url_login, ruc_emisor, usuario, clave, intentos=4):
    """
    Login OAuth2 SUNAT blindado contra:
    1. Error intermitente '503 Service Temporarily Unavailable (nginx)' del balanceador de SUNAT
       (recarga inmediata en 0.6s sin esperar 15s de timeout).
    2. 'originalUrl' / 'state' vacíos cuando el JS de loginMenuSol tarda en hidratar
       (que desviaba a https://api-seguridad.sunat.gob.pe/?state=&code=<jwt> 'Bienvenidos a SUNAT').
    3. Diferenciación exacta entre '/AutenticaMenuInternet.htm' (paso intermedio) y '/MenuInternet.htm' (destino).
    """
    import urllib.parse

    ultimo_error = None
    for i in range(1, intentos + 1):
        try:
            # 1. Sortear al instante nodos nginx con 503 Service Temporarily Unavailable
            cargado_200 = False
            for r503 in range(1, 9):
                resp = page.goto(url_login, wait_until="domcontentloaded", timeout=15000)
                st_code = resp.status if resp else 0
                titulo = (page.title() or "").lower()
                if st_code == 200 and "503" not in titulo and "unavailable" not in titulo:
                    cargado_200 = True
                    break
                print(f"   ⚡ Nodo SUNAT devolvió HTTP {st_code} ({page.title()[:40]}); reintentando nodo ({r503}/8)...")
                time.sleep(0.6)

            if not cargado_200:
                raise RuntimeError("SUNAT devolvió 503 Service Temporarily Unavailable en múltiples nodos.")

            page.wait_for_selector("#txtRuc", state="visible", timeout=12000)
            time.sleep(0.35)

            # 2. Sellar originalUrl y state en LoginForm antes del submit
            page.evaluate(
                """([origUrl, stateVal]) => {
                    const ou = document.getElementById('originalUrl') || document.querySelector('input[name="originalUrl"]');
                    if (ou) ou.value = origUrl;
                    const st = document.querySelector('input[name="state"]');
                    if (st) st.value = stateVal;
                }""",
                [AUTENTICA_SOL_URL, STATE_SOL_DEFAULT],
            )

            page.fill("#txtRuc", ruc_emisor)
            page.fill("#txtUsuario", usuario)
            page.fill("#txtContrasena", clave)
            page.locator("#btnAceptar").click(no_wait_after=True, timeout=10000)

            # 3. Monitoreo activo hasta llegar a /MenuInternet.htm (no AutenticaMenuInternet.htm)
            t0 = time.time()
            rescatado = False
            while time.time() - t0 < 28.0:
                cur_url = page.url or ""
                if _en_menu_internet_final(cur_url):
                    return
                # Si al saltar a AutenticaMenuInternet o MenuInternet cayó en 503 de nginx, forzar salto
                try:
                    tit_act = (page.title() or "").lower()
                except Exception:
                    tit_act = ""
                if ("503" in tit_act or "unavailable" in tit_act) and not rescatado:
                    rescatado = True
                    page.goto("https://e-menu.sunat.gob.pe/cl-ti-itmenu/MenuInternet.htm", wait_until="domcontentloaded", timeout=15000)
                    if _en_menu_internet_final(page.url):
                        return
                if "api-seguridad.sunat.gob.pe" in cur_url and "code=" in cur_url and not rescatado:
                    qs = urllib.parse.parse_qs(urllib.parse.urlparse(cur_url).query)
                    jwt_code = (qs.get("code") or [""])[0]
                    if jwt_code:
                        rescatado = True
                        print("   ⚡ Token OAuth detectado en api-seguridad; redirigiendo directo a MenuInternet...")
                        salto = f"{AUTENTICA_SOL_URL}?state={urllib.parse.quote(STATE_SOL_DEFAULT, safe='')}&code={urllib.parse.quote(jwt_code, safe='')}"
                        page.goto(salto, wait_until="domcontentloaded", timeout=20000)
                        for _ in range(15):
                            if _en_menu_internet_final(page.url):
                                return
                            time.sleep(0.3)
                time.sleep(0.3)

            raise RuntimeError(f"Timeout esperando MenuInternet.htm (URL actual: {(page.url or '')[:120]})")
        except Exception as e:
            ultimo_error = e
            print(f"   ⚠️ Intento login {i}/{intentos} falló: {str(e).splitlines()[0][:140]}")
            time.sleep(0.8)
    raise RuntimeError(f"No se pudo iniciar sesión en SUNAT tras {intentos} intentos: {ultimo_error}")


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


def emitir_comprobante(
    tipo,
    doc_num,
    cliente_nombre,
    items,
    emitir_final=True,
    headless=False,
    telefono="",
    enviar_wsp=False,
    dni_huesped=None,
):
    """
    Emite Boleta (EB01) o Factura (E001) en SUNAT SOL con reintento automático completo
    si la red se corta ANTES de pulsar 'Emitir' (nunca duplica comprobantes).
    En modo PRUEBA (OVALO_ENTORNO=prueba), fuerza emitir_final=False y enviar_wsp=False
    para ejecutar el flujo REAL en SUNAT SEE-SOL hasta la Vista Preliminar (Borrador).
    """
    tipo_norm = str(tipo or "BOLETA").upper().strip()
    doc_raw = str(doc_num or "").strip()

    if _es_entorno_prueba():
        emitir_final = False
        enviar_wsp = False
        print(
            "🧪 [ENTORNO PRUEBA] Ejecutando flujo REAL en SUNAT SEE-SOL hasta Vista Preliminar "
            "(Borrador con captura real, sin clic final en Emitir).",
            file=sys.stderr,
        )

    # Validación preventiva instantánea ANTES de abrir navegador en SUNAT
    try:
        if tipo_norm == "FACTURA":
            ruc_limpio = re.sub(r"\D", "", doc_raw)
            if len(ruc_limpio) != 11 or not re.match(r"^(10|15|17|20)", ruc_limpio):
                raise ValueError(f"Para FACTURA se requiere RUC válido de 11 dígitos (recibido: '{doc_raw}').")
            doc_efectivo = ruc_limpio
            if dni_huesped is not None:
                dni_check = dni_huesped
                if not str(dni_check).strip() and ruc_limpio.startswith("10"):
                    dni_check = ruc_limpio[2:10]
                normalizar_y_validar_dni(dni_check, telefono=telefono, enviar_wsp=enviar_wsp)
        else:
            doc_efectivo = normalizar_y_validar_dni(doc_raw, telefono=telefono, enviar_wsp=enviar_wsp)
    except ValueError as ve:
        return {"status": "ERROR", "tipo": tipo_norm, "error": str(ve)}

    for intento in range(1, 3):
        estado = {"emitir_pulsado": False}
        res = _emitir_comprobante_intento(
            tipo_norm,
            doc_efectivo,
            cliente_nombre,
            items,
            emitir_final,
            headless,
            estado,
        )
        if res.get("status") == "OK":
            return res
        if estado["emitir_pulsado"] or not _es_error_red(res.get("error")) or intento == 2:
            return res
        print(f"🔁 Corte de red antes de emitir (sin riesgo de duplicado). Reintentando flujo completo ({intento + 1}/2)...")
        time.sleep(2.0)
    return res


def _emitir_comprobante_intento(tipo, doc_num, cliente_nombre, items, emitir_final, headless, estado):
    if _es_entorno_prueba():
        emitir_final = False
    ruc_emisor, usuario, clave = obtener_credenciales()
    tipo = tipo.upper().strip()

    print("=" * 65)
    print(f" MOTOR DE EMISIÓN SUNAT (SEE-SOL) - {tipo}")
    print(f" Modo: {'🚀 DEFINITIVO (Emitir y Descargar PDF)' if emitir_final else '🛑 BORRADOR (Vista Preliminar)'}")
    print(f" Documento: {doc_num or 'SIN DOCUMENTO'}")
    print(f" Cliente: {cliente_nombre or 'CLIENTES VARIOS'}")
    print(f" Total Ítems: {len(items)}")
    print("=" * 65)

    with sync_playwright() as p:
        from navegador_discreto import crear_contexto_sunat, lanzar_edge_sunat
        browser = lanzar_edge_sunat(p, oculto=True)
        context = crear_contexto_sunat(browser, viewport={"width": 1600, "height": 1150})
        page = context.new_page()

        try:
            print("[1/5] Iniciando sesión en SUNAT Clave SOL...")
            URL_LOGIN = (
                "https://api-seguridad.sunat.gob.pe/v1/clientessol/4f3b88b3-d9d6-402a-b85d-6a0bc857746a/oauth2/loginMenuSol"
                "?lang=es-PE&showDni=true&showLanguages=false"
                f"&originalUrl={AUTENTICA_SOL_URL}"
                f"&state={STATE_SOL_DEFAULT}"
            )
            iniciar_sesion_sol_blindado(page, URL_LOGIN, ruc_emisor, usuario, clave)
            time.sleep(0.3)

            alerta_sunat = [False, ""]
            page.on("dialog", lambda d: (alerta_sunat.__setitem__(0, True), alerta_sunat.__setitem__(1, d.message or ""), d.accept()))

            # Cerrar posibles modales de aviso
            for btn_text in ["Continuar sin confirmar", "Cerrar", "Aceptar", "Entendido"]:
                try:
                    btn = page.get_by_role("button", name=btn_text)
                    if btn.count() > 0 and btn.first.is_visible():
                        btn.first.click()
                        time.sleep(0.25)
                except Exception:
                    pass

            # Navegación ágil al menú SEE-SOL (blindada contra pestaña 'Personas' de SUNAT)
            print(f"[2/5] Navegando a 'Emitir {tipo.capitalize()}'...")
            target_sub = "Factura Electrónica" if tipo == "FACTURA" else "Boleta de Venta Electrónica"
            target_leaf = "Emitir Factura" if tipo == "FACTURA" else "Emitir Boleta de Venta"
            page.get_by_role("heading", name="Empresas").first.wait_for(state="visible", timeout=25000)

            menu_ok = False
            for intento_menu in range(1, 5):
                try:
                    page.get_by_role("heading", name="Empresas").first.click(timeout=10000)
                    time.sleep(0.75)
                    # En SUNAT, Personas también usa #nivel1_11; si el submenú de Empresas ya está visible, evitar colapsarlo
                    if not page.get_by_text(target_sub).first.is_visible():
                        if not page.get_by_text("SEE - SOL").first.is_visible():
                            page.locator("#nivel1_11 > .glyphicon").first.click(timeout=5000)
                            time.sleep(0.5)
                        if not page.get_by_text(target_sub).first.is_visible():
                            page.get_by_text("SEE - SOL").first.click(timeout=5000)
                            time.sleep(0.55)
                    page.get_by_text(target_sub).first.wait_for(state="visible", timeout=3500)
                    menu_ok = True
                    break
                except Exception:
                    print(f"   ⚠️ Menú SUNAT aún en 'Personas' o cargando ({intento_menu}/4); re-seleccionando 'Empresas'...")
                    time.sleep(0.6)

            if not menu_ok:
                page.get_by_text(target_sub).first.wait_for(state="visible", timeout=5000)

            page.get_by_text(target_sub).first.click(timeout=5000)
            time.sleep(0.45)
            page.get_by_text(target_leaf).first.click(timeout=5000)

            # Localizar iframeApplication con muestreo rápido (cada 0.25s)
            print("   Esperando formulario dentro de iframeApplication...")
            frame = None
            t_ifr = time.time()
            for n_ifr in range(180):
                for f in page.frames:
                    if f.name == "iframeApplication" or "11.5.4.1.1" in f.url or "ww1" in f.url:
                        try:
                            if f.locator('[id="inicio.numeroDocumento"], [widgetid="inicio.tipoDocumento"]').count() > 0:
                                frame = f
                                break
                        except Exception:
                            pass
                if frame:
                    break
                time.sleep(0.25)
            print(f"   Formulario listo en {time.time()-t_ifr:.1f}s")

            if not frame:
                raise RuntimeError("No se pudo cargar el formulario dentro de iframeApplication en SUNAT.")

            # Paso 1: Ingreso de Datos del Cliente
            doc_limpio = str(doc_num or "").strip()

            def _esperar_autocompletado_cliente(max_s=3.0):
                t_c = time.time()
                while time.time() - t_c < max_s:
                    if alerta_sunat[0]:
                        break
                    try:
                        val_rz = frame.evaluate("""() => {
                            const el = document.getElementById('inicio.razonSocial') || document.getElementById('inicio.descripcionTipoDocumento');
                            return el ? (el.value || '').trim() : '';
                        }""")
                        if val_rz and len(val_rz) >= 3:
                            time.sleep(0.15)
                            return
                    except Exception:
                        pass
                    time.sleep(0.2)

            if tipo == "FACTURA":
                if len(doc_limpio) != 11 or not doc_limpio.isdigit():
                    raise ValueError(f"Para FACTURA se requiere RUC de 11 dígitos válido (recibido: '{doc_limpio}').")

                print(f"[3/5] Ingresando RUC {doc_limpio}...")
                inp_ruc = frame.locator('[id="inicio.numeroDocumento"]').first
                inp_ruc.click()
                inp_ruc.fill(doc_limpio)
                inp_ruc.press("Tab")
                _esperar_autocompletado_cliente(3.0)
                if alerta_sunat[0]:
                    raise ValueError(f"SUNAT rechazó el RUC {doc_limpio}: {alerta_sunat[1] or 'RUC no válido'}.")

                # Marcar Forma de Pago 'Al Contado'
                try:
                    frame.locator("label:has-text('Contado')").first.click(timeout=2000)
                except Exception:
                    frame.evaluate("""() => {
                        const radios = Array.from(document.querySelectorAll('input[type="radio"]'));
                        for (const r of radios) {
                            if ((r.value || '').toUpperCase() === 'CONTADO' || (r.id || '').includes('contado')) {
                                r.checked = true;
                                r.dispatchEvent(new Event('click', { bubbles: true }));
                            }
                        }
                    }""")

            else:
                # BOLETA
                if len(doc_limpio) == 7 and doc_limpio.isdigit():
                    doc_limpio = doc_limpio.zfill(8)

                es_dni = (len(doc_limpio) == 8 and doc_limpio.isdigit())

                if es_dni:
                    print(f"[3/5] Ingresando DNI {doc_limpio}...")
                    inp_doc = frame.locator('[id="inicio.numeroDocumento"]').first
                    inp_doc.click()
                    inp_doc.fill(doc_limpio)
                    time.sleep(0.2)

                    try:
                        frame.locator('[id="inicio.pagoAnticipado.show"]').get_by_role("cell", name="No", exact=True).click(timeout=2500)
                    except Exception:
                        inp_doc.press("Tab")
                    _esperar_autocompletado_cliente(2.8)

                    # REGLA ESTRICTA: Si se proporcionó DNI y SUNAT/RENIEC lo rechaza, NUNCA emitir 'SIN DOCUMENTO' en silencio
                    if alerta_sunat[0]:
                        raise ValueError(
                            f"SUNAT/RENIEC rechazó el DNI {doc_limpio}: {alerta_sunat[1] or 'DNI no encontrado en RENIEC'}. "
                            f"Verifique y corrija el DNI del huésped antes de emitir."
                        )

                else:
                    nom_final = (cliente_nombre or "CLIENTES VARIOS").upper()
                    print(f"[3/5] Configurando 'SIN DOCUMENTO' para cliente: {nom_final}...")
                    wid_btn = frame.locator("[widgetid='inicio.tipoDocumento'] .dijitArrowButtonContainer").first
                    if wid_btn.count() > 0:
                        wid_btn.click()
                        time.sleep(0.5)
                        frame.locator(".dijitMenuItem").filter(has_text="SIN DOCUMENTO").first.click()
                        time.sleep(0.6)
                    else:
                        frame.evaluate("""() => {
                            if (window.dijit && dijit.byId('inicio.tipoDocumento')) {
                                dijit.byId('inicio.tipoDocumento').set('value', '-');
                            }
                        }""")
                        time.sleep(0.6)

                    rz = frame.locator('[id="inicio.razonSocial"]')
                    rz.click()
                    rz.fill(nom_final)
                    time.sleep(0.25)

            # Continuar a pantalla de ítems y esperar botón Adicionar
            frame.get_by_role("button", name="Continuar").dispatch_event("click")
            frame.get_by_role("button", name="Adicionar").wait_for(state="visible", timeout=10000)

            # Paso 2: Agregar Ítems
            print(f"[4/5] Agregando {len(items)} ítem(s) de hospedaje...")
            agregar_items_sunat(frame, items)

            # Continuar de Ítems -> Observaciones -> Vista Preliminar sin esperas ciegas largas
            print("   Avanzando a Vista Preliminar...")
            t_pre = time.time()
            ultimo_clic_cont = 0.0
            while time.time() - t_pre < 15.0:
                try:
                    if frame.get_by_role("button", name="Emitir").is_visible():
                        break
                except Exception:
                    pass
                if time.time() - ultimo_clic_cont >= 1.1:
                    try:
                        btn_c = frame.get_by_role("button", name="Continuar")
                        if btn_c.count() > 0 and btn_c.first.is_visible():
                            btn_c.first.dispatch_event("click")
                            ultimo_clic_cont = time.time()
                    except Exception:
                        pass
                time.sleep(0.25)

            if not emitir_final:
                ruta_cap = os.path.join(DIR_CAPTURAS, f"borrador_{tipo.lower()}_{int(time.time())}.png")
                png_bytes = page.screenshot(path=ruta_cap, full_page=True)
                if not frame.get_by_role("button", name="Emitir").is_visible():
                    raise RuntimeError("SUNAT no llegó a la Vista Preliminar (revise ítems/datos). Captura: " + os.path.basename(ruta_cap))
                print(f"🛑 [MODO BORRADOR / VISTA PRELIMINAR] Captura guardada en: {ruta_cap}")
                return {
                    "status": "OK",
                    "modo": "borrador",
                    "entorno_prueba": _es_entorno_prueba(),
                    "tipo": tipo,
                    "nro_comprobante": "BORRADOR-PRELIMINAR",
                    "captura": ruta_cap,
                    "nombre_captura": os.path.basename(ruta_cap),
                    "captura_b64": base64.b64encode(png_bytes).decode("ascii") if png_bytes else "",
                    "mensaje": "Vista Preliminar alcanzada con éxito en SUNAT (sin emitir)."
                }

            # Paso 3: Emisión Definitiva (SOLO si emitir_final == True y NO estamos en entorno de prueba)
            if emitir_final and not _es_entorno_prueba():
                print(f"[5/5] Emitiendo {tipo} definitiva...")
                estado["emitir_pulsado"] = True
                time.sleep(0.6)
                btn_conf = frame.locator("#dlgBtnAceptarConfirm, #dlgBtnAceptar")
                for intento_em in range(3):
                    try:
                        frame.get_by_role("button", name="Emitir").first.click(force=True, timeout=4000)
                    except Exception:
                        frame.get_by_role("button", name="Emitir").first.dispatch_event("click", timeout=4000)
                    try:
                        btn_conf.first.wait_for(state="visible", timeout=4500)
                        break
                    except Exception:
                        time.sleep(0.5)

                # Confirmar modal Aceptar
                try:
                    btn_conf.first.click(force=True, timeout=5000)
                except Exception:
                    try:
                        btn_conf.first.dispatch_event("click", timeout=5000)
                    except Exception:
                        frame.get_by_role("button", name="Aceptar").last.click(force=True, timeout=5000)

                # Esperar botón Descargar PDF
                btn_dl = frame.get_by_role("button", name="Descargar PDF").first
                btn_dl.wait_for(state="visible", timeout=20000)

                # Descargar PDF
                print("📥 Descargando PDF oficial de SUNAT...")
                with page.expect_download(timeout=25000) as dl_info:
                    try:
                        btn_dl.dispatch_event("click", timeout=5000)
                    except Exception:
                        btn_dl.click(force=True, timeout=5000)
                download = dl_info.value

                hoy_dt = datetime.now()
                carpeta_dia = f"{hoy_dt.day:02d}_{MESES_CARPETA.get(hoy_dt.month, 'Mes')}"
                dir_base_dest = os.path.join(DIR_FACTURAS if tipo == "FACTURA" else DIR_BOLETAS, carpeta_dia)
                os.makedirs(dir_base_dest, exist_ok=True)

                nombre_pdf = download.suggested_filename
                ruta_pdf = os.path.join(dir_base_dest, nombre_pdf)
                download.save_as(ruta_pdf)
                print(f"✅ Comprobante oficial guardado: {ruta_pdf}")

                # Copia inmediata en descargas_recepcion para disponibilidad del despachador WhatsApp
                ruta_recepcion = os.path.join(DIR_RECEPCION, nombre_pdf)
                try:
                    shutil.copy2(ruta_pdf, ruta_recepcion)
                except Exception:
                    pass

                # Detectar número de comprobante
                nro_cp = ""
                try:
                    txt_emitido = frame.locator("body").inner_text()
                    prefijo = "E001" if tipo == "FACTURA" else "EB01"
                    m_cp = re.search(rf'\b({prefijo}\s*-\s*\d+)\b', txt_emitido)
                    if m_cp:
                        nro_cp = m_cp.group(1).replace(" ", "")
                except Exception:
                    pass

                if not nro_cp and nombre_pdf:
                    m_cp = re.search(r'([EFB][B0-9]{3})-?(\d{1,8})', nombre_pdf)
                    if m_cp:
                        nro_cp = f"{m_cp.group(1)}-{int(m_cp.group(2))}"

                # Copia con nombre limpio legible en la carpeta del día y SIEMPRE en Descargas (Downloads)
                dir_downloads = os.path.expanduser("~/Downloads")
                os.makedirs(dir_downloads, exist_ok=True)
                ruta_downloads = os.path.join(dir_downloads, nombre_pdf)
                if nro_cp:
                    cli_slug = re.sub(r"[^A-Z0-9]+", "_", str(cliente_nombre or doc_limpio or "CLIENTE").upper()).strip("_")[:36]
                    pref_nom = "FACTURA" if tipo == "FACTURA" else "BOLETA"
                    nombre_limpio = f"{pref_nom}_{nro_cp}_{cli_slug}.pdf"
                    ruta_limpia = os.path.join(dir_base_dest, nombre_limpio)
                    ruta_downloads = os.path.join(dir_downloads, nombre_limpio)
                    try:
                        if os.path.abspath(ruta_limpia) != os.path.abspath(ruta_pdf):
                            shutil.copy2(ruta_pdf, ruta_limpia)
                    except Exception:
                        pass
                try:
                    shutil.copy2(ruta_pdf, ruta_downloads)
                    print(f"📂 Copia en Descargas guardada: {ruta_downloads}")
                except Exception:
                    pass

                print(f"📄 Comprobante emitido detectado: {nro_cp}")

                return {
                    "status": "OK",
                    "tipo": tipo,
                    "nro_comprobante": nro_cp,
                    "archivo_pdf": ruta_recepcion,
                    "archivo_carpeta_sunat": ruta_pdf,
                    "archivo_downloads": ruta_downloads,
                    "nombre_pdf": nombre_pdf,
                    "mensaje": f"{tipo} emitida exitosamente."
                }

        except Exception as e:
            print(f"❌ Error durante el flujo de emisión: {e}")
            import traceback
            traceback.print_exc()
            err_cap = os.path.join(DIR_CAPTURAS, f"error_emision_{tipo.lower()}.png")
            try:
                page.screenshot(path=err_cap, full_page=True)
                print(f"📸 Captura de error guardada en: {err_cap}")
            except Exception:
                pass
            return {
                "status": "ERROR",
                "tipo": tipo,
                "error": str(e)
            }
        finally:
            print("Cerrando sesión de navegador...")
            context.close()
            browser.close()


def main():
    parser = argparse.ArgumentParser(description="Emisor Unificado de Boletas y Facturas SUNAT (SEE-SOL)")
    parser.add_argument("--payload-json", required=True, help="JSON con todos los parámetros de la orden")
    args = parser.parse_args()

    payload = json.loads(args.payload_json)
    tipo = str(payload.get("tipo", "BOLETA")).upper().strip()
    doc_num = str(payload.get("documento", "")).strip()
    dni_huesped = payload.get("dni_huesped") if "dni_huesped" in payload else None
    cliente = str(payload.get("cliente", "CLIENTES VARIOS")).strip()
    items = payload.get("items", [])
    telefono = str(payload.get("telefono", "")).strip()
    enviar_wsp = bool(payload.get("enviar_wsp", False))
    emitir_final = bool(payload.get("emitir", True))
    if _es_entorno_prueba():
        emitir_final = False
        enviar_wsp = False
    omitir_sheets = bool(payload.get("omitir_sheets", False))
    headless_mode = bool(payload.get("headless", True))
    id_estancia = payload.get("id_estancia")
    fila_calzado = payload.get("fila_calzado")

    # Sumar total de la orden
    total_monto = sum(float(it.get("precio_unitario", it.get("monto", 0))) * int(it.get("cantidad", 1)) for it in items)

    # 1. Ejecutar Emisión (en prueba se fuerza emitir_final=False y enviar_wsp=False dentro de emitir_comprobante)
    res = emitir_comprobante(
        tipo=tipo,
        doc_num=doc_num,
        cliente_nombre=cliente,
        items=items,
        emitir_final=emitir_final,
        headless=headless_mode,
        telefono=telefono,
        enviar_wsp=enviar_wsp,
        dni_huesped=dni_huesped,
    )

    # 2. Registrar en Google Sheets SOLO si fue emisión definitiva en entorno operativo (nunca en vista preliminar / prueba)
    if (
        res.get("status") == "OK"
        and emitir_final
        and not _es_entorno_prueba()
        and res.get("modo") != "borrador"
        and res.get("nro_comprobante")
        and res.get("nro_comprobante") != "BORRADOR-PRELIMINAR"
        and id_estancia
        and not omitir_sheets
    ):
        try:
            print(f"📝 Registrando emisión en Google Sheets (Estancia {id_estancia}, Monto S/ {total_monto:.2f})...")
            tipo_label = "Factura" if tipo == "FACTURA" else "Boleta"
            registrar_emision(
                id_estancia=id_estancia,
                tipo_comprobante=tipo_label,
                nro_comprobante=res.get("nro_comprobante"),
                monto=total_monto,
                fila_calzado=fila_calzado
            )
            res["sheets_actualizado"] = True
        except Exception as e_sheet:
            print(f"[WARN] Error actualizando Sheets: {e_sheet}")
            res["sheets_actualizado"] = False
            res["sheets_error"] = str(e_sheet)

    # 3. Enviar por WhatsApp SOLO si fue emisión definitiva en entorno operativo (nunca en entorno de prueba)
    if (
        res.get("status") == "OK"
        and emitir_final
        and not _es_entorno_prueba()
        and res.get("modo") != "borrador"
        and enviar_wsp
        and telefono
    ):
        ruta_pdf = res.get("archivo_pdf")
        nro_cp = res.get("nro_comprobante", "")
        tipo_cp_label = "Factura" if tipo == "FACTURA" else "Boleta"
        mensaje = f"Estimado(a) {cliente}, le adjuntamos su {tipo_cp_label} Electrónica {nro_cp}. ¡Muchas gracias por su preferencia en Hostal El Óvalo Mariátegui!"

        print(f"📲 Despachando comprobante por WhatsApp de Recepción al {telefono}...")
        script_wsp = os.path.join(SCRIPTS_DIR, "brazo_wsp.py")
        cmd_wsp = [
            sys.executable,
            script_wsp,
            "--telefono", telefono,
            "--archivo", ruta_pdf,
            "--mensaje", mensaje
        ]
        try:
            proc_wsp = subprocess.run(
                cmd_wsp,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=CREATE_NO_WINDOW,
            )
            res["wsp_status"] = "ENVIADO" if proc_wsp.returncode == 0 else "ERROR_WSP"
            res["wsp_output"] = proc_wsp.stdout.strip()
            print(f"✅ Resultado WhatsApp: {res['wsp_status']}")
        except Exception as e_wsp:
            res["wsp_status"] = "ERROR_WSP"
            res["wsp_error"] = str(e_wsp)

    # Marcadores delimitadores para extracción inequívoca del JSON
    print("\nJSON_RESULT_START")
    print(json.dumps(res, ensure_ascii=False))
    print("JSON_RESULT_END")

    if res.get("status") not in ("OK", "SIMULADO_PRUEBA"):
        sys.exit(1)


if __name__ == "__main__":
    main()
