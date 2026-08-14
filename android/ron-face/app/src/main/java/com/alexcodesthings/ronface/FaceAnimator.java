package com.alexcodesthings.ronface;

import android.animation.Animator;
import android.animation.AnimatorListenerAdapter;
import android.animation.AnimatorSet;
import android.animation.ValueAnimator;
import android.os.Handler;
import android.os.Looper;
import android.view.animation.DecelerateInterpolator;
import android.view.animation.Interpolator;
import android.view.animation.OvershootInterpolator;
import android.view.animation.PathInterpolator;

import java.util.ArrayList;
import java.util.List;
import java.util.Random;

final class FaceAnimator {
    private interface FloatGetter {
        float get();
    }

    private interface FloatSetter {
        void set(float value);
    }

    private enum BlinkKind {
        NORMAL,
        PARTIAL,
        DOUBLE,
        EXPRESSIVE,
        UNEVEN,
        ACKNOWLEDGEMENT
    }

    private static final class BlinkProfile {
        final float leftTarget;
        final float rightTarget;
        final long closeDuration;
        final long holdDuration;
        final long openDuration;
        final float dip;

        BlinkProfile(
                float leftTarget,
                float rightTarget,
                long closeDuration,
                long holdDuration,
                long openDuration,
                float dip
        ) {
            this.leftTarget = leftTarget;
            this.rightTarget = rightTarget;
            this.closeDuration = closeDuration;
            this.holdDuration = holdDuration;
            this.openDuration = openDuration;
            this.dip = dip;
        }
    }

    private static final class Pose {
        final float leftScale;
        final float rightScale;
        final float leftTilt;
        final float rightTilt;
        final float glow;
        final float smile;
        final float lift;
        final float eyeX;
        final float eyeY;
        final float error;

        Pose(
                float leftScale,
                float rightScale,
                float leftTilt,
                float rightTilt,
                float glow,
                float smile,
                float lift,
                float eyeX,
                float eyeY,
                float error
        ) {
            this.leftScale = leftScale;
            this.rightScale = rightScale;
            this.leftTilt = leftTilt;
            this.rightTilt = rightTilt;
            this.glow = glow;
            this.smile = smile;
            this.lift = lift;
            this.eyeX = eyeX;
            this.eyeY = eyeY;
            this.error = error;
        }
    }

    private static final Interpolator BLINK_CLOSE =
            new PathInterpolator(0.28f, 0f, 0.72f, 1f);
    private static final Interpolator BLINK_OPEN =
            new PathInterpolator(0.16f, 0.72f, 0.24f, 1f);
    private static final Interpolator SOFT = new DecelerateInterpolator();
    private static final Interpolator SMOOTH = new DecelerateInterpolator(1.4f);
    private static final Interpolator DRIFT_SMOOTH = new DecelerateInterpolator(1.2f);
    private static final Interpolator SPRING = new OvershootInterpolator(0.35f);

    // Each expression has several hand-tuned poses. Picking without immediate
    // repetition keeps Ron lively without making him restless or unpredictable.
    private static final Pose[] LISTENING_POSES = {
            new Pose(1.055f, 1.055f, 0f, 0f, 0.24f, 0.92f, -2f, 0f, 0f, 0f),
            new Pose(1.07f, 1.02f, -1.1f, -0.25f, 0.25f, 0.98f, -2.5f, -1f, -0.5f, 0f),
            new Pose(1.02f, 1.07f, 0.25f, 1.1f, 0.25f, 0.98f, -2.5f, 1f, -0.5f, 0f),
            new Pose(1.075f, 1.075f, -0.6f, 0.6f, 0.27f, 1.06f, -3f, 0f, -1f, 0f)
    };
    private static final Pose[] THINKING_POSES = {
            new Pose(0.82f, 0.98f, -1.4f, 1.4f, 0.17f, 0.38f, -0.5f, 0f, -4f, 0f),
            new Pose(0.98f, 0.82f, -1.4f, 1.4f, 0.17f, 0.38f, -0.5f, 0f, -4f, 0f),
            new Pose(0.90f, 0.90f, 1.1f, -1.1f, 0.16f, 0.24f, 0f, 0f, -3f, 0f),
            new Pose(0.78f, 1.03f, -2.3f, 0.5f, 0.18f, 0.46f, -1f, -2f, -2f, 0f)
    };
    private static final Pose[] SPEAKING_POSES = {
            new Pose(0.97f, 0.97f, 0f, 0f, 0.21f, 1f, -0.5f, 0f, 0f, 0f),
            new Pose(1.02f, 1.02f, -0.35f, 0.35f, 0.23f, 1.08f, -1.5f, 0f, 0f, 0f),
            new Pose(0.95f, 1f, -0.9f, 0.2f, 0.20f, 0.96f, 0f, -1f, 0f, 0f),
            new Pose(1.045f, 1.045f, 0f, 0f, 0.25f, 1.14f, -2f, 0f, -0.5f, 0f)
    };
    private static final Pose[] HAPPY_POSES = {
            new Pose(0.86f, 0.86f, -1.2f, 1.2f, 0.18f, 1.3f, -1f, 0f, 1f, 0f),
            new Pose(1.025f, 1.025f, -0.5f, 0.5f, 0.25f, 1.24f, -3f, 0f, -0.5f, 0f),
            new Pose(0.82f, 0.82f, 1.5f, -1.5f, 0.20f, 1.38f, 0f, 0f, 1.5f, 0f),
            new Pose(0.79f, 0.93f, -2f, 0.7f, 0.22f, 1.32f, -1.5f, -1f, 0.5f, 0f)
    };
    private static final Pose[] CONFUSED_POSES = {
            new Pose(0.76f, 1f, -4f, 2f, 0.15f, 0.72f, 0f, 1.5f, 0f, 0f),
            new Pose(1f, 0.76f, -2f, 4f, 0.15f, 0.72f, 0f, -1.5f, 0f, 0f),
            new Pose(0.88f, 0.88f, -3f, -3f, 0.16f, 0.66f, 1f, 0f, 1f, 0f),
            new Pose(0.72f, 1.035f, 2.2f, -0.7f, 0.17f, 0.78f, 0f, 2f, -1f, 0f)
    };
    private static final Pose[] ERROR_POSES = {
            new Pose(0.62f, 0.62f, 1f, -1f, 0.32f, -0.55f, 3.5f, 0f, 0f, 1f),
            new Pose(0.76f, 0.76f, 2f, -2f, 0.29f, -0.68f, 2.5f, 0f, 1f, 1f),
            new Pose(0.57f, 0.72f, -0.5f, 1.6f, 0.31f, -0.48f, 3f, -1f, 0f, 1f),
            new Pose(0.70f, 0.58f, -1.6f, 0.5f, 0.30f, -0.60f, 3f, 1f, 0f, 1f)
    };
    private static final Pose[] SLEEPING_POSES = {
            new Pose(1f, 1f, 0f, 0f, 0.035f, 0.72f, 2f, 0f, 0f, 0f),
            new Pose(0.96f, 0.96f, 0.6f, -0.6f, 0.04f, 0.86f, 2.5f, 0f, 1f, 0f),
            new Pose(1.02f, 0.98f, -0.5f, 0.5f, 0.035f, 0.78f, 2f, -1f, 0f, 0f),
            new Pose(0.98f, 1.02f, 0.5f, -0.5f, 0.04f, 0.82f, 2f, 1f, 0f, 0f)
    };
    private static final Pose[] IDLE_POSES = {
            new Pose(1f, 1f, 0f, 0f, 0.14f, 1f, 0f, 0f, 0f, 0f),
            new Pose(1.02f, 0.98f, -0.7f, -0.15f, 0.15f, 1.02f, -0.5f, -1f, 0f, 0f),
            new Pose(1.04f, 1.04f, -0.3f, 0.3f, 0.16f, 1.07f, -1f, 0f, -0.5f, 0f),
            new Pose(0.96f, 0.96f, 0.6f, -0.6f, 0.14f, 1.10f, 0f, 0f, 1f, 0f)
    };

