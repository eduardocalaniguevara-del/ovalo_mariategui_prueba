@echo off
cd /d C:\Recepcion_Ovalo_Local
echo STOP > C:\Recepcion_Ovalo_Local\data\audios_noche\STOP_RECORDING.flag
timeout /t 3 /nobreak >nul
"C:\Users\ovalo mariategui\AppData\Local\Programs\Python\Python312\python.exe" C:\Recepcion_Ovalo_Local\auditor_audio_noche.py --compilar
pause
