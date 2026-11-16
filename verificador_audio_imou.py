#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Verificador Dual de Audio (Micrófono Laptop ↔ Micrófono Cámara Imou RTSP)
-------------------------------------------------------------------------
- Compensa el micrófono débil de la laptop contrastándolo con el audio AAC 16 kHz
  de la cámara Imou (`IPC-C22FN-C`).
- Sincroniza automáticamente cualquier desfase de reloj (±5 s) mediante correlación
  cruzada de la envolvente de energía RMS (ventanas de 50 ms).
- Ejecuta `.venv_whisper` sobre el audio de la cámara Imou en sesiones candidatas
  (`1-ALTA`, `2-MEDIA`, montos > 0 o DNI) y elige la transcripción de mayor confianza
  (`avg_logprob` y `1 - no_speech_prob`).
- Emite veredicto auditable:
    * `CONFIRMADA_IMOU`       : El audio de Imou confirma el cobro/alerta.
    * `CORREGIDA_POR_IMOU`    : El audio de Imou corrige habitación/monto y cuadra en sistema.
    * `DESCARTADA_POR_IMOU`   : El audio limpio de Imou demuestra que fue ruido/alucinación en la laptop.
    * `AUDIO_LAPTOP_ + FOTO`  : Evento previo al búfer de audio Imou; evaluado por SNR acústico + captura HD.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import sqlite3
import subprocess
import sys
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import camara_imou as ci  # noqa: E402
import lenguaje_recepcion as lr  # noqa: E402
from config import DATA_DIR, DB_PATH  # noqa: E402

WHISPER_PY = BASE_DIR / ".venv_whisper" / "Scripts" / "python.exe"
WHISPER_WORKER = BASE_DIR / "whisper_local_worker.py"
VERIF_CACHE_DIR = DATA_DIR / "camara_imou" / "verificador_cache"
VERIF_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def asegurar_columnas_verificador() -> None:
    """Agrega columnas del verificador a `auditoria_audio_noche` (tabla propia del auditor, nunca tablas operativas)."""
    cols_nuevas = [
        ("transcripcion_imou", "TEXT DEFAULT ''"),
        ("confianza_laptop", "REAL DEFAULT 0.0"),
        ("confianza_imou", "REAL DEFAULT 0.0"),
        ("fuente_preferida", "TEXT DEFAULT 'LAPTOP'"),
        ("veredicto_verificador", "TEXT DEFAULT ''"),
    ]
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    try:
        existentes = {r[1] for r in conn.execute("PRAGMA table_info(auditoria_audio_noche)").fetchall()}
        for col, tipo in cols_nuevas:
            if col not in existentes:
                conn.execute(f"ALTER TABLE auditoria_audio_noche ADD COLUMN {col} {tipo}")
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def metricas_acusticas_wav(ruta_wav: Path) -> Dict[str, float]:
    """Calcula RMS, pico y relación Señal/Ruido (SNR en dB) en la banda vocal (95–7400 Hz)."""
    import numpy as np

    if not ruta_wav or not ruta_wav.exists():
        return {"rms": 0.0, "pico": 0.0, "snr_db": 0.0, "duracion_s": 0.0}
    try:
        with wave.open(str(ruta_wav), "rb") as wf:
            sr = wf.getframerate() or 16000
            n_f = wf.getnframes()
            raw = wf.readframes(n_f)
        arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
        if len(arr) == 0:
            return {"rms": 0.0, "pico": 0.0, "snr_db": 0.0, "duracion_s": 0.0}
        rms = float(np.sqrt(np.mean(arr ** 2)))
        pico = float(np.max(np.abs(arr)))
        # Dividir en ventanas de 50 ms para estimar piso de ruido (percentil 15) vs voz activa (percentil 90)
        win = max(1, int(0.05 * sr))
        n_wins = len(arr) // win
        if n_wins >= 4:
            frames = arr[: n_wins * win].reshape(n_wins, win)
            env = np.sqrt(np.mean(frames ** 2, axis=1)) + 1.0
            p_ruido = float(np.percentile(env, 15))
            p_voz = float(np.percentile(env, 90))
            snr_db = round(20.0 * math.log10(max(1.0, p_voz / max(1.0, p_ruido))), 1)
        else:
            snr_db = 0.0
        return {
            "rms": round(rms, 1),
            "pico": round(pico, 0),
            "snr_db": snr_db,
            "duracion_s": round(len(arr) / float(sr), 1),
        }
    except Exception:
        return {"rms": 0.0, "pico": 0.0, "snr_db": 0.0, "duracion_s": 0.0}


