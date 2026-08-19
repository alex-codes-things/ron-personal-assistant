package com.alexcodesthings.ronface;

import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.ByteArrayOutputStream;
import java.io.EOFException;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicBoolean;

final class SignalServer {
    interface Listener {
        void onConnected();

        void onDisconnected();

        void onExpression(String expression);

        void onSpeechStarted();

        void onSpeechLevel(float level);

        void onSpeechEnded();

        void onQuickActionResult(long requestId, boolean success, String message);

        void onProtocolError(String message);
    }

    private static final String TAG = "RonSignalServer";

    private final Listener listener;
    private final String pairingToken;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final Object writerLock = new Object();
    private final AtomicBoolean running = new AtomicBoolean(false);

    private volatile ServerSocket serverSocket;
    private volatile Socket clientSocket;
    private volatile OutputStream clientOutput;
    private volatile boolean authenticatedClient;
    private volatile long lastMessageAt;
    private volatile long lastReliableSequence = -1L;
    private Thread serverThread;

    SignalServer(String pairingToken, Listener listener) {
        this.pairingToken = pairingToken;
        this.listener = listener;
    }

    void start() {
        if (!running.compareAndSet(false, true)) {
            return;
        }
        serverThread = new Thread(this::serverLoop, "ron-face-signal-server");
        serverThread.setDaemon(true);
        serverThread.start();
    }

    void stop() {
        running.set(false);
        closeClient();
        closeServer();
        if (serverThread != null) {
            try {
                serverThread.join(1500L);
            } catch (InterruptedException exception) {
                Thread.currentThread().interrupt();
            }
        }
    }

    boolean isHeartbeatHealthy() {
        Socket socket = clientSocket;
        return socket != null
                && authenticatedClient
                && socket.isConnected()
                && !socket.isClosed()
                && SystemClock.elapsedRealtime() - lastMessageAt <= Protocol.HEARTBEAT_TIMEOUT_MS;
    }

    void closeStaleClient() {
        Socket socket = clientSocket;
        if (socket != null && !isHeartbeatHealthy()) {
            closeClient();
        }
    }

    void sendDeviceHealth(int batteryPercent, boolean charging, float temperatureC) {
        if (!authenticatedClient) {
            return;
        }
        JSONObject message = new JSONObject();
        try {
            message.put("type", "device_health");
            message.put("battery_percent", batteryPercent);
            message.put("charging", charging);
            message.put("temperature_c", temperatureC);
            send(message);
        } catch (JSONException | IOException ignored) {
            // Health is best-effort; a reconnect will restore the important face state.
        }
    }

    void requestSnapshot() {
        if (!authenticatedClient) {
            return;
        }
        JSONObject message = new JSONObject();
        try {
            message.put("type", "request_snapshot");
            send(message);
        } catch (JSONException | IOException ignored) {
            // The heartbeat path will reconnect and receive a snapshot if needed.
        }
    }

    boolean sendQuickAction(String action, long requestId) {
        if (!authenticatedClient || !Protocol.isQuickAction(action) || requestId <= 0L) {
            return false;
        }
        JSONObject message = new JSONObject();
        try {
            message.put("type", "quick_action");
            message.put("action", action);
            message.put("request_id", requestId);
            send(message);
            return true;
        } catch (JSONException | IOException exception) {
            Log.w(TAG, "Could not send tablet quick action", exception);
            return false;
        }
    }

    void sendFaceWake() {
        if (!authenticatedClient) {
            return;
        }
        JSONObject message = new JSONObject();
        try {
            message.put("type", "face_wake");
            send(message);
        } catch (JSONException | IOException exception) {
            // The visual response is always local and instant. This message is
            // only a best-effort state sync for the next reconnect snapshot.
            Log.i(TAG, "Could not synchronise the local face wake", exception);
        }
    }

    private void serverLoop() {
        while (running.get()) {
            try {
                ServerSocket server = new ServerSocket();
                server.setReuseAddress(true);
                server.bind(new InetSocketAddress(
                        InetAddress.getByName("0.0.0.0"),
                        Protocol.PORT
                ), 1);
                serverSocket = server;

                while (running.get()) {
                    Socket socket = server.accept();
                    socket.setTcpNoDelay(true);
                    socket.setKeepAlive(true);
                    socket.setSoTimeout(Protocol.HANDSHAKE_TIMEOUT_MS);
                    handleClient(socket);
                }
            } catch (IOException exception) {
                if (running.get()) {
                    Log.w(TAG, "Signal server failed; restarting", exception);
                    sleepQuietly(500L);
                }
            } finally {
                closeServer();
            }
        }
    }

