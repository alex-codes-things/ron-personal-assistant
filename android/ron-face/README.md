# Ron Face for Android

This is a native Java/Canvas face for Android 5.1.1 and newer. It has no web
view, browser runtime or Wi-Fi listener. A server binds only to
`127.0.0.1:8765`; the computer reaches that private port through `adb forward`
over USB.

## Build settings

- Current Ron Face version: 0.1.16
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
`FaceAnimator`. `RonTabletPager` owns the native two-page slide transition,
corner buttons and tool grid. `SignalServer` receives only compact state and
speech-level signals. `MainActivity` owns lifecycle, health protection and safe
fallback.

The face page has a small translucent left-arrow button in its top-right corner.
It slides the face off to the left while the next page enters from the right.
The second page uses Ron's full generated topographic image as its edge-to-edge
background and has a mirrored return button in its top-left corner. A centred
5-by-3 Stream Deck-style grid sits slightly below the page centre. The large
tiles currently include Ron's face, Spotify and YouTube; the corner arrows stay
small. Ron's face tile returns to the face page, Spotify opens the desktop app,
and YouTube opens a new Brave tab on the laptop. Android's Back action remains
a third safe way to return.

Quick actions travel back through the authenticated USB signal stream. The
tablet can send only fixed allowlisted action IDs—never commands, paths or
arbitrary URLs. Each tile ignores repeat taps while waiting and shows a brief
success or failure outline. Requests time out safely after four seconds.

Ron's face is also touch-reactive. A short, stationary tap produces a soft
direction-aware squish, tiny recoil, one-sided eye scrunch and surprised mouth
before settling smoothly. Tapping ordinary sleeping Ron wakes him with a cute
startled bounce and a real eyelid-opening transition. Thermal or critical-
battery protective sleep deliberately cannot be overridden by touch. Long
presses and finger movement are rejected, and the corner navigation arrow sits
above the face view so using it never triggers the tap reaction. The animation
always starts locally with no USB wait; when connected, a tiny best-effort wake
message keeps the laptop's next face snapshot in sync.

The Windows installer captures ADB's normal first-start daemon messages without
treating them as failures. It retries a slow initial daemon startup once, while
real ADB exit failures still stop the installation with a useful explanation.

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
