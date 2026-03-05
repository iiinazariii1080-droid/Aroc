# Reliability-Critical WebRTC Camera Streaming on Dual Raspberry Pi 5 with Janus, TURN, and a Gateway Topology

## System model and failure-domain decomposition

Your system is already close to a “flight-like” architecture: one node is the **single control plane** (gateway + orchestration), while camera nodes are **payload producers**. The key difference between “it works” and “it is rover-grade” is turning the whole camera/streaming chain into an explicit **FDIR system** (Failure Detection, Isolation, Recovery): every credible failure must be *detectable*, *isolatable*, and followed by a *bounded recovery action*—preferably autonomously. This is a core principle in spacecraft/mission engineering guidance. citeturn11view2turn11view4turn11view3

### Concrete failure domains in your topology

1) **Sensor domain (USB + RealSense)**  
   Failures: USB brownouts, camera disconnect, firmware hang, frame stalls, thermal throttling, bandwidth saturation.

2) **Media pipeline domain (capture → transform → encode → RTP/WebRTC)**  
   Failures: encoder overload on RPi, queue buildup, timestamp discontinuities, wrong caps/payload type, MTU fragmentation, depth stream “looks OK but is semantically wrong”.

3) **Janus domain (SFU/gateway + plugins + transports)**  
   Failures: handle/session leaks, ICE misconfiguration for multihoming, port-range mismatch, slowlink conditions, plugin-level corner cases, transport (WebSocket/HTTP) drop.

4) **Network domain (double-router topology + NAT + roaming uplink)**  
   Failures: uplink changes, symmetric NAT, UDP blocked, internal router instability, route/iptables drift, DNS/proxy mismatch.

5) **TURN/STUN domain (VPS relay infrastructure)**  
   Failures: allocation failures, abuse/DoS, auth misconfig, relay-port exhaustion, packet-rate ceilings, TLS/proxy friction.

6) **Client domain (iframe player + browser WebRTC engine)**  
   Failures: autoplay restrictions, cross-origin framing/CSP, ICE policy changes, codec support differences, getStats visibility gaps.

### “Rover-grade” control objective

Adopt a mission-style rule set:

- No single off-nominal event should cascade into “unprotected state” of the system (e.g., watchdog disabled, no recovery path). citeturn11view3turn11view4  
- When network is unavailable or degraded, the system must (a) **degrade deterministically**, (b) preserve evidence (logs/telemetry/optional ring buffer), and (c) keep the local control plane responsive. This aligns with standard fault management handbook guidance and ECSS FDIR expectations. citeturn11view2turn11view4turn11view3

### Failure-domain matrix (design artifact you should maintain)

| Domain | Primary detection signals | Isolation action | Recovery action (bounded) | “Safe mode” fallback |
|---|---|---|---|---|
| RealSense (USB) | frame counters stall; USB re-enumeration; udev events; pipeline “no buffers” | detach stream (unpublish) | restart camera service; USB reset; reboot node if repeated | disable that camera; keep control plane alive |
| Encoder/pipeline | queue latency; dropped frames; CPU temp; encoder error events | shed load (lower FPS/bitrate/res) | restart pipeline; fail over to “low-bandwidth profile” | publish “status slate” stream |
| Janus | Admin API: session/handle stuck; ICE state; “no media timer”; slowlink | kill affected handle; restart plugin context | restart Janus if systemic; rolling restart with state restore | local-only mode (LAN) |
| Uplink/NAT | ICE failures; TURN-only success rate; routing changes | enforce TURN-only | renew ICE; re-offer; rotate TURN | local viewer only |
| TURN | allocation failures; relay port exhaustion; bandwidth alerts; auth failures | rate-limit + deny peers | failover TURN endpoint; switch transport (udp→tls) | local mode |
| Client/iframe | play start timeout; autoplay blocked; getStats absent | fallback UI path | reload iframe; rejoin session; codec fallback | show “diagnostic page” |

The existence of this matrix (and keeping it current) is not bureaucracy—this is how you keep “no room for errors” honest. Space standards emphasize hierarchical FDIR and clear reporting of recovery actions. citeturn11view4turn11view3

## Media acquisition and encoding of RealSense color and depth

### RealSense stream realities that affect WebRTC reliability

