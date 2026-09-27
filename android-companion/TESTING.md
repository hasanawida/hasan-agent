# Android companion verification

**Execution status: this checklist has not been run on a physical phone or emulator in this task. No phone is attached.** APK compilation and lint are separate checks; they do not establish working gestures, projection lifecycle, vendor background behavior, or lock-screen protection on a device. Record each result below with the tested APK commit and Android API version.

## Test environment

Use a disposable emulator or dedicated test phone with no personal accounts or messages. Run the test Hassan server against a separate data directory and bind it to an HTTPS origin reachable through your private Tailscale network. Pair that test server with a fresh six-digit phone enrollment code. Do not copy production phone tokens, a production access key, or signing keys into test logs.

Recommended coverage:

| Target | Purpose |
| --- | --- |
| Android 8 / API 26 emulator | Minimum supported version, projection and legacy rotation fallback |
| Android 11 / API 30 emulator | Accessible Enter and modern display metrics |
| Android 13 / API 33 emulator | Runtime notification permission |
| Android 14 / API 34 emulator | One-consent-per-projection enforcement and resize callback |
| Android 15 / API 35 emulator | Target SDK behavior, background restrictions, layout/insets |
| Owner's Samsung phone, when available | Real touch, vendor accessibility lifecycle and battery settings |

Install the APK through Android Studio Device Manager or `adb install -r <apk>`. An update must use the same signing certificate as the already-installed build. Enable the Hassan accessibility service yourself in Android Settings. Do not use ADB to pre-grant accessibility, screen-capture consent, notification permission, or lock-screen access: those user flows are part of this test.

Keep the emulator and the PC dashboard visible side by side. On a recent emulator image, install Tailscale and connect it normally if the private HTTPS origin is not otherwise reachable. Do not weaken certificate validation or enable cleartext networking to make a test pass.

Prepare a disposable UI with:

- a normal editable text field containing `original`;
- a password field containing a made-up test value;
- a scrollable list and a button that increments a visible counter without submitting anything;
- enough spacing to distinguish a single tap from repeated taps.

A local test web page exposed on the same trusted test HTTPS server is sufficient; no real login form is needed. Record only synthetic values and command IDs. Keep tokens, pairing codes, screenshots of personal data, and signing credentials out of reports.

## Consent and pairing

- [ ] Fresh install: no projection, no poll-based control, no gestures, and no persistent control opt-in before enrollment and deliberate user action.
- [ ] Reject HTTP, a URL with user-info/password, a URL with a path/query/fragment, an untrusted TLS certificate, and an HTTPS redirect to another host. The credential must never follow a redirect.
- [ ] A valid phone enrollment code pairs once; reusing, guessing or expiring it fails. An invalid attempt does not claim success.
- [ ] Rotate the emulator while enrollment is pending. Only one enrollment attempt may be active; its eventual result must not silently create a second attempt from the recreated Activity.
- [ ] Enabling accessibility alone does not grant remote input. With the persistent-control switch off and no explicit display session, the companion must not run a control loop.
- [ ] On API 33+, deny notification permission when enabling persistent control. Control remains off. Grant permission on a later attempt: the intended control action resumes, including after rotating during the permission dialog; it must not unexpectedly start projection instead.
- [ ] A failed enrollment preference write must not show “paired successfully”. Fault injection for this case belongs only in a disposable test build.

## Persistent control and independent display

| Scenario | Expected result | Result / evidence |
| --- | --- | --- |
| Control switch on; projection never started | Poll advertises control when unlocked. Inspect/tap/swipe/text are available; no screen frame exists. Visible control notification has Stop All. | Not run |
| Start display with control switch off | Fresh Android capture prompt; screen appears, input remains rejected. | Not run |
| Turn control on during display-only session | Input becomes available, control notification appears, existing projection keeps running. | Not run |
| Turn control off while keeping display | Frame viewing continues. No new input/inspection executes; subsequent poll advertises control disabled. | Not run |
| Stop Display in the phone UI or projection notification | Frames stop and projection releases; opted-in persistent control remains. | Not run |
| Send `stop_screen` from the PC | Frame upload stops before success acknowledgement. Projection is released after result delivery attempt. The persistent-control setting is unchanged. | Not run |
| Stop All from app or control notification | Both display and control stop. The permission switch is off after reopening. | Not run |
| Disable Android accessibility service | No new input or inspection; existing display may continue as explicitly consented viewing, but control cannot resume until service is enabled. | Not run |
| Reopen Activity with control already opted in | Existing opt-in is shown correctly, no automatic screen consent dialog and no projection-token reuse. | Not run |
| Force-stop app, then reopen it | No promise of background continuity; no automatic projection. Check accessibility status and explicitly recover connection if the OS stopped it. | Not run |

For any stop action issued during a gesture, an already-dispatched Android gesture may finish its bounded duration (at most two seconds). No subsequent command may begin after permission revocation. Do not label that in-progress gesture as an unsent action.

## Screen restart, orientation and cleanup

