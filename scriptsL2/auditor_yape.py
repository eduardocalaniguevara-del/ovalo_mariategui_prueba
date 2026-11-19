import os
import sys
import re
import io
import socket
import requests
import urllib3.util.connection as urllib3_cn
urllib3_cn.allowed_gai_family = lambda: socket.AF_INET
import pandas as pd
from googleapiclient.discovery import build
from google.auth.transport.requests import Request
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from collections import defaultdict

if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8')

# --- CONFIGURACIÓN DE RUTAS Y CONSTANTES ---
from pathlib import Path
SCRIPTS_DIR = Path(__file__).resolve().parent
BASE_DIR = str(SCRIPTS_DIR)
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
try:
    from secretos_hostal import obtener_ruta_credentials_google as _obtener_ruta_credentials_google
    CREDENTIALS_FILE = _obtener_ruta_credentials_google()
except ImportError:
    CREDENTIALS_FILE = r"C:\Hostal_Sistema\config\credentials.json"
try:
    from hoja_activa import obtener_spreadsheet_id as _obtener_spreadsheet_id
    SPREADSHEET_ID = _obtener_spreadsheet_id()
except ImportError:
    if os.environ.get("OVALO_ENTORNO", "operativa").strip().lower() != "operativa":
        raise SystemExit("⛔ ERROR CRÍTICO: No se pudo importar hoja_activa en modo PRUEBA.")
    print("[WARN] hoja_activa no disponible; usando Hoja Operativa por defecto.", file=sys.stderr)
    SPREADSHEET_ID = "1KudqxM1D6MSCxT5Zb2_vAOTGxnw9Ja92JuFjFpDuxZ8"
FOLDER_ID = "1ojWg_sFNwCS_6QfDkudsxJYGVg0TuOKe"

SCOPES = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/spreadsheets'
]

def parse_hora_ingreso(ing_str):
    if not ing_str: return None
    s = str(ing_str).strip().lower()
    m = re.search(r'(\d{1,2}):(\d{2})(?::\d{2})?\s*(a\.m\.|p\.m\.|am|pm)?', s)
    if not m: return None
    h = int(m.group(1))
    minute = int(m.group(2))
    ampm = m.group(3)
    if ampm:
        ampm = ampm.replace('.', '')
        if 'p' in ampm and h < 12: h += 12
        if 'a' in ampm and h == 12: h = 0
    return h * 60 + minute

def clean_aw_single(part, ing_min):
    p = part.strip()
    if not p: return p
    if p.isdigit() and len(p) > 5: return p
    
    cleaned = re.sub(r'^[Ss]/\s*', '', p)
    cleaned = re.sub(r'^[Hh]:\s*', '', cleaned)
    cleaned = re.sub(r'\s+[Hh]:\s*', ' ', cleaned)
    
    m_monto = re.match(r'^(\d+(?:\.\d+)?)\s+(.+)$', cleaned)
    if not m_monto:
        return p
        
    monto_str = m_monto.group(1)
    time_str = m_monto.group(2).strip()
    
    time_str = time_str.replace(':pm', 'pm').replace(':am', 'am').replace(' ', '').lower()
    time_str = time_str.replace('a.m.', 'am').replace('p.m.', 'pm').replace('a.m', 'am').replace('p.m', 'pm')
    
    m_time = re.match(r'^(\d{1,2})[:.](\d{2})(am|pm)?$', time_str)
    if not m_time:
        m_time2 = re.match(r'^(\d{1,2})(\d{2})(am|pm)$', time_str)
        if m_time2:
            m_time = m_time2
            
    if m_time:
        h = int(m_time.group(1))
        m = m_time.group(2)
        m_int = int(m)
        ampm = m_time.group(3) if len(m_time.groups()) >= 3 and m_time.group(3) else ''
        
        if h > 12:
            h -= 12
            sufijo = 'pm'
        elif h == 12:
            if ampm:
                sufijo = ampm
            elif ing_min is not None:
                diff_am = min(abs(ing_min - m_int), 1440 - abs(ing_min - m_int))
                diff_pm = min(abs(ing_min - (720 + m_int)), 1440 - abs(ing_min - (720 + m_int)))
                sufijo = 'pm' if diff_pm < diff_am else 'am'
            else:
                sufijo = 'pm'
        elif h == 0:
            h = 12
            sufijo = 'am'
        else:
            if ampm:
                sufijo = ampm
            elif ing_min is not None:
                min_am = h * 60 + m_int
                min_pm = (h + 12) * 60 + m_int
                diff_am = min(abs(ing_min - min_am), 1440 - abs(ing_min - min_am))
                diff_pm = min(abs(ing_min - min_pm), 1440 - abs(ing_min - min_pm))
                sufijo = 'pm' if diff_pm < diff_am else 'am'
            else:
                sufijo = 'am' if h < 12 else 'pm'
                
        return f'{monto_str} {h}:{m}{sufijo}'
        
    return p

def normalizar_aw_con_ingreso(val, ing_str):
    s = str(val).strip()
    if not s: return s
    if s.isdigit() and len(s) > 5: return s
    
    ing_min = parse_hora_ingreso(ing_str)
    
    is_currency_s = bool(re.match(r'^[Ss]/\s*\d', s))
    if is_currency_s:
        return clean_aw_single(s, ing_min)
        
    if '/' in s:
        parts = s.split('/')
        return ' / '.join(clean_aw_single(pt, ing_min) for pt in parts)
    else:
        return clean_aw_single(s, ing_min)

def limpiar_hora_texto(texto):
    if not isinstance(texto, str) or not texto.strip(): return texto
    texto = texto.lower().strip()
    match = re.search(r'(\d{1,2}):(\d{2})\s*(am|pm|aam|ppm|a.m|p.m)?', texto)
    if match:
        h, m, ampm = match.group(1), match.group(2), match.group(3)
        h = int(h)
        if ampm:
            if 'p' in ampm and h < 12: h += 12
            if 'a' in ampm and h == 12: h = 0
            return f"{texto.split(match.group(0))[0]}{h:02d}:{m}{texto.split(match.group(0))[1] if len(texto.split(match.group(0)))>1 else ''}".strip()
    return texto

def parse_monto_nota(nota):
    if not nota: return None
    parts = str(nota).strip().split()
    if not parts: return None
    try:
        return float(parts[0].replace(',', '.'))
    except:
        return None

def normalizar_hora_para_grupo(detalle_original):
    if not detalle_original: return ''
    parts = str(detalle_original).strip().split()
    if len(parts) < 2: return str(detalle_original).strip().lower()
    
    monto_raw = parts[0]
    try:
        monto_float = float(monto_raw.replace(',', '.'))
        monto = f"{monto_float:.2f}"
    except:
        monto = monto_raw
        
    hora_sucia = ''.join(parts[1:]).lower().replace(' ', '').replace('.', ':')
    match = re.search(r'(\d{1,2}):(\d{2})', hora_sucia)
    if not match: return f"{monto} {hora_sucia}"
    
    h, m = int(match.group(1)), match.group(2)
    is_pm = 'pm' in hora_sucia or h >= 12
    is_am = 'am' in hora_sucia and h < 12
    
    if h == 0:
        h = 12
        is_am = True
    elif h > 12:
        h -= 12
        is_pm = True
    elif h == 12 and not is_am:
        is_pm = True
        
    sufijo = 'pm' if is_pm else 'am'
    return f'{monto} {h}:{m}{sufijo}'

def similarity(a, b):
    return SequenceMatcher(None, str(a).lower(), str(b).lower()).ratio()

def extract_normalized_time(texto):
    if not texto: return ''
    s = str(texto).lower().strip()
    m = re.search(r'(\d{1,2}):(\d{2})\s*(am|pm)?', s)
    if m:
        h = int(m.group(1))
        minute = m.group(2)
        ampm = m.group(3)
        if ampm:
            if 'p' in ampm and h < 12: h += 12
            if 'a' in ampm and h == 12: h = 0
        return f"{h:02d}:{minute}"
    return ''

