#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_wsp_inteligencia.py
Suite de validación y verificación de Visor de WhatsApp en vivo y Motor de Inteligencia.
Hotel El Óvalo Mariátegui - Entorno de PRUEBA (puerto 8082)

Verifica las 8 áreas requeridas:
1. Clasificación sobre tests/fixtures_wsp_mensajes.json (>=85% aciertos por categoría, 0 falsos pendientes en RUIDO)
2. extraer_entidades (50 como precio no habitación, 'Soy 304' -> ['304'], RUC y DNI)
3. Lógica de pendiente (ráfaga de 4 = 1 pendiente, respuesta apaga, marcar_atendido apaga, nuevo msg reabre)
4. Internos nunca en PENDIENTES
5. SEGURIDAD: MODO_PRUEBA 'SIMULADO', 0 escrituras en operativa, checksums estancias/pagos/ventas intactos, mode=ro rechaza escrituras
6. promover_a_operativa.py excluye auth_info/ y media/
7. RPC vivas en 8082 para las 6 RPC
8. UI con Edge/Playwright (viewport 1366px franja única, capturas OK/AMBAR/modal, pestañas antiguas intactas)
"""

import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import sqlite3
from pathlib import Path
import pytest

# Rutas del entorno
BASE_PRUEBA = Path(r"C:\ovalo_mariategui_prueba")
BASE_OPERATIVA = Path(r"C:\Recepcion_Ovalo_Local")
DB_PRUEBA = BASE_PRUEBA / "data" / "ovalo_sistema.db"
DB_OPERATIVA = BASE_OPERATIVA / "data" / "ovalo_sistema.db"
FIXTURES_PATH = BASE_PRUEBA / "tests" / "fixtures_wsp_mensajes.json"
SCREENSHOTS_DIR = BASE_PRUEBA / "tests" / "screenshots"
SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

# Añadir base prueba al sys.path para importar wsp_inteligencia si existe
if str(BASE_PRUEBA) not in sys.path:
    sys.path.insert(0, str(BASE_PRUEBA))


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
# 1. CLASIFICACIÓN SOBRE FIXTURES
# ============================================================================
def test_01_clasificacion_fixtures():
    """Clasificación sobre fixtures_wsp_mensajes.json: >=85% aciertos por categoría; 0 falsos pendientes en RUIDO."""
    assert FIXTURES_PATH.exists(), f"El archivo de fixtures no existe aún en: {FIXTURES_PATH}"
    
    with open(FIXTURES_PATH, "r", encoding="utf-8") as f:
        fixtures = json.load(f)
    
    assert len(fixtures) >= 60, f"Se esperaban al menos 60 fixtures, se encontraron {len(fixtures)}"

    import wsp_inteligencia as wi

    categorias_evaluar = ("COMPROBANTE", "RESERVA", "HABITACION", "VOUCHER")
    conteos = {cat: {"total": 0, "aciertos": 0} for cat in categorias_evaluar}
    ruido_total = 0
    ruido_falsos_pendientes = 0

    for item in fixtures:
        texto = item.get("texto", "")
        tipo = item.get("tipo_mensaje", "texto")
        etiqueta = item.get("etiqueta")
        ocr = item.get("ocr")
        
        pred = wi.clasificar(texto, tipo, ocr)
        cat_pred = pred.get("categoria")
        es_ruido = pred.get("es_ruido", False)

        if etiqueta in categorias_evaluar:
            conteos[etiqueta]["total"] += 1
            if cat_pred == etiqueta:
                conteos[etiqueta]["aciertos"] += 1

        if etiqueta == "RUIDO":
            ruido_total += 1
            # Un ruido jamás debe generar alerta ni dar categoría activa sin es_ruido=True
            if not es_ruido and cat_pred in categorias_evaluar:
                ruido_falsos_pendientes += 1

    print("\n--- RESULTADOS DE CLASIFICACIÓN DE FIXTURES ---")
    for cat, data in conteos.items():
        t = data["total"]
        a = data["aciertos"]
        pct = (a / t * 100.0) if t > 0 else 100.0
        print(f"  {cat:12s}: {a}/{t} ({pct:.1f}%)")
        if t > 0:
            assert pct >= 85.0, f"Categoría {cat} tiene {pct:.1f}% de aciertos (< 85%)"

    print(f"  RUIDO       : {ruido_total} evaluados, {ruido_falsos_pendientes} falsos pendientes")
    assert ruido_falsos_pendientes == 0, f"Se encontraron {ruido_falsos_pendientes} falsos pendientes en mensajes RUIDO"

    # Casos obligatorios del plan
    c1 = wi.clasificar("Buen dia Estoy a la espera de mi factura", "texto", None)
    assert c1.get("categoria") == "COMPROBANTE", f"Fallo caso obligatorio factura: {c1}"

    c2 = wi.clasificar("Una abitacion de 50 tiene", "texto", None)
    assert c2.get("categoria") == "RESERVA", f"Fallo caso obligatorio habitacion 50: {c2}"

    c3 = wi.clasificar("Dele la llave", "texto", None)
    assert c3.get("categoria") == "HABITACION", f"Fallo caso obligatorio llave: {c3}"

    c4 = wi.clasificar("Ok, gracias de todos modos", "texto", None)
    assert c4.get("es_ruido") is True or c4.get("categoria") is None, f"Fallo caso obligatorio ruido: {c4}"

    c5 = wi.clasificar("[Sticker]", "sticker", None)
    assert c5.get("es_ruido") is True or c5.get("categoria") is None, f"Fallo caso obligatorio sticker: {c5}"


# ============================================================================
# 2. EXTRACCIÓN DE ENTIDADES
# ============================================================================
def test_02_extraer_entidades():
    """'Una abitacion de 50 tiene' -> habs=[]; 'Soy 304' -> ['304']; RUC 20xxxxxxxxx y DNI 8 dígitos."""
    import wsp_inteligencia as wi

    habs_validas = {"101", "102", "103", "104", "105", "201", "202", "301", "302", "304", "401", "406"}

    # Caso 50 es precio, no habitación
    e1 = wi.extraer_entidades("Una abitacion de 50 tiene", habs_validas)
    assert e1.get("habs") == [], f"El 50 no debe interpretarse como habitación: {e1}"

    # Caso 'Soy 304'
    e2 = wi.extraer_entidades("Buenas noches, soy 304 necesito una toalla", habs_validas)
    assert "304" in (e2.get("habs") or []), f"Debe extraer hab 304: {e2}"

    # Caso RUC (20xxxxxxxxx) y DNI (8 dígitos)
    e3 = wi.extraer_entidades("Factura para RUC 20601234567 por favor", habs_validas)
    assert e3.get("ruc") == "20601234567", f"Debe extraer RUC 20601234567: {e3}"

    e4 = wi.extraer_entidades("Boleta a nombre de DNI 71234567", habs_validas)
    assert e4.get("dni") == "71234567", f"Debe extraer DNI 71234567: {e4}"


# ============================================================================
# 3. LÓGICA DE PENDIENTES
# ============================================================================
def test_03_logica_pendientes():
    """Ráfaga de 4 mensajes = 1 pendiente; respuesta es_mio=1 la apaga; marcar_atendido la apaga; mensaje posterior la reabre."""
    import wsp_inteligencia as wi

    jid_test = "SIM-51999888777@s.whatsapp.net"
    nombre_test = "Cliente Test Rafaga"

    try:
        # 1. Ráfaga de 4 mensajes entrantes
        wi.simular_mensaje(jid_test, nombre_test, "Hola buenas tardes")
        wi.simular_mensaje(jid_test, nombre_test, "Tienen habitacion matrimonial?")
        wi.simular_mensaje(jid_test, nombre_test, "Para hoy en la noche")
        wi.simular_mensaje(jid_test, nombre_test, "A cuanto esta?")

        bandeja = wi.obtener_bandeja_wsp("PENDIENTES")
        # Puede ser lista directa de tarjetas o dict con llave 'tarjetas'
        tarjetas = bandeja.get("tarjetas", bandeja) if isinstance(bandeja, dict) else bandeja
        chats_sim = [t for t in tarjetas if t.get("jid") == jid_test]

        assert len(chats_sim) == 1, f"La ráfaga de 4 mensajes debe consolidarse en exactamente 1 pendiente, hubo {len(chats_sim)}"
        
        # 2. Simular respuesta de recepción (es_mio=1 o enviar_respuesta)
        wi.enviar_respuesta(jid_test, "Hola, sí tenemos matrimonial a S/ 75", usuario="RecepcionTest")
        
        bandeja_post_resp = wi.obtener_bandeja_wsp("PENDIENTES")
        tarjetas_post = bandeja_post_resp.get("tarjetas", bandeja_post_resp) if isinstance(bandeja_post_resp, dict) else bandeja_post_resp
        chats_post = [t for t in tarjetas_post if t.get("jid") == jid_test]
        assert len(chats_post) == 0, "La respuesta de recepción (es_mio=1) debió apagar el pendiente"

        # 3. Nuevo mensaje del cliente lo reabre (esperar >1s para que avance el timestamp entero de SQLite)
        time.sleep(1.2)
        wi.simular_mensaje(jid_test, nombre_test, "Perfecto, me la separa por favor?")
        bandeja_reabierta = wi.obtener_bandeja_wsp("PENDIENTES")
        tarjetas_reab = bandeja_reabierta.get("tarjetas", bandeja_reabierta) if isinstance(bandeja_reabierta, dict) else bandeja_reabierta
        chats_reab = [t for t in tarjetas_reab if t.get("jid") == jid_test]
        assert len(chats_reab) == 1, "Un nuevo mensaje del cliente debe reabrir el pendiente"

        # 4. Marcar atendido lo apaga
        time.sleep(1.2)
        wi.marcar_atendido(jid_test, usuario="RecepcionTest")
        bandeja_atendido = wi.obtener_bandeja_wsp("PENDIENTES")
        tarjetas_at = bandeja_atendido.get("tarjetas", bandeja_atendido) if isinstance(bandeja_atendido, dict) else bandeja_atendido
        chats_at = [t for t in tarjetas_at if t.get("jid") == jid_test]
        assert len(chats_at) == 0, "marcar_atendido debió apagar el pendiente"

    finally:
        # Limpieza de mensajes simulados en la base local
        if DB_PRUEBA.exists():
            conn = sqlite3.connect(str(DB_PRUEBA))
            try:
                conn.execute("DELETE FROM wsp_recepcion_mensajes WHERE chat_jid LIKE 'SIM-%' OR jid LIKE 'SIM-%'")
                conn.execute("DELETE FROM wsp_recepcion_cola_envio WHERE telefono_o_jid LIKE 'SIM-%'")
                # Limpiar tabla wsp_alertas_estado si existe
                cur = conn.cursor()
                cur.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='wsp_alertas_estado'")
                if cur.fetchone()[0]:
                    conn.execute("DELETE FROM wsp_alertas_estado WHERE chat_jid LIKE 'SIM-%'")
                conn.commit()
            finally:
                conn.close()


# ============================================================================
# 4. CONTACTOS INTERNOS NUNCA EN PENDIENTES
# ============================================================================
def test_04_internos_nunca_en_pendientes():
    """Chats internos (Admin, Recepción, grupo Ovalo, eleiza store) nunca generan alerta ni aparecen en PENDIENTES."""
    import wsp_inteligencia as wi

    internos = [
        ("51977129752@s.whatsapp.net", "Eduardo Calani"),
        ("51951535614@s.whatsapp.net", "Eliana"),
        ("120363410025708860@g.us", "Ovalo Mariategui"),
        ("SIM-eleiza-store@s.whatsapp.net", "eleiza store"),
    ]

    for jid, nombre in internos:
        wi.simular_mensaje(jid, nombre, "Mensaje interno de coordinación del turno")

    try:
        alertas = wi.obtener_alertas_wsp()
        bandeja = wi.obtener_bandeja_wsp("PENDIENTES")
        tarjetas = bandeja.get("tarjetas", bandeja) if isinstance(bandeja, dict) else bandeja

        jids_en_pendientes = {t.get("jid") for t in tarjetas}
        for jid, nombre in internos:
            assert jid not in jids_en_pendientes, f"El contacto interno {nombre} ({jid}) apareció indebidamente en PENDIENTES"

        # Deben poder verse en la pestaña INTERNO
        bandeja_interno = wi.obtener_bandeja_wsp("INTERNO")
        tarjetas_int = bandeja_interno.get("tarjetas", bandeja_interno) if isinstance(bandeja_interno, dict) else bandeja_interno
        jids_en_interno = {t.get("jid") for t in tarjetas_int}
        for jid, nombre in internos:
            assert jid in jids_en_interno, f"El contacto interno {nombre} ({jid}) debería aparecer en la pestaña INTERNO"

    finally:
        if DB_PRUEBA.exists():
            conn = sqlite3.connect(str(DB_PRUEBA))
            try:
                conn.execute("DELETE FROM wsp_recepcion_mensajes WHERE chat_jid LIKE 'SIM-%' OR jid LIKE 'SIM-%' OR chat_nombre='eleiza store'")
                conn.commit()
            finally:
                conn.close()


# ============================================================================
# 5. SEGURIDAD: MODO PRUEBA, AISLAMIENTO Y CHECKSUMS
# ============================================================================
def test_05_seguridad_modo_prueba():
    """MODO_PRUEBA: enviar_respuesta escribe solo estado 'SIMULADO' local; 0 escrituras operativa; checksums intactos; conexión mode=ro rechaza escrituras."""
    # 1. Verificar modo_prueba en hotel_perfil.json
    perfil_path = BASE_PRUEBA / "data" / "hotel_perfil.json"
    assert perfil_path.exists(), "hotel_perfil.json no existe"
    with open(perfil_path, "r", encoding="utf-8") as f:
        perfil = json.load(f)
    assert perfil.get("modo_prueba") is True, "El entorno debe estar en modo_prueba=True"

    # 2. Checksum previo de estancias, pagos, ventas_tienda en la base de prueba
    chk_antes = get_db_checksum(DB_PRUEBA)

    # 3. Conteo previo de cola en la base operativa
    assert DB_OPERATIVA.exists(), f"No se encontró la base operativa en {DB_OPERATIVA}"
    conn_op = sqlite3.connect(f"file:{DB_OPERATIVA}?mode=ro", uri=True)
    try:
        cur_op = conn_op.cursor()
        cur_op.execute("SELECT COUNT(*) FROM wsp_recepcion_cola_envio")
        count_op_antes = cur_op.fetchone()[0]
    finally:
        conn_op.close()

    # 4. Probar que la conexión en mode=ro a la operativa rechaza escrituras
    conn_ro_test = sqlite3.connect(f"file:{DB_OPERATIVA}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError) as exc_info:
        conn_ro_test.execute("INSERT INTO wsp_recepcion_cola_envio (telefono_o_jid, mensaje, estado) VALUES ('999', 'test', 'PENDIENTE')")
    conn_ro_test.close()
    assert "readonly" in str(exc_info.value).lower(), f"Se esperaba error de solo lectura, se obtuvo: {exc_info.value}"

    # 5. Ejecutar enviar_respuesta en modo prueba
    import wsp_inteligencia as wi
    jid_envio = "SIM-51988776655@s.whatsapp.net"
    resp = wi.enviar_respuesta(jid_envio, "Mensaje de prueba de seguridad", usuario="AuditorSeguridad")
    assert resp.get("estado") == "SIMULADO" or resp.get("simulado") is True, f"enviar_respuesta no devolvió estado SIMULADO: {resp}"

    # 6. Comprobar que en la base local se registró como 'SIMULADO'
    conn_local = sqlite3.connect(str(DB_PRUEBA))
    try:
        cur_loc = conn_local.cursor()
        cola_id = resp.get("id")
        if cola_id:
            cur_loc.execute("SELECT estado FROM wsp_recepcion_cola_envio WHERE id = ?", (cola_id,))
        else:
            cur_loc.execute("SELECT estado FROM wsp_recepcion_cola_envio ORDER BY id DESC LIMIT 1")
        row = cur_loc.fetchone()
        assert row is not None, "No se encontró el registro simulado en la cola local de prueba"
        assert row[0] == "SIMULADO", f"El estado local debe ser 'SIMULADO', se encontró '{row[0]}'"
        
        # Limpieza del registro de prueba
        if cola_id:
            conn_local.execute("DELETE FROM wsp_recepcion_cola_envio WHERE id = ?", (cola_id,))
        conn_local.commit()
    finally:
        conn_local.close()

    # 7. Verificar que el COUNT(*) en la operativa NO cambió
    conn_op_post = sqlite3.connect(f"file:{DB_OPERATIVA}?mode=ro", uri=True)
    try:
        cur_op2 = conn_op_post.cursor()
        cur_op2.execute("SELECT COUNT(*) FROM wsp_recepcion_cola_envio")
        count_op_despues = cur_op2.fetchone()[0]
    finally:
        conn_op_post.close()
    assert count_op_despues == count_op_antes, f"¡CRÍTICO! La cola operativa cambió de {count_op_antes} a {count_op_despues}"

    # 8. Verificar checksums de negocio de la prueba intactos
    chk_despues = get_db_checksum(DB_PRUEBA)
    assert chk_antes == chk_despues, f"¡CRÍTICO! Las tablas estancias/pagos/ventas_tienda sufrieron cambios: antes={chk_antes}, despues={chk_despues}"


# ============================================================================
# 6. EXCLUSIONES EN PROMOVER_A_OPERATIVA.PY
# ============================================================================
def test_06_promover_a_operativa_exclusiones():
    """python promover_a_operativa.py sin --aplicar: auth_info/ y media/ NO deben aparecer en el plan."""
    cmd = [sys.executable, str(BASE_PRUEBA / "promover_a_operativa.py")]
    res = subprocess.run(cmd, cwd=str(BASE_PRUEBA), capture_output=True, text=True, timeout=30)
    
    assert res.returncode == 0, f"promover_a_operativa falló con retorno {res.returncode}:\n{res.stderr}"
    stdout_lower = res.stdout.lower()

    # Ni auth_info ni media deben figurar como archivos a copiar/promover
    assert "auth_info" not in stdout_lower, f"'auth_info' aparece en el plan de promoción:\n{res.stdout}"
    assert "whatsapp_mcp_recepcion\\auth_info" not in stdout_lower
    assert "whatsapp_mcp_recepcion/auth_info" not in stdout_lower
    assert "whatsapp_mcp_recepcion\\media" not in stdout_lower
    assert "whatsapp_mcp_recepcion/media" not in stdout_lower


# ============================================================================
# 7. RPC VIVAS EN PUERTO 8082
# ============================================================================
def test_07_rpc_vivas_8082():
    """RPC vivas en 8082 para las 6 RPC del plan (obtenerAlertasWsp, obtenerBandejaWsp, enviarRespuestaWsp, marcarChatWspAtendido, configurarAutoAdelantoWsp, simularMensajeWsp)."""
    rpc_url = "http://127.0.0.1:8082/api/rpc"

    def llamar_rpc(metodo: str, args: list) -> dict:
        req_data = json.dumps({"args": args}).encode("utf-8")
        url = f"{rpc_url}/{metodo}"
        req = urllib.request.Request(url, data=req_data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            assert resp.status == 200, f"RPC {metodo} devolvió status {resp.status}"
            raw = resp.read().decode("utf-8")
            data = json.loads(raw)
            if isinstance(data, dict) and "result" in data:
                return data["result"]
            return data

    # 1. simularMensajeWsp
    sim_res = llamar_rpc("simularMensajeWsp", [{"jid": "SIM-RPC-TEST@s.whatsapp.net", "nombre": "Cliente RPC", "texto": "Hola quiero reservar"}])
    assert sim_res is not None, "simularMensajeWsp devolvió None"

    # 2. obtenerAlertasWsp
    alertas = llamar_rpc("obtenerAlertasWsp", [])
    assert isinstance(alertas, dict), f"obtenerAlertasWsp debe devolver dict: {alertas}"
    assert "pendientes" in alertas or "nivel" in alertas, f"Estructura inválida en obtenerAlertasWsp: {alertas}"

    # 3. obtenerBandejaWsp
    bandeja = llamar_rpc("obtenerBandejaWsp", ["PENDIENTES"])
    assert isinstance(bandeja, (dict, list)), f"obtenerBandejaWsp debe devolver dict o list: {bandeja}"

    # 4. marcarChatWspAtendido
    at_res = llamar_rpc("marcarChatWspAtendido", [{"jid": "SIM-RPC-TEST@s.whatsapp.net", "usuario": "TesterRPC"}])
    assert at_res is not None, "marcarChatWspAtendido devolvió None"

    # 5. enviarRespuestaWsp
    env_res = llamar_rpc("enviarRespuestaWsp", [{"jid": "SIM-RPC-TEST@s.whatsapp.net", "texto": "Respuesta RPC", "plantilla": "ADELANTO_RESERVA", "usuario": "TesterRPC"}])
    assert env_res is not None, "enviarRespuestaWsp devolvió None"

    # 6. configurarAutoAdelantoWsp
    cfg_res = llamar_rpc("configurarAutoAdelantoWsp", [{"activo": False, "usuario": "TesterRPC"}])
    assert cfg_res is not None, "configurarAutoAdelantoWsp devolvió None"

    # Limpieza
    if DB_PRUEBA.exists():
        conn = sqlite3.connect(str(DB_PRUEBA))
        try:
            conn.execute("DELETE FROM wsp_recepcion_mensajes WHERE chat_jid LIKE 'SIM-%' OR jid LIKE 'SIM-%'")
            conn.execute("DELETE FROM wsp_recepcion_cola_envio WHERE telefono_o_jid LIKE 'SIM-%'")
            conn.commit()
        finally:
            conn.close()


# ============================================================================
# 8. VALIDACIÓN UI VISUAL CON PLAYWRIGHT Y MICROSOFT EDGE
# ============================================================================
def test_08_ui_edge_visual():
    """UI: Microsoft Edge / Playwright. Viewport 1366px franja única, capturas OK y AMBAR, modal abierto, pestañas antiguas intactas."""
    import wsp_inteligencia as wi
    from playwright.sync_api import sync_playwright

    def _limpiar_simulados():
        if DB_PRUEBA.exists():
            conn = sqlite3.connect(str(DB_PRUEBA))
            try:
                conn.execute("DELETE FROM wsp_recepcion_mensajes WHERE chat_jid LIKE 'SIM-%' OR jid LIKE 'SIM-%' OR id_mensaje LIKE 'SIM-%'")
                conn.execute("DELETE FROM wsp_recepcion_chats WHERE jid LIKE 'SIM-%'")
                cur = conn.cursor()
                cur.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='wsp_alertas_estado'")
                if cur.fetchone()[0]:
                    conn.execute("DELETE FROM wsp_alertas_estado WHERE chat_jid LIKE 'SIM-%'")
                conn.commit()
            finally:
                conn.close()

    # Asegurar estado limpio inicial
    _limpiar_simulados()

    console_errors = []
    page_errors = []

    def on_console(msg):
        loc = str(msg.location or "")
        txt = str(msg.text or "")
        if "favicon.ico" in txt or "favicon.ico" in loc:
            return
        if msg.type in ("error", "assert"):
            console_errors.append(f"[{msg.type}] {txt}")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=True)
            # Requerimiento exacto: viewport a 1366 px
            context = browser.new_context(viewport={"width": 1366, "height": 768})
            page = context.new_page()

            page.on("console", on_console)
            page.on("pageerror", lambda exc: page_errors.append(str(exc)))

            # 1. Cargar panel
            page.goto("http://127.0.0.1:8082", wait_until="networkidle", timeout=30000)
            time.sleep(1.5)

            # 2. Verificar que la barra entra en una sola franja a 1366 px
            nav_header = page.locator("header, nav, #barraSuperior, .nav-bar").first
            if nav_header.count() > 0:
                box = nav_header.bounding_box()
                if box:
                    print(f"Altura de barra superior a 1366px: {box['height']}px")
                    assert box["height"] <= 65, f"La barra superior excede la franja única a 1366px: altura={box['height']}px"

            # 3. Captura en nivel OK (al día)
            page.wait_for_selector("#wspTicker.wsp-nivel-OK, #wspTicker:has-text('WhatsApp al día')", timeout=10000)
            shot_ok = SCREENSHOTS_DIR / "wsp_01_barra_ok.png"
            page.screenshot(path=str(shot_ok))
            assert shot_ok.exists(), "No se generó la captura wsp_01_barra_ok.png"

            # 4. Simular un mensaje para poner en AMBAR
            wi.simular_mensaje("SIM-939790000", "Lourdes Calizaya", "Hola necesito mi factura de la habitacion 105")

            # Esperar a que el ticker pase a nivel AMBAR (polling cada 5 s o refresco)
            page.wait_for_selector("#wspTicker.wsp-nivel-AMBAR, #wspTicker.wsp-nivel-ROJO", timeout=12000)
            time.sleep(0.5)

            txt_ticker = page.locator("#wspTickerTexto").text_content()
            print(f"Ticker en nivel activo: '{txt_ticker}'")
            assert "al día" not in txt_ticker.lower(), f"El ticker no reflejó el mensaje en ámbar: '{txt_ticker}'"

            # Captura en nivel AMBAR
            shot_ambar = SCREENSHOTS_DIR / "wsp_02_barra_ambar.png"
            page.screenshot(path=str(shot_ambar))
            assert shot_ambar.exists(), "No se generó la captura wsp_02_barra_ambar.png"

            # 5. Abrir el modal de WhatsApp
            ticker = page.locator("#wspTicker")
            if ticker.is_visible():
                ticker.click()
            else:
                page.evaluate("() => { if (window.abrirModalWspBandeja) window.abrirModalWspBandeja(); }")
            
            page.wait_for_selector("#modalWspBandeja.flex, #modalWspBandeja:not(.hidden)", timeout=6000)
            time.sleep(1.0)

            # Captura modal abierto
            shot_modal = SCREENSHOTS_DIR / "wsp_03_modal_abierto.png"
            page.screenshot(path=str(shot_modal))
            assert shot_modal.exists(), "No se generó la captura wsp_03_modal_abierto.png"

            # Cerrar el modal para volver a la pantalla principal
            page.evaluate("() => { if (window.cerrarModalWspBandeja) window.cerrarModalWspBandeja(); }")
            modal = page.locator("#modalWspBandeja").first
            if modal.count() > 0:
                modal.wait_for(state="hidden", timeout=5000)
            time.sleep(0.5)

            # 6. Verificar funcionamiento de pestañas antiguas sin recargar
            # Hover BD (menú contextual Contabilidad)
            btn_bd = page.locator("#tabVistaEstanciasBD, button:has-text('BD')").first
            if btn_bd.count() > 0 and btn_bd.is_visible():
                btn_bd.hover()
                time.sleep(0.5)

            # Pestaña Panel
            btn_panel = page.locator("button:has-text('Panel'), [onclick*='cambiarVista'][onclick*='panel']").first
            if btn_panel.count() > 0 and btn_panel.is_visible():
                btn_panel.click()
                time.sleep(0.5)

            # Pestaña Pagos
            btn_pagos = page.locator("button:has-text('Pagos'), [onclick*='cambiarVista'][onclick*='pagos']").first
            if btn_pagos.count() > 0 and btn_pagos.is_visible():
                btn_pagos.click()
                time.sleep(0.5)

            # Pestaña Yapes
            btn_yapes = page.locator("button:has-text('Yapes'), [onclick*='cambiarVista'][onclick*='yape']").first
            if btn_yapes.count() > 0 and btn_yapes.is_visible():
                btn_yapes.click()
                time.sleep(0.5)

            browser.close()

        # Comprobar 0 errores fatales en consola
        assert len(page_errors) == 0, f"Errores de página JS detectados: {page_errors}"
        errores_criticos = [e for e in console_errors if "wsp" in e.lower() or "syntaxerror" in e.lower()]
        assert len(errores_criticos) == 0, f"Errores JS en consola relacionados con WhatsApp: {errores_criticos}"

    finally:
        # Limpieza definitiva de TODOS los chats simulados SIM-*
        _limpiar_simulados()
        alertas_final = wi.obtener_alertas_wsp()
        assert alertas_final.get("pendientes") == 0, f"Quedaron pendientes tras limpieza: {alertas_final}"
        print(f"Limpieza final completada: pendientes={alertas_final.get('pendientes')}, nivel={alertas_final.get('nivel')}")


if __name__ == "__main__":
    pytest.main(["-v", __file__])
