param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
$agentPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $agentPython)) { $agentPython = 'python' }
Write-Host '启动后在网页“工作台设置”中配置接口、模型和密钥。项目 .env 优先于系统环境。'
& $agentPython app.py serve --port $Port
