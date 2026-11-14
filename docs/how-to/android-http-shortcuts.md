---
status: current
last_reviewed: 2026-11-15
---
# Phone setup: tap-to-talk with HTTP Shortcuts (weekend slice, task 3.1)

This is the weekend-test client only (D7, open question: decided). It uses the
"HTTP Shortcuts" Android app (Waboodoo) already on the phone, on the same
Tailscale network as the bridge machine. The native Android app is described
in [`android-app.md`](android-app.md); this guide stays valid beside it.

Exact menu names can differ a little between HTTP Shortcuts versions; the
steps below describe the shapes to look for rather than a fixed version.

## 1. Create the shortcut

1. Open HTTP Shortcuts → add a new shortcut.
2. Method: `POST`.
3. URL: `http://<tailscale-ip>:8787/turn?project=<name>`, using the machine's
   Tailscale IP and one of the project names from `JARVIS_PROJECTS` on the
   bridge.
4. Request headers: add `Authorization: Bearer <token>`, matching
   `JARVIS_TOKEN` on the bridge. This is a secret — do not export or share the
   shortcut with the header filled in, and do not paste it anywhere that
   syncs to a public place.

## 2. Record and attach the audio body

1. Set the request body type to "File" (binary body), not form data.
2. Add a "Record audio" action (or equivalent microphone-recording action, if
   your HTTP Shortcuts version has one) to capture a short question as a
   file; otherwise record with any voice-recorder app and select that file as
   the body before sending.
3. Point the body's file source at the recording.

This gives the tap-to-talk shape: one tap starts the shortcut, which records,
uploads, and waits for the reply.

## 3. Play the returned audio

The bridge responds with `200` and an `audio/wav` body on success, or a
non-`200` status with a plain-text error (nothing to play in that case).

1. In the shortcut's response handling, choose "store response as a file"
   (give it a `.wav` extension).
2. Add a follow-up action that opens that file with the default app, or use
   a built-in "play sound"/media-player action if your HTTP Shortcuts version
   has one, so the reply is audible immediately without extra taps.

## 4. Put it on the home screen

Add the shortcut as a home-screen icon or widget so it is a single tap while
parked or (per `design.md`'s risk notes) only interacted with hands-free.

## Try it

Tap the icon, ask a short question about the selected project, and wait for
the spoken reply. If nothing plays, check:

- The phone is actually on the tailnet (not on mobile data with Tailscale
  off).
- The token in the header matches `JARVIS_TOKEN` on the bridge.
- `$JARVIS_STATE_DIR/bridge.log` on the machine for the actual error.
