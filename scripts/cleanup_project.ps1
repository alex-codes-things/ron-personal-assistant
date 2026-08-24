param(
    [switch]$RemoveOllamaModel,
    [string]$OllamaModel = "qwen3.5:4b",
    [string]$ProjectRoot = (Split-Path $PSScriptRoot -Parent)
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = (Resolve-Path -LiteralPath $ProjectRoot).Path
if (-not (Test-Path -LiteralPath (Join-Path $root "pyproject.toml"))) {
    throw "This does not look like the Ron project folder: $root"
}

$generatedDirectories = @(
    ".pytest_cache",
    ".ruff_cache",
    "pytest-of-root",
    "ron_personal_assistant.egg-info"
)

foreach ($name in $generatedDirectories) {
    $target = Join-Path $root $name
    if (Test-Path -LiteralPath $target) {
        Remove-Item -LiteralPath $target -Recurse -Force
        Write-Host "Removed generated folder: $name" -ForegroundColor Green
    }
}

Get-ChildItem -LiteralPath $root -Directory -Recurse -Force |
    Where-Object { $_.Name -eq "__pycache__" } |
    Sort-Object { $_.FullName.Length } -Descending |
    ForEach-Object {
        Remove-Item -LiteralPath $_.FullName -Recurse -Force
    }

Get-ChildItem -LiteralPath $root -File -Recurse -Force -Filter "*.pyc" |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }

if ($RemoveOllamaModel) {
    $ollama = Get-Command "ollama.exe" -ErrorAction SilentlyContinue
    if ($null -eq $ollama) {
        Write-Host "Ollama is not installed or is not on PATH; no model was removed." -ForegroundColor Yellow
    } else {
        & $ollama.Source stop $OllamaModel 2>$null
        & $ollama.Source rm $OllamaModel
        if ($LASTEXITCODE -ne 0) {
            throw "Ollama could not remove $OllamaModel. Run 'ollama list' to confirm its name."
        }
        Write-Host "Removed local Ollama model: $OllamaModel" -ForegroundColor Green
    }
}

Write-Host "Cleanup complete. Your .env, runtime memory, voice models, and tablet files were preserved." -ForegroundColor Cyan
