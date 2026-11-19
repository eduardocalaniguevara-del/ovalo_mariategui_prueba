#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EMISOR AUTOMÁTICO DE BOLETAS PENDIENTES ('ROJOS') CON FILTRO INTELIGENTE NARANJA
Hotel El Óvalo Mariátegui - Laptop 2 (Recepción)
Fase 6 del Pipeline Diario de las 1:00 PM

Reglas de Negocio:
  1. Ventana de Desfase: Solo evalúa check-ins con fecha <= Hoy - 2 días
     (ej. Hoy 23 de octubre -> procesa hasta el 21 de octubre inclusive).
  2. Filtro de Dudas / Casos Atípicos (Color NARANJA #FFC880):
     - Tarifa diaria > S/ 220.00.
     - Tiene Horas Extras / Servicios adicionales en Col T (Adic_Servicios > 0).
     - Tiene Snacks/Bodega en Col S (Adic_Snacks > 0) sin AJ definido.
     - Pago de deuda anterior en Col AL (Cargo_Deuda_Anterior > 0).
     - Pago mixto efectivo/yape o Yape != Tarifa Alojamiento.
     - Cliente pide Factura o tiene RUC en Col AY (>= 8 dígitos) o Col AG == 'Factura'.
     - Yape no confirmado en Banco BCP (chk_yape_banco != 'OK').
     - Grupos especiales (ej. Lucero Palomino).
     -> En todos estos casos: NO EMITE BOLETA, formatea la fila en NARANJA y
        anota la acción para revisión conjunta.
  3. Emisión Limpia:
     - Todo lo que pase los filtros limpios se emite automáticamente en SUNAT,
       se guarda el PDF en boletas_sunat/<DD>_Octubre/, se registra en Google Sheets
       y se actualiza el sistema.
"""

import os
import sys
import re
import time
import json
import shutil
import socket
import subprocess
from datetime import datetime, timedelta

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

from emitir_boleta_sunat import ejecutar_emision_boleta
from registrar_emision_sistema import registrar_emision

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
RUTA_RAW_BOLETAS = os.path.join(BASE_DIR, "datos_sunat", "sunat_boletas_octubre_raw.json")
DIR_BOLETAS_BASE = os.path.join(BASE_DIR, "boletas_sunat")


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


def actualizar_raw_boletas(nuevas_boletas, ruta_raw=None):
    ruta_raw = ruta_raw or RUTA_RAW_BOLETAS
    try:
        with open(ruta_raw, "r", encoding="utf-8") as f:
            data = json.load(f)
        bols = data.get("boletas", [])
        existentes = {b.get("numero_cp") for b in bols}
        for nb in nuevas_boletas:
            if nb.get("numero_cp") and nb["numero_cp"] not in existentes:
                bols.append(nb)
                existentes.add(nb["numero_cp"])
        data["boletas"] = bols
        with open(ruta_raw, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"   [SYNC RAW] {os.path.basename(ruta_raw)} actualizado. Total boletas: {len(bols)}")
    except Exception as e:
        print(f"   [WARN] Error actualizando raw JSON: {e}")


def evaluar_emision_automatica(dias_desfase=2, modo="definitivo", headless=True, mes=11):
    es_nov = (int(mes or 11) == 11)
    hoja_calzado = "CALZADO_NOV" if es_nov else "CALZADO_OCT"
    nombre_mes_carpeta = "Noviembre" if es_nov else "Octubre"
    nombre_mes_mayus = "NOVIEMBRE" if es_nov else "OCTUBRE"
    ruta_raw_mes = os.path.join(
        BASE_DIR,
        "datos_sunat",
        "sunat_boletas_noviembre_raw.json" if es_nov else "sunat_boletas_octubre_raw.json",
    )
    script_construir = "Construir_SUNAT_NOV.py" if es_nov else "Construir_SUNAT_OCT.py"
    script_calzar = "Calzar_Noviembre.py" if es_nov else "Calzar_Octubre.py"

    print("\n" + "=" * 75)
    print(f" FASE 6: EMISIÓN AUTOMÁTICA DE ROJOS CON FILTRO NARANJA ({hoja_calzado})")
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
            "boletas_emitidas": [],
            "casos_naranja": []
        }

    fecha_hoy_dt = datetime.now()
    fecha_limite_dt = fecha_hoy_dt - timedelta(days=dias_desfase)
    print(f" -> Fecha actual de ejecución: {fecha_hoy_dt.strftime('%d/%m/%Y %H:%M')}")
    print(f" -> Ventana de desfase ({dias_desfase} días): Se procesan Check-Ins hasta el {fecha_limite_dt.strftime('%d/%m/%Y')} inclusive.")

    # Conectar a Google Sheets
    creds = Credentials.from_service_account_file(RUTA_CREDS, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)
    ws_cal = sh.worksheet(hoja_calzado)
    rows_cal = ws_cal.get_all_values()
    ws_est = sh.worksheet("Registro_Estancias")
    rows_est = ws_est.get_all_values()

    candidatos_emision = []
    casos_naranja = []
    formatos_naranja_batch = []
    actualizaciones_accion_calzado = []

    # Color Naranja Suave para Google Sheets (#FFC880)
    color_naranja = {"red": 1.0, "green": 0.78, "blue": 0.50}

    for idx_cal, r_c in enumerate(rows_cal[1:], start=2):
        if len(r_c) < 22:
            continue
        
        fec_checkin_str = r_c[1].strip()
        fec_norm = normalizar_fecha(fec_checkin_str)
        try:
            fec_checkin_dt = datetime.strptime(fec_norm, "%d/%m/%Y")
        except Exception:
            continue

        # 1. Filtro de ventana de desfase: Check-in <= Hoy - 2 días
        if fec_checkin_dt > fecha_limite_dt:
            continue

        estado_final = r_c[19].strip()
        monto_sunat = a_num(r_c[6])
        cps_sunat = r_c[7].strip()
        yape_bancarizar = a_num(r_c[11])
        est_ref = r_c[5].strip()
        hab = r_c[2].strip()
        huesped = r_c[3].strip()
        dni_raw = r_c[4].strip()
        chk_yape_banco = r_c[17].strip()
        estado_banco = r_c[15].strip()

        # Solo nos interesan los que no tienen comprobante en SUNAT
        if monto_sunat > 0 or cps_sunat or estado_final == "OK_TRIPLE":
            continue
        if yape_bancarizar <= 0:
            continue
        if not (est_ref.startswith("E") and est_ref[1:].isdigit()):
            continue

        id_est_int = int(est_ref[1:])
        row_est = rows_est[id_est_int - 1] if id_est_int <= len(rows_est) else []

        # Extraer variables de auditoría de Registro_Estancias
        noches = max(1, int(round(a_num(row_est[15]) if len(row_est) > 15 and a_num(row_est[15]) > 0 else 1)))
        tarifa_noche = a_num(row_est[16]) if len(row_est) > 16 else 0.0
        subtotal_aloj = a_num(row_est[17]) if len(row_est) > 17 else (noches * tarifa_noche)
        adic_snacks = a_num(row_est[18]) if len(row_est) > 18 else 0.0
        adic_servicios = a_num(row_est[19]) if len(row_est) > 19 else 0.0
        efectivo_aa = a_num(row_est[26]) if len(row_est) > 26 else 0.0
        solicita_af = row_est[31].strip() if len(row_est) > 31 else ""
        tipo_cp_ag = row_est[32].strip().upper() if len(row_est) > 32 else ""
        cargo_deuda_al = a_num(row_est[37]) if len(row_est) > 37 else 0.0
        empresa_aq = row_est[42].strip() if len(row_est) > 42 else ""
        celular_ax = row_est[49].strip() if len(row_est) > 49 else ""
        ruc_ay = row_est[50].strip() if len(row_est) > 50 else ""
        nacionalidad = row_est[8].strip().upper() if len(row_est) > 8 else "PERU"

        monto_diario_yape = round(yape_bancarizar / noches, 2)

        # -------------------------------------------------------------
        # EVALUACIÓN DE CRITERIOS NARANJA (BLOQUEO DE EMISIÓN AUTOMÁTICA)
        # -------------------------------------------------------------
        motivo_naranja = None

        # Criterio 1: Grupos Especiales / Convenios
        if "LUCERO PALOMINO" in huesped.upper() or hab in ["207", "208"] and "LUCERO" in huesped.upper():
            motivo_naranja = "GRUPO ESPECIAL CONVENIO (LUCERO PALOMINO): Excluido de emisión automática"
        elif "GRUPO_ESPECIAL" in r_c[0].strip().upper():
            motivo_naranja = "GRUPO ESPECIAL: Marcado en columna A para revisión manual"

        # Criterio 2: Cliente solicita Factura con RUC
        elif len(re.sub(r"\D", "", ruc_ay)) >= 8:
            motivo_naranja = f"SOLICITA FACTURA CON RUC (RUC AY: {ruc_ay}): Prohibido emitir Boleta"
        elif "FACT" in tipo_cp_ag or "FACT" in solicita_af.upper():
            motivo_naranja = f"TIPO DE COMPROBANTE FACTURA (AG: {tipo_cp_ag}): Prohibido emitir Boleta"
        elif empresa_aq:
            motivo_naranja = f"ESTANCIA CORPORATIVA (Empresa AQ: {empresa_aq}): Validar si requiere Factura"

        # Criterio 3: Monto Diario > S/ 220.00
        elif monto_diario_yape > 220.00:
            motivo_naranja = f"MONTO DIARIO ATÍPICO > S/ 220.00 (S/ {monto_diario_yape:.2f}/noche): Requiere revisión conjunta"

        # Criterio 4: Tiene Horas Extras / Servicios Adicionales en Col T
        elif adic_servicios > 0:
            motivo_naranja = f"TIENE HORAS EXTRAS / SERVICIOS (Col T = S/ {adic_servicios:.2f}): Validar desglose de glosa"

        # Criterio 5: Pago de Deuda Pasada en Col AL
        elif cargo_deuda_al > 0:
            motivo_naranja = f"PAGO DE DEUDA ANTERIOR (Col AL = S/ {cargo_deuda_al:.2f}): No corresponde a alojamiento de hoy"

        # Criterio 6: Pago Mixto Efectivo + Yape
        elif efectivo_aa > 0 and yape_bancarizar < subtotal_aloj:
            motivo_naranja = f"PAGO MIXTO (Efectivo S/ {efectivo_aa:.2f} + Yape S/ {yape_bancarizar:.2f}): Validar importe a bancarizar"

        # Criterio 7: Yape difiere de la Tarifa de Alojamiento sin explicación
        elif subtotal_aloj > 0 and abs(yape_bancarizar - subtotal_aloj) > 1.0 and adic_snacks == 0:
            motivo_naranja = f"YAPE (S/ {yape_bancarizar:.2f}) DIFIERE DE TARIFA (S/ {subtotal_aloj:.2f}): Posible pago acumulado o parcial"

        # Criterio 8: Descuadre Bancario BCP
        elif chk_yape_banco != "OK" or "CUADRADO" not in estado_banco.upper():
            motivo_naranja = f"YAPE NO CONFIRMADO EN BANCO BCP ({estado_banco}): Prohibido emitir sin abono verificado"

        # Criterio 9: DNI Inválido
        dni_limpio = re.sub(r"\D", "", dni_raw)
        if "PER" in nacionalidad and len(dni_limpio) != 8:
            if len(dni_limpio) == 7:
                dni_limpio = dni_limpio.zfill(8)
            else:
                motivo_naranja = f"DOCUMENTO INVÁLIDO O INCOMPLETO (DNI: {dni_raw}): Validar número de documento"

        # -------------------------------------------------------------
        # CLASIFICACIÓN FINAL
        # -------------------------------------------------------------
        if motivo_naranja:
            casos_naranja.append({
                "fila_calzado": idx_cal,
                "est_ref": est_ref,
                "checkin": fec_norm,
                "hab": hab,
                "huesped": huesped,
                "yape": yape_bancarizar,
                "motivo": motivo_naranja
            })
            actualizaciones_accion_calzado.append({
                "range": f"T{idx_cal}:V{idx_cal}",
                "values": [["REVISAR_NARANJA", r_c[20], f"[NARANJA] {motivo_naranja}"]]
            })
            formatos_naranja_batch.append({
                "repeatCell": {
                    "range": {
                        "sheetId": ws_cal.id,
                        "startRowIndex": idx_cal - 1,
                        "endRowIndex": idx_cal,
                        "startColumnIndex": 0,
                        "endColumnIndex": 22,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": color_naranja
                        }
                    },
                    "fields": "userEnteredFormat.backgroundColor"
                }
            })
        else:
            # Pasa limpio a la lista de emisión automática
            dia_num = int(fec_norm[:2])
            dia_str = f"{dia_num:02d}"
            subcarpeta_dia = f"{dia_str}_{nombre_mes_carpeta}"

            # Formar glosa limpia
            if noches > 1:
                cant_item = noches
                dias_lista = [str(dia_num + k) for k in range(noches)]
                dias_ocup = [f"{(dia_num + k):02d}" for k in range(noches)]
                glosa = f"DIAS DE ALOJAMIENTO DEL {' Y '.join(dias_lista)} DE {nombre_mes_mayus}, HAB N {hab}"
            else:
                cant_item = 1
                dias_ocup = [dia_str]
                glosa = f"DIA DE ALOJAMIENTO DEL {dia_num} DE {nombre_mes_mayus}, HAB N {hab}"

            candidatos_emision.append({
                "id_estancia": id_est_int,
                "fila_calzado": idx_cal,
                "hab": hab,
                "cliente": huesped,
                "dni": dni_limpio,
                "sin_documento": not (len(dni_limpio) == 8 and dni_limpio.isdigit()),
                "monto_total": yape_bancarizar,
                "dias_ocupados": dias_ocup,
                "dia_principal": dia_str,
                "subcarpeta": subcarpeta_dia,
                "celular": celular_ax,
                "items": [{
                    "habitacion": hab,
                    "cantidad": cant_item,
                    "monto": yape_bancarizar,
                    "descripcion": glosa
                }]
            })

    # Aplicar formato NARANJA en CALZADO_OCT si hubo casos atípicos
    if actualizaciones_accion_calzado:
        try:
            ws_cal.batch_update(actualizaciones_accion_calzado, value_input_option="USER_ENTERED")
            if formatos_naranja_batch:
                sh.batch_update({"requests": formatos_naranja_batch})
            print(f" -> [ATENCIÓN] Se identificaron y pintaron {len(casos_naranja)} filas en NARANJA (Revisión conjunta).")
            for cn in casos_naranja:
                print(f"      🟠 Fila {cn['fila_calzado']} ({cn['est_ref']}) | {cn['checkin']} Hab {cn['hab']} | {cn['huesped'][:22]} (S/ {cn['yape']:.2f}) -> {cn['motivo']}")
        except Exception as e_upd:
            print(f"   [WARN] Error actualizando filas naranja en CALZADO_OCT: {e_upd}")

    # Emitir boletas limpias
    print(f"\n -> Boletas limpias aptas para emisión automática inmediata: {len(candidatos_emision)}")
    boletas_emitidas = []
    fecha_hoy_str = fecha_hoy_dt.strftime("%d/%m/%Y")

    for idx_em, b in enumerate(candidatos_emision, 1):
        sub_carp = b.get("subcarpeta", "20_Octubre")
        dir_dest = os.path.join(DIR_BOLETAS_BASE, sub_carp)
        os.makedirs(dir_dest, exist_ok=True)

        print(f"\n▶ [{idx_em}/{len(candidatos_emision)}] Emitiendo Boleta Hab {b['hab']} - {b['cliente']} (S/ {b['monto_total']:.2f})...")
        res = None
        for intento in range(1, 3):
            if intento > 1:
                time.sleep(3.5)
            res = ejecutar_emision_boleta(
                dni=b["dni"],
                items=b["items"],
                cliente_nombre=b["cliente"],
                sin_documento=b["sin_documento"],
                modo=modo,
                subcarpeta=sub_carp,
                headless=headless
            )
            if res and res.get("status") == "OK":
                break

        if res and res.get("status") == "OK":
            nro_cp = res.get("nro_comprobante", "")
            pdf_orig = res.get("archivo_pdf", "")
            if pdf_orig and os.path.exists(pdf_orig):
                nombre_limpio = f"BOLETA_{nro_cp}_{b['cliente'].replace(' ', '_')}_{b['monto_total']:.2f}.pdf"
                try:
                    shutil.copy2(pdf_orig, os.path.join(dir_dest, nombre_limpio))
                except Exception:
                    pass

            # Registrar en Sheets
            try:
                registrar_emision(
                    id_estancia=b["id_estancia"],
                    tipo_comprobante="Boleta",
                    nro_comprobante=nro_cp,
                    monto=b["monto_total"],
                    fila_calzado=b["fila_calzado"],
                    hoja_calzado=hoja_calzado,
                )
            except Exception as e_reg:
                print(f"   [WARN] Error registrando emisión en Sheets: {e_reg}")

            # Construir objeto raw para persistencia
            cp_norm = nro_cp.replace("EB01-", "EB01 - ") if "EB01-" in nro_cp else nro_cp
            it = b["items"][0]
            c_it = float(it.get("cantidad", 1))
            m_it = float(it.get("monto", 0.0))
            v_it = (m_it / c_it) / 1.105
            texto_full = (
                f"EL OVALO MARIATEGUI BOLETA DE VENTA ELECTRONICA RUC: 20611986484 {cp_norm} "
                f"Fecha de Emisión: {fecha_hoy_str} Señor(es): {b['cliente']} DNI: {b['dni']} "
                f"Cantidad {c_it:.2f} UNIDAD {it['descripcion']} Valor Unitario(*) {v_it:.4f} Importe de Venta(**) {m_it:.2f} "
                f"Importe Total : S/ {b['monto_total']:.2f}"
            )
            boleta_raw = {
                "tipo": "BOLETA",
                "numero_cp": cp_norm,
                "fecha_emision": fecha_hoy_str,
                "receptor_doc": "-" if b["sin_documento"] else b["dni"],
                "receptor_nombre": b["cliente"],
                "monto_total": b["monto_total"],
                "texto_popup": texto_full,
                "items": [{
                    "dias_ocupados": b["dias_ocupados"],
                    "dia_principal": b["dia_principal"],
                    "habitacion": b["hab"],
                    "texto_completo": texto_full
                }]
            }
            boletas_emitidas.append({
                "nro_cp": nro_cp,
                "hab": b["hab"],
                "cliente": b["cliente"],
                "dni": b["dni"],
                "monto": b["monto_total"],
                "fila_calzado": b["fila_calzado"],
                "celular": b.get("celular", "")
            })
            actualizar_raw_boletas([boleta_raw], ruta_raw=ruta_raw_mes)
            time.sleep(2.0)
        else:
            print(f"   ❌ [ERROR] Falló emisión de Boleta para Hab {b['hab']}: {res.get('error') if res else 'Sin respuesta'}")

    # Si se emitieron boletas, recalcular y pintar verde
    if boletas_emitidas:
        print(f"\n -> Recalculando {script_construir}, {script_calzar} y pintar_verde_emitidas.py...")
        py_bin = sys.executable
        subprocess.run([py_bin, os.path.join(SCRIPTS_DIR, script_construir)], check=True)
        subprocess.run([py_bin, os.path.join(SCRIPTS_DIR, script_calzar)], check=True)
        subprocess.run([py_bin, os.path.join(SCRIPTS_DIR, "pintar_verde_emitidas.py"), "--mes", str(mes)], check=True)

    print("\n" + "=" * 75)
    print(f" FASE 6 COMPLETADA: {len(boletas_emitidas)} Boletas emitidas | {len(casos_naranja)} Casos en Naranja")
    print("=" * 75)

    return {
        "status": "OK",
        "boletas_emitidas": boletas_emitidas,
        "casos_naranja": casos_naranja
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Emisor automático de boletas rojas con filtro naranja")
    parser.add_argument("--mes", type=int, default=11, choices=[10, 11], help="Mes: 11 (Noviembre, por defecto) o 10 (Octubre)")
    parser.add_argument("--desfase", type=int, default=2, help="Días de desfase (defecto: 2)")
    args, _ = parser.parse_known_args()
    evaluar_emision_automatica(dias_desfase=args.desfase, modo="definitivo", headless=True, mes=args.mes)
