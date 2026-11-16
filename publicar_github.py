#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Publicador automático a GitHub usando la credencial nativa de Windows Credential Manager
(GitHub Desktop) sin exponer tokens en archivos ni pedir contraseñas.
"""

import ctypes
import ctypes.wintypes as wt
import shutil
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
GIT_EXE = shutil.which("git") or r"C:\Hostal_Sistema\tools\mingit\cmd\git.exe"
if not Path(GIT_EXE).exists() and GIT_EXE != "git":
    for _cand in (r"C:\Program Files\Git\cmd\git.exe", r"C:\Program Files (x86)\Git\cmd\git.exe"):
        if Path(_cand).exists():
            GIT_EXE = _cand
            break

ES_PRUEBA = "prueba" in str(BASE_DIR).lower() or (BASE_DIR / "data" / "modo_prueba.flag").exists()
REPO_SLUG = (
    "github.com/eduardocalaniguevara-del/ovalo_mariategui_prueba.git"
    if ES_PRUEBA
    else "github.com/eduardocalaniguevara-del/app_local_recepcion.git"
)
CRED_TARGET = "GitHub - https://api.github.com/eduardocalaniguevara-del"


class _CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wt.DWORD),
        ("Type", wt.DWORD),
        ("TargetName", wt.LPWSTR),
        ("Comment", wt.LPWSTR),
        ("LastWritten", wt.FILETIME),
        ("CredentialBlobSize", wt.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
        ("Persist", wt.DWORD),
        ("AttributeCount", wt.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wt.LPWSTR),
        ("UserName", wt.LPWSTR),
    ]


def _obtener_token_windows() -> str:
    advapi32 = ctypes.windll.advapi32
    pcred = ctypes.POINTER(_CREDENTIAL)()
    if advapi32.CredReadW(CRED_TARGET, 1, 0, ctypes.byref(pcred)):
        cred = pcred.contents
        blob = (
            ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
            .decode("utf-8", errors="ignore")
            .strip("\x00")
        )
        advapi32.CredFree(pcred)
        return blob
    raise RuntimeError("No se encontró la sesión de GitHub Desktop en Windows Credential Manager.")


def publicar(mensaje: str = "Actualizacion automatica del sistema") -> None:
    token = _obtener_token_windows()
    push_url = f"https://eduardocalaniguevara-del:{token}@{REPO_SLUG}"
    public_url = f"https://{REPO_SLUG}"

    git_dir = BASE_DIR / ".git"
    if not git_dir.exists():
        subprocess.run([GIT_EXE, "-C", str(BASE_DIR), "init"], check=False)
        subprocess.run([GIT_EXE, "-C", str(BASE_DIR), "branch", "-M", "main"], check=False)
        subprocess.run([GIT_EXE, "-C", str(BASE_DIR), "remote", "add", "origin", public_url], check=False)
    else:
        subprocess.run([GIT_EXE, "-C", str(BASE_DIR), "remote", "set-url", "origin", public_url], check=False)

    subprocess.run([GIT_EXE, "-C", str(BASE_DIR), "add", "-A"], check=False)
    subprocess.run([GIT_EXE, "-C", str(BASE_DIR), "commit", "-m", mensaje], check=False)

    # Integrar cambios previos de la otra laptop antes de empujar
    fetch_res = subprocess.run(
        [GIT_EXE, "-C", str(BASE_DIR), "fetch", push_url, "main"],
        capture_output=True,
        text=True,
    )
    if fetch_res.returncode == 0:
        subprocess.run(
            [GIT_EXE, "-C", str(BASE_DIR), "rebase", "-X", "ours", "FETCH_HEAD"],
            capture_output=True,
            text=True,
        )

    r = subprocess.run(
        [GIT_EXE, "-C", str(BASE_DIR), "push", "-u", push_url, "main"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.replace(token, "***"))
    sha = subprocess.check_output(
        [GIT_EXE, "-C", str(BASE_DIR), "rev-parse", "--short", "HEAD"],
        text=True,
    ).strip()
    print(f"[OK] Publicado en GitHub ({REPO_SLUG}) exitosamente -> Commit: {sha}")


if __name__ == "__main__":
    msg = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Actualizacion automatica del sistema"
    publicar(msg)
