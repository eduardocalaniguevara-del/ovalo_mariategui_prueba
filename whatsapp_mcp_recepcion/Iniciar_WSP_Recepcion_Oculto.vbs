Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "C:\Recepcion_Ovalo_Local\whatsapp_mcp_recepcion"
WshShell.Run """C:\Program Files\nodejs\node.exe"" C:\Recepcion_Ovalo_Local\whatsapp_mcp_recepcion\wsp_daemon.js", 0, False
WshShell.Run """C:\Program Files\nodejs\node.exe"" C:\Recepcion_Ovalo_Local\whatsapp_mcp_recepcion\visor_web.js", 0, False
