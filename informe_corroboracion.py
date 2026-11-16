#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Informe de CORROBORACIÓN del auditor de audio (para revisar y decidir; NO modifica nada).

- Lee `auditoria_audio_noche`, `estancias`, `pagos`, `gastos`, `control_pases` en modo SOLO LECTURA.
- NO escribe en la hoja maestra de Google Sheets ni en las tablas operativas.
- Genera:
    * XLSX  -> se sube a Google Drive y se abre con Google Sheets (pestañas + columna "Decisión").
    * HTML  -> se sube a Google Drive y se abre con Google Docs (resumen narrativo).

Uso:
    python informe_corroboracion.py --desde "2026-11-04 20:00:00" --hasta "2026-11-05 08:45:00"
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
import auditor_audio_noche as aan  # noqa: E402
import lenguaje_recepcion as lr  # noqa: E402
try:
    import verificador_audio_imou as vai  # noqa: E402
except Exception:
    vai = None
from config import DB_PATH, NOMBRE_HOTEL  # noqa: E402

SALIDA_DIR = aan.REPORTES_AUDIO_DIR

ESTADOS_ALTA = {"NO_REGISTRADO_ALERTA", "PASE_NO_REGISTRADO_ALERTA", "ALERTA_PRECIO_MENOR_ANOTADO"}
ESTADOS_MEDIA = {"VERIFICAR_PASE", "VERIFICAR_DNI_VISITA", "REGISTRADO_PAGO_PENDIENTE", "OBSERVACION_MONTO",
                 "RELEVO_INVENTARIO", "VERIFICAR_VENTA_TIENDA", "MANTENIMIENTO_REPORTADO"}
BANDERAS_ALTA = {"ALERTA_YAPE", "ALERTA_REGISTRO_DATOS_FALSOS"}
BANDERAS_MEDIA = {"SALDO_O_VUELTO_PENDIENTE", "PAGO_PENDIENTE", "POR_HORAS"}

DECISIONES = ["Corroborado", "Descartado (falso positivo)", "Corregir en sistema", "Revisar cámara", "Pendiente"]

MEJORAS = [
    ("Respaldo de transcripción local (Whisper) cuando Gemini se queda sin saldo/cuota",
     "Implementado", "Anoche Gemini respondió 402 (saldo agotado) a las 21:16 y 284 clips quedaron sin texto. "
     "Ahora: corte automático de Gemini + Whisper local aislado (.venv_whisper)."),
    ("No marcar como 'procesado' un clip sin transcripción", "Implementado",
     "Los clips sin texto quedaban como procesados y nunca se reintentaban."),
    ("Agrupar clips consecutivos (≤120 s) en una sola transacción", "Implementado",
     "Un check-in partido en 2-3 clips (DNI en uno, pago en otro) se cruza una sola vez."),
    ("Pases: exigir DNI igual (o ±90 min sin DNI) para dar por registrado", "Implementado",
     "La visita DNI 45582328 (Hab. 206, 20:50) se daba por 'registrada' con la prueba del admin de las 13:46."),
    ("Lenguaje real de recepción (frases de los audios de referencia)", "Implementado",
     "DNI dígito a dígito, 'su DNI por favor', 'más tarde le doy', 'tienda cargada a', 'falta/sobra', por horas, "
     "audio de fondo (lives de ropa), cambio de sencillo. Ampliable en C:\\Hostal_Sistema\\lexico_recepcion.json."),
    ("Recepcionista nocturna no fija en 'Yolyi'", "Implementado",
     "Anoche trabajó Rous (gasto 'PAGO ROUS TURNO NOCHE'); ahora se usa quien marcó asistencia o 'Recepción Noche'."),
    ("Registrar a Paola y Rous en personal_config / horario_registros", "Requiere su decisión",
     "No se modificó: afecta tablas que se sincronizan con la hoja. Sin esto, la voz nocturna no se atribuye por nombre."),
    ("Validar perfil de voz de Rous", "Requiere su decisión",
     "Perfil marcado PROVISIONAL: se calibró asumiendo por horario que hablaba Rous; confirmar con 3-5 clips escuchados."),
    ("Recargar saldo de Gemini o mantener solo Whisper local", "Requiere su decisión",
     "Whisper local es gratis pero menos preciso (confunde nombres y algunos DNIs). Gemini da mejor texto y hablantes."),
    ("Arranque automático del grabador con Windows (Programador de tareas)", "Requiere su decisión",
     "Hoy el grabador depende de la sesión abierta; un reinicio de la PC lo detiene."),
]


def _ts(s: str):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return dt.datetime.strptime(str(s).strip(), fmt)
        except Exception:
            continue
    return None


def _buscar_foto_camara(fecha_hora_str: str, archivos_str: str = "") -> str:
    """
    Busca la captura `<HH-MM-SS>_camara.jpg` asociada al clip/sesión en `data/audios_noche/<YYYY-MM-DD>/`
    o la captura más cercana dentro de ±180 segundos. Retorna la ruta absoluta como string o "".
    """
    dia = str(fecha_hora_str or "")[:10]
    if not dia:
        return ""
    carpetas = [
        BASE / "data" / "audios_noche" / dia,
        Path(r"C:\Hostal_Sistema\audios_noche_recepcion") / dia,
    ]
    # 1. Coincidencia directa por nombre de archivo wav (<HH-MM-SS>_conversacion.wav -> <HH-MM-SS>_camara.jpg)
    for parte in [p.strip() for p in (archivos_str or "").split(",") if p.strip()]:
        pref = Path(parte).name[:8]
        if len(pref) == 8 and pref[2] == "-" and pref[5] == "-":
            for d in carpetas:
                cand = d / f"{pref}_camara.jpg"
                if cand.exists() and cand.stat().st_size > 1000:
                    return str(cand)
    # 2. Coincidencia por hora exacta de inicio o más cercana (±180 s)
    t_ev = _ts(fecha_hora_str)
    if not t_ev:
        return ""
    mejor_path = ""
    menor_diff = 9999.0
    for d in carpetas:
        if not d.exists():
            continue
        for jpg in d.glob("*_camara.jpg"):
            pref = jpg.name[:8]
            t_jpg = _ts(f"{dia} {pref.replace('-', ':')}")
            if t_jpg:
                diff = abs((t_jpg - t_ev).total_seconds())
                if diff <= 180.0 and diff < menor_diff and jpg.stat().st_size > 1000:
                    menor_diff = diff
                    mejor_path = str(jpg)
        if mejor_path:
            break
    return mejor_path


def _prioridad(estado: str, banderas: List[str], alerta: str, tipo: str = "") -> str:
    if tipo == "AUDIO_FONDO":
        return "3-INFORMATIVO"
    alta_por_bandera = BANDERAS_ALTA.intersection(banderas) or "ALERTA YAPE" in (alerta or "")
    # Una charla interna / audio de fondo con montos nunca es "alta" por sí sola (no hay cobro a cliente)
    if tipo in ("CHARLA_INTERNA", "AUDIO_FONDO", "DEUDA_INTERNA_PERSONAL") and not alta_por_bandera:
        return "2-MEDIA" if (estado in ESTADOS_ALTA | ESTADOS_MEDIA or BANDERAS_MEDIA.intersection(banderas)) else "3-INFORMATIVO"
    if estado in ESTADOS_ALTA or alta_por_bandera:
        return "1-ALTA"
    if estado in ESTADOS_MEDIA or BANDERAS_MEDIA.intersection(banderas):
        return "2-MEDIA"
    return "3-INFORMATIVO"


CAJA_CALIBRACION_JSON = BASE / "data" / "caja_calibracion.json"


