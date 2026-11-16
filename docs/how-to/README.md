---
status: current
last_reviewed: 2026-11-15
---
# How-to guides

| Guide | Covers |
|---|---|
| [`bridge-service.md`](bridge-service.md) | Installing and running the bridge service, configuring it, the two agent modes (`readonly`, `full`) with the read-back and the spoken yes, picking a Kokoro voice, the manual end-to-end check on the tailnet. |
| [`android-http-shortcuts.md`](android-http-shortcuts.md) | Setting up the tap-to-talk HTTP Shortcuts button on the phone for the weekend test. |
| [`run-in-cluster.md`](run-in-cluster.md) | The bridge as a pod behind the Tailscale operator: the image, the bind opt-in, the tailnet-only Service, the NetworkPolicy, project sync, state. |
| [`android-app.md`](android-app.md) | The native Android app: sideloading the APK from CI, first-run setup, what the errors mean, why plain HTTP is allowed only on the tailnet, how it differs from HTTP Shortcuts, the signing key. |
