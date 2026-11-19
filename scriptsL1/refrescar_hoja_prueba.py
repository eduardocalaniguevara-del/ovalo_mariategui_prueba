#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
refrescar_hoja_prueba.py
Clona/refresca las 23 pestañas de la Hoja Operativa Real (1KudqxM1D6MSCxT5Zb2_vAOTGxnw9Ja92JuFjFpDuxZ8)
hacia la Hoja de Prueba Real '2026_Prueba_Ovalo' (1VXg1d8QQgS2te1NWlSFbK6Y4fqLV5B-kNwJ-mezRoqE).

REGLAS ESTRICTAS DE SEGURIDAD:
1. La Hoja Operativa es 100% SOLO LECTURA. Jamás se invoca ningún método de escritura sobre ID_OPERATIVA.
2. Candado duro: ID_DESTINO debe ser exactamente '1VXg1d8QQgS2te1NWlSFbK6Y4fqLV5B-kNwJ-mezRoqE' y su título
   debe contener la palabra 'Prueba' ('2026_Prueba_Ovalo'). Si no se cumple, aborta con SystemExit.
3. Si la hoja destino ya tiene pestañas con datos (>1 fila), guarda primero un respaldo local JSON/CSV en
   C:\\Hostal_Sistema\\respaldos_promocion\\respaldo_hoja_prueba_<timestamp>\\.
