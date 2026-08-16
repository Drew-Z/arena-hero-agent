[CmdletBinding()]
param(
    [string]$PythonPath,
    [string]$EnvFile = ".env",
    [string]$LogFile = "arena_farmer.log",
    [ValidateRange(1, 18)]
    [int]$WorkerTarget = 18,
    [ValidateSet("hold", "pursue", "retreat")]
    [string]$BeaconPolicy = "retreat",
    [string]$BaseUrl,
    [switch]$NoCompatibilityMarker
)

$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$transientExitCode = 75
$retryDelaySeconds = 2
$maximumRetryDelaySeconds = 30
$maximumLogBytes = 5MB
$logBackupCount = 3

function Resolve-ProjectPath {
    param([Parameter(Mandatory)][string]$Value)

    if ([IO.Path]::IsPathRooted($Value)) {
        return [IO.Path]::GetFullPath($Value)
    }
    return [IO.Path]::GetFullPath((Join-Path $projectRoot $Value))
}

if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $PythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
}
else {
    $PythonPath = Resolve-ProjectPath $PythonPath
}
$agentPath = Join-Path $projectRoot "arena_farmer.py"
$envPath = Resolve-ProjectPath $EnvFile
$logPath = Resolve-ProjectPath $LogFile

function Invoke-AgentLogRotation {
    if (-not (Test-Path -LiteralPath $logPath)) {
        return
    }
    if ((Get-Item -LiteralPath $logPath).Length -lt $maximumLogBytes) {
        return
    }

    $oldestBackup = "$logPath.$logBackupCount"
    if (Test-Path -LiteralPath $oldestBackup) {
        Remove-Item -LiteralPath $oldestBackup -Force
    }
    for ($index = $logBackupCount - 1; $index -ge 1; $index--) {
        $source = "$logPath.$index"
        if (Test-Path -LiteralPath $source) {
            Move-Item -LiteralPath $source -Destination "$logPath.$($index + 1)" -Force
        }
    }
    Move-Item -LiteralPath $logPath -Destination "$logPath.1" -Force
}

if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Python environment is missing. Run .\scripts\bootstrap.ps1 first. Expected: $PythonPath"
}

$keyInEnvironment = -not [string]::IsNullOrWhiteSpace($env:ARENA_HERO_API_KEY)
$keyInFile = Test-Path -LiteralPath $envPath -PathType Leaf -and
    (Select-String -LiteralPath $envPath -Pattern '^\s*ARENA_HERO_API_KEY\s*=\s*\S+' -Quiet) -and
    -not (Select-String -LiteralPath $envPath -Pattern '^\s*ARENA_HERO_API_KEY\s*=\s*(replace-with|your-|<)' -Quiet)
if (-not $keyInEnvironment -and -not $keyInFile) {
    Write-Host "No Arena Hero API key was found. The key will be appended to $envPath."
    $secureKey = Read-Host "Enter the current Arena Hero API key" -AsSecureString
    $keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
    try {
        $plainKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
        if ([string]::IsNullOrWhiteSpace($plainKey)) {
            throw "API key cannot be empty."
        }
        $parent = Split-Path -Parent $envPath
        if ($parent) {
            [IO.Directory]::CreateDirectory($parent) | Out-Null
        }
        $existing = if (Test-Path -LiteralPath $envPath) {
            [IO.File]::ReadAllText($envPath)
        }
        else {
            ""
        }
        if ($existing.Length -gt 0 -and -not $existing.EndsWith([Environment]::NewLine)) {
            $existing += [Environment]::NewLine
        }
        [IO.File]::WriteAllText(
            $envPath,
            $existing + "ARENA_HERO_API_KEY=$($plainKey.Trim())" + [Environment]::NewLine,
            [Text.UTF8Encoding]::new($false)
        )
    }
    finally {
        $plainKey = $null
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
    }
}

$agentArguments = @(
    $agentPath,
    "--env-file", $envPath,
    "--worker-target", $WorkerTarget,
    "--beacon-policy", $BeaconPolicy
)
if (-not [string]::IsNullOrWhiteSpace($BaseUrl)) {
    $agentArguments += @("--base-url", $BaseUrl)
}
if ($NoCompatibilityMarker) {
    $agentArguments += "--no-compatibility-marker"
}

Set-Location -LiteralPath $projectRoot
while ($true) {
    Invoke-AgentLogRotation
    $runStartedAt = Get-Date
    & $PythonPath @agentArguments 2>&1 | Tee-Object -FilePath $logPath -Append
    $agentExitCode = $LASTEXITCODE

    if ($agentExitCode -ne $transientExitCode) {
        break
    }

    if (((Get-Date) - $runStartedAt).TotalMinutes -ge 5) {
        $retryDelaySeconds = 2
    }
    Write-Warning "Transient Agent failure. Restarting in $retryDelaySeconds seconds."
    Start-Sleep -Seconds $retryDelaySeconds
    $retryDelaySeconds = [Math]::Min(
        $maximumRetryDelaySeconds,
        $retryDelaySeconds * 2
    )
}

Write-Host "Agent stopped with exit code $agentExitCode."
exit $agentExitCode
