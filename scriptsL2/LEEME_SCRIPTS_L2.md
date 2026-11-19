# `scriptsL2/` — Motores CLI de Recepción (Laptop 2)

Esta carpeta hermana contiene una copia limpia e idéntica de todos los motores `.py` y la plantilla `secretos_ejemplo.json` de `scriptsL1/` (**0 contraseñas, 0 claves SOL, 0 llaves Fernet, 0 tokens JWT**).

## Cómo funciona en Laptop 2 (Recepción)
- **Transporte automático por Git:** Se descarga automáticamente al hacer `git pull` del repositorio `ovalo_mariategui_prueba` (y posteriormente al promover a `app_local_recepcion`), sin depender de Syncthing ni copiar archivos manualmente.
- **Auto-selección por `config.py`:** Cuando la WebApp corre en Laptop 2 (detectando `C:\Users\ovalo mariategui`), `SCRIPTS_HOSTAL_DIR` apunta automáticamente a `scriptsL2/`. En Laptop 1 apunta a `scriptsL1/`.
- **Bóveda local exclusiva de Laptop 2:** `secretos_hostal.py` lee automáticamente las credenciales del **Hotel Grupo 2 (RUC `20611986484`)** desde **`C:\Hostal_SecretosL2\`** (fuera de Git y fuera de Syncthing):
  - `C:\Hostal_SecretosL2\credentials.json` (Google Service Account)
  - `C:\Hostal_SecretosL2\sunat_hostal.json` (`ruc`, `usuario_sol`, `clave_sol`, `apisperu_token`)
  - `C:\Hostal_SecretosL2\supabase_hostal.json` (`url`, `anon_key`, `sync_token`)
  - *(Ver formato de plantilla en `secretos_ejemplo.json` y validar con `python verificar_boveda.py`).*
- **Auto-relativo y blindado:** `hoja_activa.py` detecta automáticamente el entorno leyendo `../data/hotel_perfil.json` (y respetando `OVALO_ENTORNO`) manteniendo los candados duros anti-Hoja Operativa en modo prueba y anti-Hotel Terrazas.
