package com.alexcodesthings.ronface;

import android.animation.Animator;
import android.animation.AnimatorListenerAdapter;
import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.LinearGradient;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.Rect;
import android.graphics.RectF;
import android.graphics.Shader;
import android.view.Gravity;
import android.view.HapticFeedbackConstants;
import android.view.MotionEvent;
import android.view.View;
import android.view.animation.PathInterpolator;
import android.widget.FrameLayout;
import android.widget.GridLayout;

import java.util.HashMap;
import java.util.Map;

/**
 * Two-page tablet shell that keeps Ron's face independent from future panels.
 *
 * <p>The second page hosts Ron's native tool grid. Changing panels never
 * interrupts the local signal server or introduces browser/network latency.</p>
 */
final class RonTabletPager extends FrameLayout {
    private static final long PAGE_DURATION_MS = 410L;
    private static final long QUICK_ACTION_TIMEOUT_MS = 4_000L;

    interface QuickActionDispatcher {
        boolean dispatch(String action, long requestId);
    }

    private final FrameLayout facePage;
    private final FrameLayout blankPage;
    private final QuickActionDispatcher quickActionDispatcher;
    private final Map<Long, ToolButton> pendingQuickActions = new HashMap<>();
    private final PathInterpolator pageInterpolator =
            new PathInterpolator(0.22f, 1f, 0.36f, 1f);

    private boolean blankPageVisible;
    private boolean transitionRunning;
    private long nextQuickActionId = 1L;

    RonTabletPager(
            Context context,
            RonFaceView faceView,
            QuickActionDispatcher quickActionDispatcher
    ) {
        super(context);
        this.quickActionDispatcher = quickActionDispatcher;
        setBackgroundColor(Color.rgb(2, 6, 11));
        setClipChildren(true);
        setClipToPadding(true);

        int buttonSize = dp(54f);
        int horizontalMargin = dp(19f);
        int topMargin = dp(17f);

        blankPage = new FrameLayout(context);
        blankPage.setContentDescription("Ron's tools panel");
        blankPage.addView(
                new BlankPanelView(context),
                new LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT)
        );

        int toolTileSize = dp(124f);
        int toolGap = dp(16f);
        int toolColumns = 5;
        int toolRows = 3;
        int toolGridWidth = toolTileSize * toolColumns + toolGap * (toolColumns - 1);
        int toolGridHeight = toolTileSize * toolRows + toolGap * (toolRows - 1);

        GridLayout toolGrid = new GridLayout(context);
        toolGrid.setColumnCount(toolColumns);
        toolGrid.setRowCount(toolRows);
        toolGrid.setAlignmentMode(GridLayout.ALIGN_BOUNDS);
        toolGrid.setUseDefaultMargins(false);
        toolGrid.setTranslationY(dp(26f));

        ToolButton faceToolButton = new ToolButton(context, ToolKind.RON);
        faceToolButton.setContentDescription("Return to Ron's face");
        faceToolButton.setOnClickListener(view -> showFacePage());
        GridLayout.LayoutParams faceToolLayout = new GridLayout.LayoutParams(
                GridLayout.spec(0),
                GridLayout.spec(0)
        );
        faceToolLayout.width = toolTileSize;
        faceToolLayout.height = toolTileSize;
        faceToolLayout.setMargins(0, 0, toolGap, toolGap);
        toolGrid.addView(faceToolButton, faceToolLayout);

        ToolButton spotifyButton = new ToolButton(context, ToolKind.SPOTIFY);
        spotifyButton.setContentDescription("Open Spotify on Ron's laptop");
        spotifyButton.setOnClickListener(
                view -> dispatchQuickAction("open_spotify", spotifyButton)
        );
        GridLayout.LayoutParams spotifyLayout = new GridLayout.LayoutParams(
                GridLayout.spec(0),
                GridLayout.spec(1)
        );
        spotifyLayout.width = toolTileSize;
        spotifyLayout.height = toolTileSize;
        spotifyLayout.setMargins(0, 0, toolGap, toolGap);
        toolGrid.addView(spotifyButton, spotifyLayout);

