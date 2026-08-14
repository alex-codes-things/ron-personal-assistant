package com.alexcodesthings.ronface;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.os.BatteryManager;

final class BatteryHealthMonitor {
    interface Listener {
        void onBatteryHealth(int percent, boolean charging, float temperatureC);
    }

    private final Context context;
    private final Listener listener;
    private boolean registered;

    private final BroadcastReceiver receiver = new BroadcastReceiver() {
        @Override
        public void onReceive(Context ignored, Intent intent) {
            dispatch(intent);
        }
    };

    BatteryHealthMonitor(Context context, Listener listener) {
        this.context = context.getApplicationContext();
        this.listener = listener;
    }

    void start() {
        if (registered) {
            return;
        }
        Intent sticky = context.registerReceiver(
                receiver,
                new IntentFilter(Intent.ACTION_BATTERY_CHANGED)
        );
        registered = true;
        if (sticky != null) {
            dispatch(sticky);
        }
    }

    void stop() {
        if (!registered) {
            return;
        }
        try {
            context.unregisterReceiver(receiver);
        } catch (IllegalArgumentException ignored) {
            // The OS may already have removed it during process teardown.
        }
        registered = false;
    }

    private void dispatch(Intent intent) {
        int level = intent.getIntExtra(BatteryManager.EXTRA_LEVEL, -1);
        int scale = intent.getIntExtra(BatteryManager.EXTRA_SCALE, -1);
        int percent = level >= 0 && scale > 0
                ? Math.round(level * 100f / scale)
                : -1;
        int temperatureTenths = intent.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, 0);
        float temperatureC = temperatureTenths / 10f;
        int status = intent.getIntExtra(BatteryManager.EXTRA_STATUS, -1);
        boolean charging = status == BatteryManager.BATTERY_STATUS_CHARGING
                || status == BatteryManager.BATTERY_STATUS_FULL;
        listener.onBatteryHealth(percent, charging, temperatureC);
    }
}