def auto_limpieza(sheet):
    print("0. Ejecutando Limpieza Automática de horas...")
    pagos_sql_updates = []
    est_sql_updates = []
    try:
        ws_pagos = sheet.worksheet("Registro_Pagos")
        data = ws_pagos.get_all_values()
        updates = []
        for i, row in enumerate(data[1:], start=2):
            if len(row) > 9:
                old_val = row[9]
                new_val = limpiar_hora_texto(old_val)
                if old_val != new_val:
                    updates.append({'range': f'J{i}', 'values': [[new_val]]})
                    pagos_sql_updates.append((new_val, i))
        if updates:
            ws_pagos.batch_update(updates)
            print(f"   -> {len(updates)} celdas de hora corregidas en Registro_Pagos.")
    except Exception as e:
        print(f"   -> Error en limpieza de Registro_Pagos: {e}")

    try:
        ws_est = sheet.worksheet("Registro_Estancias")
        all_vals = ws_est.get_all_values()
        updates_est = []
        for idx, row in enumerate(all_vals[1:], start=2):
            aw_val = row[48].strip() if len(row) > 48 else ""
            if not aw_val: continue
            hora_ingreso_raw = row[14].strip() if len(row) > 14 and row[14].strip() else (row[0].strip() if len(row) > 0 else "")
            new_val = normalizar_aw_con_ingreso(aw_val, hora_ingreso_raw)
            if new_val != aw_val:
                updates_est.append({'range': f'AW{idx}', 'values': [[new_val]]})
                est_sql_updates.append((new_val, idx))
        if updates_est:
            for i in range(0, len(updates_est), 500):
                ws_est.batch_update(updates_est[i:i+500])
            print(f"   -> {len(updates_est)} celdas de hora corregidas en Registro_Estancias (Col AW).")
    except Exception as e:
        print(f"   -> Error en limpieza de Registro_Estancias: {e}")

    if pagos_sql_updates or est_sql_updates:
        try:
            from hoja_activa import conectar_sqlite_activa
            conn = conectar_sqlite_activa(timeout=30.0)
            if conn is not None:
                try:
                    ahora_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    for nv, s_row in pagos_sql_updates:
                        conn.execute("UPDATE pagos SET nro_operacion = ? WHERE sheet_row = ?", (nv, s_row))
                    for nv, s_row in est_sql_updates:
                        conn.execute(
                            "UPDATE estancias SET nro_op_yape = ?, dirty = 0, updated_at = ? WHERE sheet_row = ?",
                            (nv, ahora_iso, s_row),
                        )
                    conn.commit()
                finally:
                    conn.close()
        except Exception as e_sq:
            print(f"   -> [WARN SQLite] auto_limpieza: {e_sq}")

def revisar_y_marcar_duplicados(sheet):
    print("0b. Ejecutando Revisión y Marcado de Duplicados en Registro_Estancias y Registro_Pagos...")
    ids_duplicados = set()
    try:
        ws_est = sheet.worksheet('Registro_Estancias')
        data_est = ws_est.get_all_values()
        est_by_id = {}
        est_by_guest_hab = defaultdict(list)

        for idx, r in enumerate(data_est[1:], start=2):
            id_est = r[0].strip() if len(r)>0 else ''
            f_in = r[1].strip() if len(r)>1 else ''
            guest = r[3].strip().upper() if len(r)>3 else ''
            hab = r[11].strip() if len(r)>11 else ''
            try:
                total = float(r[21].replace('S/', '').replace(',', '').strip()) if len(r)>21 and r[21].strip() else 0.0
            except:
                total = 0.0
            try:
                pagado = float(r[28].replace('S/', '').replace(',', '').strip()) if len(r)>28 and r[28].strip() else 0.0
            except:
                pagado = 0.0
            obs = r[47].strip() if len(r)>47 else ''
            aw = r[48].strip() if len(r)>48 else ''
            item = {'row': idx, 'id_est': id_est, 'f_in': f_in, 'guest': guest, 'hab': hab, 'total': total, 'pagado': pagado, 'obs': obs, 'aw': aw}
            if id_est: est_by_id[id_est] = item
            if guest and hab: est_by_guest_hab[(guest, hab)].append(item)

        updates_est = []
        for (guest, hab), items in est_by_guest_hab.items():
            if len(items) > 1:
                for i in range(len(items)):
                    e1 = items[i]
                    for j in range(i+1, len(items)):
                        e2 = items[j]
                        if e1['f_in'] == e2['f_in']:
                            try:
                                dt1 = pd.to_datetime(e1['id_est'], dayfirst=True)
                                dt2 = pd.to_datetime(e2['id_est'], dayfirst=True)
                                diff_min = abs((dt2 - dt1).total_seconds()) / 60
                            except:
                                diff_min = 9999
                            if diff_min <= 180:
                                if e1['pagado'] == 0 and e2['pagado'] > 0:
                                    if '[DUPLICADO]' not in e1['obs']:
                                        new_obs = f"[DUPLICADO] Reemplazado por fila {e2['row']}"
                                        updates_est.append({'range': f'AV{e1["row"]}', 'values': [[new_obs]]})
                                elif e2['pagado'] == 0 and e1['pagado'] > 0:
                                    if '[DUPLICADO]' not in e2['obs']:
                                        new_obs = f"[DUPLICADO] Reemplazado por fila {e1['row']}"
                                        updates_est.append({'range': f'AV{e2["row"]}', 'values': [[new_obs]]})
        if updates_est:
            ws_est.batch_update(updates_est)
            print(f"   -> {len(updates_est)} estancias duplicadas marcadas en Registro_Estancias (Col AV).")

        # Registro_Pagos
        ws_pagos = sheet.worksheet('Registro_Pagos')
        if ws_pagos.col_count < 11:
            ws_pagos.add_cols(1)
            ws_pagos.update_acell('K1', 'Observaciones_Auditoria')

        data_pagos = ws_pagos.get_all_values()
        if len(data_pagos) > 0 and (len(data_pagos[0]) < 11 or data_pagos[0][10].strip() == ""):
            ws_pagos.update_acell('K1', 'Observaciones_Auditoria')

        pagos_parsed = []
        for idx, r in enumerate(data_pagos[1:], start=2):
            id_pago = r[0] if len(r)>0 else ''
            f_str = r[1] if len(r)>1 else ''
            id_est = r[2] if len(r)>2 else ''
            hab = r[3] if len(r)>3 else ''
            huesped = r[4].strip().upper() if len(r)>4 else ''
            try:
                monto = float(r[5]) if len(r)>5 and r[5] else 0.0
            except:
                monto = 0.0
            medio = r[6].strip().lower() if len(r)>6 else ''
            concepto = r[8].strip() if len(r)>8 else ''
            nota = r[9].strip().lower() if len(r)>9 else ''
            obs_aud = r[10].strip() if len(r)>10 else ''
            if '[DUPLICADO' in concepto.upper() or 'DUPLICADO' in obs_aud.upper() or 'ERROR RECEPC' in obs_aud.upper():
                ids_duplicados.add(f"P{idx}")
            try:
                f_dt = pd.to_datetime(f_str, dayfirst=True)
            except:
                f_dt = None
            pagos_parsed.append({'row': idx, 'id_pago': id_pago, 'fecha_str': f_str, 'fecha_dt': f_dt, 'id_est': id_est, 'hab': hab, 'huesped': huesped, 'monto': monto, 'medio': medio, 'concepto': concepto, 'nota': nota, 'obs_aud': obs_aud})

        updates_pagos = []
        for i in range(len(pagos_parsed)):
            p1 = pagos_parsed[i]
            if p1['monto'] == 0: continue
            for j in range(i+1, min(i+20, len(pagos_parsed))):
                p2 = pagos_parsed[j]
                if p1['huesped'] == p2['huesped'] and p1['hab'] == p2['hab'] and p1['medio'] == p2['medio']:
                    if abs(p1['monto'] - p2['monto']) < 0.05:
                        if p1['fecha_dt'] and p2['fecha_dt']:
                            diff_sec = abs((p2['fecha_dt'] - p1['fecha_dt']).total_seconds())
                            same_note = (p1['nota'] == p2['nota']) and (p1['nota'] != '')
                            if diff_sec <= 600 or (same_note and diff_sec <= 86400):
                                est = est_by_id.get(p1['id_est']) or est_by_id.get(p2['id_est'])
                                is_dup = False
                                if est:
                                    if est['total'] > 0 and (p1['monto'] + p2['monto']) > est['total']:
                                        is_dup = True
                                    elif est['pagado'] == p1['monto']:
                                        is_dup = True
                                elif diff_sec <= 120:
                                    is_dup = True

                                if is_dup:
                                    dup_row = p2['row']
                                    orig_row = p1['row']
                                    ids_duplicados.add(f"P{dup_row}")
                                    msg = f"[DUPLICADO DE P{orig_row}]"
                                    if msg not in p2['obs_aud']:
                                        updates_pagos.append({'range': f'K{dup_row}', 'values': [[f"DUPLICADO (Original: P{orig_row})"]]})
                                        if not p2['concepto'].startswith('[DUPLICADO'):
                                            updates_pagos.append({'range': f'I{dup_row}', 'values': [[f"[DUPLICADO DE P{orig_row}] {p2['concepto']}"]]})
        if updates_pagos:
            for i in range(0, len(updates_pagos), 500):
                ws_pagos.batch_update(updates_pagos[i:i+500])
            print(f"   -> {len(updates_pagos)} celdas de pagos duplicados marcadas en Registro_Pagos (Col I y K).")
        print(f"   -> {len(ids_duplicados)} pagos duplicados confirmados en Registro_Pagos.")

        if updates_est or updates_pagos:
            try:
                from hoja_activa import conectar_sqlite_activa
                conn = conectar_sqlite_activa(timeout=30.0)
                if conn is not None:
                    try:
                        ahora_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        for u in updates_est:
                            m_r = re.match(r"^AV(\d+)$", str(u.get("range") or ""))
                            if m_r and u.get("values"):
                                conn.execute(
                                    "UPDATE estancias SET observaciones = ?, dirty = 0, updated_at = ? WHERE sheet_row = ?",
                                    (str(u["values"][0][0]), ahora_iso, int(m_r.group(1))),
                                )
                        for u in updates_pagos:
                            m_p = re.match(r"^I(\d+)$", str(u.get("range") or ""))
                            if m_p and u.get("values"):
                                conn.execute(
                                    "UPDATE pagos SET concepto = ? WHERE sheet_row = ?",
                                    (str(u["values"][0][0]), int(m_p.group(1))),
                                )
                        conn.commit()
                    finally:
                        conn.close()
            except Exception as e_sq_dup:
                print(f"   -> [WARN SQLite] revisar_y_marcar_duplicados: {e_sq_dup}")
    except Exception as e:
        print(f"   -> Error en revisión de duplicados: {e}")

    return ids_duplicados

