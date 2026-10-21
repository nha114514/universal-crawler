param(
    [Parameter(Mandatory = $true)][string]$PythonExecutable,
    [string]$Wheelhouse = 'D:\Dev\project\crawler-sources\build\wheelhouse\crawler-runtime',
    [string]$BrowserCache = 'D:\Dev\project\crawler-sources\build\browser-cache'
)
$ErrorActionPreference = 'Stop'
$taskProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskVenv = Join-Path $taskProjectRoot '.venv'
$taskPython = Join-Path $taskVenv 'Scripts\python.exe'
if (-not (Test-Path -LiteralPath $PythonExecutable)) { throw 'Python executable does not exist. Specify -PythonExecutable.' }
$taskVersion = & $PythonExecutable -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0 -or $taskVersion -ne '3.12') { throw 'Python 3.12 is required.' }
if (-not (Test-Path -LiteralPath $taskVenv)) {
    & $PythonExecutable -m venv $taskVenv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create Python environment.' }
}
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Existing .venv is not a valid environment; preserve it and choose a clean project copy.' }
if (-not (Test-Path -LiteralPath $Wheelhouse)) { throw 'Offline wheelhouse does not exist. Specify -Wheelhouse.' }
& $taskPython -m pip install --no-index --find-links $Wheelhouse -r (Join-Path $taskProjectRoot 'requirements.lock.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
& $taskPython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Dependency check failed.' }
if (-not (Test-Path -LiteralPath $BrowserCache)) { throw 'Browser cache does not exist. Specify -BrowserCache.' }
$taskData = Join-Path $taskProjectRoot 'data'
New-Item -ItemType Directory -Path $taskData -Force | Out-Null
$taskSettingsPath = Join-Path $taskData 'settings.json'
if (Test-Path -LiteralPath $taskSettingsPath) {
    $taskConfig = Get-Content -LiteralPath $taskSettingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $taskConfig | Add-Member -NotePropertyName browser_cache -NotePropertyValue ([IO.Path]::GetFullPath($BrowserCache)) -Force
    [IO.File]::WriteAllText($taskSettingsPath, ($taskConfig | ConvertTo-Json -Depth 30), (New-Object Text.UTF8Encoding($false)))
} else {
    [IO.File]::WriteAllText($taskSettingsPath, (@{browser_cache=[IO.Path]::GetFullPath($BrowserCache)} | ConvertTo-Json), (New-Object Text.UTF8Encoding($false)))
}
& $taskPython -c "from scrapling.fetchers import Fetcher, DynamicFetcher; import yt_dlp, pypdf, mcp, fastapi; print('Imports OK')"
if ($LASTEXITCODE -ne 0) { throw 'Required imports failed.' }
Write-Output 'Setup complete. Run Start-Crawler.cmd or build/Build.ps1.'
