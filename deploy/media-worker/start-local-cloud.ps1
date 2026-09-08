$ErrorActionPreference = 'Stop'
$arcDir = Join-Path (Split-Path $PSScriptRoot -Parent) 'ArcReel'
$pythonPath = Join-Path $arcDir '.venv\Scripts\python.exe'
$backendScript = Join-Path $PSScriptRoot 'local_backend.py'
$logDir = Join-Path $arcDir 'deploy'
if (Get-NetTCPConnection -LocalPort 1241 -State Listen -ErrorAction SilentlyContinue) {
    throw 'ArcReel backend is already running. Stop that verified backend before changing its launch environment.'
}
$backend = Start-Process -FilePath $pythonPath -WorkingDirectory $arcDir -WindowStyle Hidden -PassThru `
    -ArgumentList @(('"' + $backendScript + '"')) `
    -RedirectStandardOutput (Join-Path $logDir 'native-backend.log') `
    -RedirectStandardError (Join-Path $logDir 'native-backend.err.log')
Write-Output "ArcReel backend PID: $($backend.Id)"
if (!(Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue)) {
    $frontend = Start-Process -FilePath (Get-Command node).Source -WorkingDirectory (Join-Path $arcDir 'frontend') -WindowStyle Hidden -PassThru `
        -ArgumentList @('node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', '5173', '--strictPort') `
        -RedirectStandardOutput (Join-Path $logDir 'native-frontend.log') `
        -RedirectStandardError (Join-Path $logDir 'native-frontend.err.log')
    Write-Output "ArcReel frontend PID: $($frontend.Id)"
}
Write-Output 'Open http://localhost:5173'
