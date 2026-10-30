$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    & .\.venv\Scripts\python.exe -m pip install -e '.[test]'
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
& .\.venv\Scripts\python.exe -m uvicorn lifeev.api:app --host 127.0.0.1 --port 8000
