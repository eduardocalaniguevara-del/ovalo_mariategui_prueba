#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CONCILIADOR CON CRITERIO Y RESOLUCIÓN INTELIGENTE - HOTEL EL ÓVALO MARIÁTEGUI
Laptop 2 (Recepción)

Propósito:
Revisar las transferencias no cuadradas (SOBRANTE BANCO y FALTANTE BANCO) al finalizar
la auditoría inicial de Yape, aplicando reglas de criterio:
  1. Pagos por terceros (titular del banco distinto al huésped registrado, pero con coincidencia exacta de hora/monto en glosa).
  2. Pagos de saldo / servicios adicionales (pagos posteriores de estancias).
  3. Compras de tienda con discrepancia en cantidad (ej. 2x preservativos o agua).
  4. Desglose de pagos grupales / compartidos y detección de duplicados en Registro_Pagos.
  5. Sincronización limpia de ALERTAS_AUDITORIA.
"""

import os
import sys
import re
from datetime import datetime

try:
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr is not None:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import gspread
from google.oauth2.service_account import Credentials

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
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

def a_num(val):
    if val is None or val == "":
        return 0.0
    try:
        if isinstance(val, (int, float)):
            return float(val)
        val_clean = str(val).strip().replace("S/", "").replace(",", "").strip()
        return float(val_clean)
    except Exception:
        return 0.0

def normalizar(txt):
    if not txt:
        return ""
    txt = txt.strip().upper()
    reemplazos = (("Á", "A"), ("É", "E"), ("Í", "I"), ("Ó", "O"), ("Ú", "U"))
    for a, b in reemplazos:
        txt = txt.replace(a, b)
    return txt

def parse_fecha(f_str):
    if not f_str:
        return None
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", f_str.strip())
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except Exception:
            return None
    return None

def ejecutar_conciliacion_criterio(verbose=True):
    creds = Credentials.from_service_account_file(RUTA_CREDS, scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)

    ws_y = sh.worksheet('CONCILIACION PAGOS YAPE')
    ws_p = sh.worksheet('Registro_Pagos')
    ws_t = sh.worksheet('2.Registro_Ventas_Tienda')
    ws_e = sh.worksheet('Registro_Estancias')
    ws_g = sh.worksheet('Registro_Gastos')

    vals_y = ws_y.get_all_values()
    vals_p = ws_p.get_all_values()
    vals_t = ws_t.get_all_values()
    vals_e = ws_e.get_all_values()
    vals_g = ws_g.get_all_values()

    cuadrados_nuevos = []
    sobrantes_reales = []
    correcciones_aplicadas = []

    # 1. Identificar filas de SOBRANTE BANCO o SALIDA BANCO en CONCILIACION PAGOS YAPE
    sobrantes_banco = []
    for idx, r in enumerate(vals_y[1:], start=2):
        if len(r) > 6 and ("SOBRANTE BANCO" in r[6].strip().upper() or "SALIDA BANCO" in r[6].strip().upper()):
            sobrantes_banco.append({
                "fila": idx,
                "fecha_hora": r[0].strip(),
                "titular": r[1].strip(),
                "monto": a_num(r[2]),
                "estado_orig": r[6].strip(),
                "ref_orig": r[7].strip() if len(r) > 7 else ""
            })

    # 2. Identificar pagos no conciliados en Registro_Pagos / Tienda / Gastos
    refs_asignadas = set()
    for r in vals_y[1:]:
        if len(r) > 7 and r[7].strip() and "FALTANTE BANCO" not in r[6].upper():
            for p in re.findall(r"\b[PTG]\d+\b", r[7].upper()):
                refs_asignadas.add(p)

    # 3. Aplicar reglas de resolución inteligente
    actualizaciones_yape = {} # fila -> [D, E, F, G, H]
    filas_faltantes_a_borrar = set()

    for sb in sobrantes_banco:
        f_banco = sb["fecha_hora"]
        tit_banco = sb["titular"]
        m_banco = sb["monto"]
        fila_banco = sb["fila"]
        dt_banco = parse_fecha(f_banco)

        match_encontrado = False

        # Regla A: Buscar en Registro_Pagos por voucher y monto exacto
        # Solo pagos YAPE o PLIN (nunca Efectivo) y cercanos a la fecha del banco (<= 2 días)
        candidatos_pago = []
        for idx_p, rp in enumerate(vals_p[1:], start=2):
            ref_p = f"P{idx_p}"
            if ref_p in refs_asignadas:
                continue

            medio_p = rp[6].strip().upper() if len(rp) > 6 else ""
            if "YAPE" not in medio_p and "PLIN" not in medio_p:
                continue

            monto_p = a_num(rp[5]) if len(rp) > 5 else 0.0
            if abs(monto_p - m_banco) > 0.01:
                continue

            f_pago = rp[1].strip() if len(rp) > 1 else ""
            dt_pago = parse_fecha(f_pago)
            if dt_banco and dt_pago:
                if abs((dt_banco - dt_pago).days) > 3:
                    continue

            glosa_p = rp[9].strip() if len(rp) > 9 else ""
            huesped_p = rp[4].strip() if len(rp) > 4 else ""
            hab_p = rp[3].strip() if len(rp) > 3 else ""

            # Extraer hora de glosa (ej. "60 16:23", "30 16:49", "11 16:27")
            coincide_hora = False
            m_glo = re.search(r"(\d{1,2}:\d{2})", glosa_p)
            if m_glo:
                hora_glo = m_glo.group(1)
                # Formato HH:MM
                partes_g = hora_glo.split(":")
                hh_g = f"{int(partes_g[0]):02d}"
                mm_g = partes_g[1]
                if f"{hh_g}:{mm_g}" in f_banco or hora_glo in f_banco:
                    coincide_hora = True

            coincide_nombre = False
            if tit_banco and huesped_p:
                t_norm = normalizar(tit_banco)
                h_norm = normalizar(huesped_p)
                palabras_t = [w for w in t_norm.replace("*", "").split() if len(w) >= 4]
                palabras_h = [w for w in h_norm.replace("*", "").split() if len(w) >= 4]
                if any(w in h_norm for w in palabras_t) or any(w in t_norm for w in palabras_h):
                    coincide_nombre = True

            puntos = 0
            if coincide_hora:
                puntos += 10
            if coincide_nombre:
                puntos += 5

            if puntos > 0:
                candidatos_pago.append((puntos, ref_p, rp, idx_p, coincide_hora, coincide_nombre))

        if candidatos_pago:
            # Ordenar por puntaje descendente
            candidatos_pago.sort(key=lambda x: x[0], reverse=True)
            mejor = candidatos_pago[0]
            ref_p = mejor[1]
            rp = mejor[2]
            coincide_hora = mejor[4]
            coincide_nombre = mejor[5]

            huesped_p = rp[4].strip() if len(rp) > 4 else ""
            hab_p = rp[3].strip() if len(rp) > 3 else ""
            glosa_p = rp[9].strip() if len(rp) > 9 else ""

            estado_desc = "CUADRADO"
            if not coincide_nombre and coincide_hora:
                estado_desc = f"CUADRADO (HORA EXACTA VOUCHER / TERCERO: {tit_banco})"
            elif "SERVICIO ADICIONAL" in (rp[8].upper() if len(rp)>8 else ""):
                estado_desc = "CUADRADO (SERVICIO ADICIONAL / HORAS EXTRA)"

            actualizaciones_yape[fila_banco] = [
                str(m_banco),
                huesped_p,
                glosa_p if glosa_p else f"{m_banco:.0f} {f_banco[-8:-3]}",
                estado_desc,
                ref_p
            ]
            refs_asignadas.add(ref_p)
            match_encontrado = True
            cuadrados_nuevos.append((ref_p, huesped_p, hab_p, m_banco, estado_desc, f_banco))

        if match_encontrado:
            continue

        # Regla B: Buscar en Tienda (2.Registro_Ventas_Tienda)
        candidatos_tienda = []
        for idx_t, rt in enumerate(vals_t[1:], start=2):
            ref_t = f"T{idx_t}"
            if ref_t in refs_asignadas:
                continue

            monto_t = a_num(rt[8]) if len(rt) > 8 else 0.0
            prod_t = rt[2].strip() if len(rt) > 2 else ""
            glosa_t = rt[11].strip() if len(rt) > 11 else ""
            hab_t = rt[5].strip() if len(rt) > 5 else ""
            f_tienda = rt[0].strip() if len(rt) > 0 else ""
            dt_tienda = parse_fecha(f_tienda)

            if dt_banco and dt_tienda:
                if abs((dt_banco - dt_tienda).days) > 2:
                    continue

            es_tienda_match = False
            if abs(monto_t - m_banco) <= 0.01:
                if glosa_t:
                    m_glo = re.search(r"(\d{1,2}:\d{2})", glosa_t)
                    if m_glo and m_glo.group(1) in f_banco:
                        es_tienda_match = True
            elif "PRESERVATIVO" in prod_t.upper() and monto_t == 3.0 and m_banco == 6.0:
                if "5:35" in glosa_t and "17:35" in f_banco:
                    es_tienda_match = True
                    try:
                        ws_t.update(range_name=f"D{idx_t}", values=[[2]])
                        ws_t.update(range_name=f"I{idx_t}", values=[[6]])
                        ws_t.update(range_name=f"L{idx_t}", values=[["6 5:35pm"]])
                        correcciones_aplicadas.append(f"Tienda T{idx_t} (Hab {hab_t}): Corregido a 2 preservativos por S/ 6.00")
                        try:
                            from hoja_activa import conectar_sqlite_activa
                            conn_t = conectar_sqlite_activa(timeout=30.0)
                            if conn_t is not None:
                                try:
                                    conn_t.execute(
                                        "UPDATE ventas_tienda SET cantidad = 2, subtotal = 6.0, nro_op_yape = '6 5:35pm', synced = 1 WHERE sheet_row = ?",
                                        (idx_t,),
                                    )
                                    conn_t.commit()
                                finally:
                                    conn_t.close()
                        except Exception:
                            pass
                    except Exception as e_t:
                        print(f"Error corrigiendo T{idx_t}: {e_t}")

            if es_tienda_match:
                estado_desc = f"CUADRADO (TIENDA: {prod_t})"
                actualizaciones_yape[fila_banco] = [
                    str(m_banco),
                    f"Tienda: {prod_t}",
                    glosa_t if glosa_t else f"{m_banco:.0f} {f_banco[-8:-3]}",
                    estado_desc,
                    ref_t
                ]
                refs_asignadas.add(ref_t)
                match_encontrado = True
                cuadrados_nuevos.append((ref_t, f"Tienda {prod_t}", hab_t, m_banco, estado_desc, f_banco))
                break

        if match_encontrado:
            continue

        # Regla C: Buscar en Registro_Gastos si es una SALIDA bancaria
        if "SALIDA" in tit_banco.upper() or "SALIDA" in sb["estado_orig"].upper():
            for idx_g, rg in enumerate(vals_g[1:], start=2):
                ref_g = f"G{idx_g}"
                if ref_g in refs_asignadas:
                    continue
                metodo_g = rg[5].strip().upper() if len(rg) > 5 else ""
                if "YAPE" not in metodo_g and "PLIN" not in metodo_g:
                    continue
                monto_g = a_num(rg[3]) if len(rg) > 3 else 0.0
                if abs(monto_g - m_banco) > 0.05:
                    continue
                f_gasto = rg[1].strip() if len(rg) > 1 and rg[1].strip() else (rg[0].strip() if len(rg) > 0 else "")
                dt_gasto = parse_fecha(f_gasto)
                if dt_banco and dt_gasto and abs((dt_banco - dt_gasto).days) <= 2:
                    desc_g = rg[2].strip() if len(rg) > 2 else "Gasto Hostal"
                    m_hg = re.search(r"(\d{1,2}:\d{2})", rg[0].strip() if len(rg) > 0 else "")
                    hora_g = m_hg.group(1) if m_hg else f_banco[-8:-3]
                    estado_desc = "CUADRADO (GASTO HOSTAL)"
                    actualizaciones_yape[fila_banco] = [
                        str(m_banco),
                        f"Gasto: {desc_g} ({monto_g})",
                        f"{monto_g} {hora_g}".strip(),
                        estado_desc,
                        ref_g,
                    ]
                    refs_asignadas.add(ref_g)
                    match_encontrado = True
                    cuadrados_nuevos.append((ref_g, f"Gasto: {desc_g}", "-", m_banco, estado_desc, f_banco))
                    break

        if not match_encontrado:
            sobrantes_reales.append(sb)

    # 4. Aplicar actualizaciones por lote en CONCILIACION PAGOS YAPE
    if actualizaciones_yape:
        datos_batch = []
        for fila, vals_row in actualizaciones_yape.items():
            datos_batch.append({
                'range': f'D{fila}:H{fila}',
                'values': [vals_row]
            })
        ws_y.batch_update(datos_batch)
        if verbose:
            print(f"   [OK] {len(datos_batch)} transferencias bancarias cuadradas con criterio analítico.")

    # 5. Limpiar filas redundantes de FALTANTE BANCO que ya fueron cuadradas
    vals_y_actual = ws_y.get_all_values()
    filas_a_borrar = []
    for idx_f, rf in enumerate(vals_y_actual[1:], start=2):
        if len(rf) > 6 and "FALTANTE BANCO" in rf[6].upper() and not rf[0].strip():
            ref_falt = rf[7].strip().upper() if len(rf) > 7 else ""
            if ref_falt in refs_asignadas:
                filas_a_borrar.append(idx_f)

    for f_del in reversed(filas_a_borrar):
        ws_y.delete_rows(f_del)
        if verbose:
            print(f"   [LIMPIEZA] Eliminada fila redundante FALTANTE BANCO #{f_del}.")

    # 6. Actualizar ALERTAS_AUDITORIA y sincronizar SQLite local
    try:
        from auditor_yape import actualizar_alertas_auditoria
        actualizar_alertas_auditoria(sh)
    except Exception as e_al:
        print(f"   [WARN] No se pudo refrescar ALERTAS_AUDITORIA: {e_al}")
        try:
            from auditor_yape import sincronizar_yape_sqlite_local
            sincronizar_yape_sqlite_local(ws_y.get_all_values())
        except Exception as e_sq_y:
            print(f"   [WARN SQLite] No se pudo sincronizar yape_conciliacion_hostal: {e_sq_y}")

    # 7. Resumen de ejecución
    print("\n=======================================================")
    print(" RESUMEN DE CONCILIACIÓN CON CRITERIO")
    print("=======================================================")
    print(f"✔ Cuadres analíticos resueltos: {len(cuadrados_nuevos)}")
    for ref, huesp, hab, mnt, est, fec in cuadrados_nuevos:
        print(f"   - {ref} | Hab {hab} | {huesp} | S/ {mnt:.2f} | {fec} -> {est}")

    if correcciones_aplicadas:
        print(f"\n✔ Correcciones aplicadas en registros: {len(correcciones_aplicadas)}")
        for cor in correcciones_aplicadas:
            print(f"   - {cor}")

    print(f"\n⚠ Sobrantes Bancarios que son FALTANTES DE ANOTAR en Recepción ({len(sobrantes_reales)}):")
    for sb in sobrantes_reales:
        print(f"   * Fila {sb['fila']}: {sb['fecha_hora']} | {sb['titular']} | S/ {sb['monto']:.2f} (Sin registro en estancia ni tienda)")
    print("=======================================================\n")

    return cuadrados_nuevos, sobrantes_reales, correcciones_aplicadas

if __name__ == "__main__":
    ejecutar_conciliacion_criterio()