def _movimientos_efectivo_historico(conn) -> List[tuple]:
    """Todos los movimientos de efectivo (+ ingresos de `pagos` y tienda, − gastos/recojos) ordenados por hora."""
    movs: List[tuple] = []
    for r in conn.execute("SELECT fecha_iso, monto, medio_pago FROM pagos"):
        t = _ts(r["fecha_iso"] or "")
        if t and "efectivo" in str(r["medio_pago"] or "").lower():
            movs.append((t, float(r["monto"] or 0)))
    try:
        for r in conn.execute("SELECT fecha_iso, subtotal, metodo_pago FROM ventas_tienda"):
            t = _ts(r["fecha_iso"] or "")
            if t and "efectivo" in str(r["metodo_pago"] or "").lower():
                movs.append((t, float(r["subtotal"] or 0)))
    except Exception:
        pass
    for r in conn.execute("SELECT id_ts_str, monto, metodo_pago FROM gastos"):
        t = _ts(r["id_ts_str"] or "")
        if t and "efectivo" in str(r["metodo_pago"] or "Efectivo").lower():
            movs.append((t, -float(r["monto"] or 0)))
    movs.sort(key=lambda x: x[0])
    return movs


def _offset_calibracion(movs: List[tuple], saldo_panel: float) -> float:
    """Calibra una sola vez: en el momento de la primera ejecución se asume que el panel es correcto."""
    try:
        if CAJA_CALIBRACION_JSON.exists():
            return float(json.loads(CAJA_CALIBRACION_JSON.read_text(encoding="utf-8"))["offset"])
    except Exception:
        pass
    ahora_ = dt.datetime.now()
    offset = saldo_panel - sum(m for t, m in movs if t <= ahora_)
    CAJA_CALIBRACION_JSON.parent.mkdir(parents=True, exist_ok=True)
    CAJA_CALIBRACION_JSON.write_text(json.dumps({
        "offset": round(offset, 2), "calibrado_en": ahora_.strftime("%Y-%m-%d %H:%M:%S"), "saldo_panel_en_calibracion": saldo_panel,
        "nota": "Se asumió que la caja del panel era correcta en este momento; los cuadres se miden desde aquí.",
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return offset


def recolectar(desde: str, hasta: str) -> Dict[str, Any]:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30)  # SOLO LECTURA
    conn.row_factory = sqlite3.Row
    clips = [dict(r) for r in conn.execute(
        "SELECT * FROM auditoria_audio_noche WHERE fecha_hora_inicio BETWEEN ? AND ? ORDER BY fecha_hora_inicio",
        (desde, hasta),
    ).fetchall()]
    for c in clips:
        c["banderas"] = c.get("banderas") or ""
        tr = c.get("transcripcion") or ""
        if tr and "pendiente de transcripci" not in tr.lower() and not tr.startswith("(") and aan.clasificar_por_lexico:
            lx = aan.clasificar_por_lexico(tr)
            if lx.get("tipo_interaccion") == "AUDIO_FONDO":
                # Novelas / lives / TikTok: nunca son operación del hotel (aplica a TODA fuente de transcripción)
                c["tipo_interaccion"] = "AUDIO_FONDO"
                c["precio_hablado"] = 0.0
                c["alerta_fuga_voz"] = ""
                c["banderas"] = ""
                c["estado_cruce"] = "AUDIO_FONDO"
            elif (c.get("fuente_transcripcion") or "") == "WHISPER_LOCAL":
                re_info = aan.extraer_datos_por_reglas(tr, {"hablantes_detectados": c.get("hablantes_detectados") or "Recepción"})
                c["tipo_interaccion"] = re_info["tipo_interaccion"]
                c["hab_detectada"] = re_info["hab_detectada"]
                c["dni_detectado"] = re_info["dni_detectado"]
                if not c["dni_detectado"]:
                    c["huesped_detectado"] = ""
                c["precio_hablado"] = float(re_info["precio_hablado"] or 0.0)
                c["metodo_pago_hablado"] = re_info["metodo_pago_hablado"]
                c["alerta_fuga_voz"] = re_info["alerta_fuga_voz"]
                c["banderas"] = ",".join(re_info["banderas"])
            elif not c["banderas"]:
                c["banderas"] = ",".join(lx["banderas"])
    d0, d1 = _ts(desde), _ts(hasta)
    # Incluir d0 - 1 día porque los check-ins y gastos de madrugada (00:00 a 06:59 AM)
    # tienen id_timestamp_str del día actual pero fecha_in_dmy / fecha_gasto_dmy del turno anterior
    dmys = sorted({
        (d0 - dt.timedelta(days=1)).strftime("%d/%m/%Y"),
        d0.strftime("%d/%m/%Y"),
        d1.strftime("%d/%m/%Y"),
    })
    ph = ",".join("?" for _ in dmys)

    estancias = []
    efec_estancias_dia = 0.0
    for r in conn.execute(
        f"SELECT id_timestamp_str, hab, huesped, dni, tarifa, noches, pago_ini_efec, pago_ini_yape, pago_post_efec, "
        f"pago_post_yape, pago_visa FROM estancias WHERE fecha_in_dmy IN ({ph})", tuple(dmys)
    ):
        t = _ts(r["id_timestamp_str"] or "")
        if t and d0 <= t <= d1:
            pagado = sum(float(r[k] or 0) for k in ("pago_ini_efec", "pago_ini_yape", "pago_post_efec", "pago_post_yape", "pago_visa"))
            yape_p = float(r["pago_ini_yape"] or 0) + float(r["pago_post_yape"] or 0)
            efec_p = float(r["pago_ini_efec"] or 0) + float(r["pago_post_efec"] or 0)
            # El efectivo del turno se calcula desde la tabla `pagos` (Registro de Pagos de la web app local)
            medio_est = "Yape" if (yape_p > 0 and efec_p == 0) else ("Efectivo" if (efec_p > 0 and yape_p == 0) else ("Mixto" if (yape_p > 0 and efec_p > 0) else "-"))
            estancias.append({"hora": t, "hab": r["hab"], "huesped": r["huesped"], "dni": r["dni"],
                              "tarifa": float(r["tarifa"] or 0), "pagado": pagado, "efectivo": efec_p, "yape": yape_p, "medio": medio_est})
    estancias.sort(key=lambda x: x["hora"])

    gastos = []
    gastos_operativos_efec_dia = 0.0
    recojos_efec_dia = []
    for r in conn.execute(f"SELECT id_ts_str, descripcion, monto, categoria, metodo_pago, responsable FROM gastos WHERE fecha_gasto_dmy IN ({ph})", tuple(dmys)):
        t = _ts(r["id_ts_str"] or "")
        if t and d0 <= t <= d1:
            m_g = float(r["monto"] or 0)
            desc_g = str(r["descripcion"] or "")
            cat_g = str(r["categoria"] or "")
            met_g = str(r["metodo_pago"] or "Efectivo")
            es_recojo = "recojo" in desc_g.lower() or "recogo" in cat_g.lower() or "recojo" in cat_g.lower()
            item_g = {"hora": t, "descripcion": desc_g, "monto": m_g, "categoria": cat_g, "metodo_pago": met_g, "responsable": r["responsable"], "es_recojo": es_recojo}
            gastos.append(item_g)
            if "efectivo" in met_g.lower():
                if es_recojo:
                    recojos_efec_dia.append(item_g)
                else:
                    gastos_operativos_efec_dia += m_g
    gastos.sort(key=lambda x: x["hora"])

    ventas_tienda = []
    efec_tienda_dia = 0.0
    try:
        for r in conn.execute(
            f"SELECT fecha_iso, producto, cantidad, metodo_pago, hab, subtotal FROM ventas_tienda WHERE fecha_dmy IN ({ph})",
            tuple(dmys),
        ):
            t = _ts(r["fecha_iso"] or "")
            if t and d0 <= t <= d1:
                sub_vt = float(r["subtotal"] or 0)
                met_vt = str(r["metodo_pago"] or "Efectivo")
                if "efectivo" in met_vt.lower():
                    efec_tienda_dia += sub_vt
                ventas_tienda.append({
                    "hora": t,
                    "descripcion": f"Tienda: {int(float(r['cantidad'] or 1))}x {r['producto']} ({met_vt})",
                    "monto": sub_vt,
                    "metodo_pago": met_vt,
                    "responsable": f"Hab. {r['hab']}" if r["hab"] else "Mostrador",
                })
    except Exception:
        pass
    ventas_tienda.sort(key=lambda x: x["hora"])

    # Efectivo REAL del turno desde `pagos` (Registro de Pagos): Inicial + Posterior + Reingreso + Ajustes − Extornos/Vueltos
    pagos_efectivo: List[Dict[str, Any]] = []
    try:
        for r in conn.execute(
            f"SELECT fecha_iso, hab, huesped, monto, medio_pago, tipo_pago, concepto FROM pagos "
            f"WHERE substr(fecha_iso,1,10) IN ({ph})", tuple(dmys),
        ):
            t = _ts(r["fecha_iso"] or "")
            if t and d0 <= t <= d1 and "efectivo" in str(r["medio_pago"] or "").lower():
                pagos_efectivo.append({"hora": t, "hab": r["hab"], "huesped": r["huesped"], "monto": float(r["monto"] or 0),
                                       "tipo": r["tipo_pago"], "concepto": r["concepto"]})
    except Exception:
        pass
    pagos_efectivo.sort(key=lambda x: x["hora"])
    efec_estancias_dia = sum(p["monto"] for p in pagos_efectivo)

    # Último recojo ANTES del turno + movimientos desde entonces (para saber cuánto efectivo debía haber al iniciar el turno)
    efectivo_inicial_turno = 0.0
    ultimo_recojo_previo = None
    try:
        recs = []
        for r in conn.execute("SELECT id_ts_str, monto, descripcion, categoria, metodo_pago FROM gastos"):
            t = _ts(r["id_ts_str"] or "")
            txt = f"{r['descripcion']} {r['categoria']}".lower()
            if t and t < d0 and ("recojo" in txt or "recogo" in txt) and "efectivo" in str(r["metodo_pago"] or "Efectivo").lower():
                recs.append(t)
        ultimo_recojo_previo = max(recs) if recs else None
        if ultimo_recojo_previo:
            dmy_prev = sorted({(ultimo_recojo_previo + dt.timedelta(days=k)).strftime("%d/%m/%Y")
                               for k in range((d0.date() - ultimo_recojo_previo.date()).days + 1)})
            phq = ",".join("?" for _ in dmy_prev)
            for r in conn.execute(f"SELECT fecha_iso, monto, medio_pago FROM pagos WHERE substr(fecha_iso,1,10) IN ({phq})", tuple(dmy_prev)):
                t = _ts(r["fecha_iso"] or "")
                if t and ultimo_recojo_previo < t < d0 and "efectivo" in str(r["medio_pago"] or "").lower():
                    efectivo_inicial_turno += float(r["monto"] or 0)
            for r in conn.execute(f"SELECT fecha_iso, subtotal, metodo_pago FROM ventas_tienda WHERE fecha_dmy IN ({phq})", tuple(dmy_prev)):
                t = _ts(r["fecha_iso"] or "")
                if t and ultimo_recojo_previo < t < d0 and "efectivo" in str(r["metodo_pago"] or "").lower():
                    efectivo_inicial_turno += float(r["subtotal"] or 0)
            for r in conn.execute(f"SELECT id_ts_str, monto, metodo_pago FROM gastos WHERE fecha_gasto_dmy IN ({phq})", tuple(dmy_prev)):
                t = _ts(r["id_ts_str"] or "")
                if t and ultimo_recojo_previo < t < d0 and "efectivo" in str(r["metodo_pago"] or "Efectivo").lower():
                    efectivo_inicial_turno -= float(r["monto"] or 0)
    except Exception:
        pass

    # Caja acumulada continua (consistente entre turnos) calibrada con el panel
    saldo_panel_hist = 0.0
    try:
        rc_ = conn.execute("SELECT valor FROM estado_sistema WHERE clave = 'caja_sheets_g1'").fetchone()
        saldo_panel_hist = float(str(rc_[0]).replace(",", "").strip() or 0.0) if rc_ and rc_[0] is not None else 0.0
    except Exception:
        pass
    movs_hist = _movimientos_efectivo_historico(conn)
    offset_caja = _offset_calibracion(movs_hist, saldo_panel_hist)

    def caja_en(t_: dt.datetime) -> float:
        return offset_caja + sum(m for tt, m in movs_hist if tt <= t_)

    efectivo_inicial_turno = caja_en(d0 - dt.timedelta(seconds=1))

    pases = []
    for r in conn.execute(f"SELECT fecha_dmy, hora, hab, huesped_titular, dni_visita, nombre_visita, estado, monto FROM control_pases WHERE fecha_dmy IN ({ph})", tuple(dmys)):
        t = _ts(f"{r['fecha_dmy']} {r['hora']}")
        if t and d0 <= t <= d1:
            pases.append({**{k: r[k] for k in r.keys()}, "hora": t})

    # Relevos de turno desde `horario_registros` (marcaciones reales de entrada/salida en el turno 8am-8am)
    relevos_horario = []
    fechas_dmy_rel = sorted({d0.strftime("%d/%m/%Y"), d1.strftime("%d/%m/%Y")})
    fechas_iso_rel = sorted({d0.strftime("%Y-%m-%d"), d1.strftime("%Y-%m-%d")})
    ph_dmy_r = ",".join("?" for _ in fechas_dmy_rel)
    ph_iso_r = ",".join("?" for _ in fechas_iso_rel)
    try:
        for r in conn.execute(
            f"SELECT empleado, fecha_dmy, ymd, e1, s1, e2, s2, estado, nota, ult_marca_ts FROM horario_registros "
            f"WHERE fecha_dmy IN ({ph_dmy_r}) OR ymd IN ({ph_iso_r}) ORDER BY ymd ASC, e1 ASC",
            tuple(fechas_dmy_rel + fechas_iso_rel),
        ):
            marcas = []
            for k_m, etq in [("e1", "Entrada 1"), ("s1", "Salida 1"), ("e2", "Entrada 2"), ("s2", "Salida 2")]:
                val_m = str(r[k_m] or "").strip()
                if val_m:
                    marcas.append(f"{etq}: {val_m}")
            ymd_row = str(r["ymd"] or d0.strftime("%Y-%m-%d"))
            foto_rel = ""
            for h_cand in [r["e1"], r["s2"], r["s1"], r["e2"]]:
                h_str = str(h_cand or "").strip()
                if len(h_str) == 5 and ":" in h_str:
                    foto_rel = _buscar_foto_camara(f"{ymd_row} {h_str}:00")
                    if foto_rel:
                        break
            relevos_horario.append({
                "empleado": r["empleado"],
                "fecha_dmy": r["fecha_dmy"],
                "e1": r["e1"] or "-",
                "s1": r["s1"] or "-",
                "e2": r["e2"] or "-",
                "s2": r["s2"] or "-",
                "resumen_marcas": " | ".join(marcas) if marcas else "-",
                "estado": r["estado"] or "-",
                "nota": r["nota"] or "",
                "foto_camara": foto_rel,
            })
    except Exception:
        pass

    # Saldo actual de Caja Efectivo en PanelDisponibilidad (`estado_sistema.caja_sheets_g1`)
    saldo_caja_panel = 0.0
    try:
        row_caja = conn.execute("SELECT valor FROM estado_sistema WHERE clave = 'caja_sheets_g1'").fetchone()
        if row_caja and row_caja[0] is not None:
            saldo_caja_panel = float(str(row_caja[0]).replace(",", "").strip() or 0.0)
    except Exception:
        pass
    conn.close()

    total_recojos_dia = sum(x["monto"] for x in recojos_efec_dia)
    ahora = dt.datetime.now()
    turno_en_curso = d0 <= ahora <= d1
    flujo_neto = efec_estancias_dia + efec_tienda_dia - gastos_operativos_efec_dia - total_recojos_dia
    esperado_final = caja_en(min(d1, ahora))
    # Cuadre por relevo: efectivo que DEBE haber en caja al momento de cada relevo (acumulado desde el último recojo)
    puntos_relevo: List[dt.datetime] = []
    for rh_ in relevos_horario:
        ymd_ = None
        try:
            ymd_ = dt.datetime.strptime(str(rh_["fecha_dmy"]), "%d/%m/%Y")
        except Exception:
            pass
        for k_ in ("e1", "e2"):
            hh_ = str(rh_.get(k_) or "").strip()
            if ymd_ and len(hh_) == 5 and ":" in hh_:
                tt_ = dt.datetime.strptime(f"{ymd_.strftime('%Y-%m-%d')} {hh_}", "%Y-%m-%d %H:%M")
                if d0 <= tt_ <= d1:
                    puntos_relevo.append(tt_)
    puntos_relevo.sort()
    relevos_agrupados: List[dt.datetime] = []
    for p_ in puntos_relevo:
        if not relevos_agrupados or (p_ - relevos_agrupados[-1]).total_seconds() > 3600:
            relevos_agrupados.append(p_)
    mov = [(p["hora"], p["monto"]) for p in pagos_efectivo]
    mov += [(v["hora"], v["monto"]) for v in ventas_tienda if "efectivo" in str(v["metodo_pago"]).lower()]
    mov += [(g["hora"], -g["monto"]) for g in gastos if "efectivo" in str(g["metodo_pago"]).lower()]
    cuadre_relevos = []
    for rv in relevos_agrupados:
        esperado = caja_en(rv)
        cuadre_relevos.append({"hora": rv, "efectivo_esperado_en_caja": round(esperado, 2),
                               "foto_camara": _buscar_foto_camara(rv.strftime("%Y-%m-%d %H:%M:%S"))})
    saldo_panel = round(saldo_caja_panel, 2) if turno_en_curso else None
    dif_panel = round(saldo_panel - caja_en(ahora), 2) if saldo_panel is not None else None
    resumen_caja_efectivo = {
        "saldo_panel_actual": saldo_panel,  # solo existe si el turno está en curso (el panel solo guarda el saldo de hoy)
        "turno_en_curso": turno_en_curso,
        "ultimo_recojo_previo": ultimo_recojo_previo.strftime("%d/%m %H:%M") if ultimo_recojo_previo else "No registrado",
        "efectivo_inicial_turno": round(efectivo_inicial_turno, 2),
        "efec_estancias_dia": round(efec_estancias_dia, 2),
        "efec_tienda_dia": round(efec_tienda_dia, 2),
        "gastos_operativos_efec_dia": round(gastos_operativos_efec_dia, 2),
        "total_recojos_dia": round(total_recojos_dia, 2),
        "recojos_detalle": recojos_efec_dia,
        "flujo_neto_dia": round(flujo_neto, 2),
        "efectivo_esperado_final": round(esperado_final, 2),
        "diferencia_vs_panel": dif_panel,
        "cuadre_relevos": cuadre_relevos,
        "alerta_caja": bool(dif_panel is not None and abs(dif_panel) >= 1.0),
    }

    # Conciliación Pagos Yape (Cols A-C Banco, D-F Estancia, G Cuadre) en el rango del día
    conciliacion_yape = []
    for fy in aan.obtener_conciliacion_yape():
        t_b = _ts(fy.get("fecha_banco") or "")
        t_e = _ts((fy.get("fecha_excel") or "").split("\n")[0])
        t_ref = t_b or t_e
        if t_ref and d0 <= t_ref <= d1:
            conciliacion_yape.append({**fy, "hora": t_ref})
    conciliacion_yape.sort(key=lambda x: x["hora"])

    # Sesiones operativas (clips unidos) + cruce único por sesión (solo lectura)
    con_texto = [c for c in clips if c.get("transcripcion") and "pendiente de transcripci" not in c["transcripcion"].lower()]
    sesiones = [aan.cruzar_sesion(s) for s in aan.agrupar_en_sesiones(con_texto)]
    ids_en_sesion = {c["clip_id"] for s in sesiones for c in s["clips"]}

    eventos = []
    for s in sesiones:
        n_fondo_s = sum(1 for c_ in s["clips"] if (c_.get("tipo_interaccion") or "") == "AUDIO_FONDO")
        sesion_fondo = bool(s["clips"]) and (n_fondo_s / len(s["clips"])) > 0.5
        eventos.append({
            "hora": s.get("hora_clave") or s["inicio"], "hab": s["hab_detectada"],
            "tipo": "AUDIO_FONDO" if sesion_fondo else s["tipo_interaccion"],
            "voz": s["hablantes_detectados"], "dni": s["dni_detectado"], "persona": s["huesped_detectado"],
            "monto_audio": float(s["precio_hablado"] or 0), "monto_sistema": float(s.get("monto_registrado") or 0),
            "estado": s["estado_cruce"], "resultado": s["detalle_cruce"], "banderas": s["banderas"],
            "alerta": s["alerta_fuga_voz"], "fuente": s["fuente_transcripcion"], "n_clips": s["n_clips"],
            "archivos": ", ".join(Path(c["archivo_wav"]).name for c in s["clips"]), "transcripcion": s["transcripcion"],
        })
    # Clips sueltos relevantes que no forman parte de una sesión operativa (re-cruzados con la lógica actual)
    for c in con_texto:
        if c["clip_id"] in ids_en_sesion:
            continue
        band = [b for b in (c.get("banderas") or "").split(",") if b]
        est, det, _, m_reg, _ = aan.cruzar_con_registro_estancias_y_pagos(
            fecha_hora_audio_str=c["fecha_hora_inicio"], hab_detectada=c.get("hab_detectada") or "",
            dni_detectado=c.get("dni_detectado") or "", precio_hablado=float(c.get("precio_hablado") or 0),
            es_atencion=bool(c.get("es_atencion_cliente")), tipo_interaccion=c.get("tipo_interaccion") or "",
            transcripcion=c.get("transcripcion") or "", huesped_detectado=c.get("huesped_detectado") or "",
            incidencia_mantenimiento=c.get("incidencia_mantenimiento") or "",
        )
        if (c.get("tipo_interaccion") or "") in ("CHARLA_INTERNA",) and est == "NO_REGISTRADO_ALERTA":
            est = "MONTO_EN_CONVERSACION"
            det = f"💬 Se menciona S/ {float(c.get('precio_hablado') or 0):.2f} en una conversación interna (sin cobro claro a un cliente). Verificar contexto."
        c["estado_cruce"], c["detalle_cruce"], c["monto_registrado"] = est, det, m_reg
        relevante = (
            c.get("tipo_interaccion") in ("MANTENIMIENTO_LIMPIEZA", "RELEVO_INVENTARIO_TIENDA", "DEUDA_INTERNA_PERSONAL")
            or c.get("incidencia_mantenimiento") or band or c.get("alerta_fuga_voz")
            or est in ESTADOS_ALTA | ESTADOS_MEDIA | {"MONTO_EN_CONVERSACION"}
        )
        if not relevante:
            continue
        eventos.append({
            "hora": c["fecha_hora_inicio"], "hab": c.get("hab_detectada") or "", "tipo": c.get("tipo_interaccion") or "",
            "voz": c.get("hablantes_detectados") or "", "dni": c.get("dni_detectado") or "", "persona": c.get("huesped_detectado") or "",
            "monto_audio": float(c.get("precio_hablado") or 0), "monto_sistema": float(c.get("monto_registrado") or 0),
            "estado": c.get("estado_cruce") or "", "resultado": c.get("detalle_cruce") or c.get("incidencia_mantenimiento") or "",
            "banderas": band, "alerta": c.get("alerta_fuga_voz") or "", "fuente": c.get("fuente_transcripcion") or "GEMINI",
            "n_clips": 1, "archivos": Path(c["archivo_wav"]).name, "transcripcion": c.get("transcripcion") or "",
        })
    for e in eventos:
        e["prioridad"] = _prioridad(e["estado"], e["banderas"], e["alerta"], e["tipo"])
        e["foto_camara"] = _buscar_foto_camara(e["hora"], e["archivos"])
        e["foto_nombre"] = Path(e["foto_camara"]).name if e["foto_camara"] else "-"
        e["prioridad_original"] = e["prioridad"]
        if vai is not None and e["prioridad"] == "1-ALTA":
            v_info = vai.verificar_evento_con_imou(e)
            e["veredicto_verificador"] = v_info.get("veredicto_verificador", "")
            e["transcripcion_imou"] = v_info.get("transcripcion_imou", "")
            e["snr_imou_db"] = v_info.get("snr_imou_db", 0.0)
            e["tiene_audio_imou"] = bool(v_info.get("tiene_audio_imou"))
            if str(e["veredicto_verificador"]).startswith("DESCARTADA_POR_IMOU"):
                e["prioridad"] = "3-INFORMATIVO"  # el audio claro de Imou demuestra que no hubo cobro en mostrador
        else:
            e["veredicto_verificador"] = "OK_INFORMATIVO"
            e["transcripcion_imou"] = ""
    eventos.sort(key=lambda e: (e["prioridad"], e["hora"]))

    # ¿Cada registro del sistema tiene una conversación de audio cerca? (±15 min)
    horas_ses = [_ts(s["inicio"]) for s in sesiones]
    horas_voz = [_ts(c["fecha_hora_inicio"]) for c in con_texto if "sin voz inteligible" not in (c.get("transcripcion") or "").lower()]
    for est in estancias:
        cerca = [h for h in horas_ses if h and abs((h - est["hora"]).total_seconds()) <= 900]
        if cerca:
            est["audio_cerca"] = min(cerca, key=lambda h: abs((h - est["hora"]).total_seconds())).strftime("%H:%M:%S") + " (sesión operativa)"
        else:
            cerca_v = [h for h in horas_voz if h and abs((h - est["hora"]).total_seconds()) <= 900]
            est["audio_cerca"] = (min(cerca_v, key=lambda h: abs((h - est["hora"]).total_seconds())).strftime("%H:%M:%S") + " (voz sin cobro explícito)") if cerca_v else ""
        est["foto_camara"] = _buscar_foto_camara(est["hora"].strftime("%Y-%m-%d %H:%M:%S"))

    # Análisis de Demanda de Habitaciones, Rechazos por Ocupación y Sustitución vs. Venta Perdida
    demanda_habitaciones, resumen_demanda = _analizar_demanda_periodo(con_texto, sesiones, estancias)

    fuentes: Dict[str, int] = {}
    for c in clips:
        tr = (c.get("transcripcion") or "").lower()
        f = "PENDIENTE" if (not tr or "pendiente de transcripci" in tr) else (c.get("fuente_transcripcion") or "GEMINI")
        fuentes[f] = fuentes.get(f, 0) + 1
    return {"clips": clips, "eventos": eventos, "estancias": estancias, "gastos": gastos, "pases": pases,
            "ventas_tienda": ventas_tienda, "conciliacion_yape": conciliacion_yape,
            "relevos_horario": relevos_horario, "resumen_caja_efectivo": resumen_caja_efectivo,
            "demanda_habitaciones": demanda_habitaciones, "resumen_demanda": resumen_demanda,
            "fuentes": fuentes, "sesiones": sesiones}


def _analizar_demanda_periodo(
    con_texto: List[Dict[str, Any]],
    sesiones: List[Dict[str, Any]],
    estancias: List[Dict[str, Any]],
) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Une conversaciones consecutivas (≤210 s) donde un cliente pregunta por habitación,
    identifica qué tipo de habitación busca, si estaba ocupada, cuál era el requisito
    indispensable para ese cliente, y cruza con los check-ins reales (±20 min) para saber:
      - `1-SE_VA_POR_OCUPACION`           : Estaba ocupado su tipo indispensable y se retiró sin hospedarse.
      - `2-ACEPTA_SUSTITUCION`            : No había lo que buscaba (o negoció precio/tipo) y aceptó otra habitación menor o mayor.
      - `3-SE_VA_POR_PRECIO_O_CONSULTA`   : Había disponibilidad (o pidió por horas en fin de semana) y se retiró tras consultar.
      - `4-INGRESA_HABITACION_SOLICITADA` : Había disponibilidad del tipo buscado e ingresó normalmente.
    """
    bloques: List[Dict[str, Any]] = []
    clips_ord = sorted(con_texto, key=lambda x: x.get("fecha_hora_inicio") or "")
    for c in clips_ord:
        tr = (c.get("transcripcion") or "").strip()
        if not tr or "sin voz inteligible" in tr.lower():
            continue
        tipo_c = c.get("tipo_interaccion") or ""
        if tipo_c in ("AUDIO_FONDO", "RELEVO_INVENTARIO_TIENDA", "DEUDA_INTERNA_PERSONAL", "GASTO_CAJA", "PASE_VISITA"):
            continue
        t_c = _ts(c.get("fecha_hora_inicio") or "")
        if not t_c:
            continue
        if bloques and (t_c - bloques[-1]["t_fin"]).total_seconds() <= 210:
            bloques[-1]["t_fin"] = t_c
            bloques[-1]["textos"].append(tr)
            bloques[-1]["clips"].append(c)
        else:
            bloques.append({
                "t_ini": t_c,
                "t_fin": t_c,
                "hora_str": c.get("fecha_hora_inicio") or "",
                "textos": [tr],
                "clips": [c],
            })

    demanda_lista: List[Dict[str, Any]] = []
    estancias_usadas = set()

    for b in bloques:
        texto_unido = " ".join(b["textos"])
        t_low = lr._sin_tildes(texto_unido.lower())
        # Ignorar charlas internas del personal sobre limpieza, auditoría o turnos
        if any(p in t_low for p in (
            "auditoria de audio", "informes diarios", "lucero se quiere cambiar",
            "no la pienso limpiar", "tumbando la puerta", "envios son diarios",
            "senor montalvo", "grupo suarez", "marvison", "shalom",
            "cancelacion de la estancia", "devolucion efectivo",
        )):
            continue

        info_d = lr.analizar_demanda_habitacion(texto_unido)
        if not info_d.get("es_consulta_habitacion"):
            continue

        # Exigir señales reales de atención a cliente pidiendo/cotizando cuarto o rechazo por ocupación
        tiene_interaccion_cliente = bool(
            info_d.get("estaba_ocupado")
            or info_d.get("hotel_lleno_total")
            or any(c.get("tipo_interaccion") in ("COTIZACION_CONSULTA", "COBRO_REAL") for c in b["clips"])
            or any(w in t_low for w in (
                "buenas", "buen dia", "buenos dias", "buenas tardes", "buenas noches",
                "tienes habitacion", "tiene habitacion", "cuanto esta", "a cuanto",
                "matrimonial", "simple", "doble", "familiar", "bano privado", "bano compartido",
                "no hay habitacion", "me queda", "no tengo",
            ))
        )
        if not tiene_interaccion_cliente:
            continue

        # Buscar si hubo check-in en estancias entre -5 min y +22 min
        mejor_est = None
        menor_dt = 99999.0
        for idx_e, est in enumerate(estancias):
            if idx_e in estancias_usadas:
                continue
            diff_s = (est["hora"] - b["t_ini"]).total_seconds()
            if -300 <= diff_s <= 1320:
                # Priorizar si coincide DNI o Habitación hablada en el bloque
                coincide_dato = False
                for c in b["clips"]:
                    if (c.get("dni_detectado") and c.get("dni_detectado") == est.get("dni")) or (
                        c.get("hab_detectada") and c.get("hab_detectada") == est.get("hab")
                    ):
                        coincide_dato = True
                        break
                score_dt = abs(diff_s) - (600.0 if coincide_dato else 0.0)
                if score_dt < menor_dt:
                    menor_dt = score_dt
                    mejor_est = (idx_e, est)

        se_quedo = mejor_est is not None
        est_match = mejor_est[1] if mejor_est else None
        if mejor_est:
            estancias_usadas.add(mejor_est[0])

        estaba_ocup = bool(info_d.get("estaba_ocupado") or info_d.get("hotel_lleno_total"))
        alt_ofrecida = str(info_d.get("alternativa_ofrecida") or "Misma habitación solicitada")
        hubo_sustitucion = se_quedo and (
            estaba_ocup
            or alt_ofrecida not in ("Misma habitación solicitada", "Sin alternativa disponible")
        )

        if se_quedo and hubo_sustitucion:
            decision = "2-ACEPTA_SUSTITUCION (Se queda en alternativa menor/mayor)"
            explicacion = (
                f"Aceptó alternativa ({alt_ofrecida}) → Ingresó a Hab. {est_match['hab']} "
                f"({est_match['huesped']}) pagando S/ {est_match['pagado']:.2f} (Tarifa S/ {est_match['tarifa']:.2f})"
            )
        elif se_quedo:
            decision = "4-INGRESA_HABITACION_SOLICITADA (Se queda)"
            explicacion = (
                f"Encontró lo que buscaba → Ingresó a Hab. {est_match['hab']} "
                f"({est_match['huesped']}) pagando S/ {est_match['pagado']:.2f}"
            )
        elif estaba_ocup or "Por Horas" in str(info_d.get("requisito_indispensable") or ""):
            decision = "1-SE_VA_POR_OCUPACION (Venta perdida: no había lo que necesitaba)"
            explicacion = (
                f"SE RETIRÓ SIN HOSPEDARSE — Para este cliente era indispensable: "
                f"{info_d['requisito_indispensable']}. Alternativa ofrecida: {alt_ofrecida}"
            )
        else:
            decision = "3-SE_VA_POR_PRECIO_O_CONSULTA (Solo cotizó y se retiró)"
            explicacion = (
                f"Consultó {info_d['tipo_solicitado']} y se retiró sin tomar habitación "
                f"(Requisito/consulta: {info_d['requisito_indispensable']})"
            )

        archivos_b = ", ".join(Path(c["archivo_wav"]).name for c in b["clips"] if c.get("archivo_wav"))
        foto_b = _buscar_foto_camara(b["hora_str"], archivos_b)
        montos_b = info_d.get("montos_hablados") or []
        precio_cot = max([m for m in montos_b if 25 <= m <= 250], default=0.0)

        demanda_lista.append({
            "hora": b["hora_str"],
            "tipo_solicitado": info_d["tipo_solicitado"],
            "requisito_indispensable": info_d["requisito_indispensable"],
            "estaba_ocupado": "SÍ (Ocupado/Agotado)" if estaba_ocup else "No (Disponible)",
            "alternativa_ofrecida": alt_ofrecida,
            "decision_cliente": decision,
            "se_quedo": "SÍ (Se hospedó)" if se_quedo else "NO (Se retiró)",
            "hab_asignada": est_match["hab"] if est_match else "-",
            "huesped": est_match["huesped"] if est_match else "-",
            "monto_cotizado": precio_cot,
            "monto_pagado": float(est_match["pagado"]) if est_match else 0.0,
            "explicacion": explicacion,
            "transcripcion": texto_unido[:500],
            "foto_camara": foto_b,
            "foto_nombre": Path(foto_b).name if foto_b else "-",
            "archivos": archivos_b,
        })

    # Ordenar priorizando: 1-SE_VA_POR_OCUPACION, 2-ACEPTA_SUSTITUCION, 3-SE_VA_POR_PRECIO, 4-INGRESA
    demanda_lista.sort(key=lambda x: (x["decision_cliente"][:2], x["hora"]))

    # Resumen estadístico por tipo y motivo de retiro/sustitución
    conteo_por_decision: Dict[str, int] = {}
    indispensables_perdidos: Dict[str, int] = {}
    sustituciones_aceptadas: Dict[str, int] = {}
    conteo_tipos: Dict[str, int] = {}

    for d in demanda_lista:
        dec = d["decision_cliente"]
        conteo_por_decision[dec] = conteo_por_decision.get(dec, 0) + 1
        t_sol = d["tipo_solicitado"]
        conteo_tipos[t_sol] = conteo_tipos.get(t_sol, 0) + 1
        if dec.startswith("1-"):
            req = d["requisito_indispensable"]
            indispensables_perdidos[req] = indispensables_perdidos.get(req, 0) + 1
        elif dec.startswith("2-"):
            alt = d["alternativa_ofrecida"]
            sustituciones_aceptadas[alt] = sustituciones_aceptadas.get(alt, 0) + 1

    resumen_demanda = {
        "total_consultas": len(demanda_lista),
        "se_van_por_ocupacion": sum(1 for d in demanda_lista if d["decision_cliente"].startswith("1-")),
        "aceptan_sustitucion": sum(1 for d in demanda_lista if d["decision_cliente"].startswith("2-")),
        "se_van_por_precio": sum(1 for d in demanda_lista if d["decision_cliente"].startswith("3-")),
        "ingresan_directo": sum(1 for d in demanda_lista if d["decision_cliente"].startswith("4-")),
        "indispensables_perdidos": indispensables_perdidos,
        "sustituciones_aceptadas": sustituciones_aceptadas,
        "conteo_tipos": conteo_tipos,
    }
    return demanda_lista, resumen_demanda


def escribir_xlsx(datos: Dict[str, Any], desde: str, hasta: str, ruta: Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    colores = {"1-ALTA": "F8D7DA", "2-MEDIA": "FFF3CD", "3-INFORMATIVO": "E2EFDA"}
    cab_fill = PatternFill("solid", fgColor="1F3864")
    cab_font = Font(color="FFFFFF", bold=True)
    wb = Workbook()

    def hoja(nombre: str, cabecera: List[str], filas: List[List[Any]], anchos: List[int], dv_col: int = 0, dv_opts=None):
        ws = wb.create_sheet(nombre)
        ws.append(cabecera)
        for c in ws[1]:
            c.fill, c.font = cab_fill, cab_font
            c.alignment = Alignment(wrap_text=True, vertical="center")
        for f in filas:
            ws.append(f)
        for i, w in enumerate(anchos, 1):
            ws.column_dimensions[ws.cell(1, i).column_letter].width = w
        for row in ws.iter_rows(min_row=2):
            for c in row:
                c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        if dv_col and filas:
            letra = ws.cell(1, dv_col).column_letter
            dv = DataValidation(type="list", formula1='"' + ",".join(dv_opts) + '"', allow_blank=True)
            ws.add_data_validation(dv)
            dv.add(f"{letra}2:{letra}{len(filas) + 1}")
        return ws

    # 1. Resumen
    ws = wb.active
    ws.title = "1_Resumen"
    ev = datos["eventos"]
    rd = datos.get("resumen_demanda") or {}
    n_alta = sum(1 for e in ev if e["prioridad"] == "1-ALTA")
    n_media = sum(1 for e in ev if e["prioridad"] == "2-MEDIA")
    lineas = [
        [f"Informe de corroboración — Auditoría de audio de recepción (Turno 8:00 AM a 8:00 AM) — {NOMBRE_HOTEL}"],
        [f"Periodo auditado (8:00 AM a 8:00 AM): {desde}  →  {hasta}"],
        [f"Generado: {dt.datetime.now().strftime('%d/%m/%Y %H:%M')}"],
        [""],
        ["IMPORTANTE: este informe NO modifica la hoja maestra ni las tablas del sistema. Es solo para revisar y decidir."],
        ["Cómo usarlo: en '2_Eventos' revise primero prioridad 1-ALTA; en '4_Demanda_Habitaciones' revise qué habitaciones pidieron los clientes, cuáles estaban ocupadas y quiénes aceptaron otra vs. quiénes se fueron."],
        [""],
        ["Clips grabados en el turno (8:00 AM a 8:00 AM)", len(datos["clips"])],
        ["Transcripción por fuente", ", ".join(f"{k}: {v}" for k, v in sorted(datos["fuentes"].items()))],
        ["Eventos a revisar (sesiones + clips relevantes)", len(ev)],
        ["  Prioridad 1-ALTA", n_alta],
        ["  Prioridad 2-MEDIA", n_media],
        ["Check-ins registrados en el sistema en el periodo", len(datos["estancias"])],
        ["Consultas de habitaciones detectadas en mostrador", rd.get("total_consultas", 0)],
        ["  Se retiraron porque su tipo indispensable estaba ocupado (Venta Perdida)", rd.get("se_van_por_ocupacion", 0)],
        ["  Aceptaron otra habitación menor o mayor para quedarse (Sustitución)", rd.get("aceptan_sustitucion", 0)],
        ["  Consultaron precio / por horas y se retiraron", rd.get("se_van_por_precio", 0)],
        ["  Ingresaron directo al tipo de habitación solicitado", rd.get("ingresan_directo", 0)],
        ["Pagos Yape en CONCILIACION PAGOS YAPE en el periodo", len(datos.get("conciliacion_yape", []))],
        ["Ventas de tienda en el periodo", len(datos.get("ventas_tienda", []))],
        ["Gastos registrados en el periodo", len(datos["gastos"])],
        ["Pases registrados en el periodo", len(datos["pases"])],
        [""],
        ["Limitaciones: los clips con fuente WHISPER_LOCAL pueden tener errores en nombres y DNIs; "
         "los marcados PENDIENTE aún no tienen texto. Verifique siempre con el audio antes de decidir."],
    ]
    for l in lineas:
        ws.append(l)
    ws["A1"].font = Font(bold=True, size=14)
    ws["A5"].font = Font(bold=True, color="C00000")
    ws.column_dimensions["A"].width = 70
    ws.column_dimensions["B"].width = 50

    # 2. Eventos a corroborar
    filas = []
    for i, e in enumerate(ev, 1):
        filas.append([
            i, e["prioridad"], e["hora"][11:], e["hab"], e["tipo"], e["voz"],
            (f"{e['dni']} {e['persona']}".strip()), e["monto_audio"] or "", e["monto_sistema"] or "",
            e["resultado"], e.get("veredicto_verificador", ""),
            ", ".join(e["banderas"]) + (f" | {e['alerta']}" if e["alerta"] else ""),
            e["fuente"], e["n_clips"], e["archivos"], e.get("foto_nombre", "-"), e["transcripcion"][:1500], "", "", "",
        ])
    ws2 = hoja("2_Eventos", ["N°", "Prioridad", "Hora", "Hab.", "Tipo", "Voz / personal", "DNI / persona",
                             "Monto en audio S/", "Monto en sistema S/", "Resultado automático", "Verificador Audio Imou",
                             "Banderas / alertas", "Fuente texto", "Clips", "Archivo(s) de audio", "Foto Cámara Imou",
                             "Transcripción", "Decisión", "Comentario", "Revisado por"],
               filas, [5, 12, 10, 6, 22, 26, 24, 10, 10, 50, 32, 30, 14, 6, 34, 24, 70, 20, 30, 14], dv_col=18, dv_opts=DECISIONES)
    for r_i, e in enumerate(ev, 2):
        fill = PatternFill("solid", fgColor=colores[e["prioridad"]])
        for c in ws2[r_i][:3]:
            c.fill = fill

    # 3. Relevos de Turno (Horario) y Cuadre de Caja Efectivo (PanelDisponibilidad)
    rc = datos.get("resumen_caja_efectivo") or {}
    filas_relevos = [
        ["RESUMEN DE CAJA EFECTIVO (PanelDisponibilidad / Recojos)", "", "", "", "", "", ""],
        ["Saldo Actual Caja Efectivo en Panel (caja_sheets_g1)", f"S/ {rc.get('saldo_panel_actual', 0.0):.2f}", "Se acumula hasta que Admin registra RECOJO EFECTIVO en Gastos", "", "", "", ""],
        ["(+) Ingresos Efectivo Estancias del día", f"S/ {rc.get('efec_estancias_dia', 0.0):.2f}", "", "", "", "", ""],
        ["(+) Ingresos Efectivo Ventas Tienda del día", f"S/ {rc.get('efec_tienda_dia', 0.0):.2f}", "", "", "", "", ""],
        ["(-) Gastos Operativos en Efectivo del día", f"S/ {rc.get('gastos_operativos_efec_dia', 0.0):.2f}", "", "", "", "", ""],
        ["(-) Recojos de Efectivo por Admin (RECOJO EFECTIVO)", f"S/ {rc.get('total_recojos_dia', 0.0):.2f}",
         "; ".join(f"{x['hora'].strftime('%H:%M:%S')} S/ {x['monto']:.2f} ({x['descripcion']})" for x in rc.get("recojos_detalle", [])) or "Sin recojo en este día", "", "", "", ""],
        ["(=) Variación Neta Efectivo del Día", f"S/ {rc.get('flujo_neto_dia', 0.0):.2f}", "", "", "", "", ""],
        ["", "", "", "", "", "", ""],
        ["MARCACIONES DE HORARIO Y RELEVOS DE TURNO (horario_registros)", "Entrada 1", "Salida 1", "Entrada 2", "Salida 2", "Estado", "Foto Cámara Imou"],
    ]
    for r_h in datos.get("relevos_horario", []):
        filas_relevos.append([
            r_h["empleado"], r_h["e1"], r_h["s1"], r_h["e2"], r_h["s2"], r_h["estado"],
            Path(r_h["foto_camara"]).name if r_h.get("foto_camara") else "-",
        ])
    hoja("3_Relevos_y_Caja_Efectivo", ["Concepto / Personal", "Valor / E1", "Detalle / S1", "E2", "S2", "Estado", "Foto Cámara"],
         filas_relevos, [48, 16, 48, 12, 12, 18, 24])

    # 4. Demanda de Habitaciones, Rechazos por Ocupación y Sustitución vs. Venta Perdida
    demanda_h = datos.get("demanda_habitaciones") or []
    filas_demanda = []
    for idx_d, d in enumerate(demanda_h, 1):
        filas_demanda.append([
            idx_d,
            d["hora"][5:19],
            d["tipo_solicitado"],
            d["estaba_ocupado"],
            d["requisito_indispensable"],
            d["alternativa_ofrecida"],
            d["decision_cliente"],
            d["se_quedo"],
            d["hab_asignada"],
            d["huesped"],
            d["monto_cotizado"] or "",
            d["monto_pagado"] or "",
            d["explicacion"],
            d["foto_nombre"],
            d["transcripcion"],
        ])
    ws_dem = hoja(
        "4_Demanda_Habitaciones",
        [
            "N°", "Fecha y Hora", "Tipo de Habitación Solicitada", "¿Estaba Ocupado?",
            "Requisito Indispensable para el Cliente", "Alternativa Ofrecida por Recepción",
            "Reacción / Decisión del Cliente", "¿Se Quedó?", "Hab. Asignada", "Huésped Registrado",
            "Precio Cotizado S/", "Pagado S/", "Análisis Detallado", "Foto Cámara Imou", "Conversación en Mostrador",
        ],
        filas_demanda,
        [5, 16, 26, 18, 34, 34, 34, 15, 10, 26, 12, 12, 48, 22, 65],
    )
    colores_dem = {
        "1-": "F8D7DA",  # Rojo suave: Venta perdida por ocupación
        "2-": "FFF3CD",  # Amarillo suave: Aceptó sustitución menor/mayor
        "3-": "E2E3E5",  # Gris suave: Solo consultó precio y se retiró
        "4-": "E2EFDA",  # Verde suave: Ingresó directo al tipo solicitado
    }
    for r_i, d in enumerate(demanda_h, 2):
        pref_c = str(d.get("decision_cliente") or "")[:2]
        if pref_c in colores_dem:
            fill_d = PatternFill("solid", fgColor=colores_dem[pref_c])
            for c in ws_dem[r_i][:8]:
                c.fill = fill_d

    # 5. Registros del sistema vs audio
    filas3 = [[e["hora"].strftime("%d/%m %H:%M:%S"), e["hab"], e["huesped"], e["dni"], e["tarifa"], e["pagado"],
               e.get("medio", "-"), e["audio_cerca"] or "SIN CONVERSACIÓN CERCANA (±15 min)",
               Path(e["foto_camara"]).name if e.get("foto_camara") else "-", "", ""] for e in datos["estancias"]]
    hoja("5_Checkins_vs_audio", ["Registro sistema", "Hab.", "Huésped", "DNI", "Tarifa S/", "Pagado S/", "Medio",
                                 "Conversación de audio más cercana", "Foto Cámara Imou", "Decisión", "Comentario"],
         filas3, [16, 6, 34, 12, 10, 10, 10, 30, 24, 20, 30], dv_col=10, dv_opts=DECISIONES)

    # 6. Conciliación Pagos Yape (Cols A-C Banco, Cols D-F Estancia, Col G Cuadre)
    filas_yape = [
        [
            y["fecha_banco"], y["origen_banco"], y["monto_banco"],
            y["monto_excel"], y["huesped_excel"], y["hora_anotada"],
            y["cuadre"], y.get("fila_pagos", ""), y.get("fecha_excel", ""),
        ]
        for y in datos.get("conciliacion_yape", [])
    ]
    hoja(
        "6_Conciliacion_Yape",
        [
            "Col A: Fecha y Hora Banco", "Col B: Origen / Detalle Banco", "Col C: Monto Banco S/",
            "Col D: Monto Estancia/Excel S/", "Col E: Huésped Estancia/Excel", "Col F: Hora Anotada Excel",
            "Col G: Estado de Conciliación (Cuadre)", "Fila Pagos", "Fecha Registro Excel",
        ],
        filas_yape,
        [20, 28, 12, 12, 34, 16, 36, 12, 20],
    )

    # 7. Pases
    filas4 = [[p["hora"].strftime("%d/%m %H:%M"), p["hab"], p["huesped_titular"], p["dni_visita"], p["nombre_visita"],
               p["estado"], p["monto"]] for p in datos["pases"]]
    filas4 += [["(audio) " + e["hora"][11:], e["hab"], "", e["dni"], e["persona"], e["estado"], e["monto_audio"]]
               for e in ev if e["tipo"] == "PASE_VISITA"]
    hoja("7_Pases", ["Hora", "Hab.", "Titular", "DNI visita", "Nombre visita", "Estado / resultado", "S/"],
         filas4, [16, 6, 30, 12, 32, 30, 8])

    # 8. Caja y tienda
    filas5 = [[g["hora"].strftime("%d/%m %H:%M:%S"), "Gasto (sistema)", g["descripcion"], g["monto"], g["responsable"]] for g in datos["gastos"]]
    filas5 += [[vt["hora"].strftime("%d/%m %H:%M:%S"), "Venta Tienda (sistema)", vt["descripcion"], vt["monto"], vt["responsable"]] for vt in datos.get("ventas_tienda", [])]
    filas5 += [[e["hora"][11:], f"Audio: {e['tipo']}", e["resultado"] or e["transcripcion"][:200], e["monto_audio"] or "", e["voz"]]
               for e in ev if e["tipo"] in ("GASTO_CAJA", "RELEVO_INVENTARIO_TIENDA", "VENTA_TIENDA", "DEUDA_INTERNA_PERSONAL")]
    hoja("8_Caja_y_tienda", ["Hora", "Origen", "Detalle", "S/", "Responsable / voz"], filas5, [16, 26, 70, 10, 26])

    # 9. Mantenimiento
    filas6 = [[e["hora"][11:], e["hab"], e["resultado"], e["transcripcion"][:400], "", ""]
              for e in ev if e["tipo"] == "MANTENIMIENTO_LIMPIEZA"]
    hoja("9_Mantenimiento", ["Hora", "Hab.", "Detalle", "Transcripción", "¿Atendido?", "Comentario"],
         filas6, [10, 6, 50, 70, 12, 30], dv_col=5, dv_opts=["Sí", "No", "En proceso"])

    # 10. Mejoras
    filas7 = [[m[0], m[1], m[2], ""] for m in MEJORAS]
    hoja("10_Mejoras", ["Mejora", "Estado", "Motivo / evidencia", "¿Aprobar?"], filas7, [55, 22, 90, 14],
         dv_col=4, dv_opts=["Sí", "No", "Más adelante"])

    ruta.parent.mkdir(parents=True, exist_ok=True)
    wb.save(ruta)
    return ruta


def escribir_html(datos: Dict[str, Any], desde: str, hasta: str, ruta: Path) -> Path:
    e_ = html.escape
    ev = datos["eventos"]
    altas = [e for e in ev if e["prioridad"] == "1-ALTA"]
    medias = [e for e in ev if e["prioridad"] == "2-MEDIA"]

    def tabla(lista):
        if not lista:
            return "<p><i>Sin eventos.</i></p>"
        filas = "".join(
            f"<tr><td>{e_(x['hora'][11:])}</td><td>{e_(x['hab'] or '-')}</td><td>{e_(x['tipo'])}</td>"
            f"<td>{e_(x['resultado'])}</td><td><i>{e_(x['transcripcion'][:300])}</i></td><td>{e_(x['archivos'])}</td></tr>"
            for x in lista
        )
        return ("<table border='1' cellpadding='4' style='border-collapse:collapse;font-size:10pt'>"
                "<tr><th>Hora</th><th>Hab.</th><th>Tipo</th><th>Resultado automático</th><th>Lo que se escucha</th><th>Audio</th></tr>"
                f"{filas}</table>")

    mejoras = "".join(f"<li><b>{e_(m[0])}</b> — {e_(m[1])}. {e_(m[2])}</li>" for m in MEJORAS)
    cuerpo = f"""<html><head><meta charset="utf-8"><title>Informe de corroboración</title></head><body>
<h1>Informe de corroboración — Auditoría de audio</h1>
<p><b>{e_(NOMBRE_HOTEL)}</b> · Periodo: {e_(desde)} → {e_(hasta)} · Generado {dt.datetime.now().strftime('%d/%m/%Y %H:%M')}</p>
<p style="color:#C00000"><b>Este informe no modifica la hoja maestra ni el sistema. Sirve para corroborar y decidir.</b></p>
<p>Clips: {len(datos['clips'])} · Fuente del texto: {e_(', '.join(f'{k}: {v}' for k, v in sorted(datos['fuentes'].items())))} ·
Eventos a revisar: {len(ev)} (alta: {len(altas)}, media: {len(medias)})</p>
<h2>1. Prioridad alta</h2>{tabla(altas)}
<h2>2. Prioridad media</h2>{tabla(medias)}
<h2>3. Mejoras al sistema</h2><ul>{mejoras}</ul>
<p><i>Los textos de fuente WHISPER_LOCAL pueden contener errores de nombres o números: confirmar con el audio.</i></p>
</body></html>"""
    ruta.write_text(cuerpo, encoding="utf-8")
    return ruta


def generar(desde: str, hasta: str) -> Dict[str, Path]:
    datos = recolectar(desde, hasta)
    sufijo = f"{desde[:10]}_{desde[11:13]}h_a_{hasta[:10]}_{hasta[11:13]}h"
    rx = escribir_xlsx(datos, desde, hasta, SALIDA_DIR / f"CORROBORACION_{sufijo}.xlsx")
    rh = escribir_html(datos, desde, hasta, SALIDA_DIR / f"CORROBORACION_{sufijo}.html")
    resumen = {
        "xlsx": str(rx), "html": str(rh), "clips": len(datos["clips"]), "fuentes": datos["fuentes"],
        "eventos": len(datos["eventos"]),
        "alta": [(e["hora"][11:], e["hab"], e["tipo"], e["estado"], e["resultado"][:150]) for e in datos["eventos"] if e["prioridad"] == "1-ALTA"],
        "media": [(e["hora"][11:], e["hab"], e["tipo"], e["estado"], e["resultado"][:150]) for e in datos["eventos"] if e["prioridad"] == "2-MEDIA"],
        "checkins": [(x["hora"].strftime("%H:%M"), x["hab"], x["huesped"], x["tarifa"], x["pagado"], x["audio_cerca"]) for x in datos["estancias"]],
    }
    (SALIDA_DIR / f"CORROBORACION_{sufijo}.json").write_text(json.dumps(resumen, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return {"xlsx": rx, "html": rh, "resumen": resumen}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", required=True)
    ap.add_argument("--hasta", required=True)
    a = ap.parse_args()
    r = generar(a.desde, a.hasta)
    print(json.dumps(r["resumen"], ensure_ascii=False, indent=2, default=str))
