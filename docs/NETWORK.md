# Ron Network

Ron Network is the small optional LAN layer that lets Ron's laptop brain discover and track companion devices such as the Nexus 7 face and future ESP32 hardware.

It is deliberately **not** in the path for local commands. Volume, brightness, applications, local AI, voice, reminders and other laptop features continue to call their existing local code directly.

```text
                    Ron Brain (laptop)
                    /              \
            local features       Ron Network
            AI / voice /         /    |     \
            Windows tools      Face  future  sensors
                                     devices
```

If the entire network side disappears, normal local Ron continues running.

## What runs where

### Laptop

- device registry and health state
- low-frequency UDP discovery
- authentication token storage
- tablet connection and command handling
- AI, planning, memory and automation

### Nexus 7

- face rendering and animations
- touch and quick-action input
- battery/temperature reporting
- authenticated TCP signal server
- tiny UDP discovery responder

The Nexus does not run Ron's AI or network-management logic.

## Discovery

The laptop sends a short UDP discovery request on port `8766` roughly every 10 seconds. Ron devices may reply with non-secret information:

- device ID and friendly name
- device type
- TCP port
- capabilities
- small non-secret metadata such as app version

The pairing token is **never** included in discovery traffic.

Discovery is only a convenience. If broadcasts are blocked, set `RON_FACE_HOST` to the tablet's LAN address. USB/ADB also remains a fallback for the Nexus.

For the current hardware setup, keep the secondary router in **access-point/bridge mode** on the same LAN when possible. Avoid a second NAT/subnet for this milestone, and disable Wi-Fi client isolation if the router has that option; otherwise the laptop and Nexus may be prevented from seeing each other. Ron never needs the router's admin API.

## Device states

Ron uses four human-readable states:

- `UNKNOWN` — known but not yet proven reachable
- `ONLINE` — authenticated service is responding
- `DEGRADED` — recently seen or briefly disconnected, but not healthy enough to call online
- `OFFLINE` — no useful contact within the timeout

A single missed packet does not immediately mark a device offline. The default ageing window is 8 seconds to degraded and 20 seconds to offline, while the tablet's existing heartbeat continues to run every 2 seconds.

## Pairing and trust

The Nexus uses the existing per-install pairing token stored under `runtime/data/face_pairing_token`. The tablet receives that token during USB/ADB setup and requires it in the TCP handshake. A fresh tablet install may therefore use USB once for initial pairing; after that, normal reconnects can happen over the LAN.

Discovery does not grant trust. A newly discovered device is `unknown` until a trusted/authenticated path proves otherwise. Unknown devices cannot gain control merely by answering a broadcast.

The tablet server listens on the local LAN now, but unauthenticated clients are rejected before face commands or quick actions are processed.

## Failure behaviour

Ron Network uses short socket timeouts, low-frequency discovery and background threads. Normal behaviour on failure is:

```text
Router/Wi-Fi unavailable  -> network devices unavailable
Tablet disconnected       -> face degrades/offlines, then reconnects
Discovery blocked         -> manual host or USB fallback can still work
Bad discovery packet      -> ignored
Wrong pairing token       -> handshake rejected
Network subsystem error   -> warning/log; local Ron keeps running
```

There are no infinite retries or busy network scans.

## Future devices

A future device should have a stable ID such as:

```text
ron-light-desk
ron-motion-bedroom
ron-mic-lounge
```

It can answer the same discovery format and later use the shared bounded JSON protocol. Physical devices should report confirmed state after commands rather than making Ron assume success.

Milestone 1 intentionally does **not** implement lights, presence automation, Wake-on-LAN or room microphones. It only provides the foundation those features can safely use later.
