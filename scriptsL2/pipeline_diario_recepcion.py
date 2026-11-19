#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PIPELINE DIARIO AUTOMATIZADO - HOTEL EL ÓVALO MARIÁTEGUI
Laptop 2 (Recepción) / Laptop 1 (Controlador)

Horario de ejecución automática: 1:00 PM todos los días.
Mes procesado por defecto: NOVIEMBRE (--mes 11 -> SUNAT_NOV y CALZADO_NOV). Soporta --mes 10 para Octubre.
Flujo unificado completo (7 Fases):
  1. Conciliación Bancaria Yape/Plin (Drive -> CONCILIACION PAGOS YAPE + ALERTAS_AUDITORIA).
  2. Extracción Incremental SUNAT (Facturas, Boletas y Notas de Crédito de los últimos días).
  3. Construcción y normalización de la hoja SUNAT_NOV (o SUNAT_OCT con --mes 10).
  4. Calzado y triangulación hacia CALZADO_NOV (o CALZADO_OCT con --mes 10).
  5. [APAGADO POR DEFECTO - Requiere --habilitar-nc] Motor de Limpieza por Factura Multidía:
     - Detecta Facturas emitidas por la recepcionista.
     - Emite Notas de Crédito REALES en SUNAT para boletas previas solapadas.
     - Normaliza y prorratea las cuotas diarias en Registro_Estancias (ej. caso Jhon Masco).
  6. [APAGADO POR DEFECTO - Requiere --habilitar-emision] Emisor Automático de Boletas 'Rojos' de CALZADO_NOV / CALZADO_OCT:
     - Aplica ventana de desfase: Check-ins de hasta Hoy - 2 días.
     - Filtro Inteligente de Dudas -> Pinta de NARANJA (#FFC880) si monto diario > S/ 220,
       si tiene horas extras (Col T), bodega (Col S), RUC (Col AY), pago mixto o deuda.
     - Emite boletas REALES en SUNAT y descarga PDFs.
  7. Pintado Verde en Google Sheets y Notificación ejecutiva por WhatsApp al 977129752.

NOTA DE SEGURIDAD (18/11/2026): Los pasos 5 y 6 EMITEN comprobantes REALES en SUNAT y están
DESACTIVADOS por defecto por decisión de administración. Solo se ejecutan si se pasan
explícitamente los flags opt-in --habilitar-nc y/o --habilitar-emision.
"""

import os
import sys
import time
import argparse
import subprocess
from datetime import datetime

try:
    if sys.stdout is not None:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stderr is not None:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from pathlib import Path
SCRIPTS_DIR = Path(__file__).resolve().parent
DIR_SCRIPTS = str(SCRIPTS_DIR)
BASE_DIR = str(SCRIPTS_DIR.parent)
if DIR_SCRIPTS not in sys.path:
    sys.path.insert(0, DIR_SCRIPTS)
PYTHON_EXE = sys.executable


def _resolver_script(nombre_script: str) -> str:
    candidato_local = os.path.join(DIR_SCRIPTS, nombre_script)
    if os.path.exists(candidato_local):
        return candidato_local
    candidato_legacy = os.path.join(r"C:\Hostal_Sistema\scripts", nombre_script)
    if os.path.exists(candidato_legacy):
        return candidato_legacy
    return candidato_local


def ejecutar_comando(titulo, comando, timeout=600):
    print(f"\n{'='*70}")
    print(f" {titulo}")
    print(f"{'='*70}")
    t0 = time.time()
    try:
        proc = subprocess.run(
            comando,
            cwd=DIR_SCRIPTS,
            timeout=timeout,
            text=True,
            check=True
        )
        duracion = time.time() - t0
        print(f"   [OK] {titulo} completado en {duracion:.1f} segundos.")
        return True
    except subprocess.TimeoutExpired:
        print(f"   [ERROR CRÍTICO] {titulo} excedió el tiempo límite de {timeout}s.")
        return False
    except subprocess.CalledProcessError as e:
        print(f"   [ERROR] {titulo} finalizó con código de error {e.returncode}.")
        return False
    except Exception as e_gral:
        print(f"   [ERROR] No se pudo ejecutar {titulo}: {e_gral}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Pipeline Diario Hotel El Óvalo Mariátegui (1:00 PM)")
    parser.add_argument("--saltar-yape", action="store_true", help="Omitir la conciliación de Yape")
    parser.add_argument("--saltar-sunat", action="store_true", help="Omitir la extracción de SUNAT")
    parser.add_argument(
        "--habilitar-nc",
        action="store_true",
        help="Habilitar Paso 5 (EMITE Notas de Crédito REALES en SUNAT; apagado por defecto por decisión admin 18/11/2026)",
    )
    parser.add_argument(
        "--habilitar-emision",
        action="store_true",
        help="Habilitar Paso 6 (EMITE Boletas REALES en SUNAT; apagado por defecto por decisión admin 18/11/2026)",
    )
    parser.add_argument(
        "--saltar-nc",
        action="store_true",
        help="[Compatibilidad legado - sin efecto] Paso 5 ya está apagado por defecto (usar --habilitar-nc para activarlo)",
    )
    parser.add_argument(
        "--saltar-emision",
        action="store_true",
        help="[Compatibilidad legado - sin efecto] Paso 6 ya está apagado por defecto (usar --habilitar-emision para activarlo)",
    )
    parser.add_argument("--saltar-wsp", action="store_true", help="Omitir el envío del reporte por WhatsApp")
    parser.add_argument(
        "--mes",
        type=int,
        default=11,
        choices=[10, 11],
        help="Mes a procesar: 11 (Noviembre, por defecto) o 10 (Octubre)",
    )
    parser.add_argument("--dias", type=int, default=4, help="Días hacia atrás para la extracción de SUNAT (defecto: 4)")
    parser.add_argument("--desfase", type=int, default=2, help="Días de desfase para la emisión de boletas (defecto: 2 días antes de hoy)")
    parser.add_argument("--headless", action="store_true", default=True, help="Ejecutar navegadores en segundo plano (defecto: True)")
    parser.add_argument("--no-headless", action="store_false", dest="headless", help="Mostrar ventana visible de navegador")
    parser.add_argument("--completo", action="store_true", help="Ejecutar SUNAT en modo mes completo")

    args = parser.parse_args()

    es_nov = (args.mes == 11)
    nombre_mes_lbl = "NOVIEMBRE" if es_nov else "OCTUBRE"
    hoja_sunat = "SUNAT_NOV" if es_nov else "SUNAT_OCT"
    hoja_calzado = "CALZADO_NOV" if es_nov else "CALZADO_OCT"
    script_extraccion = "Extraccion_Noviembre_Completa.py" if es_nov else "Extraccion_Octubre_Completa.py"
    script_construir = "Construir_SUNAT_NOV.py" if es_nov else "Construir_SUNAT_OCT.py"
    script_calzar = "Calzar_Noviembre.py" if es_nov else "Calzar_Octubre.py"

    hora_inicio = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    print(f"\n######################################################################")
    print(f"  INICIANDO PIPELINE DIARIO DE AUDITORÍA Y TRIBUTACIÓN ({hora_inicio})")
    print(f"  HOTEL EL ÓVALO MARIÁTEGUI - MES ACTIVO: {nombre_mes_lbl} ({hoja_sunat} / {hoja_calzado})")
    print(f"######################################################################\n")

    exitos = []

    # PASO 1: CONCILIACIÓN BANCARIA YAPE
    if not args.saltar_yape:
        cmd_yape = [PYTHON_EXE, _resolver_script("auditor_yape.py")]
        ok_yape = ejecutar_comando("PASO 1/7: AUDITORÍA Y CONCILIACIÓN YAPE BANCARIO", cmd_yape, timeout=300)

        # PASO 1.5: RESOLUCIÓN INTELIGENTE CON CRITERIO (Terceros, Adicionales, Tienda, Deduplicación)
        cmd_criterio = [PYTHON_EXE, _resolver_script("conciliar_con_criterio.py")]
        ok_crit = ejecutar_comando("PASO 1.5: CONCILIACIÓN CON CRITERIO ANALÍTICO Y DEDUPLICACIÓN", cmd_criterio, timeout=180)
        exitos.append(("Conciliación Yape", ok_yape and ok_crit))
    else:
        print("\n[INFO] Paso 1 (Yape) omitido por parámetro --saltar-yape.")

    # PASO 2: EXTRACCIÓN INCREMENTAL SUNAT
    if not args.saltar_sunat:
        cmd_sunat = [PYTHON_EXE, _resolver_script(script_extraccion), "--dias", str(args.dias)]
        if args.headless:
            cmd_sunat.append("--headless")
        if args.completo:
            cmd_sunat.append("--completo")
        ok_sunat = ejecutar_comando(f"PASO 2/7: EXTRACCIÓN INCREMENTAL SUNAT {nombre_mes_lbl} (FAC, BOL, NC)", cmd_sunat, timeout=600)
        exitos.append((f"Extracción SUNAT ({nombre_mes_lbl})", ok_sunat))
    else:
        print("\n[INFO] Paso 2 (SUNAT) omitido por parámetro --saltar-sunat.")

    # PASO 3: CONSTRUIR SUNAT_NOV / SUNAT_OCT
    cmd_construir = [PYTHON_EXE, _resolver_script(script_construir)]
    ok_construir = ejecutar_comando(f"PASO 3/7: CONSTRUCCIÓN Y NORMALIZACIÓN DE {hoja_sunat}", cmd_construir, timeout=300)
    exitos.append((f"Construcción {hoja_sunat}", ok_construir))

    # PASO 4: CALZAR NOVIEMBRE / OCTUBRE
    cmd_calzar = [PYTHON_EXE, _resolver_script(script_calzar)]
    ok_calzar = ejecutar_comando(f"PASO 4/7: CALZADO TRIBUTARIO-OPERATIVO ({hoja_calzado})", cmd_calzar, timeout=300)
    exitos.append((f"Calzado {hoja_calzado}", ok_calzar))

    # PASO 5: MOTOR DE LIMPIEZA POR FACTURA MULTIDÍA Y EMISIÓN DE NC (OPT-IN, APAGADO POR DEFECTO)
    res_fase5 = {}
    if args.habilitar_nc:
        try:
            from motor_limpieza_facturas_nc import ejecutar_limpieza_facturas
            res_fase5 = ejecutar_limpieza_facturas(modo_emision="definitivo", headless=args.headless, mes=args.mes)
            exitos.append(("Limpieza Facturas y NC", True))
        except Exception as e_f5:
            print(f"   [ERROR] En Fase 5 (Limpieza Facturas): {e_f5}")
            exitos.append(("Limpieza Facturas y NC", False))
    else:
        print("\n[INFO] Paso 5 DESACTIVADO por administración (usar --habilitar-nc).")

    # PASO 6: EMISIÓN AUTOMÁTICA DE ROJOS CON FILTRO NARANJA (OPT-IN, APAGADO POR DEFECTO)
    res_fase6 = {}
    if args.habilitar_emision:
        try:
            from emisor_automatico_rojos import evaluar_emision_automatica
            res_fase6 = evaluar_emision_automatica(dias_desfase=args.desfase, modo="definitivo", headless=args.headless, mes=args.mes)
            exitos.append(("Emisión Automática Boletas", True))
        except Exception as e_f6:
            print(f"   [ERROR] En Fase 6 (Emisión Automática Boletas): {e_f6}")
            exitos.append(("Emisión Automática Boletas", False))
    else:
        print("\n[INFO] Paso 6 DESACTIVADO por administración (usar --habilitar-emision).")

    # PASO 7: PINTAR VERDE
    cmd_pintar = [PYTHON_EXE, _resolver_script("pintar_verde_emitidas.py"), "--mes", str(args.mes)]
    ok_pintar = ejecutar_comando(f"PASO 7/7: PINTADO DE VERDE EN GOOGLE SHEETS ({hoja_calzado})", cmd_pintar, timeout=180)
    exitos.append((f"Pintado Verde ({hoja_calzado})", ok_pintar))

    # NOTIFICACIÓN WHATSAPP AL ADMINISTRADOR
    if not args.saltar_wsp:
        try:
            import notificador_wsp_pipeline as _notif_mod
            if not args.habilitar_nc or not args.habilitar_emision:
                _orig_fmt = _notif_mod.formatear_mensaje_reporte

                def _fmt_con_aviso_admin(ry, rs, rf5, rf6, rc=None):
                    msg = _orig_fmt(ry, rs, rf5, rf6, rc)
                    aviso = "  • Emisión automática DESACTIVADA por administración."
                    if not args.habilitar_nc:
                        msg = msg.replace("  • No hubo colisiones pendientes de anular.", aviso)
                    if not args.habilitar_emision:
                        msg = msg.replace("  • No hubo boletas pendientes limpias para emitir hoy.", aviso)
                    return msg

                _notif_mod.formatear_mensaje_reporte = _fmt_con_aviso_admin
            _notif_mod.enviar_notificacion_pipeline(True, True, res_fase5, res_fase6)
        except Exception as e_wsp:
            print(f"   [WARN] Error enviando reporte por WhatsApp: {e_wsp}")

    # RESUMEN EJECUTIVO EN CONSOLA
    hora_fin = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    print(f"\n\n{'#'*70}")
    print(f"  RESUMEN EJECUTIVO DEL PIPELINE DIARIO ({hora_fin})")
    print(f"{'#'*70}")
    for nombre, resultado in exitos:
        estado_str = "✅ COMPLETADO CON ÉXITO" if resultado else "❌ FALLÓ / REVISAR"
        print(f"  - {nombre:<30}: {estado_str}")
    print(f"{'#'*70}\n")
    print(f"👉 Hojas '{hoja_calzado}' y 'Registro_Estancias' actualizadas en Google Sheets.")
    print("   Casos en Naranja listados para revisión conjunta.")
    print(f"{'#'*70}\n")


if __name__ == "__main__":
    main()
