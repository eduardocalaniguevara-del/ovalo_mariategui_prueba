import sys
import os
import re
import json
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
MES = 11
MES_STR = str(MES).zfill(2)
ANIO_STR = str(ANIO)

from pathlib import Path
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

RUTA_RAW_BOLETAS = r"C:\Hostal_Sistema\datos_sunat\sunat_boletas_noviembre_raw.json"
RUTA_RAW_FACTURAS = r"C:\Hostal_Sistema\datos_sunat\sunat_facturas_noviembre_raw.json"
RUTA_OUT_LINEAS = r"C:\Hostal_Sistema\datos_sunat\sunat_nov_lineas.json"
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
NOMBRE_HOJA_SUNAT = "SUNAT_NOV"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def normalizar_cp_corto(num_cp):
    if not num_cp:
        return ""
    m = re.search(r"([EFB][A-Z0-9]{3})\s*-\s*(\d+)", str(num_cp).strip(), re.I)
    if m:
        return f"{m.group(1).upper()}-{int(m.group(2))}"
    return str(num_cp).strip().replace(" ", "")


def extraer_noches_linea(desc_linea, fecha_emision, cantidad=1.0):
    """
    Extrae la lista de noches [(dd, mm), ...] aplicando reglas estrictamente
    al texto de la línea (nunca al encabezado de Fecha de Emisión).
    """
    dia_emision = int(fecha_emision.split("/")[0]) if "/" in fecha_emision else 1
    mes_default = fecha_emision.split("/")[1] if "/" in fecha_emision else MES_STR
    texto_upper = desc_linea.upper()

    mes = mes_default
    if "JULIO" in texto_upper:
        mes = "07"
    elif "AGOSTO" in texto_upper:
        mes = "08"
    elif "SEPTIEMBRE" in texto_upper or "SETIEMBRE" in texto_upper:
        mes = "09"
    elif "OCTUBRE" in texto_upper:
        mes = "10"
    elif "NOVIEMBRE" in texto_upper:
        mes = "11"

    # Limpiar ruido que contiene números que NO son fechas (habitación, DNI, RUC, camas, tarifas, "POR X DIAS")
    desc_clean = re.sub(r"HA[BH](?:ITACI[OÓ]N)?\.?\s*(?:N[°\.\s]*)?\s*[A-Z]?\d{3}\.?", " ", texto_upper)
    desc_clean = re.sub(r"\b(?:DNI|RUC)\s*:?\s*\d+\b", " ", desc_clean)
    desc_clean = re.sub(r"\b\d+\s*CAMAS?\b", " ", desc_clean)
    desc_clean = re.sub(r"\bX\s*\d+(?:\.\d+)?\s*SOLES\b", " ", desc_clean)
    desc_clean = re.sub(r"\bPOR\s+\d+\s+(D[IÍ]AS?)\b", r" \1 ", desc_clean)

    # 1. Rango explícito con fechas dd/mm AL dd/mm
    m_rango_fec = re.search(
        r"\b(\d{1,2})/((?:0?[1-9]|1[0-2]))(?:/202\d)?\s*(?:AL|A)\s*(\d{1,2})/((?:0?[1-9]|1[0-2]))(?:/202\d)?\b",
        desc_clean,
    )
    if m_rango_fec:
        d1, m1, d2, m2 = (
            int(m_rango_fec.group(1)),
            str(int(m_rango_fec.group(2))).zfill(2),
            int(m_rango_fec.group(3)),
            str(int(m_rango_fec.group(4))).zfill(2),
        )
        if m1 == m2 and d2 > d1:
            return [(str(x).zfill(2), m1) for x in range(d1, d2)]

    # 2. Fechas explícitas dd/mm (ej. "DEL 03/10" o "30/09")
    m_fechas = re.findall(r"\b(\d{1,2})/((?:0?[1-9]|1[0-2]))(?:/202\d)?\b", desc_clean)
    if m_fechas:
        dias_mes = [
            (str(int(d)).zfill(2), str(int(m)).zfill(2))
            for d, m in m_fechas
            if 1 <= int(d) <= 31
        ]
        if dias_mes:
            return list(dict.fromkeys(dias_mes))

    desc_clean = re.sub(r"\b202[0-9]\b", " ", desc_clean)

    # 3. Rango "DEL d1 AL d2" o "DEL d1 HASTA EL d2" (la fecha de salida no se cobra)
    m_al = re.search(
        r"(?:DEL|DESDE|ALOJAMIENTO|D[IÍ]AS?)\s*(\d{1,2})\s+(?:AL|HASTA(?:\s+EL)?)\s+(\d{1,2})\b",
        desc_clean,
    )
    if m_al:
        d1, d2 = int(m_al.group(1)), int(m_al.group(2))
        if 1 <= d1 <= 31 and 1 <= d2 <= 31:
            if d2 == d1 + 1 or d2 <= d1:
                m_d1 = "10" if (d1 >= 25 and mes == "11" and dia_emision <= 10 and "NOVIEMBRE" not in texto_upper) else mes
                return [(str(d1).zfill(2), m_d1)]
            else:
                return [(str(x).zfill(2), mes) for x in range(d1, d2)]

    # 4. Secuencia de días separados por guiones, comas, "Y" o "DEL" (ej. "DIAS 7-8-9-10", "30,01,02", "DEL 29 DEL 30")
    m_seq = re.search(
        r"(?:D[IÍ]AS?|DEL|LOS\s+D[IÍ]AS|ALOJAMIENTO)\s*(\d{1,2}(?:\s*(?:[-,]|Y|DEL)\s*\d{1,2})+)(?!\d)",
        desc_clean,
    )
    if m_seq:
        nums = [int(x) for x in re.findall(r"\d{1,2}", m_seq.group(1)) if 1 <= int(x) <= 31]
        if nums:
            res_seq = []
            for x in nums:
                m_x = "10" if (x >= 25 and any(y <= 5 for y in nums)) else mes
                res_seq.append((str(x).zfill(2), m_x))
            return list(dict.fromkeys(res_seq))

    # 5. Día único (ej. "DEL 03 DE OCTUBRE", "DEL23 DE SETIEMBRE", "DIA DE ALOJAMIENTO 02 DE OCTUBRE")
    dias_exp = re.findall(
        r"(?:D[IÍ]AS?|DEL|DE|EL|CORRESPONDIENTE|ALOJAMIENTO)\s*(\d{1,2})(?!\d)",
        desc_clean,
    )
    dias = [str(int(x)).zfill(2) for x in dias_exp if 1 <= int(x) <= 31]
    if dias:
        res_dias = []
        for d in dias:
            m_d = "10" if (int(d) >= 25 and mes == "11" and dia_emision <= 10 and "NOVIEMBRE" not in texto_upper) else mes
            res_dias.append((d, m_d))
        return list(dict.fromkeys(res_dias))

    return []


