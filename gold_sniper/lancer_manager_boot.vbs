' Demarrage differe au login Windows (45 s) puis PC Manager
DelaySeconds = 45
If WScript.Arguments.Count > 0 Then
    On Error Resume Next
    DelaySeconds = CInt(WScript.Arguments(0))
    On Error GoTo 0
End If
WScript.Sleep DelaySeconds * 1000

Set WshShell = CreateObject("WScript.Shell")
strPath = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
WshShell.Run "cmd /c cd /d """ & strPath & """ && LancerManager.bat", 0, False
