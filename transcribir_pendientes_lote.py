#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Transcripción por lotes de clips pendientes con Whisper local (cuando Gemini no está disponible).

- Prioriza clips a ±10 min de eventos reales del sistema (estancias, gastos, pases) del periodo.
- Escribe SOLO en la tabla propia del auditor (`auditoria_audio_noche`); nunca en estancias/pagos/Sheets.
- Procesa en bloques pequeños para que el avance quede guardado aunque se interrumpa.

Uso:
    python transcribir_pendientes_lote.py --desde "2026-11-04 20:00:00" --hasta "2026-11-05 08:45:00"
"""
import argparse
import datetime as dt
import sqlite3
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
import auditor_audio_noche as aan  # noqa: E402
from config import DB_PATH  # noqa: E402


def _ts(s: str):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return dt.datetime.strptime(s.strip(), fmt)
        except Exception:
            continue
    return None


def momentos_de_eventos(conn, desde: dt.datetime, hasta: dt.datetime):
    """Horas de registros reales del sistema en el periodo (solo lectura)."""
    momentos = []
    dmys = {desde.strftime("%d/%m/%Y"), hasta.strftime("%d/%m/%Y")}
    ph = ",".join("?" for _ in dmys)
    for (v,) in conn.execute(f"SELECT id_timestamp_str FROM estancias WHERE fecha_in_dmy IN ({ph})", tuple(dmys)):
        t = _ts(str(v or ""))
        if t and desde <= t <= hasta:
            momentos.append(t)
    for (v,) in conn.execute(f"SELECT id_ts_str FROM gastos WHERE fecha_gasto_dmy IN ({ph})", tuple(dmys)):
        t = _ts(str(v or ""))
        if t and desde <= t <= hasta:
            momentos.append(t)
    for f, h in conn.execute(f"SELECT fecha_dmy, hora FROM control_pases WHERE fecha_dmy IN ({ph})", tuple(dmys)):
        t = _ts(f"{f} {h}")
        if t and desde <= t <= hasta:
            momentos.append(t)
    return momentos


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", required=True)
    ap.add_argument("--hasta", required=True)
    ap.add_argument("--bloque", type=int, default=8)
    ap.add_argument("--max-duracion", type=float, default=0.0,
                    help="Si > 0, omite clips continuos mayores a estos segundos (ej. 150s de TV/TikTok).")
    ap.add_argument("--solo-prioritarios", action="store_true",
                    help="Procesa solo clips cercanos a eventos (±10m) y conversaciones de madrugada (00h-08h).")
    a = ap.parse_args()
    desde, hasta = _ts(a.desde), _ts(a.hasta)

    aan.init_audio_audit_db()
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    filas = [dict(r) for r in conn.execute(
        "SELECT * FROM auditoria_audio_noche WHERE fecha_hora_inicio BETWEEN ? AND ? "
        "AND (procesado_ia = 0 OR transcripcion LIKE '%pendiente de transcripci%') ORDER BY fecha_hora_inicio",
        (a.desde, a.hasta),
    ).fetchall()]
    filas = [f for f in filas if Path(f["archivo_wav"]).exists()]
    if a.max_duracion > 0:
        filas = [f for f in filas if float(f.get("duracion_seg") or 0) <= a.max_duracion]
    eventos = momentos_de_eventos(conn, desde, hasta)

    def prioridad(f):
        t = _ts(f["fecha_hora_inicio"])
        dur = float(f.get("duracion_seg") or 0)
        cerca = any(abs((t - e).total_seconds()) <= 600 for e in eventos) if t else False
        es_continuo_largo = dur >= 150.0
        madrugada = (t is not None and 0 <= t.hour < 8)
        if cerca and not es_continuo_largo:
            grupo = 0
        elif madrugada and dur >= 4.0 and not es_continuo_largo:
            grupo = 1
        elif cerca:
            grupo = 2
        elif madrugada:
            grupo = 3
        elif not es_continuo_largo and dur >= 5.0:
            grupo = 4
        else:
            grupo = 5
        return (grupo, f["fecha_hora_inicio"])

    filas.sort(key=prioridad)
    if a.solo_prioritarios:
        filas = [f for f in filas if prioridad(f)[0] <= 3]
    n_cerca = sum(1 for f in filas if prioridad(f)[0] in (0, 2))
    print(f"Pendientes: {len(filas)} clips ({sum(f['duracion_seg'] for f in filas)/60:.1f} min). "
          f"Cerca de eventos del sistema: {n_cerca}. Eventos de referencia: {len(eventos)}", flush=True)

    hechos = 0
    for i in range(0, len(filas), a.bloque):
        bloque = filas[i : i + a.bloque]
        res = aan.transcribir_local_whisper([Path(f["archivo_wav"]) for f in bloque])
        for f in bloque:
            r = res.get(str(Path(f["archivo_wav"])))
            if not r or not r.get("ok"):
                continue
            info = aan.analizar_audio_con_ia(Path(f["archivo_wav"]), fecha_hora_str=f["fecha_hora_inicio"],
                                             texto_local=str(r.get("texto") or ""))
            dni_l = info.get("dni_detectado") or ""
            huesp = info.get("huesped_detectado") or (aan.consultar_nombre_dni_local_o_reniec(dni_l) if len(dni_l) == 8 else "")
            est, det, idm, mreg, diff = aan.cruzar_con_registro_estancias_y_pagos(
                fecha_hora_audio_str=f["fecha_hora_inicio"], hab_detectada=info.get("hab_detectada") or "",
                dni_detectado=dni_l, precio_hablado=float(info.get("precio_hablado") or 0.0),
                es_atencion=bool(info.get("es_atencion_cliente")), tipo_interaccion=info.get("tipo_interaccion") or "",
                transcripcion=info.get("transcripcion") or "", huesped_detectado=huesp,
                incidencia_mantenimiento=info.get("incidencia_mantenimiento") or "",
            )
            conn.execute(
                """UPDATE auditoria_audio_noche SET transcripcion=?, resumen_ia=?, es_atencion_cliente=?,
                   hab_detectada=?, dni_detectado=?, huesped_detectado=?, precio_hablado=?, metodo_pago_hablado=?,
                   alerta_fuga_voz=?, estado_cruce=?, detalle_cruce=?, estancia_id_match=?, monto_registrado=?,
                   diferencia_soles=?, hablantes_detectados=?, tipo_interaccion=?, incidencia_mantenimiento=?,
                   procesado_ia=1, fuente_transcripcion=?, banderas=? WHERE clip_id=?""",
                (info.get("transcripcion") or "", info.get("resumen_ia") or "", 1 if info.get("es_atencion_cliente") else 0,
                 info.get("hab_detectada") or "", dni_l, huesp, float(info.get("precio_hablado") or 0.0),
                 info.get("metodo_pago_hablado") or "", info.get("alerta_fuga_voz") or "", est, det, idm, mreg, diff,
                 info.get("hablantes_detectados") or "", info.get("tipo_interaccion") or "",
                 info.get("incidencia_mantenimiento") or "", info.get("fuente_transcripcion") or "WHISPER_LOCAL",
                 ",".join(info.get("banderas") or []), f["clip_id"]),
            )
            hechos += 1
            tr = (info.get("transcripcion") or "")[:110].replace("\n", " ")
            print(f"[{hechos}/{len(filas)}] {f['fecha_hora_inicio'][11:]} {info.get('tipo_interaccion'):<24} {est:<26} {tr}", flush=True)
        conn.commit()
    conn.close()
    print(f"Listo: {hechos}/{len(filas)} clips transcritos con Whisper local.", flush=True)


if __name__ == "__main__":
    main()
