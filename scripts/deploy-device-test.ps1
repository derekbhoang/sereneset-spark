param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8080,
    [switch]$SkipBuild,
    [switch]$SkipSeed
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$backendEnv = Join-Path $projectRoot "backend\.env"
$composeFiles = @(
    "-f", (Join-Path $projectRoot "compose.production.yml"),
    "-f", (Join-Path $projectRoot "compose.device-test.yml")
)

if (-not (Test-Path -LiteralPath $backendEnv -PathType Leaf)) {
    throw "backend/.env is required for B2 and generation provider settings."
}

$env:ENV_FILE = $backendEnv
$env:APP_PORT = [string]$Port

$upArguments = @("compose", "--env-file", $backendEnv) + $composeFiles + @("up", "-d")
if (-not $SkipBuild) {
    $upArguments += "--build"
}

Push-Location $projectRoot
try {
    & docker @upArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Docker Compose deployment failed."
    }

    $readinessUrl = "http://127.0.0.1:$Port/api/v1/health/ready"
    $ready = $false
    for ($attempt = 1; $attempt -le 80; $attempt++) {
        try {
            $response = Invoke-RestMethod -Uri $readinessUrl -TimeoutSec 5
            if ($response.status -eq "ready") {
                $ready = $true
                break
            }
        }
        catch {
            # The API, B2 probe, or worker heartbeat may still be starting.
        }
        Start-Sleep -Seconds 3
    }

    if (-not $ready) {
        & docker compose --env-file $backendEnv @composeFiles ps --all
        throw "The deployment did not become ready within four minutes."
    }

    if (-not $SkipSeed) {
        & docker compose --env-file $backendEnv @composeFiles run --rm --no-deps api `
            python scripts/seed.py --showcase-only
        if ($LASTEXITCODE -ne 0) {
            throw "The showcase seed failed."
        }
    }

    $addresses = [System.Net.Dns]::GetHostAddresses(
        [System.Net.Dns]::GetHostName()
    ) | Where-Object {
        $_.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork -and
        -not [System.Net.IPAddress]::IsLoopback($_)
    } | Select-Object -ExpandProperty IPAddressToString -Unique

    Write-Host "Device-test deployment is ready."
    Write-Host "Local: http://127.0.0.1:$Port"
    foreach ($address in $addresses) {
        Write-Host "LAN:   http://${address}:$Port"
    }
}
finally {
    Pop-Location
}
