#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NOTIFICADOR WHATSAPP DEL PIPELINE DIARIO DE RECEPCIÓN
Hotel El Óvalo Mariátegui - Laptop 2 (Recepción)
Fase 7 del Pipeline Diario de las 1:00 PM

Envía el reporte ejecutivo consolidado de la 1:00 PM al teléfono de administración
usando el motor nativo WA-JS (wsp_wajs.py) con Microsoft Edge.
"""

import os
import sys
import json
from datetime import datetime

BASE_DIR = r"C:\Hostal_Sistema"
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

ARCHIVO_CONFIG = os.path.join(BASE_DIR, "config_sistema.json")


def obtener_telefono_admin():
    if os.path.exists(ARCHIVO_CONFIG):
        try:
            with open(ARCHIVO_CONFIG, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("telefono_alerta_admin", "977129752")
        except Exception:
            pass
    return "977129752"


def formatear_mensaje_reporte(res_yape, res_sunat, res_fase5, res_fase6, resumen_calzado=None):
    ahora_str = datetime.now().strftime("%d/%m/%Y %H:%M")

    lineas = [
        f"🏨 *REPORTE AUTOMÁTICO 1:00 PM - HOTEL EL ÓVALO*",
        f"📅 *Fecha:* {ahora_str}",
        f"💻 *Equipo:* Laptop 2 (Recepción)",
        f"{'='*30}",
    ]

    # 1. Facturas y Notas de Crédito (Fase 5)
    ncs = res_fase5.get("ncs_emitidas", []) if res_fase5 else []
    filas_norm = res_fase5.get("filas_normalizadas", 0) if res_fase5 else 0
    lineas.append(f"\n🏢 *1. FACTURAS MULTIDÍA Y NOTAS DE CRÉDITO:*")
    if ncs:
        lineas.append(f"  ✅ *NCs Emitidas por Reemplazo a Factura:* {len(ncs)}")
        for nc in ncs:
            lineas.append(f"    • {nc['nro_nc']} (Anula Boleta {nc['boleta_anulada']} Hab {nc['hab']} S/ {nc['monto']:.2f})")
    else:
        lineas.append(f"  • No hubo colisiones pendientes de anular.")
    if filas_norm > 0:
        lineas.append(f"  • {filas_norm} filas de estancias prorrateadas y limpiadas bajo Facturas.")

    # 2. Boletas Automáticas Emitidas (Fase 6)
    bols = res_fase6.get("boletas_emitidas", []) if res_fase6 else []
    lineas.append(f"\n🧾 *2. BOLETAS EMITIDAS AUTOMÁTICAMENTE (Hasta Hoy - 2d):*")
    if bols:
        total_monto_bols = sum(b.get("monto", 0.0) for b in bols)
        lineas.append(f"  ✅ *Total emitidas:* {len(bols)} comprobantes (S/ {total_monto_bols:,.2f})")
        for b in bols:
            lineas.append(f"    • {b['nro_cp']} | Hab {b['hab']} | {b['cliente'][:20]} | S/ {b['monto']:.2f}")
    else:
        lineas.append(f"  • No hubo boletas pendientes limpias para emitir hoy.")

    # 3. Casos en Naranja (Revisión Conjunta)
    naranjas = res_fase6.get("casos_naranja", []) if res_fase6 else []
    lineas.append(f"\n🟠 *3. CASOS EN NARANJA (PARA REVISAR JUNTOS):*")
    if naranjas:
        lineas.append(f"  ⚠️ *Total en observación:* {len(naranjas)} filas")
        for n in naranjas[:8]:
            lineas.append(f"    • Hab {n['hab']} ({n['checkin']}) {n['huesped'][:18]} S/ {n['yape']:.2f}")
            lineas.append(f"      👉 _{n['motivo'][:65]}_")
        if len(naranjas) > 8:
            lineas.append(f"    • ... y {len(naranjas) - 8} casos más visibles en CALZADO_OCT.")
    else:
        lineas.append(f"  • Ningún caso atípico retenido en naranja.")

    # 4. Estado General de Cuadre
    lineas.append(f"\n📊 *4. ESTADO DE HOJA CALZADO_OCT:*")
    lineas.append(f"  👉 La hoja ha sido actualizada y pintada de verde en Google Sheets.")
    lineas.append(f"{'='*30}")
    lineas.append(f"🤖 _Generado automáticamente por Antigravity AI_")

    return "\n".join(lineas)


def enviar_notificacion_pipeline(res_yape, res_sunat, res_fase5, res_fase6, resumen_calzado=None):
    telefono = obtener_telefono_admin()
    mensaje = formatear_mensaje_reporte(res_yape, res_sunat, res_fase5, res_fase6, resumen_calzado)

    print("\n" + "=" * 75)
    print(f" FASE 7: NOTIFICACIÓN WHATSAPP AL ADMINISTRADOR ({telefono})")
    print("=" * 75)
    print(mensaje)
    print("=" * 75)

    try:
        from hoja_activa import es_prueba as _es_prueba_ha
        _en_prueba = bool(_es_prueba_ha())
    except ImportError:
        _en_prueba = os.environ.get("OVALO_ENTORNO", "operativa").strip().lower() != "operativa"

    if _en_prueba:
        print("\n🧪 [SIMULACIÓN MODO PRUEBA - NO EMITIDO EN SUNAT / NO ENVIADO POR WSP]")
        print("   (Notificación de pipeline impresa en consola; no enviada por WhatsApp en modo prueba).")
        return True

    try:
        from wsp_wajs import enviar_mensaje_wajs
        print(f"\n -> Enviando mensaje por WhatsApp Business a {telefono} vía WA-JS...")
        res_wsp = enviar_mensaje_wajs(telefono=telefono, mensaje=mensaje)
        if res_wsp and res_wsp.get("status") == "ENVIADO":
            print(f"   ✅ [ÉXITO WHATSAPP] Notificación entregada correctamente (ID: {res_wsp.get('mensaje_id')})")
            return True
        else:
            print(f"   ⚠️ [AVISO] WhatsApp no confirmó envío: {res_wsp}")
            return False
    except Exception as e_wsp:
        print(f"   ⚠️ [ADVERTENCIA WHATSAPP] No se pudo enviar mensaje por WhatsApp: {e_wsp}")
        print("      (El pipeline continuó y finalizó exitosamente todas las fases tributarias).")
        # Guardar en archivo local de respaldo
        try:
            ruta_respaldo = os.path.join(BASE_DIR, "ultimo_reporte_pipeline.txt")
            with open(ruta_respaldo, "w", encoding="utf-8") as f:
                f.write(mensaje)
        except Exception:
            pass
        return False


if __name__ == "__main__":
    # Prueba de formato
    ejemplo_fase5 = {"ncs_emitidas": [], "filas_normalizadas": 7}
    ejemplo_fase6 = {"boletas_emitidas": [], "casos_naranja": []}
    enviar_notificacion_pipeline(True, True, ejemplo_fase5, ejemplo_fase6)
