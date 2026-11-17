---
status: current
last_reviewed: 2026-11-17
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
| `JARVIS_WHISPER_LANGUAGE` | no (default: detected per recording) | A language code (`en`, `nl`) to skip Whisper's language detection, which costs a second pass over the recording: on `agent-lxc` (base, int8) 0.8 s of the 1.7-1.9 s a short question takes. Set it only when you speak one language to Jarvis: Dutch speech with `en` comes out as a rough English translation. See "Where a turn's time goes". |
| `JARVIS_WHISPER_BEAM_SIZE` | no (default `5`) | Whisper's beam width, 1 to 10. `1` (greedy) took another 0.05-0.2 s off with the same transcripts on the test recordings. |
| `JARVIS_KOKORO_VOICE` | no (default `bm_george`) | A Kokoro voice name. The default is a British male voice; the image also carries `af_heart` (American female). Pick one by ear once the service runs (see below). |
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
  has its own how-to, [`agent-host.md`](agent-host.md); until it is installed and its host
  checks have passed, do not set `full` anywhere.

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

**Which projects.** In `full` mode the agent host decides (change
[`jarvis-general-conversation`](../../openspec/changes/jarvis-general-conversation/design.md),
D52):

- `GET /projects` asks the agent host for its list (one ssh connection with the list
  request `{"v":1,"list":true}`, at most 8 seconds, then ssh is stopped) and answers
  `{"projects": [...], "source": "agent_host"}`. When the agent host cannot be reached,
  its kill switch is set, its wrapper is older than the list request, or the answer cannot
  be read, the bridge lists its own projects with `"source": "bridge"` and logs why.
- `POST /turn` accepts any name of the agent host's shape
  (`[A-Za-z0-9][A-Za-z0-9._-]{0,63}`), whether or not the bridge has a copy; another name
  is a 400 and nothing is sent. The agent host refuses a name it does not have
  (`no_checkout`).
- The read-only fallback needs the bridge's own copy. Without one nothing runs and the
  phone hears what happened: "The agent host is not reachable right now.", "The agent host
  does not have this project.", "Full mode is switched off on the agent host." or "This
  project's settings need the desk."
