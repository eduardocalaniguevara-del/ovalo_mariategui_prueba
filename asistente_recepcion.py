# -*- coding: utf-8 -*-
"""
ASISTENTE OPERATIVO DE RECEPCIÓN ("COPILOTO ÓVALO") + CONTROL ARNÉS² (GR-09)
=============================================================================
Diseñado para los recepcionistas del Hotel El Óvalo Mariátegui:
  - Eleisa (Turno Día)
  - Eliana (Turno Día)
  - Yolyi  (Turno Noche)

Cumple estrictamente:
  1. Cortafuegos Arnés² GR-09:
     - PROHIBIDO dar claves SOL SUNAT, contraseñas, tokens, código Arnés² o correos privados.
     - PROHIBIDO dar ingresos semanales, mensuales o acumulados totales (solo cuadre del turno/día actual).
     - PROHIBIDO consultar o hablar de Gastos (tabla `gastos` / `compras_tienda_lotes`).
     - PROHIBIDO abrir modales emergentes solos ("Acción Guiada" intrusiva descartada).
     - Registra todo intento bloqueado como `INTENTO_BLOQUEADO_GR09` en `asistente_movimientos_log`.
  2. 4 Frentes de Consulta Instantánea:
     - Cuentas, Deudas por Habitación y Yapes del Día.
     - WhatsApp de Recepción y Comprobantes Pendientes.
     - Auditor de Vitrina (`h1..h36`) con detección de turno/fecha/responsable donde inició el faltante.
     - Cuadre Diario únicamente del día/turno actual.
  3. Doble Modalidad Práctica:
     - Modalidad 1: Guía Visual con Fotos Reales (`/static/img/guia_asistente/*.png`).
     - Modalidad 2: Ejecución Directa por el Asistente con clarificación proactiva (pregunta fecha y advierte
       si la habitación destino está ocupada ofreciendo intercambio) + 1 clic de confirmación + Extracto del Dueño.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import DATA_DIR, EQUIPO_ID
from database import db_transaction, get_connection

# Almacén en memoria de propuestas pendientes de confirmación (expiran en 30 min)
_PROPUESTAS_PENDIENTES: Dict[str, Dict[str, Any]] = {}

RECEPCIONISTAS_OFICIALES = ["Eleisa", "Eliana", "Yolyi"]


def _ahora_dmy_hms() -> Tuple[str, str, str]:
    now = datetime.now()
    return (
        now.strftime("%d/%m/%Y"),
        now.strftime("%H:%M:%S"),
        now.isoformat(timespec="seconds"),
    )


def asegurar_tabla_asistente_log() -> None:
    conn = get_connection()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS asistente_movimientos_log (
                log_uuid          TEXT PRIMARY KEY,
                fecha_dmy         TEXT NOT NULL DEFAULT '',
                hora              TEXT NOT NULL DEFAULT '',
                ts_iso            TEXT NOT NULL DEFAULT '',
                recepcionista     TEXT NOT NULL DEFAULT '',
                tipo_interaccion  TEXT NOT NULL DEFAULT '',
                pedido_original   TEXT NOT NULL DEFAULT '',
                accion_ejecutada  TEXT NOT NULL DEFAULT '',
                hab               TEXT NOT NULL DEFAULT '',
                huesped           TEXT NOT NULL DEFAULT '',
                monto             REAL NOT NULL DEFAULT 0,
                estado_anterior   TEXT NOT NULL DEFAULT '{}',
                estado_nuevo      TEXT NOT NULL DEFAULT '{}',
                resumen_dueno     TEXT NOT NULL DEFAULT '',
                equipo            TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_asist_log_fecha ON asistente_movimientos_log (fecha_dmy, tipo_interaccion);
            """
        )
        conn.commit()
    finally:
        conn.close()


