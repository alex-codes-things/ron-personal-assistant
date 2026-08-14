# Ron Face for Android

This is a native Java/Canvas face for Android 5.1.1 and newer. It has no web
view, browser runtime or Wi-Fi listener. A server binds only to
`127.0.0.1:8765`; the computer reaches that private port through `adb forward`
over USB.

## Build settings

- Current Ron Face version: 0.1.10
- Android Gradle Plugin 8.7.3
- Gradle 8.9
- JDK 17
- compile/target SDK 35
- minimum SDK 22

Open this directory in Android Studio, select JDK 17, sync, and run it on the
Nexus 7. If a wrapper has not been generated yet, run:

```powershell
gradle wrapper --gradle-version 8.9
.\gradlew.bat installDebug
```

All visual rendering and animation happens in `RonFaceView` and
`FaceAnimator`. `SignalServer` receives only compact state and speech-level
signals. `MainActivity` owns lifecycle, health protection and safe fallback.

The face is intentionally locked to landscape. Place the tablet with its long
edge horizontal.

## Runtime optimisation

- Eye geometry is generated once and transformed through reusable paths.
- Background and eye shaders are cached and repositioned with matrices.
- The drawing loop keeps only one scheduled idle frame callback.
- Blink timing varies by expression while incompatible blink types stay exclusive.
- Every expression chooses among four poses without immediately repeating one.
- Entry accents and rare micro-expressions are specific to Ron's current mood.
- Sleeping `z` bubbles and other decorations are drawn locally and disappear safely.
- Eyelids overlap the eye surface by one scaled pixel to prevent blink-edge fringing.
- Mouth width, curve, tilt and position animate independently for every expression.
- Speech morphs continuously from a closed smile into a soft squircle, widening
  for medium syllables and becoming taller for louder ones.
- Playful happy variants can show a short, automatically retracting tongue peek.
- Sequential animations capture their real value when each stage begins.
- USB signal input is buffered and reuses its bounded line buffer.

Once Android Studio has downloaded the SDK and Gradle, build and install future
versions from the repository root with:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_tablet_face.ps1
```
