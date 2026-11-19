#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Aprendizaje Estadístico de Reglas de Auditoría desde Operativo 2026
Calcula ticket promedio, mediana, percentiles 98/99 y detector de cero extra.
Guarda los parámetros en C:\\Hostal_Sistema\\reglas_auditoria.json
"""

import sys
import os
import re
import json
from datetime import datetime
import numpy as np
from collections import Counter
import gspread
from google.oauth2.service_account import Credentials

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pathlib import Path
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

try:
    from secretos_hostal import obtener_ruta_credentials_google as _obtener_ruta_credentials_google
    RUTA_CREDS = _obtener_ruta_credentials_google()
except ImportError:
    RUTA_CREDS = r"C:\Hostal_Sistema\config\credentials.json"
try:
    from hoja_activa import obtener_spreadsheet_id as _obtener_spreadsheet_id
    SPREADSHEET_ID = _obtener_spreadsheet_id()
except ImportError:
    if os.environ.get("OVALO_ENTORNO", "operativa").strip().lower() != "operativa":
        raise SystemExit("⛔ ERROR CRÍTICO: No se pudo importar hoja_activa en modo PRUEBA.")
    print("[WARN] hoja_activa no disponible; usando Hoja Operativa por defecto.", file=sys.stderr)
    SPREADSHEET_ID = "1KudqxM1D6MSCxT5Zb2_vAOTGxnw9Ja92JuFjFpDuxZ8"
OUTPUT_JSON = r"C:\Hostal_Sistema\reglas_auditoria.json"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

def clean_num(x):
    if not x:
        return 0.0
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).replace("S/", "").replace(",", "").strip()
    try:
        return float(s)
    except Exception:
        return 0.0

def calcular_y_guardar_reglas():
    print("[1/3] Conectando a Google Sheets Operativo 2026...", flush=True)
    creds = Credentials.from_service_account_file(RUTA_CREDS, scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)
    ws = sh.worksheet("Registro_Estancias")
    rows = ws.get_all_values(value_render_option="UNFORMATTED_VALUE")

    print(f"[2/3] Procesando {len(rows)-1} registros históricos...", flush=True)
    montos_estancia = []
    montos_comprobante = []
    tarifas_diarias = []

    for r in rows[1:]:
        v = clean_num(r[21] if len(r) > 21 else 0)   # Col V: Total_Cuenta
        aj = clean_num(r[35] if len(r) > 35 else 0)  # Col AJ: monto factura
        noches = clean_num(r[15] if len(r) > 15 else 1) or 1
        tarifa = clean_num(r[16] if len(r) > 16 else 0) # Col Q: Tarifa_Noche

        if v > 0:
            montos_estancia.append(v)
            if tarifa > 0:
                tarifas_diarias.append(tarifa)
            else:
                tarifas_diarias.append(round(v / noches, 2))
        if aj > 0:
            montos_comprobante.append(aj)

    v_arr = np.array(montos_estancia)
    aj_arr = np.array(montos_comprobante) if montos_comprobante else v_arr
    t_arr = np.array(tarifas_diarias)

    # Estadísticas de Estancias (Cobros Reales)
    ticket_promedio_v = round(float(np.mean(v_arr)), 2)
    mediana_v = round(float(np.median(v_arr)), 2)
    p90_v = round(float(np.percentile(v_arr, 90)), 2)
    p95_v = round(float(np.percentile(v_arr, 95)), 2)
    p98_v = round(float(np.percentile(v_arr, 98)), 2)
    p99_v = round(float(np.percentile(v_arr, 99)), 2)
    max_v = round(float(np.max(v_arr)), 2)

    # Estadísticas de Comprobantes Emitidos (AJ)
    ticket_promedio_cp = round(float(np.mean(aj_arr)), 2)
    mediana_cp = round(float(np.median(aj_arr)), 2)
    p95_cp = round(float(np.percentile(aj_arr, 95)), 2)
    p98_cp = round(float(np.percentile(aj_arr, 98)), 2)
    p99_cp = round(float(np.percentile(aj_arr, 99)), 2)
    max_cp = round(float(np.max(aj_arr)), 2)

    # Tarifas estándar detectadas
    c_tarifas = Counter([round(x, 1) for x in t_arr if 20 <= x <= 200])
    top_tarifas = sorted([float(k) for k, count in c_tarifas.most_common(12) if count >= 20])
    if not top_tarifas:
        top_tarifas = [30.0, 35.0, 40.0, 45.0, 50.0, 55.0, 60.0, 70.0, 80.0, 85.0, 90.0, 100.0, 120.0, 140.0]

    # Umbral de Alerta:
    # Se define en S/ 250.00 (supera el P99 de S/ 190.00 y P99 de comprobantes S/ 210.00).
    # Todo comprobante individual > S/ 250.00 debe ser auditado inmediatamente.
    umbral_alerta = max(250.0, round(p99_cp / 10.0) * 10.0 + 40.0)
    umbral_critico = 500.0

    # Valores propensos a 'cero extra': tarifa * 10
    cero_extra_valores = sorted([round(t * 10, 2) for t in top_tarifas])

    reglas = {
        "metadata": {
            "fecha_aprendizaje": datetime.now().isoformat(),
            "fuente_datos": "Google Sheets Operativo 2026 - Registro_Estancias",
            "total_estancias_analizadas": len(v_arr),
            "total_comprobantes_analizados": len(aj_arr),
        },
        "estadisticas_estancias": {
            "ticket_promedio": ticket_promedio_v,
            "mediana": mediana_v,
            "percentil_90": p90_v,
            "percentil_95": p95_v,
            "percentil_98": p98_v,
            "percentil_99": p99_v,
            "maximo_historico": max_v,
        },
        "estadisticas_comprobantes_aj": {
            "ticket_promedio": ticket_promedio_cp,
            "mediana": mediana_cp,
            "percentil_95": p95_cp,
            "percentil_98": p98_cp,
            "percentil_99": p99_cp,
            "maximo_historico": max_cp,
        },
        "tarifas_estandar": top_tarifas,
        "umbrales_auditoria": {
            "umbral_alerta_monto": umbral_alerta,
            "umbral_alerta_critico": umbral_critico,
            "descripcion_umbral": f"Alerta cuando un comprobante individual supera S/ {umbral_alerta:.2f} (Percentil 99 es S/ {p99_cp:.2f}).",
        },
        "regla_cero_extra": {
            "activo": True,
            "valores_sospechosos_cero_extra": cero_extra_valores,
            "descripcion": "Detecta si el monto es exactamente 10 veces una tarifa estándar (ej. 500 en vez de 50, 700 en vez de 70, 800 en vez de 80)."
        }
    }

    print("[3/3] Guardando reglas aprendidas en C:\\Hostal_Sistema\\reglas_auditoria.json...", flush=True)
    os.makedirs(os.path.dirname(OUTPUT_JSON), exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(reglas, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 65)
    print(" REGLAS DE AUDITORÍA APRENDIDAS DESDE OPERATIVO 2026")
    print("=" * 65)
    print(f"Estancias analizadas       : {len(v_arr):,}")
    print(f"Comprobantes (AJ)          : {len(aj_arr):,}")
    print(f"Ticket Promedio Estancia   : S/ {ticket_promedio_v:.2f}")
    print(f"Mediana Estancia           : S/ {mediana_v:.2f}")
    print(f"Percentil 98 (Estancias)   : S/ {p98_v:.2f}")
    print(f"Percentil 99 (Estancias)   : S/ {p99_v:.2f}")
    print(f"Percentil 98 (Comprobantes): S/ {p98_cp:.2f}")
    print(f"Percentil 99 (Comprobantes): S/ {p99_cp:.2f}")
    print("-" * 65)
    print(f"Tarifas estándar habituales: {top_tarifas}")
    print(f"🔴 UMBRAL DE ALERTA        : S/ {umbral_alerta:.2f}")
    print(f"🚨 UMBRAL CRÍTICO          : S/ {umbral_critico:.2f}")
    print(f"🔍 Detección 'Cero Extra'  : Valores como {cero_extra_valores[:6]}...")
    print("=" * 65)

    return reglas

if __name__ == "__main__":
    calcular_y_guardar_reglas()
