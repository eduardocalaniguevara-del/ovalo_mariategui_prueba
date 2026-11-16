#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Motor de inteligencia de WhatsApp de Recepción (Hotel El Óvalo Mariátegui).

Clasifica los mensajes entrantes (COMPROBANTE / RESERVA / HABITACION / VOUCHER), los cruza con la
base del hotel (celular + datos del texto) y calcula qué chats siguen "pendientes" de respuesta.

Reglas de seguridad (contrato del plan_wsp_en_vivo.md, Componente B):
  * En MODO_PRUEBA los mensajes se leen de la base OPERATIVA en SOLO LECTURA (sqlite ``mode=ro``) más los
    mensajes simulados locales (jid con prefijo ``SIM-``). Si la operativa no está disponible se cae a la
    base local.
  * El cruce (estancias, huespedes_padron, sunat_*, panel) usa SIEMPRE la base de esta carpeta y solo SELECT.
  * Escrituras permitidas SOLO en la base local: ``wsp_alertas_estado``, ``estado_sistema('wsp_auto_adelanto')``,
    ``wsp_recepcion_mensajes`` (únicamente simulados SIM-) y ``wsp_recepcion_cola_envio``
    (estado SIMULADO en MODO_PRUEBA; PENDIENTE solo cuando NO es MODO_PRUEBA).
  * Jamás se escribe en estancias, pagos ni ventas_tienda. Jamás se lanza node en MODO_PRUEBA.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import unicodedata
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import quote

import config as _cfg
from config import MODO_PRUEBA
from database import get_connection

import sqlite3

# ---------------------------------------------------------------------------
# Constantes del contrato
# ---------------------------------------------------------------------------
CATEGORIAS = ("COMPROBANTE", "RESERVA", "HABITACION", "VOUCHER")
_PRIORIDAD = {"VOUCHER": 4, "COMPROBANTE": 3, "RESERVA": 2, "HABITACION": 1}  # desempate

INTERNOS_DEFAULT = {
    "51977129752@s.whatsapp.net": "Eduardo Calani (Admin)",
    "51951535614@s.whatsapp.net": "Eliana (Recepción)",
    "120363410025708860@g.us": "Grupo Ovalo Mariategui",
}
INTERNOS_POR_NOMBRE = {"eleiza store"}  # se compara contra el nombre del chat (minúsculas)

UMBRAL_ROJO_MIN = 5
AUTO_ADELANTO_MIN = 3
VENTANA_ALERTAS_H = 48          # chats con actividad en las últimas 48 h
VENTANA_BANDEJA_DIAS = 7        # pestañas por categoría / interno
MAX_MENSAJES_TARJETA = 12
MAX_TARJETAS = 60

PLANTILLAS = {
    "ADELANTO_RESERVA": "¡Hola! 😊 Gracias por escribir al Hotel El Óvalo Mariátegui. ¿Qué tipo de habitación busca (Simple, Matrimonial, Doble) y para qué fecha y hora aproximada de llegada?",
    "DISPONIBILIDAD": "",  # se genera con libres_hoy()
    "COMPROBANTE_DATOS": "Con gusto. ¿Nos confirma su RUC (factura) o DNI (boleta) y el número de habitación?",
    "COMPROBANTE_EN_PROCESO": "Recibido, en unos minutos le enviamos su comprobante.",
    "COMPROBANTE_EMITIDO": "Su comprobante ya fue emitido. En un momento se lo reenviamos por este medio.",
    "HAB_RECIBIDO": "Recibido 👍 enseguida le atendemos.",
    "VOUCHER_RECIBIDO": "Recibimos su pago 👍 lo registramos enseguida. ¡Gracias!",
}

_BASE_DIR = Path(getattr(_cfg, "BASE_APP_DIR", Path(__file__).resolve().parent))
_DATA_DIR = Path(getattr(_cfg, "DATA_DIR", _BASE_DIR / "data"))
_PATRONES_PATH = _DATA_DIR / "wsp_patrones_entrenados.json"
_INTERNOS_PATH = _DATA_DIR / "wsp_contactos_internos.json"

_JIDS_IGNORADOS_SUFIJOS = ("@broadcast", "@newsletter")


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------
def _ahora() -> float:
    return time.time()


def _sin_tildes(s: str) -> str:
    s = unicodedata.normalize("NFD", str(s or ""))
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


def _solo_digitos(s: Any) -> str:
    return re.sub(r"\D", "", str(s or ""))


def _num(v: Any) -> float:
    try:
        if v is None or v == "":
            return 0.0
        return round(float(str(v).replace("S/", "").replace(",", "").strip()), 2)
    except Exception:
        return 0.0


def _ymd_hoy(delta_dias: int = 0) -> int:
    return int((date.today() + timedelta(days=delta_dias)).strftime("%Y%m%d"))


def _parse_dmy(s: str) -> Optional[date]:
    m = re.match(r"^\s*(\d{1,2})/(\d{1,2})/(\d{4})", str(s or ""))
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def _hora_txt(ts: int) -> str:
    try:
        dt = datetime.fromtimestamp(int(ts))
        if dt.date() == date.today():
            return dt.strftime("%H:%M")
        return dt.strftime("%d/%m %H:%M")
    except Exception:
        return ""


def _celular9(jid: str) -> str:
    """Últimos 9 dígitos del celular detrás del jid (vacío para grupos/lid)."""
    j = str(jid or "")
    if j.endswith("@g.us") or j.endswith("@lid"):
        return ""
    base = j.split("@")[0]
    if base.upper().startswith("SIM-"):
        base = base[4:]
    d = _solo_digitos(base)
    return d[-9:] if len(d) >= 9 else ""


# ---------------------------------------------------------------------------
# Base de datos: tablas locales y fuentes de mensajes
# ---------------------------------------------------------------------------
_tabla_lock = threading.Lock()
_tabla_lista = False


def _asegurar_tablas() -> None:
    global _tabla_lista
    if _tabla_lista:
        return
    with _tabla_lock:
        if _tabla_lista:
            return
        conn = get_connection()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS wsp_alertas_estado (
                    chat_jid            TEXT PRIMARY KEY,
                    atendido_hasta_ts   INTEGER DEFAULT 0,
                    atendido_por        TEXT DEFAULT '',
                    adelanto_enviado_ts INTEGER DEFAULT 0,
                    categoria_manual    TEXT DEFAULT '',
                    actualizado_en      TEXT DEFAULT (datetime('now','localtime'))
                )"""
            )
            conn.commit()
        finally:
            conn.close()
        _tabla_lista = True


def _ruta_operativa_db() -> Optional[Path]:
    """Ruta de la base operativa (solo MODO_PRUEBA). None si no aplica o no existe."""
    if not MODO_PRUEBA:
        return None
    try:
        carpeta = str((getattr(_cfg, "PERFIL_HOTEL", {}) or {}).get("carpeta_operativa") or "").strip()
    except Exception:
        carpeta = ""
    if not carpeta:
        return None
    try:
        raiz = Path(carpeta)
        if raiz.resolve() == _BASE_DIR.resolve():
            return None
        db = raiz / "data" / "ovalo_sistema.db"
        return db if db.exists() else None
    except Exception:
        return None


def _abrir_operativa_ro() -> Optional[sqlite3.Connection]:
    """Conexión SOLO LECTURA a la base operativa. Cualquier escritura lanza sqlite3.OperationalError."""
    db = _ruta_operativa_db()
    if db is None:
        return None
    try:
        uri = "file:" + quote(db.as_posix(), safe="/:") + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=3.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=3000;")
        conn.execute("PRAGMA query_only=1;")
        return conn
    except Exception:
        return None


_SQL_MENSAJES = """
SELECT COALESCE(chat_jid, jid)                         AS chat,
       COALESCE(id_mensaje, id)                        AS mid,
       COALESCE(timestamp_unix, timestamp, 0)          AS ts,
       MAX(COALESCE(es_mio, 0), COALESCE(desde_recepcion, 0)) AS es_mio,
       COALESCE(tipo_mensaje, tipo, 'texto')           AS tipo,
       COALESCE(texto, '')                             AS texto,
       COALESCE(chat_nombre, '')                       AS chat_nombre,
       COALESCE(nombre_emisor, remitente_nombre, '')   AS emisor,
       COALESCE(ruta_archivo, archivo_ruta, '')        AS archivo
  FROM wsp_recepcion_mensajes
 WHERE (timestamp_unix >= ? OR (timestamp_unix IS NULL AND timestamp >= ?))
   {extra}
 ORDER BY ts ASC
