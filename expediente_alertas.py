#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generador de Expedientes de Alertas Graves para Administración (`03_Alertas_Graves`)
------------------------------------------------------------------------------------
- Trabaja sobre el turno operativo de 8:00 AM a 8:00 AM (`dia 08:00:00` → `dia+1 07:59:59`).
- Filtra ÚNICAMENTE las alertas graves del turno (`1-ALTA` no descartadas por Imou y
  operaciones Yape con `FALTANTE BANCO` en `CONCILIACION PAGOS YAPE`).
- Por cada alerta grave crea una subcarpeta autocontenida en:
  `Downloads\\Auditor AU\\03_Alertas_Graves\\<YYYY-MM-DD>\\<HH-MM-SS>_<TIPO>\\`
  con formatos 100% amigables para el usuario:
    * `Ver_Video_Alerta.html` (Reproductor interactivo de video + audio + foto HD para abrir con doble clic en la laptop)
    * `video_imou.mp4`        (Clip de video H.264 + AAC reproducible en el reproductor de Windows o navegador)
    * `foto_imou.jpg`         (Captura Full HD 1920x1080 del mostrador en ese instante)
    * `audio_laptop.wav`      (Audio original grabado en recepción)
    * `audio_imou.wav`        (Audio extraído del micrófono de la cámara Imou, si está en búfer)
    * `Ficha_Alerta.pdf`      (Ficha ejecutiva en PDF con foto incrustada y dictamen para Administración)
- Además genera en `03_Alertas_Graves\\<YYYY-MM-DD>\\`:
    * `Reproductor_Alertas_Dia_<YYYY-MM-DD>.html` (Consola de video con todas las alertas graves del turno en una sola pantalla)
    * `Resumen_Alertas_Dia_<YYYY-MM-DD>.pdf`      (Índice ejecutivo en PDF)
