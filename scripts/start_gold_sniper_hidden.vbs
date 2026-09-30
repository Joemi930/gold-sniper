Set sh = CreateObject("WScript.Shell")
WScript.Sleep 30000
sh.CurrentDirectory = "C:\Users\tetej\Music\Bug bounty\Trading"
sh.Run """C:\Users\tetej\AppData\Local\Programs\Python\Python313\pythonw.exe"" ""C:\Users\tetej\Music\Bug bounty\Trading\scripts\gold_sniper_guard.py""", 0, False