    private static final float[] SPEECH_SHAPES = {0.04f, 0.12f, 0.22f, 0.38f, 0.56f, 0.72f};
    private static final int[] SPEECH_WEIGHTS = {12, 14, 20, 24, 19, 11};

    private final RonFaceView face;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Random random = new Random();

    private AnimatorSet poseAnimator;
    private AnimatorSet blinkAnimator;
    private AnimatorSet driftAnimator;
    private AnimatorSet microAnimator;
    private AnimatorSet speechAnimator;
    private AnimatorSet accentAnimator;
    private AnimatorSet entryAnimator;
    private AnimatorSet decorationAnimator;
    private AnimatorSet tongueAnimator;
    private final int[] lastVariants = {-1, -1, -1, -1, -1, -1, -1, -1};
    private int activeVariant;
    private boolean speaking;
    private boolean receivingSpeechLevels;
    private boolean pendingAcknowledgement;
    private long lastSpeechLevelAt;

    private final Runnable blinkRunnable = this::blinkNaturally;
    private final Runnable driftRunnable = this::animateDrift;
    private final Runnable microRunnable = this::animateMicroExpression;
    private final Runnable speechRunnable = this::animateSimulatedSyllable;
    private final Runnable speechWatchdog = this::checkSpeechSamples;
    private final Runnable safeIdleRunnable = () -> setExpression("idle");
    private final Runnable safeSleepRunnable = () -> setExpression("sleeping");
    private final Runnable acknowledgementBlinkRunnable = this::acknowledgementBlink;
    private final Runnable sleepDecorationRunnable = this::showSleepDecoration;

    private void acknowledgementBlink() {
        if ("listening".equals(face.getExpression())) {
            blink(BlinkKind.ACKNOWLEDGEMENT, true);
        }
    }

    FaceAnimator(RonFaceView face) {
        this.face = face;
        scheduleBlink(true);
        scheduleDrift(true);
        scheduleMicroExpression();
    }

    void setExpression(String expression) {
        if (!Protocol.isExpression(expression)) {
            return;
        }

        String previous = face.getExpression();
        boolean repeated = expression.equals(previous);
        face.setExpression(expression);
        cancel(poseAnimator);
        cancel(driftAnimator);
        cancel(accentAnimator);
        accentAnimator = null;
        resetMicroChannels();
        handler.removeCallbacks(acknowledgementBlinkRunnable);
        handler.removeCallbacks(sleepDecorationRunnable);
        clearDecoration();

        if ("sleeping".equals(expression)) {
            handler.removeCallbacks(blinkRunnable);
            cancel(blinkAnimator);
            blinkAnimator = null;
            pendingAcknowledgement = false;
            face.setBlinkDip(0f);
        } else {
            scheduleBlink(false);
        }

        if (!"speaking".equals(expression)) {
            stopSpeechMotion();
        }

        long duration = "sleeping".equals(previous) && !repeated ? 430L : 330L;
        activeVariant = selectVariant(expression);
        final int chosenVariant = activeVariant;
        Pose pose = poseFor(expression, chosenVariant);
        List<Animator> animations = new ArrayList<>();

        animations.add(animate(
                face::getLeftEyeScale,
                face::setLeftEyeScale,
                pose.leftScale,
                duration,
                SPRING
        ));
        animations.add(animate(
                face::getRightEyeScale,
                face::setRightEyeScale,
                pose.rightScale,
                duration,
                SPRING
        ));
        animations.add(animate(
                face::getLeftEyeTilt,
                face::setLeftEyeTilt,
                pose.leftTilt,
                duration,
                SPRING
        ));
        animations.add(animate(
                face::getRightEyeTilt,
                face::setRightEyeTilt,
                pose.rightTilt,
                duration,
                SPRING
        ));
        animations.add(animate(
                face::getGlowStrength,
                face::setGlowStrength,
                pose.glow,
                duration,
                SMOOTH
        ));
        animations.add(animate(
                face::getSmileCurve,
                face::setSmileCurve,
                pose.smile,
                duration,
                SMOOTH
        ));
        animations.add(animate(
                face::getMouthWidthScale,
                face::setMouthWidthScale,
                mouthWidthFor(expression, chosenVariant),
                duration,
                SPRING
        ));
        animations.add(animate(
                face::getMouthTilt,
                face::setMouthTilt,
                mouthTiltFor(expression, chosenVariant),
                duration,
                SPRING
        ));
        animations.add(animate(
                face::getMouthOffsetX,
                face::setMouthOffsetX,
                mouthOffsetFor(expression, chosenVariant),
                duration,
                SMOOTH
        ));
        animations.add(animate(
                face::getMouthOffsetY,
                face::setMouthOffsetY,
                mouthVerticalFor(expression, chosenVariant),
                duration,
                SMOOTH
        ));
        animations.add(animate(
                face::getFaceLift,
                face::setFaceLift,
                pose.lift,
                duration,
                SPRING
        ));
        animations.add(animate(
                face::getEyeOffsetX,
                face::setEyeOffsetX,
                pose.eyeX,
                duration,
                SMOOTH
        ));
        animations.add(animate(
                face::getEyeOffsetY,
                face::setEyeOffsetY,
                pose.eyeY,
                duration,
                SMOOTH
        ));
        animations.add(animate(
                face::getErrorMix,
                face::setErrorMix,
                pose.error,
                duration,
                SMOOTH
        ));

        if ("sleeping".equals(expression)) {
            animations.add(animate(
                    face::getLeftEyeOpen,
                    face::setLeftEyeOpen,
                    0.025f,
                    520L,
                    SMOOTH
            ));
            animations.add(animate(
                    face::getRightEyeOpen,
                    face::setRightEyeOpen,
                    0.025f,
                    540L,
                    SMOOTH
            ));
        } else if ("sleeping".equals(previous)) {
            animations.add(animate(
                    face::getLeftEyeOpen,
                    face::setLeftEyeOpen,
                    1f,
                    430L,
                    SMOOTH
            ));
            animations.add(animate(
                    face::getRightEyeOpen,
                    face::setRightEyeOpen,
                    1f,
                    455L,
                    SMOOTH
            ));
        }

        poseAnimator = new AnimatorSet();
        poseAnimator.playTogether(animations);
        poseAnimator.start();

        if ("listening".equals(expression)) {
            handler.postDelayed(acknowledgementBlinkRunnable, repeated ? 90L : 190L);
        } else if ("error".equals(expression)) {
            handler.postDelayed(this::errorFlash, duration + 20L);
        } else if ("sleeping".equals(expression)) {
            scheduleSleepDecoration(true);
        }

        handler.postDelayed(
                () -> playEntryAccent(expression, chosenVariant),
                Math.min(220L, duration - 80L)
        );

        scheduleDrift(false);
        scheduleMicroExpression();
    }

