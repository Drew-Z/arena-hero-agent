[CmdletBinding()]
param(
    [string]$PythonCommand,
    [switch]$NoUpgradePip
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPath = Join-Path $projectRoot ".venv"

if ([string]::IsNullOrWhiteSpace($PythonCommand)) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $PythonCommand = "py"
        $pythonPrefix = @("-3.11")
    }
    elseif (Get-Command python -ErrorAction SilentlyContinue) {
        $PythonCommand = "python"
        $pythonPrefix = @()
    }
    else {
        throw "Python 3.11 or newer is required."
    }
}
else {
    $pythonPrefix = @()
}

& $PythonCommand @pythonPrefix -c "import sys; raise SystemExit(sys.version_info < (3, 11))"
if ($LASTEXITCODE -ne 0) {
    throw "Python 3.11 or newer is required."
}

if (-not (Test-Path -LiteralPath $venvPath -PathType Container)) {
    & $PythonCommand @pythonPrefix -m venv $venvPath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the virtual environment."
    }
}

$venvPython = Join-Path $venvPath "Scripts\python.exe"
if (-not $NoUpgradePip) {
    & $venvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to upgrade pip."
    }
}
& $venvPython -m pip install --require-hashes -r (Join-Path $projectRoot "requirements-build.lock")
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install locked build dependencies."
}
& $venvPython -m pip install --require-hashes -r (Join-Path $projectRoot "requirements.lock")
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install locked runtime dependencies."
}
& $venvPython -m pip install --no-deps --no-build-isolation --editable $projectRoot
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install arena-hero-agent."
}
& $venvPython -m pip check
if ($LASTEXITCODE -ne 0) {
    throw "The installed dependency set is inconsistent."
}

Write-Host "Environment ready. Start with .\start_agent.ps1 or .\start_agent.cmd."
