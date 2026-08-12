[CmdletBinding()]
param(
    [string]$PythonPath,
    [string]$EnvFile = ".env",
    [string]$LogFile = "arena_farmer.log",
    [ValidateRange(1, 18)]
    [int]$WorkerTarget = 18,
    [ValidateSet("hold", "pursue", "retreat")]
    [string]$BeaconPolicy = "pursue",
    [string]$HistoryDb = "arena_history.sqlite3",
    [string]$BaseUrl,
    [ValidateRange(1, 65535)]
    [int]$DashboardPort = 8765,
    [switch]$NoDashboard,
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
$dashboardPath = Join-Path $projectRoot "arena_dashboard.py"
$envPath = Resolve-ProjectPath $EnvFile
$logPath = Resolve-ProjectPath $LogFile
$historyPath = Resolve-ProjectPath $HistoryDb
$dashboardUrl = "http://127.0.0.1:$DashboardPort/"
$dashboardLogPath = Join-Path $projectRoot "arena_dashboard.log"
$dashboardErrorLogPath = Join-Path $projectRoot "arena_dashboard.error.log"

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
$keyInFile = (Test-Path -LiteralPath $envPath -PathType Leaf) -and
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
    "--beacon-policy", $BeaconPolicy,
    "--history-db", $historyPath
)
if (-not [string]::IsNullOrWhiteSpace($BaseUrl)) {
    $agentArguments += @("--base-url", $BaseUrl)
}
if ($NoCompatibilityMarker) {
    $agentArguments += "--no-compatibility-marker"
}

function Test-DashboardReady {
    try {
        $requestParameters = @{
            UseBasicParsing = $true
            Uri = "${dashboardUrl}api/overview"
            TimeoutSec = 1
        }
        $response = Invoke-WebRequest @requestParameters
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

function Start-AgentDashboard {
    if (Test-DashboardReady) {
        Write-Host "Dashboard already running at $dashboardUrl"
        return $null
    }
    if (Get-NetTCPConnection -LocalPort $DashboardPort -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $DashboardPort is occupied by another process. Stop it or use -DashboardPort."
    }

    $arguments = @(
        ('"{0}"' -f $dashboardPath),
        "--history-db", ('"{0}"' -f $historyPath),
        "--host", "127.0.0.1",
        "--port", $DashboardPort
    )
    $startParameters = @{
        FilePath = $PythonPath
        ArgumentList = $arguments
        WorkingDirectory = $projectRoot
        WindowStyle = "Hidden"
        RedirectStandardOutput = $dashboardLogPath
        RedirectStandardError = $dashboardErrorLogPath
        PassThru = $true
    }
    $process = Start-Process @startParameters

    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        if ($process.HasExited) {
            throw "Dashboard stopped during startup. See $dashboardErrorLogPath"
        }
        if (Test-DashboardReady) {
            Write-Host "Dashboard running at $dashboardUrl"
            return $process
        }
        Start-Sleep -Milliseconds 250
    }
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    throw "Dashboard did not become ready. See $dashboardErrorLogPath"
}

Set-Location -LiteralPath $projectRoot
$stateDirectory = Join-Path $projectRoot "state"
[IO.Directory]::CreateDirectory($stateDirectory) | Out-Null
$lockPath = Join-Path $stateDirectory "windows-agent.lock"
try {
    $instanceLock = [IO.File]::Open(
        $lockPath,
        [IO.FileMode]::OpenOrCreate,
        [IO.FileAccess]::ReadWrite,
        [IO.FileShare]::None
    )
}
catch [IO.IOException] {
    Write-Host "Arena Hero Agent is already running. Use the existing CMD window."
    exit 2
}
$dashboardProcess = $null
try {
    if (-not $NoDashboard) {
        $dashboardProcess = Start-AgentDashboard
        try {
            Start-Process $dashboardUrl
        }
        catch {
            Write-Warning "Dashboard is running, but the browser could not be opened: $_"
        }
    }

    while ($true) {
        Invoke-AgentLogRotation
        $runStartedAt = Get-Date
        $previousErrorActionPreference = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            & $PythonPath @agentArguments 2>&1 |
                ForEach-Object -Process { "$_" } -ErrorAction Stop |
                Tee-Object -FilePath $logPath -Append -ErrorAction Stop
            $agentExitCode = $LASTEXITCODE
        }
        finally {
            $ErrorActionPreference = $previousErrorActionPreference
        }

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
}
finally {
    if ($null -ne $dashboardProcess -and -not $dashboardProcess.HasExited) {
        Stop-Process -Id $dashboardProcess.Id -Force -ErrorAction SilentlyContinue
        $dashboardProcess.WaitForExit()
    }
    $instanceLock.Dispose()
}

Write-Host "Agent stopped with exit code $agentExitCode."
exit $agentExitCode