def estimar_desfase_reloj_seg(wav_laptop: Path, wav_imou: Path, max_desfase_s: float = 5.0) -> float:
    """
    Estima el desfase en segundos entre `wav_laptop` y `wav_imou` usando correlación cruzada
    de la envolvente RMS (paso de 50 ms = 20 Hz).
    """
    import numpy as np

    try:
        def _env(p: Path):
            with wave.open(str(p), "rb") as wf:
                sr = wf.getframerate() or 16000
                arr = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32)
            win = max(1, int(0.05 * sr))
            n_w = len(arr) // win
            if n_w < 10:
                return None
            e = np.sqrt(np.mean(arr[: n_w * win].reshape(n_w, win) ** 2, axis=1))
            e = e - np.mean(e)
            norm = float(np.linalg.norm(e))
            return (e / norm) if norm > 1e-6 else None

        e_lap = _env(wav_laptop)
        e_imou = _env(wav_imou)
        if e_lap is None or e_imou is None:
            return 0.0
        max_lag = int(max_desfase_s / 0.05)
        mejor_lag = 0
        mejor_corr = -1.0
        for lag in range(-max_lag, max_lag + 1):
            if lag < 0:
                a = e_lap[-lag:]
                b = e_imou[: len(a)]
            elif lag > 0:
                b = e_imou[lag:]
                a = e_lap[: len(b)]
            else:
                n = min(len(e_lap), len(e_imou))
                a, b = e_lap[:n], e_imou[:n]
            n = min(len(a), len(b))
            if n < 8:
                continue
            c = float(np.dot(a[:n], b[:n]))
            if c > mejor_corr:
                mejor_corr = c
                mejor_lag = lag
        desfase = round(mejor_lag * 0.05, 2)
        ci._guardar_estado_camara("CONECTADA_OK", extra={"desfase_reloj_s": desfase})
        return desfase
    except Exception:
        return 0.0


