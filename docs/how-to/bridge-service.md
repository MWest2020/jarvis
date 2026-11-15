---
status: current
last_reviewed: 2026-11-15
---
# Running the bridge service (weekend slice)

This covers the machine side only: the HTTP endpoint, local speech, and headless
Claude Code. For the phone side, see
[`android-http-shortcuts.md`](android-http-shortcuts.md).

## Prerequisites

- Tailscale running on this machine, and its IP known (`tailscale ip -4`).
- Claude Code **2.1.322 or newer**, installed and already authenticated
  (`claude --version` works). The bridge needs its `--restricted` flag (verified with
  2.1.322) and checks at start: if `claude --help` does not list `--restricted`, it
  exits with a message saying so.
- `espeak-ng` installed (a system package; Kokoro's text-to-speech front end
  depends on it) — e.g. `apt install espeak-ng` on Debian/Ubuntu.
- `uv` installed. Never `pip` directly in this repo (AGENTS.md).
- About 2.5 GB of free memory. The running bridge holds both speech models
  (torch, faster-whisper, Kokoro) at about 1.8 GB resident.

## Install

```sh
uv sync
```

This installs `faster-whisper`, `kokoro` and `soundfile` into a local `.venv`
(gitignored). On Linux, torch comes from PyTorch's CPU index (no CUDA libraries), and
the spaCy English model Kokoro's G2P needs is installed with the rest instead of at
first use. The first run of the service downloads the Whisper and Kokoro model weights
to the usual Hugging Face / Kokoro cache directories; that needs a working internet
connection once. (In a cluster the image carries the weights; see
[`run-in-cluster.md`](run-in-cluster.md).)

## Configure

The bridge reads everything from the environment. Nothing here goes in git —
put it in a local, gitignored `.env` and `export`/`source` it, or set it in
your process manager.

| Variable | Required | Meaning |
|---|---|---|
| `JARVIS_BIND_HOST` | yes | The Tailscale IP to listen on, e.g. `100.x.y.z` (IPv4 100.64.0.0/10 or IPv6 fd7a:115c:a1e0::/48). Loopback is allowed for local tests. Every other address is refused at start, wildcards and public addresses included (Network exposure, AGENTS.md). |
| `JARVIS_BEHIND_TAILSCALE_PROXY` | no | Only for a pod behind the Tailscale operator: `1` allows a private (RFC 1918 or CGNAT) bind address. See [`run-in-cluster.md`](run-in-cluster.md). Leave it unset on a workstation. |
| `JARVIS_TOKEN` | yes | The shared secret the phone sends as `Authorization: Bearer <token>`. Generate one with `openssl rand -hex 32`. Never commit it. |
| `JARVIS_PROJECTS` | yes | Comma-separated `name=/absolute/path` pairs, e.g. `demo=/home/mark/code/demo`. The phone selects a project by name in the request. Or `*`: every git checkout directly under `JARVIS_PROJECTS_ROOT` is a project, named after its directory (see "Every checkout under a root" below). |
| `JARVIS_PROJECTS_ROOT` | with `*` only | The absolute directory that holds the checkouts, e.g. `/work`. Must exist at start. Refused when `JARVIS_PROJECTS` is a list. |
| `JARVIS_PORT` | no (default `8787`) | TCP port on the Tailscale interface. |
| `JARVIS_STATE_DIR` | no (default `~/.jarvis`) | Where the per-project session id and the log file are stored. Outside the repo, never in git. |
| `JARVIS_WHISPER_MODEL` | no (default `base`) | faster-whisper model size; CPU-friendly sizes are `tiny`, `base`, `small`. |
| `JARVIS_KOKORO_VOICE` | no (default `af_heart`) | A Kokoro voice name. Pick one by ear once the service runs (see below). |
| `JARVIS_CLAUDE_TIMEOUT_S` | no (default `120`) | Seconds one Claude Code turn may take; on timeout the whole process group is killed and the phone gets a 502. |

