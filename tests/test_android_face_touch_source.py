from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JAVA = (
    ROOT
    / "android"
    / "ron-face"
    / "app"
    / "src"
    / "main"
    / "java"
    / "com"
    / "alexcodesthings"
    / "ronface"
)


def source(name: str) -> str:
    return (JAVA / name).read_text(encoding="utf-8")


def test_face_tap_rejects_drag_and_long_press() -> None:
    face = source("RonFaceView.java")

    assert "getScaledTouchSlop" in face
    assert "heldFor <= 550L" in face
    assert "touchMoved" in face
    assert "performClick()" in face


def test_sleep_wake_respects_protective_sleep() -> None:
    activity = source("MainActivity.java")
    animator = source("FaceAnimator.java")

    assert "animator.onFaceTapped(normalisedX, normalisedY, !protectiveSleep)" in activity
    assert "signalServer.sendFaceWake()" in activity
    assert "if (wasSleeping && mayWake)" in animator
    assert 'setExpression("idle")' in animator


def test_tap_animation_has_independent_channels_and_debounce() -> None:
    face = source("RonFaceView.java")
    animator = source("FaceAnimator.java")

    assert "tapOffsetX" in face
    assert "tapMouthOpen" in face
    assert "tapGlowBoost" in face
    assert "now - lastFaceTapAt < 240L" in animator
    assert "reaction.playSequentially" in animator


def test_navigation_arrow_remains_above_touch_surface() -> None:
    pager = source("RonTabletPager.java")

    face_index = pager.index("facePage.addView(\n                faceView")
    arrow_index = pager.index("facePage.addView(nextButton")
    assert face_index < arrow_index
