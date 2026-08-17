param(
    [switch]$SkipMicrophoneTest
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VoiceModels = Join-Path $ProjectRoot "models\voice"
$KwsName = "sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20"
$KwsDirectory = Join-Path $VoiceModels $KwsName
$KwsArchive = Join-Path $VoiceModels "$KwsName.tar.bz2"
$KwsUrl = "https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/$KwsName.tar.bz2"
$VadPath = Join-Path $VoiceModels "silero_vad.onnx"
$VadUrl = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx"
$WhisperRoot = Join-Path $VoiceModels "whisper"

function Invoke-CheckedDownload {
    param(
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][string]$Destination,
        [Parameter(Mandatory = $true)][long]$MinimumBytes,
        [string]$ExpectedSha256 = ""
    )
    if (Test-Path $Destination) {
        $Existing = Get-Item $Destination
        $ExistingHashIsValid = $true
        if ($ExpectedSha256) {
            $ExistingHash = (Get-FileHash -Algorithm SHA256 -Path $Destination).Hash
            $ExistingHashIsValid = $ExistingHash -eq $ExpectedSha256
        }
        if ($Existing.Length -ge $MinimumBytes -and $ExistingHashIsValid) {
            Write-Host "Already downloaded: $($Existing.Name)"
            return
        }
        Write-Host "Replacing an incomplete or invalid download: $($Existing.Name)"
    }
    $Partial = "$Destination.partial"
    for ($Attempt = 1; $Attempt -le 3; $Attempt++) {
        try {
            if (Test-Path $Partial) { Remove-Item -Force $Partial }
            Write-Host "Downloading $(Split-Path -Leaf $Destination) (attempt $Attempt of 3)..."
            Invoke-WebRequest -Uri $Uri -OutFile $Partial -UseBasicParsing
            $Downloaded = Get-Item $Partial
            if ($Downloaded.Length -lt $MinimumBytes) {
                throw "The download was unexpectedly small ($($Downloaded.Length) bytes)."
            }
            if ($ExpectedSha256) {
                $DownloadedHash = (Get-FileHash -Algorithm SHA256 -Path $Partial).Hash
                if ($DownloadedHash -ne $ExpectedSha256) {
                    throw "The download failed its SHA-256 integrity check."
                }
            }
            Move-Item -Force $Partial $Destination
            return
        }
        catch {
            if (Test-Path $Partial) { Remove-Item -Force $Partial }
            if ($Attempt -eq 3) { throw }
            Start-Sleep -Seconds (2 * $Attempt)
        }
    }
}

function Assert-File {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -PathType Leaf $Path)) {
        throw "Required voice file is missing: $Path"
    }
}

Push-Location $ProjectRoot
try {
    $VersionText = & python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ($LASTEXITCODE -ne 0 -or $VersionText.Trim() -ne "3.12") {
        throw "Activate Ron's Python 3.12 environment before running this script."
    }

    Write-Host "Installing Ron's optional offline voice packages..."
    & python -m pip install -e ".[voice,dev]"
    if ($LASTEXITCODE -ne 0) { throw "Voice package installation failed." }

    New-Item -ItemType Directory -Force -Path $VoiceModels | Out-Null
    New-Item -ItemType Directory -Force -Path $WhisperRoot | Out-Null

    $Encoder = Join-Path $KwsDirectory "encoder-epoch-13-avg-2-chunk-8-left-64.int8.onnx"
    if (-not (Test-Path $Encoder)) {
        Invoke-CheckedDownload -Uri $KwsUrl -Destination $KwsArchive -MinimumBytes 1000000
        Write-Host "Extracting the local Hey Ron detector..."
        & tar -xf $KwsArchive -C $VoiceModels
        if ($LASTEXITCODE -ne 0) { throw "The wake-word model archive could not be extracted." }
    }

    Assert-File $Encoder
    Assert-File (Join-Path $KwsDirectory "decoder-epoch-13-avg-2-chunk-8-left-64.onnx")
    Assert-File (Join-Path $KwsDirectory "joiner-epoch-13-avg-2-chunk-8-left-64.int8.onnx")
    Assert-File (Join-Path $KwsDirectory "tokens.txt")
    Assert-File (Join-Path $KwsDirectory "en.phone")

    # Build the local keyword file ourselves. Different sherpa-onnx releases
    # expose different text2token command-line interfaces, so setup must not
    # depend on a particular CLI version.
    $Keywords = Join-Path $VoiceModels "keywords.txt"
    Write-Host "Preparing Ron's local Hey Ron keyword..."
    & python scripts\prepare_wake_word.py `
        --tokens (Join-Path $KwsDirectory "tokens.txt") `
        --output $Keywords
    if ($LASTEXITCODE -ne 0) { throw "The local Hey Ron keyword could not be prepared." }
    Assert-File $Keywords

    # The official Sherpa Silero VAD asset is 643,854 bytes. Verify its hash so
    # a truncated response or HTML error page can never be accepted as a model.
    Invoke-CheckedDownload `
        -Uri $VadUrl `
        -Destination $VadPath `
        -MinimumBytes 640000 `
        -ExpectedSha256 "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6"
    Assert-File $VadPath

    Write-Host "Downloading base.en and small.en for the offline speed/accuracy comparison..."
    $WhisperScript = @"
from faster_whisper import WhisperModel
root = r'''$WhisperRoot'''
for name in ('base.en', 'small.en'):
    print(f'Preparing {name}...')
    WhisperModel(name, device='cpu', compute_type='int8', cpu_threads=4, download_root=root)
print('Whisper models are available offline.')
"@
    & python -c $WhisperScript
    if ($LASTEXITCODE -ne 0) { throw "Whisper model preparation failed." }

    Write-Host "Running code checks..."
    & python -m pytest
    if ($LASTEXITCODE -ne 0) { throw "Ron's automated tests failed after voice setup." }

    if (-not $SkipMicrophoneTest) {
        Write-Host "Running a short microphone-level test (no recognition and no tools)..."
        & python scripts\test_microphone.py --seconds 4
        if ($LASTEXITCODE -ne 0) {
            throw "The microphone test failed. Use --list to select the correct input."
        }
    }

    Write-Host ""
    Write-Host "Voice setup complete. No tool has been executed."
    Write-Host "Next dry test: python scripts\test_wake_word.py --seconds 60"
    Write-Host "Then benchmark: python scripts\benchmark_voice.py"
}
finally {
    Pop-Location
}
