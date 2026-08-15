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
    [switch]$NoCompatibilityMarker,
    [string]$AllianceRosterUrl,
    [string]$AllianceRosterTokenFile,
    [ValidateRange(0.1, 3600.0)]
    [double]$AllianceRosterRefreshSeconds = 15.0,
    [ValidateRange(0.1, 60.0)]
    [double]$AllianceRosterTimeoutSeconds = 5.0,
    [string]$SecondaryEnvFile,
    [string]$SecondaryLogFile = "arena_farmer.secondary.log",
    [string]$SecondaryHistoryDb = "arena_history.secondary.sqlite3",
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$')]
    [string]$InstanceName = "primary",
    [switch]$EnvFileOnly
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
$instanceSuffix = if ($InstanceName -eq "primary") { "" } else { ".$InstanceName" }
$dashboardLogPath = Join-Path $projectRoot "arena_dashboard$instanceSuffix.log"
$dashboardErrorLogPath = Join-Path $projectRoot "arena_dashboard$instanceSuffix.error.log"
$localAllianceDirectory = Join-Path $projectRoot "state\local-alliance"
$localAllianceId = "local-duo"
$hasSecondary = -not [string]::IsNullOrWhiteSpace($SecondaryEnvFile)
$localAllianceEnabled = $hasSecondary -or $InstanceName -eq "secondary"
$allianceExpectedMembers = if ($localAllianceEnabled) { 2 } else { 1 }
$secondaryEnvPath = if ($hasSecondary) { Resolve-ProjectPath $SecondaryEnvFile } else { $null }
$secondaryLogPath = if ($hasSecondary) { Resolve-ProjectPath $SecondaryLogFile } else { $null }
$secondaryHistoryPath = if ($hasSecondary) { Resolve-ProjectPath $SecondaryHistoryDb } else { $null }
if ($hasSecondary -and (
    $secondaryEnvPath -eq $envPath -or
    $secondaryLogPath -eq $logPath -or
    $secondaryHistoryPath -eq $historyPath
)) {
    throw "Secondary account files must be separate from the primary account files."
}
$hasAllianceRosterUrl = -not [string]::IsNullOrWhiteSpace($AllianceRosterUrl)
$hasAllianceRosterTokenFile = -not [string]::IsNullOrWhiteSpace($AllianceRosterTokenFile)
if ($hasAllianceRosterUrl -ne $hasAllianceRosterTokenFile) {
    throw "Alliance roster URL and token file must be configured together."
}
$allianceRosterTokenPath = $null
if ($hasAllianceRosterTokenFile) {
    $allianceRosterTokenPath = Resolve-ProjectPath $AllianceRosterTokenFile
    if (-not (Test-Path -LiteralPath $allianceRosterTokenPath -PathType Leaf)) {
        throw "Alliance roster token file is missing: $allianceRosterTokenPath"
    }
}

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

function Test-ApiKeyFile {
    param([Parameter(Mandatory)][string]$Path)

    return (Test-Path -LiteralPath $Path -PathType Leaf) -and
        (Select-String -LiteralPath $Path -Pattern '^\s*ARENA_HERO_API_KEY\s*=\s*\S+' -Quiet) -and
        -not (Select-String -LiteralPath $Path -Pattern '^\s*ARENA_HERO_API_KEY\s*=\s*(replace-with|your-|<)' -Quiet)
}

function Initialize-ApiKeyFile {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Label
    )

    Write-Host "No $Label Arena Hero API key was found. It will be appended to $Path."
    $secureKey = Read-Host "Enter the $Label Arena Hero API key" -AsSecureString
    $keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
    try {
        $plainKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
        if ([string]::IsNullOrWhiteSpace($plainKey)) {
            throw "API key cannot be empty."
        }
        $parent = Split-Path -Parent $Path
        if ($parent) {
            [IO.Directory]::CreateDirectory($parent) | Out-Null
        }
        $existing = if (Test-Path -LiteralPath $Path) {
            [IO.File]::ReadAllText($Path)
        }
        else {
            ""
        }
        if ($existing.Length -gt 0 -and -not $existing.EndsWith([Environment]::NewLine)) {
            $existing += [Environment]::NewLine
        }
        [IO.File]::WriteAllText(
            $Path,
            $existing + "ARENA_HERO_API_KEY=$($plainKey.Trim())" + [Environment]::NewLine,
            [Text.UTF8Encoding]::new($false)
        )
    }
    finally {
        $plainKey = $null
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
    }
}

if ($EnvFileOnly) {
    Remove-Item Env:ARENA_HERO_API_KEY -ErrorAction SilentlyContinue
}
$keyInEnvironment = -not $EnvFileOnly -and
    -not [string]::IsNullOrWhiteSpace($env:ARENA_HERO_API_KEY)