"""


def _consulta_mensajes(conn: sqlite3.Connection, desde_ts: int, solo_sim: bool) -> List[Dict[str, Any]]:
    extra = "AND COALESCE(chat_jid, jid) LIKE 'SIM-%'" if solo_sim else ""
    cur = conn.execute(_SQL_MENSAJES.format(extra=extra), (int(desde_ts), int(desde_ts)))
    return [dict(r) for r in cur.fetchall()]


def _leer_mensajes(desde_ts: int) -> List[Dict[str, Any]]:
    """Mensajes desde ``desde_ts``. Prueba: operativa (ro) + simulados locales; si la operativa falla, base local."""
    filas: List[Dict[str, Any]] = []
    op = _abrir_operativa_ro() if MODO_PRUEBA else None
    local = get_connection()
    try:
        if op is not None:
            try:
                filas.extend(_consulta_mensajes(op, desde_ts, solo_sim=False))
                filas.extend(_consulta_mensajes(local, desde_ts, solo_sim=True))
                filas.sort(key=lambda r: r["ts"])
                return filas
            except Exception:
                filas = []  # operativa no disponible -> cae a local
        # operativa (get_connection) o respaldo local en la prueba
        return _consulta_mensajes(local, desde_ts, solo_sim=False)
    finally:
        local.close()
        if op is not None:
            op.close()


def _leer_ocr(ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """OCR/clasificación de documentos por id_mensaje (wsp_recepcion_documentos). Tolerante a ausencia."""
    ids = [i for i in ids if i]
    if not ids:
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    conns: List[sqlite3.Connection] = []
    op = _abrir_operativa_ro() if MODO_PRUEBA else None
    if op is not None:
        conns.append(op)
    conns.append(get_connection())
    try:
        marcas = ",".join("?" for _ in ids)
        for c in conns:
            try:
                cur = c.execute(
                    f"SELECT COALESCE(id_mensaje, mensaje_id) AS mid, texto_extraido, categoria, monto_detectado "
                    f"FROM wsp_recepcion_documentos WHERE COALESCE(id_mensaje, mensaje_id) IN ({marcas})",
                    ids,
                )
                for r in cur.fetchall():
                    out.setdefault(
                        str(r["mid"]),
                        {"texto": r["texto_extraido"] or "", "categoria": r["categoria"] or "", "monto": r["monto_detectado"] or ""},
                    )
            except Exception:
                continue
    finally:
        for c in conns:
            try:
                c.close()
            except Exception:
                pass
    return out


def _estado_daemon_raw() -> str:
    """Valor crudo de wsp_recepcion_estado.conexion (operativa en ro durante la prueba)."""
    conns: List[sqlite3.Connection] = []
    op = _abrir_operativa_ro() if MODO_PRUEBA else None
    if op is not None:
        conns.append(op)
    conns.append(get_connection())
    try:
        for c in conns:
            try:
                r = c.execute("SELECT valor FROM wsp_recepcion_estado WHERE clave='conexion'").fetchone()
                if r and r[0] is not None:
                    return str(r[0])
            except Exception:
                continue
    finally:
        for c in conns:
            try:
                c.close()
            except Exception:
                pass
    return ""


def estado_daemon() -> str:
    raw = _estado_daemon_raw().strip().upper()
    if raw.startswith("CONECT"):
        return "CONECTADO"
    if raw == "ESPERANDO_QR":
        return "ESPERANDO_QR"
    if not raw:
        return "DESCONECTADO_UNKNOWN"
    return raw if raw.startswith("DESCONECTADO") else f"DESCONECTADO_{raw}"


def _cargar_estado_chats() -> Dict[str, Dict[str, Any]]:
    _asegurar_tablas()
    conn = get_connection()
    try:
        rows = conn.execute("SELECT * FROM wsp_alertas_estado").fetchall()
        return {r["chat_jid"]: dict(r) for r in rows}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Internos
# ---------------------------------------------------------------------------
_internos_cache: Dict[str, Any] = {"mtime": -1.0, "jids": dict(INTERNOS_DEFAULT), "nombres": set(INTERNOS_POR_NOMBRE)}


def _cargar_internos() -> Tuple[Dict[str, str], Set[str]]:
    try:
        mt = _INTERNOS_PATH.stat().st_mtime
    except OSError:
        mt = 0.0
    if mt == _internos_cache["mtime"]:
        return _internos_cache["jids"], _internos_cache["nombres"]
    jids = dict(INTERNOS_DEFAULT)
    nombres = set(INTERNOS_POR_NOMBRE)
    if mt:
        try:
            data = json.loads(_INTERNOS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                if "jids" in data or "nombres" in data:
                    extra_j = data.get("jids") or {}
                    if isinstance(extra_j, list):
                        extra_j = {str(j): str(j) for j in extra_j}
                    jids.update({str(k): str(v) for k, v in extra_j.items()})
                    nombres |= {str(n).strip().lower() for n in (data.get("nombres") or [])}
                else:
                    jids.update({str(k): str(v) for k, v in data.items()})
            elif isinstance(data, list):
                for it in data:
                    s = str(it).strip()
                    if "@" in s or _solo_digitos(s) == s:
                        jids[s if "@" in s else f"{s}@s.whatsapp.net"] = s
                    else:
                        nombres.add(s.lower())
        except Exception:
            pass
    _internos_cache.update({"mtime": mt, "jids": jids, "nombres": nombres})
    return jids, nombres


def _es_interno(jid: str, nombre: str) -> bool:
    jids, nombres = _cargar_internos()
    if jid in jids or jid.endswith("@g.us"):
        return True
    c9 = _celular9(jid)
    if c9:
        for j in jids:
            if _celular9(j) == c9:
                return True
    if str(nombre or "").strip().lower() in nombres:
        return True
    return False


# ---------------------------------------------------------------------------
# Patrones del clasificador (diccionario base + data/wsp_patrones_entrenados.json recargable en caliente)
# ---------------------------------------------------------------------------
_NORMALIZACIONES_BASE = {
    "abitacion": "habitacion", "abitasion": "habitacion", "habitasion": "habitacion", "habitaciom": "habitacion",
    "habitacon": "habitacion", "avitacion": "habitacion", "habitacio": "habitacion", "cuarto": "habitacion",
    "fatura": "factura", "facutra": "factura", "factuta": "factura", "facctura": "factura", "fctura": "factura",
    "voleta": "boleta", "boletta": "boleta", "bolta": "boleta", "boelta": "boleta",
    "matrimoneal": "matrimonial", "matrimonil": "matrimonial", "matri": "matrimonial",
    "yapee": "yape", "yapie": "yape", "yapeo": "yape", "yapeado": "yape", "yapie": "yape", "yapé": "yape",
    "pagee": "pague", "paguee": "pague", "individual": "simple", "dobles": "doble",
    "xfa": "porfa", "porfis": "porfa", "porfavor": "porfa", "pf": "porfa", "pls": "porfa",
}

_PATRONES_BASE: Dict[str, Dict[str, Dict[str, float]]] = {
    "COMPROBANTE": {
        "palabras": {"factura": 3.0, "boleta": 3.0, "ruc": 2.5, "comprobante": 2.5, "sunat": 2.0, "facturar": 3.0,
                     "facturacion": 3.0, "boletear": 3.0, "emision": 1.5, "emitir": 1.5, "emitan": 2.0, "emita": 2.0,
                     "razon": 0.5, "social": 0.5},
        "frases": {"a la espera de mi factura": 3.0, "a nombre de": 1.5, "mi factura": 2.0, "mi boleta": 2.0,
                   "necesito factura": 2.0, "necesito boleta": 2.0, "con ruc": 2.0, "razon social": 2.0,
                   "me emiten": 1.5, "me pueden emitir": 2.0, "me envian mi": 1.5, "constancia de": 0.5},
    },
    "RESERVA": {
        "palabras": {"reservar": 3.0, "reserva": 3.0, "reservacion": 3.0, "separar": 3.0, "separen": 3.0, "separa": 2.5,
                     "disponible": 2.5, "disponibilidad": 3.0, "libre": 1.5, "libres": 1.5, "precio": 2.0,
                     "tarifa": 2.0, "costo": 2.0, "cuanto": 1.5, "cuesta": 2.0, "habitacion": 1.0, "matrimonial": 2.0,
                     "simple": 1.5, "doble": 1.5, "triple": 1.5, "tina": 1.5, "tienen": 1.5, "tiene": 1.0,
                     "hay": 0.8, "alojamiento": 1.5, "hospedaje": 2.0, "hospedar": 2.0, "llegaria": 1.5, "llegamos": 1.0,
                     "llego": 0.8, "noche": 0.8, "hoy": 0.5, "manana": 0.5, "pasado": 0.3, "personas": 1.0, "adelanto": 1.0},
        "frases": {"una habitacion de": 2.5, "me das una": 2.0, "tienen habitacion": 3.0, "tiene habitacion": 3.0,
                   "hay habitacion": 3.0, "hay cuarto": 3.0, "cuanto cuesta": 2.0, "cuanto esta": 1.5, "cuanto sale": 1.5,
                   "cuanto la noche": 3.0, "tienen disponible": 3.0, "tiene disponible": 3.0, "para hoy": 1.0,
                   "para manana": 1.0, "para esta noche": 1.5, "quisiera una": 1.5, "quiero una": 1.5,
                   "necesito una habitacion": 3.0, "busco habitacion": 3.0, "con bano privado": 1.5, "con tina": 1.0},
    },
    "HABITACION": {
        "palabras": {"llave": 3.0, "llaves": 3.0, "toalla": 3.0, "toallas": 3.0, "visita": 2.5, "visitas": 2.5,
                     "visitante": 2.5, "agua": 1.2, "caliente": 1.5, "ducha": 2.0, "tv": 1.5, "televisor": 1.5,
                     "control": 1.0, "wifi": 2.0, "clave": 1.0, "sabanas": 2.0, "frazada": 2.0, "limpieza": 2.0,
                     "pasar": 1.5, "pase": 1.5, "baja": 0.8, "bajo": 0.8, "tienda": 1.0, "snack": 1.2, "gaseosa": 1.2,
                     "ocupado": 1.0, "ruido": 1.5, "jabon": 1.5, "papel": 1.5, "foco": 1.5, "puerta": 1.5, "sale": 0.5,
                     "salgo": 1.0, "checkout": 1.5, "late": 0.8, "dele": 1.5, "soy": 1.0},
        "frases": {"dele la llave": 3.0, "le hace pasar": 2.5, "me abren": 2.0, "no hay agua": 3.0, "no funciona": 2.0,
                   "no prende": 2.0, "me puede subir": 2.0, "soy de la": 1.5, "estoy en la": 1.5, "mi habitacion": 1.5,
                   "salida tardia": 2.0, "dejo la llave": 2.5, "dejar la llave": 2.5},
    },
    "VOUCHER": {
        "palabras": {"yape": 3.0, "plin": 3.0, "transferencia": 3.0, "transferi": 3.0, "deposito": 2.5, "deposite": 2.5,
                     "voucher": 3.0, "pague": 2.5, "pago": 1.5, "pagado": 2.0, "abone": 2.5, "abono": 2.0,
                     "constancia": 1.5, "operacion": 2.0, "captura": 1.5, "adjunto": 1.0, "cancele": 2.5, "cancelado": 1.5,
                     "bcp": 1.5, "interbank": 1.5, "bbva": 1.5, "scotiabank": 1.5, "cuenta": 0.8, "cci": 1.5},
        "frases": {"ya pague": 3.0, "ya te pague": 3.0, "te yape": 3.0, "te yapeo": 3.0, "le yape": 3.0,
                   "le yapeo": 3.0, "numero de operacion": 3.0, "nro de operacion": 3.0, "comprobante de pago": 1.5,
                   "pago realizado": 3.0, "constancia de pago": 3.0, "ya hice el pago": 3.0, "ya hice el deposito": 3.0,
                   "yapeaste": 3.0, "yapeo exitoso": 3.0, "te envio el pago": 2.5, "aqui esta el pago": 2.5},
    },
}

# Vocabulario cuyo mensaje (si TODAS sus palabras están aquí) se considera ruido
_RUIDO_PALABRAS_BASE = {
    "ok", "okey", "oki", "okay", "oka", "gracias", "graciass", "grasias", "muchas", "muchisimas", "mil", "listo", "lista",
    "vale", "dale", "bueno", "buena", "buenas", "buenos", "buen", "dia", "dias", "tarde", "tardes", "noche", "noches",
    "hola", "holaa", "holi", "saludos", "senorita", "senor", "senora", "joven", "porfa", "favor", "por", "ya", "entendido",
    "perfecto", "excelente", "genial", "igual", "de", "nada", "todos", "modos", "modo", "claro", "no", "jaja", "jajaja",
    "jeje", "amen", "bendiciones", "cuidese", "hasta", "luego", "adios", "chau", "chao", "un", "abrazo", "gracia", "dios",
    "le", "lo", "la", "te", "se", "que", "esta", "esta", "bien", "super", "mucho", "estimado", "estimada", "disculpe",
    "disculpa", "perdon", "y", "a", "el", "en", "con", "su", "mi",
}
_RUIDO_EXACTOS_BASE = {"[sticker]", "[reaccion]", "[reaction]", "[emoji]", "[gif]", "👍", "🙏", "👌", "ok", "gracias"}
_TIPOS_RUIDO = {"sticker", "reaccion", "reaction", "protocol", "protocolo", "gif", "system", "sistema", "call", "llamada"}
_TIPOS_ADJUNTO = {"imagen", "image", "documento", "document", "pdf", "foto", "archivo"}

_patrones_lock = threading.Lock()
_patrones: Dict[str, Any] = {}
_patrones_mtime: float = -1.0


def _fusionar_patrones() -> Dict[str, Any]:
    """Base embebida + archivo entrenado (el archivo agrega/pisa llaves)."""
    pal = {c: dict(_PATRONES_BASE[c]["palabras"]) for c in CATEGORIAS}
    fra = {c: dict(_PATRONES_BASE[c]["frases"]) for c in CATEGORIAS}
    norm = dict(_NORMALIZACIONES_BASE)
    exactos = set(_RUIDO_EXACTOS_BASE)
    ruido_pal = set(_RUIDO_PALABRAS_BASE)
    cargado = False
    try:
        data = json.loads(_PATRONES_PATH.read_text(encoding="utf-8"))
        cargado = True
        for k, v in (data.get("normalizaciones") or {}).items():
            norm[_sin_tildes(str(k)).lower()] = _sin_tildes(str(v)).lower()
        cats = data.get("categorias") or {}
        for c in CATEGORIAS:
            ent = cats.get(c) or {}
            for p, w in (ent.get("palabras") or {}).items():
                try:
                    pal[c][_sin_tildes(str(p)).lower()] = float(w)
                except (TypeError, ValueError):
                    pass
            for f, w in (ent.get("frases") or {}).items():
                try:
                    fra[c][_sin_tildes(str(f)).lower()] = float(w)
                except (TypeError, ValueError):
                    pass
        rz = cats.get("RUIDO") or {}
        for e in rz.get("exactos") or []:
            exactos.add(str(e).strip().lower())
    except (OSError, ValueError, AttributeError):
        pass
    return {"pal": pal, "fra": fra, "norm": norm, "exactos": exactos, "ruido_pal": ruido_pal, "archivo": cargado}


def _get_patrones() -> Dict[str, Any]:
    global _patrones, _patrones_mtime
    try:
        mt = _PATRONES_PATH.stat().st_mtime
    except OSError:
        mt = 0.0
    if _patrones and mt == _patrones_mtime:
        return _patrones
    with _patrones_lock:
        if not _patrones or mt != _patrones_mtime:
            _patrones = _fusionar_patrones()
            _patrones_mtime = mt
    return _patrones


# ---------------------------------------------------------------------------
# Habitaciones válidas
# ---------------------------------------------------------------------------
_habs_cache: Dict[str, Any] = {"ts": 0.0, "set": set()}


def _habs_validas() -> Set[str]:
    if _ahora() - _habs_cache["ts"] < 300 and _habs_cache["set"]:
        return _habs_cache["set"]
    conn = get_connection()
    try:
        rows = conn.execute("SELECT DISTINCT TRIM(hab) FROM estancias WHERE TRIM(COALESCE(hab,''))<>''").fetchall()
        vals = {str(r[0]).strip().lower() for r in rows if len(str(r[0]).strip()) >= 2}
    except Exception:
        vals = set()
    finally:
        conn.close()
    _habs_cache.update({"ts": _ahora(), "set": vals})
    return vals


# ---------------------------------------------------------------------------
# Clasificación
# ---------------------------------------------------------------------------
def _normalizar(texto: str) -> Tuple[str, List[str]]:
    p = _get_patrones()
    t = _sin_tildes(str(texto or "")).lower()
    t = re.sub(r"[^a-z0-9\[\]]+", " ", t)
    toks = [p["norm"].get(tok, tok) for tok in t.split()]
    return " ".join(toks), toks


def _tokenset(toks: List[str]) -> Set[str]:
    s = set(toks)
    for t in toks:  # singular tosco: llaves->llave, facturas->factura
        if len(t) > 4 and t.endswith("s"):
            s.add(t[:-1])
        if len(t) > 5 and t.endswith("es"):
            s.add(t[:-2])
    return s


def es_ruido_mensaje(texto: str, tipo_msg: str = "texto") -> bool:
    """True si el mensaje (individual) no requiere atención: ok/gracias/saludo suelto/sticker/vacío."""
    tipo = str(tipo_msg or "texto").lower()
    if tipo in _TIPOS_RUIDO:
        return True
    if tipo in _TIPOS_ADJUNTO or tipo in ("audio", "ptt", "video"):
        return False
    p = _get_patrones()
    crudo = str(texto or "").strip().lower()
    if crudo in p["exactos"]:
        return True
    norm, toks = _normalizar(texto)
    if not toks:
        return True  # solo emojis/puntuación
    if norm in p["exactos"]:
        return True
    return all(t in p["ruido_pal"] for t in toks)


_RE_RUC = re.compile(r"(?<!\d)((?:10|15|17|20)\d{9})(?!\d)")
_RE_DNI = re.compile(r"(?<!\d)(\d{8})(?!\d)")
_RE_MONTO = re.compile(r"(?:s\s*/\s*\.?\s*|soles?\s*)(\d{1,4}(?:[.,]\d{1,2})?)|(\d{1,4}(?:[.,]\d{1,2})?)\s*(?:soles|sol)\b", re.I)
_TIPOS_HAB = (
    ("tina", "tina"), ("matrimonial", "matrimonial"), ("triple", "triple"), ("doble", "doble"), ("simple", "simple"),
)


def extraer_entidades(texto: str, habs_validas: Optional[Set[str]] = None) -> Dict[str, Any]:
    """Entidades del texto: habs (solo válidas), dni, ruc, montos, precio_ref, tipo_hab, fecha_ref, nombre."""
    habs_validas = habs_validas if habs_validas is not None else _habs_validas()
    crudo = str(texto or "")
    ent: Dict[str, Any] = {"habs": [], "dni": None, "ruc": None, "montos": [], "precio_ref": None,
                           "tipo_hab": None, "fecha_ref": None, "nombre": None}
    work = crudo

    m = _RE_RUC.search(work)
    if m:
        ent["ruc"] = m.group(1)
        work = work.replace(m.group(1), " ")
    m = _RE_DNI.search(work)
    if m:
        ent["dni"] = m.group(1)
        work = work.replace(m.group(1), " ")

    for m in _RE_MONTO.finditer(work):
        raw = (m.group(1) or m.group(2) or "").replace(",", ".")
        try:
            ent["montos"].append(float(raw))
        except ValueError:
            pass
    sin_montos = _RE_MONTO.sub(" ", work)

    norm, toks = _normalizar(sin_montos)
    vistos: List[str] = []
    for t in toks:
        if t in habs_validas and t not in vistos:
            vistos.append(t)
    ent["habs"] = vistos

    # precio de referencia: "habitación de 50", "a 60", o el primer monto con S/
    pr = None
    for m in re.finditer(r"\b(?:de|a|hasta|en|por|x)\s+(\d{2,3})\b", norm):
        v = m.group(1)
        if v not in habs_validas:
            pr = float(v)
            break
    if pr is None and ent["montos"]:
        pr = ent["montos"][0]
    ent["precio_ref"] = pr

    for clave, valor in _TIPOS_HAB:
        if re.search(rf"\b{clave}\b", norm):
            ent["tipo_hab"] = valor
            break

    if re.search(r"\b(pasado manana)\b", norm):
        ent["fecha_ref"] = "pasado mañana"
    elif re.search(r"\b(hoy|esta noche|ahorita|ahora)\b", norm):
        ent["fecha_ref"] = "hoy"
    elif re.search(r"\bmanana\b", norm):
        ent["fecha_ref"] = "mañana"
    else:
        md = re.search(r"\b(\d{1,2})[/-](\d{1,2})\b", crudo)
        if md:
            ent["fecha_ref"] = f"{int(md.group(1)):02d}/{int(md.group(2)):02d}"

    mn = re.search(
        r"(?:a nombre de|me llamo|mi nombre es|soy)\s+([A-Za-zÁÉÍÓÚáéíóúÑñ]+(?:\s+[A-Za-zÁÉÍÓÚáéíóúÑñ]+){0,3})",
        crudo, re.I)
    if mn:
        cand = re.split(r"\b(?:y|de la|de|con|para|habitacion|hab|ruc|dni)\b", mn.group(1), flags=re.I)[0].strip()
        if cand and len(cand) >= 3 and _sin_tildes(cand).lower() not in ("la", "el", "yo"):
            ent["nombre"] = cand.title()
    return ent


def clasificar(texto: str, tipo_msg: str = "texto", ocr: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """-> {"categoria": str|None, "confianza": float, "es_ruido": bool, "motivos": [str]}"""
    p = _get_patrones()
    tipo = str(tipo_msg or "texto").lower()
    texto = str(texto or "")
    if es_ruido_mensaje(texto, tipo):
        return {"categoria": None, "confianza": 0.0, "es_ruido": True, "motivos": ["RUIDO"], "puntaje": 0.0}
    habs = _habs_validas()
    norm, toks = _normalizar(texto)
    tset = _tokenset(toks)
    padded = f" {norm} "
    sc = {c: 0.0 for c in CATEGORIAS}
    motivos: List[str] = []

    for c in CATEGORIAS:
        for pal, w in p["pal"][c].items():
            if pal in tset:
                sc[c] += w
                motivos.append(f"{c}:{pal}")
        for fr, w in p["fra"][c].items():
            if f" {fr} " in padded:
                sc[c] += w
                motivos.append(f"{c}:'{fr}'")

    ent = extraer_entidades(texto, habs)
    if ent["habs"]:
        sc["HABITACION"] += 2.5
        motivos.append("HABITACION:hab_valida=" + ",".join(ent["habs"]))
    if ent["ruc"]:
        sc["COMPROBANTE"] += 2.5
        motivos.append("COMPROBANTE:ruc")
    elif ent["dni"]:
        sc["COMPROBANTE"] += 1.0
        motivos.append("COMPROBANTE:dni")
    if ent["tipo_hab"]:
        sc["RESERVA"] += 1.0
        motivos.append(f"RESERVA:tipo={ent['tipo_hab']}")

    es_adjunto = tipo in _TIPOS_ADJUNTO
    if es_adjunto:
        sc["VOUCHER"] += 2.5
        motivos.append("VOUCHER:imagen")
    if ocr:
        otxt = _sin_tildes(str(ocr.get("texto") or "")).lower()
        if re.search(r"\b(yape|plin|transferencia|operacion|constancia|deposito|voucher|bcp|interbank|bbva)\b", otxt):
            sc["VOUCHER"] += 4.0
            motivos.append("VOUCHER:ocr_bancario")
        elif re.search(r"\b(factura|boleta|ruc)\b", otxt):
            sc["COMPROBANTE"] += 2.0
            motivos.append("COMPROBANTE:ocr")
        if str(ocr.get("monto") or "").strip():
            sc["VOUCHER"] += 1.0

    orden = sorted(CATEGORIAS, key=lambda c: (sc[c], _PRIORIDAD[c]), reverse=True)
    mejor, segundo = orden[0], orden[1]
    if sc[mejor] < 1.0:
        return {"categoria": None, "confianza": 0.0, "es_ruido": es_ruido_mensaje(texto, tipo), "motivos": motivos,
                "puntaje": 0.0}
    total = sum(sc.values())
    conf = round(min(0.99, (sc[mejor] / total) * min(1.0, sc[mejor] / 4.0)), 2)
    return {"categoria": mejor, "confianza": conf, "es_ruido": False, "motivos": motivos[:12],
            "puntaje": round(sc[mejor], 2)}


# ---------------------------------------------------------------------------
# Cruce con la base del hotel (solo SELECT, base de esta carpeta)
# ---------------------------------------------------------------------------
_EST_COLS = ("estancia_uuid, hab, huesped, dni, ruc, empresa, celular, fecha_in_dmy, fecha_out_dmy, ymd_in, ymd_out, "
             "noches, tarifa, tipo_hab, estado_estancia, adic_snacks, adic_servicios, adic_comp, pago_ini_efec, "
             "pago_ini_yape, pago_post_efec, pago_post_yape, pago_visa, deuda_anterior, solicita_comp, tipo_comp, "
             "estado_comp, nro_comp, monto_factura, observaciones")


def _estancia_dict(r: sqlite3.Row) -> Dict[str, Any]:
    noches = max(1, int(r["noches"] or 1))
    tarifa = _num(r["tarifa"])
    adic = _num(r["adic_snacks"]) + _num(r["adic_servicios"]) + _num(r["adic_comp"])
    pagado = round(_num(r["pago_ini_efec"]) + _num(r["pago_post_efec"]) + _num(r["pago_ini_yape"])
                   + _num(r["pago_post_yape"]) + _num(r["pago_visa"]), 2)
    total = round(noches * tarifa + adic + _num(r["deuda_anterior"]), 2)
    return {
        "uuid": r["estancia_uuid"], "hab": str(r["hab"] or "").strip(), "huesped": r["huesped"] or "",
        "dni": r["dni"] or "", "ruc": r["ruc"] or "", "empresa": r["empresa"] or "", "celular": r["celular"] or "",
        "check_in": r["fecha_in_dmy"] or "", "check_out": r["fecha_out_dmy"] or "", "noches": noches,
        "tarifa": tarifa, "tipo_hab": r["tipo_hab"] or "", "estado_estancia": r["estado_estancia"] or "",
        "adic_comp": _num(r["adic_comp"]), "total": total, "pagado": pagado, "saldo": round(total - pagado, 2),
        "solicita_comp": r["solicita_comp"] or "", "tipo_comp": r["tipo_comp"] or "",
        "estado_comp": r["estado_comp"] or "", "nro_comp": r["nro_comp"] or "",
        "monto_factura": _num(r["monto_factura"]),
    }


def _mejor_estancia(rows: List[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if not rows:
        return None
    rows = sorted(rows, key=lambda r: (str(r["estado_estancia"] or "").lower().startswith("en curso"),
                                       int(r["ymd_in"] or 0)), reverse=True)
    return _estancia_dict(rows[0])


_SQL_CEL = "REPLACE(REPLACE(REPLACE(REPLACE(COALESCE({c},''),' ',''),'-',''),'+',''),'.','')"


def _estancia_por(conn: sqlite3.Connection, where: str, params: Tuple, min_ymd: int) -> Optional[Dict[str, Any]]:
    rows = conn.execute(
        f"SELECT {_EST_COLS} FROM estancias WHERE {where} "
        f"AND COALESCE(NULLIF(ymd_out,0), ymd_in, 0) >= ? AND LOWER(COALESCE(estado_estancia,'')) NOT LIKE 'cancel%' "
        f"ORDER BY ymd_in DESC LIMIT 12",
        params + (min_ymd,),
    ).fetchall()
    return _mejor_estancia(rows)


def _comprobante_info(conn: sqlite3.Connection, est: Dict[str, Any]) -> Dict[str, Any]:
    nro = str(est.get("nro_comp") or "").strip()
    estado = str(est.get("estado_comp") or "").strip()
    sunat: List[Dict[str, Any]] = []
    try:
        if nro:
            for r in conn.execute(
                "SELECT nro_cp, estado, total, fecha_emision FROM sunat_ventas WHERE nro_cp=? LIMIT 3", (nro,)):
                sunat.append({"nro_cp": r["nro_cp"], "estado": r["estado"], "total": _num(r["total"]),
                              "fecha_emision": r["fecha_emision"]})
        d_in = _parse_dmy(est.get("check_in"))
        if d_in and est.get("hab"):
            noches = [(d_in + timedelta(days=i)).strftime("%d/%m/%Y") for i in range(max(1, int(est.get("noches") or 1)))]
            marcas = ",".join("?" for _ in noches)
            for r in conn.execute(
                f"SELECT nro_cp, estado, monto_linea, noche_dmy FROM sunat_lineas_cache "
                f"WHERE TRIM(hab)=? AND noche_dmy IN ({marcas}) AND UPPER(COALESCE(estado,''))='VIGENTE' LIMIT 8",
                (est["hab"], *noches)):
                if not any(s["nro_cp"] == r["nro_cp"] for s in sunat):
                    sunat.append({"nro_cp": r["nro_cp"], "estado": r["estado"], "total": _num(r["monto_linea"]),
                                  "noche": r["noche_dmy"]})
    except Exception:
        pass
    emitido = bool(nro and estado.lower().startswith("emitido")) or any(
        str(s.get("estado") or "").upper() == "VIGENTE" for s in sunat)
    nro_final = nro or (sunat[0]["nro_cp"] if sunat else "")
    return {"emitido": emitido, "nro": nro_final, "tipo": est.get("tipo_comp") or "", "estado": estado,
            "solicitado": est.get("solicita_comp") or "", "sunat": sunat}


def auditar_comprobante(estancia: Dict[str, Any], monto_solicitado_dia: Optional[float] = None,
                        comprobante: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Regla del 25 % (versión SQLite de auditar_solicitud_wsp.py, sin gspread)."""
    obs: List[str] = []
    noches = max(1, int(estancia.get("noches") or 1))
    tarifa = _num(estancia.get("tarifa"))
    adic = _num(estancia.get("adic_comp"))
    saldo = _num(estancia.get("saldo"))
    ya = (comprobante or {}).get("nro") if (comprobante or {}).get("emitido") else None
    diferencia = req25 = falta = 0.0
    if monto_solicitado_dia and monto_solicitado_dia > tarifa:
        diferencia = round(max(0.0, monto_solicitado_dia * noches - tarifa * noches), 2)
        req25 = round(0.25 * diferencia, 2)
        falta = round(max(0.0, req25 - adic), 2)
        obs.append(f"Pide S/ {monto_solicitado_dia:.2f}/noche y la tarifa es S/ {tarifa:.2f}: diferencia S/ {diferencia:.2f}, "
                   f"25 % = S/ {req25:.2f}.")
        obs.append("Adicional por comprobante OK." if falta <= 0 else
                   f"Falta cobrar S/ {falta:.2f} de adicional (registrado S/ {adic:.2f}).")
    if saldo > 0.009:
        obs.append(f"Saldo pendiente S/ {saldo:.2f}: cobrar antes de emitir.")
    if ya:
        obs.append(f"Ya emitido {ya}.")
    if not obs:
        obs.append("Sin observaciones: puede emitirse.")
    return {"ya_emitido": ya, "falta_adicional": falta, "saldo": saldo, "observaciones": obs,
            "monto_solicitado_dia": monto_solicitado_dia, "diferencia": diferencia, "req_25": req25,
            "adic_registrado": adic, "listo_para_emitir": (not ya) and falta <= 0 and saldo <= 0.009}


