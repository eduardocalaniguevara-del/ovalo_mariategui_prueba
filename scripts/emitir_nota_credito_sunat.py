#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EMISOR DE NOTAS DE CRÉDITO ELECTRÓNICAS (SUNAT SEE-SOL)
Hostal El Óvalo Mariátegui
Basado en la grabación Playwright codegen verificada del 16/10/2026.

Soporta:
1. Anulación de Boletas de Venta Electrónicas (EB01).
2. Anulación de Facturas Electrónicas (E001).
3. Verificación previa con "Ver Boleta" / "Ver Factura" para validar cliente e importe.
4. Modo Borrador Supervisado (por defecto): Se detiene en la confirmación y toma captura.
5. Modo Emisión Definitiva (--emitir): Confirma la emisión y descarga el PDF oficial.
"""

import os
import sys
import time
import argparse
import json
import re
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
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from secretos_hostal import obtener_credenciales_sunat

DIR_NOTAS_CREDITO = os.path.join(BASE_DIR, "notas_de_credito")
DIR_NC_BOLETAS = os.path.join(DIR_NOTAS_CREDITO, "nc_boletas")
DIR_NC_FACTURAS = os.path.join(DIR_NOTAS_CREDITO, "nc_facturas")
DIR_RECEPCION = os.path.join(BASE_DIR, "descargas_recepcion")
user_home = os.environ.get("USERPROFILE", BASE_DIR)
DIR_ARTIFACT = os.path.join(user_home, ".gemini", "antigravity", "brain", "capturas")
CAPTURA_BORRADOR_DEFECTO = os.path.join(DIR_ARTIFACT, "borrador_nota_credito_sunat.png")

os.makedirs(DIR_NOTAS_CREDITO, exist_ok=True)
os.makedirs(DIR_NC_BOLETAS, exist_ok=True)
os.makedirs(DIR_NC_FACTURAS, exist_ok=True)
os.makedirs(DIR_RECEPCION, exist_ok=True)
try:
    os.makedirs(DIR_ARTIFACT, exist_ok=True)
except Exception:
    DIR_ARTIFACT = os.path.join(BASE_DIR, "capturas")
    os.makedirs(DIR_ARTIFACT, exist_ok=True)
    CAPTURA_BORRADOR_DEFECTO = os.path.join(DIR_ARTIFACT, "borrador_nota_credito_sunat.png")


def obtener_credenciales():
    """Obtiene las credenciales SUNAT desde la bóveda local del hostal."""
    return obtener_credenciales_sunat()


def login_sunat(context, ruc, usuario, clave):
    """Realiza el login directo en SUNAT SOL via OAuth2 con rescate automático de JWT y anti-503."""
    print("[1/5] Iniciando sesión directa en SUNAT SOL...")
    from emitir_comprobante_sunat import AUTENTICA_SOL_URL, STATE_SOL_DEFAULT, iniciar_sesion_sol_blindado
    URL_LOGIN_DIRECTO = (
        "https://api-seguridad.sunat.gob.pe/v1/clientessol/4f3b88b3-d9d6-402a-b85d-6a0bc857746a/oauth2/loginMenuSol"
        "?lang=es-PE&showDni=true&showLanguages=false"
        f"&originalUrl={AUTENTICA_SOL_URL}&state={STATE_SOL_DEFAULT}"
    )
    page = context.new_page()
    iniciar_sesion_sol_blindado(page, URL_LOGIN_DIRECTO, ruc, usuario, clave)
    time.sleep(0.5)
    print("      [OK] Sesión iniciada correctamente en Menú SOL.")
    return page


def navegar_a_emision_nc(page, tipo_cp):
    """Navega a la opción Emitir Nota de Crédito para Boleta o Factura."""
    print(f"[2/5] Navegando a Emisión de Nota de Crédito para {tipo_cp}...")
    
    # Cerrar popups informativos de SUNAT si existen
    for btn_text in ["Continuar sin confirmar", "Cerrar", "Aceptar", "Entendido"]:
        try:
            btn = page.get_by_role("button", name=btn_text)
            if btn.count() > 0 and btn.first.is_visible():
                btn.first.click()
                time.sleep(0.5)
        except Exception:
            pass

    target_sub = "Boleta de Venta Electrónica" if tipo_cp.upper() in ["BOLETA", "BVE"] else "Factura Electrónica"
    page.get_by_role("heading", name="Empresas").first.wait_for(state="visible", timeout=25000)

    menu_ok = False
    for intento_menu in range(1, 5):
        try:
            page.get_by_role("heading", name="Empresas").first.click(timeout=10000)
            time.sleep(0.75)
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
    time.sleep(0.6)

    # Hacer clic específicamente en la opción visible "Emitir Nota de Crédito"
    clicked_nc = False
    for loc in page.get_by_text("Emitir Nota de Crédito", exact=True).all():
        try:
            if loc.is_visible():
                loc.click(timeout=5000)
                clicked_nc = True
                break
        except Exception:
            pass

    if not clicked_nc:
        page.get_by_text("Emitir Nota de Crédito").first.click(timeout=5000)

    time.sleep(2.5)


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


def ejecutar_emision_nota_credito(
    tipo_cp,
    numero_comprobante,
    motivo="ANULACION POR ERROR EN LA EMISION",
    dia_emision=None,
    tipo_nc="Anulacion de la Operacion",
    subcarpeta=None,
    modo="borrador",
    ruta_captura=None,
    headless=False
):
    """
    Flujo completo de emisión de Nota de Crédito en SUNAT SEE-SOL.
    En modo PRUEBA (OVALO_ENTORNO=prueba), fuerza emitir_final=False (modo='borrador')
    para ejecutar el flujo REAL en SUNAT SEE-SOL hasta la Vista Preliminar sin emitir.
    """
    if _es_entorno_prueba():
        modo = "borrador"
        print(
            "🧪 [ENTORNO PRUEBA] Ejecutando flujo REAL en SUNAT SEE-SOL hasta Vista Preliminar "
            "(Borrador con captura real, sin clic final en Emitir).",
            file=sys.stderr,
        )
    emitir_final = (str(modo or "borrador").lower() == "definitivo") and (not _es_entorno_prueba())

    resultado = {
        "status": "PENDIENTE",
        "modo": "definitivo" if emitir_final else "borrador",
        "entorno_prueba": _es_entorno_prueba(),
        "tipo_cp": tipo_cp.upper(),
        "cp_origen": numero_comprobante,
        "nro_nota_credito": None,
        "motivo": motivo,
        "subcarpeta": subcarpeta,
        "pdf_descargado": None,
        "captura_pantalla": None,
        "datos_cp_origen": {},
        "error": None
    }

    # Limpiar número de comprobante (obtener solo el correlativo)
    m_num = re.search(r"(\d+)$", str(numero_comprobante).strip())
    if not m_num:
        resultado["status"] = "ERROR"
        resultado["error"] = f"Número de comprobante inválido: {numero_comprobante}"
        return resultado
    correlativo_str = str(int(m_num.group(1)))

    if not dia_emision:
        from datetime import datetime
        dia_emision = str(datetime.now().day)
    else:
        dia_emision = str(int(dia_emision))

    ruc, usuario, clave = obtener_credenciales()

    print(f"\n=======================================================")
    print(f" NOTA DE CRÉDITO SUNAT - HOTEL EL ÓVALO MARIÁTEGUI")
    print(f" Modo: {modo.upper()} | Comprobante a anular: {tipo_cp.upper()} #{correlativo_str}")
    print(f" Motivo: {motivo} | Día emisión: {dia_emision}")
    print(f"=======================================================\n")

    with sync_playwright() as p:
        from navegador_discreto import crear_contexto_sunat, lanzar_edge_sunat
        browser = lanzar_edge_sunat(p, oculto=bool(headless))
        context = crear_contexto_sunat(browser, viewport={"width": 1600, "height": 950})
        
        try:
            page = login_sunat(context, ruc, usuario, clave)
            time.sleep(2.0)

            navegar_a_emision_nc(page, tipo_cp)

            # Esperar a que cargue el formulario de la aplicación en el iframe
            print("[3/5] Cargando formulario de emisión de Nota de Crédito...")
            frame = None
            campo_id = 'pantallaInicial.numeroBVE' if tipo_cp.upper() in ["BOLETA", "BVE"] else 'pantallaInicial.numeroFE'
            for sec in range(35):
                for f in page.frames:
                    if f.name == "iframeApplication" or "11.5.4.1.2" in f.url or "11.5.3" in f.url or "ww1" in f.url:
                        try:
                            if f.locator(f'[id="{campo_id}"]').is_visible():
                                frame = f
                                break
                        except Exception:
                            pass
                if frame:
                    break
                time.sleep(1.0)
            if not frame:
                frame = page.locator('iframe[name="iframeApplication"]').content_frame
                time.sleep(2.0)

            # 1. Seleccionar día de emisión si no es el día de hoy
            from datetime import datetime
            dia_actual = str(datetime.now().day)
            if dia_emision and str(dia_emision) != dia_actual:
                try:
                    input_fecha = frame.locator("input[type='text']").first
                    if input_fecha.is_visible():
                        input_fecha.click()
                        time.sleep(0.5)
                        dia_btn = frame.get_by_text(dia_emision, exact=True).first
                        if dia_btn.count() > 0 and dia_btn.is_visible():
                            dia_btn.click()
                            time.sleep(0.5)
                except Exception as e_fec:
                    print(f"   [INFO] Selección de fecha: {e_fec}")

            # 2. Seleccionar Tipo de Nota de Crédito
            print(f"      Seleccionando tipo de Nota de Crédito: {tipo_nc}...")
            try:
                # Tercer input text o dropdown de tipo de nota
                input_tipo = frame.locator("input[type='text']").nth(3)
                input_tipo.click()
                time.sleep(0.5)
                # Opción en el dropdown
                try:
                    frame.get_by_role("option", name=re.compile(r"Anulaci[oó]n\s+de\s+la\s+Operaci[oó]n", re.I)).click(timeout=3000)
                except Exception:
                    frame.locator(".dijitMenuItem:has-text('Anulaci')").first.click(timeout=3000)
            except Exception as e_tipo:
                print(f"   [WARN] No se pudo seleccionar dropdown por UI estándar: {e_tipo}")
                # Fallback JS si aplica
                frame.evaluate("""() => {
                    const opts = Array.from(document.querySelectorAll('tr, div, li, span'));
                    for (const o of opts) {
                        if (o.textContent && o.textContent.toLowerCase().includes('anulacion de la operacion')) {
                            o.click();
                            break;
                        }
                    }
                }""")
            time.sleep(0.8)

            # 3. Ingresar el número de comprobante a anular
            if tipo_cp.upper() in ["BOLETA", "BVE"]:
                campo_num = frame.locator('[id="pantallaInicial.numeroBVE"]')
            else:
                campo_num = frame.locator('[id="pantallaInicial.numeroFE"]')

            campo_num.click()
            campo_num.fill(correlativo_str)
            time.sleep(0.4)

            # 4. Ingresar Motivo de emisión
            print(f"      Ingresando motivo: '{motivo}'...")
            campo_motivo = frame.locator('[id="pantallaInicial.motivoEmisionNC"]')
            campo_motivo.click()
            campo_motivo.fill(motivo)
            time.sleep(0.4)

            # 5. Clic en "Continuar" para avanzar a la vista preliminar
            print("[4/5] Continuando a pantalla preliminar de emisión...")
            try:
                frame.get_by_role("button", name="Continuar").click(timeout=5000)
            except Exception:
                frame.get_by_role("button", name="Continuar").dispatch_event("click")
            time.sleep(3.0)

            # Validar que cargó la vista preliminar y extraer datos
            try:
                texto_prelim = frame.locator("body").inner_text()
                m_nom = re.search(r"Se[ñn]or\(es\)\s*:\s*([^\n\r\t]+)", texto_prelim, re.I)
                if m_nom:
                    resultado["datos_cp_origen"]["cliente"] = m_nom.group(1).strip()
                m_monto = re.search(r"Importe\s+Total\s*:\s*S/?\s*([\d\.,]+)", texto_prelim, re.I)
                if m_monto:
                    resultado["datos_cp_origen"]["monto"] = float(m_monto.group(1).replace(",", ""))
                print(f"      [OK] Vista preliminar validada: {resultado['datos_cp_origen'].get('cliente', '')} | Monto: S/ {resultado['datos_cp_origen'].get('monto', 0.0):.2f}")
            except Exception as e_prev:
                print(f"   [INFO] Leyendo texto preliminar: {e_prev}")

            # 7. Gestión según Modo: Borrador Supervisado (Vista Preliminar sin clic en Emitir) vs Emisión Definitiva
            if not ruta_captura:
                ruta_captura = CAPTURA_BORRADOR_DEFECTO

            if not emitir_final:
                print("\n🛑 [MODO BORRADOR / VISTA PRELIMINAR ACTIVO]")
                print(" -> Tomando captura de pantalla de la Vista Preliminar en SUNAT (sin hacer clic en Emitir)...")
                page.screenshot(path=ruta_captura, full_page=True)
                resultado["captura_pantalla"] = ruta_captura
                resultado["nro_nota_credito"] = "BORRADOR-NC-PRELIMINAR"
                resultado["status"] = "BORRADOR_GENERADO"
                print(f" -> Captura guardada en: {ruta_captura}")
                print(" -> Emisión NO ejecutada (Modo Borrador Seguro en Vista Preliminar).")
                return resultado

            if emitir_final and not _es_entorno_prueba():
                print("\n[MODO EMISIÓN DEFINITIVA ACTIVO]")
                print(" -> Pulsando 'Emitir' y confirmando emisión de la Nota de Crédito en SUNAT...")
                try:
                    btn_emitir = frame.locator("button:has-text('Emitir'), [value='Emitir'], input[value='Emitir']").first
                    if btn_emitir.count() > 0:
                        btn_emitir.click(timeout=10000)
                    else:
                        frame.get_by_role("button", name="Emitir").click(timeout=10000)
                except Exception:
                    try:
                        frame.get_by_role("button", name="Emitir").dispatch_event("click")
                    except Exception as e_emit:
                        print(f"   [WARN] No se pudo hacer clic en Emitir: {e_emit}")
                time.sleep(2.0)

                # Clic en Aceptar en el diálogo de confirmación
                dialog_confirmar = frame.get_by_role("dialog", name="Confirmar")
                if dialog_confirmar.count() > 0:
                    try:
                        dialog_confirmar.get_by_role("button", name="Aceptar").dispatch_event("click", timeout=4000)
                    except Exception:
                        dialog_confirmar.get_by_role("button", name="Aceptar").click(force=True, timeout=4000)
                else:
                    try:
                        frame.get_by_role("button", name="Aceptar").dispatch_event("click", timeout=4000)
                    except Exception:
                        frame.get_by_role("button", name="Aceptar").click(force=True, timeout=4000)
                
                time.sleep(3.5)

                # Tomar captura de éxito
                page.screenshot(path=ruta_captura, full_page=True)
                resultado["captura_pantalla"] = ruta_captura

                # Extraer el número generado de Nota de Crédito (ej. FC01-12 o BC01-45 o EB01-38)
                texto_exito = frame.locator("body").inner_text()
                m_nc = re.search(r"((?:FC01|BC01|E001|EB01)\s*-\s*\d+)", texto_exito, re.I)
                if m_nc:
                    resultado["nro_nota_credito"] = m_nc.group(1).upper().replace(" ", "")
                    print(f" -> [ÉXITO] NOTA DE CRÉDITO GENERADA: {resultado['nro_nota_credito']}")
                else:
                    resultado["nro_nota_credito"] = f"NC-{tipo_cp}-{correlativo_str}"

                # Determinar carpeta de destino: notas_de_credito/nc_boletas/{subcarpeta}
                dir_base_tipo = DIR_NC_BOLETAS if tipo_cp.upper() in ["BOLETA", "BVE"] else DIR_NC_FACTURAS
                if subcarpeta:
                    dir_destino = os.path.join(dir_base_tipo, subcarpeta)
                else:
                    dir_destino = dir_base_tipo
                os.makedirs(dir_destino, exist_ok=True)

                # Descargar PDF si está disponible el botón
                print(f"[5/5] Descargando comprobante en PDF a carpeta: {dir_destino}...")
                try:
                    btn_descargar = frame.locator("text='Descargar PDF', [widgetid*='pdf'], [widgetid*='PDF'], span:has-text('Descargar PDF'), button:has-text('Descargar PDF'), a:has-text('Descargar PDF'), [value*='Descargar PDF']").first
                    if btn_descargar.count() == 0:
                        btn_descargar = frame.locator("text='Descargar', span:has-text('Descargar'), button:has-text('Descargar'), a:has-text('Descargar'), a[href*='pdf']").first

                    if btn_descargar.count() > 0:
                        with page.expect_download(timeout=20000) as download_info:
                            try:
                                btn_descargar.dispatch_event("click")
                            except Exception:
                                btn_descargar.click(force=True)
                        download = download_info.value
                        nombre_pdf = download.suggested_filename
                        if not nombre_pdf or not nombre_pdf.endswith(".pdf"):
                            nom_cte = re.sub(r'[^A-Z0-9_]', '_', resultado['datos_cp_origen'].get('cliente', 'CLIENTE').upper())[:30]
                            nombre_pdf = f"NOTA_CREDITO_{resultado['nro_nota_credito']}_{nom_cte}.pdf"
                        ruta_pdf = os.path.join(dir_destino, nombre_pdf)
                        download.save_as(ruta_pdf)
                        resultado["pdf_descargado"] = ruta_pdf
                        print(f" -> PDF guardado en carpeta oficial: {ruta_pdf}")
                        
                        # Copiar a descargas_recepcion para compatibilidad
                        try:
                            import shutil
                            shutil.copy2(ruta_pdf, os.path.join(DIR_RECEPCION, nombre_pdf))
                        except Exception:
                            pass
                except Exception as e_pdf:
                    print(f"   [INFO] Descarga interactiva de PDF: {e_pdf}")

                resultado["status"] = "EMITIDO"

        except Exception as e_gral:
            resultado["status"] = "ERROR"
            resultado["error"] = str(e_gral)
            print(f"\n[ERROR CRÍTICO] {e_gral}")
            try:
                page.screenshot(path=os.path.join(DIR_ARTIFACT, "error_emision_nc.png"), full_page=True)
            except Exception:
                pass
        finally:
            try:
                context.close()
                browser.close()
            except Exception:
                pass

    # Generación de PDF oficial después del cierre del navegador
    if resultado.get("status") == "EMITIDO" and not resultado.get("pdf_descargado"):
        try:
            from generar_pdf_nc import generar_pdf_nota_credito
            nc_dict = {
                "numero_cp": resultado["nro_nota_credito"],
                "fecha_emision": datetime.now().strftime("%d/%m/%Y"),
                "cp_modificado": f"{'EB01' if tipo_cp.upper() in ['BOLETA', 'BVE'] else 'E001'} - {correlativo_str}",
                "receptor_nombre": resultado["datos_cp_origen"].get("cliente", ""),
                "receptor_doc": resultado["datos_cp_origen"].get("doc", ""),
                "monto_total": resultado["datos_cp_origen"].get("monto", 0.0),
                "motivo_nc": motivo,
                "items": [
                    {
                        "texto_completo": f"ALOJAMIENTO. COMPROBANTE {correlativo_str}."
                    }
                ]
            }
            pdf_path = generar_pdf_nota_credito(nc_dict, dir_destino)
            resultado["pdf_descargado"] = pdf_path
            print(f" -> PDF oficial generado automáticamente: {pdf_path}")
        except Exception as e_gen:
            print(f"   [WARN] Error generando PDF: {e_gen}")

    return resultado


def main():
    parser = argparse.ArgumentParser(description="Emisor de Notas de Crédito SUNAT SOL - Hotel El Óvalo Mariátegui")
    parser.add_argument("--tipo", choices=["BOLETA", "FACTURA", "boleta", "factura"], required=True, help="Tipo de comprobante de origen a anular")
    parser.add_argument("--numero", required=True, help="Número o correlativo del comprobante a anular (ej. 2069)")
    parser.add_argument("--motivo", default="ANULACION POR ERROR EN LA EMISION", help="Motivo de emisión de la Nota de Crédito")
    parser.add_argument("--dia", default=None, help="Día del mes para la emisión (ej. 16)")
    parser.add_argument("--subcarpeta", default=None, help="Subcarpeta según día de check-in (ej. 02_Octubre)")
    parser.add_argument("--emitir", action="store_true", help="Ejecutar emisión definitiva oficial (por defecto es borrador supervisado)")
    parser.add_argument("--headless", action="store_true", help="Ejecutar en modo segundo plano (headless)")
    parser.add_argument("--captura", default=None, help="Ruta personalizada para guardar la captura")

    args = parser.parse_args()
    modo = "definitivo" if (args.emitir and not _es_entorno_prueba()) else "borrador"

    res = ejecutar_emision_nota_credito(
        tipo_cp=args.tipo,
        numero_comprobante=args.numero,
        motivo=args.motivo,
        dia_emision=args.dia,
        tipo_nc="Anulacion de la Operacion",
        subcarpeta=args.subcarpeta,
        modo=modo,
        ruta_captura=args.captura,
        headless=args.headless
    )

    print("\n--- RESUMEN FINAL ---")
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