def main():
    print("==================================================")
    print("   AUDITOR DE YAPE - HOTEL OVALO MARIATEGUI")
    print("==================================================\n")

    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
    drive_service = build('drive', 'v3', credentials=creds)
    gc = gspread.authorize(creds)
    sheet = gc.open_by_key(SPREADSHEET_ID)

    # 1. Escanear Google Drive
    print("1. Escaneando carpeta de Google Drive en busca del último archivo...")
    query = f"'{FOLDER_ID}' in parents and trashed=false and name contains '.xlsx'"
    results = drive_service.files().list(
        q=query,
        orderBy='modifiedTime desc',
        fields='files(id, name, createdTime, modifiedTime)',
        pageSize=10
    ).execute()

    items = results.get('files', [])
    if not items:
        print("   -> ❌ No se encontraron archivos en la carpeta de Google Drive.")
        return

    if not creds.valid or not creds.token:
        creds.refresh(Request())

    # Buscar entre los archivos recientes el que tenga la fecha de transacción más avanzada
    print("   -> Analizando fechas de los reportes recientes en Drive...")
    mejor_archivo = None
    mejor_fecha_max = None
    mejor_fh = None

    for item in items[:6]:
        f_id = item['id']
        try:
            dl_url = f"https://www.googleapis.com/drive/v3/files/{f_id}?alt=media"
            resp = requests.get(dl_url, headers={"Authorization": f"Bearer {creds.token}"}, timeout=30)
            if resp.status_code == 200:
                fh_temp = io.BytesIO(resp.content)
                df_temp = pd.read_excel(fh_temp, skiprows=4)
                if len(df_temp.columns) >= 6:
                    col_fec = df_temp.columns[5]
                    fechas_series = pd.to_datetime(df_temp[col_fec], format='%d/%m/%Y %H:%M:%S', errors='coerce').dropna()
                    if len(fechas_series) > 0:
                        max_f = fechas_series.max()
                        if mejor_fecha_max is None or max_f > mejor_fecha_max:
                            mejor_fecha_max = max_f
                            mejor_archivo = item
                            mejor_fh = fh_temp
        except Exception:
            continue

    if not mejor_archivo or not mejor_fh:
        mejor_archivo = items[0]
        dl_url = f"https://www.googleapis.com/drive/v3/files/{mejor_archivo['id']}?alt=media"
        resp = requests.get(dl_url, headers={"Authorization": f"Bearer {creds.token}"}, timeout=30)
        mejor_fh = io.BytesIO(resp.content)

    latest_file_id = mejor_archivo['id']
    latest_file_name = mejor_archivo['name']
    print(f"   -> Encontrado reporte más reciente: {latest_file_name} (ID: {latest_file_id})")
    print(f"   -> Última fecha de transacción bancaria en archivo: {mejor_fecha_max}")
    print("   -> Descargando archivo y procesando en memoria...")
    fh = mejor_fh

    # Leer Excel del banco
    df_banco = pd.read_excel(fh, skiprows=4)
    cols = df_banco.columns.tolist()
    if len(cols) >= 6:
        df_banco = df_banco.rename(columns={
            cols[0]: 'Tipo',
            cols[1]: 'Origen',
            cols[2]: 'Destino',
            cols[3]: 'Monto',
            cols[4]: 'Mensaje',
            cols[5]: 'Fecha'
        })

    def format_origen(r):
        if 'PAGASTE' in str(r['Tipo']).upper():
            dest = str(r['Destino']) if pd.notna(r['Destino']) else ''
            msg = f" - {str(r['Mensaje'])}" if pd.notna(r['Mensaje']) and str(r['Mensaje']).strip() else ''
            return f"SALIDA: {dest}{msg}".strip()
        return r['Origen']

    df_banco['Origen'] = df_banco.apply(format_origen, axis=1)
    df_banco['Fecha'] = pd.to_datetime(df_banco['Fecha'], format='%d/%m/%Y %H:%M:%S', errors='coerce')
    df_banco = df_banco.dropna(subset=['Fecha'])
    df_banco = df_banco[df_banco['Tipo'].astype(str).str.contains('PAG', na=False, case=False)]
    df_banco = df_banco.sort_values('Fecha').reset_index(drop=True)

    print("2. Conectando con Google Sheets...")
    auto_limpieza(sheet)
    ids_duplicados = revisar_y_marcar_duplicados(sheet)

    # --- 3. PUENTE LÓGICO: DETERMINAR FILTRADO INCREMENTAL ---
    fecha_inicio = pd.Timestamp('2026-07-11 10:20:00') # Fallback
    ids_ya_procesados = set()

    try:
        ws_conc_temp = sheet.worksheet("CONCILIACION PAGOS YAPE")
        data_conc_temp = ws_conc_temp.get_all_values()

        if len(data_conc_temp) > 1:
            # Limpiar filas finales sin conciliar (FALTANTE BANCO o SOBRANTE BANCO al final de la hoja)
            # para que al subir un nuevo Excel o corregir registros se re-evalúen limpiamente sin duplicar
            filas_a_borrar = 0
            for row in reversed(data_conc_temp[1:]):
                estado_fila = row[6].strip().upper() if len(row) > 6 else ""
                f_b_str = row[0].strip() if len(row) > 0 else ""
                if ("FALTANTE BANCO" in estado_fila and not f_b_str) or ("SOBRANTE BANCO" in estado_fila) or ("PAGO DUPLICADO" in estado_fila and not f_b_str):
                    filas_a_borrar += 1
                else:
                    break

            if filas_a_borrar > 0:
                total_rows_actual = len(data_conc_temp)
                start_del = total_rows_actual - filas_a_borrar + 1
                print(f"   -> Re-evaluando {filas_a_borrar} fila(s) pendiente(s) al final de CONCILIACION PAGOS YAPE (filas {start_del} a {total_rows_actual})...")
                ws_conc_temp.delete_rows(start_del, total_rows_actual)
                data_conc_temp = data_conc_temp[:-filas_a_borrar]

            # Recolectar IDs existentes para deduplicación (Columna H = índice 7)
            for row in data_conc_temp[1:]:
                estado_fila = row[6].strip().upper() if len(row) > 6 else ""
                if "FALTANTE BANCO" in estado_fila:
                    continue
                if len(row) > 7 and row[7].strip():
                    for pid in row[7].strip().split('\n'):
                        if pid.strip():
                            ids_ya_procesados.add(pid.strip())

            # Extraer la última fila válida del banco
            ultima_fila_banco = None
            for row in reversed(data_conc_temp[1:]):
                if len(row) > 0 and row[0].strip() != "":
                    ultima_fila_banco = row
                    break

            if ultima_fila_banco:
                u_fecha_str = ultima_fila_banco[0].strip()
                u_origen = ultima_fila_banco[1].strip() if len(ultima_fila_banco) > 1 else ""
                u_monto_str = ultima_fila_banco[2].strip() if len(ultima_fila_banco) > 2 else "0"

                print(f"   -> Último registro del BANCO en Sheets: Fecha '{u_fecha_str}', Origen '{u_origen}', Monto '{u_monto_str}'")
                
                try:
                    u_fecha = pd.to_datetime(u_fecha_str, dayfirst=True)
                except:
                    u_fecha = None

                try:
                    u_monto = float(u_monto_str)
                except:
                    u_monto = 0.0

                if u_fecha:
                    fecha_inicio = u_fecha + pd.Timedelta(seconds=1)
                    encontrado_index = -1
                    for idx, row in df_banco.iterrows():
                        time_diff = abs((row['Fecha'] - u_fecha).total_seconds())
                        nombre_match = str(row['Origen']).lower().strip() in u_origen.lower() or u_origen.lower() in str(row['Origen']).lower().strip()
                        try:
                            monto_b = float(row['Monto'])
                        except:
                            monto_b = 0.0
                        monto_match = abs(monto_b - u_monto) < 0.1

                        if time_diff <= 60 and nombre_match and monto_match:
                            encontrado_index = idx
                            break

                    if encontrado_index != -1:
                        print(f"   -> [PUENTE EXITOSO] Coincidencia encontrada en fila {encontrado_index} del reporte banco. Recortando historial repetido...")
                        df_banco = df_banco.iloc[encontrado_index + 1:].reset_index(drop=True)
                    else:
                        print("   -> [PUENTE POR FECHA] No se halló la fila exacta; usando corte por fecha última registrada.")
                        df_banco = df_banco[df_banco['Fecha'] >= fecha_inicio].reset_index(drop=True)
        else:
            print("   -> Hoja CONCILIACION PAGOS YAPE vacía. Iniciando desde 11/07/2026.")
            df_banco = df_banco[df_banco['Fecha'] >= fecha_inicio].reset_index(drop=True)

    except Exception as e:
        print(f"   -> Error evaluando última fila: {e}. Usando corte 11/07.")
        df_banco = df_banco[df_banco['Fecha'] >= fecha_inicio].reset_index(drop=True)

    print(f"   -> Encontrados {len(df_banco)} NUEVOS movimientos netos en el banco para auditar.")

    # 4. Extraer datos de Registro_Pagos (Habitaciones)
    print("4. Extrayendo datos de Registro_Pagos...")
    ws_pagos = sheet.worksheet("Registro_Pagos")
    data_pagos = ws_pagos.get_all_values()

    yapes_sheets = []
    vueltos_sheets = []
    yapes_duplicados_reporte = []
    for idx, row in enumerate(data_pagos[1:], start=2):
        row_id = f"P{idx}"
        if row_id in ids_ya_procesados:
            continue

        if len(row) > 6 and row[6].strip().lower() == "yape":
            fecha_registro_str = row[1]
            try:
                monto_sheets = float(row[5]) if row[5] else 0.0
            except:
                monto_sheets = 0.0
            dato_yape = row[9].strip().lower() if len(row) > 9 else ""
            concepto = row[8].strip() if len(row) > 8 else ""

            try:
                fecha_reg = pd.to_datetime(fecha_registro_str, dayfirst=True)
                if fecha_reg < fecha_inicio:
                    continue
            except:
                continue

            if row_id in ids_duplicados:
                yapes_duplicados_reporte.append({
                    'fila_excel': row_id,
                    'huesped': row[4] if len(row) > 4 else '',
                    'monto': monto_sheets,
                    'texto_original': dato_yape,
                    'fecha_registro_original': fecha_registro_str,
                })
                continue

            hora_sheets = None
            if dato_yape:
                match = re.search(r'(\d{1,2}):(\d{2})\s*(am|pm|aam|ppm)?', dato_yape)
                if match and fecha_reg:
                    h = int(match.group(1))
                    m = int(match.group(2))
                    ampm = match.group(3)

                    if ampm:
                        if 'p' in ampm and h < 12: h += 12
                        if 'a' in ampm and h == 12: h = 0
                    else:
                        if h < 12:
                            min_reg = fecha_reg.hour * 60 + fecha_reg.minute
                            diff_am = abs(min_reg - (h * 60 + m))
                            diff_pm = abs(min_reg - ((h + 12) * 60 + m))
                            if diff_pm < diff_am:
                                h += 12
                    try:
                        hora_sheets = fecha_reg.replace(hour=h, minute=m, second=0)
                        if hora_sheets > fecha_reg + timedelta(hours=2):
                            hora_sheets -= timedelta(days=1)
                    except:
                        pass

            if not hora_sheets and fecha_reg:
                hora_sheets = fecha_reg

            # SEPARACIÓN DE VUELTOS A CLIENTE (PAGOS NEGATIVOS O CONCEPTO VUELTO)
            es_vuelto = monto_sheets < 0 or "vuelto" in concepto.lower()
            if es_vuelto:
                vueltos_sheets.append({
                    'fila_excel': row_id,
                    'huesped': row[4] if len(row) > 4 else '',
                    'habitacion': row[3] if len(row) > 3 else '',
                    'monto': abs(monto_sheets),
                    'monto_original': monto_sheets,
                    'texto_original': dato_yape,
                    'concepto': concepto,
                    'fecha_registro_original': fecha_registro_str,
                    'fecha_estimada': hora_sheets,
                    'conciliado': False,
                    'es_vuelto_justificado': False
                })
                continue

            yapes_sheets.append({
                'fila_excel': row_id,
                'huesped': row[4] if len(row) > 4 else '',
                'monto': monto_sheets,
                'texto_original': dato_yape,
                'fecha_registro_original': fecha_registro_str,
                'fecha_estimada': hora_sheets,
                'conciliado': False
            })

    print(f"   -> {len(yapes_sheets)} registros nuevos de Yape en Habitaciones pendientes de conciliar.")
    print(f"   -> {len(vueltos_sheets)} vueltos a cliente detectados en Registro_Pagos.")

    # 4b. Extraer datos de 2.Registro_Ventas_Tienda
    print("4b. Extrayendo datos de 2.Registro_Ventas_Tienda...")
    try:
        ws_tienda = sheet.worksheet("2.Registro_Ventas_Tienda")
        data_tienda = ws_tienda.get_all_values()

        for idx, row in enumerate(data_tienda[1:], start=2):
            row_id = f"T{idx}"
            if row_id in ids_ya_procesados:
                continue

            if len(row) > 11 and row[4].strip().lower() == "yape":
                fecha_registro_str = row[0]
                try:
                    monto_sheets = float(str(row[8]).replace('S/', '').replace(',', '').strip())
                except:
                    continue

                dato_yape = row[11].strip().lower() if len(row) > 11 else ""

                try:
                    fecha_reg = pd.to_datetime(fecha_registro_str, dayfirst=True)
                    if fecha_reg < fecha_inicio:
                        continue
                except:
                    continue

                hora_sheets = None
                if dato_yape:
                    match = re.search(r'(\d{1,2}):(\d{2})\s*(am|pm|aam|ppm)?', dato_yape)
                    if match:
                        h = int(match.group(1))
                        m = int(match.group(2))
                        ampm = match.group(3)

                        if ampm:
                            if 'p' in ampm and h < 12: h += 12
                            if 'a' in ampm and h == 12: h = 0
                        else:
                            if h < 12:
                                min_reg = fecha_reg.hour * 60 + fecha_reg.minute
                                diff_am = abs(min_reg - (h * 60 + m))
                                diff_pm = abs(min_reg - ((h + 12) * 60 + m))
                                if diff_pm < diff_am:
                                    h += 12
                        try:
                            hora_sheets = fecha_reg.replace(hour=h, minute=m, second=0)
                            if hora_sheets > fecha_reg + timedelta(hours=2):
                                hora_sheets -= timedelta(days=1)
                        except:
                            pass

                if not hora_sheets:
                    hora_sheets = fecha_reg

                yapes_sheets.append({
                    'fila_excel': row_id,
                    'huesped': f"Tienda: {row[2].strip()}" if len(row) > 2 else 'Tienda',
                    'monto': monto_sheets,
                    'texto_original': dato_yape,
                    'fecha_registro_original': fecha_registro_str,
                    'fecha_estimada': hora_sheets,
                    'conciliado': False
                })

        print(f"   -> Total registros pendientes en Excel (Habitaciones + Tienda): {len(yapes_sheets)}")
    except Exception as e:
        print(f"   -> Error leyendo Tienda: {e}")

    # 4c. Extraer datos de Registro_Gastos (Salidas Yape)
    print("4c. Extrayendo datos de Registro_Gastos (Salidas / Gastos con Yape)...")
    gastos_sheets = []
    try:
        ws_gastos = sheet.worksheet("Registro_Gastos")
        data_gastos = ws_gastos.get_all_values()
        for idx, row in enumerate(data_gastos[1:], start=2):
            row_id = f"G{idx}"
            if row_id in ids_ya_procesados:
                continue

            if len(row) > 5 and 'yape' in str(row[5]).strip().lower():
                fecha_g_str = row[1]
                try:
                    monto_g = float(str(row[3]).replace('S/', '').replace(',', '').strip())
                except:
                    continue
                desc_g = row[2].strip() if len(row) > 2 else ''
                id_g = row[0].strip() if len(row) > 0 else ''

                try:
                    fecha_g = pd.to_datetime(fecha_g_str, dayfirst=True)
                    if fecha_g < (fecha_inicio.floor('D') - pd.Timedelta(days=2)):
                        continue
                except:
                    continue

                hora_g = ""
                if id_g:
                    m_h = re.search(r'(\d{1,2}:\d{2})', id_g)
                    if m_h:
                        hora_g = m_h.group(1)

                gastos_sheets.append({
                    'fila_excel': row_id,
                    'desc': desc_g,
                    'monto': monto_g,
                    'fecha_registro_original': id_g if id_g else fecha_g_str,
                    'fecha_gasto': fecha_g,
                    'hora': hora_g,
                    'conciliado': False
                })

        print(f"   -> Total gastos de Yape pendientes en Registro_Gastos: {len(gastos_sheets)}")
    except Exception as e:
        print(f"   -> Error leyendo Registro_Gastos: {e}")

    # 5. Motor de Conciliación
    print("5. Ejecutando conciliación cruzada inteligente...")
    banco_items = []
    for _, row in df_banco.iterrows():
        banco_items.append({
            'fecha': row['Fecha'],
            'origen': row['Origen'],
            'monto': float(row['Monto']),
            'es_salida': 'PAGASTE' in str(row['Tipo']).upper(),
            'conciliado': False,
            'es_vuelto_justificado': False,
            'detalle_match': None,
            'excel_huesped': '',
            'excel_monto': 0.0,
            'excel_hora': '',
            'excel_fila': '',
            'excel_fecha_registro': ''
        })

    # --- PASADA VUELTOS: Conciliación de Salidas Bancarias (PAGASTE) con Vueltos en Registro_Pagos ---
    for b in banco_items:
        if b['conciliado'] or not b['es_salida']:
            continue
        b_fecha = b['fecha'].date()
        candidatos_v = []
        for v in vueltos_sheets:
            if not v['conciliado'] and abs(v['monto'] - b['monto']) < 0.05:
                v_fecha = v['fecha_estimada'].date() if v['fecha_estimada'] else pd.to_datetime(v['fecha_registro_original'], dayfirst=True).date()
                diff_dias = abs((b_fecha - v_fecha).days)
                if diff_dias <= 2:
                    sim = similarity(v['huesped'], b['origen'])
                    diff_min = 9999
                    if v['fecha_estimada']:
                        diff_min = abs((b['fecha'] - v['fecha_estimada']).total_seconds()) / 60
                        diff_12h = abs(diff_min - 720)
                        diff_min = min(diff_min, diff_12h)
                    
                    if diff_min <= 60 or (diff_dias == 0 and sim >= 0.20) or diff_dias <= 1:
                        candidatos_v.append((diff_dias, diff_min, -sim, v))

        if candidatos_v:
            candidatos_v.sort(key=lambda x: (x[0], x[1], x[2]))
            best_v = candidatos_v[0][3]
            b['conciliado'] = True
            best_v['conciliado'] = True
            b['es_vuelto_justificado'] = True
            best_v['es_vuelto_justificado'] = True
            b['detalle_match'] = 'VUELTO A CLIENTE JUSTIFICADO (REGISTRO_PAGOS)'
            b['excel_huesped'] = f"Vuelto: {best_v['huesped']} ({best_v['habitacion']})"
            b['excel_monto'] = best_v['monto']
            b['excel_hora'] = best_v['texto_original']
            b['excel_fila'] = best_v['fila_excel']
            b['excel_fecha_registro'] = best_v['fecha_registro_original']

    # --- PASADA GASTOS: Conciliación de Salidas Bancarias (PAGASTE) con Registro_Gastos ---
    for b in banco_items:
        if b['conciliado'] or not b['es_salida']:
            continue
        b_fecha = b['fecha'].date()
        candidatos = []
        for g in gastos_sheets:
            if not g['conciliado'] and abs(g['monto'] - b['monto']) < 0.05:
                diff_dias = abs((b_fecha - g['fecha_gasto'].date()).days)
                if diff_dias <= 2:
                    sim = similarity(g['desc'], b['origen'])
                    candidatos.append((diff_dias, -sim, g))
        if candidatos:
            candidatos.sort(key=lambda x: (x[0], x[1]))
            best_g = candidatos[0][2]
            b['conciliado'] = True
            best_g['conciliado'] = True
            b['detalle_match'] = 'CUADRADO (GASTO HOSTAL)'
            b['excel_huesped'] = f"Gasto: {best_g['desc']} ({best_g['monto']})"
            b['excel_monto'] = best_g['monto']
            b['excel_hora'] = f"{best_g['monto']} {best_g['hora']}".strip()
            b['excel_fila'] = best_g['fila_excel']
            b['excel_fecha_registro'] = best_g['fecha_registro_original']

    # --- PASADA 0A: Grupos por Voucher Compartido / Hora Exacta (Ingresos) ---
    grupos_voucher = defaultdict(list)
    for y in yapes_sheets:
        if y['texto_original'] and y['fecha_estimada']:
            try:
                y_fecha_date = y['fecha_estimada'].date()
                t_norm = normalizar_hora_para_grupo(y['texto_original'])
                key1 = (t_norm, y_fecha_date)
                grupos_voucher[key1].append(y)

                t_extracted = extract_normalized_time(y['texto_original'])
                if t_extracted:
                    key2 = (f"TIME_{t_extracted}", y_fecha_date)
                    if y not in grupos_voucher[key2]:
                        grupos_voucher[key2].append(y)
            except:
                pass

    for (k_txt, f_date), y_group in list(grupos_voucher.items()):
        unconc = [y for y in y_group if not y['conciliado']]
        if len(unconc) >= 2:
            monto_total_grupo = sum(y['monto'] for y in unconc)
            nota_monto_grupo = parse_monto_nota(unconc[0]['texto_original'])
            for b in banco_items:
                if not b['conciliado'] and not b['es_salida'] and b['fecha'].date() == f_date:
                    exact_sum = abs(b['monto'] - monto_total_grupo) < 0.01
                    note_sum = (nota_monto_grupo is not None and abs(b['monto'] - nota_monto_grupo) < 0.01)
                    if not (exact_sum or note_sum):
                        continue
                    y_fecha_est = unconc[0]['fecha_estimada']
                    diff_min = abs((b['fecha'] - y_fecha_est).total_seconds()) / 60
                    diff_12h = abs(diff_min - 720)
                    group_names = " ".join(y['huesped'] for y in unconc)
                    sim = similarity(group_names, b['origen'])

                    can_match = False
                    if diff_min <= 15:
                        can_match = True
                    elif exact_sum and diff_min <= 30 and sim >= 0.20:
                        can_match = True
                    elif exact_sum and diff_12h <= 30 and sim >= 0.35:
                        can_match = True

                    if can_match:
                        b['conciliado'] = True
                        if exact_sum:
                            b['detalle_match'] = 'CUADRADO (PAGO GRUPAL COMPARTIDO)'
                        else:
                            b['detalle_match'] = f"CUADRADO (PAGO GRUPAL TIENDA - NOTA S/ {b['monto']:g} vs SUMA S/ {monto_total_grupo:g})"
                        b['excel_monto'] = monto_total_grupo
                        b['excel_huesped'] = "\n".join(y['huesped'] for y in unconc)
                        b['excel_hora'] = "\n".join(y['texto_original'] for y in unconc if y['texto_original'])
                        b['excel_fila'] = "\n".join(str(y['fila_excel']) for y in unconc)
                        b['excel_fecha_registro'] = "\n".join(y['fecha_registro_original'] for y in unconc)
                        for y in unconc: y['conciliado'] = True
                        break

    # --- PASADA 0B: Grupos por Huésped ---
    grupos_huesped = defaultdict(list)
    for y in yapes_sheets:
        if not y['conciliado']:
            try:
                y_fecha_date = pd.to_datetime(y['fecha_registro_original'], dayfirst=True).date()
                key = (y['huesped'].strip().lower(), y_fecha_date)
                grupos_huesped[key].append(y)
            except:
                pass

    for (huesped, fecha_reg_date), y_group in grupos_huesped.items():
        if len(y_group) >= 2:
            monto_total_grupo = sum(y['monto'] for y in y_group)
            for b in banco_items:
                if not b['conciliado'] and not b['es_salida'] and abs(b['monto'] - monto_total_grupo) < 0.01 and b['fecha'].date() == fecha_reg_date:
                    sim = similarity(huesped, b['origen'])
                    diffs = []
                    for y in y_group:
                        yf = y['fecha_estimada'] if y['fecha_estimada'] else pd.to_datetime(y['fecha_registro_original'], dayfirst=True)
                        diffs.append(abs((b['fecha'] - yf).total_seconds()) / 60)
                    min_diff = min(diffs)
                    min_diff_12h = min(abs(d - 720) for d in diffs)
                    if sim >= 0.35 and (min_diff <= 180 or min_diff_12h <= 180):
                        b['conciliado'] = True
                        b['detalle_match'] = 'CUADRADO (PAGO GRUPAL MISMO HUESPED)'
                        b['excel_monto'] = monto_total_grupo
                        b['excel_huesped'] = "\n".join(y['huesped'] for y in y_group)
                        b['excel_hora'] = "\n".join(y['texto_original'] for y in y_group if y['texto_original'])
                        b['excel_fila'] = "\n".join(str(y['fila_excel']) for y in y_group)
                        b['excel_fecha_registro'] = "\n".join(y['fecha_registro_original'] for y in y_group)
                        for y in y_group: y['conciliado'] = True
                        break

    # --- PASADA 0C: Abonos Múltiples del Banco para 1 Pago en Sheets (Multi-Bank Deposit) ---
    for y in yapes_sheets:
        if y['conciliado']: continue
        yf = y['fecha_estimada'] if y['fecha_estimada'] else pd.to_datetime(y['fecha_registro_original'], dayfirst=True)
        if not yf: continue
        y_date = yf.date()

        cands_b = [b for b in banco_items if not b['conciliado'] and not b['es_salida'] and b['fecha'].date() == y_date]
        if len(cands_b) < 2: continue

        b_by_titular = defaultdict(list)
        for b in cands_b:
            b_by_titular[b['origen'].strip().upper()].append(b)

        for titular, b_list in b_by_titular.items():
            if len(b_list) >= 2:
                for i in range(len(b_list)):
                    for j in range(i+1, len(b_list)):
                        b1, b2 = b_list[i], b_list[j]
                        if abs((b2['fecha'] - b1['fecha']).total_seconds()) <= 600:
                            sum_monto = b1['monto'] + b2['monto']
                            if abs(sum_monto - y['monto']) < 0.01:
                                sim = similarity(y['huesped'], titular)
                                diff_min = abs((b1['fecha'] - yf).total_seconds()) / 60
                                if sim >= 0.35 or diff_min <= 15:
                                    b1['conciliado'] = True
                                    b2['conciliado'] = True
                                    y['conciliado'] = True
                                    b1['detalle_match'] = f"CUADRADO (ABONO PARCIAL BCP 1/2: S/ {b1['monto']} + S/ {b2['monto']})"
                                    b2['detalle_match'] = f"CUADRADO (ABONO PARCIAL BCP 2/2: S/ {b1['monto']} + S/ {b2['monto']})"
                                    b1['excel_huesped'] = y['huesped']
                                    b2['excel_huesped'] = y['huesped']
                                    b1['excel_monto'] = b1['monto']
                                    b2['excel_monto'] = b2['monto']
                                    b1['excel_hora'] = f"{b1['monto']} {b1['fecha'].strftime('%H:%M')}"
                                    b2['excel_hora'] = f"{b2['monto']} {b2['fecha'].strftime('%H:%M')}"
                                    b1['excel_fila'] = y['fila_excel']
                                    b2['excel_fila'] = y['fila_excel']
                                    b1['excel_fecha_registro'] = y['fecha_registro_original']
                                    b2['excel_fecha_registro'] = y['fecha_registro_original']
                                    break
                    if y['conciliado']: break
            if y['conciliado']: break

    # --- PASADA 1: Tiempo Estricto (+/- 15 min con selección inteligente) ---
    for b in banco_items:
        if b['conciliado'] or b['es_salida']: continue
        candidatos_p1 = []
        for y in yapes_sheets:
            if not y['conciliado'] and y['fecha_estimada'] and abs(y['monto'] - b['monto']) < 0.01:
                diff_min = abs((b['fecha'] - y['fecha_estimada']).total_seconds()) / 60
                if diff_min <= 15:
                    sim = similarity(y['huesped'], b['origen'])
                    candidatos_p1.append((sim, diff_min, y))

        if candidatos_p1:
            # Sort: highest name similarity first (if sim >= 0.35), then lowest diff_min
            candidatos_p1.sort(key=lambda x: (-(1 if x[0] >= 0.35 else 0), -x[0], x[1]))
            best_sim, best_diff, best_y = candidatos_p1[0]

            best_y['conciliado'] = True
            b['conciliado'] = True
            if best_sim < 0.30 and best_diff <= 5:
                b['detalle_match'] = f"CUADRADO (HORA EXACTA VOUCHER / TERCERO: {b['origen']})"
            elif best_sim >= 0.35:
                b['detalle_match'] = 'CUADRADO'
            else:
                b['detalle_match'] = 'CUADRADO (TIEMPO CERCANO)'

            b['excel_huesped'] = best_y['huesped']
            b['excel_monto'] = best_y['monto']
            b['excel_hora'] = best_y['texto_original']
            b['excel_fila'] = best_y['fila_excel']
            b['excel_fecha_registro'] = best_y['fecha_registro_original']

    # --- PASADA VOUCHER / NOTA: Match por Monto en Nota Voucher (Diferencia / Vuelto / Descuento) ---
    for b in banco_items:
        if b['conciliado'] or b['es_salida']: continue
        for y in yapes_sheets:
            if not y['conciliado'] and y['texto_original']:
                nota_monto = parse_monto_nota(y['texto_original'])
                if nota_monto and abs(nota_monto - b['monto']) < 0.05:
                    yf = y['fecha_estimada'] if y['fecha_estimada'] else pd.to_datetime(y['fecha_registro_original'], dayfirst=True)
                    diff_min = abs((b['fecha'] - yf).total_seconds()) / 60
                    diff_12h = abs(diff_min - 720)
                    sim = similarity(y['huesped'], b['origen'])
                    if (diff_min <= 30 or diff_12h <= 30) and sim >= 0.35:
                        y['conciliado'] = True
                        b['conciliado'] = True
                        b['detalle_match'] = 'CUADRADO (MONTO EN NOTA VOUCHER / DIFERENCIA)'
                        b['excel_huesped'] = y['huesped']
                        b['excel_monto'] = y['monto']
                        b['excel_hora'] = y['texto_original']
                        b['excel_fila'] = y['fila_excel']
                        b['excel_fecha_registro'] = y['fecha_registro_original']
                        break

    # --- PASADA 2: Similitud de Nombre (+/- 60 min) ---
    for b in banco_items:
        if b['conciliado'] or b['es_salida']: continue
        for y in yapes_sheets:
            if not y['conciliado'] and y['fecha_estimada'] and abs(y['monto'] - b['monto']) < 0.01:
                diff_min = abs((b['fecha'] - y['fecha_estimada']).total_seconds()) / 60
                sim = similarity(y['huesped'], b['origen'])
                if sim >= 0.38 and diff_min <= 60:
                    y['conciliado'] = True
                    b['conciliado'] = True
                    b['detalle_match'] = 'CUADRADO (SIMILITUD NOMBRE)'
                    b['excel_huesped'] = y['huesped']
                    b['excel_monto'] = y['monto']
                    b['excel_hora'] = y['texto_original']
                    b['excel_fila'] = y['fila_excel']
                    b['excel_fecha_registro'] = y['fecha_registro_original']
                    break

    # --- PASADA 3: Error AM/PM o Desfase Horario (+/- 12h) y Registro Tardío ---
    for b in banco_items:
        if b['conciliado'] or b['es_salida']: continue
        for y in yapes_sheets:
            if not y['conciliado'] and y['fecha_estimada'] and abs(y['monto'] - b['monto']) < 0.01:
                diff_min = abs((b['fecha'] - y['fecha_estimada']).total_seconds()) / 60
                diff_12h = abs(diff_min - 720)
                sim = similarity(y['huesped'], b['origen'])
                if (diff_12h <= 30) or (sim >= 0.35 and diff_min <= 600):
                    y['conciliado'] = True
                    b['conciliado'] = True
                    b['detalle_match'] = 'CUADRADO (DESFASE HORARIO / AM-PM)'
                    b['excel_huesped'] = y['huesped']
                    b['excel_monto'] = y['monto']
                    b['excel_hora'] = y['texto_original']
                    b['excel_fila'] = y['fila_excel']
                    b['excel_fecha_registro'] = y['fecha_registro_original']
                    break

                # Caso registro tardío (+/- 1 día) con nombre coincidente y voucher idéntico (ej. Maximiliano Godoy)
                day_diff = (y['fecha_estimada'].date() - b['fecha'].date()).days
                if day_diff in [1, -1] and sim >= 0.45:
                    t_b = b['fecha'].hour * 60 + b['fecha'].minute
                    t_y = y['fecha_estimada'].hour * 60 + y['fecha_estimada'].minute
                    if abs(t_b - t_y) <= 15 or abs(abs(t_b - t_y) - 720) <= 15:
                        y['conciliado'] = True
                        b['conciliado'] = True
                        b['detalle_match'] = 'CUADRADO (REGISTRO TARDÍO CON VOUCHER COINCIDENTE)'
                        b['excel_huesped'] = y['huesped']
                        b['excel_monto'] = y['monto']
                        b['excel_hora'] = y['texto_original']
                        b['excel_fila'] = y['fila_excel']
                        b['excel_fecha_registro'] = y['fecha_registro_original']
                        break

    # --- PASADA 4: Monto Único en el Día (con filtro de coherencia horaria y nombre) ---
    for b in banco_items:
        if b['conciliado'] or b['es_salida']: continue
        b_fecha = b['fecha'].date()
        matching_yapes = []
        for y in yapes_sheets:
            if not y['conciliado'] and abs(y['monto'] - b['monto']) < 0.01:
                try:
                    y_fecha = pd.to_datetime(y['fecha_registro_original'], dayfirst=True).date()
                    if y_fecha == b_fecha:
                        matching_yapes.append(y)
                except:
                    pass
        if len(matching_yapes) == 1:
            y = matching_yapes[0]
            yf = y['fecha_estimada'] if y['fecha_estimada'] else pd.to_datetime(y['fecha_registro_original'], dayfirst=True)
            diff_min = abs((b['fecha'] - yf).total_seconds()) / 60 if yf else 9999
            diff_12h = abs(diff_min - 720)
            sim = similarity(y['huesped'], b['origen'])
            if sim >= 0.35 or diff_min <= 180 or diff_12h <= 30:
                y['conciliado'] = True
                b['conciliado'] = True
                b['detalle_match'] = 'CUADRADO (MONTO ÚNICO DÍA)'
                b['excel_huesped'] = y['huesped']
                b['excel_monto'] = y['monto']
                b['excel_hora'] = y['texto_original']
                b['excel_fila'] = y['fila_excel']
                b['excel_fecha_registro'] = y['fecha_registro_original']

    # --- 6. Construir filas para escribir ---
    reporte_rows = []
    for b in banco_items:
        if b.get('es_vuelto_justificado'):
            # Los vueltos a cliente justificados en Registro_Pagos se OMITEN de la conciliación según requerimiento
            continue

        if b['conciliado']:
            reporte_rows.append([
                b['fecha'].strftime('%d/%m/%Y %H:%M:%S'),
                b['origen'],
                b['monto'],
                b['excel_monto'],
                b['excel_huesped'],
                b['excel_hora'],
                b['detalle_match'],
                b['excel_fila'],
                b['excel_fecha_registro']
            ])
        else:
            estado_no_conc = 'SALIDA BANCO (Gasto / Vuelto no registrado)' if b['es_salida'] else 'SOBRANTE BANCO (No registrado)'
            reporte_rows.append([
                b['fecha'].strftime('%d/%m/%Y %H:%M:%S') if pd.notna(b['fecha']) else '',
                b['origen'],
                b['monto'],
                0.0,
                '',
                '',
                estado_no_conc,
                '',
                ''
            ])

    for y in yapes_sheets:
        if not y['conciliado']:
            reporte_rows.append([
                '',
                '',
                0.0,
                y['monto'],
                y['huesped'],
                y['texto_original'],
                'FALTANTE BANCO (Alerta Fraude / No depositado)',
                y['fila_excel'],
                y['fecha_registro_original']
            ])

    for y in yapes_duplicados_reporte:
        reporte_rows.append([
            '',
            '',
            0.0,
            y['monto'],
            y['huesped'],
            y['texto_original'],
            'PAGO DUPLICADO EN REGISTRO (Error Recepción - Repetido)',
            y['fila_excel'],
            y['fecha_registro_original']
        ])

    # Se omiten gastos no bancarios de Yape en el reporte final para no generar falsas alertas.
    # Los gastos por transferencia o cuenta BCP ya están registrados en Registro_Gastos y pertenecen al Estado de Cuenta BCP.

    print("\n=== RESUMEN DE CONCILIACIÓN ===")
    total_banco = len(banco_items)
    cuadrados = sum(1 for b in banco_items if b['conciliado'])
    sobrantes = sum(1 for b in banco_items if not b['conciliado'] and not b['es_salida'])
    salidas_no_reg = sum(1 for b in banco_items if not b['conciliado'] and b['es_salida'])
    faltantes_ingresos = sum(1 for y in yapes_sheets if not y['conciliado'])
    faltantes_gastos = sum(1 for g in gastos_sheets if not g['conciliado'])
    vueltos_justificados = sum(1 for v in vueltos_sheets if v['conciliado'])

    print(f"Total Banco nuevos: {total_banco}")
    print(f"[OK] Cuadrados (Ingresos + Gastos): {cuadrados}")
    if vueltos_justificados > 0:
        print(f"[OK] Vueltos a cliente conciliados con banco (omitidos de hoja): {vueltos_justificados}")
    print(f"[!] Sobrantes Banco (Ingresos no registrados en Excel): {sobrantes}")
    if salidas_no_reg > 0:
        print(f"[!] Salidas Banco no registradas (ni gasto ni vuelto): {salidas_no_reg}")
    if len(yapes_duplicados_reporte) > 0:
        print(f"[!] Pagos Duplicados en Excel (excluidos de fraude): {len(yapes_duplicados_reporte)}")
    print(f"[ALERTA] Faltantes Banco (Ingresos en Excel pero no en Yape): {faltantes_ingresos}")
    if faltantes_gastos > 0:
        print(f"[INFO] Gastos BCP / Transferencias en misma cuenta Yape: {faltantes_gastos}")

    # 7. Escribir en Google Sheets (Append Incremental)
    print("\n7. Escribiendo nuevos registros en CONCILIACION PAGOS YAPE...")
    try:
        try:
            ws_conc = sheet.worksheet("CONCILIACION PAGOS YAPE")
        except gspread.exceptions.WorksheetNotFound:
            ws_conc = sheet.add_worksheet(title="CONCILIACION PAGOS YAPE", rows="200", cols="9")
            ws_conc.append_row([
                'Fecha y Hora Banco', 'Origen / Detalle Banco', 'Monto Banco',
                'Monto Excel', 'Huésped Excel', 'Hora Anotada Excel',
                'Estado de Conciliación', 'Fila en Registro_Pagos', 'Fecha Registro Excel'
            ])

        if reporte_rows:
            ws_conc.append_rows(reporte_rows)
            print(f"   -> ÉXITO: Se agregaron {len(reporte_rows)} nuevas filas al final de la hoja.")
        else:
            print("   -> No hay registros nuevos que agregar.")

    except Exception as e:
        print(f"❌ Error al escribir en Google Sheets: {e}")

    # 8. Sincronizar casos no cuadrados en ALERTAS_AUDITORIA
    actualizar_alertas_auditoria(sheet)

