#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Exportador por TURNO OPERATIVO (8:00 AM a 8:00 AM) de Informes, Conversaciones, Videos y Revisiones IA
en `C:\\Users\\ovalo mariategui\\Downloads\\Auditor AU`:
  - `01_Informes_Diarios\\<AAAA-MM>\\`         -> Informe_Dia_YYYY-MM-DD (.pdf y .xlsx para Google Sheets)
  - `02_Conversaciones_Completas\\<AAAA-MM>\\` -> Conversaciones_Completas_Dia_YYYY-MM-DD (.pdf)
  - `03_Alertas_Graves\\<AAAA-MM-DD>\\`        -> Reproductor_Alertas_Dia_YYYY-MM-DD.html + Resumen_Alertas_Dia_YYYY-MM-DD.pdf
                                                  + subcarpetas con video_imou.mp4, foto_imou.jpg, Ver_Video_Alerta.html y Ficha_Alerta.pdf
  - `04_Revision_IA\\<AAAA-MM>\\`              -> Revision_IA_YYYY-MM-DD.pdf

Reglas clave:
- Cada día operativo `YYYY-MM-DD` abarca desde las `08:00:00` del día `YYYY-MM-DD` hasta las `07:59:59` del día siguiente (`YYYY-MM-DD + 1`).
- Todos los archivos entregables están en formatos directos para el usuario (`.pdf`, `.xlsx` para Google Sheets, `.jpg`, `.mp4` y reproductor `.html`), sin dejar archivos `.md` crudos.
- 100% SOLO LECTURA sobre `ovalo_local.db` (no modifica hojas de Google ni tablas operativas).
"""
from __future__ import annotations

import datetime as dt
import html
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE_DIR = Path(r"C:\Recepcion_Ovalo_Local")
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import camara_imou as ci  # noqa: E402
import expediente_alertas as ea  # noqa: E402
import informe_corroboracion as ic  # noqa: E402
from config import DB_PATH, NOMBRE_HOTEL  # noqa: E402

DOWNLOADS_AU_DIR = Path(r"C:\Users\ovalo mariategui\Downloads\Auditor AU")
MIRROR_AU_DIR = Path(r"C:\Hostal_Sistema\descargas_recepcion\Auditor AU")
CHROME_PATHS = [
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
]

CSS_PDF = """
@page { size: A4 portrait; margin: 12mm 10mm; }
body {
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: 9.3pt;
    line-height: 1.38;
    color: #1f2937;
    margin: 0;
    padding: 0;
}
h1 {
    font-size: 14.5pt;
    color: #1e3a8a;
    border-bottom: 2px solid #1e3a8a;
    padding-bottom: 4px;
    margin-top: 0;
}
h2 {
    font-size: 11.5pt;
    color: #1e40af;
    margin-top: 14px;
    margin-bottom: 6px;
    border-bottom: 1px solid #cbd5e1;
    padding-bottom: 2px;
}
h3 {
    font-size: 10pt;
    color: #0f172a;
    margin-top: 10px;
    margin-bottom: 3px;
}
.meta-box {
    background: #f8fafc;
    border: 1px solid #cbd5e1;
    border-left: 4px solid #1e3a8a;
    padding: 8px 12px;
    border-radius: 4px;
    margin-bottom: 10px;
}
.alert-note {
    background: #fef2f2;
    border: 1px solid #fecaca;
    border-left: 4px solid #dc2626;
    color: #991b1b;
    padding: 6px 10px;
    font-weight: 600;
    margin-bottom: 12px;
}
table {
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 12px;
    font-size: 8.2pt;
    table-layout: fixed;
}
th {
    background: #1e3a8a;
    color: #ffffff;
    text-align: left;
    padding: 5px 6px;
    font-weight: 600;
    border: 1px solid #1e3a8a;
    word-wrap: break-word;
}
td {
    padding: 4px 6px;
    border: 1px solid #cbd5e1;
    vertical-align: top;
    word-wrap: break-word;
    overflow-wrap: break-word;
}
tr:nth-child(even) td { background: #f8fafc; }
.prio-alta td { background: #fee2e2 !important; }
.prio-media td { background: #fef3c7 !important; }
.dem-perdida td { background: #fee2e2 !important; }
.dem-sust td { background: #fef3c7 !important; }
.dem-ok td { background: #dcfce7 !important; }
.conv-card {
    border: 1px solid #cbd5e1;
    border-left: 4px solid #2563eb;
    border-radius: 4px;
    padding: 7px 10px;
    margin-bottom: 8px;
    page-break-inside: avoid;
    background: #ffffff;
}
.conv-card.operativo {
    border-left-color: #d97706;
    background: #fffbeb;
}
.conv-card.alerta {
    border-left-color: #dc2626;
    background: #fef2f2;
}
.conv-header {
    font-weight: 700;
    color: #0f172a;
    font-size: 9.5pt;
    margin-bottom: 3px;
}
.conv-meta {
    font-size: 8.2pt;
    color: #475569;
    margin-bottom: 4px;
}
.conv-text {
    font-size: 9.2pt;
    color: #111827;
    background: #f1f5f9;
    padding: 5px 8px;
    border-radius: 3px;
    font-style: italic;
    white-space: pre-wrap;
    word-wrap: break-word;
}
.badge {
    display: inline-block;
    padding: 1px 5px;
    border-radius: 3px;
    font-size: 7.8pt;
    font-weight: 700;
}
.b-info { background: #475569; color: #fff; }
"""


def encontrar_chrome() -> Path:
    for p in CHROME_PATHS:
        if p.exists():
            return p
    raise FileNotFoundError("No se encontró Google Chrome para exportar a PDF.")


def html_a_pdf(html_content: str, pdf_path: Path) -> None:
    chrome = encontrar_chrome()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_html = pdf_path.with_suffix(".tmp_render.html")
    tmp_html.write_text(html_content, encoding="utf-8")
    try:
        subprocess.run(
            [
                str(chrome),
                "--headless",
                "--disable-gpu",
                "--no-pdf-header-footer",
                f"--print-to-pdf={pdf_path}",
                str(tmp_html.resolve()),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
        )
    finally:
        if tmp_html.exists():
            try:
                tmp_html.unlink()
            except Exception:
                pass


def e(val: Any) -> str:
    return html.escape(str(val if val is not None else ""))


def rango_turno_dia(dia: str) -> Tuple[str, str]:
    """
    Devuelve la ventana de 24 horas del turno operativo para `dia` (`YYYY-MM-DD`):
    desde `YYYY-MM-DD 08:00:00` hasta `(YYYY-MM-DD + 1 día) 07:59:59`.
    """
    d0 = dt.date.fromisoformat(dia)
    d1 = d0 + dt.timedelta(days=1)
    return f"{d0.isoformat()} 08:00:00", f"{d1.isoformat()} 07:59:59"


def _img_html(ruta_foto: str, max_w: str = "130px") -> str:
    """Genera etiqueta <img> con URI file:/// para que Chrome Headless incruste la foto de cámara Imou en el PDF."""
    if not ruta_foto:
        return "<span style='font-size:7.5pt;color:#64748b'>Sin captura</span>"
    p = Path(ruta_foto)
    if not p.exists():
        return f"<span style='font-size:7.5pt;color:#64748b'>{e(p.name)}</span>"
    uri = p.resolve().as_uri()
    return (
        f"<div style='text-align:center'>"
        f"<img src='{uri}' style='max-width:{max_w};max-height:85px;border-radius:3px;border:1px solid #94a3b8;display:block;margin:0 auto 2px auto'/>"
        f"<span style='font-size:6.8pt;color:#475569'>{e(p.name)}</span>"
        f"</div>"
    )


def _es_texto_real(tr: str) -> bool:
    t = (tr or "").strip().lower()
    if not t:
        return False
    if "pendiente de transcripci" in t or "sin voz inteligible" in t:
        return False
    return True


def obtener_dias_disponibles() -> List[str]:
    """
    Obtiene los días operativos (de 08:00 AM a 08:00 AM del día siguiente) con grabaciones en BD.
    Todo clip grabado entre 00:00:00 y 07:59:59 pertenece al día operativo anterior.
    """
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30)
    dias = [
        r[0]
        for r in conn.execute(
            """
            SELECT DISTINCT
                CASE WHEN substr(fecha_hora_inicio, 12, 8) < '08:00:00'
                     THEN date(substr(fecha_hora_inicio, 1, 10), '-1 day')
                     ELSE substr(fecha_hora_inicio, 1, 10)
                END AS dia_op
            FROM auditoria_audio_noche
            WHERE fecha_hora_inicio != ''
            ORDER BY dia_op ASC
            """
        ).fetchall()
        if r[0]
    ]
    conn.close()
    return dias


def obtener_clips_dia(dia: str) -> List[Dict[str, Any]]:
    desde, hasta = rango_turno_dia(dia)
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    filas = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM auditoria_audio_noche WHERE fecha_hora_inicio BETWEEN ? AND ? ORDER BY fecha_hora_inicio ASC",
            (desde, hasta),
        ).fetchall()
    ]
    conn.close()
    return filas


def generar_informe_dia(dia: str, base_au_dir: Path) -> Dict[str, Path]:
    desde, hasta = rango_turno_dia(dia)
    prefijo = f"Informe_Dia_{dia}"
    informes_dir = base_au_dir / "01_Informes_Diarios" / dia[:7]
    informes_dir.mkdir(parents=True, exist_ok=True)

    # Eliminar archivo .md antiguo si existía para dejar únicamente .pdf y .xlsx
    md_old = informes_dir / f"{prefijo}.md"
    if md_old.exists():
        md_old.unlink(missing_ok=True)

    datos = ic.recolectar(desde, hasta)
    aplicar_veredictos(datos)
    expedientes_dia = ea.generar_expedientes_dia(
        dia,
        datos,
        base_au_dir,
        desde_str=desde,
        hasta_str=hasta,
        html_a_pdf_fn=html_a_pdf,
    )
    ev = datos["eventos"]
    altas = [x for x in ev if x["prioridad"] == "1-ALTA"]
    medias = [x for x in ev if x["prioridad"] == "2-MEDIA"]
    infos = [x for x in ev if x["prioridad"] == "3-INFORMATIVO"]

    # Ya no se genera .xlsx (el usuario trabaja con PDF); se elimina el de corridas anteriores
    ruta_xlsx_old = informes_dir / f"{prefijo}.xlsx"
    if ruta_xlsx_old.exists():
        ruta_xlsx_old.unlink(missing_ok=True)

    fuentes_str = ", ".join(f"{k}: {v}" for k, v in sorted(datos["fuentes"].items()))
    rc = datos.get("resumen_caja_efectivo") or {}
    relevos_h = datos.get("relevos_horario") or []
    demanda_h = datos.get("demanda_habitaciones") or []
    rd = datos.get("resumen_demanda") or {}

    recojos_det_str = "; ".join(
        f"{x['hora'].strftime('%d/%m %H:%M:%S')} S/ {x['monto']:.2f} ({x['descripcion']})"
        for x in rc.get("recojos_detalle", [])
    ) or "Sin recojo registrado en este turno"

    def _filas_ev(lista: List[Dict[str, Any]], cls_tr: str, con_foto: bool = True) -> str:
        if not lista:
            return f"<tr><td colspan='{9 if con_foto else 8}'><i>Sin eventos en esta categoría en este turno (8:00 AM a 8:00 AM).</i></td></tr>"
        out = []
        for i, x in enumerate(lista, 1):
            celda_foto = f"<td>{_img_html(x.get('foto_camara', ''), '125px')}</td>" if con_foto else ""
            verif_txt = f"<br><span style='font-size:7.2pt;color:#0f766e'><b>Verif. Imou:</b> {e(x.get('veredicto_verificador', ''))}</span>" if x.get("veredicto_verificador") else ""
            exp_txt = f"<br><span style='font-size:7.0pt;color:#991b1b'><b>Reproductor:</b> <code>{e(x.get('carpeta_expediente', ''))}/Ver_Video_Alerta.html</code></span>" if x.get("carpeta_expediente") else ""
            out.append(
                f"<tr class='{cls_tr}'>"
                f"<td>{i}</td>"
                f"<td><b>{e(x['hora'][5:19])}</b><br><span style='font-size:7.2pt;color:#475569'>{e(x['fuente'])}</span></td>"
                f"<td><b>{e(x['hab'] or '-')}</b></td>"
                f"<td>{e(x['tipo'])}<br><span style='font-size:7.2pt'>{e(x['voz'])}</span></td>"
                f"<td>S/ {x['monto_audio']:.2f}<br><span style='font-size:7.2pt;color:#475569'>Sist: S/ {x['monto_sistema']:.2f}</span></td>"
                f"<td>{e(x['resultado'])}{verif_txt}{exp_txt}</td>"
                f"<td><i>{e((x['transcripcion'] or '')[:340])}</i></td>"
                f"{celda_foto}"
                f"<td style='font-size:7.2pt'>{e(x['archivos'])}</td>"
                f"</tr>"
            )
        return "".join(out)

    # Tabla de Demanda de Habitaciones, Rechazos por Ocupación y Sustitución
    indisp_perdidos_str = "; ".join(f"{k}: {v}" for k, v in (rd.get("indispensables_perdidos") or {}).items()) or "Ninguno en este turno"
    sust_aceptadas_str = "; ".join(f"{k}: {v}" for k, v in (rd.get("sustituciones_aceptadas") or {}).items()) or "Ninguna en este turno"

    filas_demanda_html = []
    for idx_d, d in enumerate(demanda_h, 1):
        dec = str(d.get("decision_cliente") or "")
        cls_d = "dem-perdida" if dec.startswith("1-") else ("dem-sust" if dec.startswith("2-") else ("dem-ok" if dec.startswith("4-") else ""))
        filas_demanda_html.append(
            f"<tr class='{cls_d}'>"
            f"<td>{idx_d}</td>"
            f"<td><b>{e(d['hora'][5:19])}</b></td>"
            f"<td><b>{e(d['tipo_solicitado'])}</b><br><span style='font-size:7.2pt;color:#475569'>Ocupado: {e(d['estaba_ocupado'])}</span></td>"
            f"<td><b>{e(d['requisito_indispensable'])}</b></td>"
            f"<td>{e(d['alternativa_ofrecida'])}</td>"
            f"<td><b>{e(dec)}</b><br><span style='font-size:7.3pt'>{e(d['explicacion'])}</span></td>"
            f"<td><i>{e(d['transcripcion'][:260])}</i></td>"
            f"<td>{_img_html(d.get('foto_camara', ''), '105px')}</td>"
            f"</tr>"
        )
    filas_demanda_str = "".join(filas_demanda_html) or "<tr><td colspan='8'><i>Sin consultas de habitaciones registradas en este turno.</i></td></tr>"

    filas_relevos_html = "".join(
        f"<tr><td><b>{e(rh['empleado'])}</b></td><td>{e(rh['fecha_dmy'])}</td><td><b>{e(rh['e1'])}</b></td>"
        f"<td><b>{e(rh['s1'])}</b></td><td><b>{e(rh['e2'])}</b></td><td><b>{e(rh['s2'])}</b></td>"
        f"<td>{e(rh['estado'])}</td><td>{_img_html(rh.get('foto_camara', ''), '115px')}</td></tr>"
        for rh in relevos_h
    ) or "<tr><td colspan='8'><i>Sin marcaciones de horario registradas en este turno.</i></td></tr>"

    filas_chk = "".join(
        f"<tr><td>{e(est['hora'].strftime('%d/%m %H:%M:%S'))}</td><td><b>{e(est['hab'])}</b></td>"
        f"<td>{e(est['huesped'])}</td><td>{e(est['dni'])}</td><td>S/ {est['tarifa']:.2f}</td>"
        f"<td>S/ {est['pagado']:.2f} ({e(est.get('medio', '-'))})</td><td>{e(est['audio_cerca'] or 'SIN AUDIO CERCANO')}</td>"
        f"<td>{_img_html(est.get('foto_camara', ''), '105px')}</td></tr>"
        for est in datos["estancias"]
    ) or "<tr><td colspan='8'><i>Sin check-ins registrados en este turno.</i></td></tr>"

    filas_yape_html = "".join(
        f"<tr><td>{e(y['fecha_banco'])}</td><td>{e(y['origen_banco'])}</td><td>S/ {y['monto_banco']:.2f}</td>"
        f"<td>S/ {y['monto_excel']:.2f}</td><td>{e((y.get('huesped_excel') or '').replace(chr(10), ' + '))}</td>"
        f"<td>{e((y.get('hora_anotada') or '').replace(chr(10), ' / '))}</td><td><b>{e(y['cuadre'])}</b></td></tr>"
        for y in datos.get("conciliacion_yape", [])
    ) or "<tr><td colspan='7'><i>Sin operaciones Yape en este turno.</i></td></tr>"

    filas_cuadre_relevos = "".join(
        f"<tr><td><b>{e(c['hora'].strftime('%d/%m %H:%M'))}</b></td><td>S/ {c['efectivo_esperado_en_caja']:.2f}</td><td>{_img_html(c.get('foto_camara', ''), '110px')}</td></tr>"
        for c in rc.get("cuadre_relevos", [])
    ) or "<tr><td colspan='3'><i>Sin relevos registrados en horario_registros para este turno.</i></td></tr>"
    if rc.get("saldo_panel_actual") is not None:
        dif_p = rc.get("diferencia_vs_panel") or 0.0
        estado_p = "🚨 NO CUADRA — revisar" if rc.get("alerta_caja") else "✅ Cuadra"
        caja_panel_fila = (
            f"<tr><td><b>Saldo en Panel de Disponibilidad (turno en curso)</b></td><td><b>S/ {rc['saldo_panel_actual']:.2f}</b></td>"
            f"<td><b>{estado_p}</b> — diferencia contra lo esperado: S/ {dif_p:+.2f}</td></tr>"
        )
    else:
        caja_panel_fila = ("<tr><td><b>Saldo en Panel de Disponibilidad</b></td><td>No disponible</td>"
                           "<td>El panel solo guarda el saldo de hoy; para turnos cerrados se usa el cálculo desde Registro de Pagos.</td></tr>")

    html_doc = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{CSS_PDF}</style></head><body>
    <h1>🕵️‍♂️ Informe de Auditoría y Corroboración — Turno {e(dia)} (8:00 AM a 8:00 AM) — {e(NOMBRE_HOTEL)}</h1>
    <div class="meta-box">
        <b>Turno Operativo Auditado (8:00 AM a 8:00 AM):</b> <b>{e(desde)}</b> &nbsp;→&nbsp; <b>{e(hasta)}</b> &nbsp;|&nbsp;
        <b>Generado:</b> {dt.datetime.now().strftime('%d/%m/%Y %H:%M')}<br>
        <b>Clips grabados en el turno:</b> {len(datos['clips'])} ({e(fuentes_str)}) &nbsp;|&nbsp;
        <b>Eventos del turno:</b> {len(ev)} (Prioridad 1-ALTA: <b>{len(altas)}</b>, 2-MEDIA: <b>{len(medias)}</b>, 3-INFORMATIVO: {len(infos)})<br>
        <b>Demanda de Habitaciones en Mostrador:</b> <b>{rd.get('total_consultas', 0)} consultas</b>
        (Ventas perdidas por ocupación: <b>{rd.get('se_van_por_ocupacion', 0)}</b> | Aceptaron sustitución menor/mayor: <b>{rd.get('aceptan_sustitucion', 0)}</b> | Consultaron precio/horas y se fueron: <b>{rd.get('se_van_por_precio', 0)}</b> | Ingresaron directo: <b>{rd.get('ingresan_directo', 0)}</b>)<br>
        <b>Efectivo esperado en caja al cierre del turno:</b> <b>S/ {rc.get('efectivo_esperado_final', 0.0):.2f}</b> &nbsp;|&nbsp;
        <b>Recojos de Efectivo del Turno:</b> <b>S/ {rc.get('total_recojos_dia', 0.0):.2f}</b> ({e(recojos_det_str)})
    </div>
    <div class="alert-note">
        IMPORTANTE: Este informe cubre exactamente de 8:00 AM del {e(dia)} a 8:00 AM del día siguiente. Para ver los videos con audio use <code>03_Alertas_Graves/{e(dia)}/Reproductor_Alertas_Dia_{e(dia)}.html</code>.
    </div>

    <h2>1. Prioridad 1-ALTA (Alertas principales con Captura Cámara Imou y Video MP4)</h2>
    <table>
        <thead><tr><th style="width:3%">#</th><th style="width:9%">Fecha/Hora</th><th style="width:5%">Hab</th><th style="width:11%">Tipo / Voz</th><th style="width:8%">Monto</th><th style="width:21%">Resultado Automático</th><th style="width:21%">Transcripción</th><th style="width:14%">Cámara Imou</th><th style="width:8%">Audio</th></tr></thead>
        <tbody>{_filas_ev(altas, 'prio-alta', True)}</tbody>
    </table>

    <h2>2. Demanda de Habitaciones: ¿Qué Piden los Clientes, Cuándo Aceptan Otra y Cuándo Se Van por Ocupación?</h2>
    <table>
        <thead><tr><th style="width:32%">Indicador de Demanda en Mostrador</th><th style="width:10%">Cantidad</th><th style="width:58%">Detalle / Tipo de Habitación Indispensable o Sustitución</th></tr></thead>
        <tbody>
            <tr class="dem-perdida"><td><b>1. Se Retiran porque su Tipo Estaba Ocupado (Venta Perdida)</b></td><td><b>{rd.get('se_van_por_ocupacion', 0)}</b></td><td><b>Requisito indispensable que buscaban:</b> {e(indisp_perdidos_str)}</td></tr>
            <tr class="dem-sust"><td><b>2. Aceptan Habitación Alternativa (Menor o Mayor Precio) y Se Quedan</b></td><td><b>{rd.get('aceptan_sustitucion', 0)}</b></td><td><b>Sustituciones aceptadas:</b> {e(sust_aceptadas_str)}</td></tr>
            <tr><td><b>3. Solo Consultan Precio / Por Horas y Se Retiran</b></td><td><b>{rd.get('se_van_por_precio', 0)}</b></td><td>Clientes que preguntaron tarifa o alquiler por horas y no ingresaron</td></tr>
            <tr class="dem-ok"><td><b>4. Ingresan Directo al Tipo de Habitación Solicitado</b></td><td><b>{rd.get('ingresan_directo', 0)}</b></td><td>Había disponibilidad inmediata del tipo pedido y se hospedaron</td></tr>
        </tbody>
    </table>
    <table>
        <thead><tr><th style="width:3%">#</th><th style="width:9%">Fecha/Hora</th><th style="width:13%">Tipo Solicitado</th><th style="width:15%">Requisito Indispensable</th><th style="width:14%">Alternativa Ofrecida</th><th style="width:19%">Reacción / Decisión del Cliente</th><th style="width:16%">Conversación</th><th style="width:11%">Cámara Imou</th></tr></thead>
        <tbody>{filas_demanda_str}</tbody>
    </table>

    <h2>3. Prioridad 2-MEDIA (Verificaciones, Tienda, Relevo Vitrina y Mantenimiento)</h2>
    <table>
        <thead><tr><th style="width:3%">#</th><th style="width:9%">Fecha/Hora</th><th style="width:5%">Hab</th><th style="width:11%">Tipo / Voz</th><th style="width:8%">Monto</th><th style="width:21%">Resultado Automático</th><th style="width:21%">Transcripción</th><th style="width:14%">Cámara Imou</th><th style="width:8%">Audio</th></tr></thead>
        <tbody>{_filas_ev(medias, 'prio-media', True)}</tbody>
    </table>

    <h2>4. Relevos de Turno (horario_registros) y Cuadre de Caja Efectivo (PanelDisponibilidad)</h2>
    <table>
        <thead><tr><th style="width:38%">Concepto de Caja Efectivo (calculado desde Registro de Pagos de la web app local)</th><th style="width:14%">Monto S/</th><th style="width:48%">Detalle / Observación</th></tr></thead>
        <tbody>
            <tr><td>Efectivo que debía haber al iniciar el turno (8:00 AM)</td><td>S/ {rc.get('efectivo_inicial_turno', 0.0):.2f}</td><td>Acumulado desde el último recojo previo ({e(rc.get('ultimo_recojo_previo', '-'))})</td></tr>
            <tr><td>(+) Ingresos Efectivo en Registro de Pagos (Inicial, Posterior, Reingreso, Ajustes − Extornos)</td><td>S/ {rc.get('efec_estancias_dia', 0.0):.2f}</td><td>Tabla <code>pagos</code>, medio Efectivo, entre 8:00 AM y 8:00 AM</td></tr>
            <tr><td>(+) Ingresos Efectivo por Ventas Tienda</td><td>S/ {rc.get('efec_tienda_dia', 0.0):.2f}</td><td>Ventas de vitrina en efectivo</td></tr>
            <tr><td>(-) Gastos Operativos en Efectivo</td><td>S/ {rc.get('gastos_operativos_efec_dia', 0.0):.2f}</td><td>Pagos/salidas operativas de caja en efectivo</td></tr>
            <tr><td><b>(-) Recojos de Efectivo por Admin (RECOJO EFECTIVO)</b></td><td><b>S/ {rc.get('total_recojos_dia', 0.0):.2f}</b></td><td>{e(recojos_det_str)}</td></tr>
            <tr><td><b>(=) Efectivo que debe haber en caja al cierre del turno</b></td><td><b>S/ {rc.get('efectivo_esperado_final', 0.0):.2f}</b></td><td>Variación neta del turno: S/ {rc.get('flujo_neto_dia', 0.0):.2f}</td></tr>
            {caja_panel_fila}
        </tbody>
    </table>
    <table>
        <thead><tr><th style="width:20%">Relevo (hora de entrada)</th><th style="width:25%">Efectivo que debe haber en caja</th><th style="width:55%">Cámara Imou en el relevo</th></tr></thead>
        <tbody>{filas_cuadre_relevos}</tbody>
    </table>
    <table>
        <thead><tr><th style="width:16%">Personal</th><th style="width:11%">Fecha</th><th style="width:10%">Entrada 1</th><th style="width:10%">Salida 1</th><th style="width:10%">Entrada 2</th><th style="width:10%">Salida 2</th><th style="width:15%">Estado</th><th style="width:18%">Cámara Imou Relevo</th></tr></thead>
        <tbody>{filas_relevos_html}</tbody>
    </table>

    <h2>5. Check-ins Registrados en el Turno (8am a 8am) vs. Conversación de Audio y Cámara (±15 min)</h2>
    <table>
        <thead><tr><th style="width:12%">Hora Sistema</th><th style="width:5%">Hab.</th><th style="width:23%">Huésped</th><th style="width:10%">DNI</th><th style="width:8%">Tarifa</th><th style="width:11%">Pagado</th><th style="width:16%">Audio más cercano</th><th style="width:15%">Cámara Imou</th></tr></thead>
        <tbody>{filas_chk}</tbody>
    </table>

    <h2>6. Conciliación Pagos Yape del Turno (Cols A-C Banco ↔ Cols D-F Estancia ↔ Col G Cuadre)</h2>
    <table>
        <thead><tr><th style="width:15%">Col A: Fecha Banco</th><th style="width:17%">Col B: Origen Banco</th><th style="width:8%">Col C: S/</th><th style="width:8%">Col D: S/</th><th style="width:24%">Col E: Huésped / Tienda</th><th style="width:10%">Col F: Hora</th><th style="width:18%">Col G: Cuadre</th></tr></thead>
        <tbody>{filas_yape_html}</tbody>
    </table>

    <h2>7. Eventos Informativos (Cotizaciones y Cruces Verificados OK)</h2>
    <table>
        <thead><tr><th style="width:4%">#</th><th style="width:9%">Fecha/Hora</th><th style="width:5%">Hab</th><th style="width:14%">Tipo / Voz</th><th style="width:9%">Monto</th><th style="width:24%">Resultado Automático</th><th style="width:25%">Transcripción</th><th style="width:10%">Audio</th></tr></thead>
        <tbody>{_filas_ev(infos, '', False)}</tbody>
    </table>
    </body></html>"""

    ruta_pdf = informes_dir / f"{prefijo}.pdf"
    html_a_pdf(html_doc, ruta_pdf)
    return {"pdf": ruta_pdf, "datos": datos}


def generar_conversaciones_dia(dia: str, base_au_dir: Path) -> Dict[str, Path]:
    desde, hasta = rango_turno_dia(dia)
    clips = obtener_clips_dia(dia)
    prefijo = f"Conversaciones_Completas_Dia_{dia}"
    conv_dir = base_au_dir / "02_Conversaciones_Completas" / dia[:7]
    conv_dir.mkdir(parents=True, exist_ok=True)

    # Eliminar archivo .md antiguo si existía para dejar únicamente el .pdf
    md_old = conv_dir / f"{prefijo}.md"
    if md_old.exists():
        md_old.unlink(missing_ok=True)

    con_habla_real = [c for c in clips if _es_texto_real(c.get("transcripcion") or "")]
    sin_habla = len(clips) - len(con_habla_real)

    cards_html = []
    for idx, c in enumerate(con_habla_real, 1):
        hora = c.get("fecha_hora_inicio") or ""
        dur = float(c.get("duracion_seg") or 0.0)
        wav_name = Path(c.get("archivo_wav") or "").name
        hablantes = c.get("hablantes_detectados") or "Recepción"
        tipo = c.get("tipo_interaccion") or "CHARLA_INTERNA"
        hab = c.get("hab_detectada") or "-"
        dni = c.get("dni_detectado") or "-"
        huesp = c.get("huesped_detectado") or ""
        precio = float(c.get("precio_hablado") or 0.0)
        metodo = c.get("metodo_pago_hablado") or "-"
        est_cruce = c.get("estado_cruce") or ""
        det_cruce = c.get("detalle_cruce") or ""
        fuente = c.get("fuente_transcripcion") or "GEMINI"
        tr = (c.get("transcripcion") or "").strip()

        cls_card = "conv-card"
        if "ALERTA" in est_cruce:
            cls_card += " alerta"
        elif tipo not in ("CHARLA_INTERNA", "AUDIO_FONDO"):
            cls_card += " operativo"

        datos_extra = []
        if hab != "-":
            datos_extra.append(f"<b>Hab:</b> {e(hab)}")
        if dni != "-":
            datos_extra.append(f"<b>DNI:</b> {e(dni)} {e(huesp)}")
        if precio > 0:
            datos_extra.append(f"<b>Monto audio:</b> S/ {precio:.2f}")
        if metodo != "-":
            datos_extra.append(f"<b>Medio:</b> {e(metodo)}")
        datos_str = " &nbsp;|&nbsp; ".join(datos_extra) if datos_extra else "Sin cobro/habitación mencionada"

        cards_html.append(
            f"<div class='{cls_card}'>"
            f"<div class='conv-header'>#{idx} &nbsp;·&nbsp; 🕒 {e(hora)} ({dur:.1f}s) &nbsp;·&nbsp; "
            f"<span class='badge b-info'>{e(tipo)}</span> &nbsp;·&nbsp; 👤 {e(hablantes)}</div>"
            f"<div class='conv-meta'>📁 <code>{e(wav_name)}</code> ({e(fuente)}) &nbsp;|&nbsp; {datos_str}<br>"
            f"🔎 <b>Cruce:</b> {e(det_cruce or est_cruce)}</div>"
            f"<div class='conv-text'>“{e(tr)}”</div>"
            f"</div>"
        )

    html_doc = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{CSS_PDF}</style></head><body>
    <h1>🎙️ Conversaciones Completas del Turno {e(dia)} (8:00 AM a 8:00 AM) — {e(NOMBRE_HOTEL)}</h1>
    <div class="meta-box">
        <b>Turno Operativo (8:00 AM a 8:00 AM):</b> <b>{e(desde)}</b> &nbsp;→&nbsp; <b>{e(hasta)}</b> &nbsp;|&nbsp;
        <b>Generado:</b> {dt.datetime.now().strftime('%d/%m/%Y %H:%M')}<br>
        <b>Total clips grabados en el turno:</b> {len(clips)} &nbsp;|&nbsp;
        <b>Conversaciones con voz hablada transcrita (solo de este turno):</b> <b>{len(con_habla_real)}</b>
        (se omiten {sin_habla} clips de ruido ambiental/sin voz inteligible)
    </div>
    {''.join(cards_html)}
    </body></html>"""

    ruta_pdf = conv_dir / f"{prefijo}.pdf"
    html_a_pdf(html_doc, ruta_pdf)
    return {"pdf": ruta_pdf}


def generar_guia_pdf(base_dir: Path) -> Path:
    """Genera `Guia_Auditor_AU.pdf` en la raíz de `Downloads\\Auditor AU` y elimina `LEEME.md`."""
    leeme_md = base_dir / "LEEME.md"
    if leeme_md.exists():
        leeme_md.unlink(missing_ok=True)
    html_guia = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{CSS_PDF}</style></head><body>
    <h1>📂 Guía de Uso — Auditoría de Recepción (Turno 8:00 AM a 8:00 AM) — {e(NOMBRE_HOTEL)}</h1>
    <div class="meta-box">
        Cada día operativo (por ejemplo, <b>2026-11-07</b>) comprende desde las <b>08:00:00 AM de ese día</b> hasta las <b>08:00:00 AM del día siguiente</b>, coincidiendo exactamente con el relevo de mañana de recepción.
    </div>
    <h2>1. 01_Informes_Diarios/&lt;AAAA-MM&gt;/ (Se conserva permanentemente)</h2>
    <ul>
        <li><b><code>Informe_Dia_AAAA-MM-DD.pdf</code></b>: Informe ejecutivo listo para abrir en PDF, con capturas de la Cámara Imou incrustadas, <b>Análisis de Demanda de Habitaciones (quiénes aceptan otra habitación y quiénes se retiran cuando está ocupado)</b>, relevos de turno y cuadre de Caja Efectivo.</li>
    </ul>
    <h2>2. 02_Conversaciones_Completas/&lt;AAAA-MM&gt;/ (Se conserva permanentemente)</h2>
    <ul>
        <li><b><code>Conversaciones_Completas_Dia_AAAA-MM-DD.pdf</code></b>: Todas las conversaciones habladas del turno de 8:00 AM a 8:00 AM en documento PDF.</li>
    </ul>
    <h2>3. 03_Alertas_Graves/&lt;AAAA-MM-DD&gt;/ (Retención automática: 7 días)</h2>
    <ul>
        <li><b><code>Reproductor_Alertas_Dia_AAAA-MM-DD.html</code></b>: Haga doble clic para abrir el <b>Reproductor de Video y Audio</b> en la laptop y ver todos los clips de las alertas graves del turno en una sola pantalla.</li>
        <li><b><code>Resumen_Alertas_Dia_AAAA-MM-DD.pdf</code></b>: Resumen en PDF de las alertas graves del turno.</li>
        <li>Dentro de cada subcarpeta de alerta encontrará:
            <ul>
                <li><b><code>Ver_Video_Alerta.html</code></b>: Reproductor de video individual de esa alerta.</li>
                <li><b><code>video_imou.mp4</code></b>: Video con audio reproducible en el reproductor de Windows.</li>
                <li><b><code>foto_imou.jpg</code></b>: Imagen Full HD (1920×1080) en formato JPG.</li>
                <li><b><code>Ficha_Alerta.pdf</code></b>: Ficha detallada en PDF con foto e interpretación.</li>
            </ul>
        </li>
    </ul>
    <h2>4. 04_Revision_IA/&lt;AAAA-MM&gt;/ (Se conserva permanentemente)</h2>
    <ul>
        <li><b><code>Revision_IA_AAAA-MM-DD.pdf</code></b>: Dictamen consolidado en PDF tras la inspección visual (cámara Imou) y auditiva de las alertas graves del turno.</li>
    </ul>
    </body></html>"""
    pdf_guia = base_dir / "Guia_Auditor_AU.pdf"
    html_a_pdf(html_guia, pdf_guia)
    return pdf_guia


VEREDICTOS_JSON = BASE_DIR / "data" / "veredictos_revision.json"


def cargar_veredictos() -> Dict[str, Dict[str, str]]:
    try:
        return json.loads(VEREDICTOS_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}


def aplicar_veredictos(datos: Dict[str, Any]) -> None:
    """Aplica veredictos guardados en data/veredictos_revision.json (DESCARTADO baja la alerta a informativa)."""
    ver = cargar_veredictos()
    for ev in datos.get("eventos", []):
        v = ver.get(str(ev.get("hora") or "")[:19])
        if v:
            ev["veredicto_manual"] = v
            if str(v.get("veredicto", "")).upper() == "DESCARTADO" and ev.get("prioridad") == "1-ALTA":
                ev["prioridad"] = "3-INFORMATIVO"
    datos["eventos"].sort(key=lambda x: (x["prioridad"], x["hora"]))


def generar_revision_ia_pdf_turno(dia: str, base_au_dir: Path, datos: Dict[str, Any]) -> Path:
    """Dictamen consolidado del turno: veredicto guardado > veredicto del audio Imou > pendiente."""
    rev_dir = base_au_dir / "04_Revision_IA" / dia[:7]
    rev_dir.mkdir(parents=True, exist_ok=True)
    for md_old in rev_dir.glob("*.md"):
        md_old.unlink(missing_ok=True)
    desde, hasta = rango_turno_dia(dia)
    rd = datos.get("resumen_demanda") or {}
    revisar = [x for x in datos["eventos"] if x.get("prioridad_original", x["prioridad"]) == "1-ALTA"]
    filas = []
    for i, a in enumerate(revisar, 1):
        vm = a.get("veredicto_manual")
        vi = str(a.get("veredicto_verificador") or "")
        if vm:
            dictamen = f"{vm['veredicto']}: {vm['motivo']} ({vm.get('revisado_por', '')})"
        elif vi.startswith("DESCARTADA_POR_IMOU"):
            dictamen = f"DESCARTADO por audio Imou: {vi}"
        else:
            dictamen = f"PENDIENTE DE REVISIÓN — {vi or 'sin verificación'}. Abrir el reproductor de video de la alerta."
        txt_imou = (a.get("transcripcion_imou") or "")[:220]
        extra_imou = f"<br><i>Imou escucha: {e(txt_imou)}</i>" if txt_imou else ""
        filas.append(
            f"<tr><td>{i}</td><td><b>{e(a['hora'][5:19])}</b></td><td>{e(a['hab'] or '-')}</td><td>{e(a['tipo'])}</td>"
            f"<td>S/ {a['monto_audio']:.2f}</td><td>{e(a['resultado'])}</td>"
            f"<td><b>{e(dictamen)}</b>{extra_imou}</td>"
            f"<td>{_img_html(a.get('foto_camara', ''), '135px')}</td></tr>"
        )
    filas_str = "".join(filas) or "<tr><td colspan='8'><i>Sin alertas 1-ALTA en este turno.</i></td></tr>"
    html_rev = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>{CSS_PDF}</style></head><body>
    <h1>🤖 Revisión de Alertas (Audio Laptop + Audio Imou + Cámara) — Turno {e(dia)}</h1>
    <div class="meta-box"><b>Turno (8:00 AM a 8:00 AM):</b> {e(desde)} → {e(hasta)}<br>
    <b>Alertas revisadas:</b> {len(revisar)} &nbsp;|&nbsp; <b>Consultas de habitación:</b> {rd.get('total_consultas', 0)}</div>
    <table><thead><tr><th style="width:4%">#</th><th style="width:10%">Fecha/Hora</th><th style="width:5%">Hab</th><th style="width:11%">Tipo</th><th style="width:8%">Audio S/</th><th style="width:20%">Alerta inicial</th><th style="width:26%">Dictamen</th><th style="width:16%">Captura</th></tr></thead>
    <tbody>{filas_str}</tbody></table></body></html>"""
    out_pdf = rev_dir / f"Revision_IA_{dia}.pdf"
    html_a_pdf(html_rev, out_pdf)
    return out_pdf


def preparar_estructura_au(base_dir: Path) -> None:
    base_dir.mkdir(parents=True, exist_ok=True)
    for old_sub in ("Informes", "Conversaciones_Completas"):
        p_old = base_dir / old_sub
        if p_old.exists() and p_old.is_dir():
            shutil.rmtree(p_old, ignore_errors=True)
    for sub in ("01_Informes_Diarios", "02_Conversaciones_Completas", "03_Alertas_Graves", "04_Revision_IA"):
        (base_dir / sub).mkdir(parents=True, exist_ok=True)
    generar_guia_pdf(base_dir)


def sincronizar_espejo_sin_videos(origen: Path, destino: Path) -> None:
    """Sincroniza `Guia_Auditor_AU.pdf` y las carpetas `01_`, `02_` y `04_` al espejo (sin duplicar `.mp4` pesados)."""
    preparar_estructura_au(destino)
    for sub_name in ("01_Informes_Diarios", "02_Conversaciones_Completas", "04_Revision_IA"):
        s_dir = origen / sub_name
        d_dir = destino / sub_name
        if s_dir.exists():
            if d_dir.exists():
                shutil.rmtree(d_dir, ignore_errors=True)
            shutil.copytree(s_dir, d_dir, dirs_exist_ok=True)
    s_al = origen / "03_Alertas_Graves"
    d_al = destino / "03_Alertas_Graves"
    if d_al.exists():
        shutil.rmtree(d_al, ignore_errors=True)
    if s_al.exists():
        for p in s_al.rglob("*"):
            if p.is_file() and p.suffix.lower() in (".pdf", ".jpg", ".html"):
                rel = p.relative_to(s_al)
                dst_f = d_al / rel
                dst_f.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dst_f)


def main() -> None:
    ci.limpiar_retencion_imou(dias_buffer=7, dias_alertas=7)
    preparar_estructura_au(DOWNLOADS_AU_DIR)

    dias = obtener_dias_disponibles()
    if len(sys.argv) > 1:  # uso: python exportar_auditor_au.py 2026-11-06  (solo regenera desde ese turno)
        dias = [d for d in dias if d >= sys.argv[1]]
    print(f"Turnos operativos (8am a 8am) a generar: {dias}")

    for dia in dias:
        desde, hasta = rango_turno_dia(dia)
        print(f"-> Procesando turno {dia} ({desde} -> {hasta})...")
        res_inf = generar_informe_dia(dia, DOWNLOADS_AU_DIR)
        generar_conversaciones_dia(dia, DOWNLOADS_AU_DIR)
        generar_revision_ia_pdf_turno(dia, DOWNLOADS_AU_DIR, res_inf["datos"])

    # Limpiar cualquier archivo .md residual en toda la carpeta Downloads\Auditor AU
    for xl_residual in DOWNLOADS_AU_DIR.rglob("*.xlsx"):
        try:
            xl_residual.unlink()
        except Exception:
            pass
    for md_residual in DOWNLOADS_AU_DIR.rglob("*.md"):
        try:
            md_residual.unlink()
        except Exception:
            pass

    sincronizar_espejo_sin_videos(DOWNLOADS_AU_DIR, MIRROR_AU_DIR)

    print("\n=== ESTRUCTURA FINAL EN DOWNLOADS\\AUDITOR AU (SIN ARCHIVOS .MD CRUDOS) ===")
    for sub_name in ("01_Informes_Diarios", "02_Conversaciones_Completas", "03_Alertas_Graves", "04_Revision_IA"):
        sub = DOWNLOADS_AU_DIR / sub_name
        print(f"\n[{sub.name}]")
        for f in sorted(sub.rglob("*")):
            if f.is_file():
                rel = f.relative_to(sub)
                print(f"  {str(rel):<72} {f.stat().st_size/1024:>7.1f} KB")


if __name__ == "__main__":
    main()
