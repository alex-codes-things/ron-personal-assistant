package com.alexcodesthings.ronface;

import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;

final class Protocol {
    static final int VERSION = 1;
    static final int PORT = 8765;
    static final int MAX_MESSAGE_BYTES = 8192;
    static final int HANDSHAKE_TIMEOUT_MS = 5000;
    static final long HEARTBEAT_TIMEOUT_MS = 6500L;

    private static final Set<String> EXPRESSIONS = new HashSet<>(Arrays.asList(
            "idle",
            "listening",
            "thinking",
            "speaking",
            "happy",
            "confused",
            "error",
            "sleeping"
    ));

    private Protocol() {
    }

    static boolean isExpression(String value) {
        return value != null && EXPRESSIONS.contains(value);
    }

    static float clamp(float value, float minimum, float maximum) {
        return Math.max(minimum, Math.min(maximum, value));
    }
}