def cruzar(jid: str, entidades: Dict[str, Any], categoria: Optional[str] = None) -> Dict[str, Any]:
    """Vía 1 (celular) y vía 2 (texto: hab/dni/ruc/nombre) contra estancias activas o de los últimos 7 días."""
    ent = entidades or {}
    min_ymd = _ymd_hoy(-7)
    cel9 = _celular9(jid)
    est_cel = est_txt = None
    huesped = None
    coincidencias: List[str] = []
    conn = get_connection()
    try:
        if cel9:
            col_e = _SQL_CEL.format(c="celular")
            est_cel = _estancia_por(conn, f"{col_e} LIKE ?", ("%" + cel9,), min_ymd)
            col_p = _SQL_CEL.format(c="celular")
            rp = conn.execute(
                f"SELECT dni, nombre, empresa, ruc, celular, visitas, hab_frec, tarifa_frec FROM huespedes_padron "
                f"WHERE {col_p} LIKE ? ORDER BY visitas DESC LIMIT 1", ("%" + cel9,)).fetchone()
            if rp:
                huesped = dict(rp)
                if not est_cel and rp["dni"]:
                    est_cel = _estancia_por(conn, "dni=?", (rp["dni"],), min_ymd)
        # --- vía 2: texto
        for h in ent.get("habs") or []:
            est_txt = _estancia_por(conn, "TRIM(hab)=?", (str(h),), min_ymd)
            if est_txt:
                coincidencias.append(f"hab {h}")
                break
        if not est_txt and ent.get("dni"):
            est_txt = _estancia_por(conn, "dni=?", (ent["dni"],), min_ymd)
            if est_txt:
                coincidencias.append("dni")
            elif not huesped:
                rp = conn.execute("SELECT dni, nombre, empresa, ruc, celular, visitas, hab_frec, tarifa_frec "
                                  "FROM huespedes_padron WHERE dni=? LIMIT 1", (ent["dni"],)).fetchone()
                if rp:
                    huesped = dict(rp)
        if not est_txt and ent.get("ruc"):
            est_txt = _estancia_por(conn, "ruc=?", (ent["ruc"],), min_ymd)
            if est_txt:
                coincidencias.append("ruc")
            elif not huesped:
                rp = conn.execute("SELECT dni, nombre, empresa, ruc, celular, visitas, hab_frec, tarifa_frec "
                                  "FROM huespedes_padron WHERE ruc=? LIMIT 1", (ent["ruc"],)).fetchone()
                if rp:
                    huesped = dict(rp)
        if not est_txt and ent.get("nombre"):
            partes = [x for x in _sin_tildes(ent["nombre"]).upper().split() if len(x) >= 3][:3]
            if len(partes) >= 2:
                cond = " AND ".join("UPPER(huesped) LIKE ?" for _ in partes)
                est_txt = _estancia_por(conn, cond, tuple(f"%{x}%" for x in partes), min_ymd)
                if est_txt:
                    coincidencias.append("nombre")

        via = None
        estancia = None
        if est_cel and est_txt:
            if est_cel["uuid"] == est_txt["uuid"]:
                via, estancia = "AMBAS", est_txt
                coincidencias.insert(0, "celular")
            else:
                via, estancia = "TEXTO", est_txt  # el texto manda; se informa la otra
        elif est_cel:
            via, estancia = "CELULAR", est_cel
            coincidencias.insert(0, "celular")
        elif est_txt:
            via, estancia = "TEXTO", est_txt
        elif huesped:
            via = "CELULAR" if cel9 else "TEXTO"

        res: Dict[str, Any] = {"via": via, "estancia": estancia, "huesped": huesped, "pagos": None,
                               "comprobante": None, "auditoria_25": None, "coincidencias": coincidencias}
        if est_cel and est_txt and est_cel["uuid"] != est_txt["uuid"]:
            res["estancia_por_celular"] = {"hab": est_cel["hab"], "huesped": est_cel["huesped"]}
        if estancia:
            res["pagos"] = {"total": estancia["pagado"], "saldo": estancia["saldo"], "cuenta_total": estancia["total"]}
            res["comprobante"] = _comprobante_info(conn, estancia)
            if categoria == "COMPROBANTE":
                monto = ent.get("precio_ref") or (ent.get("montos") or [None])[0]
                res["auditoria_25"] = auditar_comprobante(estancia, monto, res["comprobante"])
        return res
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Libres hoy / plantillas
# ---------------------------------------------------------------------------
_libres_cache: Dict[str, Any] = {"ts": 0.0, "datos": []}


