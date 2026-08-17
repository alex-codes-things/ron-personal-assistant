param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("get", "set")]
    [string]$Action,

    [ValidateRange(0, 100)]
    [int]$Level = 0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$brightness = Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness |
    Select-Object -First 1
if ($null -eq $brightness) {
    throw "No controllable internal display was found."
}

if ($Action -eq "set") {
    $methods = Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods |
        Select-Object -First 1
    if ($null -eq $methods) {
        throw "No brightness control method was found."
    }
    Invoke-CimMethod -InputObject $methods -MethodName WmiSetBrightness `
        -Arguments @{ Timeout = 1; Brightness = [byte]$Level } | Out-Null
    $brightness = Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness |
        Select-Object -First 1
}

[ordered]@{
    ok = $true
    level = [int]$brightness.CurrentBrightness
} | ConvertTo-Json -Compress