        ToolButton youtubeButton = new ToolButton(context, ToolKind.YOUTUBE);
        youtubeButton.setContentDescription("Open YouTube in Brave on Ron's laptop");
        youtubeButton.setOnClickListener(
                view -> dispatchQuickAction("open_youtube", youtubeButton)
        );
        GridLayout.LayoutParams youtubeLayout = new GridLayout.LayoutParams(
                GridLayout.spec(0),
                GridLayout.spec(2)
        );
        youtubeLayout.width = toolTileSize;
        youtubeLayout.height = toolTileSize;
        youtubeLayout.setMargins(0, 0, toolGap, toolGap);
        toolGrid.addView(youtubeButton, youtubeLayout);
        LayoutParams toolGridLayout = new LayoutParams(toolGridWidth, toolGridHeight);
        toolGridLayout.gravity = Gravity.CENTER;
        blankPage.addView(toolGrid, toolGridLayout);

        ArrowButton returnButton = new ArrowButton(context, false);
        returnButton.setContentDescription("Return to Ron's face");
        returnButton.setOnClickListener(view -> showFacePage());
        LayoutParams returnButtonLayout = new LayoutParams(buttonSize, buttonSize);
        returnButtonLayout.gravity = Gravity.TOP | Gravity.START;
        returnButtonLayout.leftMargin = horizontalMargin;
        returnButtonLayout.topMargin = topMargin;
        blankPage.addView(returnButton, returnButtonLayout);
        blankPage.setVisibility(INVISIBLE);
        addView(
                blankPage,
                new LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT)
        );

        facePage = new FrameLayout(context);
        facePage.setContentDescription("Ron's face panel");
        facePage.addView(
                faceView,
                new LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT)
        );
        ArrowButton nextButton = new ArrowButton(context, true);
        nextButton.setContentDescription("Open Ron's next panel");
        nextButton.setOnClickListener(view -> showBlankPage());
        LayoutParams nextButtonLayout = new LayoutParams(buttonSize, buttonSize);
        nextButtonLayout.gravity = Gravity.TOP | Gravity.END;
        nextButtonLayout.rightMargin = horizontalMargin;
        nextButtonLayout.topMargin = topMargin;
        facePage.addView(nextButton, nextButtonLayout);
        addView(
                facePage,
                new LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT)
        );
    }

    boolean isBlankPageVisible() {
        return blankPageVisible;
    }

    void showBlankPage() {
        if (blankPageVisible || transitionRunning || getWidth() <= 0) {
            return;
        }
        transitionRunning = true;
        blankPage.setVisibility(VISIBLE);
        blankPage.setTranslationX(getWidth());
        facePage.setTranslationX(0f);
        animatePages(-getWidth(), 0f, true);
    }

    void showFacePage() {
        if (!blankPageVisible || transitionRunning || getWidth() <= 0) {
            return;
        }
        transitionRunning = true;
        facePage.setVisibility(VISIBLE);
        facePage.setTranslationX(-getWidth());
        blankPage.setTranslationX(0f);
        animatePages(0f, getWidth(), false);
    }

    void onQuickActionResult(long requestId, boolean success, String message) {
        ToolButton button = pendingQuickActions.remove(requestId);
        if (button == null) {
            return;
        }
        button.showResult(success, message);
    }

    @Override
    protected void onSizeChanged(int width, int height, int oldWidth, int oldHeight) {
        super.onSizeChanged(width, height, oldWidth, oldHeight);
        cancelPageAnimations();
        transitionRunning = false;
        if (blankPageVisible) {
            facePage.setTranslationX(-width);
            facePage.setVisibility(INVISIBLE);
            blankPage.setTranslationX(0f);
            blankPage.setVisibility(VISIBLE);
        } else {
            facePage.setTranslationX(0f);
            facePage.setVisibility(VISIBLE);
            blankPage.setTranslationX(width);
            blankPage.setVisibility(INVISIBLE);
        }
    }

    @Override
    protected void onDetachedFromWindow() {
        cancelPageAnimations();
        for (ToolButton button : pendingQuickActions.values()) {
            button.cancelPendingState();
        }
        pendingQuickActions.clear();
        super.onDetachedFromWindow();
    }

    private void dispatchQuickAction(String action, ToolButton button) {
        if (button.isRequestPending()) {
            return;
        }
        long requestId = nextQuickActionId++;
        if (nextQuickActionId <= 0L) {
            nextQuickActionId = 1L;
        }
        button.showPending();
        if (!quickActionDispatcher.dispatch(action, requestId)) {
            button.showResult(false, "Ron is not connected to the laptop");
            return;
        }

        pendingQuickActions.put(requestId, button);
        postDelayed(() -> {
            ToolButton waitingButton = pendingQuickActions.remove(requestId);
            if (waitingButton != null) {
                waitingButton.showResult(false, "The laptop did not respond");
            }
        }, QUICK_ACTION_TIMEOUT_MS);
    }

    private void animatePages(float faceTarget, float blankTarget, boolean toBlank) {
        facePage.animate()
                .translationX(faceTarget)
                .setDuration(PAGE_DURATION_MS)
                .setInterpolator(pageInterpolator)
                .start();
        blankPage.animate()
                .translationX(blankTarget)
                .setDuration(PAGE_DURATION_MS)
                .setInterpolator(pageInterpolator)
                .setListener(new AnimatorListenerAdapter() {
                    private boolean cancelled;

                    @Override
                    public void onAnimationEnd(Animator animation) {
                        blankPage.animate().setListener(null);
                        if (cancelled) {
                            return;
                        }
                        blankPageVisible = toBlank;
                        transitionRunning = false;
                        if (toBlank) {
                            facePage.setVisibility(INVISIBLE);
                        } else {
                            blankPage.setVisibility(INVISIBLE);
                        }
                    }

                    @Override
                    public void onAnimationCancel(Animator animation) {
                        cancelled = true;
                        blankPage.animate().setListener(null);
                        transitionRunning = false;
                    }
                })
                .start();
    }

    private void cancelPageAnimations() {
        facePage.animate().cancel();
        blankPage.animate().cancel();
        blankPage.animate().setListener(null);
    }

    private int dp(float value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    /** Ron-themed contour canvas used behind the tablet tool grid. */
    private static final class BlankPanelView extends View {
        private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.DITHER_FLAG);
        private final Rect bitmapSource = new Rect();
        private final RectF bitmapDestination = new RectF();
        private final Bitmap contourPattern;

        BlankPanelView(Context context) {
            super(context);
            setBackgroundColor(Color.rgb(7, 19, 29));
            contourPattern = BitmapFactory.decodeResource(
                    getResources(),
                    R.drawable.ron_contour_background
            );
            if (contourPattern != null) {
                bitmapSource.set(0, 0, contourPattern.getWidth(), contourPattern.getHeight());
            }
        }

        @Override
        protected void onSizeChanged(int width, int height, int oldWidth, int oldHeight) {
            super.onSizeChanged(width, height, oldWidth, oldHeight);
            bitmapDestination.set(0f, 0f, Math.max(0, width), Math.max(0, height));
        }

        @Override
        protected void onDraw(Canvas canvas) {
            super.onDraw(canvas);

            paint.setStyle(Paint.Style.FILL);
            paint.setShader(null);
            paint.setColor(Color.rgb(7, 19, 29));
            paint.setAlpha(255);
            canvas.drawRect(0f, 0f, getWidth(), getHeight(), paint);

            if (contourPattern != null && !bitmapDestination.isEmpty()) {
                paint.setAlpha(255);
                paint.setFilterBitmap(true);
                canvas.drawBitmap(contourPattern, bitmapSource, bitmapDestination, paint);
            }

            paint.setAlpha(255);
            paint.setFilterBitmap(false);
            paint.setShader(null);
        }
    }

    private enum ToolKind {
        RON,
        SPOTIFY,
        YOUTUBE
    }

    /** Large Stream Deck-style tile with native-drawn icons and result feedback. */
    private static final class ToolButton extends View {
        private static final int SQUIRCLE_POINTS = 64;
        private static final int FEEDBACK_NONE = 0;
        private static final int FEEDBACK_PENDING = 1;
        private static final int FEEDBACK_SUCCESS = 2;
        private static final int FEEDBACK_FAILED = 3;

        private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.DITHER_FLAG);
        private final Path outerSquircle = new Path();
        private final Path screenSquircle = new Path();
        private final Path logoPath = new Path();
        private final RectF leftEye = new RectF();
        private final RectF rightEye = new RectF();
        private final RectF mouth = new RectF();
        private final RectF logoBounds = new RectF();
        private final ToolKind kind;
        private final float density;
        private final Runnable clearFeedback = () -> {
            feedbackState = FEEDBACK_NONE;
            invalidate();
        };

        private Shader tileShader;
        private Shader eyeShader;
        private boolean requestPending;
        private int feedbackState = FEEDBACK_NONE;

        ToolButton(Context context, ToolKind kind) {
            super(context);
            this.kind = kind;
            density = getResources().getDisplayMetrics().density;
            setClickable(true);
            setFocusable(true);
            setSoundEffectsEnabled(false);
        }

        @Override
        protected void onSizeChanged(int width, int height, int oldWidth, int oldHeight) {
            super.onSizeChanged(width, height, oldWidth, oldHeight);
            buildSquircle(outerSquircle, width, height, 3.5f * density);
            buildSquircle(screenSquircle, width, height, 14f * density);

            float centreX = width / 2f;
            float eyeTop = height * 0.315f;
            float eyeWidth = width * 0.135f;
            float eyeHeight = height * 0.285f;
            float eyeGap = width * 0.115f;
            leftEye.set(
                    centreX - eyeGap / 2f - eyeWidth,
                    eyeTop,
                    centreX - eyeGap / 2f,
                    eyeTop + eyeHeight
            );
            rightEye.set(
                    centreX + eyeGap / 2f,
                    eyeTop,
                    centreX + eyeGap / 2f + eyeWidth,
                    eyeTop + eyeHeight
            );
            mouth.set(
                    centreX - width * 0.105f,
                    height * 0.635f,
                    centreX + width * 0.105f,
                    height * 0.765f
            );

            tileShader = new LinearGradient(
                    0f,
                    0f,
                    width,
                    height,
                    new int[]{Color.rgb(24, 62, 72), Color.rgb(8, 27, 38)},
                    null,
                    Shader.TileMode.CLAMP
            );
            eyeShader = new LinearGradient(
                    0f,
                    eyeTop,
                    0f,
                    eyeTop + eyeHeight,
                    new int[]{Color.rgb(147, 255, 233), Color.rgb(32, 218, 196)},
                    null,
                    Shader.TileMode.CLAMP
            );
        }

        @Override
        protected void drawableStateChanged() {
            super.drawableStateChanged();
            invalidate();
        }

        @Override
        protected void onDraw(Canvas canvas) {
            super.onDraw(canvas);
            boolean pressed = isPressed();

            // A broad, low-alpha halo separates the tile from the contour texture.
            canvas.save();
            canvas.scale(1.07f, 1.07f, getWidth() / 2f, getHeight() / 2f);
            paint.setShader(null);
            paint.setStyle(Paint.Style.FILL);
            paint.setColor(pressed
                    ? Color.argb(70, 47, 234, 211)
                    : Color.argb(42, 47, 234, 211));
            canvas.drawPath(outerSquircle, paint);
            canvas.restore();

            paint.setStyle(Paint.Style.FILL);
            paint.setShader(tileShader);
            paint.setAlpha(pressed ? 255 : 246);
            canvas.drawPath(outerSquircle, paint);

            paint.setShader(null);
            paint.setAlpha(255);
            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeWidth((pressed ? 1.8f : 1.25f) * density);
            paint.setColor(pressed
                    ? Color.argb(235, 130, 255, 235)
                    : Color.argb(172, 91, 235, 215));
            canvas.drawPath(outerSquircle, paint);

            paint.setStyle(Paint.Style.FILL);
            paint.setColor(Color.argb(246, 2, 9, 14));
            canvas.drawPath(screenSquircle, paint);

            switch (kind) {
                case SPOTIFY:
                    drawSpotify(canvas);
                    break;
                case YOUTUBE:
                    drawYouTube(canvas);
                    break;
                case RON:
                default:
                    drawRon(canvas, pressed);
                    break;
            }

            drawFeedback(canvas);
        }

        private void drawRon(Canvas canvas, boolean pressed) {
            drawMiniEyeGlow(canvas, leftEye, pressed);
            drawMiniEyeGlow(canvas, rightEye, pressed);

            paint.setShader(eyeShader);
            paint.setStyle(Paint.Style.FILL);
            paint.setAlpha(255);
            float eyeRadius = Math.min(leftEye.width(), leftEye.height()) * 0.32f;
            canvas.drawRoundRect(leftEye, eyeRadius, eyeRadius, paint);
            canvas.drawRoundRect(rightEye, eyeRadius, eyeRadius, paint);

            paint.setShader(null);
            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeWidth(2.25f * density);
            paint.setStrokeCap(Paint.Cap.ROUND);
            paint.setColor(pressed
                    ? Color.rgb(204, 255, 246)
                    : Color.rgb(82, 241, 215));
            canvas.drawArc(mouth, 20f, 140f, false, paint);
        }

        private void drawSpotify(Canvas canvas) {
            float centreX = getWidth() / 2f;
            float centreY = getHeight() / 2f;
            float radius = Math.min(getWidth(), getHeight()) * 0.27f;

            paint.setShader(null);
            paint.setStyle(Paint.Style.FILL);
            paint.setColor(Color.rgb(30, 215, 96));
            canvas.drawCircle(centreX, centreY, radius, paint);

            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeCap(Paint.Cap.ROUND);
            paint.setStrokeJoin(Paint.Join.ROUND);
            paint.setColor(Color.rgb(3, 18, 12));
            drawSpotifyBand(canvas, centreX, centreY, radius, -0.29f, 0.070f);
            drawSpotifyBand(canvas, centreX, centreY, radius, 0.02f, 0.060f);
            drawSpotifyBand(canvas, centreX, centreY, radius, 0.30f, 0.050f);
        }

        private void drawSpotifyBand(
                Canvas canvas,
                float centreX,
                float centreY,
                float radius,
                float verticalPosition,
                float strokeScale
        ) {
            logoPath.reset();
            float startX = centreX - radius * 0.59f;
            float endX = centreX + radius * 0.59f;
            float y = centreY + radius * verticalPosition;
            logoPath.moveTo(startX, y);
            logoPath.quadTo(
                    centreX,
                    y - radius * 0.25f,
                    endX,
                    y + radius * 0.055f
            );
            paint.setStrokeWidth(radius * strokeScale);
            canvas.drawPath(logoPath, paint);
        }

        private void drawYouTube(Canvas canvas) {
            float centreX = getWidth() / 2f;
            float centreY = getHeight() / 2f;
            float width = getWidth() * 0.53f;
            float height = getHeight() * 0.36f;
            logoBounds.set(
                    centreX - width / 2f,
                    centreY - height / 2f,
                    centreX + width / 2f,
                    centreY + height / 2f
            );

            paint.setShader(null);
            paint.setStyle(Paint.Style.FILL);
            paint.setColor(Color.rgb(255, 0, 45));
            float corner = height * 0.25f;
            canvas.drawRoundRect(logoBounds, corner, corner, paint);

            logoPath.reset();
            float triangleWidth = width * 0.24f;
            float triangleHeight = height * 0.48f;
            logoPath.moveTo(centreX - triangleWidth * 0.36f, centreY - triangleHeight / 2f);
            logoPath.lineTo(centreX - triangleWidth * 0.36f, centreY + triangleHeight / 2f);
            logoPath.lineTo(centreX + triangleWidth * 0.64f, centreY);
            logoPath.close();
            paint.setColor(Color.WHITE);
            canvas.drawPath(logoPath, paint);
        }

        private void drawFeedback(Canvas canvas) {
            if (feedbackState == FEEDBACK_NONE) {
                return;
            }
            int colour;
            if (feedbackState == FEEDBACK_PENDING) {
                colour = Color.rgb(126, 244, 225);
            } else if (feedbackState == FEEDBACK_SUCCESS) {
                colour = Color.rgb(117, 255, 190);
            } else {
                colour = Color.rgb(255, 145, 105);
            }
            paint.setShader(null);
            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeJoin(Paint.Join.ROUND);
            paint.setStrokeWidth(3f * density);
            paint.setColor(colour);
            canvas.drawPath(outerSquircle, paint);
        }

        boolean isRequestPending() {
            return requestPending;
        }

        void showPending() {
            removeCallbacks(clearFeedback);
            requestPending = true;
            feedbackState = FEEDBACK_PENDING;
            invalidate();
        }

        void showResult(boolean success, String message) {
            requestPending = false;
            feedbackState = success ? FEEDBACK_SUCCESS : FEEDBACK_FAILED;
            if (message != null && !message.isEmpty()) {
                announceForAccessibility(message);
            }
            invalidate();
            removeCallbacks(clearFeedback);
            postDelayed(clearFeedback, success ? 850L : 1_250L);
        }

        void cancelPendingState() {
            requestPending = false;
            feedbackState = FEEDBACK_NONE;
            removeCallbacks(clearFeedback);
            invalidate();
        }

        private void drawMiniEyeGlow(Canvas canvas, RectF eye, boolean pressed) {
            paint.setShader(null);
            paint.setStyle(Paint.Style.FILL);
            paint.setColor(Color.argb(pressed ? 46 : 30, 47, 234, 211));
            float glow = 5.5f * density;
            canvas.drawRoundRect(
                    eye.left - glow,
                    eye.top - glow,
                    eye.right + glow,
                    eye.bottom + glow,
                    glow,
                    glow,
                    paint
            );
        }

        @Override
        public boolean onTouchEvent(MotionEvent event) {
            int action = event.getActionMasked();
            if (action == MotionEvent.ACTION_DOWN) {
                animate().scaleX(0.94f).scaleY(0.94f).setDuration(70L).start();
            } else if (action == MotionEvent.ACTION_UP) {
                performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY);
                animate().scaleX(1f).scaleY(1f).setDuration(150L).start();
            } else if (action == MotionEvent.ACTION_CANCEL) {
                animate().scaleX(1f).scaleY(1f).setDuration(150L).start();
            }
            return super.onTouchEvent(event);
        }

        @Override
        protected void onDetachedFromWindow() {
            removeCallbacks(clearFeedback);
            super.onDetachedFromWindow();
        }

        private void buildSquircle(Path path, int width, int height, float inset) {
            path.reset();
            float centreX = width / 2f;
            float centreY = height / 2f;
            float halfWidth = Math.max(0f, width / 2f - inset);
            float halfHeight = Math.max(0f, height / 2f - inset);
            float power = 2f / 4.8f;
            for (int index = 0; index <= SQUIRCLE_POINTS; index++) {
                double angle = Math.PI * 2.0 * index / SQUIRCLE_POINTS;
                double cosine = Math.cos(angle);
                double sine = Math.sin(angle);
                float x = centreX + halfWidth
                        * Math.signum((float) cosine)
                        * (float) Math.pow(Math.abs(cosine), power);
                float y = centreY + halfHeight
                        * Math.signum((float) sine)
                        * (float) Math.pow(Math.abs(sine), power);
                if (index == 0) {
                    path.moveTo(x, y);
                } else {
                    path.lineTo(x, y);
                }
            }
            path.close();
        }
    }

    /** Mostly-transparent squircle with a soft directional arrow. */
    private static final class ArrowButton extends View {
        private static final int SQUIRCLE_POINTS = 56;

        private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.DITHER_FLAG);
        private final Path squircle = new Path();
        private final Path arrow = new Path();
        private final boolean pointsLeft;
        private final float density;

        ArrowButton(Context context, boolean pointsLeft) {
            super(context);
            this.pointsLeft = pointsLeft;
            density = getResources().getDisplayMetrics().density;
            setClickable(true);
            setFocusable(true);
            setSoundEffectsEnabled(false);
        }

        @Override
        protected void onSizeChanged(int width, int height, int oldWidth, int oldHeight) {
            super.onSizeChanged(width, height, oldWidth, oldHeight);
            buildSquircle(width, height);
            buildArrow(width, height);
        }

        @Override
        protected void drawableStateChanged() {
            super.drawableStateChanged();
            invalidate();
        }

        @Override
        protected void onDraw(Canvas canvas) {
            super.onDraw(canvas);
            boolean pressed = isPressed();

            paint.setShader(null);
            paint.setStyle(Paint.Style.FILL);
            paint.setColor(pressed
                    ? Color.argb(62, 91, 225, 205)
                    : Color.argb(34, 91, 225, 205));
            canvas.drawPath(squircle, paint);

            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeWidth(1.15f * density);
            paint.setStrokeJoin(Paint.Join.ROUND);
            paint.setColor(pressed
                    ? Color.argb(116, 144, 255, 236)
                    : Color.argb(62, 144, 255, 236));
            canvas.drawPath(squircle, paint);

            paint.setStyle(Paint.Style.STROKE);
            paint.setStrokeWidth(2.15f * density);
            paint.setStrokeCap(Paint.Cap.ROUND);
            paint.setStrokeJoin(Paint.Join.ROUND);
            paint.setColor(pressed
                    ? Color.argb(255, 210, 255, 247)
                    : Color.argb(220, 180, 255, 242));
            canvas.drawPath(arrow, paint);
        }

        @Override
        public boolean onTouchEvent(MotionEvent event) {
            int action = event.getActionMasked();
            if (action == MotionEvent.ACTION_DOWN) {
                animate().scaleX(0.92f).scaleY(0.92f).setDuration(75L).start();
            } else if (action == MotionEvent.ACTION_UP) {
                performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY);
                animate().scaleX(1f).scaleY(1f).setDuration(145L).start();
            } else if (action == MotionEvent.ACTION_CANCEL) {
                animate().scaleX(1f).scaleY(1f).setDuration(145L).start();
            }
            return super.onTouchEvent(event);
        }

        private void buildSquircle(int width, int height) {
            squircle.reset();
            float centreX = width / 2f;
            float centreY = height / 2f;
            float halfWidth = Math.max(0f, width / 2f - 1.5f * density);
            float halfHeight = Math.max(0f, height / 2f - 1.5f * density);
            float power = 2f / 4.6f;
            for (int index = 0; index <= SQUIRCLE_POINTS; index++) {
                double angle = Math.PI * 2.0 * index / SQUIRCLE_POINTS;
                double cosine = Math.cos(angle);
                double sine = Math.sin(angle);
                float x = centreX + halfWidth
                        * Math.signum((float) cosine)
                        * (float) Math.pow(Math.abs(cosine), power);
                float y = centreY + halfHeight
                        * Math.signum((float) sine)
                        * (float) Math.pow(Math.abs(sine), power);
                if (index == 0) {
                    squircle.moveTo(x, y);
                } else {
                    squircle.lineTo(x, y);
                }
            }
            squircle.close();
        }

        private void buildArrow(int width, int height) {
            arrow.reset();
            float centreX = width / 2f;
            float centreY = height / 2f;
            float direction = pointsLeft ? -1f : 1f;
            float tipX = centreX + direction * 7.5f * density;
            float tailX = centreX - direction * 7.5f * density;
            float cornerX = centreX + direction * 0.5f * density;
            float wing = 7.2f * density;

            arrow.moveTo(tailX, centreY);
            arrow.lineTo(tipX, centreY);
            arrow.moveTo(tipX, centreY);
            arrow.lineTo(cornerX, centreY - wing);
            arrow.moveTo(tipX, centreY);
            arrow.lineTo(cornerX, centreY + wing);
        }
    }
}