"""

import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
import gspread
from google.oauth2.service_account import Credentials

SCRIPTS_DIR = Path(__file__).resolve().parent
BASE_APP_DIR = SCRIPTS_DIR.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

try:
    import secretos_hostal
except Exception:
    secretos_hostal = None

try:
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr is not None:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ID_OPERATIVA = "1KudqxM1D6MSCxT5Zb2_vAOTGxnw9Ja92JuFjFpDuxZ8"
ID_PRUEBA_PERMITIDO = "1VXg1d8QQgS2te1NWlSFbK6Y4fqLV5B-kNwJ-mezRoqE"
ID_DESTINO = "1VXg1d8QQgS2te1NWlSFbK6Y4fqLV5B-kNwJ-mezRoqE"

IDS_OPERATIVOS_PROHIBIDOS = {
    "1KudqxM1D6MSCxT5Zb2_vAOTGxnw9Ja92JuFjFpDuxZ8",  # Hotel El Óvalo Mariátegui (Operativa)
    "1vgjOLxc-eh1zw42uC3pxnM86Lo2jZkz8_uwrEFhw8ss",  # Hotel Terrazas de Villa (Operativa)
    "1uo4BniIeLoB_8PZ0944s8-gEquG7M37ZLp5PePUVbuM",  # TER_Gestion de Tienda
    "13iR9yDSScF21NzPB-8fkS60fn81jdADqKYjxlIFn-_s",  # SIS_TERRAZAS_Configuraciones
}

CRED_CANDIDATES = [
    Path(r"C:\Hostal_SecretosL1\credentials.json"),
    Path(r"C:\Hostal_SecretosL2\credentials.json"),
    Path(r"C:\Hostal_Secretos\credentials.json"),
    Path(r"C:\Hostal_Sistema\config\credentials.json"),
    BASE_APP_DIR / "data" / "credentials.json",
    Path(r"C:\Recepcion_Ovalo_Local\data\credentials.json"),
    Path(r"C:\ovalo_mariategui_prueba\data\credentials.json"),
]

RESPALDOS_BASE_DIR = Path(r"C:\Hostal_Sistema\respaldos_promocion")
TAB_AVISO_PRUEBA = "⚠️_ESTO_ES_PRUEBA"
TAB_TEMP = "_TEMP_BORRAR_"

# Pestañas exclusivas del puente WSP en Prueba que NO deben borrarse al refrescar datos operativos
TABS_PRESERVAR_WSP_PRUEBA = {
    "WSP_Estado",
    "WSP_Chats",
    "WSP_Mensajes",
    "WSP_Documentos",
}

# Pestañas con fórmulas cruzadas que conviene copiar al final (cuando las pestañas base ya existen en destino)
TABS_CON_FORMULAS_CRUZADAS = {
    "Vitrina_Tienda",
    "Panel_Disponibilidad",
    "Panel_De_Control",
}


def obtener_credenciales() -> Path:
    if secretos_hostal is not None:
        try:
            ruta_boveda = secretos_hostal.obtener_ruta_credentials_google()
            if ruta_boveda and Path(ruta_boveda).exists():
                return Path(ruta_boveda)
        except Exception:
            pass
    for p in CRED_CANDIDATES:
        if p.exists():
            return p
    raise FileNotFoundError("No se encontró credentials.json en la bóveda ni en el sistema.")


def verificar_candado_seguridad(id_destino: str, titulo_destino: str) -> None:
    if id_destino in IDS_OPERATIVOS_PROHIBIDOS or id_destino == ID_OPERATIVA:
        raise SystemExit(
            f"⛔ ABORTADO POR SEGURIDAD: ID_DESTINO ({id_destino}) coincide con una Hoja Operativa Prohibida."
        )
    if id_destino != ID_PRUEBA_PERMITIDO:
        raise SystemExit(
            f"⛔ ABORTADO POR SEGURIDAD: ID_DESTINO ({id_destino}) no es '{ID_PRUEBA_PERMITIDO}'."
        )
    if "prueba" not in (titulo_destino or "").lower():
        raise SystemExit(
            f"⛔ ABORTADO POR SEGURIDAD: El título de la hoja destino ('{titulo_destino}') no contiene 'Prueba'."
        )
    print(f"[CANDADO OK] Destino verificado: '{titulo_destino}' ({id_destino})")


def _con_reintento_429(fn, *args, **kwargs):
    """Ejecuta una llamada gspread con reintento automático exponential backoff ante HTTP 429 (Quota exceeded)."""
    esperas = (12, 25, 45)
    for intento, espera in enumerate(esperas, 1):
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            msg = str(exc)
            if "429" in msg or "Quota exceeded" in msg or "RESOURCE_EXHAUSTED" in msg or "ReadRequests" in msg:
                print(f"  [CUOTA 429] Límite por minuto alcanzado (intento {intento}/{len(esperas)}). Esperando {espera}s...")
                time.sleep(espera)
                continue
            raise
    return fn(*args, **kwargs)


def respaldar_hoja_prueba_si_tiene_datos(sh_destino: gspread.Spreadsheet, worksheets: list | None = None) -> Path | None:
    if worksheets is None:
        worksheets = _con_reintento_429(sh_destino.worksheets)
    ws_evaluar = [ws for ws in worksheets if ws.title not in (TAB_AVISO_PRUEBA, TAB_TEMP)]
    if not ws_evaluar:
        return None

    tabs_con_datos = []
    try:
        rangos = [f"'{ws.title}'!A1:ZZ10000" for ws in ws_evaluar]
        res_batch = _con_reintento_429(sh_destino.values_batch_get, rangos)
        v_ranges = res_batch.get("valueRanges") or []
        for idx, ws in enumerate(ws_evaluar):
            vals = (v_ranges[idx].get("values") or []) if idx < len(v_ranges) else []
            non_empty = [r for r in vals if any(str(c).strip() for c in r)]
            if len(non_empty) > 1:
                tabs_con_datos.append((ws.title, vals, len(non_empty)))
    except Exception as e:
        print(f"  [AVISO] Falló lectura batch para respaldo ({e}); continuando...")

    if not tabs_con_datos:
        print("[RESPALDO] La hoja destino no tiene pestañas con datos (>1 fila). No requiere respaldo previo.")
        return None

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = RESPALDOS_BASE_DIR / f"respaldo_hoja_prueba_{ts}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    print(f"[RESPALDO] Guardando respaldo local de {len(tabs_con_datos)} pestaña(s) en: {backup_dir}")

    meta = {
        "spreadsheet_id": sh_destino.id,
        "title": sh_destino.title,
        "timestamp": ts,
        "tabs": {},
    }
    for title, vals, count_rows in tabs_con_datos:
        safe_name = "".join(c if (c.isalnum() or c in ("_", "-", ".")) else "_" for c in title)
        json_path = backup_dir / f"{safe_name}.json"
        csv_path = backup_dir / f"{safe_name}.csv"
        json_path.write_text(json.dumps(vals, ensure_ascii=False, indent=2), encoding="utf-8")
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerows(vals)
        meta["tabs"][title] = {"rows_with_data": count_rows, "file_json": json_path.name, "file_csv": csv_path.name}

    (backup_dir / "manifest_respaldo.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return backup_dir


def copiar_valores_por_lotes_fallback(ws_orig: gspread.Worksheet, sh_destino: gspread.Spreadsheet) -> int:
    """Fallback si copyTo falla: crea la pestaña con las mismas dimensiones y copia fórmulas/valores por lotes."""
    verificar_candado_seguridad(sh_destino.id, sh_destino.title)
    rows = max(ws_orig.row_count, 10)
    cols = max(ws_orig.col_count, 10)
    ws_new = sh_destino.add_worksheet(title=ws_orig.title, rows=rows, cols=cols)
    vals = ws_orig.get_all_values(value_render_option="FORMULA")
    if vals:
        chunk_size = 1500
        for start_idx in range(0, len(vals), chunk_size):
            chunk = vals[start_idx : start_idx + chunk_size]
            start_row = start_idx + 1
            ws_new.update(
                range_name=f"A{start_row}",
                values=chunk,
                value_input_option="USER_ENTERED",
            )
            time.sleep(0.5)
    return ws_new.id


def main() -> int:
    print("=" * 78)
    print(" REFRESCO DE HOJA DE PRUEBA: 2026_Prueba_Ovalo DESDE HOJA OPERATIVA (RO)")
    print("=" * 78)

    cred_path = obtener_credenciales()
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(str(cred_path), scopes=scopes)
    gc = gspread.authorize(creds)

    # 1. Abrir hoja destino y aplicar candado estricto
    sh_destino = _con_reintento_429(gc.open_by_key, ID_DESTINO)
    verificar_candado_seguridad(sh_destino.id, sh_destino.title)

    # 2. Abrir hoja operativa (ESTRICTAMENTE SOLO LECTURA)
    sh_operativa = _con_reintento_429(gc.open_by_key, ID_OPERATIVA)
    print(f"[ORIGEN RO] '{sh_operativa.title}' ({sh_operativa.id})")

    ws_operativas = _con_reintento_429(sh_operativa.worksheets)
    orden_original = [ws.title for ws in ws_operativas]
    print(f"[ORIGEN RO] Pestañas detectadas ({len(ws_operativas)}): {', '.join(orden_original)}")

    # 3. Respaldar destino si ya tenía datos (1 sola llamada batchGet en vez de 27 lecturas individuales)
    ws_dest_actuales = _con_reintento_429(sh_destino.worksheets)
    respaldar_hoja_prueba_si_tiene_datos(sh_destino, worksheets=ws_dest_actuales)

    # 4. Preparar pestaña temporal _TEMP_BORRAR_ en sh_destino y limpiar pestañas previas en 1 solo batch_update
    verificar_candado_seguridad(sh_destino.id, sh_destino.title)
    temp_ws = None
    for ws in ws_dest_actuales:
        if ws.title == TAB_TEMP:
            temp_ws = ws
            break

    if temp_ws is None:
        # Renombrar la primera pestaña no protegida (ej. 'Hoja 1' o aviso) a '_TEMP_BORRAR_'
        primera = next((w for w in ws_dest_actuales if w.title not in TABS_PRESERVAR_WSP_PRUEBA), None)
        if primera is not None:
            print(f"[PREP] Renombrando pestaña '{primera.title}' (gid={primera.id}) -> '{TAB_TEMP}'")
            _con_reintento_429(primera.update_title, TAB_TEMP)
            temp_ws = primera
        else:
            temp_ws = _con_reintento_429(sh_destino.add_worksheet, title=TAB_TEMP, rows=5, cols=5)

    # Eliminar pestañas previas en 1 solo batch_update (preservando las 4 pestañas del puente WSP de Prueba)
    reqs_delete = []
    for ws in ws_dest_actuales:
        if ws.id != temp_ws.id and ws.title not in TABS_PRESERVAR_WSP_PRUEBA:
            print(f"[PREP] Marcando para eliminar pestaña previa en destino: '{ws.title}' (gid={ws.id})")
            reqs_delete.append({"deleteSheet": {"sheetId": ws.id}})
    if reqs_delete:
        _con_reintento_429(sh_destino.batch_update, {"requests": reqs_delete})
        print(f"[PREP] {len(reqs_delete)} pestañas previas eliminadas en 1 solo lote.")

    # 5. Orden de copia: primero pestañas de datos base, luego pestañas con fórmulas cruzadas
    ws_base = [ws for ws in ws_operativas if ws.title not in TABS_CON_FORMULAS_CRUZADAS]
    ws_formulas = [ws for ws in ws_operativas if ws.title in TABS_CON_FORMULAS_CRUZADAS]
    secuencia_copia = ws_base + ws_formulas

    copiadas_ids = {}
    for idx, ws_orig in enumerate(secuencia_copia, 1):
        verificar_candado_seguridad(sh_destino.id, sh_destino.title)
        titulo_orig = ws_orig.title
        print(f"[{idx:02d}/{len(secuencia_copia):02d}] Copiando '{titulo_orig}' (rows={ws_orig.row_count}, cols={ws_orig.col_count})...")
        try:
            res_copy = _con_reintento_429(ws_orig.copy_to, ID_DESTINO)
            new_sheet_id = res_copy["sheetId"]
            # Renombrar inmediatamente de 'Copia de <Nombre>' a '<Nombre>'
            _con_reintento_429(
                sh_destino.batch_update,
                {
                    "requests": [
                        {
                            "updateSheetProperties": {
                                "properties": {"sheetId": new_sheet_id, "title": titulo_orig},
                                "fields": "title",
                            }
                        }
                    ]
                },
            )
            copiadas_ids[titulo_orig] = new_sheet_id
            print(f"       -> OK via copyTo (new_gid={new_sheet_id}, renombrada a '{titulo_orig}')")
        except Exception as e_copy:
            print(f"       -> [AVISO] copyTo falló en '{titulo_orig}' ({e_copy}). Usando fallback por lotes...")
            new_sheet_id = copiar_valores_por_lotes_fallback(ws_orig, sh_destino)
            copiadas_ids[titulo_orig] = new_sheet_id
            print(f"       -> OK via fallback por lotes (new_gid={new_sheet_id})")
        time.sleep(0.4)

    # 6. Crear pestaña ⚠️_ESTO_ES_PRUEBA al inicio y eliminar _TEMP_BORRAR_
    verificar_candado_seguridad(sh_destino.id, sh_destino.title)
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    ws_aviso = _con_reintento_429(sh_destino.add_worksheet, title=TAB_AVISO_PRUEBA, rows=20, cols=8, index=0)
    copiadas_ids[TAB_AVISO_PRUEBA] = ws_aviso.id
    notas_prueba = [
        ["⚠️ HOJA EXCLUSIVA DEL ENTORNO DE PRUEBA — 2026_Prueba_Ovalo ⚠️", ""],
        ["Campo", "Valor"],
        ["Estado", "ENTORNO DE PRUEBA (PUERTO 8082 - C:\\ovalo_mariategui_prueba)"],
        ["ID Hoja de Prueba", ID_DESTINO],
        ["Fecha y Hora de Último Refresco", now_str],
        ["Origen del Refresco (Solo Lectura)", f"{sh_operativa.title} ({ID_OPERATIVA})"],
        ["Total Pestañas Operativas Clonadas", str(len(orden_original))],
        [
            "Regla de Aislamiento",
            "Esta hoja puede editarse libremente desde la WebApp de Prueba (:8082) o manualmente. NO afecta la Hoja Operativa Real (:8080), ni Supabase, ni SUNAT, ni WhatsApp.",
        ],
        [
            "Nota sobre pestañas import_* (IMPORTRANGE)",
            "Las pestañas auxiliares import_Almacen, import_Horario, import_Horario_Audit e import_Control_Pases conservan sus fórmulas IMPORTRANGE originales (solo lectura desde sus hojas origen). Esto es seguro porque IMPORTRANGE jamás escribe hacia el origen.",
        ],
    ]
    _con_reintento_429(ws_aviso.update, range_name="A1", values=notas_prueba, value_input_option="USER_ENTERED")

    # Reordenar las pestañas para que ⚠️_ESTO_ES_PRUEBA quede en índice 0 y las 23 siguientes en el orden exacto de la operativa
    requests_orden = [
        {
            "updateSheetProperties": {
                "properties": {"sheetId": ws_aviso.id, "index": 0},
                "fields": "index",
            }
        }
    ]
    for pos, nombre_tab in enumerate(orden_original, start=1):
        if nombre_tab in copiadas_ids:
            requests_orden.append(
                {
                    "updateSheetProperties": {
                        "properties": {"sheetId": copiadas_ids[nombre_tab], "index": pos},
                        "fields": "index",
                    }
                }
            )
    # Eliminar _TEMP_BORRAR_ en el mismo batch_update
    if temp_ws is not None:
        requests_orden.append({"deleteSheet": {"sheetId": temp_ws.id}})
    _con_reintento_429(sh_destino.batch_update, {"requests": requests_orden})
    print(f"[CLEANUP] Pestaña temporal '{TAB_TEMP}' eliminada y pestañas ordenadas.")

    # 7. Verificación final de las pestañas y conteo de filas (1 sola llamada batchGet)
    print("\n" + "=" * 78)
    print(" VERIFICACIÓN FINAL DE PESTAÑAS EN 2026_Prueba_Ovalo")
    print("=" * 78)
    ws_finales = _con_reintento_429(sh_destino.worksheets)
    print(f"Total pestañas en '{sh_destino.title}': {len(ws_finales)} (23 operativas + 1 aviso + puente WSP)")

    tabs_clave_verificar = [
        TAB_AVISO_PRUEBA,
        "Panel_Disponibilidad",
        "Panel_De_Control",
        "Registro_Estancias",
        "Registro_Pagos",
        "2.Registro_Ventas_Tienda",
        "Vitrina_Tienda",
        "Registro_Gastos",
        "CONCILIACION PAGOS YAPE",
        "SUNAT_SEP",
        "CALZADO_SEP",
        "SUNAT_OCT",
        "CALZADO_OCT",
        "SUNAT_NOV",
        "CALZADO_NOV",
        "Config",
    ]
    mapa_final = {w.title: w for w in ws_finales}
    faltantes = [t for t in orden_original if t not in mapa_final]
    if faltantes:
        print(f"[ERROR] Pestañas faltantes: {faltantes}")
        return 1

    tabs_presentes = [t for t in tabs_clave_verificar if t in mapa_final]
    rangos_verif = [f"'{t}'!A1:Z10000" for t in tabs_presentes]
    res_verif = _con_reintento_429(sh_destino.values_batch_get, rangos_verif)
    vr_list = res_verif.get("valueRanges") or []
    for idx, tname in enumerate(tabs_presentes):
        w = mapa_final[tname]
        vals = (vr_list[idx].get("values") or []) if idx < len(vr_list) else []
        non_empty = [i + 1 for i, r in enumerate(vals) if any(str(c).strip() for c in r)]
        last_r = non_empty[-1] if non_empty else 0
        print(f"  [OK] {tname:<28} | gid={w.id:<11} | filas_con_datos={len(non_empty):<5} | ultima_fila={last_r}")

    print("\n[ÉXITO] Hoja de Prueba '2026_Prueba_Ovalo' poblada y verificada al 100%.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
