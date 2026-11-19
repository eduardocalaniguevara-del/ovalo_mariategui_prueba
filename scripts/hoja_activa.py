#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SELECTOR CENTRAL DE HOJA DE GOOGLE SHEETS Y ENTORNO CLI (OPERATIVA vs PRUEBA)
Hotel El Óvalo Mariátegui — 100% Auto-Relativo (scripts/)

Detecta automáticamente BASE_APP_DIR = Path(__file__).resolve().parent.parent:
  - Lee BASE_APP_DIR / "data" / "hotel_perfil.json" (respetando OVALO_ENTORNO si está definido).
  - Usa BASE_APP_DIR / "data" / "ovalo_sistema.db" en obtener_sqlite_path().
  - Mantiene candados duros anti-ID operativo y anti-Terrazas.
"""

import json
import os
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
BASE_APP_DIR = SCRIPTS_DIR.parent
DATA_DIR = BASE_APP_DIR / "data"
PERFIL_HOTEL_PATH = DATA_DIR / "hotel_perfil.json"

RUTA_CONFIG_ENTORNOS = r"C:\Hostal_Sistema\config\entornos_hojas.json"
ID_OPERATIVA_DEFAULT = "1KudqxM1D6MSCxT5Zb2_vAOTGxnw9Ja92JuFjFpDuxZ8"
ID_PRUEBA_DEFAULT = "1VXg1d8QQgS2te1NWlSFbK6Y4fqLV5B-kNwJ-mezRoqE"
ID_TERRAZAS_PROHIBIDO = "1vgjOLxc-eh1zw42uC3pxnM86Lo2jZkz8_uwrEFhw8ss"


def _cargar_perfil_hotel() -> dict:
    """Lee BASE_APP_DIR / 'data' / 'hotel_perfil.json' de forma segura."""
    if not PERFIL_HOTEL_PATH.exists():
        return {}
    try:
        datos = json.loads(PERFIL_HOTEL_PATH.read_text(encoding="utf-8-sig"))
        return datos if isinstance(datos, dict) else {}
    except Exception:
        return {}


def _es_carpeta_prueba() -> bool:
    ruta_low = str(BASE_APP_DIR).lower()
    return ("prueba" in ruta_low) and ("terrazas" not in ruta_low)


def entorno() -> str:
    raw = os.environ.get("OVALO_ENTORNO", "").strip().lower()
    if not raw:
        perfil = _cargar_perfil_hotel()
        if perfil.get("modo_prueba") is True:
            raw = "prueba"
        elif _es_carpeta_prueba():
            raw = "prueba"
        else:
            cwd_low = os.getcwd().lower()
            if "ovalo_mariategui_prueba" in cwd_low or os.environ.get("OVALO_MODO_PRUEBA", "").strip().lower() in ("1", "true", "yes"):
                raw = "prueba"
            else:
                raw = "operativa"
    if raw not in ("operativa", "prueba"):
        raise SystemExit(
            f"⛔ ERROR CRÍTICO DE ENTORNO: OVALO_ENTORNO={raw!r} no es válido. "
            "Valores permitidos: 'operativa' o 'prueba'."
        )
    return raw


def es_prueba() -> bool:
    return entorno() == "prueba"


MODO_PRUEBA = es_prueba()


def obtener_spreadsheet_id(silencioso: bool = False) -> str:
    ent = entorno()
    perfil = _cargar_perfil_hotel()

    cfg = {}
    cfg_ok = False
    if os.path.exists(RUTA_CONFIG_ENTORNOS):
        try:
            with open(RUTA_CONFIG_ENTORNOS, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if isinstance(cfg, dict):
                cfg_ok = True
        except Exception as e:
            if not silencioso:
                print(f"[WARN hoja_activa] Error leyendo {RUTA_CONFIG_ENTORNOS}: {e}", file=sys.stderr)

    sid_perfil = str(perfil.get("spreadsheet_id") or "").strip()
    if sid_perfil in ("", "SIN_HOJA_MODO_PRUEBA"):
        sid_perfil = ""

    sid = ""
    if ent == "prueba":
        # Prioridad 1: hotel_perfil.json local (cuando está en modo_prueba=True)
        if perfil.get("modo_prueba") is True and sid_perfil:
            sid = sid_perfil
        # Prioridad 2: entornos_hojas.json (si existe)
        elif cfg_ok and str(cfg.get("prueba", "")).strip():
            sid = str(cfg.get("prueba", "")).strip()
        # Prioridad 3: si estamos en carpeta prueba sin perfil/entornos, abortar por seguridad
        else:
            raise SystemExit(
                "⛔ ERROR CRÍTICO DE SEGURIDAD: No se pudo determinar el Spreadsheet ID de PRUEBA "
                f"(ni en {PERFIL_HOTEL_PATH} ni en {RUTA_CONFIG_ENTORNOS}). "
                "Abortado para no tocar la Hoja Operativa."
            )
    else:
        # Entorno operativa
        if (not perfil.get("modo_prueba", False)) and sid_perfil and sid_perfil != ID_PRUEBA_DEFAULT:
            sid = sid_perfil
        elif cfg_ok and str(cfg.get("operativa", "")).strip():
            sid = str(cfg.get("operativa", "")).strip()
        else:
            if not silencioso and not cfg_ok:
                print(
                    f"[WARN hoja_activa] Usando Hoja Operativa por defecto ({ID_OPERATIVA_DEFAULT[:12]}...).",
                    file=sys.stderr,
                )
            sid = ID_OPERATIVA_DEFAULT

    ids_terrazas = {
        ID_TERRAZAS_PROHIBIDO,
        str(cfg.get("terrazas_NO_TOCAR", "")).strip(),
    } - {""}
    if sid in ids_terrazas:
        raise SystemExit(
            f"⛔ ERROR CRÍTICO DE SEGURIDAD: El Spreadsheet ID ({sid}) corresponde a Hotel Terrazas (PROHIBIDO). Abortado."
        )

    ids_prohibidos_en_prueba = {
        ID_OPERATIVA_DEFAULT,
        ID_TERRAZAS_PROHIBIDO,
        str(cfg.get("operativa", "")).strip(),
        str(cfg.get("terrazas_NO_TOCAR", "")).strip(),
        "SIN_HOJA_MODO_PRUEBA",
    } - {""}

    if ent == "prueba" and sid in ids_prohibidos_en_prueba:
        raise SystemExit(
            f"⛔ ERROR CRÍTICO DE SEGURIDAD: En modo PRUEBA el Spreadsheet ID ({sid}) coincide con una hoja OPERATIVA o Terrazas. Abortado."
        )

    if not silencioso:
        print(f"[ENTORNO HOJA] {ent.upper()} -> {sid[:12]}...", file=sys.stderr)
    return sid


def obtener_sqlite_path() -> str:
    """Devuelve la ruta de la base de datos SQLite activa (auto-relativa a BASE_APP_DIR)."""
    local_db = BASE_APP_DIR / "data" / "ovalo_sistema.db"
    local_legacy = BASE_APP_DIR / "data" / "ovalo_local.db"

    if es_prueba():
        if _es_carpeta_prueba() or local_db.exists():
            return str(local_db)
        return r"C:\ovalo_mariategui_prueba\data\ovalo_sistema.db"

    # Entorno operativa
    if not _es_carpeta_prueba():
        if local_db.exists():
            return str(local_db)
        if local_legacy.exists():
            return str(local_legacy)
        return str(local_db)

    # Si alguien forzó OVALO_ENTORNO=operativa invocando desde carpeta de prueba
    ruta_op = Path(r"C:\Recepcion_Ovalo_Local\data\ovalo_sistema.db")
    if ruta_op.exists():
        return str(ruta_op)
    return str(local_db)


def conectar_sqlite_activa(timeout: float = 30.0):
    """Abre una conexión SQLite segura con WAL y busy_timeout=20000 hacia la BD activa."""
    import sqlite3
    db_path = obtener_sqlite_path()
    if not os.path.exists(db_path):
        return None
    conn = sqlite3.connect(db_path, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=20000;")
    return conn


def limpiar_outbox_caches_locales(conn) -> None:
    """Elimina de _supabase_outbox las tablas de caché solo-local para no ensuciar la cola de Supabase."""
    try:
        has_outbox = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='_supabase_outbox'"
        ).fetchone()
        if has_outbox:
            conn.execute(
                "DELETE FROM _supabase_outbox WHERE tabla IN ('sunat_lineas_cache', 'calzado_cache', 'yape_conciliacion_hostal')"
            )
    except Exception:
        pass


if __name__ == "__main__":
    sid_actual = obtener_spreadsheet_id(silencioso=False)
    print(
        json.dumps(
            {
                "entorno": entorno(),
                "es_prueba": es_prueba(),
                "base_app_dir": str(BASE_APP_DIR),
                "scripts_dir": str(SCRIPTS_DIR),
                "spreadsheet_id": sid_actual,
                "sqlite_path": obtener_sqlite_path(),
            },
            ensure_ascii=False,
        )
    )
