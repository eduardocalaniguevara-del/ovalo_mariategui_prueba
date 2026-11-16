#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Módulo de Integración Local con Cámara Imou de Recepción (IPC-C22FN-C)
----------------------------------------------------------------------
- Conexión 100% local vía RTSP TCP (Cero gasto de API / Cero nube).
- Canales RTSP verificados en recepción:
    * `subtype=0` (Principal): Video HEVC Full HD 1920x1080 + Audio AAC 16 kHz mono.
    * `subtype=1` (Subflujo):  Video H.264 SD 640x480 + Audio AAC 16 kHz mono (~270 kbps).
- Auto-descubrimiento de IP en la red LAN por MAC (`90-6a-94-ce-f6-b2`) o puerto 554
  si el router cambia la IP por DHCP (IP actual: `192.168.0.3`).
- Captura instantánea Full HD (1920x1080) sincronizada con cada conversación de audio
  detectada en el mostrador (`<HH-MM-SS>_camara.jpg`).
- Búfer continuo circular de 7 días (`GrabadorBufferImou`) en tramos de 60 s:
  guarda `<HH-MM-SS>.mp4` (H.264 + AAC) y `<HH-MM-SS>.wav` (PCM 16 kHz mono) en
  `C:\\Recepcion_Ovalo_Local\\data\\camara_imou\\buffer\\<YYYY-MM-DD>\\`.
- Extracción instantánea de clips `.mp4` y `.wav` para el verificador de audio
  y para la carpeta `03_Alertas_Graves` de Administración (retención automática 7 días).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_APP_DIR = Path(__file__).resolve().parent
if str(BASE_APP_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_APP_DIR))

from config import DATA_DIR

IMOU_MODELO = "IPC-C22FN-C"
IMOU_SN = "9K0B1D9GDND42F7"
IMOU_MAC = "90-6a-94-ce-f6-b2"
IMOU_USER = os.environ.get("IMOU_USER", "admin")
IMOU_SAFETY_CODE = os.environ.get("IMOU_SAFETY_CODE", "L239F4C7")
IMOU_DEFAULT_IP = os.environ.get("IMOU_IP", "192.168.0.3")

CAMARA_BASE_DIR = DATA_DIR / "camara_imou"
BUFFER_CAMARA_DIR = CAMARA_BASE_DIR / "buffer"
CAMARA_CACHE_JSON = DATA_DIR / "estado_camara_imou.json"
VIDEOS_CAMARA_DIR = DATA_DIR / "videos_camara_imou"

BUFFER_CAMARA_DIR.mkdir(parents=True, exist_ok=True)
VIDEOS_CAMARA_DIR.mkdir(parents=True, exist_ok=True)

_ip_cache: str = IMOU_DEFAULT_IP
_lock_cam = threading.Lock()
_buffer_thread: Optional["GrabadorBufferImou"] = None


def _puerto_abierto(ip: str, port: int = 554, timeout: float = 0.6) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((ip, port)) == 0
    except Exception:
        return False
    finally:
        s.close()


def obtener_ip_camara() -> str:
    """Devuelve la IP activa de la cámara Imou en la red local (con auto-descubrimiento por MAC/puerto 554)."""
    global _ip_cache
    if _ip_cache and _puerto_abierto(_ip_cache, 554, timeout=0.6):
        return _ip_cache

    # 1. Buscar por dirección física MAC en la tabla ARP
    try:
        arp_out = subprocess.check_output("arp -a", shell=True, text=True, errors="replace", timeout=3)
        for linea in arp_out.splitlines():
            if IMOU_MAC.lower() in linea.lower():
                m = re.search(r"(\d+\.\d+\.\d+\.\d+)", linea)
                if m and _puerto_abierto(m.group(1), 554, timeout=0.8):
                    _ip_cache = m.group(1)
                    return _ip_cache
    except Exception:
        pass

    # 2. Escanear subred local buscando puerto 554 + 37777 (Dahua/Imou)
    try:
        ips_locales = socket.gethostbyname_ex(socket.gethostname())[2]
        ip_lan = next((ip for ip in ips_locales if ip.startswith(("192.168.", "10.", "172."))), "192.168.0.29")
        prefijo = ".".join(ip_lan.split(".")[:3]) + "."
        import concurrent.futures

        def _probar(i: int) -> Optional[str]:
            cand = f"{prefijo}{i}"
            if _puerto_abierto(cand, 554, 0.35) and _puerto_abierto(cand, 37777, 0.35):
                return cand
            return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=48) as ex:
            for r in ex.map(_probar, range(1, 255)):
                if r:
                    _ip_cache = r
                    return _ip_cache
    except Exception:
        pass

    return _ip_cache or IMOU_DEFAULT_IP


