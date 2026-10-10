# Visitor Management System (v2)

A face-recognition front desk: registered people are let in automatically, strangers are photographed and their
host approves or denies from a phone or the dashboard.

```
Kiosk (/)            tablet / PC at the door - live camera, auto check-in, new-visitor form
Admin (/admin)       live approvals, visit log + CSV, enrol faces, block list, hosts, settings
Approval link        /approve/<token>  - emailed/webhooked to the host, works on a phone, no login
```

## Run it

```bash
./run.sh            # Linux / macOS      (Windows: run.bat)   -> http://localhost:8081
```
First start downloads the face models (~280 MB, once). If `VMS_ADMIN_PASSWORD` is not set, a random admin
password is printed in the console. Docker: `docker build -t vms . && docker run -p 8081:8081 -v vms:/data vms`.

**Camera note:** browsers only allow camera access on `http://localhost` or **https**. For a kiosk on another
device put the server behind HTTPS (Caddy / nginx / `tailscale serve`), or run the kiosk browser on the server itself.

## What changed vs. the original

| Original | Now |
|---|---|
| Approval server only; the face-recognition desktop code was not in the repo | Complete system: recognition, kiosk, admin, approvals |
| In-memory state, lost on restart; one pending request at a time | SQLite database, any number of concurrent requests, full history |
| Unauthenticated approve/deny page | Admin login; hosts get unguessable single-use links |
| Plain grey page | Responsive kiosk + admin UI, live updates, sound alert |

## How the recognition works (and why it's more accurate)

1. **RetinaFace/SCRFD** detector + 5-point landmark alignment.
2. **ArcFace ResNet-50 (WebFace600K)** 512-d embeddings (the usual Haar/LBPH approach is far less reliable).
3. **Quality gate before any decision** - face size, sharpness, brightness, head pose, framing, and "more than one
   face" are all checked; the kiosk tells the visitor what to fix ("Move closer", "Look straight...").
4. **Multi-frame verification** - 3 frames are captured and *all* must agree on the same person (stops
   mid-capture swaps and blurry one-off frames).
5. **Gallery per person** (3-12 samples at different angles/light). Score = mean of the best two similarities.
6. **Safe decisions** - must pass a threshold *and* beat the runner-up by a margin. Close calls are never auto-approved;
   they go to a human. Blocked people trigger an alert instead of entry.
7. Enrolment rejects bad samples, drops outliers (different person in frame) and warns on duplicates.

Tune it in **Settings** (threshold default 0.45; raise for stricter doors). Add more samples to anyone who gets
asked to wait too often.

## Configuration (env vars)

`VMS_ADMIN_USER/PASSWORD`, `VMS_PORT`, `VMS_BASE_URL`, `VMS_DATA_DIR`, `VMS_MODEL_PACK` (`buffalo_l` accurate,
`buffalo_s` lighter), `VMS_SMTP_*`, `VMS_SECURITY_EMAIL`, `VMS_WEBHOOK_URL` (Slack/Discord/Teams). See `.env.example`.

## Limitations - please read

* **No liveness / anti-spoofing.** A high-quality photo or a screen held to the camera can fool any pure RGB-camera
  system. For doors that matter, add a dedicated anti-spoof model or an IR/depth camera, and keep a human approval step.
* Accuracy depends on camera placement and light - face the camera away from windows, mount at face height.
* CPU-only by default: ~0.2 s detection / ~0.4 s recognition per frame on a laptop. Fine for a front desk.
* Face data is biometric data. Get consent, publish a notice, and set a retention period (Settings; default 90 days)
  - laws such as GDPR, DPDP (India), BIPA apply. Use HTTPS and a strong admin password in production.
* Not a rate-limited internet-facing service: keep it on your LAN/VPN.