def extraer_habitacion_linea(desc_linea):
    m = re.search(
        r"HA[BH](?:ITACI[OÓ]N)?[\.,]?\s*(?:N[°\.,\s]*)?\s*([A-Z]?\d{3})\b",
        desc_linea,
        re.I,
    )
    if m:
        return m.group(1).upper(), False
    return "GRUPAL", True


def parsear_comprobante(item, tipo_default):
    textos = []
    for it in item.get("items", []):
        if isinstance(it, dict) and "texto_completo" in it:
            t = it["texto_completo"].strip()
            if t and t not in textos:
                textos.append(t)
    texto_popup = item.get("texto_popup", "").strip() or (textos[0] if textos else "")

    # 1. Nro_Comprobante
    num_cp = normalizar_cp_corto(item.get("numero_cp", ""))
    if not num_cp:
        m_cp_pop = re.search(r"\b([EFB][A-Z0-9]{3}\s*-\s*\d+)\b", texto_popup, re.I)
        if m_cp_pop:
            num_cp = normalizar_cp_corto(m_cp_pop.group(1))

    # 2. Tipo
    tipo = tipo_default.upper()
    if (
        tipo_default.upper() == "NOTA_CREDITO"
        or num_cp.startswith(("FC", "BC", "NC"))
        or "NOTA DE CREDITO" in texto_popup.upper()
        or "NOTA DE CRÉDITO" in texto_popup.upper()
    ):
        tipo = "NOTA_CREDITO"
    elif num_cp.startswith("E001") or num_cp.startswith("F"):
        tipo = "FACTURA"
    elif num_cp.startswith("EB01") or num_cp.startswith("B"):
        tipo = "BOLETA"

    # 3. Estado (Solo es ANULADO directo con monto 0 si fue dado de baja sin línea de NC separada)
    if re.search(r"\b(?:COMPROBANTE\s+ANULADO|DE\s+BAJA)\b", texto_popup, re.I):
        estado = "ANULADO"
    else:
        estado = "VIGENTE"

    # 4. Cliente
    cliente = ""
    m_rec = re.search(r"Señor\(es\)\s*:\s*([^\n\r\t]+)", texto_popup, re.I)
    if m_rec:
        cliente = m_rec.group(1).strip()
    if not cliente or cliente.isdigit():
        for c_cand in [item.get("receptor_nombre", ""), item.get("cliente_nombre", "")]:
            c_cand = str(c_cand).strip()
            if c_cand and not c_cand.isdigit():
                cliente = c_cand
                break

    # 5. Doc (DNI / RUC)
    doc = ""
    m_dni = re.search(r"\bDNI\s*:\s*(\d{8})\b", texto_popup, re.I)
    if m_dni:
        doc = m_dni.group(1)
    else:
        m_rucs = re.findall(r"\bRUC\s*:\s*(\d{11})\b", texto_popup, re.I)
        rucs_cli = [r for r in m_rucs if r != "20611986484"]
        if rucs_cli:
            doc = rucs_cli[0]
    if not doc:
        doc_cand = str(item.get("receptor_doc", "") or item.get("cliente_doc", "")).strip()
        if doc_cand and doc_cand != "20611986484" and doc_cand.isdigit() and len(doc_cand) in (8, 11):
            doc = doc_cand
    if not doc:
        m_dni_desc = re.search(r"\bDNI\s*:?\s*(\d{8})\b", texto_popup, re.I)
        if m_dni_desc:
            doc = m_dni_desc.group(1)

    # 6. Fecha de Emisión
    fecha_emision = str(item.get("fecha_emision", "")).strip()
    m_fec = re.search(r"Fecha de Emisi[oó]n\s*:\s*(\d{2}/\d{2}/\d{4})", texto_popup, re.I)
    if m_fec:
        fecha_emision = m_fec.group(1)

    # 7. Importe Total del comprobante
    total_cp = 0.0
    m_tot = re.search(r"Importe Total\s*[:\s]*S/[^\d]*([\d\.,]+)", texto_popup, re.I)
    if m_tot:
        try:
            total_cp = float(m_tot.group(1).replace(",", ""))
        except:
            total_cp = 0.0
    if total_cp == 0.0:
        try:
            total_cp = float(item.get("monto_total", 0.0) or 0.0)
        except:
            total_cp = 0.0

    signo = -1.0 if tipo == "NOTA_CREDITO" else 1.0
    if estado == "ANULADO":
        total_cp = 0.0

    # 8. Extraer líneas de ítems del popup
    bloque_items = texto_popup
    m_bloque = re.search(
        r"Valor Unitario(?:\(\*\))?(?:[^\n]*)\n(.*?)(?:\(\*\)\s*Sin impuestos|\Z)",
        texto_popup,
        re.S | re.I,
    )
    if m_bloque:
        bloque_items = m_bloque.group(1)

    raw_items = re.findall(
        r"(\d+(?:\.\d+)?)\s+(UNIDAD|ZZ|NIU|SERV\w*)\s+(.*?)\s+(\d+(?:\.\d+)?)(?:\s+\d+(?:\.\d+)?\s+(\d+(?:\.\d+)?))?(?=\s*(?:\d+\.\d+\s+(?:UNIDAD|ZZ|NIU|SERV)|\Z))",
        bloque_items,
        re.S | re.I,
    )

    lineas_parsed = []
    for cant_s, unidad_s, desc_s, vu_s, imp_s in raw_items:
        cant = float(cant_s)
        vu = float(vu_s)
        desc_limpia = " ".join(desc_s.strip().split())
        monto_calc = round(cant * vu * 1.105, 2)
        if estado == "ANULADO":
            monto_calc = 0.0
        lineas_parsed.append(
            {
                "cantidad": cant,
                "valor_unitario": vu,
                "descripcion": desc_limpia,
                "monto_linea": monto_calc * signo,
            }
        )

    if not lineas_parsed:
        for it in item.get("items", []):
            if isinstance(it, dict) and "texto_completo" in it:
                t_comp = it["texto_completo"].strip()
                if len(t_comp) > 20 and "UNIDAD" in t_comp and not t_comp.startswith("Cantidad\tUnidad Medida\tDescripci"):
                    m_vu = re.search(r"(\d+(?:\.\d+)?)\s*$", t_comp)
                    vu_val = float(m_vu.group(1)) if m_vu else (total_cp / 1.105 if total_cp else 0.0)
                    desc_val = re.sub(r"^\d+(?:\.\d+)?\s+(?:UNIDAD|ZZ|NIU|SERV\w*)\s*", "", " ".join(t_comp.split()))
                    desc_val = re.sub(r"\s+\d+(?:\.\d+)?$", "", desc_val)
                    monto_val = round(vu_val * 1.105, 2)
                    lineas_parsed.append(
                        {
                            "cantidad": 1.0,
                            "valor_unitario": vu_val,
                            "descripcion": desc_val,
                            "monto_linea": monto_val * signo,
                        }
                    )
        if not lineas_parsed:
            desc_fb = " ".join(texto_popup.split())[:180]
            lineas_parsed.append(
                {
                    "cantidad": 1.0,
                    "valor_unitario": total_cp / 1.105 if total_cp else 0.0,
                    "descripcion": desc_fb,
                    "monto_linea": total_cp * signo,
                }
            )

    # 9. Validación por comprobante (Regla 3): suma de líneas = Importe Total ± 0.10
    suma_lineas = round(sum(abs(l["monto_linea"]) for l in lineas_parsed), 2)
    flag_suma = False
    if estado != "ANULADO" and abs(suma_lineas - total_cp) > 0.10:
        flag_suma = True
        if suma_lineas > 0:
            factor = total_cp / suma_lineas
            acum = 0.0
            for idx_l, l in enumerate(lineas_parsed):
                if idx_l < len(lineas_parsed) - 1:
                    nuevo_m = round(abs(l["monto_linea"]) * factor, 2)
                    l["monto_linea"] = nuevo_m * signo
                    acum += nuevo_m
                else:
                    l["monto_linea"] = round(total_cp - acum, 2) * signo
        else:
            por_lin = round(total_cp / len(lineas_parsed), 2)
            for l in lineas_parsed:
                l["monto_linea"] = por_lin * signo

    # 9.5 Desglose de facturas grupales NEWREST según columnas AQ/AR/AW de Registro_Estancias
    if num_cp in ("E001-2050", "E001-2054") and len(lineas_parsed) == 1 and abs(total_cp - 240.0) <= 0.10:
        desc_orig = lineas_parsed[0]["descripcion"]
        vu_unit = round(80.0 / 1.105, 4)
        habs_nr = ["102", "204", "308"] if num_cp == "E001-2050" else ["308", "204", "102"]
        lineas_parsed = [
            {
                "cantidad": 1.0,
                "valor_unitario": vu_unit,
                "descripcion": f"{desc_orig} [NEWREST HAB {h_nr}]",
                "monto_linea": 80.0 * signo,
            }
            for h_nr in habs_nr
        ]
    elif num_cp == "E001-2060" and len(lineas_parsed) == 1 and abs(total_cp - 80.0) <= 0.10:
        lineas_parsed[0]["descripcion"] = f"{lineas_parsed[0]['descripcion']} [NEWREST HAB 204]"
    elif num_cp == "E001-2095" and abs(total_cp - 145.0) <= 0.10:
        # QUISAPATA SOLUCIONES S.A.C. (26/10/2026): 2 Dobles (401, 402 a S/ 55) + 1 Simple (404 a S/ 35)
        lineas_parsed = [
            {
                "cantidad": 1.0,
                "valor_unitario": round(m_q / 1.105, 4),
                "descripcion": f"DIA DE ALOJAMIENTO DEL 26 DE OCTUBRE [QUISAPATA HAB {h_q}]",
                "monto_linea": m_q * signo,
            }
            for h_q, m_q in [("401", 55.0), ("402", 55.0), ("404", 35.0)]
        ]
    elif num_cp == "E001-2093" and abs(total_cp - 1175.0) <= 0.10:
        # QUISAPATA SOLUCIONES S.A.C. (27 al 31/10/2026 - Checkin 27/10): 3 Dobles (401, 402, 405 a S/ 275) + 2 Simples (403, 404 a S/ 175)
        lineas_parsed = [
            {
                "cantidad": 1.0,
                "valor_unitario": round(m_q / 1.105, 4),
                "descripcion": f"DIA DE ALOJAMIENTO DEL 27 DE OCTUBRE [QUISAPATA HAB {h_q}]",
                "monto_linea": m_q * signo,
            }
            for h_q, m_q in [("401", 275.0), ("402", 275.0), ("405", 275.0), ("403", 175.0), ("404", 175.0)]
        ]
    elif num_cp == "E001-2105" and abs(total_cp - 3705.0) <= 0.10:
        # QUISAPATA SOLUCIONES S.A.C. (01 al 15/11/2026 - Checkin 01/11): 3 Dobles (401, 402, 405 a S/ 855) + 2 Simples (403, 404 a S/ 570)
        lineas_parsed = [
            {
                "cantidad": 1.0,
                "valor_unitario": round(m_q / 1.105, 4),
                "descripcion": f"DIA DE ALOJAMIENTO DEL 01 DE NOVIEMBRE [QUISAPATA HAB {h_q}]",
                "monto_linea": m_q * signo,
            }
            for h_q, m_q in [("401", 855.0), ("402", 855.0), ("405", 855.0), ("403", 570.0), ("404", 570.0)]
        ]
    elif num_cp == "E001-2113" and abs(total_cp - 233.0) <= 0.10:
        # QUISAPATA SOLUCIONES S.A.C. (08/11/2026): Hab 203 (57), Hab 207 (50), Hab 307 (50), Hab 406 (38), Hab 407 (38)
        lineas_parsed = [
            {
                "cantidad": 1.0,
                "valor_unitario": round(m_q / 1.105, 4),
                "descripcion": f"DIA DE ALOJAMIENTO DEL 08 DE NOVIEMBRE [QUISAPATA HAB {h_q}]",
                "monto_linea": m_q * signo,
            }
            for h_q, m_q in [("203", 57.0), ("207", 50.0), ("307", 50.0), ("406", 38.0), ("407", 38.0)]
        ]

    # 10. Expandir cada línea por sus noches de alojamiento
    filas_salida = []
    nro_lineas_cp = len(lineas_parsed)

    for l in lineas_parsed:
        desc = l["descripcion"]
        hab, sin_hab = extraer_habitacion_linea(desc)
        noches = extraer_noches_linea(desc, fecha_emision, l["cantidad"])
        # Caso EB01-6509 emitido el 01/10/2026 donde tipearon "01 DE SEPTIEMBRE" por error en vez de "01 DE OCTUBRE"
        if num_cp == "EB01-6509" and noches == [("01", "09")]:
            noches = [("01", "10")]
        elif num_cp == "E001-2110" and noches == [("05", "10")]:
            noches = [("05", "11")]
        if sin_hab and filas_salida and filas_salida[-1]["habitacion"] != "GRUPAL":
            # En EB01-6504 las horas extras (S/ 15) corresponden a la Hab 306 del 02/10 (E4899 AJ=65)
            if num_cp == "EB01-6504":
                hab = "306"
                sin_hab = False
                noches = [("02", "10")]
            else:
                hab = filas_salida[-1]["habitacion"]
                sin_hab = False
                if not noches:
                    noches = [(filas_salida[-1]["dia"], filas_salida[-1]["mes"])]
        if not noches:
            partes_f = fecha_emision.split("/")
            if len(partes_f) == 3:
                noches = [(partes_f[0].zfill(2), partes_f[1].zfill(2))]
            else:
                noches = [("01", MES_STR)]

        n_noches = len(noches)
        monto_total_linea = l["monto_linea"]
        acum_noche = 0.0

        for idx_n, (dia_s, mes_s) in enumerate(noches):
            if idx_n < n_noches - 1:
                m_noche = round(monto_total_linea / n_noches, 2)
                acum_noche += m_noche
            else:
                m_noche = round(monto_total_linea - acum_noche, 2)

            cp_mod_corto = normalizar_cp_corto(item.get("cp_modificado", ""))
            nc_asoc_corto = normalizar_cp_corto(item.get("nc_asociada", ""))

            flags = []
            if sin_hab:
                flags.append("SIN_HAB")
            if flag_suma:
                flags.append("SUMA≠TOTAL")
            if int(mes_s) != MES:
                flags.append("NOCHE_FUERA_DE_MES")
            if tipo == "NOTA_CREDITO" and cp_mod_corto:
                flags.append(f"ANULA_A:{cp_mod_corto}")
            if tipo != "NOTA_CREDITO" and nc_asoc_corto:
                flags.append(f"ANULADO_POR_NC:{nc_asoc_corto}")

            noche_str = f"{dia_s}/{mes_s}/{ANIO_STR}"
            filas_salida.append(
                {
                    "noche": noche_str,
                    "dia": dia_s,
                    "mes": mes_s,
                    "anio": ANIO_STR,
                    "habitacion": hab,
                    "monto_linea": m_noche,
                    "tipo": tipo,
                    "nro_comprobante": f"NC:{num_cp}" if tipo == "NOTA_CREDITO" and not num_cp.startswith("NC") else num_cp,
                    "cp_modificado": cp_mod_corto,
                    "cliente": cliente,
                    "doc": doc,
                    "fecha_emision": fecha_emision,
                    "estado": estado,
                    "total_comprobante": round(total_cp * signo, 2),
                    "nro_lineas": nro_lineas_cp,
                    "texto_linea": desc,
                    "flags": flags,
                }
            )

    return filas_salida, total_cp * signo, tipo, num_cp


