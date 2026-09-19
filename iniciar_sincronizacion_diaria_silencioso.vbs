Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
ScriptDir = FSO.GetParentFolderName(WScript.ScriptFullName)
Cmd = "python """ & ScriptDir & "\scripts\daily_master_sync.py"""
WshShell.Run Cmd, 0, False