def construir_url_rtsp(substream: bool = False, ip: Optional[str] = None) -> str:
    ip_usar = ip or obtener_ip_camara()
    sub = 1 if substream else 0
    return f"rtsp://{IMOU_USER}:{IMOU_SAFETY_CODE}@{ip_usar}:554/cam/realmonitor?channel=1&subtype={sub}"


def leer_estado_camara() -> Dict[str, Any]:
    try:
        if CAMARA_CACHE_JSON.exists():
            return json.loads(CAMARA_CACHE_JSON.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _guardar_estado_camara(estado: str, ultima_foto: str = "", extra: Optional[Dict[str, Any]] = None) -> None:
    try:
        prev = leer_estado_camara()
        payload = {
            **prev,
            "modelo": IMOU_MODELO,
            "sn": IMOU_SN,
            "mac": IMOU_MAC,
            "ip": _ip_cache,
            "estado": estado,
            "updated_at": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        if ultima_foto:
            payload["ultima_foto"] = ultima_foto
        if extra:
            payload.update(extra)
        CAMARA_CACHE_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def capturar_snapshot_imou(
    ruta_destino_jpg: Path,
    espejo_dir: Optional[Path] = None,
    substream: bool = False,
    timeout_s: float = 5.0,
) -> Optional[Path]:
    """
    Abre el stream RTSP local de la cámara Imou, extrae 1 fotograma nítido Full HD (1920x1080)
    y lo guarda en `ruta_destino_jpg` (y opcionalmente en `espejo_dir`).
    Nunca lanza excepción para no interrumpir el demonio de audio.
    """
    try:
        import av

        url = construir_url_rtsp(substream=substream)
        ruta_destino_jpg.parent.mkdir(parents=True, exist_ok=True)
        usout = str(int(timeout_s * 1_000_000))
        container = av.open(url, options={"rtsp_transport": "tcp", "stimeout": usout})
        try:
            for frame in container.decode(video=0):
                img = frame.to_image()
                img.save(str(ruta_destino_jpg), quality=88)
                if espejo_dir:
                    try:
                        espejo_dir.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(ruta_destino_jpg, espejo_dir / ruta_destino_jpg.name)
                    except Exception:
                        pass
                _guardar_estado_camara("CONECTADA_OK", str(ruta_destino_jpg))
                return ruta_destino_jpg
        finally:
            container.close()
    except Exception as e:
        _guardar_estado_camara(f"AVISO: {e}")
    return None


def grabar_tramo_continuo_mp4(
    duracion_seg: int = 60,
    substream: bool = True,
    carpeta_destino: Optional[Path] = None,
    guardar_wav_16k: bool = True,
) -> Optional[Path]:
    """
    Graba un tramo MP4 (video H.264 remux 0% CPU + audio AAC 16 kHz) desde el RTSP de la cámara Imou,
    y opcionalmente guarda el archivo `.wav` PCM 16 kHz mono paralelo para transcripción/verificación.
    """
    try:
        import av
        import numpy as np

        ahora = dt.datetime.now()
        carpeta_dia = carpeta_destino or (BUFFER_CAMARA_DIR / ahora.strftime("%Y-%m-%d"))
        carpeta_dia.mkdir(parents=True, exist_ok=True)
        prefijo = ahora.strftime("%H-%M-%S")
        ruta_mp4 = carpeta_dia / f"{prefijo}.mp4"
        ruta_wav = carpeta_dia / f"{prefijo}.wav"

        url = construir_url_rtsp(substream=substream)
        in_c = av.open(url, options={"rtsp_transport": "tcp", "stimeout": "6000000", "rw_timeout": "8000000"})
        in_v = in_c.streams.video[0]
        in_a = in_c.streams.audio[0] if in_c.streams.audio else None

        out_c = av.open(str(ruta_mp4), mode="w")
        out_v = out_c.add_stream_from_template(in_v)
        out_a = out_c.add_stream("aac", rate=16000) if in_a else None
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000) if in_a else None

        pcm_chunks: List[Any] = []
        t0 = time.time()
        n_pkts = 0
        streams_demux = [in_v] + ([in_a] if in_a else [])
        try:
            for pkt in in_c.demux(*streams_demux):
                if pkt.dts is None:
                    continue
                if pkt.stream.type == "video":
                    pkt.stream = out_v
                    out_c.mux(pkt)
                    n_pkts += 1
                elif in_a and out_a and resampler and pkt.stream.type == "audio":
                    for fr in pkt.decode():
                        for rf in resampler.resample(fr):
                            if guardar_wav_16k:
                                pcm_chunks.append(rf.to_ndarray().reshape(-1))
                            rf.pts = None
                            for op in out_a.encode(rf):
                                out_c.mux(op)
                if (time.time() - t0) >= duracion_seg:
                    break
                if (time.time() - t0) >= duracion_seg + 20:  # corte de seguridad si el flujo se atasca
                    break
            if out_a:
                for op in out_a.encode(None):
                    out_c.mux(op)
        finally:
            out_c.close()
            in_c.close()

        if guardar_wav_16k and pcm_chunks:
            data_pcm = np.concatenate(pcm_chunks).astype(np.int16)
            with wave.open(str(ruta_wav), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(data_pcm.tobytes())

        if n_pkts > 0 and ruta_mp4.exists():
            dur_real = round(time.time() - t0, 1)
            _guardar_estado_camara(
                "BUFFER_ACTIVO_OK",
                extra={
                    "ultimo_tramo_mp4": str(ruta_mp4),
                    "ultimo_tramo_wav": str(ruta_wav) if ruta_wav.exists() else "",
                    "ultimo_tramo_dur_s": dur_real,
                },
            )
            return ruta_mp4
    except Exception as e:
        _guardar_estado_camara(f"ERROR_GRABACION: {e}")
    return None


class GrabadorBufferImou(threading.Thread):
    """
    Hilo demonio 24/7 que mantiene el búfer circular de 7 días de video+audio de la cámara Imou
    en `data/camara_imou/buffer/<YYYY-MM-DD>/<HH-MM-SS>.mp4` y `.wav` en tramos de 60 segundos.
    """

    def __init__(self, duracion_tramo_seg: int = 60, dias_retencion: int = 7) -> None:
        super().__init__(daemon=True, name="GrabadorBufferImou")
        self.duracion_tramo_seg = duracion_tramo_seg
        self.dias_retencion = dias_retencion
        self._stop_event = threading.Event()
        self._ultima_limpieza_dia = ""

    def detener(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        espera_err = 5.0
        while not self._stop_event.is_set():
            hoy = dt.datetime.now().strftime("%Y-%m-%d")
            if hoy != self._ultima_limpieza_dia:
                try:
                    limpiar_retencion_imou(dias_buffer=self.dias_retencion, dias_alertas=self.dias_retencion)
                    self._ultima_limpieza_dia = hoy
                except Exception:
                    pass
            mp4 = grabar_tramo_continuo_mp4(
                duracion_seg=self.duracion_tramo_seg,
                substream=True,
                guardar_wav_16k=True,
            )
            if mp4 is not None:
                espera_err = 5.0
            else:
                self._stop_event.wait(espera_err)
                espera_err = min(60.0, espera_err * 1.5)


def iniciar_buffer_imou_en_segundo_plano(duracion_tramo_seg: int = 60, dias_retencion: int = 7) -> GrabadorBufferImou:
    global _buffer_thread
    if _buffer_thread is None or not _buffer_thread.is_alive():
        _buffer_thread = GrabadorBufferImou(duracion_tramo_seg=duracion_tramo_seg, dias_retencion=dias_retencion)
        _buffer_thread.start()
    return _buffer_thread


def _listar_tramos_en_ventana(
    desde_dt: dt.datetime,
    hasta_dt: dt.datetime,
    extension: str = ".wav",
    margen_prev_s: float = 70.0,
) -> List[Tuple[dt.datetime, Path]]:
    """Lista los archivos de tramo (`.wav` o `.mp4`) del búfer que se solapan con `[desde_dt, hasta_dt]`."""
    dias = sorted({
        (desde_dt - dt.timedelta(days=1)).strftime("%Y-%m-%d"),
        desde_dt.strftime("%Y-%m-%d"),
        hasta_dt.strftime("%Y-%m-%d"),
    })
    encontrados: List[Tuple[dt.datetime, Path]] = []
    for d in dias:
        c_dia = BUFFER_CAMARA_DIR / d
        if not c_dia.exists():
            continue
        for f in sorted(c_dia.glob(f"*{extension}")):
            pref = f.stem[:8]
            try:
                t_ini = dt.datetime.strptime(f"{d} {pref.replace('-', ':')}", "%Y-%m-%d %H:%M:%S")
            except Exception:
                continue
            # Cada tramo dura ~60s; incluir si inició desde (desde_dt - margen_prev_s) hasta hasta_dt
            if (desde_dt - dt.timedelta(seconds=margen_prev_s)) <= t_ini <= hasta_dt:
                encontrados.append((t_ini, f))
    encontrados.sort(key=lambda x: x[0])
    return encontrados


def extraer_audio_imou(
    desde_dt: dt.datetime,
    hasta_dt: dt.datetime,
    destino_wav: Path,
    desfase_reloj_s: float = 0.0,
) -> Optional[Path]:
    """
    Extrae el audio PCM 16 kHz mono de la cámara Imou para la ventana `[desde_dt, hasta_dt]`
    (aplicando `desfase_reloj_s`) a partir de los tramos del búfer local.
    """
    import numpy as np

    d0 = desde_dt + dt.timedelta(seconds=desfase_reloj_s)
    d1 = hasta_dt + dt.timedelta(seconds=desfase_reloj_s)
    if d1 <= d0:
        d1 = d0 + dt.timedelta(seconds=10)

    tramos = _listar_tramos_en_ventana(d0, d1, extension=".wav", margen_prev_s=75.0)
    if not tramos:
        return None

    segmentos_pcm: List[Any] = []
    for t_ini, wav_path in tramos:
        try:
            with wave.open(str(wav_path), "rb") as wf:
                sr = wf.getframerate()
                n_frames = wf.getnframes()
                raw = wf.readframes(n_frames)
            arr = np.frombuffer(raw, dtype=np.int16)
            dur_tramo = len(arr) / float(sr)
            t_fin = t_ini + dt.timedelta(seconds=dur_tramo)
            if t_fin <= d0 or t_ini >= d1:
                continue
            s_ini = max(0.0, (d0 - t_ini).total_seconds())
            s_fin = min(dur_tramo, (d1 - t_ini).total_seconds())
            i0 = max(0, int(s_ini * sr))
            i1 = min(len(arr), int(s_fin * sr))
            if i1 > i0:
                segmentos_pcm.append(arr[i0:i1])
        except Exception:
            continue

    if not segmentos_pcm:
        return None

    full_pcm = np.concatenate(segmentos_pcm).astype(np.int16)
    if len(full_pcm) < 8000:  # menos de 0.5 s
        return None
    destino_wav.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(destino_wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(full_pcm.tobytes())
    return destino_wav


def generar_mp4_desde_foto_y_audio(
    foto_jpg: Path,
    audio_wav: Path,
    destino_mp4: Path,
) -> Optional[Path]:
    """
    Respaldo para alertas históricas (anteriores al inicio del búfer MP4 continuo):
    crea un video `.mp4` (H.264 + AAC) combinando la captura `_camara.jpg` del evento
    con el audio `_conversacion.wav` para que Administración pueda reproducirlo en un solo clic.
    """
    try:
        import av
        import numpy as np
        from PIL import Image

        if not foto_jpg.exists() or not audio_wav.exists():
            return None

        img = Image.open(str(foto_jpg)).convert("RGB")
        # Ajustar a dimensiones pares (ej. 1280x720 o 640x360) para H.264
        w, h = 960, 540
        img = img.resize((w, h))
        arr_rgb = np.array(img, dtype=np.uint8)

        with wave.open(str(audio_wav), "rb") as wf:
            sr = wf.getframerate()
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)
        pcm = np.frombuffer(raw, dtype=np.int16)
        dur_s = max(2.0, min(90.0, len(pcm) / float(sr or 16000)))

        destino_mp4.parent.mkdir(parents=True, exist_ok=True)
        out_c = av.open(str(destino_mp4), mode="w")
        fps = 5
        v_stream = out_c.add_stream("libx264", rate=fps)
        v_stream.width = w
        v_stream.height = h
        v_stream.pix_fmt = "yuv420p"
        a_stream = out_c.add_stream("aac", rate=16000)

        try:
            # Codificar frames de video
            total_v_frames = int(dur_s * fps)
            for i in range(total_v_frames):
                vf = av.VideoFrame.from_ndarray(arr_rgb, format="rgb24")
                vf.pts = i
                for pkt in v_stream.encode(vf):
                    out_c.mux(pkt)
            for pkt in v_stream.encode(None):
                out_c.mux(pkt)

            # Codificar audio en bloques de 1024 muestras
            paso = 1024
            for i in range(0, len(pcm), paso):
                bloque = pcm[i : i + paso]
                if len(bloque) == 0:
                    continue
                af = av.AudioFrame.from_ndarray(bloque.reshape(1, -1), format="s16", layout="mono")
                af.sample_rate = 16000
                af.pts = i
                for pkt in a_stream.encode(af):
                    out_c.mux(pkt)
            for pkt in a_stream.encode(None):
                out_c.mux(pkt)
        finally:
            out_c.close()

        return destino_mp4 if destino_mp4.exists() else None
    except Exception:
        return None


def extraer_clip_imou(
    desde_dt: dt.datetime,
    hasta_dt: dt.datetime,
    destino_mp4: Path,
    foto_respaldo: Optional[Path] = None,
    audio_respaldo: Optional[Path] = None,
) -> Optional[Path]:
    """
    Extrae el clip de video `.mp4` con audio desde el búfer continuo de la cámara Imou
    para la ventana `[desde_dt, hasta_dt]`. Si el evento ocurrió antes de que existiera el búfer,
    genera automáticamente un `.mp4` reproducible usando `foto_respaldo` + `audio_respaldo`.
    """
    tramos = _listar_tramos_en_ventana(desde_dt, hasta_dt, extension=".mp4", margen_prev_s=75.0)
    if tramos:
        try:
            # Si cubre 1 tramo, copiar directo; si cubre varios tramos, concatenar remuxeando
            destino_mp4.parent.mkdir(parents=True, exist_ok=True)
            if len(tramos) == 1:
                shutil.copy2(tramos[0][1], destino_mp4)
                return destino_mp4
            import av

            primero = av.open(str(tramos[0][1]))
            out_c = av.open(str(destino_mp4), mode="w")
            out_v = out_c.add_stream_from_template(primero.streams.video[0])
            out_a = out_c.add_stream_from_template(primero.streams.audio[0]) if primero.streams.audio else None
            primero.close()

            offset_v = 0
            offset_a = 0
            for _, mp4_p in tramos[:3]:
                inc = av.open(str(mp4_p))
                max_v_pts = offset_v
                max_a_pts = offset_a
                try:
                    streams_in = [inc.streams.video[0]] + ([inc.streams.audio[0]] if (inc.streams.audio and out_a) else [])
                    for pkt in inc.demux(*streams_in):
                        if pkt.dts is None or pkt.pts is None:
                            continue
                        if pkt.stream.type == "video":
                            pkt.pts += offset_v
                            pkt.dts += offset_v
                            max_v_pts = max(max_v_pts, pkt.pts + (pkt.duration or 1))
                            pkt.stream = out_v
                            out_c.mux(pkt)
                        elif out_a and pkt.stream.type == "audio":
                            pkt.pts += offset_a
                            pkt.dts += offset_a
                            max_a_pts = max(max_a_pts, pkt.pts + (pkt.duration or 1))
                            pkt.stream = out_a
                            out_c.mux(pkt)
                finally:
                    inc.close()
                offset_v = max_v_pts
                offset_a = max_a_pts
            out_c.close()
            if destino_mp4.exists() and destino_mp4.stat().st_size > 1000:
                return destino_mp4
        except Exception:
            pass

    if foto_respaldo and audio_respaldo:
        return generar_mp4_desde_foto_y_audio(foto_respaldo, audio_respaldo, destino_mp4)
    return None


def limpiar_retencion_imou(dias_buffer: int = 7, dias_alertas: int = 7) -> Dict[str, int]:
    """
    Aplica el temporizador de retención de 7 días para que no ocupe espacio:
    - Elimina carpetas del búfer (`data/camara_imou/buffer/<YYYY-MM-DD>`) con más de `dias_buffer` días.
    - Elimina carpetas de `Downloads\\Auditor AU\\03_Alertas_Graves\\<YYYY-MM-DD>` con más de `dias_alertas` días.
    - Elimina capturas crudas `*_camara.jpg` de `data/audios_noche/<YYYY-MM-DD>` con más de `dias_buffer` días.
    - NUNCA toca `01_Informes_Diarios`, `02_Conversaciones_Completas` ni `04_Revision_IA`.
    """
    limite_buf = (dt.datetime.now() - dt.timedelta(days=dias_buffer)).strftime("%Y-%m-%d")
    limite_al = (dt.datetime.now() - dt.timedelta(days=dias_alertas)).strftime("%Y-%m-%d")
    borrados = {"buffer_dias": 0, "alertas_dias": 0, "fotos_crudas": 0}

    # 1. Búfer continuo
    if BUFFER_CAMARA_DIR.exists():
        for sub in BUFFER_CAMARA_DIR.iterdir():
            if sub.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", sub.name) and sub.name < limite_buf:
                shutil.rmtree(sub, ignore_errors=True)
                borrados["buffer_dias"] += 1

    # 2. Expedientes de Alertas Graves en Downloads\Auditor AU\03_Alertas_Graves
    for base_au in [
        Path(r"C:\Users\ovalo mariategui\Downloads\Auditor AU\03_Alertas_Graves"),
        Path(r"C:\Hostal_Sistema\descargas_recepcion\Auditor AU\03_Alertas_Graves"),
    ]:
        if base_au.exists():
            for sub in base_au.iterdir():
                if sub.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", sub.name) and sub.name < limite_al:
                    shutil.rmtree(sub, ignore_errors=True)
                    borrados["alertas_dias"] += 1

    # 3. Fotos crudas en data/audios_noche/<YYYY-MM-DD> y espejo
    for raiz_aud in [DATA_DIR / "audios_noche", Path(r"C:\Hostal_Sistema\audios_noche_recepcion")]:
        if raiz_aud.exists():
            for sub in raiz_aud.iterdir():
                if sub.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", sub.name) and sub.name < limite_buf:
                    for jpg in sub.glob("*_camara.jpg"):
                        try:
                            jpg.unlink()
                            borrados["fotos_crudas"] += 1
                        except Exception:
                            pass

    return borrados


if __name__ == "__main__":
    p_test = DATA_DIR / "reportes_audio" / "ultimo_snapshot_imou.jpg"
    res = capturar_snapshot_imou(p_test)
    print(f"Snapshot Imou ({obtener_ip_camara()}): {res}")