def actualizar_alertas_auditoria(sheet):
    print("\n8. Actualizando sección de Yapes por corregir en ALERTAS_AUDITORIA...")
    try:
        ws_conc = sheet.worksheet("CONCILIACION PAGOS YAPE")
        ws_alertas = sheet.worksheet("ALERTAS_AUDITORIA")
        conc_rows = ws_conc.get_all_values()
        alertas_rows = ws_alertas.get_all_values()

        # Encontrar la fila de cabecera de la sección 2 en ALERTAS_AUDITORIA
        sec2_title_row = None
        for idx, r in enumerate(alertas_rows, start=1):
            if len(r) > 0 and ("AUDITORÍA Y CONCILIACIÓN" in r[0].upper() or "PAGOS BANCARIOS" in r[0].upper()):
                sec2_title_row = idx
                break

        if not sec2_title_row:
            sec2_title_row = len(alertas_rows) + 2

        header_row = sec2_title_row + 1
        data_start_row = sec2_title_row + 2

        total_validos = 0
        total_cuadrados = 0
        pendientes_yape = []

        for idx, r in enumerate(conc_rows[1:], start=2):
            if not any(c.strip() for c in r):
                continue
            f_banco = r[0].strip() if len(r) > 0 else ""
            origen_b = r[1].strip() if len(r) > 1 else ""
            m_banco = r[2].strip() if len(r) > 2 else "0"
            m_excel = r[3].strip() if len(r) > 3 else "0"
            huesped_e = r[4].strip() if len(r) > 4 else ""
            hora_e = r[5].strip() if len(r) > 5 else ""
            estado = r[6].strip() if len(r) > 6 else ""
            fila_reg = r[7].strip() if len(r) > 7 else ""
            f_reg = r[8].strip() if len(r) > 8 else ""

            if not estado:
                continue

            total_validos += 1
            if estado.upper().startswith("CUADRADO"):
                total_cuadrados += 1
                continue

            # Construir fila para ALERTAS_AUDITORIA
            fecha_mostrar = f_banco if f_banco else f_reg
            monto_mostrar = m_banco if (m_banco and m_banco != "0" and m_banco != "0.0") else m_excel
            origen_mostrar = origen_b if origen_b else f"{huesped_e} ({fila_reg})"

            est_upper = estado.upper()
            if "SOBRANTE" in est_upper:
                diag = f"Ingreso de S/ {m_banco} en banco sin registro en Habitaciones ni Tienda (Fila {idx} Conciliación)"
                rec = "Verificar a qué habitación o venta de tienda corresponde y registrarlo con su hora de voucher"
            elif "FALTANTE" in est_upper:
                diag = f"Registrado en {fila_reg} ({huesped_e}, nota '{hora_e}') por S/ {m_excel}, pero no figura en el Yape"
                rec = "Revisar voucher físico/celular, corregir si fue Efectivo o verificar si es registro duplicado"
            elif "DIFERENCIA" in est_upper:
                diag = f"Yape depositó S/ {m_banco}, pero en {fila_reg} ({huesped_e}) se registró S/ {m_excel}"
                rec = "Corregir el monto en Registro_Pagos o cobrar la diferencia pendiente al huésped"
            else:
                diag = f"Caso en revisión: {huesped_e or origen_b} ({fila_reg or f'Fila {idx}'}) - Nota: {hora_e}"
                rec = "Confirmar medio de pago real en recepción (posible efectivo registrado como Yape)"

            pendientes_yape.append([
                fecha_mostrar,
                monto_mostrar,
                origen_mostrar,
                estado,
                diag,
                rec,
                "",
                ""
            ])

        tasa = (total_cuadrados / total_validos) if total_validos > 0 else 1.0
        ws_alertas.update_acell(f"G{sec2_title_row}", round(tasa, 6))

        # Limpiar filas antiguas debajo de la cabecera de sección 2 y escribir las actuales
        max_clear = max(len(alertas_rows), data_start_row + len(pendientes_yape) + 5)
        empty_block = [[""] * 8 for _ in range(max_clear - data_start_row + 1)]
        if empty_block:
            ws_alertas.update(range_name=f"A{data_start_row}:H{max_clear}", values=empty_block)

        if pendientes_yape:
            end_row = data_start_row + len(pendientes_yape) - 1
            ws_alertas.update(range_name=f"A{data_start_row}:H{end_row}", values=pendientes_yape)
            print(f"   -> {len(pendientes_yape)} casos de Yape por revisar/corregir publicados en ALERTAS_AUDITORIA.")
        else:
            ws_alertas.update(range_name=f"A{data_start_row}:H{data_start_row}", values=[["✅ Todo cuadrado al 100%", "", "", "SIN ALERTAS PENDIENTES", "", "", "", ""]])
            print("   -> 0 alertas pendientes: todos los Yapes están cuadrados.")

        sincronizar_yape_sqlite_local(conc_rows)
    except Exception as e:
        print(f"   -> Error al actualizar ALERTAS_AUDITORIA: {e}")


