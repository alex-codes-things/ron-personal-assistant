param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("get", "set", "mute", "unmute", "toggle")]
    [string]$Action,

    [ValidateRange(0, 100)]
    [int]$Level = 0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

[Guid("5CDF2C82-841E-4546-9722-0CF74078229A")]
[InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
[ComImport]
interface IAudioEndpointVolume {
    [PreserveSig]
    int RegisterControlChangeNotify(IntPtr notify);
    [PreserveSig]
    int UnregisterControlChangeNotify(IntPtr notify);
    [PreserveSig]
    int GetChannelCount(out uint count);
    [PreserveSig]
    int SetMasterVolumeLevel(float levelDb, Guid context);
    [PreserveSig]
    int SetMasterVolumeLevelScalar(float level, Guid context);
    [PreserveSig]
    int GetMasterVolumeLevel(out float levelDb);
    [PreserveSig]
    int GetMasterVolumeLevelScalar(out float level);
    [PreserveSig]
    int SetChannelVolumeLevel(uint channel, float levelDb, Guid context);
    [PreserveSig]
    int SetChannelVolumeLevelScalar(uint channel, float level, Guid context);
    [PreserveSig]
    int GetChannelVolumeLevel(uint channel, out float levelDb);
    [PreserveSig]
    int GetChannelVolumeLevelScalar(uint channel, out float level);
    [PreserveSig]
    int SetMute([MarshalAs(UnmanagedType.Bool)] bool mute, Guid context);
    [PreserveSig]
    int GetMute([MarshalAs(UnmanagedType.Bool)] out bool mute);
    [PreserveSig]
    int GetVolumeStepInfo(out uint step, out uint stepCount);
    [PreserveSig]
    int VolumeStepUp(Guid context);
    [PreserveSig]
    int VolumeStepDown(Guid context);
    [PreserveSig]
    int QueryHardwareSupport(out uint hardwareSupportMask);
    [PreserveSig]
    int GetVolumeRange(out float minDb, out float maxDb, out float incrementDb);
}

[Guid("D666063F-1587-4E43-81F1-B948E807363F")]
[InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
[ComImport]
interface IMMDevice {
    [PreserveSig]
    int Activate(ref Guid iid, uint clsCtx, IntPtr activationParams,
        [MarshalAs(UnmanagedType.IUnknown)] out object interfacePointer);
}

[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6")]
[InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
[ComImport]
interface IMMDeviceEnumerator {
    [PreserveSig]
    int EnumAudioEndpoints(int dataFlow, uint stateMask, out IntPtr devices);
    [PreserveSig]
    int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice endpoint);
}

[ComImport]
[Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
class MMDeviceEnumeratorComObject { }

public static class RonAudio {
    static void Check(int result) {
        if (result < 0) Marshal.ThrowExceptionForHR(result);
    }

    static IAudioEndpointVolume Endpoint() {
        var enumerator = (IMMDeviceEnumerator)(new MMDeviceEnumeratorComObject());
        IMMDevice device;
        Check(enumerator.GetDefaultAudioEndpoint(0, 1, out device));
        Guid iid = typeof(IAudioEndpointVolume).GUID;
        object endpoint;
        Check(device.Activate(ref iid, 23, IntPtr.Zero, out endpoint));
        return (IAudioEndpointVolume)endpoint;
    }

    public static void SetLevel(float level) {
        Check(Endpoint().SetMasterVolumeLevelScalar(level, Guid.Empty));
    }

    public static float GetLevel() {
        float level;
        Check(Endpoint().GetMasterVolumeLevelScalar(out level));
        return level;
    }

    public static void SetMuted(bool muted) {
        Check(Endpoint().SetMute(muted, Guid.Empty));
    }

    public static bool GetMuted() {
        bool muted;
        Check(Endpoint().GetMute(out muted));
        return muted;
    }
}
"@

switch ($Action) {
    "set" { [RonAudio]::SetLevel($Level / 100.0) }
    "mute" { [RonAudio]::SetMuted($true) }
    "unmute" { [RonAudio]::SetMuted($false) }
    "toggle" { [RonAudio]::SetMuted(-not [RonAudio]::GetMuted()) }
}

$result = [ordered]@{
    ok = $true
    level = [int][Math]::Round([RonAudio]::GetLevel() * 100)
    muted = [RonAudio]::GetMuted()
}
$result | ConvertTo-Json -Compress
