import sys
import os
import re
import json
import collections
from datetime import datetime, timedelta
import gspread
from google.oauth2.service_account import Credentials

try:
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr is not None:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ANIO = 2026
MES = 10
MES_STR = str(MES).zfill(2)
ANIO_STR = str(ANIO)

from pathlib import Path
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

RUTA_LINEAS_JSON = r"C:\Hostal_Sistema\datos_sunat\sunat_oct_lineas.json"
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

HOJA_ESTANCIAS = "Registro_Estancias"
HOJA_PAGOS = "Registro_Pagos"
HOJA_CONCILIACION = "CONCILIACION PAGOS YAPE"
HOJA_CALZADO = "CALZADO_OCT"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def a_num(val):
    if val is None or val == "":
        return 0.0
    if isinstance(val, (int, float)):
        return round(float(val), 2)
    s = str(val).replace("S/", "").replace(",", "").strip()
    try:
        return round(float(s), 2)
    except:
        return 0.0


def normalizar_fecha(fec_str):
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", str(fec_str).strip())
    if m:
        return f"{int(m.group(1)):02d}/{int(m.group(2)):02d}/{m.group(3)}"
    return str(fec_str).strip()


def generar_ventana_noches(fecha_checkin_str, noches_reales):
    try:
        dt = datetime.strptime(normalizar_fecha(fecha_checkin_str), "%d/%m/%Y")
    except:
        return [normalizar_fecha(fecha_checkin_str)]
    n = max(1, int(round(a_num(noches_reales) or 1)))
    return [(dt + timedelta(days=k)).strftime("%d/%m/%Y") for k in range(n)]


def cargar_datos_sheets(sh):
    print("[1/4] Leyendo Registro_Estancias, Registro_Pagos y CONCILIACION PAGOS YAPE para Octubre...", flush=True)
    ws_est = sh.worksheet(HOJA_ESTANCIAS)
    fmt_est = ws_est.get_all_values()
    unf_est = ws_est.get_all_values(value_render_option="UNFORMATTED_VALUE")

    estancias_mes = []
    for idx_row, (rf, ru) in enumerate(zip(fmt_est, unf_est), start=1):
        if idx_row == 1:
            continue
        fec_b = rf[1].strip() if len(rf) > 1 else ""
        if re.match(rf"^\d{{1,2}}/0?{MES}/{ANIO}$", fec_b):
            fec_norm = normalizar_fecha(fec_b)
            id_est = rf[0].strip() if len(rf) > 0 else ""
            huesped = rf[3].strip() if len(rf) > 3 else ""
            dni = rf[4].strip() if len(rf) > 4 else ""
            hab = rf[11].strip().upper() if len(rf) > 11 else ""
            noches_p = max(1, int(round(a_num(ru[15]) if len(ru) > 15 and a_num(ru[15]) > 0 else 1)))
            total_v = a_num(ru[21]) if len(ru) > 21 else 0.0
            efectivo_aa = a_num(ru[26]) if len(ru) > 26 else 0.0
            yape_ab = a_num(ru[27]) if len(ru) > 27 else 0.0
            pagado_ac = a_num(ru[28]) if len(ru) > 28 else 0.0
            solicita_af = rf[31].strip() if len(rf) > 31 else ""
            estado_cp_ah = rf[33].strip() if len(rf) > 33 else ""
            monto_aj = a_num(ru[35]) if len(ru) > 35 else 0.0
            ruc_ay = rf[50].strip() if len(rf) > 50 else ""
            excedente_az = a_num(ru[51]) if len(ru) > 51 else 0.0

            estancias_mes.append(
                {
                    "row": idx_row,
                    "id_estancia": id_est,
                    "noche_checkin": fec_norm,
                    "habitacion": hab,
                    "huesped": huesped,
                    "dni": dni,
                    "ruc": ruc_ay,
                    "noches": noches_p,
                    "ventana": generar_ventana_noches(fec_norm, noches_p),
                    "total_v": total_v,
                    "efectivo_aa": efectivo_aa,
                    "yape_ab": yape_ab,
                    "pagado_ac": pagado_ac,
                    "solicita_af": solicita_af,
                    "estado_cp_ah": estado_cp_ah,
                    "monto_aj": monto_aj,
                    "excedente_az": excedente_az,
                    "lineas_sunat": [],
                    "cubierto_por_cp": "",
                    "ambiguo": False,
                }
            )

    ws_pag = sh.worksheet(HOJA_PAGOS)
    fmt_pag = ws_pag.get_all_values()
    unf_pag = ws_pag.get_all_values(value_render_option="UNFORMATTED_VALUE")
    pagos_por_fila = {}
    pagos_por_estancia = collections.defaultdict(list)

    for idx_p, (pf, pu) in enumerate(zip(fmt_pag, unf_pag), start=1):
        if idx_p == 1:
            continue
        id_pago = pf[0].strip() if len(pf) > 0 else ""
        fec_pago = pf[1].strip() if len(pf) > 1 else ""
        id_est_p = pf[2].strip() if len(pf) > 2 else ""
        hab_p = pf[3].strip().upper() if len(pf) > 3 else ""
        monto_p = a_num(pu[5]) if len(pu) > 5 else 0.0
        medio_p = pf[6].strip() if len(pf) > 6 else ""
        concepto_p = pf[8].strip() if len(pf) > 8 else ""

        p_obj = {
            "row_pago": idx_p,
            "ref_p": f"P{idx_p}",
            "id_pago": id_pago,
            "fecha_hora": fec_pago,
            "id_estancia": id_est_p,
            "habitacion": hab_p,
            "monto": monto_p,
            "medio": medio_p,
            "concepto": concepto_p,
            "estados_banco": [],
            "montos_banco": [],
        }
        pagos_por_fila[f"P{idx_p}"] = p_obj
        if id_est_p:
            pagos_por_estancia[id_est_p].append(p_obj)
        if fec_pago and fec_pago != id_est_p:
            pagos_por_estancia[fec_pago].append(p_obj)

    ws_conc = sh.worksheet(HOJA_CONCILIACION)
    fmt_conc = ws_conc.get_all_values()
    unf_conc = ws_conc.get_all_values(value_render_option="UNFORMATTED_VALUE")

    banco_sin_registro = []
    filas_banco_mes = 0
    suma_banco_mes = 0.0

    for idx_c, (cf, cu) in enumerate(zip(fmt_conc, unf_conc), start=1):
        if idx_c == 1:
            continue
        fec_banco = cf[0].strip() if len(cf) > 0 else ""
        origen_b = cf[1].strip() if len(cf) > 1 else ""
        monto_b = a_num(cu[2]) if len(cu) > 2 else 0.0
        estado_g = cf[6].strip() if len(cf) > 6 else ""
        refs_h = cf[7].strip() if len(cf) > 7 else ""

        es_mes = bool(re.match(rf"^\d{{1,2}}/0?{MES}/{ANIO}", fec_banco))
        if es_mes:
            filas_banco_mes += 1
            suma_banco_mes = round(suma_banco_mes + monto_b, 2)
            if "SOBRANTE BANCO" in estado_g.upper():
                banco_sin_registro.append(
                    {
                        "row_conc": idx_c,
                        "fecha_hora": fec_banco,
                        "origen": origen_b,
                        "monto": monto_b,
                        "estado": estado_g,
                    }
                )

        for ref_p in re.findall(r"\bP\d+\b", refs_h.upper()):
            if ref_p in pagos_por_fila:
                pagos_por_fila[ref_p]["estados_banco"].append(estado_g)
                pagos_por_fila[ref_p]["montos_banco"].append(monto_b)

    ok_recepcion = {}
    try:
        ws_cal_prev = sh.worksheet(HOJA_CALZADO)
        vals_cal_prev = ws_cal_prev.get_all_values()
        for r_idx_c, r_cal in enumerate(vals_cal_prev, start=1):
            if r_idx_c == 1 or not r_cal:
                continue
            marca_a = str(r_cal[0]).strip().upper()
            if marca_a:
                fila_est_ref = ""
                for cell_val in r_cal[1:8]:
                    if re.match(r"^E\d+$", str(cell_val).strip().upper()):
                        fila_est_ref = str(cell_val).strip().upper()
                        break
                if fila_est_ref:
                    ok_recepcion[fila_est_ref] = marca_a
    except Exception:
        pass

    return ws_est, estancias_mes, pagos_por_estancia, banco_sin_registro, filas_banco_mes, suma_banco_mes, ok_recepcion