def transcribir_con_whisper_metricas(rutas_wav: List[Path]) -> Dict[str, Dict[str, Any]]:
    """Ejecuta `whisper_local_worker.py` y devuelve texto + `avg_logprob` + `no_speech_prob` + `confianza`."""
    validas = [str(p) for p in rutas_wav if p and p.exists()]
    if not validas or not WHISPER_PY.exists():
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    try:
        proc = subprocess.run(
            [str(WHISPER_PY), str(WHISPER_WORKER), *validas],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        for line in (proc.stdout or "").splitlines():
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    d = json.loads(line)
                    if d.get("wav"):
                        out[str(Path(d["wav"]).resolve())] = d
                except Exception:
                    pass
    except Exception:
        pass
    return out


def unir_wavs_sesion(archivos_wav: List[Path], destino_wav: Path) -> Optional[Path]:
    """Une los clips `.wav` de una sesión operativa en un solo `.wav` mono 16 kHz."""
    import numpy as np

    partes: List[Any] = []
    for p in archivos_wav:
        if not p or not p.exists():
            continue
        try:
            with wave.open(str(p), "rb") as wf:
                raw = wf.readframes(wf.getnframes())
            arr = np.frombuffer(raw, dtype=np.int16)
            if len(arr) > 0:
                partes.append(arr)
        except Exception:
            continue
    if not partes:
        return None
    full = np.concatenate(partes).astype(np.int16)
    destino_wav.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(destino_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(full.tobytes())
    return destino_wav


def resolver_rutas_wav_evento(dia: str, archivos_str: str) -> List[Path]:
    """Localiza los archivos `_conversacion.wav` de un evento en `data/audios_noche/<dia>`."""
    rutas: List[Path] = []
    carpetas = [
        DATA_DIR / "audios_noche" / dia,
        Path(r"C:\Hostal_Sistema\audios_noche_recepcion") / dia,
    ]
    for nombre in [x.strip() for x in (archivos_str or "").split(",") if x.strip()]:
        for c in carpetas:
            cand = c / Path(nombre).name
            if cand.exists():
                rutas.append(cand)
                break
    return rutas


def verificar_evento_con_imou(evento: Dict[str, Any]) -> Dict[str, Any]:
    """
    Verifica un evento (`1-ALTA` o `2-MEDIA`) contrastando el audio de la laptop con el audio
    del búfer de la cámara Imou (si existe para esa hora) y calculando la calidad acústica (SNR dB).
    Usa caché en `data/camara_imou/verificador_cache` para no re-transcribir en cada generación de reporte.
    """
    hora_str = str(evento.get("hora") or "")
    dia = hora_str[:10]
    clave_cache = f"{dia}_{hora_str[11:].replace(':', '-')}_{evento.get('tipo', 'EV')}_v2"
    archivo_cache = VERIF_CACHE_DIR / f"{clave_cache}.json"

    if archivo_cache.exists():
        try:
            cached = json.loads(archivo_cache.read_text(encoding="utf-8"))
            if cached.get("tiene_audio_imou") and cached.get("veredicto_verificador"):
                return cached
        except Exception:
            pass

    rutas_lap = resolver_rutas_wav_evento(dia, str(evento.get("archivos") or ""))
    wav_lap_principal = rutas_lap[0] if rutas_lap else None
    met_lap = metricas_acusticas_wav(wav_lap_principal) if wav_lap_principal else {"rms": 0.0, "snr_db": 0.0, "duracion_s": 0.0}

    # Intentar extraer audio de la cámara Imou desde el búfer continuo para esa ventana (solo en alertas 1-ALTA)
    t_ini = None
    if str(evento.get("prioridad") or "1-ALTA") == "1-ALTA":
        for fmt in ("%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
            try:
                t_ini = dt.datetime.strptime(hora_str, fmt)
                break
            except Exception:
                continue

    wav_imou_temp = VERIF_CACHE_DIR / f"{clave_cache}_imou.wav"
    tiene_audio_imou = False
    if t_ini is not None:
        d0 = t_ini - dt.timedelta(seconds=5)
        d1 = t_ini + dt.timedelta(seconds=max(15.0, float(evento.get("n_clips") or 1) * 18.0))
        extraido = ci.extraer_audio_imou(d0, d1, wav_imou_temp)
        tiene_audio_imou = bool(extraido and extraido.exists() and extraido.stat().st_size > 8000)

    if archivo_cache.exists() and not tiene_audio_imou:
        try:
            cached = json.loads(archivo_cache.read_text(encoding="utf-8"))
            if cached.get("veredicto_verificador"):
                return cached
        except Exception:
            pass

    if tiene_audio_imou:
        met_imou = metricas_acusticas_wav(wav_imou_temp)
        desfase_s = estimar_desfase_reloj_seg(wav_lap_principal, wav_imou_temp) if wav_lap_principal else 0.0
        res_w = transcribir_con_whisper_metricas([wav_imou_temp])
        info_w = next(iter(res_w.values()), {})
        txt_imou = str(info_w.get("texto") or "").strip()
        conf_imou = float(info_w.get("confianza") or 0.0)
        lx_imou = lr.clasificar_por_lexico(txt_imou) if txt_imou else {}
        monto_imou = float(lx_imou.get("precio") or 0.0)
        hab_imou = str(lx_imou.get("hab") or "")
        monto_lap = float(evento.get("monto_audio") or 0.0)
        monto_sis = float(evento.get("monto_sistema") or 0.0)

        tipo_imou = str(lx_imou.get("tipo_interaccion") or "")
        banderas_imou = set(lx_imou.get("banderas") or [])
        imou_sin_operacion = bool(txt_imou) and tipo_imou in ("AUDIO_FONDO", "CHARLA_INTERNA") \
            and not lx_imou.get("dni") and not monto_imou and not hab_imou \
            and not (banderas_imou & {"ALERTA_YAPE", "PAGO_PENDIENTE", "PAGO_MIXTO"})
        tipo_lap = str(evento.get("tipo") or "")

        if tipo_imou == "AUDIO_FONDO" or tipo_lap == "AUDIO_FONDO":
            veredicto = "DESCARTADA_POR_IMOU (El micrófono de Imou capta audio de TV/celular, no una atención en mostrador)"
            fuente_pref = "IMOU"
        elif not txt_imou and met_imou["snr_db"] < 6.0 and monto_lap > 0 and monto_sis == 0:
            veredicto = "DESCARTADA_POR_IMOU (Sin voz real en mostrador en audio Imou)"
            fuente_pref = "IMOU"
        elif imou_sin_operacion and conf_imou >= 0.45 and monto_lap > 0 and monto_sis == 0:
            veredicto = "DESCARTADA_POR_IMOU (Audio Imou más claro: no se menciona cobro, DNI ni habitación)"
            fuente_pref = "IMOU"
        elif monto_imou > 0 and abs(monto_imou - monto_sis) <= 1.0:
            veredicto = f"CORREGIDA_POR_IMOU (Imou captó S/ {monto_imou:.2f} = Sistema S/ {monto_sis:.2f})"
            fuente_pref = "IMOU"
        elif txt_imou and (abs(monto_imou - monto_lap) <= 5.0 or hab_imou == str(evento.get("hab") or "")):
            veredicto = f"CONFIRMADA_DUAL (Imou SNR {met_imou['snr_db']:.1f} dB confirma interacción)"
            fuente_pref = "IMOU" if met_imou["snr_db"] >= met_lap["snr_db"] else "LAPTOP"
        else:
            veredicto = f"VERIFICADA_CON_IMOU (Audio Imou SNR {met_imou['snr_db']:.1f} dB vs Laptop {met_lap['snr_db']:.1f} dB)"
            fuente_pref = "IMOU" if conf_imou >= 0.45 else "LAPTOP"

        out = {
            "tiene_audio_imou": True,
            "transcripcion_imou": txt_imou,
            "confianza_imou": conf_imou,
            "snr_laptop_db": met_lap["snr_db"],
            "snr_imou_db": met_imou["snr_db"],
            "desfase_reloj_s": desfase_s,
            "fuente_preferida": fuente_pref,
            "veredicto_verificador": veredicto,
            "wav_imou_path": str(wav_imou_temp),
        }
        archivo_cache.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        return out

    # Caso histórico (antes de activar el búfer de audio continuo Imou): evaluar SNR + foto cámara Imou
    tiene_foto = bool(evento.get("foto_camara") and Path(str(evento["foto_camara"])).exists())
    snr = met_lap["snr_db"]
    if snr >= 12.0 and tiene_foto:
        veredicto = f"VOZ_CLARA_Y_FOTO_HD (Laptop SNR {snr:.1f} dB + Captura Imou)"
    elif tiene_foto:
        veredicto = f"CORROBORAR_EN_VIDEO_ALERTA (Laptop SNR {snr:.1f} dB + Captura Imou)"
    else:
        veredicto = f"SOLO_AUDIO_LAPTOP (SNR {snr:.1f} dB — previo a cámara)"

    out = {
        "tiene_audio_imou": False,
        "transcripcion_imou": "",
        "confianza_imou": 0.0,
        "snr_laptop_db": snr,
        "snr_imou_db": 0.0,
        "desfase_reloj_s": 0.0,
        "fuente_preferida": "LAPTOP",
        "veredicto_verificador": veredicto,
        "wav_imou_path": "",
    }
    archivo_cache.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    asegurar_columnas_verificador()
    print("Columnas del verificador aseguradas en auditoria_audio_noche.")
