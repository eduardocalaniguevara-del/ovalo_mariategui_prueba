# -*- coding: utf-8 -*-
"""
Extractor OCR Local de Recepción (Laptop 2) conectado directamente a ovalo_sistema.db:
- Lee PDFs digitales (PyMuPDF) y PDFs escaneados / imágenes de vouchers Yape/Plin/Facturas (RapidOCR).
- Filtro Anti-Fotos Vacías: ignora imágenes con < 15 letras reales.
- Escribe en `wsp_recepcion_documentos` y en `hostal_documentos_ocr` dentro de `C:\Recepcion_Ovalo_Local\data\ovalo_sistema.db`.
- Los Triggers SQLite de `ovalo_sistema.db` sincronizan cada documento automáticamente hacia Supabase (`hostal-ovalo`).
"""
import os
import re
import json
import sqlite3
import logging
import argparse
from datetime import datetime

import numpy as np
from PIL import Image
import pymupdf

logging.getLogger("RapidOCR").setLevel(logging.ERROR)
from rapidocr import RapidOCR

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_HOSTAL_PATH = os.path.abspath(os.path.join(BASE_DIR, "..", "data", "ovalo_sistema.db"))
MIN_LETRAS_REALES = 15

RE_SERIE = re.compile(r"\b([FEBR][A-Z0-9]{3})\s*[-–]\s*0*(\d{1,8})\b", re.IGNORECASE)
RE_RUC = re.compile(r"\b((?:10|15|17|20)\d{9})\b")
RE_DNI = re.compile(r"\bDNI\s*[:N°º.-]*\s*(\d{8})\b", re.IGNORECASE)
RE_MONTO = re.compile(r"(?:S/|S\.|\bPEN\b|TOTAL\s*[:S/.]*)\s*(\d{1,3}(?:,\d{3})*(?:\.\d{2})|\d+\.\d{2})", re.IGNORECASE)
RE_OPERACION = re.compile(r"(?:operaci[oó]n|nro\.?\s*op|c[oó]digo\s*de\s*seguridad)\s*[:#N°º.-]*\s*([0-9]{4,14})", re.IGNORECASE)

_ENGINE = None


def get_ocr():
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = RapidOCR()
    return _ENGINE


