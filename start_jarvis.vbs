WScript.Sleep 3000
Set WshShell = CreateObject("WScript.Shell")
strPath = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
WshShell.CurrentDirectory = strPath
pythonwPath = strPath & "\venv\Scripts\pythonw.exe"
targetPath = strPath & "\supervisor.py"
If CreateObject("Scripting.FileSystemObject").FileExists(pythonwPath) Then
	WshShell.Run Chr(34) & pythonwPath & Chr(34) & " " & Chr(34) & targetPath & Chr(34), 0, False
Else
	WshShell.Run "cmd /c " & Chr(34) & Chr(34) & strPath & "\start_jarvis.bat" & Chr(34) & Chr(34), 1, False
End If
Set WshShell = Nothing
