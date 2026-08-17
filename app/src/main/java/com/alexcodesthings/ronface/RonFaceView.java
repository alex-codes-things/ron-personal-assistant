package com.alexcodesthings.ronface;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.LinearGradient;
import android.graphics.Matrix;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RadialGradient;
import android.graphics.RectF;
import android.graphics.Shader;
import android.graphics.Typeface;
import android.os.SystemClock;
import android.util.AttributeSet;
import android.view.View;

final class RonFaceView extends View {
    private static final int SQUIRCLE_POINTS = 72;
    private static final RectF UNIT_EYE_RECT = new RectF(-0.5f, -0.5f, 0.5f, 0.5f);
    private static final RectF UNIT_SHADER_RECT = new RectF(0f, 0f, 1f, 1f);
    private static final RectF UNIT_RADIAL_RECT = new RectF(-1f, -1f, 1f, 1f);

    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.DITHER_FLAG);
    private final Matrix pathMatrix = new Matrix();
    private final Matrix shaderMatrix = new Matrix();
    private final Path unitSquirclePath = new Path();
    private final Path leftEyePath = new Path();
    private final Path rightEyePath = new Path();
    private final Path topLidPath = new Path();
    private final Path bottomLidPath = new Path();
    private final Path lidClipPath = new Path();
    private final Path lidSeamPath = new Path();
    private final Path mouthPath = new Path();
    private final Path mouthDetailPath = new Path();
    private final Path decorationPath = new Path();
    private final RectF leftEyeBounds = new RectF();
    private final RectF rightEyeBounds = new RectF();
    private final RectF leftShapedBounds = new RectF();
    private final RectF rightShapedBounds = new RectF();
    private final RectF lidEyeBounds = new RectF();
    private final RectF mouthBounds = new RectF();
    private final RectF radialDestinationRect = new RectF();
    private final long startedAt = SystemClock.elapsedRealtime();
    private final Runnable frameRunnable = this::invalidate;

    private Shader backgroundShader;
    private Shader eyeBaseShader;
    private Shader eyeHighlightShader;
    private Shader eyeSheenShader;
    private Shader eyelidShader;
    private float eyeShaderErrorMix = Float.NaN;

    private String expression = "idle";
    private float leftEyeOpen = 1f;
    private float rightEyeOpen = 1f;
    private float leftEyeScale = 1f;
    private float rightEyeScale = 1f;
    private float leftEyeTilt = 0f;
    private float rightEyeTilt = 0f;
    private float glowStrength = 0.14f;
    private float mouthOpen = 0f;
    private float smileCurve = 1f;
    private float mouthWidthScale = 1f;
    private float mouthTilt = 0f;
    private float mouthOffsetX = 0f;
    private float mouthOffsetY = 0f;
    private float tongueAmount = 0f;
    private float faceLift = 0f;
    private float eyeOffsetX = 0f;
    private float eyeOffsetY = 0f;
    private float idleOffsetX = 0f;
    private float idleOffsetY = 0f;
    private float microLeftScale = 1f;
    private float microRightScale = 1f;
    private float microLeftTilt = 0f;
    private float microRightTilt = 0f;
    private float microSmile = 1f;
    private float errorMix = 0f;
    private float blinkDip = 0f;
    private String decoration = "none";
    private float decorationProgress = 0f;

    RonFaceView(Context context) {
        super(context);
        initialise();
    }

    RonFaceView(Context context, AttributeSet attributes) {
        super(context, attributes);
        initialise();
    }

    private void initialise() {
        setBackgroundColor(Color.rgb(2, 6, 11));
        setKeepScreenOn(true);
        setLayerType(View.LAYER_TYPE_HARDWARE, null);
        setContentDescription("Ron's animated face");
        buildUnitSquircle();
        eyeHighlightShader = new RadialGradient(
                0f,
                0f,
                1f,
                new int[]{
                        Color.argb(48, 226, 255, 246),
                        Color.argb(13, 143, 252, 229),
                        Color.TRANSPARENT
                },
                new float[]{0f, 0.58f, 1f},
                Shader.TileMode.CLAMP
        );
        eyeSheenShader = new LinearGradient(
                0f,
                0f,
                0f,
                1f,
                new int[]{
                        Color.argb(38, 244, 255, 252),
                        Color.argb(10, 244, 255, 252),
                        Color.TRANSPARENT
                },
                new float[]{0f, 0.174f, 0.62f},
                Shader.TileMode.CLAMP
        );
        eyelidShader = new LinearGradient(
                0f,
                0f,
                0f,
                1f,
                new int[]{Color.rgb(8, 23, 31), Color.rgb(7, 19, 27), Color.rgb(6, 16, 24)},
                new float[]{0f, 0.5f, 1f},
                Shader.TileMode.CLAMP
        );
    }

    @Override
    protected void onSizeChanged(int width, int height, int oldWidth, int oldHeight) {
        super.onSizeChanged(width, height, oldWidth, oldHeight);
        if (width <= 0 || height <= 0) {
            backgroundShader = null;
            return;
        }
        backgroundShader = new RadialGradient(
                width / 2f,
                height / 2f - 28f,
                Math.max(width, height) * 0.79f,
                new int[]{Color.rgb(10, 23, 32), Color.rgb(5, 14, 21), Color.rgb(1, 5, 9)},
                new float[]{0f, 0.52f, 1f},
                Shader.TileMode.CLAMP
        );
    }

    @Override
    protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        float scale = Math.min(getWidth() / 900f, getHeight() / 600f);
        float centreX = getWidth() / 2f;
        float centreY = getHeight() / 2f;
        float seconds = (SystemClock.elapsedRealtime() - startedAt) / 1000f;

        drawBackground(canvas);

        float breathing = (
                (float) Math.sin(seconds * 0.82f) * 0.55f
                        + (float) Math.sin(seconds * 1.37f + 1.1f) * 0.22f
        );
        if ("sleeping".equals(expression)) {
            breathing *= 2f;
        }

        float faceX = centreX + idleOffsetX * scale;
        float faceY = centreY + (faceLift + idleOffsetY + breathing) * scale;
        // These proportions fill a Nexus 7 landscape screen without making the
        // face feel crowded. The eyes remain distinctly taller than they are wide.
        float eyeWidth = 90f * scale;
        float eyeHeight = 150f * scale;
        float eyeGap = 68f * scale;
        float restingEyeTop = faceY - 144f * scale + eyeOffsetY * scale;
        float eyeTop = restingEyeTop + blinkDip * scale;
        float eyesX = faceX + eyeOffsetX * scale;

        leftEyeBounds.set(
                eyesX - eyeGap / 2f - eyeWidth,
                eyeTop,
                eyesX - eyeGap / 2f,
                eyeTop + eyeHeight
        );
        rightEyeBounds.set(
                eyesX + eyeGap / 2f,
                eyeTop,
                eyesX + eyeGap / 2f + eyeWidth,
                eyeTop + eyeHeight
        );

        float glowDrift = (
                (float) Math.sin(seconds * 0.47f + 0.4f) * 0.008f
                        + (float) Math.sin(seconds * 0.83f + 2.1f) * 0.004f
        );

        drawEye(
                canvas,
                leftEyeBounds,
                leftShapedBounds,
                leftEyePath,
                scale,
                leftEyeTilt + microLeftTilt,
                leftEyeScale * microLeftScale,
                leftEyeOpen,
                glowDrift
        );
        drawEye(
                canvas,
                rightEyeBounds,
                rightShapedBounds,
                rightEyePath,
                scale,
                rightEyeTilt + microRightTilt,
                rightEyeScale * microRightScale,
                rightEyeOpen,
                glowDrift
        );
        drawMouth(canvas, faceX, restingEyeTop, eyeHeight, scale);
        drawDecoration(canvas, faceX, restingEyeTop, eyeHeight, scale);

        long delay = decorationProgress > 0.001f
                ? 33L
                : "sleeping".equals(expression)
                ? 180L
                : ("speaking".equals(expression) ? 16L : 33L);
        removeCallbacks(frameRunnable);
        postDelayed(frameRunnable, delay);
    }

    @Override
    protected void onDetachedFromWindow() {
        removeCallbacks(frameRunnable);
        super.onDetachedFromWindow();
    }

    private void drawBackground(Canvas canvas) {
        prepareShaderFill();
        if (backgroundShader == null) {
            paint.setColor(Color.rgb(2, 6, 11));
            canvas.drawRect(0f, 0f, getWidth(), getHeight(), paint);
            return;
        }
        paint.setShader(backgroundShader);
        canvas.drawRect(0f, 0f, getWidth(), getHeight(), paint);
        paint.setShader(null);
    }

    private void drawEye(
            Canvas canvas,
            RectF eye,
            RectF shapedEye,
            Path eyePath,
            float scale,
            float tilt,
            float eyeScale,
            float blinkVisibility,
            float glowDrift
    ) {
        float height = eye.height() * Protocol.clamp(eyeScale, 0.4f, 1.08f);
        shapedEye.set(
                eye.left,
                eye.centerY() - height / 2f,
                eye.right,
                eye.centerY() + height / 2f
        );
        updateSquircle(shapedEye, eyePath);

        canvas.save();
        canvas.rotate(tilt, shapedEye.centerX(), shapedEye.centerY());
        drawEyeGlow(canvas, eyePath, scale, blinkVisibility, glowDrift);

        canvas.save();
        canvas.translate(0f, 1.5f * scale);
        paint.setStyle(Paint.Style.FILL);
        paint.setShader(null);
        paint.setColor(Color.argb(65, 0, 23, 24));
        canvas.drawPath(eyePath, paint);
        canvas.restore();

        drawEyeSurface(canvas, shapedEye, eyePath);
        drawEyelids(canvas, shapedEye, blinkVisibility, scale);
        canvas.restore();
    }

    private void drawEyeGlow(
            Canvas canvas,
            Path eyePath,
            float scale,
            float blinkVisibility,
            float glowDrift
    ) {
        float strength = Math.max(0f, Math.min(1f, glowStrength + glowDrift));
        float visibility = Protocol.clamp(blinkVisibility, 0f, 1f);
        // Retain a trace of light through a blink so the eyes feel softly covered
        // rather than abruptly powered off.
        strength *= 0.08f + 0.92f * (float) Math.pow(visibility, 1.05f);
        int glowColour = blendColour(
                Color.rgb(58, 236, 209),
                Color.rgb(237, 134, 72),
                errorMix
        );

        paint.setShader(null);
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeJoin(Paint.Join.ROUND);
        // Wide, low-alpha layers live behind the filled eye. There is deliberately
        // no narrow high-alpha layer, because that reads as a neon outline while
        // the eyelids are moving.
        drawGlowStroke(canvas, eyePath, scale, glowColour, strength, 48f, 14);
        drawGlowStroke(canvas, eyePath, scale, glowColour, strength, 34f, 20);
        drawGlowStroke(canvas, eyePath, scale, glowColour, strength, 22f, 28);
    }

    private void drawGlowStroke(
            Canvas canvas,
            Path eyePath,
            float scale,
            int colour,
            float strength,
            float width,
            int maximumAlpha
    ) {
        paint.setStrokeWidth(width * scale);
        paint.setColor(withAlpha(colour, Math.round(maximumAlpha * strength)));
        canvas.drawPath(eyePath, paint);
    }

    private void drawEyeSurface(Canvas canvas, RectF eye, Path eyePath) {
        // The middle deliberately matches the proven mouth colour. The top adds
        // a soft mint highlight while the bottom remains bright turquoise rather
        // than dropping into the dark teal that the Nexus panel was crushing.
        // Paint is deliberately reused to avoid frame-by-frame allocations. The
        // preceding glow and shadow use translucent colours, so their alpha must
        // not be allowed to dim the shader that forms the actual eye surface.
        prepareShaderFill();
        ensureEyeBaseShader();
        mapUnitShaderToEye(eyeBaseShader, eye);
        paint.setShader(eyeBaseShader);
        canvas.drawPath(eyePath, paint);

        float highlightX = eye.centerX() - eye.width() * 0.14f;
        float highlightY = eye.centerY() - eye.height() * 0.22f;
        float highlightRadius = eye.height() * 0.86f;
        radialDestinationRect.set(
                highlightX - highlightRadius,
                highlightY - highlightRadius,
                highlightX + highlightRadius,
                highlightY + highlightRadius
        );
        shaderMatrix.setRectToRect(
                UNIT_RADIAL_RECT,
                radialDestinationRect,
                Matrix.ScaleToFit.FILL
        );
        eyeHighlightShader.setLocalMatrix(shaderMatrix);
        paint.setShader(eyeHighlightShader);
        canvas.drawPath(eyePath, paint);

        mapUnitShaderToEye(eyeSheenShader, eye);
        paint.setShader(eyeSheenShader);
        canvas.drawPath(eyePath, paint);
    }

    private void drawEyelids(
            Canvas canvas,
            RectF eye,
            float blinkVisibility,
            float scale
    ) {
        float closure = 1f - Protocol.clamp(blinkVisibility, 0f, 1f);
        if (closure <= 0.001f) {
            return;
        }

        // The lid deliberately overlaps the glowing eye by one logical pixel.
        // Matching both shapes exactly can leave a cyan anti-aliased fringe at
        // the top and sides when the eye is closed on the Nexus display.
        float lidOverlap = 1f * scale;
        lidEyeBounds.set(
                eye.left - lidOverlap,
                eye.top - lidOverlap,
                eye.right + lidOverlap,
                eye.bottom + lidOverlap
        );
        updateSquircle(lidEyeBounds, lidClipPath);

        float meetingY = eye.top + eye.height() * 0.74f;
        float topEdgeY = lidEyeBounds.top + (meetingY - lidEyeBounds.top) * closure;
        float bottomEdgeY = lidEyeBounds.bottom
                - (lidEyeBounds.bottom - meetingY) * closure;
        float topBow = 2.2f * scale * closure;
        float bottomBow = 1.1f * scale * closure;

        topLidPath.reset();
        topLidPath.moveTo(lidEyeBounds.left, lidEyeBounds.top - scale);
        topLidPath.lineTo(lidEyeBounds.right, lidEyeBounds.top - scale);
        topLidPath.lineTo(lidEyeBounds.right, topEdgeY);
        topLidPath.cubicTo(
                lidEyeBounds.right - lidEyeBounds.width() * 0.28f,
                topEdgeY + topBow,
                lidEyeBounds.left + lidEyeBounds.width() * 0.28f,
                topEdgeY + topBow,
                lidEyeBounds.left,
                topEdgeY
        );
        topLidPath.close();

        bottomLidPath.reset();
        bottomLidPath.moveTo(lidEyeBounds.left, lidEyeBounds.bottom + scale);
        bottomLidPath.lineTo(lidEyeBounds.right, lidEyeBounds.bottom + scale);
        bottomLidPath.lineTo(lidEyeBounds.right, bottomEdgeY);
        bottomLidPath.cubicTo(
                lidEyeBounds.right - lidEyeBounds.width() * 0.28f,
                bottomEdgeY - bottomBow,
                lidEyeBounds.left + lidEyeBounds.width() * 0.28f,
                bottomEdgeY - bottomBow,
                lidEyeBounds.left,
                bottomEdgeY
        );
        bottomLidPath.close();

        canvas.save();
        canvas.clipPath(lidClipPath);
        prepareShaderFill();
        mapUnitShaderToEye(eyelidShader, eye);
        paint.setShader(eyelidShader);
        canvas.drawPath(topLidPath, paint);
        canvas.drawPath(bottomLidPath, paint);

        paint.setShader(null);
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeWidth(0.55f * scale);
        paint.setStrokeCap(Paint.Cap.ROUND);
        float seamVisibility = closure * closure;
        int seamAlpha = Math.round((12f + glowStrength * 36f) * seamVisibility);
        paint.setColor(Color.argb(seamAlpha, 73, 226, 202));
        float seamInset = 7f * scale;
        float seamBow = topBow * (1f - closure * 0.65f);
        lidSeamPath.reset();
        lidSeamPath.moveTo(eye.left + seamInset, topEdgeY);
        lidSeamPath.cubicTo(
                eye.left + eye.width() * 0.30f,
                topEdgeY + seamBow,
                eye.right - eye.width() * 0.30f,
                topEdgeY + seamBow,
                eye.right - seamInset,
                topEdgeY
        );
        canvas.drawPath(lidSeamPath, paint);
        canvas.restore();
    }

    private void ensureEyeBaseShader() {
        if (eyeBaseShader != null && Math.abs(eyeShaderErrorMix - errorMix) < 0.001f) {
            return;
        }
        eyeShaderErrorMix = errorMix;
        int top = blendColour(Color.rgb(138, 255, 235), Color.rgb(249, 205, 137), errorMix);
        int middle = blendColour(Color.rgb(73, 226, 202), Color.rgb(238, 161, 91), errorMix);
        int bottom = blendColour(Color.rgb(43, 205, 190), Color.rgb(210, 108, 67), errorMix);
        eyeBaseShader = new LinearGradient(
                0f,
                0f,
                0f,
                1f,
                new int[]{top, middle, bottom},
                new float[]{0f, 0.52f, 1f},
                Shader.TileMode.CLAMP
        );
    }

    private void mapUnitShaderToEye(Shader shader, RectF eye) {
        shaderMatrix.setRectToRect(UNIT_SHADER_RECT, eye, Matrix.ScaleToFit.FILL);
        shader.setLocalMatrix(shaderMatrix);
    }

    private void prepareShaderFill() {
        paint.setShader(null);
        paint.setStyle(Paint.Style.FILL);
        paint.setColor(Color.WHITE);
        paint.setAlpha(255);
    }

    private void drawMouth(
            Canvas canvas,
            float centreX,
            float eyeTop,
            float eyeHeight,
            float scale
    ) {
        float mouthY = eyeTop + eyeHeight + (53f + mouthOffsetY) * scale;
        float mouthCentreX = centreX + mouthOffsetX * scale;
        float halfWidth = 28f * mouthWidthScale * scale;
        float openAmount = Protocol.clamp(mouthOpen, 0f, 1f);
        float openBlend = smoothStep(0.025f, 0.17f, openAmount);

        canvas.save();
        canvas.rotate(mouthTilt, mouthCentreX, mouthY);
        if (openBlend < 0.995f) {
            if (tongueAmount > 0.001f) {
                drawClosedTongue(
                        canvas,
                        mouthCentreX,
                        mouthY,
                        scale,
                        tongueAmount,
                        1f - openBlend
                );
            }
            drawClosedMouth(
                    canvas,
                    mouthCentreX,
                    mouthY,
                    halfWidth,
                    scale,
                    1f - openBlend
            );
        }
        if (openBlend > 0.005f) {
            drawOpenMouth(
                    canvas,
                    mouthCentreX,
                    mouthY,
                    halfWidth,
                    scale,
                    openAmount,
                    openBlend
            );
        }
        canvas.restore();
    }

    private void drawClosedMouth(
            Canvas canvas,
            float centreX,
            float mouthY,
            float halfWidth,
            float scale,
            float opacity
    ) {
        float curve = closedMouthCurve() * microSmile;
        mouthPath.reset();
        if ("confused".equals(expression)) {
            mouthPath.moveTo(centreX - halfWidth, mouthY);
            mouthPath.cubicTo(
                    centreX - halfWidth * 0.62f,
                    mouthY + 4.8f * scale,
                    centreX - halfWidth * 0.24f,
                    mouthY + 4.4f * scale,
                    centreX,
                    mouthY + 0.5f * scale
            );
            mouthPath.cubicTo(
                    centreX + halfWidth * 0.28f,
                    mouthY - 3.9f * scale,
                    centreX + halfWidth * 0.63f,
                    mouthY - 3.3f * scale,
                    centreX + halfWidth,
                    mouthY + 0.7f * scale
            );
        } else {
            mouthPath.moveTo(centreX - halfWidth, mouthY - 0.4f * scale);
            mouthPath.cubicTo(
                    centreX - halfWidth * 0.46f,
                    mouthY + 7.6f * curve * scale,
                    centreX + halfWidth * 0.42f,
                    mouthY + 8.2f * curve * scale,
                    centreX + halfWidth,
                    mouthY - 1.1f * scale
            );
        }

        int mouthColour = blendColour(
                Color.rgb(73, 226, 202),
                Color.rgb(238, 161, 91),
                errorMix
        );

        paint.setShader(null);
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeCap(Paint.Cap.ROUND);

        // Broad, faint passes create depth without turning the mouth into a
        // sharp neon outline.
        paint.setStrokeWidth(11f * scale);
        paint.setColor(withAlpha(mouthColour, Math.round(8f * opacity)));
        canvas.drawPath(mouthPath, paint);
        paint.setStrokeWidth(7f * scale);
        paint.setColor(withAlpha(mouthColour, Math.round(13f * opacity)));
        canvas.drawPath(mouthPath, paint);

        paint.setStrokeWidth(3.8f * scale);
        paint.setColor(withAlpha(mouthColour, Math.round(245f * opacity)));
        canvas.drawPath(mouthPath, paint);
    }

    private void drawOpenMouth(
            Canvas canvas,
            float centreX,
            float mouthY,
            float halfWidth,
            float scale,
            float openAmount,
            float opacity
    ) {
        float middleWidth = 1f - Math.min(1f, Math.abs(openAmount - 0.43f) / 0.43f);
        float phonemeWidth = 0.72f + middleWidth * 0.18f + openAmount * 0.05f;
        float shapedHalfWidth = halfWidth * phonemeWidth;
        float openHeight = (5.5f + openAmount * 23f) * scale;
        float mouthCentreY = mouthY + openHeight * 0.30f;
        mouthBounds.set(
                centreX - shapedHalfWidth,
                mouthCentreY - openHeight / 2f,
                centreX + shapedHalfWidth,
                mouthCentreY + openHeight / 2f
        );
        // Echo the eyes' soft cubed geometry instead of turning the speaking
        // mouth into an organic blob. Medium syllables widen like an "ee";
        // louder syllables become a taller, slightly narrower rounded box.
        updateSquircle(mouthBounds, mouthPath);

        paint.setShader(null);
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeJoin(Paint.Join.ROUND);
        paint.setStrokeWidth(10f * scale);
        int mouthColour = blendColour(
                Color.rgb(73, 226, 202),
                Color.rgb(238, 161, 91),
                errorMix
        );
        paint.setColor(withAlpha(mouthColour, Math.round(8f * opacity)));
        canvas.drawPath(mouthPath, paint);

        paint.setStyle(Paint.Style.FILL);
        int innerColour = blendColour(
                Color.rgb(4, 29, 27),
                Color.rgb(42, 22, 18),
                errorMix
        );
        paint.setColor(withAlpha(innerColour, Math.round(248f * opacity)));
        canvas.drawPath(mouthPath, paint);

        if (tongueAmount > 0.001f) {
            canvas.save();
            canvas.clipPath(mouthPath);
            drawTongueShape(
                    canvas,
                    centreX,
                    mouthBounds.top + openHeight * 0.48f,
                    8.5f * scale,
                    10f * scale,
                    tongueAmount,
                    opacity
            );
            canvas.restore();
        }

        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeJoin(Paint.Join.ROUND);
        paint.setStrokeCap(Paint.Cap.ROUND);
        paint.setStrokeWidth(2.45f * scale);
        paint.setColor(withAlpha(mouthColour, Math.round(238f * opacity)));
        canvas.drawPath(mouthPath, paint);

        if (openAmount > 0.27f) {
            float detailOpacity = smoothStep(0.27f, 0.62f, openAmount) * opacity;
            float detailY = mouthBounds.bottom - openHeight * 0.23f;
            mouthDetailPath.reset();
            mouthDetailPath.moveTo(
                    centreX - shapedHalfWidth * 0.32f,
                    detailY
            );
            mouthDetailPath.cubicTo(
                    centreX - shapedHalfWidth * 0.11f,
                    detailY + openHeight * 0.08f,
                    centreX + shapedHalfWidth * 0.11f,
                    detailY + openHeight * 0.08f,
                    centreX + shapedHalfWidth * 0.32f,
                    detailY
            );
            paint.setStrokeWidth(1.35f * scale);
            paint.setColor(withAlpha(mouthColour, Math.round(82f * detailOpacity)));
            canvas.drawPath(mouthDetailPath, paint);
        }
    }

    private void drawClosedTongue(
            Canvas canvas,
            float centreX,
            float mouthY,
            float scale,
            float amount,
            float opacity
    ) {
        float curve = Math.max(0.35f, closedMouthCurve() * microSmile);
        float tongueTop = mouthY + 7.3f * curve * scale;
        drawTongueShape(
                canvas,
                centreX + 1.5f * scale,
                tongueTop,
                6.5f * scale,
                10.5f * scale,
                amount,
                opacity
        );
    }

    private void drawTongueShape(
            Canvas canvas,
            float centreX,
            float top,
            float maximumHalfWidth,
            float maximumHeight,
            float amount,
            float opacity
    ) {
        float visible = Protocol.clamp(amount, 0f, 1f);
        float halfWidth = maximumHalfWidth * (0.58f + visible * 0.42f);
        float height = maximumHeight * visible;
        if (height < 0.2f) {
            return;
        }

        mouthDetailPath.reset();
        mouthDetailPath.moveTo(centreX - halfWidth, top);
        mouthDetailPath.lineTo(centreX + halfWidth, top);
        mouthDetailPath.cubicTo(
                centreX + halfWidth,
                top + height * 0.62f,
                centreX + halfWidth * 0.58f,
                top + height,
                centreX,
                top + height
        );
        mouthDetailPath.cubicTo(
                centreX - halfWidth * 0.58f,
                top + height,
                centreX - halfWidth,
                top + height * 0.62f,
                centreX - halfWidth,
                top
        );
        mouthDetailPath.close();

        paint.setShader(null);
        paint.setStyle(Paint.Style.FILL);
        paint.setColor(Color.argb(
                Math.round(245f * opacity),
                255,
                132,
                153
        ));
        canvas.drawPath(mouthDetailPath, paint);

        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeJoin(Paint.Join.ROUND);
        paint.setStrokeWidth(Math.max(1f, maximumHeight * 0.11f));
        paint.setColor(Color.argb(
                Math.round(145f * opacity),
                255,
                190,
                199
        ));
        canvas.drawPath(mouthDetailPath, paint);

        mouthDetailPath.reset();
        mouthDetailPath.moveTo(centreX, top + height * 0.44f);
        mouthDetailPath.lineTo(centreX, top + height * 0.83f);
        paint.setStrokeCap(Paint.Cap.ROUND);
        paint.setStrokeWidth(Math.max(0.8f, maximumHeight * 0.075f));
        paint.setColor(Color.argb(
                Math.round(72f * opacity * visible),
                145,
                56,
                81
        ));
        canvas.drawPath(mouthDetailPath, paint);
    }

    private float closedMouthCurve() {
        return smileCurve;
    }

    private void drawDecoration(
            Canvas canvas,
            float centreX,
            float eyeTop,
            float eyeHeight,
            float scale
    ) {
        float progress = Protocol.clamp(decorationProgress, 0f, 1f);
        if (progress <= 0.001f || "none".equals(decoration)) {
            return;
        }

        float envelope = (float) Math.sin(Math.PI * progress);
        int colour = blendColour(
                Color.rgb(91, 244, 220),
                Color.rgb(246, 169, 99),
                errorMix
        );
        float mouthY = eyeTop + eyeHeight + 53f * scale;

        paint.setShader(null);
        paint.setStyle(Paint.Style.FILL);
        paint.setStrokeCap(Paint.Cap.ROUND);

        switch (decoration) {
            case "sleep_z":
                drawSleepZs(canvas, centreX, mouthY, scale, progress, envelope, colour);
                break;
            case "sparkles":
                drawSparkles(canvas, centreX, eyeTop, eyeHeight, scale, progress, envelope, colour);
                break;
            case "thinking_dots":
                drawThinkingDots(canvas, centreX, mouthY, scale, progress, envelope, colour);
                break;
            case "question":
                drawQuestion(canvas, centreX, eyeTop, eyeHeight, scale, progress, envelope, colour);
                break;
            case "attention":
                drawAttentionRays(canvas, centreX, eyeTop, scale, envelope, colour);
                break;
            default:
                break;
        }
    }

    private void drawSleepZs(
            Canvas canvas,
            float centreX,
            float mouthY,
            float scale,
            float progress,
            float envelope,
            int colour
    ) {
        paint.setTextAlign(Paint.Align.CENTER);
        paint.setTypeface(Typeface.DEFAULT_BOLD);

        float rise = progress * 45f * scale;
        float sway = (float) Math.sin(progress * Math.PI * 1.6f) * 5f * scale;
        paint.setTextSize(17f * scale);
        paint.setColor(withAlpha(colour, Math.round(155f * envelope)));
        canvas.drawText("z", centreX + 36f * scale + sway, mouthY - 12f * scale - rise, paint);

        float secondProgress = Protocol.clamp((progress - 0.22f) / 0.78f, 0f, 1f);
        float secondEnvelope = (float) Math.sin(Math.PI * secondProgress);
        if (secondProgress > 0f) {
            paint.setTextSize(12f * scale);
            paint.setColor(withAlpha(colour, Math.round(118f * secondEnvelope)));
            canvas.drawText(
                    "z",
                    centreX + 24f * scale - sway * 0.35f,
                    mouthY - 8f * scale - secondProgress * 30f * scale,
                    paint
            );
        }
    }

    private void drawSparkles(
            Canvas canvas,
            float centreX,
            float eyeTop,
            float eyeHeight,
            float scale,
            float progress,
            float envelope,
            int colour
    ) {
        float pulse = 0.82f + 0.18f * (float) Math.sin(progress * Math.PI * 3f);
        paint.setColor(withAlpha(colour, Math.round(180f * envelope)));
        drawSparkle(canvas, centreX - 103f * scale, eyeTop + 22f * scale, 9f * scale * pulse);
        drawSparkle(canvas, centreX + 109f * scale, eyeTop + eyeHeight * 0.48f, 7f * scale * pulse);
        paint.setColor(withAlpha(colour, Math.round(110f * envelope)));
        drawSparkle(canvas, centreX + 91f * scale, eyeTop - 3f * scale, 4.5f * scale * pulse);
    }

    private void drawSparkle(Canvas canvas, float x, float y, float radius) {
        decorationPath.reset();
        decorationPath.moveTo(x, y - radius);
        decorationPath.lineTo(x + radius * 0.27f, y - radius * 0.27f);
        decorationPath.lineTo(x + radius, y);
        decorationPath.lineTo(x + radius * 0.27f, y + radius * 0.27f);
        decorationPath.lineTo(x, y + radius);
        decorationPath.lineTo(x - radius * 0.27f, y + radius * 0.27f);
        decorationPath.lineTo(x - radius, y);
        decorationPath.lineTo(x - radius * 0.27f, y - radius * 0.27f);
        decorationPath.close();
        canvas.drawPath(decorationPath, paint);
    }

    private void drawThinkingDots(
            Canvas canvas,
            float centreX,
            float mouthY,
            float scale,
            float progress,
            float envelope,
            int colour
    ) {
        paint.setColor(withAlpha(colour, Math.round(145f * envelope)));
        float phase = progress * (float) Math.PI * 4f;
        for (int index = 0; index < 3; index++) {
            float bob = (float) Math.sin(phase - index * 0.75f) * 2.4f * scale;
            canvas.drawCircle(
                    centreX + (index - 1) * 13f * scale,
                    mouthY + 14f * scale + bob,
                    (2.3f + index * 0.35f) * scale,
                    paint
            );
        }
    }

    private void drawQuestion(
            Canvas canvas,
            float centreX,
            float eyeTop,
            float eyeHeight,
            float scale,
            float progress,
            float envelope,
            int colour
    ) {
        paint.setTextAlign(Paint.Align.CENTER);
        paint.setTypeface(Typeface.DEFAULT_BOLD);
        paint.setTextSize(24f * scale);
        paint.setColor(withAlpha(colour, Math.round(135f * envelope)));
        float bob = (float) Math.sin(progress * Math.PI) * 5f * scale;
        canvas.drawText(
                "?",
                centreX + 116f * scale,
                eyeTop + eyeHeight * 0.42f - bob,
                paint
        );
    }

    private void drawAttentionRays(
            Canvas canvas,
            float centreX,
            float eyeTop,
            float scale,
            float envelope,
            int colour
    ) {
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeWidth(2.2f * scale);
        paint.setColor(withAlpha(colour, Math.round(135f * envelope)));
        decorationPath.reset();
        decorationPath.moveTo(centreX - 13f * scale, eyeTop - 18f * scale);
        decorationPath.lineTo(centreX - 17f * scale, eyeTop - 31f * scale);
        decorationPath.moveTo(centreX, eyeTop - 20f * scale);
        decorationPath.lineTo(centreX, eyeTop - 35f * scale);
        decorationPath.moveTo(centreX + 13f * scale, eyeTop - 18f * scale);
        decorationPath.lineTo(centreX + 17f * scale, eyeTop - 31f * scale);
        canvas.drawPath(decorationPath, paint);
    }

    private void buildUnitSquircle() {
        unitSquirclePath.reset();
        float exponent = 4.4f;

        for (int index = 0; index <= SQUIRCLE_POINTS; index++) {
            double angle = Math.PI * 2.0 * index / SQUIRCLE_POINTS;
            double cosine = Math.cos(angle);
            double sine = Math.sin(angle);
            float x = 0.5f * signedPower(cosine, 2.0 / exponent);
            float y = 0.5f * signedPower(sine, 2.0 / exponent);
            if (index == 0) {
                unitSquirclePath.moveTo(x, y);
            } else {
                unitSquirclePath.lineTo(x, y);
            }
        }
        unitSquirclePath.close();
    }

    private void updateSquircle(RectF rectangle, Path destination) {
        pathMatrix.setRectToRect(UNIT_EYE_RECT, rectangle, Matrix.ScaleToFit.FILL);
        destination.reset();
        unitSquirclePath.transform(pathMatrix, destination);
    }

    private static float signedPower(double value, double power) {
        return (float) Math.copySign(Math.pow(Math.abs(value), power), value);
    }

    private static float smoothStep(float start, float end, float value) {
        float progress = Protocol.clamp((value - start) / (end - start), 0f, 1f);
        return progress * progress * (3f - 2f * progress);
    }

    private static int blendColour(int start, int end, float amount) {
        amount = Protocol.clamp(amount, 0f, 1f);
        return Color.argb(
                Math.round(Color.alpha(start) + (Color.alpha(end) - Color.alpha(start)) * amount),
                Math.round(Color.red(start) + (Color.red(end) - Color.red(start)) * amount),
                Math.round(Color.green(start) + (Color.green(end) - Color.green(start)) * amount),
                Math.round(Color.blue(start) + (Color.blue(end) - Color.blue(start)) * amount)
        );
    }

    private static int withAlpha(int colour, int alpha) {
        return Color.argb(
                Math.max(0, Math.min(255, alpha)),
                Color.red(colour),
                Color.green(colour),
                Color.blue(colour)
        );
    }

    void setExpression(String value) {
        expression = value;
        invalidate();
    }

    String getExpression() {
        return expression;
    }

    float getLeftEyeOpen() { return leftEyeOpen; }
    void setLeftEyeOpen(float value) { leftEyeOpen = value; invalidate(); }
    float getRightEyeOpen() { return rightEyeOpen; }
    void setRightEyeOpen(float value) { rightEyeOpen = value; invalidate(); }
    float getLeftEyeScale() { return leftEyeScale; }
    void setLeftEyeScale(float value) { leftEyeScale = value; invalidate(); }
    float getRightEyeScale() { return rightEyeScale; }
    void setRightEyeScale(float value) { rightEyeScale = value; invalidate(); }
    float getLeftEyeTilt() { return leftEyeTilt; }
    void setLeftEyeTilt(float value) { leftEyeTilt = value; invalidate(); }
    float getRightEyeTilt() { return rightEyeTilt; }
    void setRightEyeTilt(float value) { rightEyeTilt = value; invalidate(); }
    float getGlowStrength() { return glowStrength; }
    void setGlowStrength(float value) { glowStrength = value; invalidate(); }
    float getMouthOpen() { return mouthOpen; }
    void setMouthOpen(float value) { mouthOpen = value; invalidate(); }
    float getSmileCurve() { return smileCurve; }
    void setSmileCurve(float value) { smileCurve = value; invalidate(); }
    float getMouthWidthScale() { return mouthWidthScale; }
    void setMouthWidthScale(float value) { mouthWidthScale = value; invalidate(); }
    float getMouthTilt() { return mouthTilt; }
    void setMouthTilt(float value) { mouthTilt = value; invalidate(); }
    float getMouthOffsetX() { return mouthOffsetX; }
    void setMouthOffsetX(float value) { mouthOffsetX = value; invalidate(); }
    float getMouthOffsetY() { return mouthOffsetY; }
    void setMouthOffsetY(float value) { mouthOffsetY = value; invalidate(); }
    float getTongueAmount() { return tongueAmount; }
    void setTongueAmount(float value) { tongueAmount = value; invalidate(); }
    float getFaceLift() { return faceLift; }
    void setFaceLift(float value) { faceLift = value; invalidate(); }
    float getEyeOffsetX() { return eyeOffsetX; }
    void setEyeOffsetX(float value) { eyeOffsetX = value; invalidate(); }
    float getEyeOffsetY() { return eyeOffsetY; }
    void setEyeOffsetY(float value) { eyeOffsetY = value; invalidate(); }
    float getIdleOffsetX() { return idleOffsetX; }
    void setIdleOffsetX(float value) { idleOffsetX = value; invalidate(); }
    float getIdleOffsetY() { return idleOffsetY; }
    void setIdleOffsetY(float value) { idleOffsetY = value; invalidate(); }
    float getMicroLeftScale() { return microLeftScale; }
    void setMicroLeftScale(float value) { microLeftScale = value; invalidate(); }
    float getMicroRightScale() { return microRightScale; }
    void setMicroRightScale(float value) { microRightScale = value; invalidate(); }
    float getMicroLeftTilt() { return microLeftTilt; }
    void setMicroLeftTilt(float value) { microLeftTilt = value; invalidate(); }
    float getMicroRightTilt() { return microRightTilt; }
    void setMicroRightTilt(float value) { microRightTilt = value; invalidate(); }
    float getMicroSmile() { return microSmile; }
    void setMicroSmile(float value) { microSmile = value; invalidate(); }
    float getErrorMix() { return errorMix; }
    void setErrorMix(float value) { errorMix = value; invalidate(); }
    float getBlinkDip() { return blinkDip; }
    void setBlinkDip(float value) { blinkDip = value; invalidate(); }
    String getDecoration() { return decoration; }
    void setDecoration(String value) { decoration = value; invalidate(); }
    float getDecorationProgress() { return decorationProgress; }
    void setDecorationProgress(float value) { decorationProgress = value; invalidate(); }
}