def _primer_precio(precio_ref: Any) -> str:
    m = re.search(r"\d+(?:\.\d+)?", str(precio_ref or ""))
    return m.group(0) if m else ""


_libres_lock = threading.Lock()
_libres_refrescando = False


def _calcular_libres() -> List[Dict[str, Any]]:
    import domain_logic as dl
    panel = dl.obtener_datos_panel_local(None)
    grupos: Dict[Tuple[str, str], List[str]] = {}
    for h in panel.get("habitaciones", []):
        if str(h.get("estado") or "").lower() == "libre":
            grupos.setdefault((str(h.get("tipo") or ""), str(h.get("precioRef") or "")), []).append(str(h.get("hab")))
    return [{"tipo": t, "precio": pr, "precio_num": _primer_precio(pr), "habs": sorted(hs), "cantidad": len(hs)}
            for (t, pr), hs in sorted(grupos.items(), key=lambda kv: (float(_primer_precio(kv[0][1]) or 0), kv[0][0]))]


def _refrescar_libres() -> None:
    global _libres_refrescando
    try:
        _libres_cache.update({"ts": _ahora(), "datos": _calcular_libres()})
    except Exception:
        _libres_cache["ts"] = _ahora() - 20  # reintenta pronto
    finally:
        _libres_refrescando = False