The Intel D400 family exposes depth and color as **separate interfaces/endpoints** (not a single monolithic stream), and simultaneous streaming constraints exist. Depth is typically **Z16 (16-bit)**; there are also **Y8** luminance streams and color **YUY2** modes, and the D435i includes an IMU stream. citeturn11view1turn1search3

Key constraints from the D400 datasheet that are directly relevant to your “2 streams on the second camera” design:

- Depth+color can run simultaneously, but **RGB↔depth hardware sync is only supported if all streams use the same frame rate**. If your depth pipeline is “adapted” (e.g., different FPS), you can silently lose sync correctness even if video “plays.” citeturn11view1  
- USB 3.1 Gen1 supports more combinations; USB 2.0 supports only a subset—this matters for hub usage and for marginal cabling/power. citeturn11view1  
- The D435i IMU packets are **hardware-timestamped using the depth sensor hardware clock** to enable temporal synchronization with depth frames—useful if you ever fuse telemetry or need consistent alignment. citeturn1search3

### Depth over WebRTC: make the semantic contract explicit

WebRTC video codecs and browser decoders are not designed to preserve a 16-bit metric depth field natively. If you are “adapting depth for Janus streaming,” you must document a hard contract such as:

- **Depth encoding mode**:  
  - Option A: map Z16 to an 8-bit grayscale + publish the scale/offset as metadata (datachannel or sidecar)  
  - Option B: pseudo-color visualization (good for operators, not for computation)  
  - Option C: split higher bits/lower bits into two video planes (complex, fragile)
- **Range and saturation policy**: what happens for invalid depth (0), out-of-range, NaNs.  
- **Timestamp policy**: which clock is authoritative; what happens on discontinuities.

If you don’t formalize this contract, you’ll pass “streaming tests” while failing “mission tests” (depth is wrong but looks plausible).

### Hardware baseline that impacts stability on Raspberry Pi 5

A Raspberry Pi 5 has **2× USB 3.0 ports**, **Gigabit Ethernet**, and requires robust power (documented as **5V/5A via USB‑C**). It also includes a **real-time clock powered from an external battery**, which matters for certificate validity, log correlation, and autonomous recovery when no NTP is available. citeturn11view0

image_group{"layout":"carousel","aspect_ratio":"16:9","query":["Intel RealSense D435i camera close-up","Intel RealSense D435 depth camera front view","Raspberry Pi 5 board high resolution photo","Raspberry Pi 5 USB-C power supply 5V 5A"] ,"num_per_query":1}

### Media pipeline checklist (camera → encode → packetization)

- [ ] Lock down **exact stream profiles** per camera (resolution, FPS, format). For D435/D435i, explicitly record whether depth is Z16, whether color is YUY2, and whether you require RGB↔depth hardware sync (same FPS). citeturn11view1  
- [ ] Define and version a **Depth Semantic Contract** (range mapping + invalid policy + timestamp policy). Treat changes as breaking changes requiring full regression.  
- [ ] Ensure camera-side pipelines handle **device hot-unplug**: detect loss-of-device and exit cleanly (so the supervisor can restart).  
- [ ] Enforce bounded queues: every stage should have explicit max queue time/size; drop policy must be deterministic (drop-old vs drop-new).  
- [ ] MTU discipline: plan for **no IP fragmentation** on media paths; if fragmentation happens, you will see “random” loss under certain routers. (This interacts with DTLS MTU settings on the WebRTC side; see Janus section.) citeturn8view28turn8search19  
- [ ] CPU/thermal guardrails: define “de-rate profiles” (e.g., if CPU > X% or temp > Y°C, switch to lower FPS/bitrate).  
- [ ] For depth-as-video, test under packet loss: depth visualization should degrade gracefully rather than turning into misleading artifacts.

## WebRTC building blocks that determine whether your system survives NAT and outages

WebRTC reliability hinges on three standards layers:

- **ICE**: candidate gathering + connectivity checks across network topologies. citeturn0search2turn0search18turn0search22  
- **STUN**: discover public mapped addresses and keep NAT bindings alive. citeturn1search0turn1search4  
- **TURN**: relay media when direct paths fail (e.g., symmetric NAT, UDP blocked). citeturn1search1turn1search5  

Security-wise, modern WebRTC is designed to be encrypted end-to-end at the media layer using **DTLS for keying** and **SRTP for media**. citeturn6search35turn1search2turn3search3

