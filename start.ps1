$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython = (Get-Command python -ErrorAction Stop).Source
$env:PYTHONIOENCODING = 'utf-8'
Write-Host 'Open http://localhost:8765 after the server starts. Keep this window open for daily updates.'
& $taskPython server.py