def libres_hoy() -> List[Dict[str, Any]]:
    """Habitaciones libres hoy agrupadas por tipo y precio de referencia (panel local).
    Caché de 30 s; al vencer se refresca en segundo plano (el panel tarda ~2 s) y se devuelve el último valor."""
    global _libres_refrescando
    if _libres_cache["ts"]:
        if _ahora() - _libres_cache["ts"] >= 30:
            with _libres_lock:
                if not _libres_refrescando:
                    _libres_refrescando = True
                    threading.Thread(target=_refrescar_libres, name="WspLibres", daemon=True).start()
        return _libres_cache["datos"]
    with _libres_lock:  # primera vez: síncrono
        if not _libres_cache["ts"]:
            _refrescar_libres()
    return _libres_cache["datos"]


def _texto_disponibilidad() -> str:
    libres = libres_hoy()
    if not libres:
        return "Por ahora no tenemos habitaciones libres para hoy. Con gusto le avisamos si se libera alguna. 🙏"
    partes = list(dict.fromkeys(
        f"{g['tipo']} S/ {g['precio_num']}" if g.get("precio_num") else g["tipo"] for g in libres))
    return "Hoy tenemos libres: " + ", ".join(partes) + ". ¿Cuál le interesa y a qué hora llegaría? 😊"