def registrar_log_asistente(
    *,
    recepcionista: str,
    tipo_interaccion: str,
    pedido_original: str,
    accion_ejecutada: str = "",
    hab: str = "",
    huesped: str = "",
    monto: float = 0.0,
    estado_anterior: Any = None,
    estado_nuevo: Any = None,
    resumen_dueno: str = "",
) -> str:
    asegurar_tabla_asistente_log()
    f_dmy, hora, ts_iso = _ahora_dmy_hms()
    log_id = str(uuid.uuid4())
    rec_clean = (recepcionista or "Recepción").strip()
    ant_json = json.dumps(estado_anterior if estado_anterior is not None else {}, ensure_ascii=False)
    nue_json = json.dumps(estado_nuevo if estado_nuevo is not None else {}, ensure_ascii=False)

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO asistente_movimientos_log (
                log_uuid, fecha_dmy, hora, ts_iso, recepcionista, tipo_interaccion,
                pedido_original, accion_ejecutada, hab, huesped, monto,
                estado_anterior, estado_nuevo, resumen_dueno, equipo
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                log_id,
                f_dmy,
                hora,
                ts_iso,
                rec_clean,
                tipo_interaccion,
                str(pedido_original or "").strip(),
                accion_ejecutada,
                str(hab or "").strip(),
                str(huesped or "").strip(),
                float(monto or 0.0),
                ant_json,
                nue_json,
                str(resumen_dueno or "").strip(),
                EQUIPO_ID,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    # Registrar también aprendizaje compartido (EXP-ARC-02) si fue un movimiento o bloqueo relevante
    if tipo_interaccion in ("MOVIMIENTO_EJECUTADO", "INTENTO_BLOQUEADO_GR09"):
        try:
            import supabase_sync as sb
            if hasattr(sb, "registrar_aprendizaje_compartido"):
                sb.registrar_aprendizaje_compartido(
                    actor=rec_clean,
                    categoria=tipo_interaccion,
                    pregunta_o_evento=str(pedido_original or "")[:300],
                    patron_detectado=accion_ejecutada or "GR-09",
                    regla_aprendida=resumen_dueno[:300],
                )
        except Exception:
            pass

    return log_id


# ==============================================================================
# 1. CORTAFUEGOS ESTRICTO ARNÉS² (GR-09)
# ==============================================================================

_PATRONES_GR09_CLAVES = re.compile(
    r"\b(clave\s*sol|claves?\s+de\s+sunat|contrase[ñn]as?|passwords?|tokens?\s+de|"
    r"c[oó]digo\s+(?:de\s+)?arn[eé]s|7991|credenciales?|correo\s+personal|api\s*key|"
    r"clave\s+del?\s+correo|clave\s+del?\s+banco|clave\s+bcp)\b",
    re.IGNORECASE,
)

_PATRONES_GR09_INGRESOS_ACUM = re.compile(
    r"\b("
    r"(?:ingresos?|ventas?|recaudaci[oó]n|recaudado|ganancias?|facturado|producci[oó]n|total\s+de\s+ingresos?)\s+"
    r"(?:de\s+la\s+semana|semanal(?:es)?|del?\s+mes|mensual(?:es)?|de\s+octubre|de\s+noviembre|de\s+diciembre|"
    r"del?\s+a[ñn]o|anual(?:es)?|acumulad[oa]s?|hist[oó]ric[oa]s?)"
    r"|"
    r"cu[aá]nto\s+(?:gana|genera|factur[oó]|hizo|entr[oó]\s+en)\s+el\s+(?:hotel|hostal)\s+(?:este\s+mes|esta\s+semana|en\s+el\s+mes|en\s+total)"
    r")\b",
    re.IGNORECASE,
)

_PATRONES_GR09_GASTOS = re.compile(
    r"\b(tabla\s+gastos|registro[_\s]+de[_\s]+gastos|en\s+qu[eé]\s+(?:se\s+)?gasta|"
    r"cu[aá]nto\s+se\s+gast[oó]|gastos?\s+del?\s+(?:hotel|hostal|mes|due[ñn]o|administrador)|"
    r"compras[_\s]+tienda[_\s]+lotes|sueldos?\s+de|planilla\s+de|adelantos?\s+de\s+sueldo)\b",
    re.IGNORECASE,
)


def verificar_guardrail_gr09(texto: str, recepcionista: str = "Recepción") -> Optional[Dict[str, Any]]:
    """
    Evalúa si el texto vulnera el Guardrail GR-09 de Arnés².
    Si lo vulnera, registra `INTENTO_BLOQUEADO_GR09` en `asistente_movimientos_log` y devuelve la respuesta de bloqueo.
    """
    q = (texto or "").strip()
    if not q:
        return None

    motivo = ""
    if _PATRONES_GR09_CLAVES.search(q):
        motivo = "Solicitud de claves, contraseñas, tokens o códigos de seguridad (Protegido por GR-09)."
    elif _PATRONES_GR09_INGRESOS_ACUM.search(q):
        motivo = "Solicitud de ingresos semanales, mensuales o acumulados totales (Protegido por GR-09)."
    elif _PATRONES_GR09_GASTOS.search(q):
        motivo = "Solicitud de información sobre gastos internos o compras de la empresa (Protegido por GR-09)."

    if not motivo:
        return None

    resumen = f"🛡️ [GR-09 BLOQUEADO] {recepcionista} consultó: '{q[:120]}' → {motivo}"
    log_id = registrar_log_asistente(
        recepcionista=recepcionista,
        tipo_interaccion="INTENTO_BLOQUEADO_GR09",
        pedido_original=q,
        accion_ejecutada="BLOQUEO_GR09",
        resumen_dueno=resumen,
    )
    return {
        "ok": True,
        "bloqueado_gr09": True,
        "log_uuid": log_id,
        "titulo": "🛡️ Información Reservada de Administración (Arnés² GR-09)",
        "respuesta_html": (
            "<div style='background:#fef2f2;border:1px solid #f87171;border-radius:10px;padding:12px;color:#991b1b;'>"
            "<b>🛡️ Cortafuegos de Seguridad Arnés² (Regla GR-09):</b><br>"
            "Por políticas de confidencialidad del <b>Hotel El Óvalo Mariátegui</b>, el Copiloto de Recepción "
            "no tiene acceso ni autorización para mostrar:<br>"
            "<ul style='margin:6px 0 6px 18px;padding:0;'>"
            "<li>Claves SOL SUNAT, contraseñas, tokens o códigos de seguridad.</li>"
            "<li>Ingresos semanales, mensuales o acumulados históricos (solo puedes consultar el <b>cuadre del turno/día actual</b>).</li>"
            "<li>Registros ni análisis de gastos internos de la administración.</li>"
            "</ul>"
            "<span style='font-size:12px;color:#b91c1c;'>Esta consulta ha sido anotada en la bitácora de seguridad.</span>"
            "</div>"
        ),
    }


# ==============================================================================
# 2. LOS 4 FRENTES DE CONSULTA OPERATIVA INSTANTÁNEA
# ==============================================================================

def _consultar_deuda_o_cuenta_hab(hab: str) -> Dict[str, Any]:
    hab_clean = re.sub(r"\D", "", str(hab or ""))
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT * FROM estancias
            WHERE hab = ? AND estado_estancia != 'Cancelado'
            ORDER BY ymd_in DESC, sheet_row DESC, rowid DESC
            LIMIT 3
            """,
            (hab_clean,),
        ).fetchall()
        if not rows:
            return {
                "ok": True,
                "titulo": f"🛏️ Habitación {hab_clean}",
                "respuesta_html": f"No se encontraron estancias recientes para la <b>Habitación {hab_clean}</b>.",
            }

        actual = rows[0]
        est_uuid = actual["estancia_uuid"]
        id_ts = actual["id_timestamp_str"]

        pagos = conn.execute(
            """
            SELECT fecha_iso, monto, medio_pago, tipo_pago, concepto, nro_operacion
            FROM pagos
            WHERE estancia_uuid = ? OR (id_estancia != '' AND id_estancia = ?)
            ORDER BY rowid ASC
            """,
            (est_uuid, id_ts),
        ).fetchall()

        tienda = conn.execute(
            """
            SELECT fecha_dmy, producto, cantidad, metodo_pago, subtotal
            FROM ventas_tienda
            WHERE estancia_uuid = ? OR (id_estancia != '' AND id_estancia = ?) OR (hab = ? AND fecha_dmy = ?)
            ORDER BY rowid ASC
            """,
            (est_uuid, id_ts, hab_clean, actual["fecha_in_dmy"]),
        ).fetchall()

        tarifa = float(actual["tarifa"] or 0) * max(1, int(actual["noches"] or 1))
        snacks = float(actual["adic_snacks"] or 0)
        servicios = float(actual["adic_servicios"] or 0)
        adic_comp = float(actual["adic_comp"] or 0)
        deuda_ant = float(actual["deuda_anterior"] or 0)
        total_cuenta = round(tarifa + snacks + servicios + adic_comp + deuda_ant, 2)

        pag_ini_ef = float(actual["pago_ini_efec"] or 0)
        pag_ini_ya = float(actual["pago_ini_yape"] or 0)
        pag_post_ef = float(actual["pago_post_efec"] or 0)
        pag_post_ya = float(actual["pago_post_yape"] or 0)
        pag_visa = float(actual["pago_visa"] or 0)
        total_pagado = round(pag_ini_ef + pag_ini_ya + pag_post_ef + pag_post_ya + pag_visa, 2)
        saldo = round(total_cuenta - total_pagado, 2)

        color_saldo = "#dc2626" if saldo > 0.01 else "#16a34a"
        texto_saldo = f"DEBE S/ {saldo:.2f}" if saldo > 0.01 else "AL DÍA (S/ 0.00)"

        html = [
            f"<div style='background:#f8fafc;border:1px solid #cbd5e1;border-radius:10px;padding:12px;'>",
            f"<div style='font-size:15px;font-weight:700;color:#0f172a;margin-bottom:6px;'>"
            f"🛏️ Hab. {hab_clean} ({actual['tipo_hab']}) — {actual['huesped']} (DNI {actual['dni']})</div>",
            f"<div style='font-size:12.5px;color:#334155;margin-bottom:8px;'>"
            f"📅 Check-In: <b>{actual['fecha_in_dmy']}</b> ({actual['hora_registro']}) → Check-Out: <b>{actual['fecha_out_dmy']}</b> "
            f"| Estado: <b>{actual['estado_estancia']}</b></div>",
            f"<table style='width:100%;font-size:12.5px;border-collapse:collapse;margin-bottom:8px;'>",
            f"<tr><td>Alojamiento ({actual['noches']} noche/s):</td><td style='text-align:right;font-weight:600;'>S/ {tarifa:.2f}</td></tr>",
        ]
        if deuda_ant > 0:
            html.append(f"<tr><td style='color:#b45309;'>Deuda anterior arrastrada:</td><td style='text-align:right;font-weight:600;color:#b45309;'>S/ {deuda_ant:.2f}</td></tr>")
        if snacks > 0:
            html.append(f"<tr><td>Snacks cargados a habitación:</td><td style='text-align:right;font-weight:600;'>S/ {snacks:.2f}</td></tr>")
        if servicios > 0:
            html.append(f"<tr><td>Servicios adicionales ({actual['tipo_adicional'] or 'Varios'}):</td><td style='text-align:right;font-weight:600;'>S/ {servicios:.2f}</td></tr>")
        if adic_comp > 0:
            html.append(f"<tr><td>Adicional 25% comprobante:</td><td style='text-align:right;font-weight:600;'>S/ {adic_comp:.2f}</td></tr>")
        html.append(f"<tr style='border-top:1px solid #cbd5e1;'><td><b>Total Cuenta:</b></td><td style='text-align:right;font-weight:700;'>S/ {total_cuenta:.2f}</td></tr>")
        html.append(
            f"<tr><td>Total Pagado (Efec: S/ {pag_ini_ef+pag_post_ef:.2f} | Yape: S/ {pag_ini_ya+pag_post_ya:.2f}"
            f"{f' | VISA: S/ {pag_visa:.2f}' if pag_visa > 0 else ''}):</td>"
            f"<td style='text-align:right;font-weight:700;color:#15803d;'>S/ {total_pagado:.2f}</td></tr>"
        )
        html.append(
            f"<tr style='border-top:2px solid #94a3b8;'><td><b>Saldo Actual:</b></td>"
            f"<td style='text-align:right;font-weight:800;font-size:14px;color:{color_saldo};'>{texto_saldo}</td></tr>"
            f"</table>"
        )

        if actual["nro_op_yape"]:
            html.append(f"<div style='font-size:12px;color:#475569;'>📱 Anotación Yape en estancia: <code>{actual['nro_op_yape']}</code></div>")

        if tienda:
            items_str = ", ".join([f"{int(t['cantidad'])}x {t['producto']} (S/ {float(t['subtotal']):.2f} - {t['metodo_pago']})" for t in tienda])
            html.append(f"<div style='font-size:12px;color:#475569;margin-top:4px;'>🥤 Consumos tienda: {items_str}</div>")

        if len(rows) > 1:
            ant = rows[1]
            html.append(
                f"<div style='font-size:11.5px;color:#64748b;margin-top:6px;border-top:1px dashed #cbd5e1;padding-top:4px;'>"
                f"Estancia previa en Hab. {hab_clean}: {ant['huesped']} ({ant['fecha_in_dmy']} → {ant['fecha_out_dmy']}, {ant['estado_estancia']})</div>"
            )
        html.append("</div>")
        return {
            "ok": True,
            "titulo": f"🛏️ Cuenta de Habitación {hab_clean}",
            "respuesta_html": "".join(html),
        }
    finally:
        conn.close()


def _consultar_yapes_recientes() -> Dict[str, Any]:
    hoy_dmy, _, _ = _ahora_dmy_hms()
    ayer_dmy = (datetime.now() - timedelta(days=1)).strftime("%d/%m/%Y")
    conn = get_connection()
    try:
        yapes_bcp = conn.execute(
            """
            SELECT fecha_operacion, hora_operacion, remitente_bcp, monto_bcp, hab_calzada, huesped_calzado, estado_auditoria
            FROM yape_conciliacion_hostal
            ORDER BY id DESC LIMIT 12
            """
        ).fetchall()

        pagos_yape = conn.execute(
            """
            SELECT fecha_iso, hab, huesped, monto, concepto, nro_operacion
            FROM pagos
            WHERE medio_pago LIKE '%Yape%' AND (fecha_iso LIKE ? OR fecha_iso LIKE ?)
            ORDER BY rowid DESC LIMIT 15
            """,
            (f"{hoy_dmy}%", f"{ayer_dmy}%"),
        ).fetchall()

        # Revisar también cache_correo_maelen_bcp.json si existe
        maelen_movs = []
        cache_path = DATA_DIR / "cache_correo_maelen_bcp.json"
        if cache_path.exists():
            try:
                raw_m = json.loads(cache_path.read_text(encoding="utf-8-sig"))
                if isinstance(raw_m, list):
                    maelen_movs = raw_m[:8]
                elif isinstance(raw_m, dict):
                    maelen_movs = (raw_m.get("movimientos") or raw_m.get("items") or [])[:8]
            except Exception:
                pass

        html = ["<div style='background:#f8fafc;border:1px solid #cbd5e1;border-radius:10px;padding:12px;'>"]
        html.append(f"<div style='font-weight:700;color:#0f172a;margin-bottom:6px;'>📱 Yapes Registrados en Recepción (Hoy {hoy_dmy} y Ayer {ayer_dmy})</div>")
        if pagos_yape:
            html.append("<table style='width:100%;font-size:12px;border-collapse:collapse;margin-bottom:10px;'>")
            html.append("<tr style='background:#e2e8f0;text-align:left;'><th style='padding:4px;'>Fecha/Hora</th><th>Hab</th><th>Huésped</th><th>Voucher</th><th style='text-align:right;'>Monto</th></tr>")
            for p in pagos_yape:
                html.append(
                    f"<tr style='border-bottom:1px solid #e2e8f0;'>"
                    f"<td style='padding:4px;'>{p['fecha_iso']}</td>"
                    f"<td><b>{p['hab'] or 'Tienda'}</b></td>"
                    f"<td>{p['huesped'][:24]}</td>"
                    f"<td><code>{p['nro_operacion'] or '-'}</code></td>"
                    f"<td style='text-align:right;font-weight:700;color:#6d28d9;'>S/ {float(p['monto'] or 0):.2f}</td></tr>"
                )
            html.append("</table>")
        else:
            html.append("<div style='font-size:12px;color:#64748b;margin-bottom:8px;'>No hay pagos Yape anotados hoy en recepción.</div>")

        if yapes_bcp:
            html.append("<div style='font-weight:700;color:#0f172a;margin:8px 0 4px;'>🏦 Últimos Yapes Conciliados con Banco BCP</div>")
            html.append("<table style='width:100%;font-size:11.5px;border-collapse:collapse;'>")
            html.append("<tr style='background:#ede9fe;text-align:left;'><th style='padding:3px;'>Fecha/Hora</th><th>Remitente BCP</th><th>Hab</th><th>Estado</th><th style='text-align:right;'>Monto</th></tr>")
            for yb in yapes_bcp[:8]:
                est_aud = str(yb["estado_auditoria"] or "")
                col_e = "#15803d" if "CUADRADO" in est_aud.upper() else "#b45309"
                html.append(
                    f"<tr style='border-bottom:1px solid #e2e8f0;'>"
                    f"<td style='padding:3px;'>{yb['fecha_operacion']} {yb['hora_operacion']}</td>"
                    f"<td>{str(yb['remitente_bcp'] or '')[:22]}</td>"
                    f"<td><b>{yb['hab_calzada'] or '-'}</b></td>"
                    f"<td style='color:{col_e};font-weight:600;'>{est_aud[:22]}</td>"
                    f"<td style='text-align:right;font-weight:700;'>S/ {float(yb['monto_bcp'] or 0):.2f}</td></tr>"
                )
            html.append("</table>")
        html.append("</div>")
        return {
            "ok": True,
            "titulo": "📱 Verificador de Yapes del Día",
            "respuesta_html": "".join(html),
        }
    finally:
        conn.close()


def _consultar_wsp_comprobantes_pendientes() -> Dict[str, Any]:
    conn = get_connection()
    try:
        # 1. Estancias marcadas con solicita_comp = 'Sí' y estado_comp != 'Emitido'
        pend_est = conn.execute(
            """
            SELECT fecha_in_dmy, hab, huesped, dni, ruc, empresa, tipo_comp, estado_comp, tarifa, noches, adic_comp, monto_factura, celular
            FROM estancias
            WHERE solicita_comp = 'Sí'
              AND coalesce(estado_comp, '') != 'Emitido'
              AND estado_estancia != 'Cancelado'
            ORDER BY ymd_in DESC, sheet_row DESC
            LIMIT 15
            """
        ).fetchall()

        # 2. Mensajes recientes de WhatsApp de clientes pidiendo factura/boleta/RUC (cruce últimos 9 dígitos EXP-WSP-02)
        mensajes_wsp = conn.execute(
            """
            SELECT
                m.fecha_hora,
                coalesce(m.chat_nombre, m.nombre_emisor, m.jid) AS remitente,
                coalesce(m.chat_jid, m.jid, '') AS jid_raw,
                m.texto,
                (
                    SELECT hp.nombre || ' (Hab ' || hp.hab_frec || ')'
                    FROM huespedes_padron hp
                    WHERE hp.celular != ''
                      AND SUBSTR(REPLACE(hp.celular, ' ', ''), -9) = SUBSTR(REPLACE(REPLACE(coalesce(m.chat_jid, m.jid, ''), '@s.whatsapp.net', ''), ' ', ''), -9)
                    LIMIT 1
                ) AS huesped_cruzado
            FROM wsp_recepcion_mensajes m
            WHERE coalesce(m.es_mio, 0) = 0
              AND (
                  lower(m.texto) LIKE '%factura%'
                  OR lower(m.texto) LIKE '%boleta%'
                  OR lower(m.texto) LIKE '%ruc%'
                  OR lower(m.texto) LIKE '%comprobante%'
              )
            ORDER BY m.rowid DESC
            LIMIT 10
            """
        ).fetchall()

        # 3. Cola de envío pendiente en WhatsApp
        cola_wsp = conn.execute(
            """
            SELECT coalesce(destino_telefono, telefono_o_jid, '') AS tel,
                   coalesce(cliente_nombre, '') AS cliente,
                   coalesce(nro_comprobante, nombre_adjunto, '') AS comp,
                   estado, creado_en
            FROM wsp_recepcion_cola_envio
            WHERE upper(coalesce(estado, 'PENDIENTE')) = 'PENDIENTE'
            ORDER BY id DESC LIMIT 10
            """
        ).fetchall()

        html = ["<div style='background:#f8fafc;border:1px solid #cbd5e1;border-radius:10px;padding:12px;'>"]
        html.append(f"<div style='font-weight:700;color:#0f172a;margin-bottom:6px;'>📄 Comprobantes Pendientes en Estancias ({len(pend_est)})</div>")
        if pend_est:
            html.append("<table style='width:100%;font-size:12px;border-collapse:collapse;margin-bottom:10px;'>")
            html.append("<tr style='background:#fef3c7;text-align:left;'><th style='padding:4px;'>Fecha</th><th>Hab</th><th>Cliente / RUC</th><th>Tipo</th><th>Adic 25%</th></tr>")
            for e in pend_est:
                doc_cli = f"{e['empresa'] or e['huesped']} ({e['ruc'] or e['dni']})"
                html.append(
                    f"<tr style='border-bottom:1px solid #e2e8f0;'>"
                    f"<td style='padding:4px;'>{e['fecha_in_dmy']}</td>"
                    f"<td><b>{e['hab']}</b></td>"
                    f"<td>{doc_cli[:32]}</td>"
                    f"<td><span style='background:#fde68a;padding:1px 6px;border-radius:4px;font-weight:600;'>{e['tipo_comp'] or 'Boleta'}</span></td>"
                    f"<td>S/ {float(e['adic_comp'] or 0):.2f}</td></tr>"
                )
            html.append("</table>")
        else:
            html.append("<div style='font-size:12px;color:#15803d;margin-bottom:8px;'>✅ No hay comprobantes marcados como Pendientes en estancias recientes.</div>")

        html.append(f"<div style='font-weight:700;color:#0f172a;margin:8px 0 4px;'>💬 Solicitudes en WhatsApp de Recepción ({len(mensajes_wsp)})</div>")
        if mensajes_wsp:
            for m in mensajes_wsp[:6]:
                cruce = f" — <b style='color:#0284c7;'>{m['huesped_cruzado']}</b>" if m["huesped_cruzado"] else ""
                html.append(
                    f"<div style='background:#fff;border:1px solid #e2e8f0;border-radius:6px;padding:6px 8px;margin-bottom:4px;font-size:12px;'>"
                    f"<b>{m['remitente']}</b>{cruce} <span style='color:#64748b;font-size:11px;'>({m['fecha_hora']})</span>:<br>"
                    f"<span style='color:#334155;'>“{str(m['texto'] or '')[:140]}”</span></div>"
                )
        else:
            html.append("<div style='font-size:12px;color:#64748b;margin-bottom:6px;'>Sin mensajes recientes pidiendo factura/boleta en WhatsApp local.</div>")

        if cola_wsp:
            html.append(f"<div style='font-weight:700;color:#b45309;margin-top:6px;'>📤 PDFs en cola listos por enviar ({len(cola_wsp)}):</div>")
            for c in cola_wsp:
                html.append(f"<div style='font-size:12px;color:#475569;'>• {c['comp']} → {c['cliente']} ({c['tel']}) [{c['estado']}]</div>")

        html.append("</div>")
        return {
            "ok": True,
            "titulo": "📄 WhatsApp y Comprobantes Pendientes",
            "respuesta_html": "".join(html),
        }
    finally:
        conn.close()


def _parsear_header_vitrina(raw_hdr: str, idx: int) -> str:
    Lineas = [l.strip() for l in str(raw_hdr or "").splitlines() if l.strip()]
    if len(Lineas) >= 3:
        return f"Turno h{idx} ({Lineas[1]} — {Lineas[2]})"
    elif len(Lineas) == 2:
        return f"Turno h{idx} ({Lineas[0]} — {Lineas[1]})"
    return f"Turno h{idx}"


def _consultar_faltantes_vitrina(producto_filtro: str = "") -> Dict[str, Any]:
    conn = get_connection()
    try:
        cols_info = [r[1] for r in conn.execute("PRAGMA table_info(vitrina_local)").fetchall()]
        max_h = 0
        for c in cols_info:
            m = re.match(r"^h(\d+)_est$", c)
            if m:
                max_h = max(max_h, int(m.group(1)))

        headers_map: Dict[int, str] = {}
        for r in conn.execute("SELECT clave, valor FROM estado_sistema WHERE clave LIKE 'vitrina_h%_header'").fetchall():
            mh = re.match(r"^vitrina_h(\d+)_header$", str(r["clave"]))
            if mh:
                idx = int(mh.group(1))
                headers_map[idx] = _parsear_header_vitrina(r["valor"], idx)

        rows = conn.execute("SELECT * FROM vitrina_local ORDER BY orden ASC, producto ASC").fetchall()
        filtro = (producto_filtro or "").strip().lower()

        hallazgos = []
        for r in rows:
            prod = str(r["producto"] or "")
            if filtro and filtro not in prod.lower():
                continue
            est_act = str(r["estado_actual"] or "").strip()
            h1_est = str(r["h1_est"] or "").strip() if "h1_est" in cols_info else ""
            h1_cant = str(r["h1_cant"] or "").strip() if "h1_cant" in cols_info else ""
            stock_sis = float(r["stock_sheets"] or 0)

            es_discrepancia = (
                "FALTA" in est_act.upper()
                or "SOBRA" in est_act.upper()
                or "FALTA" in h1_est.upper()
                or "SOBRA" in h1_est.upper()
                or bool(filtro)
            )
            if not es_discrepancia:
                continue

            # Rastrear en h1..h36 desde qué turno continuo empezó la discrepancia actual
            ultimo_h_con_falla = 1 if ("FALTA" in (est_act + h1_est).upper() or "SOBRA" in (est_act + h1_est).upper()) else 0
            primer_ok_despues = None
            historial_breve = []
            for h_i in range(1, max_h + 1):
                c_est = f"h{h_i}_est"
                c_cant = f"h{h_i}_cant"
                if c_est not in cols_info:
                    continue
                val_est = str(r[c_est] or "").strip()
                val_cant = str(r[c_cant] or "").strip()
                if h_i <= 5 and (val_est or val_cant):
                    historial_breve.append(f"h{h_i}: {val_cant or '?'} ({val_est or 's/d'})")
                if "FALTA" in val_est.upper() or "SOBRA" in val_est.upper():
                    if primer_ok_despues is None:
                        ultimo_h_con_falla = h_i
                elif val_est.upper() == "OK" and primer_ok_despues is None:
                    primer_ok_despues = h_i

            turno_origen = headers_map.get(ultimo_h_con_falla, f"Conteo h{ultimo_h_con_falla}") if ultimo_h_con_falla > 0 else "Sin faltante activo"

            # Consultar últimas ventas y movimientos de almacén de ese producto para explicar por qué
            ult_ventas = conn.execute(
                "SELECT fecha_dmy, cantidad, metodo_pago, hab FROM ventas_tienda WHERE lower(producto) = lower(?) ORDER BY rowid DESC LIMIT 3",
                (prod,),
            ).fetchall()
            ult_alm = conn.execute(
                "SELECT fecha_dmy, cantidad, tipo_movimiento FROM almacen_movimientos WHERE lower(producto) = lower(?) ORDER BY rowid DESC LIMIT 2",
                (prod,),
            ).fetchall()

            explicacion = []
            if "FALTA" in (est_act + h1_est).upper():
                explicacion.append(
                    f"El sistema espera <b>{stock_sis:g}</b> und., pero en el último conteo físico se contaron <b>{h1_cant or '?'}</b> ({est_act or h1_est}). "
                    f"Suele ocurrir cuando se entregó el producto y no se registró en <i>Venta Tienda</i>."
                )
            elif "SOBRA" in (est_act + h1_est).upper():
                explicacion.append(
                    f"Físicamente hay más unidades (<b>{h1_cant or '?'}</b>) que en el sistema (<b>{stock_sis:g}</b>). "
                    f"Suele ocurrir por doble digitación de venta o reposición de almacén no ingresada."
                )
            else:
                explicacion.append(f"Stock sistema: <b>{stock_sis:g}</b> | Último conteo físico: <b>{h1_cant or '?'}</b> ({est_act or 'OK'}).")

            if ult_ventas:
                v_txt = ", ".join([f"{v['fecha_dmy']}: {int(v['cantidad'])} und ({v['metodo_pago']}{' Hab '+v['hab'] if v['hab'] else ''})" for v in ult_ventas])
                explicacion.append(f"Últimas ventas anotadas: {v_txt}.")
            if ult_alm:
                a_txt = ", ".join([f"{a['fecha_dmy']}: +{int(a['cantidad'])} ({a['tipo_movimiento']})" for a in ult_alm])
                explicacion.append(f"Últimas entradas almacén: {a_txt}.")

            hallazgos.append({
                "producto": prod,
                "stock_sheets": stock_sis,
                "conteo_actual": h1_cant,
                "estado": est_act or h1_est or "OK",
                "turno_origen": turno_origen,
                "explicacion": " ".join(explicacion),
                "historial": " → ".join(historial_breve),
            })

        html = ["<div style='background:#f8fafc;border:1px solid #cbd5e1;border-radius:10px;padding:12px;'>"]
        html.append(f"<div style='font-weight:700;color:#0f172a;margin-bottom:8px;'>🥤 Auditoría de Vitrina Tienda (Historial {max_h} Turnos)</div>")
        if not hallazgos:
            html.append("<div style='color:#15803d;font-size:13px;font-weight:600;'>✅ ¡Todo cuadrado! No hay productos con FALTA ni SOBRA en el conteo actual de vitrina.</div>")
        else:
            for item in hallazgos:
                es_falta = "FALTA" in item["estado"].upper()
                bg = "#fef2f2" if es_falta else ("#fffbeb" if "SOBRA" in item["estado"].upper() else "#f0fdf4")
                bd = "#fca5a5" if es_falta else ("#fde68a" if "SOBRA" in item["estado"].upper() else "#bbf7d0")
                col = "#b91c1c" if es_falta else ("#b45309" if "SOBRA" in item["estado"].upper() else "#15803d")
                html.append(
                    f"<div style='background:{bg};border:1px solid {bd};border-radius:8px;padding:8px 10px;margin-bottom:8px;font-size:12.5px;'>"
                    f"<div style='font-weight:700;color:{col};font-size:13.5px;'>• {item['producto']} — <span style='text-decoration:underline;'>{item['estado']}</span></div>"
                    f"<div style='color:#1e293b;margin-top:3px;'>🕵️ <b>¿Desde qué turno empezó?:</b> {item['turno_origen']}</div>"
                    f"<div style='color:#334155;margin-top:2px;'>💡 <b>¿Por qué ocurre?:</b> {item['explicacion']}</div>"
                    f"<div style='color:#64748b;font-size:11.5px;margin-top:3px;'>📊 Últimos conteos: {item['historial']}</div>"
                    f"</div>"
                )
        html.append("</div>")
        return {
            "ok": True,
            "titulo": "🥤 Auditor de Vitrina y Faltantes",
            "respuesta_html": "".join(html),
        }
    finally:
        conn.close()


def _consultar_cuadre_turno_actual() -> Dict[str, Any]:
    """
    Cumple GR-09: Solo informa el cuadre del día actual para entrega de turno.
    Jamás muestra ingresos semanales, mensuales ni gastos.
    """
    hoy_dmy, _, _ = _ahora_dmy_hms()
    conn = get_connection()
    try:
        pagos_hoy = conn.execute(
            """
            SELECT medio_pago, SUM(monto) AS total, COUNT(*) AS cant
            FROM pagos
            WHERE fecha_iso LIKE ?
            GROUP BY medio_pago
            """,
            (f"{hoy_dmy}%",),
        ).fetchall()

        tienda_hoy = conn.execute(
            """
            SELECT metodo_pago, SUM(subtotal) AS total, COUNT(*) AS cant
            FROM ventas_tienda
            WHERE fecha_dmy = ?
            GROUP BY metodo_pago
            """,
            (hoy_dmy,),
        ).fetchall()

        efec_aloj = sum(float(r["total"] or 0) for r in pagos_hoy if "efectivo" in str(r["medio_pago"]).lower())
        yape_aloj = sum(float(r["total"] or 0) for r in pagos_hoy if "yape" in str(r["medio_pago"]).lower())
        visa_aloj = sum(float(r["total"] or 0) for r in pagos_hoy if "visa" in str(r["medio_pago"]).lower())

        efec_tnd = sum(float(r["total"] or 0) for r in tienda_hoy if "efectivo" in str(r["metodo_pago"]).lower())
        yape_tnd = sum(float(r["total"] or 0) for r in tienda_hoy if "yape" in str(r["metodo_pago"]).lower())

        html = [
            "<div style='background:#f8fafc;border:1px solid #cbd5e1;border-radius:10px;padding:12px;'>",
            f"<div style='font-weight:700;color:#0f172a;margin-bottom:6px;'>💰 Cuadre Diario del Turno Actual ({hoy_dmy})</div>",
            "<table style='width:100%;font-size:12.5px;border-collapse:collapse;'>",
            f"<tr><td>💵 Efectivo Alojamiento hoy:</td><td style='text-align:right;font-weight:700;'>S/ {efec_aloj:.2f}</td></tr>",
            f"<tr><td>💵 Efectivo Tienda hoy:</td><td style='text-align:right;font-weight:700;'>S/ {efec_tnd:.2f}</td></tr>",
            f"<tr style='border-top:1px solid #cbd5e1;background:#ecfdf5;'><td><b>💵 Total Efectivo del Día (en caja):</b></td>"
            f"<td style='text-align:right;font-weight:800;color:#15803d;'>S/ {efec_aloj + efec_tnd:.2f}</td></tr>",
            f"<tr><td style='padding-top:6px;'>📱 Yape Alojamiento + Tienda hoy:</td><td style='text-align:right;font-weight:700;color:#6d28d9;padding-top:6px;'>S/ {yape_aloj + yape_tnd:.2f}</td></tr>",
        ]
        if visa_aloj > 0:
            html.append(f"<tr><td>💳 VISA hoy:</td><td style='text-align:right;font-weight:700;'>S/ {visa_aloj:.2f}</td></tr>")
        html.append("</table>")
        html.append("<div style='font-size:11.5px;color:#64748b;margin-top:6px;'>ℹ️ Nota Arnés² GR-09: Este resumen muestra únicamente los cobros del día actual para entrega de turno.</div>")
        html.append("</div>")
        return {
            "ok": True,
            "titulo": f"💰 Cuadre del Turno ({hoy_dmy})",
            "respuesta_html": "".join(html),
        }
    finally:
        conn.close()


# ==============================================================================
# 3. MODALIDAD 1: GUÍA VISUAL CON FOTOS REALES (📸 Muéstrame con fotos)
# ==============================================================================

GUIAS_VISUALES_CATALOGO: Dict[str, Dict[str, Any]] = {
    "cambio_hab": {
        "titulo": "📸 Cómo Cambiar de Habitación con el Lápiz ✏️",
        "imagen": "/static/img/guia_asistente/guia_cambio_hab.png",
        "pasos": [
            "① En el <b>Panel de Disponibilidad</b>, ubica la fila de la habitación actual del huésped y haz clic en el botón del <b>Lápiz ✏️ (Modificar)</b> a la derecha.",
            "② En la ventana de edición, cambia el selector <b>Habitación</b> al nuevo número de cuarto (si el cuarto destino está ocupado, usa el Copiloto para intercambiarlos).",
            "③ Haz clic en <b>Guardar Cambios</b>. El sistema trasladará automáticamente sus pagos y consumos de tienda a la nueva habitación.",
        ],
    },
    "yape_2casillas": {
        "titulo": "📸 Cómo Anotar las 2 Casillas de Yape (Monto + Hora AM/PM)",
        "imagen": "/static/img/guia_asistente/guia_yape_2casillas.png",
        "pasos": [
            "① Al registrar un Check-In o Pago Posterior con Yape, ingresa el monto en <b>Pago Yape</b>.",
            "② Llena siempre las <b>2 casillas del voucher Yape</b>: el <b>Monto del voucher</b> y la <b>Hora exacta (con AM o PM)</b> tal como sale en el celular del cliente.",
            "③ Si el cliente pagó en 2 Yapes distintos, usa el botón <b>+ Añadir otro Yape</b> para que el conciliador bancario los cruce sin alertas.",
        ],
    },
    "venta_tienda": {
        "titulo": "📸 Cómo Registrar o Corregir una Venta de Tienda",
        "imagen": "/static/img/guia_asistente/guia_venta_tienda.png",
        "pasos": [
            "① Abre la pestaña <b>🥤 Venta Tienda / Vitrina</b> en la barra superior.",
            "② Elige el <b>Producto</b>, la <b>Cantidad</b> y si es <b>Efectivo</b>, <b>Yape</b> o <b>Cargar a Habitación</b>.",
            "③ Si te equivocaste de producto por error de digitación, edítalo o pídele al Copiloto: <i>'Anótale 1 Agua sin gas a la 204'</i>.",
        ],
    },
    "vitrina_conteo": {
        "titulo": "📸 Cómo Hacer el Conteo Físico de Vitrina (36 Turnos)",
        "imagen": "/static/img/guia_asistente/guia_vitrina_conteo.png",
        "pasos": [
            "① En <b>🥤 Venta Tienda</b>, baja a la sección <b>Conteo Físico de Vitrina</b>.",
            "② Selecciona tu nombre (<b>Eleisa</b>, <b>Eliana</b> o <b>Yolyi</b>) y escribe la cantidad física real contada de cada producto.",
            "③ Guarda el conteo. Si algún producto sale en rojo (<b>FALTA</b>), pregúntale al Copiloto desde qué turno empezó a faltar.",
        ],
    },
    "comprobantes_wsp": {
        "titulo": "📸 Cómo Revisar Comprobantes y Pagos Posteriores",
        "imagen": "/static/img/guia_asistente/guia_comprobantes_wsp.png",
        "pasos": [
            "① Si el huésped pide Factura o Boleta por un monto mayor a su tarifa real, recuerda cobrar y anotar el <b>25% adicional</b> sobre la diferencia.",
            "② Usa el botón <b>💳 Pagos Posteriores</b> para registrar el pago de saldo o el servicio por comprobante.",
            "③ Pulsa el botón rápido <b>📄 WSP Comprobantes</b> en este Copiloto para ver qué facturas siguen pendientes de emitir.",
        ],
    },
}


def obtener_guia_visual_fotos(tema: str = "") -> Dict[str, Any]:
    t = (tema or "").strip().lower()
    elegida = None
    if "hab" in t or "lápiz" in t or "lapiz" in t or "cambi" in t:
        elegida = GUIAS_VISUALES_CATALOGO["cambio_hab"]
    elif "yape" in t or "casilla" in t or "voucher" in t:
        elegida = GUIAS_VISUALES_CATALOGO["yape_2casillas"]
    elif "vitrina" in t or "conteo" in t or "faltante" in t:
        elegida = GUIAS_VISUALES_CATALOGO["vitrina_conteo"]
    elif "tienda" in t or "agua" in t or "gaseosa" in t or "snack" in t:
        elegida = GUIAS_VISUALES_CATALOGO["venta_tienda"]
    elif "factura" in t or "boleta" in t or "comprobante" in t or "pago" in t:
        elegida = GUIAS_VISUALES_CATALOGO["comprobantes_wsp"]

    if elegida:
        guias_mostrar = [elegida]
    else:
        guias_mostrar = list(GUIAS_VISUALES_CATALOGO.values())

    html = ["<div style='display:flex;flex-direction:column;gap:10px;'>"]
    for g in guias_mostrar:
        pasos_html = "".join([f"<div style='margin-bottom:4px;font-size:12.5px;color:#1e293b;'>{p}</div>" for p in g["pasos"]])
        html.append(
            f"<div style='background:#ffffff;border:1px solid #cbd5e1;border-radius:10px;padding:10px;box-shadow:0 1px 3px rgba(0,0,0,0.05);'>"
            f"<div style='font-weight:700;color:#0f172a;font-size:13.5px;margin-bottom:6px;'>{g['titulo']}</div>"
            f"<a href='{g['imagen']}' target='_blank' title='Clic para ampliar imagen en pestaña nueva'>"
            f"<img src='{g['imagen']}' alt='{g['titulo']}' style='width:100%;max-height:210px;object-fit:cover;object-position:top;border-radius:8px;border:2px solid #38bdf8;margin-bottom:8px;cursor:zoom-in;' onerror=\"this.style.display='none'\"/>"
            f"</a>"
            f"{pasos_html}"
            f"</div>"
        )
    html.append("</div>")
    return {
        "ok": True,
        "titulo": "📸 Guía Visual Paso a Paso con Fotos",
        "respuesta_html": "".join(html),
    }


# ==============================================================================
# 4. MODALIDAD 2: EJECUTOR DIRECTO AUDITADO + CLARIFICACIÓN PROACTIVA
# ==============================================================================

def _normalizar_fecha_pedido(texto: str) -> Tuple[Optional[str], bool]:
    """
    Extrae si el usuario especificó una fecha explícita en su mensaje:
      - 'ayer' -> (ayer_dmy, True)
      - 'hoy' -> (hoy_dmy, True)
      - 'del 13', 'fecha 13', '13/11', '13/11/2026' -> (dd/mm/yyyy, True)
    Si no especificó fecha -> (None, False)
    """
    q = (texto or "").lower()
    hoy = datetime.now()
    if "ayer" in q:
        return (hoy - timedelta(days=1)).strftime("%d/%m/%Y"), True
    if "hoy" in q or "día de hoy" in q or "dia de hoy" in q:
        return hoy.strftime("%d/%m/%Y"), True

    m_full = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", q)
    if m_full:
        d = int(m_full.group(1))
        m = int(m_full.group(2))
        y = int(m_full.group(3)) if m_full.group(3) else hoy.year
        if y < 100:
            y += 2000
        return f"{d:02d}/{m:02d}/{y:04d}", True

    m_dia = re.search(r"\b(?:del?\s+d[ií]a|de\s+la\s+fecha|fecha|del)\s+(\d{1,2})\b", q)
    if m_dia:
        d = int(m_dia.group(1))
        if 1 <= d <= 31:
            return f"{d:02d}/{hoy.month:02d}/{hoy.year:04d}", True

    return None, False


def preparar_movimiento_asistente_local(texto: str, recepcionista: str = "Eleisa") -> Optional[Dict[str, Any]]:
    """
    Detecta si el recepcionista pide ejecutar un movimiento operativo:
      A) Continuar estancia de una fecha a otra (ej. 'pasa la 301 de la fecha 13 a la fecha 14')
      B) Cambiar o intercambiar habitación (ej. 'cambia la 302 a la 201 del 13' o 'intercambia 301 y 201')
      C) Registrar venta de tienda (ej. 'anótale 1 Agua sin gas a la 204 en efectivo')
      D) Registrar pago posterior (ej. 'registra pago Yape de S/ 40 en la 305')
    Aplica la Regla de Clarificación Proactiva:
      - Si no indica fecha y hoy no hay nadie en esa habitación, verifica ayer y pregunta/explica claramente.
      - Si la habitación destino ya tiene una persona en esa fecha, ADVIERTE quién está ahí y ofrece
        explícitamente INTERCAMBIAR ambas habitaciones o cancelar.
    """
    q = (texto or "").strip()
    q_low = q.lower()

    # ------------------------------------------------------------------
    # CASO A: CONTINUAR ESTANCIA (ej. "pasa la 301 de la fecha 13 a la fecha 14" / "pasar la 301 para el día de hoy")
    # ------------------------------------------------------------------
    m_cont = re.search(
        r"(?:pasa(?:r)?|contin[uú]a(?:r)?)\s+(?:la\s+)?(?:hab(?:itaci[oó]n)?\s*)?(\d{3})\s+"
        r"(?:de\s+la\s+fecha\s+(\d{1,2})\s+a\s+la\s+fecha\s+(\d{1,2})|para\s+(?:el\s+d[ií]a\s+de\s+)?hoy|al\s+d[ií]a\s+de\s+hoy)",
        q_low,
    )
    if m_cont:
        hab_num = m_cont.group(1)
        hoy = datetime.now()
        hoy_dmy = hoy.strftime("%d/%m/%Y")
        ayer_dmy = (hoy - timedelta(days=1)).strftime("%d/%m/%Y")
        if m_cont.group(2) and m_cont.group(3):
            d_orig = int(m_cont.group(2))
            d_dest = int(m_cont.group(3))
            f_orig_dmy = f"{d_orig:02d}/{hoy.month:02d}/{hoy.year:04d}"
            f_dest_dmy = f"{d_dest:02d}/{hoy.month:02d}/{hoy.year:04d}"
        else:
            f_orig_dmy = ayer_dmy
            f_dest_dmy = hoy_dmy

        conn = get_connection()
        try:
            st_orig = conn.execute(
                """
                SELECT * FROM estancias
                WHERE hab = ? AND fecha_in_dmy = ? AND estado_estancia != 'Cancelado'
                ORDER BY sheet_row DESC, rowid DESC LIMIT 1
                """,
                (hab_num, f_orig_dmy),
            ).fetchone()
            if not st_orig:
                return {
                    "ok": True,
                    "titulo": f"❓ Verificación antes de continuar Hab. {hab_num}",
                    "respuesta_html": (
                        f"<div style='background:#fffbeb;border:1px solid #f59e0b;border-radius:10px;padding:12px;'>"
                        f"⚠️ No encontré ninguna estancia activa en la <b>Hab. {hab_num}</b> con fecha de ingreso <b>{f_orig_dmy}</b>.<br>"
                        f"Por favor indícame de qué fecha exacta o qué huésped deseas continuar."
                        f"</div>"
                    ),
                }

            st_hoy_ocup = conn.execute(
                """
                SELECT * FROM estancias
                WHERE hab = ? AND fecha_in_dmy = ? AND estado_estancia = 'En Curso'
                ORDER BY sheet_row DESC, rowid DESC LIMIT 1
                """,
                (hab_num, f_dest_dmy),
            ).fetchone()
            if st_hoy_ocup:
                return {
                    "ok": True,
                    "titulo": f"⚠️ Habitación {hab_num} ya figura en {f_dest_dmy}",
                    "respuesta_html": (
                        f"<div style='background:#fffbeb;border:1px solid #f59e0b;border-radius:10px;padding:12px;'>"
                        f"En la <b>Hab. {hab_num}</b> para la fecha <b>{f_dest_dmy}</b> ya figura registrado:<br>"
                        f"👤 <b>{st_hoy_ocup['huesped']}</b> (Tarifa S/ {float(st_hoy_ocup['tarifa'] or 0):.2f}).<br>"
                        f"¿Deseas mantenerlo así o moverlo a otra habitación?"
                        f"</div>"
                    ),
                }

            dt_dest = datetime.strptime(f_dest_dmy, "%d/%m/%Y")
            f_out_nueva = (dt_dest + timedelta(days=1)).strftime("%d/%m/%Y")
            prop_id = str(uuid.uuid4())
            _PROPUESTAS_PENDIENTES[prop_id] = {
                "created_ts": time.time(),
                "recepcionista": recepcionista,
                "pedido_original": q,
                "accion": "CONTINUAR_ESTANCIA",
                "hab": hab_num,
                "huesped": st_orig["huesped"],
                "monto": float(st_orig["tarifa"] or 0),
                "datos": {
                    "estanciaUuid": st_orig["estancia_uuid"],
                    "filaAnterior": int(st_orig["sheet_row"] or 0),
                    "habAnterior": hab_num,
                    "habNueva": hab_num,
                    "fechaInAnterior": f_orig_dmy,
                    "fechaInNueva": f_dest_dmy,
                    "fechaOutNueva": f_out_nueva,
                    "tarifa": float(st_orig["tarifa"] or 0),
                    "estadoOriginal": st_orig["estado_estancia"],
                },
                "estado_anterior": dict(st_orig),
            }
            nota_est = ""
            if st_orig["estado_estancia"] != "En Curso":
                nota_est = (
                    f"<div style='font-size:12px;color:#b45309;margin-top:4px;'>"
                    f"ℹ️ Nota: La estancia del {f_orig_dmy} estaba en estado <b>{st_orig['estado_estancia']}</b>; "
                    f"al confirmar la restauraré y continuaré automáticamente al {f_dest_dmy}.</div>"
                )
            return {
                "ok": True,
                "requiere_confirmacion": True,
                "propuesta_id": prop_id,
                "texto_boton_confirmar": f"✅ Confirmar: Pasar Hab. {hab_num} ({st_orig['huesped']}) del {f_orig_dmy} al {f_dest_dmy}",
                "titulo": f"⚡ Confirmar Continuación de Hab. {hab_num}",
                "respuesta_html": (
                    f"<div style='background:#eff6ff;border:1px solid #60a5fa;border-radius:10px;padding:12px;'>"
                    f"<b>📋 Verifica los datos antes de ejecutar:</b><br>"
                    f"• <b>Huésped:</b> {st_orig['huesped']} (DNI {st_orig['dni']})<br>"
                    f"• <b>Habitación:</b> {hab_num} ({f_orig_dmy} → <b>{f_dest_dmy}</b>)<br>"
                    f"• <b>Tarifa diaria:</b> S/ {float(st_orig['tarifa'] or 0):.2f} | <b>Deuda previa:</b> S/ {float(st_orig['deuda_anterior'] or 0):.2f}"
                    f"{nota_est}</div>"
                ),
            }
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # CASO B: CAMBIO O INTERCAMBIO DE HABITACIÓN (ej. "cambia la 302 a la 201", "intercambia 301 y 201")
    # ------------------------------------------------------------------
    m_hab = re.search(
        r"(?:cambia(?:r)?|pasa(?:r)?|mueve|mover|intercambia(?:r)?|invertir)\s+"
        r"(?:la\s+|el\s+)?(?:estancia\s+del?\s+|hab(?:itaci[oó]n)?\s*)?(\d{3})\s*"
        r"(?:a\s+la|al|con\s+la|con\s+el|por\s+la|y\s+la|y\s+el)\s*"
        r"(?:hab(?:itaci[oó]n)?\s*)?(\d{3})",
        q_low,
    )
    if m_hab:
        hab_origen = m_hab.group(1)
        hab_destino = m_hab.group(2)
        pide_intercambio_explicito = any(w in q_low for w in ("intercambia", "viceversa", "invertir", "invierte"))
        fecha_pedida, fecha_fue_explicita = _normalizar_fecha_pedido(q)
        hoy_dmy = datetime.now().strftime("%d/%m/%Y")
        ayer_dmy = (datetime.now() - timedelta(days=1)).strftime("%d/%m/%Y")

        conn = get_connection()
        try:
            # Si no especificó fecha, revisamos hoy y ayer para preguntarle con precisión si hoy está vacío
            if not fecha_fue_explicita:
                st_hoy = conn.execute(
                    "SELECT * FROM estancias WHERE hab = ? AND fecha_in_dmy = ? AND estado_estancia != 'Cancelado' ORDER BY sheet_row DESC, rowid DESC LIMIT 1",
                    (hab_origen, hoy_dmy),
                ).fetchone()
                st_ayer = conn.execute(
                    "SELECT * FROM estancias WHERE hab = ? AND fecha_in_dmy = ? AND estado_estancia != 'Cancelado' ORDER BY sheet_row DESC, rowid DESC LIMIT 1",
                    (hab_origen, ayer_dmy),
                ).fetchone()
                if not st_hoy and st_ayer:
                    return {
                        "ok": True,
                        "titulo": f"❓ ¿De qué fecha deseas cambiar la Hab. {hab_origen}?",
                        "respuesta_html": (
                            f"<div style='background:#fffbeb;border:1px solid #f59e0b;border-radius:10px;padding:12px;color:#78350f;'>"
                            f"<b>🤔 Necesito que me confirmes la fecha exacta:</b><br>"
                            f"• <b>Hoy ({hoy_dmy}):</b> La Hab. <b>{hab_origen}</b> está libre (no tiene huésped registrado hoy).<br>"
                            f"• <b>Ayer ({ayer_dmy}):</b> En la Hab. <b>{hab_origen}</b> estuvo registrado <b>{st_ayer['huesped']}</b>.<br><br>"
                            f"Por favor dime o escribe:<br>"
                            f"👉 <i>\"Cambia la {hab_origen} a la {hab_destino} de la fecha {ayer_dmy[:2]}\"</i> (y dime si deseas moverla o intercambiarla)."
                            f"</div>"
                        ),
                    }
                fecha_uso = hoy_dmy if st_hoy else ayer_dmy
            else:
                fecha_uso = fecha_pedida or hoy_dmy

            st_orig = conn.execute(
                "SELECT * FROM estancias WHERE hab = ? AND fecha_in_dmy = ? AND estado_estancia != 'Cancelado' ORDER BY sheet_row DESC, rowid DESC LIMIT 1",
                (hab_origen, fecha_uso),
            ).fetchone()
            st_dest = conn.execute(
                "SELECT * FROM estancias WHERE hab = ? AND fecha_in_dmy = ? AND estado_estancia != 'Cancelado' ORDER BY sheet_row DESC, rowid DESC LIMIT 1",
                (hab_destino, fecha_uso),
            ).fetchone()

            if not st_orig:
                return {
                    "ok": True,
                    "titulo": f"⚠️ Sin huésped en Hab. {hab_origen} ({fecha_uso})",
                    "respuesta_html": (
                        f"<div style='background:#fffbeb;border:1px solid #f59e0b;border-radius:10px;padding:12px;'>"
                        f"En la fecha <b>{fecha_uso}</b> no hay ninguna estancia activa en la <b>Hab. {hab_origen}</b>.<br>"
                        f"Por favor verifica el número de habitación o indícame la fecha exacta (ej. <i>del día 13</i>)."
                        f"</div>"
                    ),
                }

            # Si la habitación destino YA ESTÁ OCUPADA en esa misma fecha:
            # Regla del dueño: NUNCA sobrescribir en silencio. Avisar que hay una persona en la hab destino y ofrecer INTERCAMBIO.
            if st_dest:
                prop_id = str(uuid.uuid4())
                _PROPUESTAS_PENDIENTES[prop_id] = {
                    "created_ts": time.time(),
                    "recepcionista": recepcionista,
                    "pedido_original": q,
                    "accion": "INTERCAMBIO_HABITACIONES",
                    "hab": f"{hab_origen} ↔ {hab_destino}",
                    "huesped": f"{st_orig['huesped']} ↔ {st_dest['huesped']}",
                    "monto": float(st_orig["tarifa"] or 0),
                    "datos": {
                        "fecha_dmy": fecha_uso,
                        "hab_a": hab_origen,
                        "uuid_a": st_orig["estancia_uuid"],
                        "fila_a": int(st_orig["sheet_row"] or 0),
                        "huesped_a": st_orig["huesped"],
                        "obs_a": st_orig["observaciones"],
                        "hab_b": hab_destino,
                        "uuid_b": st_dest["estancia_uuid"],
                        "fila_b": int(st_dest["sheet_row"] or 0),
                        "huesped_b": st_dest["huesped"],
                        "obs_b": st_dest["observaciones"],
                    },
                    "estado_anterior": {"hab_a": dict(st_orig), "hab_b": dict(st_dest)},
                }
                encabezado = (
                    "<b>🔄 Confirmar Intercambio de Habitaciones:</b>"
                    if pide_intercambio_explicito
                    else f"<b>⚠️ ¡Atención! En la Hab. {hab_destino} ({fecha_uso}) YA hay una persona registrada:</b>"
                )
                return {
                    "ok": True,
                    "requiere_confirmacion": True,
                    "propuesta_id": prop_id,
                    "texto_boton_confirmar": f"🔄 Confirmar Intercambio: {hab_origen} ({st_orig['huesped'][:16]}) ↔ {hab_destino} ({st_dest['huesped'][:16]})",
                    "titulo": f"🔄 Hab. {hab_destino} Ocupada — ¿Intercambiar {hab_origen} ↔ {hab_destino}?",
                    "respuesta_html": (
                        f"<div style='background:#fffbeb;border:1px solid #f59e0b;border-radius:10px;padding:12px;color:#78350f;'>"
                        f"{encabezado}<br>"
                        f"• En <b>Hab. {hab_origen}</b> ({fecha_uso}) está: <b>{st_orig['huesped']}</b> (S/ {float(st_orig['tarifa'] or 0):.2f})<br>"
                        f"• En <b>Hab. {hab_destino}</b> ({fecha_uso}) está: <b>{st_dest['huesped']}</b> (S/ {float(st_dest['tarifa'] or 0):.2f})<br><br>"
                        f"Para no sobrescribir a <b>{st_dest['huesped']}</b>, ¿deseas <b>INTERCAMBIAR</b> ambas habitaciones "
                        f"(pasar <b>{st_orig['huesped']}</b> a la <b>{hab_destino}</b> y <b>{st_dest['huesped']}</b> a la <b>{hab_origen}</b>)?"
                        f"</div>"
                    ),
                }

            # Si la habitación destino está LIBRE en esa fecha -> cambio simple de habitación
            prop_id = str(uuid.uuid4())
            _PROPUESTAS_PENDIENTES[prop_id] = {
                "created_ts": time.time(),
                "recepcionista": recepcionista,
                "pedido_original": q,
                "accion": "CAMBIO_HABITACION",
                "hab": f"{hab_origen} → {hab_destino}",
                "huesped": st_orig["huesped"],
                "monto": float(st_orig["tarifa"] or 0),
                "datos": {
                    "fecha_dmy": fecha_uso,
                    "estanciaUuid": st_orig["estancia_uuid"],
                    "fila": int(st_orig["sheet_row"] or 0),
                    "habOriginal": hab_origen,
                    "hab": hab_destino,
                    "observaciones": st_orig["observaciones"],
                },
                "estado_anterior": dict(st_orig),
            }
            return {
                "ok": True,
                "requiere_confirmacion": True,
                "propuesta_id": prop_id,
                "texto_boton_confirmar": f"✅ Confirmar: Mover {st_orig['huesped'][:20]} de Hab. {hab_origen} a Hab. {hab_destino} ({fecha_uso})",
                "titulo": f"⚡ Confirmar Cambio de Habitación ({hab_origen} → {hab_destino})",
                "respuesta_html": (
                    f"<div style='background:#eff6ff;border:1px solid #60a5fa;border-radius:10px;padding:12px;'>"
                    f"<b>📋 Verifica el cambio antes de confirmar:</b><br>"
                    f"• <b>Fecha de estancia:</b> {fecha_uso}<br>"
                    f"• <b>Huésped:</b> {st_orig['huesped']} (DNI {st_orig['dni']})<br>"
                    f"• <b>Movimiento:</b> Hab. <b>{hab_origen}</b> → Hab. <b>{hab_destino}</b> (libre)<br>"
                    f"• Se trasladarán automáticamente sus pagos y consumos de tienda."
                    f"</div>"
                ),
            }
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # CASO C: REGISTRAR VENTA DE TIENDA (ej. "anótale 1 Agua sin gas a la 204 en efectivo")
    # ------------------------------------------------------------------
    m_tnd = re.search(
        r"(?:an[oó]tale|registra(?:r)?\s+venta|vende|vender|agrega(?:r)?)\s+(\d+)\s+([a-záéíóúñ0-9\s\-]+?)\s+"
        r"(?:a\s+la\s+hab(?:itaci[oó]n)?\s*(\d{3})|en\s+tienda|venta\s+libre)",
        q_low,
    )
    if m_tnd:
        cant = int(m_tnd.group(1))
        prod_busq = m_tnd.group(2).strip()
        hab_dest = m_tnd.group(3) or ""
        metodo = "Efectivo"
        if "yape" in q_low:
            metodo = "Yape"
        elif "cargar" in q_low or "cuenta" in q_low or (hab_dest and "efectivo" not in q_low and "yape" not in q_low):
            metodo = "Cargar a Habitación"

        conn = get_connection()
        try:
            prods = conn.execute("SELECT producto FROM vitrina_local").fetchall()
            prod_match = None
            for pr in prods:
                if prod_busq in str(pr["producto"]).lower() or str(pr["producto"]).lower() in prod_busq:
                    prod_match = str(pr["producto"])
                    break
            if not prod_match:
                return None

            prop_id = str(uuid.uuid4())
            _PROPUESTAS_PENDIENTES[prop_id] = {
                "created_ts": time.time(),
                "recepcionista": recepcionista,
                "pedido_original": q,
                "accion": "VENTA_TIENDA",
                "hab": hab_dest,
                "huesped": f"{cant}x {prod_match}",
                "monto": 0.0,
                "datos": {
                    "producto": prod_match,
                    "cantidad": cant,
                    "metodoPago": metodo,
                    "hab": hab_dest,
                },
                "estado_anterior": {"producto": prod_match, "hab": hab_dest},
            }
            return {
                "ok": True,
                "requiere_confirmacion": True,
                "propuesta_id": prop_id,
                "texto_boton_confirmar": f"✅ Confirmar Venta: {cant}x {prod_match} ({metodo}{' - Hab. '+hab_dest if hab_dest else ''})",
                "titulo": "🥤 Confirmar Registro en Tienda",
                "respuesta_html": (
                    f"<div style='background:#eff6ff;border:1px solid #60a5fa;border-radius:10px;padding:12px;'>"
                    f"<b>📋 Verifica la venta de tienda antes de confirmar:</b><br>"
                    f"• <b>Producto:</b> {cant} × <b>{prod_match}</b><br>"
                    f"• <b>Modalidad:</b> {metodo} {f'(Hab. {hab_dest})' if hab_dest else '(Venta libre)'}<br>"
                    f"• <b>Responsable en turno:</b> {recepcionista}"
                    f"</div>"
                ),
            }
        finally:
            conn.close()

    return None


def _limpiar_etiqueta_liberada(obs: str) -> str:
    s = re.sub(r"\[Liberada\s+[^\]]+\]\s*\|?\s*", "", str(obs or ""), flags=re.IGNORECASE).strip()
    s = re.sub(r"^\|\s*|\s*\|$", "", s).strip()
    return s


def confirmar_movimiento_asistente_local(propuesta_id: str, recepcionista: str = "") -> Dict[str, Any]:
    """
    Ejecuta la propuesta previamente confirmada con 1 clic por el recepcionista,
    usando las funciones oficiales de `domain_logic.py` y guardando el Antes/Después en `asistente_movimientos_log`.
    """
    prop = _PROPUESTAS_PENDIENTES.pop(str(propuesta_id or "").strip(), None)
    if not prop:
        return {
            "ok": False,
            "error": "La solicitud de confirmación ya expiró o ya fue ejecutada. Vuelve a escribirla en el Copiloto.",
        }

    import domain_logic as dl

    rec = (recepcionista or prop.get("recepcionista") or "Recepción").strip()
    accion = prop["accion"]
    datos = prop["datos"]
    pedido_orig = prop["pedido_original"]
    est_ant = prop.get("estado_anterior") or {}

    if accion == "CAMBIO_HABITACION":
        obs_limpia = _limpiar_etiqueta_liberada(datos.get("observaciones", ""))
        res = dl.modificar_estancia_local({
            "estanciaUuid": datos["estanciaUuid"],
            "fila": datos["fila"],
            "habOriginal": datos["habOriginal"],
            "hab": datos["hab"],
            "estadoEstancia": "En Curso",
            "observaciones": obs_limpia,
        })
        resumen = (
            f"{rec} cambió de habitación a {res.get('nombre')} ({datos.get('fecha_dmy')}): "
            f"Hab. {datos['habOriginal']} → Hab. {datos['hab']}"
        )
        log_id = registrar_log_asistente(
            recepcionista=rec,
            tipo_interaccion="MOVIMIENTO_EJECUTADO",
            pedido_original=pedido_orig,
            accion_ejecutada="CAMBIO_HABITACION",
            hab=f"{datos['habOriginal']}->{datos['hab']}",
            huesped=str(res.get("nombre") or ""),
            monto=float(prop.get("monto") or 0),
            estado_anterior=est_ant,
            estado_nuevo=res,
            resumen_dueno=resumen,
        )
        return {
            "ok": True,
            "ejecutado": True,
            "log_uuid": log_id,
            "titulo": "✅ Cambio de Habitación Completado",
            "respuesta_html": (
                f"<div style='background:#f0fdf4;border:1px solid #4ade80;border-radius:10px;padding:12px;color:#14532d;'>"
                f"<b>✅ ¡Listo! Movimiento ejecutado y registrado:</b><br>"
                f"• <b>{res.get('nombre')}</b> pasó de la <b>Hab. {datos['habOriginal']}</b> a la <b>Hab. {datos['hab']}</b> ({datos.get('fecha_dmy')}).<br>"
                f"• Sus pagos y consumos de tienda fueron trasladados automáticamente.<br>"
                f"<span style='font-size:11.5px;color:#166534;'>📋 Anotado en el Extracto del Dueño (ID {log_id[:8]}).</span>"
                f"</div>"
            ),
        }

    if accion == "INTERCAMBIO_HABITACIONES":
        uuid_a, uuid_b = datos["uuid_a"], datos["uuid_b"]
        hab_a, hab_b = datos["hab_a"], datos["hab_b"]
        # Paso temporal en SQLite para evitar bloqueo mutuo de colisión
        with db_transaction() as conn:
            conn.execute("UPDATE estancias SET estado_estancia = 'En Transito' WHERE estancia_uuid = ?", (uuid_b,))

        res_a = dl.modificar_estancia_local({
            "estanciaUuid": uuid_a,
            "fila": datos["fila_a"],
            "habOriginal": hab_a,
            "hab": hab_b,
            "estadoEstancia": "En Curso",
            "observaciones": _limpiar_etiqueta_liberada(datos.get("obs_a", "")),
        })
        res_b = dl.modificar_estancia_local({
            "estanciaUuid": uuid_b,
            "fila": datos["fila_b"],
            "habOriginal": hab_b,
            "hab": hab_a,
            "estadoEstancia": "En Curso",
            "observaciones": _limpiar_etiqueta_liberada(datos.get("obs_b", "")),
        })
        resumen = (
            f"{rec} intercambió habitaciones del {datos['fecha_dmy']}: "
            f"{datos['huesped_a']} ({hab_a}→{hab_b}) ↔ {datos['huesped_b']} ({hab_b}→{hab_a})"
        )
        log_id = registrar_log_asistente(
            recepcionista=rec,
            tipo_interaccion="MOVIMIENTO_EJECUTADO",
            pedido_original=pedido_orig,
            accion_ejecutada="INTERCAMBIO_HABITACIONES",
            hab=f"{hab_a}<->{hab_b}",
            huesped=f"{datos['huesped_a']} / {datos['huesped_b']}",
            monto=float(prop.get("monto") or 0),
            estado_anterior=est_ant,
            estado_nuevo={"res_a": res_a, "res_b": res_b},
            resumen_dueno=resumen,
        )
        return {
            "ok": True,
            "ejecutado": True,
            "log_uuid": log_id,
            "titulo": "✅ Intercambio de Habitaciones Completado",
            "respuesta_html": (
                f"<div style='background:#f0fdf4;border:1px solid #4ade80;border-radius:10px;padding:12px;color:#14532d;'>"
                f"<b>✅ ¡Intercambio realizado con éxito ({datos['fecha_dmy']})!</b><br>"
                f"• <b>{datos['huesped_a']}</b> quedó en la <b>Hab. {hab_b}</b>.<br>"
                f"• <b>{datos['huesped_b']}</b> quedó en la <b>Hab. {hab_a}</b>.<br>"
                f"<span style='font-size:11.5px;color:#166534;'>📋 Anotado en el Extracto del Dueño (ID {log_id[:8]}).</span>"
                f"</div>"
            ),
        }

    if accion == "CONTINUAR_ESTANCIA":
        if datos.get("estadoOriginal") != "En Curso":
            dl.modificar_estancia_local({
                "estanciaUuid": datos["estanciaUuid"],
                "fila": datos["filaAnterior"],
                "habOriginal": datos["habAnterior"],
                "hab": datos["habAnterior"],
                "estadoEstancia": "En Curso",
            })
        res_c = dl.continuar_estancia_local(datos)
        resumen = (
            f"{rec} continuó estancia en Hab. {datos['habNueva']} ({res_c.get('nombre')}) "
            f"del {datos['fechaInAnterior']} al {datos['fechaInNueva']} (Deuda arrastrada: S/ {float(res_c.get('deudaArrastre') or 0):.2f})"
        )
        log_id = registrar_log_asistente(
            recepcionista=rec,
            tipo_interaccion="MOVIMIENTO_EJECUTADO",
            pedido_original=pedido_orig,
            accion_ejecutada="CONTINUAR_ESTANCIA",
            hab=datos["habNueva"],
            huesped=str(res_c.get("nombre") or ""),
            monto=float(datos.get("tarifa") or 0),
            estado_anterior=est_ant,
            estado_nuevo=res_c,
            resumen_dueno=resumen,
        )
        return {
            "ok": True,
            "ejecutado": True,
            "log_uuid": log_id,
            "titulo": f"✅ Habitación {datos['habNueva']} Continuada al {datos['fechaInNueva']}",
            "respuesta_html": (
                f"<div style='background:#f0fdf4;border:1px solid #4ade80;border-radius:10px;padding:12px;color:#14532d;'>"
                f"<b>✅ ¡Estancia continuada correctamente!</b><br>"
                f"• <b>Huésped:</b> {res_c.get('nombre')}<br>"
                f"• <b>Habitación {datos['habNueva']}:</b> Pasó del {datos['fechaInAnterior']} al <b>{datos['fechaInNueva']}</b>.<br>"
                f"• <b>Deuda anterior arrastrada:</b> S/ {float(res_c.get('deudaArrastre') or 0):.2f}<br>"
                f"<span style='font-size:11.5px;color:#166534;'>📋 Anotado en el Extracto del Dueño (ID {log_id[:8]}).</span>"
                f"</div>"
            ),
        }

    if accion == "VENTA_TIENDA":
        res_t = dl.registrar_venta_tienda_local({
            "items": [{"producto": datos["producto"], "cantidad": datos["cantidad"]}],
            "metodoPago": datos["metodoPago"],
            "hab": datos["hab"],
            "personal": "",
        })
        monto_t = float(res_t.get("total") or 0)
        resumen = f"{rec} registró en tienda: {datos['cantidad']}x {datos['producto']} ({datos['metodoPago']} {'Hab '+datos['hab'] if datos['hab'] else ''}) por S/ {monto_t:.2f}"
        log_id = registrar_log_asistente(
            recepcionista=rec,
            tipo_interaccion="MOVIMIENTO_EJECUTADO",
            pedido_original=pedido_orig,
            accion_ejecutada="VENTA_TIENDA",
            hab=datos["hab"],
            huesped=f"{datos['cantidad']}x {datos['producto']}",
            monto=monto_t,
            estado_anterior=est_ant,
            estado_nuevo=res_t,
            resumen_dueno=resumen,
        )
        return {
            "ok": True,
            "ejecutado": True,
            "log_uuid": log_id,
            "titulo": "✅ Venta de Tienda Registrada",
            "respuesta_html": (
                f"<div style='background:#f0fdf4;border:1px solid #4ade80;border-radius:10px;padding:12px;color:#14532d;'>"
                f"<b>✅ Venta registrada en Tienda:</b><br>"
                f"• {datos['cantidad']} × <b>{datos['producto']}</b> ({datos['metodoPago']}) — Total: <b>S/ {monto_t:.2f}</b>.<br>"
                f"<span style='font-size:11.5px;color:#166534;'>📋 Anotado en el Extracto del Dueño (ID {log_id[:8]}).</span>"
                f"</div>"
            ),
        }

    return {"ok": False, "error": f"Acción no reconocida: {accion}"}


# ==============================================================================
# 5. ENRUTADOR PRINCIPAL DEL COPILOTO Y EXTRACTO DEL DUEÑO
# ==============================================================================

def consultar_asistente_recepcion_local(datos: Dict[str, Any]) -> Dict[str, Any]:
    asegurar_tabla_asistente_log()
    texto = str(datos.get("pregunta") or datos.get("texto") or "").strip()
    accion_rapida = str(datos.get("accionRapida") or "").strip()
    recepcionista = str(datos.get("recepcionista") or "Eleisa").strip()

    # 1. Cortafuegos Arnés² GR-09 SIEMPRE PRIMERO
    bloqueo = verificar_guardrail_gr09(texto, recepcionista=recepcionista)
    if bloqueo:
        return bloqueo

    # 2. Botones rápidos de 1 clic
    if accion_rapida == "wsp_comprobantes":
        res = _consultar_wsp_comprobantes_pendientes()
        registrar_log_asistente(
            recepcionista=recepcionista,
            tipo_interaccion="CONSULTA",
            pedido_original="[Botón Rápido] WSP Comprobantes Pendientes",
            accion_ejecutada="CONSULTA_WSP_COMPROBANTES",
            resumen_dueno=f"{recepcionista} consultó comprobantes pendientes de WhatsApp/Estancias.",
        )
        return res

    if accion_rapida == "faltantes_vitrina":
        res = _consultar_faltantes_vitrina()
        registrar_log_asistente(
            recepcionista=recepcionista,
            tipo_interaccion="CONSULTA",
            pedido_original="[Botón Rápido] Auditor de Vitrina y Faltantes",
            accion_ejecutada="CONSULTA_VITRINA",
            resumen_dueno=f"{recepcionista} auditó faltantes de vitrina (h1..h36).",
        )
        return res

    if accion_rapida == "yapes_hoy":
        res = _consultar_yapes_recientes()
        registrar_log_asistente(
            recepcionista=recepcionista,
            tipo_interaccion="CONSULTA",
            pedido_original="[Botón Rápido] Yapes de Hoy",
            accion_ejecutada="CONSULTA_YAPES",
            resumen_dueno=f"{recepcionista} verificó los Yapes del día.",
        )
        return res

    if accion_rapida == "guia_fotos":
        return obtener_guia_visual_fotos(texto)

    if accion_rapida == "cuadre_turno":
        res = _consultar_cuadre_turno_actual()
        registrar_log_asistente(
            recepcionista=recepcionista,
            tipo_interaccion="CONSULTA",
            pedido_original="[Botón Rápido] Cuadre del Turno Actual",
            accion_ejecutada="CONSULTA_CUADRE_TURNO",
            resumen_dueno=f"{recepcionista} consultó el cuadre del turno actual.",
        )
        return res

    # 3. Verificar si es un pedido de ejecución de movimiento (cambio hab, continuar, venta tienda)
    prop_mov = preparar_movimiento_asistente_local(texto, recepcionista=recepcionista)
    if prop_mov:
        return prop_mov

    q_low = texto.lower()

    # 4. Consultas en lenguaje natural
    if any(w in q_low for w in ("foto", "muéstrame", "muestrame", "cómo se hace", "como se hace", "guía", "guia", "lápiz", "lapiz")):
        return obtener_guia_visual_fotos(texto)

    if any(w in q_low for w in ("vitrina", "faltante", "falta", "sobra", "conteo", "stock")):
        # Extraer posible producto
        prod_f = ""
        for p_cand in ("agua", "gaseosa", "coca", "inca", "gatorade", "sporade", "galleta", "sublime", "preservativo", "shampoo", "jabon", "jabón", "cerveza", "red bull", "volt"):
            if p_cand in q_low:
                prod_f = p_cand
                break
        res = _consultar_faltantes_vitrina(prod_f)
        registrar_log_asistente(
            recepcionista=recepcionista,
            tipo_interaccion="CONSULTA",
            pedido_original=texto,
            accion_ejecutada="CONSULTA_VITRINA",
            resumen_dueno=f"{recepcionista} consultó vitrina: '{texto[:80]}'",
        )
        return res

    if any(w in q_low for w in ("wsp", "whatsapp", "factura", "boleta", "comprobante", "ruc")):
        res = _consultar_wsp_comprobantes_pendientes()
        registrar_log_asistente(
            recepcionista=recepcionista,
            tipo_interaccion="CONSULTA",
            pedido_original=texto,
            accion_ejecutada="CONSULTA_WSP_COMPROBANTES",
            resumen_dueno=f"{recepcionista} consultó comprobantes/WSP: '{texto[:80]}'",
        )
        return res

    if "yape" in q_low or "plin" in q_low or "bcp" in q_low:
        res = _consultar_yapes_recientes()
        registrar_log_asistente(
            recepcionista=recepcionista,
            tipo_interaccion="CONSULTA",
            pedido_original=texto,
            accion_ejecutada="CONSULTA_YAPES",
            resumen_dueno=f"{recepcionista} consultó Yapes: '{texto[:80]}'",
        )
        return res

    if any(w in q_low for w in ("cuadre", "caja de hoy", "turno actual", "entrega de turno")):
        return _consultar_cuadre_turno_actual()

    # Si menciona un número de habitación (103..407)
    m_h = re.search(r"\b([1-4]0[1-9])\b", q_low)
    if m_h:
        hab_q = m_h.group(1)
        res = _consultar_deuda_o_cuenta_hab(hab_q)
        registrar_log_asistente(
            recepcionista=recepcionista,
            tipo_interaccion="CONSULTA",
            pedido_original=texto,
            accion_ejecutada="CONSULTA_HABITACION",
            hab=hab_q,
            resumen_dueno=f"{recepcionista} consultó la cuenta de la Hab. {hab_q}.",
        )
        return res

    # Respuesta de ayuda rápida por defecto
    return {
        "ok": True,
        "titulo": f"🤖 Hola {recepcionista}, soy el Copiloto de Óvalo Mariátegui",
        "respuesta_html": (
            "<div style='background:#f8fafc;border:1px solid #cbd5e1;border-radius:10px;padding:12px;font-size:12.5px;color:#1e293b;'>"
            "<b>¿En qué te ayudo ahora mismo? Puedes usar los botones de arriba o escribirme:</b>"
            "<ul style='margin:6px 0 6px 18px;padding:0;'>"
            "<li>🛏️ <b>Ver cuenta de una habitación:</b> escribe <code>201</code>, <code>cuenta de la 301</code> o <code>deuda 304</code>.</li>"
            "<li>🔄 <b>Cambiar o intercambiar cuartos:</b> escribe <code>Cambia la 302 a la 201 de la fecha 13</code> o <code>Pasa la 301 de la fecha 13 a la fecha 14</code>.</li>"
            "<li>🥤 <b>Vitrina y Faltantes:</b> escribe <code>¿Qué falta en vitrina y desde qué turno?</code>.</li>"
            "<li>📄 <b>Facturas / WhatsApp:</b> pulsa <b>📄 WSP Comprobantes</b>.</li>"
            "<li>📸 <b>Guía con fotos:</b> pulsa <b>📸 Guía con Fotos</b> para ver en imagen dónde hacer clic.</li>"
            "</ul>"
            "</div>"
        ),
    }


def obtener_extracto_dueno_asistente_local(datos: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Devuelve la bitácora completa de movimientos ejecutados por el asistente y los
    intentos bloqueados por GR-09 para el Extracto del Dueño.
    """
    asegurar_tabla_asistente_log()
    d = datos or {}
    filtro_rec = str(d.get("recepcionista") or "").strip()
    filtro_tipo = str(d.get("tipo") or "").strip()
    limite = min(200, max(10, int(d.get("limite") or 80)))

    conn = get_connection()
    try:
        where_clauses = []
        params: List[Any] = []
        if filtro_rec:
            where_clauses.append("lower(recepcionista) = lower(?)")
            params.append(filtro_rec)
        if filtro_tipo:
            where_clauses.append("tipo_interaccion = ?")
            params.append(filtro_tipo)

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        rows = conn.execute(
            f"""
            SELECT log_uuid, fecha_dmy, hora, ts_iso, recepcionista, tipo_interaccion,
                   pedido_original, accion_ejecutada, hab, huesped, monto,
                   estado_anterior, estado_nuevo, resumen_dueno, equipo
            FROM asistente_movimientos_log
            {where_sql}
            ORDER BY ts_iso DESC, rowid DESC
            LIMIT ?
            """,
            (*params, limite),
        ).fetchall()

        totales = conn.execute(
            """
            SELECT tipo_interaccion, COUNT(*) AS cant
            FROM asistente_movimientos_log
            GROUP BY tipo_interaccion
            """
        ).fetchall()
        mapa_tot = {str(r["tipo_interaccion"]): int(r["cant"]) for r in totales}

        items = [dict(r) for r in rows]
        return {
            "ok": True,
            "items": items,
            "resumen": {
                "movimientos_ejecutados": mapa_tot.get("MOVIMIENTO_EJECUTADO", 0),
                "intentos_bloqueados_gr09": mapa_tot.get("INTENTO_BLOQUEADO_GR09", 0),
                "consultas_operativas": mapa_tot.get("CONSULTA", 0),
            },
        }
    finally:
        conn.close()