    private void handleClient(Socket socket) {
        closeClient();
        clientSocket = socket;
        lastReliableSequence = -1L;
        boolean authenticated = false;

        try {
            clientOutput = socket.getOutputStream();
            InputStream input = new BufferedInputStream(socket.getInputStream(), 2048);
            ByteArrayOutputStream lineBuffer = new ByteArrayOutputStream(256);

            while (running.get() && !socket.isClosed()) {
                String line = readBoundedLine(input, lineBuffer);
                if (line == null) {
                    throw new EOFException("Computer closed the signal stream");
                }
                lastMessageAt = SystemClock.elapsedRealtime();

                JSONObject message;
                try {
                    message = new JSONObject(line);
                } catch (JSONException exception) {
                    reportProtocolError("Malformed JSON was rejected");
                    continue;
                }

                if (!authenticated) {
                    authenticated = authenticate(message);
                    if (!authenticated) {
                        throw new IOException("Face handshake rejected");
                    }
                    authenticatedClient = true;
                    socket.setSoTimeout((int) Protocol.HEARTBEAT_TIMEOUT_MS);
                    post(listener::onConnected);
                    continue;
                }

                handleMessage(message);
            }
        } catch (IOException exception) {
            if (running.get()) {
                Log.i(TAG, "Face client disconnected: " + exception.getMessage());
            }
        } finally {
            closeClient();
            if (authenticated) {
                post(listener::onDisconnected);
            }
        }
    }

    private boolean authenticate(JSONObject message) {
        if (!"hello".equals(message.optString("type"))) {
            reportProtocolError("The first message must be hello");
            return false;
        }
        if (message.optInt("protocol", -1) != Protocol.VERSION) {
            reportProtocolError("Protocol version mismatch");
            return false;
        }
        if (pairingToken == null || pairingToken.length() < 32) {
            reportProtocolError("Tablet has not been paired through ADB");
            return false;
        }
        if (!constantTimeEquals(pairingToken, message.optString("token", ""))) {
            reportProtocolError("Pairing token mismatch");
            return false;
        }

        JSONObject ready = new JSONObject();
        try {
            ready.put("type", "ready");
            ready.put("protocol", Protocol.VERSION);
            ready.put("device", "ron-face");
            ready.put("device_type", "display");
            ready.put("friendly_name", "Ron Face");
            ready.put("face_version", BuildConfig.VERSION_NAME);
            ready.put("capabilities", new JSONArray()
                    .put("face")
                    .put("quick_actions")
                    .put("battery_health"));
            send(ready);
            return true;
        } catch (JSONException | IOException exception) {
            reportProtocolError("Could not complete handshake");
            return false;
        }
    }

    private void handleMessage(JSONObject message) throws IOException {
        String type = message.optString("type", "");
        switch (type) {
            case "ping":
                JSONObject pong = new JSONObject();
                try {
                    pong.put("type", "pong");
                    pong.put("sent_at", message.optDouble("sent_at", 0.0));
                    send(pong);
                } catch (JSONException exception) {
                    throw new IOException("Could not encode heartbeat", exception);
                }
                break;
            case "state_snapshot":
                if (acceptReliable(message)) {
                    applySnapshot(message);
                }
                break;
            case "expression":
                if (acceptReliable(message)) {
                    applyExpression(message.optString("value"));
                }
                break;
            case "speech_started":
                if (acceptReliable(message)) {
                    post(listener::onSpeechStarted);
                }
                break;
            case "speech_ended":
                if (acceptReliable(message)) {
                    post(listener::onSpeechEnded);
                }
                break;
            case "speech_level":
                float level = Protocol.clamp((float) message.optDouble("value", 0.0), 0f, 1f);
                post(() -> listener.onSpeechLevel(level));
                break;
            case "quick_action_result":
                applyQuickActionResult(message);
                break;
            default:
                reportProtocolError("Unknown message type was ignored: " + type);
        }
    }

