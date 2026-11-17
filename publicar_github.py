#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Publicador automático a GitHub usando la credencial nativa de Windows Credential Manager
(GitHub Desktop) sin exponer tokens en archivos ni pedir contraseñas.
"""

import ctypes
import ctypes.wintypes as wt
import json
import re
import shutil
import subprocess
import sys
import urllib.request
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
CRED_TARGETS = [
    ("eduardocalaniguevara-del", "GitHub - https://api.github.com/eduardocalaniguevara-del"),
    ("hostalovalo-design", "GitHub - https://api.github.com/hostalovalo-design"),
    ("hostalovalo-design", "git:https://github.com"),
]


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


def _extraer_token_limpio(blob: str) -> str:
    m = re.search(r"(gh[opusr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|[a-f0-9]{40})", blob or "")
    if m:
        return m.group(1)
    return (blob or "").strip()


def _obtener_credencial_windows() -> tuple[str, str]:
    advapi32 = ctypes.windll.advapi32
    for default_user, target in CRED_TARGETS:
        pcred = ctypes.POINTER(_CREDENTIAL)()
        if advapi32.CredReadW(target, 1, 0, ctypes.byref(pcred)):
            cred = pcred.contents
            blob = (
                ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
                .decode("utf-8", errors="ignore")
                .strip("\x00")
            )
            uname = (cred.UserName or default_user).strip()
            if uname in ("PersonalAccessToken", "oauth2", ""):
                uname = default_user
            advapi32.CredFree(pcred)
            tok = _extraer_token_limpio(blob)
            if tok:
                return uname, tok
    return "", ""


def _aceptar_invitaciones_pendientes(token: str) -> None:
    if not token:
        return
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "OvaloPruebaUpdater",
    }
    try:
        req = urllib.request.Request("https://api.github.com/user/repository_invitations", headers=headers)
        with urllib.request.urlopen(req, timeout=10) as r:
            invs = json.loads(r.read().decode("utf-8"))
        for inv in invs or []:
            iid = inv.get("id")
            if iid:
                req_p = urllib.request.Request(
                    f"https://api.github.com/user/repository_invitations/{iid}",
                    headers=headers,
                    method="PATCH",
                )
                try:
                    urllib.request.urlopen(req_p, timeout=10).close()
                except Exception:
                    pass
    except Exception:
        pass


def _obtener_token_windows() -> str:
    _, token = _obtener_credencial_windows()
    if token:
        return token
    raise RuntimeError("No se encontró la sesión de GitHub en Windows Credential Manager.")


def _asegurar_identidad_git() -> None:
    r_mail = subprocess.run([GIT_EXE, "-C", str(BASE_DIR), "config", "user.email"], capture_output=True, text=True)
    if not (r_mail.stdout or "").strip():
        subprocess.run([GIT_EXE, "-C", str(BASE_DIR), "config", "user.email", "hostal.ovalo@gmail.com"], check=False)
    r_name = subprocess.run([GIT_EXE, "-C", str(BASE_DIR), "config", "user.name"], capture_output=True, text=True)
    if not (r_name.stdout or "").strip():
        subprocess.run([GIT_EXE, "-C", str(BASE_DIR), "config", "user.name", "Hostal Ovalo Recepcion"], check=False)


def publicar(mensaje: str = "Actualizacion automatica del sistema") -> None:
    gh_user, token = _obtener_credencial_windows()
    _aceptar_invitaciones_pendientes(token)
    public_url = f"https://{REPO_SLUG}"
    push_url = f"https://x-access-token:{token}@{REPO_SLUG}" if token else public_url

    git_dir = BASE_DIR / ".git"
    if not git_dir.exists():
        subprocess.run([GIT_EXE, "-C", str(BASE_DIR), "init"], check=False)
        subprocess.run([GIT_EXE, "-C", str(BASE_DIR), "branch", "-M", "main"], check=False)
        subprocess.run([GIT_EXE, "-C", str(BASE_DIR), "remote", "add", "origin", public_url], check=False)
    else:
        subprocess.run([GIT_EXE, "-C", str(BASE_DIR), "remote", "set-url", "origin", public_url], check=False)

    _asegurar_identidad_git()
    subprocess.run([GIT_EXE, "-C", str(BASE_DIR), "add", "-A"], check=False)
    subprocess.run([GIT_EXE, "-C", str(BASE_DIR), "commit", "-m", mensaje], check=False)

    # Integrar cambios previos de la otra laptop antes de empujar
    fetch_res = subprocess.run(
        [GIT_EXE, "-C", str(BASE_DIR), "fetch", push_url, "main"],
        capture_output=True,
        text=True,
    )
    if fetch_res.returncode == 0:
        # Merge real (nunca rebase -X ours, que en un rebase "ours" es el remoto y pisaría lo local)
        ya_incluido = subprocess.run(
            [GIT_EXE, "-C", str(BASE_DIR), "merge-base", "--is-ancestor", "FETCH_HEAD", "HEAD"],
            capture_output=True,
            text=True,
        ).returncode == 0
        if not ya_incluido:
            mg = subprocess.run(
                [GIT_EXE, "-C", str(BASE_DIR), "merge", "--no-edit", "-X", "ours", "FETCH_HEAD"],
                capture_output=True,
                text=True,
            )
            if mg.returncode != 0:
                subprocess.run([GIT_EXE, "-C", str(BASE_DIR), "merge", "--abort"], check=False)
                raise RuntimeError("Fusión con origin/main falló (conflictos); resolver a mano antes de publicar.\n" + (mg.stdout or "")[-400:])

    r = subprocess.run(
        [GIT_EXE, "-C", str(BASE_DIR), "push", "-u", push_url, "main"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        err_msg = r.stderr.replace(token, "***") if token else r.stderr
        raise RuntimeError(err_msg)
    sha = subprocess.check_output(
        [GIT_EXE, "-C", str(BASE_DIR), "rev-parse", "--short", "HEAD"],
        text=True,
    ).strip()
    print(f"[OK] Publicado en GitHub ({REPO_SLUG}) exitosamente -> Commit: {sha}")


if __name__ == "__main__":
    msg = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Actualizacion automatica del sistema"
    publicar(msg)
