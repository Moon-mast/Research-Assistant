$ErrorActionPreference = "Stop"
$ws = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath("Desktop")
$lnkPath = Join-Path $desktop "科研小助手.lnk"
$lnk = $ws.CreateShortcut($lnkPath)
$lnk.TargetPath = "D:\my harness\.venv\Scripts\pythonw.exe"
$lnk.Arguments = '"D:\my harness\assistant_web.py"'
$lnk.WorkingDirectory = "D:\my harness"
$lnk.IconLocation = "D:\my harness\assets\assistant.ico,0"
$lnk.Description = "科研小助手 · AI 研究助理（论文库 / 问答 / 翻译 / 综述）"
$lnk.Save()
Write-Output "created: $lnkPath"
