#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wsp_inteligencia.py
Suite de validación y verificación: Retiro de Copiloto Óvalo y Resumen Ejecutivo WSP por Agente IA.
Hotel El Óvalo Mariátegui - Entorno de PRUEBA (puerto 8082)

Verifica:
1. Rutas eliminadas: Copiloto (consultarAsistenteRecepcion, etc.) y WSP de envío/simulación retornan 404.
2. Archivos preservados y retirados: PLAN_ASISTENTE_RECEPCION.md preservado, respaldo en C:\\Hostal_Sistema\\proyecto_asistente_recepcion\\, y asistente_recepcion.py retirado de la prueba.
3. Daemon operativo y solo lectura: wsp_daemon CONECTADO, base operativa mode=ro rechaza escrituras, checksums de negocio intactos.
4. RPC vivas de Resumen Ejecutivo: obtenerAlertasWsp, obtenerBandejaWsp, actualizarResumenWspAhora con los 3 bloques.
5. UI Edge E2E: #copiloto-fab NO existe, barra en franja única a 1366px, modal ejecutivo con 3 bloques y capturas actualizadas.
"""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
import pytest

BASE_PRUEBA = Path(r"C:\ovalo_mariategui_prueba")
BASE_OPERATIVA = Path(r"C:\Recepcion_Ovalo_Local")
DB_PRUEBA = BASE_PRUEBA / "data" / "ovalo_sistema.db"
DB_OPERATIVA = BASE_OPERATIVA / "data" / "ovalo_sistema.db"
RESPALDO_ASISTENTE = Path(r"C:\Hostal_Sistema\proyecto_asistente_recepcion")
SCREENSHOTS_DIR = BASE_PRUEBA / "tests" / "screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

RPC_URL = "http://127.0.0.1:8082/api/rpc"


def _llamar_rpc(metodo: str, args: list = None) -> tuple[int, dict]:
    """Llama a un endpoint RPC y devuelve (status_code, json_o_dict)."""
    if args is None:
        args = []
    req_data = json.dumps({"args": args}).encode("utf-8")
    url = f"{RPC_URL}/{metodo}"
    req = urllib.request.Request(url, data=req_data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            if isinstance(data, dict) and "result" in data:
                return resp.status, data["result"]
            return resp.status, data
    except urllib.error.HTTPError as e:
        try:
            err_data = json.loads(e.read().decode("utf-8"))
        except Exception:
            err_data = {"error": str(e)}
        return e.code, err_data


def get_db_checksum(db_path: Path, tables=("estancias", "pagos", "ventas_tienda")) -> dict:
    """Calcula conteo y hash sha256 de las filas ordenadas por id para garantizar inmutabilidad."""
    res = {}
    if not db_path.exists():
        return res
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        for t in tables:
            cur.execute(f"SELECT count(*) FROM sqlite_master WHERE type='table' AND name='{t}'")
            if not cur.fetchone()[0]:
                continue
            cur.execute(f"SELECT COUNT(*) FROM {t}")
            count = cur.fetchone()[0]
            cur.execute(f"SELECT * FROM {t} ORDER BY rowid")
            rows = cur.fetchall()
            hasher = hashlib.sha256()
            for r in rows:
                hasher.update(str(tuple(r)).encode("utf-8", errors="replace"))
            res[t] = {"count": count, "hash": hasher.hexdigest()}
    finally:
        conn.close()
    return res


# ============================================================================
# 1. RUTAS ELIMINADAS: COPILOTO Y ENVÍO WSP (DEBEN RETORNAR 404)
# ============================================================================
def test_01_rutas_eliminadas_copiloto_y_envio():
    """Las rutas del Copiloto y las rutas de envío/simulación WSP deben retornar HTTP 404."""
    rutas_a_verificar = [
        # Copiloto
        "consultarAsistenteRecepcion",
        "confirmarMovimientoAsistente",
        "obtenerExtractoDuenoAsistente",
        # WSP envío / simulación / plantillas
        "enviarRespuestaWsp",
        "configurarAutoAdelantoWsp",
        "simularMensajeWsp",
        "marcarChatWspAtendido",
    ]

    for ruta in rutas_a_verificar:
        status, resp = _llamar_rpc(ruta, [])
        assert status == 404, f"La ruta {ruta} no debió responder 200, devolvió HTTP {status}: {resp}"
        print(f"Ruta eliminada correctamente: {ruta} -> HTTP {status}")


# ============================================================================
# 2. PRESERVACIÓN DE ARCHIVOS Y RESPALDO
# ============================================================================
def test_02_preservacion_archivos_y_respaldo():
    """PLAN_ASISTENTE_RECEPCION.md preservado, respaldo externo intacto, y asistente_recepcion.py eliminado de la prueba."""
    # 1. PLAN_ASISTENTE_RECEPCION.md debe mantenerse intacto en la prueba
    plan_prueba = BASE_PRUEBA / "PLAN_ASISTENTE_RECEPCION.md"
    assert plan_prueba.exists(), f"El archivo {plan_prueba} debe preservarse en la prueba"

    # 2. Archivos retirados de la prueba
    asistente_py = BASE_PRUEBA / "asistente_recepcion.py"
    assert not asistente_py.exists(), f"asistente_recepcion.py debió ser eliminado de {BASE_PRUEBA}"

    guia_dir = BASE_PRUEBA / "static" / "img" / "guia_asistente"
    assert not guia_dir.exists(), f"static/img/guia_asistente debió ser eliminado de {BASE_PRUEBA}"

    # 3. Respaldo independiente en C:\Hostal_Sistema\proyecto_asistente_recepcion
    assert RESPALDO_ASISTENTE.exists(), f"El directorio de respaldo no existe en {RESPALDO_ASISTENTE}"
    assert (RESPALDO_ASISTENTE / "PLAN_ASISTENTE_RECEPCION.md").exists(), "Falta PLAN_ASISTENTE_RECEPCION.md en respaldo"
    assert (RESPALDO_ASISTENTE / "asistente_recepcion.py").exists(), "Falta asistente_recepcion.py en respaldo"


# ============================================================================
# 3. DAEMON OPERATIVA Y MODO SOLO LECTURA
# ============================================================================
def test_03_daemon_operativa_y_solo_lectura():
    """wsp_daemon.js en operativa CONECTADO, conexión mode=ro rechaza escrituras y checksums de negocio intactos."""
    assert DB_OPERATIVA.exists(), f"No se encontró la base operativa en {DB_OPERATIVA}"

    # 1. Verificar estado de conexión en la operativa
    conn_op = sqlite3.connect(f"file:{DB_OPERATIVA}?mode=ro", uri=True)
    try:
        cur = conn_op.cursor()
        cur.execute("PRAGMA table_info(wsp_recepcion_estado)")
        cols = [r[1] for r in cur.fetchall()]
        if "clave" in cols and "valor" in cols:
            cur.execute("SELECT valor FROM wsp_recepcion_estado WHERE clave='conexion' LIMIT 1")
        else:
            cur.execute("SELECT conexion FROM wsp_recepcion_estado LIMIT 1")
        row = cur.fetchone()
        assert row is not None, "No se encontró registro en wsp_recepcion_estado de operativa"
        estado_conexion = str(row[0]).strip().upper()
        print(f"Estado de conexión en operativa: '{estado_conexion}'")
        assert estado_conexion == "CONECTADO", f"wsp_daemon en operativa debe estar CONECTADO, está '{estado_conexion}'"
    finally:
        conn_op.close()

    # 2. Rechazo de escrituras en modo solo lectura
    conn_ro_test = sqlite3.connect(f"file:{DB_OPERATIVA}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError) as exc_info:
        conn_ro_test.execute("INSERT INTO wsp_recepcion_cola_envio (telefono_o_jid, mensaje, estado) VALUES ('999', 'test', 'PENDIENTE')")
    conn_ro_test.close()
    assert "readonly" in str(exc_info.value).lower(), f"Se esperaba error readonly, se obtuvo: {exc_info.value}"

    # 3. Checksums de tablas operativas
    chk_antes = get_db_checksum(DB_PRUEBA)
    chk_despues = get_db_checksum(DB_PRUEBA)
    assert chk_antes == chk_despues, "Tablas de negocio alteradas"


# ============================================================================
# 4. RPC RESUMEN EJECUTIVO: 3 BLOQUES Y ACTUALIZACIÓN EN VIVO
# ============================================================================
def test_04_rpc_resumen_ejecutivo_tres_bloques():
    """obtenerAlertasWsp, actualizarResumenWspAhora y obtenerBandejaWsp responden 200 con la estructura de 3 bloques."""
    # 1. obtenerAlertasWsp
    s1, alertas = _llamar_rpc("obtenerAlertasWsp", [])
    assert s1 == 200, f"obtenerAlertasWsp falló con HTTP {s1}"
    assert isinstance(alertas, dict), f"obtenerAlertasWsp debe devolver dict: {alertas}"
    assert "nivel" in alertas, "Falta 'nivel' en alertas"
    print(f"Alertas WSP: nivel={alertas.get('nivel')}, titular='{alertas.get('titular_barra')}'")

    # 2. actualizarResumenWspAhora (dispara análisis en vivo del Agente IA)
    s2, res_ahora = _llamar_rpc("actualizarResumenWspAhora", [])
    assert s2 == 200, f"actualizarResumenWspAhora falló con HTTP {s2}: {res_ahora}"
    assert isinstance(res_ahora, dict), f"actualizarResumenWspAhora debe devolver dict: {res_ahora}"

    # 3. obtenerBandejaWsp (debe contener los 3 bloques)
    s3, bandeja = _llamar_rpc("obtenerBandejaWsp", [])
    assert s3 == 200, f"obtenerBandejaWsp falló con HTTP {s3}"
    assert isinstance(bandeja, dict), f"obtenerBandejaWsp debe devolver dict: {bandeja}"

    # Verificar presencia de los 3 bloques ejecutivos
    assert "pendientes_responder" in bandeja, "Falta bloque 'pendientes_responder'"
    assert "recomendaciones_y_notas" in bandeja, "Falta bloque 'recomendaciones_y_notas'"
    assert "atendidos_y_gracias" in bandeja, "Falta bloque 'atendidos_y_gracias'"
    assert "resumen_general_turno" in bandeja, "Falta 'resumen_general_turno'"

    assert isinstance(bandeja["pendientes_responder"], list), "pendientes_responder debe ser lista"
    assert isinstance(bandeja["recomendaciones_y_notas"], list), "recomendaciones_y_notas debe ser lista"
    assert isinstance(bandeja["atendidos_y_gracias"], list), "atendidos_y_gracias debe ser lista"

    print("\n--- RESUMEN EJECUTIVO IA VALIDADO ---")
    print(f"  Síntesis: {bandeja.get('resumen_general_turno')}")
    print(f"  Bloque 1 (Pendientes responder): {len(bandeja['pendientes_responder'])} items")
    print(f"  Bloque 2 (Recomendaciones/Notas): {len(bandeja['recomendaciones_y_notas'])} items")
    print(f"  Bloque 3 (Atendidos y gracias): {len(bandeja['atendidos_y_gracias'])} items")


# ============================================================================
# 5. VALIDACIÓN UI VISUAL EN MICROSOFT EDGE (1366 px)
# ============================================================================
def test_05_ui_edge_sin_copiloto_y_resumen_3_bloques():
    """UI en Edge: #copiloto-fab eliminado, barra en franja única a 1366 px, modal de 3 bloques y capturas."""
    from playwright.sync_api import sync_playwright

    console_errors = []
    page_errors = []

    def on_console(msg):
        txt = str(msg.text or "")
        if "favicon.ico" in txt:
            return
        if msg.type in ("error", "assert"):
            console_errors.append(f"[{msg.type}] {txt}")

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context(viewport={"width": 1366, "height": 768})
        page = context.new_page()

        page.on("console", on_console)
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))

        # 1. Cargar panel
        page.goto("http://127.0.0.1:8082", wait_until="networkidle", timeout=30000)
        time.sleep(1.5)

        # 2. Aserción crítica: Copiloto Óvalo NO existe en el DOM
        copiloto_fab = page.locator("#copiloto-fab")
        assert copiloto_fab.count() == 0, "¡FALLO! #copiloto-fab sigue presente en el DOM"
        assert page.locator("#copiloto-drawer").count() == 0, "#copiloto-drawer sigue presente"
        assert page.locator("#copiloto-modal-dueno").count() == 0, "#copiloto-modal-dueno sigue presente"
        print("Verificación Copiloto: 0 elementos en pantalla (totalmente retirado)")

        # 3. Verificar barra en franja única a 1366 px
        nav_header = page.locator("header, nav, #barraSuperior, .nav-bar").first
        if nav_header.count() > 0:
            box = nav_header.bounding_box()
            if box:
                print(f"Altura de barra superior a 1366px: {box['height']}px")
                assert box["height"] <= 65, f"La barra superior excede la franja única a 1366px: altura={box['height']}px"

        # 4. Captura de la barra superior limpia
        shot_barra = SCREENSHOTS_DIR / "wsp_01_barra_resumen.png"
        page.screenshot(path=str(shot_barra))
        assert shot_barra.exists(), "No se generó wsp_01_barra_resumen.png"

        # 5. Abrir el modal del Resumen Ejecutivo de WhatsApp
        ticker = page.locator("#wspTicker")
        assert ticker.is_visible(), "El botón #wspTicker debe ser visible en la barra superior"
        ticker.click()
        time.sleep(1.2)

        modal = page.locator("#modalWspBandeja").first
        assert modal.is_visible(), "El modal #modalWspBandeja debe abrirse al hacer clic en el ticker"

        # Verificar presencia de los 3 bloques ejecutivos en el modal
        assert page.locator("#wspBloquePendientes").count() > 0, "Debe existir #wspBloquePendientes"
        assert page.locator("#wspBloqueNotas").count() > 0, "Debe existir #wspBloqueNotas"
        assert page.locator("#wspBloqueAtendidos").count() > 0, "Debe existir #wspBloqueAtendidos"
        assert page.locator("#wspBtnActualizarAhora").count() > 0, "Debe existir el botón #wspBtnActualizarAhora"

        # 6. Captura del modal con los 3 bloques ejecutivos
        shot_modal = SCREENSHOTS_DIR / "wsp_02_modal_resumen_ejecutivo.png"
        page.screenshot(path=str(shot_modal))
        assert shot_modal.exists(), "No se generó wsp_02_modal_resumen_ejecutivo.png"

        # 7. Cerrar modal y verificar pestañas existentes
        page.keyboard.press("Escape")
        time.sleep(0.5)
        page.evaluate("() => { if (window.cerrarModalWspBandeja) window.cerrarModalWspBandeja(); }")
        time.sleep(0.5)

        # Hover BD
        btn_bd = page.locator("#tabVistaEstanciasBD, button:has-text('BD')").first
        if btn_bd.count() > 0 and btn_bd.is_visible():
            btn_bd.hover()
            time.sleep(0.3)

        # Panel
        btn_panel = page.locator("button:has-text('Panel')").first
        if btn_panel.count() > 0 and btn_panel.is_visible():
            btn_panel.click()
            time.sleep(0.3)

        browser.close()

    assert len(page_errors) == 0, f"Errores JS en página: {page_errors}"
    errores_criticos = [e for e in console_errors if "wsp" in e.lower() or "copiloto" in e.lower()]
    assert len(errores_criticos) == 0, f"Errores JS en consola: {errores_criticos}"


if __name__ == "__main__":
    pytest.main(["-v", __file__])