    void onConnected() {
        handler.removeCallbacks(safeIdleRunnable);
        handler.removeCallbacks(safeSleepRunnable);
    }

    void onDisconnected(boolean remainSleeping) {
        speaking = false;
        receivingSpeechLevels = false;
        stopSpeechMotion();
        handler.removeCallbacks(safeIdleRunnable);
        handler.removeCallbacks(safeSleepRunnable);
        if (remainSleeping) {
            setExpression("sleeping");
            return;
        }
        handler.postDelayed(safeIdleRunnable, 800L);
        handler.postDelayed(safeSleepRunnable, 5L * 60L * 1000L);
    }

    void onSpeechStarted() {
        speaking = true;
        receivingSpeechLevels = false;
        setExpression("speaking");
        scheduleSimulatedSyllable(20L);
        handler.removeCallbacks(speechWatchdog);
        handler.postDelayed(speechWatchdog, 260L);
    }

    void onSpeechLevel(float level) {
        if (!speaking) {
            onSpeechStarted();
        }
        receivingSpeechLevels = true;
        lastSpeechLevelAt = android.os.SystemClock.elapsedRealtime();
        handler.removeCallbacks(speechRunnable);
        animateSpeechTarget(0.03f + Protocol.clamp(level, 0f, 1f) * 0.82f);
        handler.removeCallbacks(speechWatchdog);
        handler.postDelayed(speechWatchdog, 260L);
    }

    void onSpeechEnded() {
        speaking = false;
        receivingSpeechLevels = false;
        handler.removeCallbacks(speechRunnable);
        handler.removeCallbacks(speechWatchdog);
        if ("speaking".equals(face.getExpression())) {
            setExpression("idle");
        } else {
            stopSpeechMotion();
        }
    }

    void destroy() {
        handler.removeCallbacksAndMessages(null);
        cancel(poseAnimator);
        cancel(blinkAnimator);
        cancel(driftAnimator);
        cancel(microAnimator);
        cancel(speechAnimator);
        cancel(accentAnimator);
        cancel(entryAnimator);
        cancel(decorationAnimator);
        cancel(tongueAnimator);
    }

    private Pose poseFor(String expression, int variant) {
        Pose[] poses = posesFor(expression);
        return poses[Math.max(0, Math.min(variant, poses.length - 1))];
    }

    private Pose[] posesFor(String expression) {
        switch (expression) {
            case "listening":
                return LISTENING_POSES;
            case "thinking":
                return THINKING_POSES;
            case "speaking":
                return SPEAKING_POSES;
            case "happy":
                return HAPPY_POSES;
            case "confused":
                return CONFUSED_POSES;
            case "error":
                return ERROR_POSES;
            case "sleeping":
                return SLEEPING_POSES;
            case "idle":
            default:
                return IDLE_POSES;
        }
    }

    private int selectVariant(String expression) {
        Pose[] poses = posesFor(expression);
        int expressionIndex = expressionIndex(expression);
        int previous = lastVariants[expressionIndex];
        int selected = random.nextInt(poses.length);
        if (poses.length > 1 && selected == previous) {
            selected = (selected + 1 + random.nextInt(poses.length - 1)) % poses.length;
        }
        lastVariants[expressionIndex] = selected;
        return selected;
    }

    private int expressionIndex(String expression) {
        switch (expression) {
            case "listening": return 0;
            case "thinking": return 1;
            case "speaking": return 2;
            case "happy": return 3;
            case "confused": return 4;
            case "error": return 5;
            case "sleeping": return 6;
            case "idle":
            default: return 7;
        }
    }

    private float mouthWidthFor(String expression, int variant) {
        switch (expression) {
            case "listening": return variant == 3 ? 0.98f : 0.88f + variant * 0.02f;
            case "thinking": return variant == 2 ? 0.82f : 0.74f + variant * 0.02f;
            case "speaking": return 1.02f + variant * 0.035f;
            case "happy": return variant == 1 ? 1.24f : 1.12f + variant * 0.025f;
            case "confused": return 0.80f + variant * 0.02f;
            case "error": return 0.84f + variant * 0.015f;
            case "sleeping": return 0.80f + variant * 0.018f;
            case "idle":
            default: return 0.96f + variant * 0.018f;
        }
    }

