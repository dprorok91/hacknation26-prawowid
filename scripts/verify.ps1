$ErrorActionPreference = 'Stop'
& python (Join-Path $PSScriptRoot 'codex_env.py') verify @args
exit $LASTEXITCODE
