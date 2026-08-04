' Launches run_daily.bat with no console window.
'
' Task Scheduler runs a .bat in a visible cmd window, which pops up over
' whatever you are doing -- once per trigger, and again for every retry. This
' wrapper starts the same batch hidden (window style 0) and waits for it
' (True), so the scheduled task still reports the batch's real exit code.
'
' Task action:  wscript.exe  "<this file>"

Dim shell, fso, here
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
here = fso.GetParentFolderName(WScript.ScriptFullName)

shell.CurrentDirectory = here
WScript.Quit shell.Run("""" & here & "\run_daily.bat""", 0, True)
