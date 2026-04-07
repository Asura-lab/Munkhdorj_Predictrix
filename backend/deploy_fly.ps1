param(
    [string]$AppName = "predictrix-api",
    [string]$Region = "nrt",
    [string]$SecretsFile = "",
    [switch]$AllowDotenvSecrets,
    [switch]$KeepRunning
)

$ErrorActionPreference = "Stop"

function Fail($message) {
    Write-Host "[ERROR] $message" -ForegroundColor Red
    exit 1
}

function Info($message) {
    Write-Host "[INFO] $message" -ForegroundColor Cyan
}

function Get-SecretsFromDotEnv {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path $Path)) {
        return @()
    }

    return Get-Content $Path |
        ForEach-Object { ($_ -split '#')[0].Trim() } |
        Where-Object { $_ -match '^[A-Za-z_][A-Za-z0-9_]*=' }
}

function Invoke-Fly {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Args,
        [string]$InputFile
    )

    $outFile = [System.IO.Path]::GetTempFileName()
    $errFile = [System.IO.Path]::GetTempFileName()

    try {
        $startParams = @{
            FilePath               = "fly"
            ArgumentList           = $Args
            NoNewWindow            = $true
            Wait                   = $true
            PassThru               = $true
            RedirectStandardOutput = $outFile
            RedirectStandardError  = $errFile
        }

        if ($InputFile) {
            $startParams.RedirectStandardInput = $InputFile
        }

        $proc = Start-Process @startParams

        $stdout = ""
        $stderr = ""
        if (Test-Path $outFile) {
            $stdout = Get-Content $outFile -Raw
        }
        if (Test-Path $errFile) {
            $stderr = Get-Content $errFile -Raw
        }

        if (-not [string]::IsNullOrWhiteSpace($stdout)) {
            Write-Host $stdout.TrimEnd()
        }
        if (-not [string]::IsNullOrWhiteSpace($stderr)) {
            Write-Host $stderr.TrimEnd()
        }

        return [PSCustomObject]@{
            ExitCode = $proc.ExitCode
            StdOut   = $stdout
            StdErr   = $stderr
            Combined = (($stdout + "`n" + $stderr).Trim())
        }
    }
    finally {
        Remove-Item $outFile, $errFile -ErrorAction SilentlyContinue
    }
}

function Run-Fly {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Args,
        [string]$InputFile
    )

    $res = Invoke-Fly -Args $Args -InputFile $InputFile
    if ($res.ExitCode -ne 0) {
        Fail ("fly " + ($Args -join " ") + " failed with exit code " + $res.ExitCode)
    }
    return $res
}

if (-not (Get-Command fly -ErrorAction SilentlyContinue)) {
    Fail "fly CLI not found. Install first and reopen PowerShell."
}

Set-Location $PSScriptRoot

Info "Checking Fly authentication..."
$authRes = Invoke-Fly -Args @("auth", "whoami")
if ($authRes.ExitCode -ne 0) {
    Fail "Not logged in. Run: fly auth login"
}

Info "Ensuring app exists: $AppName"
$listRes = Run-Fly -Args @("apps", "list", "--json")
$apps = @()
if (-not [string]::IsNullOrWhiteSpace($listRes.StdOut)) {
    $apps = $listRes.StdOut | ConvertFrom-Json
}

$appExists = $false
foreach ($app in $apps) {
    if ($app.name -eq $AppName) {
        $appExists = $true
        break
    }
}

if (-not $appExists) {
    $createRes = Invoke-Fly -Args @("apps", "create", $AppName)
    if ($createRes.ExitCode -ne 0) {
        if ($createRes.Combined -match "payment information") {
            Fail "Fly requires billing info before app creation. Add card at: https://fly.io/dashboard/asura-jims/billing"
        }
        Fail ("Failed to create app '" + $AppName + "'.")
    }
}

Info "Updating fly.toml app + region"
$content = Get-Content .\fly.toml -Raw
$content = $content -replace 'app = ".*?"', ('app = "' + $AppName + '"')
$content = $content -replace 'primary_region = ".*?"', ('primary_region = "' + $Region + '"')
Set-Content .\fly.toml $content

Info "Collecting secrets (production-first)"
$secretMap = @{}

