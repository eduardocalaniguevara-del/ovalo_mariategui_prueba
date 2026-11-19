#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GESTOR CENTRAL DE BÓVEDA LOCAL DE SECRETOS (GRUPO 2 - HOTEL EL ÓVALO MARIÁTEGUI)
Ubicación en repositorio: scripts/secretos_hostal.py

Reglas de seguridad:
- 0 contraseñas, 0 tokens, 0 llaves Fernet y 0 credenciales dentro del código fuente.
- Estándar universal para todas las laptops (Laptop 1, Laptop 2, Laptop 3...):
  Lee prioritariamente desde la bóveda local única C:\\Hostal_Secretos\\ (fuera de Git y fuera de Syncthing),
  con respaldo de compatibilidad hacia nombres heredados (C:\\Hostal_SecretosL2, C:\\Hostal_SecretosL1, C:\\Hostal_Sistema\\config).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_PERFIL_LAPTOP2 = Path(r"C:\Users\ovalo mariategui")
_BOVEDA_COMUN = Path(r"C:\Hostal_Secretos")
_BOVEDA_L1 = Path(r"C:\Hostal_SecretosL1")
_BOVEDA_L2 = Path(r"C:\Hostal_SecretosL2")
_FALLBACK_CONFIG = Path(r"C:\Hostal_Sistema\config")


def es_laptop_2() -> bool:
    """Detecta si se ejecuta en Laptop 2 (Recepción)."""
    return _PERFIL_LAPTOP2.exists()


def _candidatos_boveda() -> List[Path]:
    """Retorna la lista ordenada de carpetas candidatas de bóveda (estándar universal C:\\Hostal_Secretos primero)."""
    candidatos: List[Path] = []
    env_dir = os.environ.get("HOSTAL_BOVEDA_DIR", "").strip()
    if env_dir:
        candidatos.append(Path(env_dir))

    if es_laptop_2():
        orden = [_BOVEDA_COMUN, _BOVEDA_L2, _BOVEDA_L1, _FALLBACK_CONFIG]
    else:
        orden = [_BOVEDA_COMUN, _BOVEDA_L1, _BOVEDA_L2, _FALLBACK_CONFIG]

    for ruta in orden:
        if ruta not in candidatos:
            candidatos.append(ruta)
    return candidatos


def obtener_dir_boveda() -> Path:
    """
    Auto-detecta y retorna el directorio Path de la bóveda local activa.
    Si aún no existe ninguna carpeta, devuelve la ruta preferida para esta laptop.
    """
    candidatos = _candidatos_boveda()
    for ruta in candidatos:
        if ruta.exists() and ruta.is_dir():
            return ruta
    return candidatos[0]


def _buscar_archivo_boveda(nombre_archivo: str) -> Optional[Path]:
    """Busca un archivo dentro de los directorios candidatos de bóveda en orden de prioridad."""
    for carpeta in _candidatos_boveda():
        candidato = carpeta / nombre_archivo
        if candidato.exists() and candidato.is_file():
            return candidato
    return None


def _leer_json_seguro(ruta: Path) -> Dict:
    with ruta.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"El archivo de bóveda {ruta.name} no contiene un objeto JSON válido.")
    return data


def obtener_ruta_credentials_google() -> str:
    """
    Retorna la ruta absoluta (str) hacia credentials.json de Google Service Account
    siguiendo el orden de prioridad de bóveda (con fallback a C:\\Hostal_Sistema\\config\\credentials.json).
    """
    encontrado = _buscar_archivo_boveda("credentials.json")
    if encontrado is not None:
        return str(encontrado)
    return str(_FALLBACK_CONFIG / "credentials.json")


def obtener_sunat_hostal(requerido: bool = True) -> Dict[str, str]:
    """
    Carga sunat_hostal.json desde la bóveda local del Hotel Grupo 2.
    Retorna dict con llaves: ruc, usuario_sol, clave_sol, apisperu_token.
    """
    ruta = _buscar_archivo_boveda("sunat_hostal.json")
    if ruta is None:
        if requerido:
            raise FileNotFoundError(
                f"No se encontró sunat_hostal.json en la bóveda local ({_BOVEDA_COMUN}). "
                f"Cree el archivo usando secretos_ejemplo.json como plantilla."
            )
        return {
            "ruc": "",
            "usuario_sol": "",
            "clave_sol": "",
            "apisperu_token": "",
        }

    data = _leer_json_seguro(ruta)
    ruc = str(data.get("ruc") or "").strip()
    usuario_sol = str(data.get("usuario_sol") or data.get("usuario") or "").strip()
    clave_sol = str(data.get("clave_sol") or data.get("clave") or "").strip()
    apisperu_token = str(data.get("apisperu_token") or "").strip()

    if requerido and not (ruc and usuario_sol and clave_sol):
        raise ValueError(
            f"sunat_hostal.json en {ruta.parent} está incompleto (requiere ruc, usuario_sol y clave_sol)."
        )

    return {
        "ruc": ruc,
        "usuario_sol": usuario_sol,
        "clave_sol": clave_sol,
        "apisperu_token": apisperu_token,
    }


def obtener_credenciales_sunat() -> Tuple[str, str, str]:
    """Helper conveniente para scripts SUNAT: retorna tupla (ruc, usuario_sol, clave_sol)."""
    datos = obtener_sunat_hostal(requerido=True)
    return datos["ruc"], datos["usuario_sol"], datos["clave_sol"]


def obtener_apisperu_token() -> str:
    """
    Retorna el token APIsPERU desde sunat_hostal.json en bóveda.
    Si faltara la bóveda o el campo, devuelve cadena vacía '' sin lanzar excepción.
    """
    try:
        datos = obtener_sunat_hostal(requerido=False)
        return str(datos.get("apisperu_token") or "").strip()
    except Exception:
        return ""


def obtener_supabase_hostal(requerido: bool = False) -> Dict[str, str]:
    """
    Carga supabase_hostal.json desde la bóveda local del proyecto hostal-ovalo.
    Retorna dict con llaves: url, anon_key, sync_token.
    """
    ruta = _buscar_archivo_boveda("supabase_hostal.json")
    if ruta is None:
        if requerido:
            raise FileNotFoundError(
                f"No se encontró supabase_hostal.json en la bóveda local ({_BOVEDA_COMUN})."
            )
        return {
            "url": "",
            "anon_key": "",
            "sync_token": "",
        }

    data = _leer_json_seguro(ruta)
    url = str(data.get("url") or "").strip()
    anon_key = str(data.get("anon_key") or "").strip()
    sync_token = str(data.get("sync_token") or "").strip()

    if requerido and not (url and anon_key and sync_token):
        raise ValueError(
            f"supabase_hostal.json en {ruta.parent} está incompleto (requiere url, anon_key y sync_token)."
        )

    return {
        "url": url,
        "anon_key": anon_key,
        "sync_token": sync_token,
    }
