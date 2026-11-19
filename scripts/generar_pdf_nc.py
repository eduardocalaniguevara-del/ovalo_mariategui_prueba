#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Genera la representación impresa oficial en PDF de una Nota de Crédito Electrónica SUNAT
y la guarda en la carpeta por día de check-in:
C:\Hostal_Sistema\notas_de_credito\nc_boletas\02_Octubre\
"""

import os
import sys
import json
import re
from playwright.sync_api import sync_playwright

try:
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr is not None:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

def generar_pdf_nota_credito(nc_data, dir_destino, nombre_archivo=None):
    os.makedirs(dir_destino, exist_ok=True)
    
    nro_nc = nc_data.get("numero_cp", "EB01-00")
    fec_emision = nc_data.get("fecha_emision", "")
    cp_mod = nc_data.get("cp_modificado", "")
    receptor_nom = nc_data.get("receptor_nombre", "")
    receptor_doc = nc_data.get("receptor_doc", "")
    motivo = nc_data.get("motivo_nc", "ANULACION POR ERROR EN LA EMISION")
    monto_total = float(nc_data.get("monto_total", 0.0))
    op_gravada = round(monto_total / 1.105, 2)
    igv = round(monto_total - op_gravada, 2)
    
    # Extraer descripcion
    desc = "ALOJAMIENTO DEL DIA 02 DE OCTUBRE. HAB N 206."
    val_unit = f"{monto_total / 1.105:.4f}"
    imp_vta = f"{monto_total:.2f}"
    
    for it in nc_data.get("items", []):
        t = it.get("texto_completo", "")
        if "ALOJAMIENTO" in t or "HAB" in t:
            m_desc = re.search(r'(ALOJAMIENTO[^\t\n\r]+)', t)
            if m_desc:
                desc = m_desc.group(1).strip()
            break

    html_content = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<style>
  body {{
    font-family: Arial, Helvetica, sans-serif;
    font-size: 11px;
    color: #000;
    margin: 25px 35px;
  }}
  .header-table {{
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 12px;
  }}
  .empresa-box {{
    width: 60%;
    vertical-align: top;
  }}
  .empresa-title {{
    font-weight: bold;
    font-size: 13px;
    margin-bottom: 3px;
  }}
  .empresa-sub {{
    font-size: 11px;
    font-weight: bold;
    margin-bottom: 2px;
  }}
  .empresa-dir {{
    font-size: 10px;
    color: #333;
  }}
  .ruc-box {{
    width: 38%;
    border: 2px solid #000;
    text-align: center;
    padding: 10px 5px;
    vertical-align: middle;
  }}
  .ruc-box h2 {{
    margin: 0 0 5px 0;
    font-size: 13px;
    font-weight: bold;
  }}
  .ruc-box h3 {{
    margin: 0 0 5px 0;
    font-size: 12px;
    font-weight: bold;
  }}
  .ruc-box h1 {{
    margin: 0;
    font-size: 14px;
    font-weight: bold;
  }}
  .datos-table {{
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 12px;
    font-size: 11px;
  }}
  .datos-table td {{
    padding: 2px 4px;
  }}
  .items-table {{
    width: 100%;
    border-collapse: collapse;
    margin-bottom: 12px;
  }}
  .items-table th {{
    border-top: 1px solid #000;
    border-bottom: 1px solid #000;
    padding: 5px 4px;
    font-size: 10px;
    text-align: left;
  }}
  .items-table td {{
    padding: 5px 4px;
    font-size: 10px;
  }}
  .totales-table {{
    width: 45%;
    margin-left: auto;
    border-collapse: collapse;
    margin-bottom: 15px;
  }}
  .totales-table td {{
    padding: 2px 4px;
    font-size: 10px;
    text-align: right;
  }}
  .pie-legal {{
    border-top: 1px solid #000;
    padding-top: 8px;
    font-size: 9px;
    text-align: center;
    color: #222;
    font-style: italic;
  }}
</style>
</head>
<body>

<table class="header-table">
  <tr>
    <td class="empresa-box">
      <div class="empresa-title">EL OVALO MARIATEGUI</div>
      <div class="empresa-sub">MAELEN HOGAR EMPRESA INDIVIDUAL DE RESPONSABILIDAD LIMITADA</div>
      <div class="empresa-dir">
        AV. PAZ 507 INT. A URB. VILLA MILITAR MARISCAL NI<br>
        MOQUEGUA - MOQUEGUA - MARISCAL NIETO
      </div>
    </td>
    <td class="ruc-box">
      <h2>RUC: 20611986484</h2>
      <h3>NOTA DE CREDITO ELECTRONICA</h3>
      <h1>{nro_nc}</h1>
    </td>
  </tr>
</table>

<table class="datos-table">
  <tr>
    <td style="width: 25%;"><strong>Documento que modifica</strong></td>
    <td style="width: 2%;">:</td>
    <td>Boleta de Venta Electr&oacute;nica {cp_mod}</td>
  </tr>
  <tr>
    <td><strong>Fecha de Emisi&oacute;n</strong></td>
    <td>:</td>
    <td>{fec_emision}</td>
  </tr>
  <tr>
    <td><strong>Se&ntilde;or(es)</strong></td>
    <td>:</td>
    <td>{receptor_nom}</td>
  </tr>
  <tr>
    <td><strong>DNI / RUC</strong></td>
    <td>:</td>
    <td>{receptor_doc}</td>
  </tr>
  <tr>
    <td><strong>Tipo de Moneda</strong></td>
    <td>:</td>
    <td>SOLES</td>
  </tr>
  <tr>
    <td><strong>Motivo o Sustento</strong></td>
    <td>:</td>
    <td>{motivo}</td>
  </tr>
  <tr>
    <td><strong>Tipo de Nota</strong></td>
    <td>:</td>
    <td>ANULACION DE LA OPERACION</td>
  </tr>
</table>

<table class="items-table">
  <thead>
    <tr>
      <th style="width: 10%;">Cantidad</th>
      <th style="width: 12%;">Unidad Medida</th>
      <th style="width: 48%;">Descripci&oacute;n</th>
      <th style="width: 15%; text-align: right;">Valor Unitario(*)</th>
      <th style="width: 15%; text-align: right;">Importe de Venta(**)</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>1.00</td>
      <td>UNIDAD</td>
      <td>{desc}</td>
      <td style="text-align: right;">{val_unit}</td>
      <td style="text-align: right;">{imp_vta}</td>
    </tr>
  </tbody>
</table>

<table class="totales-table">
  <tr>
    <td>Op. Gravada :</td>
    <td style="width: 35%;">S/ {op_gravada:.2f}</td>
  </tr>
  <tr>
    <td>Op. Exonerada :</td>
    <td>S/ 0.00</td>
  </tr>
  <tr>
    <td>Op. Inafecta :</td>
    <td>S/ 0.00</td>
  </tr>
  <tr>
    <td>ISC :</td>
    <td>S/ 0.00</td>
  </tr>
  <tr>
    <td>IGV (10.5%) :</td>
    <td>S/ {igv:.2f}</td>
  </tr>
  <tr>
    <td>Otros Cargos :</td>
    <td>S/ 0.00</td>
  </tr>
  <tr>
    <td>Otros Tributos :</td>
    <td>S/ 0.00</td>
  </tr>
  <tr style="border-top: 1px solid #000; font-weight: bold; font-size: 11px;">
    <td>Importe Total :</td>
    <td>S/ {monto_total:.2f}</td>
  </tr>
</table>

<div style="font-weight: bold; margin-bottom: 20px;">
  SON: CINCUENTA Y 00/100 SOLES
</div>

<div class="pie-legal">
  Esta es una representaci&oacute;n impresa de una Nota de Cr&eacute;dito Electr&oacute;nica, generada en el Sistema de la SUNAT.<br>
  El Emisor Electr&oacute;nico puede verificarla utilizando su clave SOL, el Adquirente o Usuario puede consultar su validez en SUNAT Virtual: www.sunat.gob.pe, en Opciones sin Clave SOL/ Consulta de Validez del CPE.
</div>

</body>
</html>
"""

    if not nombre_archivo:
        nom_cte = re.sub(r'[^A-Z0-9_]', '_', receptor_nom.upper())[:30]
        nombre_archivo = f"NOTA_CREDITO_{nro_nc.replace(' ', '')}_{nom_cte}_{monto_total:.2f}.pdf"

    ruta_pdf = os.path.join(dir_destino, nombre_archivo)

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page()
        page.set_content(html_content, wait_until="load")
        page.pdf(
            path=ruta_pdf,
            format="A4",
            margin={"top": "15mm", "bottom": "15mm", "left": "15mm", "right": "15mm"},
            print_background=True
        )
        browser.close()

    print(f"✅ PDF de Nota de Crédito generado exitosamente: {ruta_pdf}")
    
    # Guardar copia con nombre oficial SUNAT
    ruta_sunat_std = os.path.join(dir_destino, f"PDF-NOTACREDITO{nro_nc.replace(' ', '')}20611986484.pdf")
    try:
        import shutil
        shutil.copy2(ruta_pdf, ruta_sunat_std)
        # Copiar también a descargas_recepcion
        dir_rec = r"C:\Hostal_Sistema\descargas_recepcion"
        os.makedirs(dir_rec, exist_ok=True)
        shutil.copy2(ruta_pdf, os.path.join(dir_rec, nombre_archivo))
    except Exception as e_cp:
        print(f"   [INFO] Copia secundaria: {e_cp}")

    return ruta_pdf

if __name__ == "__main__":
    with open(r"C:\Hostal_Sistema\datos_sunat\sunat_notas_credito_octubre_raw.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    ncs = data.get("notas_credito", [])
    nc_38 = None
    for nc in ncs:
        if "38" in nc.get("numero_cp", ""):
            nc_38 = nc
            break
            
    if nc_38:
        dir_dest = r"C:\Hostal_Sistema\notas_de_credito\nc_boletas\02_Octubre"
        generar_pdf_nota_credito(nc_38, dir_dest)
    else:
        print("No se encontró EB01-38 en sunat_notas_credito_octubre_raw.json")