def ensure_schema(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS wsp_recepcion_documentos (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            id_mensaje        TEXT UNIQUE,
            jid               TEXT,
            fecha_hora        TEXT,
            tipo_archivo      TEXT,
            nombre_archivo    TEXT,
            ruta_local        TEXT,
            metodo_extraccion TEXT,
            categoria         TEXT,
            ruc_emisor        TEXT,
            serie_numero      TEXT,
            monto_detectado   TEXT,
            operacion_nro     TEXT,
            texto_extraido    TEXT,
            fecha_procesado   TEXT DEFAULT (datetime('now', 'localtime'))
        );
        CREATE TABLE IF NOT EXISTS hostal_documentos_ocr (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            id_mensaje        TEXT UNIQUE,
            jid               TEXT NOT NULL DEFAULT '',
            origen_chat       TEXT NOT NULL DEFAULT '',
            fecha_hora        TEXT NOT NULL DEFAULT '',
            tipo_archivo      TEXT NOT NULL DEFAULT '',
            nombre_archivo    TEXT NOT NULL DEFAULT '',
            ruta_local        TEXT NOT NULL DEFAULT '',
            metodo_extraccion TEXT NOT NULL DEFAULT '',
            categoria         TEXT NOT NULL DEFAULT '',
            ruc_emisor        TEXT,
            serie_numero      TEXT,
            monto_detectado   TEXT,
            operacion_nro     TEXT,
            texto_extraido    TEXT NOT NULL DEFAULT '',
            fecha_procesado   TEXT DEFAULT (datetime('now', 'localtime'))
        );
    """)


def ocr_imagen_pil(pil_img: Image.Image) -> str:
    rgb = pil_img.convert("RGB")
    res = get_ocr()(np.array(rgb))
    if not res or not getattr(res, "txts", None):
        return ""
    lineas = [t.strip() for t in res.txts if t and t.strip()]
    return "\n".join(lineas)


def extraer_texto_archivo(ruta: str, max_paginas_pdf: int = 8) -> tuple[str, str, str]:
    ext = os.path.splitext(ruta)[1].lower()
    if ext == ".pdf":
        partes = []
        metodo = "pymupdf"
        with pymupdf.open(ruta) as doc:
            for i, page in enumerate(doc):
                if i >= max_paginas_pdf:
                    break
                txt_digital = (page.get_text("text") or "").strip()
                letras_dig = re.findall(r"[A-Za-zÁÉÍÓÚáéíóúÑñ0-9]", txt_digital)
                if len(letras_dig) >= 25:
                    partes.append(txt_digital)
                else:
                    metodo = "pymupdf+rapidocr"
                    pix = page.get_pixmap(dpi=200, alpha=False)
                    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    txt_ocr = ocr_imagen_pil(img)
                    if txt_ocr:
                        partes.append(txt_ocr)
        return "\n".join(partes).strip(), "pdf", metodo

    if ext in (".xml", ".txt"):
        with open(ruta, "rb") as f:
            return f.read().decode("utf-8", errors="ignore").strip(), ext.lstrip("."), "texto_directo"

    if ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
        with Image.open(ruta) as im:
            return ocr_imagen_pil(im), "imagen", "rapidocr"

    return "", ext.lstrip("."), "desconocido"


def clasificar_y_extraer_campos(texto: str, nombre_archivo: str = "") -> dict:
    letras = re.findall(r"[A-Za-zÁÉÍÓÚáéíóúÑñ0-9]", texto or "")
    palabras = re.findall(r"[A-Za-zÁÉÍÓÚáéíóúÑñ]{2,}", texto or "")

    if len(letras) < MIN_LETRAS_REALES or len(palabras) < 2:
        return {
            "tiene_texto": 0,
            "categoria": "DESCARTADO_SIN_TEXTO",
            "serie_numero": None,
            "ruc_dni": None,
            "monto_detectado": None,
            "operacion_nro": None,
        }

    low = (texto + " " + nombre_archivo).lower()

    m_serie = RE_SERIE.search(texto) or RE_SERIE.search(nombre_archivo)
    serie_num = f"{m_serie.group(1).upper()}-{m_serie.group(2)}" if m_serie else None

    rucs = RE_RUC.findall(texto)
    ruc_propio = "20611986484"
    otros_rucs = [r for r in rucs if r != ruc_propio]
    ruc_dni = otros_rucs[0] if otros_rucs else (rucs[0] if rucs else None)
    if not ruc_dni:
        m_dni = RE_DNI.search(texto)
        if m_dni:
            ruc_dni = m_dni.group(1)

    montos_raw = RE_MONTO.findall(texto)
    montos = []
    for mr in montos_raw:
        try:
            montos.append(float(mr.replace(",", "")))
        except ValueError:
            pass
    monto = str(max(montos)) if montos else None

    m_op = RE_OPERACION.search(texto)
    operacion = m_op.group(1) if m_op else None

    if any(k in low for k in ("factura electr", "boleta de venta", "recibo por honorarios", "nota de cr", "igv", "op. gravada")) or serie_num:
        categoria = "COMPROBANTE_SUNAT"
    elif any(k in low for k in ("yape", "yapeaste", "plin", "bcp", "bbva", "interbank", "scotiabank", "transferencia exitosa", "constancia de pago", "pago exitoso")):
        categoria = "VOUCHER_BANCARIO"
    elif any(k in low for k in ("electrosur", "eps tacna", "movistar", "claro", "entel", "bitel", "suministro", "recibo de luz", "consumo de agua")):
        categoria = "RECIBO_SERVICIO"
    elif any(k in low for k in ("contrato", "arrendamiento", "cláusula", "clausula", "cotización", "cotizacion", "proforma")):
        categoria = "CONTRATO_LEGAL"
    else:
        categoria = "OTRO_DOCUMENTO"

    return {
        "tiene_texto": 1,
        "categoria": categoria,
        "serie_numero": serie_num,
        "ruc_dni": ruc_dni,
        "monto_detectado": monto,
        "operacion_nro": operacion,
    }


def procesar_un_archivo(conn: sqlite3.Connection, ruta: str, id_mensaje: str = None,
                        jid: str = "MANUAL", nombre_emisor: str = "RECEPCION_LOCAL",
                        fecha_hora: str = None, borrar_si_vacia: bool = False) -> dict:
    ruta_abs = os.path.abspath(ruta)
    if not os.path.exists(ruta_abs):
        return {"ok": False, "error": f"No existe archivo: {ruta_abs}"}

    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fecha_hora = fecha_hora or ahora
    id_mensaje = id_mensaje or f"LOCAL::{ruta_abs}"
    nombre_archivo = os.path.basename(ruta_abs)

    texto, tipo_archivo, metodo = extraer_texto_archivo(ruta_abs)
    info = clasificar_y_extraer_campos(texto, nombre_archivo)

    ruta_guardar = ruta_abs
    if info["tiene_texto"] == 0 and borrar_si_vacia and tipo_archivo == "imagen":
        try:
            os.remove(ruta_abs)
            ruta_guardar = "[DESCARTADA_SIN_TEXTO]"
        except OSError:
            pass

    conn.execute("""
        INSERT INTO wsp_recepcion_documentos (
            id_mensaje, jid, fecha_hora, tipo_archivo, nombre_archivo, ruta_local,
            metodo_extraccion, categoria, ruc_emisor, serie_numero, monto_detectado,
            operacion_nro, texto_extraido, fecha_procesado
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id_mensaje) DO UPDATE SET
            categoria = excluded.categoria,
            ruc_emisor = excluded.ruc_emisor,
            serie_numero = excluded.serie_numero,
            monto_detectado = excluded.monto_detectado,
            operacion_nro = excluded.operacion_nro,
            texto_extraido = excluded.texto_extraido,
            fecha_procesado = excluded.fecha_procesado
    """, (
        id_mensaje, jid, fecha_hora, tipo_archivo, nombre_archivo, ruta_guardar,
        metodo, info["categoria"], info["ruc_dni"], info["serie_numero"],
        info["monto_detectado"], info["operacion_nro"],
        texto if info["tiene_texto"] else "", ahora
    ))

    if info["tiene_texto"] == 1:
        conn.execute("""
            INSERT INTO hostal_documentos_ocr (
                id_mensaje, jid, origen_chat, fecha_hora, tipo_archivo, nombre_archivo,
                ruta_local, metodo_extraccion, categoria, ruc_emisor, serie_numero,
                monto_detectado, operacion_nro, texto_extraido, fecha_procesado
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id_mensaje) DO UPDATE SET
                categoria = excluded.categoria,
                ruc_emisor = excluded.ruc_emisor,
                serie_numero = excluded.serie_numero,
                monto_detectado = excluded.monto_detectado,
                operacion_nro = excluded.operacion_nro,
                texto_extraido = excluded.texto_extraido,
                fecha_procesado = excluded.fecha_procesado
        """, (
            id_mensaje, jid, f"WSP_RECEPCION:{nombre_emisor}", fecha_hora, tipo_archivo,
            nombre_archivo, ruta_guardar, metodo, info["categoria"], info["ruc_dni"],
            info["serie_numero"], info["monto_detectado"], info["operacion_nro"],
            texto, ahora
        ))

    conn.commit()
    return {
        "ok": True,
        "id_mensaje": id_mensaje,
        "archivo": nombre_archivo,
        "tipo_archivo": tipo_archivo,
        **info,
        "resumen_texto": (texto[:280] + "...") if len(texto) > 280 else texto
    }


def procesar_pendientes_wsp(conn: sqlite3.Connection, borrar_vacias: bool = True) -> list:
    cur = conn.execute("""
        SELECT m.id_mensaje, m.jid, COALESCE(m.nombre_emisor, m.jid), m.fecha_hora, m.ruta_archivo
        FROM wsp_recepcion_mensajes m
        LEFT JOIN wsp_recepcion_documentos d ON d.id_mensaje = m.id_mensaje
        WHERE m.ruta_archivo IS NOT NULL
          AND m.ruta_archivo != ''
          AND d.id IS NULL
        ORDER BY m.timestamp_unix ASC
    """)
    pendientes = cur.fetchall()
    resultados = []
    for msg_id, jid, nombre_emisor, fecha_hora, ruta in pendientes:
        if os.path.exists(ruta):
            r = procesar_un_archivo(
                conn, ruta, id_mensaje=msg_id, jid=jid,
                nombre_emisor=nombre_emisor, fecha_hora=fecha_hora, borrar_si_vacia=borrar_vacias
            )
            resultados.append(r)
    return resultados


def main():
    parser = argparse.ArgumentParser(description="Extractor OCR + PDF Recepción a ovalo_sistema.db")
    parser.add_argument("--pendientes", action="store_true", help="Procesar archivos pendientes de wsp_recepcion_mensajes")
    parser.add_argument("--archivo", type=str, help="Ruta de un PDF o imagen puntual a extraer y guardar")
    parser.add_argument("--chat-nombre", type=str, default="RECEPCION_LOCAL")
    parser.add_argument("--borrar-vacias", action="store_true", help="Borrar del disco fotos sin texto")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_HOSTAL_PATH, timeout=15)
    ensure_schema(conn)

    if args.archivo:
        res = procesar_un_archivo(conn, args.archivo, nombre_emisor=args.chat_nombre, borrar_si_vacia=args.borrar_vacias)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    elif args.pendientes:
        res = procesar_pendientes_wsp(conn, borrar_vacias=True)
        print(json.dumps({"procesados": len(res), "items": res}, ensure_ascii=False, indent=2))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
