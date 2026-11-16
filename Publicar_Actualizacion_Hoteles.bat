@echo off
chcp 65001 > nul
cd /d "%~dp0"

echo ========================================================
echo   PUBLICANDO CAMBIOS EN GITHUB DE PRUEBA (L1 ^<--^> L2)
echo   Repositorio: eduardocalaniguevara-del/ovalo_mariategui_prueba
echo ========================================================
python publicar_github.py "Actualizacion de entorno de prueba (8082)"
echo.
echo [OK] Cambios enviados al GitHub de Prueba. La otra laptop (8082)
echo      descargara la actualizacion automaticamente en ~20s.
pause