    private float mouthTiltFor(String expression, int variant) {
        switch (expression) {
            case "listening":
                return variant == 1 ? -1.2f : (variant == 2 ? 1.2f : 0f);
            case "thinking":
                return variant == 0 ? -2.4f : (variant == 1 ? 2.4f : (variant == 3 ? -1.5f : 0f));
            case "speaking":
                return variant == 2 ? -0.8f : (variant == 3 ? 0.8f : 0f);
            case "happy":
                return variant == 3 ? -1.4f : (variant == 2 ? 0.7f : 0f);
            case "confused":
                return variant % 2 == 0 ? -3.2f : 3.2f;
            case "error":
                return variant == 2 ? -1.3f : (variant == 3 ? 1.3f : 0f);
            case "sleeping":
                return variant == 2 ? -0.7f : (variant == 3 ? 0.7f : 0f);
            case "idle":
            default:
                return variant == 1 ? -0.7f : (variant == 3 ? 0.7f : 0f);
        }
    }

    private float mouthOffsetFor(String expression, int variant) {
        switch (expression) {
            case "listening":
                return variant == 1 ? -1.5f : (variant == 2 ? 1.5f : 0f);
            case "thinking":
                return variant == 0 ? 3.5f : (variant == 1 ? -3.5f : (variant == 3 ? 2f : 0f));
            case "happy":
                return variant == 3 ? -1.5f : 0f;
            case "confused":
                return variant == 0 ? 2.5f : (variant == 1 ? -2.5f : (variant == 3 ? 3.5f : 0f));
            case "error":
                return variant == 2 ? -1.5f : (variant == 3 ? 1.5f : 0f);
            case "sleeping":
                return variant == 2 ? -1f : (variant == 3 ? 1f : 0f);
            default:
                return 0f;
        }
    }

    private float mouthVerticalFor(String expression, int variant) {
        switch (expression) {
            case "happy": return variant == 1 ? -2.5f : -1.5f;
            case "thinking": return 2f;
            case "confused": return variant == 2 ? 1.5f : 0.5f;
            case "error": return 2f;
            case "sleeping": return 3f;
            case "speaking": return -0.5f;
            default: return 0f;
        }
    }

    private void scheduleBlink(boolean initial) {
        handler.removeCallbacks(blinkRunnable);
        if ("sleeping".equals(face.getExpression())) {
            return;
        }
        long delay;
        if (initial) {
            delay = randomBetween(1400, 2600);
        } else {
            switch (face.getExpression()) {
                case "listening":
                    delay = randomBetween(4500, 8200);
                    break;
                case "thinking":
                    delay = randomBetween(2600, 5200);
                    break;
                case "speaking":
                    delay = randomBetween(3600, 6500);
                    break;
                case "happy":
                    delay = randomBetween(2800, 5800);
                    break;
                case "confused":
                    delay = randomBetween(3000, 6100);
                    break;
                case "error":
                    delay = randomBetween(5200, 9000);
                    break;
                case "idle":
                default:
                    delay = randomBetween(3000, 6800);
                    break;
            }
            if (random.nextFloat() < 0.12f) {
                delay += randomBetween(1600, 3000);
            }
        }
        handler.postDelayed(blinkRunnable, delay);
    }

    private void blinkNaturally() {
        if ("speaking".equals(face.getExpression()) && face.getMouthOpen() > 0.28f) {
            handler.postDelayed(blinkRunnable, randomBetween(90, 160));
            return;
        }
        blink(selectBlinkKind(face.getExpression()), false);
    }

    private BlinkKind selectBlinkKind(String expression) {
        int roll = random.nextInt(100);
        if ("thinking".equals(expression)) {
            if (roll < 22) return BlinkKind.PARTIAL;
            if (roll < 31) return BlinkKind.DOUBLE;
            if (roll < 34) return BlinkKind.EXPRESSIVE;
            return BlinkKind.NORMAL;
        }
        if ("happy".equals(expression)) {
            if (roll < 8) return BlinkKind.PARTIAL;
            if (roll < 28) return BlinkKind.DOUBLE;
            if (roll < 30) return BlinkKind.EXPRESSIVE;
            return BlinkKind.NORMAL;
        }
        if ("confused".equals(expression)) {
            if (roll < 8) return BlinkKind.UNEVEN;
            if (roll < 23) return BlinkKind.PARTIAL;
            if (roll < 30) return BlinkKind.DOUBLE;
            if (roll < 33) return BlinkKind.EXPRESSIVE;
            return BlinkKind.NORMAL;
        }
        if ("listening".equals(expression)) {
            if (roll < 6) return BlinkKind.PARTIAL;
            if (roll < 12) return BlinkKind.DOUBLE;
            if (roll < 13) return BlinkKind.EXPRESSIVE;
            return BlinkKind.NORMAL;
        }
        if ("speaking".equals(expression) || "error".equals(expression)) {
            if (roll < 8) return BlinkKind.PARTIAL;
            if (roll < 16) return BlinkKind.DOUBLE;
            if (roll < 17) return BlinkKind.EXPRESSIVE;
            return BlinkKind.NORMAL;
        }
        if (roll < 11) return BlinkKind.PARTIAL;
        if (roll < 24) return BlinkKind.DOUBLE;
        if (roll < 26) return BlinkKind.EXPRESSIVE;
        return BlinkKind.NORMAL;
    }

    private void blink(BlinkKind kind, boolean acknowledgement) {
        if ("sleeping".equals(face.getExpression())) {
            return;
        }
        if (blinkAnimator != null && blinkAnimator.isRunning()) {
            if (acknowledgement) {
                pendingAcknowledgement = true;
            }
            return;
        }

        handler.removeCallbacks(blinkRunnable);
        BlinkProfile firstProfile = createBlinkProfile(kind, false);
        AnimatorSet first = oneBlink(firstProfile, acknowledgement);
        blinkAnimator = new AnimatorSet();
        if (kind == BlinkKind.DOUBLE) {
            BlinkProfile secondProfile = createBlinkProfile(kind, true);
            blinkAnimator.playSequentially(
                    first,
                    pause(randomBetween(76, 118)),
                    oneBlink(secondProfile, false)
            );
        } else {
            blinkAnimator.play(first);
        }
        blinkAnimator.addListener(new AnimatorListenerAdapter() {
            @Override
            public void onAnimationEnd(Animator animation) {
                blinkAnimator = null;
                face.setBlinkDip(0f);
                if (pendingAcknowledgement && "listening".equals(face.getExpression())) {
                    pendingAcknowledgement = false;
                    handler.postDelayed(acknowledgementBlinkRunnable, 80L);
                    return;
                }
                pendingAcknowledgement = false;
                scheduleBlink(false);
            }
        });
        blinkAnimator.start();
    }

