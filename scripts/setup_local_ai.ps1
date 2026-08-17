param(
    [string]$Model = "qwen3.5:4b",
    [string]$ProjectRoot = (Split-Path $PSScriptRoot -Parent)
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Stop-WithMessage {
    param([string]$Message)
    throw $Message
}

Write-Host "`nRon AI: local model setup" -ForegroundColor Cyan

$python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    Stop-WithMessage (
        "Ron's virtual environment was not found. From the project folder run: " +
        "py -3.12 -m venv .venv"
    )
}

$ollamaCommand = Get-Command "ollama.exe" -ErrorAction SilentlyContinue
if ($null -eq $ollamaCommand) {
    Write-Host "Ollama is not installed or is not on PATH." -ForegroundColor Yellow
    Write-Host "Install it from the official page:" -ForegroundColor Yellow
    Write-Host "  https://ollama.com/download/windows" -ForegroundColor White
    Write-Host "Restart PowerShell after installation, then run this script again." -ForegroundColor White
    exit 2
}

$versionText = & $ollamaCommand.Source --version 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) {
    Stop-WithMessage "Ollama was found but its version check failed: $versionText"
}
Write-Host $versionText.Trim() -ForegroundColor Green

Write-Host "Checking the local Ollama service..." -ForegroundColor Cyan
try {
    $null = Invoke-RestMethod `
        -Method Get `
        -Uri "http://127.0.0.1:11434/api/version" `
        -TimeoutSec 3
} catch {
    Write-Host "Starting Ollama in the background..." -ForegroundColor Yellow
    Start-Process -FilePath $ollamaCommand.Source -ArgumentList "serve" -WindowStyle Hidden
    $ready = $false
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        Start-Sleep -Milliseconds 500
        try {
            $null = Invoke-RestMethod `
                -Method Get `
                -Uri "http://127.0.0.1:11434/api/version" `
                -TimeoutSec 2
            $ready = $true
            break
        } catch {
            continue
        }
    }
    if (-not $ready) {
        Stop-WithMessage "Ollama did not become ready. Open the Ollama app and retry."
    }
}

if ($Model -notmatch '^[A-Za-z0-9._/-]+:[A-Za-z0-9._-]+$') {
    Stop-WithMessage "The model name contains unsupported characters: $Model"
}

Write-Host "`nDownloading/verifying $Model..." -ForegroundColor Cyan
& $ollamaCommand.Source pull $Model
if ($LASTEXITCODE -ne 0) {
    Stop-WithMessage "Ollama could not pull $Model. Check disk space and internet access."
}

$env:RON_LOCAL_MODEL = $Model
$env:RON_OLLAMA_URL = "http://127.0.0.1:11434"
$env:RON_MODEL_KEEP_ALIVE = "-1"
$env:RON_MODEL_CONTEXT = "8192"

Write-Host "`nRunning Ron's real latency benchmark..." -ForegroundColor Cyan
Push-Location $ProjectRoot
try {
    & $python "scripts\benchmark_ai.py"
    $benchmarkExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
if ($benchmarkExitCode -ne 0) {
    Stop-WithMessage "Ron's AI benchmark failed with exit code $benchmarkExitCode."
}

Write-Host "`nLocal AI setup is complete." -ForegroundColor Green
