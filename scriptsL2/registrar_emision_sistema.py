#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Módulo para registrar automáticamente comprobantes emitidos en:
1. 'Registro_Estancias' (Col AG: Tipo, AH: Estado 'Emitido', AI: Nro Comprobante, AJ: monto factura)
2. 'CALZADO_OCT' (Col G: Monto_SUNAT, Col H: Comprobantes_SUNAT, Col I: Monto_AJ, Estado: CONCILIADO TOTAL)
"""

import sys
import os
import re
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
SCOPES = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]


def registrar_emision(id_estancia, tipo_comprobante, nro_comprobante, monto, fila_calzado=None, hoja_calzado="CALZADO_OCT"):
    """
    Actualiza Registro_Estancias y CALZADO_OCT / CALZADO_NOV.
    id_estancia: ej. 'E5143' o número entero 5143.
    """
    creds = Credentials.from_service_account_file(RUTA_CREDS, scopes=SCOPES)
    client = gspread.authorize(creds)
    sh = client.open_by_key(SPREADSHEET_ID)

    # 1. Obtener fila en Registro_Estancias
    if isinstance(id_estancia, str) and id_estancia.upper().startswith("E"):
        row_estancia = int(id_estancia[1:])
    else:
        row_estancia = int(id_estancia)

    ws_est = sh.worksheet("Registro_Estancias")
    monto_val = float(monto)
    monto_fmt = f"{monto_val:.2f}"

    # Leer Total_Pagado (AC) o Tarifa (Z) para calcular Excedente_Efectivo_SUNAT (AZ)
    vals_pago = ws_est.get(f"Z{row_estancia}:AC{row_estancia}")
    pago_real = 0.0
    if vals_pago and len(vals_pago[0]) >= 4:
        try:
            ac_str = str(vals_pago[0][3]).replace("S/", "").replace(",", "").strip()
            z_str = str(vals_pago[0][0]).replace("S/", "").replace(",", "").strip()
            pago_real = float(ac_str) if ac_str else (float(z_str) if z_str else 0.0)
        except Exception:
            pago_real = 0.0

    excedente_val = round(max(0.0, monto_val - pago_real), 2) if pago_real > 0 else 0.0
    excedente_fmt = f"{excedente_val:.2f}" if excedente_val > 0 else ""

    # Rango AG:AJ y AZ
    # AG = Tipo, AH = Estado, AI = Nro, AJ = Monto factura, AZ = Excedente_Efectivo_SUNAT
    ws_est.batch_update([
        {
            "range": f"AG{row_estancia}:AJ{row_estancia}",
            "values": [[tipo_comprobante, "Emitido", nro_comprobante, monto_fmt]]
        },
        {
            "range": f"AZ{row_estancia}",
            "values": [[excedente_fmt]]
        }
    ])
    print(f"✅ 'Registro_Estancias' fila {row_estancia} actualizada: AG={tipo_comprobante}, AH=Emitido, AI={nro_comprobante}, AJ={monto_fmt}, AZ(Excedente)={excedente_fmt or '0.00'}")

    # 2. Actualizar CALZADO si se especifica la fila
    if fila_calzado:
        ws_calz = sh.worksheet(hoja_calzado)
        est_sunat_aj = f"OK (+S/ {excedente_val:.2f} Excedente Efectivo)" if excedente_val > 0 else "OK"
        est_triple = "OK_TRIPLE (CON EXCEDENTE EFECTIVO)" if excedente_val > 0 else "OK_TRIPLE"
        accion_txt = f"CONCILIADO OK (INYECTAR S/ {excedente_val:.2f} EFECTIVO A BANCO)" if excedente_val > 0 else "CONCILIADO OK"
        ws_calz.batch_update([
            {
                "range": f"G{fila_calzado}:I{fila_calzado}",
                "values": [[monto_fmt, nro_comprobante, monto_fmt]]
            },
            {
                "range": f"N{fila_calzado}",
                "values": [[f"{excedente_val:.2f}"]]
            },
            {
                "range": f"Q{fila_calzado}:V{fila_calzado}",
                "values": [[est_sunat_aj, "OK", "OK", est_triple, "0.00", accion_txt]]
            }
        ])
        print(f"✅ '{hoja_calzado}' fila {fila_calzado} actualizada: Comprobante={nro_comprobante}, Monto={monto_fmt}, Excedente={excedente_val:.2f}, Estado={est_triple}")

    try:
        from datetime import datetime
        from hoja_activa import (
            conectar_sqlite_activa as _conectar_sqlite_activa,
            limpiar_outbox_caches_locales as _limpiar_outbox_caches_locales,
            obtener_sqlite_path as _obtener_sqlite_path,
        )

        conn = _conectar_sqlite_activa(timeout=30.0)
        if conn is not None:
            try:
                ahora_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                hoy_dmy = datetime.now().strftime("%d/%m/%Y")
                nro_cp_norm = re.sub(r"\s+", "", str(nro_comprobante or "").upper())
                m_cp = re.match(r"^([A-Z0-9]+)-0*(\d+)$", nro_cp_norm)
                if m_cp:
                    serie_cp = m_cp.group(1)
                    numero_cp = int(m_cp.group(2))
                    nro_cp_norm = f"{serie_cp}-{numero_cp}"
                else:
                    serie_cp = ""
                    numero_cp = 0

                es_fac = serie_cp.startswith("E001") or "FAC" in str(tipo_comprobante or "").upper()
                tipo_est = "Factura" if es_fac else "Boleta"
                tipo_sunat = "FACTURA" if es_fac else "BOLETA"

                # 1. Buscar datos de la estancia en SQLite para hidratar sunat_ventas y sunat_lineas_cache
                est_row = conn.execute(
                    "SELECT estancia_uuid, fecha_in_dmy, hab, huesped, dni, ruc, empresa, noches FROM estancias WHERE sheet_row = ? LIMIT 1",
                    (row_estancia,),
                ).fetchone()

                # Actualizar estancias (dirty=0 porque este script ya escribió en Google Sheets; el trigger SQLite encola en _supabase_outbox)
                conn.execute(
                    """
                    UPDATE estancias
                    SET tipo_comp = ?,
                        estado_comp = 'Emitido',
                        nro_comp = ?,
                        monto_factura = ?,
                        excedente_efec_sunat = ?,
                        dirty = 0,
                        updated_at = ?
                    WHERE sheet_row = ?
                    """,
                    (tipo_est, nro_cp_norm, monto_val, excedente_val, ahora_iso, row_estancia),
                )

                fecha_in_dmy = str(est_row["fecha_in_dmy"] or "").strip() if est_row else hoy_dmy
                hab_est = str(est_row["hab"] or "").strip().upper() if est_row else ""
                huesped_est = str(est_row["huesped"] or "").strip() if est_row else ""
                dni_est = str(est_row["dni"] or "").strip() if est_row else ""
                ruc_est = str(est_row["ruc"] or "").strip() if est_row else ""
                emp_est = str(est_row["empresa"] or "").strip() if est_row else ""

                m_fec = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", fecha_in_dmy)
                if m_fec:
                    periodo_mes = f"{m_fec.group(3)}-{int(m_fec.group(2)):02d}"
                    noche_dmy = f"{int(m_fec.group(1)):02d}/{int(m_fec.group(2)):02d}/{m_fec.group(3)}"
                else:
                    periodo_mes = datetime.now().strftime("%Y-%m")
                    noche_dmy = hoy_dmy

                rec_doc = (ruc_est or dni_est) if es_fac else (dni_est or ruc_est)
                rec_nom = (emp_est or huesped_est) if es_fac else (huesped_est or emp_est)
                desc_item = f"ALOJAMIENTO DEL {noche_dmy} HAB. {hab_est}".strip()

                # 2. UPSERT fila por fila en sunat_ventas (sin DELETE masivo)
                if nro_cp_norm:
                    sv_cols = {r[1] for r in conn.execute("PRAGMA table_info(sunat_ventas)").fetchall()}
                    sv_tiene_id = ("id" in sv_cols and "tipo_doc" in sv_cols)
                    if sv_tiene_id:
                        sv_exist = conn.execute(
                            "SELECT id FROM sunat_ventas WHERE nro_cp = ? LIMIT 1",
                            (nro_cp_norm,),
                        ).fetchone()
                        if sv_exist:
                            conn.execute(
                                """
                                UPDATE sunat_ventas
                                SET periodo_mes = ?, tipo_doc = ?, tipo = ?, serie = ?, numero = ?,
                                    fecha_emision = ?, receptor_doc = ?, receptor_nombre = ?,
                                    total = ?, monto_total = ?, estado = 'VIGENTE',
                                    resumen_items = CASE WHEN COALESCE(resumen_items, '') = '' THEN ? ELSE resumen_items END,
                                    updated_at = ?, actualizado_en = ?
                                WHERE id = ?
                                """,
                                (
                                    periodo_mes,
                                    tipo_sunat,
                                    tipo_sunat,
                                    serie_cp,
                                    numero_cp,
                                    hoy_dmy,
                                    rec_doc,
                                    rec_nom,
                                    monto_val,
                                    monto_val,
                                    desc_item,
                                    ahora_iso,
                                    ahora_iso,
                                    sv_exist["id"],
                                ),
                            )
                        else:
                            conn.execute(
                                """
                                INSERT INTO sunat_ventas (
                                    periodo_mes, tipo_doc, tipo, nro_cp, serie, numero,
                                    fecha_emision, receptor_doc, receptor_nombre,
                                    total, monto_total, estado, resumen_items, updated_at, actualizado_en
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'VIGENTE', ?, ?, ?)
                                """,
                                (
                                    periodo_mes,
                                    tipo_sunat,
                                    tipo_sunat,
                                    nro_cp_norm,
                                    serie_cp,
                                    numero_cp,
                                    hoy_dmy,
                                    rec_doc,
                                    rec_nom,
                                    monto_val,
                                    monto_val,
                                    desc_item,
                                    ahora_iso,
                                    ahora_iso,
                                ),
                            )
                    else:
                        sv_exist = conn.execute(
                            "SELECT nro_cp FROM sunat_ventas WHERE nro_cp = ? LIMIT 1",
                            (nro_cp_norm,),
                        ).fetchone()
                        if sv_exist:
                            conn.execute(
                                """
                                UPDATE sunat_ventas
                                SET tipo = ?, periodo_mes = ?, fecha_emision = ?,
                                    receptor_doc = ?, receptor_nombre = ?, monto_total = ?,
                                    estado = 'VIGENTE',
                                    resumen_items = CASE WHEN COALESCE(resumen_items, '') = '' THEN ? ELSE resumen_items END,
                                    actualizado_en = ?
                                WHERE nro_cp = ?
                                """,
                                (
                                    tipo_sunat,
                                    periodo_mes,
                                    hoy_dmy,
                                    rec_doc,
                                    rec_nom,
                                    monto_val,
                                    desc_item,
                                    ahora_iso,
                                    nro_cp_norm,
                                ),
                            )
                        else:
                            conn.execute(
                                """
                                INSERT INTO sunat_ventas (
                                    nro_cp, tipo, periodo_mes, fecha_emision,
                                    receptor_doc, receptor_nombre, monto_total,
                                    estado, resumen_items, actualizado_en
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'VIGENTE', ?, ?)
                                """,
                                (
                                    nro_cp_norm,
                                    tipo_sunat,
                                    periodo_mes,
                                    hoy_dmy,
                                    rec_doc,
                                    rec_nom,
                                    monto_val,
                                    desc_item,
                                    ahora_iso,
                                ),
                            )

                    # 3. UPSERT en sunat_lineas_cache
                    sl_exist = conn.execute(
                        "SELECT id_linea FROM sunat_lineas_cache WHERE nro_cp = ? AND noche_dmy = ? AND hab = ? LIMIT 1",
                        (nro_cp_norm, noche_dmy, hab_est),
                    ).fetchone()
                    if sl_exist:
                        conn.execute(
                            """
                            UPDATE sunat_lineas_cache
                            SET tipo = ?, monto_linea = ?, total_cp = ?, cliente = ?, doc = ?,
                                fecha_emision = ?, estado = 'VIGENTE', descripcion = ?
                            WHERE id_linea = ?
                            """,
                            (tipo_sunat, monto_val, monto_val, rec_nom, rec_doc, hoy_dmy, desc_item, sl_exist["id_linea"]),
                        )
                    else:
                        conn.execute(
                            """
                            INSERT INTO sunat_lineas_cache (
                                nro_cp, tipo, noche_dmy, hab, monto_linea, total_cp,
                                cliente, doc, fecha_emision, estado, descripcion
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'VIGENTE', ?)
                            """,
                            (nro_cp_norm, tipo_sunat, noche_dmy, hab_est, monto_val, monto_val, rec_nom, rec_doc, hoy_dmy, desc_item),
                        )

                # 4. Actualizar calzado_cache si existe la fila E<row_estancia>
                ref_e = f"E{row_estancia}"
                est_triple_db = "OK_TRIPLE (CON EXCEDENTE EFECTIVO)" if excedente_val > 0 else "OK_TRIPLE"
                accion_db = f"CONCILIADO OK (INYECTAR S/ {excedente_val:.2f} EFECTIVO A BANCO)" if excedente_val > 0 else "CONCILIADO OK"
                conn.execute(
                    """
                    UPDATE calzado_cache
                    SET monto_sunat = ?,
                        comprobantes = ?,
                        monto_aj = ?,
                        excedente_efec = ?,
                        estado_conciliacion = ?,
                        accion = ?
                    WHERE fila_est = ? OR clave_calzado = ?
                    """,
                    (monto_val, nro_cp_norm, monto_val, excedente_val, est_triple_db, accion_db, ref_e, ref_e),
                )

                _limpiar_outbox_caches_locales(conn)
                conn.commit()
                print(f"✅ SQLite local ({_obtener_sqlite_path()}) sincronizada: estancias(row={row_estancia}), sunat_ventas({nro_cp_norm}), sunat_lineas_cache y calzado_cache.")
            finally:
                conn.close()
    except Exception as exc_sql:
        print(f"[WARN SQLite] No se pudo sincronizar SQLite en registrar_emision: {exc_sql}")

    return True


if __name__ == "__main__":
    if len(sys.argv) < 5:
        print("Uso: python registrar_emision_sistema.py <ID_ESTANCIA> <TIPO> <NRO_COMPROBANTE> <MONTO> [FILA_CALZADO]")
        sys.exit(1)

    id_est = sys.argv[1]
    tipo = sys.argv[2]
    nro = sys.argv[3]
    monto = float(sys.argv[4])
    fila_calz = int(sys.argv[5]) if len(sys.argv) > 5 else None

    registrar_emision(id_est, tipo, nro, monto, fila_calz)