    private BlinkProfile createBlinkProfile(BlinkKind kind, boolean secondOfDouble) {
        switch (kind) {
            case PARTIAL:
                float partialTarget = randomFloat(0.52f, 0.64f);
                return new BlinkProfile(
                        partialTarget,
                        partialTarget,
                        randomBetween(40, 55),
                        randomBetween(6, 14),
                        randomBetween(75, 105),
                        randomFloat(0.35f, 0.75f)
                );
            case EXPRESSIVE:
                float expressiveTarget = randomFloat(0.04f, 0.08f);
                return new BlinkProfile(
                        expressiveTarget,
                        expressiveTarget,
                        randomBetween(68, 82),
                        randomBetween(34, 50),
                        randomBetween(135, 170),
                        randomFloat(1.2f, 1.7f)
                );
            case UNEVEN:
                float deepTarget = randomFloat(0.07f, 0.16f);
                float shallowTarget = randomFloat(0.45f, 0.60f);
                boolean leftDeep = random.nextBoolean();
                return new BlinkProfile(
                        leftDeep ? deepTarget : shallowTarget,
                        leftDeep ? shallowTarget : deepTarget,
                        randomBetween(55, 72),
                        randomBetween(18, 30),
                        randomBetween(115, 150),
                        randomFloat(0.9f, 1.35f)
                );
            case ACKNOWLEDGEMENT:
                return new BlinkProfile(0.06f, 0.06f, 50L, 18L, 102L, 1.0f);
            case DOUBLE:
                if (secondOfDouble) {
                    return new BlinkProfile(
                            0.02f,
                            0.02f,
                            randomBetween(42, 52),
                            randomBetween(16, 24),
                            randomBetween(86, 108),
                            randomFloat(0.9f, 1.35f)
                    );
                }
                return new BlinkProfile(
                        0.025f,
                        0.025f,
                        randomBetween(44, 54),
                        randomBetween(14, 22),
                        randomBetween(78, 100),
                        randomFloat(0.85f, 1.25f)
                );
            case NORMAL:
            default:
                float target = randomFloat(0.015f, 0.035f);
                return new BlinkProfile(
                        target,
                        target,
                        randomBetween(46, 64),
                        randomBetween(18, 32),
                        randomBetween(92, 130),
                        randomFloat(1.1f, 1.7f)
                );
        }
    }

    private AnimatorSet oneBlink(BlinkProfile profile, boolean acknowledgement) {
        long leftDelay = 0L;
        long rightDelay = 0L;
        if (!acknowledgement) {
            int leadRoll = random.nextInt(10);
            if (leadRoll < 3) {
                rightDelay = randomBetween(4, 10);
            } else if (leadRoll < 6) {
                leftDelay = randomBetween(4, 10);
            }
        }

        AnimatorSet left = eyelidSequence(
                face::getLeftEyeOpen,
                face::setLeftEyeOpen,
                profile.leftTarget,
                leftDelay,
                profile
        );
        AnimatorSet right = eyelidSequence(
                face::getRightEyeOpen,
                face::setRightEyeOpen,
                profile.rightTarget,
                rightDelay,
                profile
        );
        AnimatorSet dip = blinkDipSequence(profile);
        AnimatorSet group = new AnimatorSet();
        group.playTogether(left, right, dip);
        return group;
    }

    private AnimatorSet eyelidSequence(
            FloatGetter getter,
            FloatSetter setter,
            float target,
            long delay,
            BlinkProfile profile
    ) {
        ValueAnimator close = animate(
                getter,
                setter,
                target,
                profile.closeDuration,
                BLINK_CLOSE
        );
        close.setStartDelay(delay);
        ValueAnimator open = animate(
                getter,
                setter,
                1f,
                profile.openDuration,
                BLINK_OPEN
        );
        AnimatorSet sequence = new AnimatorSet();
        sequence.playSequentially(close, pause(profile.holdDuration), open);
        return sequence;
    }

    private AnimatorSet blinkDipSequence(BlinkProfile profile) {
        AnimatorSet sequence = new AnimatorSet();
        sequence.playSequentially(
                animate(
                        face::getBlinkDip,
                        face::setBlinkDip,
                        profile.dip,
                        profile.closeDuration,
                        BLINK_CLOSE
                ),
                pause(profile.holdDuration),
                animate(
                        face::getBlinkDip,
                        face::setBlinkDip,
                        0f,
                        profile.openDuration,
                        BLINK_OPEN
                )
        );
        return sequence;
    }

    private void scheduleDrift(boolean initial) {
        handler.removeCallbacks(driftRunnable);
        handler.postDelayed(
                driftRunnable,
                initial ? randomBetween(1500, 2800) : randomBetween(3200, 6800)
        );
    }

    private void animateDrift() {
        cancel(driftAnimator);
        String expression = face.getExpression();
        long duration = randomBetween(1500, 2800);
        List<Animator> motions = new ArrayList<>();

        if ("idle".equals(expression) || "happy".equals(expression) || "sleeping".equals(expression)) {
            float xRange = "sleeping".equals(expression) ? 0.65f : 1.35f;
            float yRange = "sleeping".equals(expression) ? 0.40f : 0.80f;
            motions.add(animate(
                    face::getIdleOffsetX,
                    face::setIdleOffsetX,
                    randomFloat(-xRange, xRange),
                    duration,
                    DRIFT_SMOOTH
            ));
            motions.add(animate(
                    face::getIdleOffsetY,
                    face::setIdleOffsetY,
                    randomFloat(-yRange, yRange),
                    duration,
                    DRIFT_SMOOTH
            ));
        } else if ("thinking".equals(expression)) {
            motions.add(animate(
                    face::getEyeOffsetX,
                    face::setEyeOffsetX,
                    randomFloat(-3.2f, 3.2f),
                    duration,
                    DRIFT_SMOOTH
            ));
        } else {
            motions.add(animate(face::getIdleOffsetX, face::setIdleOffsetX, 0f, duration, DRIFT_SMOOTH));
            motions.add(animate(face::getIdleOffsetY, face::setIdleOffsetY, 0f, duration, DRIFT_SMOOTH));
        }

        driftAnimator = new AnimatorSet();
        driftAnimator.playTogether(motions);
        driftAnimator.addListener(new AnimatorListenerAdapter() {
            @Override
            public void onAnimationEnd(Animator animation) {
                driftAnimator = null;
                scheduleDrift(false);
            }
        });
        driftAnimator.start();
    }

