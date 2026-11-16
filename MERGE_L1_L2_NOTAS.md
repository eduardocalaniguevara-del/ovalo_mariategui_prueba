# Fusión L1 (Laptop 1) + L2 (Laptop 2) — 2026-11-16

Repositorio: `eduardocalaniguevara-del/ovalo_mariategui_prueba` (rama `main`).
Respaldo previo completo de L2: `C:\Hostal_Sistema\respaldos_promocion\backup_prueba_L2_pre_git_merge`.
Las dos historias quedan unidas con un commit de fusión (`--allow-unrelated-histories`): ninguna línea de L1 se pierde del historial.

## Decisión por archivo (20 archivos divergentes; 101 eran idénticos)

| Archivo | Resultado | Motivo |
|---|---|---|
| `auto_updater.py`, `publicar_github.py`, `Publicar_Actualizacion_Hoteles.bat`, `leemeantrigravityrecepcion.md` | **L1** | Canal Git exclusivo de prueba (L1 es superconjunto) |
| `.agent/harness/guardrails/safety_rules.json` | **L1** (v2.1.0, GR-01..GR-13) | L2 solo tenía versión/fecha anteriores |
| `arnes_recepcion/.agent/harness/guardrails/safety_rules.json` | **L1 + regla L2** | La regla del Copiloto (GR-09 de L2) chocaba con "Ley 1" (GR-09 de L1): se conserva como **GR-14** (alias "GR-09 Copiloto Óvalo (Laptop 2)"); v2.2.0 |
| `arnes_recepcion/.agent/harness/manifest.json` | **L1 + regla L2** | Igual: entrada GR-14 con alias |
| `.gitignore` | **Unión** | L1 (`.playwright-cli/`, `test-results/`, `tests/screenshots/`) + L2 (`.env`, `auth_info/`, `media/`, `.venv_whisper/`, `node_modules/`, `scratch/`, `qr_*.png`) |
| `LEEME_PRUEBA.txt` | **Unión** | Ambas rutas de operativa |
| `main.py`, `database.py`, `domain_logic.py`, `supabase_sync.py`, `contabilidad_logic.py`, `auditor_calzado_bcp_sunat.py` | **L2** | L2 = L1 + líneas adicionales (0 líneas exclusivas de L1) |
| `static/PanelDisponibilidad.html` | **L2** | Solo difería en la barra superior + visor WSP + modal (+1262 líneas) |
| `pases_logic.py` | **L2** | La versión L1 es idéntica al ancestro común (verificado por hash) (`instalador_app_recepcion`); L2 la modificó (corte de mediodía + `from pathlib import Path`) |
| `promover_a_operativa.py` | **L2** | Exclusiones `auth_info`, `media`, `.venv_whisper` |
| `yape_manager.py` | **L2** | L2 = versión operativa más nueva (criterios A/B/C de conciliación); las 12 líneas de L1 son la iteración previa de los mismos bloques |
| `auditor_audio_noche.py` | **L2** | Verificado con el ancestro común (`instalador_app_recepcion`): la versión L1 es **idéntica** al ancestro (L1 no la modificó), así que la versión L2 es el resultado correcto de la fusión a 3 vías; no se pierde nada de L1. |

## Pendiente de decisión humana
- `data/wsp_patrones_entrenados.json` vive en `data/` (ignorado por git: configuración por laptop); si no existe, `wsp_inteligencia.py` usa su diccionario base embebido y se regenera con `python scripts_wsp/minar_patrones_wsp.py`.
