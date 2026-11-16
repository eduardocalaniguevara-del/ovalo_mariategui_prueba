#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Set de AUDIOS DE REFERENCIA del auditor de recepción + evaluador de precisión.

- Copia a `data/audios_referencia/` los clips elegidos (solo copia; no borra ni modifica originales).
- Guarda `referencias.json` con: hora, archivo, transcripción REAL (Gemini, 04/11/2026 20:01–21:16)
  y la etiqueta esperada (tipo / habitación / DNI / monto / banderas) definida al revisar cada caso.
- Modo texto  : evalúa las reglas de lenguaje sobre la transcripción de referencia.
- Modo --audio: transcribe el audio con Whisper local y evalúa todo el flujo (audio -> reglas).

Uso:
    python evaluar_referencias.py            # construye/actualiza el set y evalúa en modo texto
    python evaluar_referencias.py --audio    # además evalúa audio -> Whisper -> reglas
"""
import json
import shutil
import sqlite3
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
from config import DATA_DIR, DB_PATH  # noqa: E402

REF_DIR = DATA_DIR / "audios_referencia"
REF_JSON = REF_DIR / "referencias.json"

# Etiquetas esperadas (revisadas a mano a partir de la transcripción real de cada clip).
# precio: número exacto, lista de válidos, o None si no aplica.
ESPERADOS = [
    ("2026-11-04 20:09:25", "DEUDA_INTERNA_PERSONAL", "", "", 25, [], "Deuda personal Eliana→Eleiza (no es ingreso)"),
    ("2026-11-04 20:16:27", "COTIZACION_CONSULTA", "", "", 40, [], "Pregunta por cuartos de 40"),
    ("2026-11-04 20:18:38", "PRUEBA_SISTEMA", "", "74118293", None, [], "DNI del admin dictado dígito a dígito (capacitación)"),
    ("2026-11-04 20:19:29", "VENTA_TIENDA", "", "", None, [], "Venta de agua en mostrador"),
    ("2026-11-04 20:24:36", "COTIZACION_CONSULTA", "", "", [75, 80], [], "Cotiza matrimonial 75 / con tina 80"),
    ("2026-11-04 20:25:40", "COBRO_REAL", "", "", 150, [], "Cobro 2 noches S/150 por QR"),
    ("2026-11-04 20:34:19", "MANTENIMIENTO_LIMPIEZA", "102", "", None, [], "Sin Wi-Fi en Hab. 102 (Carlos Ramos)"),
    ("2026-11-04 20:35:47", "LIQUIDACION_CUENTA", "", "", 23, ["SALDO_O_VUELTO_PENDIENTE"], "Huésped con deuda, solo pagó 23"),
    ("2026-11-04 20:38:52", "COTIZACION_CONSULTA", "", "", 40, ["POR_HORAS"], "Por horas: 55 → 40 (mínimo simple)"),
    ("2026-11-04 20:43:59", "COTIZACION_CONSULTA", "", "", 95, [], "Cotiza 95"),
    ("2026-11-04 20:50:09", "PASE_VISITA", "206", "45582328", None, [], "Visita Hab. 206 con DNI dictado"),
    ("2026-11-04 20:52:17", "COBRO_REAL", "202", "", 20, [], "Abono S/20 Hab. 202"),
    ("2026-11-04 20:53:49", None, "", "", None, ["ALERTA_REGISTRO_DATOS_FALSOS"], "Conversación sobre poner nombre 'fake'"),
    ("2026-11-04 21:01:02", "COBRO_REAL", "", "75484415", None, [], "Check-in Raúl Mamani (DNI dígito a dígito)"),
    ("2026-11-04 21:02:31", None, "406", "", None, ["SALDO_O_VUELTO_PENDIENTE"], "Vuelto pendiente S/5 a Hab. 406"),
    ("2026-11-04 21:10:26", "AUDIO_FONDO", "", "", None, [], "Live de venta de ropa (fondo)"),
    ("2026-11-04 21:13:16", "CHARLA_INTERNA", "", "", None, ["CAMBIO_SENCILLO"], "Cambio de billete por sencillo"),
    ("2026-11-04 21:13:25", "CONTINUAR_ESTANCIA", "", "", 40, [], "Continúa hasta las 4 por S/40"),
    ("2026-11-04 21:14:18", "AUDIO_FONDO", "", "", None, [], "Live de venta de ropa (fondo)"),
    ("2026-11-04 21:15:35", "PASE_VISITA", "206", "", None, [], "Pase Hab. 206"),
]


def construir_set() -> list:
    REF_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    refs = []
    for ts, tipo, hab, dni, precio, banderas, nota in ESPERADOS:
        r = conn.execute(
            "SELECT archivo_wav, duracion_seg, transcripcion FROM auditoria_audio_noche WHERE fecha_hora_inicio = ? LIMIT 1",
            (ts,),
        ).fetchone()
        if not r:
            continue
        src = Path(r["archivo_wav"])
        dst = REF_DIR / f"{ts[:10]}_{src.name}"
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)
        refs.append({
            "hora": ts, "archivo": dst.name, "duracion_seg": r["duracion_seg"],
            "transcripcion_referencia": r["transcripcion"], "fuente": "Gemini (04/11/2026, antes del corte de saldo)",
            "esperado": {"tipo": tipo, "hab": hab, "dni": dni, "precio": precio, "banderas": banderas},
            "nota": nota,
        })
    conn.close()
    REF_JSON.write_text(json.dumps(refs, ensure_ascii=False, indent=2), encoding="utf-8")
    return refs


def _comparar(esp: dict, obt: dict) -> list:
    fallas = []
    if esp["tipo"] and obt["tipo_interaccion"] != esp["tipo"]:
        fallas.append(f"tipo {obt['tipo_interaccion']}≠{esp['tipo']}")
    if esp["hab"] and obt["hab_detectada"] != esp["hab"]:
        fallas.append(f"hab {obt['hab_detectada'] or '-'}≠{esp['hab']}")
    if esp["dni"] and obt["dni_detectado"] != esp["dni"]:
        fallas.append(f"dni {obt['dni_detectado'] or '-'}≠{esp['dni']}")
    if esp["precio"] is not None:
        validos = esp["precio"] if isinstance(esp["precio"], list) else [esp["precio"]]
        if float(obt["precio_hablado"] or 0) not in [float(v) for v in validos]:
            fallas.append(f"S/ {obt['precio_hablado']}∉{validos}")
    for b in esp["banderas"]:
        if b not in (obt.get("banderas") or []):
            fallas.append(f"falta bandera {b}")
    return fallas


def evaluar(refs: list, textos: dict, etiqueta: str) -> int:
    import auditor_audio_noche as aan

    ok = 0
    print(f"\n=== Evaluación ({etiqueta}) ===")
    for ref in refs:
        texto = textos.get(ref["archivo"], "")
        obt = aan.extraer_datos_por_reglas(texto)
        fallas = _comparar(ref["esperado"], obt)
        ok += not fallas
        marca = "✅" if not fallas else "❌"
        print(f"{marca} {ref['hora'][11:]} {ref['nota'][:46]:<46} -> {obt['tipo_interaccion']:<24} "
              f"hab={obt['hab_detectada'] or '-':<4} dni={obt['dni_detectado'] or '-':<9} S/{obt['precio_hablado']:<6}"
              + (f"  [{'; '.join(fallas)}]" if fallas else ""))
    print(f"Resultado {etiqueta}: {ok}/{len(refs)} correctos ({100*ok/max(1,len(refs)):.0f}%)")
    return ok


if __name__ == "__main__":
    refs = construir_set()
    print(f"Set de referencia: {len(refs)} clips en {REF_DIR}")
    evaluar(refs, {r["archivo"]: r["transcripcion_referencia"] for r in refs}, "texto Gemini → reglas")
    if "--audio" in sys.argv:
        import auditor_audio_noche as aan
        res = aan.transcribir_local_whisper([REF_DIR / r["archivo"] for r in refs])
        textos_w = {Path(k).name: v.get("texto", "") for k, v in res.items() if v.get("ok")}
        for r in refs:
            r["transcripcion_whisper"] = textos_w.get(r["archivo"], "")
        REF_JSON.write_text(json.dumps(refs, ensure_ascii=False, indent=2), encoding="utf-8")
        evaluar(refs, textos_w, "audio → Whisper local → reglas")