- [ ] Cancel the Android projection consent dialog. No foreground projection service or upload loop remains, and the Start Display button becomes usable again.
- [ ] Tap Start Display repeatedly or rotate while its consent dialog is open. Only one capture request is pending. The recreated Activity tracks the pending request.
- [ ] Stop display, then immediately try to restart while a PC `stop_screen` acknowledgement is delayed. Start Display remains unavailable until the previous projection service has actually finished stopping; a newly granted capture token must not be silently ignored by the old service.
- [ ] Every new projection session asks Android for fresh consent. Reopening the Activity, a network reconnect, or a saved control opt-in never reuses a previous capture token.
- [ ] API 34+: rotate portrait → landscape → portrait. Verify the surface and virtual display resize together, the image retains its aspect ratio, and taps hit the visual target at all four corners. No second `createVirtualDisplay` occurs for the same projection token.
- [ ] API 26–33: rotation stops display with the restart message. Persistent input permission remains governed by the switch. A new explicit projection session aligns taps correctly.
- [ ] If an OEM lets the user override full-display sharing with a single app region, the companion must refuse mismatched dimensions rather than deliver offset touches.
- [ ] Stop projection from Android's system screen-sharing chip. No stale reader, frame upload, foreground projection notification or surface remains.
- [ ] Stop/start display at least ten times with a steady test screen. Watch the app's memory in Android Studio Profiler; it should not grow by one permanent ImageReader/bitmap set per cycle.
- [ ] Check portrait/landscape phones whose ImageReader last row omits padding. Capture must not fail with a buffer-underflow error.

## Lock-screen and content boundaries

- [ ] During a session, press the emulator/phone power button. Projection stops; `inspect`, `tap`, `swipe`, `text` and navigation keys do not run while the screen is off.
- [ ] Turn the screen on without unlocking a PIN-protected test device. All inspection/input remains blocked. The PC does not receive lock-screen text, notification contents, PIN entry or any unlock attempt.
- [ ] Unlock manually. Previously opted-in persistent control may return; projection stays off until fresh user consent.
- [ ] Repeat screen-off testing with no secure PIN configured. `PowerManager.isInteractive()` still prevents screen-off inspection/input.
- [ ] Inspect the synthetic form's password field. Its text, description and all descendants are excluded, with only a redacted placeholder/bounds permitted.
- [ ] Focus the password field and attempt text/Enter. Both are rejected, including when password status belongs to an ancestor node.
- [ ] Inspect a large or deeply nested accessibility tree. Work is bounded by 100 reported nodes, 500 visited nodes, depth 35, and 48 KiB result content; truncation is reported.
- [ ] Open a synthetic secure window using Android `FLAG_SECURE`, if available. A blank/protected frame is accepted behavior; there must be no attempt to bypass it.

## Command transport and race regression checks

Use the real test server for ordinary actions. A fault-injection HTTPS server with the same documented endpoints is needed for deliberately duplicated, expired, malformed or delayed replies. It must be a trusted disposable origin; do not change TLS checks in the production app. Its logs should contain only command IDs, action names, timestamps and result status.

| Injection / action | Expected result | Result / evidence |
| --- | --- | --- |
| Normal single tap on counter | Exactly one increment and one success result | Not run |
| Arabic text `مرحبا من الاختبار` | Focused normal field is replaced exactly; text is not appended to old content | Not run |
| Unsupported action, NaN/out-of-range coordinates, unsupported key | No input; explicit failure | Not run |
| Command already expired or deadline over 120 seconds ahead | Rejected without execution | Not run |
| Command expires while waiting for Android's main thread | Main-thread deadline check rejects it | Not run |
| Same command ID delivered twice, including after a metadata refresh | At most one gesture; second delivery is rejected by bounded in-process deduplication | Not run |
| Metadata changes before a delivered command reaches the main thread | Stale command does not execute; a failure result may be reported | Not run |
| First valid frame arrives while an executed command is uploading its result | Metadata refresh interrupts only `/poll`, not `/result`; original result is allowed to complete | Not run |
| Rotate or stop display while `/result` upload is delayed and control remains allowed | Result transport is not closed merely to update projection metadata | Not run |
| Switch control off before a queued command executes | No new input, even if the UI switches control on again quickly | Not run |
| Result endpoint returns 403 for ownership loss | Pairing token is retained; no command is replayed | Not run |
| Frame endpoint temporarily returns 403 before screen metadata arrives | Capture and pairing survive; later frames retry | Not run |
| Any authenticated phone endpoint returns 401 after revocation | Local token and permanent-control opt-in are cleared, display stops | Not run |
| Drop connection after the counter increment but before result response | Counter stays incremented once; no automatic re-execution. PC may show uncertain/failed result | Not run |
| Disable and restore Tailscale | Bounded backoff; no replay of prior commands, fresh poll when connection returns | Not run |

The process-wide duplicate-ID cache holds the latest 512 IDs and is not an exactly-once guarantee across process death. The PC must continue using one-shot command delivery with unique IDs and short expiry. No retry should be interpreted as safe solely because a network acknowledgement was lost.

## Release evidence template

Record:

- APK source commit, APK SHA-256 and signing certificate fingerprint (public fingerprint only).
- Emulator/device model, Android API/build, screen resolution and navigation mode.
- Test server version and whether the run used Tailscale or a private test HTTPS route.
- Each checked case's result, sanitized command ID and expected/observed input count.
- Remaining failures and device-specific limits; do not mark this checklist passed from compilation or lint alone.

Build checks remain `gradle :app:assembleDebug :app:lintDebug`. These tests must be repeated after lifecycle changes; a previous successful APK build does not validate a later Java revision.
