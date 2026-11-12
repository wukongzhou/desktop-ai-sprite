# 桌面精灵：创建 / 删除桌面图标与开机启动快捷方式
param([string]$Action = "install", [string]$Dir = $PSScriptRoot)
$startup = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\桌面精灵.lnk"
$desktop = Join-Path ([Environment]::GetFolderPath("Desktop")) "桌面精灵.lnk"
$shell = New-Object -ComObject WScript.Shell
if ($Action -eq "remove") {
    Remove-Item $startup -Force -ErrorAction SilentlyContinue
    exit 0
}
$icon = Join-Path $Dir "精灵.ico"
$sc = $shell.CreateShortcut($desktop)
$sc.TargetPath = Join-Path $Dir "启动精灵.bat"
$sc.WorkingDirectory = $Dir
$sc.IconLocation = $icon
$sc.Description = "桌面精灵"
$sc.Save()
$sc2 = $shell.CreateShortcut($startup)
$sc2.TargetPath = "pythonw.exe"
$sc2.Arguments = '"' + (Join-Path $Dir "desktop_pet.py") + '" --boot'
$sc2.WorkingDirectory = $Dir
$sc2.IconLocation = $icon
$sc2.Description = "桌面精灵（开机启动）"
$sc2.Save()
