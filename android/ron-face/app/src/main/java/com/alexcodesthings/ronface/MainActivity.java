package com.alexcodesthings.ronface;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.view.View;
import android.view.WindowManager;

@SuppressWarnings("deprecation")
public final class MainActivity extends Activity implements SignalServer.Listener {
    private static final String TAG = "RonFaceActivity";
    private static final String PREFERENCES = "ron_face_private";
    private static final String TOKEN_KEY = "pairing_token";
    private static final String TOKEN_EXTRA = "ron_pairing_token";
    private static final long HEALTH_REPORT_INTERVAL_MS = 30_000L;

    private final Handler handler = new Handler(Looper.getMainLooper());

    private RonFaceView faceView;
    private RonTabletPager tabletPager;
    private FaceAnimator animator;
    private SignalServer signalServer;
    private BatteryHealthMonitor batteryMonitor;
    private boolean computerConnected;
    private boolean protectiveSleep;
    private int batteryPercent = -1;
    private boolean charging;
    private float temperatureC;
    private float appliedBrightness = Float.NaN;

    private final Runnable safetyCheck = new Runnable() {
        @Override
        public void run() {
            if (computerConnected && !signalServer.isHeartbeatHealthy()) {
                signalServer.closeStaleClient();
                computerConnected = false;
                animator.onDisconnected(protectiveSleep);
            }
            handler.postDelayed(this, 1_000L);
        }
    };

    private final Runnable healthReport = new Runnable() {
        @Override
        public void run() {
            signalServer.sendDeviceHealth(batteryPercent, charging, temperatureC);
            handler.postDelayed(this, HEALTH_REPORT_INTERVAL_MS);
        }
    };

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        applyImmersiveMode();

        faceView = new RonFaceView(this);
        animator = new FaceAnimator(faceView);
        faceView.setFaceTapListener(this::onFaceTapped);
        tabletPager = new RonTabletPager(this, faceView, this::dispatchQuickAction);
        setContentView(tabletPager);

        storeTokenFromIntent(getIntent());
        startSignalServer();
        batteryMonitor = new BatteryHealthMonitor(this, this::onBatteryHealth);
        batteryMonitor.start();
        handler.post(safetyCheck);
        handler.post(healthReport);
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        String previous = loadPairingToken();
        storeTokenFromIntent(intent);
        String current = loadPairingToken();
        if (!current.equals(previous)) {
            restartSignalServer();
        }
        applyImmersiveMode();
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus) {
            applyImmersiveMode();
        }
    }

    @Override
    public void onBackPressed() {
        if (tabletPager != null && tabletPager.isBlankPageVisible()) {
            tabletPager.showFacePage();
            return;
        }
        super.onBackPressed();
    }

    @Override
    protected void onDestroy() {
        handler.removeCallbacksAndMessages(null);
        if (batteryMonitor != null) {
            batteryMonitor.stop();
        }
        if (signalServer != null) {
            signalServer.stop();
        }
        if (animator != null) {
            animator.destroy();
        }
        super.onDestroy();
    }

    @Override
    public void onConnected() {
        computerConnected = true;
        animator.onConnected();
    }

    @Override
    public void onDisconnected() {
        if (!computerConnected) {
            return;
        }
        computerConnected = false;
        animator.onDisconnected(protectiveSleep);
    }

    @Override
    public void onExpression(String expression) {
        animator.setExpression(protectiveSleep ? "sleeping" : expression);
    }

    @Override
    public void onSpeechStarted() {
        if (!protectiveSleep) {
            animator.onSpeechStarted();
        }
    }

    @Override
    public void onSpeechLevel(float level) {
        if (!protectiveSleep) {
            animator.onSpeechLevel(level);
        }
    }

    @Override
    public void onSpeechEnded() {
        animator.onSpeechEnded();
    }

    @Override
    public void onQuickActionResult(long requestId, boolean success, String message) {
        if (tabletPager != null) {
            tabletPager.onQuickActionResult(requestId, success, message);
        }
    }

    @Override
    public void onProtocolError(String message) {
        Log.w(TAG, message);
    }

    private boolean dispatchQuickAction(String action, long requestId) {
        return signalServer != null && signalServer.sendQuickAction(action, requestId);
    }

    private void onFaceTapped(float normalisedX, float normalisedY) {
        if (animator == null) {
            return;
        }
        // Thermal and critical-battery sleep is a safety state, so a tap may
        // still produce a tiny response but cannot force the display awake.
        boolean wokeFromSleep = !protectiveSleep
                && faceView != null
                && "sleeping".equals(faceView.getExpression());
        animator.onFaceTapped(normalisedX, normalisedY, !protectiveSleep);
        if (wokeFromSleep && signalServer != null) {
            signalServer.sendFaceWake();
        }
    }

    private void onBatteryHealth(int percent, boolean isCharging, float temperature) {
        batteryPercent = percent;
        charging = isCharging;
        temperatureC = temperature;

        boolean wasProtectiveSleep = protectiveSleep;
        boolean criticalHeat = temperature >= 45f;
        boolean criticalBattery = percent >= 0 && percent <= 3 && !isCharging;
        protectiveSleep = criticalHeat || criticalBattery;

        // The face must remain readable in a normally lit room. The halo is
        // controlled separately in RonFaceView, so brightness can favour eye colour.
        float brightness = 0.62f;
        if (temperature >= 40f) {
            brightness = 0.28f;
        }
        if (protectiveSleep) {
            brightness = 0.10f;
            animator.setExpression("sleeping");
        } else {
            if (percent >= 0 && percent <= 10 && !isCharging) {
                brightness = Math.min(brightness, 0.22f);
            }
            if (wasProtectiveSleep) {
                animator.setExpression("idle");
                signalServer.requestSnapshot();
            }
        }
        setBrightness(brightness);
        signalServer.sendDeviceHealth(percent, isCharging, temperature);
    }

    private void startSignalServer() {
        signalServer = new SignalServer(loadPairingToken(), this);
        signalServer.start();
    }

    private void restartSignalServer() {
        computerConnected = false;
        animator.onDisconnected(protectiveSleep);
        signalServer.stop();
        startSignalServer();
    }

    private void storeTokenFromIntent(Intent intent) {
        if (intent == null) {
            return;
        }
        String supplied = intent.getStringExtra(TOKEN_EXTRA);
        if (supplied == null || supplied.length() < 32 || supplied.length() > 256) {
            return;
        }
        if (!loadPairingToken().isEmpty()) {
            return;
        }
        getSharedPreferences(PREFERENCES, MODE_PRIVATE)
                .edit()
                .putString(TOKEN_KEY, supplied)
                .apply();
    }

    private String loadPairingToken() {
        SharedPreferences preferences = getSharedPreferences(PREFERENCES, MODE_PRIVATE);
        return preferences.getString(TOKEN_KEY, "");
    }

    private void setBrightness(float brightness) {
        float safeBrightness = Protocol.clamp(brightness, 0.05f, 1f);
        if (!Float.isNaN(appliedBrightness)
                && Math.abs(appliedBrightness - safeBrightness) < 0.001f) {
            return;
        }
        WindowManager.LayoutParams parameters = getWindow().getAttributes();
        parameters.screenBrightness = safeBrightness;
        getWindow().setAttributes(parameters);
        appliedBrightness = safeBrightness;
    }

    private void applyImmersiveMode() {
        getWindow().getDecorView().setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                        | View.SYSTEM_UI_FLAG_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_LAYOUT_STABLE
        );
    }
}