"""

from __future__ import annotations

import datetime as dt
import html
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import camara_imou as ci  # noqa: E402
import verificador_audio_imou as vai  # noqa: E402


def _slug(texto: str, max_len: int = 28) -> str:
    s = re.sub(r"[^A-Za-z0-9_]+", "_", str(texto or "").strip()).strip("_")
    return (s[:max_len] or "ALERTA").upper()


def _parse_dt(s: str) -> Optional[dt.datetime]:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return dt.datetime.strptime(str(s).strip(), fmt)
        except Exception:
            continue
    return None


def _e(val: Any) -> str:
    return html.escape(str(val if val is not None else ""))


def _hora_anotada_a_dt(hora_an: str, fecha_ref: str, dia: str) -> Optional[dt.datetime]:
    """Convierte '12:56pm' / '7:41 am' / '07:41' en datetime dentro del turno 8am-8am de `dia`."""
    m = re.search(r"(\d{1,2})[:.](\d{2})\s*([ap]\.?m\.?)?", str(hora_an or "").lower())
    if not m:
        return None
    hh, mm, ap = int(m.group(1)), int(m.group(2)), (m.group(3) or "")
    if ap.startswith("p") and hh < 12:
        hh += 12
    if ap.startswith("a") and hh == 12:
        hh = 0
    base = dt.date.fromisoformat(dia)
    cand = dt.datetime(base.year, base.month, base.day, hh % 24, mm)
    if cand < dt.datetime(base.year, base.month, base.day, 8, 0):
        cand += dt.timedelta(days=1)  # madrugada: pertenece al día siguiente del turno
    return cand


def _generar_reproductor_individual_html(
    carpeta_exp: Path,
    titulo: str,
    meta_items: List[tuple[str, str]],
    transcripcion_laptop: str,
    transcripcion_imou: str,
    dictamen_ia_html: str,
    tiene_video: bool,
    tiene_foto: bool,
    tiene_audio_lap: bool,
    tiene_audio_imou: bool,
) -> Path:
    """Crea `Ver_Video_Alerta.html` dentro de la carpeta de la alerta con reproductor HTML5 nativo."""
    meta_rows = "".join(
        f"<tr><td class='k'>{_e(k)}</td><td class='v'>{_e(v)}</td></tr>"
        for k, v in meta_items
    )
    video_block = (
        "<video controls autoplay preload='metadata' poster='foto_imou.jpg'>"
        "<source src='video_imou.mp4' type='video/mp4'>"
        "Su navegador no soporta reproducción de video HTML5."
        "</video>"
        if tiene_video
        else (
            "<img src='foto_imou.jpg' class='main-photo' alt='Captura Cámara Imou'/>"
            if tiene_foto
            else "<div class='no-media'>Sin video ni captura en este instante</div>"
        )
    )
    audio_lap_block = (
        "<div class='audio-box'><b>🎙️ Audio Micrófono Laptop (`audio_laptop.wav`):</b><br>"
        "<audio controls preload='metadata' src='audio_laptop.wav'></audio></div>"
        if tiene_audio_lap
        else ""
    )
    audio_imou_block = (
        "<div class='audio-box'><b>📷 Audio Micrófono Cámara Imou (`audio_imou.wav`):</b><br>"
        "<audio controls preload='metadata' src='audio_imou.wav'></audio></div>"
        if tiene_audio_imou
        else ""
    )
    foto_block = (
        "<div class='photo-card'><h3>📸 Captura Full HD Cámara Imou (`foto_imou.jpg`)</h3>"
        "<a href='foto_imou.jpg' target='_blank'><img src='foto_imou.jpg' alt='Foto Imou'/></a>"
        "<p class='hint'>Haga clic en la imagen para abrir `foto_imou.jpg` en tamaño completo (1920×1080).</p></div>"
        if tiene_foto
        else ""
    )
    imou_tr_block = (
        f"<div class='tr-box imou'><b>📷 Transcripción desde Micrófono Cámara Imou:</b><p>“{_e(transcripcion_imou)}”</p></div>"
        if transcripcion_imou
        else ""
    )

    html_str = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>{_e(titulo)}</title>
<style>
  body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 20px; }}
  .container {{ max-width: 1180px; margin: 0 auto; }}
  .header {{ background: #1e293b; border-left: 6px solid #ef4444; padding: 16px 20px; border-radius: 8px; margin-bottom: 18px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }}
  .header h1 {{ margin: 0; font-size: 18pt; color: #f8fafc; }}
  .btn-pdf {{ background: #2563eb; color: #fff; text-decoration: none; padding: 9px 16px; border-radius: 6px; font-weight: 600; font-size: 10pt; }}
  .btn-pdf:hover {{ background: #1d4ed8; }}
  .grid {{ display: grid; grid-template-columns: 1.25fr 1fr; gap: 18px; }}
  @media (max-width: 900px) {{ .grid {{ grid-template-columns: 1fr; }} }}
  .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 8px; padding: 16px; }}
  .card h2, .card h3 {{ margin-top: 0; color: #93c5fd; font-size: 13pt; border-bottom: 1px solid #334155; padding-bottom: 6px; }}
  video, .main-photo {{ width: 100%; max-height: 440px; background: #000; border-radius: 6px; border: 1px solid #475569; }}
  .audio-box {{ margin-top: 12px; background: #0f172a; padding: 10px 12px; border-radius: 6px; border: 1px solid #334155; font-size: 9.5pt; }}
  audio {{ width: 100%; margin-top: 6px; }}
  table.meta {{ width: 100%; border-collapse: collapse; font-size: 9.8pt; margin-bottom: 12px; }}
  table.meta td {{ padding: 6px 8px; border-bottom: 1px solid #334155; vertical-align: top; }}
  table.meta td.k {{ color: #94a3b8; font-weight: 600; width: 38%; }}
  table.meta td.v {{ color: #f1f5f9; font-weight: 500; }}
  .tr-box {{ background: #0f172a; border-left: 4px solid #3b82f6; padding: 10px 14px; border-radius: 6px; margin-top: 12px; font-size: 10pt; }}
  .tr-box.imou {{ border-left-color: #10b981; }}
  .tr-box p {{ margin: 6px 0 0 0; font-style: italic; color: #e2e8f0; line-height: 1.45; }}
  .ia-box {{ background: #172554; border: 1px solid #3b82f6; border-left: 5px solid #60a5fa; padding: 12px 14px; border-radius: 6px; margin-top: 14px; font-size: 9.8pt; line-height: 1.45; }}
  .photo-card {{ margin-top: 16px; text-align: center; }}
  .photo-card img {{ max-width: 100%; max-height: 260px; border-radius: 6px; border: 1px solid #64748b; }}
  .hint {{ font-size: 8.5pt; color: #94a3b8; margin: 4px 0 0 0; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>🚨 {_e(titulo)}</h1>
    <a class="btn-pdf" href="Ficha_Alerta.pdf" target="_blank">📄 Abrir Ficha en PDF (Ficha_Alerta.pdf)</a>
  </div>
  <div class="grid">
    <div class="card">
      <h2>🎬 Reproductor de Video y Audio de la Alerta</h2>
      {video_block}
      {audio_lap_block}
      {audio_imou_block}
      {foto_block}
    </div>
    <div class="card">
      <h2>📋 Detalle Contable y Verificación IA</h2>
      <table class="meta">{meta_rows}</table>
      <div class="tr-box">
        <b>🗣️ Lo que se escucha en Recepción:</b>
        <p>“{_e(transcripcion_laptop or '(Sin transcripción)')}”</p>
      </div>
      {imou_tr_block}
      <div class="ia-box">
        <b>🤖 Dictamen Visual y Auditivo IA:</b><br>
        {dictamen_ia_html}
      </div>
    </div>
  </div>
</div>
</body>
</html>"""
    out_path = carpeta_exp / "Ver_Video_Alerta.html"
    out_path.write_text(html_str, encoding="utf-8")
    return out_path