def sincronizar_yape_sqlite_local(conc_rows):
    """
    Sincroniza los registros de CONCILIACION PAGOS YAPE directamente hacia la tabla
    yape_conciliacion_hostal en la SQLite activa (Prueba :8082 si es_prueba(), u Operativa :8080 si no).
    """
    try:
        from hoja_activa import (
            conectar_sqlite_activa,
            limpiar_outbox_caches_locales,
            obtener_sqlite_path,
        )

        conn = conectar_sqlite_activa(timeout=30.0)
        if conn is None:
            print(f"   -> [WARN SQLite] No se encontró BD activa en {obtener_sqlite_path()}")
            return

        ahora_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            # Mapas de referencia por sheet_row para enriquecer hab_calzada e id_estancia_calzada
            pagos_by_row = {}
            for pr in conn.execute("SELECT sheet_row, pago_id, hab, huesped, nro_operacion FROM pagos WHERE sheet_row > 0").fetchall():
                pagos_by_row[f"P{pr['sheet_row']}"] = dict(pr)

            tienda_by_row = {}
            for tr in conn.execute("SELECT sheet_row, venta_uuid, hab, producto, nro_op_yape FROM ventas_tienda WHERE sheet_row > 0").fetchall():
                tienda_by_row[f"T{tr['sheet_row']}"] = dict(tr)

            existentes = {}
            for yr in conn.execute(
                "SELECT id, fecha_operacion, hora_operacion, remitente_bcp, ROUND(monto_bcp, 2) AS m, estado_auditoria, id_estancia_calzada, hab_calzada, huesped_calzado, observacion FROM yape_conciliacion_hostal"
            ).fetchall():
                d_y = dict(yr)
                f_op = str(d_y["fecha_operacion"] or "").strip()
                m_f = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", f_op)
                if m_f:
                    f_op = f"{int(m_f.group(1)):02d}/{int(m_f.group(2)):02d}/{m_f.group(3)}"
                h_op = str(d_y["hora_operacion"] or "").strip()
                m_val = round(float(d_y["m"] or 0.0), 2)
                existentes[(f_op, h_op, m_val)] = d_y

            insertados = 0
            actualizados = 0

            for r in conc_rows[1:]:
                if not r or not str(r[0] or "").strip():
                    continue
                f_banco = str(r[0]).strip()
                partes_dt = f_banco.split()
                if len(partes_dt) < 2:
                    continue
                m_f = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", partes_dt[0])
                if not m_f:
                    continue
                fecha_op = f"{int(m_f.group(1)):02d}/{int(m_f.group(2)):02d}/{m_f.group(3)}"
                hora_op = partes_dt[1].strip()
                if len(hora_op) == 5:
                    hora_op += ":00"

                remitente_raw = str(r[1] if len(r) > 1 else "").strip().upper()
                try:
                    monto_bcp = round(float(str(r[2] if len(r) > 2 else "0").replace("S/", "").replace(",", "").strip() or 0.0), 2)
                except Exception:
                    monto_bcp = 0.0
                if monto_bcp <= 0:
                    continue

                huesped_e = str(r[4] if len(r) > 4 else "").strip()
                hora_e = str(r[5] if len(r) > 5 else "").strip()
                estado_sh = str(r[6] if len(r) > 6 else "").strip()
                ref_sh = str(r[7] if len(r) > 7 else "").strip()

                est_up = estado_sh.upper()
                es_salida = "SALIDA" in est_up or remitente_raw.startswith("SALIDA")
                if es_salida and not remitente_raw.startswith("SALIDA"):
                    remitente_bcp = f"SALIDA: {remitente_raw}"
                else:
                    remitente_bcp = remitente_raw or "CLIENTE YAPE"

                if est_up.startswith("CUADRADO"):
                    estado_aud = "COMPROBADO"
                elif "DIFERENCIA" in est_up or "EXCEDENTE" in est_up:
                    estado_aud = "EXCEDENTE"
                elif es_salida:
                    estado_aud = "SALIDA"
                else:
                    estado_aud = "SIN ANOTAR"

                # Resolver referencias Pxxxx / Txxxx / Gxxxx
                refs_tokens = re.findall(r"\b[PTG]\d+\b", ref_sh.upper())
                ids_calz = []
                habs_calz = []
                for tk in refs_tokens:
                    if tk in pagos_by_row:
                        p_info = pagos_by_row[tk]
                        if p_info.get("pago_id"):
                            ids_calz.append(str(p_info["pago_id"]))
                        if p_info.get("hab") and str(p_info["hab"]) not in habs_calz:
                            habs_calz.append(str(p_info["hab"]))
                    elif tk in tienda_by_row:
                        t_info = tienda_by_row[tk]
                        if t_info.get("venta_uuid"):
                            ids_calz.append(f"TIENDA-{t_info['venta_uuid']}")
                        h_t = str(t_info.get("hab") or "TIENDA").strip() or "TIENDA"
                        if h_t not in habs_calz:
                            habs_calz.append(h_t)
                    else:
                        ids_calz.append(tk)

                id_calz_str = "|".join(ids_calz) if ids_calz else ref_sh.replace("\n", "|")
                hab_calz_str = "/".join(habs_calz) if habs_calz else ("TIENDA" if "TIENDA" in est_up else "")
                obs_str = f"✅ {estado_sh}: {huesped_e} (S/ {monto_bcp:.2f}, glosa: '{hora_e}')" if estado_aud == "COMPROBADO" else estado_sh

                clave = (fecha_op, hora_op, monto_bcp)
                ex = existentes.get(clave)
                if ex is None:
                    conn.execute(
                        """
                        INSERT INTO yape_conciliacion_hostal (
                            fecha_operacion, hora_operacion, remitente_bcp, monto_bcp,
                            nro_operacion, id_estancia_calzada, hab_calzada, huesped_calzado,
                            estado_auditoria, observacion, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            fecha_op,
                            hora_op,
                            remitente_bcp,
                            monto_bcp,
                            hora_e or f"{monto_bcp:g}",
                            id_calz_str,
                            hab_calz_str,
                            huesped_e,
                            estado_aud,
                            obs_str,
                            ahora_iso,
                        ),
                    )
                    insertados += 1
                else:
                    # Actualizar si el estado en la hoja pasó a COMPROBADO/EXCEDENTE o si faltaba enlazar
                    ex_est = str(ex.get("estado_auditoria") or "")
                    if estado_aud in ("COMPROBADO", "EXCEDENTE") and (
                        ex_est not in ("COMPROBADO", "EXCEDENTE")
                        or (not ex.get("huesped_calzado") and huesped_e)
                    ):
                        conn.execute(
                            """
                            UPDATE yape_conciliacion_hostal
                            SET id_estancia_calzada = CASE WHEN COALESCE(id_estancia_calzada, '') = '' THEN ? ELSE id_estancia_calzada END,
                                hab_calzada = CASE WHEN COALESCE(hab_calzada, '') = '' THEN ? ELSE hab_calzada END,
                                huesped_calzado = CASE WHEN COALESCE(huesped_calzado, '') = '' THEN ? ELSE huesped_calzado END,
                                nro_operacion = CASE WHEN COALESCE(nro_operacion, '') = '' THEN ? ELSE nro_operacion END,
                                estado_auditoria = ?,
                                observacion = ?,
                                updated_at = ?
                            WHERE id = ?
                            """,
                            (
                                id_calz_str,
                                hab_calz_str,
                                huesped_e,
                                hora_e,
                                estado_aud,
                                obs_str,
                                ahora_iso,
                                ex["id"],
                            ),
                        )
                        actualizados += 1

            limpiar_outbox_caches_locales(conn)
            conn.commit()
            tot_y = conn.execute("SELECT COUNT(*) FROM yape_conciliacion_hostal").fetchone()[0]
            print(
                f"   -> [SQLITE LOCAL] '{obtener_sqlite_path()}' sincronizada: "
                f"yape_conciliacion_hostal (insertados={insertados}, actualizados={actualizados}, total={tot_y})."
            )
        finally:
            conn.close()
    except Exception as e_sq_y:
        print(f"   -> [WARN SQLite] No se pudo sincronizar yape_conciliacion_hostal: {e_sq_y}")

if __name__ == '__main__':
    main()

