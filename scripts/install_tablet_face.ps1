param(
    [string]$ProjectRoot = (Split-Path $PSScriptRoot -Parent)
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$androidProject = Join-Path $ProjectRoot "android\ron-face"
$gradleFile = Join-Path $androidProject "app\build.gradle"
$apkFile = Join-Path $androidProject "app\build\outputs\apk\debug\app-debug.apk"
$buildLog = Join-Path $ProjectRoot "ron-face-build-error.txt"
$packageName = "com.alexcodesthings.ronface"
$componentName = "$packageName/.MainActivity"
$sdkRoot = Join-Path $env:LOCALAPPDATA "Android\Sdk"
$adb = Join-Path $sdkRoot "platform-tools\adb.exe"
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Stop-WithMessage {
    param([string]$Message)
    throw $Message
}

Write-Host "`nRon Face: safe build and install" -ForegroundColor Cyan

if (-not (Test-Path $gradleFile)) {
    Stop-WithMessage "Android project not found at: $androidProject"
}

if (-not (Test-Path $adb)) {
    Stop-WithMessage "ADB not found at: $adb"
}

$gradleSource = [System.IO.File]::ReadAllText($gradleFile)
$versionMatch = [regex]::Match($gradleSource, 'versionName\s+"([^"]+)"')
if (-not $versionMatch.Success) {
    Stop-WithMessage "versionName is missing from app/build.gradle"
}
$expectedVersion = $versionMatch.Groups[1].Value

Write-Host "Target version: $expectedVersion" -ForegroundColor Green

$javaCandidates = @(
    (Join-Path $env:ProgramFiles "Android\Android Studio\jbr"),
    (Join-Path $env:LOCALAPPDATA "Programs\Android Studio\jbr")
)

$studioProcess = Get-Process "studio64" -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($null -ne $studioProcess) {
    $studioBin = Split-Path $studioProcess.Path -Parent
    $studioHome = Split-Path $studioBin -Parent
    $javaCandidates += (Join-Path $studioHome "jbr")
}

$javaHome = $javaCandidates |
    Where-Object { Test-Path (Join-Path $_ "bin\java.exe") } |
    Select-Object -First 1
if ($null -eq $javaHome) {
    Stop-WithMessage "Android Studio's Java 17 runtime was not found."
}

$gradleExecutable = $null
$wrapper = Join-Path $androidProject "gradlew.bat"
if (Test-Path $wrapper) {
    $gradleExecutable = Get-Item $wrapper
} else {
    $gradleExecutable = Get-ChildItem `
        (Join-Path $env:USERPROFILE ".gradle\wrapper\dists") `
        -Filter "gradle.bat" `
        -File `
        -Recurse `
        -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match 'gradle-8\.(9|1[0-9])' } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
}
if ($null -eq $gradleExecutable) {
    Stop-WithMessage "Gradle 8.9 or newer was not found in the local cache."
}

$env:JAVA_HOME = $javaHome
$env:ANDROID_HOME = $sdkRoot
$env:ANDROID_SDK_ROOT = $sdkRoot
$env:Path = "$(Join-Path $javaHome 'bin');$env:Path"

$sdkForGradle = $sdkRoot.Replace("\", "/")
[System.IO.File]::WriteAllText(
    (Join-Path $androidProject "local.properties"),
    "sdk.dir=$sdkForGradle`r`n",
    $utf8NoBom
)

Write-Host "Java:   $javaHome"
Write-Host "Gradle: $($gradleExecutable.FullName)"
Write-Host "SDK:    $sdkRoot"

Write-Host "`nChecking connected tablets..." -ForegroundColor Cyan
$deviceText = & $adb devices 2>&1 | Out-String
$readyDevices = @()
foreach ($line in ($deviceText -split "`r?`n")) {
    if ($line -match '^(\S+)\s+device\b') {
        $readyDevices += $Matches[1]
    }
}

$preferredSerial = $env:RON_TABLET_SERIAL
if (-not [string]::IsNullOrWhiteSpace($preferredSerial)) {
    if ($readyDevices -notcontains $preferredSerial) {
        Stop-WithMessage "Configured tablet $preferredSerial is not connected and authorised."
    }
    $serial = $preferredSerial
} elseif ($readyDevices.Count -eq 1) {
    $serial = $readyDevices[0]
} elseif ($readyDevices.Count -eq 0) {
    Stop-WithMessage "No authorised Android tablet is connected."
} else {
    Stop-WithMessage "Multiple devices are connected. Set RON_TABLET_SERIAL first."
}

Write-Host "Tablet: $serial" -ForegroundColor Green
$adbTarget = @("-s", $serial)

Write-Host "`nBuilding a fresh debug APK..." -ForegroundColor Cyan
if (Test-Path $buildLog) {
    Remove-Item -LiteralPath $buildLog -Force
}
Push-Location $androidProject
$oldPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    & $gradleExecutable.FullName --no-daemon --console=plain clean assembleDebug 2>&1 |
        Tee-Object -FilePath $buildLog
    $buildExitCode = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $oldPreference
    Pop-Location
}

if ($buildExitCode -ne 0) {
    Write-Host "`nFirst useful compiler error:" -ForegroundColor Red
    $firstError = Select-String `
        -Path $buildLog `
        -Pattern ':\d+: error:|error: cannot find symbol|error: variable .* might not have been initialized' `
        -Context 0, 5 |
        Select-Object -First 1
    if ($null -ne $firstError) {
        Write-Host $firstError.ToString() -ForegroundColor Red
    } else {
        Write-Host "No Java error block was detected automatically." -ForegroundColor Yellow
    }
    Stop-WithMessage "Gradle failed. Full output was saved to: $buildLog"
}
if (-not (Test-Path $apkFile)) {
    Stop-WithMessage "Build succeeded but app-debug.apk was not produced."
}
if (Test-Path $buildLog) {
    Remove-Item -LiteralPath $buildLog -Force
}

Write-Host "APK built: $apkFile" -ForegroundColor Green
Write-Host "`nInstalling without clearing Ron Face data..." -ForegroundColor Cyan

$oldPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    $installOutput = & $adb @adbTarget install -r $apkFile 2>&1 | Out-String
} finally {
    $ErrorActionPreference = $oldPreference
}
Write-Host $installOutput

if ($installOutput -notmatch 'Success') {
    if ($installOutput -match 'INSTALL_FAILED_UPDATE_INCOMPATIBLE') {
        Stop-WithMessage (
            "The installed app has a different signature. Uninstall it manually " +
            "only if you accept clearing its pairing data, then run this script again."
        )
    }
    Stop-WithMessage "ADB rejected the APK: $installOutput"
}

& $adb @adbTarget shell am start -n $componentName | Out-Host
Start-Sleep -Seconds 2

$packageDetails = & $adb @adbTarget shell dumpsys package $packageName 2>&1 | Out-String
if ($packageDetails -notmatch "versionName=$([regex]::Escape($expectedVersion))") {
    Stop-WithMessage "Installed package does not report version $expectedVersion."
}

Write-Host "`nRon Face $expectedVersion is installed and running." -ForegroundColor Green
