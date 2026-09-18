Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = scriptDir
pyPath = "C:\Python314\python.exe"
If Not fso.FileExists(pyPath) Then
    pyPath = "python"
End If
WshShell.Run """" & pyPath & """ -u scripts\zendesk_hourly_worker.py", 0, False
