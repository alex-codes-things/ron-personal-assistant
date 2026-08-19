package com.alexcodesthings.ronface;

import android.util.Log;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

import java.io.IOException;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.atomic.AtomicBoolean;

/** Lightweight LAN discovery. It never broadcasts or returns the pairing token. */
final class DiscoveryResponder {
    private static final String TAG = "RonDiscovery";
    private static final int MAX_DATAGRAM_BYTES = 8192;

    private final AtomicBoolean running = new AtomicBoolean(false);
    private volatile DatagramSocket socket;
    private Thread thread;

    void start() {
        if (!running.compareAndSet(false, true)) {
            return;
        }
        thread = new Thread(this::loop, "ron-face-discovery");
        thread.setDaemon(true);
        thread.start();
    }

    void stop() {
        running.set(false);
        DatagramSocket active = socket;
        socket = null;
        if (active != null) {
            active.close();
        }
        if (thread != null) {
            try {
                thread.join(1000L);
            } catch (InterruptedException exception) {
                Thread.currentThread().interrupt();
            }
        }
    }

    private void loop() {
        byte[] buffer = new byte[MAX_DATAGRAM_BYTES];
        while (running.get()) {
            try {
                DatagramSocket responder = new DatagramSocket(null);
                responder.setReuseAddress(true);
                responder.bind(new InetSocketAddress("0.0.0.0", Protocol.DISCOVERY_PORT));
                socket = responder;

                while (running.get()) {
                    DatagramPacket packet = new DatagramPacket(buffer, buffer.length);
                    responder.receive(packet);
                    handlePacket(responder, packet);
                }
            } catch (IOException exception) {
                if (running.get()) {
                    Log.w(TAG, "Discovery responder unavailable; retrying", exception);
                    sleepQuietly(1000L);
                }
            } finally {
                DatagramSocket active = socket;
                socket = null;
                if (active != null) {
                    active.close();
                }
            }
        }
    }

    private void handlePacket(DatagramSocket responder, DatagramPacket packet) {
        if (packet.getLength() <= 0 || packet.getLength() > MAX_DATAGRAM_BYTES) {
            return;
        }
        String raw = new String(
                packet.getData(),
                packet.getOffset(),
                packet.getLength(),
                StandardCharsets.UTF_8
        );
        JSONObject request;
        try {
            request = new JSONObject(raw);
        } catch (JSONException exception) {
            return;
        }
        if (!"ron_discover".equals(request.optString("type"))) {
            return;
        }
        if (request.optInt("protocol", -1) != Protocol.VERSION) {
            return;
        }
        String requestId = request.optString("request_id", "");
        if (requestId.length() < 1 || requestId.length() > 80) {
            return;
        }

        try {
            JSONObject reply = new JSONObject();
            reply.put("type", "ron_device");
            reply.put("protocol", Protocol.VERSION);
            reply.put("request_id", requestId);
            reply.put("device_id", "ron-face");
            reply.put("friendly_name", "Ron Face");
            reply.put("device_type", "display");
            reply.put("port", Protocol.PORT);
            reply.put("capabilities", new JSONArray()
                    .put("face")
                    .put("quick_actions")
                    .put("battery_health"));
            reply.put("metadata", new JSONObject()
                    .put("face_version", BuildConfig.VERSION_NAME));

            byte[] encoded = reply.toString().getBytes(StandardCharsets.UTF_8);
            if (encoded.length > MAX_DATAGRAM_BYTES) {
                return;
            }
            DatagramPacket response = new DatagramPacket(
                    encoded,
                    encoded.length,
                    packet.getAddress(),
                    packet.getPort()
            );
            responder.send(response);
        } catch (JSONException | IOException exception) {
            Log.d(TAG, "Could not answer discovery request", exception);
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
