# `scripts/` — Motores CLI Únicos del Hostal (Estándar Universal para Todas las Laptops)

Esta carpeta única dentro del repositorio Git contiene los 27 archivos limpios (motores `.py` + plantilla `secretos_ejemplo.json`) compartidos por **Laptop 1 (Administración)**, **Laptop 2 (Recepción)** y cualquier laptop futura (**0 contraseñas, 0 claves SOL, 0 llaves Fernet, 0 tokens JWT**).

## Estándar Universal (Cero Duplicados)
- **Código único por Git (`scripts/`):** Todas las laptops descargan exactamente el mismo código con `git pull` (canal único GitHub `ovalo_mariategui_prueba` y, al promover, `app_local_recepcion`), sin Syncthing ni carpetas duplicadas por equipo.
- **Bóveda local única (`C:\Hostal_Secretos\`):** Cada laptop guarda fuera de Git y fuera de Syncthing sus 3 archivos exclusivos del **Hotel Grupo 2 (RUC `20611986484`)**:
  - `C:\Hostal_Secretos\credentials.json` (Google Service Account)
  - `C:\Hostal_Secretos\sunat_hostal.json` (`ruc`, `usuario_sol`, `clave_sol`, `apisperu_token`)
  - `C:\Hostal_Secretos\supabase_hostal.json` (`url`, `anon_key`, `sync_token`)
  - *(Ver plantilla en `secretos_ejemplo.json` y validar siempre con `python scripts/verificar_boveda.py`).*
- **Auto-relativo y blindado:** `hoja_activa.py` detecta automáticamente el entorno leyendo `../data/hotel_perfil.json` (y respetando `OVALO_ENTORNO`), manteniendo los candados duros anti-Hoja Operativa en modo prueba y anti-Hotel Terrazas.
