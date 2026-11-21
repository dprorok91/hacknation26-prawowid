$ErrorActionPreference = 'Stop'
& python (Join-Path $PSScriptRoot 'codex_env.py') doctor @args
exit $LASTEXITCODE
