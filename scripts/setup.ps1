$ErrorActionPreference = 'Stop'
& python (Join-Path $PSScriptRoot 'codex_env.py') setup @args
exit $LASTEXITCODE
