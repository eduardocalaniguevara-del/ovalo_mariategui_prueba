#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
ORQUESTADOR DE EMISIÓN + ENVÍO POR WHATSAPP
Hostal El Óvalo Mariátegui - Laptop 1 (Controlador)

Unifica la validación de auditoría, preparación del comprobante
electrónico (PDF), sincronización con Recepción (Syncthing) y el
despacho remoto a través de WhatsApp (Laptop 2).
"""

import os
import sys
import time
import json
import re
import glob
import shutil
import argparse
import subprocess
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Rutas estándar del sistema
BASE_DIR = r"C:\Hostal_Sistema"
DIR_SCRIPTS = os.path.dirname(os.path.abspath(__file__))
if DIR_SCRIPTS not in sys.path:
    sys.path.insert(0, DIR_SCRIPTS)
DIR_ENVIOS_TEMP = os.path.join(BASE_DIR, "scripts", "envios_temp")
DIR_DESCARGAS = os.path.join(BASE_DIR, "descargas_recepcion")
ARCHIVO_REGLAS = os.path.join(BASE_DIR, "reglas_auditoria.json")
ARCHIVO_CONFIG = os.path.join(BASE_DIR, "config_sistema.json")
SCRIPT_EJECUTAR_REMOTO = os.path.join(BASE_DIR, "ejecutar_remoto.py")

# Asegurar directorios
os.makedirs(DIR_ENVIOS_TEMP, exist_ok=True)
os.makedirs(DIR_DESCARGAS, exist_ok=True)


def cargar_reglas():
    """Carga las reglas estadísticas de auditoría."""
    if os.path.exists(ARCHIVO_REGLAS):
        try:
            with open(ARCHIVO_REGLAS, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[WARN] No se pudo leer {ARCHIVO_REGLAS}: {e}")
    return {
        "umbrales_auditoria": {"umbral_alerta_monto": 250.0, "umbral_alerta_critico": 500.0},
        "regla_cero_extra": {"activo": True, "valores_sospechosos_cero_extra": [300.0, 350.0, 400.0, 500.0, 600.0, 700.0]}
    }


def validar_monto(monto, forzar=False):
    """
    Valida el monto contra las reglas de auditoría aprendidas.
    Si supera el límite y no se especificó --forzar, bloquea la emisión.
    """
    reglas = cargar_reglas()
    umbrales = reglas.get("umbrales_auditoria", {})
    umbral_alerta = umbrales.get("umbral_alerta_monto", 250.0)
    umbral_critico = umbrales.get("umbral_alerta_critico", 500.0)

    cero_extra = reglas.get("regla_cero_extra", {})
    valores_cero_extra = cero_extra.get("valores_sospechosos_cero_extra", [])

    es_cero_extra = (monto in valores_cero_extra)
    es_exceso = (monto > umbral_alerta)

    if (es_cero_extra or es_exceso) and not forzar:
        motivo = []
        if es_cero_extra:
            motivo.append(f"Monto S/ {monto:.2f} coincide con posible error de 'Cero Extra' (ej. tarifa S/ {monto/10:.2f} con un cero de más).")
        if es_exceso:
            motivo.append(f"Monto S/ {monto:.2f} supera el percentil habitual de estancias (límite S/ {umbral_alerta:.2f}).")

        mensaje_bloqueo = (
            "\n" + "=" * 65 + "\n"
            "🚨 [AUDITORÍA PREVENTIVA] EMISIÓN BLOQUEADA POR SEGURIDAD\n"
            + "=" * 65 + "\n"
            + "\n".join(f"- {m}" for m in motivo) + "\n\n"
            "👉 Si el monto es CORRECTO (ej. estadía corporativa o larga),\n"
            "   vuelva a ejecutar agregando el parámetro --forzar para autorizar la emisión.\n"
            + "=" * 65
        )
        return False, mensaje_bloqueo

    return True, "Validación de monto aprobada."


def buscar_ultimo_comprobante(cliente=None, ruc_dni=None):
    """
    Busca el comprobante PDF más reciente en descargas_recepcion
    opcionalmente filtrando por nombre o documento.
    """
    archivos = glob.glob(os.path.join(DIR_DESCARGAS, "*.pdf"))
    if not archivos:
        return None

    # Ordenar por fecha de modificación descendente
    archivos.sort(key=os.path.getmtime, reverse=True)

    if not cliente and not ruc_dni:
        return archivos[0]

    termino_busqueda = (ruc_dni or "").strip()
    if not termino_busqueda and cliente:
        # Tomar primera palabra significativa del cliente
        palabras = [p for p in cliente.split() if len(p) > 3]
        termino_busqueda = palabras[0] if palabras else cliente

    # Buscar coincidencia en nombre de archivo o contenido reciente
    for ruta in archivos[:15]:
        nombre = os.path.basename(ruta)
        if termino_busqueda.lower() in nombre.lower():
            return ruta

    # Retornar el más reciente como fallback
    return archivos[0]


def emitir_y_despachar(cliente, ruc_dni, monto, concepto, telefono, ruta_archivo=None, forzar=False, mensaje_personalizado=None):
    """Flujo unificado de validación, preparación y despacho remoto vía WhatsApp."""
    print("=" * 65)
    print(" ORQUESTADOR DE COMPROBANTES Y DESPACHO WHATSAPP")
    print("=" * 65)
    print(f"Cliente        : {cliente or 'No especificado'}")
    print(f"RUC/DNI        : {ruc_dni or 'No especificado'}")
    print(f"Monto Total    : S/ {monto:.2f}")
    print(f"Concepto       : {concepto or 'Servicio de Hospedaje'}")
    print(f"Teléfono Dest. : {telefono}")
    print("-" * 65)

    # 1. Validación de Auditoría
    valido, mensaje_val = validar_monto(monto, forzar=forzar)
    if not valido:
        print(mensaje_val)
        sys.exit(1)
    else:
        print(f"✅ [1/4] {mensaje_val}")

    try:
        if DIR_SCRIPTS not in sys.path:
            sys.path.insert(0, DIR_SCRIPTS)
        from hoja_activa import es_prueba as _es_prueba_ha
        _en_prueba = bool(_es_prueba_ha())
    except ImportError:
        _en_prueba = os.environ.get("OVALO_ENTORNO", "operativa").strip().lower() != "operativa"

    if _en_prueba:
        print("\n🧪 [SIMULACIÓN MODO PRUEBA - NO EMITIDO EN SUNAT / NO ENVIADO POR WSP]")
        print(f"   Cliente: {cliente} | RUC/DNI: {ruc_dni} | Monto: S/ {monto:.2f} | Teléfono: {telefono}")
        return 0

    # 2. Localización / Preparación del PDF
    pdf_origen = None
    if ruta_archivo and os.path.isfile(ruta_archivo):
        pdf_origen = os.path.abspath(ruta_archivo)
    else:
        print("🔍 [2/4] Localizando comprobante en descargas de recepción...")
        pdf_origen = buscar_ultimo_comprobante(cliente=cliente, ruc_dni=ruc_dni)

    if not pdf_origen or not os.path.isfile(pdf_origen):
        print(f"❌ [ERROR] No se encontró ningún comprobante PDF para adjuntar.")
        print(f"   Por favor especifique la ruta exacta con --archivo <ruta.pdf>.")
        sys.exit(2)

    nombre_pdf = os.path.basename(pdf_origen)
    pdf_destino = os.path.join(DIR_ENVIOS_TEMP, nombre_pdf)

    # Copiar comprobante a envios_temp
    shutil.copy2(pdf_origen, pdf_destino)
    print(f"📁 [2/4] Comprobante preparado en envios_temp: {nombre_pdf}")

    # 3. Esperar a Syncthing
    print("⏳ [3/4] Esperando 2 segundos para sincronización con Laptop 2 (Syncthing)...")
    time.sleep(2.0)

    # 4. Disparo Remoto hacia Laptop 2
    mensaje_final = mensaje_personalizado or "Estimado cliente, le adjuntamos su comprobante electrónico. ¡Gracias por su preferencia!"
    print(f"🚀 [4/4] Enviando orden remota a Laptop 2 (Recepción) -> WhatsApp: {telefono}...")

    cmd = [
        sys.executable,
        SCRIPT_EJECUTAR_REMOTO,
        "brazo_wsp.py",
        "--telefono", str(telefono),
        "--archivo", pdf_destino,
        "--mensaje", mensaje_final
    ]

    try:
        res = subprocess.run(cmd, text=True, capture_output=False)
        if res.returncode == 0:
            print("\n" + "=" * 65)
            print("🎉 ¡OPERACIÓN COMPLETADA CON ÉXITO!")
            print(f"   Comprobante : {nombre_pdf}")
            print(f"   Destinatario: {telefono}")
            print("=" * 65)
            return 0
        else:
            print(f"\n❌ Error al ejecutar orden remota en Laptop 2 (Código {res.returncode}).")
            return res.returncode
    except Exception as e:
        print(f"\n❌ Excepción ejecutando orden remota: {e}")
        return 3


def main():
    parser = argparse.ArgumentParser(description="Orquestador de Emisión y Envío por WhatsApp")
    parser.add_argument("--cliente", type=str, default="", help="Nombre o Razón Social del cliente")
    parser.add_argument("--ruc_dni", type=str, default="", help="RUC o DNI del cliente")
    parser.add_argument("--monto", type=float, required=True, help="Monto total en Soles (ej. 70.00)")
    parser.add_argument("--concepto", type=str, default="Alojamiento", help="Descripción del servicio")
    parser.add_argument("--telefono", type=str, required=True, help="Número de WhatsApp destino")
    parser.add_argument("--archivo", type=str, default=None, help="Ruta al comprobante PDF (opcional)")
    parser.add_argument("--mensaje", type=str, default=None, help="Mensaje opcional para WhatsApp")
    parser.add_argument("--forzar", action="store_true", help="Autorizar emisión si supera el umbral de auditoría")

    args = parser.parse_args()

    sys.exit(emitir_y_despachar(
        cliente=args.cliente,
        ruc_dni=args.ruc_dni,
        monto=args.monto,
        concepto=args.concepto,
        telefono=args.telefono,
        ruta_archivo=args.archivo,
        forzar=args.forzar,
        mensaje_personalizado=args.mensaje
    ))


if __name__ == "__main__":
    main()
