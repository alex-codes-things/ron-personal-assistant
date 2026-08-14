# Ron

Ron is an open-source personal assistant built from scratch in Python. His
animated face runs as a native Android app on a dedicated Nexus 7 while his
brain and future add-ons run on the computer.

Current native face release: **0.1.10**.

## How the pieces fit together

```text
Python add-ons -> Coordinator -> TabletFaceDisplay -> ADB USB tunnel
                                                    -> Native Android face
```

The computer sends small semantic messages such as `listening`, `thinking`
and `speaking`. The tablet creates every animation locally, so a normal frame
does not depend on the computer or network timing.

Each expression now selects from four non-repeating poses and matching entry
motions. Small local details—happy sparkles, curious question marks, thinking
dots, attentive rays and occasional sleeping `z` bubbles—give Ron more
personality without adding traffic to the USB connection.

The mouth is expression-aware and morphs smoothly between a closed curve and
a soft cubed speech shape that echoes Ron's eyes. Its width, position and tilt
follow the selected face variant, while playful happy animations can briefly
show a soft coral tongue.

## Project structure

```text
RonPersonal/
|-- android/ron-face/             Native Nexus 7 face application
|   `-- app/src/main/java/com/alexcodesthings/ronface/
|       |-- MainActivity.java     Android lifecycle and safety controller
|       |-- RonFaceView.java      Face drawing
|       |-- FaceAnimator.java     Local animation system
|       |-- SignalServer.java     Private USB signal receiver
|       |-- Protocol.java         Shared protocol constants and validation
|       |-- BatteryHealthMonitor.java
|       `-- BootReceiver.java
|-- scripts/face_demo.py          Safe connection and animation test
|-- src/ron/
|   |-- __main__.py               Tiny executable entry point
|   |-- app.py                    Creates and starts Ron's systems
|   |-- core/
|   |   |-- events.py             Shared event names and data
|   |   `-- coordinator.py        Thread-safe add-on communication
|   `-- display/
|       |-- face.py               Public face-system adapter
|       |-- tablet_client.py      Reconnect and state synchronisation
|       |-- adb.py                Safe, exact-device ADB operations
|       |-- protocol.py           Bounded JSON-lines codec
|       `-- desktop_preview.py    Optional old PySide6 preview
`-- tests/                        Fast Python safety tests
```

`main` does not contain face drawing or transport code. Future systems should
subscribe to and publish `RonEvent` objects through `Coordinator`.

## Requirements

- Windows computer with Python 3.12 and Git
- Android Studio with JDK 17, Android SDK 35 and Platform Tools
- Nexus 7 running Android 5.1.1 (API 22)
- A data-capable USB cable

No Wi-Fi or browser is used for face signals.

## 1. Set up Python

From the project folder in PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m pytest
```

If `adb.exe` is not on `PATH`, set its absolute path for the current terminal:

```powershell
$env:RON_ADB_PATH = "C:\Users\YOUR_NAME\AppData\Local\Android\Sdk\platform-tools\adb.exe"
```

## 2. Build and install the native face

1. Enable Developer options and USB debugging on the Nexus 7.
2. Connect the tablet with USB and accept its trust prompt.
3. Open `android/ron-face` as a project in Android Studio.
4. Set the Gradle JDK to 17 and allow the project to sync.
5. Select the Nexus 7 and press **Run**.

The project requires Gradle 8.9. If Android Studio asks for a Gradle wrapper,
run this once from `android/ron-face` with Gradle 8.9 installed:

```powershell
gradle wrapper --gradle-version 8.9
.\gradlew.bat installDebug
```

The app starts at boot, stays in landscape and uses immersive full-screen mode.

After the first Android Studio setup, future face versions can be built and
installed without using the Android Studio interface:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_tablet_face.ps1
```

The installer validates Java, Gradle, the Android SDK, the exact connected
tablet, the APK build result and the installed version. It never clears app
data automatically.

## 3. Lock Ron to the correct tablet

Check the device connection:

```powershell
adb devices -l
```

With one authorised device connected, Ron remembers its serial automatically.
If you connect multiple Android devices, specify the Nexus 7 explicitly:

```powershell
$env:RON_TABLET_SERIAL = "YOUR_TABLET_SERIAL"
```

## 4. Test, then run Ron

Run the complete face demonstration:

```powershell
python scripts\face_demo.py
```

Then start Ron normally:

```powershell
python -m ron
```

The Python program does not open a computer window. It starts the tablet app,
creates a temporary ADB port forward and reconnects automatically after cable,
ADB, app or tablet restarts.

## Built-in failure safety

- Exact tablet serial selection; Ron never guesses when several devices exist.
- Separate reporting for disconnected, offline and unauthorised devices.
- Bounded ADB calls, socket messages, reconnect waits and shutdown waits.
- Loopback-only tablet server reached through ADB USB forwarding.
- First-use pairing token, protocol-version handshake and constant-time check.
- Maximum 8 KiB JSON messages with malformed-input rejection.
- Heartbeats and automatic stale-connection closure.
- Full state snapshot after every reconnect.
- Ordered reliable state events and replaceable rate-limited speech samples.
- Queue-overflow snapshot recovery instead of replaying stale animations.
- Local idle fallback after signal loss and sleep fallback after five minutes.
- Stale speech watchdog so the mouth cannot freeze open.
- Battery, charging and temperature reporting.
- Automatic dimming at 40 C and protective sleep at 45 C or critical battery.
- Errors in one future add-on are isolated by the coordinator.

## Re-pairing

The tablet accepts a pairing token only when it has none. To deliberately reset
pairing, stop Ron, clear **Ron Face** app data on Android, delete
`data/face_pairing_token` on the computer, and start Ron again over USB.

## License

MIT. See `LICENSE`.