def _plantillas_renderizadas() -> Dict[str, str]:
    out = dict(PLANTILLAS)
    out["DISPONIBILIDAD"] = _texto_disponibilidad()
    return out


def _render_plantilla(nombre: str) -> str:
    if nombre == "DISPONIBILIDAD":
        return _texto_disponibilidad()
    return PLANTILLAS.get(nombre, "")


# ---------------------------------------------------------------------------
# Análisis de chats (pendiente / categoría)
# ---------------------------------------------------------------------------
def _resumen(textos: List[str], largo: int = 90) -> str:
    s = " / ".join(t.strip().replace("\n", " ") for t in textos if t and t.strip())
    return s if len(s) <= largo else s[: largo - 1] + "…"


_PLACEHOLDER_TIPO = {"imagen": "[Imagen]", "image": "[Imagen]", "foto": "[Imagen]", "documento": "[Documento]",
                     "document": "[Documento]", "pdf": "[Documento]", "audio": "[Audio]", "ptt": "[Audio]",
                     "video": "[Video]", "sticker": "[Sticker]"}


def _texto_visible(m: Dict[str, Any]) -> str:
    t = str(m.get("texto") or "").strip()
    return t or _PLACEHOLDER_TIPO.get(str(m.get("tipo") or "").lower(), "")


