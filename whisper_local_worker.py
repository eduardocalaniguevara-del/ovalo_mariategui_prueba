#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Worker de transcripción LOCAL (offline) para el auditor de audio.

Se ejecuta SIEMPRE como subproceso con el intérprete aislado `.venv_whisper`
(ctranslate2 4.4.0 estable). Motivo: `ctranslate2 4.8.5` del Python principal
provoca "access violation" nativo en esta PC; si se cargara dentro del demonio
24/7 apagaría también la grabación. Aislado, un fallo solo afecta a este proceso.

Uso:
    .venv_whisper\\Scripts\\python.exe whisper_local_worker.py ruta1.wav ruta2.wav ...
Salida (stdout, una línea JSON por clip):
    {"wav": "...", "ok": true, "texto": "...", "duracion": 12.3, "seg_proceso": 4.1}
"""
import json
import os
import sys
import time

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Prompt neutro sin oraciones operativas para evitar que Whisper alucine frases del prompt en clips con ruido/TV.
PROMPT_HOSTAL = (
    "Transcripción literal de recepción del Hotel El Óvalo Mariátegui en Perú. "
    "Nombres propios: Eliana, Paola, Eleiza, Rous, Yolyi, Eduardo, Lalo, Maelen Hogar, Yape, Plin, Gatorade."
)

HOTWORDS_HOSTAL_PERU = (
    "Yape Plin Maelen Hogar Eliana Paola Eleiza Rous Yolyi Eduardo Lalo "
    "DNI RUC boleta factura matrimonial simple triple habitación cuarto "
    "efectivo sencillo vuelto lucas soles Gatorade Sublime Shalom"
)

MODELO = os.environ.get("AUDITOR_WHISPER_MODELO", "small")
HILOS = int(os.environ.get("AUDITOR_WHISPER_HILOS", "4"))


def leer_wav_16k(ruta):
    """Lee el WAV mono 16 kHz del grabador y aplica filtro pasa-banda vocal (100Hz-7200Hz) para limpiar zumbido."""
    import wave
    import numpy as np

    with wave.open(ruta, "rb") as wf:
        sr = wf.getframerate()
        ch = wf.getnchannels()
        raw = wf.readframes(wf.readnframes() if hasattr(wf, "readnframes") else wf.getnframes())
    y = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        y = y.reshape(-1, ch).mean(axis=1)
    if sr != 16000 and len(y) > 0:
        n_dst = int(len(y) * 16000 / sr)
        y = np.interp(np.linspace(0, len(y) - 1, n_dst), np.arange(len(y)), y).astype(np.float32)
    # Filtro espectral rápido en FFT para eliminar ruido grave (<95 Hz, ventilador/mesa) y agudo (>7400 Hz)
    if len(y) > 512:
        spec = np.fft.rfft(y)
        freqs = np.fft.rfftfreq(len(y), d=1.0 / 16000.0)
        mask = (freqs >= 95.0) & (freqs <= 7400.0)
        y = np.fft.irfft(spec * mask, n=len(y)).astype(np.float32)
    return y


def main(rutas):
    from faster_whisper import WhisperModel

    modelo = WhisperModel(MODELO, device="cpu", compute_type="int8", cpu_threads=HILOS)
    for ruta in rutas:
        t0 = time.time()
        try:
            segs, info = modelo.transcribe(
                leer_wav_16k(ruta),
                language="es",
                beam_size=3,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 600},
                initial_prompt=PROMPT_HOSTAL,
                hotwords=HOTWORDS_HOSTAL_PERU,
                condition_on_previous_text=False,
                no_speech_threshold=0.6,
            )
            partes = []
            logprobs = []
            nospeech = []
            for s in segs:
                txt = s.text.strip()
                # Descarta segmentos con alta probabilidad de silencio/ruido
                if txt and s.no_speech_prob < 0.75:
                    partes.append(txt)
                    logprobs.append(float(getattr(s, "avg_logprob", -1.0)))
                    nospeech.append(float(getattr(s, "no_speech_prob", 0.5)))
            texto = " ".join(partes).strip()
            # Whisper a veces "repite" el prompt cuando no hay voz: se descarta
            if texto and texto[:60] in PROMPT_HOSTAL:
                texto = ""
            avg_lp = round(sum(logprobs) / len(logprobs), 3) if logprobs else -2.0
            avg_ns = round(sum(nospeech) / len(nospeech), 3) if nospeech else 1.0
            # Puntaje 0..1 combinando certeza de tokens (exp(avg_logprob)) y presencia de voz (1 - no_speech_prob)
            import math
            conf = round(max(0.0, min(1.0, math.exp(max(-3.0, avg_lp)) * (1.0 - avg_ns))), 3) if texto else 0.0
            print(json.dumps({
                "wav": ruta, "ok": True, "texto": texto,
                "duracion": round(float(info.duration), 1),
                "seg_proceso": round(time.time() - t0, 1),
                "avg_logprob": avg_lp,
                "no_speech_prob": avg_ns,
                "confianza": conf,
            }, ensure_ascii=False), flush=True)
        except Exception as e:
            print(json.dumps({"wav": ruta, "ok": False, "error": str(e)[:300]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