### Reliability implications for your roaming gateway (RPi .10)

Because your main RPi connects to “any router with internet,” you cannot assume:

- inbound reachability,
- stable public IP,
- permissive UDP,
- non-symmetric NAT.

Therefore, **design for TURN-first operation** in the worst case. ICE is a framework for trying multiple paths; your operational policy can decide whether you prefer “direct whenever possible” or “always relay to maximize predictability.” ICE itself is defined as exchanging multiple candidates and performing connectivity checks to find a working path. citeturn0search2turn1search0turn1search1

A practical hard requirement in many hostile networks is TURN on TCP/TLS (often on port 443). A common reason is that HTTP proxies/firewalls allow CONNECT on standard HTTPS ports. citeturn3search17turn3search6

### WebRTC/TURN checklist (protocol correctness)

- [ ] Decide and document the **connectivity policy** for “roaming uplink” scenarios:
  - **Policy A (best-effort direct)**: prefer host/srflx, use TURN as last resort.
  - **Policy B (predictable)**: force TURN relay (potentially TURN/TLS on 443) so both sides only need outbound connectivity. citeturn1search1turn3search17  
- [ ] Validate STUN/TURN servers with the Trickle ICE test tool (candidate gathering + connectivity checks is exactly what this tool is for). citeturn6search3turn6search7  
- [ ] Build alerting around: ICE connection failures, time-to-first-frame, and TURN allocation failures. These are “link health” in practice. citeturn0search2turn1search1  
- [ ] Treat “UDP blocked” as a first-class case; ensure TURN/TCP and TURN/TLS are tested (not only configured). citeturn1search1turn3search17  
- [ ] Confirm media encryption expectations end-to-end: DTLS-SRTP is the keying method used in WebRTC, and SRTP provides confidentiality/integrity/replay protection for RTP payloads. citeturn1search2turn3search3turn6search35  

## Janus hardening: plugin choice, ICE/media settings, and observability

Janus is an open-source, general-purpose WebRTC server designed and developed by entity["company","Meetecho","webrtc gateway vendor"], tailored for Linux. citeturn5search19turn2search13  
It supports multiple control transports: HTTP REST (default), WebSockets, and message-queue options. citeturn5search2turn5search33

### Plugin architecture: Streaming vs VideoRoom (SFU)

You are currently using a single Janus instance as the aggregation point. Two Janus plugin approaches matter:

- **VideoRoom plugin**: Janus acts as an SFU (publish/subscribe). This is the canonical choice when you have “publishers” (your RPis) and multiple “viewers” (clients). citeturn0search1turn5search37  
- **Streaming plugin**: viewers watch media generated by another tool (e.g., GStreamer/ffmpeg) sent to Janus (RTP mountpoints) or played from files; it explicitly supports “live streaming of media generated by another tool.” citeturn8search2turn5search3turn0search0  

Your system description (“depth stream adapted for Janus streaming”) strongly suggests the **Streaming plugin with RTP input** for the depth feed. If so, treat the RTP ingest boundary as a *safety-critical interface*: payload types, codec profiles, timestamps, SSRC stability, and RTCP feedback handling must be deterministic.

### ICE/media settings in Janus that are reliability-critical

Janus exposes configuration knobs that directly influence reliability in multihomed / NAT / lossy networks, including:

- `ice_enforce_list`, `ice_ignore_list`, `keep_private_host`, `full_trickle`, ICE TCP settings, and NAT mapping options. citeturn8search15turn8search19turn8search28  
- `rtp_port_range`, `dtls_mtu`, `no_media_timer`, `slowlink_threshold`, and TWCC period. citeturn8search19turn8search28  

Port-range nuance that becomes a frequent “it works locally” trap: Janus community guidance indicates `rtp_port_range` in core config is for **ports Janus will use on the WebRTC side**, and plugins may have their own port needs. citeturn8search4

Also note the explicit warning in the sample configuration: you generally **should not configure TURN on the Janus side unless you truly intend it**, because TURN is usually configured on the client side—*but your roaming, inbound-restricted Janus deployment may be the exception*. citeturn5search12turn8search19

### Observability: Admin API and WebRTC getStats

Janus provides an **Admin/Monitor API** to query session/handle and media-level information, which is purpose-built for diagnosing and monitoring WebRTC behavior. citeturn5search1turn5search13  
On the client side, WebRTC provides `getStats()` via the W3C Statistics API, which gives you RTP/ICE metrics needed for closed-loop health evaluation. citeturn6search1turn6search33  