    private void scheduleMicroExpression() {
        handler.removeCallbacks(microRunnable);
        handler.postDelayed(microRunnable, randomBetween(8000, 20000));
    }

    private void animateMicroExpression() {
        String expression = face.getExpression();
        if ("speaking".equals(expression)
                || (entryAnimator != null && entryAnimator.isRunning())) {
            scheduleMicroExpression();
            return;
        }

        int choice = random.nextInt(4);
        cancel(microAnimator);
        List<Animator> pulses = new ArrayList<>();
        switch (expression) {
            case "listening":
                if (choice == 0) {
                    blink(BlinkKind.ACKNOWLEDGEMENT, false);
                } else if (choice == 1) {
                    pulses.add(pulse(face::getMicroLeftScale, face::setMicroLeftScale, 1.035f, 1f));
                    pulses.add(pulse(face::getMicroRightScale, face::setMicroRightScale, 1.035f, 1f));
                } else if (choice == 2) {
                    pulses.add(pulse(face::getMicroLeftTilt, face::setMicroLeftTilt, -0.75f, 0f));
                    pulses.add(pulse(face::getMicroRightTilt, face::setMicroRightTilt, -0.45f, 0f));
                } else {
                    playDecoration("attention", 800L);
                }
                break;
            case "thinking":
                if (choice == 0) {
                    blink(BlinkKind.PARTIAL, false);
                } else if (choice == 1) {
                    pulses.add(pulse(face::getMicroLeftScale, face::setMicroLeftScale, 0.91f, 1f));
                    pulses.add(pulse(face::getMicroRightScale, face::setMicroRightScale, 1.02f, 1f));
                } else if (choice == 2) {
                    pulses.add(pulse(face::getMicroLeftTilt, face::setMicroLeftTilt, -1f, 0f));
                    pulses.add(pulse(face::getMicroRightTilt, face::setMicroRightTilt, 0.45f, 0f));
                } else {
                    playDecoration("thinking_dots", 1350L);
                }
                break;
            case "happy":
                if (choice == 0) {
                    pulses.add(pulse(face::getMicroLeftScale, face::setMicroLeftScale, 0.93f, 1f));
                    pulses.add(pulse(face::getMicroRightScale, face::setMicroRightScale, 0.93f, 1f));
                    pulses.add(pulse(face::getMicroSmile, face::setMicroSmile, 1.12f, 1f));
                } else if (choice == 1) {
                    pulses.add(pulse(face::getMicroLeftScale, face::setMicroLeftScale, 0.90f, 1f));
                    pulses.add(pulse(face::getMicroRightScale, face::setMicroRightScale, 1.035f, 1f));
                    pulses.add(pulse(face::getMicroLeftTilt, face::setMicroLeftTilt, -1.2f, 0f));
                    pulses.add(pulse(face::getMicroRightTilt, face::setMicroRightTilt, 0.6f, 0f));
                } else if (choice == 2) {
                    if (random.nextBoolean()) {
                        playTonguePeek(randomBetween(420, 650));
                    } else {
                        playDecoration("sparkles", 1100L);
                    }
                } else {
                    blink(BlinkKind.DOUBLE, false);
                }
                break;
            case "confused":
                if (choice == 0) {
                    blink(BlinkKind.UNEVEN, false);
                } else if (choice == 1) {
                    pulses.add(pulse(face::getMicroLeftTilt, face::setMicroLeftTilt, -1.3f, 0f));
                    pulses.add(pulse(face::getMicroRightTilt, face::setMicroRightTilt, -0.8f, 0f));
                } else if (choice == 2) {
                    pulses.add(pulse(face::getMicroLeftScale, face::setMicroLeftScale, 0.90f, 1f));
                    pulses.add(pulse(face::getMicroRightScale, face::setMicroRightScale, 1.035f, 1f));
                } else {
                    playDecoration("question", 1200L);
                }
                break;
            case "error":
                if (choice == 0) {
                    blink(BlinkKind.PARTIAL, false);
                } else {
                    pulses.add(pulse(face::getMicroLeftScale, face::setMicroLeftScale, 0.94f, 1f));
                    pulses.add(pulse(face::getMicroRightScale, face::setMicroRightScale, 0.94f, 1f));
                }
                break;
            case "sleeping":
                if (choice < 2) {
                    pulses.add(pulse(face::getMicroSmile, face::setMicroSmile, 1.045f, 1f));
                } else {
                    float sleepyTilt = choice == 2 ? 0.45f : -0.45f;
                    pulses.add(pulse(face::getMicroLeftTilt, face::setMicroLeftTilt, sleepyTilt, 0f));
                    pulses.add(pulse(face::getMicroRightTilt, face::setMicroRightTilt, -sleepyTilt, 0f));
                }
                break;
            case "idle":
            default:
                if (choice == 0) {
                    pulses.add(pulse(face::getMicroLeftScale, face::setMicroLeftScale, 0.94f, 1f));
                    pulses.add(pulse(face::getMicroRightScale, face::setMicroRightScale, 0.94f, 1f));
                } else if (choice == 1) {
                    pulses.add(pulse(face::getMicroLeftScale, face::setMicroLeftScale, 0.90f, 1f));
                    pulses.add(pulse(face::getMicroRightScale, face::setMicroRightScale, 1.035f, 1f));
                    pulses.add(pulse(face::getMicroLeftTilt, face::setMicroLeftTilt, -1.2f, 0f));
                    pulses.add(pulse(face::getMicroRightTilt, face::setMicroRightTilt, 0.6f, 0f));
                } else if (choice == 2) {
                    pulses.add(pulse(face::getMicroSmile, face::setMicroSmile, 1.10f, 1f));
                    pulses.add(pulse(face::getMicroLeftScale, face::setMicroLeftScale, 0.96f, 1f));
                    pulses.add(pulse(face::getMicroRightScale, face::setMicroRightScale, 0.96f, 1f));
                    if (random.nextFloat() < 0.18f) {
                        playTonguePeek(randomBetween(300, 480));
                    }
                } else {
                    blink(BlinkKind.DOUBLE, false);
                }
                break;
        }

        if (pulses.isEmpty()) {
            scheduleMicroExpression();
            return;
        }

        microAnimator = new AnimatorSet();
        microAnimator.playTogether(pulses);
        microAnimator.addListener(new AnimatorListenerAdapter() {
            @Override
            public void onAnimationEnd(Animator animation) {
                microAnimator = null;
                scheduleMicroExpression();
            }
        });
        microAnimator.start();
    }