$preferredKeys = @(
    "MONGO_URI",
    "SECRET_KEY",
    "MAIL_SERVER",
    "MAIL_PORT",
    "MAIL_USE_TLS",
    "MAIL_USE_SSL",
    "MAIL_USERNAME",
    "MAIL_PASSWORD",
    "MAIL_DEFAULT_SENDER",
    "ALPHAVANTAGE_API_KEY",
    "TWELVEDATA_API_KEY",
    "JWT_ISSUER",
    "JWT_AUDIENCE",
    "ACCESS_TOKEN_EXPIRATION_MINUTES",
    "REFRESH_TOKEN_EXPIRATION_DAYS",
    "ALLOW_AUTH_CODE_IN_RESPONSE",
    "ENABLE_BACKGROUND_JOBS",
    "BG_LOCK_TTL_SECONDS",
    "ACTIVE_GBDT_MODEL_PATH"
)
$preferredKeys += (1..21 | ForEach-Object { "GEMINI_API_KEY_$_" })

# 1) Environment variables first
foreach ($key in $preferredKeys) {
    $val = [Environment]::GetEnvironmentVariable($key)
    if (-not [string]::IsNullOrWhiteSpace($val)) {
        $secretMap[$key] = $val
    }
}

# 2) Optional dotenv fallback (explicit only)
$dotenvPath = if (-not [string]::IsNullOrWhiteSpace($SecretsFile)) { $SecretsFile } else { ".\\config\\.env" }
if ($AllowDotenvSecrets -or -not [string]::IsNullOrWhiteSpace($SecretsFile)) {
    $dotenvLines = Get-SecretsFromDotEnv -Path $dotenvPath
    foreach ($line in $dotenvLines) {
        $parts = $line -split '=', 2
        if ($parts.Count -ne 2) { continue }
        $k = $parts[0].Trim()
        $v = $parts[1]
        if (-not $secretMap.ContainsKey($k) -and -not [string]::IsNullOrWhiteSpace($v)) {
            $secretMap[$k] = $v
        }
    }
    Info "Dotenv fallback enabled: $dotenvPath"
} else {
    Info "Dotenv fallback disabled. Using only process environment secrets."
}

$missingRequired = @("MONGO_URI", "SECRET_KEY") | Where-Object { -not $secretMap.ContainsKey($_) }
if ($missingRequired.Count -gt 0) {
    Fail ("Missing required secrets: " + ($missingRequired -join ", ") + ". Set env vars or use -AllowDotenvSecrets.")
}

$secretLines = $secretMap.GetEnumerator() |
    Sort-Object Name |
    ForEach-Object { "$($_.Name)=$($_.Value)" }

if (-not $secretLines -or $secretLines.Count -eq 0) {
    Fail "No secrets collected for import."
}

$tempSecrets = [System.IO.Path]::GetTempFileName()
try {
    Set-Content -Path $tempSecrets -Value $secretLines
    $secretRes = Invoke-Fly -Args @("secrets", "import", "-a", $AppName) -InputFile $tempSecrets
    if ($secretRes.ExitCode -ne 0) {
        Fail "Failed to import secrets."
    }
}
finally {
    Remove-Item $tempSecrets -ErrorAction SilentlyContinue
}

Info "Scaling profile: one machine, shared-cpu-1x, 1024MB"

Info "Deploying"
Run-Fly -Args @("deploy", "--remote-only", "-a", $AppName)

# Ensure cost profile after deployment (works for both first and subsequent deploys)
Run-Fly -Args @("scale", "count", "1", "-a", $AppName)
Run-Fly -Args @("scale", "vm", "shared-cpu-1x", "--memory", "1024", "-a", $AppName)

Info "Deployment complete."
Run-Fly -Args @("status", "-a", $AppName)

if (-not $KeepRunning) {
    Info "Cost-safe mode: stopping machines now (app will auto-start on next request)"
    $machinesRes = Run-Fly -Args @("machine", "list", "--json", "-a", $AppName)
    $machines = @()
    if (-not [string]::IsNullOrWhiteSpace($machinesRes.StdOut)) {
        $machines = $machinesRes.StdOut | ConvertFrom-Json
    }

    foreach ($m in $machines) {
        if ($m.id) {
            Run-Fly -Args @("machine", "stop", $m.id, "-a", $AppName)
        }
    }

    Info "All machines stopped. To run continuously, execute with -KeepRunning."
}