def _analizar_chat(jid: str, msgs: List[Dict[str, Any]], estado: Dict[str, Any], ahora: float,
                   habs: Set[str]) -> Dict[str, Any]:
    nombre = ""
    for m in reversed(msgs):
        if not m["es_mio"] and (m["chat_nombre"] or m["emisor"]):
            nombre = m["chat_nombre"] or m["emisor"]
            break
    if not nombre:
        nombre = next((m["chat_nombre"] for m in reversed(msgs) if m["chat_nombre"]), "") or _celular9(jid) or jid
    interno = _es_interno(jid, nombre)
    ultimo_ts = msgs[-1]["ts"]
    info: Dict[str, Any] = {"jid": jid, "nombre": nombre, "interno": interno, "msgs": msgs, "ultimo_ts": ultimo_ts,
                            "pendiente": False, "categoria": None, "confianza": 0.0, "espera_min": 0, "primer_ts": 0,
                            "burst": [], "atendido": False, "motivos": []}
    if interno:
        info["categoria"] = "INTERNO"
        return info

    ult_mio = max((m["ts"] for m in msgs if m["es_mio"]), default=0)
    corte = max(ult_mio, int((estado or {}).get("atendido_hasta_ts") or 0))
    pend = [m for m in msgs if not m["es_mio"] and m["ts"] > corte and not es_ruido_mensaje(m["texto"], m["tipo"])]

    if pend:
        burst = pend
        pendiente = True
    else:
        # ráfaga más reciente del cliente (referencia para las pestañas por categoría)
        burst = []
        for m in reversed(msgs):
            if m["es_mio"]:
                if burst:
                    break
                continue
            burst.append(m)
        burst = [m for m in reversed(burst) if not es_ruido_mensaje(m["texto"], m["tipo"])]
        pendiente = False
        if not burst:
            return info

    ids_adj = [m["mid"] for m in burst if str(m["tipo"]).lower() in _TIPOS_ADJUNTO]
    ocr = None
    if ids_adj:
        docs = _leer_ocr(ids_adj)
        if docs:
            ocr = {"texto": " ".join(d["texto"] for d in docs.values()),
                   "monto": next((d["monto"] for d in docs.values() if d["monto"]), "")}
    texto_unido = " / ".join(_texto_visible(m) for m in burst if _texto_visible(m))
    tipo_dom = next((str(m["tipo"]).lower() for m in burst if str(m["tipo"]).lower() in _TIPOS_ADJUNTO), "texto")
    cl = clasificar(texto_unido, tipo_dom, ocr)
    categoria, conf, motivos = cl["categoria"], cl["confianza"], list(cl["motivos"])
    if len(burst) > 1:
        # un último mensaje claramente de dinero/documento (voucher o comprobante) no debe quedar oculto por la ráfaga
        ultimo = burst[-1]
        cl_u = clasificar(_texto_visible(ultimo), str(ultimo["tipo"]).lower(), ocr)
        if (cl_u["categoria"] in ("VOUCHER", "COMPROBANTE") and cl_u["categoria"] != categoria
                and cl_u["puntaje"] >= 4.0):
            categoria, conf, motivos = cl_u["categoria"], cl_u["confianza"], ["ultimo_mensaje"] + cl_u["motivos"]
    if categoria is None:
        ctx = " / ".join(_texto_visible(m) for m in msgs[-6:] if _texto_visible(m))
        cl2 = clasificar(ctx, tipo_dom, ocr)
        if cl2["categoria"]:
            categoria, conf, motivos = cl2["categoria"], round(cl2["confianza"] * 0.6, 2), ["contexto"] + cl2["motivos"]
    manual = str((estado or {}).get("categoria_manual") or "").strip().upper()
    if manual in CATEGORIAS:
        categoria, conf, motivos = manual, 1.0, ["manual"]
    if categoria is None:
        # sin pistas léxicas: si el celular es de un huésped activo -> HABITACION; si no -> RESERVA (prospecto)
        activo = False
        c9 = _celular9(jid)
        if c9:
            conn = get_connection()
            try:
                col = _SQL_CEL.format(c="celular")
                activo = conn.execute(
                    f"SELECT 1 FROM estancias WHERE {col} LIKE ? AND LOWER(COALESCE(estado_estancia,''))='en curso' LIMIT 1",
                    ("%" + c9,)).fetchone() is not None
            except Exception:
                activo = False
            finally:
                conn.close()
        categoria = "HABITACION" if activo else "RESERVA"
        conf, motivos = 0.25, ["sin_pistas:" + ("huesped_activo" if activo else "prospecto")]

    primer_ts = burst[0]["ts"]
    info.update({
        "burst": burst, "categoria": categoria, "confianza": conf, "motivos": motivos, "primer_ts": primer_ts,
        "texto_burst": texto_unido, "pendiente": pendiente and primer_ts >= ahora - VENTANA_ALERTAS_H * 3600,
        "espera_min": max(0, int((ahora - primer_ts) // 60)),
        "atendido": (not pendiente) and corte > 0,
    })
    return info


def _analizar_todos(ventana_s: int) -> List[Dict[str, Any]]:
    ahora = _ahora()
    msgs = _leer_mensajes(int(ahora - ventana_s))
    estados = _cargar_estado_chats()
    habs = _habs_validas()
    por_chat: Dict[str, List[Dict[str, Any]]] = {}
    for m in msgs:
        j = str(m["chat"] or "")
        if not j or j.endswith(_JIDS_IGNORADOS_SUFIJOS) or j == "status@broadcast":
            continue
        por_chat.setdefault(j, []).append(m)
    return [_analizar_chat(j, ms, estados.get(j, {}), ahora, habs) for j, ms in por_chat.items()]


def _nivel(espera_min: int) -> str:
    return "ROJO" if espera_min >= UMBRAL_ROJO_MIN else "AMBAR"


# ---------------------------------------------------------------------------
# Cache de cruce por chat (para el polling liviano)
# ---------------------------------------------------------------------------
_cruce_cache: Dict[Tuple[str, int], Tuple[float, Dict[str, Any], Dict[str, Any]]] = {}


def _cruce_chat(info: Dict[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """-> (entidades, cruce) con caché de 20 s por (jid, primer_ts)."""
    key = (info["jid"], int(info.get("primer_ts") or 0))
    hit = _cruce_cache.get(key)
    if hit and _ahora() - hit[0] < 20:
        return hit[1], hit[2]
    ent = extraer_entidades(info.get("texto_burst", ""), _habs_validas())
    try:
        cr = cruzar(info["jid"], ent, info.get("categoria"))
    except Exception as exc:  # el cruce nunca debe tumbar la bandeja
        cr = {"via": None, "estancia": None, "huesped": None, "pagos": None, "comprobante": None,
              "auditoria_25": None, "error": str(exc)}
    if len(_cruce_cache) > 300:
        _cruce_cache.clear()
    _cruce_cache[key] = (_ahora(), ent, cr)
    return ent, cr


# ---------------------------------------------------------------------------
# API pública: alertas / bandeja
# ---------------------------------------------------------------------------
def auto_adelanto_activo() -> bool:
    conn = get_connection()
    try:
        r = conn.execute("SELECT valor FROM estado_sistema WHERE clave='wsp_auto_adelanto'").fetchone()
        return bool(r and str(r[0]).strip() in ("1", "true", "True", "SI", "si"))
    except Exception:
        return False
    finally:
        conn.close()


def obtener_alertas_wsp() -> Dict[str, Any]:
    """Liviano (polling cada 5 s)."""
    ahora = _ahora()
    chats = _analizar_todos(VENTANA_ALERTAS_H * 3600)
    pend = [c for c in chats if c["pendiente"]]
    pend.sort(key=lambda c: c["primer_ts"])  # el más antiguo primero
    estado = estado_daemon()
    max_esp = max((c["espera_min"] for c in pend), default=0)
    if pend:
        nivel = _nivel(max_esp)
    elif estado != "CONECTADO":
        nivel = "SIN_CONEXION"
    else:
        nivel = "OK"
    top = []
    for c in pend[:2]:
        ent, cr = _cruce_chat(c)
        hab = (ent.get("habs") or [None])[0] or ((cr.get("estancia") or {}).get("hab")) or None
        top.append({"jid": c["jid"], "nombre": c["nombre"], "categoria": c["categoria"],
                    "resumen": _resumen([_texto_visible(m) for m in c["burst"]]), "hab": hab,
                    "espera_min": c["espera_min"], "nivel": _nivel(c["espera_min"])})
    por_cat = {k: 0 for k in CATEGORIAS}
    for c in pend:
        por_cat[c["categoria"]] = por_cat.get(c["categoria"], 0) + 1
    return {"estado_daemon": estado, "pendientes": len(pend), "max_espera_min": max_esp, "nivel": nivel, "top": top,
            "pendientes_por_categoria": por_cat, "modo_prueba": bool(MODO_PRUEBA), "auto_adelanto": auto_adelanto_activo(),
            "ts": int(ahora)}


def _normalizar_pestana(p: Any) -> str:
    s = _sin_tildes(str(p or "PENDIENTES")).upper().strip()
    mapa = {"PENDIENTES": "PENDIENTES", "PENDIENTE": "PENDIENTES", "COMPROBANTE": "COMPROBANTE",
            "COMPROBANTES": "COMPROBANTE", "RESERVA": "RESERVA", "RESERVAS": "RESERVA", "HABITACION": "HABITACION",
            "HABITACIONES": "HABITACION", "VOUCHER": "VOUCHER", "VOUCHERS": "VOUCHER", "INTERNO": "INTERNO",
            "INTERNOS": "INTERNO", "GRUPOS": "INTERNO"}
    return mapa.get(s, "PENDIENTES")


def _acciones(categoria: str, cr: Dict[str, Any], libres: bool = True) -> List[Dict[str, Any]]:
    est = (cr or {}).get("estancia") or {}
    comp = (cr or {}).get("comprobante") or {}
    acc: List[Dict[str, Any]] = []

    def enviar(id_: str, etiqueta: str, plantilla: str) -> None:
        acc.append({"id": id_, "etiqueta": etiqueta, "tipo": "enviar", "plantilla": plantilla,
                    "texto": _render_plantilla(plantilla)})

    if categoria == "RESERVA":
        enviar("ADELANTO_RESERVA", "Preguntar tipo y hora", "ADELANTO_RESERVA")
        enviar("DISPONIBILIDAD", "Enviar disponibilidad de hoy", "DISPONIBILIDAD")
    elif categoria == "COMPROBANTE":
        if est.get("hab"):
            acc.append({"id": "PREFILL", "etiqueta": "Pre-llenar comprobante", "tipo": "prellenar", "plantilla": "",
                        "texto": "", "hab": est["hab"]})
        if comp.get("emitido"):
            acc.append({"id": "REENVIAR_PDF", "etiqueta": "Reenviar PDF", "tipo": "enviar",
                        "plantilla": "COMPROBANTE_EMITIDO",
                        "texto": PLANTILLAS["COMPROBANTE_EMITIDO"].replace("Su comprobante", f"Su comprobante {comp.get('nro')}".strip()),
                        "nro": comp.get("nro")})
        enviar("COMPROBANTE_DATOS", "Pedir RUC/DNI", "COMPROBANTE_DATOS")
        enviar("COMPROBANTE_EN_PROCESO", "Recibido, en proceso", "COMPROBANTE_EN_PROCESO")
    elif categoria == "HABITACION":
        enviar("HAB_RECIBIDO", "Recibido, enseguida", "HAB_RECIBIDO")
    elif categoria == "VOUCHER":
        acc.append({"id": "VER_YAPES", "etiqueta": "Ver en Yapes", "tipo": "ver_yapes", "plantilla": "", "texto": ""})
        enviar("VOUCHER_RECIBIDO", "Pago recibido", "VOUCHER_RECIBIDO")
    return acc


def _tarjeta(info: Dict[str, Any], con_cruce: bool = True) -> Dict[str, Any]:
    msgs = info["msgs"][-MAX_MENSAJES_TARJETA:]
    mensajes = [{"hora": _hora_txt(m["ts"]), "es_mio": bool(m["es_mio"]), "texto": _texto_visible(m),
                 "tipo": str(m["tipo"] or "texto"), "ts": int(m["ts"])} for m in msgs]
    base = {"jid": info["jid"], "nombre": info["nombre"], "celular9": _celular9(info["jid"]),
            "categoria": info["categoria"], "confianza": info["confianza"], "espera_min": info["espera_min"],
            "nivel": _nivel(info["espera_min"]) if info["pendiente"] else "OK", "mensajes": mensajes,
            "pendiente": info["pendiente"], "interno": info["interno"], "atendido": info["atendido"],
            "ultimo_ts": int(info["ultimo_ts"]), "simulado": str(info["jid"]).upper().startswith("SIM-"),
            "motivos": info.get("motivos", []), "entidades": {}, "cruce": None, "acciones": []}
    if info["interno"] or not info.get("burst"):
        return base
    if con_cruce:
        ent, cr = _cruce_chat(info)
        base["entidades"], base["cruce"] = ent, cr
        base["acciones"] = _acciones(info["categoria"], cr)
        if info["categoria"] == "RESERVA":
            base["libres"] = libres_hoy()
    return base


def obtener_bandeja_completa(pestana: str = "PENDIENTES") -> Dict[str, Any]:
    pest = _normalizar_pestana(pestana)
    chats = _analizar_todos(VENTANA_BANDEJA_DIAS * 86400)
    conteos = {"PENDIENTES": 0, "COMPROBANTE": 0, "RESERVA": 0, "HABITACION": 0, "VOUCHER": 0, "INTERNO": 0}
    for c in chats:
        if c["interno"]:
            conteos["INTERNO"] += 1
            continue
        if c["pendiente"]:
            conteos["PENDIENTES"] += 1
        if c["categoria"] in conteos:
            conteos[c["categoria"]] += 1
    if pest == "PENDIENTES":
        sel = sorted((c for c in chats if c["pendiente"]), key=lambda c: c["primer_ts"])
    elif pest == "INTERNO":
        sel = sorted((c for c in chats if c["interno"]), key=lambda c: c["ultimo_ts"], reverse=True)
    else:
        sel = sorted((c for c in chats if not c["interno"] and c["categoria"] == pest),
                     key=lambda c: c["ultimo_ts"], reverse=True)
    tarjetas = [_tarjeta(c) for c in sel[:MAX_TARJETAS]]
    return {"pestana": pest, "tarjetas": tarjetas, "conteos": conteos, "modo_prueba": bool(MODO_PRUEBA),
            "auto_adelanto": auto_adelanto_activo(), "plantillas": _plantillas_renderizadas(),
            "libres_hoy": libres_hoy(), "estado_daemon": estado_daemon()}


def obtener_bandeja_wsp(pestana: str = "PENDIENTES") -> List[Dict[str, Any]]:
    """Contrato del plan: lista de tarjetas (el RPC usa obtener_bandeja_completa)."""
    return obtener_bandeja_completa(pestana)["tarjetas"]


# ---------------------------------------------------------------------------
# Escrituras (solo base local)
# ---------------------------------------------------------------------------
def _upsert_estado_chat(conn: sqlite3.Connection, jid: str, **campos: Any) -> None:
    cols = ["chat_jid"] + list(campos.keys()) + ["actualizado_en"]
    vals = [jid] + list(campos.values())
    marcas = ",".join(["?"] * len(vals)) + ",datetime('now','localtime')"
    sets = ",".join(f"{k}=excluded.{k}" for k in list(campos.keys()) + ["actualizado_en"])
    conn.execute(
        f"INSERT INTO wsp_alertas_estado ({','.join(cols)}) VALUES ({marcas}) "
        f"ON CONFLICT(chat_jid) DO UPDATE SET {sets}",
        vals,
    )


def _insertar_mensaje_sim(conn: sqlite3.Connection, jid: str, nombre: str, texto: str, es_mio: int,
                          tipo: str = "texto") -> str:
    """Inserta un mensaje SIMULADO (jid SIM-) en la base LOCAL. Nunca se usa con jids reales."""
    if not str(jid).upper().startswith("SIM-"):
        raise ValueError("Solo se permiten mensajes simulados (jid con prefijo SIM-)")
    ts = int(_ahora())
    mid = "SIM-" + uuid.uuid4().hex[:16].upper()
    fecha = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    emisor = "Ovalo Mariategui" if es_mio else nombre
    conn.execute(
        """INSERT INTO wsp_recepcion_mensajes
           (id_mensaje, jid, remitente_jid, nombre_emisor, es_mio, timestamp_unix, fecha_hora, tipo_mensaje, texto,
            tiene_archivo, id, chat_jid, chat_nombre, remitente_nombre, desde_recepcion, timestamp, tipo)
           VALUES (?,?,?,?,?,?,?,?,?,0,?,?,?,?,?,?,?)""",
        (mid, jid, "recepcion" if es_mio else jid, emisor, es_mio, ts, fecha, tipo, texto,
         mid, jid, nombre, emisor, es_mio, ts, tipo),
    )
    return mid


def enviar_respuesta(jid: str, texto: str, plantilla: str = "", usuario: str = "") -> Dict[str, Any]:
    """MODO_PRUEBA -> cola LOCAL con estado='SIMULADO'. Operativa -> cola con estado='PENDIENTE' (lo envía wsp_daemon.js)."""
    jid = str(jid or "").strip()
    plantilla = str(plantilla or "").strip()
    texto = str(texto or "").strip()
    if not jid:
        return {"ok": False, "error": "Falta el jid del chat"}
    if jid.endswith("@g.us"):
        return {"ok": False, "error": "No se envían respuestas a grupos desde el visor"}
    if not texto and plantilla:
        texto = _render_plantilla(plantilla)
    if not texto:
        return {"ok": False, "error": "Texto vacío"}
    if len(texto) > 4000:
        return {"ok": False, "error": "Texto demasiado largo (máx. 4000)"}
    _asegurar_tablas()
    simulado = bool(MODO_PRUEBA)
    estado = "SIMULADO" if simulado else "PENDIENTE"
    c9 = _celular9(jid)
    destino = jid if "@" in jid and not jid.upper().startswith("SIM-") else (f"51{c9}" if c9 else jid)
    conn = get_connection()
    try:
        cur = conn.execute(
            """INSERT INTO wsp_recepcion_cola_envio
               (telefono_o_jid, mensaje, texto, destino_telefono, cliente_nombre, estado, detalle_error)
               VALUES (?,?,?,?,?,?,?)""",
            (destino, texto, texto, c9 or destino, str(usuario or ""), estado,
             "Simulado (MODO_PRUEBA): no se envió por WhatsApp" if simulado else None),
        )
        cola_id = cur.lastrowid
        ahora = int(_ahora())
        extra: Dict[str, Any] = {}
        if plantilla == "ADELANTO_RESERVA":
            extra["adelanto_enviado_ts"] = ahora
        if simulado:
            # el eco de WhatsApp nunca volverá en la prueba: se apaga la alerta localmente
            extra["atendido_hasta_ts"] = ahora
            extra["atendido_por"] = (str(usuario or "") + " (simulado)").strip()
            if jid.upper().startswith("SIM-"):
                _insertar_mensaje_sim(conn, jid, "", texto, es_mio=1)
        if extra:
            _upsert_estado_chat(conn, jid, **extra)
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "simulado": simulado, "estado": estado, "id": cola_id, "jid": jid, "texto": texto,
            "plantilla": plantilla,
            "mensaje": "Simulado: no se envió por WhatsApp" if simulado else "En cola: se enviará en unos segundos"}


def marcar_atendido(jid: str, usuario: str = "") -> Dict[str, Any]:
    jid = str(jid or "").strip()
    if not jid:
        return {"ok": False, "error": "Falta el jid del chat"}
    _asegurar_tablas()
    ts = int(_ahora())
    conn = get_connection()
    try:
        _upsert_estado_chat(conn, jid, atendido_hasta_ts=ts, atendido_por=str(usuario or ""))
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "jid": jid, "atendido_hasta_ts": ts, "atendido_por": str(usuario or "")}


def configurar_auto_adelanto(activo: bool, usuario: str = "") -> Dict[str, Any]:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO estado_sistema (clave, valor, updated_at) VALUES ('wsp_auto_adelanto', ?, datetime('now','localtime')) "
            "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor, updated_at=excluded.updated_at",
            ("1" if activo else "0",),
        )
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "activo": bool(activo), "usuario": str(usuario or "")}


