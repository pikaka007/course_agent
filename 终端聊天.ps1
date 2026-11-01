param([ValidateSet('live','demo')][string]$Mode = 'live', [string]$Resume = '')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
$agentPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $agentPython)) { $agentPython = 'python' }
$agentArgs = @('app.py', 'chat', '--mode', $Mode)
if ($Resume) { $agentArgs += @('--resume', $Resume) }
& $agentPython @agentArgs
