# 사용할 수 있는 Python으로 데모 DB를 준비하고 개발 서버를 실행합니다.
$ProjectRoot = $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$InstalledPython = Join-Path $env:LOCALAPPDATA "Programs\Python\Python314\python.exe"
$PythonCandidates = @()
$CommandPython = Get-Command python -ErrorAction SilentlyContinue

if ($CommandPython) {
    $PythonCandidates += $CommandPython.Source
}
if (Test-Path -LiteralPath $VenvPython) {
    $PythonCandidates += $VenvPython
}
if (Test-Path -LiteralPath $InstalledPython) {
    $PythonCandidates += $InstalledPython
}

$PythonPath = $null
foreach ($Candidate in $PythonCandidates | Select-Object -Unique) {
    try {
        & $Candidate -c "import django, dotenv" *> $null
        if ($LASTEXITCODE -eq 0) {
            $PythonPath = $Candidate
            break
        }
    }
    catch {
        continue
    }
}

if (-not $PythonPath) {
    Write-Error "Django 의존성이 설치된 Python을 찾지 못했습니다. .venv와 requirements.txt 설치 상태를 확인하세요."
    exit 1
}

$env:DEMO_MODE = "true"
$env:MYSQL_MOUNTED = "false"
$env:WELFARE_OPERATOR_ID = "demo-admin"
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

Push-Location -LiteralPath $ProjectRoot
try {
    & $PythonPath manage.py prepare_demo
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    & $PythonPath manage.py runserver
}
finally {
    Pop-Location
}
