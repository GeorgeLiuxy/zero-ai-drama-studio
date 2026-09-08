param([switch]$Reload)

$ErrorActionPreference = 'Stop'
$projectDir = $PSScriptRoot
$pythonPath = Join-Path $projectDir '.venv\Scripts\python.exe'
$nodePath = (Get-Command node).Source
$frontendDir = Join-Path $projectDir 'frontend'
$logDir = Join-Path $projectDir 'deploy'
if (!(Test-Path -LiteralPath $pythonPath)) { throw 'Run uv sync --python 3.12 --no-dev first.' }
if (!(Test-Path -LiteralPath (Join-Path $frontendDir 'node_modules\vite\bin\vite.js'))) {
    throw 'Run corepack pnpm install in frontend first.'
}
foreach ($port in @(1241, 5173)) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $port is already in use. Check the existing service before starting."
    }
}
$backendArgs = @('-m', 'uvicorn', 'server.app:app', '--host', '127.0.0.1', '--port', '1241')
if ($Reload) { $backendArgs += @('--reload', '--reload-dir', 'server', '--reload-dir', 'lib') }
$backend = Start-Process -FilePath $pythonPath -WorkingDirectory $projectDir -WindowStyle Hidden -PassThru `
    -ArgumentList $backendArgs `
    -RedirectStandardOutput (Join-Path $logDir 'native-backend.log') `
    -RedirectStandardError (Join-Path $logDir 'native-backend.err.log')
$frontend = Start-Process -FilePath $nodePath -WorkingDirectory $frontendDir -WindowStyle Hidden -PassThru `
    -ArgumentList @('node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', '5173', '--strictPort') `
    -RedirectStandardOutput (Join-Path $logDir 'native-frontend.log') `
    -RedirectStandardError (Join-Path $logDir 'native-frontend.err.log')
Write-Output "Backend PID: $($backend.Id); frontend PID: $($frontend.Id)"
Write-Output 'Open http://localhost:5173'
