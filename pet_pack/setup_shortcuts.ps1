# Desktop AI Sprite: user-invoked shortcut and optional startup management.
param([string]$Action = "install", [string]$Dir = $PSScriptRoot)
$startup = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\桌面精灵.lnk"
$desktop = Join-Path ([Environment]::GetFolderPath("Desktop")) "桌面精灵.lnk"
if ($Action -eq "remove") {
    Remove-Item $startup -Force -ErrorAction SilentlyContinue
    exit 0
}
$shell = New-Object -ComObject WScript.Shell
$icon = Join-Path $Dir "精灵.ico"
$exe = Join-Path $Dir "DesktopPet.exe"
$launcher = Join-Path $Dir "启动精灵.bat"
if (Test-Path $exe) {
    $desktopTarget = $exe
} else {
    $desktopTarget = $launcher
}
$sc = $shell.CreateShortcut($desktop)
$sc.TargetPath = $desktopTarget
$sc.WorkingDirectory = $Dir
$sc.IconLocation = $icon
$sc.Description = "桌面 AI 精灵"
$sc.Save()

$sc2 = $shell.CreateShortcut($startup)
if (Test-Path $exe) {
    $sc2.TargetPath = $exe
    $sc2.Arguments = "--boot"
} else {
    $sc2.TargetPath = "pythonw.exe"
    $sc2.Arguments = '"' + (Join-Path $Dir "desktop_pet.py") + '" --boot'
}
$sc2.WorkingDirectory = $Dir
$sc2.IconLocation = $icon
$sc2.Description = "桌面精灵（用户主动启用的开机启动）"
$sc2.Save()
