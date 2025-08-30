#
# run.ps1 — Developer wrapper for Windows 11 + Docker Desktop
# Purpose: Start Docker Desktop if needed, load ENV vars from one place, and
#          run docker compose for local dev/debug of the teltubby service.
#
# Usage examples (PowerShell 7):
#   ./run.ps1                         # default action: up (build + start)
#   ./run.ps1 -Action up -NoBuild     # start without rebuilding
#   ./run.ps1 -Action down            # stop and remove containers
#   ./run.ps1 -Action logs            # tail logs for the service
#   ./run.ps1 -Action status          # show compose status
#   ./run.ps1 -Action restart         # down + up
#   ./run.ps1 -Action rebuild         # build --no-cache + up
#
param(
    # Action to perform against docker compose
    [ValidateSet("up","down","restart","logs","status","rebuild","ps")]
    [string]$Action = "up",

    # When -Action up, skip the image rebuild if specified
    [switch]$NoBuild
)

set-strictmode -version latest
$ErrorActionPreference = "Stop"

# ----------------------------------------------------------------------------
# Constants and paths
# ----------------------------------------------------------------------------

# [string] ProjectRoot: Absolute path of the repository root
$ProjectRoot = Split-Path -Parent $PSCommandPath

# [string] DockerDesktopExe: Default installation path for Docker Desktop
$DockerDesktopExe = "C:\\Program Files\\Docker\\Docker\\Docker Desktop.exe"

# [string] EnvConfigPath: Single place to configure environment variables for dev
$EnvConfigPath = Join-Path $ProjectRoot "env.local.ps1"

# [string] DotEnvPath: Optional dotenv file used directly by docker compose
$DotEnvPath = Join-Path $ProjectRoot ".env"

# ----------------------------------------------------------------------------
# Helper: Write-Info — consistent informational output
# ----------------------------------------------------------------------------
function Write-Info {
    param([string]$Message)
    Write-Host "[teltubby/run] $Message" -ForegroundColor Cyan
}

# ----------------------------------------------------------------------------
# Helper: Ensure-EnvFile — creates a template env.local.ps1 if missing
# ----------------------------------------------------------------------------
function Ensure-EnvFile {
    # If a .env file exists, prefer that and do not create env.local.ps1
    if (Test-Path -LiteralPath $DotEnvPath) { return }
    if (Test-Path -LiteralPath $EnvConfigPath) { return }
    Write-Info "Creating sample env file at $EnvConfigPath"
    $template = @'
# env.local.ps1 — Local developer configuration for docker compose
#
# NOTE:
# - This file is dot-sourced by run.ps1 to populate $env:* variables used by
#   docker compose. Edit values below to match your environment.

# --- Telegram ---
$env:TELEGRAM_BOT_TOKEN       = "<bot_token>"          # [string]
$env:TELEGRAM_WHITELIST_IDS   = "123456789"           # [string] comma-separated
$env:TELEGRAM_MODE            = "polling"              # [string] polling|webhook
$env:WEBHOOK_URL              = ""                      # [string] when webhook
$env:WEBHOOK_SECRET           = ""                      # [string] optional

# --- MinIO / S3 ---
$env:S3_ENDPOINT              = "http://localhost:9000"   # [string]
$env:S3_ACCESS_KEY_ID         = "admin"
$env:S3_SECRET_ACCESS_KEY     = "minio123"
$env:S3_BUCKET                = "telegram"
$env:S3_REGION                = ""                     # [string|null]
$env:S3_FORCE_PATH_STYLE      = "true"                 # [bool-string]
$env:MINIO_TLS_SKIP_VERIFY    = "true"                 # [bool-string] only for self-signed dev

# --- Ingestion ---
$env:ALBUM_AGGREGATION_WINDOW_SECONDS = "10"          # [int-string]
$env:MAX_FILE_GB                      = "4"           # [int-string]
$env:BOT_API_MAX_FILE_SIZE_BYTES      = "52428800"    # [int-string] 50MB

# --- Dedup / DB ---
$env:SQLITE_PATH              = "/data/teltubby.db"
$env:DEDUP_ENABLE            = "true"

# --- Concurrency & I/O ---
$env:CONCURRENCY             = "8"
$env:IO_TIMEOUT_SECONDS      = "60"
$env:S3_MULTIPART_THRESHOLD_MB = "8"
$env:S3_MULTIPART_PART_SIZE_MB = "16"

# --- Quota & Alerts ---
$env:QUOTA_ALERT_THRESHOLD_PCT  = "80"
$env:QUOTA_ALERT_COOLDOWN_HOURS = "24"
# Optional explicit bucket quota in bytes to simulate capacity locally (e.g., 10485760 = 10MB)
$env:S3_BUCKET_QUOTA_BYTES      = ""  # leave empty to disable

# --- Logging & Health ---
$env:LOG_LEVEL                 = "INFO"
$env:LOG_ROTATE_MAX_BYTES      = "5242880"   # 5MB
$env:LOG_ROTATE_BACKUP_COUNT   = "10"
$env:HEALTH_PORT               = "8081"
$env:BIND_HEALTH_LOCALHOST_ONLY = "true"
'@
    Set-Content -LiteralPath $EnvConfigPath -Value $template -Encoding UTF8
}

