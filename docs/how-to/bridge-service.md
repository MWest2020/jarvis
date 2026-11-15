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
| `JARVIS_AGENT_MODE` | no (default `readonly`) | `readonly` or `full`. See "Agent modes" below. Unset, the bridge behaves exactly as before there were modes and reads none of the `full` settings. |

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

## Agent modes

Two modes, chosen with `JARVIS_AGENT_MODE` (change
[`jarvis-full-agent`](../../openspec/changes/jarvis-full-agent/design.md), D30 to D40).

- **`readonly`** (the default): everything above. The turn runs next to the bridge with
  `Read`, `Glob`, `Grep`. Nothing connects anywhere but the Anthropic API.
- **`full`**: the turn runs on the *agent host*, the machine where your own coding session
  lives, as that session's user and under its rules. The bridge opens one `ssh` connection
  per turn to a wrapper there. Speech stays with the bridge. The agent host side (the
  wrapper, the confirmation gate, the `authorized_keys` line) is phase 1 of the change and
  has its own how-to, `agent-host.md`, which arrives with that phase; until it is installed
  and its host checks have passed, do not set `full` anywhere.

Settings for `full`, all refused at start when they are not what they should be:

| Variable | Required | Meaning |
|---|---|---|
| `JARVIS_AGENT_HOST` | yes | The agent host: a DNS name or an address on the tailnet (in a cluster: the name of the tailnet egress Service). |
| `JARVIS_AGENT_USER` | yes | The Unix user of the coding session there. |
| `JARVIS_AGENT_PORT` | no (default `22`) | sshd's port. |
| `JARVIS_AGENT_KEY_FILE` | yes | Absolute path of the bridge's private key: a dedicated key whose `authorized_keys` line on the agent host forces the wrapper. A file, never a variable. |
| `JARVIS_AGENT_KNOWN_HOSTS_FILE` | yes | Absolute path of a `known_hosts` file with the agent host's public host key, for exactly the name (and port, as `[name]:port`, when it is not 22) in `JARVIS_AGENT_HOST`. An unknown or changed host key fails the turn; there is no trust on first use. |
| `JARVIS_AGENT_TIMEOUT_S` | no (default `660`) | Seconds the bridge waits for the agent host. Keep it above the agent host's own turn timeout (600 s by default), so its answer arrives first. |
| `JARVIS_CONFIRM_YES` | no (default `yes,ja`) | The closed list of affirmations, comma-separated, written as they are compared: lower case, no punctuation. |
| `JARVIS_CONFIRM_NO` | no (default `no,nee`) | The closed list of refusals. A word cannot be on both lists. |
| `JARVIS_CONFIRM_MIN_SPEECH_S` | no (default `0.3`) | The least detected speech, in seconds, a recording must hold before a yes counts. |