- So a repository that is only on the agent host (for example one Jarvis just created and
  cloned there, see [`agent-host.md`](agent-host.md#a-new-repository-becomes-a-project)) is
  in the list and can be asked about at once. The pod's sync is unchanged and runs at pod
  start: its copies only serve the fallback, and a new repository reaches them at the next
  restart.

In `readonly` mode the bridge's own list decides, as before.

**A new project.** When a turn created a repository, the agent host's reply carries a
`project` field ([`agent-host.md`](agent-host.md#a-new-repository-becomes-a-project)), on
every kind of reply. The bridge says what became of it after the summary (before a
read-back, so the question stays last; after the error line of a failed turn): "It's ready.
You can switch to it.", "I'm still setting it up at the desk.", "A different folder with
that name exists. I passed that on to the coding session." or "The repository was created, but I could not
set it up as a project. I passed that on to the coding session." When it is ready, the reply carries the
header `X-Jarvis-Projects-Changed: 1` and the app fetches its list again. An app that
shows typed project names sends `X-Jarvis-Projects: typed` with each turn and hears "It's
ready. Add it to your project list in the app." instead.

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
memory only, for 60 seconds from when the reply arrived (less when the agent host says it
keeps the call for less). It keeps them only once the read-back has been synthesised and
written to the phone: if text-to-speech or the write fails, nobody heard it, so the bridge
sends answer `no` to the agent host and keeps nothing.
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
free text below and is never replaced by another sentence, and nothing is taken out of it
or tidied. One that would have to be changed to be spoken cannot be spoken: empty, longer
than 500 characters, white space at either end, anything that splits into lines, any
control, format, private-use or unassigned character, or any separator other than the
plain space (no line or paragraph separator, no no-break space). The bridge
then cancels the call on the agent host (answer `no`), keeps nothing and says "I could
not read that back, so I cancelled it. Ask me again." Detected speech is measured with the voice activity detector inside
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
extra or duplicate field, a value of the wrong type or shape, or a `spoken` that holds half
a surrogate pair, and then reports the turn as failed without running it again. `spoken` is the agent's marker line and goes through
the same summary filter as a read-only reply; the full reply never leaves the agent host.

| From the agent host | The phone hears |
|---|---|
| `busy` | "The agent host is busy. Try again in a moment." |
| `busy` or `low_memory` in answer to a yes | "The agent host is busy. Say yes again in a moment." The read-back stays pending with the same id and the same deadline, and the reply has `X-Jarvis-Needs-Confirmation: 1` again. |
| `low_memory` | "The agent host is busy: it is short of memory. Try again later." |
| `timeout` | "The agent took too long on the agent host and was stopped. I did not run it again." |
| (the bridge stopped waiting) | "It's still running at the desk. Ask me for the result in a bit." |
| `agent_failed` | "The agent turn failed on the agent host. I did not run it again." |
| `expired` | "That took too long. Ask again." |
| `bad_request` | "The agent host refused the request." |
| notice `needs_desk` | "I passed that on to the coding session." |
| notice `run_alone` | "That command has to be run on its own, so it was refused." |
| notice `settings_changed` | "The project settings changed. I passed that on to the coding session." |
| notice `new_session` | "I started a new session for this project." |
| notice `target_changed` | "The push target changed. I passed that on to the coding session." |
| notice `out_of_budget` (with `agent_failed`: the usage limit shared with the coding session) | "The agent is out of budget for now." After a yes followed by "I do not know whether it ran. Check at the desk." |

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
uses it when its projects field is empty or `*` ([`android-app.md`](android-app.md)). In
`full` mode the names come from the agent host and the answer has a second field,
`"source": "agent_host"` or, when the agent host could not list, `"source": "bridge"` (see
"Agent modes").

`POST /turn?project=<name>` with `Authorization: Bearer <token>` and the
recorded audio as the body. Replies:

| Status | When |
|---|---|
| 200 | `audio/wav` with the spoken summary. Headers: `X-Jarvis-Summary` (URL-encoded text of what is spoken), `X-Jarvis-Session-Id` and `X-Jarvis-Timing` (milliseconds per stage, see "Where a turn's time goes"). Silence gets a spoken "didn't catch that" and no agent turn. In `full` mode also a read-back (then with `X-Jarvis-Needs-Confirmation: 1`) or a spoken error of the agent host, and `X-Jarvis-Projects-Changed: 1` when a new project became ready; see "Agent modes". |
| 400 | No project, an unknown project (in `full` mode: a name the agent host would not accept by its shape), an empty body, or a `Content-Length` that is not a number (digits only: `+5`, `5_0` or padded values are refused) |
| 401 | Missing or wrong token |
| 409 | That project is still busy with another turn (one turn per session at a time). The lock lives only while that turn runs, so names asked for in `full` mode leave nothing behind. |
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

Only the summary is spoken, at most 160 characters of it (Claude Code is asked for
one sentence, two at most, of about 100 to 150 characters, because speech is the slow
part of a turn; a longer one is cut at its last sentence end within 160 characters,
or else at a word, with "…"), and only when it contains nothing that looks like code or a path (backticks, slashes, file
names, shell operators) or like a credential (`sk-ant-`, or any run of 32 or more
letters, digits, `_` or `-`). Otherwise the phone hears "Done. Check the log for
details."

## Where a turn's time goes

Every answered turn writes one INFO line, in the file and on stdout, with numbers only
(no transcript, no summary):

```
project=jarvis-home timing read_ms=4 stt_ms=1702 vad_ms=61 agent_ms=7650 tts_ms=5420 write_ms=3 total_ms=14840 audio_in_s=5.5 audio_out_s=9.9 spoken_chars=143
```

| Field | What |
|---|---|
| `read_ms` | Reading the upload after the head arrived |
| `stt_ms` | Decoding the recording and Whisper's transcript |
| `vad_ms` | `full` mode only: the detected-speech measurement (D36, step 5) |
| `agent_ms` | The agent: in `full` mode the ssh round trip to the agent host (the turn record there splits it further, see [the agent host's records](agent-host.md#reading-the-records)); in `readonly` mode the local Claude Code turn |
| `tts_ms` | Kokoro rendering the spoken line |
| `write_ms` | Writing the WAV to the phone |
| `total_ms` | From the request's head to the last byte written |
| `audio_in_s`, `audio_out_s` | Seconds of the recording and of the reply, from their WAV headers (absent when the upload is not WAV) |
| `spoken_chars` | Length of the spoken line |

The same stages, without `write_ms` and with the total up to the moment the headers go
out, are in the reply header `X-Jarvis-Timing`, e.g.
`read=4;stt=1702;vad=61;agent=7650;tts=5420;total=14837`. A client may show it; the app
does not need it.

```sh
grep ' timing ' "$JARVIS_STATE_DIR/bridge.log" | tail          # on a workstation
kubectl -n jarvis logs deploy/jarvis -c bridge | grep ' timing ' # in the cluster
```

### Where the 20 seconds went (2026-11-17, before this timing existed)

Mark: "why is the response 20 sec, for either long or short interactions?" Put together
from the pod's log (the agent host's answer and the reply's request line are logged with
timestamps), the agent host's records, and the speech models run on `agent-lxc` (4 vCPUs,
the same size as the pod's limit on node-03):

| Stage | Trivial question ("can you do web searches?", 09:47) | Where the number comes from |
|---|---|---|
| Transcript (whisper base, int8, beam 5, language detected) | about 1.7-1.9 s | Measured on `agent-lxc` with 4.4-9.3 s recordings |
| Agent host, ssh round trip | 7.6 s, of which about 1 s Claude Code's start and 59,000 tokens written to the prompt cache: a resumed session after a night | The turn record; the bridge logged its answer 0.02 s after the record was written |
| Speech (Kokoro, 143 characters) | 5.4 s | The pod's log: agent answer at 09:47:13.57, reply sent at 09:47:18.99. The next two turns: 6.0 s for 159 and 161 characters. About 0.038 s per character, half of real time |
| **On the bridge** | **about 15 s** | |
| The phone | upload, a 10 s WAV (about 480 KB) back over the tailnet, start of playback | not measured |

So the fixed part is about 7 s (transcript, Claude Code's start, speech), and the agent's
own part is 4 s for a trivial answer and 15-80 s for one that searches or reads. That is
why a short turn does not feel much faster than a long one.

What the bridge itself can save, measured on `agent-lxc` with English test recordings
(three runs each, best time):

| Whisper setting | 5.5 s recording | 6.8 s | 9.3 s |
|---|---|---|---|
| Current: beam 5, language detected | 1.72 s | 1.75 s | 1.93 s |
| `JARVIS_WHISPER_BEAM_SIZE=1` | 1.61 s | 1.64 s | 1.68 s |
| `JARVIS_WHISPER_LANGUAGE=en` | 0.94 s | 1.01 s | 1.15 s |
| both | 0.88 s | 0.91 s | 0.97 s |

The transcripts were identical in every row. Whisper's VAD filter saved nothing on
recordings this short (Whisper pads to 30 s anyway). A Dutch recording with `en` came out
as a loose English translation, so `en` is for an English-only driver.

Only the summary line is spoken (at most 160 characters), so speech is already as short
as the bridge can make it without saying less. Kokoro's `speed=1.15` rendered 7 % faster.
**Speech cannot start sooner** without streaming: the agent host answers once, at the end
of the turn, and the WAV goes out with a `Content-Length` after it is complete. Streaming
is the next step: Kokoro yields audio per sentence, and the first sentence of a summary
(about 70 characters) is ready after about 2.5 s instead of 5.5 s; the rest renders faster
than it plays. That needs a streamed response (a WAV header without a length, or raw PCM)
and a player on the phone that starts on the first bytes (`AudioTrack` instead of the
in-memory `MediaPlayer`): about half a day on the bridge and a day in the app, for about 3
seconds off the time to the first word of every turn.

## Picking a Kokoro voice

Jarvis speaks with `bm_george`, a British male voice, unless `JARVIS_KOKORO_VOICE`
says otherwise. Kokoro ships several voices: the first letter of a name is the
language (`a` American, `b` British English), the second `f` or `m`. A `b…` voice is
pronounced as British English, every other voice as American English. Try a few by
setting `JARVIS_KOKORO_VOICE` and listening to a response, or render one directly:

```sh
uv run python -c "
from bridge.tts import synthesize
open('/tmp/voice-test.wav', 'wb').write(synthesize('This is a test.', voice='bm_george'))
"
```

Play `/tmp/voice-test.wav` and try other voice names (see the Kokoro project's
voice list) until one sounds right for spoken replies in a car. In the cluster image
only the baked voices work (`bm_george` and `af_heart`); see
[`run-in-cluster.md`](run-in-cluster.md).

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