# ----------------------------------------------------------------------------
# Helper: Load-Env — loads env.local.ps1 and exports variables to process env
# ----------------------------------------------------------------------------
function Load-Env {
    if (Test-Path -LiteralPath $DotEnvPath) {
        # docker compose will load .env automatically for variable substitution
        Write-Info "Detected .env — relying on docker compose to load it. Skipping env.local.ps1."
        return
    }
    if (-not (Test-Path -LiteralPath $EnvConfigPath)) {
        throw "Env file not found: $EnvConfigPath (it should have been created)."
    }
    Write-Info "Loading $EnvConfigPath"
    . $EnvConfigPath
}

# ----------------------------------------------------------------------------
# Helper: Ensure-Docker — starts Docker Desktop if the engine is not ready
# ----------------------------------------------------------------------------
function Ensure-Docker {
    try {
        docker version --format '{{.Server.Version}}' | Out-Null
        return
    } catch {
        Write-Info "Docker engine not ready; attempting to start Docker Desktop"
    }

    if (-not (Test-Path -LiteralPath $DockerDesktopExe)) {
        throw "Docker Desktop not found at: $DockerDesktopExe"
    }

    Start-Process -FilePath $DockerDesktopExe | Out-Null

    $timeoutSec = 120
    $start = [DateTime]::UtcNow
    while ($true) {
        try {
            docker info --format '{{.ServerVersion}}' | Out-Null
            break
        } catch {
            Start-Sleep -Seconds 3
            if (([DateTime]::UtcNow - $start).TotalSeconds -ge $timeoutSec) {
                throw "Timed out waiting for Docker engine to become ready."
            }
        }
    }
    Write-Info "Docker engine is ready."
}

# ----------------------------------------------------------------------------
# Helper: Invoke-Compose — wraps docker compose in the project root
# ----------------------------------------------------------------------------
function Invoke-Compose {
    param([string[]]$Args)
    Push-Location $ProjectRoot
    try {
        & docker compose @Args
    } finally {
        Pop-Location
    }
}

# ----------------------------------------------------------------------------
# Main flow
# ----------------------------------------------------------------------------
Write-Info "Project root: $ProjectRoot"
Ensure-EnvFile
Load-Env
Ensure-Docker

switch ($Action) {
    "up" {
        $composeArgs = @("up","-d")
        if (-not $NoBuild.IsPresent) { $composeArgs = @("up","--build","-d") }
        Write-Info "docker compose $($composeArgs -join ' ')"
        Invoke-Compose -Args $composeArgs
        $hostPort = if ($env:HOST_HEALTH_PORT) { $env:HOST_HEALTH_PORT } else { "8081" }
        Write-Info "Service is starting. Health: http://127.0.0.1:$hostPort/healthz"
    }
    "down" {
        Write-Info "docker compose down"
        Invoke-Compose -Args @("down")
    }
    "restart" {
        Write-Info "docker compose down"
        Invoke-Compose -Args @("down")
        $composeArgs = @("up","--build","-d")
        Write-Info "docker compose $($composeArgs -join ' ')"
        Invoke-Compose -Args $composeArgs
    }
    "logs" {
        Write-Info "docker compose logs -f teltubby"
        Invoke-Compose -Args @("logs","-f","teltubby")
    }
    "status" { goto ps }
    "ps" {
        :ps Write-Info "docker compose ps"
        Invoke-Compose -Args @("ps")
    }
    "rebuild" {
        Write-Info "docker compose build --no-cache"
        Invoke-Compose -Args @("build","--no-cache")
        Write-Info "docker compose up -d"
        Invoke-Compose -Args @("up","-d")
    }
    default {
        throw "Unsupported action: $Action"
    }
}

Write-Info "Done."

