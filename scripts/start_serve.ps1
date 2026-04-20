# Start the reloadable scheduler from the repo root. Point Task Scheduler at this script or at python.exe -m agent serve.
Set-Location (Split-Path -Parent $PSScriptRoot)
& (Join-Path $PSScriptRoot "..\venv\Scripts\python.exe") -m agent serve
