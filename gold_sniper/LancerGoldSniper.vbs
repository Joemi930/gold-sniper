Set WshShell = CreateObject("WScript.Shell")
strPath = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
WshShell.Run "cmd /c cd /d """ & strPath & """ && GoldSniper.bat", 0, False