def cargar_comprobantes_unicos():
    datos = {"facturas": [], "boletas": [], "notas_credito": []}
    if os.path.exists(RUTA_RAW_BOLETAS):
        with open(RUTA_RAW_BOLETAS, "r", encoding="utf-8") as f:
            datos = json.load(f)

    vistos = set()
    comprobantes = []
    mapa_lineas_origen = {}

    for fac in datos.get("facturas", []):
        filas_cp, tot_cp, tipo_cp, num_cp = parsear_comprobante(fac, "FACTURA")
        if num_cp and num_cp not in vistos:
            vistos.add(num_cp)
            comprobantes.append((num_cp, tipo_cp, tot_cp, filas_cp))
            mapa_lineas_origen[num_cp] = filas_cp

    for bol in datos.get("boletas", []):
        filas_cp, tot_cp, tipo_cp, num_cp = parsear_comprobante(bol, "BOLETA")
        if num_cp and num_cp not in vistos:
            vistos.add(num_cp)
            comprobantes.append((num_cp, tipo_cp, tot_cp, filas_cp))
            mapa_lineas_origen[num_cp] = filas_cp

    for nc in datos.get("notas_credito", []):
        cp_mod_pre = normalizar_cp_corto(nc.get("cp_modificado", ""))
        if cp_mod_pre in ("EB01-6699", "EB01-6752", "E001-2088"):
            continue
        filas_cp, tot_cp, tipo_cp, num_cp = parsear_comprobante(nc, "NOTA_CREDITO")
        key_nc = f"NC:{num_cp}"
        if num_cp and key_nc not in vistos:
            vistos.add(key_nc)
            # Si la NC modifica un comprobante conocido, alinear noche y habitación con el comprobante origen
            cp_mod = normalizar_cp_corto(nc.get("cp_modificado", ""))
            if cp_mod and cp_mod in mapa_lineas_origen and len(filas_cp) == len(mapa_lineas_origen[cp_mod]):
                for f_nc, f_orig in zip(filas_cp, mapa_lineas_origen[cp_mod]):
                    f_nc["noche"] = f_orig["noche"]
                    f_nc["dia"] = f_orig["dia"]
                    f_nc["mes"] = f_orig["mes"]
                    f_nc["anio"] = f_orig["anio"]
                    f_nc["habitacion"] = f_orig["habitacion"]
                    if "SIN_HAB" in f_nc["flags"] and "SIN_HAB" not in f_orig["flags"]:
                        f_nc["flags"].remove("SIN_HAB")
            comprobantes.append((key_nc, tipo_cp, tot_cp, filas_cp))

    return comprobantes


