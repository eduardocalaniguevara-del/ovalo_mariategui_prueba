#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prueba A/B de Calidad de Audio: Cámara Imou RTSP (AAC 16 kHz) vs. Micrófono Laptop
----------------------------------------------------------------------------------
- Graba N segundos del stream RTSP de la cámara Imou (`camara_imou.grabar_tramo_continuo_mp4`)
  sin interrumpir el demonio 24/7 (`auditor_audio_noche.py --vigilar`).
- Compara las métricas acústicas (`RMS`, `Pico`, `SNR dB`) y la transcripción de
  `.venv_whisper` (`avg_logprob`, `no_speech_prob`, `confianza`) contra los clips
  de la laptop grabados en la misma ventana o el clip con voz más reciente.
- Guarda el reporte lado a lado en `C:\\Recepcion_Ovalo_Local\\data\\reportes_audio\\prueba_ab_<fecha>.md`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import camara_imou as ci  # noqa: E402
import verificador_audio_imou as vai  # noqa: E402
from config import DATA_DIR  # noqa: E402


def ejecutar_prueba_ab(duracion_seg: int = 20) -> Path:
    t_ini = dt.datetime.now()
    mp4_path = ci.grabar_tramo_continuo_mp4(duracion_seg=duracion_seg, substream=True, guardar_wav_16k=True)
    t_fin = dt.datetime.now()

    wav_imou = mp4_path.with_suffix(".wav") if mp4_path else None
    met_imou = vai.metricas_acusticas_wav(wav_imou) if wav_imou else {"rms": 0.0, "pico": 0.0, "snr_db": 0.0, "duracion_s": 0.0}

    # Buscar clips de laptop en la carpeta de hoy (en la ventana o el último clip con voz del día)
    dia_hoy = t_ini.strftime("%Y-%m-%d")
    carpeta_lap = DATA_DIR / "audios_noche" / dia_hoy
    clips_lap = sorted(carpeta_lap.glob("*_conversacion.wav")) if carpeta_lap.exists() else []
    wav_lap = clips_lap[-1] if clips_lap else None
    met_lap = vai.metricas_acusticas_wav(wav_lap) if wav_lap else {"rms": 0.0, "pico": 0.0, "snr_db": 0.0, "duracion_s": 0.0}

    a_transcribir = [p for p in [wav_imou, wav_lap] if p and p.exists()]
    res_w = vai.transcribir_con_whisper_metricas(a_transcribir)
    w_imou = res_w.get(str(wav_imou.resolve()), {}) if (wav_imou and wav_imou.exists()) else {}
    w_lap = res_w.get(str(wav_lap.resolve()), {}) if (wav_lap and wav_lap.exists()) else {}

    desfase_s = vai.estimar_desfase_reloj_seg(wav_lap, wav_imou) if (wav_lap and wav_imou and wav_lap.exists() and wav_imou.exists()) else 0.0

    reporte_dir = DATA_DIR / "reportes_audio"
    reporte_dir.mkdir(parents=True, exist_ok=True)
    ruta_md = reporte_dir / f"prueba_ab_{t_ini.strftime('%Y-%m-%d_%H-%M')}.md"

    md = [
        f"# 🎙️ Prueba A/B de Audio: Cámara Imou RTSP vs. Micrófono Laptop ({t_ini.strftime('%d/%m/%Y %H:%M:%S')})",
        "",
        f"- **Ventana capturada en Cámara Imou (`192.168.0.3:554`):** `{t_ini.strftime('%H:%M:%S')}` → `{t_fin.strftime('%H:%M:%S')}` (`{duracion_seg}s`)",
        f"- **Archivo MP4 + Audio Imou generado en búfer:** `{mp4_path}`",
        f"- **Archivo WAV 16 kHz Imou:** `{wav_imou}`",
        f"- **Último clip de voz en Laptop comparado:** `{wav_lap}`",
        f"- **Desfase estimado de reloj (`estimar_desfase_reloj_seg`):** `{desfase_s:+.2f} s`",
        "",
        "## Tabla Comparativa Lado a Lado",
        "",
        "| Métrica | 📷 Micrófono Cámara Imou (AAC 16 kHz) | 💻 Micrófono Laptop Recepción |",
        "| :--- | :---: | :---: |",
        f"| **Duración (s)** | `{met_imou['duracion_s']:.1f} s` | `{met_lap['duracion_s']:.1f} s` |",
        f"| **Energía RMS** | `{met_imou['rms']:.1f}` | `{met_lap['rms']:.1f}` |",
        f"| **Pico de Amplitud** | `{met_imou['pico']:.0f}` | `{met_lap['pico']:.0f}` |",
        f"| **Relación Señal/Ruido (SNR dB)** | **`{met_imou['snr_db']:.1f} dB`** | **`{met_lap['snr_db']:.1f} dB`** |",
        f"| **Whisper `avg_logprob`** | `{w_imou.get('avg_logprob', -2.0)}` | `{w_lap.get('avg_logprob', -2.0)}` |",
        f"| **Whisper `no_speech_prob`** | `{w_imou.get('no_speech_prob', 1.0)}` | `{w_lap.get('no_speech_prob', 1.0)}` |",
        f"| **Puntaje de Confianza (0..1)** | **`{w_imou.get('confianza', 0.0)}`** | **`{w_lap.get('confianza', 0.0)}`** |",
        f"| **Texto Transcrito** | *{w_imou.get('texto') or '(Silencio en mostrador durante la ventana)'}* | *{w_lap.get('texto') or '(Sin voz)'}* |",
        "",
    ]
    ruta_md.write_text("\n".join(md), encoding="utf-8")
    return ruta_md


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--duracion", type=int, default=20)
    args = ap.parse_args()
    out = ejecutar_prueba_ab(duracion_seg=args.duracion)
    print(f"Reporte A/B generado en: {out}")