    private AnimatorSet pulse(
            FloatGetter getter,
            FloatSetter setter,
            float target,
            float resting
    ) {
        AnimatorSet pulse = new AnimatorSet();
        pulse.playSequentially(
                animate(getter, setter, target, randomBetween(280, 430), SOFT),
                pause(randomBetween(380, 720)),
                animate(getter, setter, resting, randomBetween(420, 620), SOFT)
        );
        return pulse;
    }

    private void resetMicroChannels() {
        cancel(microAnimator);
        cancel(entryAnimator);
        cancel(tongueAnimator);
        entryAnimator = null;
        tongueAnimator = null;
        face.setMicroLeftScale(1f);
        face.setMicroRightScale(1f);
        face.setMicroLeftTilt(0f);
        face.setMicroRightTilt(0f);
        face.setMicroSmile(1f);
        face.setTongueAmount(0f);
    }

    private void playTonguePeek(long holdDuration) {
        String expression = face.getExpression();
        if (!"happy".equals(expression) && !"idle".equals(expression)) {
            return;
        }

        cancel(tongueAnimator);
        AnimatorSet animator = new AnimatorSet();
        animator.playSequentially(
                animate(face::getTongueAmount, face::setTongueAmount, 1f, 150L, SPRING),
                pause(holdDuration),
                animate(face::getTongueAmount, face::setTongueAmount, 0f, 250L, SOFT)
        );
        animator.addListener(new AnimatorListenerAdapter() {
            @Override
            public void onAnimationEnd(Animator animation) {
                if (tongueAnimator == animator) {
                    face.setTongueAmount(0f);
                    tongueAnimator = null;
                }
            }
        });
        tongueAnimator = animator;
        animator.start();
    }

    private void playEntryAccent(String expression, int variant) {
        if (!expression.equals(face.getExpression()) || variant != activeVariant) {
            return;
        }

        cancel(entryAnimator);
        List<Animator> accents = new ArrayList<>();
        switch (expression) {
            case "listening":
                accents.add(quickPulse(
                        face::getMicroLeftScale,
                        face::setMicroLeftScale,
                        variant == 1 ? 1.055f : 1.035f,
                        1f
                ));
                accents.add(quickPulse(
                        face::getMicroRightScale,
                        face::setMicroRightScale,
                        variant == 2 ? 1.055f : 1.035f,
                        1f
                ));
                if (variant == 3) {
                    playDecoration("attention", 780L);
                }
                break;
            case "thinking":
                accents.add(quickPulse(
                        face::getMicroLeftTilt,
                        face::setMicroLeftTilt,
                        variant % 2 == 0 ? -0.85f : 0.45f,
                        0f
                ));
                accents.add(quickPulse(
                        face::getMicroRightTilt,
                        face::setMicroRightTilt,
                        variant % 2 == 0 ? -0.45f : 0.85f,
                        0f
                ));
                if (variant >= 2) {
                    playDecoration("thinking_dots", 1350L);
                }
                break;
            case "speaking":
                // Speech already compresses the eyes in time with syllables, so its
                // entry accent only warms the smile and never fights that channel.
                accents.add(quickPulse(
                        face::getMicroSmile,
                        face::setMicroSmile,
                        variant == 3 ? 1.16f : 1.08f,
                        1f
                ));
                break;
            case "happy":
                accents.add(quickPulse(
                        face::getMicroLeftScale,
                        face::setMicroLeftScale,
                        variant == 1 ? 1.055f : 0.94f,
                        1f
                ));
                accents.add(quickPulse(
                        face::getMicroRightScale,
                        face::setMicroRightScale,
                        variant == 1 ? 1.055f : 0.94f,
                        1f
                ));
                accents.add(quickPulse(
                        face::getMicroSmile,
                        face::setMicroSmile,
                        1.14f,
                        1f
                ));
                if (variant == 1 || variant == 3) {
                    playDecoration("sparkles", 1150L);
                    if (variant == 3) {
                        playTonguePeek(520L);
                    }
                } else if (variant == 0) {
                    blink(BlinkKind.EXPRESSIVE, false);
                }
                break;
            case "confused":
                accents.add(quickPulse(
                        face::getMicroLeftTilt,
                        face::setMicroLeftTilt,
                        variant % 2 == 0 ? -1.1f : 0.75f,
                        0f
                ));
                accents.add(quickPulse(
                        face::getMicroRightTilt,
                        face::setMicroRightTilt,
                        variant % 2 == 0 ? -0.7f : 1.1f,
                        0f
                ));
                if (variant == 0 || variant == 3) {
                    playDecoration("question", 1200L);
                }
                break;
            case "error":
                accents.add(quickPulse(
                        face::getMicroLeftScale,
                        face::setMicroLeftScale,
                        0.92f,
                        1f
                ));
                accents.add(quickPulse(
                        face::getMicroRightScale,
                        face::setMicroRightScale,
                        0.92f,
                        1f
                ));
                break;
            case "sleeping":
                accents.add(quickPulse(
                        face::getMicroSmile,
                        face::setMicroSmile,
                        1.055f,
                        1f
                ));
                break;
            case "idle":
            default:
                accents.add(quickPulse(
                        face::getMicroLeftScale,
                        face::setMicroLeftScale,
                        variant == 2 ? 1.025f : 0.98f,
                        1f
                ));
                accents.add(quickPulse(
                        face::getMicroRightScale,
                        face::setMicroRightScale,
                        variant == 2 ? 1.025f : 0.98f,
                        1f
                ));
                break;
        }

        if (accents.isEmpty()) {
            return;
        }
        entryAnimator = new AnimatorSet();
        entryAnimator.playTogether(accents);
        entryAnimator.addListener(new AnimatorListenerAdapter() {
            @Override
            public void onAnimationEnd(Animator animation) {
                entryAnimator = null;
            }
        });
        entryAnimator.start();
    }