def sincronizar_sqlite_local(todas_lineas, comprobantes):
    """
    Sincroniza directamente sunat_lineas_cache (reemplazando solo el mes 11/2026 y sus CPs)
    y sunat_ventas (UPSERT fila por fila sin DELETE masivo) en la SQLite activa
    (Prueba :8082 si es_prueba(), u Operativa :8080 si no).
    """
    try:
        from datetime import datetime
        from hoja_activa import (
            conectar_sqlite_activa,
            limpiar_outbox_caches_locales,
            obtener_sqlite_path,
        )

        conn = conectar_sqlite_activa(timeout=30.0)
        if conn is None:
            print(f"[WARN SQLite] No se encontró BD activa en {obtener_sqlite_path()}", flush=True)
            return

        ahora_iso = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        periodo_mes = f"{ANIO_STR}-{MES_STR}"
        patron_mes = f"%/{MES_STR}/{ANIO_STR}"

        try:
            # 1. UPSERT fila por fila en sunat_ventas (NUNCA DELETE masivo para no disparar borrados en Supabase)
            sv_cols = {r[1] for r in conn.execute("PRAGMA table_info(sunat_ventas)").fetchall()}
            sv_tiene_id = ("id" in sv_cols and "tipo_doc" in sv_cols)
            upserted_sv = 0
            for key_cp, tipo_cp, tot_cp, filas_cp in comprobantes:
                if not filas_cp:
                    continue
                f0 = filas_cp[0]
                nro_cp_raw = str(f0.get("nro_comprobante") or key_cp).strip().upper()
                nro_cp_clean = re.sub(r"^NC:", "", nro_cp_raw).strip()
                if not nro_cp_clean:
                    continue
                m_s = re.match(r"^([A-Z0-9]+)-0*(\d+)$", nro_cp_clean)
                serie_cp = m_s.group(1) if m_s else ""
                num_int = int(m_s.group(2)) if m_s else 0
                fec_em = str(f0.get("fecha_emision") or "").strip()
                cli_cp = re.sub(r"^-\s*", "", str(f0.get("cliente") or "")).strip()
                doc_cp = str(f0.get("doc") or "").strip()
                est_cp = str(f0.get("estado") or "VIGENTE").strip().upper()
                resumen_cp = " | ".join(
                    dict.fromkeys(str(fl.get("texto_linea") or "").strip() for fl in filas_cp if fl.get("texto_linea"))
                )[:600]
                monto_abs = round(abs(float(tot_cp or 0.0)), 2)

                if sv_tiene_id:
                    sv_row = conn.execute(
                        """
                        SELECT id, periodo_mes, tipo, fecha_emision, receptor_doc, receptor_nombre,
                               ROUND(COALESCE(monto_total, total, 0), 2) AS m, estado, resumen_items
                        FROM sunat_ventas
                        WHERE nro_cp = ? AND (tipo = ? OR tipo_doc = ? OR COALESCE(tipo, '') = '')
                        LIMIT 1
                        """,
                        (nro_cp_clean, tipo_cp, tipo_cp),
                    ).fetchone()
                    if sv_row is None:
                        sv_row = conn.execute(
                            "SELECT id, periodo_mes, tipo, fecha_emision, receptor_doc, receptor_nombre, ROUND(COALESCE(monto_total, total, 0), 2) AS m, estado, resumen_items FROM sunat_ventas WHERE nro_cp = ? LIMIT 1",
                            (nro_cp_clean,),
                        ).fetchone()

                    if sv_row is None:
                        conn.execute(
                            """
                            INSERT INTO sunat_ventas (
                                periodo_mes, tipo_doc, tipo, nro_cp, serie, numero,
                                fecha_emision, receptor_doc, receptor_nombre,
                                total, monto_total, estado, resumen_items, updated_at, actualizado_en
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                periodo_mes,
                                tipo_cp,
                                tipo_cp,
                                nro_cp_clean,
                                serie_cp,
                                num_int,
                                fec_em,
                                doc_cp,
                                cli_cp,
                                monto_abs,
                                monto_abs,
                                est_cp,
                                resumen_cp,
                                ahora_iso,
                                ahora_iso,
                            ),
                        )
                        upserted_sv += 1
                    else:
                        changed = (
                            str(sv_row["estado"] or "") != est_cp
                            or abs(float(sv_row["m"] or 0.0) - monto_abs) > 0.01
                            or str(sv_row["tipo"] or "") != tipo_cp
                            or str(sv_row["receptor_doc"] or "") != doc_cp
                            or str(sv_row["receptor_nombre"] or "") != cli_cp
                        )
                        if changed:
                            conn.execute(
                                """
                                UPDATE sunat_ventas
                                SET periodo_mes = ?, tipo_doc = ?, tipo = ?, serie = ?, numero = ?,
                                    fecha_emision = ?, receptor_doc = ?, receptor_nombre = ?,
                                    total = ?, monto_total = ?, estado = ?, resumen_items = ?,
                                    updated_at = ?, actualizado_en = ?
                                WHERE id = ?
                                """,
                                (
                                    periodo_mes,
                                    tipo_cp,
                                    tipo_cp,
                                    serie_cp,
                                    num_int,
                                    fec_em,
                                    doc_cp,
                                    cli_cp,
                                    monto_abs,
                                    monto_abs,
                                    est_cp,
                                    resumen_cp,
                                    ahora_iso,
                                    ahora_iso,
                                    sv_row["id"],
                                ),
                            )
                            upserted_sv += 1
                else:
                    sv_row = conn.execute(
                        """
                        SELECT nro_cp, periodo_mes, tipo, fecha_emision, receptor_doc, receptor_nombre,
                               ROUND(COALESCE(monto_total, 0), 2) AS m, estado, resumen_items
                        FROM sunat_ventas
                        WHERE nro_cp = ?
                        LIMIT 1
                        """,
                        (nro_cp_clean,),
                    ).fetchone()
                    if sv_row is None:
                        conn.execute(
                            """
                            INSERT INTO sunat_ventas (
                                nro_cp, tipo, periodo_mes, fecha_emision,
                                receptor_doc, receptor_nombre, monto_total,
                                estado, resumen_items, actualizado_en
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                nro_cp_clean,
                                tipo_cp,
                                periodo_mes,
                                fec_em,
                                doc_cp,
                                cli_cp,
                                monto_abs,
                                est_cp,
                                resumen_cp,
                                ahora_iso,
                            ),
                        )
                        upserted_sv += 1
                    else:
                        changed = (
                            str(sv_row["estado"] or "") != est_cp
                            or abs(float(sv_row["m"] or 0.0) - monto_abs) > 0.01
                            or str(sv_row["tipo"] or "") != tipo_cp
                            or str(sv_row["receptor_doc"] or "") != doc_cp
                            or str(sv_row["receptor_nombre"] or "") != cli_cp
                        )
                        if changed:
                            conn.execute(
                                """
                                UPDATE sunat_ventas
                                SET tipo = ?, periodo_mes = ?, fecha_emision = ?,
                                    receptor_doc = ?, receptor_nombre = ?, monto_total = ?,
                                    estado = ?, resumen_items = ?, actualizado_en = ?
                                WHERE nro_cp = ?
                                """,
                                (
                                    tipo_cp,
                                    periodo_mes,
                                    fec_em,
                                    doc_cp,
                                    cli_cp,
                                    monto_abs,
                                    est_cp,
                                    resumen_cp,
                                    ahora_iso,
                                    nro_cp_clean,
                                ),
                            )
                            upserted_sv += 1

            # 2. Actualizar sunat_lineas_cache reemplazando SOLO las filas de este mes/lote (preservando Octubre y CPEs recientes)
            cps_lote = sorted({str(l.get("nro_comprobante") or "").strip().upper() for l in todas_lineas if l.get("nro_comprobante")})
            cps_lote_set = set(cps_lote)
            extra_nov_locales = [
                tuple(r)
                for r in conn.execute(
                    """
                    SELECT nro_cp, tipo, noche_dmy, hab, monto_linea, total_cp,
                           cliente, doc, fecha_emision, estado, descripcion
                    FROM sunat_lineas_cache
                    WHERE fecha_emision LIKE ?
                    """,
                    (patron_mes,),
                ).fetchall()
                if str(r[0] or "").strip().upper() not in cps_lote_set
            ]
            conn.execute(
                "DELETE FROM sunat_lineas_cache WHERE fecha_emision LIKE ? OR (COALESCE(fecha_emision, '') = '' AND noche_dmy LIKE ?)",
                (patron_mes, patron_mes),
            )
            for i_chunk in range(0, len(cps_lote), 150):
                chunk = cps_lote[i_chunk : i_chunk + 150]
                ph = ",".join("?" for _ in chunk)
                conn.execute(f"DELETE FROM sunat_lineas_cache WHERE nro_cp IN ({ph})", chunk)

            for l in todas_lineas:
                conn.execute(
                    """
                    INSERT INTO sunat_lineas_cache (
                        nro_cp, tipo, noche_dmy, hab, monto_linea, total_cp,
                        cliente, doc, fecha_emision, estado, descripcion
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(l.get("nro_comprobante") or "").strip().upper(),
                        str(l.get("tipo") or "BOLETA").strip().upper(),
                        str(l.get("noche") or "").strip(),
                        str(l.get("habitacion") or "").strip().upper(),
                        round(float(l.get("monto_linea") or 0.0), 2),
                        round(float(l.get("total_comprobante") or 0.0), 2),
                        re.sub(r"^-\s*", "", str(l.get("cliente") or "")).strip(),
                        str(l.get("doc") or "").strip(),
                        str(l.get("fecha_emision") or "").strip(),
                        str(l.get("estado") or "VIGENTE").strip().upper(),
                        str(l.get("texto_linea") or "").strip(),
                    ),
                )
            for r_ext in extra_nov_locales:
                conn.execute(
                    """
                    INSERT INTO sunat_lineas_cache (
                        nro_cp, tipo, noche_dmy, hab, monto_linea, total_cp,
                        cliente, doc, fecha_emision, estado, descripcion
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    r_ext,
                )

            limpiar_outbox_caches_locales(conn)
            conn.commit()
            tot_sl = conn.execute("SELECT COUNT(*) FROM sunat_lineas_cache").fetchone()[0]
            print(
                f"[SQLITE LOCAL] '{obtener_sqlite_path()}' sincronizada: "
                f"{len(todas_lineas)} líneas Noviembre cargadas en sunat_lineas_cache (total acumulado={tot_sl}), "
                f"{upserted_sv} cambios UPSERT en sunat_ventas.",
                flush=True,
            )
        finally:
            conn.close()
    except Exception as e_sql:
        print(f"[WARN SQLite] Error sincronizando SQLite en Construir_SUNAT_NOV: {e_sql}", flush=True)


def main():
    solo_sqlite = "--solo-sqlite" in sys.argv
    print("[FASE 2 NOVIEMBRE] Construyendo líneas por noche para SUNAT_NOV...", flush=True)
    comprobantes = cargar_comprobantes_unicos()

    todas_lineas = []
    totales_cp_por_tipo = {"FACTURA": 0.0, "BOLETA": 0.0, "NOTA_CREDITO": 0.0}
    conteo_cp_por_tipo = {"FACTURA": 0, "BOLETA": 0, "NOTA_CREDITO": 0}

    for num_cp, tipo_cp, tot_cp, filas_cp in comprobantes:
        totales_cp_por_tipo[tipo_cp] = round(totales_cp_por_tipo.get(tipo_cp, 0.0) + tot_cp, 2)
        conteo_cp_por_tipo[tipo_cp] = conteo_cp_por_tipo.get(tipo_cp, 0) + 1
        todas_lineas.extend(filas_cp)

    conteo_llave = {}
    for l in todas_lineas:
        if l["estado"] == "VIGENTE" and l["habitacion"] != "GRUPAL":
            k = (l["noche"], l["habitacion"])
            delta = -1 if l["tipo"] == "NOTA_CREDITO" else 1
            conteo_llave[k] = conteo_llave.get(k, 0) + delta

    for l in todas_lineas:
        if l["estado"] == "VIGENTE" and l["habitacion"] != "GRUPAL":
            if conteo_llave.get((l["noche"], l["habitacion"]), 0) > 1:
                if "DUPLICADO" not in l["flags"]:
                    l["flags"].append("DUPLICADO")

    def sort_key(x):
        return (int(x["anio"]), int(x["mes"]), int(x["dia"]), x["habitacion"], x["nro_comprobante"])

    todas_lineas.sort(key=sort_key)

    with open(RUTA_OUT_LINEAS, "w", encoding="utf-8") as f:
        json.dump(todas_lineas, f, ensure_ascii=False, indent=2)

    print("\n================== PUERTA 2 OCTUBRE: VERIFICACIÓN DE PARSEO ==================")
    print("Cuadre de suma de Monto_Línea vs Total_Comprobante por tipo:")
    for t in ["FACTURA", "BOLETA", "NOTA_CREDITO"]:
        suma_lin = round(sum(l["monto_linea"] for l in todas_lineas if l["tipo"] == t), 2)
        tot_t = totales_cp_por_tipo.get(t, 0.0)
        diff = round(suma_lin - tot_t, 2)
        print(
            f"   -> {t}: {conteo_cp_por_tipo.get(t, 0)} comprobantes | "
            f"Suma Comprobantes = S/ {tot_t:,.2f} | Suma Líneas = S/ {suma_lin:,.2f} | Dif = S/ {diff:,.2f}"
        )

    sin_hab_list = [l for l in todas_lineas if "SIN_HAB" in l["flags"]]
    suma_neq_list = [l for l in todas_lineas if "SUMA≠TOTAL" in l["flags"]]
    fuera_mes_list = [l for l in todas_lineas if "NOCHE_FUERA_DE_MES" in l["flags"]]
    dup_list = [l for l in todas_lineas if "DUPLICADO" in l["flags"]]

    print(
        f"\nConteo de Flags en {len(todas_lineas)} filas generadas:\n"
        f"   - SIN_HAB: {len(sin_hab_list)}\n"
        f"   - SUMA≠TOTAL: {len(suma_neq_list)}\n"
        f"   - NOCHE_FUERA_DE_MES: {len(fuera_mes_list)}\n"
        f"   - DUPLICADO (misma noche+hab): {len(dup_list)}"
    )
    for ejemplo in (sin_hab_list + suma_neq_list + fuera_mes_list)[:8]:
        print(
            f"   [Flag] {ejemplo['nro_comprobante']} | {ejemplo['noche']} | "
            f"Hab: {ejemplo['habitacion']} | S/ {ejemplo['monto_linea']:.2f} | {ejemplo['flags']} | {ejemplo['texto_linea'][:70]}"
        )

    if solo_sqlite:
        print(f"\n[MODO --solo-sqlite] Omitiendo escritura en Google Sheets ('{NOMBRE_HOJA_SUNAT}').", flush=True)
        sincronizar_sqlite_local(todas_lineas, comprobantes)
        return

    print(f"\n[GOOGLE SHEETS] Escribiendo {len(todas_lineas)} filas en '{NOMBRE_HOJA_SUNAT}'...", flush=True)
    creds = Credentials.from_service_account_file(RUTA_CREDS, scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SPREADSHEET_ID)

    try:
        ws = sh.worksheet(NOMBRE_HOJA_SUNAT)
        ws.clear()
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=NOMBRE_HOJA_SUNAT, rows=max(len(todas_lineas) + 50, 250), cols=15)

    encabezados = [
        "Noche",              # A
        "Habitación",         # B
        "Monto_Línea",        # C
        "Tipo",               # D
        "Nro_Comprobante",    # E
        "Cliente",            # F
        "Doc",                # G
        "Fecha_Emisión",      # H
        "Estado",             # I
        "Total_Comprobante",  # J
        "Nro_Líneas",         # K
        "Texto_Línea",        # L
        "Flags",              # M
    ]

    filas_sheet = [encabezados]
    for l in todas_lineas:
        filas_sheet.append(
            [
                l["noche"],
                l["habitacion"],
                l["monto_linea"],
                l["tipo"],
                l["nro_comprobante"],
                l["cliente"],
                l["doc"],
                l["fecha_emision"],
                l["estado"],
                l["total_comprobante"],
                l["nro_lineas"],
                l["texto_linea"],
                ", ".join(l["flags"]),
            ]
        )

    ws.update(values=filas_sheet, range_name="A1", value_input_option="USER_ENTERED")

    try:
        ws.freeze(rows=1)
        ws.set_basic_filter(f"A1:M{len(filas_sheet)}")
        ws.format(
            "A1:M1",
            {
                "backgroundColor": {"red": 0.16, "green": 0.24, "blue": 0.38},
                "textFormat": {"bold": True, "foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0}},
                "horizontalAlignment": "CENTER",
            },
        )
        ws.format(f"A2:A{len(filas_sheet)}", {"numberFormat": {"type": "DATE", "pattern": "dd/mm/yyyy"}})
        ws.format(f"C2:C{len(filas_sheet)}", {"numberFormat": {"type": "NUMBER", "pattern": "#,##0.00"}})
        ws.format(f"J2:J{len(filas_sheet)}", {"numberFormat": {"type": "NUMBER", "pattern": "#,##0.00"}})
    except Exception as e_fmt:
        print(f"   [WARN] Formato opcional: {e_fmt}")

    print(f"[OK] Hoja '{NOMBRE_HOJA_SUNAT}' actualizada exitosamente con {len(todas_lineas)} filas.", flush=True)
    sincronizar_sqlite_local(todas_lineas, comprobantes)


if __name__ == "__main__":
    main()