def tick_auto_adelanto() -> None:
    """Hilo de fondo (cada 30 s): si el interruptor está activo y un chat RESERVA lleva >= 3 min sin respuesta
    y sin adelanto previo en esa ráfaga -> enviar ADELANTO_RESERVA una sola vez."""
    if not auto_adelanto_activo():
        return
    estados = _cargar_estado_chats()
    for c in _analizar_todos(VENTANA_ALERTAS_H * 3600):
        if not c["pendiente"] or c["categoria"] != "RESERVA" or c["interno"]:
            continue
        if c["espera_min"] < AUTO_ADELANTO_MIN:
            continue
        if int((estados.get(c["jid"]) or {}).get("adelanto_enviado_ts") or 0) >= c["primer_ts"]:
            continue
        try:
            enviar_respuesta(c["jid"], "", "ADELANTO_RESERVA", "AUTO")
        except Exception:
            pass


def simular_mensaje(jid: str, nombre: str, texto: str) -> Dict[str, Any]:
    """SOLO MODO_PRUEBA: inserta un mensaje entrante simulado en la base LOCAL (jid con prefijo SIM-)."""
    if not MODO_PRUEBA:
        return {"ok": False, "error": "simularMensajeWsp solo existe en MODO_PRUEBA"}
    texto = str(texto or "").strip()
    if not texto:
        return {"ok": False, "error": "Texto vacío"}
    jid = str(jid or "SIM-999999999").strip()
    if not jid.upper().startswith("SIM-"):
        jid = "SIM-" + (_solo_digitos(jid) or "999999999")
    nombre = str(nombre or "Cliente Prueba").strip() or "Cliente Prueba"
    _asegurar_tablas()
    conn = get_connection()
    try:
        mid = _insertar_mensaje_sim(conn, jid, nombre, texto, es_mio=0)
        conn.commit()
    finally:
        conn.close()
    cl = clasificar(texto, "texto", None)
    return {"ok": True, "jid": jid, "nombre": nombre, "id_mensaje": mid, "clasificacion": cl,
            "entidades": extraer_entidades(texto, _habs_validas())}


# ---------------------------------------------------------------------------
# Vigilante del daemon (SOLO fuera de MODO_PRUEBA)
# ---------------------------------------------------------------------------
def _daemon_corriendo(script: str = "wsp_daemon.js") -> bool:
    try:
        import psutil  # type: ignore
        for p in psutil.process_iter(["name", "cmdline"]):
            nm = str(p.info.get("name") or "").lower()
            if nm.startswith("node"):
                if any(script in str(a) for a in (p.info.get("cmdline") or [])):
                    return True
        return False
    except ImportError:
        pass
    try:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Get-CimInstance Win32_Process -Filter \"Name='node.exe'\" | Where-Object {{ $_.CommandLine -like '*{script}*' }} | "
             f"Select-Object -First 1 -ExpandProperty ProcessId"],
            capture_output=True, text=True, timeout=20, creationflags=flags)
        return bool(out.stdout.strip())
    except Exception:
        return True  # ante la duda no se lanza un duplicado


def vigilar_daemon() -> Dict[str, Any]:
    """Relanza ``node wsp_daemon.js`` si no hay proceso. NUNCA en MODO_PRUEBA. No relanza si pide QR / sesión cerrada."""
    if MODO_PRUEBA:
        return {"accion": "omitido", "motivo": "MODO_PRUEBA"}
    raw = _estado_daemon_raw().strip().upper()
    if raw == "ESPERANDO_QR" or "LOGGED_OUT" in raw or raw.endswith("_401"):
        return {"accion": "omitido", "motivo": "pide QR / sesión cerrada", "estado": raw}
    carpeta = _BASE_DIR / "whatsapp_mcp_recepcion"
    script = carpeta / "wsp_daemon.js"
    if not script.exists():
        return {"accion": "omitido", "motivo": "no existe wsp_daemon.js"}
    if _daemon_corriendo("wsp_daemon.js"):
        return {"accion": "ninguna", "motivo": "ya corre"}
    node = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
    if not Path(node).exists():
        return {"accion": "omitido", "motivo": "node no encontrado"}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.Popen([node, str(script)], cwd=str(carpeta), creationflags=flags,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    return {"accion": "lanzado", "node": node}