    private boolean acceptReliable(JSONObject message) {
        long sequence = message.optLong("sequence", -1L);
        if (sequence < 0L || sequence <= lastReliableSequence) {
            return false;
        }
        lastReliableSequence = sequence;
        return true;
    }

    private void applySnapshot(JSONObject message) {
        String expression = message.optString("expression", "idle");
        boolean speechActive = message.optBoolean("speech_active", false);
        float level = Protocol.clamp(
                (float) message.optDouble("speech_level", 0.0),
                0f,
                1f
        );

        if (!Protocol.isExpression(expression)) {
            reportProtocolError("Invalid snapshot expression was rejected");
            return;
        }

        post(() -> {
            listener.onExpression(expression);
            if (speechActive) {
                listener.onSpeechStarted();
                listener.onSpeechLevel(level);
            } else {
                listener.onSpeechEnded();
            }
        });
    }

    private void applyExpression(String expression) {
        if (!Protocol.isExpression(expression)) {
            reportProtocolError("Invalid expression was rejected");
            return;
        }
        post(() -> listener.onExpression(expression));
    }

    private void applyQuickActionResult(JSONObject message) {
        long requestId = message.optLong("request_id", -1L);
        String status = message.optString("status", "failed");
        String detail = message.optString("message", "");
        if (requestId <= 0L || !("success".equals(status) || "failed".equals(status))) {
            reportProtocolError("Invalid quick-action result was rejected");
            return;
        }
        if (detail.length() > 160) {
            detail = detail.substring(0, 160);
        }
        boolean success = "success".equals(status);
        String safeDetail = detail;
        post(() -> listener.onQuickActionResult(requestId, success, safeDetail));
    }

    private void send(JSONObject message) throws IOException {
        byte[] encoded = (message.toString() + "\n").getBytes(StandardCharsets.UTF_8);
        if (encoded.length > Protocol.MAX_MESSAGE_BYTES) {
            throw new IOException("Outgoing message exceeds size limit");
        }
        synchronized (writerLock) {
            OutputStream output = clientOutput;
            if (output == null) {
                throw new IOException("No authenticated face client");
            }
            output.write(encoded);
            output.flush();
        }
    }

    private static String readBoundedLine(
            InputStream input,
            ByteArrayOutputStream buffer
    ) throws IOException {
        buffer.reset();
        while (true) {
            int value = input.read();
            if (value < 0) {
                return buffer.size() == 0
                        ? null
                        : new String(buffer.toByteArray(), StandardCharsets.UTF_8);
            }
            if (value == '\n') {
                return new String(buffer.toByteArray(), StandardCharsets.UTF_8);
            }
            if (value != '\r') {
                buffer.write(value);
            }
            if (buffer.size() > Protocol.MAX_MESSAGE_BYTES) {
                throw new IOException("Incoming message exceeds size limit");
            }
        }
    }

    private static boolean constantTimeEquals(String first, String second) {
        byte[] left = first.getBytes(StandardCharsets.UTF_8);
        byte[] right = second.getBytes(StandardCharsets.UTF_8);
        int difference = left.length ^ right.length;
        int maximum = Math.max(left.length, right.length);
        for (int index = 0; index < maximum; index++) {
            byte leftValue = index < left.length ? left[index] : 0;
            byte rightValue = index < right.length ? right[index] : 0;
            difference |= leftValue ^ rightValue;
        }
        return difference == 0;
    }

    private void reportProtocolError(String message) {
        Log.w(TAG, message);
        post(() -> listener.onProtocolError(message));
    }

    private void post(Runnable action) {
        mainHandler.post(action);
    }

    private void closeClient() {
        Socket socket = clientSocket;
        clientSocket = null;
        clientOutput = null;
        authenticatedClient = false;
        if (socket != null) {
            try {
                socket.close();
            } catch (IOException ignored) {
            }
        }
    }

    private void closeServer() {
        ServerSocket server = serverSocket;
        serverSocket = null;
        if (server != null) {
            try {
                server.close();
            } catch (IOException ignored) {
            }
        }
    }

    private static void sleepQuietly(long milliseconds) {
        try {
            Thread.sleep(milliseconds);
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
        }
    }
}
