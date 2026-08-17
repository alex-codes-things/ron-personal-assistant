package com.alexcodesthings.ronface;

import android.animation.Animator;
import android.animation.AnimatorListenerAdapter;
import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Path;
import android.graphics.RadialGradient;
import android.graphics.Shader;
import android.view.Gravity;
import android.view.HapticFeedbackConstants;
import android.view.MotionEvent;
import android.view.View;
import android.view.animation.PathInterpolator;
import android.widget.FrameLayout;

/**
 * Two-page tablet shell that keeps Ron's face independent from future panels.
 *
 * <p>The second page is deliberately empty for now. Both pages are native
 * Android views, so changing panels never interrupts the local signal server or
 * introduces browser/network latency.</p>
 */
final class RonTabletPager extends FrameLayout {
    private static final long PAGE_DURATION_MS = 410L;

    private final FrameLayout facePage;
    private final FrameLayout blankPage;
    private final PathInterpolator pageInterpolator =
            new PathInterpolator(0.22f, 1f, 0.36f, 1f);

    private boolean blankPageVisible;
    private boolean transitionRunning;

    RonTabletPager(Context context, RonFaceView faceView) {
        super(context);
        setBackgroundColor(Color.rgb(2, 6, 11));
        setClipChildren(true);
        setClipToPadding(true);

        int buttonSize = dp(54f);
        int horizontalMargin = dp(19f);
        int topMargin = dp(17f);

        blankPage = new FrameLayout(context);
        blankPage.setContentDescription("Ron's blank panel");
        blankPage.addView(
                new BlankPanelView(context),
                new LayoutParams(LayoutParams.MATCH_PARENT, LayoutParams.MATCH_PARENT)
        );
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
        super.onDetachedFromWindow();
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

    /** Dark empty canvas that exactly matches Ron's current screen atmosphere. */
    private static final class BlankPanelView extends View {
        private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG | Paint.DITHER_FLAG);
        private Shader backgroundShader;

        BlankPanelView(Context context) {
            super(context);
            setBackgroundColor(Color.rgb(2, 6, 11));
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
            paint.setStyle(Paint.Style.FILL);
            if (backgroundShader == null) {
                paint.setShader(null);
                paint.setColor(Color.rgb(2, 6, 11));
            } else {
                paint.setShader(backgroundShader);
                paint.setColor(Color.WHITE);
            }
            canvas.drawRect(0f, 0f, getWidth(), getHeight(), paint);
            paint.setShader(null);
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
