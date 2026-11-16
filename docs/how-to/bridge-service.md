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
| `JARVIS_HEADER_DEADLINE_S` | no (default `10`) | Seconds a client gets, in total, to send the request line and all headers. A number above 0, at most 300; anything else is refused at start. See "What the endpoints do". |
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
| `JARVIS_AGENT_HOST` | yes | The agent host: a DNS name or an address on a private network the bridge shares with it (the tailnet on a workstation; in a cluster see [`run-in-cluster.md`](run-in-cluster.md#full-mode-the-road-to-the-agent-host)). |
| `JARVIS_AGENT_USER` | yes | The Unix user of the coding session there. |
| `JARVIS_AGENT_PORT` | no (default `22`) | sshd's port. |
| `JARVIS_AGENT_KEY_FILE` | yes | Absolute path of the bridge's private key: a dedicated key whose `authorized_keys` line on the agent host forces the wrapper. A file, never a variable. |
| `JARVIS_AGENT_KNOWN_HOSTS_FILE` | yes | Absolute path of a `known_hosts` file with the agent host's public host key, for exactly the name (and port, as `[name]:port`, when it is not 22) in `JARVIS_AGENT_HOST`. An unknown or changed host key fails the turn; there is no trust on first use. |
| `JARVIS_AGENT_TIMEOUT_S` | no (default `170`, at most `175`) | Seconds the bridge waits for the agent host before it tells the phone that the turn is still running. The phone itself gives up after 180 seconds (the Android app's read timeout), and the bridge must have answered before that. The turn may take longer on the agent host (its own timeout is 600 s by default): the bridge only stops waiting. |
| `JARVIS_CONFIRM_YES` | no (default `yes,ja`) | The closed list of affirmations, comma-separated, written as they are compared: lower case, no punctuation. |
| `JARVIS_CONFIRM_NO` | no (default `no,nee`) | The closed list of refusals. A word cannot be on both lists. |
| `JARVIS_CONFIRM_MIN_SPEECH_S` | no (default `0.3`) | The least detected speech, in seconds, a recording must hold before it counts as an utterance at all in `full` mode, a yes included. |

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
    -o LogLevel=VERBOSE -E <a temporary file> -o IdentityFile=<key file>
    -p <port> <user>@<host>
```

That is the list of design D32 with two changes made after review. The key is named with
`-o IdentityFile=`, not `-i`: with `-i` a key file that is not there is dropped with a
warning and ssh goes on with the default keys in `~/.ssh`; with the option it stays the
only identity (a test asks `ssh -G`). And `LogLevel=VERBOSE` with `-E` are added. They
change nothing about what the connection may do. They make ssh write its own account of the
connection to a file of the bridge's (mode `0600`, in the temporary directory, removed
after the turn), apart from whatever the remote command prints on stderr.

`ssh` gets `PATH` and `HOME` and no other variable: not the token, not the Claude
credentials. The request goes over stdin. Before every turn, not only at start, the key
file and the known-hosts file are checked again (there, regular files, nothing for group
or others); when one fails, ssh is not started and the turn is answered read-only as "could
not be reached". The reply and the remote stderr are read as they come and never kept
beyond 64 KB plus a small margin: more than that is not a reply, ssh is stopped and the
turn is reported as failed.

**When a turn takes longer than the wait.** After `JARVIS_AGENT_TIMEOUT_S` the bridge stops
waiting, frees the project for the next request and says "It's still running at the desk.
Ask me for the result in a bit." It does not cut the connection: ssh is left alone until it
ends by itself, so the turn on the agent host runs to its end and is recorded there; what
it still sends back is read and thrown away. (An ssh that is still there after 15 minutes
is stopped.) The agent host runs one turn at a time, so a request during that time gets
"The agent host is busy"; afterwards the next turn resumes the same session there, which
knows what happened. A read-back that arrives after the bridge stopped waiting is not
heard and expires on the agent host.

**Read-only for one turn.** A `full` turn is answered by the `readonly` adapter instead,
and its first sentence says why, in exactly four cases, all before a turn can have started
on the agent host: the connection could not be made, the agent host does not have the
project (`no_checkout`), its kill switch is set (`disabled`), or the project's settings are
unsafe there (`unsafe_settings`). In every other case the turn is **never run again**, in
either mode: the phone hears what went wrong and that it was not run again.

"The connection could not be made" is decided on ssh's own log and on nothing else, and
needs all of: exit status 255, nothing on stdout, no `Authenticated to` line (ssh writes one
the moment the agent host accepts the key, before the forced command runs), and one of the
lines OpenSSH only writes before authentication: `ssh: connect to host …`, `ssh: Could not
resolve hostname …`, `kex_exchange_identification: …`, a banner-exchange failure, or
`…: Permission denied (publickey).`. Anything else is "a turn may have started":
`Connection reset by …` and `Connection closed by …` (OpenSSH writes them for a session
that broke later too), any other exit status, and an empty log, with one exception: when
the log is empty and ssh's real stderr holds exactly one line, `No user exists for uid …`
(exit 255) or `Couldn't open logfile …` (exit 1), ssh stopped before it had a log or a
connection, and that is "could not be made" too. What the wrapper, the agent or
a git or ssh child on the agent host prints never counts, even when it is one of those
very lines: it arrives on ssh's stderr, not in ssh's log. Checked with OpenSSH 9.6 against
a real sshd with a forced command (2026-11-16): a closed port, an unknown name, a refused
key and a changed host key each left their line in the log and nothing on stderr; a remote
command that printed `ssh: connect to host … Connection refused` and was then killed left
only `Authenticated to …` in the log.

A host key that is not the pinned one fails the turn and is not answered read-only. A
`no_checkout`, `disabled`, `unsafe_settings`, `busy`, `low_memory`, `bad_request` or
`expired` that comes with a turn id contradicts itself (those codes mean no turn started,
the id means one did): it is treated as a turn that ran, so never answered read-only.

**Confirmation.** When the agent on the agent host reaches a gated action (a merge, a
deploy, a delete: design D36), the reply is a read-back and nothing else, with the header
`X-Jarvis-Needs-Confirmation: 1`. The bridge keeps its id and hash for that project, in
memory only, for 60 seconds (less when the agent host says it keeps the call for less).
The next utterance for that project decides, from the transcript of the driver's own audio
and from nothing else:

| The transcript, lowered and without punctuation, is | What happens |
|---|---|
| exactly a word on the affirmation list | The confirmation is sent with answer `yes`; the reply is the agent's. |
| exactly a word on the refusal list | Answer `no` is sent, the agent host clears the call and starts no agent turn: "Cancelled." |
| anything else ("yes but wait", another question) | An ordinary request with one line in front saying the action was not confirmed; it clears the call on the agent host like every request. |

**Every utterance in `full` mode needs the minimum of detected speech**
(`JARVIS_CONFIRM_MIN_SPEECH_S`), not only a yes: Whisper writes short words ("yes", "you")
from noise, and a full agent does not act on a sound. A recording with less, and a
transcript that holds no word at all once punctuation and symbols are taken out ("...", a
note sign), is answered like silence: "Sorry, I didn't catch that.", no turn anywhere, and
a waiting read-back keeps waiting. Transcript and configured words are compared in one
Unicode form (NFKC), so a word with an accent matches however it was typed.

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
nothing counts as an utterance.

When a yes was sent and no answer came back that says what happened (the connection broke,
its reply could not be read, `timeout`,
`agent_failed`), the phone hears the failure and "I do not know whether it ran. Check at
the desk." It never hears "Nothing was confirmed" in that case; that sentence is kept for
when it is certain that the agent host started nothing.

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
| (the bridge stopped waiting) | "It's still running at the desk. Ask me for the result in a bit." |
| `agent_failed` | "The agent turn failed on the agent host. I did not run it again." |
| `expired` | "That took too long. Ask again." |
| `bad_request` | "The agent host refused the request." |
| notice `needs_desk` | "That one needs the desk." |
| notice `run_alone` | "That command has to be run on its own, so it was refused." |
| notice `settings_changed` | "The project settings changed. That needs the desk." |
| notice `new_session` | "I started a new session for this project." |
| notice `target_changed` | "The push target changed. That one needs the desk." |
| notice `out_of_budget` (with `agent_failed`: the usage limit shared with the coding session) | "The agent is out of budget for now." |

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
| 400 | No project, an unknown project, an empty body, or a `Content-Length` that is not a number (digits only: `+5`, `5_0` or padded values are refused) |
| 401 | Missing or wrong token |
| 409 | That project is still busy with another turn (one turn per session at a time) |
| 413 | Audio larger than 10 MB |
| 502 | The Claude Code turn failed or timed out (details in the log) |
| 408 | The request line and headers did not arrive within 10 seconds in total (any route, before the token is looked at). The connection is closed. |
| 503 | Eight connections are already open (any route, before the token is looked at). `Retry-After: 5`. |

Every reply closes its connection: the bridge answers HTTP/1.0 and keeps none alive.

How long a client can keep a connection without a turn running:

- **The request line and headers: 10 seconds in total** (`JARVIS_HEADER_DEADLINE_S`),
  counted from the moment the connection is taken up, however the bytes are spread. When
  the time is up the bridge sends a 408 and closes. A client that sends a header byte
  every few seconds gets the same 10 seconds as one that sends nothing.
- **A body that is read to be thrown away: 30 seconds in total** (below).
- **The body of an accepted turn: 30 seconds per read.** Only a client with the token gets
  this far.

The bridge serves at most eight connections at once (`MAX_CONNECTIONS` in
`bridge/server.py`). A connection counts from the moment it is accepted until it closes.
The ninth gets no thread and no place in a queue: the bridge writes a 503 at once, without
reading the request, and closes. A client that had already started a large upload can see
a broken connection instead of the 503. The phone app shows "The bridge is busy with other
connections" for a 503.

The two limits work together. Without the cap every connection got a thread. With the cap
alone, eight clients trickling header bytes could hold all eight slots for as long as they
liked, token or not. With the header deadline such a client loses its slot after 10
seconds, so eight of them must reconnect every 10 seconds to keep the bridge busy. That is
still possible for anyone who can connect: the bridge does not limit connections per
address, and without the token a client can also hold a slot for 30 seconds with an
announced body that never comes. The tailnet ACL and the NetworkPolicy decide who can
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