### Janus hardening checklist (super-senior baseline)

- [ ] Pin the **Janus build provenance** (official source/build pipeline). Janus explicitly warns against unofficial Windows .exe builds; for mission systems, treat supply-chain as a primary risk. citeturn5search19turn2search13  
- [ ] Choose plugin strategy per stream:
  - Color camera(s) as VideoRoom publishers if you want scalable SFU semantics. citeturn0search1turn5search37  
  - Depth feed via Streaming RTP mountpoint only if you can fully control RTP correctness and want a strictly one-way “broadcast”. citeturn8search2turn0search0  
- [ ] Enforce a single, documented **media port range** for Janus WebRTC-facing ports; validate that firewall rules match that range and do not overlap other services. citeturn8search19turn8search4  
- [ ] Set and validate `dtls_mtu` to a value consistent with your real path MTU to avoid fragmentation-based “random loss.” citeturn8search28turn8search19  
- [ ] Configure `ice_enforce_list` / `ice_ignore_list` for your multihomed gateway (Wi‑Fi uplink + Ethernet toward isolated router) to prevent Janus from advertising unusable candidates. citeturn8search15turn8search19  
- [ ] Turn on Admin API in production (secured), and collect:
  - ICE state transitions  
  - candidate pair selection changes  
  - packet loss/jitter/RTT where available citeturn5search1turn5search13  
- [ ] Define “no media” objective timeouts using Janus `no_media_timer` semantics (and enforce automatic re-offer/restart policy above it). citeturn8search19turn8search28  
- [ ] Instrument slowlink: `slowlink_threshold` and the alerting around it should be connected to your adaptive policy (bitrate/FPS downgrade, or operator warning). citeturn8search28turn8search19  

## Network edge reality: Cloudflare, DNS/proxy boundaries, and TURN security

### Cloudflare: what it can and cannot proxy in your design

Standard entity["company","Cloudflare","internet infrastructure company"] proxying is, by default, for **HTTP/HTTPS ports**; Cloudflare documents the specific supported port lists. If you proxy a record and expect arbitrary UDP to pass, it will fail unless you use specialized products. citeturn4search0turn4search4turn4search1

For **TCP/UDP proxying at Layer 4**, Cloudflare positions Spectrum as the product that terminates TCP/UDP sockets and proxies payloads as-is. citeturn4search1turn4search4turn4search19  
Cloudflare also offers a managed Realtime TURN service (and a free STUN endpoint per their FAQ), which can be part of a reliability strategy if you want to outsource TURN operations. citeturn3search1turn0search27

**Implication for your rover-grade checklist:** split your hostnames by function:

- Web UI / iframe page (HTTP(S), WebSockets): can be proxied normally. citeturn4search3turn4search0  
- TURN endpoints (UDP/TCP/TLS): must be reachable as TURN, not “accidentally HTTP-proxied.” (Either DNS-only, Spectrum, or managed TURN.) citeturn4search0turn4search1turn3search1  

### TURN is both your reliability lever and your security liability

TURN servers can be abused as high-power relays (SSRF-like behavior and DoS amplification). Security research and advisories emphasize isolating TURN and restricting peer access. citeturn0search23turn2search2turn3search2turn2search6

For coturn specifically, there are explicit controls for loopback/multicast restrictions and for denying peer IP ranges. citeturn3search2turn3search21turn3search25

Also, WebRTC/TURN deployments should use proper authentication (long-term credentials or TURN REST API style ephemeral credentials). Coturn documents TURN REST API support via `--use-auth-secret` and long-term credential mechanisms. citeturn4search2turn4search27turn4search17

### Networking checklist (gateway + Cloudflare + TURN)

- [ ] **DNS/proxy hygiene**:  
  - Keep UI hostnames proxied only on supported HTTP/HTTPS ports. citeturn4search0  
  - Keep TURN hostnames DNS-only (or Spectrum / managed TURN) so TURN packets are actually delivered. citeturn4search1turn3search1turn4search0  
- [ ] TURN reachability profiles:
  - UDP 3478 (best performance)  
  - TCP 3478 (fallback)  
  - TLS 443 or 5349 (hostile networks/proxies) citeturn3search17turn3search6  
