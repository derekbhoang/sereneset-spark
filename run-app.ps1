param(
    [switch]$Install,
    [switch]$SkipDocker,
    [switch]$Stop,
    [switch]$Reload,
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173,
    [string]$HostAddress = "127.0.0.1"
)

$ErrorActionPreference = "Stop"

$RootDir = $PSScriptRoot
$BackendDir = Join-Path $RootDir "backend"
$FrontendDir = Join-Path $RootDir "frontend"
$RunDir = Join-Path $RootDir ".run"
$BackendLog = Join-Path $RunDir "backend.out.log"
$BackendErrorLog = Join-Path $RunDir "backend.err.log"
$FrontendLog = Join-Path $RunDir "frontend.out.log"
$FrontendErrorLog = Join-Path $RunDir "frontend.err.log"
$BackendPidFile = Join-Path $RunDir "backend.pid"
$FrontendPidFile = Join-Path $RunDir "frontend.pid"

function Write-Step {
    param([string]$Message)
    Write-Host "[sereneset] $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "[sereneset] $Message" -ForegroundColor Green
}

function Test-Command {
    param([string]$Command)
    return $null -ne (Get-Command $Command -ErrorAction SilentlyContinue)
}

function Wait-Http {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 45
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)

    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                return $true
            }
        }
        catch {
            Start-Sleep -Seconds 1
        }
    }

    return $false
}

function Wait-Tcp {
    param(
        [string]$ComputerName,
        [int]$Port,
        [int]$TimeoutSeconds = 45
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)

    while ((Get-Date) -lt $deadline) {
        $client = [System.Net.Sockets.TcpClient]::new()
        try {
            $connection = $client.BeginConnect($ComputerName, $Port, $null, $null)
            if ($connection.AsyncWaitHandle.WaitOne(1000)) {
                $client.EndConnect($connection)
                return $true
            }
        }
        catch {
            Start-Sleep -Seconds 1
        }
        finally {
            $client.Close()
        }
    }

    return $false
}

function Stop-ExistingProcess {
    param(
        [string]$PidFile,
        [string]$Name
    )

    if (-not (Test-Path $PidFile)) {
        return
    }

    $processId = Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $processId) {
        Remove-Item $PidFile -Force
        return
    }

    Write-Step "Stopping previous $Name process ($processId)"
    Stop-ProcessTree -ProcessId ([int]$processId)

    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

function Stop-ProcessTree {
    param([int]$ProcessId)

    if ($ProcessId -le 0) {
        return
    }

    $output = & cmd.exe /c "taskkill /PID $ProcessId /T /F 2>&1"
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        Write-Host "[sereneset] Could not stop process $ProcessId" -ForegroundColor Yellow
        if ($output) {
            Write-Host $output -ForegroundColor Yellow
        }
    }
}

function Get-ListeningPortProcessIds {
    param([int]$Port)

    $processIds = @()
    $lines = netstat -ano -p tcp

    foreach ($line in $lines) {
        $columns = $line.Trim() -split "\s+"
        if ($columns.Count -lt 5) {
            continue
        }

        $localAddress = $columns[1]
        $state = $columns[3]
        $processId = $columns[4]

        if ($localAddress -match ":$Port$" -and $state -eq "LISTENING") {
            $processIds += [int]$processId
        }
    }

    return $processIds | Sort-Object -Unique
}

function Stop-PortProcess {
    param(
        [int]$Port,
        [string]$Name
    )

    $processIds = Get-ListeningPortProcessIds -Port $Port
    foreach ($processId in $processIds) {
        Write-Step "Stopping $Name process on port $Port ($processId)"
        Stop-ProcessTree -ProcessId $processId
    }
}

