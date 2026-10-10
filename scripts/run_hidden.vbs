' run_hidden.vbs - runs a batch file completely hidden and waits for it.
' Why: the Genesis scheduled tasks use InteractiveToken, which pops a visible
' console window on the user's desktop; when that window is closed the task
' dies with 0xC000013A (STATUS_CONTROL_C_EXIT) - the Sep 27-28 kill pattern.
' Usage: wscript.exe run_hidden.vbs "C:\path\to\script.bat"
Set sh = CreateObject("WScript.Shell")
If WScript.Arguments.Count <> 1 Then WScript.Quit 87
sh.Run """" & WScript.Arguments(0) & """", 0, True