The tools headless Claude Code can use are **fixed** for this weekend slice:
`Read`, `Glob`, `Grep` only (read-only). `--allowedTools` alone would not do
that (it only pre-approves, and your own settings, such as an `auto` permission
mode or allowed Bash rules, and MCP servers would still apply), so each turn runs
with `--tools Read,Glob,Grep`, `--permission-mode dontAsk`, `--strict-mcp-config`
(no MCP servers), `--restricted` (file tools confined to the project directory, and your
user, project and local settings files ignored, so a repo's own `.claude/settings.json`
hooks never run) and `--disallowedTools Bash,Edit,Write,...,Read(//proc/**)` as a second
layer. The `/proc` rule matters: Grep follows Read deny rules, and without it Grep on
`/proc/self/environ` (or a committed symlink to it) returned the process environment.
There is no approval flow yet for anything beyond that list (D6); that is a
later task. The transcript goes to Claude Code over stdin, and the `JARVIS_*`
variables (the token) are removed from its environment. That alone does not hide them
(the bridge's own `/proc/<pid>/environ` still has them); the two flags above do.

## Run

```sh
uv run jarvis-bridge
```

The service logs to `$JARVIS_STATE_DIR/bridge.log` (full Claude Code replies
and transcripts are logged there, even though only a short summary is ever
spoken — D5) and also to stdout. Transcripts and full replies go to the file only;
stdout gets the request lines, the summaries and short error lines; error details
(Claude Code's stderr, an unexpected reply) and tracebacks go to the file. The file rotates at 10 MB
and keeps three old files. Both redact the token, the Claude credentials and anything
shaped like `sk-ant-…`, tracebacks included.

## Every checkout under a root

With `JARVIS_PROJECTS=*` and `JARVIS_PROJECTS_ROOT=/work`, each directory directly under
`/work` that is a git checkout (it has a `.git`) is a project with the directory's name.
Skipped: plain directories, files, symlinks, and names that start with a dot or contain
anything but letters, digits, `.`, `_` and `-`. So a project name never leads out of the
root. The bridge looks again at most every 10 seconds, so a checkout that appears or goes
away shows up in the list and in turns without a restart. With no checkouts at start it
still starts, logs a warning and lists nothing.

## What the endpoints do

Two routes, both behind the same token check (constant time) and the same rules for a
refused request (below).

`GET /projects` with `Authorization: Bearer <token>`: the project names, sorted, as
`application/json`:

```json
{"projects": ["bazibo", "jarvis", "wordsworth"]}
```

Names only, never paths. 401 without a valid token, 404 on any other path. The phone app
uses it when its projects field is empty or `*` ([`android-app.md`](android-app.md)).

`POST /turn?project=<name>` with `Authorization: Bearer <token>` and the
recorded audio as the body. Replies:

| Status | When |
|---|---|
| 200 | `audio/wav` with the spoken summary. Headers: `X-Jarvis-Summary` (URL-encoded text of what is spoken) and `X-Jarvis-Session-Id`. Silence gets a spoken "didn't catch that" and no agent turn. |
| 400 | No project, an unknown project, an empty body, or a `Content-Length` that is not a number (digits only: `+5`, `5_0` or padded values are refused) |
| 401 | Missing or wrong token |
| 409 | That project is still busy with another turn (one turn per session at a time) |
| 413 | Audio larger than 10 MB |
| 502 | The Claude Code turn failed or timed out (details in the log) |
| 503 | Eight connections are already open (any route, before the token is looked at). `Retry-After: 5`. |

A connection that stalls for 30 seconds is closed.

The bridge serves at most eight connections at once (`MAX_CONNECTIONS` in
`bridge/server.py`). A connection counts from the moment it is accepted until it closes,
whatever it is doing: sending headers, uploading, waiting for the agent, or sitting idle on
keep-alive. The ninth gets no thread and no place in a queue: the bridge writes a 503 at
once, without reading the request, and closes. A client that had already started a large
upload can see a broken connection instead of the 503. The phone app shows "The bridge is
busy with other connections" for a 503. What the cap does not solve: the 30 seconds apply
to each read, not to the whole request, so eight clients on the tailnet that each send a
header byte every few seconds hold all eight slots for as long as they like, token or not.
The cap turns unlimited threads into a bridge that says it is busy; it does not keep the
bridge reachable under such an attack. The tailnet ACL and the NetworkPolicy decide who can
connect at all ([`run-in-cluster.md`](run-in-cluster.md)).

A refused request (404, 401, 400) still gets its status line when the phone is already
uploading a long question: if the announced `Content-Length` is a valid number of at most
10 MB, the bridge first reads the body and throws it away (in 64 KB chunks, never kept),
then answers. Before this, it answered and closed with the upload still arriving, and from
about 700 KB (some 22 s of speech) the phone saw a broken connection instead of "token
refused" (jarvis#12). The trade-off: an unauthenticated client can make the bridge read and
discard up to 10 MB per request. It cannot hold a thread for long with that: the drain
stops after 30 seconds in total, however slowly the bytes come, and then the connection is
closed. A body over 10 MB (413), or a missing or invalid `Content-Length`, is not read at
all: the reply goes out at once with `Connection: close`. The log and the session file
are created owner-only (`0600`, in a `0700` directory, rotated logs too): the log
holds full replies, which can quote file contents.

Only the summary is spoken, and only when it is short (300 characters at most)
and contains nothing that looks like code or a path (backticks, slashes, file
names, shell operators) or like a credential (`sk-ant-`, or any run of 32 or more
letters, digits, `_` or `-`). Otherwise the phone hears "Done. Check the log for
details."

## Picking a Kokoro voice

Kokoro ships several voices. Try a few by setting `JARVIS_KOKORO_VOICE` and
listening to a response, or render one directly:

```sh
uv run python -c "
from bridge.tts import synthesize
open('/tmp/voice-test.wav', 'wb').write(synthesize('This is a test.', voice='af_heart'))
"
```

Play `/tmp/voice-test.wav` and try other voice names (see the Kokoro project's
voice list) until one sounds right for spoken replies in a car.

## Manual end-to-end check on the tailnet (task 4.1)

This is the real test the weekend slice is for, and it needs real hardware
this change was not written from (a phone on the tailnet, a microphone, a
running Claude Code session) — it is not something to automate in CI. From
another device on the same tailnet:

```sh
curl -X POST \
  "http://<tailscale-ip>:8787/turn?project=<name>" \
  -H "Authorization: Bearer <token>" \
  --data-binary @test-question.wav \
  -o reply.wav
```

Plain HTTP is a deliberate departure from D2's "HTTPS": Tailscale's WireGuard
tunnel already encrypts traffic between tailnet peers, so the weekend slice has
no separate TLS layer.
The shared token is the application-level check on top of that.

Play `reply.wav`. It should be a short spoken summary of Claude Code's answer
about the given project. Check `$JARVIS_STATE_DIR/bridge.log` on the machine
for the full reply and the recognized transcript. Ask a follow-up the same
way and confirm (from the log) that the same `session_id` is resumed.

## Test on 2026-11-14

A host session ran the bridge on a 2-CPU, 4 GB machine on the tailnet and sent two
spoken questions (rendered by Kokoro) with curl to its Tailscale address:

- Turn 1, "What is this project about, in one sentence?": transcribed correctly, Claude
  Code answered in about 4 s, and the phone-side WAV said "Jarvis lets you talk from your
  Android phone to a coding agent like Claude Code on your own computer, privately and
  with speech handled locally."
- Turn 2, the follow-up "And which speech models does it use?": resumed the same session
  id and answered about faster-whisper and Kokoro.
- No token and a wrong token: 401.

That machine was too small. With several other processes on it, memory and swap ran
out, so a Kokoro render took minutes and one Claude turn hit the 120 s timeout. The
bridge loads both models at start now, and the free-memory prerequisite above comes
from that run. Run it on a machine with the memory to spare.