function Stop-UvicornServerProcessesFromLog {
    if (-not (Test-Path $BackendErrorLog)) {
        return
    }

    $matches = Select-String `
        -Path $BackendErrorLog `
        -Pattern "Started server process \[(\d+)\]" `
        -AllMatches `
        -ErrorAction SilentlyContinue

    $processIds = @()
    foreach ($matchInfo in $matches) {
        foreach ($match in $matchInfo.Matches) {
            $processIds += [int]$match.Groups[1].Value
        }
    }

    foreach ($processId in ($processIds | Sort-Object -Unique)) {
        Write-Step "Stopping backend server child process from log ($processId)"
        Stop-ProcessTree -ProcessId $processId
    }
}

function Wait-PortClosed {
    param(
        [int]$Port,
        [int]$TimeoutSeconds = 10
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)

    while ((Get-Date) -lt $deadline) {
        if (-not (Get-ListeningPortProcessIds -Port $Port)) {
            return $true
        }

        Start-Sleep -Seconds 1
    }

    return $false
}

function Get-PythonPath {
    $venvPython = Join-Path $BackendDir ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        return $venvPython
    }

    if (Test-Command "python") {
        return "python"
    }

    throw "Python was not found. Create backend\.venv or install Python."
}

function Get-NpmPath {
    if (Test-Command "npm.cmd") {
        return "npm.cmd"
    }

    if (Test-Command "npm") {
        return "npm"
    }

    throw "npm was not found. Install Node.js first."
}

New-Item -ItemType Directory -Path $RunDir -Force | Out-Null

if ($Stop) {
    Stop-ExistingProcess -PidFile $BackendPidFile -Name "backend"
    Stop-ExistingProcess -PidFile $FrontendPidFile -Name "frontend"
    Stop-PortProcess -Port $BackendPort -Name "backend"
    Stop-PortProcess -Port $FrontendPort -Name "frontend"
    if (-not (Wait-PortClosed -Port $BackendPort -TimeoutSeconds 2)) {
        Stop-UvicornServerProcessesFromLog
    }
    $BackendStopped = Wait-PortClosed -Port $BackendPort
    $FrontendStopped = Wait-PortClosed -Port $FrontendPort

    if ($BackendStopped -and $FrontendStopped) {
        Write-Ok "Stopped app processes"
    }
    else {
        Write-Host "[sereneset] Some app ports are still active." -ForegroundColor Yellow
        if (-not $BackendStopped) {
            Write-Host "  Backend port still active: $BackendPort" -ForegroundColor Yellow
        }
        if (-not $FrontendStopped) {
            Write-Host "  Frontend port still active: $FrontendPort" -ForegroundColor Yellow
        }
        Write-Host "  Close the old server window, or rerun -Stop from an Administrator PowerShell." -ForegroundColor Yellow
    }
    exit 0
}

Write-Step "Preparing SereneSet Spark"

if (-not $SkipDocker) {
    if (-not (Test-Command "docker")) {
        throw "Docker was not found. Start Docker Desktop or run with -SkipDocker."
    }

    Write-Step "Starting Postgres with Docker Compose"
    Push-Location $BackendDir
    try {
        docker compose up -d postgres
    }
    finally {
        Pop-Location
    }

    Write-Step "Waiting for Postgres on 127.0.0.1:5432"
    if (-not (Wait-Tcp -ComputerName "127.0.0.1" -Port 5432 -TimeoutSeconds 60)) {
        throw "Postgres did not become ready on 127.0.0.1:5432."
    }
}

$PythonPath = Get-PythonPath
$NpmPath = Get-NpmPath

if ($Install) {
    Write-Step "Installing backend Python dependencies"
    & $PythonPath -m pip install -r (Join-Path $BackendDir "requirements.txt")

    Write-Step "Installing frontend Node dependencies"
    Push-Location $FrontendDir
    try {
        & $NpmPath install
    }
    finally {
        Pop-Location
    }
}

Write-Step "Running Alembic migrations"
Push-Location $BackendDir
try {
    & $PythonPath -m alembic upgrade head
}
finally {
    Pop-Location
}

