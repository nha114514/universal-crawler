param()
$ErrorActionPreference = 'Stop'
$taskProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskPython = Join-Path $taskProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Run build/Setup.ps1 before building.' }
Push-Location -LiteralPath $taskProjectRoot
try {
    & $taskPython -m PyInstaller --noconfirm --clean --onefile --noconsole --name Crawler --distpath dist --workpath build/pyinstaller --specpath build --exclude-module crawler_system --exclude-module httpx desktop.py
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
    Copy-Item -LiteralPath (Join-Path $taskProjectRoot 'dist\Crawler.exe') -Destination (Join-Path $taskProjectRoot 'Crawler.exe') -Force
    $taskShell = New-Object -ComObject WScript.Shell
    $taskShortcut = $taskShell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) '综合采集.lnk'))
    $taskShortcut.TargetPath = Join-Path $taskProjectRoot 'Crawler.exe'
    $taskShortcut.WorkingDirectory = $taskProjectRoot
    $taskShortcut.Description = '本地综合爬虫：网页、论文、视频与 MCP 资料库'
    $taskShortcut.Save()
    Get-FileHash -LiteralPath (Join-Path $taskProjectRoot 'Crawler.exe') -Algorithm SHA256
} finally { Pop-Location }
