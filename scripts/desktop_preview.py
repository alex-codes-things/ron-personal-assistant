"""Optional desktop preview of Ron's face; normal startup uses the tablet."""

from __future__ import annotations

import math
import random
import sys
from typing import Literal

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QElapsedTimer,
    QObject,
    QParallelAnimationGroup,
    QPauseAnimation,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSequentialAnimationGroup,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget

type Expression = Literal[
    "idle",
    "listening",
    "thinking",
    "speaking",
    "happy",
    "confused",
    "error",
    "sleeping",
]


def animated_float_property(
    attribute: str,
    signal_name: str,
    notify_signal: Signal,
    minimum: float,
    maximum: float,
) -> Property:
    """Create a repainting float property for Qt animations."""

    def getter(instance: RonFace) -> float:
        return float(getattr(instance, attribute))

    def setter(instance: RonFace, value: float) -> None:
        bounded = max(minimum, min(maximum, float(value)))
        if math.isclose(bounded, getattr(instance, attribute), abs_tol=0.0001):
            return
        setattr(instance, attribute, bounded)
        getattr(instance, signal_name).emit(bounded)
        instance.update()

    return Property(float, getter, setter, notify=notify_signal)


class RonFace(QWidget):
    """Draw and animate Ron's face."""

    left_eye_open_changed = Signal(float)
    right_eye_open_changed = Signal(float)
    left_eye_scale_changed = Signal(float)
    right_eye_scale_changed = Signal(float)
    left_eye_tilt_changed = Signal(float)
    right_eye_tilt_changed = Signal(float)
    glow_strength_changed = Signal(float)
    mouth_open_changed = Signal(float)
    smile_curve_changed = Signal(float)
    face_lift_changed = Signal(float)
    eye_offset_x_changed = Signal(float)
    eye_offset_y_changed = Signal(float)
    error_mix_changed = Signal(float)
    idle_offset_x_changed = Signal(float)
    idle_offset_y_changed = Signal(float)
    micro_left_scale_changed = Signal(float)
    micro_right_scale_changed = Signal(float)
    micro_left_tilt_changed = Signal(float)
    micro_right_tilt_changed = Signal(float)
    micro_smile_changed = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._left_eye_open = 1.0
        self._right_eye_open = 1.0
        self._left_eye_scale = 1.0
        self._right_eye_scale = 1.0
        self._left_eye_tilt = 0.0
        self._right_eye_tilt = 0.0
        self._glow_strength = 0.105
        self._mouth_open = 0.0
        self._smile_curve = 1.0
        self._face_lift = 0.0
        self._eye_offset_x = 0.0
        self._eye_offset_y = 0.0
        self._error_mix = 0.0
        self._idle_offset_x = 0.0
        self._idle_offset_y = 0.0
        self._micro_left_scale = 1.0
        self._micro_right_scale = 1.0
        self._micro_left_tilt = 0.0
        self._micro_right_tilt = 0.0
        self._micro_smile = 1.0
        self._expression: Expression = "idle"
        self._elapsed = QElapsedTimer()
        self._elapsed.start()

        self._motion_timer = QTimer(self)
        self._motion_timer.setInterval(16)
        self._motion_timer.timeout.connect(self.update)
        self._motion_timer.start()

        self.setMinimumSize(500, 400)
        self.animator = FaceAnimationController(self)

    def paintEvent(self, event: QPaintEvent) -> None:
        """Draw the current frame."""
        del event

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        scale = min(self.width() / 900, self.height() / 600)
        centre_x = self.width() / 2
        centre_y = self.height() / 2
        seconds = self._elapsed.elapsed() / 1000

        # Two tiny mismatched waves feel less clock-like than one perfect sine.
        breathing = (
            math.sin(seconds * 0.82) * 0.55
            + math.sin(seconds * 1.37 + 1.1) * 0.22
        )
        if self._expression == "sleeping":
            breathing *= 2.0

        face_x = centre_x + self._idle_offset_x * scale
        face_y = (
            centre_y
            + self._face_lift * scale
            + self._idle_offset_y * scale
            + breathing * scale
        )

        self._draw_background(painter, centre_x, centre_y)

        # Roughly 18% larger than the previous face.
        eye_width = 76 * scale
        eye_height = 130 * scale
        eye_gap = 57 * scale
        eye_top = face_y - 126 * scale + self._eye_offset_y * scale
        eyes_x = face_x + self._eye_offset_x * scale

        left_eye = QRectF(
            eyes_x - eye_gap / 2 - eye_width,
            eye_top,
            eye_width,
            eye_height,
        )
        right_eye = QRectF(
            eyes_x + eye_gap / 2,
            eye_top,
            eye_width,
            eye_height,
        )

        left_scale = self._left_eye_scale * self._micro_left_scale
        right_scale = self._right_eye_scale * self._micro_right_scale
        left_tilt = self._left_eye_tilt + self._micro_left_tilt
        right_tilt = self._right_eye_tilt + self._micro_right_tilt

        self._draw_eye(
            painter,
            left_eye,
            scale,
            left_tilt,
            left_scale,
            self._left_eye_open,
        )
        self._draw_eye(
            painter,
            right_eye,
            scale,
            right_tilt,
            right_scale,
            self._right_eye_open,
        )

        self._draw_mouth(
            painter,
            face_x,
            eye_top,
            eye_height,
            scale,
        )
        painter.end()

    def _draw_background(
        self,
        painter: QPainter,
        centre_x: float,
        centre_y: float,
    ) -> None:
        """Draw a dark display with a subtle central atmosphere."""
        background = QRadialGradient(
            QPointF(centre_x, centre_y - 28),
            max(self.width(), self.height()) * 0.79,
        )
        background.setColorAt(0.0, QColor("#0D1B25"))
        background.setColorAt(0.48, QColor("#07121A"))
        background.setColorAt(1.0, QColor("#02060B"))
        painter.fillRect(self.rect(), QBrush(background))

    def _create_squircle_path(self, rect: QRectF) -> QPainterPath:
        """Create a true superellipse rather than a rounded rectangle."""
        path = QPainterPath()
        centre = rect.center()
        radius_x = rect.width() / 2
        radius_y = rect.height() / 2

        # A higher exponent makes the sides straighter while keeping the
        # corners continuous and soft.
        exponent = 4.4
        points = 96

        for index in range(points + 1):
            angle = (2 * math.pi * index) / points
            cosine = math.cos(angle)
            sine = math.sin(angle)
            x = centre.x() + radius_x * self._signed_power(cosine, 2 / exponent)
            y = centre.y() + radius_y * self._signed_power(sine, 2 / exponent)

            if index == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)

        path.closeSubpath()
        return path

    @staticmethod
    def _signed_power(value: float, power: float) -> float:
        """Raise a signed value to a fractional power."""
        return math.copysign(abs(value) ** power, value)

    @staticmethod
    def _blend_rgb(
        start: tuple[int, int, int],
        end: tuple[int, int, int],
        amount: float,
    ) -> tuple[int, int, int]:
        """Blend two RGB triples."""
        amount = max(0.0, min(1.0, amount))
        return tuple(
            round(start[index] + (end[index] - start[index]) * amount)
            for index in range(3)
        )

    @staticmethod
    def _blend_colour(start: QColor, end: QColor, amount: float) -> QColor:
        """Blend two colours, including alpha."""
        amount = max(0.0, min(1.0, amount))
        return QColor(
            round(start.red() + (end.red() - start.red()) * amount),
            round(start.green() + (end.green() - start.green()) * amount),
            round(start.blue() + (end.blue() - start.blue()) * amount),
            round(start.alpha() + (end.alpha() - start.alpha()) * amount),
        )

    def _draw_eye(
        self,
        painter: QPainter,
        eye: QRectF,
        scale: float,
        tilt: float,
        expression_scale: float,
        blink_visibility: float,
    ) -> None:
        """Draw one eye, then close two eyelids over its fixed surface."""
        eye_height = max(0.40, min(1.08, expression_scale)) * eye.height()
        shaped_eye = QRectF(
            eye.left(),
            eye.center().y() - eye_height / 2,
            eye.width(),
            eye_height,
        )
        eye_path = self._create_squircle_path(shaped_eye)

        painter.save()
        painter.translate(shaped_eye.center())
        painter.rotate(tilt)
        painter.translate(-shaped_eye.center())

        self._draw_eye_glow(painter, eye_path, scale, blink_visibility)
        self._draw_eye_depth(painter, eye_path, scale)
        self._draw_eye_surface(painter, shaped_eye, eye_path, scale)
        self._draw_eyelids(
            painter,
            shaped_eye,
            eye_path,
            blink_visibility,
            scale,
        )
        painter.restore()

    def _draw_eye_glow(
        self,
        painter: QPainter,
        eye_path: QPainterPath,
        scale: float,
        blink_visibility: float,
    ) -> None:
        """Draw a broad atmospheric bloom, not a hard outline."""
        seconds = self._elapsed.elapsed() / 1000
        idle_drift = (
            math.sin(seconds * 0.47 + 0.4) * 0.004
            + math.sin(seconds * 0.83 + 2.1) * 0.002
        )
        blink_fade = blink_visibility**1.7
        strength = min(1.0, self._glow_strength + idle_drift) * blink_fade

        glow_colour = self._blend_rgb(
            (50, 245, 210),
            (255, 155, 82),
            self._error_mix,
        )
        glow_layers = (
            (58, 7),
            (42, 10),
            (28, 16),
            (16, 24),
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)

        for width, maximum_alpha in glow_layers:
            glow_pen = QPen(
                QColor(
                    *glow_colour,
                    round(maximum_alpha * strength),
                )
            )
            glow_pen.setWidthF(width * scale)
            glow_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(glow_pen)
            painter.drawPath(eye_path)

    def _draw_eyelids(
        self,
        painter: QPainter,
        eye: QRectF,
        eye_path: QPainterPath,
        blink_visibility: float,
        scale: float,
    ) -> None:
        """Slide two dark lids over the eye without deforming the eye itself."""
        closure = 1.0 - max(0.0, min(1.0, blink_visibility))
        if closure <= 0.001:
            return

        # The upper lid performs most of a natural blink. Both lids meet
        # below centre instead of squeezing the eye equally from both sides.
        meeting_y = eye.top() + eye.height() * 0.74
        top_edge_y = eye.top() + (meeting_y - eye.top()) * closure
        bottom_edge_y = eye.bottom() - (eye.bottom() - meeting_y) * closure
        top_bow = 2.2 * scale * closure
        bottom_bow = 1.1 * scale * closure

        top_lid = QPainterPath()
        top_lid.moveTo(eye.left(), eye.top() - scale)
        top_lid.lineTo(eye.right(), eye.top() - scale)
        top_lid.lineTo(eye.right(), top_edge_y)
        top_lid.cubicTo(
            eye.right() - eye.width() * 0.28,
            top_edge_y + top_bow,
            eye.left() + eye.width() * 0.28,
            top_edge_y + top_bow,
            eye.left(),
            top_edge_y,
        )
        top_lid.closeSubpath()

        bottom_lid = QPainterPath()
        bottom_lid.moveTo(eye.left(), eye.bottom() + scale)
        bottom_lid.lineTo(eye.right(), eye.bottom() + scale)
        bottom_lid.lineTo(eye.right(), bottom_edge_y)
        bottom_lid.cubicTo(
            eye.right() - eye.width() * 0.28,
            bottom_edge_y - bottom_bow,
            eye.left() + eye.width() * 0.28,
            bottom_edge_y - bottom_bow,
            eye.left(),
            bottom_edge_y,
        )
        bottom_lid.closeSubpath()

        painter.save()
        painter.setClipPath(eye_path)
        painter.setPen(Qt.PenStyle.NoPen)

        lid_gradient = QLinearGradient(
            eye.center().x(),
            eye.top(),
            eye.center().x(),
            eye.bottom(),
        )
        lid_gradient.setColorAt(0.0, QColor("#08171F"))
        lid_gradient.setColorAt(0.5, QColor("#07131B"))
        lid_gradient.setColorAt(1.0, QColor("#061018"))
        painter.setBrush(QBrush(lid_gradient))
        painter.drawPath(top_lid)
        painter.drawPath(bottom_lid)

        # A dim mint edge keeps the final closed slit soft rather than harsh.
        edge_alpha = round(42 * closure)
        edge_pen = QPen(QColor(65, 220, 193, edge_alpha))
        edge_pen.setWidthF(0.7 * scale)
        edge_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(edge_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        edge_y = min(top_edge_y + top_bow, meeting_y)
        painter.drawLine(
            QPointF(eye.left() + 7 * scale, edge_y),
            QPointF(eye.right() - 7 * scale, edge_y),
        )
        painter.restore()

    def _draw_eye_depth(
        self,
        painter: QPainter,
        eye_path: QPainterPath,
        scale: float,
    ) -> None:
        """Add only enough depth to separate the eye from the display."""
        painter.save()
        painter.translate(0, 1.5 * scale)
        painter.fillPath(eye_path, QColor(0, 23, 24, 65))
        painter.restore()

    def _draw_eye_surface(
        self,
        painter: QPainter,
        eye: QRectF,
        eye_path: QPainterPath,
        scale: float,
    ) -> None:
        """Draw a softly luminous mint surface."""
        top_colour = self._blend_colour(
            QColor("#76EFD8"),
            QColor("#FFD38A"),
            self._error_mix,
        )
        middle_colour = self._blend_colour(
            QColor("#43DFC4"),
            QColor("#FFAD5C"),
            self._error_mix,
        )
        bottom_colour = self._blend_colour(
            QColor("#22B9A8"),
            QColor("#DF7348"),
            self._error_mix,
        )
        rim_colour = self._blend_colour(
            QColor(179, 255, 237, 88),
            QColor(255, 220, 160, 95),
            self._error_mix,
        )

        base = QLinearGradient(
            eye.left(),
            eye.top(),
            eye.right(),
            eye.bottom(),
        )
        base.setColorAt(0.0, top_colour)
        base.setColorAt(0.46, middle_colour)
        base.setColorAt(1.0, bottom_colour)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillPath(eye_path, QBrush(base))

        inner_light = QRadialGradient(
            QPointF(
                eye.center().x() - eye.width() * 0.14,
                eye.center().y() - eye.height() * 0.22,
            ),
            eye.height() * 0.86,
        )
        inner_light.setColorAt(0.0, QColor(220, 255, 246, 45))
        inner_light.setColorAt(0.58, QColor(115, 255, 224, 10))
        inner_light.setColorAt(1.0, QColor(0, 65, 62, 36))
        painter.fillPath(eye_path, QBrush(inner_light))

        reflection = QLinearGradient(
            eye.left(),
            eye.top(),
            eye.left(),
            eye.top() + eye.height() * 0.62,
        )
        reflection.setColorAt(0.0, QColor(255, 255, 255, 34))
        reflection.setColorAt(0.28, QColor(255, 255, 255, 12))
        reflection.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.fillPath(eye_path, QBrush(reflection))

        rim_pen = QPen(rim_colour)
        rim_pen.setWidthF(0.95 * scale)
        rim_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(rim_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(eye_path)

    def _draw_mouth(
        self,
        painter: QPainter,
        centre_x: float,
        eye_top: float,
        eye_height: float,
        scale: float,
    ) -> None:
        """Draw a closer, wider and subtly asymmetric smile."""
        mouth_y = eye_top + eye_height + 47 * scale
        mouth_open = self._effective_mouth_open()
        half_width = (25 + mouth_open * 4) * scale

        if mouth_open < 0.05:
            self._draw_closed_smile(painter, centre_x, mouth_y, half_width, scale)
        else:
            self._draw_open_mouth(
                painter,
                centre_x,
                mouth_y,
                half_width,
                scale,
                mouth_open,
            )

    def _effective_mouth_open(self) -> float:
        """Return the speech controller's current mouth value."""
        return self._mouth_open

    def _draw_closed_smile(
        self,
        painter: QPainter,
        centre_x: float,
        mouth_y: float,
        half_width: float,
        scale: float,
    ) -> None:
        """Draw Ron's resting smile."""
        smile_curve = self._smile_curve * self._micro_smile
        smile = QPainterPath()
        smile.moveTo(centre_x - half_width, mouth_y - 0.5 * scale)
        smile.cubicTo(
            centre_x - half_width * 0.44,
            mouth_y + 8.2 * smile_curve * scale,
            centre_x + half_width * 0.40,
            mouth_y + 8.8 * smile_curve * scale,
            centre_x + half_width,
            mouth_y - 1.3 * scale,
        )

        # A faint lower echo softens the mouth against the display.
        lower_glow_pen = QPen(QColor(53, 239, 207, 18))
        lower_glow_pen.setWidthF(11 * scale)
        lower_glow_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(lower_glow_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(smile.translated(0, 1.5 * scale))

        smile_pen = QPen(QColor("#4CE5C8"))
        smile_pen.setWidthF(3.8 * scale)
        smile_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(smile_pen)
        painter.drawPath(smile)

    def _draw_open_mouth(
        self,
        painter: QPainter,
        centre_x: float,
        mouth_y: float,
        half_width: float,
        scale: float,
        mouth_open: float,
    ) -> None:
        """Draw the speaking mouth."""
        open_height = (7 + mouth_open * 25) * scale
        mouth = QPainterPath()
        mouth.moveTo(centre_x - half_width, mouth_y)
        mouth.cubicTo(
            centre_x - half_width * 0.48,
            mouth_y - open_height * 0.30,
            centre_x + half_width * 0.48,
            mouth_y - open_height * 0.30,
            centre_x + half_width,
            mouth_y,
        )
        mouth.cubicTo(
            centre_x + half_width * 0.48,
            mouth_y + open_height,
            centre_x - half_width * 0.48,
            mouth_y + open_height,
            centre_x - half_width,
            mouth_y,
        )
        mouth.closeSubpath()

        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillPath(mouth, QColor("#05231F"))
        mouth_rim = QPen(QColor("#4DE4C7"))
        mouth_rim.setWidthF(3.0 * scale)
        mouth_rim.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(mouth_rim)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(mouth)

    def set_expression(self, expression: Expression) -> None:
        """Ask the animation controller to transition into a state."""
        self.animator.set_expression(expression)

    def set_speech_level(self, level: float | None) -> None:
        """Provide live audio amplitude, or None for simulated speech."""
        self.animator.set_speech_level(level)

    left_eye_open = animated_float_property(
        "_left_eye_open",
        "left_eye_open_changed",
        left_eye_open_changed,
        0.0,
        1.0,
    )
    right_eye_open = animated_float_property(
        "_right_eye_open",
        "right_eye_open_changed",
        right_eye_open_changed,
        0.0,
        1.0,
    )
    left_eye_scale = animated_float_property(
        "_left_eye_scale",
        "left_eye_scale_changed",
        left_eye_scale_changed,
        0.35,
        1.20,
    )
    right_eye_scale = animated_float_property(
        "_right_eye_scale",
        "right_eye_scale_changed",
        right_eye_scale_changed,
        0.35,
        1.20,
    )
    left_eye_tilt = animated_float_property(
        "_left_eye_tilt",
        "left_eye_tilt_changed",
        left_eye_tilt_changed,
        -12.0,
        12.0,
    )
    right_eye_tilt = animated_float_property(
        "_right_eye_tilt",
        "right_eye_tilt_changed",
        right_eye_tilt_changed,
        -12.0,
        12.0,
    )
    glow_strength = animated_float_property(
        "_glow_strength",
        "glow_strength_changed",
        glow_strength_changed,
        0.0,
        1.0,
    )
    mouth_open = animated_float_property(
        "_mouth_open",
        "mouth_open_changed",
        mouth_open_changed,
        0.0,
        1.0,
    )
    smile_curve = animated_float_property(
        "_smile_curve",
        "smile_curve_changed",
        smile_curve_changed,
        0.35,
        1.40,
    )
    face_lift = animated_float_property(
        "_face_lift",
        "face_lift_changed",
        face_lift_changed,
        -10.0,
        10.0,
    )
    eye_offset_x = animated_float_property(
        "_eye_offset_x",
        "eye_offset_x_changed",
        eye_offset_x_changed,
        -10.0,
        10.0,
    )
    eye_offset_y = animated_float_property(
        "_eye_offset_y",
        "eye_offset_y_changed",
        eye_offset_y_changed,
        -10.0,
        10.0,
    )
    error_mix = animated_float_property(
        "_error_mix",
        "error_mix_changed",
        error_mix_changed,
        0.0,
        1.0,
    )
    idle_offset_x = animated_float_property(
        "_idle_offset_x",
        "idle_offset_x_changed",
        idle_offset_x_changed,
        -4.0,
        4.0,
    )
    idle_offset_y = animated_float_property(
        "_idle_offset_y",
        "idle_offset_y_changed",
        idle_offset_y_changed,
        -3.0,
        3.0,
    )
    micro_left_scale = animated_float_property(
        "_micro_left_scale",
        "micro_left_scale_changed",
        micro_left_scale_changed,
        0.70,
        1.15,
    )
    micro_right_scale = animated_float_property(
        "_micro_right_scale",
        "micro_right_scale_changed",
        micro_right_scale_changed,
        0.70,
        1.15,
    )
    micro_left_tilt = animated_float_property(
        "_micro_left_tilt",
        "micro_left_tilt_changed",
        micro_left_tilt_changed,
        -6.0,
        6.0,
    )
    micro_right_tilt = animated_float_property(
        "_micro_right_tilt",
        "micro_right_tilt_changed",
        micro_right_tilt_changed,
        -6.0,
        6.0,
    )
    micro_smile = animated_float_property(
        "_micro_smile",
        "micro_smile_changed",
        micro_smile_changed,
        0.75,
        1.25,
    )


class FaceAnimationController(QObject):
    """Coordinate pose, eyelid, idle, micro and speech animation channels."""

    POSES: dict[Expression, dict[str, float]] = {
        "idle": {
            "left_eye_scale": 1.0,
            "right_eye_scale": 1.0,
            "left_eye_tilt": 0.0,
            "right_eye_tilt": 0.0,
            "glow_strength": 0.105,
            "smile_curve": 1.0,
            "face_lift": 0.0,
            "eye_offset_x": 0.0,
            "eye_offset_y": 0.0,
            "error_mix": 0.0,
        },
        "listening": {
            "left_eye_scale": 1.055,
            "right_eye_scale": 1.055,
            "left_eye_tilt": 0.0,
            "right_eye_tilt": 0.0,
            "glow_strength": 0.24,
            "smile_curve": 0.92,
            "face_lift": -2.0,
            "eye_offset_x": 0.0,
            "eye_offset_y": 0.0,
            "error_mix": 0.0,
        },
        "thinking": {
            "left_eye_scale": 0.82,
            "right_eye_scale": 0.98,
            "left_eye_tilt": -1.4,
            "right_eye_tilt": 1.4,
            "glow_strength": 0.135,
            "smile_curve": 0.82,
            "face_lift": -0.5,
            "eye_offset_x": 0.0,
            "eye_offset_y": -4.0,
            "error_mix": 0.0,
        },
        "speaking": {
            "left_eye_scale": 0.97,
            "right_eye_scale": 0.97,
            "left_eye_tilt": 0.0,
            "right_eye_tilt": 0.0,
            "glow_strength": 0.18,
            "smile_curve": 1.0,
            "face_lift": -0.5,
            "eye_offset_x": 0.0,
            "eye_offset_y": 0.0,
            "error_mix": 0.0,
        },
        "happy": {
            "left_eye_scale": 0.86,
            "right_eye_scale": 0.86,
            "left_eye_tilt": -1.2,
            "right_eye_tilt": 1.2,
            "glow_strength": 0.15,
            "smile_curve": 1.30,
            "face_lift": -1.0,
            "eye_offset_x": 0.0,
            "eye_offset_y": 1.0,
            "error_mix": 0.0,
        },
        "confused": {
            "left_eye_scale": 0.76,
            "right_eye_scale": 1.0,
            "left_eye_tilt": -4.0,
            "right_eye_tilt": 2.0,
            "glow_strength": 0.12,
            "smile_curve": 0.72,
            "face_lift": 0.0,
            "eye_offset_x": 1.5,
            "eye_offset_y": 0.0,
            "error_mix": 0.0,
        },
        "error": {
            "left_eye_scale": 0.62,
            "right_eye_scale": 0.62,
            "left_eye_tilt": 1.0,
            "right_eye_tilt": -1.0,
            "glow_strength": 0.30,
            "smile_curve": 0.42,
            "face_lift": 3.5,
            "eye_offset_x": 0.0,
            "eye_offset_y": 0.0,
            "error_mix": 1.0,
        },
        "sleeping": {
            "left_eye_scale": 1.0,
            "right_eye_scale": 1.0,
            "left_eye_tilt": 0.0,
            "right_eye_tilt": 0.0,
            "glow_strength": 0.045,
            "smile_curve": 0.72,
            "face_lift": 2.0,
            "eye_offset_x": 0.0,
            "eye_offset_y": 0.0,
            "error_mix": 0.0,
        },
    }

    SPRING_PROPERTIES = {
        "left_eye_scale",
        "right_eye_scale",
        "left_eye_tilt",
        "right_eye_tilt",
        "face_lift",
    }

    def __init__(self, face: RonFace) -> None:
        super().__init__(face)
        self.face = face
        self._pose_group: QParallelAnimationGroup | None = None
        self._blink_group: QSequentialAnimationGroup | None = None
        self._drift_group: QParallelAnimationGroup | None = None
        self._micro_group: QParallelAnimationGroup | None = None
        self._speech_group: QParallelAnimationGroup | None = None
        self._accent_group: QSequentialAnimationGroup | None = None
        self._speech_level: float | None = None

        self._blink_timer = self._single_shot_timer(self._blink)
        self._drift_timer = self._single_shot_timer(self._start_idle_drift)
        self._micro_timer = self._single_shot_timer(self._start_micro_expression)
        self._speech_timer = self._single_shot_timer(self._next_speech_shape)

        self._schedule_blink(initial=True)
        self._schedule_drift(initial=True)
        self._schedule_micro_expression()

    def _single_shot_timer(self, callback) -> QTimer:
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(callback)
        return timer

    def set_expression(self, expression: Expression) -> None:
        """Smoothly move all pose channels into an expression."""
        if expression not in self.POSES:
            raise ValueError(f"Unknown Ron expression: {expression}")

        previous = self.face._expression
        self.face._expression = expression
        self._stop_animation("_pose_group")
        self._stop_animation("_drift_group")
        self._stop_animation("_accent_group")
        self._reset_micro_channels()

        if expression == "sleeping":
            self._blink_timer.stop()
            self._stop_animation("_blink_group")
        elif previous == "sleeping":
            self._stop_animation("_blink_group")
            self._schedule_blink()
        else:
            self._schedule_blink()

        if expression == "speaking":
            self._schedule_speech_shape(immediate=True)
        else:
            self._stop_speaking_motion()

        duration = 430 if previous == "sleeping" else 330
        group = QParallelAnimationGroup(self)

        for property_name, target in self.POSES[expression].items():
            animation = self._pose_animation(property_name, target, duration)
            group.addAnimation(animation)

        if expression == "sleeping":
            group.addAnimation(
                self._float_animation("left_eye_open", 0.025, 520)
            )
            group.addAnimation(
                self._float_animation("right_eye_open", 0.025, 540)
            )
        elif previous == "sleeping":
            group.addAnimation(
                self._float_animation("left_eye_open", 1.0, 430)
            )
            group.addAnimation(
                self._float_animation("right_eye_open", 1.0, 455)
            )

        group.finished.connect(self._pose_finished)
        self._pose_group = group
        group.start()

        if expression == "listening":
            QTimer.singleShot(190, self._listening_acknowledgement)
        elif expression == "error":
            QTimer.singleShot(duration + 20, self._error_flash)

        self._schedule_drift()
        self._schedule_micro_expression()

    def _pose_animation(
        self,
        property_name: str,
        target: float,
        duration: int,
    ) -> QPropertyAnimation:
        start = float(self.face.property(property_name))
        animation = self._float_animation(property_name, target, duration)
        animation.setEasingCurve(QEasingCurve.Type.InOutCubic)

        if property_name in self.SPRING_PROPERTIES and not math.isclose(start, target):
            overshoot = target + (target - start) * 0.10
            animation.setKeyValueAt(0.78, overshoot)

        return animation

    def _pose_finished(self) -> None:
        if self._pose_group is not None:
            self._pose_group.deleteLater()
        self._pose_group = None

    def _schedule_blink(self, initial: bool = False) -> None:
        if self.face._expression == "sleeping":
            return
        delay = random.randint(1400, 2600) if initial else random.randint(3000, 7200)
        self._blink_timer.start(delay)

    def _blink(
        self,
        force_double: bool = False,
        acknowledgement: bool = False,
    ) -> None:
        if self.face._expression == "sleeping" or self._blink_group is not None:
            return

        partial = not acknowledgement and random.random() < 0.09
        target = 0.34 if partial else 0.025
        double = force_double or (not acknowledgement and random.random() < 0.14)
        group = QSequentialAnimationGroup(self)
        group.addAnimation(self._single_blink(target, acknowledgement))

        if double:
            group.addAnimation(QPauseAnimation(random.randint(90, 145)))
            group.addAnimation(self._single_blink(0.025, False))

        group.finished.connect(self._blink_finished)
        self._blink_group = group
        group.start()

    def _single_blink(
        self,
        target: float,
        acknowledgement: bool,
    ) -> QParallelAnimationGroup:
        group = QParallelAnimationGroup()
        group.addAnimation(self._eyelid_sequence("left_eye_open", target, 0))
        delay = 8 if acknowledgement else random.randint(8, 15)
        group.addAnimation(self._eyelid_sequence("right_eye_open", target, delay))
        return group

    def _eyelid_sequence(
        self,
        property_name: str,
        target: float,
        delay: int,
    ) -> QSequentialAnimationGroup:
        sequence = QSequentialAnimationGroup()
        if delay:
            sequence.addAnimation(QPauseAnimation(delay))

        close = self._float_animation(property_name, target, 54)
        close.setEasingCurve(QEasingCurve.Type.InQuad)
        reopen = self._float_animation(property_name, 1.0, 112)
        reopen.setEasingCurve(QEasingCurve.Type.OutCubic)

        sequence.addAnimation(close)
        sequence.addAnimation(QPauseAnimation(26 if target < 0.1 else 10))
        sequence.addAnimation(reopen)
        return sequence

    def _blink_finished(self) -> None:
        if self._blink_group is not None:
            self._blink_group.deleteLater()
        self._blink_group = None
        self._schedule_blink()

    def _listening_acknowledgement(self) -> None:
        if self.face._expression == "listening":
            self._blink(acknowledgement=True)

    def _schedule_drift(self, initial: bool = False) -> None:
        delay = random.randint(1500, 2800) if initial else random.randint(3200, 6800)
        self._drift_timer.start(delay)

    def _start_idle_drift(self) -> None:
        self._stop_animation("_drift_group")
        expression = self.face._expression
        group = QParallelAnimationGroup(self)
        duration = random.randint(1500, 2800)

        if expression in {"idle", "happy", "sleeping"}:
            x_range = 0.65 if expression == "sleeping" else 1.35
            y_range = 0.40 if expression == "sleeping" else 0.80
            group.addAnimation(
                self._float_animation(
                    "idle_offset_x",
                    random.uniform(-x_range, x_range),
                    duration,
                    QEasingCurve.Type.InOutSine,
                )
            )
            group.addAnimation(
                self._float_animation(
                    "idle_offset_y",
                    random.uniform(-y_range, y_range),
                    duration,
                    QEasingCurve.Type.InOutSine,
                )
            )
        elif expression == "thinking":
            group.addAnimation(
                self._float_animation(
                    "eye_offset_x",
                    random.uniform(-3.2, 3.2),
                    duration,
                    QEasingCurve.Type.InOutSine,
                )
            )
        else:
            group.addAnimation(
                self._float_animation(
                    "idle_offset_x",
                    0.0,
                    duration,
                    QEasingCurve.Type.InOutSine,
                )
            )
            group.addAnimation(
                self._float_animation(
                    "idle_offset_y",
                    0.0,
                    duration,
                    QEasingCurve.Type.InOutSine,
                )
            )

        group.finished.connect(self._drift_finished)
        self._drift_group = group
        group.start()

    def _drift_finished(self) -> None:
        if self._drift_group is not None:
            self._drift_group.deleteLater()
        self._drift_group = None
        self._schedule_drift()

    def _schedule_micro_expression(self) -> None:
        self._micro_timer.start(random.randint(8000, 20000))

    def _start_micro_expression(self) -> None:
        if self.face._expression not in {"idle", "happy"}:
            self._schedule_micro_expression()
            return

        choice = random.choice(("soften", "curious", "smile", "double_blink"))
        if choice == "double_blink":
            self._blink(force_double=True)
            self._schedule_micro_expression()
            return

        targets = {
            "soften": {
                "micro_left_scale": 0.94,
                "micro_right_scale": 0.94,
            },
            "curious": {
                "micro_left_scale": 0.90,
                "micro_right_scale": 1.035,
                "micro_left_tilt": -1.2,
                "micro_right_tilt": 0.6,
            },
            "smile": {
                "micro_smile": 1.10,
                "micro_left_scale": 0.96,
                "micro_right_scale": 0.96,
            },
        }[choice]

        group = QParallelAnimationGroup(self)
        for property_name, target in targets.items():
            resting = 0.0 if "tilt" in property_name else 1.0
            group.addAnimation(
                self._pulse_sequence(property_name, target, resting)
            )

        group.finished.connect(self._micro_finished)
        self._micro_group = group
        group.start()

    def _pulse_sequence(
        self,
        property_name: str,
        target: float,
        resting: float,
    ) -> QSequentialAnimationGroup:
        sequence = QSequentialAnimationGroup()
        sequence.addAnimation(
            self._float_animation(
                property_name,
                target,
                random.randint(280, 430),
                QEasingCurve.Type.InOutCubic,
            )
        )
        sequence.addAnimation(QPauseAnimation(random.randint(380, 720)))
        sequence.addAnimation(
            self._float_animation(
                property_name,
                resting,
                random.randint(420, 620),
                QEasingCurve.Type.InOutCubic,
            )
        )
        return sequence

    def _micro_finished(self) -> None:
        if self._micro_group is not None:
            self._micro_group.deleteLater()
        self._micro_group = None
        self._schedule_micro_expression()

    def _reset_micro_channels(self) -> None:
        self._stop_animation("_micro_group")
        for property_name, value in {
            "micro_left_scale": 1.0,
            "micro_right_scale": 1.0,
            "micro_left_tilt": 0.0,
            "micro_right_tilt": 0.0,
            "micro_smile": 1.0,
        }.items():
            self.face.setProperty(property_name, value)

    def set_speech_level(self, level: float | None) -> None:
        """Use real audio amplitude when supplied; otherwise use syllable shapes."""
        self._speech_level = None if level is None else max(0.0, min(1.0, level))
        if self.face._expression != "speaking":
            return

        if self._speech_level is None:
            self._schedule_speech_shape(immediate=True)
        else:
            self._speech_timer.stop()
            self._animate_speech_target(0.03 + self._speech_level * 0.82)

    def _schedule_speech_shape(self, immediate: bool = False) -> None:
        if self.face._expression != "speaking" or self._speech_level is not None:
            return
        self._speech_timer.start(20 if immediate else random.randint(70, 165))

    def _next_speech_shape(self) -> None:
        if self.face._expression != "speaking" or self._speech_level is not None:
            return

        shapes = (0.04, 0.12, 0.22, 0.38, 0.56, 0.72)
        weights = (12, 14, 20, 24, 19, 11)
        target = random.choices(shapes, weights=weights, k=1)[0]
        self._animate_speech_target(target)
        self._schedule_speech_shape()

    def _animate_speech_target(self, target: float) -> None:
        self._stop_animation("_speech_group")
        group = QParallelAnimationGroup(self)
        duration = random.randint(65, 125)
        group.addAnimation(
            self._float_animation(
                "mouth_open",
                target,
                duration,
                QEasingCurve.Type.OutCubic,
            )
        )

        eye_compression = 1.0 - min(0.035, target * 0.045)
        group.addAnimation(
            self._float_animation(
                "micro_left_scale",
                eye_compression,
                duration + 30,
                QEasingCurve.Type.InOutSine,
            )
        )
        group.addAnimation(
            self._float_animation(
                "micro_right_scale",
                eye_compression,
                duration + 30,
                QEasingCurve.Type.InOutSine,
            )
        )
        group.finished.connect(self._speech_finished)
        self._speech_group = group
        group.start()

    def _speech_finished(self) -> None:
        if self._speech_group is not None:
            self._speech_group.deleteLater()
        self._speech_group = None

    def _stop_speaking_motion(self) -> None:
        self._speech_timer.stop()
        self._stop_animation("_speech_group")
        group = QParallelAnimationGroup(self)
        group.addAnimation(self._float_animation("mouth_open", 0.0, 170))
        group.addAnimation(self._float_animation("micro_left_scale", 1.0, 190))
        group.addAnimation(self._float_animation("micro_right_scale", 1.0, 190))
        group.finished.connect(self._speech_finished)
        self._speech_group = group
        group.start()

    def _error_flash(self) -> None:
        if self.face._expression != "error":
            return
        self._stop_animation("_accent_group")
        group = QSequentialAnimationGroup(self)
        group.addAnimation(
            self._float_animation(
                "glow_strength",
                0.46,
                75,
                QEasingCurve.Type.OutCubic,
            )
        )
        group.addAnimation(
            self._float_animation(
                "glow_strength",
                0.30,
                210,
                QEasingCurve.Type.OutCubic,
            )
        )
        group.finished.connect(self._accent_finished)
        self._accent_group = group
        group.start()

    def _accent_finished(self) -> None:
        if self._accent_group is not None:
            self._accent_group.deleteLater()
        self._accent_group = None

    def _float_animation(
        self,
        property_name: str,
        target: float,
        duration: int,
        curve: QEasingCurve.Type = QEasingCurve.Type.InOutCubic,
    ) -> QPropertyAnimation:
        animation = QPropertyAnimation(self.face, property_name.encode())
        animation.setDuration(duration)
        animation.setStartValue(float(self.face.property(property_name)))
        animation.setEndValue(target)
        animation.setEasingCurve(curve)
        return animation

    def _stop_animation(self, attribute: str) -> None:
        animation = getattr(self, attribute)
        if animation is not None:
            animation.stop()
            animation.deleteLater()
            setattr(self, attribute, None)


class RonWindow(QMainWindow):
    """Ron's main desktop window."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Ron")
        self.resize(900, 600)
        self.setMinimumSize(500, 400)

        self.face = RonFace()
        self.setCentralWidget(self.face)


def main() -> None:
    """Start Ron."""
    app = QApplication(sys.argv)
    window = RonWindow()
    window.show()
    raise SystemExit(app.exec())


if __name__ == "__main__":
    main()