    private AnimatorSet quickPulse(
            FloatGetter getter,
            FloatSetter setter,
            float target,
            float resting
    ) {
        AnimatorSet pulse = new AnimatorSet();
        pulse.playSequentially(
                animate(getter, setter, target, randomBetween(105, 155), SOFT),
                pause(randomBetween(40, 85)),
                animate(getter, setter, resting, randomBetween(210, 300), SPRING)
        );
        return pulse;
    }

    private void scheduleSleepDecoration(boolean initial) {
        handler.removeCallbacks(sleepDecorationRunnable);
        if (!"sleeping".equals(face.getExpression())) {
            return;
        }
        handler.postDelayed(
                sleepDecorationRunnable,
                initial ? randomBetween(1100, 2200) : randomBetween(4600, 8200)
        );
    }

    private void showSleepDecoration() {
        if (!"sleeping".equals(face.getExpression())) {
            return;
        }
        playDecoration("sleep_z", 1850L);
        scheduleSleepDecoration(false);
    }

    private void playDecoration(String kind, long duration) {
        cancel(decorationAnimator);
        face.setDecoration(kind);
        face.setDecorationProgress(0f);

        AnimatorSet animator = new AnimatorSet();
        animator.play(animate(
                face::getDecorationProgress,
                face::setDecorationProgress,
                1f,
                duration,
                SOFT
        ));
        animator.addListener(new AnimatorListenerAdapter() {
            @Override
            public void onAnimationEnd(Animator animation) {
                if (decorationAnimator == animator) {
                    face.setDecorationProgress(0f);
                    face.setDecoration("none");
                    decorationAnimator = null;
                }
            }
        });
        decorationAnimator = animator;
        animator.start();
    }

    private void clearDecoration() {
        cancel(decorationAnimator);
        decorationAnimator = null;
        face.setDecorationProgress(0f);
        face.setDecoration("none");
    }

    private void checkSpeechSamples() {
        if (!speaking) {
            return;
        }
        long age = android.os.SystemClock.elapsedRealtime() - lastSpeechLevelAt;
        if (!receivingSpeechLevels || age > 250L) {
            receivingSpeechLevels = false;
            scheduleSimulatedSyllable(15L);
        }
        handler.postDelayed(speechWatchdog, 250L);
    }

    private void scheduleSimulatedSyllable(long delay) {
        if (!speaking || receivingSpeechLevels) {
            return;
        }
        handler.removeCallbacks(speechRunnable);
        handler.postDelayed(speechRunnable, delay);
    }

    private void animateSimulatedSyllable() {
        if (!speaking || receivingSpeechLevels) {
            return;
        }
        int roll = random.nextInt(100);
        int total = 0;
        float target = SPEECH_SHAPES[0];
        for (int index = 0; index < SPEECH_WEIGHTS.length; index++) {
            total += SPEECH_WEIGHTS[index];
            if (roll < total) {
                target = SPEECH_SHAPES[index];
                break;
            }
        }
        animateSpeechTarget(target);
        scheduleSimulatedSyllable(randomBetween(70, 165));
    }

    private void animateSpeechTarget(float target) {
        cancel(speechAnimator);
        long duration = randomBetween(65, 125);
        float compression = 1f - Math.min(0.035f, target * 0.045f);
        speechAnimator = new AnimatorSet();
        speechAnimator.playTogether(
                animate(face::getMouthOpen, face::setMouthOpen, target, duration, SOFT),
                animate(
                        face::getMicroLeftScale,
                        face::setMicroLeftScale,
                        compression,
                        duration + 30L,
                        SOFT
                ),
                animate(
                        face::getMicroRightScale,
                        face::setMicroRightScale,
                        compression,
                        duration + 30L,
                        SOFT
                )
        );
        speechAnimator.start();
    }

    private void stopSpeechMotion() {
        handler.removeCallbacks(speechRunnable);
        handler.removeCallbacks(speechWatchdog);
        cancel(speechAnimator);
        speechAnimator = new AnimatorSet();
        speechAnimator.playTogether(
                animate(face::getMouthOpen, face::setMouthOpen, 0f, 170L, SOFT),
                animate(face::getMicroLeftScale, face::setMicroLeftScale, 1f, 190L, SOFT),
                animate(face::getMicroRightScale, face::setMicroRightScale, 1f, 190L, SOFT)
        );
        speechAnimator.start();
    }

    private void errorFlash() {
        if (!"error".equals(face.getExpression())) {
            return;
        }
        cancel(accentAnimator);
        float restingGlow = poseFor("error", activeVariant).glow;
        accentAnimator = new AnimatorSet();
        accentAnimator.playSequentially(
                animate(face::getGlowStrength, face::setGlowStrength, 0.50f, 75L, SOFT),
                animate(face::getGlowStrength, face::setGlowStrength, restingGlow, 210L, SOFT)
        );
        accentAnimator.start();
    }

    private ValueAnimator animate(
            FloatGetter getter,
            FloatSetter setter,
            float target,
            long duration,
            Interpolator interpolator
    ) {
        final float[] start = {getter.get()};
        ValueAnimator animator = ValueAnimator.ofFloat(0f, 1f);
        animator.setDuration(duration);
        animator.setInterpolator(interpolator);
        animator.addListener(new AnimatorListenerAdapter() {
            @Override
            public void onAnimationStart(Animator animation) {
                start[0] = getter.get();
            }
        });
        animator.addUpdateListener(value -> {
            float progress = (float) value.getAnimatedValue();
            setter.set(start[0] + (target - start[0]) * progress);
        });
        return animator;
    }

    private Animator pause(long duration) {
        ValueAnimator delay = ValueAnimator.ofFloat(0f, 0f);
        delay.setDuration(duration);
        return delay;
    }

    private void cancel(Animator animator) {
        if (animator != null) {
            animator.removeAllListeners();
            animator.cancel();
        }
    }

    private long randomBetween(int minimum, int maximum) {
        return minimum + random.nextInt(maximum - minimum + 1);
    }

    private float randomFloat(float minimum, float maximum) {
        return minimum + random.nextFloat() * (maximum - minimum);
    }
}