def _generar_ficha_pdf(
    html_a_pdf_fn: Optional[Callable[[str, Path], None]],
    carpeta_exp: Path,
    titulo: str,
    meta_items: List[tuple[str, str]],
    transcripcion_laptop: str,
    transcripcion_imou: str,
    dictamen_ia_texto: str,
    foto_dst: Path,
) -> Optional[Path]:
    """Genera `Ficha_Alerta.pdf` dentro de la carpeta del expediente usando Chrome Headless."""
    if html_a_pdf_fn is None:
        return None
    filas_html = "".join(
        f"<tr><td style='font-weight:700;width:34%;background:#f8fafc'>{_e(k)}</td><td>{_e(v)}</td></tr>"
        for k, v in meta_items
    )
    img_block = ""
    if foto_dst.exists():
        uri = foto_dst.resolve().as_uri()
        img_block = (
            f"<h2>📸 Captura Cámara Imou (foto_imou.jpg)</h2>"
            f"<div style='text-align:center;margin-bottom:12px'>"
            f"<img src='{uri}' style='max-width:100%;max-height:310px;border:1px solid #64748b;border-radius:4px'/>"
            f"</div>"
        )
    imou_block = (
        f"<h3>📷 Transcripción desde Micrófono Cámara Imou</h3>"
        f"<div style='background:#ecfdf5;border-left:4px solid #059669;padding:8px 10px;font-style:italic'>“{_e(transcripcion_imou)}”</div>"
        if transcripcion_imou
        else ""
    )
    ia_clean = "<br>".join(_e(line) for line in (dictamen_ia_texto or "").splitlines() if line.strip())
    doc = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
    @page {{ size: A4 portrait; margin: 14mm 12mm; }}
    body {{ font-family: 'Segoe UI', Arial, sans-serif; font-size: 10pt; color: #1e293b; line-height: 1.42; }}
    h1 {{ font-size: 14.5pt; color: #991b1b; border-bottom: 2px solid #dc2626; padding-bottom: 5px; margin-top: 0; }}
    h2 {{ font-size: 11.5pt; color: #1e3a8a; margin-top: 14px; margin-bottom: 6px; border-bottom: 1px solid #cbd5e1; padding-bottom: 2px; }}
    table {{ width: 100%; border-collapse: collapse; margin-bottom: 12px; font-size: 9.5pt; }}
    td {{ border: 1px solid #cbd5e1; padding: 6px 8px; vertical-align: top; }}
    .tr-box {{ background: #f1f5f9; border-left: 4px solid #2563eb; padding: 8px 12px; font-style: italic; border-radius: 4px; margin-bottom: 10px; }}
    .ia-box {{ background: #eff6ff; border: 1px solid #93c5fd; border-left: 4px solid #1d4ed8; padding: 9px 12px; border-radius: 4px; font-size: 9.3pt; }}
    </style></head><body>
    <h1>🚨 {_e(titulo)}</h1>
    <table><tbody>{filas_html}</tbody></table>
    {img_block}
    <h2>🗣️ Conversación Grabada en Recepción</h2>
    <div class="tr-box">“{_e(transcripcion_laptop or '(Sin transcripción)')}”</div>
    {imou_block}
    <h2>🤖 Dictamen y Guía de Verificación (Administración / IA)</h2>
    <div class="ia-box">{ia_clean}</div>
    </body></html>"""
    pdf_path = carpeta_exp / "Ficha_Alerta.pdf"
    if pdf_path.exists() and pdf_path.stat().st_size > 1500:
        return pdf_path
    try:
        html_a_pdf_fn(doc, pdf_path)
    except Exception:
        pass
    return pdf_path


def generar_expedientes_dia(
    dia: str,
    datos: Dict[str, Any],
    base_au_dir: Path,
    desde_str: str = "",
    hasta_str: str = "",
    html_a_pdf_fn: Optional[Callable[[str, Path], None]] = None,
) -> List[Dict[str, Any]]:
    """
    Construye las carpetas de `03_Alertas_Graves/<dia>/` para las alertas `1-ALTA` y los
    `FALTANTE BANCO` de Yape, generando `.mp4`, `.jpg`, `.wav`, `Ficha_Alerta.pdf` y el
    reproductor `Ver_Video_Alerta.html` + `Reproductor_Alertas_Dia_<dia>.html`.
    """
    alertas_base = base_au_dir / "03_Alertas_Graves" / dia
    # Limpiar subcarpetas obsoletas de corridas con ventana anterior para no mezclar turnos
    if alertas_base.exists():
        for old_item in alertas_base.iterdir():
            if old_item.is_file() and old_item.suffix.lower() == ".md":
                old_item.unlink(missing_ok=True)

    eventos = datos.get("eventos") or []
    altas = [
        e for e in eventos
        if e.get("prioridad") == "1-ALTA"
        and not str(e.get("veredicto_verificador") or "").startswith("DESCARTADA_POR_IMOU")
    ]

    faltantes_yape = [
        y for y in (datos.get("conciliacion_yape") or [])
        if "FALTANTE" in str(y.get("cuadre") or "").upper() or "FRAUDE" in str(y.get("cuadre") or "").upper()
    ]

    expedientes: List[Dict[str, Any]] = []
    alertas_base.mkdir(parents=True, exist_ok=True)
    carpetas_validas = set()

    # 1. Procesar eventos 1-ALTA
    for idx, ev in enumerate(altas, 1):
        hora_full = str(ev.get("hora") or f"{dia} 08:00:00")
        dia_real_clip = hora_full[:10]
        hh_mm_ss = hora_full[11:19].replace(":", "-") if len(hora_full) >= 19 else f"AL_{idx:02d}"
        hab_lbl = f"HAB{ev['hab']}" if ev.get("hab") else "MOSTRADOR"
        monto_lbl = f"S{int(round(float(ev.get('monto_audio') or 0)))}"
        nombre_carpeta = f"{hh_mm_ss}_{_slug(ev.get('tipo', 'ALERTA'), 18)}_{hab_lbl}_{monto_lbl}"
        carpeta_exp = alertas_base / nombre_carpeta
        carpeta_exp.mkdir(parents=True, exist_ok=True)
        carpetas_validas.add(nombre_carpeta)

        # Limpiar archivos .md crudos si existían de antes
        for md_old in ("ficha.md", "revision_ia.md"):
            p_md = carpeta_exp / md_old
            if p_md.exists():
                p_md.unlink(missing_ok=True)

        # Copiar foto_imou.jpg
        foto_orig = Path(str(ev.get("foto_camara") or "")) if ev.get("foto_camara") else None
        foto_dst = carpeta_exp / "foto_imou.jpg"
        if foto_orig and foto_orig.exists():
            shutil.copy2(foto_orig, foto_dst)

        # Unir/copiar audio_laptop.wav buscando en la fecha real del clip (ej. madrugada del día siguiente)
        wavs_lap = vai.resolver_rutas_wav_evento(dia_real_clip, str(ev.get("archivos") or ""))
        if not wavs_lap and dia_real_clip != dia:
            wavs_lap = vai.resolver_rutas_wav_evento(dia, str(ev.get("archivos") or ""))
        audio_lap_dst = carpeta_exp / "audio_laptop.wav"
        if wavs_lap:
            vai.unir_wavs_sesion(wavs_lap, audio_lap_dst)

        # Extraer audio_imou.wav y video_imou.mp4
        t_ev = _parse_dt(hora_full) or dt.datetime.now()
        d0 = t_ev - dt.timedelta(seconds=35)
        d1 = t_ev + dt.timedelta(seconds=35)

        audio_imou_dst = carpeta_exp / "audio_imou.wav"
        ci.extraer_audio_imou(d0, d1, audio_imou_dst)

        video_dst = carpeta_exp / "video_imou.mp4"
        if not video_dst.exists() or video_dst.stat().st_size < 5000:
            ci.extraer_clip_imou(
                d0,
                d1,
                video_dst,
                foto_respaldo=foto_dst if foto_dst.exists() else None,
                audio_respaldo=audio_lap_dst if audio_lap_dst.exists() else None,
            )

        verif_str = str(ev.get("veredicto_verificador") or "Pendiente de verificación")
        rel_carpeta = f"03_Alertas_Graves/{dia}/{nombre_carpeta}"
        ev["carpeta_expediente"] = rel_carpeta

        dictamen_ia_txt = (
            f"Monto involucrado: S/ {float(ev.get('monto_audio') or 0):.2f} | "
            f"Verificador Dual Imou: {verif_str}.\n"
            f"Puntos de verificación en el reproductor de video (video_imou.mp4 / foto_imou.jpg):\n"
            f"1) Confirmar si hay huésped frente al mostrador entregando efectivo o mostrando pantalla de Yape.\n"
            f"2) Si en la cámara solo aparecen las recepcionistas o el administrador revisando cuentas/pantallas, "
            f"marcar como 'Descartado (falso positivo)' en la hoja de Google Sheets."
        )

        meta_items = [
            ("Fecha y Hora del Evento", hora_full),
            ("Turno Operativo (8am a 8am)", f"{desde_str or dia} → {hasta_str or dia}"),
            ("Habitación Detectada", ev.get("hab") or "Mostrador (sin hab. explícita)"),
            ("Tipo de Interacción", ev.get("tipo") or "-"),
            ("Personal / Voces en Mostrador", ev.get("voz") or "Recepción"),
            ("Monto Hablado en Audio", f"S/ {float(ev.get('monto_audio') or 0):.2f}"),
            ("Monto Registrado en Sistema", f"S/ {float(ev.get('monto_sistema') or 0):.2f}"),
            ("Resultado del Cruce Contable", ev.get("resultado") or "-"),
            ("Verificador Audio (Laptop ↔ Imou)", verif_str),
            ("Archivos Multimedia", "video_imou.mp4 | foto_imou.jpg | audio_laptop.wav"),
        ]

        _generar_ficha_pdf(
            html_a_pdf_fn,
            carpeta_exp,
            f"Expediente de Alerta Grave #{idx} — {hora_full}",
            meta_items,
            str(ev.get("transcripcion") or ""),
            str(ev.get("transcripcion_imou") or ""),
            dictamen_ia_txt,
            foto_dst,
        )
        _generar_reproductor_individual_html(
            carpeta_exp,
            f"Alerta Grave #{idx} — {hora_full} ({ev.get('tipo')})",
            meta_items,
            str(ev.get("transcripcion") or ""),
            str(ev.get("transcripcion_imou") or ""),
            _e(dictamen_ia_txt).replace("\n", "<br>"),
            video_dst.exists(),
            foto_dst.exists(),
            audio_lap_dst.exists(),
            audio_imou_dst.exists(),
        )

        expedientes.append({
            "codigo": nombre_carpeta,
            "hora": hora_full[5:19],
            "hora_full": hora_full,
            "hab": ev.get("hab") or "-",
            "tipo": ev.get("tipo") or "-",
            "monto_audio": float(ev.get("monto_audio") or 0.0),
            "monto_sistema": float(ev.get("monto_sistema") or 0.0),
            "resultado": ev.get("resultado") or "",
            "verificador": verif_str,
            "transcripcion": str(ev.get("transcripcion") or ""),
            "carpeta": carpeta_exp,
            "rel_carpeta": rel_carpeta,
            "tiene_video": video_dst.exists(),
            "tiene_foto": foto_dst.exists(),
            "tiene_audio_lap": audio_lap_dst.exists(),
            "tiene_audio_imou": audio_imou_dst.exists(),
        })

    # 2. Procesar FALTANTE BANCO en CONCILIACION PAGOS YAPE
    for idx_y, fy in enumerate(faltantes_yape, 1):
        hora_an = str(fy.get("hora_anotada") or "").strip()
        t_yape = _hora_anotada_a_dt(hora_an, str(fy.get("fecha_banco") or fy.get("fecha_excel") or ""), dia)
        ev_cerca = None
        if t_yape is not None:
            cand = [(abs((_parse_dt(str(e.get("hora") or "")) - t_yape).total_seconds()), e) for e in eventos
                    if _parse_dt(str(e.get("hora") or "")) is not None]
            cand = [c for c in cand if c[0] <= 600]
            if cand:
                ev_cerca = min(cand, key=lambda x: x[0])[1]
        hora_full_y = ev_cerca["hora"] if ev_cerca else (t_yape.strftime("%Y-%m-%d %H:%M:%S") if t_yape else f"{dia} 08:00:00")
        dia_real_y = hora_full_y[:10]
        hh_mm_ss = hora_full_y[11:19].replace(":", "-") if (ev_cerca or t_yape) else f"YAPE_SIN_HORA_{idx_y}"
        monto_ex = float(fy.get("monto_excel") or 0.0)
        nombre_carpeta = f"{hh_mm_ss}_YAPE_FALTANTE_BANCO_S{int(round(monto_ex))}"
        carpeta_exp = alertas_base / nombre_carpeta
        carpeta_exp.mkdir(parents=True, exist_ok=True)
        carpetas_validas.add(nombre_carpeta)

        for md_old in ("ficha.md", "revision_ia.md"):
            p_md = carpeta_exp / md_old
            if p_md.exists():
                p_md.unlink(missing_ok=True)

        foto_dst = carpeta_exp / "foto_imou.jpg"
        audio_lap_dst = carpeta_exp / "audio_laptop.wav"
        video_dst = carpeta_exp / "video_imou.mp4"

        if ev_cerca:
            if ev_cerca.get("foto_camara") and Path(ev_cerca["foto_camara"]).exists():
                shutil.copy2(ev_cerca["foto_camara"], foto_dst)
            wavs_l = vai.resolver_rutas_wav_evento(dia_real_y, str(ev_cerca.get("archivos") or ""))
            if wavs_l:
                vai.unir_wavs_sesion(wavs_l, audio_lap_dst)
            if not video_dst.exists() and foto_dst.exists() and audio_lap_dst.exists():
                ci.generar_mp4_desde_foto_y_audio(foto_dst, audio_lap_dst, video_dst)

        rel_carpeta = f"03_Alertas_Graves/{dia}/{nombre_carpeta}"
        meta_items_y = [
            ("Concepto / Huésped en Excel (Col E)", str(fy.get("huesped_excel") or "-")),
            ("Hora Anotada en Excel (Col F)", hora_an),
            ("Monto Anotado en Excel (Col D)", f"S/ {monto_ex:.2f}"),
            ("Monto Recibido en Banco (Col C)", f"S/ {float(fy.get('monto_banco') or 0):.2f}"),
            ("Estado Conciliación Yape (Col G)", str(fy.get("cuadre") or "FALTANTE BANCO")),
        ]
        tr_y = ev_cerca.get("transcripcion", "") if ev_cerca else "(Operación anotada como Yape que no figura en el extracto bancario)"
        dictamen_y = (
            f"La hoja CONCILIACION PAGOS YAPE reporta '{fy.get('cuadre')}' por S/ {monto_ex:.2f} "
            f"({fy.get('huesped_excel')}). Verificar con recepción si se cobró en efectivo por error de registro o si no ingresó el Yape."
        )
        _generar_ficha_pdf(
            html_a_pdf_fn,
            carpeta_exp,
            f"Alerta Yape (FALTANTE BANCO) — {dia} ({hora_an})",
            meta_items_y,
            tr_y,
            "",
            dictamen_y,
            foto_dst,
        )
        _generar_reproductor_individual_html(
            carpeta_exp,
            f"Alerta Yape FALTANTE BANCO — S/ {monto_ex:.2f} ({hora_an})",
            meta_items_y,
            tr_y,
            "",
            _e(dictamen_y),
            video_dst.exists(),
            foto_dst.exists(),
            audio_lap_dst.exists(),
            False,
        )

        expedientes.append({
            "codigo": nombre_carpeta,
            "hora": hora_full_y[5:19],
            "hora_full": hora_full_y,
            "hab": "Tienda",
            "tipo": "YAPE_FALTANTE_BANCO",
            "monto_audio": monto_ex,
            "monto_sistema": 0.0,
            "resultado": f"Col G: {fy.get('cuadre')} ({fy.get('huesped_excel')} — S/ {monto_ex:.2f})",
            "verificador": "ALERTA_CONCILIACION_YAPE",
            "transcripcion": tr_y,
            "carpeta": carpeta_exp,
            "rel_carpeta": rel_carpeta,
            "tiene_video": video_dst.exists(),
            "tiene_foto": foto_dst.exists(),
            "tiene_audio_lap": audio_lap_dst.exists(),
            "tiene_audio_imou": False,
        })

    # Eliminar carpetas de alertas antiguas que ya no pertenecen a este turno 8am-8am
    for sub in list(alertas_base.iterdir()):
        if sub.is_dir() and sub.name not in carpetas_validas:
            shutil.rmtree(sub, ignore_errors=True)

    # 3. Generar consola maestra `Reproductor_Alertas_Dia_<dia>.html` y `Resumen_Alertas_Dia_<dia>.pdf`
    cards_reproductor = []
    filas_res_pdf = []
    for i, ex in enumerate(expedientes, 1):
        cod = ex["codigo"]
        vid_html = (
            f"<video controls preload='metadata' poster='{cod}/foto_imou.jpg' style='width:100%;max-height:280px;background:#000;border-radius:6px'>"
            f"<source src='{cod}/video_imou.mp4' type='video/mp4'></video>"
            if ex["tiene_video"]
            else (
                f"<a href='{cod}/foto_imou.jpg' target='_blank'><img src='{cod}/foto_imou.jpg' style='width:100%;max-height:260px;object-fit:contain;border-radius:6px'/></a>"
                if ex["tiene_foto"]
                else "<div style='padding:30px;text-align:center;color:#94a3b8'>Sin video</div>"
            )
        )
        cards_reproductor.append(
            f"<div class='alerta-card'>"
            f"<div class='alerta-head'>"
            f"<span><b>#{i} · {_e(ex['hora'])}</b> · Hab: <b>{_e(ex['hab'])}</b> · {_e(ex['tipo'])} · <b>S/ {ex['monto_audio']:.2f}</b></span>"
            f"<span class='links'>"
            f"<a href='{cod}/Ver_Video_Alerta.html' target='_blank'>🎬 Abrir Reproductor Completo</a> "
            f"<a href='{cod}/Ficha_Alerta.pdf' target='_blank'>📄 Abrir PDF</a> "
            f"<a href='{cod}/foto_imou.jpg' target='_blank'>🖼️ Abrir Foto JPG</a>"
            f"</span></div>"
            f"<div class='alerta-body'>"
            f"<div class='media-col'>{vid_html}</div>"
            f"<div class='info-col'>"
            f"<p><b>Resultado Contable:</b> {_e(ex['resultado'])}</p>"
            f"<p><b>Verificador Audio Imou:</b> <code>{_e(ex['verificador'])}</code></p>"
            f"<p class='tr'>“{_e(ex['transcripcion'][:360])}”</p>"
            f"</div></div></div>"
        )
        foto_p = ex["carpeta"] / "foto_imou.jpg"
        img_cell = (
            f"<img src='{foto_p.resolve().as_uri()}' style='max-width:135px;max-height:85px;border-radius:3px;border:1px solid #64748b'/>"
            if foto_p.exists()
            else "-"
        )
        filas_res_pdf.append(
            f"<tr><td>{i}</td><td><b>{_e(ex['hora'])}</b></td><td><b>{_e(ex['hab'])}</b></td>"
            f"<td>{_e(ex['tipo'])}</td><td>S/ {ex['monto_audio']:.2f}</td>"
            f"<td>{_e(ex['resultado'])}<br><span style='font-size:7.5pt;color:#0f766e'><b>{_e(ex['verificador'])}</b></span></td>"
            f"<td>{img_cell}</td>"
            f"<td style='font-size:7.8pt'><code>{_e(cod)}/video_imou.mp4</code><br><code>{_e(cod)}/Ficha_Alerta.pdf</code></td></tr>"
        )

    reproductor_dia_html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Reproductor de Video de Alertas Graves — Turno {dia} (8:00 AM a 8:00 AM)</title>
<style>
  body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 20px; }}
  .wrap {{ max-width: 1200px; margin: 0 auto; }}
  .top {{ background: #1e293b; border-left: 6px solid #ef4444; padding: 16px 20px; border-radius: 8px; margin-bottom: 18px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; }}
  .top h1 {{ margin: 0; font-size: 17pt; }}
  .top p {{ margin: 4px 0 0 0; color: #cbd5e1; font-size: 10pt; }}
  .btn {{ background: #2563eb; color: #fff; text-decoration: none; padding: 9px 15px; border-radius: 6px; font-weight: 600; font-size: 9.5pt; }}
  .alerta-card {{ background: #1e293b; border: 1px solid #334155; border-radius: 8px; margin-bottom: 16px; overflow: hidden; }}
  .alerta-head {{ background: #334155; padding: 10px 16px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; font-size: 10.5pt; }}
  .links a {{ background: #0f172a; color: #93c5fd; text-decoration: none; padding: 5px 10px; border-radius: 4px; font-size: 9pt; margin-left: 6px; border: 1px solid #475569; }}
  .links a:hover {{ background: #1d4ed8; color: #fff; }}
  .alerta-body {{ display: grid; grid-template-columns: 420px 1fr; gap: 16px; padding: 14px 16px; }}
  @media (max-width: 860px) {{ .alerta-body {{ grid-template-columns: 1fr; }} }}
  .info-col p {{ margin: 6px 0; font-size: 9.8pt; line-height: 1.42; }}
  .info-col .tr {{ background: #0f172a; border-left: 4px solid #3b82f6; padding: 8px 12px; border-radius: 4px; font-style: italic; color: #e2e8f0; margin-top: 10px; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div>
      <h1>🎬 Reproductor de Video — Alertas Graves del Turno {_e(dia)} (8:00 AM a 8:00 AM)</h1>
      <p>Periodo auditado: <b>{_e(desde_str or dia)}</b> → <b>{_e(hasta_str or dia)}</b> · Total alertas graves con video/captura: <b>{len(expedientes)}</b></p>
    </div>
    <a class="btn" href="Resumen_Alertas_Dia_{_e(dia)}.pdf" target="_blank">📄 Abrir Resumen en PDF</a>
  </div>
  {''.join(cards_reproductor) if cards_reproductor else "<div class='alerta-card' style='padding:24px;text-align:center'>✅ Sin alertas graves pendientes en este turno de 8:00 AM a 8:00 AM.</div>"}
</div>
</body>
</html>"""
    (alertas_base / f"Reproductor_Alertas_Dia_{dia}.html").write_text(reproductor_dia_html, encoding="utf-8")

    if html_a_pdf_fn is not None:
        filas_str = "".join(filas_res_pdf) or "<tr><td colspan='8'><i>Sin alertas graves en este turno (8:00 AM a 8:00 AM).</i></td></tr>"
        res_pdf_html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>
        @page {{ size: A4 portrait; margin: 12mm 10mm; }}
        body {{ font-family: 'Segoe UI', Arial, sans-serif; font-size: 9.2pt; color: #1e293b; }}
        h1 {{ font-size: 14pt; color: #991b1b; border-bottom: 2px solid #dc2626; padding-bottom: 4px; margin-top: 0; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 8.5pt; }}
        th {{ background: #991b1b; color: #fff; padding: 5px 6px; text-align: left; border: 1px solid #991b1b; }}
        td {{ border: 1px solid #cbd5e1; padding: 5px 6px; vertical-align: top; }}
        </style></head><body>
        <h1>📂 Resumen de Alertas Graves del Turno {_e(dia)} (8:00 AM a 8:00 AM)</h1>
        <p><b>Periodo:</b> {_e(desde_str or dia)} → {_e(hasta_str or dia)} &nbsp;|&nbsp; <b>Para ver los videos con audio:</b> Abra <code>Reproductor_Alertas_Dia_{_e(dia)}.html</code> o el archivo <code>video_imou.mp4</code> dentro de cada carpeta.</p>
        <table>
          <thead><tr><th>#</th><th>Fecha y Hora</th><th>Hab.</th><th>Tipo</th><th>Monto S/</th><th>Resultado y Verificador Imou</th><th>Captura Cámara JPG</th><th>Archivos del Expediente</th></tr></thead>
          <tbody>{filas_str}</tbody>
        </table>
        </body></html>"""
        try:
            html_a_pdf_fn(res_pdf_html, alertas_base / f"Resumen_Alertas_Dia_{dia}.pdf")
        except Exception:
            pass

    return expedientes