- [ ] TURN relay port range explicit and monitored (avoid “random ephemeral ports with mystery firewall”). If using managed TURN, understand per-allocation limits (packet rate / Mbps). citeturn0search3turn3search1  
- [ ] TURN abuse prevention:
  - Require auth (no anonymous TURN in production). citeturn4search2turn4search27  
  - Disable loopback peers and multicast peers. citeturn3search2turn3search21turn2search2  
  - Deny private IP ranges as peer targets unless you have a controlled need. citeturn3search10turn3search25  
- [ ] Gateway routing invariants:
  - ip_forward + NAT rules are configuration-managed and audited (no “snowflake iptables”).  
  - The isolated router network should remain stable even if uplink router changes.  
- [ ] QoS: at minimum, classify and prioritize outbound TURN/media traffic over background tasks. (On Linux this is typically tc/qdisc/cake/fq_codel territory; treat it as a required integration test, not a tuning afterthought.)

## Autonomy and off-nominal handling for rover-grade streaming

If your requirement is “reliable and autonomous in abnormal situations,” you need a formal fault-management approach, not only “restart on crash.”

Space-grade guidance emphasizes:

- define and test fault management throughout the lifecycle, citeturn11view2  
- implement hierarchical FDIR (handle faults at the lowest level; escalate upward), and report actions unambiguously. citeturn11view4turn11view3  
- avoid unprotected states and ensure recovery after a single failure within a function where feasible. citeturn11view3  

### Watchdogs and supervised services on embedded Linux

For Linux systems, watchdogs are a standard last-resort recovery mechanism. systemd supports hardware watchdogs exposed via `/dev/watchdog` and service-level watchdog supervision. citeturn7search7  
For your deployment, implement both:

- **hardware watchdog**: resets node on kernel/systemd hang  
- **service watchdog**: restarts Janus/pipeline on missed heartbeats

This matches the “bounded recovery” principle: you must be able to recover from lock-ups without human intervention.

### Autonomy checklist (FDIR implementation, not just advice)

- [ ] Define explicit **system modes** (Nominal / Degraded / Local-only / Safe). Each mode has:
  - which streams are published,
  - bitrate/FPS caps,
  - which dependencies are required (TURN required? uplink required?),
  - what constitutes “exit criteria” back to nominal. citeturn11view3turn11view4  
- [ ] Implement hierarchical recovery ladders (per ECSS-style FDIR):
  1) retry/recreate handle  
  2) restart pipeline process  
  3) restart Janus  
  4) reboot node  
  5) degrade permanently (keep control alive) citeturn11view4turn11view3  
- [ ] Every recovery action must emit an **unambiguous event record** (what failed, when, what was done). This is explicitly expected in hierarchical FDIR reporting guidance. citeturn11view4  
- [ ] Hardware time discipline:
  - Use the Pi 5 RTC battery so time survives reboots without network. citeturn11view0  
  - If absolute time can’t be trusted, don’t couple “ability to stream” to “perfect time” (e.g., allow local-only mode with local trust anchors).  
- [ ] Watchdogs:
  - systemd watchdog enabled for Janus and for each camera pipeline service. citeturn7search7  
  - Define watchdog intervals based on worst-case CPU load (avoid false positives).  
- [ ] Evidence preservation:
  - Keep ring-buffer logs and (optionally) a short rolling local recording of key telemetry so postmortems are possible even after reboots. Space autonomy levels explicitly consider onboard storage during ground outages. citeturn11view4  

## Verification and acceptance checklists that drive “no room for errors”

This section is the practical “prove it” layer: it turns the architecture into a qualification plan. WebRTC gives you two critical observability sources:

- **Browser-side getStats() (W3C)** for RTP/ICE metrics. citeturn6search1turn6search33  
- **Janus Admin API** for session/handle/PeerConnection inspection. citeturn5search1turn5search13  

### End-to-end acceptance checklist (must pass before “field/rover”)

- [ ] **ICE/TURN qualification**  
  - Validate STUN/TURN with Trickle ICE for each target network class (home NAT, enterprise Wi‑Fi, LTE hotspot, UDP-blocked). citeturn6search3turn6search7  
  - Record: time-to-connected, selected candidate type (host/srflx/relay), and stability over 1+ hour sessions. citeturn0search2turn1search1  
