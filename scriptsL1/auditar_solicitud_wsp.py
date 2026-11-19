#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AUDITOR DE SOLICITUDES DE COMPROBANTES POR WHATSAPP (DOBLE CORROBORACIÓN + REGLA 25% ADICIONAL)
Hostal El Óvalo Mariátegui - C:\\Hostal_Sistema\\scripts\\auditar_solicitud_wsp.py

Funciones:
1. Cruza los datos solicitados por el cliente en WhatsApp (Habitación, Huésped/DNI, RUC, Días, Monto por día / Total).
2. Doble Corroboración de Emisión:
   - Corroboración 1 (SUNAT): Revisa JSONs extraídos de SUNAT (sunat_boletas_<mes>_raw.json / sunat_<mes>_lineas.json) y PDFs en facturas_sunat/ y boletas_sunat/.
   - Corroboración 2 (WebApp Local + Google Sheets): Revisa SQLite local (ovalo_local.db) y Registro_Estancias (Cols AG, AH, AI, AJ).
3. Validación de Tarifa vs Monto Solicitado (Regla del 25% de Adicional por Comprobante):
   - Si Monto_Solicitado_Dia > Tarifa_Noche (ej. Tarifa S/ 40 y pide Factura de S/ 90 -> Diferencia = S/ 50/día):
     Exige que el 25% de la diferencia (S/ 12.50/día) esté anotado en Adic_Comprobante (Col U) a lo largo de los días
     de estancia (permitiendo acumulación en el último día, ej. 12.5 + 0 + 25 = 37.50 en 3 días) y que el Saldo sea 0.
   - Si no figura el cobro adicional o falta pagar, genera un MENSAJE DE OBSERVACIÓN claro antes de emitir.