def main():
    creds = Credentials.from_service_account_file(RUTA_CREDS, scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)

    ws_est, estancias, pagos_por_estancia, banco_sin_registro, filas_banco_mes, suma_banco_mes, ok_recepcion = cargar_datos_sheets(sh)

    with open(RUTA_LINEAS_JSON, "r", encoding="utf-8") as f:
        lineas_sunat = json.load(f)

    # ================== 3.0 CALIBRACIÓN ==================
    print("\n================== 3.0 CALIBRACIÓN DE REGLAS OCTUBRE ==================")
    con_aj = [e for e in estancias if e["monto_aj"] > 0]
    eq_v = sum(1 for e in con_aj if abs(e["monto_aj"] - e["total_v"]) <= 0.01)
    eq_ac = sum(1 for e in con_aj if abs(e["monto_aj"] - e["pagado_ac"]) <= 0.01)
    pct_v = (eq_v / len(con_aj) * 100) if con_aj else 0.0
    pct_ac = (eq_ac / len(con_aj) * 100) if con_aj else 0.0
    print(f"Estancias en Octubre: {len(estancias)} | Con AJ > 0: {len(con_aj)}")
    print(f"   -> AJ == V (Total_Cuenta):  {eq_v}/{len(con_aj)} ({pct_v:.1f}%)")
    print(f"   -> AJ == AC (Total_Pagado): {eq_ac}/{len(con_aj)} ({pct_ac:.1f}%)")

    indice_sunat_hab_noche = collections.defaultdict(float)
    for l in lineas_sunat:
        if l["estado"] == "VIGENTE" and l["habitacion"] != "GRUPAL":
            indice_sunat_hab_noche[(l["noche"], l["habitacion"])] += l["monto_linea"]

    multi_noche_aj = [e for e in con_aj if e["noches"] > 1]
    match_ventana = 0
    match_solo_checkin = 0
    for e in multi_noche_aj:
        s_checkin = indice_sunat_hab_noche.get((e["noche_checkin"], e["habitacion"]), 0.0)
        s_ventana = sum(indice_sunat_hab_noche.get((d, e["habitacion"]), 0.0) for d in e["ventana"])
        if abs(e["monto_aj"] - s_ventana) <= 0.01 and s_ventana > 0:
            match_ventana += 1
        if abs(e["monto_aj"] - s_checkin) <= 0.01 and s_checkin > 0:
            match_solo_checkin += 1

    regla_calibracion = (
        f"Calibración Oct-{ANIO}: AJ coincide {pct_ac:.1f}% con AC y {pct_v:.1f}% con V; "
        f"en P>1 cuadra ventana={match_ventana} vs solo_checkin={match_solo_checkin}."
    )

    # ================== 3.1 ASIGNACIÓN SUNAT -> ESTANCIA ==================
    print("\n[2/4] Asignando líneas SUNAT a estancias de Octubre...", flush=True)
    est_por_checkin = collections.defaultdict(list)
    est_por_ventana = collections.defaultdict(list)
    for e in estancias:
        if e["habitacion"]:
            est_por_checkin[(e["noche_checkin"], e["habitacion"])].append(e)
            for d_v in e["ventana"]:
                est_por_ventana[(d_v, e["habitacion"])].append(e)

    lineas_sin_asignar = []
    lineas_grupales_revisar = []

    lineas_por_cp = collections.defaultdict(list)
    for l in lineas_sunat:
        if l["estado"] == "VIGENTE" and l["habitacion"] != "GRUPAL":
            lineas_por_cp[(l["nro_comprobante"], l["habitacion"])].append(l)

    lineas_ya_consumidas = set()
    for (cp_num, hab_cp), l_list in lineas_por_cp.items():
        if len(l_list) > 1:
            suma_cp = round(sum(x["monto_linea"] for x in l_list), 2)
            noches_cp = [x["noche"] for x in l_list]
            est_cands = []
            for n_d in noches_cp:
                est_cands.extend(est_por_checkin.get((n_d, hab_cp), []))
            match_acum = [e for e in est_cands if abs(e["monto_aj"] - suma_cp) <= 0.05 and not e["lineas_sunat"]]
            if len(match_acum) == 1:
                e_dest = match_acum[0]
                for x in l_list:
                    e_dest["lineas_sunat"].append(x)
                    lineas_ya_consumidas.add(id(x))
                for e_otra in est_cands:
                    if e_otra["row"] != e_dest["row"] and e_otra["monto_aj"] == 0:
                        if (e_otra["dni"] and e_otra["dni"] == e_dest["dni"]) or (
                            e_otra["huesped"][:12].upper() == e_dest["huesped"][:12].upper()
                        ):
                            e_otra["cubierto_por_cp"] = f"{cp_num} (acum. en E{e_dest['row']})"

    # Procesar primero Facturas y Boletas, y al final Notas de Crédito para que encuentren a su comprobante origen
    lineas_ordenadas = sorted(lineas_sunat, key=lambda x: 1 if x.get("tipo") == "NOTA_CREDITO" else 0)

    for l in lineas_ordenadas:
        if id(l) in lineas_ya_consumidas:
            continue

        # Si es Nota de Crédito y tiene cp_modificado, asignar a la misma estancia que tiene el comprobante modificado
        cp_mod = str(l.get("cp_modificado", "")).strip()
        if l.get("tipo") == "NOTA_CREDITO" and cp_mod:
            est_origen = [
                e for e in estancias
                if any(x.get("nro_comprobante") == cp_mod and x.get("noche") == l.get("noche") for x in e["lineas_sunat"])
            ] or [
                e for e in estancias
                if any(x.get("nro_comprobante") == cp_mod for x in e["lineas_sunat"])
            ]
            if est_origen:
                est_origen[0]["lineas_sunat"].append(l)
                continue

        if l["estado"] != "VIGENTE" or l["monto_linea"] == 0:
            candidatos_anul = est_por_checkin.get((l["noche"], l["habitacion"]), [])
            if candidatos_anul:
                candidatos_anul[0]["lineas_sunat"].append(l)
            else:
                lineas_sin_asignar.append(l)
            continue

        hab = l["habitacion"]
        noche = l["noche"]
        doc = str(l.get("doc", "")).strip()
        monto_l = l["monto_linea"]

        if hab == "GRUPAL":
            matches_doc = [
                e
                for e in estancias
                if noche in e["ventana"] and doc and (e["dni"] == doc or e["ruc"] == doc)
            ]
            if len(matches_doc) == 1:
                matches_doc[0]["lineas_sunat"].append(l)
            else:
                lineas_grupales_revisar.append(l)
            continue

        cands = est_por_checkin.get((noche, hab), [])
        if len(cands) == 1:
            cands[0]["lineas_sunat"].append(l)
            continue
        elif len(cands) > 1:
            por_doc = [e for e in cands if doc and (e["dni"] == doc or e["ruc"] == doc)]
            if len(por_doc) == 1:
                por_doc[0]["lineas_sunat"].append(l)
                continue
            por_aj = [e for e in cands if not e["lineas_sunat"] and abs(e["monto_aj"] - monto_l) <= 0.01]
            if len(por_aj) == 1:
                por_aj[0]["lineas_sunat"].append(l)
                continue
            libres = [e for e in cands if not e["lineas_sunat"]]
            elegida = libres[0] if libres else cands[0]
            elegida["lineas_sunat"].append(l)
            elegida["ambiguo"] = True
            continue

        cands_v = est_por_ventana.get((noche, hab), [])
        if len(cands_v) == 1:
            cands_v[0]["lineas_sunat"].append(l)
            continue
        elif len(cands_v) > 1:
            por_doc = [e for e in cands_v if doc and (e["dni"] == doc or e["ruc"] == doc)]
            elegida = por_doc[0] if len(por_doc) == 1 else cands_v[0]
            elegida["lineas_sunat"].append(l)
            if len(por_doc) != 1:
                elegida["ambiguo"] = True
            continue

        lineas_sin_asignar.append(l)

    # ================== 3.2 Y 3.3 YAPE, BANCO, JERARQUÍA (FACTURA > BOLETA) Y EXCEDENTE EFECTIVO ==================
    print("[3/4] Aplicando jerarquía (Factura > Boleta), Excedente Efectivo SUNAT y calculando ESTADO_FINAL...", flush=True)
    filas_calzado = []
    batch_est_auto = []
    verificados_ok_recep = []
    pendientes_ok_recep = []

    # Asegurar que el encabezado AZ1 exista en Registro_Estancias
    try:
        val_az1 = ws_est.acell("AZ1").value
        if not val_az1 or str(val_az1).strip() == "":
            batch_est_auto.append({"range": "AZ1", "values": [["Excedente_Efectivo_SUNAT"]]})
    except Exception:
        pass

    for e in estancias:
        fila_ref = f"E{e['row']}"
        tenia_ok_recep = fila_ref in ok_recepcion

        monto_sunat = round(sum(l["monto_linea"] for l in e["lineas_sunat"] if l["estado"] == "VIGENTE"), 2)
        cps_list = list(dict.fromkeys(l["nro_comprobante"] for l in e["lineas_sunat"]))
        cps_str = ", ".join(cps_list)
        if not cps_str and e["cubierto_por_cp"]:
            cps_str = e["cubierto_por_cp"]

        pagos_e = pagos_por_estancia.get(e["id_estancia"], [])
        pagos_yape = [p for p in pagos_e if "YAPE" in p["medio"].upper() and p["monto"] > 0]

        yape_banco_conf = 0.0
        estados_b_set = []
        sin_verif = False

        tiene_fac_vigente = any(l.get("tipo") == "FACTURA" and l.get("estado") == "VIGENTE" for l in e["lineas_sunat"])
        for py in pagos_yape:
            ests = py["estados_banco"]
            es_liq_emp = any(k in py.get("concepto", "").upper() for k in ("EMPRESA", "QUISAPATA"))
            if any(st.upper().startswith("CUADRADO") for st in ests):
                yape_banco_conf = round(yape_banco_conf + py["monto"], 2)
                for st in ests:
                    if st not in estados_b_set:
                        estados_b_set.append(st)
            elif es_liq_emp and tiene_fac_vigente:
                yape_banco_conf = round(yape_banco_conf + py["monto"], 2)
                st_emp = "CUADRADO (LIQUIDACIÓN EMPRESA FACTURADA)"
                if st_emp not in estados_b_set:
                    estados_b_set.append(st_emp)
            else:
                sin_verif = True
                if ests:
                    for st in ests:
                        if st not in estados_b_set:
                            estados_b_set.append(st)

        yape_ab = e["yape_ab"]
        yape_ab_positivo = yape_ab if yape_ab > 0 else 0.0

        if yape_ab_positivo == 0 and not pagos_yape:
            estado_banco = "VUELTO_YAPE" if yape_ab < 0 else "SIN_YAPE"
        elif sin_verif or (yape_ab_positivo > 0 and yape_banco_conf == 0):
            estado_banco = "YAPE_NO_VERIFICADO_EN_BANCO"
        else:
            estado_banco = " | ".join(estados_b_set) if estados_b_set else "CUADRADO"

        # Identificar comprobantes positivos activos (no anulados ya por una Nota de Crédito)
        cps_anulados_por_nc = set()
        for l in e["lineas_sunat"]:
            if l.get("tipo") == "NOTA_CREDITO" and l.get("cp_modificado"):
                cps_anulados_por_nc.add(l["cp_modificado"])
            for flg in l.get("flags", []):
                if str(flg).startswith("ANULADO_POR_NC:"):
                    cps_anulados_por_nc.add(l["nro_comprobante"])

        facs_activas = collections.defaultdict(float)
        bols_activas = collections.defaultdict(float)
        tiene_flag_duplicado = False
        for l in e["lineas_sunat"]:
            if l["estado"] == "VIGENTE" and l["nro_comprobante"] not in cps_anulados_por_nc:
                if "DUPLICADO" in l.get("flags", []):
                    tiene_flag_duplicado = True
                if l["tipo"] == "FACTURA":
                    facs_activas[l["nro_comprobante"]] = round(facs_activas[l["nro_comprobante"]] + l["monto_linea"], 2)
                elif l["tipo"] == "BOLETA":
                    bols_activas[l["nro_comprobante"]] = round(bols_activas[l["nro_comprobante"]] + l["monto_linea"], 2)

        aj = e["monto_aj"]
        pago_real_est = e["pagado_ac"] if e["pagado_ac"] > 0 else max(yape_ab_positivo + e["efectivo_aa"], yape_banco_conf, e["total_v"])

        motivo_duplicado = ""
        monto_cp_valido = monto_sunat

        # REGLA 1: JERARQUÍA FACTURA > BOLETA EN DUPLICADOS DE LA MISMA ESTANCIA
        if facs_activas and bols_activas:
            # Prima la Factura sobre la Boleta -> Se conserva la Factura y se da de baja la Boleta con Nota de Crédito
            monto_cp_valido = round(sum(facs_activas.values()), 2)
            cps_fac_str = ", ".join(facs_activas.keys())
            cps_bol_str = ", ".join(bols_activas.keys())
            monto_bol_baja = round(sum(bols_activas.values()), 2)
            motivo_duplicado = (
                f"PRIMA FACTURA {cps_fac_str} (S/ {monto_cp_valido:.2f}): "
                f"Dar de baja BOLETA {cps_bol_str} con Nota de Crédito por S/ {monto_bol_baja:.2f}"
            )
        elif len(bols_activas) > 1 and (tiene_flag_duplicado or (aj > 0 and monto_sunat > aj + 0.05)):
            # Duplicado entre Boletas: conservar la que cuadre con AJ/pago_real o la primera emitida
            bol_items = list(bols_activas.items())
            bol_conservar = bol_items[0]
            for b_k, b_m in bol_items:
                if (aj > 0 and abs(b_m - aj) <= 0.05) or (pago_real_est > 0 and abs(b_m - pago_real_est) <= 0.05):
                    bol_conservar = (b_k, b_m)
                    break
            bols_baja = [(k, m) for k, m in bol_items if k != bol_conservar[0]]
            monto_cp_valido = bol_conservar[1]
            cps_baja_str = ", ".join(k for k, _ in bols_baja)
            monto_baja = round(sum(m for _, m in bols_baja), 2)
            motivo_duplicado = (
                f"DUPLICADO BOLETAS ({', '.join(bols_activas.keys())}): "
                f"Dar de baja BOLETA {cps_baja_str} con Nota de Crédito por S/ {monto_baja:.2f}"
            )
        elif len(facs_activas) > 1 and (tiene_flag_duplicado or (aj > 0 and monto_sunat > aj + 0.05)):
            # Duplicado entre Facturas
            fac_items = list(facs_activas.items())
            fac_conservar = fac_items[0]
            for f_k, f_m in fac_items:
                if (aj > 0 and abs(f_m - aj) <= 0.05) or (pago_real_est > 0 and abs(f_m - pago_real_est) <= 0.05):
                    fac_conservar = (f_k, f_m)
                    break
            facs_baja = [(k, m) for k, m in fac_items if k != fac_conservar[0]]
            monto_cp_valido = fac_conservar[1]
            cps_baja_str = ", ".join(k for k, _ in facs_baja)
            monto_baja = round(sum(m for _, m in facs_baja), 2)
            motivo_duplicado = (
                f"DUPLICADO FACTURAS ({', '.join(facs_activas.keys())}): "
                f"Dar de baja FACTURA {cps_baja_str} con Nota de Crédito por S/ {monto_baja:.2f}"
            )

        # REGLA 2: EXCEDENTE EN EFECTIVO ASUMIDO (Cuando piden Factura/Boleta por monto mayor al pago real/Yape)
        excedente_efectivo = 0.0
        if monto_cp_valido > 0 and pago_real_est > 0 and (monto_cp_valido > pago_real_est + 0.05):
            excedente_efectivo = round(monto_cp_valido - pago_real_est, 2)

        # Actualizar automáticamente AJ en Registro_Estancias si:
        # 1) AJ estaba vacío (0), o
        # 2) Prima una Factura o comprobante con Excedente Efectivo (ej. Factura S/ 60 o S/ 120 vs pago S/ 50 o S/ 78)
        if monto_cp_valido > 0 and (aj == 0 or (excedente_efectivo > 0 and abs(aj - monto_cp_valido) > 0.05)):
            aj = monto_cp_valido
            e["monto_aj"] = monto_cp_valido
            batch_est_auto.append({"range": f"AJ{e['row']}", "values": [[monto_cp_valido]]})

        # Actualizar automáticamente AZ (Excedente_Efectivo_SUNAT) en Registro_Estancias si cambió
        if abs(excedente_efectivo - e["excedente_az"]) > 0.01:
            e["excedente_az"] = excedente_efectivo
            batch_est_auto.append({"range": f"AZ{e['row']}", "values": [[excedente_efectivo if excedente_efectivo > 0 else ""]]})

        if e["cubierto_por_cp"] and monto_sunat == 0 and aj == 0:
            chk_sunat_aj = "OK"
        elif monto_sunat > 0 and aj == 0:
            chk_sunat_aj = "AJ_VACIO"
        elif monto_sunat > 0 and abs(monto_sunat - aj) <= 0.05 and not motivo_duplicado:
            chk_sunat_aj = "OK"
        elif monto_sunat > 0 and (abs(monto_sunat - aj) > 0.05 or motivo_duplicado):
            chk_sunat_aj = "DIFIERE"
        elif monto_sunat == 0 and aj > 0:
            chk_sunat_aj = "SIN_SUNAT"
        else:
            chk_sunat_aj = "SIN_COMPROBANTE"

        if yape_ab_positivo == 0 and yape_banco_conf == 0:
            chk_yape_banco = "N/A"
        elif yape_banco_conf == 0 and yape_ab_positivo > 0:
            chk_yape_banco = "SIN_BANCO"
        elif abs(yape_ab_positivo - yape_banco_conf) <= 0.01 or (
            yape_banco_conf > yape_ab_positivo and abs(monto_sunat - yape_ab_positivo) <= 0.05 and abs(aj - yape_ab_positivo) <= 0.05
        ):
            chk_yape_banco = "OK"
        else:
            chk_yape_banco = "DIFIERE"

        if yape_banco_conf == 0:
            chk_yape_sunat = "N/A"
        elif monto_sunat == 0 and not e["cubierto_por_cp"]:
            chk_yape_sunat = "YAPE_SIN_COMPROBANTE"
        elif (
            e["cubierto_por_cp"]
            or (monto_sunat + 0.05 >= yape_banco_conf)
            or (yape_banco_conf > yape_ab_positivo and abs(monto_sunat - yape_ab_positivo) <= 0.05)
        ):
            chk_yape_sunat = "OK"
        else:
            chk_yape_sunat = "DIFIERE"

        if chk_yape_sunat == "YAPE_SIN_COMPROBANTE":
            estado_final = "YAPE_SIN_COMPROBANTE"
            accion = f"FALTA BOLETA/FACTURA: Emitir comprobante por Yape S/ {yape_banco_conf:.2f} y anotar en AJ{e['row']}"
            if tenia_ok_recep:
                accion = f"[Marcó OK pero AÚN NO figura en SUNAT] {accion}"
                pendientes_ok_recep.append((fila_ref, e["noche_checkin"], e["habitacion"], e["huesped"], yape_banco_conf))
        elif e["ambiguo"]:
            estado_final = "AMBIGUO"
            accion = f"Revisar reingreso el mismo día en Hab {e['habitacion']} (fila {e['row']})"
        elif chk_sunat_aj == "AJ_VACIO":
            estado_final = "AJ_VACIO"
            accion = f"Anotar S/ {monto_sunat:.2f} ({cps_str}) en celda AJ{e['row']}"
        elif (
            chk_sunat_aj in ("DIFIERE", "SIN_SUNAT")
            or chk_yape_banco in ("DIFIERE", "SIN_BANCO")
            or chk_yape_sunat == "DIFIERE"
        ):
            estado_final = "DIFIERE"
            if chk_sunat_aj == "SIN_SUNAT":
                accion = f"AJ{e['row']} tiene S/ {aj:.2f} pero no se halló comprobante en SUNAT"
            elif chk_sunat_aj == "DIFIERE":
                if motivo_duplicado:
                    extra_exc = f" | Excedente Efectivo Asumido (AZ{e['row']}): S/ {excedente_efectivo:.2f}" if excedente_efectivo > 0 else ""
                    accion = f"{motivo_duplicado}{extra_exc}"
                elif monto_sunat > aj > 0 and len(cps_list) > 1:
                    accion = (
                        f"DUPLICADO EN SUNAT ({cps_str}): Emitir Nota de Crédito manual por "
                        f"S/ {monto_sunat - aj:.2f} (SUNAT S/ {monto_sunat:.2f} vs AJ{e['row']} S/ {aj:.2f})"
                    )
                else:
                    accion = f"Revisar diferencia: SUNAT S/ {monto_sunat:.2f} vs AJ{e['row']} S/ {aj:.2f}"
            elif chk_yape_banco == "SIN_BANCO":
                accion = f"Yape anotado en Estancia (S/ {yape_ab_positivo:.2f}) sin pago Yape en Banco"
            else:
                accion = f"Revisar diferencia Yape: Estancia S/ {yape_ab_positivo:.2f} vs SUNAT S/ {monto_sunat:.2f}"
            if tenia_ok_recep and monto_sunat > 0:
                verificados_ok_recep.append((fila_ref, e["noche_checkin"], e["habitacion"], e["huesped"], cps_str, monto_sunat))
        elif monto_sunat == 0 and aj == 0 and yape_banco_conf == 0 and not e["cubierto_por_cp"]:
            continue
        else:
            estado_final = "OK_TRIPLE"
            if excedente_efectivo > 0:
                accion = f"Cuadre correcto (Incluye S/ {excedente_efectivo:.2f} de Excedente Efectivo por depositar al banco)"
            elif e["cubierto_por_cp"]:
                accion = f"Cuadre correcto ({e['cubierto_por_cp']})"
            else:
                accion = "Cuadre correcto"
            if tenia_ok_recep:
                verificados_ok_recep.append((fila_ref, e["noche_checkin"], e["habitacion"], e["huesped"], cps_str, monto_sunat))

        dif_sunat_aj = round(monto_sunat - aj, 2)

        filas_calzado.append(
            {
                "noche": e["noche_checkin"],
                "habitacion": e["habitacion"],
                "huesped": e["huesped"],
                "dni": e["dni"],
                "fila_est": fila_ref,
                "monto_sunat": monto_sunat,
                "comprobantes": cps_str,
                "monto_aj": aj,
                "total_v": e["total_v"],
                "pagado_ac": e["pagado_ac"],
                "yape_ab": yape_ab,
                "efectivo_aa": e["efectivo_aa"],
                "excedente_efectivo": excedente_efectivo,
                "yape_banco": yape_banco_conf,
                "estado_banco": estado_banco,
                "chk_sunat_aj": chk_sunat_aj,
                "chk_yape_banco": chk_yape_banco,
                "chk_yape_sunat": chk_yape_sunat,
                "estado_final": estado_final,
                "diferencia": dif_sunat_aj,
                "accion": accion,
            }
        )

    if batch_est_auto:
        ws_est.batch_update(batch_est_auto, value_input_option="USER_ENTERED")
        print(f"   [AUTO-ESTANCIAS] Se actualizaron automáticamente {len(batch_est_auto)} celdas (AJ / AZ Excedente Efectivo) en Registro_Estancias.")

    if ok_recepcion:
        print(f"\n================== VERIFICACIÓN DE MARCAS 'OK' DE RECEPCIÓN ({len(ok_recepcion)} marcadas en Col A) ==================")
        for fr, nch, hb, hsp, cp_s, m_s in verificados_ok_recep:
            print(f"   [VERIFICADO EN SUNAT -> BLANCO + LIMPIADO] {fr} | {nch} | Hab {hb:<3} | {hsp[:28]:<28} | {cp_s} (S/ {m_s:.2f})")
        for fr, nch, hb, hsp, m_y in pendientes_ok_recep:
            print(f"   [NO HALLADO EN SUNAT -> SIGUE ROJO]        {fr} | {nch} | Hab {hb:<3} | {hsp[:28]:<28} | Falta emitir S/ {m_y:.2f}")

    for l in lineas_grupales_revisar:
        filas_calzado.append(
            {
                "noche": l["noche"],
                "habitacion": l["habitacion"],
                "huesped": l["cliente"],
                "dni": str(l.get("doc", "")).strip(),
                "fila_est": "SIN_FILA",
                "monto_sunat": l["monto_linea"],
                "comprobantes": l["nro_comprobante"],
                "monto_aj": 0.0,
                "total_v": 0.0,
                "pagado_ac": 0.0,
                "yape_ab": 0.0,
                "efectivo_aa": 0.0,
                "excedente_efectivo": 0.0,
                "yape_banco": 0.0,
                "estado_banco": "N/A",
                "chk_sunat_aj": "SIN_ESTANCIA",
                "chk_yape_banco": "N/A",
                "chk_yape_sunat": "N/A",
                "estado_final": "GRUPAL_REVISAR",
                "diferencia": l["monto_linea"],
                "accion": f"Comprobante grupal sin habitación ({l['texto_linea'][:50]})",
            }
        )

    for l in lineas_sin_asignar:
        es_sep = "/09/" in str(l["noche"])
        filas_calzado.append(
            {
                "noche": l["noche"],
                "habitacion": l["habitacion"],
                "huesped": l["cliente"],
                "dni": str(l.get("doc", "")).strip(),
                "fila_est": "MES_ANTERIOR_SEP" if es_sep else "SIN_FILA",
                "monto_sunat": l["monto_linea"],
                "comprobantes": l["nro_comprobante"],
                "monto_aj": 0.0,
                "total_v": 0.0,
                "pagado_ac": 0.0,
                "yape_ab": 0.0,
                "efectivo_aa": 0.0,
                "excedente_efectivo": 0.0,
                "yape_banco": 0.0,
                "estado_banco": f"SUNAT_{l['estado']}",
                "chk_sunat_aj": "NOCHE_SEP" if es_sep else "SIN_ESTANCIA",
                "chk_yape_banco": "N/A",
                "chk_yape_sunat": "N/A",
                "estado_final": "SUNAT_SIN_ESTANCIA",
                "diferencia": l["monto_linea"],
                "accion": (
                    f"Noche de Septiembre facturada en Octubre ({l['nro_comprobante']})"
                    if es_sep
                    else (
                        f"Comprobante {l['estado']} sin estancia en Oct ({l['texto_linea'][:45]})"
                        if l["estado"] == "VIGENTE"
                        else f"Comprobante ANULADO ({l['nro_comprobante']})"
                    )
                ),
            }
        )

    print("\n================== PUERTA 3 OCTUBRE: VERIFICACIÓN DE CUADRE ==================")
    total_sunat_mes = round(sum(l["monto_linea"] for l in lineas_sunat if l["estado"] == "VIGENTE"), 2)
    total_sunat_calzado = round(sum(f["monto_sunat"] for f in filas_calzado), 2)
    total_aj_calzado = round(sum(f["monto_aj"] for f in filas_calzado), 2)
    total_excedente_efectivo = round(sum(f["excedente_efectivo"] for f in filas_calzado), 2)
    total_yape_ab = round(sum(f["yape_ab"] for f in filas_calzado), 2)
    total_yape_banco_est = round(sum(f["yape_banco"] for f in filas_calzado), 2)

    print(f"1) Estancias de octubre evaluadas: {len(estancias)} | Mostradas en CALZADO_OCT: {len(filas_calzado)}")
    print(
        f"2) Filas extra SUNAT (GRUPAL_REVISAR={len(lineas_grupales_revisar)}, "
        f"SUNAT_SIN_ESTANCIA={len(lineas_sin_asignar)})"
    )
    print(
        f"3) Suma Monto_SUNAT en CALZADO_OCT = S/ {total_sunat_calzado:,.2f} vs "
        f"Total SUNAT_OCT = S/ {total_sunat_mes:,.2f} (Dif = S/ {total_sunat_calzado - total_sunat_mes:,.2f})"
    )
    print(f"4) Total Excedente Efectivo SUNAT (Por depositar al banco) = S/ {total_excedente_efectivo:,.2f}")

    orden_estados = [
        "YAPE_SIN_COMPROBANTE",
        "DIFIERE",
        "SUNAT_SIN_ESTANCIA",
        "GRUPAL_REVISAR",
        "AMBIGUO",
        "AJ_VACIO",
        "OK_TRIPLE",
    ]

    resumen_estados = []
    print("\nResumen por ESTADO_FINAL:")
    for st in orden_estados:
        sub = [f for f in filas_calzado if f["estado_final"] == st]
        cnt = len(sub)
        if st == "YAPE_SIN_COMPROBANTE":
            monto_st = round(sum(max(f["yape_banco"], f["yape_ab"]) for f in sub), 2)
        elif st in ("AJ_VACIO", "OK_TRIPLE", "SUNAT_SIN_ESTANCIA", "GRUPAL_REVISAR", "AMBIGUO"):
            monto_st = round(sum(f["monto_sunat"] for f in sub), 2)
        elif st == "DIFIERE":
            monto_st = round(sum(abs(f["diferencia"]) for f in sub), 2)
        else:
            monto_st = round(sum(f["pagado_ac"] for f in sub), 2)
        resumen_estados.append((st, cnt, monto_st))
        print(f"   - {st:<26}: {cnt:>4} casos | Monto ref: S/ {monto_st:>10,.2f}")

    for st_dbg in ("AJ_VACIO", "DIFIERE", "SUNAT_SIN_ESTANCIA", "GRUPAL_REVISAR"):
        sub_dbg = [f for f in filas_calzado if f["estado_final"] == st_dbg]
        if sub_dbg:
            print(f"\nDetalle de {st_dbg} ({len(sub_dbg)}):")
            for x in sub_dbg:
                print(
                    f"   * {x['noche']} | Hab {x['habitacion']:<6} | {x['huesped'][:28]:<28} | DNI: {x['dni']:<11} | "
                    f"Fila {x['fila_est']:<8} | SUNAT: {x['monto_sunat']:>6.2f} ({x['comprobantes']}) | AJ: {x['monto_aj']:>6.2f} | "
                    f"ExcEfec: {x['excedente_efectivo']:>5.2f} | {x['accion']}"
                )

    def sincronizar_sqlite_local(filas_calzado):
        """
        Sincroniza directamente calzado_cache en la SQLite activa
        (Prueba :8082 si es_prueba(), u Operativa :8080 si no),
        reemplazando únicamente las filas de Octubre (%/10/2026 y %/09/2026) para que
        Octubre y Noviembre coexistan completos en calzado_cache.
        """
        try:
            from hoja_activa import (
                conectar_sqlite_activa,
                limpiar_outbox_caches_locales,
                obtener_sqlite_path,
            )

            conn = conectar_sqlite_activa(timeout=30.0)
            if conn is None:
                print(f"[WARN SQLite] No se encontró BD activa en {obtener_sqlite_path()}", flush=True)
                return

            patron_mes = f"%/{MES_STR}/{ANIO_STR}"
            try:
                claves_vistas = set()
                registros_a_insertar = []
                for idx_cal, fc in enumerate(filas_calzado):
                    noche_c = str(fc.get("noche") or "").strip()
                    hab_c = str(fc.get("habitacion") or "").strip().upper()
                    fila_est = str(fc.get("fila_est") or "").strip().upper()
                    es_fila_real = fila_est.startswith("E") and fila_est[1:].isdigit()
                    clave_cal = fila_est if (es_fila_real and fila_est not in claves_vistas) else f"OCT|{noche_c}|{hab_c}|{fila_est}|{idx_cal}"
                    claves_vistas.add(clave_cal)
                    registros_a_insertar.append(
                        (
                            clave_cal,
                            idx_cal + 2,
                            noche_c,
                            hab_c,
                            str(fc.get("huesped") or "").strip(),
                            str(fc.get("dni") or "").strip(),
                            fila_est,
                            round(float(fc.get("monto_sunat") or 0.0), 2),
                            str(fc.get("comprobantes") or "").strip(),
                            round(float(fc.get("monto_aj") or 0.0), 2),
                            round(float(fc.get("yape_ab") or 0.0), 2),
                            round(float(fc.get("efectivo_aa") or 0.0), 2),
                            round(float(fc.get("excedente_efectivo") or 0.0), 2),
                            round(float(fc.get("yape_banco") or 0.0), 2),
                            str(fc.get("estado_banco") or "").strip(),
                            str(fc.get("estado_final") or "").strip(),
                            str(fc.get("accion") or "").strip(),
                        )
                    )

                conn.execute(
                    """
                    DELETE FROM calzado_cache
                    WHERE (noche_dmy LIKE ? AND COALESCE(fila_est, '') != 'MES_ANTERIOR_OCT')
                       OR fila_est = 'MES_ANTERIOR_SEP'
                       OR clave_calzado LIKE 'OCT|%'
                       OR clave_calzado IN ('SIN_FILA', 'MES_ANTERIOR_SEP')
                    """,
                    (patron_mes,),
                )
                claves_list = [r[0] for r in registros_a_insertar]
                for i_chunk in range(0, len(claves_list), 150):
                    chunk = claves_list[i_chunk : i_chunk + 150]
                    ph = ",".join("?" for _ in chunk)
                    conn.execute(f"DELETE FROM calzado_cache WHERE clave_calzado IN ({ph})", chunk)

                conn.executemany(
                    """
                    INSERT OR REPLACE INTO calzado_cache (
                        clave_calzado, fila_calzado, noche_dmy, hab, huesped, dni,
                        fila_est, monto_sunat, comprobantes, monto_aj, yape_ab,
                        efectivo_aa, excedente_efec, yape_bcp, estado_bcp, estado_conciliacion, accion
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    registros_a_insertar,
                )

                limpiar_outbox_caches_locales(conn)
                conn.commit()
                tot_cal = conn.execute("SELECT COUNT(*) FROM calzado_cache").fetchone()[0]
                print(
                    f"[SQLITE LOCAL] '{obtener_sqlite_path()}' sincronizada: "
                    f"{len(registros_a_insertar)} filas Octubre cargadas en calzado_cache (total acumulado={tot_cal}).",
                    flush=True,
                )
            finally:
                conn.close()
        except Exception as e_sql:
            print(f"[WARN SQLite] Error sincronizando SQLite en Calzar_Octubre: {e_sql}", flush=True)

    if "--solo-sqlite" in sys.argv:
        print(f"\n[MODO --solo-sqlite] Omitiendo escritura en Google Sheets ('{HOJA_CALZADO}').", flush=True)
        sincronizar_sqlite_local(filas_calzado)
        return

    print(f"\n[4/4] Escribiendo {len(filas_calzado)} filas en hoja '{HOJA_CALZADO}' (con Columna A limpia)...", flush=True)
    try:
        ws_cal = sh.worksheet(HOJA_CALZADO)
        if ws_cal.col_count < 27:
            ws_cal.add_cols(27 - ws_cal.col_count)
        ws_cal.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws_cal = sh.add_worksheet(title=HOJA_CALZADO, rows=max(len(filas_calzado) + 50, 350), cols=27)

    encabezados = [
        "OK_Recepcion",                  # A (Columna donde recepción pone OK y luego se limpia)
        "Noche_CheckIn",                 # B (Fecha ingreso)
        "Habitación",                    # C (Número hab)
        "Huésped",                       # D (Nombre cliente)
        "DNI",                           # E (Documento)
        "Fila_Estancias",                # F (Referencia fila ej. E5226)
        "Monto_SUNAT",                   # G (Total emitido en SUNAT)
        "Comprobantes_SUNAT",            # H (Número de Boleta/Factura)
        "Comprobante_Anotado_AJ",        # I (Monto de comprobante anotado por recepción en col AJ)
        "Total_Cuenta_Hospedaje",         # J (Cuenta total estancia - Col V)
        "Total_Cobrado_General",         # K (Efectivo + Yape cobrado - Col AC)
        "⭐ YAPE_A_BANCARIZAR (Col AB)", # L (MONTO QUE SE DEBE EMITIR Y BANCARIZAR)
        "Efectivo_Caja_Chica (Col AA)",  # M (Efectivo que queda en caja física, no va a banco)
        "Excedente_Efectivo_a_Banco",    # N (Efectivo que debe depositarse si boleta > Yape)
        "Yape_Confirmado_BCP",           # O (Yape verificado en cuenta bancaria BCP)
        "Estado_BCP",                    # P (CUADRADO BCP / SIN_PAGO)
        "Control_SUNAT_vs_Anotado",      # Q (Revisión SUNAT vs lo anotado en AJ)
        "Control_Yape_vs_BCP",           # R (Revisión Yape recepción vs banco)
        "Control_Yape_vs_SUNAT",         # S (Revisión Yape vs boleta emitida)
        "ESTADO_CONCILIACION",           # T (OK_TRIPLE, YAPE_SIN_COMPROBANTE, etc.)
        "Diferencia_Comprobante",        # U (Diferencia numérica SUNAT - AJ)
        "Acción_Para_Recepcion",         # V (Instrucción clara de qué hacer)
        "",                              # W (separador)
        "RESUMEN_ESTADO",                # X
        "CANTIDAD",                      # Y
        "MONTO_REF_S/",                  # Z
    ]

    con_aj_final = [e for e in estancias if e["monto_aj"] > 0]
    con_exc_final = [f for f in filas_calzado if f["excedente_efectivo"] > 0]
    lineas_resumen = []
    for st, cnt, m_st in resumen_estados:
        lineas_resumen.append([st, cnt, m_st])
    lineas_resumen.append(["", "", ""])
    lineas_resumen.append(["=== TOTALES GLOBALES ===", "", ""])
    lineas_resumen.append(["Total Emitido SUNAT (SUNAT_OCT)", len(lineas_sunat), total_sunat_mes])
    lineas_resumen.append(["Total Anotado en AJ (Estancias)", len(con_aj_final), total_aj_calzado])
    lineas_resumen.append(["Diferencia Global (SUNAT - AJ)", "", round(total_sunat_mes - total_aj_calzado, 2)])
    lineas_resumen.append(["Total Excedente Efectivo SUNAT (Por Depositar)", len(con_exc_final), total_excedente_efectivo])
    lineas_resumen.append(["Ventas SUNAT Netas de Excedente (SUNAT - Exc)", "", round(total_sunat_mes - total_excedente_efectivo, 2)])
    lineas_resumen.append(["Total Yape en Estancias (AB)", "", total_yape_ab])
    lineas_resumen.append(["Total Yape Confirmado Banco (Hab)", "", total_yape_banco_est])
    lineas_resumen.append(["Total Banco Oct (CONCILIACION)", filas_banco_mes, suma_banco_mes])
    lineas_resumen.append(["", "", ""])
    lineas_resumen.append(["=== BANCO_SIN_REGISTRO (SOBRANTE) ===", len(banco_sin_registro), round(sum(b["monto"] for b in banco_sin_registro), 2)])
    for b in banco_sin_registro:
        lineas_resumen.append([f"{b['fecha_hora']} - {b['origen'][:24]}", f"C{b['row_conc']}", b["monto"]])
    lineas_resumen.append(["", "", ""])
    lineas_resumen.append(["NOTA CALIBRACIÓN", regla_calibracion, ""])

    filas_sheet = [encabezados]
    max_rows = max(len(filas_calzado), len(lineas_resumen))

    for i in range(max_rows):
        if i < len(filas_calzado):
            fc = filas_calzado[i]
            row_izq = [
                "",  # Columna A siempre se limpia después de verificar contra SUNAT
                fc["noche"],
                fc["habitacion"],
                fc["huesped"],
                fc["dni"],
                fc["fila_est"],
                fc["monto_sunat"],
                fc["comprobantes"],
                fc["monto_aj"],
                fc["total_v"],
                fc["pagado_ac"],
                fc["yape_ab"],
                fc["efectivo_aa"],
                fc["excedente_efectivo"],
                fc["yape_banco"],
                fc["estado_banco"],
                fc["chk_sunat_aj"],
                fc["chk_yape_banco"],
                fc["chk_yape_sunat"],
                fc["estado_final"],
                fc["diferencia"],
                fc["accion"],
            ]
        else:
            row_izq = [""] * 22

        if i < len(lineas_resumen):
            row_der = [""] + lineas_resumen[i]
        else:
            row_der = ["", "", "", ""]

        filas_sheet.append(row_izq + row_der)

    ws_cal.update(values=filas_sheet, range_name="A1", value_input_option="USER_ENTERED")

    mapa_colores = {
        "YAPE_SIN_COMPROBANTE": {"red": 0.98, "green": 0.76, "blue": 0.76},
        "DIFIERE": {"red": 1.00, "green": 0.78, "blue": 0.50},
        "SUNAT_SIN_ESTANCIA": {"red": 1.00, "green": 0.95, "blue": 0.60},
        "GRUPAL_REVISAR": {"red": 1.00, "green": 0.95, "blue": 0.60},
        "AMBIGUO": {"red": 1.00, "green": 0.95, "blue": 0.60},
        "AJ_VACIO": {"red": 1.00, "green": 0.95, "blue": 0.60},
    }

    formatos_batch = [
        {
            "range": "A2:Z350",
            "format": {
                "backgroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
            },
        },
        {
            "range": "A1:V1",
            "format": {
                "backgroundColor": {"red": 0.16, "green": 0.24, "blue": 0.38},
                "textFormat": {"bold": True, "foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0}},
                "horizontalAlignment": "CENTER",
            },
        },
        {
            "range": "X1:Z1",
            "format": {
                "backgroundColor": {"red": 0.22, "green": 0.34, "blue": 0.18},
                "textFormat": {"bold": True, "foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0}},
                "horizontalAlignment": "CENTER",
            },
        },
        {
            "range": f"G2:G{len(filas_sheet)}",
            "format": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0.00"}},
        },
        {
            "range": f"I2:O{len(filas_sheet)}",
            "format": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0.00"}},
        },
        {
            "range": f"U2:U{len(filas_sheet)}",
            "format": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0.00"}},
        },
        {
            "range": "Z2:Z35",
            "format": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0.00"}},
        },
    ]

    for idx_f, fc in enumerate(filas_calzado, start=2):
        col_rgb = mapa_colores.get(fc["estado_final"])
        if col_rgb:
            formatos_batch.append(
                {
                    "range": f"A{idx_f}:V{idx_f}",
                    "format": {"backgroundColor": col_rgb},
                }
            )

    try:
        ws_cal.freeze(rows=1)
        ws_cal.set_basic_filter(f"A1:V{len(filas_calzado) + 1}")
        ws_cal.batch_format(formatos_batch)
    except Exception as e_fmt:
        print(f"   [WARN] Formato opcional en CALZADO_OCT: {e_fmt}")

    print(f"[OK] Hoja '{HOJA_CALZADO}' creada y formateada exitosamente.", flush=True)
    sincronizar_sqlite_local(filas_calzado)


if __name__ == "__main__":
    main()
