param([switch]$Live)
$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
Push-Location $taskRoot
try {
    # Prior paused PageRank/discovery contract drafts are not production tests.
    $testModules = @(Get-ChildItem -LiteralPath (Join-Path $taskRoot 'tests') -Filter 'test_*.py' -File | Where-Object { $_.Name -notin @('test_discovery.py', 'test_ranking.py') } | ForEach-Object { 'tests.' + $_.BaseName })
    & $taskPython -m unittest @testModules -v
    if ($LASTEXITCODE -ne 0) { throw 'Regression tests failed.' }
    & $taskPython -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Dependency check failed.' }
    & node --check crawler_system/static/app.js
    if ($LASTEXITCODE -ne 0) { throw 'Frontend syntax check failed.' }
    if ($Live) {
        & $taskPython tests/verify_mcp.py --live
        if ($LASTEXITCODE -ne 0) { throw 'Live MCP verification failed.' }
        & $taskPython tests/check_media_search.py
        if ($LASTEXITCODE -ne 0) { throw 'Media/search verification failed.' }
        & $taskPython tests/verify_paper_search.py
        if ($LASTEXITCODE -ne 0) { throw 'Paper candidate verification failed.' }
        & $taskPython tests/verify_workflows.py
        if ($LASTEXITCODE -ne 0) { throw 'Download/crawl verification failed.' }
        & $taskPython tests/verify_errors.py
        if ($LASTEXITCODE -ne 0) { throw 'Error/cancellation verification failed.' }
        & $taskPython tests/verify_ui.py
        if ($LASTEXITCODE -ne 0) { throw 'Desktop web UI verification failed.' }
    }
    Write-Output 'Requested verification completed. Inspect evidence/ for individual live outcomes.'
} finally { Pop-Location }
