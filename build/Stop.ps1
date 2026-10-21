$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskData = Join-Path $taskRoot 'data'
$taskLease = Join-Path $taskData 'service.json'
if (-not (Test-Path -LiteralPath $taskLease)) { Write-Output 'No project service has been started.'; return }
$taskInfo = Get-Content -LiteralPath $taskLease -Raw -Encoding UTF8 | ConvertFrom-Json
try {
    $taskHealth = Invoke-RestMethod -Uri ($taskInfo.url + '/api/health') -TimeoutSec 2
} catch { Write-Output 'Project service is already stopped.'; return }
if ($taskHealth.application -ne 'universal-crawler-v1' -or [IO.Path]::GetFullPath($taskHealth.data_directory) -ne $taskData) { throw 'This port does not belong to this project.' }
$taskSecret = [IO.File]::ReadAllText((Join-Path $taskData 'api-token')).Trim()
$taskHeaders = @{ Authorization = 'Bearer ' + $taskSecret }
Invoke-RestMethod -Method Post -Uri ($taskInfo.url + '/api/shutdown') -Headers $taskHeaders -TimeoutSec 5 | Out-Null
$taskStopped = $false
for ($taskAttempt = 0; $taskAttempt -lt 30; $taskAttempt++) {
    Start-Sleep -Milliseconds 200
    try { Invoke-RestMethod -Uri ($taskInfo.url + '/api/health') -TimeoutSec 1 | Out-Null }
    catch { $taskStopped = $true; break }
}
if (-not $taskStopped) { throw 'Service shutdown is still in progress. Check data/service.log.' }
Write-Output 'Project background service stopped.'