"""

import os
import sys
import re
import json
import glob
import sqlite3
import argparse
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials

try:
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr is not None:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from pathlib import Path
SCRIPTS_DIR = Path(__file__).resolve().parent
BASE_APP_DIR = SCRIPTS_DIR.parent
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


def _obtener_candidatos_db_local():
    candidatos = []
    try:
        from hoja_activa import es_prueba as _es_prueba_ha, obtener_sqlite_path as _obtener_sqlite_path
        en_prueba = bool(_es_prueba_ha())
        ruta_activa = _obtener_sqlite_path()
        if ruta_activa:
            candidatos.append(ruta_activa)
    except ImportError:
        raw = os.environ.get("OVALO_ENTORNO", "").strip().lower()
        en_prueba = (
            ("ovalo_mariategui_prueba" in os.getcwd().lower() or os.environ.get("OVALO_MODO_PRUEBA", "").strip().lower() in ("1", "true", "yes"))
            if not raw
            else (raw != "operativa")
        )
    if en_prueba:
        extras = [
            str(BASE_APP_DIR / "data" / "ovalo_sistema.db"),
            str(BASE_APP_DIR / "data" / "ovalo_local.db"),
            r"C:\ovalo_mariategui_prueba\data\ovalo_sistema.db",
            r"C:\ovalo_mariategui_prueba\data\ovalo_local.db",
        ]
    else:
        extras = [
            str(BASE_APP_DIR / "data" / "ovalo_sistema.db"),
            str(BASE_APP_DIR / "data" / "ovalo_local.db"),
            r"C:\Recepcion_Ovalo_Local\data\ovalo_sistema.db",
            r"C:\Recepcion_Ovalo_Local\data\ovalo_local.db",
            r"C:\Hostal_Sistema\instalador_app_recepcion\data\ovalo_local.db",
            r"C:\Hostal_Sistema\actualizacion_webapp_local\data\ovalo_local.db",
        ]
    for ruta in extras:
        if ruta not in candidatos:
            candidatos.append(ruta)
    return candidatos


DB_LOCAL_CANDIDATES = _obtener_candidatos_db_local()


def a_num(val):
    if val is None or val == "":
        return 0.0
    try:
        if isinstance(val, (int, float)):
            return round(float(val), 2)
        s = str(val).replace("S/", "").replace(",", "").strip()
        return round(float(s), 2)
    except Exception:
        return 0.0


def normalizar_fecha_corta(f_str):
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", str(f_str or "").strip())
    if m:
        return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{m.group(3)}"
    return str(f_str or "").strip()


def verificar_en_sunat_extraido(hab="", ruc_dni="", fechas_dias=None, nros_conocidos=None, sh=None):
    """Corroboración 1: Busca en los JSON extraídos de SUNAT (Nov/Oct), en SQLite (sunat_ventas/sunat_lineas_cache),
    en hojas SUNAT_NOV/SUNAT_OCT y en PDFs descargados de SUNAT si ya se emitió comprobante."""
    fechas_dias = fechas_dias or []
    nros_conocidos = [n for n in (nros_conocidos or []) if n]
    encontrados = []
    for ruta_json in sorted(glob.glob(r"C:\Hostal_Sistema\datos_sunat\sunat_*_lineas.json"), reverse=True):
        try:
            with open(ruta_json, "r", encoding="utf-8") as f:
                lineas = json.load(f)
            for l in lineas:
                if l.get("estado") != "VIGENTE" or l.get("tipo") == "NOTA_CREDITO":
                    continue
                nro_l = str(l.get("nro_comprobante", "")).strip()
                coincide_hab = bool(hab and str(l.get("habitacion", "")).strip() == str(hab).strip())
                coincide_doc = bool(ruc_dni and str(l.get("doc", "")).strip() == str(ruc_dni).strip())
                coincide_noche = bool(fechas_dias and l.get("noche") in fechas_dias)
                if nro_l in nros_conocidos or (coincide_hab and coincide_noche) or (coincide_doc and (coincide_hab or coincide_noche)):
                    encontrados.append(nro_l)
        except Exception:
            pass

    # Corroborar en sunat_lineas_cache y sunat_ventas dentro de ovalo_sistema.db
    db_path = next((p for p in _obtener_candidatos_db_local() if os.path.exists(p) and os.path.getsize(p) > 0), None)
    if db_path:
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            has_slc = cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sunat_lineas_cache'").fetchone()
            if has_slc:
                for row in cur.execute("SELECT nro_cp, tipo, noche_dmy, hab, doc, estado FROM sunat_lineas_cache").fetchall():
                    est_r = str(row["estado"] or "VIGENTE").upper()
                    if "ANUL" in est_r or "BAJA" in est_r or str(row["tipo"] or "").upper() == "NOTA_CREDITO":
                        continue
                    nro_l = str(row["nro_cp"] or "").strip()
                    noche_r = normalizar_fecha_corta(row["noche_dmy"])
                    coincide_hab = bool(hab and str(row["hab"] or "").strip() == str(hab).strip())
                    coincide_doc = bool(ruc_dni and str(row["doc"] or "").strip() == str(ruc_dni).strip())
                    coincide_noche = bool(fechas_dias and noche_r in fechas_dias)
                    if nro_l and (nro_l in nros_conocidos or (coincide_hab and coincide_noche) or (coincide_doc and (coincide_hab or coincide_noche))):
                        encontrados.append(nro_l)
            has_sv = cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sunat_ventas'").fetchone()
            if has_sv and (ruc_dni or nros_conocidos):
                for row in cur.execute("SELECT nro_cp, receptor_doc, estado, resumen_items FROM sunat_ventas").fetchall():
                    est_r = str(row["estado"] or "VIGENTE").upper()
                    if "ANUL" in est_r or "BAJA" in est_r:
                        continue
                    nro_v = str(row["nro_cp"] or "").strip()
                    doc_v = str(row["receptor_doc"] or "").strip()
                    res_it = str(row["resumen_items"] or "").upper()
                    if nro_v in nros_conocidos or (ruc_dni and doc_v == str(ruc_dni).strip() and (not hab or f"HAB N {hab}".upper() in res_it or f"HAB {hab}".upper() in res_it)):
                        if nro_v:
                            encontrados.append(nro_v)
            conn.close()
        except Exception:
            pass

    # Corroborar en hojas SUNAT_NOV / SUNAT_OCT si el spreadsheet ya fue abierto
    if sh is not None:
        for nombre_hoja_sunat in ("SUNAT_NOV", "SUNAT_OCT"):
            try:
                ws_s = sh.worksheet(nombre_hoja_sunat)
                for r_s in ws_s.get_all_values()[1:]:
                    if len(r_s) < 9:
                        continue
                    tipo_s = (r_s[1] if len(r_s) > 1 else "").strip().upper()
                    nro_s = (r_s[2] if len(r_s) > 2 else "").strip().replace(" ", "")
                    doc_s = (r_s[4] if len(r_s) > 4 else "").strip()
                    noche_s = normalizar_fecha_corta(r_s[6] if len(r_s) > 6 else "")
                    hab_s = (r_s[7] if len(r_s) > 7 else "").strip()
                    est_s = (r_s[11] if len(r_s) > 11 else "VIGENTE").strip().upper()
                    if "ANUL" in est_s or "NOTA" in tipo_s:
                        continue
                    coincide_hab = bool(hab and hab_s == str(hab).strip())
                    coincide_doc = bool(ruc_dni and doc_s == str(ruc_dni).strip())
                    coincide_noche = bool(fechas_dias and noche_s in fechas_dias)
                    if nro_s and (nro_s in nros_conocidos or (coincide_hab and coincide_noche) or (coincide_doc and (coincide_hab or coincide_noche))):
                        encontrados.append(nro_s)
            except Exception:
                pass

    # También verificar PDFs emitidos en carpetas de SUNAT y Downloads
    carpetas_pdf = [
        r"C:\Hostal_Sistema\facturas_sunat\**\*.pdf",
        r"C:\Hostal_Sistema\boletas_sunat\**\*.pdf",
        r"C:\Users\ovalo mariategui\Downloads\*.pdf",
    ]
    for patron in carpetas_pdf:
        for ruta_pdf in glob.glob(patron, recursive=True):
            nombre = os.path.basename(ruta_pdf).upper()
            for nro in nros_conocidos:
                if nro.upper() in nombre:
                    encontrados.append(nro.upper())
            if hab and f"HAB_{hab}".upper() in nombre:
                m_nro = re.search(r"(E001-\d+|EB01-\d+)", nombre)
                if m_nro:
                    encontrados.append(m_nro.group(1))
    return list(dict.fromkeys(encontrados))


def verificar_en_webapp_local(hab="", fechas_dias=None, dni="", ruc=""):
    """Corroboración 2A: Consulta la base SQLite de la WebApp Local (ovalo_sistema.db de Prueba u Operativa)."""
    fechas_dias = fechas_dias or []
    resultados_db = []
    candidatos = _obtener_candidatos_db_local()
    db_path = next((p for p in candidatos if os.path.exists(p) and os.path.getsize(p) > 0), None)
    if not db_path:
        return resultados_db

    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cols = {r[1] for r in cur.execute("PRAGMA table_info(estancias)").fetchall()}
        col_fecha_in = "fecha_in_dmy" if "fecha_in_dmy" in cols else "fecha_in"
        col_monto_fact = "monto_factura" if "monto_factura" in cols else "monto_fact"

        query = "SELECT * FROM estancias WHERE 1=1"
        params = []
        if hab:
            query += " AND TRIM(hab) = ?"
            params.append(str(hab).strip())
        cur.execute(query, params)
        for row in cur.fetchall():
            rd = dict(row)
            f_in = normalizar_fecha_corta(rd.get(col_fecha_in, ""))
            if fechas_dias and f_in not in fechas_dias:
                continue
            if dni and str(rd.get("dni") or "").strip() != str(dni).strip():
                continue
            noches = max(1, int(round(a_num(rd.get("noches", 1)) or 1)))
            tarifa = a_num(rd.get("tarifa", 0))
            adic_snacks = a_num(rd.get("adic_snacks", 0))
            adic_servicios = a_num(rd.get("adic_servicios", 0))
            adic_comp = a_num(rd.get("adic_comp", 0))
            deuda_ant = a_num(rd.get("deuda_anterior", 0))
            total_calc = a_num(rd.get("total")) if "total" in rd else round(noches * tarifa + adic_snacks + adic_servicios + adic_comp + deuda_ant, 2)
            pagado_calc = (
                a_num(rd.get("pagado"))
                if "pagado" in rd
                else round(
                    a_num(rd.get("pago_ini_efec", 0))
                    + a_num(rd.get("pago_ini_yape", 0))
                    + a_num(rd.get("pago_post_efec", 0))
                    + a_num(rd.get("pago_post_yape", 0))
                    + a_num(rd.get("pago_visa", 0)),
                    2,
                )
            )
            saldo_calc = a_num(rd.get("saldo")) if "saldo" in rd else round(max(0.0, total_calc - pagado_calc), 2)
            resultados_db.append({
                "db_source": db_path,
                "sheet_row": rd.get("sheet_row"),
                "fecha_in": f_in,
                "hab": str(rd.get("hab") or "").strip(),
                "huesped": str(rd.get("huesped") or "").strip(),
                "dni": str(rd.get("dni") or "").strip(),
                "ruc": str(rd.get("ruc") or "").strip(),
                "empresa": str(rd.get("empresa") or "").strip(),
                "tarifa": tarifa,
                "adic_comp": adic_comp,
                "total": total_calc,
                "pagado": pagado_calc,
                "saldo": saldo_calc,
                "estado_comp": str(rd.get("estado_comp") or "").strip(),
                "tipo_comp": str(rd.get("tipo_comp") or "").strip(),
                "nro_comp": str(rd.get("nro_comp") or "").strip(),
                "monto_fact": a_num(rd.get(col_monto_fact, 0)),
            })
        conn.close()
    except Exception:
        pass
    return resultados_db


def auditar_estancias_solicitud(hab, fechas_dias, monto_dia_solicitado, ruc="", dni="", cliente_wsp=""):
    creds = Credentials.from_service_account_file(
        RUTA_CREDS,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)
    ws_est = sh.worksheet("Registro_Estancias")
    fmt_est = ws_est.get_all_values()
    unf_est = ws_est.get_all_values(value_render_option="UNFORMATTED_VALUE")

    fechas_norm = [normalizar_fecha_corta(f) for f in fechas_dias]
    estancias_halladas = []

    for idx, (rf, ru) in enumerate(zip(fmt_est, unf_est), start=1):
        if idx == 1:
            continue
        f_in = normalizar_fecha_corta(rf[1] if len(rf) > 1 else "")
        hab_row = (rf[11] if len(rf) > 11 else "").strip()
        dni_row = (rf[4] if len(rf) > 4 else "").strip()
        ruc_row = (rf[50] if len(rf) > 50 else "").strip()

        if hab and hab_row != str(hab).strip():
            continue
        if fechas_norm and f_in not in fechas_norm:
            continue
        if dni and dni_row != str(dni).strip():
            continue

        noches = max(1, int(round(a_num(ru[15]) if len(ru) > 15 and a_num(ru[15]) > 0 else 1)))
        tarifa_noche = a_num(ru[16]) if len(ru) > 16 else 0.0
        subtotal_aloj = a_num(ru[17]) if len(ru) > 17 else tarifa_noche * noches
        adic_serv = a_num(ru[19]) if len(ru) > 19 else 0.0
        adic_comp = a_num(ru[20]) if len(ru) > 20 else 0.0
        total_cuenta = a_num(ru[21]) if len(ru) > 21 else 0.0
        total_pagado = a_num(ru[28]) if len(ru) > 28 else 0.0
        saldo_final = a_num(ru[29]) if len(ru) > 29 else 0.0
        deuda_ant = a_num(ru[37]) if len(ru) > 37 else 0.0

        estancias_halladas.append({
            "fila": f"E{idx}",
            "row_num": idx,
            "fecha_in": f_in,
            "fecha_out": normalizar_fecha_corta(rf[2] if len(rf) > 2 else ""),
            "huesped": (rf[3] if len(rf) > 3 else "").strip(),
            "dni": dni_row,
            "hab": hab_row,
            "noches": noches,
            "tarifa_noche": tarifa_noche,
            "subtotal_aloj": subtotal_aloj,
            "adic_serv": adic_serv,
            "adic_comp": adic_comp,
            "deuda_ant": deuda_ant,
            "total_cuenta": total_cuenta,
            "total_pagado": total_pagado,
            "saldo_final": saldo_final,
            "solicita_comp": (rf[31] if len(rf) > 31 else "").strip(),
            "tipo_comp": (rf[32] if len(rf) > 32 else "").strip(),
            "estado_comp": (rf[33] if len(rf) > 33 else "").strip(),
            "nro_comp": (rf[34] if len(rf) > 34 else "").strip(),
            "monto_aj": a_num(ru[35]) if len(ru) > 35 else 0.0,
            "empresa": (rf[42] if len(rf) > 42 else "").strip(),
            "celular": (rf[49] if len(rf) > 49 else "").strip(),
            "ruc": ruc_row,
        })

    # Corroboración 1 y 2
    ya_emitido_sheets = [e["nro_comp"] for e in estancias_halladas if e["nro_comp"] or e["estado_comp"].upper() == "EMITIDO"]
    webapp_matches = verificar_en_webapp_local(hab=hab, fechas_dias=fechas_norm, dni=dni, ruc=ruc)
    ya_emitido_webapp = [w["nro_comp"] for w in webapp_matches if w.get("nro_comp") or str(w.get("estado_comp") or "").upper() == "EMITIDO"]
    ya_emitido_sunat = verificar_en_sunat_extraido(
        hab=hab,
        ruc_dni=ruc or dni,
        fechas_dias=fechas_norm,
        nros_conocidos=ya_emitido_sheets + ya_emitido_webapp,
        sh=sh,
    )

    n_dias_solicitados = len(fechas_norm) if fechas_norm else sum(e["noches"] for e in estancias_halladas)
    monto_total_solicitado = round(monto_dia_solicitado * max(1, n_dias_solicitados), 2)

    suma_tarifa_real = round(sum(e["subtotal_aloj"] for e in estancias_halladas), 2)
    suma_adic_comp_anotado = round(sum(e["adic_comp"] for e in estancias_halladas), 2)
    suma_pagado_real = round(sum(e["total_pagado"] for e in estancias_halladas), 2)
    saldo_ultimo_dia = estancias_halladas[-1]["saldo_final"] if estancias_halladas else 0.0

    # Cálculo de la Regla del 25% de Adicional por Comprobante
    diferencia_total = max(0.0, round(monto_total_solicitado - suma_tarifa_real, 2))
    adicional_25_requerido = round(diferencia_total * 0.25, 2)
    adicional_por_dia_req = round(adicional_25_requerido / max(1, n_dias_solicitados), 2)

    observaciones = []

    if not estancias_halladas:
        observaciones.append(f"⚠️ No se encontraron estancias en Hab. {hab} para las fechas {fechas_norm}.")
    else:
        if diferencia_total > 0:
            if abs(suma_adic_comp_anotado - adicional_25_requerido) > 0.50 and suma_adic_comp_anotado < adicional_25_requerido:
                faltante_adic = round(adicional_25_requerido - suma_adic_comp_anotado, 2)
                observaciones.append(
                    f"⚠️ OBSERVACIÓN DE ADICIONAL (25%): La tarifa real de la Hab. {hab} es S/ {suma_tarifa_real:.2f} "
                    f"(S/ {suma_tarifa_real/max(1,n_dias_solicitados):.2f}/día) y solicita comprobante por S/ {monto_total_solicitado:.2f} "
                    f"(S/ {monto_dia_solicitado:.2f}/día). Por la diferencia de S/ {diferencia_total:.2f} corresponde un cobro adicional (25%) "
                    f"de S/ {adicional_25_requerido:.2f} (S/ {adicional_por_dia_req:.2f}/día), pero en el sistema solo figura anotado "
                    f"S/ {suma_adic_comp_anotado:.2f} (Falta anotar/cobrar S/ {faltante_adic:.2f})."
                )
        if saldo_ultimo_dia > 0.50 or suma_pagado_real < (suma_tarifa_real + adicional_25_requerido - 0.50):
            observaciones.append(
                f"⚠️ OBSERVACIÓN DE PAGO: El total a cobrar (Alojamiento S/ {suma_tarifa_real:.2f} + Adicional 25% S/ {adicional_25_requerido:.2f} "
                f"= S/ {suma_tarifa_real + adicional_25_requerido:.2f}) no figura cancelado en su totalidad "
                f"(Total Pagado: S/ {suma_pagado_real:.2f} | Saldo Final: S/ {saldo_ultimo_dia:.2f})."
            )

    es_ya_emitido = bool(ya_emitido_sunat or ya_emitido_sheets or ya_emitido_webapp)
    if es_ya_emitido:
        accion = "YA_EMITIDO"
    elif len(observaciones) == 0:
        accion = "EMITIR_DIRECTO"
    else:
        accion = "OBSERVACION_PEDIR_CONFIRMACION"

    reporte = {
        "habitacion": hab,
        "fechas_evaluadas": fechas_norm,
        "dias": n_dias_solicitados,
        "monto_dia_solicitado": monto_dia_solicitado,
        "monto_total_solicitado": monto_total_solicitado,
        "corroboracion_1_sunat": list(dict.fromkeys(ya_emitido_sunat)),
        "corroboracion_2_sheets_webapp": list(dict.fromkeys(ya_emitido_sheets + ya_emitido_webapp)),
        "db_local_consultada": webapp_matches[0]["db_source"] if webapp_matches else next((p for p in _obtener_candidatos_db_local() if os.path.exists(p)), None),
        "estancias_webapp_halladas": len(webapp_matches),
        "ya_emitido": es_ya_emitido,
        "tarifa_real_total": suma_tarifa_real,
        "diferencia_sobre_tarifa": diferencia_total,
        "adicional_25_requerido_total": adicional_25_requerido,
        "adicional_25_requerido_por_dia": adicional_por_dia_req,
        "adicional_anotado_total": suma_adic_comp_anotado,
        "desglose_adicional_por_dia": [
            {"fila": e["fila"], "fecha": e["fecha_in"], "tarifa": e["tarifa_noche"], "adic_comp": e["adic_comp"], "pagado": e["total_pagado"], "saldo": e["saldo_final"]}
            for e in estancias_halladas
        ],
        "total_pagado_estancia": suma_pagado_real,
        "saldo_final_estancia": saldo_ultimo_dia,
        "accion": accion,
        "emitir_directo": (accion == "EMITIR_DIRECTO"),
        "pedir_confirmacion_por_observacion": (accion == "OBSERVACION_PEDIR_CONFIRMACION"),
        "observaciones": observaciones,
    }
    return reporte


def main():
    parser = argparse.ArgumentParser(description="Auditor de solicitudes de comprobantes por WhatsApp")
    parser.add_argument("--hab", required=True, help="Número de habitación (ej. 406)")
    parser.add_argument("--fechas", required=True, help="Fechas separadas por coma (ej. 09/11/2026,10/11/2026,11/11/2026)")
    parser.add_argument("--monto-dia", type=float, required=True, help="Monto solicitado por día en el comprobante (ej. 90)")
    parser.add_argument("--ruc", default="", help="RUC solicitado (opcional)")
    parser.add_argument("--dni", default="", help="DNI del huésped (opcional)")
    args = parser.parse_args()

    fechas_list = [x.strip() for x in args.fechas.split(",") if x.strip()]
    rep = auditar_estancias_solicitud(
        hab=args.hab,
        fechas_dias=fechas_list,
        monto_dia_solicitado=args.monto_dia,
        ruc=args.ruc,
        dni=args.dni,
    )
    print(json.dumps(rep, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
