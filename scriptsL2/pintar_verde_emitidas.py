import os
import sys
import argparse
import gspread
from google.oauth2.service_account import Credentials

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

parser = argparse.ArgumentParser(description="Pintar de verde comprobantes emitidos en CALZADO_NOV / CALZADO_OCT")
parser.add_argument("--mes", type=int, default=11, choices=[10, 11], help="Mes de calzado: 11 (CALZADO_NOV, por defecto) o 10 (CALZADO_OCT)")
args, _ = parser.parse_known_args()
HOJA_CALZADO = "CALZADO_NOV" if args.mes == 11 else "CALZADO_OCT"

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

creds = Credentials.from_service_account_file(RUTA_CREDS, scopes=SCOPES)
client = gspread.authorize(creds)
sh = client.open_by_key(SPREADSHEET_ID)
ws = sh.worksheet(HOJA_CALZADO)

rows = ws.get_all_values()

# Comprobantes emitidos por el sistema (Octubre + Noviembre)
cps_emitidos = [f"EB01-{n}" for n in range(6611, 7500)] + [f"EB01 - {n}" for n in range(6611, 7500)]
cps_emitidos += [f"E001-{n}" for n in range(2064, 2300)] + [f"E001 - {n}" for n in range(2064, 2300)]

filas_a_pintar_verde = []
filas_especiales_morado = []

for idx, r in enumerate(rows, start=1):
    if idx == 1:
        continue
    estancia_id = r[5] if len(r) > 5 else ""
    cps_sunat = r[7] if len(r) > 7 else ""
    
    # Casos especiales Lucero 207 (morado)
    if estancia_id in ["E5049", "E5162"] or "LUCERO PALOMINO" in (r[3] if len(r)>3 else "") and (r[2] if len(r)>2 else "") == "207":
        filas_especiales_morado.append((idx, estancia_id))
        continue

    # Verificar si contiene algún comprobante emitido por nosotros
    es_emitido = any(cp in cps_sunat for cp in cps_emitidos)
    if es_emitido:
        filas_a_pintar_verde.append((idx, r[2], r[3], cps_sunat))

print(f"Total filas detectadas para pintar de VERDE: {len(filas_a_pintar_verde)}")
for f_idx, hab, huesped, cp in filas_a_pintar_verde:
    print(f"  Fila {f_idx}: Hab {hab} | {huesped[:25]} | {cp}")

print(f"\nTotal filas especiales en MORADO: {len(filas_especiales_morado)}")

requests = []

# Color Verde Suave (#D9F2D9)
color_verde = {"red": 0.85, "green": 0.95, "blue": 0.85}
# Color Morado Suave (#E6D8F5)
color_morado = {"red": 0.90, "green": 0.85, "blue": 0.96}

for f_idx, _, _, _ in filas_a_pintar_verde:
    requests.append({
        "repeatCell": {
            "range": {
                "sheetId": ws.id,
                "startRowIndex": f_idx - 1,
                "endRowIndex": f_idx,
                "startColumnIndex": 0,
                "endColumnIndex": 26,
            },
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": color_verde
                }
            },
            "fields": "userEnteredFormat.backgroundColor"
        }
    })

for f_idx, _ in filas_especiales_morado:
    requests.append({
        "repeatCell": {
            "range": {
                "sheetId": ws.id,
                "startRowIndex": f_idx - 1,
                "endRowIndex": f_idx,
                "startColumnIndex": 0,
                "endColumnIndex": 26,
            },
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": color_morado
                }
            },
            "fields": "userEnteredFormat.backgroundColor"
        }
    })

if requests:
    sh.batch_update({"requests": requests})
    print(f"\n✅ ¡Pintado completado exitosamente en Google Sheets!")
else:
    print("\nNo se encontraron filas para pintar.")