Stop-ExistingProcess -PidFile $BackendPidFile -Name "backend"
Stop-ExistingProcess -PidFile $FrontendPidFile -Name "frontend"
Stop-PortProcess -Port $BackendPort -Name "backend"
Stop-PortProcess -Port $FrontendPort -Name "frontend"
if (-not (Wait-PortClosed -Port $BackendPort -TimeoutSeconds 2)) {
    Stop-UvicornServerProcessesFromLog
}
if (-not (Wait-PortClosed -Port $BackendPort)) {
    throw "Backend port $BackendPort is still in use. Stop that process before starting the app."
}
if (-not (Wait-PortClosed -Port $FrontendPort)) {
    throw "Frontend port $FrontendPort is still in use. Stop that process before starting the app."
}

Remove-Item $BackendLog, $BackendErrorLog, $FrontendLog, $FrontendErrorLog -Force -ErrorAction SilentlyContinue

Write-Step "Starting backend on http://$HostAddress`:$BackendPort"
$BackendArguments = @(
    "-m",
    "uvicorn",
    "app.main:app",
    "--host",
    $HostAddress,
    "--port",
    [string]$BackendPort
)
if ($Reload) {
    $BackendArguments = @(
        "-m",
        "uvicorn",
        "app.main:app",
        "--reload",
        "--host",
        $HostAddress,
        "--port",
        [string]$BackendPort
    )
}
$BackendProcess = Start-Process `
    -FilePath $PythonPath `
    -ArgumentList $BackendArguments `
    -WorkingDirectory $BackendDir `
    -RedirectStandardOutput $BackendLog `
    -RedirectStandardError $BackendErrorLog `
    -WindowStyle Hidden `
    -PassThru
$BackendProcess.Id | Set-Content $BackendPidFile

Write-Step "Starting frontend on http://$HostAddress`:$FrontendPort"
$FrontendProcess = Start-Process `
    -FilePath $NpmPath `
    -ArgumentList @(
        "run",
        "dev",
        "--",
        "--host",
        $HostAddress,
        "--port",
        [string]$FrontendPort
    ) `
    -WorkingDirectory $FrontendDir `
    -RedirectStandardOutput $FrontendLog `
    -RedirectStandardError $FrontendErrorLog `
    -WindowStyle Hidden `
    -PassThru
$FrontendProcess.Id | Set-Content $FrontendPidFile

$BackendReady = Wait-Http -Url "http://$HostAddress`:$BackendPort/docs" -TimeoutSeconds 45
$FrontendReady = Wait-Http -Url "http://$HostAddress`:$FrontendPort" -TimeoutSeconds 45

if ($BackendReady) {
    Write-Ok "Backend is ready: http://$HostAddress`:$BackendPort/docs"
}
else {
    Write-Host "[sereneset] Backend did not respond yet. Check $BackendLog and $BackendErrorLog" -ForegroundColor Yellow
}

if ($FrontendReady) {
    Write-Ok "Frontend is ready: http://$HostAddress`:$FrontendPort"
}
else {
    Write-Host "[sereneset] Frontend did not respond yet. Check $FrontendLog and $FrontendErrorLog" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Logs:"
Write-Host "  backend:  $BackendLog"
Write-Host "  backend errors:  $BackendErrorLog"
Write-Host "  frontend: $FrontendLog"
Write-Host "  frontend errors: $FrontendErrorLog"
Write-Host ""
Write-Host "Stop commands:"
Write-Host "  Stop-Process -Id (Get-Content `"$BackendPidFile`")"
Write-Host "  Stop-Process -Id (Get-Content `"$FrontendPidFile`")"
Write-Host ""
Write-Host "Run with dependency install:"
Write-Host "  .\run-app.ps1 -Install"
Write-Host ""
Write-Host "Run backend with Uvicorn reload:"
Write-Host "  .\run-app.ps1 -Reload"
Write-Host ""
Write-Host "Stop app processes:"
Write-Host "  .\run-app.ps1 -Stop"
