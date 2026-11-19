#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MOTOR DE LIMPIEZA POR FACTURA MULTIDÍA & EMISIÓN AUTOMÁTICA DE NOTAS DE CRÉDITO
Hotel El Óvalo Mariátegui - Laptop 2 (Recepción)
Fase 5 del Pipeline Diario de las 1:00 PM

Objetivo:
  1. Detectar Facturas (E001) emitidas en SUNAT que cubren varios días o estancias.
  2. Rastrear si en esas fechas y habitación (o para ese huésped/DNI) se emitieron
     Boletas (EB01) previas que sigan vigentes.
  3. Emitir automáticamente las Notas de Crédito en SUNAT para anular esas Boletas
     duplicadas (evitando doble tributación).
  4. Normalizar 'Registro_Estancias' prorrateando la cuota diaria en cada fila
     (sea una sola fila con Noches > 1, o múltiples filas creadas por 'Continuar estancia'),
     colocando la Factura en AI, el monto diario en AJ y marcando 'Emitido SUNAT'
     para que CALZADO_OCT quede 100% cuadrado en VERDE (OK_TRIPLE).
"""

import os
import sys
import re
import json
import socket
from datetime import datetime
import collections

try:
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr is not None:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import urllib3.util.connection as urllib3_cn
urllib3_cn.allowed_gai_family = lambda: socket.AF_INET

import gspread
from google.oauth2.service_account import Credentials

from pathlib import Path
BASE_DIR = r"C:\Hostal_Sistema"
SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from emitir_nota_credito_sunat import ejecutar_emision_nota_credito

try:
    from secretos_hostal import obtener_ruta_credentials_google as _obtener_ruta_credentials_google
    RUTA_CREDS = _obtener_ruta_credentials_google()
except ImportError:
    RUTA_CREDS = os.path.join(BASE_DIR, "config", "credentials.json")
try:
    from hoja_activa import obtener_spreadsheet_id as _obtener_spreadsheet_id
    SPREADSHEET_ID = _obtener_spreadsheet_id()
except ImportError:
    if os.environ.get("OVALO_ENTORNO", "operativa").strip().lower() != "operativa":
        raise SystemExit("⛔ ERROR CRÍTICO: No se pudo importar hoja_activa en modo PRUEBA.")
    print("[WARN] hoja_activa no disponible; usando Hoja Operativa por defecto.", file=sys.stderr)
    SPREADSHEET_ID = "1KudqxM1D6MSCxT5Zb2_vAOTGxnw9Ja92JuFjFpDuxZ8"
RUTA_LINEAS_JSON = os.path.join(BASE_DIR, "datos_sunat", "sunat_oct_lineas.json")
RUTA_RAW_BOLETAS = os.path.join(BASE_DIR, "datos_sunat", "sunat_boletas_octubre_raw.json")
RUTA_RAW_FACTURAS = os.path.join(BASE_DIR, "datos_sunat", "sunat_facturas_octubre_raw.json")


def a_num(val):
    if val is None or val == "":
        return 0.0
    if isinstance(val, (int, float)):
        return round(float(val), 2)
    s = str(val).replace("S/", "").replace(",", "").strip()
    try:
        return round(float(s), 2)
    except Exception:
        return 0.0


def normalizar_fecha(fec_str):
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", str(fec_str).strip())
    if m:
        return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{m.group(3)}"
    return str(fec_str).strip()


def cargar_datos_sunat(ruta_lineas=None, ruta_facturas=None, ruta_boletas=None):
    ruta_lineas = ruta_lineas or RUTA_LINEAS_JSON
    ruta_facturas = ruta_facturas or RUTA_RAW_FACTURAS
    ruta_boletas = ruta_boletas or RUTA_RAW_BOLETAS
    lineas = []
    if os.path.exists(ruta_lineas):
        with open(ruta_lineas, "r", encoding="utf-8") as f:
            lineas = json.load(f)

    facturas_raw = []
    if os.path.exists(ruta_facturas):
        with open(ruta_facturas, "r", encoding="utf-8") as f:
            data = json.load(f)
            facturas_raw = data.get("facturas", [])

    boletas_raw = []
    if os.path.exists(ruta_boletas):
        with open(ruta_boletas, "r", encoding="utf-8") as f:
            data = json.load(f)
            boletas_raw = data.get("boletas", [])

    return lineas, facturas_raw, boletas_raw


def actualizar_raw_boletas_con_nc(nueva_nc, ruta_boletas=None):
    """Guarda la nueva Nota de Crédito en sunat_boletas_<mes>_raw.json para persistencia inmediata."""
    ruta_boletas = ruta_boletas or RUTA_RAW_BOLETAS
    try:
        with open(ruta_boletas, "r", encoding="utf-8") as f:
            data = json.load(f)
        bols = data.get("boletas", [])
        
        # Verificar si ya existe
        nro_nc = nueva_nc.get("numero_cp", "")
        if not any(b.get("numero_cp") == nro_nc for b in bols):
            bols.append(nueva_nc)
            data["boletas"] = bols
            with open(ruta_boletas, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"   [SYNC RAW] Nota de Crédito {nro_nc} agregada a {os.path.basename(ruta_boletas)}")
    except Exception as e:
        print(f"   [WARN] Error actualizando raw boletas con NC: {e}")


def ejecutar_limpieza_facturas(modo_emision="definitivo", headless=True, mes=11):
    es_nov = (int(mes or 11) == 11)
    nombre_mes_carpeta = "Noviembre" if es_nov else "Octubre"
    ruta_lineas_mes = os.path.join(BASE_DIR, "datos_sunat", "sunat_nov_lineas.json" if es_nov else "sunat_oct_lineas.json")
    ruta_boletas_mes = os.path.join(BASE_DIR, "datos_sunat", "sunat_boletas_noviembre_raw.json" if es_nov else "sunat_boletas_octubre_raw.json")
    ruta_facturas_mes = os.path.join(BASE_DIR, "datos_sunat", "sunat_facturas_noviembre_raw.json" if es_nov else "sunat_facturas_octubre_raw.json")
    script_construir = "Construir_SUNAT_NOV.py" if es_nov else "Construir_SUNAT_OCT.py"
    script_calzar = "Calzar_Noviembre.py" if es_nov else "Calzar_Octubre.py"

    print("\n" + "=" * 75)
    print(f" FASE 5: MOTOR DE LIMPIEZA POR FACTURA MULTIDÍA Y EMISIÓN DE NC ({nombre_mes_carpeta.upper()})")
    print("=" * 75)

    try:
        from hoja_activa import es_prueba as _es_prueba_ha
        _en_prueba = bool(_es_prueba_ha())
    except ImportError:
        _en_prueba = os.environ.get("OVALO_ENTORNO", "operativa").strip().lower() != "operativa"

    if _en_prueba:
        aviso_sim = "🧪 [SIMULACIÓN MODO PRUEBA - NO EMITIDO EN SUNAT / NO ENVIADO POR WSP]"
        print(f" -> {aviso_sim}")
        return {
            "status": "SIMULADO_PRUEBA",
            "simulado": True,
            "aviso": aviso_sim,
            "facturas_procesadas": 0,
            "ncs_emitidas": [],
            "filas_normalizadas": 0
        }

    lineas_sunat, facturas_raw, boletas_raw = cargar_datos_sunat(ruta_lineas_mes, ruta_facturas_mes, ruta_boletas_mes)
    if not lineas_sunat:
        print("[INFO] No hay líneas SUNAT cargadas en sunat_oct_lineas.json.")
        return {"status": "OK", "ncs_emitidas": [], "estancias_normalizadas": 0}

    # 1. Mapear Notas de Crédito existentes (para saber qué boletas ya están anuladas)
    boletas_ya_anuladas = set()
    for l in lineas_sunat:
        if l.get("tipo") == "NOTA_CREDITO":
            cp_mod = str(l.get("cp_modificado", "")).strip()
            if cp_mod:
                boletas_ya_anuladas.add(cp_mod)
        for flg in l.get("flags", []):
            if str(flg).startswith("ANULA_A:") or str(flg).startswith("ANULADO_POR_NC:"):
                boletas_ya_anuladas.add(str(flg).split(":", 1)[1].strip())

    # 2. Agrupar líneas de Facturas vigentes por Factura y Habitación
    # Map: (nro_factura, habitacion) -> list of lineas
    facturas_por_hab = collections.defaultdict(list)
    facturas_info = {}

    for l in lineas_sunat:
        if l.get("tipo") == "FACTURA" and l.get("estado") == "VIGENTE":
            nro_fac = l["nro_comprobante"]
            hab = l.get("habitacion", "").strip().upper()
            if hab and hab != "GRUPAL":
                facturas_por_hab[(nro_fac, hab)].append(l)
                facturas_info[nro_fac] = {
                    "cliente": l.get("cliente", ""),
                    "doc": l.get("doc", ""),
                    "fecha_emision": l.get("fecha_emision", "")
                }

    print(f" -> Se detectaron {len(facturas_por_hab)} bloques de Facturas con habitación específica.")

    # 3. Detectar colisiones: Boletas activas en la misma habitación y noche que una Factura
    colisiones = []
    for (nro_fac, hab), lineas_fac in facturas_por_hab.items():
        noches_factura = {l["noche"]: l["monto_linea"] for l in lineas_fac}
        
        # Buscar boletas vigentes que ocupen esas mismas noches en esa misma habitación
        for b in lineas_sunat:
            if (
                b.get("tipo") == "BOLETA"
                and b.get("estado") == "VIGENTE"
                and b.get("habitacion", "").strip().upper() == hab
                and b.get("noche") in noches_factura
            ):
                nro_bol = b["nro_comprobante"]
                # Verificar si ya está anulada
                if nro_bol in boletas_ya_anuladas or f"EB01-{nro_bol}" in boletas_ya_anuladas or nro_bol.replace("EB01-", "") in boletas_ya_anuladas:
                    continue
                
                colisiones.append({
                    "nro_factura": nro_fac,
                    "hab": hab,
                    "noche": b["noche"],
                    "dia": b.get("dia", b["noche"][:2]),
                    "nro_boleta": nro_bol,
                    "monto_boleta": b["monto_linea"],
                    "cliente_boleta": b.get("cliente", ""),
                    "doc_boleta": b.get("doc", "")
                })

    # Deduplicar colisiones por nro_boleta
    boletas_a_anular = {}
    for c in colisiones:
        b_num = c["nro_boleta"]
        if b_num not in boletas_a_anular:
            boletas_a_anular[b_num] = c

    print(f" -> Boletas colisionantes activas detectadas para anulación con NC: {len(boletas_a_anular)}")

    # 4. Emitir Notas de Crédito para las boletas colisionantes
    ncs_emitidas = []
    for b_num, info_c in boletas_a_anular.items():
        print(f"\n⚡ [EMITIENDO NOTA DE CRÉDITO] Boleta {b_num} (S/ {info_c['monto_boleta']:.2f}) en Hab {info_c['hab']} solapada por Factura {info_c['nro_factura']}...")
        
        dia_str = str(info_c["dia"]).zfill(2)
        subcarpeta_dia = f"{dia_str}_{nombre_mes_carpeta}"
        motivo_nc = f"ANULACION DE LA OPERACION POR REEMPLAZO CON FACTURA {info_c['nro_factura']}"

        res_nc = ejecutar_emision_nota_credito(
            tipo_cp="BOLETA",
            numero_comprobante=b_num,
            motivo=motivo_nc,
            dia_emision=dia_str,
            subcarpeta=subcarpeta_dia,
            modo=modo_emision,
            headless=headless
        )

        if res_nc and res_nc.get("status") in ["EMITIDO", "BORRADOR_GENERADO"]:
            nro_nc = res_nc.get("nro_nota_credito", f"NC-EB01-{b_num}")
            print(f"   ✅ [ÉXITO] Nota de Crédito emitida: {nro_nc} (Anula Boleta {b_num})")
            
            # Registrar en la lista de anuladas en memoria
            boletas_ya_anuladas.add(b_num)
            
            # Formatear objeto para raw JSON
            nc_raw = {
                "tipo": "NOTA_CREDITO",
                "numero_cp": nro_nc,
                "cp_modificado": b_num,
                "fecha_emision": datetime.now().strftime("%d/%m/%Y"),
                "receptor_doc": info_c["doc_boleta"],
                "receptor_nombre": info_c["cliente_boleta"],
                "monto_total": info_c["monto_boleta"],
                "texto_popup": f"NOTA DE CREDITO ELECTRONICA {nro_nc} Anula Boleta {b_num} {info_c['cliente_boleta']}. Total: S/ -{info_c['monto_boleta']:.2f}",
                "items": [{
                    "dias_ocupados": [dia_str],
                    "dia_principal": dia_str,
                    "habitacion": info_c["hab"],
                    "texto_completo": f"NOTA DE CREDITO {nro_nc} Anula Boleta {b_num}"
                }]
            }
            actualizar_raw_boletas_con_nc(nc_raw, ruta_boletas=ruta_boletas_mes)
            ncs_emitidas.append({
                "nro_nc": nro_nc,
                "boleta_anulada": b_num,
                "factura": info_c["nro_factura"],
                "hab": info_c["hab"],
                "monto": info_c["monto_boleta"]
            })
        else:
            print(f"   ❌ [ERROR] No se pudo emitir la NC para la boleta {b_num}: {res_nc.get('error')}")

    # 5. Normalizar y Prorratear en 'Registro_Estancias'
    # Conectar a Google Sheets
    creds = Credentials.from_service_account_file(RUTA_CREDS, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)
    ws_est = sh.worksheet("Registro_Estancias")
    rows_est = ws_est.get_all_values()

    batch_updates = []
    filas_normalizadas_count = 0

    print("\n -> Normalizando filas en 'Registro_Estancias' con prorrateo diario de Facturas...")

    for (nro_fac, hab), lineas_fac in facturas_por_hab.items():
        # Mapear fecha -> cuota diaria de la factura
        cuotas_por_noche = {l["noche"]: l["monto_linea"] for l in lineas_fac}
        
        # Buscar filas de Registro_Estancias para esa habitación y noches
        for idx_row, r in enumerate(rows_est[1:], start=2):
            if len(r) < 12:
                continue
            fec_in = normalizar_fecha(r[1])
            hab_r = r[11].strip().upper()

            if hab_r == hab and fec_in in cuotas_por_noche:
                cuota_noche = cuotas_por_noche[fec_in]
                aj_actual = a_num(r[35]) if len(r) > 35 else 0.0
                tipo_actual = r[32].strip() if len(r) > 32 else ""
                nro_actual = r[34].strip() if len(r) > 34 else ""
                
                # Si AJ actual difiere de la cuota diaria o no tiene la Factura asignada
                if abs(aj_actual - cuota_noche) > 0.05 or nro_fac not in nro_actual:
                    # Actualizar celdas:
                    # AF (col 32): Solicita_Comprobante -> 'Sí'
                    # AG (col 33): Tipo_Comprobante -> 'Factura'
                    # AH (col 34): Estado_Comprobante -> 'Emitido SUNAT'
                    # AI (col 35): Nro_Comprobante -> nro_fac
                    # AJ (col 36): monto factura -> cuota_noche
                    batch_updates.append({"range": f"AF{idx_row}", "values": [["Sí"]]})
                    batch_updates.append({"range": f"AG{idx_row}", "values": [["Factura"]]})
                    batch_updates.append({"range": f"AH{idx_row}", "values": [["Emitido SUNAT"]]})
                    batch_updates.append({"range": f"AI{idx_row}", "values": [[nro_fac]]})
                    batch_updates.append({"range": f"AJ{idx_row}", "values": [[cuota_noche]]})
                    filas_normalizadas_count += 1
                    print(f"      Fila E{idx_row} ({fec_in} Hab {hab}): AJ={cuota_noche:.2f} | Factura={nro_fac}")

    if batch_updates:
        ws_est.batch_update(batch_updates, value_input_option="USER_ENTERED")
        print(f"\n   [OK BATCH] Se actualizaron {len(batch_updates)} celdas en Registro_Estancias ({filas_normalizadas_count} filas normalizadas).")
    else:
        print("\n   [INFO] Todas las filas de Registro_Estancias ya se encontraban normalizadas con sus Facturas.")

    # 6. Si se emitieron NCs, regenerar Construir_SUNAT_NOV/OCT y Calzar_Noviembre/Octubre
    if ncs_emitidas:
        print(f"\n -> Regenerando {script_construir} y {script_calzar} con las nuevas NCs...")
        import subprocess
        py_bin = sys.executable
        subprocess.run([py_bin, os.path.join(SCRIPTS_DIR, script_construir)], check=True)
        subprocess.run([py_bin, os.path.join(SCRIPTS_DIR, script_calzar)], check=True)

    print("\n" + "=" * 75)
    print(f" FASE 5 COMPLETADA: {len(ncs_emitidas)} NCs emitidas | {filas_normalizadas_count} filas de estancias normalizadas")
    print("=" * 75)

    return {
        "status": "OK",
        "facturas_procesadas": len(facturas_por_hab),
        "ncs_emitidas": ncs_emitidas,
        "filas_normalizadas": filas_normalizadas_count
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Motor de limpieza por factura multidía y emisión de NC")
    parser.add_argument("--mes", type=int, default=11, choices=[10, 11], help="Mes: 11 (Noviembre, por defecto) o 10 (Octubre)")
    args, _ = parser.parse_known_args()
    ejecutar_limpieza_facturas(modo_emision="definitivo", headless=True, mes=args.mes)
