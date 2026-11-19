#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VERIFICADOR DE BÓVEDA LOCAL DE SECRETOS (HOTEL EL ÓVALO MARIÁTEGUI - GRUPO 2)
Comprueba que todos los JSON y campos requeridos existan y estén poblados,
sin imprimir jamás contraseñas, llaves ni tokens en pantalla o logs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from secretos_hostal import (  # noqa: E402
    es_laptop_2,
    obtener_apisperu_token,
    obtener_dir_boveda,
    obtener_ruta_credentials_google,
    obtener_sunat_hostal,
    obtener_supabase_hostal,
)

try:
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr is not None:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ARCHIVOS_PERMITIDOS = {"credentials.json", "sunat_hostal.json", "supabase_hostal.json"}
RUC_HOSTAL_ESPERADO = "20611986484"


def verificar_boveda() -> int:
    errores = []
    boveda = obtener_dir_boveda()
    print(f"[BOVEDA] Equipo detectado : {'Laptop 2 (Recepcion)' if es_laptop_2() else 'Laptop 1 (Admin)'}")
    print(f"[BOVEDA] Directorio activo: {boveda}")

    if not boveda.exists():
        print(f"[ERROR] El directorio de bóveda no existe: {boveda}")
        return 1

    # 1. Verificar limpieza hermética (solo archivos esperados del Hotel Grupo 2)
    archivos_presentes = {p.name for p in boveda.iterdir() if p.is_file()}
    extra = sorted(archivos_presentes - ARCHIVOS_PERMITIDOS)
    if extra:
        errores.append(f"Archivos no permitidos en bóveda (solo Grupo 2): {extra}")
    else:
        print(f"[OK] Aislamiento Grupo 2 verificado ({len(archivos_presentes)} archivos permitidos).")

    # 2. Verificar credentials.json (Google Service Account)
    ruta_g = Path(obtener_ruta_credentials_google())
    if not ruta_g.exists():
        errores.append(f"Falta credentials.json en {ruta_g}")
    else:
        try:
            gdata = json.loads(ruta_g.read_text(encoding="utf-8-sig"))
            campos_g = ("type", "project_id", "client_email", "private_key")
            faltan_g = [c for c in campos_g if not str(gdata.get(c) or "").strip()]
            if faltan_g:
                errores.append(f"credentials.json incompleto, faltan campos: {faltan_g}")
            else:
                print(
                    f"[OK] credentials.json válido ({ruta_g.name}, project_id={gdata.get('project_id')}, "
                    f"campos={len(campos_g)}/4 OK)"
                )
        except Exception as exc:
            errores.append(f"Error leyendo credentials.json: {exc}")

    # 3. Verificar sunat_hostal.json
    try:
        sunat = obtener_sunat_hostal(requerido=True)
        for campo in ("ruc", "usuario_sol", "clave_sol", "apisperu_token"):
            if not str(sunat.get(campo) or "").strip():
                errores.append(f"sunat_hostal.json: falta campo '{campo}'")
        if sunat.get("ruc") != RUC_HOSTAL_ESPERADO:
            errores.append(
                f"sunat_hostal.json: RUC '{sunat.get('ruc')}' no coincide con Hotel Grupo 2 ({RUC_HOSTAL_ESPERADO})"
            )
        token_ok = bool(obtener_apisperu_token())
        if not token_ok:
            errores.append("obtener_apisperu_token() devolvió cadena vacía")
        if not any("sunat_hostal.json" in e for e in errores):
            print(
                f"[OK] sunat_hostal.json válido (ruc={sunat['ruc']}, "
                f"usuario_len={len(sunat['usuario_sol'])}, "
                f"clave_len={len(sunat['clave_sol'])}, "
                f"apisperu_len={len(sunat['apisperu_token'])})"
            )
    except Exception as exc:
        errores.append(f"sunat_hostal.json inválido: {exc}")

    # 4. Verificar supabase_hostal.json
    try:
        sb = obtener_supabase_hostal(requerido=True)
        for campo in ("url", "anon_key", "sync_token"):
            if not str(sb.get(campo) or "").strip():
                errores.append(f"supabase_hostal.json: falta campo '{campo}'")
        if "fmlfqupwscymosvwnnzx" not in sb.get("url", ""):
            errores.append("supabase_hostal.json: URL no apunta al proyecto hostal-ovalo (fmlfqupwscymosvwnnzx)")
        if not any("supabase_hostal.json" in e for e in errores):
            print(
                f"[OK] supabase_hostal.json válido (url={sb['url']}, "
                f"anon_key_len={len(sb['anon_key'])}, "
                f"sync_token_len={len(sb['sync_token'])})"
            )
    except Exception as exc:
        errores.append(f"supabase_hostal.json inválido: {exc}")

    if errores:
        print("\n[FAIL] Se encontraron problemas en la bóveda:")
        for err in errores:
            print(f"  - {err}")
        return 1

    print("\n[SUCCESS] Bóveda local verificada al 100% (0 secretos impresos).")
    return 0


if __name__ == "__main__":
    sys.exit(verificar_boveda())