- [ ] **Media correctness**  
  - Color: verify consistent FPS, no long stalls, and that encoder never enters unbounded queue growth.  
  - Depth: verify Depth Semantic Contract with golden test patterns (known distances) and packet loss injection.  
  - If you rely on RGB↔depth sync, verify same-FPS enforcement in all deployed profiles. citeturn11view1  
- [ ] **Resource envelope** (RPi 5 + camera load)  
  - Under worst-case load (both streams active, N viewers, TURN relay), system remains within CPU/thermal limits and never thrashes memory.  
  - Confirm stable USB 3 operation and power margin (5V/5A). citeturn11view0  
- [ ] **Janus robustness**  
  - Admin API enabled and secured; can enumerate sessions/handles under load. citeturn5search1turn5search13  
  - Validate configured WebRTC `rtp_port_range` and any plugin-specific port usage; firewall matches reality. citeturn8search4turn8search19  
  - Validate `dtls_mtu` and ensure no fragmentation-induced failure at typical MTUs. citeturn8search28turn8search19  
- [ ] **Off-nominal drills (must be automated tests, not manual demos)**  
  - Unplug/replug each camera while clients are watching: system restores stream within bounded T (define T).  
  - Kill pipeline process: supervisor restarts it; stream returns.  
  - Restart Janus: clients auto-reconnect (or client UI makes failure explicit and recovers on user action, per your requirements).  
  - Drop uplink Wi‑Fi (gateway loses internet): local isolated network continues working; remote sessions transition to safe state. citeturn11view4turn11view3  
- [ ] **TURN security and survivability**  
  - Confirm long-term or REST-style auth is required; no anonymous relay. citeturn4search2turn4search27  
  - Confirm `no-loopback-peers`/`no-multicast-peers` and deny rules to prevent SSRF-like relay abuse. citeturn3search2turn2search2turn3search25  
  - Load test: allocation rate, sustained throughput, packet rate; define headroom. If using managed TURN, incorporate documented per-allocation limits. citeturn0search3turn3search1  

### Runtime monitoring checklist (what must be continuously enforced)

- [ ] **SLOs (explicit)**:
  - connection success rate (ICE connected within X seconds),
  - time-to-first-frame,
  - rebuffer/stall frequency,
  - mean time to recovery (MTTR) after induced faults.  
- [ ] Client stats collection: periodically sample getStats() and record at least:
  - candidate-pair RTT, available bitrate,
  - packets lost, jitter,
  - frames decoded/dropped (where available). citeturn6search1turn6search33turn6search2  
- [ ] Server stats collection:
  - Janus Admin API snapshots on anomaly triggers (ICE failed, no media, slowlink). citeturn5search1turn5search13  
- [ ] Alerting policy:
  - Multi-stage: warn → degrade profile → restart handle/pipeline → reboot node. This maps to hierarchical FDIR expectations. citeturn11view4turn11view3  

### Client iframe embedding checklist (stability and correctness)

- [ ] Ensure the iframe origin and the parent app satisfy framing policy:
  - correct `Content-Security-Policy: frame-ancestors ...` and no conflicting frame denial headers (this is a frequent hidden failure mode).  
- [ ] WebSocket signaling path is stable under your proxying choice; Cloudflare supports proxied WebSockets for supported ports. citeturn4search3turn4search0  
- [ ] If autoplay is required, explicitly set iframe permissions and implement a robust “click-to-start” fallback.  
- [ ] Implement a deterministic “play start timeout” in the iframe player:
  - if ICE/DTLS doesn’t reach “playing” by T seconds, expose diagnostic state + auto-retry path.  
- [ ] Provide an operator-accessible “diagnostics view” that shows:
  - ICE candidate type selected (direct vs relay),
  - bitrate,
  - packet loss/jitter/RTT (from getStats),  
  - last recovery action taken.

## Entity references used in this report

entity["company","Meetecho","webrtc gateway vendor"] entity["company","Cloudflare","internet infrastructure company"] entity["company","Intel","semiconductor company"] entity["company","Raspberry Pi Ltd","single-board computer vendor"] entity["organization","NASA","us space agency"] entity["organization","Internet Engineering Task Force","internet standards body"] entity["organization","World Wide Web Consortium","web standards body"] entity["organization","European Cooperation for Space Standardization","space standards org"] entity["place","Mars","planet in solar system"]