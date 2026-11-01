$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$agentPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $agentPython)) { $agentPython = 'python' }
& $agentPython -m pip install --index-url https://pypi.org/simple -r requirements-lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
Write-Host '终端与PDF依赖已安装。双击 终端聊天.bat 或 启动.bat 开始。'