if (-not $keyInEnvironment -and -not (Test-ApiKeyFile $envPath)) {
    Initialize-ApiKeyFile $envPath $InstanceName
}
if ($hasSecondary -and -not (Test-ApiKeyFile $secondaryEnvPath)) {
    Initialize-ApiKeyFile $secondaryEnvPath "secondary"
}

$agentArguments = @(
    $agentPath,
    "--env-file", $envPath,
    "--worker-target", $WorkerTarget,
    "--beacon-policy", $BeaconPolicy,
    "--history-db", $historyPath
)
if ($localAllianceEnabled) {
    $agentArguments += @(
        "--alliance-directory", $localAllianceDirectory,
        "--alliance-id", $localAllianceId,
        "--alliance-account-id", $InstanceName,
        "--alliance-expected-members", $allianceExpectedMembers
    )
}
if (-not [string]::IsNullOrWhiteSpace($BaseUrl)) {
    $agentArguments += @("--base-url", $BaseUrl)
}
if ($NoCompatibilityMarker) {
    $agentArguments += "--no-compatibility-marker"
}
if ($hasAllianceRosterUrl) {
    $agentArguments += @(
        "--alliance-roster-url", $AllianceRosterUrl.Trim(),
        "--alliance-roster-token-file", $allianceRosterTokenPath,
        "--alliance-roster-refresh-seconds",
        $AllianceRosterRefreshSeconds.ToString([Globalization.CultureInfo]::InvariantCulture),
        "--alliance-roster-timeout-seconds",
        $AllianceRosterTimeoutSeconds.ToString([Globalization.CultureInfo]::InvariantCulture)
    )
}

function Test-DashboardReady {
    try {
        $requestParameters = @{
            UseBasicParsing = $true
            Uri = "${dashboardUrl}api/overview?history=0"
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
    if ($localAllianceEnabled) {
        $arguments += @(
            "--alliance-directory", ('"{0}"' -f $localAllianceDirectory),
            "--alliance-account-id", $InstanceName
        )
        if ($hasSecondary) {
            $arguments += @("--allied-history-db", ('"{0}"' -f $secondaryHistoryPath))
        }
    }
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

function Start-SecondaryAgent {
    $arguments = @(
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", ('"{0}"' -f $PSCommandPath),
        "-InstanceName", "secondary",
        "-EnvFileOnly",
        "-EnvFile", ('"{0}"' -f $secondaryEnvPath),
        "-LogFile", ('"{0}"' -f $secondaryLogPath),
        "-HistoryDb", ('"{0}"' -f $secondaryHistoryPath),
        "-WorkerTarget", $WorkerTarget,
        "-BeaconPolicy", $BeaconPolicy,
        "-NoDashboard"
    )
    if (-not [string]::IsNullOrWhiteSpace($BaseUrl)) {
        $arguments += @("-BaseUrl", $BaseUrl)
    }
    if ($NoCompatibilityMarker) {
        $arguments += "-NoCompatibilityMarker"
    }
    if ($hasAllianceRosterUrl) {
        $arguments += @(
            "-AllianceRosterUrl", $AllianceRosterUrl.Trim(),
            "-AllianceRosterTokenFile", ('"{0}"' -f $allianceRosterTokenPath),
            "-AllianceRosterRefreshSeconds", $AllianceRosterRefreshSeconds,
            "-AllianceRosterTimeoutSeconds", $AllianceRosterTimeoutSeconds
        )
    }

    $process = Start-Process -FilePath "powershell.exe" -ArgumentList $arguments `
        -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru
    Start-Sleep -Milliseconds 300
    $process.Refresh()
    if ($process.HasExited) {
        throw "Secondary Agent failed to start. See $secondaryLogPath"
    }
    Write-Host "Secondary Agent running in this launcher. Log: $secondaryLogPath"
    return $process
}

Set-Location -LiteralPath $projectRoot
$stateDirectory = Join-Path $projectRoot "state"
[IO.Directory]::CreateDirectory($stateDirectory) | Out-Null
$lockName = if ($InstanceName -eq "primary") {
    "windows-agent.lock"
}
else {
    "windows-agent.$InstanceName.lock"
}
$lockPath = Join-Path $stateDirectory $lockName
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
$secondaryProcess = $null
try {
    if ($hasSecondary) {
        $secondaryProcess = Start-SecondaryAgent
    }
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
    if ($null -ne $secondaryProcess -and -not $secondaryProcess.HasExited) {
        & taskkill.exe /PID $secondaryProcess.Id /T /F 2>$null | Out-Null
        $secondaryProcess.WaitForExit()
    }
    if ($null -ne $dashboardProcess -and -not $dashboardProcess.HasExited) {
        Stop-Process -Id $dashboardProcess.Id -Force -ErrorAction SilentlyContinue
        $dashboardProcess.WaitForExit()
    }
    $instanceLock.Dispose()
}

Write-Host "Agent stopped with exit code $agentExitCode."
exit $agentExitCode