The bridge does not start in `full` mode when the key file or the known-hosts file is
missing, is not a regular file, gives any permission to group or others (so `0400` or
`0600`, owned by the bridge's user), has whitespace in its path, or lies inside a project
directory or under `JARVIS_PROJECTS_ROOT` (the read-only agent can read everything there).
It also needs the OpenSSH client on `PATH`.

**Which projects.** The bridge's own project list still decides what can be asked: `POST
/turn` refuses a project it does not know before the agent host is contacted, and `GET
/projects` lists the bridge's projects. In `full` mode a project must be on the bridge's
list *and* on the agent host's. One that exists only on the agent host cannot be reached by
voice; one that exists only at the bridge is answered read-only, and says so. A name the
agent host would refuse by its shape (it accepts `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`) is
never sent and is answered read-only too.

**The connection.** Exactly this, with no remote command (a test fixes it):

```
ssh -T -F /dev/null -o BatchMode=yes -o IdentitiesOnly=yes -o IdentityAgent=none
    -o StrictHostKeyChecking=yes -o UserKnownHostsFile=<known-hosts file>
    -o GlobalKnownHostsFile=/dev/null -o ConnectTimeout=10 -o ServerAliveInterval=15
    -i <key file> -p <port> <user>@<host>
```

`ssh` gets `PATH` and `HOME` and no other variable: not the token, not the Claude
credentials. The request goes over stdin.

**Read-only for one turn.** A `full` turn is answered by the `readonly` adapter instead,
and its first sentence says why, in exactly four cases, all before a turn can have started
on the agent host: the connection could not be made (ssh's own message for no route, no
listener, no name, no banner or a refused key, with exit status 255 and nothing on stdout),
the agent host does not have the project (`no_checkout`), its kill switch is set
(`disabled`), or the project's settings are unsafe there (`unsafe_settings`). In every
other case, a lost connection included, the turn is **never run again**, in either mode:
the phone hears what went wrong and that it was not run again. A host key that is not the
pinned one fails the turn and is not answered read-only.

**Confirmation.** When the agent on the agent host reaches a gated action (a merge, a
deploy, a delete: design D36), the reply is a read-back and nothing else, with the header
`X-Jarvis-Needs-Confirmation: 1`. The bridge keeps its id and hash for that project, in
memory only, for 60 seconds (less when the agent host says it keeps the call for less).
The next utterance for that project decides, from the transcript of the driver's own audio
and from nothing else:

| The transcript, lowered and without punctuation, is | What happens |
|---|---|
| exactly a word on the affirmation list, and the recording holds at least the minimum of detected speech | The confirmation is sent with answer `yes`; the reply is the agent's. |
| exactly a word on the affirmation list, with less detected speech (Whisper writes "yes" from noise) | Cancelled on both sides (answer `no`), no agent turn: "I did not hear a clear yes. Cancelled." |
| exactly a word on the refusal list | Answer `no` is sent, the agent host clears the call and starts no agent turn: "Cancelled." |
| anything else ("yes but wait", another question) | An ordinary request with one line in front saying the action was not confirmed; it clears the call on the agent host like every request. |

A yes after the 60 seconds, after a restart of the bridge, for another project, or with
nothing waiting is an ordinary utterance and releases nothing. A yes that passes the
bridge's 60 seconds but is too late on the agent host (`expired`) is answered "That took
too long. Ask again." A "yes" in the agent's reply, a file or an issue is never looked at.
The read-back is spoken as the agent host built it: it does not go through the filter for
free text below and is never replaced by another sentence. One that is empty, has a line
break or a control character, or is longer than 500 characters cannot be spoken: the bridge
then cancels the call on the agent host (answer `no`), keeps nothing and says "That one
needs the desk." Detected speech is measured with the voice activity detector inside
faster-whisper (a file in the package, nothing is downloaded); if it cannot be measured,
the yes does not count.

**The contract with the agent host** (version 1, design D33, as settled in
[#27](https://github.com/MWest2020/jarvis/pull/27)). The request is one JSON object of at
most 64 KB with exactly `v`, `project`, `prompt`, `confirm`; `confirm` is `null` or
`{"id","sha256","answer"}`, and with a `confirm` the prompt is the empty string. The reply
is one JSON object of at most 64 KB with exactly `v`, `status`, `error`, `turn`, `spoken`,
`pending`, `readback`, `confirm`, `notice`. The bridge refuses a reply with a missing,
extra or duplicate field or a value of the wrong type or shape, and then reports the turn
as failed without running it again. `spoken` is the agent's marker line and goes through
the same summary filter as a read-only reply; the full reply never leaves the agent host.

| From the agent host | The phone hears |
|---|---|
| `busy` | "The agent host is busy. Try again in a moment." |
| `low_memory` | "The agent host is busy: it is short of memory. Try again later." |
| `timeout` | "The agent took too long on the agent host and was stopped. I did not run it again." |
| `agent_failed` | "The agent turn failed on the agent host. I did not run it again." |
| `expired` | "That took too long. Ask again." |
| `bad_request` | "The agent host refused the request." |
| notice `needs_desk` | "That one needs the desk." |
| notice `run_alone` | "That command has to be run on its own, so it was refused." |
| notice `settings_changed` | "The project settings changed. That needs the desk." |
| notice `new_session` | "I started a new session for this project." |

A notice's sentence comes first, followed by the agent's summary when there is one that
may be spoken. All of these are ordinary `200` replies with audio: the driver cannot read a
status code. `X-Jarvis-Session-Id` carries the agent host's turn id in `full` mode, and
stdout logs `agent=full turn=<id> outcome=<code>` per turn, never the request or the key
path. A `full` turn uses the agent host's own Claude Code session for the project;
`readonly` turns (the fallback included) use the bridge's session file, so the two never
resume each other's conversation.

What `full` mode trusts is said plainly in the design, D40: whoever holds the phone's
token, or the bridge's key at the bridge's address, can put any prompt to a full agent.

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
| 200 | `audio/wav` with the spoken summary. Headers: `X-Jarvis-Summary` (URL-encoded text of what is spoken) and `X-Jarvis-Session-Id`. Silence gets a spoken "didn't catch that" and no agent turn. In `full` mode also a read-back (then with `X-Jarvis-Needs-Confirmation: 1`) or a spoken error of the agent host; see "Agent modes". |
| 400 | No project, an unknown project, an empty body, or a `Content-Length` that is not a number |
| 401 | Missing or wrong token |
| 409 | That project is still busy with another turn (one turn per session at a time) |
| 413 | Audio larger than 10 MB |
| 502 | The Claude Code turn failed or timed out (details in the log) |

A connection that stalls for 30 seconds is closed.

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
